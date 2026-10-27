"""The fake pager's memory: an append-only JSON-lines file, so pages survive a restart and tests can read them."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from slo_lab.domain.pages import Page


class JsonLinesPageStore:
    """Appends one line per page and can list or clear them."""

    def __init__(self, path: Path) -> None:
        """Use ``path``, creating its folder if needed."""
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, page: Page, received_at: float) -> None:
        """Append a page with the time it arrived (Unix seconds)."""
        record = {
            "alert": page.alert,
            "severity": page.severity,
            "offset_seconds": page.offset.total_seconds(),
            "received_at": received_at,
        }
        with self._lock, self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def list(self) -> list[dict[str, object]]:
        """Every page recorded so far."""
        with self._lock:
            if not self._path.exists():
                return []
            return [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line]

    def clear(self) -> None:
        """Forget every page."""
        with self._lock:
            self._path.write_text("", encoding="utf-8")
