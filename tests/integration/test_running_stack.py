"""Against the stack that `make up` started: every part answers and the service's SLI series reach Prometheus."""

from __future__ import annotations

import os
import time

import pytest
import requests

from slo_lab.lab.stores import LokiClient, PagerClient, TempoClient

pytestmark = pytest.mark.integration

ENV = os.environ
SERVICE = f"http://127.0.0.1:{ENV.get('SERVICE_HOST_PORT', '28083')}"
PROMETHEUS = f"http://127.0.0.1:{ENV.get('PROMETHEUS_HOST_PORT', '29090')}"
PAGER = f"http://127.0.0.1:{ENV.get('PAGER_HOST_PORT', '28084')}"
GRAFANA = f"http://127.0.0.1:{ENV.get('GRAFANA_HOST_PORT', '23000')}"
BODY = {"items": [{"sku": "SKU-001", "quantity": 2}]}


def _query(expression: str) -> list[dict[str, object]]:
    response = requests.get(f"{PROMETHEUS}/api/v1/query", params={"query": expression}, timeout=120)
    assert response.status_code == 200
    result: list[dict[str, object]] = response.json()["data"]["result"]
    return result


def test_the_backends_the_pager_and_grafana_answer():
    assert TempoClient(f"http://127.0.0.1:{ENV.get('TEMPO_HOST_PORT', '23200')}").ready()
    assert LokiClient(f"http://127.0.0.1:{ENV.get('LOKI_HOST_PORT', '23100')}").ready()
    assert requests.get(f"{PROMETHEUS}/-/healthy", timeout=120).status_code == 200
    assert requests.get(f"{PAGER}/healthz", timeout=120).status_code == 200
    auth = ("admin", ENV.get("GRAFANA_ADMIN_PASSWORD", ""))
    for uid in ("prometheus", "loki"):
        response = requests.get(f"{GRAFANA}/api/datasources/uid/{uid}/health", auth=auth, timeout=60)
        assert response.status_code == 200, uid
    # Grafana 11.2 has no backend health check for Tempo; query it through the proxy, as "Save & test" does.
    assert requests.get(f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/search?limit=1", auth=auth, timeout=60).ok


def test_a_checkout_succeeds_and_the_lab_simulation_can_fail_it():
    ok = requests.post(f"{SERVICE}/checkout", json=BODY, timeout=120)
    assert ok.status_code == 200
    assert ok.json()["items"] == 2
    assert len(ok.headers["X-Trace-Id"]) == 32
    assert requests.post(f"{SERVICE}/checkout", json={**BODY, "simulate": "error"}, timeout=120).status_code == 500
    assert requests.post(f"{SERVICE}/checkout", json={"items": []}, timeout=120).status_code == 400


def test_the_sli_series_reach_prometheus_with_the_300_ms_bucket():
    for _ in range(20):
        requests.post(f"{SERVICE}/checkout", json=BODY, timeout=120)
        requests.get(f"{SERVICE}/healthz", timeout=120)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not _query('http_requests_total{route="/checkout",status="200"}'):
        time.sleep(3)
    assert _query('http_requests_total{route="/checkout",status="200"}')
    assert _query('http_request_duration_seconds_bucket{route="/checkout",le="0.3"}')
    assert _query('http_request_duration_seconds_count{route="/checkout"}')


def test_prometheus_loaded_the_generated_burn_rate_rules_and_not_the_threshold_alerts():
    groups = requests.get(f"{PROMETHEUS}/api/v1/rules", timeout=120).json()["data"]["groups"]
    names = {rule["name"] for group in groups for rule in group["rules"]}
    assert names == {
        "AvailabilityBudgetFastBurn",
        "AvailabilityBudgetSlowBurn",
        "LatencyBudgetFastBurn",
        "LatencyBudgetSlowBurn",
    }


def test_prometheus_is_connected_to_alertmanager():
    response = requests.get(f"{PROMETHEUS}/api/v1/alertmanagers", timeout=120)
    alertmanagers = response.json()["data"]["activeAlertmanagers"]
    assert [item["url"] for item in alertmanagers] == ["http://alertmanager:9093/api/v2/alerts"]


def test_the_pager_records_a_firing_webhook_and_ignores_resolved_ones():
    pager = PagerClient(PAGER)
    pager.clear()
    payload = {
        "alerts": [
            {"status": "firing", "labels": {"alertname": "Probe", "severity": "ticket"}},
            {"status": "resolved", "labels": {"alertname": "Probe", "severity": "ticket"}},
        ]
    }
    assert requests.post(f"{PAGER}/webhook", json=payload, timeout=120).status_code == 200
    assert [page["alert"] for page in pager.pages()] == ["Probe"]
    pager.clear()
