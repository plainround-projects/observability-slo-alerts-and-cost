"""Tail sampling and redaction, measured: the same traffic through a raw gateway and a protected one."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from slo_lab.lab.traffic import TEST_CARD, TEST_EMAIL, TrafficPlan, TrafficResult, send_traffic

RAW_CONFIG = "./observability/collector/gateway.raw.yaml"
PROTECTED_CONFIG = "./observability/collector/gateway.yaml"
SETTLE_SECONDS = 45.0
SEARCH_LIMIT = 20_000
SAMPLE_BODIES = 10
ORDINARY_SAMPLE = 300
EMAIL_FRAGMENT = "test-user-redact-me"


class GatewaySwitch(Protocol):
    """Recreates the gateway Collector with another config."""

    def use_gateway_config(self, config_path: str) -> None:
        """Switch config."""
        ...


class TraceStore(Protocol):
    """The Tempo calls the scenario makes."""

    def bytes_received(self) -> float:
        """Bytes accepted so far."""
        ...

    def trace_json(self, trace_id: str) -> str | None:
        """The stored trace, or ``None``."""
        ...

    def search(self, traceql: str, start: float, end: float, limit: int = ...) -> list[str]:
        """Trace ids matching a TraceQL query."""
        ...


class LogStore(Protocol):
    """The Loki calls the scenario makes."""

    def bytes_received(self) -> float:
        """Bytes accepted so far."""
        ...

    def query(self, logql: str, start: float, end: float, limit: int = ...) -> Sequence[object]:
        """Lines matching a LogQL query."""
        ...


@dataclass(frozen=True, slots=True)
class PassResult:
    """What one gateway configuration did with the traffic."""

    config: str
    sent: int
    trace_bytes: float
    log_bytes: float
    stored_traces: int
    errors_sent: int
    errors_found: int
    slow_sent: int
    slow_found: int
    ordinary_sent: int
    ordinary_found: int
    email_in_traces: int
    card_in_traces: int
    email_in_logs: int
    card_in_logs: int

    @property
    def ordinary_kept_share(self) -> float:
        """The share of ordinary (fast, successful) traces that were stored."""
        return self.ordinary_found / self.ordinary_sent if self.ordinary_sent else 0.0


@dataclass(frozen=True, slots=True)
class VolumeReport:
    """The raw and protected passes side by side."""

    raw: PassResult
    protected: PassResult

    @property
    def trace_reduction(self) -> float:
        """Share of trace bytes not stored with the protected gateway."""
        return 1 - self.protected.trace_bytes / self.raw.trace_bytes if self.raw.trace_bytes else 0.0


class VolumeScenario:
    """Runs the same seeded traffic through both gateway configurations and inspects every store."""

    def __init__(
        self,
        compose: GatewaySwitch,
        tempo: TraceStore,
        loki: LogStore,
        service_url: str,
        requests: int,
        seed: int = 4242,
        send: Callable[[TrafficPlan], TrafficResult] = send_traffic,
        log: Callable[[str], None] = print,
        settle_seconds: float = SETTLE_SECONDS,
        gateway_ready: Callable[[], bool] = lambda: True,
        ready_timeout_seconds: float = 900.0,
    ) -> None:
        """Take the collaborators and the size of the traffic."""
        self._compose = compose
        self._tempo = tempo
        self._loki = loki
        self._url = service_url
        self._requests = requests
        self._seed = seed
        self._send = send
        self._log = log
        self._settle = settle_seconds
        self._gateway_ready = gateway_ready
        self._ready_timeout = ready_timeout_seconds

    def run(self) -> VolumeReport:
        """Run the raw pass, then the protected pass."""
        return VolumeReport(self._pass(RAW_CONFIG), self._pass(PROTECTED_CONFIG))

    def _wait_for_gateway(self) -> None:
        """The Collector image has no shell, so compose cannot health-check it; poll its health_check extension."""
        deadline = time.monotonic() + self._ready_timeout
        while not self._gateway_ready():
            if time.monotonic() > deadline:
                raise TimeoutError("the gateway did not report healthy in time")
            time.sleep(3)

    def _pass(self, config: str) -> PassResult:
        self._log(f"pass with {config}")
        self._compose.use_gateway_config(config)
        self._wait_for_gateway()
        trace_before, log_before = self._tempo.bytes_received(), self._loki.bytes_received()
        traffic = self._send(TrafficPlan(self._url, self._requests, self._seed, rate_per_second=80.0))
        self._log(f"  sent {traffic.sent} requests, statuses {dict(sorted(traffic.statuses.items()))}")
        time.sleep(self._settle)
        trace_bytes = self._tempo.bytes_received() - trace_before
        log_bytes = self._loki.bytes_received() - log_before
        window = (traffic.started_at - 5, time.time() + 5)

        # One search lists every trace the store holds for the service in the window; a lookup per trace would be
        # thousands of slow requests.
        held = set(self._tempo.search('{ resource.service.name = "checkout" }', *window, limit=SEARCH_LIMIT))

        def stored(trace_ids: list[str], exact: bool = False) -> list[str]:
            found = [trace_id for trace_id in trace_ids if trace_id in held]
            if exact:
                # A search is approximate while data is in the ingester; the few ids it missed get a direct lookup.
                missed = [trace_id for trace_id in trace_ids if trace_id not in held]
                found += [trace_id for trace_id in missed if self._tempo.trace_json(trace_id) is not None]
            return found

        found_errors = stored(traffic.error_traces, exact=True)
        found_slow = stored(traffic.slow_traces, exact=True)
        # Ordinary traces are thousands; a seeded sample is looked up exactly, which is what the kept share is.
        sample = random.Random(self._seed).sample(
            traffic.ordinary_traces, min(ORDINARY_SAMPLE, len(traffic.ordinary_traces))
        )
        found_ordinary = [trace_id for trace_id in sample if self._tempo.trace_json(trace_id) is not None]
        personal = stored(traffic.personal_data_traces, exact=True)
        email_hits = self._tempo.search(
            f'{{ span.customer.email =~ ".*{EMAIL_FRAGMENT}.*" }}', *window, limit=SEARCH_LIMIT
        )
        card_hits = self._tempo.search('{ span.payment.card =~ ".*4242.*" }', *window, limit=SEARCH_LIMIT)
        # The search covers attribute values; reading a sample of whole stored traces covers everything else in them.
        bodies = [self._tempo.trace_json(trace_id) or "" for trace_id in personal[:SAMPLE_BODIES]]
        email_log = self._loki.query(f'{{service_name="checkout"}} |= "{TEST_EMAIL}"', *window)
        card_log = self._loki.query(f'{{service_name="checkout"}} |= "{TEST_CARD}"', *window)
        return PassResult(
            config=config,
            sent=traffic.sent,
            trace_bytes=trace_bytes,
            log_bytes=log_bytes,
            stored_traces=len(found_errors) + len(found_slow) + len(found_ordinary) + len(personal),
            errors_sent=len(traffic.error_traces),
            errors_found=len(found_errors),
            slow_sent=len(traffic.slow_traces),
            slow_found=len(found_slow),
            ordinary_sent=len(sample),
            ordinary_found=len(found_ordinary),
            email_in_traces=max(len(email_hits), sum(TEST_EMAIL in body for body in bodies)),
            card_in_traces=max(len(card_hits), sum(TEST_CARD in body for body in bodies)),
            email_in_logs=len(email_log),
            card_in_logs=len(card_log),
        )


def render_report(report: VolumeReport) -> str:
    """The comparison as the table the README quotes."""
    raw, protected = report.raw, report.protected
    rows = [
        ("traffic sent (requests)", raw.sent, protected.sent),
        ("trace bytes accepted by Tempo", f"{raw.trace_bytes:,.0f}", f"{protected.trace_bytes:,.0f}"),
        ("log bytes accepted by Loki", f"{raw.log_bytes:,.0f}", f"{protected.log_bytes:,.0f}"),
        (
            "error traces found / sent",
            f"{raw.errors_found}/{raw.errors_sent}",
            f"{protected.errors_found}/{protected.errors_sent}",
        ),
        (
            "slow traces found / sent",
            f"{raw.slow_found}/{raw.slow_sent}",
            f"{protected.slow_found}/{protected.slow_sent}",
        ),
        (
            f"ordinary traces kept (sample of {raw.ordinary_sent})",
            f"{raw.ordinary_kept_share:.1%}",
            f"{protected.ordinary_kept_share:.1%}",
        ),
        ("test email found in stored traces", raw.email_in_traces, protected.email_in_traces),
        ("test card found in stored traces", raw.card_in_traces, protected.card_in_traces),
        ("test email found in logs", raw.email_in_logs, protected.email_in_logs),
        ("test card found in logs", raw.card_in_logs, protected.card_in_logs),
    ]
    width = max(len(str(row[0])) for row in rows)
    lines = [f"{'':{width}}  {'raw gateway':>16}  {'protected gateway':>18}"]
    lines.extend(f"{row[0]:{width}}  {row[1]!s:>16}  {row[2]!s:>18}" for row in rows)
    lines.append(f"stored trace volume reduced by {report.trace_reduction:.1%}")
    return "\n".join(lines)
