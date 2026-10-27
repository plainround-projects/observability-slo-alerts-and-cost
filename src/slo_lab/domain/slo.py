"""A service level objective: what counts as good and valid, the target and the error budget it implies."""

from __future__ import annotations

from dataclasses import dataclass

from slo_lab.domain.errors import InvalidSloError

WINDOW_TOKEN = "__WINDOW__"
MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True, slots=True)
class Slo:
    """An objective over a rolling window.

    ``good`` and ``valid`` are PromQL expressions that contain ``__WINDOW__`` where a range such as ``1h`` goes; the
    SLI is good / valid, and the objective is the share of valid events that must be good.
    """

    name: str
    objective: float
    window_days: int
    description: str
    good: str
    valid: str

    def __post_init__(self) -> None:
        """Reject an objective outside (0, 1), a non-positive window, or expressions without the window token."""
        if not 0.0 < self.objective < 1.0:
            raise InvalidSloError(f"{self.name}: objective must be between 0 and 1 exclusive, got {self.objective}")
        if self.window_days < 1:
            raise InvalidSloError(f"{self.name}: window_days must be at least 1")
        if WINDOW_TOKEN not in self.good or WINDOW_TOKEN not in self.valid:
            raise InvalidSloError(f"{self.name}: good and valid must both contain {WINDOW_TOKEN}")

    @property
    def error_budget(self) -> float:
        """The share of valid events allowed to be bad: 1 - objective."""
        return 1.0 - self.objective

    @property
    def window_minutes(self) -> int:
        """The window in minutes."""
        return self.window_days * MINUTES_PER_DAY

    def budget_minutes(self) -> float:
        """The budget as minutes of total outage over the window."""
        return self.error_budget * self.window_minutes

    def budget_requests(self, requests_per_minute: float) -> float:
        """The budget as bad requests allowed over the window at a steady request rate."""
        return self.error_budget * requests_per_minute * self.window_minutes

    def error_ratio_expression(self, window: str) -> str:
        """PromQL for the share of bad events over ``window`` (for example ``1h``)."""
        good = self.good.replace(WINDOW_TOKEN, window)
        valid = self.valid.replace(WINDOW_TOKEN, window)
        return f"(1 - ({good}) / ({valid}))"
