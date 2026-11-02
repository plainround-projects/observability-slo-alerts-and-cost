# Loki (3.1.1)

## What it is

Loki is a database for **logs** made by Grafana Labs. Unlike a full-text search engine it does not index the text of each line. It indexes only a small set of **labels** per log stream (here `service_name`) and stores the line text compressed in chunks. Queries use **LogQL**: you pick streams with label matchers, then filter lines. This repository pins Loki 3.1.1 as one process with local storage.

## Why this repository uses it

Logs are the second place where personal data leaks: the naive checkout writes `payment received from <email> with card <number>` into a log line. The volume scenario counts the **bytes Loki accepted** (its own counter) and asks LogQL whether the exact test email and card number appear in any stored line. With the raw gateway they do; with the protected one the lines are stored as `payment received from <email> with card <card>`.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/loki/loki.yaml` | Loki's configuration (a single-binary Loki with local storage). |
| `docker-compose.yml` (service `loki`) | The pinned image, config, data volume, port 23100, health check. |
| `observability/collector/gateway.yaml` | The exporter `otlphttp/loki` and the log redaction (see `opentelemetry-collector.md`). |
| `observability/grafana/provisioning/datasources/datasources.yaml` | The Loki data source and the link from a span to its log lines. |
| `src/slo_lab/lab/stores.py` | `LokiClient`: bytes received and LogQL range queries (see `requests.md`). |
| `src/slo_lab/lab/volume.py` | The exact-value queries (see `python-lab-scenarios.md`). |

## How it works underneath

A **stream** is all the lines that share exactly the same labels. The OTLP route is Loki's native one: the Collector posts to `/otlp`, and Loki turns the resource attribute `service.name` into the label `service_name`. Other fields of the log record (the trace id and span id) are stored as **structured metadata**: per-line key/value pairs that are *not* labels, so they do not create new streams, yet you can still filter on them. Structured metadata needs `allow_structured_metadata: true` and the schema version v13 with the TSDB index.

`schema_config` says how data is laid out over time. A new config entry applies from a date. `store: tsdb` is the index type, `object_store: filesystem` puts chunks in a folder, and `period: 24h` creates a new index table every day.

LogQL: `{service_name="back"}` selects a stream. `| trace_id="abc"` filters by a structured metadata field. `| json` would parse the line as JSON. `|= "text"` filters by substring. The HTTP API `/loki/api/v1/query_range` returns results over a time range; the request header `X-Loki-Response-Encoding-Flags: categorize-labels` asks Loki to include structured metadata in the answer.

`auth_enabled: false` turns off multi-tenancy, so no tenant header is needed.

## Walkthrough of this repository's use

### `loki.yaml`

```yaml
# Single-binary Loki with local storage. Structured metadata must be on to receive the OTLP trace and span ids.
auth_enabled: false

server:
  http_listen_port: 3100
  log_level: warn
```

The comment states the one setting that is easy to forget: structured metadata must be on. `auth_enabled: false` turns off multi-tenancy. `server` sets the HTTP port to 3100 and the log level to `warn`.

```yaml
common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
```

`common` holds settings shared by Loki's internal parts. `instance_addr: 127.0.0.1` is the address Loki advertises to itself. `path_prefix: /loki` is the base folder (a volume in Compose). `replication_factor: 1` keeps one copy of the data. The `ring` is how several Loki instances agree who owns what; with `store: inmemory` a single instance keeps that in memory. `storage.filesystem` puts chunks (compressed log data) in `/loki/chunks` and rules in `/loki/rules`.

```yaml
schema_config:
  configs:
    - from: "2024-04-01"
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
```

`schema_config` describes the on-disk layout. One entry, valid `from` 1 April 2024: `store: tsdb` is the index type, `object_store: filesystem` keeps chunks in a folder, `schema: v13` is the schema version (required for structured metadata), and the index tables are named with prefix `index_` and rotate every `24h`.

```yaml
limits_config:
  allow_structured_metadata: true
  volume_enabled: true

analytics:
  reporting_enabled: false
```

`limits_config.allow_structured_metadata: true` is the key switch: without it Loki rejects log records that carry structured metadata, which is how OTLP sends the trace id and span id. `volume_enabled: true` enables the volume API that Grafana's log explorer uses. `analytics.reporting_enabled: false` turns off anonymous usage reports.

### The Compose service

```yaml
  loki:
    image: grafana/loki:3.1.1
    command: ["-config.file=/etc/loki/loki.yaml"]
    volumes:
      - ./observability/loki/loki.yaml:/etc/loki/loki.yaml:ro
      - loki-data:/loki
    ports:
      - "127.0.0.1:${LOKI_HOST_PORT:-23100}:3100"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3100/ready | grep -q ready"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

The same pattern as Tempo: pinned image, `-config.file` flag, config bind-mounted read-only, a data volume at `/loki`, host port 23100 on loopback and a `wget ... | grep -q ready` health check on `/ready`.

### LogQL and counters in this repository

```python
        email_log = self._loki.query(f'{{service_name="checkout"}} |= "{TEST_EMAIL}"', *window)
        card_log = self._loki.query(f'{{service_name="checkout"}} |= "{TEST_CARD}"', *window)
```

`{service_name="checkout"} |= "test-user-redact-me@example.com"` selects the checkout service's stream (`service_name` comes from the OTLP resource attribute `service.name`) and keeps the lines that **contain** the exact text (`|=` is a substring filter). The second query does the same with the test card number. Each returns the matching lines; the scenario counts them.

```python
    def bytes_received(self) -> float:
        """Total log bytes the distributor has accepted since Loki started."""
        return metric_value(_get(f"{self._base}/metrics").text, "loki_distributor_bytes_received_total")
```

`loki_distributor_bytes_received_total` is a counter on Loki's own `/metrics` page: the log bytes its distributor accepted. The difference of two readings is the volume one pass added.

```python
    def query(self, logql: str, start: float, end: float, limit: int = 5000) -> list[LogLine]:
        """Run a LogQL range query and return the lines oldest first."""
        params = {
            "query": logql,
            "start": int(start * 1e9),
            "end": int(end * 1e9),
            "limit": limit,
            "direction": "forward",
        }
        response = _get(f"{self._base}/loki/api/v1/query_range", params=params)
        if response.status_code != 200:
            raise StoreError(f"Loki answered {response.status_code}: {response.text[:200]}")
        lines = [
            LogLine(int(entry[0]), entry[1])
            for stream in response.json()["data"]["result"]
            for entry in stream["values"]
        ]
        return sorted(lines, key=lambda line: line.timestamp_ns)
```

`query` sends a LogQL range query (`/loki/api/v1/query_range`) with times in nanoseconds, `direction: forward`, and returns the lines oldest first.

## Try it yourself

```bash
curl -s http://127.0.0.1:23100/ready
```

Expected (needs `make up`): `ready`.

```bash
curl -s http://127.0.0.1:23100/metrics | grep ^loki_distributor_bytes_received_total
```

Expected: one counter; send traffic and read it again.

```bash
curl -s --get "http://127.0.0.1:23100/loki/api/v1/query_range" --data-urlencode 'query={service_name="checkout"}' --data-urlencode 'limit=2' | python3 -m json.tool | head -30
```

Expected (after traffic): log lines from the checkout service, each a JSON line with `trace_id`.

```bash
.venv/bin/python -m pytest tests/unit/lab/test_lab.py -k volume -q
```

Expected: `2 passed`.

```bash
make volume
```

Expected: in the table, `test email found in logs` and `test card found in logs` are above zero for the raw gateway and 0 for the protected one.

## Mistakes people make

- **Turning high-cardinality values into labels** (trace ids, user ids). Use structured metadata or keep them in the line.
- **Forgetting `allow_structured_metadata`**, so the OTLP trace ids are rejected.
- **Querying a time range with no data** and thinking Loki is broken. Always give start and end.
- **Not asking for categorized labels**, then missing structured metadata in API results.

## Check yourself

1. What does Loki index? *Only the labels of each stream, not the text of the lines.*
2. What is structured metadata? *Per-line key/value pairs stored beside the line, not part of the stream's labels.*
3. How does `service.name` reach Loki as a label? *Loki's OTLP endpoint converts it to `service_name`.*
4. What does `{service_name="back"} | trace_id="x"` do? *Selects the back service's stream and keeps lines whose trace id is `x`.*
5. Why is `auth_enabled` false here? *A single-user lab needs no tenant separation.*

## Further reading

- Loki 3.1 documentation: https://grafana.com/docs/loki/v3.1.x/
- Configuration: https://grafana.com/docs/loki/v3.1.x/configure/
- Structured metadata: https://grafana.com/docs/loki/v3.1.x/get-started/labels/structured-metadata/
- OTLP ingestion: https://grafana.com/docs/loki/v3.1.x/send-data/otel/
- LogQL: https://grafana.com/docs/loki/v3.1.x/query/
- HTTP API: https://grafana.com/docs/loki/v3.1.x/reference/loki-http-api/
