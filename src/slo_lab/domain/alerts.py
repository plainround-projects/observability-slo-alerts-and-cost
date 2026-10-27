"""Alert semantics as Prometheus applies them: an expression, a for-duration, and the episodes that result."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlertRule:
    """One alerting rule."""

    name: str
    expression: str
    for_seconds: float
    severity: str


@dataclass(frozen=True, slots=True)
class FiringEpisode:
    """One continuous stretch during which an alert was firing; its start is when the pager is notified."""

    alert: str
    severity: str
    started_at: float
    ended_at: float


def firing_episodes(
    rule: AlertRule, step_seconds: float, evaluations: Iterable[tuple[float, bool]]
) -> list[FiringEpisode]:
    """Turn (time, expression-is-true) evaluations into firing episodes.

    As in Prometheus: when the expression becomes true the alert is pending; it fires once the expression has been
    true at every evaluation for ``for_seconds``; it stops when the expression is false at an evaluation.
    """
    episodes: list[FiringEpisode] = []
    pending_since: float | None = None
    firing_since: float | None = None
    last_true = 0.0
    for when, is_true in evaluations:
        if is_true:
            if pending_since is None:
                pending_since = when
            last_true = when
            if firing_since is None and when - pending_since >= rule.for_seconds:
                firing_since = when
            continue
        if firing_since is not None:
            episodes.append(FiringEpisode(rule.name, rule.severity, firing_since, last_true + step_seconds))
        pending_since = firing_since = None
    if firing_since is not None:
        episodes.append(FiringEpisode(rule.name, rule.severity, firing_since, last_true + step_seconds))
    return episodes
