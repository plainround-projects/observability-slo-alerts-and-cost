"""OpenTelemetry adapters: request metrics for the SLIs and span annotation."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.metrics import Meter

# 0.3 is the latency SLO threshold, so it must be an exact bucket boundary for the SLI to be computable.
LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.3, 1.0, 3.0)


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


class OtelAnnotator:
    """Sets attributes on whichever span is current."""

    def set(self, key: str, value: str) -> None:
        """Attach ``key`` = ``value`` to the current span."""
        trace.get_current_span().set_attribute(key, value)
