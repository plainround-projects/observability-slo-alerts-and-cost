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
