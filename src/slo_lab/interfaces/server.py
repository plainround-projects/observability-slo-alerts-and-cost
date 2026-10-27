"""Run a WSGI app on waitress and shut down cleanly on SIGTERM or Ctrl-C."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from flask import Flask
from waitress.server import create_server

from slo_lab.infrastructure.shutdown import install_termination_handler

_log = logging.getLogger(__name__)


def run_server(app: Flask, host: str, port: int, on_close: Sequence[Callable[[], None]] = ()) -> None:
    """Serve until SIGTERM or Ctrl-C, let in-flight requests finish, then run the ``on_close`` callbacks."""
    install_termination_handler()
    server = create_server(app, host=host, port=port, threads=16, ident="slo-lab")
    _log.info("listening", extra={"host": host, "port": port})
    try:
        server.run()
    except KeyboardInterrupt:
        _log.info("shutdown requested; finishing in-flight requests")
    finally:
        server.close()
        for close in on_close:
            close()
        _log.info("stopped")
