"""Docker control for the scenarios: switch the gateway config, backfill and start the replay Prometheus."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

Runner = Callable[[Sequence[str], Mapping[str, str], Path], None]

PROJECT = "observability-slo-alerts-and-cost"
REPLAY_VOLUME = f"{PROJECT}_replay-data"
PROMETHEUS_IMAGE = "prom/prometheus:v2.54.1"


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

    def use_gateway_config(self, config_path: str) -> None:
        """Recreate the gateway Collector with the given config file and wait until it is running."""
        self._run(
            ["docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "--wait", "otel-gateway"],
            {"GATEWAY_CONFIG": config_path},
        )

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

    def start_replay_prometheus(self) -> None:
        """Start the Prometheus that serves the backfilled week."""
        self._run(["docker", "compose", "--profile", "replay", "up", "-d", "--wait", "prometheus-replay"])
