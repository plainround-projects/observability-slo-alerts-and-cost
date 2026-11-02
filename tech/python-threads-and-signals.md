# Python threads, locks, signals and clocks (standard library)

## What it is

Four pieces of the standard library, all about doing more than one thing at a time and about stopping cleanly.

- A **thread** is a path of execution inside a process. A process can have many; they share memory. `concurrent.futures.ThreadPoolExecutor` keeps a pool of worker threads; `pool.submit(function, argument)` hands a job to the pool and returns a **Future**, a ticket for a result that is not ready yet. `future.result()` waits for the job and returns its value (or raises the error it raised).
- A **lock** (`threading.Lock`) lets only one thread at a time run a protected piece of code. When threads change shared data, a lock stops two of them from corrupting it.
- **Thread-local storage** (`threading.local()`) gives each thread its own private attributes.
- A **signal** is a small message the operating system sends to a process. `SIGTERM` means "please stop" (what `docker stop` sends). `SIGINT` is Ctrl-C, which Python turns into `KeyboardInterrupt`. `SIGKILL` cannot be caught.

Clocks: `time.monotonic()` only moves forward and is right for durations and pacing; `time.time()` is the wall clock (Unix seconds); `time.sleep(s)` pauses the calling thread.

## Why this repository uses it

Three things need concurrency or clean stops: the **traffic generator** sends planted requests (errors, slow ones, requests with personal data) at a steady rate while earlier ones may still be waiting; the **fake pager** may receive two webhooks at once and appends to one file; and both services must stop cleanly when Docker asks (SIGTERM).

## Where it appears

| Path | What it does |
| --- | --- |
| `src/slo_lab/lab/traffic.py` | A thread pool, a thread-local session per worker, a lock around the shared result and open-loop pacing. |
| `src/slo_lab/infrastructure/pager_store.py` | A lock around file appends, reads and clears (see `alertmanager.md`). |
| `src/slo_lab/infrastructure/shutdown.py` | Turns SIGTERM into `KeyboardInterrupt`. |
| `src/slo_lab/interfaces/server.py` | Catches that exception and shuts the server down (see `waitress.md`). |
| `src/slo_lab/lab/live.py`, `volume.py`, `stores.py` | Poll with `time.monotonic()` deadlines and `time.sleep`. |

## How it works underneath

A thread pool starts its workers lazily and keeps them. `submit` puts a job on an internal queue; a free worker takes it. Because of the global interpreter lock only one thread runs Python code at any instant, but a thread that waits for the network lets the others run, so 16 workers keep 16 requests in flight.

`result.statuses[status] = result.statuses.get(status, 0) + 1` is a read, an add and a write: three steps another thread could interleave with, losing a count. Holding a lock around it makes the three steps one unit.

**Open-loop pacing** sends request number *i* at time `begin + i ÷ rate`, whether or not earlier requests have finished. That keeps the arrival rate steady (as real users do) and means a slow request does not slow the senders down. A **closed loop** waits for each answer before sending the next, and so hides exactly the slowness you want to see.

Python delivers signals only to the main thread, between bytecode instructions. A handler installed with `signal.signal(signal.SIGTERM, handler)` is called then; raising an exception from it interrupts whatever the main thread was doing.

## Walkthrough of this repository's use

### `infrastructure/shutdown.py`

```python
"""Turn SIGTERM into the same clean exit as Ctrl-C so in-flight work finishes before the process stops."""

from __future__ import annotations

import signal
from types import FrameType


def _raise_keyboard_interrupt(signal_number: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def install_termination_handler() -> None:
    """Make SIGTERM raise ``KeyboardInterrupt`` in the main thread, where the server loop handles it."""
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
```

`FrameType` is only a type hint: a signal handler receives the signal number and the **frame**, the interpreter's record of the code that was running. `_raise_keyboard_interrupt` ignores both and raises `KeyboardInterrupt`. `install_termination_handler` registers it for `SIGTERM`, so `docker stop` behaves like Ctrl-C and the same `except KeyboardInterrupt` path in `server.py` runs.

### `lab/traffic.py`: planted traffic

```python
"""Live traffic for the checkout service, with planted errors, slow requests and personal data."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

TEST_EMAIL = "test-user-redact-me@example.com"
# The card network's published test number; never a real card.
TEST_CARD = "4242424242424242"
```

The imports and two constants. `TEST_EMAIL` and `TEST_CARD` are the planted personal data. The comment on the card says it is the card network's published test number, never a real card. Using obvious test values means that finding one in a store proves a leak and cannot expose a real person.

```python
@dataclass(frozen=True, slots=True)
class TrafficPlan:
    """How many requests to send and what share of them are planted cases."""

    base_url: str
    requests: int
    seed: int
    error_share: float = 0.03
    slow_share: float = 0.01
    personal_data_share: float = 0.05
    rate_per_second: float = 0.0
    concurrency: int = 16
    outage: bool = False
```

`TrafficPlan` is the recipe: how many requests, the seed, and the share of each planted kind: 3 percent errors, 1 percent slow, 5 percent with personal data. `rate_per_second=0.0` means "as fast as possible"; `concurrency` is the number of workers (16); `outage=True` makes every request an error (the live outage).

```python
@dataclass(slots=True)
class TrafficResult:
    """The trace ids of each kind of request, as the client saw them in ``X-Trace-Id``."""

    error_traces: list[str] = field(default_factory=list)
    slow_traces: list[str] = field(default_factory=list)
    personal_data_traces: list[str] = field(default_factory=list)
    ordinary_traces: list[str] = field(default_factory=list)
    statuses: dict[int, int] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def all_traces(self) -> list[str]:
        """Every trace id seen."""
        return self.error_traces + self.slow_traces + self.personal_data_traces + self.ordinary_traces

    @property
    def sent(self) -> int:
        """Requests answered (any status)."""
        return sum(self.statuses.values())
```

`TrafficResult` collects the trace ids of each kind, as the client saw them in the `X-Trace-Id` response header, plus the count of each status and the times. It is a non-frozen dataclass (`@dataclass(slots=True)`) because the traffic code fills it in. `field(default_factory=list)` gives every instance its own empty list. `all_traces` joins the four lists; `sent` sums the status counts.

```python
def build_body(kind: str, index: int) -> dict[str, object]:
    """The JSON body for one request of the given kind."""
    body: dict[str, object] = {"items": [{"sku": f"SKU-{index % 40:03d}", "quantity": 1 + index % 3}]}
    if kind == "personal":
        body.update(customer_email=TEST_EMAIL, card_number=TEST_CARD)
    if kind == "error":
        body["simulate"] = "error"
    if kind == "slow":
        body["simulate"] = "slow"
    return body
```

`build_body(kind, index)` makes the JSON body for one request: one basket line whose SKU and quantity depend on the index (`index % 40`, `1 + index % 3`). A `personal` request adds the test email and card number; an `error` request adds `"simulate": "error"`; a `slow` one adds `"simulate": "slow"`. The service accepts `simulate` only because the lab turned `ALLOW_SIMULATION` on.

```python
def classify(rng: random.Random, plan: TrafficPlan) -> str:
    """Draw the kind of the next request."""
    if plan.outage:
        return "error"
    draw = rng.random()
    if draw < plan.error_share:
        return "error"
    if draw < plan.error_share + plan.slow_share:
        return "slow"
    if draw < plan.error_share + plan.slow_share + plan.personal_data_share:
        return "personal"
    return "ordinary"
```

`classify(rng, plan)` draws the kind of the next request. In an outage everything is an error. Otherwise one random draw between 0 and 1 is compared with cumulative thresholds: below 0.03 error, below 0.04 slow, below 0.09 personal, else ordinary. Drawing once and comparing with running totals is the standard way to pick among weighted choices.

```python
def send_traffic(
    plan: TrafficPlan,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> TrafficResult:
    """Send the plan's requests (paced when ``rate_per_second`` is set) and collect the trace ids by kind."""
    rng = random.Random(plan.seed)
    kinds = [classify(rng, plan) for _ in range(plan.requests)]
    result = TrafficResult(started_at=time.time())
    lock = threading.Lock()
    local = threading.local()
    buckets = {
        "error": result.error_traces,
        "slow": result.slow_traces,
        "personal": result.personal_data_traces,
        "ordinary": result.ordinary_traces,
    }
```

`send_traffic` takes the plan and two injectable functions: `clock` and `sleep` (a test passes fakes and sees the waits it would have made). It draws every kind first with a seeded generator, so a plan always means the same traffic. `buckets` maps each kind to the list in the result where its trace ids go.

```python
    def send(item: tuple[int, str]) -> None:
        index, kind = item
        session = getattr(local, "session", None)
        if session is None:
            session = local.session = requests.Session()
        try:
            response = session.post(f"{plan.base_url}/checkout", json=build_body(kind, index), timeout=30)
            status, trace_id = response.status_code, response.headers.get("X-Trace-Id", "")
        except requests.RequestException:
            status, trace_id = 0, ""
        with lock:
            result.statuses[status] = result.statuses.get(status, 0) + 1
            if trace_id:
                buckets[kind].append(trace_id)
```

The inner function `send` runs in a worker. It gets this thread's `requests.Session` from the thread-local object (creating it on first use) so connections are reused, posts the JSON body with a 30-second timeout, and reads the status and the `X-Trace-Id` header. A network error is recorded as status 0 with no trace id. Inside `with lock:` it updates the status count and appends the trace id to the right list.

```python
    begin = clock()
    with ThreadPoolExecutor(max_workers=plan.concurrency) as pool:
        futures = []
        for index, kind in enumerate(kinds):
            if plan.rate_per_second > 0:
                wait = begin + index / plan.rate_per_second - clock()
                if wait > 0:
                    sleep(wait)
            futures.append(pool.submit(send, (index, kind)))
        for future in futures:
            future.result()
    result.finished_at = time.time()
    return result
```

The main loop: `begin` is the start on the monotonic clock. For each request, if a rate is set, it computes `wait = begin + index / rate - clock()` and sleeps that long if positive (open-loop pacing), then submits the job. After all are submitted, `future.result()` for each waits for completion and would re-raise any error. The finish time is stored and the result returned.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/lab/test_lab.py -k "traffic or dead_service or bodies" -q
```

Expected: `4 passed`. One test sends 200 requests to a throw-away server with a fake clock and checks 199 paced waits of 0.01 s each (100 requests a second).

```bash
.venv/bin/python -c "
import random
from slo_lab.lab.traffic import TrafficPlan, classify
plan = TrafficPlan('http://x', 1000, seed=1)
rng = random.Random(2)
kinds = [classify(rng, plan) for _ in range(5000)]
print({k: kinds.count(k) for k in ('error', 'slow', 'personal', 'ordinary')})"
```

Expected: about `{'error': 150, 'slow': 50, 'personal': 250, 'ordinary': 4550}` (3, 1 and 5 percent of 5,000).

```bash
.venv/bin/python -c "
import signal, os
from slo_lab.infrastructure.shutdown import install_termination_handler
install_termination_handler()
try:
    os.kill(os.getpid(), signal.SIGTERM)
except KeyboardInterrupt:
    print('SIGTERM became KeyboardInterrupt')"
```

Expected: `SIGTERM became KeyboardInterrupt`.

```bash
docker compose stop checkout && docker compose logs checkout | tail -3; docker compose start checkout
```

Expected (needs `make up`): `shutdown requested; finishing in-flight requests` and `stopped` just before the container exits.

```bash
.venv/bin/python -c "
from slo_lab.lab.traffic import TrafficPlan, send_traffic
r = send_traffic(TrafficPlan('http://127.0.0.1:9', 3, seed=1, concurrency=2))
print(r.statuses, r.all_traces)"
```

Expected: `{0: 3} []`: a dead service counts as status 0 and gives no trace ids.

## Mistakes people make

- **Updating a shared counter without a lock** and losing counts.
- **Sharing one `requests.Session` between threads.** Sessions are not documented as thread-safe; each worker has its own.
- **Closed-loop load**, which slows down as the system slows down and hides the problem.
- **A signal handler that does real work.** This one only raises.
- **Using `time.time()` to pace or time.** It can jump; `time.monotonic()` cannot.

## Check yourself

1. What does the lock protect in `send_traffic`? *The shared status counts and the trace-id lists.*
2. What is open-loop pacing? *Sending each request at its scheduled time, regardless of whether earlier ones finished.*
3. Why use test values like `4242424242424242`? *They are published test numbers; finding one proves a leak without exposing anyone.*
4. What does a thread-local session give? *One connection pool per worker, with no sharing.*
5. What does SIGTERM become here? *A `KeyboardInterrupt`, handled where the server loop runs.*

## Further reading

- `concurrent.futures`: https://docs.python.org/3.12/library/concurrent.futures.html
- `threading`: https://docs.python.org/3.12/library/threading.html
- `signal`: https://docs.python.org/3.12/library/signal.html
- `time`: https://docs.python.org/3.12/library/time.html
- `random`: https://docs.python.org/3.12/library/random.html
