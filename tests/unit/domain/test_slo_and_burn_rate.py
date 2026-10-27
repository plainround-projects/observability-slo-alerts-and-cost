from datetime import timedelta

import pytest

from slo_lab.domain.burn_rate import BurnRatePolicy, BurnRateRule, prometheus_duration
from slo_lab.domain.errors import InvalidPolicyError, InvalidSloError
from slo_lab.domain.slo import Slo

GOOD = 'sum(rate(http_requests_total{status!~"5.."}[__WINDOW__]))'
VALID = "sum(rate(http_requests_total[__WINDOW__]))"
AVAILABILITY = Slo("availability", 0.995, 30, "d", GOOD, VALID)
LATENCY = Slo("latency", 0.95, 30, "d", GOOD, VALID)


def test_a_99_5_percent_objective_over_30_days_allows_216_minutes_of_outage():
    assert AVAILABILITY.error_budget == pytest.approx(0.005)
    assert AVAILABILITY.budget_minutes() == pytest.approx(216.0)


def test_at_500_requests_a_minute_the_budget_is_108_thousand_bad_requests():
    assert AVAILABILITY.budget_requests(500) == pytest.approx(108_000)


@pytest.mark.parametrize("objective", [0.0, 1.0, 1.5, -0.1])
def test_an_objective_outside_the_open_unit_interval_is_rejected(objective):
    with pytest.raises(InvalidSloError):
        Slo("x", objective, 30, "d", GOOD, VALID)


def test_a_window_below_one_day_or_expressions_without_the_token_are_rejected():
    with pytest.raises(InvalidSloError):
        Slo("x", 0.99, 0, "d", GOOD, VALID)
    with pytest.raises(InvalidSloError, match="__WINDOW__"):
        Slo("x", 0.99, 30, "d", "sum(up)", VALID)


def test_the_error_ratio_expression_puts_the_window_into_both_halves():
    expression = AVAILABILITY.error_ratio_expression("1h")
    assert expression.startswith("(1 - (")
    assert expression.count("[1h]") == 2
    assert "__WINDOW__" not in expression


def test_fast_burn_of_14_4_over_an_hour_spends_2_percent_of_a_30_day_budget():
    rule = BurnRateRule("page", 14.4, timedelta(hours=1), timedelta(minutes=5))
    assert rule.budget_consumed(AVAILABILITY) == pytest.approx(0.02)
    assert rule.error_ratio_threshold(AVAILABILITY) == pytest.approx(0.072)
    assert rule.hours_to_exhaustion(AVAILABILITY) == pytest.approx(50.0)


def test_the_lessons_slow_burn_spends_5_percent_and_the_tuned_one_1_4_percent():
    lesson = BurnRateRule("ticket", 6, timedelta(hours=6), timedelta(minutes=30))
    tuned = BurnRateRule("ticket", 5, timedelta(hours=2), timedelta(minutes=15))
    assert lesson.budget_consumed(AVAILABILITY) == pytest.approx(0.05)
    assert tuned.budget_consumed(AVAILABILITY) == pytest.approx(0.0139, abs=1e-4)
    assert tuned.error_ratio_threshold(AVAILABILITY) == pytest.approx(0.025)


def test_the_same_burn_rate_is_a_much_larger_error_ratio_for_a_looser_objective():
    rule = BurnRateRule("page", 14.4, timedelta(hours=1), timedelta(minutes=5))
    assert rule.error_ratio_threshold(LATENCY) == pytest.approx(0.72)


@pytest.mark.parametrize(
    ("severity", "rate", "long", "short"),
    [
        ("page", 1.0, timedelta(hours=1), timedelta(minutes=5)),
        ("page", 14.4, timedelta(minutes=5), timedelta(hours=1)),
        ("page", 14.4, timedelta(hours=1), timedelta(hours=1)),
        ("sms", 14.4, timedelta(hours=1), timedelta(minutes=5)),
    ],
)
def test_inconsistent_rules_are_rejected(severity, rate, long, short):
    with pytest.raises(InvalidPolicyError):
        BurnRateRule(severity, rate, long, short)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (timedelta(hours=1), "1h"),
        (timedelta(minutes=5), "5m"),
        (timedelta(hours=6), "6h"),
        (timedelta(days=2), "2d"),
        (timedelta(seconds=90), "90s"),
    ],
)
def test_durations_are_written_the_way_promql_writes_them(value, text):
    assert prometheus_duration(value) == text


def test_a_policy_keeps_its_rules_in_order():
    rules = (
        BurnRateRule("page", 14.4, timedelta(hours=1), timedelta(minutes=5)),
        BurnRateRule("ticket", 5, timedelta(hours=2), timedelta(minutes=15)),
    )
    assert BurnRatePolicy("p", "d", rules).rules[0].severity == "page"
