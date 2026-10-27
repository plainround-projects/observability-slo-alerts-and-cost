"""The checkout service's HTTP surface."""

from __future__ import annotations

import logging
import time
from typing import Any

from flask import Flask, Response, g, jsonify, request
from opentelemetry import trace
from werkzeug.exceptions import HTTPException

from slo_lab.application.place_checkout import PlaceCheckout
from slo_lab.domain.checkout import (
    CheckoutFailedError,
    CheckoutRequest,
    InvalidCheckoutError,
    OrderLine,
    Simulation,
)
from slo_lab.infrastructure.otel_telemetry import OtelRequestMetrics

_log = logging.getLogger(__name__)


def _problem(status: int, code: str, detail: str) -> tuple[Response, int]:
    return jsonify({"error": code, "detail": detail}), status


def parse_checkout(body: Any, allow_simulation: bool) -> CheckoutRequest:
    """Turn a decoded JSON body into a validated request, raising ``InvalidCheckoutError``."""
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise InvalidCheckoutError("body must be an object with an items list")
    try:
        lines = tuple(OrderLine(str(item["sku"]), int(item["quantity"])) for item in body["items"])
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidCheckoutError("each item needs a sku and an integer quantity") from error
    simulation = Simulation.NONE
    if "simulate" in body:
        if not allow_simulation:
            raise InvalidCheckoutError("simulation is disabled")
        try:
            simulation = Simulation(body["simulate"])
        except ValueError as error:
            raise InvalidCheckoutError("simulate must be none, error or slow") from error
    email, card = body.get("customer_email"), body.get("card_number")
    if not all(value is None or isinstance(value, str) for value in (email, card)):
        raise InvalidCheckoutError("customer_email and card_number must be strings")
    return CheckoutRequest(lines, email, card, simulation)


def create_service_app(place_checkout: PlaceCheckout, metrics: OtelRequestMetrics, allow_simulation: bool) -> Flask:
    """Build the Flask app: ``POST /checkout`` and ``GET /healthz``; every request is counted and timed."""
    app = Flask("checkout")

    @app.before_request
    def start_timer() -> None:
        g.started = time.monotonic()

    @app.after_request
    def record(response: Response) -> Response:
        route = request.url_rule.rule if request.url_rule else "unmatched"
        metrics.record(route, response.status_code, time.monotonic() - g.get("started", time.monotonic()))
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            response.headers["X-Trace-Id"] = trace.format_trace_id(context.trace_id)
        return response

    @app.post("/checkout")
    def checkout() -> tuple[Response, int]:
        receipt = place_checkout.execute(parse_checkout(request.get_json(silent=True), allow_simulation))
        return jsonify({"reference": receipt.reference, "items": receipt.item_count}), 200

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok"})

    @app.errorhandler(InvalidCheckoutError)
    def invalid(error: InvalidCheckoutError) -> tuple[Response, int]:
        return _problem(400, "invalid_checkout", str(error))

    @app.errorhandler(CheckoutFailedError)
    def failed(error: CheckoutFailedError) -> tuple[Response, int]:
        return _problem(500, "checkout_failed", str(error))

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> tuple[Response, int]:
        return _problem(error.code or 500, (error.name or "error").lower().replace(" ", "_"), error.description or "")

    @app.errorhandler(Exception)
    def unexpected(error: Exception) -> tuple[Response, int]:
        _log.exception("unhandled error")
        return _problem(500, "internal_error", "the service failed")

    return app
