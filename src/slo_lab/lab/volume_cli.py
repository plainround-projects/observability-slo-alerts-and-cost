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
        TempoClient(f"http://127.0.0.1:{env.get('TEMPO_HOST_PORT', '3200')}"),
        LokiClient(f"http://127.0.0.1:{env.get('LOKI_HOST_PORT', '3100')}"),
        f"http://127.0.0.1:{env.get('SERVICE_HOST_PORT', '28083')}",
        args.requests,
        gateway_ready=lambda: _gateway_ready(env.get("GATEWAY_HEALTH_HOST_PORT", "13133")),
    )
    print(render_report(scenario.run()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
