import pytest

from slo_lab.application.place_checkout import PlaceCheckout
from slo_lab.domain.checkout import CheckoutFailedError, InvalidCheckoutError, Receipt, Simulation
from slo_lab.infrastructure.otel_telemetry import OtelRequestMetrics
from slo_lab.infrastructure.pager_store import JsonLinesPageStore
from slo_lab.interfaces.pager_app import create_pager_app
from slo_lab.interfaces.service_app import create_service_app, parse_checkout


class StubCheckout(PlaceCheckout):
    def __init__(self, error=None):
        self._error, self.seen = error, []

    def execute(self, request):
        self.seen.append(request)
        if self._error:
            raise self._error
        return Receipt("ord-000001", 3)


BODY = {"items": [{"sku": "SKU-001", "quantity": 3}]}


def _client(memory_telemetry, error=None, allow=True):
    metrics = OtelRequestMetrics(memory_telemetry.meter_provider.get_meter("t"))
    stub = StubCheckout(error)
    return create_service_app(stub, metrics, allow).test_client(), stub


def test_a_checkout_returns_a_receipt_and_is_counted_by_route_and_status(memory_telemetry):
    client, stub = _client(memory_telemetry)
    response = client.post("/checkout", json=BODY)
    assert (response.status_code, response.get_json()) == (200, {"reference": "ord-000001", "items": 3})
    assert stub.seen[0].lines[0].sku == "SKU-001"
    data = memory_telemetry.metric_reader.get_metrics_data()
    counter = next(
        m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name == "http_requests"
    )
    assert dict(counter.data.data_points[0].attributes) == {"route": "/checkout", "status": "200"}


def test_a_failed_checkout_is_a_500_and_is_still_counted(memory_telemetry):
    client, _ = _client(memory_telemetry, CheckoutFailedError("down"))
    assert client.post("/checkout", json=BODY).status_code == 500
    data = memory_telemetry.metric_reader.get_metrics_data()
    counter = next(
        m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name == "http_requests"
    )
    assert dict(counter.data.data_points[0].attributes)["status"] == "500"


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"items": "x"},
        {"items": [{"sku": "SKU-001"}]},
        {"items": [{"sku": "bad sku", "quantity": 1}]},
        {"items": [{"sku": "A", "quantity": 0}]},
        {"items": []},
        {"items": [{"sku": "A", "quantity": 1}], "customer_email": 5},
    ],
)
def test_invalid_bodies_are_a_400(memory_telemetry, body):
    client, stub = _client(memory_telemetry)
    response = client.post("/checkout", json=body) if body is not None else client.post("/checkout", data="nope")
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_checkout"
    assert stub.seen == []


def test_simulation_is_only_accepted_when_the_lab_enables_it():
    assert parse_checkout({**BODY, "simulate": "error"}, True).simulation is Simulation.ERROR
    with pytest.raises(InvalidCheckoutError, match="disabled"):
        parse_checkout({**BODY, "simulate": "error"}, False)
    with pytest.raises(InvalidCheckoutError, match="simulate"):
        parse_checkout({**BODY, "simulate": "explode"}, True)


def test_health_and_unknown_routes(memory_telemetry):
    client, _ = _client(memory_telemetry)
    assert client.get("/healthz").get_json() == {"status": "ok"}
    assert client.get("/nope").get_json()["error"] == "not_found"


def test_unexpected_errors_are_a_json_500(memory_telemetry):
    client, _ = _client(memory_telemetry, RuntimeError("bug"))
    response = client.post("/checkout", json=BODY)
    assert (response.status_code, response.get_json()["error"]) == (500, "internal_error")


def test_responses_inside_a_span_carry_the_trace_id(memory_telemetry):
    client, _ = _client(memory_telemetry)
    with memory_telemetry.tracer_provider.get_tracer("t").start_as_current_span("request") as span:
        response = client.get("/healthz")
        expected = f"{span.get_span_context().trace_id:032x}"
    assert response.headers["X-Trace-Id"] == expected


def _pager(tmp_path):
    store = JsonLinesPageStore(tmp_path / "pages.jsonl")
    return create_pager_app(store, clock=lambda: 1234.0).test_client(), store


WEBHOOK = {
    "alerts": [
        {"status": "firing", "labels": {"alertname": "AvailabilityBudgetFastBurn", "severity": "page"}},
        {"status": "resolved", "labels": {"alertname": "Other", "severity": "page"}},
    ]
}


def test_the_pager_records_firing_alerts_only_and_timestamps_them(tmp_path):
    client, _ = _pager(tmp_path)
    assert client.post("/webhook", json=WEBHOOK).status_code == 200
    pages = client.get("/pages").get_json()
    assert [(page["alert"], page["severity"], page["received_at"]) for page in pages] == [
        ("AvailabilityBudgetFastBurn", "page", 1234.0)
    ]


def test_the_pager_rejects_a_payload_that_is_not_a_webhook_and_can_be_cleared(tmp_path):
    client, _ = _pager(tmp_path)
    assert client.post("/webhook", json={"nope": 1}).status_code == 400
    client.post("/webhook", json=WEBHOOK)
    assert client.delete("/pages").get_json() == {"cleared": True}
    assert client.get("/pages").get_json() == []
    assert client.get("/healthz").get_json() == {"status": "ok"}
