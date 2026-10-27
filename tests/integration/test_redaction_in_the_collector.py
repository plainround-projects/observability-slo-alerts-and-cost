"""Run the real Collector image with the gateway's redaction processor and a debug exporter; post the test values."""

from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests
import yaml

from slo_lab.lab.traffic import TEST_CARD, TEST_EMAIL

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "otel/opentelemetry-collector-contrib:0.111.0"
SPACED_CARD = "4242 4242 4242 4242"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def collector_output(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    gateway = yaml.safe_load((ROOT / "observability/collector/gateway.yaml").read_text())
    config = {
        "receivers": {"otlp": {"protocols": {"http": {"endpoint": "0.0.0.0:4318"}}}},
        "processors": {"transform/redact": gateway["processors"]["transform/redact"]},
        "exporters": {"debug": {"verbosity": "detailed"}},
        "service": {
            "pipelines": {
                signal: {"receivers": ["otlp"], "processors": ["transform/redact"], "exporters": ["debug"]}
                for signal in ("traces", "logs")
            }
        },
    }
    path = tmp_path_factory.mktemp("collector") / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    name, port = f"redaction-probe-{uuid.uuid4().hex[:8]}", _free_port()
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:4318",
            "-v", f"{path}:/etc/otelcol/config.yaml:ro", IMAGE, "--config=/etc/otelcol/config.yaml",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 600
        while True:
            try:
                requests.get(f"http://127.0.0.1:{port}/", timeout=5)
                break
            except requests.RequestException:
                if time.monotonic() > deadline:
                    pytest.fail("the Collector did not start listening within 10 minutes")
                time.sleep(3)
        now = str(time.time_ns())
        body = {
            "resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [{"logRecords": [{
                "timeUnixNano": now,
                "body": {"stringValue": json.dumps({"msg": f"payment from {TEST_EMAIL} with card {SPACED_CARD} ok"})},
                "attributes": [{"key": "note", "value": {"stringValue": f"card={TEST_CARD} mail={TEST_EMAIL}"}}],
            }]}]}]
        }  # fmt: skip
        span = {
            "resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": [{
                "traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "b7ad6b7169203331", "name": "POST /checkout",
                "kind": 2, "startTimeUnixNano": now, "endTimeUnixNano": now,
                "attributes": [
                    {"key": "customer.email", "value": {"stringValue": TEST_EMAIL}},
                    {"key": "payment.card", "value": {"stringValue": TEST_CARD}},
                    {"key": "order.ref", "value": {"stringValue": "ord-000042"}},
                ],
            }]}]}]
        }  # fmt: skip
        assert requests.post(f"http://127.0.0.1:{port}/v1/logs", json=body, timeout=180).status_code == 200
        assert requests.post(f"http://127.0.0.1:{port}/v1/traces", json=span, timeout=180).status_code == 200
        time.sleep(3)
        yield subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=True).stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def test_the_test_values_never_leave_the_collector(collector_output):
    assert TEST_EMAIL not in collector_output
    assert TEST_CARD not in collector_output
    assert SPACED_CARD not in collector_output


def test_only_the_sensitive_part_is_replaced_and_the_rest_of_each_field_survives(collector_output):
    assert "payment from <email> with card <card> ok" in collector_output
    assert "card=<card> mail=<email>" in collector_output
    assert "customer.email: Str(<email>)" in collector_output
    assert "payment.card: Str(<card>)" in collector_output
    assert "order.ref: Str(ord-000042)" in collector_output
