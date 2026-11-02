# Prometheus (v2.54.1), its rules and promtool

## What it is

Prometheus is a database for metrics and a program that **scrapes** them. A **scrape** is an HTTP GET that Prometheus makes every few seconds to a **target** (an address that publishes its current numbers as plain text). Prometheus stores each number with its time stamp in a **time series database** (TSDB). A **time series** is one metric name plus one exact set of **labels** (key/value pairs such as `service_name="back"`), with a list of (time, value) points.

You ask Prometheus questions in **PromQL**, its query language. Prometheus can also evaluate **rules** on a schedule: **recording rules** save a result as a new metric; **alerting rules** fire an alert when an expression returns something for long enough. This repository pins Prometheus v2.54.1.

## Why this repository uses it

Prometheus is where the SLIs live and where the alert rules are evaluated, twice. The **live** Prometheus scrapes the gateway's metrics exporter every 5 seconds, evaluates the generated burn-rate rules and sends firing alerts to Alertmanager. A **second** Prometheus (the replay instance, started on demand) serves a simulated week that was backfilled with `promtool`, and the lab evaluates three alert sets against it with real PromQL at every minute of that week. `promtool` also tests the generated rules offline with fake series.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/prometheus/prometheus.yml` | The live Prometheus: scrapes, loads the generated rules, sends alerts to Alertmanager (see `alertmanager.md`). |
| `observability/prometheus/rules/burn_rate.rules.yml` | The generated live rules (see `slo-and-burn-rate.md`). |
| `observability/prometheus/replay/prometheus.yml` | The replay instance: scrapes and loads nothing. |
| `observability/prometheus/replay/threshold.rules.yml`, `burn_rate_lesson.rules.yml` | Rule sets that are replayed, never loaded. |
| `observability/prometheus/tests/burn_rate.rules.test.yml` | The promtool unit tests of the generated rules. |
| `docker-compose.yml` (services `prometheus`, `prometheus-replay`) | Run them. |
| `Makefile` (`lint-config`, `test-rules`) | `promtool check rules`, `check config`, `test rules`. |
| `src/slo_lab/infrastructure/prometheus_http.py` | The range evaluator (see `requests.md`). |

## How it works underneath

Prometheus is **pull-based**: the targets do not send; Prometheus asks. In this repository the application sends OTLP to the Collector, the Collector converts it to the Prometheus text format on port 8889, and Prometheus scrapes that port every `scrape_interval`.

Metric types:

- A **counter** only goes up (reset to zero when a process restarts). Its name usually ends in `_total`.
- A **gauge** goes up and down.
- A **histogram** is stored as several series: `<name>_bucket{le="X"}` counts observations less than or equal to X (cumulative), `<name>_sum` is the sum of all observations, and `<name>_count` is how many there were. `le` means "less than or equal". The bucket `le="+Inf"` equals the count.

**Exemplars** are stored in a separate in-memory area enabled by `--enable-feature=exemplar-storage`, and are only readable if the scrape used the OpenMetrics format.

An **alert** goes through states: *inactive* (the expression returns nothing), *pending* (it returns something, but not for the `for:` duration yet), *firing*. A `for: 1m` means the expression must stay true across evaluations for a full minute. Prometheus sends firing alerts to Alertmanager (not used in this repository); here you see them in the UI.

`promtool` is the command-line companion. `promtool check config` validates the main file and the rule files it lists. `promtool test rules` runs a **unit test** for rules: you give fake input series and the time points to check, and it tells you whether the alert fires. The test never contacts a real Prometheus.

Storage lives in a folder (`--storage.tsdb.path`). Data older than `--storage.tsdb.retention.time` is deleted.

**Two ways to evaluate a rule.** A live Prometheus evaluates rules at *now*, every `evaluation_interval`, remembering the pending state between evaluations. A replay cannot do that for a week in the past, so this lab does it by hand: it runs the rule's **expression** as a *range query* (`/api/v1/query_range`) over the week at one-minute steps, treats every minute at which the query returns any series as "true", and applies the `for` duration logic itself (`firing_episodes`, see `python.md`). The generated burn-rate rules have no `for` (the two windows already smooth them), so for them a firing minute is a page; `HighCPU` has `for: 1m`.

**`promtool test rules`** takes a test file with fake **input series** and a list of checks "at this time, this alert has exactly these labels and annotations". The notation `"0+500x70 35000x40"` means: start at 0 and add 500 each step for 70 steps (a counter growing by 500 a minute), then 35,000 repeated 40 times (a counter that stopped); `"0x70 0+500x40"` is 0 seventy times, then growing by 500 forty times.

## Walkthrough of this repository's use

### `prometheus.yml`: the live instance

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

rule_files:
  - /etc/prometheus/rules/*.rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  # The gateway's Prometheus exporter: the service's SLI metrics. Metrics are never sampled.
  - job_name: otel-gateway
    static_configs:
      - targets: ["otel-gateway:8889"]
  - job_name: otel-gateway-internal
    static_configs:
      - targets: ["otel-gateway:8888"]
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```

`scrape_interval: 5s` and `evaluation_interval: 5s` (short, for the lab). `rule_files` loads only the generated live rules from `/etc/prometheus/rules/`. The `alerting` block points at `alertmanager:9093`. Three scrape jobs: `otel-gateway` (`otel-gateway:8889`, the exporter that carries the service's SLI metrics, with the comment "Metrics are never sampled"), `otel-gateway-internal` (`:8888`, the gateway's own metrics) and `prometheus` itself.

### `replay/prometheus.yml`

```yaml
# The replay instance serves only the backfilled week; it scrapes nothing and loads no rules, because the replay
# evaluates rule expressions itself at every historical step.
global:
  scrape_interval: 1m
scrape_configs: []
```

The comment says why it has no jobs and no rules: it serves only the backfilled week, and the replay evaluates the expressions itself at every historical step. `scrape_configs: []` is an empty list.

### The compose services

```yaml
  prometheus:
    image: prom/prometheus:v2.54.1
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=2d
    volumes:
      - ./observability/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./observability/prometheus/rules:/etc/prometheus/rules:ro
      - prometheus-data:/prometheus
    ports:
      - "127.0.0.1:${PROMETHEUS_HOST_PORT:-29090}:9090"
    depends_on:
      alertmanager: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9090/-/healthy"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

The live Prometheus: pinned image, flags (`--config.file`, `--storage.tsdb.path=/prometheus`, `--storage.tsdb.retention.time=2d`), mounts (its configuration and the rules folder, read-only, and the data volume), port 29090 on loopback. It waits for Alertmanager to be healthy and has a `wget` health check on `/-/healthy`.

### The unit tests of the rules: `burn_rate.rules.test.yml`

```yaml
# Replays fault scenarios through the generated burn-rate rules with promtool. Each scenario starts with an hour or
# more of healthy traffic (500 requests a minute) so the one-hour and two-hour windows have history, as in production.
# Run with: make test-rules
rule_files:
  - ../rules/burn_rate.rules.yml

evaluation_interval: 1m

```

The comment says each scenario starts with an hour or more of healthy traffic (500 requests a minute), so the one- and two-hour windows have history, as in production. `rule_files` points at the generated live rules. `evaluation_interval: 1m`.

```yaml
tests:
  - name: an outage that spends 2 percent of the budget in an hour pages within minutes
    interval: 1m
    input_series:
      - series: 'http_requests_total{route="/checkout",status="200"}'
        values: "0+500x70 35000x40"
      - series: 'http_requests_total{route="/checkout",status="500"}'
        values: "0x70 0+500x40"
      - series: 'http_requests_total{route="/healthz",status="200"}'
        values: "0+4x400"
    alert_rule_test:
      - eval_time: 72m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts: []
      - eval_time: 80m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts:
          - exp_labels:
              severity: page
              slo: availability
            exp_annotations:
              summary: "Availability error budget burning at 14.4x: 2.0% of the 30-day budget in 1h."
              runbook: "Check the availability SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause."
```

**Test 1, an outage pages within minutes.** For 70 minutes the 200-counter grows by 500 a minute and the 500-counter stays 0; then for 40 minutes the 200-counter is frozen at 35,000 while the 500-counter grows by 500 a minute (every request fails). The health-check route grows by 4 a minute (it must not matter). At 72 minutes — two minutes into the outage, when the five-minute window already agrees but the one-hour window has not yet crossed 7.2 percent — `AvailabilityBudgetFastBurn` must **not** be firing. At 80 minutes it must fire with exactly the labels `severity: page`, `slo: availability` and the generated summary and runbook.

```yaml
  - name: a 90 second blip pages no one because the one-hour window never agrees
    interval: 30s
    input_series:
      - series: 'http_requests_total{route="/checkout",status="200"}'
        values: "0+250x140 35000x3 35000+250x200"
      - series: 'http_requests_total{route="/checkout",status="500"}'
        values: "0x140 0+250x3 750x200"
      - series: 'http_requests_total{route="/healthz",status="200"}'
        values: "0+4x400"
    alert_rule_test:
      - eval_time: 70m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts: []
      - eval_time: 71m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts: []
      - eval_time: 72m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts: []
      - eval_time: 75m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts: []
      - eval_time: 80m
        alertname: AvailabilityBudgetSlowBurn
        exp_alerts: []
```

**Test 2, a 90-second blip pages no one.** At 30-second spacing: healthy for 70 minutes (`0+250x140`), then 90 seconds of failures (`0+250x3` on the 500 counter), then healthy again. At 70, 71, 72 and 75 minutes the fast-burn alert must not fire, and at 80 minutes the slow-burn alert must not either: the one-hour window never agrees with the five-minute one.

```yaml
  - name: a steady 3 percent leak opens a ticket and never pages
    interval: 1m
    input_series:
      - series: 'http_requests_total{route="/checkout",status="200"}'
        values: "0+500x120 60000+485x140"
      - series: 'http_requests_total{route="/checkout",status="500"}'
        values: "0+0x120 0+15x140"
      - series: 'http_requests_total{route="/healthz",status="200"}'
        values: "0+4x400"
    alert_rule_test:
      - eval_time: 3h
        alertname: AvailabilityBudgetSlowBurn
        exp_alerts: []
      - eval_time: 3h50m
        alertname: AvailabilityBudgetSlowBurn
        exp_alerts:
          - exp_labels:
              severity: ticket
              slo: availability
            exp_annotations:
              summary: "Availability error budget burning at 5x: 1.4% of the 30-day budget in 2h."
              runbook: "Check the availability SLI panel, open an error or slow trace from the last 15m, and roll back or fix the cause."
      - eval_time: 3h50m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts: []
```

**Test 3, a steady 3 percent leak opens a ticket and never pages.** 500 requests a minute with 15 failing (`0+485x140` good, `0+15x140` bad) after two healthy hours. At 3 hours the slow-burn ticket is not yet firing; at 3 hours 50 minutes it is, with `severity: ticket`; and at the same time `AvailabilityBudgetFastBurn` is not firing.

```yaml
  - name: health checks do not dilute the error ratio
    interval: 1m
    input_series:
      - series: 'http_requests_total{route="/checkout",status="200"}'
        values: "0+500x70 35000x40"
      - series: 'http_requests_total{route="/checkout",status="500"}'
        values: "0x70 0+500x40"
      - series: 'http_requests_total{route="/healthz",status="200"}'
        values: "0+4000x110"
    alert_rule_test:
      - eval_time: 80m
        alertname: AvailabilityBudgetFastBurn
        exp_alerts:
          - exp_labels:
              severity: page
              slo: availability
            exp_annotations:
              summary: "Availability error budget burning at 14.4x: 2.0% of the 30-day budget in 1h."
              runbook: "Check the availability SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause."
```

**Test 4, health checks do not dilute the error ratio.** The same outage as test 1, but the health route now grows by 4,000 a minute (eight times the checkout traffic). The fast-burn alert must still fire at 80 minutes, because the SLI excludes `/healthz`.

```yaml
  - name: sustained slowness pages the latency SLO and ordinary speed does not
    interval: 1m
    input_series:
      - series: 'http_request_duration_seconds_bucket{route="/checkout",le="0.3"}'
        values: "0+490x70 34300x60"
      - series: 'http_request_duration_seconds_count{route="/checkout"}'
        values: "0+500x130"
    alert_rule_test:
      - eval_time: 70m
        alertname: LatencyBudgetFastBurn
        exp_alerts: []
      - eval_time: 90m
        alertname: LatencyBudgetFastBurn
        exp_alerts: []
      - eval_time: 125m
        alertname: LatencyBudgetFastBurn
        exp_alerts:
          - exp_labels:
              severity: page
              slo: latency
            exp_annotations:
              summary: "Latency error budget burning at 14.4x: 2.0% of the 30-day budget in 1h."
              runbook: "Check the latency SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause."
```

**Test 5, sustained slowness pages the latency SLO and ordinary speed does not.** The count of checkout requests grows by 500 a minute for the whole test. The `le="0.3"` bucket (requests within 300 ms) grows by 490 a minute for 70 minutes, which is 2 percent slow, the ordinary share, and then stays frozen at 34,300 for 60 minutes: from minute 70 on, every new request is slower than 300 ms. At 70 and 90 minutes the latency fast-burn alert must not fire (ordinary speed, then too little history of slowness for the one-hour window to agree); at 125 minutes it fires with `slo: latency` and the generated summary and runbook.

### How `make` runs them

```makefile
# promtool, amtool and the Collector validate their own files, so a typo fails here and not at 3 a.m.
lint-config: ## promtool checks every rule file and config; amtool and the Collector validate theirs
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check rules /etc/prometheus/rules/burn_rate.rules.yml /etc/prometheus/replay/threshold.rules.yml \
	  /etc/prometheus/replay/burn_rate_lesson.rules.yml
	docker run --rm --entrypoint sh -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) -c \
	  'mkdir -p /tmp/rules && cp /etc/prometheus/rules/*.yml /tmp/rules/ && sed "s#/etc/prometheus/rules#/tmp/rules#" /etc/prometheus/prometheus.yml > /tmp/prometheus.yml && promtool check config /tmp/prometheus.yml'
	docker run --rm --entrypoint amtool -v "$(CURDIR)/observability/alertmanager:/etc/alertmanager:ro" $(ALERTMANAGER_IMAGE) \
	  check-config /etc/alertmanager/alertmanager.yml
	docker run --rm -v "$(CURDIR)/observability/collector/gateway.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
	docker run --rm -v "$(CURDIR)/observability/collector/gateway.raw.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
```

`lint-config` runs, in throw-away containers: `promtool check rules` on the live rules and both replay rule files; a `sh -c` script that rewrites the rules path in a copy of the live configuration and runs `promtool check config` on it (the configuration names `/etc/prometheus/rules/`, which exists only in the real container); `amtool check-config` on the Alertmanager file; and `validate` on both gateway files.

```makefile
test-rules: ## replay scenarios through promtool: a fast burn pages, a blip does not, a slow leak opens a ticket
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  test rules /etc/prometheus/tests/burn_rate.rules.test.yml
```

`test-rules` runs `promtool test rules` on the test file.

## Try it yourself

```bash
make test-rules
```

Expected: `SUCCESS`.

```bash
make lint-config
```

Expected: `SUCCESS` for the rule files, the configuration and the Alertmanager file, and silence from the two Collector validations.

```bash
curl -s "http://127.0.0.1:29090/api/v1/rules" | python3 -c "import json,sys; print([r['name'] for g in json.load(sys.stdin)['data']['groups'] for r in g['rules']])"
```

Expected (needs `make up`): `['AvailabilityBudgetFastBurn', 'AvailabilityBudgetSlowBurn', 'LatencyBudgetFastBurn', 'LatencyBudgetSlowBurn']`, and not `HighCPU` or `AnyError`.

```bash
curl -s "http://127.0.0.1:29090/api/v1/targets" | python3 -c "import json,sys; print([(t['labels']['job'], t['health']) for t in json.load(sys.stdin)['data']['activeTargets']])"
```

Expected: three jobs, each `up`.

```bash
make replay
```

Expected (needs `make up`, several minutes): for the threshold set around 500 notifications and under 1 percent actionable; for the burn-rate set 3 notifications, all actionable, every user-facing incident alerted (the outage with a page, the degradation with a ticket), the 90-second blip silent; and the lesson's own policy missing the degradation.

## Mistakes people make

- **High-cardinality labels.** Every distinct label value creates a series. Never label by user id or order id.
- **Using `rate()` on a gauge** or `avg()` of a quantile.
- **Too short a range in `rate(x[30s])`** with a scrape interval close to it: needs at least two samples inside the range.
- **Trusting the p95 of a barely loaded system.** One slow request out of twenty sits right on the p95 edge; a ratio like "share slower than 1 s" is steadier.
- **Reloading a bad config.** Run `promtool check config` first. `make lint-config` does.

## Check yourself

1. Does Prometheus receive data or fetch it? *It fetches (scrapes) it.*
2. What is a time series? *A metric name plus a set of labels, with its points over time.*
3. What are the three series a histogram makes? *`_bucket` (with `le`), `_sum` and `_count`.*
4. What does `for: 1m` mean on an alert? *The expression must be true continuously for one minute before the alert fires.*
5. What does `promtool test rules` need that a real Prometheus supplies in production? *Nothing; you give it fake input series.*

## Further reading

- Prometheus v2.54 documentation: https://prometheus.io/docs/prometheus/2.54/
- Configuration: https://prometheus.io/docs/prometheus/2.54/configuration/configuration/
- Alerting rules: https://prometheus.io/docs/prometheus/2.54/configuration/alerting_rules/
- Unit testing for rules: https://prometheus.io/docs/prometheus/2.54/configuration/unit_testing_rules/
- Metric types: https://prometheus.io/docs/concepts/metric_types/
- Exemplars: https://prometheus.io/docs/prometheus/2.54/feature_flags/#exemplars-storage
- OpenMetrics: https://prometheus.io/docs/instrumenting/exposition_formats/#openmetrics-text-format
