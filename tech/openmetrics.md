# OpenMetrics and promtool backfill

## What it is

**OpenMetrics** is a text format for metrics: one line per sample, `name{label="value"} number timestamp`, preceded by `# TYPE`, `# UNIT` and `# HELP` lines that describe each metric, and ending with a line `# EOF`. It is the standardized descendant of the Prometheus text format; Prometheus can scrape it, and `promtool` can read a file in this format.

**Backfill** means loading historical data into a time-series database. Prometheus normally stores only what it scraped itself, in real time. `promtool tsdb create-blocks-from openmetrics <input file> <output folder>` reads an OpenMetrics file whose samples carry **timestamps in the past** and writes **TSDB blocks**: the on-disk files of Prometheus's database. A Prometheus started on that folder can then answer queries about the past as if it had been running all along.

## Why this repository uses it

The lesson asks you to compare alerting strategies on a week of traffic. Waiting a week is not an option, and a real week would not contain the planted incidents. So the repository simulates the week with a seeded generator, writes it as an OpenMetrics file, backfills it into a second Prometheus (the "replay" instance), and evaluates the alert expressions against it with real PromQL at every minute of the week.

## Where it appears

| Path | What it does |
| --- | --- |
| `src/slo_lab/infrastructure/openmetrics.py` | Writes the simulated week as an OpenMetrics file. |
| `src/slo_lab/lab/compose.py` | Runs `promtool tsdb create-blocks-from openmetrics` in a container and fixes the volume's ownership. |
| `src/slo_lab/lab/replay.py` | Orchestrates: simulate, write, backfill, start, evaluate. |
| `docker-compose.yml` (service `prometheus-replay`, profile `replay`) | The Prometheus that serves the backfilled week. |
| `observability/prometheus/replay/prometheus.yml` | Its configuration: scrapes nothing, loads no rules. |
| `tests/unit/infrastructure/test_infrastructure.py` | Tests the writer. |

## How it works underneath

OpenMetrics rules the writer must follow: a **counter** sample is named `<name>_total` and counts upward (cumulative); a **histogram** is exposed as `_bucket{le="..."}` samples (cumulative: each bucket counts everything at or below its bound, and `+Inf` counts all), `_count` and `_sum`; every line of the same metric must be grouped; timestamps are in seconds; the file ends with `# EOF`.

`create-blocks-from` slices the input by `--max-block-duration` (24 hours here, so a week becomes seven blocks) and writes each block with its index and chunks. It must run as a user that can write the output folder. The Prometheus image runs as the user `nobody`, so after creating the blocks as root the lab changes the ownership of the volume to `nobody:nobody` (`chown -R`); otherwise Prometheus could not open them.

## Walkthrough of this repository's use

### `infrastructure/openmetrics.py`

```python
"""Write the simulated week as an OpenMetrics file that promtool can backfill into a TSDB."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TextIO

from slo_lab.domain.traffic import Tick

# Cumulative upper bounds (seconds) of the latency histogram, matching the live service's buckets.
BUCKETS = ("0.01", "0.05", "0.1", "0.3", "1", "3", "+Inf")
CHECKOUT = "/checkout"
HEALTH = "/healthz"
```

`BUCKETS` are the latency histogram's upper bounds in seconds as strings: 0.01, 0.05, 0.1, **0.3** (the SLO threshold, an exact boundary), 1, 3 and `+Inf`, the same as the live service's histogram. `CHECKOUT` and `HEALTH` are the two routes.

```python
def write_week(ticks: Iterable[Tick], start_epoch: int, out: TextIO) -> int:
    """Write counters, the latency histogram and the CPU gauge for every tick; return the number of ticks."""
    out.write("# TYPE http_requests counter\n# HELP http_requests HTTP requests handled.\n")
    out.write("# TYPE http_request_duration_seconds histogram\n# UNIT http_request_duration_seconds seconds\n")
    out.write("# HELP http_request_duration_seconds HTTP request duration.\n")
    out.write("# TYPE cpu_usage_percent gauge\n# HELP cpu_usage_percent Process CPU use.\n")
```

`write_week` takes the ticks, the start time (Unix seconds) and a text stream. It writes the metadata lines first: the counter `http_requests` (OpenMetrics adds `_total`), the histogram `http_request_duration_seconds` with its unit, and the gauge `cpu_usage_percent`.

```python
    ok_total = error_total = health_total = fast_total = count_total = 0
    # Histogram state: requests at or below each bound (all requests are at or below +Inf).
    below = dict.fromkeys(BUCKETS, 0)
    duration_sum = 0.0
    lines: list[str] = []
    count = 0
    for tick in ticks:
        count += 1
        timestamp = start_epoch + int(tick.offset.total_seconds())
```

The running totals are kept across ticks because counters are cumulative: ok, error, health-check, fast and total request counts. `below = dict.fromkeys(BUCKETS, 0)` makes a dictionary with every bucket bound set to 0: the cumulative count at or below each bound. `lines` is a buffer of text lines. For each tick the timestamp is the start plus the tick's offset.

```python
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
```

For each tick: add its counts to the totals; the **fast** requests are those not slow (`requests - slow`), all at or below 0.3 s. For the three smallest bounds, a fixed share of the fast requests falls below each (`_share_below`: 5 percent below 10 ms, 45 percent below 50 ms, 80 percent below 100 ms). All fast requests are below 0.3 s. Below 1 s are the fast ones plus half of the slow. Below 3 s and `+Inf` is everything. The sum of durations adds 0.08 s per fast request and 0.9 s per slow one. The histogram is a model, not a measurement; what matters is the share at or below 0.3 s, which is exactly the latency SLI.

```python
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
```

Thirteen lines per tick: the 200 and 500 counters of `/checkout`, the 200 counter of `/healthz`, the seven bucket lines, the `_count`, the `_sum` and the CPU gauge. Each carries the tick's timestamp. The f-strings double their braces (`{{` `}}`) to write literal ones.

```python
        if len(lines) >= 20_000:
            out.write("\n".join(lines) + "\n")
            lines.clear()
    out.write("\n".join(lines) + "\n# EOF\n")
    return count
```

Every 20,000 buffered lines the buffer is written and cleared, so a week (20,160 ticks × 13 lines = 262,080 lines) never sits in memory at once. The last write adds `# EOF`. The function returns the number of ticks.

```python
def _share_below(bound: str) -> float:
    """Of the fast requests (at most 0.3 s), the share that finished within ``bound`` seconds."""
    return {"0.01": 0.05, "0.05": 0.45, "0.1": 0.8}[bound]
```

`_share_below` is a lookup table: of the fast requests, the share that finished within the given bound.

### Backfill and the replay Prometheus

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

`backfill_replay_data` first removes a previous replay container and volume (`docker compose --profile replay rm -sf prometheus-replay`, `docker volume rm -f`) and creates the volume again. Then it runs a one-off Prometheus container as root with `promtool` as the entrypoint: `tsdb create-blocks-from openmetrics --max-block-duration=24h /input/week.om /prometheus`, with the volume mounted at `/prometheus` and the file mounted read-only at `/input/week.om`. A second one-off container runs `chown -R nobody:nobody /prometheus`. The list is written one flag per line; `# fmt: skip` tells the formatter to leave the layout alone.

```yaml
  # Serves the backfilled week (make replay). It loads no rules: the replay evaluates them at each historical step.
  prometheus-replay:
    image: prom/prometheus:v2.54.1
    profiles: ["replay"]
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=30d
    volumes:
      - ./observability/prometheus/replay/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - replay-data:/prometheus
    ports:
      - "127.0.0.1:${REPLAY_HOST_PORT:-29091}:9090"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9090/-/healthy"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

The `prometheus-replay` service belongs to the **profile** `replay`: a service with a profile is not started by a plain `docker compose up`, so `make up` does not start it; `make replay` does (`--profile replay`). It loads `observability/prometheus/replay/prometheus.yml`, keeps its data in the volume `replay-data` with a 30-day retention, and is published on 29091. The comment says it loads no rules: the replay evaluates the expressions itself.

```yaml
# The replay instance serves only the backfilled week; it scrapes nothing and loads no rules, because the replay
# evaluates rule expressions itself at every historical step.
global:
  scrape_interval: 1m
scrape_configs: []
```

`scrape_configs: []` is an empty list: it scrapes nothing, so the backfilled data is all it holds. Without a retention that covers a week, the old blocks would be deleted: the 30-day retention above prevents that.

## Try it yourself

```bash
.venv/bin/python -c "
import io
from datetime import timedelta
from slo_lab.domain.traffic import Tick
from slo_lab.infrastructure.openmetrics import write_week
out = io.StringIO()
write_week([Tick(timedelta(0), 98, 0, 2, 2, 40.0)], 1000000, out)
print(out.getvalue())"
```

Expected: the metadata lines, then 13 sample lines with the timestamp `1000000`, ending with `# EOF`. The `le="0.3"` bucket shows `96` and the count `98`.

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_infrastructure.py -k openmetrics -q
```

Expected: `2 passed`.

```bash
make replay
```

Expected (needs `make up`; several minutes): the simulation message, the size of the file in MB, then the report in `prometheus.md`.

```bash
curl -s "http://127.0.0.1:29091/api/v1/query?query=count(http_requests_total)"
```

Expected (after `make replay`): the number of replayed counter series (3).

```bash
curl -s "http://127.0.0.1:29091/api/v1/status/tsdb" | python3 -c "import json,sys; d=json.load(sys.stdin)['data']['headStats']; print(d['minTime'], d['maxTime'])"
```

Expected: head times; the backfilled blocks are older than the head, which is why queries use explicit start and end times.

## Mistakes people make

- **A counter that goes down.** Cumulative counters must not decrease, or `rate()` treats it as a reset.
- **Missing `# EOF`.** `promtool` rejects the file.
- **Wrong ownership of the output.** Prometheus then cannot open the blocks; hence the `chown`.
- **Too short a retention** on the replay instance, so the old blocks are deleted at start.
- **Mixing timestamps in milliseconds and seconds.** OpenMetrics uses seconds.
- **Backfilling into a running Prometheus' own folder.** The replay uses a separate instance.

## Check yourself

1. What does `create-blocks-from openmetrics` produce? *TSDB blocks of historical data from a text file.*
2. Why are histogram buckets cumulative? *Each `le` bucket counts everything at or below its bound, and `+Inf` counts all.*
3. Why is a separate Prometheus used? *The week is in the past; the live Prometheus stores only what it scrapes now.*
4. Why the `chown`? *The blocks were written as root, and Prometheus runs as `nobody`.*
5. What does a Compose profile do? *Services in it start only when the profile is requested.*

## Further reading

- OpenMetrics specification: https://github.com/prometheus/OpenMetrics/blob/main/specification/OpenMetrics.md
- promtool TSDB backfilling (v2.54): https://prometheus.io/docs/prometheus/2.54/storage/#backfilling-from-openmetrics-format
- Compose profiles: https://docs.docker.com/compose/how-tos/profiles/
