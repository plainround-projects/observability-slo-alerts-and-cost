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
