# OpenTelemetry Collector (contrib 0.111.0): the gateway

## What it is

The OpenTelemetry Collector is a standalone program that receives telemetry, changes it, and sends it on. Applications send OTLP to the Collector at one address, and the Collector decides where each signal goes. That keeps the applications simple and lets you change backends without changing code. Its configuration is one YAML file with four kinds of building blocks:

- **receivers** accept data (for example `otlp`, which listens for OTLP over gRPC on port 4317 and over HTTP on port 4318);
- **processors** change data on the way (batch it, limit memory, drop or rewrite attributes);
- **exporters** send data out (to Tempo, Prometheus, Loki);
- **extensions** add side features that are not in the data path (a health check endpoint).

You choose the image `otel/opentelemetry-collector-contrib`, the distribution that includes the community components. This repository pins version 0.111.0.

## Why this repository uses it

This repository's Collector is a **gateway**: every service sends its telemetry here, and here two policies are applied before any backend stores a byte. **Redaction** replaces personal data (email addresses, card-like numbers) inside span attributes and log bodies. **Tail sampling** keeps every error trace, every trace over one second, and a small share of the rest, which cuts the stored trace volume while keeping what an engineer actually looks for. Metrics are never sampled or redacted: the SLIs must count every request, and metric labels are bounded.

Two configurations exist: `gateway.yaml` (protected) and `gateway.raw.yaml` (the same without the two policies). The raw one is the "before" of the volume comparison and the **positive control** of the redaction test: if the test values cannot be found in the raw gateway's output, finding none in the protected one would prove nothing.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/collector/gateway.yaml` | The protected gateway. |
| `observability/collector/gateway.raw.yaml` | The same without redaction and sampling. |
| `docker-compose.yml` (service `otel-gateway`) | Runs it; the file mounted is chosen by `GATEWAY_CONFIG`. |
| `src/slo_lab/lab/compose.py`, `lab/volume.py` | Switch between the two (see `python-subprocess.md`, `python-lab-scenarios.md`). |
| `tests/observability/test_configuration_as_code.py` | Checks the processor order and the patterns. |
| `tests/integration/test_redaction_in_the_collector.py` | Runs the real Collector image with the real redaction processor and checks its output. |
| `Makefile` (`lint-config`) | `validate` on both files. |

## How it works underneath

Defining a component under `receivers:` or `exporters:` does **not** turn it on. Only components listed in `service.pipelines` run. A **pipeline** is a signal type (`traces`, `metrics` or `logs`) plus an ordered list of receivers, processors and exporters. Data enters from the receivers, passes through each processor in the written order, and is sent to every exporter.

Names can have a suffix after a slash (`otlp/tempo`, `otlphttp/loki`): the part before the slash is the component type, and the part after is a label so you can define the same type twice.

Inside the process, each pipeline is a chain of queues and workers. The `memory_limiter` processor checks memory use every `check_interval`. When use crosses the soft limit (`limit_mib` minus `spike_limit_mib`) it starts refusing new data, which tells the sender to retry later; this prevents the Linux out-of-memory killer from stopping the process. The `batch` processor collects items and sends them when `send_batch_size` is reached or `timeout` passes, so the backend gets a few big requests, not thousands of small ones.

The Collector also reports on itself: its own logs and metrics (`service.telemetry`), and a `health_check` extension that answers HTTP 200 on port 13133 when the process is running. The image has no shell or `wget`, so Docker cannot run a health-check command inside it; other tools call the extension from outside.

**Order matters, and this file's comment says why.** A trace pipeline is `memory_limiter`, then `transform/redact`, then `tail_sampling`, then `batch`. Redaction goes **before** sampling so that nothing sensitive sits in the sampler's memory or reaches a backend, whichever way the sampling decision goes. The memory limiter stays first and `batch` last.

**Tail sampling** decides after the fact. Spans of one trace arrive separately, from different services and at different times. The `tail_sampling` processor holds the spans of each trace in memory until `decision_wait` has passed since the first span, evaluates its **policies** on the whole trace, and keeps or drops the trace as one unit. (A *head* sampler decides when the first span is created, before it knows whether the request will fail.) `num_traces` is how many traces it may hold at once; `expected_new_traces_per_sec` sizes its internal tables. A trace is kept if **any** policy says keep.

**OTTL**, the OpenTelemetry Transformation Language, is the small language of the `transform` processor. A *statement* is a function call such as `replace_pattern(body, "regex", "replacement")`. The statements run against each telemetry item in a *context* (`span`, `log`, ...), where paths like `attributes` or `body` name parts of the item. `replace_all_patterns(attributes, "value", regex, replacement)` goes through every attribute and replaces matches in its *value* (the other mode, `"key"`, would rewrite the names). `error_mode: ignore` means a statement that fails on one item is skipped and the data still flows, instead of dropping it.

## Walkthrough of this repository's use

### `gateway.yaml`

```yaml
# The gateway: every service sends here; this is where traces are sampled and personal data is removed before any
# backend stores it. Order matters: redact first, so nothing sensitive sits in the sampler's memory or reaches a backend.
extensions:
  health_check:
    endpoint: 0.0.0.0:13133
```

The comment states the whole idea. The `health_check` extension answers HTTP 200 on port 13133 (published as 23133); the image has no shell, so the lab polls it from outside to know when a newly started gateway is ready.

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
    spike_limit_mib: 128
```

One receiver, OTLP over HTTP on 4318 (the service sends here). `memory_limiter` is the first processor: 512 MiB hard limit, 128 MiB spike limit (soft limit 384), checked every second. Larger than in the other labs because the tail sampler holds traces in memory.

```yaml
  # Replaces the sensitive value inside an attribute or a log body and keeps the rest, so the field that held it (and
  # what surrounded it) still helps debugging. Email addresses and 13 to 19 digit card-like numbers (spaces or dashes
  # allowed between digits).
  transform/redact:
    error_mode: ignore
    trace_statements:
      - context: span
        statements:
          - 'replace_all_patterns(attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "<email>")'
          - 'replace_all_patterns(attributes, "value", "\\b\\d(?:[ -]?\\d){12,18}\\b", "<card>")'
    log_statements:
      - context: log
        statements:
          - 'replace_pattern(body, "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "<email>")'
          - 'replace_pattern(body, "\\b\\d(?:[ -]?\\d){12,18}\\b", "<card>")'
          - 'replace_all_patterns(attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "<email>")'
          - 'replace_all_patterns(attributes, "value", "\\b\\d(?:[ -]?\\d){12,18}\\b", "<card>")'
```

The redaction processor `transform/redact` (the part after the slash is just a label). The comment says what it replaces: the sensitive value *inside* an attribute or a log body, keeping the rest, so the field and its context still help debugging: email addresses and 13 to 19 digit card-like numbers (spaces or dashes allowed between digits). `trace_statements` run on spans: `replace_all_patterns(attributes, "value", <email regex>, "<email>")` and the same for the card regex; so `customer.email` becomes `<email>` and `payment.card` becomes `<card>` while `order.ref` is untouched. `log_statements` run on log records: `replace_pattern(body, ...)` rewrites the log text (so `payment from a@b.com with card 4242 4242 4242 4242 ok` becomes `payment from <email> with card <card> ok`), and the attributes are also scrubbed. The regular expressions are explained in `regular-expressions.md`; the doubled backslashes are because the OTTL string syntax treats `\` as an escape.

```yaml
  # Waits for a whole trace, then keeps every error, every trace over one second, and a small share of the rest.
  tail_sampling:
    decision_wait: 10s
    num_traces: 50000
    expected_new_traces_per_sec: 100
    policies:
      - name: keep-every-error
        type: status_code
        status_code:
          status_codes: [ERROR]
      - name: keep-every-slow-trace
        type: latency
        latency:
          threshold_ms: 1000
      - name: keep-a-small-share-of-the-rest
        type: probabilistic
        probabilistic:
          sampling_percentage: 5
```

The tail sampler. `decision_wait: 10s`: wait ten seconds after a trace's first span before deciding. `num_traces: 50000` and `expected_new_traces_per_sec: 100` size the buffers. Three policies, kept if any matches: `keep-every-error` (type `status_code`, status `ERROR`) keeps every trace with an error span; `keep-every-slow-trace` (type `latency`, `threshold_ms: 1000`) keeps every trace longer than a second; `keep-a-small-share-of-the-rest` (type `probabilistic`, `sampling_percentage: 5`) keeps 5 percent of the remaining traces at random. The e2e test asserts that errors and slow traces are all kept, that the ordinary kept share is between 0 and 15 percent, and that the stored volume falls by at least 60 percent.

```yaml
  batch:
    timeout: 1s
    send_batch_size: 512
```

`batch` groups items: 512 at a time or after a second.

```yaml
exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls:
      insecure: true
  prometheus:
    endpoint: 0.0.0.0:8889
    resource_to_telemetry_conversion:
      enabled: true
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
```

Three exporters. `otlp/tempo` sends traces to `tempo:4317` over gRPC without TLS. `prometheus` publishes the metrics on port 8889 for Prometheus to scrape, copying resource attributes onto metrics as labels. `otlphttp/loki` sends logs to Loki's native OTLP endpoint `http://loki:3100/otlp`.

```yaml
service:
  extensions: [health_check]
  telemetry:
    logs:
      level: warn
    metrics:
      level: basic
      address: 0.0.0.0:8888
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, transform/redact, tail_sampling, batch]
      exporters: [otlp/tempo]
    # Metrics are never sampled or redacted here: the SLIs must count every request, and metric labels are bounded.
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheus]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, transform/redact, batch]
      exporters: [otlphttp/loki]
```

`service` switches components on: the extension, the Collector's own diagnostics (logs at `warn`, metrics at level `basic` on port 8888) and three pipelines. Traces: `memory_limiter` → `transform/redact` → `tail_sampling` → `batch` → Tempo. Metrics (the comment explains): never sampled or redacted, because the SLIs must count every request and metric labels are bounded: `memory_limiter` → `batch` → Prometheus. Logs: `memory_limiter` → `transform/redact` → `batch` → Loki. Logs are redacted but not sampled.

### `gateway.raw.yaml`: the control

```yaml
# The same gateway without sampling or redaction: the "before" of the volume and privacy comparison and the positive
# control of the redaction test (the test values must be findable here, or finding none in gateway.yaml proves nothing).
# Do not use outside the lab.
extensions:
  health_check:
    endpoint: 0.0.0.0:13133

receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
    spike_limit_mib: 128
  batch:
    timeout: 1s
    send_batch_size: 512

exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls:
      insecure: true
  prometheus:
    endpoint: 0.0.0.0:8889
    resource_to_telemetry_conversion:
      enabled: true
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
```

The comment names its two roles and says "Do not use outside the lab." The extension, receiver, memory limiter, batch and exporters are identical to the protected file.

```yaml
service:
  extensions: [health_check]
  telemetry:
    logs:
      level: warn
    metrics:
      level: basic
      address: 0.0.0.0:8888
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/tempo]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheus]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/loki]
```

The same service block with all three pipelines at `memory_limiter` → `batch`: no redaction, no sampling. A configuration test asserts that this file equals the protected one minus the two policies: same receivers, same exporters, and no `tail_sampling` or `transform/redact` anywhere.

### The Compose service and the check

```yaml
  otel-gateway:
    image: otel/opentelemetry-collector-contrib:0.111.0
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      # make run switches this between gateway.raw.yaml and gateway.yaml.
      - ${GATEWAY_CONFIG:-./observability/collector/gateway.yaml}:/etc/otelcol/config.yaml:ro
    ports:
      - "127.0.0.1:${GATEWAY_HEALTH_HOST_PORT:-23133}:13133"
    depends_on:
      tempo: { condition: service_healthy }
      loki: { condition: service_healthy }
```

`otel-gateway` runs the pinned image with `--config=/etc/otelcol/config.yaml`. The mounted file is `${GATEWAY_CONFIG:-./observability/collector/gateway.yaml}` (the comment: `make run` switches it between the raw and the protected file). Only the health port is published (23133); the service reaches the gateway on the Compose network. It waits for Tempo and Loki to be healthy.

```makefile
	docker run --rm -v "$(CURDIR)/observability/collector/gateway.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
	docker run --rm -v "$(CURDIR)/observability/collector/gateway.raw.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
```

`lint-config` runs `validate` on both gateway files, each mounted into the Collector image.

### The real-Collector redaction test

```python
"""Run the real Collector image with the gateway's redaction processor and a debug exporter; post the test values."""

from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests
import yaml

from slo_lab.lab.traffic import TEST_CARD, TEST_EMAIL

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "otel/opentelemetry-collector-contrib:0.111.0"
SPACED_CARD = "4242 4242 4242 4242"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])

```

The test builds a minimal Collector configuration from the real `transform/redact` block (`gateway["processors"]["transform/redact"]`), with an OTLP/HTTP receiver, the debug exporter (which prints every item) and the same processor for traces and logs, writes it to a temporary file and starts the pinned Collector image with it on a free port (`_free_port` binds port 0 and reads back the number the system chose).

```python
@pytest.fixture(scope="module")
def collector_output(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    gateway = yaml.safe_load((ROOT / "observability/collector/gateway.yaml").read_text())
    config = {
        "receivers": {"otlp": {"protocols": {"http": {"endpoint": "0.0.0.0:4318"}}}},
        "processors": {"transform/redact": gateway["processors"]["transform/redact"]},
        "exporters": {"debug": {"verbosity": "detailed"}},
        "service": {
            "pipelines": {
                signal: {"receivers": ["otlp"], "processors": ["transform/redact"], "exporters": ["debug"]}
                for signal in ("traces", "logs")
            }
        },
    }
    path = tmp_path_factory.mktemp("collector") / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    name, port = f"redaction-probe-{uuid.uuid4().hex[:8]}", _free_port()
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:4318",
            "-v", f"{path}:/etc/otelcol/config.yaml:ro", IMAGE, "--config=/etc/otelcol/config.yaml",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 600
        while True:
            try:
                requests.get(f"http://127.0.0.1:{port}/", timeout=5)
                break
            except requests.RequestException:
                if time.monotonic() > deadline:
                    pytest.fail("the Collector did not start listening within 10 minutes")
                time.sleep(3)
```

A module-scoped fixture starts the container (`docker run -d --name ... -p 127.0.0.1:<port>:4318 -v config:/etc/otelcol/config.yaml:ro IMAGE --config=...`) and waits up to ten minutes until the port answers any HTTP request. `try ... finally` removes the container whatever happens.

```python
        now = str(time.time_ns())
        body = {
            "resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [{"logRecords": [{
                "timeUnixNano": now,
                "body": {"stringValue": json.dumps({"msg": f"payment from {TEST_EMAIL} with card {SPACED_CARD} ok"})},
                "attributes": [{"key": "note", "value": {"stringValue": f"card={TEST_CARD} mail={TEST_EMAIL}"}}],
            }]}]}]
        }  # fmt: skip
        span = {
            "resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": [{
                "traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "b7ad6b7169203331", "name": "POST /checkout",
                "kind": 2, "startTimeUnixNano": now, "endTimeUnixNano": now,
                "attributes": [
                    {"key": "customer.email", "value": {"stringValue": TEST_EMAIL}},
                    {"key": "payment.card", "value": {"stringValue": TEST_CARD}},
                    {"key": "order.ref", "value": {"stringValue": "ord-000042"}},
                ],
            }]}]}]
        }  # fmt: skip
        assert requests.post(f"http://127.0.0.1:{port}/v1/logs", json=body, timeout=180).status_code == 200
        assert requests.post(f"http://127.0.0.1:{port}/v1/traces", json=span, timeout=180).status_code == 200
        time.sleep(3)
        yield subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=True).stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


```

It posts one OTLP log record whose body contains the test email and a spaced card number and whose attributes contain both, and one span with `customer.email`, `payment.card` and `order.ref`, as JSON to `/v1/logs` and `/v1/traces`, expects 200, waits three seconds, and returns the container's log (`docker logs`; the debug exporter writes to standard error, hence `.stderr`).

```python
def test_the_test_values_never_leave_the_collector(collector_output):
    assert TEST_EMAIL not in collector_output
    assert TEST_CARD not in collector_output
    assert SPACED_CARD not in collector_output


def test_only_the_sensitive_part_is_replaced_and_the_rest_of_each_field_survives(collector_output):
    assert "payment from <email> with card <card> ok" in collector_output
    assert "card=<card> mail=<email>" in collector_output
    assert "customer.email: Str(<email>)" in collector_output
    assert "payment.card: Str(<card>)" in collector_output
    assert "order.ref: Str(ord-000042)" in collector_output
```

Two tests on that output: the test values appear **nowhere**, and the surrounding text survives: `payment from <email> with card <card> ok`, `card=<card> mail=<email>`, `customer.email: Str(<email>)`, `payment.card: Str(<card>)`, while `order.ref: Str(ord-000042)` is untouched.

## Try it yourself

```bash
make lint-config
```

Expected (needs Docker): the gateway files validate (no output) among the other validators' `SUCCESS` lines.

```bash
.venv/bin/python -m pytest tests/integration/test_redaction_in_the_collector.py -q
```

Expected (needs Docker and the Collector image): `2 passed` in about a minute: the real redaction processor run on the real image.

```bash
make volume
```

Expected (needs `make up`; several minutes): a table of raw versus protected: stored trace bytes, errors and slow traces found 100 percent in both, the ordinary kept share about 100 percent versus about 5 percent, and the test email and card found in traces and logs for the raw gateway and **0** for the protected one.

```bash
curl -s http://127.0.0.1:23133/
```

Expected: the gateway's health JSON (`"status":"Server available"`).

```bash
.venv/bin/python -m pytest tests/observability -k "gateway or processors or redaction or tail" -q
```

Expected: the configuration tests about the gateway pass.

## Mistakes people make

- **Defining a component but not adding it to a pipeline.** It then does nothing and no error is shown.
- **Wrong processor order.** `memory_limiter` must be first, `batch` last.
- **Using `localhost` as an exporter endpoint** inside a container. Use the service name.
- **Forgetting `tls: insecure: true`** for a plain-text local gRPC endpoint.
- **Using a deprecated exporter.** The `loki` exporter is deprecated for logs in 0.111.0; this repository sends logs to Loki's native OTLP endpoint with `otlphttp`.

## Check yourself

1. What are the four kinds of Collector components? *Receivers, processors, exporters and extensions.*
2. What makes a defined component actually run? *Listing it in a pipeline (extensions: under `service.extensions`).*
3. Why is `memory_limiter` first? *So it can refuse data before the other processors use more memory.*
4. What does `batch` do? *It groups items into larger requests, sent by size or timeout.*
5. Why does `otlp/tempo` have a slash? *The suffix lets you define several components of the same type.*

## Further reading

- Collector documentation: https://opentelemetry.io/docs/collector/
- Configuration: https://opentelemetry.io/docs/collector/configuration/
- Components list (contrib v0.111.0): https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.111.0
- `memory_limiter` processor: https://github.com/open-telemetry/opentelemetry-collector/tree/v0.111.0/processor/memorylimiterprocessor
- `batch` processor: https://github.com/open-telemetry/opentelemetry-collector/tree/v0.111.0/processor/batchprocessor
