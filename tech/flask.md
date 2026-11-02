# Flask

## What it is

Flask is a small Python library for writing web applications. You create an `app` object, and tell it which Python function answers which URL path and HTTP method. Flask does the HTTP details: it parses the request, calls your function, turns the return value into a response. It is called a **micro-framework** because it gives you routing, request and response objects and error handling, and leaves everything else (database, login) to other libraries. This repository uses Flask 3.1.

## Why this repository uses it

Two small web applications: the **checkout service**, whose requests the SLOs are about, and the **fake pager**, which records what Alertmanager sends. Flask keeps both to a page of code. The checkout app is also where the SLI metrics are recorded: a hook around every request counts it by route and status and times it, which is exactly what the availability and latency SLOs divide.

## Where it appears

| Path | What it does with Flask |
| --- | --- |
| `src/slo_lab/interfaces/service_app.py` | The checkout service: `POST /checkout`, `GET /healthz`, request counting and timing, error handlers. |
| `src/slo_lab/interfaces/service_main.py` | Builds the use case, the metrics and the app; runs it. |
| `src/slo_lab/interfaces/pager_app.py`, `pager_main.py` | The fake pager (explained line by line in `alertmanager.md`). |
| `tests/unit/interfaces/test_apps.py` | Calls both apps through Flask's test client. |

## How it works underneath

A Flask app is a **WSGI application**. WSGI (Web Server Gateway Interface) is the Python standard for the contract between a web server and an application: the server calls the app as a function with the request data and a callback, and the app returns the response body. Flask itself does not listen on a port; a WSGI server (here, waitress) does, and calls the Flask app for each request.

`@app.get("/order/<int:order_id>")` registers a **route**. The `<int:order_id>` part is a **converter**: it captures that part of the path, converts it to an `int`, and passes it as the argument `order_id`. A path such as `/order/abc` does not match, so Flask answers 404.

A function returns a `Response`, usually made with `jsonify(...)`, which turns a Python dict into a JSON body with the right `Content-Type` header. A return value of `(response, 503)` sets the status code.

`@app.after_request` registers a function that runs on every response before it is sent, and may change it (here: add a header). `@app.errorhandler(SomeError)` registers a function that turns that exception into a response. Flask picks the most specific handler: a handler for `OrderNotFoundError` wins over one for `Exception`.

`app.test_client()` gives a fake client that calls the app directly, without a network, which is how the unit tests exercise the routes.

The OpenTelemetry Flask instrumentation wraps the app so each request becomes a server span, and reads the incoming `traceparent` header.

Two more Flask features this app uses: `g` is an object that lives for one request (a place to store values between the "before" and "after" hooks of the same request), and `@app.before_request` registers a function that runs before every route.

## Walkthrough of this repository's use

### `interfaces/service_app.py`

```python
"""The checkout service's HTTP surface."""

from __future__ import annotations

import logging
import time
from typing import Any

from flask import Flask, Response, g, jsonify, request
from opentelemetry import trace
from werkzeug.exceptions import HTTPException

from slo_lab.application.place_checkout import PlaceCheckout
from slo_lab.domain.checkout import (
    CheckoutFailedError,
    CheckoutRequest,
    InvalidCheckoutError,
    OrderLine,
    Simulation,
)
from slo_lab.infrastructure.otel_telemetry import OtelRequestMetrics

_log = logging.getLogger(__name__)
```

`g`, `jsonify` and `request` come from Flask. The module imports the use case, the domain request types and errors, and `OtelRequestMetrics` (the adapter that counts and times).

```python
def _problem(status: int, code: str, detail: str) -> tuple[Response, int]:
    return jsonify({"error": code, "detail": detail}), status
```

`_problem(status, code, detail)` builds the one JSON shape of every error: `{"error": "<code>", "detail": "<text>"}`.

```python
def parse_checkout(body: Any, allow_simulation: bool) -> CheckoutRequest:
    """Turn a decoded JSON body into a validated request, raising ``InvalidCheckoutError``."""
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise InvalidCheckoutError("body must be an object with an items list")
    try:
        lines = tuple(OrderLine(str(item["sku"]), int(item["quantity"])) for item in body["items"])
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidCheckoutError("each item needs a sku and an integer quantity") from error
    simulation = Simulation.NONE
    if "simulate" in body:
        if not allow_simulation:
            raise InvalidCheckoutError("simulation is disabled")
        try:
            simulation = Simulation(body["simulate"])
        except ValueError as error:
            raise InvalidCheckoutError("simulate must be none, error or slow") from error
    email, card = body.get("customer_email"), body.get("card_number")
    if not all(value is None or isinstance(value, str) for value in (email, card)):
        raise InvalidCheckoutError("customer_email and card_number must be strings")
    return CheckoutRequest(lines, email, card, simulation)
```

`parse_checkout(body, allow_simulation)` turns the decoded JSON body into a validated `CheckoutRequest`. The body must be an object with an `items` list; each item needs a `sku` and an integer `quantity` (a `KeyError`, `TypeError` or `ValueError` while reading becomes one `InvalidCheckoutError`, with `from error`). A `simulate` key is accepted only when the lab allows simulation (`ALLOW_SIMULATION`), and its value must be `none`, `error` or `slow`. The email and card, if present, must be strings (`all(value is None or isinstance(value, str) for value in (email, card))`). Because the function takes `Any` (JSON can be anything), it checks every assumption by hand: the validation sits at the edge of the system.

```python
def create_service_app(place_checkout: PlaceCheckout, metrics: OtelRequestMetrics, allow_simulation: bool) -> Flask:
    """Build the Flask app: ``POST /checkout`` and ``GET /healthz``; every request is counted and timed."""
    app = Flask("checkout")

```

`create_service_app` is a factory taking the use case, the metrics adapter and the simulation switch.

```python
    @app.before_request
    def start_timer() -> None:
        g.started = time.monotonic()

    @app.after_request
    def record(response: Response) -> Response:
        route = request.url_rule.rule if request.url_rule else "unmatched"
        metrics.record(route, response.status_code, time.monotonic() - g.get("started", time.monotonic()))
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            response.headers["X-Trace-Id"] = trace.format_trace_id(context.trace_id)
        return response
```

`@app.before_request` stores the start time (monotonic clock) in `g.started`. `@app.after_request` runs for **every** response: it finds the route as the URL **rule** (`request.url_rule.rule`, such as `/checkout`, or `"unmatched"` when no route matched, so the label stays bounded and an attacker's random URLs do not create metric series), records the request with its status and duration (`g.get("started", ...)` has a default in case the before hook did not run), and sets the `X-Trace-Id` response header from the current span. This hook is the source of `http_requests_total` and `http_request_duration_seconds`, the SLI metrics.

```python
    @app.post("/checkout")
    def checkout() -> tuple[Response, int]:
        receipt = place_checkout.execute(parse_checkout(request.get_json(silent=True), allow_simulation))
        return jsonify({"reference": receipt.reference, "items": receipt.item_count}), 200

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok"})
```

`POST /checkout` parses the JSON (`request.get_json(silent=True)` returns `None` instead of raising when the body is not JSON), runs the use case and answers `{"reference": ..., "items": ...}`. `GET /healthz` answers `{"status": "ok"}`; the OpenTelemetry Flask instrumentation is told to ignore it and the SLO queries exclude `/healthz` from valid requests.

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

    @app.errorhandler(Exception)
    def unexpected(error: Exception) -> tuple[Response, int]:
        _log.exception("unhandled error")
        return _problem(500, "internal_error", "the service failed")

    return app
```

Four error handlers: `InvalidCheckoutError` becomes `400 invalid_checkout`; `CheckoutFailedError` (the simulated server failure) becomes `500 checkout_failed`, which the availability SLI counts as bad; Flask's own `HTTPException` (404, 405) becomes the same JSON shape; any other `Exception` is logged with its traceback and answered with a generic `500 internal_error`. Note that the `after_request` hook runs for these error responses too, so a failed request is counted with its 500 status.

### `interfaces/service_main.py`: the composition root

```python
"""Composition root of the checkout service."""

from __future__ import annotations

import os

from opentelemetry import metrics

from slo_lab.application.place_checkout import PlaceCheckout
from slo_lab.infrastructure.logging_setup import configure_logging
from slo_lab.infrastructure.otel_telemetry import OtelAnnotator, OtelRequestMetrics
from slo_lab.infrastructure.runtime import SequentialReferences, SleepDelay
from slo_lab.infrastructure.settings import ServiceSettings
from slo_lab.interfaces.server import run_server
from slo_lab.interfaces.service_app import create_service_app


def main() -> None:
    """Start the checkout service."""
    settings = ServiceSettings.from_env(os.environ)
    configure_logging(settings.log_level, settings.service_name)
    use_case = PlaceCheckout(OtelAnnotator(), SleepDelay(), SequentialReferences(), settings.slow_seconds)
    request_metrics = OtelRequestMetrics(metrics.get_meter("slo_lab.checkout"))
    app = create_service_app(use_case, request_metrics, settings.allow_simulation)
    run_server(app, settings.bind_host, settings.port)
```

It reads `ServiceSettings`, configures logging, builds the use case with its four collaborators (`OtelAnnotator`, `SleepDelay`, `SequentialReferences`, the slow time), builds the metrics adapter from a meter named `slo_lab.checkout` (see `opentelemetry.md`), creates the app and runs it with `run_server`. Started under `opentelemetry-instrument`, the SDK is configured before `main()` runs.

### The fake pager

`interfaces/pager_app.py` and `pager_main.py` are quoted and explained in `alertmanager.md`; they use the same Flask features: routes (`@app.post`, `@app.get`, `@app.delete`), `jsonify`, `request.get_json(silent=True)`.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/interfaces/test_apps.py -q
```

Expected: all pass (the service and pager tests).

```bash
curl -s -i -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[{"sku":"SKU-001","quantity":2}]}'
```

Expected (needs `make up`): `200`, an `X-Trace-Id` header and `{"items":2,"reference":"ord-000001"}` (the reference counts up).

```bash
curl -s -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[{"sku":"SKU-001","quantity":2}],"simulate":"error"}' -w "\n%{http_code}\n"
```

Expected: `{"detail":"the payment provider did not answer","error":"checkout_failed"}` and `500`: the lab allows simulation.

```bash
curl -s -X POST http://127.0.0.1:28083/checkout -d 'nope' -w "\n%{http_code}\n"
```

Expected: `invalid_checkout` and `400`.

```bash
curl -s "http://127.0.0.1:29090/api/v1/query?query=http_requests_total" | python3 -c "import json,sys; print(sorted({(r['metric']['route'], r['metric']['status']) for r in json.load(sys.stdin)['data']['result']}))"
```

Expected (after the requests above, about ten seconds later): pairs such as `('/checkout', '200')` and `('/checkout', '500')`.

## Mistakes people make

- **Running `app.run()` in production.** That is the development server. This repository uses waitress.
- **Handling every error in every route.** One `errorhandler` per error type gives one consistent answer.
- **Returning HTML error pages from an API.** The `HTTPException` handler here turns Flask's own errors (404, 405) into JSON.
- **Global mutable state** shared between requests. The app here only reads objects built at start.

## Check yourself

1. What is WSGI? *The Python standard interface between a web server and an application.*
2. What does `<int:order_id>` do? *Captures the path part, converts it to an int and passes it to the function.*
3. What does `after_request` do? *Runs on each response before it is sent, and can change it.*
4. How does the test client differ from a real client? *It calls the app in-process; there is no network.*
5. Why is `app.run()` not used? *It is the development server, not meant for production.*

## Further reading

- Flask 3.1 documentation: https://flask.palletsprojects.com/en/3.1.x/
- Routing: https://flask.palletsprojects.com/en/3.1.x/quickstart/#routing
- Error handlers: https://flask.palletsprojects.com/en/3.1.x/errorhandling/
- Testing: https://flask.palletsprojects.com/en/3.1.x/testing/
- WSGI: https://peps.python.org/pep-3333/
