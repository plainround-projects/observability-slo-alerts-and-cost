# Service level objectives for the checkout service

These are the definitions the burn-rate alerts are generated from (`observability/slo/slos.yaml`). Change the YAML, run
`make rules`, and `make lint` fails until the generated rule files match.

## Availability

- **Valid request:** any request to a route other than `/healthz`. Health checks always succeed, so counting them would
  dilute the error ratio (the promtool test "health checks do not dilute the error ratio" shows the difference).
- **Good request:** a valid request that did not end in a 5xx.
- **SLI:** good / valid.
- **SLO:** 99.5% over a rolling 30-day window.
- **Error budget:** 0.5% of valid requests, which is **216 minutes** of total outage over the window, or, at this
  service's baseline of 500 valid requests a minute (21,600,000 a month), **108,000 bad requests** a month.

## Latency

- **Valid request:** the same as above.
- **Good request:** a valid request that finished within 300 ms (the `le="0.3"` bucket of
  `http_request_duration_seconds`; 0.3 is an exact bucket boundary, which is why the service defines its buckets).
- **SLI:** good / valid.
- **SLO:** 95% of valid requests within 300 ms over a rolling 30-day window.
- **Error budget:** 5% of valid requests, which is **2,160 minutes** of every request being slow, or **1,080,000 slow
  requests** a month at 500 a minute.

## Burn-rate alerts

A burn rate of 1 spends the budget exactly over the window. A rule fires when the budget burns at the rule's rate over
**both** of its windows: the long one proves the problem has already cost something, the short one proves it is still
happening.

| Policy | Rule | Burn rate | Windows | Budget spent in the long window | Availability threshold | What it catches |
|---|---|---|---|---|---|---|
| default (live) | page | 14.4 | 1 h and 5 min | 2.0% | bad ratio above 7.2% | an outage: 100% errors reach it in about 5 minutes |
| default (live) | ticket | 5 | 2 h and 15 min | 1.4% | bad ratio above 2.5% | a steady 3% error rate, after about 100 minutes |
| lesson (replayed only) | page | 14.4 | 1 h and 5 min | 2.0% | bad ratio above 7.2% | the same outage |
| lesson (replayed only) | ticket | 6 | 6 h and 30 min | 5.0% | bad ratio above 3.0% | nothing planted this week: see below |

The lesson's ticket rule (6x over 6 hours and 30 minutes) cannot see the planted two-hour, 3% degradation. A 3% error
rate against a 0.5% budget is a burn rate of 6, but it lasts two hours, and the six-hour window averages it down to
about 1% (a third of 3%), far below the 3% threshold. The default policy shortens the long window to two hours and
lowers the burn rate to 5, so the same degradation spends 1.4% of the month's budget in the long window and opens a
ticket. `make replay` runs both and prints the difference.
