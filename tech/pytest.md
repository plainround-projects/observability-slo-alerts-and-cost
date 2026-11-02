# pytest (with pytest-cov and coverage)

## What it is

pytest is a Python library and command that finds and runs **tests**. A test is a function whose name starts with `test_`. It sets up some data, calls the code being tested, and uses `assert` to state what must be true. If an `assert` is false, or any error escapes, the test is reported as failed; otherwise it passes. pytest also gives you **fixtures** (reusable setup code that tests receive by naming it as a parameter), **parametrization** (one test run with many inputs) and **markers** (labels on tests so you can run a subset).

## Why this repository uses it

Everything the lesson claims is a number, and the tests check the numbers at three levels. **Unit tests** prove the arithmetic and the logic (a 99.5 percent objective allows 216 minutes; a 14.4× burn over an hour spends 2 percent; an alert fires only after its `for` duration; the traffic simulation is repeatable) and run the three scenarios against fakes. **Configuration tests** lint the YAML and JSON as code, including the processor order of the gateway and the redaction patterns on sample text. **Integration and end-to-end tests** run the real Collector image, the live stack, the replay of the week, the volume comparison and a real outage with a real page.

## Where it appears

| Path | What it holds |
| --- | --- |
| `tests/conftest.py` | The shared in-memory OpenTelemetry fixture. |
| `tests/unit/domain`, `application`, `infrastructure`, `interfaces`, `lab` | Unit tests, no containers. |
| `tests/observability/test_configuration_as_code.py` | Lints compose, both gateways, Prometheus, Alertmanager, the SLO document and the dashboards. |
| `tests/integration/test_running_stack.py`, `test_redaction_in_the_collector.py` | The running stack, and the real Collector image (marker `integration`). |
| `tests/e2e/test_lesson_scenario.py` | The lesson's steps against the real stack (marker `e2e`). |
| `pyproject.toml` | `[tool.pytest.ini_options]` and `[tool.coverage.*]`. |

## How it works underneath

`pytest` starts one process. It reads the configuration in `pyproject.toml`, walks the folders listed in `testpaths`, imports every file named `test_*.py` and collects every function named `test_*`. Before it runs a test it looks at the parameter names: for each name it finds a fixture function of that name (in the test file, or in `conftest.py` files in parent folders), runs it, and passes the result in. If a fixture uses `yield`, the code after the `yield` runs after the test, as cleanup.

pytest rewrites `assert` statements while it imports test files, so a failed `assert a == b` prints both values and where they differ.

`monkeypatch` is a built-in fixture that changes an attribute or environment variable for one test and puts it back afterwards. `caplog` captures log records. `tmp_path` is a fresh temporary folder. `pytest.raises(SomeError)` is a context manager that passes only if the block raises that error.

**Coverage** measures which lines of your code ran during the tests. `pytest-cov` plugs the `coverage` tool into pytest.

**Unit tests** test one piece without the outside world (fakes stand in for the database and network). **Integration tests** test pieces against real running services. **End-to-end tests** run the whole scenario the way a user would. This repository keeps the three kinds in three folders and uses markers so that `make test` runs only the first kind.

## Walkthrough of this repository's use

### Configuration in `pyproject.toml`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
    "integration: needs the stack from docker-compose.yml (make up)",
    "e2e: replays the week, sends live traffic and queries the stores (make up first)",
]

[tool.coverage.run]
source = ["slo_lab"]
branch = true

[tool.coverage.report]
show_missing = true
skip_covered = false
```

`testpaths = ["tests"]`; `addopts = "-ra --strict-markers"` prints a summary for every non-passing test and turns unknown markers into errors; the markers `integration` (needs the stack) and `e2e` (replays the week, sends live traffic, queries the stores) are declared. `[tool.coverage.run]` measures only `slo_lab` and counts branches; `[tool.coverage.report]` prints missing line numbers. `make test` fails if the domain and application layers fall under 95 percent.

### `tests/conftest.py`

```python
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
```

The in-memory OpenTelemetry fixture used by tests that need spans or metrics: a tracer provider with an in-memory span exporter, a meter provider with an in-memory reader, bundled in a dataclass and yielded; the code after `yield` shuts them down. A test asks for it by naming `memory_telemetry`.

### Domain

```python
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

```

`GOOD` and `VALID` are minimal SLI expressions with the token; `AVAILABILITY` and `LATENCY` are two `Slo` objects. A 99.5 percent objective over 30 days gives a budget of 0.005 and 216 minutes (`pytest.approx` compares floats with a tolerance); at 500 requests a minute the budget is 108,000 bad requests.

```python
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

```

Objectives of 0, 1, 1.5 and −0.1 are rejected; a window under one day or expressions without the token are rejected (`match="__WINDOW__"` checks the message). The error-ratio expression puts the window in both halves (`count("[1h]") == 2`). A burn of 14.4 over an hour spends 2 percent, has threshold 0.072 and lasts 50 hours; the lesson's slow burn spends 5 percent, the tuned one about 1.4 percent with threshold 0.025; and the same burn rate is a 0.72 threshold for the looser latency objective.

```python
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
```

Four inconsistent rules are rejected (a burn rate of 1, windows in the wrong order, equal windows, an unknown severity); durations are written the way PromQL writes them (`1h`, `5m`, `6h`, `2d`, `90s`); a policy keeps its rules in order.

```python
from datetime import timedelta

from slo_lab.domain.traffic import TICK_SECONDS, Incident, TrafficProfile, simulate, week_incidents


def _run(days=1, incidents=(), seed=1, profile=None):
    return list(simulate(profile or TrafficProfile(), tuple(incidents), days, seed))


def test_a_week_has_20160_ticks_of_30_seconds():
    assert TICK_SECONDS == 30
    ticks = _run(days=7)
    assert len(ticks) == 7 * 86400 // 30
    assert ticks[1].offset - ticks[0].offset == timedelta(seconds=30)


def test_the_same_seed_gives_the_same_week_and_another_seed_a_different_one():
    assert _run(seed=3) == _run(seed=3)
    assert _run(seed=3) != _run(seed=4)


def test_ordinary_traffic_is_about_500_requests_a_minute_with_almost_no_errors():
    ticks = _run(days=1, seed=5)
    per_minute = sum(tick.requests for tick in ticks) / (24 * 60)
    errors = sum(tick.error_requests for tick in ticks)
    assert 450 <= per_minute <= 550
    assert errors / sum(tick.requests for tick in ticks) < 0.001
    assert all(tick.health_checks >= 0 for tick in ticks)


```

`_run` helps build a simulated period. A week has 20,160 ticks 30 seconds apart; the same seed gives the same week and another seed a different one (dataclass equality compares every field); ordinary traffic is about 500 requests a minute (450 to 550) with errors under 0.1 percent.

```python
def test_a_full_outage_makes_every_request_fail_only_while_it_lasts():
    outage = Incident("outage", timedelta(hours=1), timedelta(minutes=10), True, error_rate=1.0)
    ticks = _run(incidents=[outage], seed=6)
    inside = [t for t in ticks if outage.active_at(t.offset)]
    outside = [t for t in ticks if not outage.active_at(t.offset)]
    assert len(inside) == 20
    assert all(t.ok_requests == 0 and t.error_requests > 0 for t in inside)
    assert sum(t.error_requests for t in outside) < 0.001 * sum(t.requests for t in outside)


def test_a_partial_degradation_fails_about_three_percent():
    degradation = Incident("deg", timedelta(hours=2), timedelta(hours=2), True, error_rate=0.03)
    inside = [t for t in _run(incidents=[degradation], seed=7) if degradation.active_at(t.offset)]
    ratio = sum(t.error_requests for t in inside) / sum(t.requests for t in inside)
    assert 0.025 < ratio < 0.035


def test_a_cpu_spike_changes_cpu_and_nothing_else():
    spike = Incident("spike", timedelta(hours=3), timedelta(minutes=5), False, cpu_percent=95.0)
    ticks = _run(incidents=[spike], seed=8)
    inside = [t for t in ticks if spike.active_at(t.offset)]
    assert all(t.cpu_percent > 85 for t in inside)
    assert sum(t.error_requests for t in inside) <= 2


def test_ordinary_cpu_bursts_exist_but_ordinary_cpu_stays_below_80():
    ticks = _run(days=2, seed=9)
    high = [t for t in ticks if t.cpu_percent > 80]
    assert 0 < len(high) < len(ticks) * 0.1
    assert all(1.0 <= t.cpu_percent <= 100.0 for t in ticks)


def test_the_planted_week_has_the_lessons_three_incidents_and_a_90_second_blip():
    incidents = {incident.name: incident for incident in week_incidents()}
    assert set(incidents) == {"full-outage", "partial-degradation", "cpu-spike-no-impact", "blip-90s"}
    assert incidents["full-outage"].user_facing
    assert incidents["partial-degradation"].error_rate == 0.03
    assert not incidents["cpu-spike-no-impact"].user_facing
    assert incidents["blip-90s"].duration == timedelta(seconds=90)
    assert incidents["full-outage"].end == timedelta(minutes=1810)


def test_small_means_use_the_exact_poisson_sampler():
    profile = TrafficProfile(requests_per_minute=10.0)
    ticks = _run(days=1, profile=profile, seed=10)
    assert 8 <= sum(t.requests for t in ticks) / (24 * 60) <= 12
```

A full outage fails every request only while it lasts (20 ticks of 30 s inside a 10-minute window); a partial degradation fails about 3 percent (between 2.5 and 3.5); a CPU spike changes CPU and nothing else; ordinary CPU bursts exist but stay below 80 percent most of the time (under 10 percent of ticks); the planted week has exactly the four named incidents with the right flags and the outage ends at minute 1,810; small means use the exact Poisson sampler.

```python
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

```

`_evals(pattern)` turns a list of 0s and 1s into (time, flag) pairs one minute apart. An alert with `for` of 120 s fires only after the expression has been true for the for-duration: for `[0, 1, 1, 1, 1, 0, 0]` the episode runs from minute 3 to minute 5; a condition shorter than that never fires; each separate stretch is its own episode; a zero `for` fires on the first true evaluation and an open episode is closed at the end.

```python
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
```

`INCIDENTS` are an outage (minutes 100 to 110), a degradation (300 to 420) and a quiet CPU incident. Pages inside a user-facing incident or its 15-minute grace are actionable (3 of 4 → 0.75); false alarms are attributed to the quiet incident or to "ordinary baseline noise"; coverage reports the first alert, its severity and its delay, and the first *page* delay (a ticket may come first); an undetected incident is reported as such; with no pages the actionable share is 1.0.

### Application

```python
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

```

Building blocks: a minimal SLO, a page rule and a ticket rule, and a policy. Rule names say the SLO and the speed; the expression needs both windows above the derived threshold (`text.count("> 0.072") == 2`, `"[1h]"` and `"[5m]"` present, the `\nand\n` between); one alert per SLO and rule with no `for`; the rule-file document has labels and a summary stating the budget spent (`14.4x`, `2.0% of the 30-day budget in 1h`).

```python
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

```

`StubEvaluator` returns fixed minutes at which an expression is true. Replay turns each firing episode into one page at its offset (true at minutes 10 to 13 with a 2-minute `for` fires at minute 12; the stray 30 and 31 do not); the pages of several rules are ordered by time; `_report` builds page reports; a quieter, more actionable set that misses nothing is an improvement, one that misses an incident is not.

```python
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
```

Fakes for the annotator, delay and references. An ordinary checkout returns a receipt (`ord-000001`, 5 items) and writes one completion line; the naive use case puts the email and card on the span and into a log line (`caplog` captures the log: `any("4242424242424242" in record.getMessage() ...)`); a slow simulation waits 1.4 s; an error simulation raises.

### Infrastructure

```python
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest

from slo_lab.domain.pages import Page
from slo_lab.domain.traffic import Tick
from slo_lab.infrastructure.definitions_io import (
    DefinitionError,
    load_alert_rules,
    load_policies,
    load_slos,
    parse_duration,
    render_rule_file,
)
from slo_lab.infrastructure.openmetrics import write_week
from slo_lab.infrastructure.otel_telemetry import LATENCY_BUCKETS, OtelAnnotator, OtelRequestMetrics
from slo_lab.infrastructure.pager_store import JsonLinesPageStore
from slo_lab.infrastructure.prometheus_http import PrometheusError, PrometheusRangeEvaluator
from slo_lab.infrastructure.runtime import SequentialReferences, SleepDelay
from slo_lab.infrastructure.settings import PagerSettings, ServiceSettings, SettingsError

ROOT = Path(__file__).resolve().parents[3]


def test_service_settings_defaults_and_overrides():
    defaults = ServiceSettings.from_env({})
    assert (defaults.allow_simulation, defaults.slow_seconds, defaults.port) == (False, 1.4, 8080)
    custom = ServiceSettings.from_env({"ALLOW_SIMULATION": "true", "SLOW_SECONDS": "2", "SERVICE_PORT": "9000"})
    assert (custom.allow_simulation, custom.slow_seconds, custom.port) == (True, 2.0, 9000)


@pytest.mark.parametrize(
    "env", [{"ALLOW_SIMULATION": "maybe"}, {"SLOW_SECONDS": "-1"}, {"LOG_LEVEL": "LOUD"}, {"SERVICE_PORT": "x"}]
)
def test_invalid_service_settings_are_rejected(env):
    with pytest.raises(SettingsError):
        ServiceSettings.from_env(env)


def test_pager_settings():
    assert PagerSettings.from_env({"PAGES_FILE": "/tmp/p.jsonl"}).pages_file == "/tmp/p.jsonl"
    assert PagerSettings.from_env({}).port == 8090


@pytest.mark.parametrize(("text", "seconds"), [("0m", 0), ("30s", 30), ("5m", 300), ("2h", 7200), ("1d", 86400)])
def test_durations_parse(text, seconds):
    assert parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["", "5", "m", "1w", "1.5h", "-5m"])
def test_bad_durations_are_rejected(text):
    with pytest.raises(DefinitionError):
        parse_duration(text)


def test_the_real_definitions_load_and_describe_the_lessons_objectives():
    slos = {slo.name: slo for slo in load_slos(ROOT / "observability/slo/slos.yaml")}
    assert (slos["availability"].objective, slos["latency"].objective) == (0.995, 0.95)
    assert slos["availability"].budget_minutes() == pytest.approx(216)
    policies = load_policies(ROOT / "observability/slo/policies.yaml")
    assert {rule.severity for rule in policies["default"].rules} == {"page", "ticket"}
    assert policies["lesson"].rules[1].long_window == timedelta(hours=6)

```

Settings defaults and overrides; invalid values rejected; the pager's settings; durations parse (`0m`, `30s`, `5m`, `2h`, `1d`) and bad ones (`""`, `5`, `m`, `1w`, `1.5h`, `-5m`) are rejected with `DefinitionError`; the real definitions load and describe the lesson's objectives (0.995 and 0.95, a budget of 216 minutes, the `lesson` policy's six-hour window).

```python
def test_the_replay_threshold_rules_load_with_their_for_durations():
    rules = {rule.name: rule for rule in load_alert_rules(ROOT / "observability/prometheus/replay/threshold.rules.yml")}
    assert rules["HighCPU"].for_seconds == 60
    assert rules["AnyError"].for_seconds == 0
    assert rules["HighCPU"].expression == "cpu_usage_percent > 80"


def test_rendered_rule_files_start_with_the_do_not_edit_header_and_keep_multiline_expressions_readable():
    text = render_rule_file({"groups": [{"name": "g", "rules": [{"alert": "A", "expr": "a\nand\nb"}]}]})
    assert text.startswith("# Generated by")
    assert "expr: |-" in text


def test_a_definition_file_that_is_not_a_mapping_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(DefinitionError):
        load_slos(path)


def _tick(index, ok=100, errors=0, slow=2):
    return Tick(timedelta(seconds=30 * index), ok, errors, slow, 2, 40.0)

```

The threshold rules load with their `for` durations (HighCPU 60 s, AnyError 0); rendered rule files start with the do-not-edit header and write multi-line expressions as `expr: |-` blocks; a definition file that is a list instead of a mapping is rejected (`tmp_path`).

```python
def test_the_openmetrics_file_has_cumulative_counters_a_complete_histogram_and_ends_with_eof():
    out = io.StringIO()
    assert write_week([_tick(0), _tick(1, errors=10), _tick(2)], 1_000_000, out) == 3
    lines = out.getvalue().splitlines()
    assert lines[-1] == "# EOF"
    assert "# TYPE http_requests counter" in lines
    ok = [line for line in lines if line.startswith('http_requests_total{route="/checkout",status="200"}')]
    assert [int(line.split()[1]) for line in ok] == [100, 200, 300]
    errors = [line for line in lines if line.startswith('http_requests_total{route="/checkout",status="500"}')]
    assert [int(line.split()[1]) for line in errors] == [0, 10, 10]
    assert ok[0].endswith("1000000")
    buckets = [line for line in lines if "_bucket" in line and line.endswith(" 1000000")]
    assert len(buckets) == 7
    counts = [int(line.split("}")[1].split()[0]) for line in buckets]
    assert counts == sorted(counts)


def test_the_openmetrics_latency_sli_is_the_share_of_requests_in_the_0_3_bucket():
    out = io.StringIO()
    write_week([_tick(0, ok=98, errors=0, slow=2)], 0, out)
    text = out.getvalue()
    fast = next(int(line.split()[1]) for line in text.splitlines() if 'le="0.3"' in line)
    total = next(
        int(line.split()[1]) for line in text.splitlines() if line.startswith("http_request_duration_seconds_count")
    )
    assert (fast, total) == (96, 98)
```

OpenMetrics: cumulative counters (100, 200, 300 and 0, 10, 10), a complete 7-bucket histogram with non-decreasing counts, the file ends with `# EOF`; and the latency SLI is the share of requests in the `0.3` bucket (96 of 98).

```python
def test_the_request_metrics_record_a_counter_and_a_histogram_with_the_slo_bucket(memory_telemetry):
    metrics = OtelRequestMetrics(memory_telemetry.meter_provider.get_meter("t"))
    metrics.record("/checkout", 200, 0.12)
    metrics.record("/checkout", 500, 0.02)
    data = memory_telemetry.metric_reader.get_metrics_data()
    by_name = {m.name: m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    assert sum(point.value for point in by_name["http_requests"].data.data_points) == 2
    histogram = by_name["http_request_duration"].data.data_points[0]
    assert tuple(histogram.explicit_bounds) == LATENCY_BUCKETS
    assert 0.3 in histogram.explicit_bounds


def test_the_annotator_sets_attributes_on_the_current_span(memory_telemetry):
    tracer = memory_telemetry.tracer_provider.get_tracer("t")
    with tracer.start_as_current_span("request"):
        OtelAnnotator().set("customer.email", "a@example.com")
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert span.attributes["customer.email"] == "a@example.com"


def test_references_count_up_and_the_delay_waits():
    references = SequentialReferences()
    assert [references.next(), references.next()] == ["ord-000001", "ord-000002"]
    SleepDelay().wait(0.0)


def test_the_page_store_appends_lists_and_clears(tmp_path):
    store = JsonLinesPageStore(tmp_path / "nested" / "pages.jsonl")
    assert store.list() == []
    store.add(Page("A", "page", timedelta(minutes=5)), 123.0)
    store.add(Page("B", "ticket", timedelta()), 124.0)
    pages = store.list()
    assert [page["alert"] for page in pages] == ["A", "B"]
    assert pages[0]["offset_seconds"] == 300
    store.clear()
    assert store.list() == []

```

The request metrics record a counter and a histogram whose explicit bounds equal the chosen buckets (and contain 0.3); the annotator sets attributes on the current span; references count up; the page store appends, lists and clears (also creating its folder).

```python
class Response:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, str(body)

    def json(self):
        return self._body


class Session:
    def __init__(self, responder):
        self.responder, self.calls = responder, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responder(url, params)


def test_true_at_collects_the_timestamps_of_every_returned_series():
    def respond(url, params):
        return Response(
            {"data": {"result": [{"values": [[100, "1"], [160, "1"]]}, {"values": [[160, "1"], [220, "1"]]}]}}
        )

    evaluator = PrometheusRangeEvaluator("http://p:9090", Session(respond))
    assert evaluator.true_at("x", 100, 220, 60) == {100.0, 160.0, 220.0}


def test_long_ranges_are_queried_in_chunks_under_prometheus_point_limit():
    session = Session(lambda url, params: Response({"data": {"result": []}}))
    PrometheusRangeEvaluator("http://p:9090", session).true_at("x", 0, 7 * 86400, 30)
    assert len(session.calls) == 3
    for _, params in session.calls:
        assert (float(params["end"]) - float(params["start"])) / 30 < 11_000


def test_prometheus_errors_are_named():
    evaluator = PrometheusRangeEvaluator("http://p:9090", Session(lambda url, params: Response({}, status=422)))
    with pytest.raises(PrometheusError, match="422"):
        evaluator.true_at("x", 0, 60, 60)
    with pytest.raises(PrometheusError, match="422"):
        evaluator.scalar("x")


def test_scalar_and_ready():
    ok = PrometheusRangeEvaluator(
        "http://p:9090", Session(lambda url, params: Response({"data": {"result": [{"value": [0, "7"]}]}}))
    )
    assert ok.scalar("x") == 7
    assert ok.ready()
    assert json.dumps({}) == "{}"
```

A fake `Response` and `Session` stand in for Prometheus. `true_at` collects the timestamps of every series; long ranges are queried in chunks under the point limit (a week at 30 seconds is 3 queries); errors are named; `scalar` and `ready` work. (The last assertion, `json.dumps({}) == "{}"`, is a leftover that keeps an import in use.)

```python
import io
import json
import logging

from slo_lab.infrastructure.logging_setup import JsonFormatter, configure_logging


def _record(message="hello", **extra):
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_every_line_is_one_json_object_with_the_standard_fields():
    payload = json.loads(JsonFormatter("back").format(_record("order assembled", order_id=5)))
    assert payload["msg"] == "order assembled"
    assert (payload["level"], payload["service"], payload["logger"]) == ("info", "back", "test.logger")
    assert payload["order_id"] == 5
    assert "trace_id" not in payload


def test_inside_a_span_the_line_carries_the_trace_and_span_ids(memory_telemetry):
    tracer = memory_telemetry.tracer_provider.get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        payload = json.loads(JsonFormatter("back").format(_record()))
        context = span.get_span_context()
    assert payload["trace_id"] == f"{context.trace_id:032x}"
    assert payload["span_id"] == f"{context.span_id:016x}"


def test_exceptions_are_included_as_text():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
    assert "ValueError: boom" in json.loads(JsonFormatter("back").format(record))["exception"]


def test_configure_logging_replaces_handlers_and_writes_json_to_the_stream():
    stream = io.StringIO()
    configure_logging("INFO", "front", stream)
    configure_logging("INFO", "front", stream)
    logging.getLogger("x").info("once")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["service"] == "front"
```

The four logging tests (see `python-logging.md`).

### Interfaces

```python
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
```

`StubCheckout` inherits the real use case and replaces `execute`. `_client` builds the service app with real OpenTelemetry metrics on the in-memory pipeline. A checkout returns a receipt and is counted with the attributes `route=/checkout` and `status=200` (the metric reader hands back the data points); a failed checkout is a 500 and is still counted.

```python
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

```

Invalid bodies (`None`, `{}`, a wrong items type, a missing quantity, a bad SKU, a zero quantity, no items, a non-string email) are a 400 `invalid_checkout` and never reach the use case; simulation is only accepted when the lab enables it; health and unknown routes answer in JSON; an unexpected error is a JSON 500; responses inside a span carry the trace id.

```python
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
```

The pager: it records firing alerts only and timestamps them with the injected clock (`1234.0`); it rejects a payload that is not a webhook and can be cleared.

```python
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from slo_lab.interfaces.health_cli import main


def _server(status):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_exit_code_is_0_for_a_200_and_1_for_anything_else():
    healthy, broken = _server(200), _server(503)
    try:
        assert main(["--url", f"http://127.0.0.1:{healthy.server_address[1]}/healthz"]) == 0
        assert main(["--url", f"http://127.0.0.1:{broken.server_address[1]}/healthz"]) == 1
    finally:
        healthy.shutdown()
        broken.shutdown()


def test_exit_code_is_1_when_nothing_listens():
    assert main(["--url", "http://127.0.0.1:9/healthz", "--timeout", "0.5"]) == 1
```

The container probe returns 0 for a 200, 1 for a 503, and 1 when nothing listens.

### The lab

```python
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

```

The imports show how many pieces the lab has. `ROOT` finds the repository.

```python
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

```

The metric reader sums every label set of one metric and ignores commented and similarly named ones; ids come back as hex from hex or base64; the traffic mix is seeded (about 3 percent errors, 1 percent slow, 5 percent personal over 5,000 draws) and outage mode fails everything; bodies carry the planted data.

```python
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

```

A throw-away server answers 500 for error requests and 200 otherwise, with an `X-Trace-Id`. `send_traffic` sorts trace ids by kind and paces requests (199 waits of 0.01 s at 100 a second, using a fake clock and a recording `sleep`); a dead service counts as status 0 with no trace ids.

```python
class FakeCompose:
    def __init__(self):
        self.configs = []

    def use_gateway_config(self, config_path):
        self.configs.append(config_path)


class FakeTempo:
    """Holds every trace in the raw pass; in the protected pass it drops the ordinary ones and masks the values."""

    def __init__(self, compose):
        self.compose = compose
        self.base = 0.0
        self.reads_in_pass = 0
        self.config = None

    def _raw(self):
        return self.compose.configs[-1] == RAW_CONFIG

    def bytes_received(self):
        """The first reading of a pass is the baseline; every later one includes the pass's bytes and then stays put."""
        if self.config != self.compose.configs[-1]:
            self.config, self.reads_in_pass = self.compose.configs[-1], 0
            self.base += 10_000.0
        self.reads_in_pass += 1
        return self.base if self.reads_in_pass == 1 else self.base + (1000.0 if self._raw() else 100.0)

    def trace_json(self, trace_id):
        if not self._raw() and trace_id.startswith("o"):
            return None
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

```

Fakes for the volume scenario: `FakeCompose` records the configurations used; `FakeTempo` holds every trace in the raw pass and, in the protected pass, drops the ordinary ones and masks the values (its `bytes_received` returns a baseline on the first reading of a pass and the pass's bytes afterwards); `FakeLoki` returns three lines for the raw pass and none for the protected one; `_fake_send` returns a ready-made result.

```python
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
        quiet_interval_seconds=0.0,
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
```

The volume scenario compares a raw gateway with a protected one: the configurations used are the raw one then the protected one; the raw pass finds the test email and card in two traces each and three log lines, the protected one finds none; errors and slow traces are all found; no ordinary trace is stored; trace volume falls by 90 percent.

```python
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

```

The live check reports how long the page took (the fake pager's `received_at` is 100 s in the future) and a missing page.

```python
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
```

Pages survive a round trip through the pager log format; the week replay backfills, then evaluates and writes a pager log per alert set (a fake evaluator says the `outage` expression is true at minutes 1,805 to 1,814, so the loud set gets one actionable page and the report text contains `first page (Loud) 5 min after onset` and `partial-degradation: NO ALERT`).

```python
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

```

The analyze command reads a pager log (`capsys` captures printed output); rules are generated into a temporary folder and `--check` flags a stale file and then passes; the committed generated rules are current.

```python
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
        send=send, log=lambda _: None, settle_seconds=0.0, quiet_interval_seconds=0.0, gateway_ready=ready,
    ).run()  # fmt: skip
    assert polls_before_each_send == [3, 4]
    never = VolumeScenario(
        FakeCompose(), FakeTempo(compose), FakeLoki(compose), "http://x", 25,
        send=_fake_send, log=lambda _: None, gateway_ready=lambda: False, ready_timeout_seconds=0.0,
    )  # fmt: skip
    with pytest.raises(TimeoutError):
        never.run()
```

Compose commands pass the gateway configuration and backfill runs promtool; the incident helper is a value object; the volume scenario waits for the gateway before sending (the readiness function answers false twice, then true: polls before each send are 3 and 4) and gives up after the timeout (`TimeoutError`). `monkeypatch.setattr("time.sleep", lambda seconds: None)` makes the waiting instant for this test only.

```python
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from slo_lab.lab import health_cli


def test_every_part_is_reported_down_when_nothing_listens():
    results = health_cli.run_checks({"SERVICE_HOST_PORT": "9", "GATEWAY_HEALTH_HOST_PORT": "9"})
    assert len(results) == 9
    assert not any(ok for _, ok in results[:2])


def test_one_answering_part_is_reported_up_and_the_exit_code_is_1_while_others_are_down(monkeypatch, capsys):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("SERVICE_HOST_PORT", str(server.server_address[1]))
        monkeypatch.setenv("GATEWAY_HEALTH_HOST_PORT", "9")
        assert health_cli.main() == 1
    finally:
        server.shutdown()
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "ok   checkout /healthz"
    assert printed[1] == "FAIL gateway health_check"
```

The new stack health command reports all nine parts down when nothing listens, and, with one throw-away server answering for the service, prints `ok   checkout /healthz` and `FAIL gateway health_check` and exits 1.

### Configuration as code

```python
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

```

Images pinned, the base image pinned by digest and the user non-root, every port on loopback; four fixtures read compose and both gateway files.

```python
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

```

Every variable the Compose file reads is in `.env.example`; the replay Prometheus is a profile and the live one is not; the gateway orders traces as `memory_limiter`, `transform/redact`, `tail_sampling`, `batch` and logs as `memory_limiter`, `transform/redact`, `batch`, with metrics neither redacted nor sampled; the three tail-sampling policies are as documented and the sampled share is 1 to 10 percent; the raw gateway is the protected one minus the two policies.

```python
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

```

The redaction patterns are extracted from the OTTL statements and applied to sample text: the test email and the card in three spellings are replaced and the surrounding text kept; harmless values (an order reference, a trace id, `1.4s`, a timestamp, a SKU) are unchanged (see `regular-expressions.md`).

```python
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
```

Prometheus loads only the generated live rules and alerts through Alertmanager; Alertmanager sends everything to the pager webhook; every generated alert has a severity, a summary and a runbook; `docs/slo.md` states the budget in minutes and requests that the definitions imply (formatted from the `Slo` object, so the document cannot drift); both dashboards use provisioned data sources and unique panel ids.

### Integration and end-to-end

```python
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
```

Against the stack: the Tempo, Loki, Prometheus, pager and Grafana data sources answer; a checkout succeeds, the lab simulation can fail it with a 500 and an empty basket is a 400; the SLI series reach Prometheus including the `le="0.3"` bucket; Prometheus has loaded exactly the four generated rules and not the threshold alerts; Prometheus is connected to Alertmanager; the pager records a firing webhook and ignores a resolved one.

```python
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
        PrometheusRangeEvaluator(f"http://127.0.0.1:{ENV.get('REPLAY_HOST_PORT', '29091')}"),
        tmp_path_factory.mktemp("replay"),
        log=lambda _: None,
    )
    week.prepare(start_epoch)
    return week.evaluate(load_alert_sets(ROOT, RULE_FILES), start_epoch)


@pytest.fixture(scope="module")
def volume() -> VolumeReport:
    return VolumeScenario(
        Compose(ROOT, ENV),
        TempoClient(f"http://127.0.0.1:{ENV.get('TEMPO_HOST_PORT', '23200')}"),
        LokiClient(f"http://127.0.0.1:{ENV.get('LOKI_HOST_PORT', '23100')}"),
        f"http://127.0.0.1:{ENV.get('SERVICE_HOST_PORT', '28083')}",
        requests=3000,
        log=lambda _: None,
    ).run()

```

Fixtures run the replay (once per module: simulate, backfill, evaluate three sets) and the volume comparison (3,000 requests).

```python
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
    assert volume.raw.ordinary_kept_share >= 0.95


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
```

Step 1: the threshold alerts are mostly noise (at least 100 notifications, under 20 percent actionable, CPU-spike false alarms present, at least 50 from ordinary baseline noise). Step 3: the fast-burn page comes within 10 minutes of the outage and the blip pages no one; a slow leak opens a ticket and the lesson's own numbers miss it. Step 4: the burn-rate set is an improvement with at most 10 notifications, all actionable, and no CPU false alarm. Step 5: tail sampling cuts stored traces by at least 60 percent, keeps every planted error and slow request, and keeps at most 15 percent of ordinary traces. Step 6: the raw gateway stores the test values and the protected one stores none. The live stack pages the fake pager on a real outage within six minutes.

## Try it yourself

```bash
make test
```

Expected: `138 passed` and a coverage table; then `Required test coverage of 95% reached`.

```bash
.venv/bin/python -m pytest tests/unit/domain -v | head -20
```

Expected: one line per test with `PASSED`.

```bash
.venv/bin/python -m pytest tests/unit -k "burn or budget" -q
```

Expected: only the tests whose names contain those words run.

```bash
.venv/bin/python -m pytest tests/integration tests/e2e --collect-only -q | tail -3
```

Expected: the integration and end-to-end tests are listed without running.

```bash
make test-e2e
```

Expected (needs `make up`; about fifteen minutes): all end-to-end tests pass.

## Mistakes people make

- **Tests that depend on each other's order or leftovers.** Each test here builds its own data.
- **Sleeping for a fixed time** to wait for something. This repository polls with a deadline, and its unit tests replace `time.sleep` with `monkeypatch`.
- **Patching globally and forgetting to undo.** `monkeypatch` undoes itself.
- **Asserting too little.** `assert response` passes for any non-empty object. Assert the exact thing that matters.
- **Mocking everything.** A test full of mocks proves the mocks work. Fakes here are small hand-written classes with real behavior.
- **Typos in marker names.** `--strict-markers` turns an unknown marker into an error.

## Check yourself

1. What makes a function a test for pytest? *Its name starts with `test_` and it lives in a file named `test_*.py` (or in the configured paths).*
2. How does a test receive a fixture? *By naming it as a parameter.*
3. What does code after `yield` in a fixture do? *It runs after the test as cleanup.*
4. What does `pytest.raises` check? *That the block inside it raises the named exception.*
5. Why keep unit and integration tests apart? *Unit tests need no running services and run fast; integration tests need the stack.*

## Further reading

- pytest 9 documentation: https://docs.pytest.org/en/stable/
- Fixtures: https://docs.pytest.org/en/stable/how-to/fixtures.html
- Parametrize: https://docs.pytest.org/en/stable/how-to/parametrize.html
- pytest-cov: https://pytest-cov.readthedocs.io/en/latest/
- coverage.py: https://coverage.readthedocs.io/en/7.17.0/
