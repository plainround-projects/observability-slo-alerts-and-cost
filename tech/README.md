# Technology notes

These notes teach every technology used in `observability-slo-alerts-and-cost`, from zero, through this repository's own files. They assume you have never used any of them. Every source file, configuration file, dashboard, alert rule, compose file and Makefile line in the repository is quoted and explained in one of them. Each note has the same nine parts: what it is, why this repository uses it, where it appears, how it works underneath, a walkthrough of the repository's use, commands to try, common mistakes, five questions to check yourself, and links to the official documentation for the pinned version.

## If you have ten minutes

Read these three, in this order:

1. [slo-and-burn-rate.md](slo-and-burn-rate.md): what an SLO, an error budget and a burn rate are, and why two windows.
2. [opentelemetry-collector.md](opentelemetry-collector.md): the gateway that redacts personal data and tail-samples traces before anything is stored.
3. [prometheus.md](prometheus.md): how the generated rules are evaluated, tested and replayed over a week.

## Reading order

**The idea**

1. [slo-and-burn-rate.md](slo-and-burn-rate.md): SLIs, SLOs, budgets, burn rates and the SLO files.

**The language and its standard library**

2. [python.md](python.md): the language, the domain and application layers, the rule generator.
3. [python-lab-scenarios.md](python-lab-scenarios.md): the replay, volume and live scenarios.
4. [python-logging.md](python-logging.md): JSON log lines with trace ids.
5. [python-threads-and-signals.md](python-threads-and-signals.md): the traffic generator and clean shutdown.
6. [python-subprocess.md](python-subprocess.md): how the scenarios drive Docker and promtool.
7. [argparse-and-urllib.md](argparse-and-urllib.md): the command-line tools and the health probe.
8. [regular-expressions.md](regular-expressions.md): SKUs, durations, PromQL matchers and the redaction patterns.

**Web, data and protocol**

9. [http.md](http.md): the protocol everything speaks.
10. [flask.md](flask.md): the checkout service.
11. [waitress.md](waitress.md): the server that runs it.
12. [requests.md](requests.md): the Prometheus range evaluator and the clients of the stores.
13. [pyyaml.md](pyyaml.md): reading and writing the definitions and generated rules.

**Telemetry, alerting and dashboards**

14. [opentelemetry.md](opentelemetry.md): the SLI metrics, spans and the `OTEL_*` settings.
15. [opentelemetry-collector.md](opentelemetry-collector.md): the gateway.
16. [prometheus.md](prometheus.md), [promql.md](promql.md) and [openmetrics.md](openmetrics.md): rules, queries and the backfilled week.
17. [alertmanager.md](alertmanager.md): routing, inhibition and the fake pager.
18. [tempo.md](tempo.md) and [loki.md](loki.md): the stores.
19. [grafana.md](grafana.md): provisioning and the two dashboards.

**Running everything**

20. [docker.md](docker.md): the image.
21. [docker-compose.md](docker-compose.md): the stack and the replay profile.
22. [make.md](make.md): the commands you type.
23. [bash.md](bash.md): the shell inside them.
24. [dotenv.md](dotenv.md): the `.env` files.

**Tests and quality**

25. [pytest.md](pytest.md): all tests and coverage.
26. [ruff.md](ruff.md), [mypy.md](mypy.md), [import-linter.md](import-linter.md): lint, types, layers.
27. [uv-and-lock-files.md](uv-and-lock-files.md): locked dependencies and the audit.
28. [pyproject-toml.md](pyproject-toml.md): packaging and tool settings.
29. [github-actions.md](github-actions.md): CI.

**File formats and housekeeping**

30. [yaml.md](yaml.md) and [json.md](json.md).
31. [git-and-editorconfig.md](git-and-editorconfig.md).

## The files and their roles

| File | Role |
| --- | --- |
| [slo-and-burn-rate.md](slo-and-burn-rate.md) | The concept, `slos.yaml`, `policies.yaml`, the generated rules and the threshold alerts. |
| [python.md](python.md) | Python 3.12: domain, application, settings, runtime helpers. |
| [python-lab-scenarios.md](python-lab-scenarios.md) | The three experiments as classes with injected collaborators. |
| [python-logging.md](python-logging.md) | The standard `logging` module and the JSON formatter. |
| [python-threads-and-signals.md](python-threads-and-signals.md) | Threads, locks, thread-local sessions, signals and clocks. |
| [python-subprocess.md](python-subprocess.md) | `subprocess` and the Docker control. |
| [argparse-and-urllib.md](argparse-and-urllib.md) | The eight command-line tools and the health probes. |
| [regular-expressions.md](regular-expressions.md) | Python `re`, PromQL, TraceQL and OTTL patterns. |
| [http.md](http.md) | Statuses as SLIs, webhooks, timeouts, chunked range queries. |
| [flask.md](flask.md) | The checkout service. |
| [waitress.md](waitress.md) | The production WSGI server. |
| [requests.md](requests.md) | HTTP clients and the range evaluator. |
| [pyyaml.md](pyyaml.md) | Reading and writing YAML, custom dumper. |
| [opentelemetry.md](opentelemetry.md) | The SLI metrics and the span annotator. |
| [opentelemetry-collector.md](opentelemetry-collector.md) | The raw and the protected gateway, redaction and tail sampling. |
| [prometheus.md](prometheus.md) | Live and replay Prometheus, promtool and the rule tests. |
| [promql.md](promql.md) | Every PromQL expression in the repository. |
| [openmetrics.md](openmetrics.md) | The OpenMetrics file and `promtool` backfill. |
| [alertmanager.md](alertmanager.md) | Alertmanager, the inhibit rule and the fake pager. |
| [tempo.md](tempo.md) | Tempo configuration, TraceQL searches and its byte counters. |
| [loki.md](loki.md) | Loki configuration and LogQL checks. |
| [grafana.md](grafana.md) | Four data sources and two dashboards. |
| [docker.md](docker.md) | The Dockerfile (cache mounts, digest pin, non-root user, `/data`) and `.dockerignore`. |
| [docker-compose.md](docker-compose.md) | The Compose file, key by key. |
| [make.md](make.md) | The Makefile, target by target. |
| [bash.md](bash.md) | The shell in recipes, health checks and the redaction test. |
| [dotenv.md](dotenv.md) | `.env` and `.env.example`. |
| [pytest.md](pytest.md) | Unit, configuration, integration and end-to-end tests; coverage. |
| [ruff.md](ruff.md) | Linting and formatting. |
| [mypy.md](mypy.md) | Static type checking. |
| [import-linter.md](import-linter.md) | The layer and forbidden-import contracts. |
| [uv-and-lock-files.md](uv-and-lock-files.md) | The hash-pinned lock files and `pip-audit`. |
| [pyproject-toml.md](pyproject-toml.md) | Package metadata, eight commands and TOML. |
| [github-actions.md](github-actions.md) | The CI workflow. |
| [yaml.md](yaml.md) | The YAML features used by the configuration files. |
| [json.md](json.md) | JSON in dashboards, requests, the pager and OTLP. |
| [git-and-editorconfig.md](git-and-editorconfig.md) | `.gitignore` and `.editorconfig`. |
