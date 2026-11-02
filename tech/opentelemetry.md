# OpenTelemetry (Python API, SDK, exporter and instrumentation)

## What it is

OpenTelemetry (OTel) is an open standard, plus libraries for many languages, for producing **telemetry**: the three kinds of evidence a running program can give about itself.

- A **trace** records the path of one request through the system. It is a tree of **spans**. A span is one timed step with a name, a start time, an end time, a parent span, and **attributes** (key/value notes such as `db.statement`). All spans of one request share one **trace id**.
- A **metric** is a number measured over time: a counter that only goes up, a gauge that moves up and down, or a **histogram** that counts how many measurements fell into each range ("bucket") of values.
- A **log** is a line of text (here JSON) with a time and a level. When written inside a span, a log line can carry that span's trace id and span id, which is what links the three signals.

OpenTelemetry does not store or display anything. It defines how to *produce* and *send* the data (the **OTLP** protocol, the OpenTelemetry Protocol, over HTTP or gRPC) so that any backend can receive it.

## Why this repository uses it

The service sends all three signals to **one gateway**, and the gateway is where the lesson's policies live. The SLI metrics (a request counter by route and status, and a latency histogram) are produced with OpenTelemetry; the traces come from the Flask instrumentation plus two attributes that the use case sets by hand; the logs come from the standard logging module through the OpenTelemetry handler. The personal data that the naive use case puts on a span and in a log line travels through the same SDK, which is why the redaction has to happen at the gateway, before anything is stored.

## Where it appears

| Path | What it does with OpenTelemetry |
| --- | --- |
| `src/slo_lab/infrastructure/otel_telemetry.py` | The request counter and latency histogram (the SLIs), and the annotator that sets span attributes. |
| `src/slo_lab/interfaces/service_app.py` | Records the metrics for every response; copies the trace id into `X-Trace-Id`. |
| `src/slo_lab/interfaces/service_main.py` | Gets the meter `slo_lab.checkout`. |
| `src/slo_lab/infrastructure/logging_setup.py` | The log handler and trace ids (see `python-logging.md`). |
| `Dockerfile`, `docker-compose.yml` | Start through `opentelemetry-instrument` and set the `OTEL_*` variables. |
| `pyproject.toml` | The distro, the OTLP/HTTP exporter and the Flask instrumentation. |
| `tests/conftest.py`, `tests/unit/infrastructure/test_infrastructure.py`, `tests/unit/interfaces/test_apps.py` | An in-memory pipeline that tests read back. |

## How it works underneath

The Python packages are split by role:

- `opentelemetry-api` holds the names your code calls (`trace.get_tracer`, `metrics.get_meter`). With no SDK configured the calls do nothing, so libraries can use the API safely.
- `opentelemetry-sdk` is the real implementation: it makes spans, aggregates metrics, batches them and hands them to **exporters**.
- `opentelemetry-exporter-otlp-proto-http` is the exporter that sends OTLP over HTTP (protobuf-encoded) to a Collector.
- `opentelemetry-instrumentation-*` packages wrap a library (Flask, requests, psycopg) so that every request, outgoing call or SQL statement creates a span automatically. This is **auto-instrumentation**.
- `opentelemetry-distro` and `opentelemetry-instrument` are the glue: the command `opentelemetry-instrument <program>` sets up the SDK from environment variables (`OTEL_*`), switches on all installed instrumentations, and then runs the program. The program's own code needs no setup lines.

**Context propagation.** The current span is stored in a *context* (a `contextvars` variable) so code deeper in the call chain automatically becomes a child. When `requests` calls another service, the requests instrumentation adds the `traceparent` header, and the Flask instrumentation on the other side reads it and continues the same trace.

**Resource.** A set of attributes that describe the process producing the data (`service.name=back`). The backends use `service.name` to tell the services apart.

**Exemplars.** When a measurement is recorded inside a span, the SDK can attach the trace id and span id of that span to the histogram sample. Prometheus then stores "this sample (1.5 s) came from trace X". A dashboard turns it into a dot you can click to open the trace.

**Logs.** Python's standard `logging` module is bridged: a `LoggingHandler` converts each record into an OTLP log record, including the active trace id and span id.

Data leaves the process in batches on a timer, so a request is not visible in the backends for a few seconds.

## Walkthrough of this repository's use

### `infrastructure/otel_telemetry.py`

```python
"""OpenTelemetry adapters: request metrics for the SLIs and span annotation."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.metrics import Meter

# 0.3 is the latency SLO threshold, so it must be an exact bucket boundary for the SLI to be computable.
LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.3, 1.0, 3.0)
```

Only the OpenTelemetry **API** is imported (`trace`, `Meter`). The comment above `LATENCY_BUCKETS` states the key constraint: **0.3 is the latency SLO threshold, so it must be an exact bucket boundary** for the SLI to be computable. The boundaries are 10 ms, 50 ms, 100 ms, 300 ms, 1 s and 3 s. The share of requests within 300 ms is the `le="0.3"` bucket divided by the count; if 0.3 were not an edge, the best Prometheus could do is interpolate.

```python
class OtelRequestMetrics:
    """``http_requests_total`` and ``http_request_duration_seconds``, labeled by route and status."""

    def __init__(self, meter: Meter) -> None:
        """Create the instruments on ``meter``."""
        self._requests = meter.create_counter("http_requests", unit="1", description="HTTP requests handled.")
        self._duration = meter.create_histogram(
            "http_request_duration",
            unit="s",
            description="HTTP request duration.",
            explicit_bucket_boundaries_advisory=LATENCY_BUCKETS,
        )

    def record(self, route: str, status: int, duration_seconds: float) -> None:
        """Count one request and observe its duration."""
        self._requests.add(1, {"route": route, "status": str(status)})
        self._duration.record(duration_seconds, {"route": route})
```

`OtelRequestMetrics` creates two instruments on a meter. `meter.create_counter("http_requests", unit="1", ...)` is a counter of requests; Prometheus will name it `http_requests_total`. `meter.create_histogram("http_request_duration", unit="s", ..., explicit_bucket_boundaries_advisory=LATENCY_BUCKETS)` is the latency histogram in seconds (exposed as `http_request_duration_seconds_bucket`, `_sum`, `_count`) with the chosen bucket edges. `record(route, status, duration_seconds)` adds one to the counter with the attributes `route` and `status` (as a string), and records the duration in the histogram with only the `route` attribute. The labels are bounded on purpose (a handful of routes, a handful of statuses): never a user id or an order id.

```python
class OtelAnnotator:
    """Sets attributes on whichever span is current."""

    def set(self, key: str, value: str) -> None:
        """Attach ``key`` = ``value`` to the current span."""
        trace.get_current_span().set_attribute(key, value)
```

`OtelAnnotator.set(key, value)` sets an attribute on whichever span is current (`trace.get_current_span().set_attribute(key, value)`). This is how the use case, which knows nothing about OpenTelemetry (it only sees the `Annotator` protocol), puts the email and card on the span. The leak is real, so the redaction is tested against it.

### The environment

```yaml
    command: ["opentelemetry-instrument", "slo-service"]
    environment:
      BIND_HOST: 0.0.0.0
      SERVICE_PORT: "8080"
      # Lab only: lets a request ask to fail or run slowly, so the scenarios can plant known incidents.
      ALLOW_SIMULATION: "true"
      OTEL_SERVICE_NAME: checkout
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-gateway:4318
      OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
      OTEL_TRACES_EXPORTER: otlp
      OTEL_METRICS_EXPORTER: otlp
      OTEL_LOGS_EXPORTER: otlp
      OTEL_METRIC_EXPORT_INTERVAL: "5000"
      OTEL_RESOURCE_ATTRIBUTES: service.version=0.1.0,deployment.environment=local
      OTEL_PYTHON_FLASK_EXCLUDED_URLS: healthz
      LOG_LEVEL: INFO
```

The service runs as `opentelemetry-instrument slo-service`. `OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-gateway:4318` sends everything to the gateway, by its Compose name, on the OTLP/HTTP port; `OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf`; all three exporters set to `otlp`; `OTEL_METRIC_EXPORT_INTERVAL: "5000"` exports metrics every 5 seconds (the default is 60); `OTEL_RESOURCE_ATTRIBUTES` adds `service.version` and `deployment.environment`; `OTEL_PYTHON_FLASK_EXCLUDED_URLS: healthz` keeps the health checks out of the traces; `OTEL_SERVICE_NAME: checkout` becomes the `service.name` that Loki turns into the label `service_name` and Tempo searches as `resource.service.name`. `ALLOW_SIMULATION: "true"` is the lab-only switch that lets requests ask to fail or run slowly.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_infrastructure.py -k "request_metrics or annotator" -q
```

Expected: `2 passed`.

```bash
curl -s -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[{"sku":"SKU-001","quantity":1}]}' -D - -o /dev/null | grep -i x-trace-id
```

Expected (needs `make up`): `X-Trace-Id: ` plus 32 hex characters.

```bash
sleep 12; curl -s "http://127.0.0.1:29090/api/v1/query?query=sum(http_requests_total)" | head -c 200
```

Expected: a result with a number: the counter reached Prometheus through the gateway.

```bash
curl -s "http://127.0.0.1:29090/api/v1/query?query=http_request_duration_seconds_bucket" | python3 -c "import json,sys; print(sorted({r['metric']['le'] for r in json.load(sys.stdin)['data']['result']}))"
```

Expected: `['+Inf', '0.01', '0.05', '0.1', '0.3', '1', '3']` (the edges, including `0.3`); the exact text of the numbers may be `1.0` and `3.0` depending on the exporter.

```bash
docker compose exec checkout env | grep ^OTEL_ | sort
```

Expected: the variables listed above.

## Mistakes people make

- **Using unbounded values as metric labels** (user ids, order ids). Every distinct value is a new time series. This repository puts only a four-value bucket on the metric and the order id on spans and logs, where unlimited values are fine.
- **Recording a measurement outside the span** and then wondering why the exemplar is missing. Exemplars need a span active at the moment of recording.
- **Forgetting that the SDK buffers.** A short-lived process must flush on exit.
- **Mixing up the API and the SDK.** Application code should only import the API.
- **Missing propagation.** If a client library is not instrumented, the trace breaks into two.

## Check yourself

1. What are the three signals? *Traces, metrics and logs.*
2. What is a span? *One timed step in a request, with a parent, attributes and a trace id shared with the rest of the request.*
3. What does `opentelemetry-instrument` do? *It configures the SDK from `OTEL_*` variables, enables the installed instrumentations and runs your program.*
4. How does the trace continue from the front service to the back service? *The `requests` instrumentation sends a `traceparent` header and the Flask instrumentation reads it.*
5. What is an exemplar? *A trace id attached to one histogram sample, so you can open the trace that produced it.*

## Further reading

- OpenTelemetry concepts: https://opentelemetry.io/docs/concepts/
- Python instrumentation (SDK 1.45, instrumentations 0.66b1): https://opentelemetry.io/docs/languages/python/
- Zero-code Python: https://opentelemetry.io/docs/zero-code/python/
- OTLP specification: https://opentelemetry.io/docs/specs/otlp/
- Environment variables: https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/
