# argparse and urllib (standard library command-line tools)

## What it is

Two standard-library modules that the command-line tools of this repository are built on.

- `argparse` reads the **command-line arguments** a program was started with (for example `--requests 6000`), converts them to the right types, supplies defaults, and prints a help text for `--help`. A **flag** such as `--check` with `action="store_true"` is `True` when present and `False` otherwise.
- `urllib.request` is a small HTTP client that comes with Python. `urlopen(url, timeout=5)` makes a GET request and returns a response you can use in a `with` block; `Request(url)` builds a request on which you can add headers first.

A program's **exit code** is the number it returns to the operating system: `0` for success, anything else for failure. `sys.exit(main())` passes the return value of `main()` to the system, so `make` and Docker can see success or failure.

## Why this repository uses it

The lab is a set of small commands, each behind a `make` target: `slo-rules` (generate rules, or check they are current), `slo-replay` (replay the week), `slo-analyze` (judge a pager log), `slo-volume` (compare the gateway's policies), `slo-live` (a real outage), `slo-health` (the container health probe) and the stack health check. Their exit codes are the verdicts: `make lint` fails when generated rules are stale; `make replay` fails when the burn-rate set is not an improvement; `make live` fails when no page arrives.

## Where it appears

| Path | What it is |
| --- | --- |
| `src/slo_lab/interfaces/health_cli.py` | `slo-health`: probes one URL; the Docker health check of the service and the pager. |
| `src/slo_lab/lab/health_cli.py` | The stack check run by `make health`. |
| `src/slo_lab/lab/rules_cli.py` | `slo-rules`. |
| `src/slo_lab/lab/replay_cli.py` | `slo-replay`. |
| `src/slo_lab/lab/analyze_cli.py` | `slo-analyze`. |
| `src/slo_lab/lab/volume_cli.py` | `slo-volume`. |
| `src/slo_lab/lab/live_cli.py` | `slo-live`. |
| `pyproject.toml` | Maps each command name to its `main` function (see `pyproject-toml.md`). |

## How it works underneath

At start-up the operating system hands the program a list of strings (`sys.argv`). `argparse.ArgumentParser()` builds a parser; `add_argument("--requests", type=int, default=6000)` declares one option; `parse_args(argv)` reads the list, converts each value with `type`, and returns an object whose attributes are the values (`args.requests`). A positional argument (`add_argument("pager_log", type=Path)`) has no dashes and is required. `parse_args(None)` reads `sys.argv`; a test passes an explicit list. On an error or `--help` it prints a message and exits.

`urlopen` opens a TCP connection, sends `GET <path> HTTP/1.1` and waits up to `timeout` seconds for each step. A non-success status raises `urllib.error.HTTPError` (a subclass of `URLError`). A refused or timed-out connection raises `URLError` or `TimeoutError`; both derive from `OSError`. **HTTP basic authentication** sends `Authorization: Basic <token>` where the token is `user:password` in base64 (an encoding, not encryption).

## Walkthrough of this repository's use

### `interfaces/health_cli.py`: the container health probe

```python
"""``slo-health``: exit 0 when a service's /healthz answers 200; used by the Docker health checks."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request


def main(argv: list[str] | None = None) -> int:
    """Probe the URL and return the process exit code."""
    parser = argparse.ArgumentParser(description="Probe a service's health endpoint.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        with urllib.request.urlopen(args.url, timeout=args.timeout) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, TimeoutError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

All imports are standard library, so the probe adds nothing to the image. `main(argv=None) -> int` returns the exit code (testable). `--url` is required; `--timeout` is a float, default 5.0. `return 0 if response.status == 200 else 1`: only a 200 answer is healthy; any network error is exit code 1 with no traceback. The last two lines run `main()` only when the file is executed as a program. Compose's health check for both the service and the pager is `slo-health --url http://localhost:<port>/healthz --timeout 10` (see `docker-compose.md`).

### `lab/health_cli.py`: the stack check

```python
"""``python -m slo_lab.lab.health_cli``: the service, the gateway, the backends, the pager and Grafana all answer."""

from __future__ import annotations

import base64
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping


def _http_ok(url: str, auth: tuple[str, str] | None = None) -> bool:
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False

```

`_http_ok(url, auth=None)` builds a request; with credentials it adds the basic-authentication header (`base64.b64encode(f"{user}:{password}".encode()).decode()`: encode the text to bytes, base64 it, decode back to text). Any network error is `False`.

```python
def run_checks(env: Mapping[str, str]) -> list[tuple[str, bool]]:
    """One (name, answered) pair per part of the stack, using the ports the compose file publishes."""
    local = "http://127.0.0.1:"
    grafana = f"{local}{env.get('GRAFANA_HOST_PORT', '23000')}"
    login = ("admin", env.get("GRAFANA_ADMIN_PASSWORD", ""))
    return [
        ("checkout /healthz", _http_ok(f"{local}{env.get('SERVICE_HOST_PORT', '28083')}/healthz")),
        ("gateway health_check", _http_ok(f"{local}{env.get('GATEWAY_HEALTH_HOST_PORT', '23133')}/")),
        ("tempo /ready", _http_ok(f"{local}{env.get('TEMPO_HOST_PORT', '23200')}/ready")),
        ("loki /ready", _http_ok(f"{local}{env.get('LOKI_HOST_PORT', '23100')}/ready")),
        ("prometheus /-/healthy", _http_ok(f"{local}{env.get('PROMETHEUS_HOST_PORT', '29090')}/-/healthy")),
        ("pager /healthz", _http_ok(f"{local}{env.get('PAGER_HOST_PORT', '28084')}/healthz")),
        ("grafana queries prometheus", _http_ok(f"{grafana}/api/datasources/uid/prometheus/health", login)),
        ("grafana queries loki", _http_ok(f"{grafana}/api/datasources/uid/loki/health", login)),
        # Grafana 11.2 has no backend health check for Tempo; query it through the proxy, as "Save & test" does.
        ("grafana queries tempo", _http_ok(f"{grafana}/api/datasources/proxy/uid/tempo/api/search?limit=1", login)),
    ]
```

`run_checks(env)` returns a list of `(name, answered)` pairs using the ports the Compose file publishes, read from the environment with defaults (28083 service, 23133 gateway health, 23200 Tempo, 23100 Loki, 29090 Prometheus, 28084 pager, 23000 Grafana). The first six are plain `GET`s; the last three ask Grafana, with the admin login, to query its data sources. The comment explains the Tempo case: Grafana 11.2 has no backend health check for Tempo, so a real search is sent through Grafana's proxy path, as the "Save & test" button does.

```python
def main() -> int:
    """Print one line per check and exit 1 when any fails."""
    checks = run_checks(os.environ)
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
```

`main` prints `ok   name` or `FAIL name` for each and returns 0 only if `all(...)` are true. `make up` ends with it.

### `lab/rules_cli.py`: generate or check

```python
"""``slo-rules``: generate the burn-rate rule files from the SLO and policy definitions, or check they are current."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slo_lab.application.rule_builder import rule_file_document
from slo_lab.infrastructure.definitions_io import load_policies, load_slos, render_rule_file

# policy name -> generated file, relative to the repository root.
TARGETS = {
    "default": "observability/prometheus/rules/burn_rate.rules.yml",
    "lesson": "observability/prometheus/replay/burn_rate_lesson.rules.yml",
}
```

`TARGETS` maps a policy name to the file it generates: `default` goes to the live rules, `lesson` to the replay folder.

```python
def main(argv: list[str] | None = None) -> int:
    """Write the files, or with ``--check`` exit 1 when any differs from what the definitions generate."""
    parser = argparse.ArgumentParser(description="Generate burn-rate rule files from the SLO definitions.")
    parser.add_argument("--check", action="store_true", help="fail if a generated file is stale instead of writing")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    slos = load_slos(args.root / "observability/slo/slos.yaml")
    policies = load_policies(args.root / "observability/slo/policies.yaml")
    stale = False
    for policy_name, relative in TARGETS.items():
        text = render_rule_file(rule_file_document(slos, policies[policy_name]))
        path = args.root / relative
        if args.check:
            current = path.read_text() if path.exists() else ""
            if current != text:
                print(f"stale: {relative} (run `make rules`)")
                stale = True
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(f"wrote {relative}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
```

`--check` is a flag; `--root` is the repository folder (default: the current directory). It loads the SLOs and policies, renders each target file's text, and either compares it with what is on disk (`--check`: print `stale: <file> (run make rules)` and return 1 for any difference) or writes it (`mkdir(parents=True, exist_ok=True)` then `write_text`). `make rules` writes; `make lint` checks.

### `lab/replay_cli.py`: replay the week

```python
"""``slo-replay``: replay the simulated week through the threshold alerts and both burn-rate policies."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from slo_lab.infrastructure.prometheus_http import PrometheusRangeEvaluator
from slo_lab.lab.compose import Compose
from slo_lab.lab.replay import DAYS, WeekReplay, load_alert_sets, render_report

RULE_FILES = {
    "threshold": "observability/prometheus/replay/threshold.rules.yml",
    "burn_rate_lesson": "observability/prometheus/replay/burn_rate_lesson.rules.yml",
    "burn_rate": "observability/prometheus/rules/burn_rate.rules.yml",
}
```

`RULE_FILES` names the three alert sets: the threshold alerts, the lesson's burn-rate numbers and this repository's burn-rate rules.

```python
def main(argv: list[str] | None = None) -> int:
    """Run the replay and print the comparison; exit 1 when the burn-rate set missed a user-facing incident."""
    parser = argparse.ArgumentParser(description="Replay a simulated week through alert rule sets.")
    parser.add_argument("--work", type=Path, default=Path("var"))
    parser.add_argument(
        "--reuse", action="store_true", help="evaluate against the replay Prometheus that is already running"
    )
    args = parser.parse_args(argv)
    root = Path.cwd()
    env = os.environ
    start_epoch = int(time.time()) // 3600 * 3600 - DAYS * 86400
    replay = WeekReplay(
        Compose(root, env),
        PrometheusRangeEvaluator(f"http://127.0.0.1:{env.get('REPLAY_HOST_PORT', '29091')}"),
        args.work,
    )
    if args.reuse:
        start_epoch = replay.existing_start_epoch()
    else:
        replay.prepare(start_epoch)
    alert_sets = load_alert_sets(root, RULE_FILES)
    report = replay.evaluate(alert_sets, start_epoch)
    print(render_report(report))
```

`--work` is the folder for generated files (default `var`). `--reuse` is a flag: evaluate against the replay Prometheus that is already running instead of simulating and backfilling again. The week starts at the last whole hour minus seven days (`int(time.time()) // 3600 * 3600 - DAYS * 86400`; `//` is integer division). With `--reuse` it reads the earlier start time from the work folder. Then it loads the alert sets, evaluates them and prints the report.

```python
    comparison = report.compare("threshold", "burn_rate")
    print(
        f"\nthreshold -> burn_rate: {comparison.before.total} -> {comparison.after.total} notifications, "
        f"actionable {comparison.before.actionable_share:.1%} -> {comparison.after.actionable_share:.1%}, "
        f"every user-facing incident still alerted: {comparison.nothing_real_missed}"
    )
    return 0 if comparison.improved else 1


if __name__ == "__main__":
    sys.exit(main())
```

It compares the threshold set with the burn-rate set, prints one summary line (notifications before and after, actionable shares, whether every user-facing incident still alerted) and returns 0 only when the comparison says the second set **improved**: quieter, more actionable and nothing real missed.

### `lab/analyze_cli.py`, `volume_cli.py`, `live_cli.py`

```python
"""``slo-analyze``: classify a pager log against the planted incidents."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slo_lab.domain.pages import classify
from slo_lab.domain.traffic import week_incidents
from slo_lab.lab.replay import ReplayReport, pages_from_lines, render_report


def main(argv: list[str] | None = None) -> int:
    """Print the notification count, the actionable share and the incident coverage of a pager log."""
    parser = argparse.ArgumentParser(description="Classify a pager log against the planted incidents.")
    parser.add_argument("pager_log", type=Path)
    parser.add_argument("--name", default=None)
    args = parser.parse_args(argv)
    pages = pages_from_lines(args.pager_log.read_text())
    report = classify(pages, week_incidents())
    print(render_report(ReplayReport({args.name or args.pager_log.stem: report}, {}, 500.0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`slo-analyze <file>` classifies a pager log (JSON Lines, one page per line) against the planted incidents. The positional `pager_log` is a `Path`; `--name` labels the report (default: the file's name without extension, `Path.stem`). It prints the notification count, the actionable share and the coverage.

```python
"""``slo-volume``: send the same traffic through a raw and a protected gateway and compare what the stores hold."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from slo_lab.lab.compose import Compose
from slo_lab.lab.stores import LokiClient, TempoClient
from slo_lab.lab.volume import VolumeScenario, render_report


def _gateway_ready(port: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    """Run both passes and print the comparison."""
    parser = argparse.ArgumentParser(
        description="Compare stored volume and personal data with and without the gateway's policies."
    )
    parser.add_argument("--requests", type=int, default=6000)
    args = parser.parse_args(argv)
    env = os.environ
    scenario = VolumeScenario(
        Compose(Path.cwd(), env),
        TempoClient(f"http://127.0.0.1:{env.get('TEMPO_HOST_PORT', '23200')}"),
        LokiClient(f"http://127.0.0.1:{env.get('LOKI_HOST_PORT', '23100')}"),
        f"http://127.0.0.1:{env.get('SERVICE_HOST_PORT', '28083')}",
        args.requests,
        gateway_ready=lambda: _gateway_ready(env.get("GATEWAY_HEALTH_HOST_PORT", "23133")),
    )
    print(render_report(scenario.run()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`slo-volume --requests 6000` runs the volume and redaction comparison. `_gateway_ready(port)` polls the Collector's `health_check` extension (which answers 200 on `/`); the scenario waits for it after switching the gateway's configuration because the image has no shell and Compose cannot health-check it. The clients get their ports from the environment; `gateway_ready=lambda: ...` passes a function.

```python
"""``slo-live``: a real outage against the running stack, and the fast-burn page arriving at the fake pager."""

from __future__ import annotations

import argparse
import os
import sys

from slo_lab.lab.live import run_live_outage
from slo_lab.lab.stores import PagerClient


def main(argv: list[str] | None = None) -> int:
    """Run the outage and print how long the page took; exit 1 when no page arrived."""
    parser = argparse.ArgumentParser(description="Fail every request for a while and wait for the fast-burn page.")
    parser.add_argument("--warm", type=float, default=120.0, help="seconds of healthy traffic first")
    parser.add_argument("--outage", type=float, default=180.0, help="seconds of 100% errors")
    args = parser.parse_args(argv)
    env = os.environ
    outcome = run_live_outage(
        PagerClient(f"http://127.0.0.1:{env.get('PAGER_HOST_PORT', '28084')}"),
        f"http://127.0.0.1:{env.get('SERVICE_HOST_PORT', '28083')}",
        warm_seconds=args.warm,
        outage_seconds=args.outage,
    )
    if outcome.page is None:
        print("no page arrived")
        return 1
    print(
        f"page '{outcome.page['alert']}' ({outcome.page['severity']}) arrived "
        f"{outcome.seconds_to_page:.0f} s after the outage began"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`slo-live` takes `--warm` (seconds of healthy traffic first, default 120) and `--outage` (seconds of 100 percent errors, default 180). It runs the live outage, prints how long the page took and returns 0, or prints `no page arrived` and returns 1.

## Try it yourself

```bash
.venv/bin/slo-replay --help
```

Expected: usage with `--work` and `--reuse`.

```bash
.venv/bin/slo-rules --check; echo "exit $?"
```

Expected: `exit 0`.

```bash
echo "# stale" >> observability/prometheus/rules/burn_rate.rules.yml; .venv/bin/slo-rules --check; echo "exit $?"; git checkout observability/prometheus/rules/burn_rate.rules.yml
```

Expected: `stale: observability/prometheus/rules/burn_rate.rules.yml (run \`make rules\`)` and `exit 1`; the last command restores the file.

```bash
.venv/bin/slo-health --url http://127.0.0.1:9/healthz --timeout 1; echo "exit $?"
```

Expected: `exit 1`.

```bash
make health
```

Expected (after `make up`): nine lines starting with `ok`.

## Mistakes people make

- **Calling `sys.exit` deep inside a function.** Only the last two lines of each file exit.
- **Ignoring exit codes in a script.** `make` stops on a non-zero code; that is the point of the verdicts here.
- **No timeout on `urlopen`.** It would wait forever; every call sets one.
- **Forgetting `store_true`** for a flag, which would require a value.
- **Sending credentials over a network you do not control.** Basic authentication is readable; here it goes to `127.0.0.1` only.

## Check yourself

1. What does `action="store_true"` do? *Makes the option a flag: true when present, false otherwise.*
2. What exit code means failure? *Any non-zero code.*
3. What is `--reuse` for? *To evaluate against the replay Prometheus that is already running.*
4. Why does `slo-volume` poll the gateway's health endpoint? *The Collector image has no shell, so Compose cannot health-check it.*
5. What makes `slo-replay` return 1? *The burn-rate set is not an improvement over the threshold set.*

## Further reading

- `argparse`: https://docs.python.org/3.12/library/argparse.html
- `urllib.request`: https://docs.python.org/3.12/library/urllib.request.html
- `pathlib`: https://docs.python.org/3.12/library/pathlib.html
- `base64`: https://docs.python.org/3.12/library/base64.html
