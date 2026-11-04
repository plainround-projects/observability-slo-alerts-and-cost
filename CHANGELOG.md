# Changelog

All notable changes to this project are recorded here. The format follows Keep a Changelog and the project uses
semantic versioning.

## [Unreleased]

### Added

- Drawn architecture and flow figures in `docs/diagrams/`, light and dark, shown in the README.

## [0.1.0] - 2026-10-27

### Added

- SLO definitions as code (availability 99.5%, latency 95% within 300 ms over 30 days) and burn-rate policies, from which
  the page and ticket alert rules are generated; `make lint` fails when a generated file is stale.
- A seeded simulator of a week of traffic with four planted incidents, backfilled into a second Prometheus with `promtool`,
  and a replay that evaluates the threshold alerts and two burn-rate policies over it with real PromQL.
- A fake pager behind Alertmanager, and a live outage check that waits for the fast-burn page.
- A Collector gateway with redaction (emails, card-like numbers) and tail sampling (errors, slow traces, 5% of the rest),
  a raw gateway as the positive control, and the volume and privacy comparison.
- promtool replay tests (fast burn, blip, slow leak, health-check exclusion, latency), unit, integration and end-to-end
  tests, ruff, mypy and import-linter checks, and a CI workflow.
