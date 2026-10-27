"""Replay alert rules over stored history and report when each one would have notified the pager."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from slo_lab.application.ports import RangeEvaluator
from slo_lab.domain.alerts import AlertRule, firing_episodes
from slo_lab.domain.pages import Page


class ReplayAlerts:
    """Evaluates each rule's expression at every step with real PromQL, then applies Prometheus's for-duration rule."""

    def __init__(self, evaluator: RangeEvaluator) -> None:
        """Take the evaluator that runs PromQL over the stored week."""
        self._evaluator = evaluator

    def run(self, rules: Sequence[AlertRule], start: float, end: float, step_seconds: float) -> list[Page]:
        """Return one page per firing episode, ordered by time."""
        steps = [start + index * step_seconds for index in range(int((end - start) // step_seconds) + 1)]
        pages: list[Page] = []
        for rule in rules:
            true_times = self._evaluator.true_at(rule.expression, start, end, step_seconds)
            evaluations = [(when, when in true_times) for when in steps]
            for episode in firing_episodes(rule, step_seconds, evaluations):
                pages.append(Page(rule.name, rule.severity, timedelta(seconds=episode.started_at - start)))
        return sorted(pages, key=lambda page: page.offset)
