"""Composition root of the checkout service."""

from __future__ import annotations

import os

from opentelemetry import metrics

from slo_lab.application.place_checkout import PlaceCheckout
from slo_lab.infrastructure.logging_setup import configure_logging
from slo_lab.infrastructure.otel_telemetry import OtelAnnotator, OtelRequestMetrics
from slo_lab.infrastructure.runtime import SequentialReferences, SleepDelay
from slo_lab.infrastructure.settings import ServiceSettings
from slo_lab.interfaces.server import run_server
from slo_lab.interfaces.service_app import create_service_app


def main() -> None:
    """Start the checkout service."""
    settings = ServiceSettings.from_env(os.environ)
    configure_logging(settings.log_level, settings.service_name)
    use_case = PlaceCheckout(OtelAnnotator(), SleepDelay(), SequentialReferences(), settings.slow_seconds)
    request_metrics = OtelRequestMetrics(metrics.get_meter("slo_lab.checkout"))
    app = create_service_app(use_case, request_metrics, settings.allow_simulation)
    run_server(app, settings.bind_host, settings.port)
