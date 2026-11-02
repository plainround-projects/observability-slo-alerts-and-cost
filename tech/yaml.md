# YAML

## What it is

YAML is a text format for configuration data. It describes the same things as JSON (key/value maps, lists, strings, numbers, true/false, null) but uses **indentation** and dashes instead of braces and brackets, and allows `#` comments. Every Compose file, Prometheus configuration, Collector configuration, Grafana provisioning file, alert rule file and GitHub workflow in this repository is YAML.

## Why this repository uses it

YAML is both what the lab is configured with and what the lab *produces*. Compose, the Collector, Tempo, Loki, Prometheus, Alertmanager, Grafana and the CI workflow are YAML files; the SLO definitions and policies are YAML; the burn-rate alert rules are **generated** YAML; and the promtool tests are YAML with a little language of their own.

## Where it appears

| Path | What it configures |
| --- | --- |
| `docker-compose.yml` | The containers. |
| `observability/slo/slos.yaml`, `policies.yaml` | The SLOs and burn-rate policies (data the generator reads). |
| `observability/prometheus/rules/burn_rate.rules.yml`, `replay/*.rules.yml` | Alert rules (generated, and the hand-written threshold set). |
| `observability/prometheus/prometheus.yml`, `replay/prometheus.yml`, `tests/burn_rate.rules.test.yml` | Prometheus and the promtool tests. |
| `observability/alertmanager/alertmanager.yml` | Alertmanager. |
| `observability/collector/gateway.yaml`, `gateway.raw.yaml` | The two gateways. |
| `observability/tempo/tempo.yaml`, `observability/loki/loki.yaml` | The stores. |
| `observability/grafana/provisioning/**/*.yaml` | Grafana data sources and dashboard loader. |
| `.github/workflows/ci.yml` | CI. |
| `src/slo_lab/infrastructure/definitions_io.py` | Reads and writes YAML (see `pyyaml.md`). |

## How it works underneath

- A **mapping** is a set of `key: value` lines at the same indentation. Indentation must use spaces, never tabs.
- A **sequence** (list) is a series of lines that start with `- `. A list item that is a mapping puts its first key on the dash line and the next keys aligned under it.
- A **flow** style is allowed for short values: `[a, b]` for a list and `{k: v}` for a mapping.
- Scalars (single values) can be unquoted (`22`, `true`, `ticket`), or quoted. A value like `"5000"` in quotes is a string, while `5000` is a number. Unquoted `yes`, `no`, `on`, `off` can be read as booleans by some parsers; quote them when you mean text.
- A multi-line string uses `|` (keep line breaks) or `>` (join lines). Prometheus alert expressions use `|`.
- **Anchors and merge keys.** `&name` marks a block so it can be reused; `*name` copies it; `<<: *name` merges the copied mapping into the current one. Compose files use it to share environment variables.
- A file can hold several documents separated by `---`. Not used here.

The parser turns the text into the same structures a program would build in memory (maps and lists), and the program then reads keys from them. The `tests/observability` tests use PyYAML's `safe_load` to do exactly that.

## Walkthrough of this repository's use

**Folded text with `>-`.** In `slos.yaml` a long description is written over several lines:

```yaml
    description: >-
      A valid request is any request to a route other than /healthz (health checks always succeed, so counting them
      would hide real errors). A good request is a valid one that did not end in a 5xx.
```

`>-` means: a "folded" scalar: the lines are joined with spaces into one string, and the `-` removes the final line break. The loader strips whitespace anyway.

**Flow maps in a list.** In `policies.yaml` each rule is a one-line map:

```yaml
    rules:
      - { severity: page, burn_rate: 14.4, long_window: 1h, short_window: 5m }
      - { severity: ticket, burn_rate: 5, long_window: 2h, short_window: 15m }
```

`{ severity: page, burn_rate: 14.4, long_window: 1h, short_window: 5m }` is a mapping written inline; `1h` and `5m` are plain strings (parsed by `parse_duration`), `14.4` and `5` are numbers.

**Literal blocks with `|-`.** The generated rules write each two-window expression as a block:

```yaml
  - alert: AvailabilityBudgetFastBurn
    expr: |-
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[1h]))) / (sum(rate(http_requests_total{route!="/healthz"}[1h])))) > 0.072
      and
      (1 - (sum(rate(http_requests_total{route!="/healthz",status!~"5.."}[5m]))) / (sum(rate(http_requests_total{route!="/healthz"}[5m])))) > 0.072
    labels:
      severity: page
      slo: availability
    annotations:
      summary: 'Availability error budget burning at 14.4x: 2.0% of the 30-day budget in 1h.'
      runbook: Check the availability SLI panel, open an error or slow trace from the last 5m, and roll back or fix the cause.
```

`expr: |-` starts a literal block: every following more-indented line is part of the string, with its line breaks kept (`|`) and the final line break removed (`-`). The `summary` is in single quotes because it contains a colon followed by a space. The `-` before `alert` starts a list item; its keys line up under it.

**Lists of maps and nested lists in Alertmanager.**

```yaml
# While the fast-burn page for an SLO is firing, its slow-burn ticket adds nothing.
inhibit_rules:
  - source_matchers: [severity="page"]
    target_matchers: [severity="ticket"]
    equal: [slo]
```

`source_matchers: [severity="page"]` is a list on one line whose single item is the text `severity="page"` (a matcher in Alertmanager's own syntax).

**A string with its own quotes (promtool series).**

```yaml
    input_series:
      - series: 'http_requests_total{route="/checkout",status="200"}'
        values: "0+500x70 35000x40"
      - series: 'http_requests_total{route="/checkout",status="500"}'
        values: "0x70 0+500x40"
      - series: 'http_requests_total{route="/healthz",status="200"}'
        values: "0+4x400"
```

Single quotes outside, double quotes inside; `values: "0+500x70 35000x40"` is text in promtool's mini-language.

**Anchors are not used** in this repository (an anchor, `&name`, names a block so `<<: *name` can copy it elsewhere); every service lists its own variables.

**Reading it from code.** The configuration tests and the definitions reader use `yaml.safe_load`:

```python
def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())
```

## Try it yourself

```bash
.venv/bin/python -c "import yaml; print(yaml.safe_load(open('observability/slo/policies.yaml'))['policies']['default']['rules'][0])"
```

Expected: `{'severity': 'page', 'burn_rate': 14.4, 'long_window': '1h', 'short_window': '5m'}`.

```bash
.venv/bin/python -c "import yaml; print(yaml.safe_load(open('observability/slo/slos.yaml'))['slos'][0]['description'])"
```

Expected: the description as one line (the folded lines joined by spaces).

```bash
docker compose config | sed -n 1,12p
```

Expected: the Compose file with variables substituted.

```bash
.venv/bin/python -c "import yaml; print(yaml.safe_load('a: \"5000\"\nb: 5000'))"
```

Expected: `{'a': '5000', 'b': 5000}`.

```bash
.venv/bin/python -m pytest tests/observability -q
```

Expected: all pass.

## Mistakes people make

- **Tabs.** YAML forbids them for indentation.
- **Wrong indentation level** quietly moves a key to a different parent.
- **Unquoted special characters.** A value containing `: ` or starting with `{`, `*` or `&` must be quoted.
- **Numbers that should be strings.** A port mapping like `"127.0.0.1:23000:3000"` must be quoted, or some parsers read `22:22` as a sexagesimal number.
- **Duplicate keys**, where the last one silently wins.

## Check yourself

1. How does YAML show that one key is inside another? *By indenting it further with spaces.*
2. What is the difference between `5000` and `"5000"`? *The first is a number; the second is a string.*
3. What does `<<: *service-environment` do? *It copies the anchored mapping's keys into this mapping.*
4. Can you indent YAML with tabs? *No.*
5. What does `|` after a key mean? *The following indented lines are one multi-line string with line breaks kept.*

## Further reading

- YAML 1.2 specification: https://yaml.org/spec/1.2.2/
- PyYAML documentation: https://pyyaml.org/wiki/PyYAMLDocumentation
