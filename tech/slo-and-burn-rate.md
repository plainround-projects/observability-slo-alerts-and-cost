# SLOs, error budgets and burn-rate alerts

## What it is

This is the idea the repository is built around, not a tool. Read it before the code.

- A **service level indicator (SLI)** is a measurement of how well a service does for its users, written as a ratio: *good events ÷ valid events*. Availability: valid events are requests (not health checks), good ones are those that did not end in a 5xx. Latency: good ones finished within 300 ms.
- A **service level objective (SLO)** is a target for an SLI over a period: "99.5 percent of valid requests are good over 30 days".
- The **error budget** is what the SLO allows to go wrong: `1 − 0.995 = 0.5 percent` of requests, or `0.005 × 30 days = 216 minutes` of total outage, or `108,000` bad requests at 500 requests a minute.
- The **burn rate** is how fast the budget is being spent compared with the pace that would exactly use it up at the end of the period. Burn rate 1: exactly on budget. Burn rate 14.4: the budget would be gone in `30 days ÷ 14.4 ≈ 50 hours`, and one hour of it costs 2 percent of the month's budget.
- A **burn-rate alert** pages when the burn rate is high over a **long window** (proves it is real: it has already cost something) **and** over a **short window** (proves it is still happening). It is called "multiwindow, multi-burn-rate" when you have several such rules, one fast and loud, one slow and quiet.
- **Threshold alerts** are the alternative most teams start with: "CPU above 80 percent", "any 5xx". They fire on *causes* or on single events, not on user pain.

## Why this repository uses it

The lesson's claim is measurable: replace noisy threshold alerts with burn-rate alerts and you get far fewer notifications, nearly all actionable, and no real incident missed. The repository defines the SLOs as data, derives every threshold, replays a seeded simulated week through three alert sets with real PromQL, and counts what reached a fake pager.

## Where it appears

| Path | What it holds |
| --- | --- |
| `observability/slo/slos.yaml` | The two SLOs: objective, window, and the PromQL of "good" and "valid". |
| `observability/slo/policies.yaml` | The burn-rate rules in two policies: `default` (the live Prometheus) and `lesson` (the numbers of the lesson's solution, replayed for comparison). |
| `observability/prometheus/rules/burn_rate.rules.yml` | The generated rules the live Prometheus loads. |
| `observability/prometheus/replay/burn_rate_lesson.rules.yml` | The generated rules for the `lesson` policy (replay only). |
| `observability/prometheus/replay/threshold.rules.yml` | The noisy threshold alerts (replay only). |
| `src/slo_lab/domain/slo.py`, `burn_rate.py` | The arithmetic. |
| `src/slo_lab/application/rule_builder.py` | The generator. |
| `docs/slo.md` | The SLO document: what counts as valid and good, with the budget in minutes and requests. |

## How it works underneath

**Why two windows.** One long window alone keeps alerting for a long time after an incident ends (the average stays high). One short window alone pages on every blip. Requiring both gives fast detection of a real, ongoing problem and a quick all-clear.

**Why the same burn rate is a different threshold per SLO.** The alert expression compares the bad-event ratio with `burn_rate × error budget`. For availability (budget 0.5 percent) 14.4 means 7.2 percent bad requests. For latency (95 percent objective, budget 5 percent) 14.4 means 72 percent slow requests.

**Why the lesson's slow-burn numbers miss the planted degradation.** A steady 3 percent error rate for two hours is a burn rate of `0.03 ÷ 0.005 = 6`. The lesson's ticket rule needs 6× over *six hours*, but the six-hour window averages a two-hour 3 percent episode down to about 1 percent, below the 3 percent threshold. The default policy of this repository uses 5× over **two** hours (threshold 2.5 percent), which a 3 percent degradation does cross. The replay shows both: the lesson's numbers miss it, this repository's catch it with a ticket.

**Why a blip pages no one.** The 90-second blip is 100 percent errors for 90 s, about 750 bad requests: the 5-minute window crosses 7.2 percent for a moment, but the 1-hour window stays far below, so both are never true at once.

## Walkthrough of this repository's use

### `observability/slo/slos.yaml`

```yaml
# The service level objectives, as code. `make rules` turns these and policies.yaml into the burn-rate alert rules.
# __WINDOW__ is replaced by each alert window (1h, 5m, 2h, 15m ...).
window_days: 30
```

The comments explain that `make rules` turns this file and `policies.yaml` into alert rules, and that `__WINDOW__` is replaced by each alert window. `window_days: 30` is the SLO window, shared by both SLOs.

```yaml
slos:
  - name: availability
    objective: 0.995
    description: >-
      A valid request is any request to a route other than /healthz (health checks always succeed, so counting them
      would hide real errors). A good request is a valid one that did not end in a 5xx.
    good: sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[__WINDOW__]))
    valid: sum(rate(http_requests_total{route!="/healthz"}[__WINDOW__]))
```

The availability SLO. `objective: 0.995`. The description (a YAML folded string, `>-`, which joins the lines into one) says a valid request is any request to a route other than `/healthz`, because health checks always succeed and would hide real errors, and that a good one is a valid one that did not end in a 5xx. `good` is the rate of requests whose route is not `/healthz` and whose status does **not** match the regular expression `5..` (`status!~"5.."`). `valid` is the rate of all requests to routes other than `/healthz`. In PromQL `rate(x[__WINDOW__])` is the per-second rate over the window and `sum(...)` adds the series (see `promql.md`).

```yaml
  - name: latency
    objective: 0.95
    description: >-
      A valid request is any request to a route other than /healthz. A good request is a valid one that finished within
      300 ms (the le="0.3" histogram bucket).
    good: sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[__WINDOW__]))
    valid: sum(rate(http_request_duration_seconds_count{route!="/healthz"}[__WINDOW__]))
```

The latency SLO. `objective: 0.95`. A good request finished within 300 ms: the histogram bucket with `le="0.3"` counts observations at or below 0.3 seconds. `valid` is the histogram's `_count`. For the SLI to be computable, 0.3 must be an exact bucket boundary; the service's histogram has one at 0.3 on purpose.

### `observability/slo/policies.yaml`

```yaml
# Burn-rate policies. A rule fires when the budget burns at `burn_rate` times the sustainable pace over BOTH windows.
policies:
  # The policy the live Prometheus runs.
  default:
    description: >-
      Page when 2% of the 30-day budget would be spent in an hour (14.4x). Open a ticket when 1.4% would be spent in two
      hours (5x), which is what a steady 3% error rate does to a 99.5% objective.
    rules:
      - { severity: page, burn_rate: 14.4, long_window: 1h, short_window: 5m }
      - { severity: ticket, burn_rate: 5, long_window: 2h, short_window: 15m }
```

Policy `default`. Its description says what it does: page when 2 percent of the 30-day budget would be spent in an hour (14.4×), open a ticket when 1.4 percent would be spent in two hours (5×), which is what a steady 3 percent error rate does to a 99.5 percent objective. Each rule is written in YAML's one-line map style: severity, burn rate, long window, short window. `page`: 14.4× over 1 h and 5 m. `ticket`: 5× over 2 h and 15 m.

```yaml
  # The numbers the lesson's solution shows. Replayed for comparison only: its 6x over 6 hours cannot see a two-hour
  # 3% degradation, because the six-hour window averages it down to about 1%.
  lesson:
    description: Page at 14.4x over 1h and 5m; ticket at 6x over 6h and 30m.
    rules:
      - { severity: page, burn_rate: 14.4, long_window: 1h, short_window: 5m }
      - { severity: ticket, burn_rate: 6, long_window: 6h, short_window: 30m }
```

Policy `lesson`: the numbers of the lesson's solution, kept for the comparison only: page at 14.4× over 1 h and 5 m, ticket at 6× over 6 h and 30 m. The comment says why it is replayed: its 6× over 6 hours cannot see a two-hour 3 percent degradation.

### The generated rules

```yaml
# Generated by `make rules` from observability/slo/slos.yaml and observability/slo/policies.yaml.
# Do not edit by hand; change the definitions and regenerate. `make lint` fails when this file is stale.
groups:
- name: slo-burn-rate-default
  rules:
  - alert: AvailabilityBudgetFastBurn
    expr: |-
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[1h]))) / (sum(rate(http_requests_total{route!="/healthz"}[1h])))) > 0.072
      and
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[5m]))) / (sum(rate(http_requests_total{route!="/healthz"}[5m])))) > 0.072
    labels:
      severity: page
      slo: availability
    annotations:
      summary: 'Availability error budget burning at 14.4x: 2.0% of the 30-day budget in 1h.'
      runbook: Check the availability SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause.
```

The first two lines are the header: generated, do not edit, `make lint` fails when stale. `AvailabilityBudgetFastBurn` has a two-line expression joined by `and`: the 1-hour bad ratio `(1 - (good) / (valid))` is greater than **0.072** and the 5-minute one is greater than 0.072. The thresholds are computed: `14.4 × 0.005`. Labels `severity: page` and `slo: availability` are used by Alertmanager to route and to inhibit. The summary states the budget spent (`2.0% of the 30-day budget in 1h`) and the runbook tells what to do.

```yaml
  - alert: AvailabilityBudgetSlowBurn
    expr: |-
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[2h]))) / (sum(rate(http_requests_total{route!="/healthz"}[2h])))) > 0.025
      and
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[15m]))) / (sum(rate(http_requests_total{route!="/healthz"}[15m])))) > 0.025
    labels:
      severity: ticket
      slo: availability
    annotations:
      summary: 'Availability error budget burning at 5x: 1.4% of the 30-day budget in 2h.'
      runbook: Check the availability SLI panel, open an error or slow trace from the last 15m, and roll back or fix the cause.
```

`AvailabilityBudgetSlowBurn` (ticket): 2 h and 15 m windows, threshold **0.025** (`5 × 0.005`), 1.4 percent of the budget in 2 h.

```yaml
  - alert: LatencyBudgetFastBurn
    expr: |-
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[1h]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[1h])))) > 0.72
      and
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[5m]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[5m])))) > 0.72
    labels:
      severity: page
      slo: latency
    annotations:
      summary: 'Latency error budget burning at 14.4x: 2.0% of the 30-day budget in 1h.'
      runbook: Check the latency SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause.
  - alert: LatencyBudgetSlowBurn
    expr: |-
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[2h]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[2h])))) > 0.25
      and
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[15m]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[15m])))) > 0.25
    labels:
      severity: ticket
      slo: latency
    annotations:
      summary: 'Latency error budget burning at 5x: 1.4% of the 30-day budget in 2h.'
      runbook: Check the latency SLI panel, open an error or slow trace from the last 15m, and roll back or fix the cause.
```

The two latency rules: the same windows with the latency SLI and thresholds **0.72** and **0.25** (`14.4 × 0.05` and `5 × 0.05`).

```yaml
# Generated by `make rules` from observability/slo/slos.yaml and observability/slo/policies.yaml.
# Do not edit by hand; change the definitions and regenerate. `make lint` fails when this file is stale.
groups:
- name: slo-burn-rate-lesson
  rules:
  - alert: AvailabilityBudgetFastBurn
    expr: |-
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[1h]))) / (sum(rate(http_requests_total{route!="/healthz"}[1h])))) > 0.072
      and
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[5m]))) / (sum(rate(http_requests_total{route!="/healthz"}[5m])))) > 0.072
    labels:
      severity: page
      slo: availability
    annotations:
      summary: 'Availability error budget burning at 14.4x: 2.0% of the 30-day budget in 1h.'
      runbook: Check the availability SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause.
```

The same generated file for the `lesson` policy: the same header, the group `slo-burn-rate-lesson`, and the same fast-burn page for availability (1 h and 5 m, threshold 0.072).

```yaml
  - alert: AvailabilityBudgetSlowBurn
    expr: |-
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[6h]))) / (sum(rate(http_requests_total{route!="/healthz"}[6h])))) > 0.03
      and
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[30m]))) / (sum(rate(http_requests_total{route!="/healthz"}[30m])))) > 0.03
    labels:
      severity: ticket
      slo: availability
    annotations:
      summary: 'Availability error budget burning at 6x: 5.0% of the 30-day budget in 6h.'
      runbook: Check the availability SLI panel, open an error or slow trace from the last 30m, and roll back or fix the cause.
```

The lesson policy's availability ticket: 6 h and 30 m, threshold **0.03** (`6 × 0.005`): the rule that cannot see a two-hour 3 percent degradation, because the 3 percent threshold is not exceeded once the 6-hour window averages the episode.

```yaml
  - alert: LatencyBudgetFastBurn
    expr: |-
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[1h]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[1h])))) > 0.72
      and
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[5m]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[5m])))) > 0.72
    labels:
      severity: page
      slo: latency
    annotations:
      summary: 'Latency error budget burning at 14.4x: 2.0% of the 30-day budget in 1h.'
      runbook: Check the latency SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause.
  - alert: LatencyBudgetSlowBurn
    expr: |-
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[6h]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[6h])))) > 0.3
      and
      (1 - (sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[30m]))) / (sum(rate(http_request_duration_seconds_count{route!="/healthz"}[30m])))) > 0.3
    labels:
      severity: ticket
      slo: latency
    annotations:
      summary: 'Latency error budget burning at 6x: 5.0% of the 30-day budget in 6h.'
      runbook: Check the latency SLI panel, open an error or slow trace from the last 30m, and roll back or fix the cause.
```

The latency pair for the lesson policy: the same fast-burn page (threshold 0.72) and a slow burn at 6× over 6 h and 30 m with threshold **0.3** (`6 × 0.05`).

### The threshold alerts to beat

```yaml
# The alerts most teams start with: a cause (CPU) and a symptom without a threshold (any single 5xx).
# Replayed over the simulated week to measure how noisy they are; never loaded by the live Prometheus.
groups:
  - name: threshold
    rules:
      - alert: HighCPU
        expr: cpu_usage_percent > 80
        for: 1m
        labels:
          severity: page
        annotations:
          summary: CPU is above 80%.
          runbook: Check what is running; CPU alone does not say whether any user is affected.
      - alert: AnyError
        expr: increase(http_requests_total{status=~"5.."}[1m]) > 0
        labels:
          severity: page
        annotations:
          summary: At least one 5xx in the last minute.
          runbook: Check the error rate; a single error is usually noise.
```

The alerts "most teams start with". `HighCPU`: `cpu_usage_percent > 80` for one minute, a **cause**, and CPU alone does not say whether any user is affected. `AnyError`: `increase(http_requests_total{status=~"5.."}[1m]) > 0`, with no `for`, paging on every single 5xx: a **symptom without a threshold**. Replayed over the week they produce hundreds of notifications.

## Try it yourself

```bash
make rules && git status --short
```

Expected: two `wrote ...` lines and no change in `git status`.

```bash
.venv/bin/python -c "
from pathlib import Path
from slo_lab.infrastructure.definitions_io import load_slos, load_policies
slos = {s.name: s for s in load_slos(Path('observability/slo/slos.yaml'))}
policies = load_policies(Path('observability/slo/policies.yaml'))
for name, policy in policies.items():
    for rule in policy.rules:
        print(name, rule.severity, rule.burn_rate, round(rule.error_ratio_threshold(slos['availability']), 4), round(rule.budget_consumed(slos['availability']), 4))"
```

Expected: four lines: `default page 14.4 0.072 0.02`, `default ticket 5.0 0.025 0.0139`, `lesson page 14.4 0.072 0.02`, `lesson ticket 6.0 0.03 0.05`.

```bash
make test-rules
```

Expected (needs Docker): `SUCCESS`; five promtool scenarios: an outage pages, a 90-second blip does not, a 3 percent leak opens a ticket and never pages, health checks do not dilute the ratio, sustained slowness pages the latency SLO.

```bash
cat docs/slo.md | head -20
```

Expected: the SLO document with `216 minutes` and `108,000 bad requests`.

```bash
make replay
```

Expected (needs `make up`): the comparison table described in `prometheus.md`.

## Mistakes people make

- **Alerting on causes** (CPU, memory) instead of on user pain. CPU bursts are normal.
- **Counting health checks as requests**, which dilutes the error ratio. The SLI excludes them, and a promtool scenario proves it.
- **Typing thresholds by hand.** They drift from the objective. Here they are generated and `make lint` fails when the files are stale.
- **One window.** Too slow to stop, or too jumpy.
- **A latency SLO whose threshold is not a histogram bucket edge.** The SLI cannot be computed exactly.
- **Treating the budget as a target to spend.** It is a limit; the alerts exist to stop spending it by accident.

## Check yourself

1. What is an SLI? *A ratio of good events to valid events, measuring what users experience.*
2. How many minutes of total outage does a 99.5 percent objective over 30 days allow? *216.*
3. What does a burn rate of 14.4 over one hour cost? *2 percent of the 30-day budget.*
4. Why must both windows agree before the page fires? *The long one proves the problem is real, the short one proves it is still happening.*
5. Why does the lesson's ticket rule (6× over 6 h) miss a two-hour 3 percent degradation? *The six-hour window averages it down to about 1 percent, below the rule's 3 percent threshold.*

## Further reading

- Google SRE Workbook, "Alerting on SLOs": https://sre.google/workbook/alerting-on-slos/
- Google SRE Book, "Service Level Objectives": https://sre.google/sre-book/service-level-objectives/
- Prometheus alerting rules (2.54): https://prometheus.io/docs/prometheus/2.54/configuration/alerting_rules/
