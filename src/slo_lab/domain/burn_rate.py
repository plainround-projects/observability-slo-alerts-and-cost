"""Multiwindow, multi-burn-rate alert arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from slo_lab.domain.errors import InvalidPolicyError
from slo_lab.domain.slo import Slo


def prometheus_duration(value: timedelta) -> str:
    """Format a duration the way PromQL writes it: ``6h``, ``30m``, ``90s``."""
    seconds = int(value.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


@dataclass(frozen=True, slots=True)
class BurnRateRule:
    """Alert when the budget burns at ``burn_rate`` times the sustainable pace over BOTH windows.

    The long window proves the problem is real (it has already cost something); the short window proves it is still
    happening (so the alert resolves soon after the fix and a blip that ended is not paged).
    """

    severity: str
    burn_rate: float
    long_window: timedelta
    short_window: timedelta

    def __post_init__(self) -> None:
        """Validate the rate and the order of the windows."""
        if self.burn_rate <= 1.0:
            raise InvalidPolicyError("a burn rate of 1 or less spends the budget no faster than it refills")
        if self.short_window >= self.long_window:
            raise InvalidPolicyError("the short window must be shorter than the long window")
        if self.severity not in {"page", "ticket"}:
            raise InvalidPolicyError(f"severity must be page or ticket, got {self.severity!r}")

    def budget_consumed(self, slo: Slo) -> float:
        """The share of the whole window's budget spent when the burn lasts for the long window."""
        return self.burn_rate * self.long_window.total_seconds() / (slo.window_days * 86400)

    def error_ratio_threshold(self, slo: Slo) -> float:
        """The bad-event ratio above which both windows must sit for the alert to fire."""
        return self.burn_rate * slo.error_budget

    def hours_to_exhaustion(self, slo: Slo) -> float:
        """How long the whole budget lasts at this burn rate."""
        return slo.window_days * 24 / self.burn_rate


@dataclass(frozen=True, slots=True)
class BurnRatePolicy:
    """A named set of burn-rate rules applied to every SLO."""

    name: str
    description: str
    rules: tuple[BurnRateRule, ...]
