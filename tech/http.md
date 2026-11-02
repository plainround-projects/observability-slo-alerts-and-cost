# HTTP (what the service, Prometheus, Alertmanager and the lab tools say to each other)

## What it is

HTTP is the protocol (the agreed rules) that programs use to talk over a network. A **client** sends a **request**; a **server** sends back a **response**. A request has a **method** (`GET` reads, `POST` creates or sends data), a **path** (`/order/3`), **headers** (extra information as `Name: value` lines) and sometimes a **body**. A response has a **status code**, headers and a body. This repository's services, the monitoring stores, Grafana and the Collector all speak HTTP.

## Why this repository uses it

The SLIs are counts of HTTP requests and their statuses, so the protocol is the subject: the **availability** SLI divides requests that did not end in a 5xx by all valid requests, and the **latency** SLI counts requests that finished within 300 ms. Every arrow of the lab is HTTP too: the traffic generator posts to the service, the service sends OTLP to the gateway, Prometheus scrapes the gateway and sends alerts to Alertmanager, Alertmanager posts a webhook to the pager, and Grafana and the lab tools query the stores.

## Where it appears

| Where | The HTTP involved |
| --- | --- |
| `interfaces/service_app.py` | `POST /checkout` and its statuses (200, 400, 500) and the `X-Trace-Id` header. |
| `lab/traffic.py` | The load: POSTs with JSON bodies and timeouts. |
| `interfaces/pager_app.py` | `POST /webhook` from Alertmanager; `GET`/`DELETE /pages`. |
| `observability/alertmanager/alertmanager.yml` | The webhook receiver. |
| `observability/prometheus/prometheus.yml` | Scrapes and the Alertmanager address. |
| `infrastructure/prometheus_http.py` | `GET /api/v1/query_range` in chunks. |
| `observability/collector/gateway.yaml` | OTLP/HTTP receiver on 4318. |

## How it works underneath

The client opens a **TCP connection** to the server's address and **port** (a number that picks one program on that machine, such as 8080). It writes the request as text lines. The server reads them, does the work and writes the response. Status codes are grouped by first digit: `2xx` success (`200 OK`), `3xx` redirect, `4xx` the client made a mistake (`400 Bad Request`, `404 Not Found`), `5xx` the server failed (`500`, `502 Bad Gateway`, `503 Service Unavailable`, `504 Gateway Timeout`).

A **timeout** is how long the client waits before giving up. A **connection timeout** applies to opening the connection; a **read timeout** applies to waiting for the response bytes. A **retry** sends the same request again after a failure; it is only safe for requests that change nothing (`GET` is idempotent).

The address `127.0.0.1` (also `localhost`) is the loopback address: it always means "this same machine". `0.0.0.0` as a *listen* address means "all network interfaces of this machine".

A **health endpoint** (here `/healthz`) is a path that answers 200 when the service can do its job; orchestrators and Docker call it repeatedly.

A **header** can carry the trace identity between services. `traceparent` is the W3C standard header that OpenTelemetry uses to pass the trace id and the parent span id from the caller to the callee.

## Walkthrough of this repository's use

**Statuses are the SLI.** The service answers 200 for a good checkout, 400 for an invalid body (the client's mistake: still a valid request for the SLI, and counted as good because it is not a 5xx) and 500 for a failed checkout (bad):

```python
    @app.errorhandler(InvalidCheckoutError)
    def invalid(error: InvalidCheckoutError) -> tuple[Response, int]:
        return _problem(400, "invalid_checkout", str(error))

    @app.errorhandler(CheckoutFailedError)
    def failed(error: CheckoutFailedError) -> tuple[Response, int]:
        return _problem(500, "checkout_failed", str(error))

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> tuple[Response, int]:
        return _problem(error.code or 500, (error.name or "error").lower().replace(" ", "_"), error.description or "")
```

The availability SLI is `status!~"5.."` over `route!="/healthz"`: only the 5xx count as bad, and health checks are not valid requests (they always succeed and would dilute the ratio).

**The webhook.** Alertmanager sends `POST /webhook` with a JSON body; the pager answers 200 with how many alerts it recorded, or 400 for something that is not a webhook:

```python
    @app.post("/webhook")
    def webhook() -> tuple[Response, int]:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
            return jsonify({"error": "expected an Alertmanager webhook payload"}), 400
        now = float(clock())
        for alert in body["alerts"]:
            if alert.get("status") != "firing":
                continue
            labels = alert.get("labels", {})
            store.add(
                Page(str(labels.get("alertname", "unknown")), str(labels.get("severity", "page")), timedelta()), now
            )
        return jsonify({"recorded": len(body["alerts"])}), 200
```

**Timeouts and retries.** The clients of the stores wait up to 120 seconds and try three times; a single request to the service waits 30 s; the range queries wait 300 s:

```python
def _get(url: str, **kwargs: Any) -> requests.Response:
    """GET with a generous timeout and three tries, because the stores can be slow on a busy machine."""
    timeout = kwargs.pop("timeout", 120)
    for attempt in range(3):
        try:
            return requests.get(url, timeout=timeout, **kwargs)
        except requests.RequestException as error:
            if attempt == 2:
                raise StoreError(f"GET {url} failed: {error}") from error
            time.sleep(5.0)
    raise AssertionError("unreachable")  # pragma: no cover
```

**Chunked range queries.** Prometheus refuses a range query that would return more than 11,000 points per series, so the evaluator asks for at most 10,000 at a time:

```python
    def true_at(self, expression: str, start: float, end: float, step_seconds: float) -> set[float]:
        """Evaluation times at which the expression returned at least one series, queried in chunks."""
        times: set[float] = set()
        chunk = self.MAX_POINTS * step_seconds
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + chunk - step_seconds, end)
            params = {
                "query": expression,
                "start": f"{cursor:.0f}",
                "end": f"{chunk_end:.0f}",
                "step": f"{step_seconds:.0f}",
            }
            try:
                response = self._session.get(f"{self._base}/api/v1/query_range", params=params, timeout=300)
            except requests.RequestException as error:
                raise PrometheusError(f"range query failed: {error}") from error
            if response.status_code != 200:
                raise PrometheusError(f"Prometheus answered {response.status_code}: {response.text[:300]}")
            for series in response.json()["data"]["result"]:
                times.update(float(when) for when, _ in series["values"])
            cursor = chunk_end + step_seconds
        return times
```

**A trace across the boundary.** The service is the only instrumented hop here, so the trace starts at the service's server span; a client may send a `traceparent` header to continue its own trace (see `opentelemetry.md`).

## Try it yourself

```bash
curl -s -i -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[{"sku":"SKU-001","quantity":1}]}' | head -10
```

Expected (needs `make up`): `HTTP/1.1 200 OK`, headers including `Content-Type: application/json`, `X-Trace-Id` and `Server: slo-lab`, then the body.

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[]}'
```

Expected: `400`.

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[{"sku":"SKU-001","quantity":1}],"simulate":"error"}'
```

Expected: `500`.

```bash
curl -s -X POST http://127.0.0.1:28084/webhook -H 'Content-Type: application/json' -d '{"nope":1}' -w "\n%{http_code}\n"
```

Expected: an error JSON and `400`.

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://127.0.0.1:23133/
```

Expected: `200` and a few milliseconds: the gateway's health endpoint.

## Mistakes people make

- **No timeout.** A call without a timeout can wait forever. Every client call here sets one.
- **Retrying non-idempotent calls.** Retrying a payment could charge twice.
- **Using `0.0.0.0` as a destination.** It is only meaningful as a listen address.
- **Confusing 502, 503 and 504.** 502: a gateway got a bad answer; 503: not ready or overloaded; 504: the upstream did not answer in time.

## Check yourself

1. What are the parts of an HTTP request? *Method, path, headers, optional body.*
2. What does a 404 mean? *The thing asked for was not found.*
3. Which statuses count as server failures? *The 5xx family.*
4. What does listening on `127.0.0.1` allow? *Only connections from the same machine.*
5. Why is `GET` safe to retry? *It does not change anything on the server.*

## Further reading

- MDN, HTTP overview: https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview
- HTTP semantics (RFC 9110): https://www.rfc-editor.org/rfc/rfc9110
- W3C Trace Context (the `traceparent` header): https://www.w3.org/TR/trace-context/
