"""The boundaries the use cases talk through; infrastructure supplies the implementations."""

from __future__ import annotations

from typing import Protocol

from slo_lab.domain.pages import Page


class RangeEvaluator(Protocol):
    """Evaluates a PromQL alert expression over a time range against stored (replayed) data."""

    def true_at(self, expression: str, start: float, end: float, step_seconds: float) -> set[float]:
        """The evaluation times (Unix seconds) at which the expression returned at least one series."""
        ...


class Annotator(Protocol):
    """Adds attributes to the span of the request being handled."""

    def set(self, key: str, value: str) -> None:
        """Attach ``key`` = ``value`` to the current span."""
        ...


class Delay(Protocol):
    """Waits; injected so tests do not sleep."""

    def wait(self, seconds: float) -> None:
        """Block for ``seconds``."""
        ...


class Reference(Protocol):
    """Creates order references; injected so tests are deterministic."""

    def next(self) -> str:
        """A new reference such as ``ord-000042``."""
        ...


class PageStore(Protocol):
    """The fake pager's record of what it was paged with."""

    def add(self, page: Page, received_at: float) -> None:
        """Record one page."""
        ...
