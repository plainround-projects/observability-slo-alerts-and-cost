# requests and urllib3

## What it is

`requests` is the most widely used Python library for making HTTP calls. `requests.get(url)` sends a GET request and returns a `Response` with `.status_code`, `.headers`, `.text` and `.json()`. A **Session** keeps connections open between calls (faster) and lets you set options once. `urllib3` is the lower-level library that `requests` uses underneath, and `urllib.request` from the standard library is a simpler client that needs no installation. This repository uses requests 2.34 and urllib3 2.8.

## Why this repository uses it

Three jobs. The **traffic generator** posts planted checkouts to the service. The **range evaluator** asks the replay Prometheus the question the whole lesson rests on: at which minutes of the week was this alert expression true? The **store clients** read Tempo, Loki and the fake pager to count what each stored. All of them are plain HTTP with timeouts.

## Where it appears

| Path | What it does with requests |
| --- | --- |
| `src/slo_lab/infrastructure/prometheus_http.py` | `PrometheusRangeEvaluator`: range queries over the replayed week, in chunks. |
| `src/slo_lab/lab/stores.py` | Clients for Tempo, Loki and the pager, with retries. |
| `src/slo_lab/lab/traffic.py` | One session per worker thread (see `python-threads-and-signals.md`). |
| `tests/unit/infrastructure/test_infrastructure.py` | Tests the evaluator with a fake session. |

## How it works underneath

A Session owns a **connection pool** per host. The first call opens a TCP connection; later calls reuse it (HTTP keep-alive). An **adapter** (`HTTPAdapter`) is mounted for a URL prefix such as `http://`, and decides how connections are made and how failures are retried. A `Retry` object from urllib3 describes the policy: `total` attempts, which status codes to retry (`status_forcelist`), which HTTP methods are safe to repeat (`allowed_methods`), `backoff_factor` (the pause grows: roughly `backoff_factor * 2^(attempt-1)` seconds), and `read=False` (do not retry when the server already received the request and the wait for its answer ran out).

`timeout=5.0` is applied to the connect step and to each wait for data. It is **not** a limit for the whole request. Without a timeout, `requests` waits forever. Failures raise exceptions that derive from `requests.RequestException`; `requests.Timeout` is the timeout one.

`response.json()` parses the body as JSON and raises `ValueError` (a `JSONDecodeError`) when the body is not JSON.

The OpenTelemetry requests instrumentation patches `Session.send`, so each outgoing call becomes a client span and the `traceparent` header is added.

## Walkthrough of this repository's use

### `infrastructure/prometheus_http.py`

```python
"""Evaluate PromQL over stored history through Prometheus's HTTP API."""

from __future__ import annotations

from typing import Any, Protocol

import requests


class HttpGetter(Protocol):
    """The one ``requests.Session`` method this client uses, so tests can stand in for the network."""

    def get(self, url: str, params: Any = ..., timeout: float = ...) -> Any:
        """Issue a GET request."""
        ...


class PrometheusError(RuntimeError):
    """Prometheus answered with an error or something this client cannot read."""
```

`HttpGetter` is a `Protocol` describing the one method of `requests.Session` used here, `get(url, params, timeout)`; a test passes a fake that has only that method. `Any` is used for the return type because the fake and the real response differ. `PrometheusError` is this module's error.

```python
class PrometheusRangeEvaluator:
    """Runs an alert expression as a range query; a timestamp with any result is a timestamp where the alert is true."""

    # Prometheus refuses range queries that would return more than 11,000 points per series.
    MAX_POINTS = 10_000

    def __init__(self, base_url: str, session: HttpGetter | None = None) -> None:
        """Take the base URL of the (replay) Prometheus."""
        self._base = base_url.rstrip("/")
        self._session = session or requests.Session()

    def ready(self) -> bool:
        """True when Prometheus reports itself healthy."""
        try:
            return self._session.get(f"{self._base}/-/healthy", timeout=5).status_code == 200
        except requests.RequestException:
            return False
```

`PrometheusRangeEvaluator` implements the `RangeEvaluator` port of the application layer. `MAX_POINTS = 10_000` is a class constant; the comment explains: Prometheus refuses a range query that would return more than 11,000 points per series. `ready` calls `/-/healthy` and answers False on any network error.

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

`true_at(expression, start, end, step_seconds)` evaluates an alert expression over the replayed week and returns the set of times at which it returned at least one series (which, for an alert expression, means "the condition is true now"). A week at one-minute steps is 10,080 points (just over `MAX_POINTS`), and at 30-second steps 20,160, so the range is cut into **chunks** of at most `MAX_POINTS` steps: two chunks for the replay's one-minute steps. For each chunk it sends `GET /api/v1/query_range` with the query, `start`, `end` and `step`, a 300-second timeout, and checks the status. Each result series has `values` as `[timestamp, "value"]` pairs; the timestamps of all series are added to the set (`times.update(float(when) for when, _ in series["values"])`; `_` is a name for a value you ignore). `cursor = chunk_end + step_seconds` moves to the next chunk. The unit test checks that a week at 30-second steps is queried in exactly 3 chunks, each under 11,000 points.

```python
    def scalar(self, expression: str, default: float = 0.0) -> float:
        """One value of an instant query (at most one series)."""
        response = self._session.get(f"{self._base}/api/v1/query", params={"query": expression}, timeout=30)
        if response.status_code != 200:
            raise PrometheusError(f"Prometheus answered {response.status_code}: {response.text[:300]}")
        result = response.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else default
```

`scalar` runs an instant query (`/api/v1/query`) expected to return at most one series and returns its value, or a default.

### `lab/stores.py`: Tempo, Loki and the pager

```python
"""Small HTTP clients for Tempo, Loki, the Collector's counters and the fake pager."""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import requests


class StoreError(RuntimeError):
    """A backend answered with an error or something this client cannot read."""


@dataclass(frozen=True, slots=True)
class LogLine:
    """One log entry from Loki."""

    timestamp_ns: int
    line: str
```

`StoreError` is the one error; `LogLine` is a Loki entry (time in nanoseconds and the text).

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

`_get` makes the call with a 120-second timeout and **three tries**, five seconds apart. The docstring says why: the stores can be slow on a busy machine. After the third failure it raises `StoreError`. The last line, `raise AssertionError("unreachable")`, satisfies the type checker (the loop always returns or raises) and is excluded from coverage with a comment.

```python
def metric_value(text: str, name: str) -> float:
    """Sum every sample of ``name`` in Prometheus text exposition (all label sets)."""
    pattern = re.compile(rf"^{re.escape(name)}(?:{{[^}}]*}})?\s+([0-9.eE+-]+)", re.MULTILINE)
    return sum(float(match) for match in pattern.findall(text))
```

`metric_value(text, name)` sums every sample of a metric in a Prometheus text page (explained with the regular expression in `regular-expressions.md`). It is how the lab reads Tempo's and Loki's own counters of accepted bytes from their `/metrics` pages.

```python
class TempoClient:
    """Trace lookup, TraceQL search and the bytes Tempo has accepted."""

    def __init__(self, base_url: str) -> None:
        """Take the Tempo base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Tempo reports itself ready."""
        response = _get(f"{self._base}/ready")
        return response.status_code == 200 and "ready" in response.text

    def bytes_received(self) -> float:
        """Total span bytes the distributor has accepted since Tempo started."""
        return metric_value(_get(f"{self._base}/metrics").text, "tempo_distributor_bytes_received_total")

    def spans_received(self) -> float:
        """Total spans the distributor has accepted since Tempo started."""
        return metric_value(_get(f"{self._base}/metrics").text, "tempo_distributor_spans_received_total")

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

`TempoClient`: `ready`; `bytes_received` and `spans_received` read `tempo_distributor_bytes_received_total` and `tempo_distributor_spans_received_total` from `/metrics` (the volume the gateway sent through to storage); `trace_json(id)` fetches `/api/traces/<id>` as JSON text, or `None` on a 404; `search(traceql, start, end, limit=5000)` calls `/api/search` and returns trace ids.

```python
class LokiClient:
    """LogQL range queries and the bytes Loki has accepted."""

    def __init__(self, base_url: str) -> None:
        """Take the Loki base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Loki reports itself ready."""
        response = _get(f"{self._base}/ready")
        return response.status_code == 200 and "ready" in response.text

    def bytes_received(self) -> float:
        """Total log bytes the distributor has accepted since Loki started."""
        return metric_value(_get(f"{self._base}/metrics").text, "loki_distributor_bytes_received_total")

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

`LokiClient`: `ready`, `bytes_received` (`loki_distributor_bytes_received_total`) and `query(logql, start, end, limit)`, a LogQL range query with times in nanoseconds, `direction: forward` (oldest first); the result lines are flattened from the streams and sorted by time.

```python
class PagerClient:
    """The fake pager's HTTP API."""

    def __init__(self, base_url: str) -> None:
        """Take the pager base URL."""
        self._base = base_url.rstrip("/")

    def pages(self) -> list[Mapping[str, Any]]:
        """Every page recorded so far."""
        response = _get(f"{self._base}/pages")
        if response.status_code != 200:
            raise StoreError(f"pager answered {response.status_code}")
        pages: list[Mapping[str, Any]] = response.json()
        return pages

    def clear(self) -> None:
        """Forget every page."""
        requests.delete(f"{self._base}/pages", timeout=10)

    def wait_for_page(self, alert: str, timeout_seconds: float) -> Mapping[str, Any] | None:
        """Poll until a page for ``alert`` arrives, or return ``None`` after the timeout."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for page in self.pages():
                if page["alert"] == alert:
                    return page
            time.sleep(3.0)
        return None
```

`PagerClient`: `pages()` lists the fake pager's records, `clear()` deletes them, and `wait_for_page(alert, timeout_seconds)` polls every three seconds until a page for that alert exists or the deadline passes (returning `None`).

```python
def decode_id(value: str) -> str:
    """Tempo returns ids either as hex or as base64 bytes; normalize to lowercase hex."""
    stripped = value.strip()
    if len(stripped) in (16, 32) and all(char in "0123456789abcdefABCDEF" for char in stripped):
        return stripped.lower()
    return base64.b64decode(stripped + "=" * (-len(stripped) % 4)).hex()
```

`decode_id` normalizes Tempo's ids (hex or base64) to lowercase hex, as in the sister tools.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_infrastructure.py -k "true_at or chunks or errors or scalar" -q
```

Expected: `4 passed`.

```bash
.venv/bin/python -c "
from slo_lab.lab.stores import TempoClient, LokiClient
print(TempoClient('http://127.0.0.1:23200').bytes_received(), LokiClient('http://127.0.0.1:23100').bytes_received())"
```

Expected (needs `make up`): two numbers (bytes accepted since each store started), growing as traffic flows.

```bash
curl -s "http://127.0.0.1:29091/api/v1/query_range?query=up&start=0&end=60&step=60" | head -c 200
```

Expected (needs the replay Prometheus): `{"status":"success",...}` with an empty result for a time before the data.

```bash
curl -s -X POST http://127.0.0.1:28083/checkout -H 'Content-Type: application/json' -d '{"items":[{"sku":"SKU-001","quantity":1}]}' -D - -o /dev/null | grep -i x-trace-id
```

Expected: the trace id the traffic generator reads from the same header.

```bash
curl -s http://127.0.0.1:23200/metrics | grep ^tempo_distributor_bytes_received_total
```

Expected: one line with the counter's current value.

## Mistakes people make

- **No timeout.** The most common cause of a hung service.
- **Retrying a read timeout.** The slow server is already busy; a retry doubles the work. `read=False` avoids it.
- **Creating a new Session per call**, losing connection reuse.
- **Catching `Exception` instead of `requests.RequestException`.**
- **Not checking `status_code` before `.json()`.**

## Check yourself

1. What does a Session add over `requests.get`? *Connection reuse and shared settings.*
2. What does `timeout=5.0` limit? *The connect wait and each wait for data, not the total time.*
3. Which exception means "took too long"? *`requests.Timeout`.*
4. Why is only `GET` retried here? *It is idempotent: repeating it changes nothing.*
5. What does `raise_on_status=False` change? *After the retries are used up, the last response is returned instead of an exception.*

## Further reading

- requests 2.34 documentation: https://requests.readthedocs.io/en/stable/
- Advanced usage (sessions, adapters, timeouts): https://requests.readthedocs.io/en/stable/user/advanced/
- urllib3 `Retry`: https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html#urllib3.util.Retry
- `urllib.request`: https://docs.python.org/3.12/library/urllib.request.html
