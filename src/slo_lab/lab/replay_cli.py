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
    comparison = report.compare("threshold", "burn_rate")
    print(
        f"\nthreshold -> burn_rate: {comparison.before.total} -> {comparison.after.total} notifications, "
        f"actionable {comparison.before.actionable_share:.1%} -> {comparison.after.actionable_share:.1%}, "
        f"every user-facing incident still alerted: {comparison.nothing_real_missed}"
    )
    return 0 if comparison.improved else 1


if __name__ == "__main__":
    sys.exit(main())
