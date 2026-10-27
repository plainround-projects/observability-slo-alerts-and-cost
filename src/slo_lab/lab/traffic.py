"""Live traffic for the checkout service, with planted errors, slow requests and personal data."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

TEST_EMAIL = "test-user-redact-me@example.com"
# The card network's published test number; never a real card.
TEST_CARD = "4242424242424242"


@dataclass(frozen=True, slots=True)
class TrafficPlan:
    """How many requests to send and what share of them are planted cases."""

    base_url: str
    requests: int
    seed: int
    error_share: float = 0.03
    slow_share: float = 0.01
    personal_data_share: float = 0.05
    rate_per_second: float = 0.0
    concurrency: int = 16
    outage: bool = False


@dataclass(slots=True)
class TrafficResult:
    """The trace ids of each kind of request, as the client saw them in ``X-Trace-Id``."""

    error_traces: list[str] = field(default_factory=list)
    slow_traces: list[str] = field(default_factory=list)
    personal_data_traces: list[str] = field(default_factory=list)
    ordinary_traces: list[str] = field(default_factory=list)
    statuses: dict[int, int] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def all_traces(self) -> list[str]:
        """Every trace id seen."""
        return self.error_traces + self.slow_traces + self.personal_data_traces + self.ordinary_traces

    @property
    def sent(self) -> int:
        """Requests answered (any status)."""
        return sum(self.statuses.values())


def build_body(kind: str, index: int) -> dict[str, object]:
    """The JSON body for one request of the given kind."""
    body: dict[str, object] = {"items": [{"sku": f"SKU-{index % 40:03d}", "quantity": 1 + index % 3}]}
    if kind == "personal":
        body.update(customer_email=TEST_EMAIL, card_number=TEST_CARD)
    if kind == "error":
        body["simulate"] = "error"
    if kind == "slow":
        body["simulate"] = "slow"
    return body


def classify(rng: random.Random, plan: TrafficPlan) -> str:
    """Draw the kind of the next request."""
    if plan.outage:
        return "error"
    draw = rng.random()
    if draw < plan.error_share:
        return "error"
    if draw < plan.error_share + plan.slow_share:
        return "slow"
    if draw < plan.error_share + plan.slow_share + plan.personal_data_share:
        return "personal"
    return "ordinary"


def send_traffic(
    plan: TrafficPlan,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> TrafficResult:
    """Send the plan's requests (paced when ``rate_per_second`` is set) and collect the trace ids by kind."""
    rng = random.Random(plan.seed)
    kinds = [classify(rng, plan) for _ in range(plan.requests)]
    result = TrafficResult(started_at=time.time())
    lock = threading.Lock()
    local = threading.local()
    buckets = {
        "error": result.error_traces,
        "slow": result.slow_traces,
        "personal": result.personal_data_traces,
        "ordinary": result.ordinary_traces,
    }

    def send(item: tuple[int, str]) -> None:
        index, kind = item
        session = getattr(local, "session", None)
        if session is None:
            session = local.session = requests.Session()
        try:
            response = session.post(f"{plan.base_url}/checkout", json=build_body(kind, index), timeout=30)
            status, trace_id = response.status_code, response.headers.get("X-Trace-Id", "")
        except requests.RequestException:
            status, trace_id = 0, ""
        with lock:
            result.statuses[status] = result.statuses.get(status, 0) + 1
            if trace_id:
                buckets[kind].append(trace_id)

    begin = clock()
    with ThreadPoolExecutor(max_workers=plan.concurrency) as pool:
        futures = []
        for index, kind in enumerate(kinds):
            if plan.rate_per_second > 0:
                wait = begin + index / plan.rate_per_second - clock()
                if wait > 0:
                    sleep(wait)
            futures.append(pool.submit(send, (index, kind)))
        for future in futures:
            future.result()
    result.finished_at = time.time()
    return result
