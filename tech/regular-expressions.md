# Regular expressions (Python `re`, PromQL, TraceQL, the Collector's OTTL)

## What it is

A **regular expression** (regex) is a short pattern that describes a set of strings: "one to 32 letters, digits or dashes", "something that looks like an email address", "a status starting with 5". A program checks whether a text **matches** the pattern, finds the parts that match, or replaces them.

The building blocks:

- A plain character matches itself: `abc` matches `abc`.
- `.` matches any one character (except a line break).
- `[A-Za-z0-9-]` is a **character class**: one character from the listed ranges and symbols. `[^}]` means "one character that is not `}`". `\d` is a digit, `\s` is whitespace, `\b` is a word boundary (the edge between a letter or digit and something else).
- A **quantifier** says how many: `*` zero or more, `+` one or more, `?` zero or one, `{1,32}` between 1 and 32, `{12,18}` between 12 and 18.
- `^` is the start of the text and `$` the end.
- `(...)` is a **group**: it keeps the part that matched so you can read it back. `(?:...)` is a group that only groups, without keeping the text. `|` means "or".
- A backslash makes a special character literal: `\.` is a real dot, `\\` a real backslash.

Several tools in this repository use regular expressions, and they do not all use the same flavor: Python's `re` module, PromQL (Go's RE2 syntax), TraceQL, and the Collector's OTTL language (also RE2, through Go).

## Why this repository uses it

Regexes appear in five places: validating a product code, parsing durations like `5m`, reading numbers out of a Prometheus page, selecting requests whose status starts with 5, and — the lesson's privacy step — finding email addresses and card numbers inside span attributes and log bodies so the gateway can replace them before anything is stored.

## Where it appears

| Where | The pattern |
| --- | --- |
| `src/slo_lab/domain/checkout.py` | The SKU shape. |
| `src/slo_lab/infrastructure/definitions_io.py` | The duration `^(\d+)(s\|m\|h\|d)$`. |
| `src/slo_lab/lab/stores.py` | Reading one metric's value out of the Prometheus text page. |
| `observability/slo/slos.yaml`, the generated rules | `status!~"5.."` in PromQL. |
| `observability/prometheus/replay/threshold.rules.yml` | `status=~"5.."`. |
| `observability/collector/gateway.yaml` | The email and card patterns of the redaction. |
| `src/slo_lab/lab/volume.py` | TraceQL `=~` regex on attribute values. |
| `tests/observability/test_configuration_as_code.py`, `tests/integration/test_redaction_in_the_collector.py` | Check the redaction patterns on sample text. |

## How it works underneath

A regex engine reads the pattern, builds a small machine from it, and runs it over the text. Python's `re` is a **backtracking** engine: it tries one path, and if that fails it goes back and tries another; some patterns written carelessly can take exponential time on certain inputs. Go's RE2 (used by Prometheus, Loki, Tempo and the Collector) is built to run in time proportional to the text, and does not support some features (such as look-behind).

`re.compile(pattern)` turns a pattern into an object once; `pattern.fullmatch(text)` succeeds only if the **whole** text matches, `pattern.match(text)` anchors at the start, `pattern.findall(text)` returns every match (or every group), `re.sub(pattern, replacement, text)` replaces. A **raw string** `r"..."` stops Python from treating backslashes as escapes, so `r"\d"` is the two characters backslash and `d`.

In PromQL, `=~` means "label matches the regex" and `!~` "does not match"; the pattern must match the **entire** label value (it is implicitly anchored), so `5..` matches `500` and `503` but not `1500`.

## Walkthrough of this repository's use

### The SKU

```python
_SKU = re.compile(r"^[A-Za-z0-9-]{1,32}$")
```

`^[A-Za-z0-9-]{1,32}$`: from the start, one to 32 characters, each a letter, a digit or a dash, then the end. `SKU-001` matches; `bad sku` (a space), an empty string and a 33-character code do not. `OrderLine.__post_init__` uses `fullmatch`, which makes the `^` and `$` redundant but harmless.

### The duration

```python
_DURATION = re.compile(r"^(\d+)(s|m|h|d)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
```

`^(\d+)(s|m|h|d)$`: one or more digits (group 1), then exactly one of the four unit letters (group 2). `5m` matches with groups `5` and `m`; `1.5h`, `1w`, `-5m`, `5` and `m` do not. The second line maps the letter to seconds.

```python
def parse_duration(text: str) -> timedelta:
    """Parse ``5m``, ``1h``, ``30s``, ``0m`` or ``2d``."""
    match = _DURATION.fullmatch(text.strip())
    if not match:
        raise DefinitionError(f"not a duration: {text!r}")
    return timedelta(seconds=int(match.group(1)) * _UNIT_SECONDS[match.group(2)])
```

`fullmatch(text.strip())` returns a match object or `None`; `match.group(1)` and `match.group(2)` read the groups back.

### Reading one metric's value out of a page

```python
def metric_value(text: str, name: str) -> float:
    """Sum every sample of ``name`` in Prometheus text exposition (all label sets)."""
    pattern = re.compile(rf"^{re.escape(name)}(?:{{[^}}]*}})?\s+([0-9.eE+-]+)", re.MULTILINE)
    return sum(float(match) for match in pattern.findall(text))
```

The pattern is built with an **f-string** and `re.escape(name)`, which backslash-escapes the metric name so characters like `.` in it are literal. `rf"..."` is a raw f-string, and `{{` and `}}` inside it are literal braces. The pattern means: at the start of a line (`re.MULTILINE` makes `^` match after each line break), the name, then optionally a label set `{[^}]*}` (an opening brace, any characters except `}`, a closing brace), then whitespace, then the number `([0-9.eE+-]+)` captured in a group. `findall` returns every captured number; `sum(float(...))` adds them. A line `a_total{tenant="x"} 10` and a line `a_total 1e2` both count; `ab_total 99` does not (the name must be followed by `{` or whitespace), and a comment line `# a_total 1` does not (it starts with `#`).

### Status classes in PromQL

```yaml
    good: sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[__WINDOW__]))
    valid: sum(rate(http_requests_total{route!="/healthz"}[__WINDOW__]))
```

`status!~"5.."` keeps requests whose status does **not** match "5 followed by any two characters": all 5xx. `route!="/healthz"` is an exact "not equal", no regex needed.

```yaml
        expr: increase(http_requests_total{status=~"5.."}[1m]) > 0
```

`status=~"5.."` keeps the 5xx.

### The redaction patterns

```yaml
  # Replaces the sensitive value inside an attribute or a log body and keeps the rest, so the field that held it (and
  # what surrounded it) still helps debugging. Email addresses and 13 to 19 digit card-like numbers (spaces or dashes
  # allowed between digits).
  transform/redact:
    error_mode: ignore
    trace_statements:
      - context: span
        statements:
          - 'replace_all_patterns(attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "<email>")'
          - 'replace_all_patterns(attributes, "value", "\\b\\d(?:[ -]?\\d){12,18}\\b", "<card>")'
    log_statements:
      - context: log
        statements:
          - 'replace_pattern(body, "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "<email>")'
          - 'replace_pattern(body, "\\b\\d(?:[ -]?\\d){12,18}\\b", "<card>")'
          - 'replace_all_patterns(attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "<email>")'
          - 'replace_all_patterns(attributes, "value", "\\b\\d(?:[ -]?\\d){12,18}\\b", "<card>")'
```

The comment says what they do: replace the sensitive value inside an attribute or a log body and keep the rest. The two patterns (written inside a single-quoted YAML string, in an OTTL function call, with each backslash doubled because the OTTL string syntax treats `\` as an escape):

- Email: `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` — one or more characters allowed in the local part, an `@`, one or more characters allowed in the domain, a literal dot, and a top-level domain of two or more letters. It is deliberately simple; it catches ordinary addresses.
- Card: `\b\d(?:[ -]?\d){12,18}\b` — a word boundary, a digit, then 12 to 18 more digits each optionally preceded by one space or dash, then a word boundary. So 13 to 19 digits in all, with or without separators (`4242424242424242`, `4242 4242 4242 4242`, `4242-4242-4242-4242`). `(?:...)` groups without capturing. The boundary at the end stops the pattern from swallowing the character that follows. The trailing `\b` was a fix: an earlier version consumed the separator after the number and produced `<card>ok` instead of `<card> ok`.

The patterns are replaced by `<email>` and `<card>` inside the text, so `payment from a@b.com with card 4242 4242 4242 4242 ok` becomes `payment from <email> with card <card> ok`.

### Checking the patterns

```python
def _redaction_patterns(gateway: dict[str, Any]) -> list[str]:
    statements = [
        statement
        for group in gateway["processors"]["transform/redact"]["trace_statements"]
        + gateway["processors"]["transform/redact"]["log_statements"]
        for statement in group["statements"]
    ]
    patterns = []
    for statement in statements:
        literals = re.findall(r'"((?:[^"\\]|\\.)*)"', statement)
        assert literals[-1] in {"<email>", "<card>"}, statement
        patterns.append(literals[-2].replace("\\\\", "\\"))
    return patterns


def test_the_redaction_patterns_remove_the_test_email_and_card_and_leave_ordinary_values_alone(gateway):
    patterns = _redaction_patterns(gateway)
    email_patterns = [pattern for pattern in patterns if "@" in pattern]
    card_patterns = [pattern for pattern in patterns if "@" not in pattern]
    assert email_patterns
    assert card_patterns
    for pattern in email_patterns:
        assert re.sub(pattern, "<email>", f"payment from {TEST_EMAIL} done") == "payment from <email> done"
    for pattern in card_patterns:
        for card in (TEST_CARD, "4242 4242 4242 4242", "4242-4242-4242-4242"):
            assert re.sub(pattern, "<card>", f"card {card} ok") == "card <card> ok"
    for harmless in ("ord-000042", "0af7651916cd43dd8448eb211c80319c", "1.4s", "2026-10-27T10:00:00Z", "SKU-017"):
        for pattern in patterns:
            assert re.sub(pattern, "X", harmless) == harmless
```

The test pulls the patterns out of the YAML statements (`re.findall(r'"((?:[^"\\]|\\.)*)"', statement)` finds the double-quoted string literals of an OTTL statement; `\\\\` is turned back into `\\` for Python), then applies each to sample text: the test email, the card in three spellings, and harmless values (`ord-000042`, a 32-hex trace id, `1.4s`, a timestamp, `SKU-017`), which must be left unchanged. A pattern that matched too much (a 32-digit trace id, for example) would fail here, before it could damage data in production.

## Try it yourself

```bash
.venv/bin/python -c "
import re
print(re.sub(r'\b\d(?:[ -]?\d){12,18}\b', '<card>', 'card 4242 4242 4242 4242 ok and ord-000042'))"
```

Expected: `card <card> ok and ord-000042`.

```bash
.venv/bin/python -c "
import re
pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
print(re.sub(pattern, '<email>', 'payment from test-user@example.com done'))"
```

Expected: `payment from <email> done`.

```bash
.venv/bin/python -c "
from slo_lab.infrastructure.definitions_io import parse_duration
print(parse_duration('90s'), parse_duration('2h'))"
```

Expected: `0:01:30 2:00:00`.

```bash
.venv/bin/python -c "
from slo_lab.lab.stores import metric_value
print(metric_value('a_total{x=\"1\"} 10\na_total 2.5\nab_total 99\n', 'a_total'))"
```

Expected: `12.5`.

```bash
.venv/bin/python -m pytest tests/observability -k redaction -q
```

Expected: `1 passed`.

## Mistakes people make

- **Forgetting that `.` means "any character".** To match a literal dot, escape it (`\.`).
- **Unanchored patterns.** `\d{3}` matches the digits inside a longer number; use anchors or `fullmatch`.
- **A pattern that is too greedy**, such as one that matches any long run of digits and so redacts trace ids and timestamps. The harmless-values test guards against it.
- **Mixing flavors.** A Python-only feature (look-behind) will fail in Prometheus or the Collector.
- **Not escaping user text** put into a pattern. `re.escape` is used for the metric name.
- **Trusting a regex as a security boundary.** Redaction by pattern catches the common shapes only; the lesson says the real fix is not to put personal data into telemetry at all.

## Check yourself

1. What does `fullmatch` require that `match` does not? *The whole text must match, not just its start.*
2. What does `{12,18}` mean? *Between 12 and 18 repetitions.*
3. Why `(?:...)` and not `(...)`? *It groups without capturing the text.*
4. What does `status!~"5.."` select in PromQL? *Requests whose status is not a 5xx.*
5. Why does the card pattern end with `\b`? *So it does not swallow the character after the number.*

## Further reading

- Python `re` (3.12): https://docs.python.org/3.12/library/re.html
- Regular Expression HOWTO: https://docs.python.org/3.12/howto/regex.html
- RE2 syntax (used by Prometheus, Loki, Tempo and the Collector): https://github.com/google/re2/wiki/Syntax
- PromQL selectors and matchers: https://prometheus.io/docs/prometheus/2.54/querying/basics/#time-series-selectors
