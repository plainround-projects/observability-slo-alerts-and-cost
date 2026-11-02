# Python: the three lab scenarios (replay, volume, live)

## What it is

This note reads the three programs that run the lesson's experiments, as Python. They are all built the same way, which is the main thing to learn from them:

- The experiment is a **class** with a `run` method.
- Everything it touches in the outside world (Docker, Prometheus, Tempo, Loki, the pager, the clock) is passed in through the constructor as a **`Protocol`** (a description of the methods it needs).
- The real command (`slo-replay`, `slo-volume`, `slo-live`) passes real clients; the unit tests pass small fakes, and the whole experiment runs in milliseconds without any container.
- The result is a **frozen dataclass** (a plain value) and a `render_report` function turns it into the table the README quotes.

## Why this repository uses it

The lesson's steps are measurements: how many notifications the threshold alerts produce on a simulated week and how many the burn-rate alerts do; how many bytes of traces a gateway without and with tail sampling stores and whether the personal data is still findable; and how long a real outage takes to reach a pager. Writing each as a class with injected collaborators makes the experiment itself testable, which is the only way to trust the numbers it prints.

## Where it appears

| Path | What it is |
| --- | --- |
| `src/slo_lab/lab/replay.py` | Steps 1 to 4: simulate a week, backfill it, replay three alert sets with real PromQL, judge them. |
| `src/slo_lab/lab/volume.py` | Steps 5 and 6: the same traffic through a raw and a protected gateway, then inspect every store. |
| `src/slo_lab/lab/live.py` | A real outage and the page arriving at the fake pager. |
| `tests/unit/lab/test_lab.py` | Tests them with fakes. |
| `tests/e2e/test_lesson_scenario.py` | Runs them against the real stack. |

## How it works underneath

A `Protocol` class lists method signatures. A class satisfies it simply by having methods with those names and compatible types; it does not inherit from anything. `mypy` checks the match. A `Protocol` that inherits from another (`class ReplayStore(RangeEvaluator, Protocol)`) adds methods to it.

A default parameter that is a function (`log: Callable[[str], None] = print`, `send: Callable[[TrafficPlan], TrafficResult] = send_traffic`, `clock: ... = time.monotonic`) lets the real code use the real thing and a test substitute a fake without a framework.

## Walkthrough of this repository's use

### `lab/replay.py`

```python
"""The replayed week: simulate, backfill, evaluate three alert sets with real PromQL, and judge them."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from slo_lab.application.compare import Comparison
from slo_lab.application.ports import RangeEvaluator
from slo_lab.application.replay_alerts import ReplayAlerts
from slo_lab.domain.alerts import AlertRule
from slo_lab.domain.pages import Page, PageReport, classify
from slo_lab.domain.traffic import Incident, TrafficProfile, simulate, week_incidents
from slo_lab.infrastructure.definitions_io import load_alert_rules
from slo_lab.infrastructure.openmetrics import write_week

DAYS = 7
```

The imports show the layers: the lab may use `application` (`ReplayAlerts`, `Comparison`), `domain` (`classify`, `simulate`) and `infrastructure` (`load_alert_rules`, `write_week`), and nothing imports the lab. `DAYS = 7`.

```python
class ReplayCompose(Protocol):
    """The docker operations the replay needs."""

    def backfill_replay_data(self, openmetrics_file: Path) -> None:
        """Create the replay TSDB blocks from the file."""
        ...

    def start_replay_prometheus(self) -> None:
        """Start the Prometheus serving them."""
        ...


class ReplayStore(RangeEvaluator, Protocol):
    """A Prometheus that can answer range queries over the backfilled week."""

    def ready(self) -> bool:
        """True when it is serving."""
        ...


STEP_SECONDS = 60.0
SEED = 20261027

```

Two protocols: `ReplayCompose` (backfill the data, start the replay Prometheus) and `ReplayStore` (a `RangeEvaluator` that can also say whether it is ready). `STEP_SECONDS = 60.0` is the resolution at which alerts are evaluated; `SEED = 20261027` makes the week repeatable.

```python
@dataclass(frozen=True, slots=True)
class AlertSet:
    """A named set of rules to replay."""

    name: str
    rules: tuple[AlertRule, ...]


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What each alert set did over the same week."""

    reports: dict[str, PageReport]
    pages: dict[str, list[Page]]
    requests_per_minute: float

    def compare(self, before: str, after: str) -> Comparison:
        """Judge ``after`` against ``before``."""
        return Comparison(self.reports[before], self.reports[after])
```

`AlertSet` is a named set of rules to replay. `ReplayReport` holds, per alert set, the pages and the judged `PageReport`, and the traffic rate; `compare(before, after)` builds a `Comparison`.

```python
def pages_to_lines(pages: list[Page]) -> str:
    """The pager log format: one JSON object per line."""
    return "".join(
        json.dumps({"alert": p.alert, "severity": p.severity, "offset_seconds": p.offset.total_seconds()}) + "\n"
        for p in pages
    )


def pages_from_lines(text: str) -> list[Page]:
    """Parse a pager log written by ``pages_to_lines``."""
    pages = []
    for line in text.splitlines():
        if line.strip():
            record = json.loads(line)
            pages.append(Page(record["alert"], record["severity"], timedelta(seconds=record["offset_seconds"])))
    return pages


def load_alert_sets(directory: Path, names: dict[str, str]) -> list[AlertSet]:
    """Read ``{set name: rule file}`` relative to ``directory``."""
    return [AlertSet(name, tuple(load_alert_rules(directory / filename))) for name, filename in names.items()]
```

`pages_to_lines` writes pages as JSON Lines (one object per line) and `pages_from_lines` reads them back (skipping blank lines); the round trip is tested. `load_alert_sets(directory, names)` reads each rule file with `load_alert_rules`.

```python
class WeekReplay:
    """Runs the whole replay against a Prometheus that holds the backfilled week."""

    def __init__(
        self,
        compose: ReplayCompose,
        evaluator: ReplayStore,
        work_directory: Path,
        incidents: tuple[Incident, ...] | None = None,
        profile: TrafficProfile | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        """Take the docker control, the PromQL evaluator and a folder for the generated files."""
        self._compose = compose
        self._evaluator = evaluator
        self._work = work_directory
        self._incidents = incidents or week_incidents()
        self._profile = profile or TrafficProfile()
        self._log = log
```

`WeekReplay` receives the docker control, the evaluator, a work folder, optionally different incidents or profile (default: the planted week and the ordinary profile), and a `log` function (default `print`).

```python
    def prepare(self, start_epoch: int) -> None:
        """Simulate the week, write it as OpenMetrics, backfill it with promtool and start the replay Prometheus."""
        self._work.mkdir(parents=True, exist_ok=True)
        path = self._work / "week.om"
        (self._work / "week.start").write_text(str(start_epoch))
        self._log(f"simulating {DAYS} days at 30 s resolution (seed {SEED})")
        with path.open("w", encoding="utf-8") as handle:
            ticks = write_week(simulate(self._profile, self._incidents, DAYS, SEED), start_epoch, handle)
        self._log(f"wrote {ticks} ticks ({path.stat().st_size / 1e6:.1f} MB); backfilling with promtool")
        self._compose.backfill_replay_data(path)
        self._compose.start_replay_prometheus()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not self._evaluator.ready():
            time.sleep(2)

    def existing_start_epoch(self) -> int:
        """The start of the week that ``prepare`` backfilled earlier, remembered in the work folder."""
        return int((self._work / "week.start").read_text())
```

`prepare(start_epoch)` makes the work folder, remembers the start time in `week.start`, simulates the week and writes it as OpenMetrics to `week.om` (`path.open("w", encoding="utf-8")`), logs how many ticks and megabytes, then asks Docker to backfill and to start the replay Prometheus, and waits up to 120 seconds for it to be ready. `existing_start_epoch` reads the remembered start for `--reuse`.

```python
    def evaluate(self, alert_sets: list[AlertSet], start_epoch: int) -> ReplayReport:
        """Replay every set over the week and classify the pages."""
        end = start_epoch + DAYS * 86400 - 60
        replay = ReplayAlerts(self._evaluator)
        pages: dict[str, list[Page]] = {}
        reports: dict[str, PageReport] = {}
        for alert_set in alert_sets:
            self._log(f"replaying {alert_set.name}: {len(alert_set.rules)} rules")
            pages[alert_set.name] = replay.run(alert_set.rules, float(start_epoch), float(end), STEP_SECONDS)
            reports[alert_set.name] = classify(pages[alert_set.name], self._incidents)
            (self._work / f"pages_{alert_set.name}.jsonl").write_text(pages_to_lines(pages[alert_set.name]))
        return ReplayReport(reports, pages, self._profile.requests_per_minute)
```

`evaluate(alert_sets, start_epoch)` computes the end of the week (start + 7 days − 60 s), and for each alert set replays every rule over the whole week with `ReplayAlerts` (real PromQL, one evaluation per minute), classifies the pages against the planted incidents, and writes a pager log `pages_<name>.jsonl`. It returns the report.

```python
def render_report(report: ReplayReport) -> str:
    """The comparison as the table the README quotes."""
    lines = []
    for name, page_report in report.reports.items():
        lines.append(
            f"{name}: {page_report.total} notifications, {page_report.actionable} actionable "
            f"({page_report.actionable_share:.1%})"
        )
        for alert, count in sorted(page_report.by_alert.items()):
            lines.append(f"    {alert}: {count}")
        for cause, count in sorted(page_report.false_alarm_causes.items()):
            lines.append(f"    false alarms during {cause}: {count}")
        for item in page_report.coverage:
            if item.detected and item.delay is not None:
                if item.first_severity == "page":
                    page = ""
                elif item.first_page_delay is not None:
                    page = f"; first page {item.first_page_delay.total_seconds() / 60:.0f} min after onset"
                else:
                    page = "; no page"
                lines.append(
                    f"    {item.incident}: first {item.first_severity} ({item.first_alert}) "
                    f"{item.delay.total_seconds() / 60:.0f} min after onset{page}"
                )
            else:
                lines.append(f"    {item.incident}: NO ALERT")
    return "\n".join(lines)
```

`render_report` prints, for each alert set: the number of notifications and the actionable share, a line per alert name with its count, a line per cause of false alarms, and for each user-facing incident the first alert, its severity and delay in minutes after onset and — when the first alert was only a ticket — when the first *page* came (or `no page`). An incident nobody alerted on prints `NO ALERT`.

### `lab/volume.py`

```python
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
SETTLE_SECONDS = 30.0
QUIET_TIMEOUT_SECONDS = 300.0
SEARCH_LIMIT = 20_000
SAMPLE_BODIES = 10
ORDINARY_SAMPLE = 300
EMAIL_FRAGMENT = "test-user-redact-me"
```

The constants: the two gateway configurations (`gateway.raw.yaml` has no protection; `gateway.yaml` has redaction and tail sampling), how long to settle, a limit of 20,000 for searches, a seeded sample of 300 ordinary traces, and the fragment of the test email used in searches.

```python
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
```

Three protocols: `GatewaySwitch` (use another gateway configuration), `TraceStore` (bytes accepted, a trace by id, a TraceQL search) and `LogStore` (bytes accepted, a LogQL query).

```python
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
```

`PassResult` holds everything one pass measured: bytes accepted by Tempo and Loki, how many traces are stored, how many errors and slow requests were sent and found, how many ordinary traces were sampled and found, and how many times the test email and card appear in stored traces and in logs. `ordinary_kept_share` is the share of the sampled ordinary traces that Tempo holds. `VolumeReport` has the raw and the protected pass and computes `trace_reduction`: `1 - protected bytes / raw bytes`.

```python
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
        quiet_interval_seconds: float = 15.0,
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
        self._quiet_interval = quiet_interval_seconds

    def run(self) -> VolumeReport:
        """Run the raw pass, then the protected pass."""
        return VolumeReport(self._pass(RAW_CONFIG), self._pass(PROTECTED_CONFIG))
```

`VolumeScenario` takes the collaborators, the number of requests, the seed, and injectable pieces: the traffic function, a log function, how long to settle, a function that says whether the gateway is ready, and timeouts. `run` runs the raw pass and then the protected pass.

```python
    def _wait_for_gateway(self) -> None:
        """The Collector image has no shell, so compose cannot health-check it; poll its health_check extension."""
        deadline = time.monotonic() + self._ready_timeout
        while not self._gateway_ready():
            if time.monotonic() > deadline:
                raise TimeoutError("the gateway did not report healthy in time")
            time.sleep(3)

    def _wait_until_ingestion_is_quiet(self) -> None:
        """Wait out the sampler's decision wait and the exporters' batching: at least ``settle`` seconds, then until
        Tempo's accepted-bytes counter has not moved for two readings (or five minutes have passed)."""
        time.sleep(self._settle)
        previous = self._tempo.bytes_received()
        deadline = time.monotonic() + QUIET_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(self._quiet_interval)
            current = self._tempo.bytes_received()
            if current == previous:
                return
            previous = current
```

Two waits that exist because of real problems. `_wait_for_gateway` polls the gateway's `health_check` until it reports healthy, because the Collector image has no shell and Compose cannot health-check it; without it, traffic was sent to a gateway that was still starting and the measurement read zeros. `_wait_until_ingestion_is_quiet` first sleeps the settle time, then reads Tempo's accepted-bytes counter every 15 seconds until two readings are equal (or five minutes pass): it waits out the sampler's decision wait (10 s) and the exporters' batching instead of guessing a fixed delay.

```python
    def _pass(self, config: str) -> PassResult:
        self._log(f"pass with {config}")
        self._compose.use_gateway_config(config)
        self._wait_for_gateway()
        trace_before, log_before = self._tempo.bytes_received(), self._loki.bytes_received()
        traffic = self._send(TrafficPlan(self._url, self._requests, self._seed, rate_per_second=80.0))
        self._log(f"  sent {traffic.sent} requests, statuses {dict(sorted(traffic.statuses.items()))}")
        self._wait_until_ingestion_is_quiet()
        trace_bytes = self._tempo.bytes_received() - trace_before
        log_bytes = self._loki.bytes_received() - log_before
        window = (traffic.started_at - 5, time.time() + 5)

```

`_pass(config)`: switch the gateway to the configuration, wait for it, read the byte counters **before**, send 6,000 requests at 80 per second, wait until ingestion is quiet, read the counters **after** (the difference is what this pass added) and define the search window.

```python
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
```

One TraceQL search lists every trace Tempo holds for the service in the window (`held`, a set); looking each trace up one by one would be thousands of slow requests. `stored(ids, exact)` keeps the ids found in that set; when `exact` is true, the ones the search missed (a search is approximate while data is still in the ingester) get a direct lookup by id. Errors and slow requests (few) use the exact check. Ordinary traces are thousands, so a **seeded sample** of 300 (`random.Random(seed).sample(...)`) is looked up exactly, which is what "kept share" means.

```python
        personal = stored(traffic.personal_data_traces, exact=True)
        email_hits = self._tempo.search(
            f'{{ span.customer.email =~ ".*{EMAIL_FRAGMENT}.*" }}', *window, limit=SEARCH_LIMIT
        )
        card_hits = self._tempo.search('{ span.payment.card =~ ".*4242.*" }', *window, limit=SEARCH_LIMIT)
        # The search covers attribute values; reading a sample of whole stored traces covers everything else in them.
        bodies = [self._tempo.trace_json(trace_id) or "" for trace_id in personal[:SAMPLE_BODIES]]
        email_log = self._loki.query(f'{{service_name="checkout"}} |= "{TEST_EMAIL}"', *window)
        card_log = self._loki.query(f'{{service_name="checkout"}} |= "{TEST_CARD}"', *window)
```

The privacy checks: a TraceQL search for spans whose `customer.email` attribute matches a regular expression containing the test email's fragment, another for `payment.card` matching `.*4242.*`; a sample of whole stored traces is also read as text and searched for the exact test values (the search covers attribute values; reading whole traces covers everything else in them); and two LogQL queries look for the exact email and card in the log lines.

```python
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
```

All of it goes into a `PassResult`. `max(len(email_hits), sum(TEST_EMAIL in body for body in bodies))` takes the larger of the two ways of counting; `sum(<booleans>)` counts the true ones.

```python
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
```

`render_report` builds a table of rows (label, raw value, protected value) and prints them in aligned columns: the f-string `{row[0]:{width}}` pads to a computed width, `{row[1]!s:>16}` right-aligns the text of the value in 16 characters. The last line says by how much the stored trace volume was reduced.

### `lab/live.py`

```python
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

```

`FAST_BURN_ALERT` is the name of the alert that must page. `Pager` is the protocol (clear, wait for a page). `LiveOutcome` holds the request counts, the page, and the seconds to the page.

```python
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
```

`run_live_outage`: clear the pager; send **warm-up** traffic with no planted errors for 120 seconds at 10 requests a second (so the windows have healthy history); note the time; send an **outage** (every request fails, `outage=True`) for 180 seconds; then wait up to 240 seconds for the fast-burn page and compute how long after the outage began it arrived (`page["received_at"] - outage_started`; the pager timestamps the page when it arrives). The traffic function is injected, so a unit test drives the logic without a network.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/lab/test_lab.py -q
```

Expected: `17 passed`.

```bash
.venv/bin/python - <<'EOF'
from datetime import timedelta
from slo_lab.domain.pages import Page
from slo_lab.lab.replay import pages_from_lines, pages_to_lines
text = pages_to_lines([Page("A", "page", timedelta(minutes=5))])
print(text, end="")
print(pages_from_lines(text))
EOF
```

Expected: a JSON line `{"alert": "A", "severity": "page", "offset_seconds": 300.0}` and the list with one `Page`.

```bash
make replay
```

Expected (needs `make up`): the comparison table described in `prometheus.md`.

```bash
make volume
```

Expected: the table described in `opentelemetry-collector.md`; the last line says the stored trace volume was reduced by about 80 percent.

```bash
make live
```

Expected: `page 'AvailabilityBudgetFastBurn' (page) arrived N s after the outage began` with N around 140.

## Mistakes people make

- **A mutable default argument.** Writing `def f(items=[])` shares one list between all calls. Use `None` and create the list inside the function. The code in this repository either uses immutable defaults (`()`, `None`, numbers) or `field(default_factory=dict)` in dataclasses.
- **Catching everything.** `except Exception:` hides bugs. This repository catches named errors and lets the rest travel up to one place that logs them and returns a 500 answer.
- **Comparing floats with `==`.** Floating-point numbers carry tiny rounding errors. Tests here compare with ranges or with exact values that are safe (for example `0.25` is exactly representable).
- **Using the wall clock to measure durations.** `time.time()` can jump when the system clock is corrected. Code that measures how long something took uses `time.monotonic()`, which only moves forward.
- **Forgetting that `bool` is an `int`.** In Python `True == 1`. The `OrderId` check rejects booleans on purpose.
- **Installing packages into the system Python.** Use a virtual environment. `make install` creates `.venv` for you.

## Check yourself

1. What is the difference between an image and a container? *An image is the read-only package; a container is a running process started from it.*
2. Why does a Dockerfile copy the dependency file before the source code? *So Docker can reuse the cached install layer when only the source changed.*
3. What happens to files a container writes when the container is removed? *They disappear, unless they were written to a volume or a bind mount.*
4. What does a digest give you that a tag does not? *A tag can be moved to a different image later; a digest always names the same bytes.*
5. Which signal does `docker stop` send first, and what comes after the grace period? *`SIGTERM`; then `SIGKILL`.*

## Further reading

- Python 3.12 documentation: https://docs.python.org/3.12/
- The tutorial: https://docs.python.org/3.12/tutorial/index.html
- The standard library index: https://docs.python.org/3.12/library/index.html
- Type hints (`typing`): https://docs.python.org/3.12/library/typing.html
