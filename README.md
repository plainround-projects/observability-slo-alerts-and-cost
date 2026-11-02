# observability-slo-alerts-and-cost

Burn-rate alerts on availability and latency SLOs that replace noisy threshold alerts (measured on a replayed week with planted incidents), and a Collector gateway that tail-samples traces and redacts personal data before anything is stored.

| | |
| --- | --- |
| **Lesson** | `/prep/observability/project-slo-alerts-and-cost`, "Replace noisy alerts and cut the telemetry bill" (`data-structure-explainer/src/content/tracks/observability/14-project-slo-alerts-and-cost.md` in the Plainround app) |
| **Planned hours** | 12 |
| **Level** | Advanced |
| **Architecture** | [ARCHITECTURE.md](ARCHITECTURE.md), decisions in [docs/adr](docs/adr), the SLO document in [docs/slo.md](docs/slo.md) |

## Prerequisites

| Tool | Version | Used for |
| --- | --- | --- |
| Docker with Compose v2 | 24 or newer (tested with 24.0.6 and Compose v2.23); about 4 GB for the Docker VM | the whole stack, and `promtool`, `amtool` and the Collector's `validate` for linting |
| Python | 3.12 or 3.13 (tested with 3.13; the CI and the image use 3.12) | the lab commands and the tests on your machine |
| GNU Make | 3.81 or newer (the one on macOS works) | every command below |
| Images | `otel/opentelemetry-collector-contrib:0.111.0`, `grafana/tempo:2.6.1`, `grafana/loki:3.1.1`, `prom/prometheus:v2.54.1`, `prom/alertmanager:v0.27.0`, `grafana/grafana:11.2.0`; `python:3.12.8-slim-bookworm` is the base of the service image | pulled by Docker on first `make up` |
| Python packages | Flask, waitress, requests, PyYAML and the OpenTelemetry distro, exporter and Flask instrumentation at run time; pytest, pytest-cov, ruff, mypy, import-linter, pip-audit for development. All pinned with hashes in `requirements.lock` and `requirements-dev.lock`; `make` installs them into `.venv/` | everything |

No cloud account, no secrets. The values in `.env.example` are local-only placeholders; `make up` copies them to `.env`. Every port is bound to `127.0.0.1`: the checkout service on 28083, the fake pager 28084, Grafana 23000, Prometheus 29090, the replay Prometheus 29091, Tempo 23200, Loki 23100. Change any of them in `.env` if the port is taken.

## Setup, run, test, stop

```bash
make up              # builds the image, starts the service, gateway, Tempo, Loki, Prometheus, Alertmanager, the fake pager and Grafana
make rules           # regenerates the burn-rate rule files from observability/slo/*.yaml (make lint fails if they are stale)
make replay          # steps 1 to 4: a simulated week backfilled into a second Prometheus; threshold vs burn-rate alerts compared
make volume          # steps 5 and 6: the same traffic through a raw and a protected gateway; bytes stored, errors found, test values searched for
make live            # a real outage on the running stack, and the fast-burn page arriving at the fake pager (about 6 minutes)
make run             # replay, volume and live in turn
make check           # format check, lint (incl. promtool, amtool, Collector validate), types, import rules, unit tests with coverage, promtool replay tests
make test-integration   # the service, gateway, Prometheus, Alertmanager and pager wired together (needs make up first)
make test-e2e        # the lesson's steps asserted against the stores and the pager (needs make up first; about 20 minutes)
make audit           # known-vulnerability check of the locked dependencies
make down            # stops everything, including the replay Prometheus, and deletes its data
```

`make help` lists every target. `make volume` recreates the gateway twice, so do not run it while watching a dashboard you want to keep. Grafana is at <http://localhost:23000> (user `admin`, password `GRAFANA_ADMIN_PASSWORD` from `.env`): "SLO overview" for the live service and "Replayed week" for the backfilled one.

What `make replay` prints (a seeded week, so the numbers repeat; the 15 minutes of promtool backfilling and the PromQL replay take about 15 minutes on a busy laptop, a minute or two on a quiet one):

```text
simulating 7 days at 30 s resolution (seed 20261027)
wrote 20160 ticks (19.6 MB); backfilling with promtool
replaying threshold: 2 rules
replaying burn_rate_lesson: 4 rules
replaying burn_rate: 4 rules
threshold: 528 notifications, 3 actionable (0.6%)
    AnyError: 486
    HighCPU: 42
    false alarms during blip-90s: 2
    false alarms during cpu-spike-no-impact: 1
    false alarms during ordinary baseline noise: 522
    full-outage: first page (AnyError) 0 min after onset
    partial-degradation: first page (AnyError) 0 min after onset
burn_rate_lesson: 2 notifications, 2 actionable (100.0%)
    AvailabilityBudgetFastBurn: 1
    AvailabilityBudgetSlowBurn: 1
    full-outage: first page (AvailabilityBudgetFastBurn) 4 min after onset
    partial-degradation: NO ALERT
burn_rate: 3 notifications, 3 actionable (100.0%)
    AvailabilityBudgetFastBurn: 1
    AvailabilityBudgetSlowBurn: 2
    full-outage: first ticket (AvailabilityBudgetSlowBurn) 3 min after onset; first page 4 min after onset
    partial-degradation: first ticket (AvailabilityBudgetSlowBurn) 101 min after onset; no page

threshold -> burn_rate: 528 -> 3 notifications, actionable 0.6% -> 100.0%, every user-facing incident still alerted: True
```

What `make volume` prints (6,000 requests through each gateway, the same seeded traffic: 3% planted errors, 1% planted 1.4-second requests, 5% carrying the test email and the published test card number):

```text
                                           raw gateway   protected gateway
traffic sent (requests)                           6000                6000
trace bytes accepted by Tempo                2,746,057             489,386
log bytes accepted by Loki                   3,660,575           3,650,596
error traces found / sent                      176/176             176/176
slow traces found / sent                         61/61               61/61
ordinary traces kept (sample of 300)            100.0%                4.3%
test email found in stored traces                  307                   0
test card found in stored traces                   307                   0
test email found in logs                           307                   0
test card found in logs                            307                   0
stored trace volume reduced by 82.2%
```

The ordinary-trace row looks up a seeded sample of 300 ordinary traces one by one, so it is exact for the sample: the raw gateway stores all of them, the protected gateway about the 5% its probabilistic policy allows. Traces for the planted errors and slow requests are searched first and the few the search missed are looked up one by one, so those rows are exact. Logs are not sampled, so their volume is unchanged; only their content is.

What `make live` prints (about six minutes; the timing depends on the 5-second evaluation interval, the 5-second group wait and your machine):

```text
warm-up: 120 s of healthy traffic
outage: every request fails for 180 s
page 'AvailabilityBudgetFastBurn' (page) arrived 137 s after the outage began
```

The threshold alerts notify 528 times in the week and 3 of those are inside a planted user-facing incident; the burn-rate set notifies 3 times and all 3 are. The lesson's own slow-burn numbers (`burn_rate_lesson`) produce no alert for the two-hour degradation.


## Done when: the lesson's checklist and the proof for each line

| # | Done when (from the lesson) | Command | What proves it |
| --- | --- | --- | --- |
| 1 | You have the alert count and the share that were actionable. | `make replay` | The report above: `threshold: 528 notifications, 3 actionable (0.6%)`, with the notifications split by alert and the false alarms attributed to the CPU spike, the blip and baseline noise. "Actionable" is objective: a notification that starts inside (or within 15 minutes after) a planted user-facing incident. `pages_threshold.jsonl` in `var/` is the pager log. `tests/e2e` asserts the shape (at least 100 notifications, under 20% actionable). |
| 2 | The SLO document says what counts as valid and good, and the error budget in minutes and requests. | read [docs/slo.md](docs/slo.md) | Valid excludes `/healthz`; good is not a 5xx (availability) or within 300 ms (latency); the budget is 216 minutes and 108,000 bad requests a month at 500 requests a minute for availability, 2,160 minutes and 1,080,000 slow requests for latency. `tests/observability` recomputes these numbers from `slos.yaml` and fails if the document disagrees. |
| 3 | Injected faults that would spend 2 percent of the budget in an hour page within minutes, a short blip pages no one, and a slow leak opens a ticket. | `make test-rules` (promtool replay of injected series) and `make replay` (the planted week) | promtool: the outage pages after about 5 minutes of 100% errors (not at 2); the 90-second blip pages nobody at any evaluation; a steady 3% leak opens a ticket after about 100 minutes and never pages; health checks do not dilute the ratio; sustained slowness pages the latency SLO. Replay: `full-outage ... first page 4 min after onset`; the blip gets no burn-rate alert; the degradation gets a ticket 101 minutes in. Mutating the page threshold in a copy makes promtool fail, so the tests can fail. |
| 4 | The README shows alert counts and the actionable share before and after, and every planted user-facing problem still produced an alert. | `make replay` | `528 -> 3 notifications, actionable 0.6% -> 100.0%, every user-facing incident still alerted: True`, with the same seed and the same incident schedule for every set. The command exits 1 if the burn-rate set missed an incident or was not quieter and more actionable. |
| 5 | Stored trace volume drops by most of its size, and every planted error or slow request can still be found. | `make volume` | The table's "trace bytes accepted by Tempo", "error traces found / sent" and "slow traces found / sent" rows for the raw and the protected gateway, and "stored trace volume reduced by". `tests/e2e` asserts a reduction of at least 60%, every planted error and slow trace found, and 0 to 15% of the ordinary traces kept. |
| 6 | A search of every backend finds none of the test values, and the README lists what you redact and where. | `make volume` and `make test-integration` | The table's four "test ... found in ..." rows: the raw gateway has them (the positive control), the protected gateway has none, for the email and the card, in Tempo and in Loki. `tests/integration/test_redaction_in_the_collector.py` runs the real Collector image with the redaction processor and shows the values replaced inside the log body and the span attributes while a trace id and an order reference are untouched. What is redacted is listed below. |

**What is redacted, and where** (`transform/redact` in `observability/collector/gateway.yaml`, in the traces and the logs pipelines, before sampling):

| What | Pattern | Where | Replaced with |
| --- | --- | --- | --- |
| Email addresses | `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` | every span attribute value; every log record attribute value; the log body | `<email>` |
| Card-like numbers | 13 to 19 digits, optionally separated by single spaces or dashes | the same three places | `<card>` |

Only the matched text is replaced, so the field, and the words around the value, survive. Metric labels are not touched: they are bounded by design (the previous project's placement rule) and carry no request data. Names, addresses and numbers in other shapes are not caught; see [ARCHITECTURE.md](ARCHITECTURE.md#limits-of-the-teaching-build-against-a-production-one).


## Stretch goals

| Stretch (from the lesson) | Built? |
| --- | --- |
| Add an eBPF-based agent and compare what it sees with no code changes against your manual instrumentation. | No. [ARCHITECTURE.md](ARCHITECTURE.md#how-to-extend-it) says how it would plug in. |
| Store one wide event per request instead of separate logs and compare cost and query power. | No. |
| Add an SLO for a background job, where requests are not the right unit. | No. The architecture notes say what the SLI would be (runs finished on time over runs started). |

## What is simplified for teaching

Only what the lesson scopes out, plus where this build differs from the lesson's text:

- One gateway instance, so tail sampling needs no load-balancing tier; one Prometheus and one Alertmanager; local disk; no authentication or TLS.
- The week is **replayed, not played**: a seeded model writes 30-second samples, `promtool` backfills them, and each alert's real expression is run as a range query ([ADR 0001](docs/adr/0001-replay-by-backfill-and-range-queries.md)). The lesson's simulator plays a week in about 20 minutes with one tick per simulated minute, which a real Prometheus (wall-clock windows) cannot evaluate faithfully. The replay takes about a minute.
- A page is one firing episode of an alert (what Alertmanager would send for `group_by: [alertname]`); the replay ignores Alertmanager's grouping delays and repeats. The live outage check exercises the real Alertmanager path once.
- The lesson plants three incidents; there is a fourth, a 90-second blip of 100% errors, because step 3 requires a blip the fast-burn page ignores.
- **The lesson's slow-burn numbers cannot catch its own planted degradation** (6x over 6 hours and 30 minutes against a two-hour 3% error rate: the long window averages it to about 1%). Both policies are replayed and compared; the live Prometheus runs the tuned one (5x over 2 hours and 15 minutes). See [ADR 0002](docs/adr/0002-a-ticket-tier-that-sees-a-two-hour-degradation.md) and [docs/slo.md](docs/slo.md).
- The lesson's budget arithmetic says 500 requests a minute is "about 2,160,000 valid requests/month"; it is 21,600,000, so 0.5% is **108,000** bad requests, not 10,800.
- The lesson's simulator and `analyze_pages.py` are not literal files here: the model is `slo_lab.domain.traffic`, the analysis is `slo-analyze` (and the replay's report). The fake pager is a small service that Alertmanager posts to.
- The service has a lab-only `simulate` field (`ALLOW_SIMULATION=true` in the compose file) so a request can fail or run slowly on demand; a production service has no such field. The service is also deliberately naive about personal data (the email goes on a span, the card number into a log line), which is the leak the gateway exists to stop.
- Stored volume is measured as the bytes Tempo and Loki accepted (their own distributor counters) over the same seeded traffic, not as bytes on disk after compaction.
- The service's CPU gauge exists only in the simulated week, where the threshold alert needs it; the live service does not export one.

## Project structure

```text
src/slo_lab/
  domain/            Slo and its error budget, burn-rate rules and policies, the seeded traffic model and its incidents,
                     alert-firing semantics (for-durations), page classification, the checkout request.
                     Depends on nothing outside the standard library.
  application/       PlaceCheckout, build_alert_rules (thresholds derived from SLO and policy), ReplayAlerts, Comparison,
                     and the ports they need. Depends on domain only.
  infrastructure/    adapters: OpenTelemetry metrics and span annotation, YAML definitions and rule files, the OpenMetrics
                     writer, the Prometheus range-query client, the pager's JSON-lines store, settings (environment,
                     validated), JSON logging, signal handling. Depends on application and domain.
  interfaces/        the checkout service and the fake pager (Flask on waitress), their composition roots, the health command.
                     Depends on everything below; nothing depends on it.
  lab/               the experiments: replay, volume and redaction, live outage, live traffic, the store clients and the
                     CLIs (slo-replay, slo-volume, slo-live, slo-rules, slo-analyze). A sibling of interfaces.
tests/
  unit/              domain, application (with fakes), infrastructure, interfaces, lab. No containers.
  observability/     lints compose, both gateway configs (including the redaction patterns), Prometheus, Alertmanager,
                     the SLO document and the dashboards.
  integration/       the running stack wired together.
  e2e/               the replay, the volume and redaction comparison and the live page, asserted.
observability/
  slo/               slos.yaml and policies.yaml: the source of every threshold.
  prometheus/        prometheus.yml; rules/ (generated burn-rate rules, loaded live); replay/ (the threshold alerts, the
                     lesson's burn-rate numbers, the replay instance's config); tests/ (promtool replay scenarios).
  collector/         gateway.yaml (redact, tail-sample) and gateway.raw.yaml (the positive control).
  alertmanager/ tempo/ loki/ grafana/   the rest of the pipeline and the dashboards as code.
docs/                slo.md (the SLO document, lesson step 2) and the decision records in adr/.
tech/                one beginner-level note per technology the repository uses, with every file quoted and explained (start at tech/README.md).
docker-compose.yml   the stack (the replay Prometheus is the "replay" profile).   Dockerfile  the service and pager image.
pyproject.toml       project metadata and every tool's configuration (ruff, mypy, pytest, import-linter).
requirements*.lock   hash-pinned dependencies (runtime, and runtime plus development tools).
Makefile             the only commands a reader needs (make help).
```

There is no `sql/` folder: nothing in this project touches a database.

The dependency rule (domain depends on nothing; application on domain; infrastructure on both; interfaces and lab on everything; nothing on them) is enforced by `make lint` through import-linter. A second contract forbids `domain` and `application` from importing Flask, requests, YAML, OpenTelemetry or waitress.

## Troubleshooting

- **A port is in use:** change the matching `*_HOST_PORT` in `.env` and run `make up` again.
- **`make replay` finds no data:** the replay Prometheus serves blocks written into the `replay-data` volume; `make down` removes it, and `make replay` rebuilds it from scratch.
- **`make volume` finds fewer traces than expected:** tail sampling waits ten seconds per trace before deciding, and the scenario waits 45 seconds after the traffic; on a very busy machine raise `SETTLE_SECONDS` in `src/slo_lab/lab/volume.py`.
- **Containers are "unhealthy" on a busy machine:** the health checks wait up to ten minutes per container; Grafana's first start migrates its database and is the slowest. `docker compose ps` shows what is still starting.
- **Start clean:** `make down && make up` (the volumes are deleted).

## Author and license

Written by Sumit Srivastava. Released under the MIT License, see [LICENSE](LICENSE). Changes are in [CHANGELOG.md](CHANGELOG.md).
