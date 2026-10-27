# 3. Redact first, then sample, and replace the value instead of the field

Status: accepted, 2026-10-27

## Context

The gateway has to remove personal data from span attributes and log bodies and keep every error and slow trace. Both are
processors in the same pipeline, so their order matters, and a redaction can destroy debugging value if it is too blunt.

## Decision

The traces pipeline is `memory_limiter`, `transform/redact`, `tail_sampling`, `batch`; the logs pipeline is
`memory_limiter`, `transform/redact`, `batch`. Redaction uses OTTL `replace_all_patterns` (attributes) and
`replace_pattern` (log bodies) to replace the matched text with `<email>` or `<card>`, leaving the rest of the attribute
or line intact. The patterns are an email address and 13 to 19 digits with optional spaces or dashes between them.

## Alternatives considered

- **Sample first.** The sampler would hold unredacted spans in memory for ten seconds, and a policy that matched on an
  attribute would see the sensitive value.
- **Delete the attribute.** Loses the knowledge that the field was set and what surrounded the value.
- **Hash the value.** Keeps equality (the same email hashes the same way), which is useful, but a hash of a low-entropy
  value such as a card number can be reversed by brute force; masking is the safe default.
- **The contrib `redaction` processor.** In this Collector version (0.111.0) it is Beta for traces and Alpha for logs.
  `transform` is Alpha for every signal, but one OTTL statement list per signal states exactly which field is rewritten
  and with what, including the log body where the card number lands, and keeps the whole policy in one processor.

## Consequences

- A test applies the extracted patterns to the test email, the test card in three formats and a list of harmless strings
  (trace ids, order references, timestamps) and requires no change to the harmless ones.
- The raw gateway config is the positive control: the volume scenario finds the test values there.
