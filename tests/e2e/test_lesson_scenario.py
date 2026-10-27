"""The lesson's six steps against the real stack, asserted number by number."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from slo_lab.domain.traffic import week_incidents
from slo_lab.infrastructure.prometheus_http import PrometheusRangeEvaluator
from slo_lab.lab.compose import Compose
from slo_lab.lab.live import FAST_BURN_ALERT, LiveOutcome, run_live_outage
from slo_lab.lab.replay import DAYS, ReplayReport, WeekReplay, load_alert_sets
from slo_lab.lab.stores import LokiClient, PagerClient, TempoClient
from slo_lab.lab.volume import VolumeReport, VolumeScenario

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
ENV = os.environ
RULE_FILES = {
    "threshold": "observability/prometheus/replay/threshold.rules.yml",
    "burn_rate_lesson": "observability/prometheus/replay/burn_rate_lesson.rules.yml",
    "burn_rate": "observability/prometheus/rules/burn_rate.rules.yml",
}


@pytest.fixture(scope="module")
def replay(tmp_path_factory: pytest.TempPathFactory) -> ReplayReport:
    start_epoch = int(time.time()) // 3600 * 3600 - DAYS * 86400
    week = WeekReplay(
        Compose(ROOT, ENV),
        PrometheusRangeEvaluator(f"http://127.0.0.1:{ENV.get('REPLAY_HOST_PORT', '9091')}"),
        tmp_path_factory.mktemp("replay"),
        log=lambda _: None,
    )
    week.prepare(start_epoch)
    return week.evaluate(load_alert_sets(ROOT, RULE_FILES), start_epoch)


@pytest.fixture(scope="module")
def volume() -> VolumeReport:
    return VolumeScenario(
        Compose(ROOT, ENV),
        TempoClient(f"http://127.0.0.1:{ENV.get('TEMPO_HOST_PORT', '3200')}"),
        LokiClient(f"http://127.0.0.1:{ENV.get('LOKI_HOST_PORT', '3100')}"),
        f"http://127.0.0.1:{ENV.get('SERVICE_HOST_PORT', '28083')}",
        requests=3000,
        log=lambda _: None,
    ).run()


def test_step_1_the_threshold_alerts_are_mostly_noise(replay):
    before = replay.reports["threshold"]
    assert before.total >= 100
    assert before.actionable_share < 0.2
    assert before.false_alarm_causes["cpu-spike-no-impact"] >= 1
    assert before.false_alarm_causes["ordinary baseline noise"] >= 50


def test_step_3_the_fast_burn_page_comes_within_minutes_of_the_outage_and_the_blip_pages_no_one(replay):
    after = replay.reports["burn_rate"]
    outage = next(item for item in after.coverage if item.incident == "full-outage")
    assert outage.first_page_delay is not None
    assert outage.first_page_delay.total_seconds() <= 10 * 60
    blip = next(incident for incident in week_incidents() if incident.name == "blip-90s")
    paged_in_blip = [
        page for page in replay.pages["burn_rate"] if blip.start <= page.offset <= blip.end + 15 * blip.duration
    ]
    assert paged_in_blip == []


def test_step_3_a_slow_leak_opens_a_ticket_and_the_lessons_own_numbers_miss_it(replay):
    degradation = next(item for item in replay.reports["burn_rate"].coverage if item.incident == "partial-degradation")
    assert degradation.first_severity == "ticket"
    lesson = next(
        item for item in replay.reports["burn_rate_lesson"].coverage if item.incident == "partial-degradation"
    )
    assert not lesson.detected


def test_step_4_far_fewer_notifications_all_actionable_and_every_real_incident_alerted(replay):
    comparison = replay.compare("threshold", "burn_rate")
    assert comparison.improved
    assert comparison.after.total <= 10
    assert comparison.after.actionable_share == 1.0
    assert "cpu-spike-no-impact" not in comparison.after.false_alarm_causes


def test_step_5_tail_sampling_cuts_stored_traces_and_keeps_every_planted_error_and_slow_request(volume):
    assert volume.trace_reduction >= 0.6
    assert volume.protected.errors_found == volume.protected.errors_sent > 0
    assert volume.protected.slow_found == volume.protected.slow_sent > 0
    assert 0.0 < volume.protected.ordinary_kept_share <= 0.15
    assert volume.raw.ordinary_kept_share >= 0.9


def test_step_6_the_raw_gateway_stores_the_test_values_and_the_protected_one_stores_none(volume):
    raw, protected = volume.raw, volume.protected
    assert min(raw.email_in_traces, raw.card_in_traces, raw.email_in_logs, raw.card_in_logs) > 0
    assert (protected.email_in_traces, protected.card_in_traces) == (0, 0)
    assert (protected.email_in_logs, protected.card_in_logs) == (0, 0)


def test_the_live_stack_pages_the_fake_pager_through_alertmanager_on_a_real_outage():
    outcome: LiveOutcome = run_live_outage(
        PagerClient(f"http://127.0.0.1:{ENV.get('PAGER_HOST_PORT', '28084')}"),
        f"http://127.0.0.1:{ENV.get('SERVICE_HOST_PORT', '28083')}",
        warm_seconds=90,
        outage_seconds=150,
        log=lambda _: None,
    )
    assert outcome.page is not None
    assert outcome.page["alert"] == FAST_BURN_ALERT
    assert outcome.seconds_to_page is not None
    assert outcome.seconds_to_page < 6 * 60
