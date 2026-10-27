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
