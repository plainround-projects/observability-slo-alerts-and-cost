"""Write the simulated week as an OpenMetrics file that promtool can backfill into a TSDB."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TextIO

from slo_lab.domain.traffic import Tick

# Cumulative upper bounds (seconds) of the latency histogram, matching the live service's buckets.
BUCKETS = ("0.01", "0.05", "0.1", "0.3", "1", "3", "+Inf")
CHECKOUT = "/checkout"
HEALTH = "/healthz"


def write_week(ticks: Iterable[Tick], start_epoch: int, out: TextIO) -> int:
    """Write counters, the latency histogram and the CPU gauge for every tick; return the number of ticks."""
    out.write("# TYPE http_requests counter\n# HELP http_requests HTTP requests handled.\n")
    out.write("# TYPE http_request_duration_seconds histogram\n# UNIT http_request_duration_seconds seconds\n")
    out.write("# HELP http_request_duration_seconds HTTP request duration.\n")
    out.write("# TYPE cpu_usage_percent gauge\n# HELP cpu_usage_percent Process CPU use.\n")
    ok_total = error_total = health_total = fast_total = count_total = 0
    # Histogram state: requests at or below each bound (all requests are at or below +Inf).
    below = dict.fromkeys(BUCKETS, 0)
    duration_sum = 0.0
    lines: list[str] = []
    count = 0
    for tick in ticks:
        count += 1
        timestamp = start_epoch + int(tick.offset.total_seconds())
        ok_total += tick.ok_requests
        error_total += tick.error_requests
        health_total += tick.health_checks
        fast = tick.requests - tick.slow_requests
        fast_total += fast
        count_total += tick.requests
        for bound in BUCKETS[:3]:
            below[bound] += int(fast * _share_below(bound))
        below["0.3"] += fast
        below["1"] += fast + tick.slow_requests // 2
        below["3"] += tick.requests
        below["+Inf"] += tick.requests
        duration_sum += fast * 0.08 + tick.slow_requests * 0.9
        lines.append(f'http_requests_total{{route="{CHECKOUT}",status="200"}} {ok_total} {timestamp}')
        lines.append(f'http_requests_total{{route="{CHECKOUT}",status="500"}} {error_total} {timestamp}')
        lines.append(f'http_requests_total{{route="{HEALTH}",status="200"}} {health_total} {timestamp}')
        for bound in BUCKETS:
            lines.append(
                f'http_request_duration_seconds_bucket{{route="{CHECKOUT}",le="{bound}"}} {below[bound]} {timestamp}'
            )
        lines.append(f'http_request_duration_seconds_count{{route="{CHECKOUT}"}} {count_total} {timestamp}')
        lines.append(f'http_request_duration_seconds_sum{{route="{CHECKOUT}"}} {duration_sum:.3f} {timestamp}')
        lines.append(f"cpu_usage_percent {tick.cpu_percent:.2f} {timestamp}")
        if len(lines) >= 20_000:
            out.write("\n".join(lines) + "\n")
            lines.clear()
    out.write("\n".join(lines) + "\n# EOF\n")
    return count


def _share_below(bound: str) -> float:
    """Of the fast requests (at most 0.3 s), the share that finished within ``bound`` seconds."""
    return {"0.01": 0.05, "0.05": 0.45, "0.1": 0.8}[bound]
