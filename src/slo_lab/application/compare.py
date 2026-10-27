"""The before-and-after comparison the lesson's step 4 asks for."""

from __future__ import annotations

from dataclasses import dataclass

from slo_lab.domain.pages import PageReport


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two alert sets judged against the same planted incidents."""

    before: PageReport
    after: PageReport

    @property
    def fewer_notifications(self) -> bool:
        """True when the second set notified less often."""
        return self.after.total < self.before.total

    @property
    def nothing_real_missed(self) -> bool:
        """True when the second set still alerted on every user-facing incident."""
        return self.after.every_user_facing_incident_detected

    @property
    def improved(self) -> bool:
        """Quieter, more actionable and nothing real missed."""
        return (
            self.fewer_notifications
            and self.nothing_real_missed
            and self.after.actionable_share > self.before.actionable_share
        )
