"""Lint the files that configure the stack: compose, both gateways, Prometheus, Alertmanager, the SLO document."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from slo_lab.infrastructure.definitions_io import load_slos
from slo_lab.lab.traffic import TEST_CARD, TEST_EMAIL

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "observability"
DATASOURCE_UIDS = {"prometheus", "prometheus-replay", "tempo", "loki"}


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return dict(_yaml(ROOT / "docker-compose.yml"))


@pytest.fixture(scope="module")
def gateway() -> dict[str, Any]:
    return dict(_yaml(OBSERVABILITY / "collector" / "gateway.yaml"))


@pytest.fixture(scope="module")
def raw_gateway() -> dict[str, Any]:
    return dict(_yaml(OBSERVABILITY / "collector" / "gateway.raw.yaml"))


def test_every_third_party_image_is_pinned_to_a_version(compose):
    images = [service["image"] for service in compose["services"].values() if "build" not in service]
    assert len(images) >= 6
    for image in images:
        assert ":" in image
        assert not image.endswith(":latest")


def test_the_base_image_is_pinned_by_digest_and_the_service_is_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^FROM .*@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    assert re.search(r"^USER app$", dockerfile, re.MULTILINE)


def test_every_published_port_is_bound_to_loopback(compose):
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on every interface"


def test_every_variable_the_compose_file_reads_is_in_env_example():
    used = set(re.findall(r"\$\{([A-Z_]+)", (ROOT / "docker-compose.yml").read_text()))
    declared = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines() if "=" in line}
    assert used <= declared, sorted(used - declared)


def test_the_replay_prometheus_is_a_profile_so_make_up_does_not_start_it(compose):
    assert compose["services"]["prometheus-replay"]["profiles"] == ["replay"]
    assert "profiles" not in compose["services"]["prometheus"]


def test_traces_are_redacted_then_sampled_then_batched_with_the_memory_limiter_first(gateway):
    processors = gateway["service"]["pipelines"]["traces"]["processors"]
    assert processors == ["memory_limiter", "transform/redact", "tail_sampling", "batch"]


def test_logs_are_redacted_and_metrics_are_neither_redacted_nor_sampled(gateway):
    pipelines = gateway["service"]["pipelines"]
    assert pipelines["logs"]["processors"] == ["memory_limiter", "transform/redact", "batch"]
    assert pipelines["metrics"]["processors"] == ["memory_limiter", "batch"]


def test_tail_sampling_keeps_errors_and_slow_traces_and_a_small_share_of_the_rest(gateway):
    policies = {policy["name"]: policy for policy in gateway["processors"]["tail_sampling"]["policies"]}
    assert policies["keep-every-error"]["status_code"]["status_codes"] == ["ERROR"]
    assert policies["keep-every-slow-trace"]["latency"]["threshold_ms"] == 1000
    assert 1 <= policies["keep-a-small-share-of-the-rest"]["probabilistic"]["sampling_percentage"] <= 10
    assert len(policies) == 3


def test_the_raw_gateway_is_the_protected_one_minus_the_two_policies(gateway, raw_gateway):
    assert raw_gateway["receivers"] == gateway["receivers"]
    assert raw_gateway["exporters"] == gateway["exporters"]
    for signal, pipeline in raw_gateway["service"]["pipelines"].items():
        assert "tail_sampling" not in pipeline["processors"]
        assert "transform/redact" not in pipeline["processors"]
        assert pipeline["receivers"] == gateway["service"]["pipelines"][signal]["receivers"]


def _redaction_patterns(gateway: dict[str, Any]) -> list[str]:
    statements = [
        statement
        for group in gateway["processors"]["transform/redact"]["trace_statements"]
        + gateway["processors"]["transform/redact"]["log_statements"]
        for statement in group["statements"]
    ]
    patterns = []
    for statement in statements:
        literals = re.findall(r'"((?:[^"\\]|\\.)*)"', statement)
        assert literals[-1] in {"<email>", "<card>"}, statement
        patterns.append(literals[-2].replace("\\\\", "\\"))
    return patterns


def test_the_redaction_patterns_remove_the_test_email_and_card_and_leave_ordinary_values_alone(gateway):
    patterns = _redaction_patterns(gateway)
    email_patterns = [pattern for pattern in patterns if "@" in pattern]
    card_patterns = [pattern for pattern in patterns if "@" not in pattern]
    assert email_patterns
    assert card_patterns
    for pattern in email_patterns:
        assert re.sub(pattern, "<email>", f"payment from {TEST_EMAIL} done") == "payment from <email> done"
    for pattern in card_patterns:
        for card in (TEST_CARD, "4242 4242 4242 4242", "4242-4242-4242-4242"):
            assert re.sub(pattern, "<card>", f"card {card} ok") == "card <card> ok"
    for harmless in ("ord-000042", "0af7651916cd43dd8448eb211c80319c", "1.4s", "2026-10-27T10:00:00Z", "SKU-017"):
        for pattern in patterns:
            assert re.sub(pattern, "X", harmless) == harmless


def test_prometheus_loads_only_the_generated_live_rules_and_alerts_through_alertmanager(compose):
    config = _yaml(OBSERVABILITY / "prometheus" / "prometheus.yml")
    assert config["rule_files"] == ["/etc/prometheus/rules/*.rules.yml"]
    assert config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    assert [path.name for path in (OBSERVABILITY / "prometheus" / "rules").glob("*.yml")] == ["burn_rate.rules.yml"]
    assert compose["services"]["prometheus"]["volumes"][1].endswith("/rules:/etc/prometheus/rules:ro")


def test_alertmanager_sends_every_alert_to_the_pager_webhook():
    config = _yaml(OBSERVABILITY / "alertmanager" / "alertmanager.yml")
    assert config["route"]["receiver"] == "fake-pager"
    assert config["receivers"][0]["webhook_configs"][0]["url"] == "http://pager:8090/webhook"


def test_every_generated_alert_has_a_severity_a_summary_and_a_runbook():
    for path in (OBSERVABILITY / "prometheus").glob("**/*.rules.yml"):
        for group in _yaml(path)["groups"]:
            for rule in group["rules"]:
                assert rule["labels"]["severity"] in {"page", "ticket"}, rule["alert"]
                assert {"summary", "runbook"} <= set(rule["annotations"]), rule["alert"]


def test_the_slo_document_states_the_budget_in_minutes_and_in_requests_that_the_definitions_imply():
    document = (ROOT / "docs" / "slo.md").read_text()
    slos = {slo.name: slo for slo in load_slos(OBSERVABILITY / "slo" / "slos.yaml")}
    assert f"**{slos['availability'].budget_minutes():,.0f} minutes**" in document
    assert f"**{slos['availability'].budget_requests(500):,.0f} bad requests**" in document
    assert f"**{slos['latency'].budget_minutes():,.0f} minutes**" in document
    for phrase in ("Valid request", "Good request", "99.5%", "300 ms"):
        assert phrase in document


@pytest.mark.parametrize("name", ["slo-overview", "replayed-week"])
def test_dashboards_use_provisioned_datasources_and_unique_panel_ids(name):
    dashboard = json.loads((OBSERVABILITY / "grafana" / "dashboards" / f"{name}.json").read_text())
    sources = _yaml(OBSERVABILITY / "grafana" / "provisioning" / "datasources" / "datasources.yaml")["datasources"]
    assert {source["uid"] for source in sources} == DATASOURCE_UIDS
    panels = dashboard["panels"]
    assert len({panel["id"] for panel in panels}) == len(panels)
    for panel in panels:
        assert panel["title"]
        assert panel["datasource"]["uid"] in DATASOURCE_UIDS
        assert all(target["datasource"]["uid"] in DATASOURCE_UIDS for target in panel["targets"])
