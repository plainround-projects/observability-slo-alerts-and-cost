# Tempo (2.6.1)

## What it is

Tempo is a database for **traces** made by Grafana Labs. It accepts spans (here, over OTLP gRPC from the Collector), stores them cheaply in blocks on disk, and answers two kinds of question: "give me the trace with this id" and "find traces matching this condition" in a query language called **TraceQL**. This repository pins Tempo 2.6.1 and runs it as one process ("single binary") with its data on a local volume. That is enough for a laptop; a production Tempo is split into several services.

## Why this repository uses it

Tempo is what the gateway's tail sampling and redaction are measured on. The volume scenario sends the same traffic through a gateway without and with the two policies and reads **how many bytes of spans Tempo accepted** (its own counter), **which planted traces it holds** (a TraceQL search plus exact lookups) and **whether the test email and card appear in what it stored** (TraceQL searches on attribute values, and reading whole traces).

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/tempo/tempo.yaml` | Tempo's configuration. |
| `docker-compose.yml` (service `tempo`) | The pinned image, config, data volume, port 23200, health check. |
| `observability/collector/gateway.yaml` | The exporter that sends traces here (see `opentelemetry-collector.md`). |
| `observability/grafana/provisioning/datasources/datasources.yaml` | The Tempo data source, linked to Loki. |
| `src/slo_lab/lab/stores.py` | `TempoClient`: bytes and spans received, trace by id, TraceQL search (see `requests.md`). |
| `src/slo_lab/lab/volume.py` | Uses the searches (see `python-lab-scenarios.md`). |

## How it works underneath

Tempo's pieces, all inside one process here: the **distributor** receives spans and hashes the trace id to choose an ingester; the **ingester** holds recent spans in memory and in a write-ahead log (`wal`, a file where every write is recorded first, so a crash loses nothing), and cuts a **block** after `max_block_duration`; the **compactor** merges small blocks and deletes blocks older than `block_retention`; the **querier** reads blocks and the ingester to answer queries.

A trace arrives in pieces, because each service exports its own spans separately. Tempo can therefore return a **partial trace** for a few seconds: for example the back service's spans without the front service's root span. Tools that need a whole trace (the `wait_for_trace` helper here) check that every span's parent is present.

TraceQL looks like `{ resource.service.name = "back" && duration > 1s }`. Braces hold conditions on one span; `resource.` reads attributes of the resource (the process), `span.` reads span attributes, `duration` is the span's length. The API `/api/search?q=...` runs TraceQL; `/api/traces/<id>` fetches one trace; `/ready` says whether Tempo is ready.

## Walkthrough of this repository's use

### `tempo.yaml`

```yaml
# Single-binary Tempo with local storage: enough for a laptop, not a production topology.
stream_over_http_enabled: true
server:
  http_listen_port: 3200
  log_level: warn
```

The comment says what this is. `stream_over_http_enabled: true` allows streaming search results. `server` sets the HTTP port to 3200 (the API and `/ready`) and quiet logs.

```yaml
distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
```

The `distributor` receives spans from the Collector over OTLP gRPC on port 4317 inside the container.

```yaml
ingester:
  max_block_duration: 2m

compactor:
  compaction:
    block_retention: 24h
```

The `ingester` cuts a block of recent traces every two minutes, so traces become searchable from storage soon; the `compactor` deletes blocks older than 24 hours.

```yaml
storage:
  trace:
    backend: local
    wal:
      path: /var/tempo/wal
    local:
      path: /var/tempo/blocks

usage_report:
  reporting_enabled: false
```

Storage is `local`: the write-ahead log in `/var/tempo/wal` and blocks in `/var/tempo/blocks`, on the `tempo-data` volume. `usage_report` is switched off so Tempo sends no statistics.

### The Compose service

```yaml
  tempo:
    image: grafana/tempo:2.6.1
    command: ["-config.file=/etc/tempo/tempo.yaml"]
    volumes:
      - ./observability/tempo/tempo.yaml:/etc/tempo/tempo.yaml:ro
      - tempo-data:/var/tempo
    ports:
      - "127.0.0.1:${TEMPO_HOST_PORT:-23200}:3200"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3200/ready | grep -q ready"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

The `-config.file` flag, the read-only mounted config, the data volume, port 3200 published as 23200 on the loopback address, and a health check that fetches `/ready` and looks for the word `ready`.

### The searches and counters the volume scenario uses

```python
        held = set(self._tempo.search('{ resource.service.name = "checkout" }', *window, limit=SEARCH_LIMIT))
```

`{ resource.service.name = "checkout" }` lists every trace the store holds for the service in the time window (`resource.` reads attributes of the process, here the `service.name` set by `OTEL_SERVICE_NAME`).

```python
        email_hits = self._tempo.search(
            f'{{ span.customer.email =~ ".*{EMAIL_FRAGMENT}.*" }}', *window, limit=SEARCH_LIMIT
        )
        card_hits = self._tempo.search('{ span.payment.card =~ ".*4242.*" }', *window, limit=SEARCH_LIMIT)
```

`{ span.customer.email =~ ".*test-user-redact-me.*" }` finds spans whose `customer.email` attribute matches a regular expression (`=~`; `span.` is the prefix for span attributes); the other search looks for `payment.card` matching `.*4242.*`. After the protected gateway these must return nothing.

```python
    def bytes_received(self) -> float:
        """Total span bytes the distributor has accepted since Tempo started."""
        return metric_value(_get(f"{self._base}/metrics").text, "tempo_distributor_bytes_received_total")

    def spans_received(self) -> float:
        """Total spans the distributor has accepted since Tempo started."""
        return metric_value(_get(f"{self._base}/metrics").text, "tempo_distributor_spans_received_total")
```

`tempo_distributor_bytes_received_total` and `tempo_distributor_spans_received_total` are counters on Tempo's own `/metrics` page: the bytes and spans its distributor accepted since it started. The difference of two readings is the volume one pass added.

```python
    def trace_json(self, trace_id: str) -> str | None:
        """The stored trace as JSON text, or ``None`` when Tempo does not have it."""
        response = _get(f"{self._base}/api/traces/{trace_id}", headers={"Accept": "application/json"})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise StoreError(f"Tempo answered {response.status_code} for {trace_id}: {response.text[:200]}")
        return response.text

    def search(self, traceql: str, start: float, end: float, limit: int = 5000) -> list[str]:
        """Ids of traces matching a TraceQL query."""
        params = {"q": traceql, "start": int(start), "end": int(end) + 1, "limit": limit}
        response = _get(f"{self._base}/api/search", params=params)
        if response.status_code != 200:
            raise StoreError(f"Tempo search answered {response.status_code}: {response.text[:200]}")
        return [item["traceID"] for item in response.json().get("traces", [])]
```

`trace_json(id)` reads one stored trace (`/api/traces/<id>`); `search` runs a TraceQL query on `/api/search`.

## Try it yourself

```bash
curl -s http://127.0.0.1:23200/ready
```

Expected (needs `make up`): `ready`.

```bash
curl -s http://127.0.0.1:23200/metrics | grep -E "^tempo_distributor_(bytes|spans)_received_total"
```

Expected: two counters; send traffic and read them again to see them grow.

```bash
curl -s --get "http://127.0.0.1:23200/api/search" --data-urlencode 'q={ resource.service.name = "checkout" }' --data-urlencode 'limit=3' | python3 -m json.tool | head -15
```

Expected (after traffic): up to three traces.

```bash
.venv/bin/python -m pytest tests/unit/lab/test_lab.py -k volume -q
```

Expected: `2 passed`: the volume scenario against fakes.

```bash
make volume
```

Expected (several minutes): the table in `opentelemetry-collector.md`; the raw pass finds the test values with `span.customer.email` and `span.payment.card` searches, the protected pass finds none.

## Mistakes people make

- **Looking for the trace immediately.** Spans take seconds to flush and index.
- **Treating a partial trace as the whole.** Check that every parent exists.
- **Running the single binary in production.** It is for learning.
- **Forgetting that ids may be base64 or hex** depending on the API; the client here normalizes.

## Check yourself

1. What does Tempo store? *Traces (spans).*
2. What does the WAL protect? *Recent spans that are not yet in a block, in case of a crash.*
3. Why might a trace look incomplete right after the request? *The services export spans separately and Tempo receives them at slightly different times.*
4. What does `{ duration > 1s }` select? *Spans longer than one second.*
5. Which endpoint tells you Tempo is ready? *`/ready`.*

## Further reading

- Tempo 2.6 documentation: https://grafana.com/docs/tempo/v2.6.x/
- Configuration: https://grafana.com/docs/tempo/v2.6.x/configuration/
- TraceQL: https://grafana.com/docs/tempo/v2.6.x/traceql/
- HTTP API: https://grafana.com/docs/tempo/v2.6.x/api_docs/
