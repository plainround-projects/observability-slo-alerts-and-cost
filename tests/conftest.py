"""Shared fixtures: an in-memory OpenTelemetry pipeline that tests can read back."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@dataclass
class InMemoryTelemetry:
    """Providers whose spans and metrics stay in memory."""

    tracer_provider: TracerProvider
    span_exporter: InMemorySpanExporter
    meter_provider: MeterProvider
    metric_reader: InMemoryMetricReader


@pytest.fixture
def memory_telemetry() -> Iterator[InMemoryTelemetry]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    yield InMemoryTelemetry(tracer_provider, exporter, meter_provider, reader)
    tracer_provider.shutdown()
    meter_provider.shutdown()
