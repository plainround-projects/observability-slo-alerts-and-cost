from datetime import timedelta

import pytest

from slo_lab.application.compare import Comparison
from slo_lab.application.place_checkout import PlaceCheckout
from slo_lab.application.replay_alerts import ReplayAlerts
from slo_lab.application.rule_builder import alert_name, build_alert_rules, expression, rule_file_document
from slo_lab.domain.alerts import AlertRule
from slo_lab.domain.burn_rate import BurnRatePolicy, BurnRateRule
from slo_lab.domain.checkout import CheckoutFailedError, CheckoutRequest, OrderLine, Simulation
from slo_lab.domain.pages import IncidentCoverage, PageReport
from slo_lab.domain.slo import Slo

GOOD = 'sum(rate(http_requests_total{status!~"5.."}[__WINDOW__]))'
VALID = "sum(rate(http_requests_total[__WINDOW__]))"
SLO = Slo("availability", 0.995, 30, "d", GOOD, VALID)
PAGE = BurnRateRule("page", 14.4, timedelta(hours=1), timedelta(minutes=5))
TICKET = BurnRateRule("ticket", 5, timedelta(hours=2), timedelta(minutes=15))
POLICY = BurnRatePolicy("default", "d", (PAGE, TICKET))


def test_rule_names_say_the_slo_and_the_speed():
    assert alert_name(SLO, PAGE) == "AvailabilityBudgetFastBurn"
    assert alert_name(SLO, TICKET) == "AvailabilityBudgetSlowBurn"


def test_the_expression_needs_both_windows_above_the_derived_threshold():
    text = expression(SLO, PAGE)
    assert text.count("> 0.072") == 2
    assert "[1h]" in text
    assert "[5m]" in text
    assert "\nand\n" in text


def test_one_alert_per_slo_and_rule_with_no_for_duration():
    rules = build_alert_rules([SLO], POLICY)
    assert [(rule.name, rule.severity, rule.for_seconds) for rule in rules] == [
        ("AvailabilityBudgetFastBurn", "page", 0.0),
        ("AvailabilityBudgetSlowBurn", "ticket", 0.0),
    ]


def test_the_rule_file_document_has_labels_and_a_summary_that_states_the_budget_spent():
    document = rule_file_document([SLO], POLICY)
    group = document["groups"][0]
    assert group["name"] == "slo-burn-rate-default"
    first = group["rules"][0]
    assert first["labels"] == {"severity": "page", "slo": "availability"}
    assert "14.4x" in first["annotations"]["summary"]
    assert "2.0% of the 30-day budget in 1h" in first["annotations"]["summary"]


class StubEvaluator:
    def __init__(self, true_minutes):
        self.true_minutes = true_minutes

    def true_at(self, expression, start, end, step_seconds):
        return {start + minute * 60 for minute in self.true_minutes}


def test_replay_turns_each_firing_episode_into_one_page_at_its_offset():
    rule = AlertRule("A", "x", 120.0, "page")
    pages = ReplayAlerts(StubEvaluator([10, 11, 12, 13, 30, 31])).run([rule], 1000.0, 1000.0 + 3600, 60.0)
    assert [page.offset for page in pages] == [timedelta(minutes=12)]
    assert (pages[0].alert, pages[0].severity) == ("A", "page")


def test_replay_orders_pages_from_several_rules_by_time():
    first = AlertRule("Late", "x", 0.0, "page")
    second = AlertRule("Early", "y", 0.0, "ticket")

    class Two:
        def true_at(self, expression, start, end, step_seconds):
            return {start + (50 if expression == "x" else 5) * 60}

    pages = ReplayAlerts(Two()).run([first, second], 0.0, 3600.0, 60.0)
    assert [page.alert for page in pages] == ["Early", "Late"]


def _report(total, actionable, detected=True):
    delay = timedelta(minutes=5) if detected else None
    coverage = (IncidentCoverage("outage", "A" if detected else None, "page", delay, delay),)
    return PageReport(total, actionable, {}, {}, coverage)


def test_a_quieter_more_actionable_set_that_misses_nothing_is_an_improvement():
    assert Comparison(_report(300, 60), _report(3, 3)).improved


def test_a_quieter_set_that_misses_an_incident_is_not_an_improvement():
    comparison = Comparison(_report(300, 60), _report(1, 1, detected=False))
    assert comparison.fewer_notifications
    assert not comparison.nothing_real_missed
    assert not comparison.improved


class Annotator:
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value


class Delay:
    def __init__(self):
        self.waited = []

    def wait(self, seconds):
        self.waited.append(seconds)


class References:
    def next(self):
        return "ord-000001"


def _use_case(annotator=None, delay=None):
    return PlaceCheckout(annotator or Annotator(), delay or Delay(), References(), slow_seconds=1.4)


LINES = (OrderLine("SKU-001", 2), OrderLine("SKU-002", 3))


def test_an_ordinary_checkout_returns_a_receipt_and_logs_one_completion_line(caplog):
    with caplog.at_level("INFO"):
        receipt = _use_case().execute(CheckoutRequest(LINES))
    assert (receipt.reference, receipt.item_count) == ("ord-000001", 5)
    assert [record.getMessage() for record in caplog.records] == ["checkout completed"]


def test_the_naive_use_case_puts_the_email_and_card_on_the_span_and_in_a_log_line(caplog):
    annotator = Annotator()
    with caplog.at_level("INFO"):
        _use_case(annotator).execute(CheckoutRequest(LINES, "a@example.com", "4242424242424242"))
    assert annotator.values == {"customer.email": "a@example.com", "payment.card": "4242424242424242"}
    assert any("4242424242424242" in record.getMessage() for record in caplog.records)


def test_a_slow_simulation_waits_and_an_error_simulation_raises():
    delay = Delay()
    _use_case(delay=delay).execute(CheckoutRequest(LINES, simulation=Simulation.SLOW))
    assert delay.waited == [1.4]
    with pytest.raises(CheckoutFailedError):
        _use_case().execute(CheckoutRequest(LINES, simulation=Simulation.ERROR))
