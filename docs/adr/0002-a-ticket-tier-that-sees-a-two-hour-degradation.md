# 2. The ticket tier is 5x over two hours, not 6x over six

Status: accepted, 2026-10-27

## Context

The lesson plants a two-hour degradation at 3% errors against a 99.5% objective and says the slow-burn rule (6x over 6
hours and 30 minutes) opens a ticket 42 minutes in. The arithmetic does not allow it. The burn rate of 3% against a 0.5%
budget is 6, but it lasts two hours; the six-hour window therefore averages it to a third of 3%, about 1%, while the rule
needs more than 3%. The degradation spends 1.7% of the month's budget, below the 5% the six-hour rule is built to notice.

## Decision

`policies.yaml` keeps both: `lesson` (14.4x over 1 h and 5 m; 6x over 6 h and 30 m), replayed for comparison only, and
`default`, which the live Prometheus runs: 14.4x over 1 h and 5 m (page), 5x over 2 h and 15 m (ticket). The ticket
threshold is a bad ratio of 2.5%; the planted degradation reaches it about 100 minutes in and holds it until it ends.

## Alternatives considered

- **Keep the lesson's numbers and weaken the incident** (raise the error rate until it fits). Changes the planted problem
  to fit the rule.
- **Page on the degradation.** A fast burn threshold low enough to catch 3% would page on every blip.

## Consequences

- The Done-when line "every planted user-facing problem still produced an alert" holds, and the README shows the
  lesson's parameters missing it. That finding is itself an interview point: standard thresholds are a starting point,
  and the windows must be chosen against the incidents you need to catch.
- The ticket arrives 100 minutes into a 120-minute incident: late, but a ticket's job is to make sure someone looks the
  next working day, and the budget spent by then is about 1.4%.
