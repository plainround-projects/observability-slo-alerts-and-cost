"""Composition root of the fake pager."""

from __future__ import annotations

import os
from pathlib import Path

from slo_lab.infrastructure.logging_setup import configure_logging
from slo_lab.infrastructure.pager_store import JsonLinesPageStore
from slo_lab.infrastructure.settings import PagerSettings
from slo_lab.interfaces.pager_app import create_pager_app
from slo_lab.interfaces.server import run_server


def main() -> None:
    """Start the fake pager."""
    settings = PagerSettings.from_env(os.environ)
    configure_logging(settings.log_level, "pager")
    run_server(create_pager_app(JsonLinesPageStore(Path(settings.pages_file))), settings.bind_host, settings.port)
