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
