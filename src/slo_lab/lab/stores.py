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


def _get(url: str, **kwargs: Any) -> requests.Response:
    try:
        return requests.get(url, timeout=kwargs.pop("timeout", 30), **kwargs)
    except requests.RequestException as error:
        raise StoreError(f"GET {url} failed: {error}") from error


def metric_value(text: str, name: str) -> float:
    """Sum every sample of ``name`` in Prometheus text exposition (all label sets)."""
    pattern = re.compile(rf"^{re.escape(name)}(?:{{[^}}]*}})?\s+([0-9.eE+-]+)", re.MULTILINE)
    return sum(float(match) for match in pattern.findall(text))


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


def decode_id(value: str) -> str:
    """Tempo returns ids either as hex or as base64 bytes; normalize to lowercase hex."""
    stripped = value.strip()
    if len(stripped) in (16, 32) and all(char in "0123456789abcdefABCDEF" for char in stripped):
        return stripped.lower()
    return base64.b64decode(stripped + "=" * (-len(stripped) % 4)).hex()
