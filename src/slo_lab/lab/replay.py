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
