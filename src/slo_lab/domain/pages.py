"""Classify what the pager received against the incidents that were planted."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from slo_lab.domain.traffic import Incident

GRACE = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Page:
    """One notification the fake pager received, at ``offset`` into the replayed week."""

    alert: str
    severity: str
    offset: timedelta


@dataclass(frozen=True, slots=True)
class IncidentCoverage:
    """Whether (and how fast) a user-facing incident produced an alert."""

    incident: str
    first_alert: str | None
    first_severity: str | None
    delay: timedelta | None
    first_page_delay: timedelta | None

    @property
    def detected(self) -> bool:
        """True when at least one alert fired for the incident."""
        return self.first_alert is not None


@dataclass(frozen=True, slots=True)
class PageReport:
    """The totals the lesson asks for."""

    total: int
    actionable: int
    by_alert: dict[str, int]
    false_alarm_causes: dict[str, int]
    coverage: tuple[IncidentCoverage, ...]

    @property
    def actionable_share(self) -> float:
        """Actionable notifications over all notifications (1.0 when there were none)."""
        return self.actionable / self.total if self.total else 1.0

    @property
    def every_user_facing_incident_detected(self) -> bool:
        """True when each user-facing incident produced at least one alert."""
        return all(item.detected for item in self.coverage)


def _matching(page: Page, incident: Incident) -> bool:
    return incident.start <= page.offset <= incident.end + GRACE


def classify(pages: list[Page], incidents: tuple[Incident, ...]) -> PageReport:
    """An alert is actionable when it starts inside (or just after) a user-facing incident."""
    user_facing = [incident for incident in incidents if incident.user_facing]
    quiet = [incident for incident in incidents if not incident.user_facing]
    causes: Counter[str] = Counter()
    actionable = 0
    for page in pages:
        if any(_matching(page, incident) for incident in user_facing):
            actionable += 1
            continue
        matched = next((incident for incident in quiet if _matching(page, incident)), None)
        causes[matched.name if matched else "ordinary baseline noise"] += 1
    coverage = []
    for incident in user_facing:
        hits = sorted((page for page in pages if _matching(page, incident)), key=lambda page: page.offset)
        first = hits[0] if hits else None
        first_page = next((page for page in hits if page.severity == "page"), None)
        coverage.append(
            IncidentCoverage(
                incident.name,
                first.alert if first else None,
                first.severity if first else None,
                first.offset - incident.start if first else None,
                first_page.offset - incident.start if first_page else None,
            )
        )
    return PageReport(
        total=len(pages),
        actionable=actionable,
        by_alert=dict(Counter(page.alert for page in pages)),
        false_alarm_causes=dict(causes),
        coverage=tuple(coverage),
    )
