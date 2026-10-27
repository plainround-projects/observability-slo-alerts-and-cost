from datetime import timedelta

from slo_lab.domain.alerts import AlertRule, firing_episodes
from slo_lab.domain.pages import Page, classify
from slo_lab.domain.traffic import Incident

RULE = AlertRule("HighCPU", "x", 120.0, "page")


def _evals(pattern, step=60.0):
    return [(index * step, flag) for index, flag in enumerate(pattern)]


def test_an_alert_fires_only_after_the_expression_has_been_true_for_the_for_duration():
    episodes = firing_episodes(RULE, 60.0, _evals([0, 1, 1, 1, 1, 0, 0]))
    assert len(episodes) == 1
    assert (episodes[0].started_at, episodes[0].ended_at) == (180.0, 300.0)


def test_a_condition_shorter_than_the_for_duration_never_fires():
    assert firing_episodes(RULE, 60.0, _evals([0, 1, 1, 0, 1, 0])) == []


def test_each_separate_stretch_is_its_own_episode_and_so_its_own_page():
    episodes = firing_episodes(RULE, 60.0, _evals([1, 1, 1, 0, 1, 1, 1, 1]))
    assert [episode.started_at for episode in episodes] == [120.0, 360.0]


def test_a_zero_for_duration_fires_on_the_first_true_evaluation_and_open_episodes_are_closed():
    rule = AlertRule("AnyError", "x", 0.0, "page")
    episodes = firing_episodes(rule, 60.0, _evals([0, 1, 1]))
    assert (episodes[0].started_at, episodes[0].ended_at) == (60.0, 180.0)


def _incident(name, start_minutes, duration_minutes, user_facing):
    return Incident(name, timedelta(minutes=start_minutes), timedelta(minutes=duration_minutes), user_facing)


INCIDENTS = (
    _incident("outage", 100, 10, True),
    _incident("degradation", 300, 120, True),
    _incident("cpu", 600, 5, False),
)


def _page(alert, minutes, severity="page"):
    return Page(alert, severity, timedelta(minutes=minutes))


def test_pages_inside_a_user_facing_incident_or_its_grace_period_are_actionable():
    report = classify([_page("A", 105), _page("A", 120), _page("B", 350), _page("A", 130)], INCIDENTS)
    assert (report.total, report.actionable) == (4, 3)
    assert report.actionable_share == 0.75


def test_false_alarms_are_attributed_to_the_quiet_incident_or_to_baseline_noise():
    report = classify([_page("C", 602), _page("C", 50), _page("C", 52), _page("A", 105)], INCIDENTS)
    assert report.false_alarm_causes == {"cpu": 1, "ordinary baseline noise": 2}
    assert report.by_alert == {"C": 3, "A": 1}


def test_coverage_reports_the_first_alert_and_its_delay_for_each_user_facing_incident():
    report = classify([_page("A", 106), _page("S", 340, "ticket"), _page("A", 108)], INCIDENTS)
    assert [item.incident for item in report.coverage] == ["outage", "degradation"]
    assert (report.coverage[0].first_alert, report.coverage[0].delay) == ("A", timedelta(minutes=6))
    assert report.coverage[0].first_page_delay == timedelta(minutes=6)
    assert report.coverage[1].first_severity == "ticket"
    assert report.coverage[1].first_page_delay is None
    assert report.every_user_facing_incident_detected


def test_an_incident_without_an_alert_is_reported_as_undetected():
    report = classify([_page("A", 106)], INCIDENTS)
    assert not report.coverage[1].detected
    assert report.coverage[1].delay is None
    assert not report.every_user_facing_incident_detected


def test_no_pages_at_all_is_an_actionable_share_of_one():
    assert classify([], INCIDENTS).actionable_share == 1.0
