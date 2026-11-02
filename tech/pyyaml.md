# PyYAML (6.0)

## What it is

PyYAML is the Python library that reads and writes YAML files (see `yaml.md` for the format itself). `yaml.safe_load(text)` turns YAML text into plain Python dicts, lists, strings, numbers, booleans and `None`. `yaml.dump(data, ...)` turns such data back into YAML text. "Safe" means the loader will only build those plain types and never run code or build arbitrary objects, which is what you want for any file you did not write yourself.

A **dumper** is the class that decides how each Python value is written. You can subclass `yaml.SafeDumper` and register a **representer**: a function that tells the dumper how to write one type.

## Why this repository uses it

The lesson's idea is that thresholds are derived, never typed. The SLO objectives and the burn-rate policies live in two small YAML files; Python reads them, calculates every alert threshold and writes the Prometheus rule files as YAML again. The same library reads the committed rule files back (for the replay) and the configuration tests use it to read the compose file and the Collector's configuration.

## Where it appears

| Path | What it does with PyYAML |
| --- | --- |
| `src/slo_lab/infrastructure/definitions_io.py` | Reads `slos.yaml`, `policies.yaml` and rule files; writes the generated rule files. |
| `src/slo_lab/lab/rules_cli.py` | Calls the writer (`make rules`) or compares with the committed files (`make lint`). |
| `tests/observability/test_configuration_as_code.py` | Reads every configuration file with `yaml.safe_load`. |
| `tests/unit/infrastructure/test_infrastructure.py` | Tests the reader and the writer. |
| `pyproject.toml` | Depends on `pyyaml` at run time (the generator is part of the product) and on `types-PyYAML` for mypy. |

## How it works underneath

`safe_load` runs a **parser** that turns the text into a tree of nodes (mappings, sequences, scalars), then a **constructor** that turns each node into a Python value. Quoted `"5"` becomes a string; unquoted `5` becomes an `int`; `>-` and `|-` folded and literal block scalars become strings. Anything that is not a mapping at the top of the file (for example a list) is returned as a list; the code checks for that.

`yaml.dump(document, Dumper=..., sort_keys=False, width=10_000)` walks the Python value and asks the dumper's **representers** to turn each into nodes, then an **emitter** writes the text. `sort_keys=False` keeps the order in which the dict was built, which matters for readable rule files. `width` is where long lines would be wrapped; a huge value keeps a long PromQL expression on one line.

## Walkthrough of this repository's use

```python
"""Read and write the YAML files that define SLOs, burn-rate policies and alert rules."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from slo_lab.domain.alerts import AlertRule
from slo_lab.domain.burn_rate import BurnRatePolicy, BurnRateRule
from slo_lab.domain.errors import DomainError
from slo_lab.domain.slo import Slo

_DURATION = re.compile(r"^(\d+)(s|m|h|d)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
```

The imports bring in `re` (for durations), `timedelta`, `Path`, `yaml` and the domain types. `_DURATION` is a regular expression for a duration such as `5m` (see `regular-expressions.md`) and `_UNIT_SECONDS` maps a unit letter to seconds.

```python
class DefinitionError(DomainError):
    """A definition file is malformed."""


def parse_duration(text: str) -> timedelta:
    """Parse ``5m``, ``1h``, ``30s``, ``0m`` or ``2d``."""
    match = _DURATION.fullmatch(text.strip())
    if not match:
        raise DefinitionError(f"not a duration: {text!r}")
    return timedelta(seconds=int(match.group(1)) * _UNIT_SECONDS[match.group(2)])
```

`DefinitionError` is the error for a malformed file. `parse_duration` accepts `0m`, `30s`, `5m`, `2h`, `1d` and nothing else (`fullmatch` must match the whole text; `1.5h`, `1w` and `-5m` are rejected): it multiplies the number by the unit's seconds and returns a `timedelta`.

```python
def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text())
    if not isinstance(document, dict):
        raise DefinitionError(f"{path}: expected a mapping at the top level")
    return document
```

`_load` reads a file with `Path.read_text()` and `yaml.safe_load`, and insists on a mapping at the top level.

```python
def load_slos(path: Path) -> list[Slo]:
    """Read ``slos.yaml``."""
    document = _load(path)
    window = int(document["window_days"])
    return [
        Slo(
            name=item["name"],
            objective=float(item["objective"]),
            window_days=window,
            description=item["description"].strip(),
            good=item["good"].strip(),
            valid=item["valid"].strip(),
        )
        for item in document["slos"]
    ]
```

`load_slos` reads `window_days` once and builds one `Slo` per item in `slos:`. `.strip()` removes the trailing newline that a folded YAML string (`>-`) may leave. Building a `Slo` runs its checks, so a bad objective stops here.

```python
def load_policies(path: Path) -> dict[str, BurnRatePolicy]:
    """Read ``policies.yaml``."""
    policies: dict[str, BurnRatePolicy] = {}
    for name, item in _load(path)["policies"].items():
        rules = tuple(
            BurnRateRule(
                severity=rule["severity"],
                burn_rate=float(rule["burn_rate"]),
                long_window=parse_duration(rule["long_window"]),
                short_window=parse_duration(rule["short_window"]),
            )
            for rule in item["rules"]
        )
        policies[name] = BurnRatePolicy(name, item["description"].strip(), rules)
    return policies
```

`load_policies` reads each named policy and builds a `BurnRateRule` for each rule: severity, burn rate, and the two windows parsed from text like `1h` and `5m`. The result is a dict from policy name to `BurnRatePolicy`.

```python
def load_alert_rules(path: Path) -> list[AlertRule]:
    """Read the alerting rules (not recording rules) from a Prometheus rule file."""
    rules: list[AlertRule] = []
    for group in _load(path)["groups"]:
        for rule in group["rules"]:
            if "alert" in rule:
                rules.append(
                    AlertRule(
                        rule["alert"],
                        rule["expr"].strip(),
                        parse_duration(str(rule.get("for", "0s"))).total_seconds(),
                        rule["labels"]["severity"],
                    )
                )
    return rules
```

`load_alert_rules` reads a Prometheus rule file and returns only the **alerting** rules (items that have an `alert` key; recording rules are skipped). The `for` duration defaults to `0s` when absent. This is how the replay learns the `for: 1m` of `HighCPU`.

```python
GENERATED_HEADER = (
    "# Generated by `make rules` from observability/slo/slos.yaml and observability/slo/policies.yaml.\n"
    "# Do not edit by hand; change the definitions and regenerate. `make lint` fails when this file is stale.\n"
)
```

The header of every generated file, so nobody edits it by hand: it says which command generated it and that `make lint` fails when it is stale. Adjacent string literals in parentheses are joined into one string.

```python
class _RuleDumper(yaml.SafeDumper):
    """Writes multi-line strings (the two-window expressions) as readable literal blocks."""


def _literal_multiline(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_RuleDumper.add_representer(str, _literal_multiline)


def render_rule_file(document: dict[str, Any]) -> str:
    """The YAML text of a generated rule file, with its header."""
    return GENERATED_HEADER + yaml.dump(document, Dumper=_RuleDumper, sort_keys=False, width=10_000)
```

`_RuleDumper` subclasses `yaml.SafeDumper`. `_literal_multiline` is a representer: for a string containing a line break it asks for the style `"|"` (a literal block, one string with its line breaks kept), otherwise the default style. `add_representer(str, ...)` registers it for all strings. The effect: the two-window expressions are written as readable multi-line blocks (`expr: |-`) instead of one line full of `\n`. `render_rule_file` returns the header plus the dumped document.

## Try it yourself

```bash
.venv/bin/python -c "
import yaml
print(yaml.safe_load(open('observability/slo/slos.yaml'))['slos'][0]['objective'])"
```

Expected: `0.995`.

```bash
.venv/bin/python -c "
from slo_lab.infrastructure.definitions_io import render_rule_file
print(render_rule_file({'groups': [{'name': 'g', 'rules': [{'alert': 'A', 'expr': 'a\nand\nb'}]}]}))"
```

Expected: the two header comment lines, then `groups:` and a rule whose `expr: |-` is followed by the three lines `a`, `and`, `b`.

```bash
make rules && git status --short
```

Expected: `wrote observability/prometheus/rules/burn_rate.rules.yml` and `wrote observability/prometheus/replay/burn_rate_lesson.rules.yml`, and then no output from `git status`: regenerating produced exactly the committed files.

```bash
.venv/bin/python -c "
import yaml
print(yaml.safe_load('- just\n- a list'))"
```

Expected: `['just', 'a list']`: a top-level list, which `_load` would reject.

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_infrastructure.py -k "definitions or durations or rendered or mapping" -q
```

Expected: the reader and writer tests pass.

## Mistakes people make

- **`yaml.load` instead of `yaml.safe_load`** on data you do not control: it can construct arbitrary Python objects.
- **Letting the dumper sort keys.** `sort_keys=False` keeps the order the code built.
- **Hand-editing a generated file.** The header says not to; `make lint` detects it.
- **Reading numbers as strings.** `"0.995"` in quotes would be a string; the loader code calls `float(...)` anyway.
- **Forgetting the YAML 1.1 surprises** (`no`, `on`, `off` as booleans). PyYAML implements YAML 1.1; this repository quotes anything ambiguous.

## Check yourself

1. What is the difference between `safe_load` and `load`? *`safe_load` builds only plain data; `load` can build arbitrary objects.*
2. What does a representer do? *Tells the dumper how to write one type of value.*
3. Why `sort_keys=False`? *To keep the order of the rule's keys as built.*
4. Which rules does `load_alert_rules` keep? *Those with an `alert` key; recording rules are skipped.*
5. Why does `make lint` regenerate and compare? *So a hand-edited or stale generated file fails the build.*

## Further reading

- PyYAML documentation: https://pyyaml.org/wiki/PyYAMLDocumentation
- `yaml.safe_load` and dumping: https://pyyaml.org/wiki/PyYAMLDocumentation#dumping-yaml
- YAML 1.1 specification: https://yaml.org/spec/1.1/
