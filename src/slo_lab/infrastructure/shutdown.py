"""Turn SIGTERM into the same clean exit as Ctrl-C so in-flight work finishes before the process stops."""

from __future__ import annotations

import signal
from types import FrameType


def _raise_keyboard_interrupt(signal_number: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def install_termination_handler() -> None:
    """Make SIGTERM raise ``KeyboardInterrupt`` in the main thread, where the server loop handles it."""
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
