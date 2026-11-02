# Python subprocess (running Docker and promtool from Python)

## What it is

`subprocess` is the standard-library module for starting other programs from Python. `subprocess.run(command, ...)` starts the program named by the first item of the list, waits for it to finish and returns a `CompletedProcess` with the **return code** (the exit code) and, if asked, the captured output. A command is a **list** of strings (`["docker", "compose", "up"]`), not one string, so no shell is involved and nothing needs quoting.

## Why this repository uses it

The scenarios must change the running stack: switch the gateway Collector between a configuration without protection and the protected one, create the replay database from a file with `promtool`, and start the replay Prometheus. The module `lab/compose.py` wraps those few `docker` commands. The command runner is a parameter, so the unit tests use a fake and need no Docker.

## Where it appears

| Path | What it does |
| --- | --- |
| `src/slo_lab/lab/compose.py` | The three operations and the function that runs commands. |
| `src/slo_lab/lab/volume.py` | Calls `use_gateway_config` before each pass (see `python-lab-scenarios.md`). |
| `src/slo_lab/lab/replay.py` | Calls `backfill_replay_data` and `start_replay_prometheus`. |
| `tests/unit/lab/test_lab.py` | Tests them with a fake runner and with a real failing command. |

## How it works underneath

`subprocess.run` asks the operating system to start a new process. The arguments used here:

- `cwd=` sets the working directory of the new process (the folder with `docker-compose.yml`).
- `env=` replaces the new process's environment. Docker Compose reads `${NAME}` placeholders from the environment, so setting `GATEWAY_CONFIG` here changes which Collector configuration the gateway container mounts, with no edit to any file.
- `capture_output=True` collects standard output and standard error; `text=True` gives them as text.
- `check=False` means "do not raise on a non-zero return code"; the code checks `returncode` itself and raises its own error carrying the output.

## Walkthrough of this repository's use

```python
"""Docker control for the scenarios: switch the gateway config, backfill and start the replay Prometheus."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

Runner = Callable[[Sequence[str], Mapping[str, str], Path], None]

PROJECT = "observability-slo-alerts-and-cost"
REPLAY_VOLUME = f"{PROJECT}_replay-data"
PROMETHEUS_IMAGE = "prom/prometheus:v2.54.1"

```

`Runner` is a **type alias**: a name for "a function taking the command (a sequence of strings), the environment and the working directory, and returning nothing". `PROJECT` is the Compose project name, so the replay volume's full name is `observability-slo-alerts-and-cost_replay-data`. `PROMETHEUS_IMAGE` is the pinned Prometheus image used to run `promtool` (it ships in that image).

```python
class ComposeError(RuntimeError):
    """A docker command failed."""


def run_command(command: Sequence[str], environment: Mapping[str, str], working_directory: Path) -> None:
    """Run a command, raising ``ComposeError`` with its output when it fails."""
    completed = subprocess.run(
        list(command), cwd=working_directory, env=dict(environment), capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise ComposeError(
            f"{' '.join(command)} failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
        )
```

`ComposeError` is this module's error. `run_command` calls `subprocess.run` with the arguments above and, when the return code is not 0, raises `ComposeError` with the command line, the code and both output streams, so a failure in a long scenario explains itself.

```python
class Compose:
    """The few docker operations the scenarios need; the runner is injectable so tests need no Docker."""

    def __init__(
        self, project_directory: Path, base_environment: Mapping[str, str], runner: Runner = run_command
    ) -> None:
        """Take the folder holding docker-compose.yml, the environment to start from and a command runner."""
        self._directory = project_directory
        self._base = dict(base_environment)
        self._runner = runner

    def _run(self, command: Sequence[str], overrides: Mapping[str, str] | None = None) -> None:
        self._runner(command, {**self._base, **(overrides or {})}, self._directory)
```

`Compose` stores the project folder, a copy of the base environment and the runner (default: the real one). `_run` merges the base environment with optional per-call overrides using `{**base, **(overrides or {})}` (later keys win; `or {}` handles `None`).

```python
    def use_gateway_config(self, config_path: str) -> None:
        """Recreate the gateway Collector with the given config file and wait until it is running."""
        self._run(
            ["docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "--wait", "otel-gateway"],
            {"GATEWAY_CONFIG": config_path},
        )
```

`use_gateway_config(config_path)` runs `docker compose up -d --force-recreate --no-deps --wait otel-gateway` with `GATEWAY_CONFIG` set to the chosen file: recreate only the gateway container so it mounts the other configuration (`-d` in the background, `--force-recreate` even if nothing else changed, `--no-deps` leave other services alone, `--wait` block until it is running).

```python
    def backfill_replay_data(self, openmetrics_file: Path) -> None:
        """Replace the replay volume with blocks created from ``openmetrics_file`` by promtool."""
        self._run(["docker", "compose", "--profile", "replay", "rm", "-sf", "prometheus-replay"])
        self._run(["docker", "volume", "rm", "-f", REPLAY_VOLUME])
        self._run(["docker", "volume", "create", REPLAY_VOLUME])
        self._run(
            [
                "docker", "run", "--rm", "--user", "root", "--entrypoint", "promtool",
                "-v", f"{REPLAY_VOLUME}:/prometheus",
                "-v", f"{openmetrics_file.resolve()}:/input/week.om:ro",
                PROMETHEUS_IMAGE,
                "tsdb", "create-blocks-from", "openmetrics", "--max-block-duration=24h",
                "/input/week.om", "/prometheus",
            ]
        )  # fmt: skip
        self._run(
            [
                "docker", "run", "--rm", "--user", "root", "--entrypoint", "chown",
                "-v", f"{REPLAY_VOLUME}:/prometheus", PROMETHEUS_IMAGE, "-R", "nobody:nobody", "/prometheus",
            ]
        )  # fmt: skip
```

`backfill_replay_data` (explained step by step in `openmetrics.md`): remove the old replay container and volume, create the volume, run `promtool tsdb create-blocks-from openmetrics` as root in a one-off container, and `chown` the volume to `nobody`.

```python
    def start_replay_prometheus(self) -> None:
        """Start the Prometheus that serves the backfilled week."""
        self._run(["docker", "compose", "--profile", "replay", "up", "-d", "--wait", "prometheus-replay"])
```

`start_replay_prometheus` runs `docker compose --profile replay up -d --wait prometheus-replay`. The profile flag is what makes Compose consider the replay service at all.

## Try it yourself

```bash
.venv/bin/python -c "
from slo_lab.lab.compose import run_command
from pathlib import Path
run_command(['sh', '-c', 'echo boom >&2; exit 3'], {}, Path('.'))" 2>&1 | tail -4
```

Expected: a `ComposeError` whose message contains `failed (3)` and `boom`.

```bash
.venv/bin/python -c "
from pathlib import Path
from slo_lab.lab.compose import Compose
Compose(Path('.'), {}, runner=lambda c, e, d: print(' '.join(c), e.get('GATEWAY_CONFIG'))).use_gateway_config('./observability/collector/gateway.raw.yaml')"
```

Expected: the command `docker compose up -d --force-recreate --no-deps --wait otel-gateway` and the chosen file, printed and not run.

```bash
.venv/bin/python -m pytest tests/unit/lab/test_lab.py -k compose -q
```

Expected: `1 passed`.

```bash
GATEWAY_CONFIG=./observability/collector/gateway.raw.yaml docker compose config | grep -B1 gateway.raw
```

Expected: the resolved Compose file mounts the raw configuration for the gateway.

```bash
docker volume ls | grep replay-data
```

Expected (after `make replay`): `observability-slo-alerts-and-cost_replay-data`.

## Mistakes people make

- **One string with `shell=True`.** Quoting errors and injection risks.
- **Not checking the return code.** The error here carries both output streams.
- **Replacing the environment by accident.** `env=` replaces everything, so the base environment is copied in first.
- **Forgetting `--wait`.** The next step would start before the container is ready.
- **Running `promtool` as the image's default user.** It could not write the output; hence `--user root` and the `chown`.

## Check yourself

1. Why is a command a list and not a string? *No shell is involved, so no quoting or injection problems.*
2. What does `--force-recreate` do? *Replaces the container even if its configuration did not change.*
3. How does the lab switch the gateway's configuration without editing a file? *It sets `GATEWAY_CONFIG` for `docker compose up`, which the Compose file reads with `${GATEWAY_CONFIG:-...}`.*
4. Why is the runner injected? *So tests can fake it and need no Docker.*
5. What does the Compose profile `replay` do? *The replay service starts only when that profile is requested.*

## Further reading

- `subprocess`: https://docs.python.org/3.12/library/subprocess.html
- `docker compose up`: https://docs.docker.com/reference/cli/docker/compose/up/
- `docker volume`: https://docs.docker.com/reference/cli/docker/volume/
