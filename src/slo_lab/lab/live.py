"""Live alerting check: real traffic, a real outage, and the page arriving at the fake pager through Alertmanager."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from slo_lab.lab.traffic import TrafficPlan, TrafficResult, send_traffic

FAST_BURN_ALERT = "AvailabilityBudgetFastBurn"


class Pager(Protocol):
    """The fake pager calls the check makes."""

    def clear(self) -> None:
        """Forget every page."""
        ...

    def wait_for_page(self, alert: str, timeout_seconds: float) -> Mapping[str, Any] | None:
        """The page for ``alert``, or ``None`` after the timeout."""
        ...


@dataclass(frozen=True, slots=True)
class LiveOutcome:
    """What the live outage produced."""

    warm_requests: int
    outage_requests: int
    page: Mapping[str, Any] | None
    seconds_to_page: float | None


def run_live_outage(
    pager: Pager,
    service_url: str,
    warm_seconds: float = 120.0,
    outage_seconds: float = 180.0,
    rate_per_second: float = 10.0,
    send: Callable[[TrafficPlan], TrafficResult] = send_traffic,
    log: Callable[[str], None] = print,
) -> LiveOutcome:
    """Send healthy traffic, then fail every request for a few minutes, and wait for the fast-burn page."""
    pager.clear()
    log(f"warm-up: {warm_seconds:.0f} s of healthy traffic")
    warm = send(TrafficPlan(service_url, int(warm_seconds * rate_per_second), 1, error_share=0.0, slow_share=0.0,
                            personal_data_share=0.0, rate_per_second=rate_per_second))  # fmt: skip
    log(f"outage: every request fails for {outage_seconds:.0f} s")
    outage_started = time.time()
    outage = send(
        TrafficPlan(service_url, int(outage_seconds * rate_per_second), 2, rate_per_second=rate_per_second, outage=True)
    )
    page = pager.wait_for_page(FAST_BURN_ALERT, timeout_seconds=240.0)
    seconds = None if page is None else float(page["received_at"]) - outage_started
    return LiveOutcome(warm.sent, outage.sent, page, seconds)
