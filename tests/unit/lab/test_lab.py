import json
import random
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from slo_lab.domain.alerts import AlertRule
from slo_lab.domain.pages import Page
from slo_lab.domain.traffic import Incident, week_incidents
from slo_lab.lab.analyze_cli import main as analyze_main
from slo_lab.lab.compose import Compose, ComposeError, run_command
from slo_lab.lab.live import FAST_BURN_ALERT, run_live_outage
from slo_lab.lab.replay import AlertSet, WeekReplay, pages_from_lines, pages_to_lines, render_report
from slo_lab.lab.rules_cli import main as rules_main
from slo_lab.lab.stores import decode_id, metric_value
from slo_lab.lab.traffic import TEST_CARD, TEST_EMAIL, TrafficPlan, TrafficResult, build_body, classify, send_traffic
from slo_lab.lab.volume import PROTECTED_CONFIG, RAW_CONFIG, VolumeScenario
from slo_lab.lab.volume import render_report as render_volume

ROOT = Path(__file__).resolve().parents[3]


def test_metric_value_sums_every_label_set_of_one_metric():
    text = 'a_total{tenant="x"} 10\na_total{tenant="y"} 5.5\nab_total 99\n# a_total 1\na_total 1e2\n'
    assert metric_value(text, "a_total") == 115.5
    assert metric_value(text, "missing") == 0


def test_ids_come_back_as_hex_from_hex_or_base64():
    assert decode_id("00F067AA0BA902B7") == "00f067aa0ba902b7"
    assert decode_id("APBnqgupArc=") == "00f067aa0ba902b7"


def test_the_traffic_mix_is_seeded_and_the_outage_mode_fails_everything():
    plan = TrafficPlan("http://x", 1000, seed=1)
    kinds = [classify(random.Random(1), plan) for _ in range(3)]
    assert kinds == [classify(random.Random(1), plan) for _ in range(3)]
    rng = random.Random(2)
    drawn = [classify(rng, plan) for _ in range(5000)]
    assert 100 < drawn.count("error") < 200
    assert 20 < drawn.count("slow") < 90
    assert 150 < drawn.count("personal") < 350
    assert {classify(random.Random(3), TrafficPlan("http://x", 1, 3, outage=True)) for _ in range(20)} == {"error"}


def test_request_bodies_carry_the_planted_personal_data_and_simulations():
    assert build_body("personal", 3)["customer_email"] == TEST_EMAIL
    assert build_body("personal", 3)["card_number"] == TEST_CARD
    assert build_body("error", 0)["simulate"] == "error"
    assert build_body("slow", 0)["simulate"] == "slow"
    assert "simulate" not in build_body("ordinary", 0)


def _server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            status = 500 if body.get("simulate") == "error" else 200
            self.send_response(status)
            self.send_header("X-Trace-Id", f"{len(seen):032x}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_send_traffic_sorts_trace_ids_by_kind_and_paces_requests():
    server, _ = _server()
    waits: list[float] = []
    try:
        plan = TrafficPlan(
            f"http://127.0.0.1:{server.server_address[1]}", 200, seed=4, rate_per_second=100.0, concurrency=4
        )
        result = send_traffic(plan, clock=lambda: 0.0, sleep=waits.append)
    finally:
        server.shutdown()
    assert result.sent == 200
    assert len(result.all_traces) == 200
    assert len(result.error_traces) == result.statuses.get(500, 0) > 0
    assert len(result.personal_data_traces) > 0
    assert len(waits) == 199
    assert waits[0] == pytest.approx(0.01)


def test_a_dead_service_counts_as_status_zero_with_no_trace_ids():
    result = send_traffic(TrafficPlan("http://127.0.0.1:9", 3, seed=1, concurrency=2))
    assert result.statuses == {0: 3}
    assert result.all_traces == []


class FakeCompose:
    def __init__(self):
        self.configs = []

    def use_gateway_config(self, config_path):
        self.configs.append(config_path)


class FakeTempo:
    """Holds every trace in the raw pass; in the protected pass it drops the ordinary ones and masks the values."""

    def __init__(self, compose):
        self.compose = compose
        self.bytes = 0.0

    def _raw(self):
        return self.compose.configs[-1] == RAW_CONFIG

    def bytes_received(self):
        self.bytes += 1000.0 if self._raw() else 100.0
        return self.bytes

    def trace_json(self, trace_id):
        return json.dumps({"id": trace_id, "customer.email": TEST_EMAIL if self._raw() else "<email>"})

    def search(self, traceql, start, end, limit=0):
        everything = ["e1", "e2", "s1", "p1", "p2"] + [f"o{i}" for i in range(20)]
        kept = everything if self._raw() else [t for t in everything if not t.startswith("o")]
        if "customer.email" in traceql:
            return ["p1", "p2"] if self._raw() else []
        if "payment.card" in traceql:
            return ["p1", "p2"] if self._raw() else []
        return kept


class FakeLoki:
    def __init__(self, compose):
        self.compose = compose
        self.volume = 0.0

    def bytes_received(self):
        self.volume += 500.0
        return self.volume

    def query(self, logql, start, end, limit=0):
        return [object()] * 3 if self.compose.configs[-1] == RAW_CONFIG else []


def _fake_send(plan):
    return TrafficResult(
        error_traces=["e1", "e2"], slow_traces=["s1"], personal_data_traces=["p1", "p2"],
        ordinary_traces=[f"o{i}" for i in range(20)], statuses={200: 23, 500: 2}, started_at=0.0, finished_at=1.0,
    )  # fmt: skip


def test_the_volume_scenario_compares_a_raw_gateway_with_a_protected_one():
    compose = FakeCompose()
    scenario = VolumeScenario(
        compose,
        FakeTempo(compose),
        FakeLoki(compose),
        "http://x",
        25,
        send=_fake_send,
        log=lambda _: None,
        settle_seconds=0.0,
    )
    report = scenario.run()
    assert compose.configs == [RAW_CONFIG, PROTECTED_CONFIG]
    assert (report.raw.email_in_traces, report.raw.card_in_traces, report.raw.email_in_logs) == (2, 2, 3)
    assert (report.protected.email_in_traces, report.protected.card_in_traces, report.protected.email_in_logs) == (
        0,
        0,
        0,
    )
    assert (report.protected.errors_found, report.protected.slow_found) == (2, 1)
    assert report.protected.ordinary_found == 0
    assert report.raw.ordinary_kept_share == 1.0
    assert report.trace_reduction == pytest.approx(0.9)
    table = render_volume(report)
    assert "reduced by 90.0%" in table
    assert "test card found in logs" in table


class FakePager:
    def __init__(self, page):
        self.page, self.cleared = page, False

    def clear(self):
        self.cleared = True

    def wait_for_page(self, alert, timeout_seconds):
        assert alert == FAST_BURN_ALERT
        return self.page


def test_the_live_check_reports_how_long_the_page_took():
    import time

    pager = FakePager({"alert": FAST_BURN_ALERT, "received_at": time.time() + 100})

    def send(plan):
        return TrafficResult(statuses={200: plan.requests})

    outcome = run_live_outage(
        pager, "http://x", warm_seconds=2, outage_seconds=3, rate_per_second=10, send=send, log=lambda _: None
    )
    assert pager.cleared
    assert (outcome.warm_requests, outcome.outage_requests) == (20, 30)
    assert outcome.seconds_to_page is not None
    assert outcome.seconds_to_page > 90


def test_the_live_check_reports_a_missing_page():
    outcome = run_live_outage(
        FakePager(None), "http://x", 1, 1, 10, send=lambda plan: TrafficResult(statuses={200: 1}), log=lambda _: None
    )
    assert outcome.page is None
    assert outcome.seconds_to_page is None


def test_pages_survive_a_round_trip_through_the_pager_log_format():
    pages = [Page("A", "page", timedelta(minutes=5)), Page("B", "ticket", timedelta(hours=2))]
    assert pages_from_lines(pages_to_lines(pages)) == pages
    assert pages_from_lines("\n\n") == []


class FakeReplayCompose:
    def __init__(self):
        self.steps = []

    def backfill_replay_data(self, path):
        self.steps.append(("backfill", path.name, path.exists()))

    def start_replay_prometheus(self):
        self.steps.append(("start",))


class FakeEvaluator:
    def ready(self):
        return True

    def true_at(self, expression, start, end, step_seconds):
        return {start + minute * 60 for minute in range(1805, 1815)} if expression == "outage" else set()


def test_the_week_replay_backfills_then_evaluates_and_writes_a_pager_log_per_alert_set(tmp_path):
    compose = FakeReplayCompose()
    replay = WeekReplay(compose, FakeEvaluator(), tmp_path, incidents=week_incidents(), log=lambda _: None)
    start = 1_800_000_000
    replay.prepare(start)
    assert compose.steps == [("backfill", "week.om", True), ("start",)]
    assert (tmp_path / "week.om").read_text().rstrip().endswith("# EOF")
    sets = [
        AlertSet("quiet", (AlertRule("Quiet", "nothing", 0.0, "page"),)),
        AlertSet("loud", (AlertRule("Loud", "outage", 0.0, "page"),)),
    ]
    report = replay.evaluate(sets, start)
    assert report.reports["quiet"].total == 0
    assert report.reports["loud"].total == 1
    assert report.reports["loud"].actionable == 1
    assert (tmp_path / "pages_loud.jsonl").exists()
    text = render_report(report)
    assert "loud: 1 notifications, 1 actionable (100.0%)" in text
    assert "full-outage: first page (Loud) 5 min after onset\n" in text + "\n"
    assert "partial-degradation: NO ALERT" in text
    assert report.compare("quiet", "loud").before.total == 0


def test_the_analyze_command_reads_a_pager_log(tmp_path, capsys):
    log = tmp_path / "pages.jsonl"
    log.write_text(
        pages_to_lines(
            [Page("AnyError", "page", timedelta(minutes=1805)), Page("AnyError", "page", timedelta(minutes=50))]
        )
    )
    assert analyze_main([str(log), "--name", "before"]) == 0
    output = capsys.readouterr().out
    assert "before: 2 notifications, 1 actionable (50.0%)" in output
    assert "false alarms during ordinary baseline noise: 1" in output


def test_rules_are_generated_and_the_check_flags_a_stale_file(tmp_path, capsys):
    for relative in ("observability/slo/slos.yaml", "observability/slo/policies.yaml"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / relative).read_text())
    assert rules_main(["--root", str(tmp_path), "--check"]) == 1
    assert "stale" in capsys.readouterr().out
    assert rules_main(["--root", str(tmp_path)]) == 0
    assert rules_main(["--root", str(tmp_path), "--check"]) == 0
    generated = (tmp_path / "observability/prometheus/rules/burn_rate.rules.yml").read_text()
    assert generated == (ROOT / "observability/prometheus/rules/burn_rate.rules.yml").read_text()


def test_the_committed_generated_rules_are_current():
    assert rules_main(["--root", str(ROOT), "--check"]) == 0


def test_compose_commands_pass_the_gateway_config_and_backfill_runs_promtool(tmp_path):
    seen = []
    compose = Compose(tmp_path, {"KEEP": "1"}, runner=lambda command, env, cwd: seen.append((list(command), dict(env))))
    compose.use_gateway_config("./observability/collector/gateway.raw.yaml")
    assert seen[0][1]["GATEWAY_CONFIG"] == "./observability/collector/gateway.raw.yaml"
    assert seen[0][1]["KEEP"] == "1"
    seen.clear()
    compose.backfill_replay_data(tmp_path / "week.om")
    flat = [" ".join(command) for command, _ in seen]
    assert any("tsdb create-blocks-from openmetrics --max-block-duration=24h" in line for line in flat)
    assert any("volume rm -f" in line for line in flat)
    seen.clear()
    compose.start_replay_prometheus()
    assert "--profile replay up -d --wait prometheus-replay" in " ".join(seen[0][0])
    with pytest.raises(ComposeError, match="boom"):
        run_command(["sh", "-c", "echo boom >&2; exit 3"], {}, tmp_path)


def test_incident_helper_is_a_value_object():
    assert Incident("x", timedelta(0), timedelta(minutes=1), True).end == timedelta(minutes=1)


def test_the_volume_scenario_waits_for_the_gateway_before_sending_and_gives_up_after_the_timeout(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    compose = FakeCompose()
    polls: list[bool] = []
    polls_before_each_send: list[int] = []
    answers = iter([False, False, True])

    def ready() -> bool:
        polls.append(True)
        return next(answers, True)

    def send(plan):
        polls_before_each_send.append(len(polls))
        return _fake_send(plan)

    VolumeScenario(
        compose, FakeTempo(compose), FakeLoki(compose), "http://x", 25,
        send=send, log=lambda _: None, settle_seconds=0.0, gateway_ready=ready,
    ).run()  # fmt: skip
    assert polls_before_each_send == [3, 4]
    never = VolumeScenario(
        FakeCompose(), FakeTempo(compose), FakeLoki(compose), "http://x", 25,
        send=_fake_send, log=lambda _: None, gateway_ready=lambda: False, ready_timeout_seconds=0.0,
    )  # fmt: skip
    with pytest.raises(TimeoutError):
        never.run()
