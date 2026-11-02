# PromQL

## What it is

PromQL is the query language of Prometheus. A query is an **expression** that produces numbers from stored series. You type it in the Prometheus UI, in a Grafana panel, or in an alert rule. There is no `SELECT`: you name a metric, narrow it with label matchers in braces, and apply functions.

## Why this repository uses it

The SLIs, the alert thresholds, the dashboards and the replay are all PromQL. In particular, the burn-rate expressions are *generated* PromQL: the repository writes the query text from the SLO definition. Reading them correctly is the way to trust the numbers.

## Where it appears

| Path | Expression |
| --- | --- |
| `observability/slo/slos.yaml` | The `good` and `valid` halves of each SLI, with `__WINDOW__`. |
| `observability/prometheus/rules/burn_rate.rules.yml`, `replay/*.rules.yml` | The generated and the threshold alert expressions. |
| `src/slo_lab/domain/slo.py` | `error_ratio_expression`, which builds `(1 - (good) / (valid))`. |
| `observability/grafana/dashboards/slo-overview.json`, `replayed-week.json` | One expression per panel. |
| `src/slo_lab/infrastructure/prometheus_http.py` | Runs alert expressions as range queries. |

## How it works underneath

- A **selector** `metric{label="value"}` picks series. `=` is equal, `!=` is not equal, `=~` matches a regular expression.
- A **range vector** `metric[1m]` is the last minute of points of each selected series.
- `rate(counter[1m])` is the per-second increase of a counter, averaged over the range; it handles counter resets.
- `sum(...)` adds series together. `sum by (label) (...)` keeps the named label and adds the rest. `sum without (label)` drops the named label.
- `histogram_quantile(0.95, sum by (le) (rate(x_bucket[1m])))` estimates the 95th percentile from histogram buckets: it finds the bucket where 95% of observations have been counted and interpolates inside it. The result is an estimate that depends on the bucket boundaries.
- Arithmetic between two results matches series by identical label sets; `sum(...)` without `by` leaves one series with no labels, which makes `sum(a) / sum(b)` match.
- A comparison such as `> 0.02` filters: the expression returns a value only where the comparison is true; for an alert, "returns something" means "fire".
- An **instant query** evaluates at one time. A **range query** evaluates at many times (`start`, `end`, `step`) and is what a graph uses.

Features beyond the basics that this repository uses:

- **Label matchers with regular expressions**: `status!~"5.."`, `status=~"5.."` (the whole value must match).
- **`vector(14.4 * 0.005)`** produces a constant series with no labels; the dashboard uses it to draw the threshold line.
- **`ALERTS{alertstate="firing"}`** is a synthetic series Prometheus creates for every pending or firing alert; `sum by (alertname, severity) (ALERTS{...})` counts them.
- **`and`** between two expressions keeps the left series that have a matching series (same labels) on the right. With `sum(...)` on both sides (no labels), it means "both are non-empty": which is how "both windows exceed the threshold" is written.
- **`increase(x[1m])`** is the growth of a counter over the range, extrapolated; `increase(...) > 0` fires when anything was added.

## Walkthrough of this repository's use

### The availability SLI

```yaml
    good: sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[__WINDOW__]))
    valid: sum(rate(http_requests_total{route!="/healthz"}[__WINDOW__]))
```

`good`: `sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[__WINDOW__]))`, the rate of requests that are not health checks and not 5xx, summed over every series; `valid`: the same without the status filter. `__WINDOW__` becomes `1h`, `5m`, `2h`, `15m`. The bad ratio is `1 - good / valid`.

### The latency SLI

```yaml
    good: sum(rate(http_request_duration_seconds_bucket{route!="/healthz",le="0.3"}[__WINDOW__]))
    valid: sum(rate(http_request_duration_seconds_count{route!="/healthz"}[__WINDOW__]))
```

`good`: the rate of the histogram bucket with `le="0.3"` (requests within 300 ms); `valid`: the rate of `_count` (all requests). Both exclude `/healthz`.

### The generated alert

```yaml
  - alert: AvailabilityBudgetFastBurn
    expr: |-
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[1h]))) / (sum(rate(http_requests_total{route!="/healthz"}[1h])))) > 0.072
      and
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[5m]))) / (sum(rate(http_requests_total{route!="/healthz"}[5m])))) > 0.072
```

Reading the page rule: `(1 - (sum(rate(...[1h]))) / (sum(rate(...[1h])))) > 0.072` — the bad ratio over one hour is above 7.2 percent — `and` — the same over five minutes. The result is a series only when both comparisons are true; a firing alert is simply "this expression returned something". The expression text is built by `error_ratio_expression` from the SLO and by `expression` in `rule_builder.py` from the rule.

### The threshold alerts

```yaml
      - alert: HighCPU
        expr: cpu_usage_percent > 80
        for: 1m
```

`HighCPU`: `cpu_usage_percent > 80` for a minute: a gauge compared with a number.

```yaml
      - alert: AnyError
        expr: increase(http_requests_total{status=~"5.."}[1m]) > 0
        labels:
```

`AnyError`: `increase(http_requests_total{status=~"5.."}[1m]) > 0`: any 5xx in the last minute.

### The dashboards

```json
          "expr": "1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[1h]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[1h])))",
```

Panel 1, the bad-request ratio over 1 hour; its siblings use `[5m]` and `vector(14.4 * 0.005)` (the page threshold line).

```json
          "expr": "sum by (alertname, severity) (ALERTS{alertstate=\"firing\"})",
```

Panel 5 counts the alerts that are firing now, by name and severity.

Panel 2 divides each ratio by the error budget (`/ 0.005`) to show the **burn rate** directly: 1.0 spends exactly the monthly budget; the page rule fires above 14.4. Panel 3 is the latency SLI's bad share, `1 - sum(rate(..._bucket{...,le="0.3"}[1h])) / sum(rate(..._count{...}[1h]))`. Panel 4 is requests per second by status.

## Try it yourself

```bash
curl -s --get "http://127.0.0.1:29090/api/v1/query" --data-urlencode 'query=1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[5m]))) / (sum(rate(http_requests_total{route!="/healthz"}[5m])))'
```

Expected (needs `make up` and some traffic): a value near 0 (for example `0.0001`), the bad-request ratio of the last five minutes.

```bash
.venv/bin/python -c "
from slo_lab.infrastructure.definitions_io import load_slos
from pathlib import Path
slo = load_slos(Path('observability/slo/slos.yaml'))[0]
print(slo.error_ratio_expression('1h'))"
```

Expected: the availability bad-ratio expression with `[1h]` in both halves.

```bash
curl -s --get "http://127.0.0.1:29090/api/v1/query" --data-urlencode 'query=vector(14.4 * 0.005)'
```

Expected: a value `0.072`.

```bash
curl -s --get "http://127.0.0.1:29090/api/v1/query" --data-urlencode 'query=ALERTS'
```

Expected: an empty result while nothing fires; during `make live`, `AvailabilityBudgetFastBurn` appears with `alertstate="firing"`.

```bash
curl -s --get "http://127.0.0.1:29091/api/v1/query_range" --data-urlencode 'query=cpu_usage_percent > 80' --data-urlencode "start=$(date -v-7d +%s)" --data-urlencode "end=$(date +%s)" --data-urlencode 'step=3600' | head -c 300
```

Expected (after `make replay`; `date -v-7d` is the macOS form, on Linux use `date -d '7 days ago' +%s`): the hours of the replayed week in which CPU was above 80 percent.

## Mistakes people make

- **`rate()` on a histogram `_bucket` without summing by `le`** first.
- **Averaging percentiles** across instances. Sum the buckets, then take the quantile.
- **Dividing by zero** when traffic stops: the result is `NaN`, and a comparison with NaN is false.
- **Forgetting that quoting rules** differ in JSON (`\"`) and YAML.

## Check yourself

1. What does `rate(x[1m])` return? *The per-second increase of a counter averaged over the last minute.*
2. What does `sum by (le) (...)` keep? *Only the `le` label; other labels are added together.*
3. How do you estimate the p95 from a histogram? *`histogram_quantile(0.95, sum by (le) (rate(..._bucket[...])))`.*
4. What does `metric[5m]` mean? *A range vector: all points of the last five minutes.*
5. What does `>` do in an alert expression? *Filters results to those greater than the number; a non-empty result fires the alert.*

## Further reading

- Querying basics: https://prometheus.io/docs/prometheus/2.54/querying/basics/
- Functions: https://prometheus.io/docs/prometheus/2.54/querying/functions/
- Operators: https://prometheus.io/docs/prometheus/2.54/querying/operators/
- Histograms and summaries: https://prometheus.io/docs/practices/histograms/
