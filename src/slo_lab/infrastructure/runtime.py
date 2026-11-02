"""Small runtime adapters: a real delay and a counter-based reference generator."""

from __future__ import annotations

import itertools
import time


class SleepDelay:
    """Waits with ``time.sleep``."""

    def wait(self, seconds: float) -> None:
        """Block for ``seconds``."""
        time.sleep(seconds)


class SequentialReferences:
    """``ord-000001``, then ``ord-000002``, then ``ord-000003``; thread-safe because ``itertools.count`` is."""

    def __init__(self) -> None:
        """Start at 1."""
        self._counter = itertools.count(1)

    def next(self) -> str:
        """The next reference."""
        return f"ord-{next(self._counter):06d}"
