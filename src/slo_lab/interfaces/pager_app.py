"""The fake pager: counts and timestamps every notification Alertmanager sends it."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from flask import Flask, Response, jsonify, request

from slo_lab.domain.pages import Page
from slo_lab.infrastructure.pager_store import JsonLinesPageStore


def create_pager_app(store: JsonLinesPageStore, clock: Any = time.time) -> Flask:
    """Build the Flask app: ``POST /webhook`` (Alertmanager), ``GET /pages``, ``DELETE /pages``, ``GET /healthz``."""
    app = Flask("pager")

    @app.post("/webhook")
    def webhook() -> tuple[Response, int]:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
            return jsonify({"error": "expected an Alertmanager webhook payload"}), 400
        now = float(clock())
        for alert in body["alerts"]:
            if alert.get("status") != "firing":
                continue
            labels = alert.get("labels", {})
            store.add(
                Page(str(labels.get("alertname", "unknown")), str(labels.get("severity", "page")), timedelta()), now
            )
        return jsonify({"recorded": len(body["alerts"])}), 200

    @app.get("/pages")
    def pages() -> Response:
        return jsonify(store.list())

    @app.delete("/pages")
    def clear() -> Response:
        store.clear()
        return jsonify({"cleared": True})

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok"})

    return app
