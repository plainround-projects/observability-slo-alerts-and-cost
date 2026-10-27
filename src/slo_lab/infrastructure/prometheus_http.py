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

    def scalar(self, expression: str, default: float = 0.0) -> float:
        """One value of an instant query (at most one series)."""
        response = self._session.get(f"{self._base}/api/v1/query", params={"query": expression}, timeout=30)
        if response.status_code != 200:
            raise PrometheusError(f"Prometheus answered {response.status_code}: {response.text[:300]}")
        result = response.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else default
