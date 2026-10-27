# 1. Replay a week by backfilling it and evaluating rules as range queries

Status: accepted, 2026-10-27

## Context

The lesson's simulator compresses a week into about 20 minutes, one tick per simulated minute. A real Prometheus cannot
evaluate `rate(...[1h])` over compressed time: its windows are wall-clock. Scaling every window by the compression
factor would test different rules from the ones that run in production.

## Decision

The simulator writes a week of 30-second samples as OpenMetrics; `promtool tsdb create-blocks-from openmetrics` turns it
into TSDB blocks; a second Prometheus serves them. The replay then runs each alert's real expression as a PromQL range
query at one-minute steps and applies the `for` duration itself (`firing_episodes`), producing one page per firing
episode. The unchanged production expressions (1 h, 5 m, 2 h, 15 m windows) are what is tested.

## Alternatives considered

- **Play the week live, compressed.** Needs scaled windows, so it tests different rules.
- **`promtool test rules` over a week of input series.** Checks alert states at chosen instants, but cannot count pages
  over a week. It is used for the targeted scenarios (outage, blip, leak) in `tests/`.
- **Backfill recording rules and alert on them.** `promtool` can backfill recording rules, but alert states are not
  backfilled, so the alert logic would still need an external evaluator.

## Consequences

- A week replays in seconds, deterministically, with a seed.
- The `for` handling and the notion of a page are reimplemented in the domain (`firing_episodes`), tested against
  Prometheus's documented semantics, and the live path (`make live`) checks that the real Prometheus and Alertmanager
  agree with it for the fast-burn case.
- Evaluation is at one-minute steps, coarser than the live five-second evaluation interval.
