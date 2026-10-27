# 4. Only traces are sampled; metrics and the SLIs are not

Status: accepted, 2026-10-27

## Context

Tail sampling keeps a share of traces. If the metrics were derived from the sampled traces (span metrics), every SLI
would count the survivors only, and the error ratio would be biased towards the kept errors.

## Decision

The service records `http_requests_total` and the latency histogram directly with the metrics API, and the gateway's
metrics pipeline has only `memory_limiter` and `batch`. A configuration test asserts that the metrics pipeline contains
neither `tail_sampling` nor `transform/redact`.

## Alternatives considered

- **Span metrics from the Collector.** Convenient, but they inherit whatever the sampling policy dropped.

## Consequences

- The SLI numbers are exact, and the sampling saving applies to traces only.
- Metric labels must be bounded (the previous project's placement rule), which is why nothing needs redacting there.
