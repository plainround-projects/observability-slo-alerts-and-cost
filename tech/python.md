# Python

## What it is

Python is a programming language. A programming language is a way to write instructions that a computer follows. You write the instructions in plain text files that end in `.py`. A program called the **interpreter** reads those files and carries out the instructions one at a time. You do not have to translate them into machine code first.

Python is popular for services, scripts and tests because its code is short and readable. This repository uses Python 3.12 inside the container images and Python 3.12 or 3.13 on your own machine. The text `from __future__ import annotations` at the top of most files and the `str | None` style of writing types both need Python 3.10 or newer; `StrEnum` needs 3.11; `datetime.UTC` needs 3.11.

Some words you will meet again and again in these notes:

- A **module** is one `.py` file. A **package** is a folder of modules that has an `__init__.py` file in it. Here, `three_signals` is the package and `three_signals/domain/orders.py` is the module `three_signals.domain.orders`.
- A **function** is a named block of instructions. A **class** is a blueprint for objects that keep some data and have functions (called **methods**) that work on that data.
- A **type hint** (also called an annotation) is a note such as `order_id: int` that says what kind of value is expected. Python itself ignores type hints when the program runs. Tools such as `mypy` read them and warn you about mistakes before you run anything.
- A **standard library** module comes with Python itself (for example `json`, `logging`, `time`, `random`). A **third-party** package is installed from outside (for example Flask or requests).

## Why this repository uses it

Everything that can be written as code is Python here: the checkout service, the fake pager, the arithmetic of error budgets and burn rates, the generator that turns an SLO definition into alert rules, the simulated week of traffic, the replay that judges alert rules, and the tests. The design keeps the *ideas* of the lesson (what is a budget, when does an alert fire, what counts as a useful page) in the `domain` and `application` layers as plain functions and classes that you can test in milliseconds, and keeps the tools (Flask, YAML, Prometheus, promtool) in the outer layers.

## Where it appears

| Path | What it does with Python |
| --- | --- |
| `src/slo_lab/domain/` | SLOs, burn-rate arithmetic, the traffic model, alert semantics, page classification, the checkout request. No outside libraries. |
| `src/slo_lab/application/` | Use cases: place a checkout, generate rules, replay alerts, compare two alert sets; and the `Protocol`s they need. |
| `src/slo_lab/infrastructure/` | Adapters: settings, small runtime helpers, OpenTelemetry, YAML files, OpenMetrics output, the Prometheus API, the pager's store, logging, shutdown. |
| `src/slo_lab/interfaces/` | The two Flask apps, their entry points and the health probe. |
| `src/slo_lab/lab/` | The experiments: replay, volume, live outage, traffic, clients of the stores. |
| `tests/` | pytest tests (see `pytest.md`). |

## How it works underneath

When you run `python something.py`, the operating system starts one **process**. A process is one running program with its own memory. The interpreter inside that process reads your file, turns it into a simpler internal form called **bytecode**, and runs the bytecode.

When one module says `import json`, the interpreter looks for a module with that name. It looks in the folders listed in `sys.path`. The first time a module is imported, its top-level lines run once, and the result is remembered. Importing the same module again is cheap because the remembered copy is reused.

Python is **dynamically typed**. A variable has no fixed type; the value it points to has one. That is why type hints and `mypy` exist: they add the checking that the language itself does not do.

Python has a **global interpreter lock** (the GIL). In the standard build only one thread runs Python bytecode at a time. Threads still help when the program mostly waits for the network or a database, because a waiting thread lets another thread run. All the services in these repositories spend most of their time waiting, so threads are enough.

A **virtual environment** (a `.venv` folder) is a private copy of the interpreter's package folder. You install the project's dependencies into it, so they do not mix with other projects on the same machine.

## Python features you will meet in this repository

Read this list once. Each feature is explained again, with an example from the code, where it first appears.

- **f-strings.** `f"order {order_id} does not exist"` builds a string by putting the value of the names inside the braces into the text. The `f` before the opening quote turns it on.
- **Type hints.** `def next_delay_seconds(self) -> float:` says the function returns a float. `int | None` means "an int, or None". `list[str]` is a list of strings. `tuple[OrderItem, ...]` is a tuple of any length whose items are all `OrderItem`.
- **`from __future__ import annotations`.** It tells Python to treat all type hints as text and not evaluate them when the module loads. This lets a class name itself in a hint and keeps imports used only for hints cheap.
- **Dataclasses.** `@dataclass(frozen=True, slots=True)` turns a class that only lists fields into one that has a constructor, an equality test and a readable text form generated for it. `frozen=True` forbids changing a field after creation, so the object is a fixed value. `slots=True` stores fields in a compact fixed layout instead of a dictionary.
- **Enums.** `class ItemCountBucket(StrEnum)` defines a small closed set of named values. A `StrEnum` member is also a string, so `str(ItemCountBucket.ONE_TO_TWO)` is `"1-2"`.
- **Protocols.** `class OrderRepository(Protocol)` describes what methods an object must have, without forcing it to inherit from anything. Any class with those methods fits. This is called structural typing.
- **Context managers.** `with something as name:` runs setup code on entering and cleanup code on leaving, even when an error happens. A class can offer this with `__enter__` and `__exit__`; a function can offer it with the `@contextmanager` decorator and a `yield`.
- **Decorators.** A line such as `@app.get("/healthz")` above a function hands that function to another function, which registers it or wraps it.
- **Exceptions.** `raise SomeError("text")` stops the current function and travels up the call chain until an `except SomeError:` catches it. `raise NewError(...) from error` keeps the original error attached as the cause.
- **Comprehensions.** `[x * 2 for x in numbers if x > 0]` builds a list from another sequence in one line. `{...}` with `for` builds a set or a dictionary. `( ... for ...)` inside a function call builds values lazily one at a time.
- **Keyword arguments and `**`.** `f(a=1, b=2)` passes by name. `{**one, **two}` merges two dictionaries into a new one; later keys win.
- **Closures.** A function defined inside another function can use the outer function's variables. Flask route functions in this repository are defined that way inside `create_*_app`.
- **Properties.** `@property` lets a method be read like a field: `order.total_cents` with no parentheses.
- **Class methods.** `@classmethod` gives a method that receives the class (`cls`) instead of one object. It is used here as an alternative constructor: `BackSettings.from_env(os.environ)`.
- **The `if __name__ == "__main__":` guard.** The code under it runs only when the file is started directly, not when another module imports it.

## Walkthrough of this repository's use

### The package markers

`src/slo_lab/__init__.py`:

```python
"""Burn-rate alerts on SLOs, measured on a replayed week, and a telemetry pipeline that samples and redacts."""
```
`src/slo_lab/domain/__init__.py`:

```python
"""SLOs, burn-rate arithmetic, the traffic model, alert semantics and page classification; depends on nothing else."""
```
`src/slo_lab/application/__init__.py`:

```python
"""Use cases and the ports they need; depends on the domain only."""
```
`src/slo_lab/infrastructure/__init__.py`:

```python
"""Adapters for OpenTelemetry, YAML files, Prometheus, promtool and the process environment."""
```
`src/slo_lab/interfaces/__init__.py`:

```python
"""HTTP entry points and process wiring; may depend on every other layer, nothing depends on it."""
```
```python
"""The experiments: the replayed week, the live traffic, and the volume and redaction comparison.

The services never import this package; it only drives the running stack from outside.
```

Each `__init__.py` makes its folder a package and holds a docstring saying what the layer is for and what it may depend on. The first states the whole project: burn-rate alerts on SLOs measured on a replayed week, and a pipeline that samples and redacts.

### `domain/errors.py`

```python
"""Named errors that cross layer boundaries."""


class DomainError(Exception):
    """Base class for every error this project raises on purpose."""


class InvalidSloError(DomainError):
    """An SLO definition is out of range or incomplete."""


class InvalidPolicyError(DomainError):
    """A burn-rate policy is inconsistent (a short window longer than its long window, a non-positive rate)."""
```

`DomainError` is the base class of every error raised on purpose; `InvalidSloError` and `InvalidPolicyError` say that a definition is out of range or inconsistent. A class with only a docstring needs no other body.

### `domain/slo.py`: what an objective is

A **service level objective** (SLO) is a promise about service quality over a period, written as a number. A **service level indicator** (SLI) is the measurement the promise is about, here a ratio: good events divided by valid events. An SLO of 99.5 percent availability over 30 days says: at least 99.5 percent of valid requests must be good. The **error budget** is what the promise allows to go wrong: `1 − 0.995 = 0.5 percent`. Over 30 days, 0.5 percent of the time is `0.005 × 43,200 min = 216 minutes` of total outage; at 500 requests a minute it is `0.005 × 500 × 43,200 = 108,000` bad requests.

```python
"""A service level objective: what counts as good and valid, the target and the error budget it implies."""

from __future__ import annotations

from dataclasses import dataclass

from slo_lab.domain.errors import InvalidSloError

WINDOW_TOKEN = "__WINDOW__"
MINUTES_PER_DAY = 24 * 60
```

`WINDOW_TOKEN = "__WINDOW__"` is a placeholder that appears inside the PromQL text of an SLI; the generator replaces it with `1h`, `5m` and so on. `MINUTES_PER_DAY = 24 * 60` is arithmetic written out so the number's meaning is visible.

```python
@dataclass(frozen=True, slots=True)
class Slo:
    """An objective over a rolling window.

    ``good`` and ``valid`` are PromQL expressions that contain ``__WINDOW__`` where a range such as ``1h`` goes; the
    SLI is good / valid, and the objective is the share of valid events that must be good.
    """

    name: str
    objective: float
    window_days: int
    description: str
    good: str
    valid: str
```

`Slo` is a frozen dataclass: a name, the objective (a fraction such as 0.995), the window in days, a description, and two PromQL expressions: `good` (the rate of good requests) and `valid` (the rate of valid requests).

```python
    def __post_init__(self) -> None:
        """Reject an objective outside (0, 1), a non-positive window, or expressions without the window token."""
        if not 0.0 < self.objective < 1.0:
            raise InvalidSloError(f"{self.name}: objective must be between 0 and 1 exclusive, got {self.objective}")
        if self.window_days < 1:
            raise InvalidSloError(f"{self.name}: window_days must be at least 1")
        if WINDOW_TOKEN not in self.good or WINDOW_TOKEN not in self.valid:
            raise InvalidSloError(f"{self.name}: good and valid must both contain {WINDOW_TOKEN}")
```

`__post_init__` rejects an objective that is not strictly between 0 and 1 (`0.0 < x < 1.0`: a chained comparison), a window under one day, and expressions that lack the window token. Failing at construction means a typo in `slos.yaml` stops the program at once.

```python
    @property
    def error_budget(self) -> float:
        """The share of valid events allowed to be bad: 1 - objective."""
        return 1.0 - self.objective

    @property
    def window_minutes(self) -> int:
        """The window in minutes."""
        return self.window_days * MINUTES_PER_DAY

    def budget_minutes(self) -> float:
        """The budget as minutes of total outage over the window."""
        return self.error_budget * self.window_minutes

    def budget_requests(self, requests_per_minute: float) -> float:
        """The budget as bad requests allowed over the window at a steady request rate."""
        return self.error_budget * requests_per_minute * self.window_minutes
```

`error_budget` is `1 - objective`. `window_minutes` multiplies days by 1,440. `budget_minutes()` is the budget as minutes of full outage (`0.005 × 43,200 = 216`). `budget_requests(requests_per_minute)` is the budget as bad requests at a steady rate (`108,000` at 500 a minute). A test and the SLO document check these numbers.

```python
    def error_ratio_expression(self, window: str) -> str:
        """PromQL for the share of bad events over ``window`` (for example ``1h``)."""
        good = self.good.replace(WINDOW_TOKEN, window)
        valid = self.valid.replace(WINDOW_TOKEN, window)
        return f"(1 - ({good}) / ({valid}))"
```

`error_ratio_expression("1h")` builds the PromQL for the share of bad events over a window: `(1 - (good) / (valid))` with the token replaced by the window (`str.replace`). The parentheses make sure the division happens before the subtraction.

### `domain/burn_rate.py`: how fast the budget is spent

A **burn rate** is how fast the budget is being spent compared with the pace that would use exactly the whole budget over exactly the whole window. Burn rate 1 spends the budget just as the window ends. Burn rate 14.4 spends it 14.4 times too fast, so a 30-day budget lasts `720 / 14.4 = 50` hours; and in one hour it consumes `14.4 / 720 = 2` percent of the whole budget.

```python
"""Multiwindow, multi-burn-rate alert arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from slo_lab.domain.errors import InvalidPolicyError
from slo_lab.domain.slo import Slo


def prometheus_duration(value: timedelta) -> str:
    """Format a duration the way PromQL writes it: ``6h``, ``30m``, ``90s``."""
    seconds = int(value.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"
```

`prometheus_duration` formats a `timedelta` the way PromQL writes durations. It converts to whole seconds and tries the units from the biggest to the smallest: days (86,400 s), hours (3,600 s), minutes (60 s). The first unit that divides the seconds exactly wins (`seconds % size == 0`, the **remainder** is zero), so one hour is `1h`, 90 minutes is `90m`, 90 seconds is `90s`. `seconds // size` is integer division.

```python
@dataclass(frozen=True, slots=True)
class BurnRateRule:
    """Alert when the budget burns at ``burn_rate`` times the sustainable pace over BOTH windows.

    The long window proves the problem is real (it has already cost something); the short window proves it is still
    happening (so the alert resolves soon after the fix and a blip that ended is not paged).
    """

    severity: str
    burn_rate: float
    long_window: timedelta
    short_window: timedelta

    def __post_init__(self) -> None:
        """Validate the rate and the order of the windows."""
        if self.burn_rate <= 1.0:
            raise InvalidPolicyError("a burn rate of 1 or less spends the budget no faster than it refills")
        if self.short_window >= self.long_window:
            raise InvalidPolicyError("the short window must be shorter than the long window")
        if self.severity not in {"page", "ticket"}:
            raise InvalidPolicyError(f"severity must be page or ticket, got {self.severity!r}")
```

`BurnRateRule` says: alert when the budget burns at `burn_rate` times the sustainable pace over **both** a long window and a short one. The docstring gives the reason for two windows: the long window proves the problem is real (it has already cost something), and the short window proves it is still happening, so the alert stops soon after the fix and a blip that ended is not paged. `__post_init__` rejects a rate of 1 or less (it would spend the budget no faster than it refills), a short window that is not shorter than the long one, and a severity other than `page` or `ticket`. The `{...}` set lookup `not in {"page", "ticket"}` is a quick membership test.

```python
    def budget_consumed(self, slo: Slo) -> float:
        """The share of the whole window's budget spent when the burn lasts for the long window."""
        return self.burn_rate * self.long_window.total_seconds() / (slo.window_days * 86400)

    def error_ratio_threshold(self, slo: Slo) -> float:
        """The bad-event ratio above which both windows must sit for the alert to fire."""
        return self.burn_rate * slo.error_budget

    def hours_to_exhaustion(self, slo: Slo) -> float:
        """How long the whole budget lasts at this burn rate."""
        return slo.window_days * 24 / self.burn_rate
```

Three derived numbers. `budget_consumed` is `burn_rate × long window ÷ SLO window`: for 14.4 over 1 hour in a 30-day window, `14.4 × 3,600 ÷ 2,592,000 = 0.02`. `error_ratio_threshold` is `burn_rate × error budget`: for 99.5 percent, `14.4 × 0.005 = 0.072`, so both windows must show more than 7.2 percent bad requests; for the 95 percent latency objective the same burn rate is `14.4 × 0.05 = 0.72`. `hours_to_exhaustion` is `window_days × 24 ÷ burn_rate`. No threshold is ever typed by hand; it is derived.

```python
@dataclass(frozen=True, slots=True)
class BurnRatePolicy:
    """A named set of burn-rate rules applied to every SLO."""

    name: str
    description: str
    rules: tuple[BurnRateRule, ...]
```

`BurnRatePolicy` is a named tuple of rules applied to every SLO (for example "default" and "lesson").

### `domain/alerts.py`: what "for" means

```python
"""Alert semantics as Prometheus applies them: an expression, a for-duration, and the episodes that result."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlertRule:
    """One alerting rule."""

    name: str
    expression: str
    for_seconds: float
    severity: str


@dataclass(frozen=True, slots=True)
class FiringEpisode:
    """One continuous stretch during which an alert was firing; its start is when the pager is notified."""

    alert: str
    severity: str
    started_at: float
    ended_at: float
```

`AlertRule` holds a name, a PromQL expression, a `for_seconds` duration and a severity. `FiringEpisode` is one continuous stretch during which the alert fires; its start is when the pager is notified.

```python
def firing_episodes(
    rule: AlertRule, step_seconds: float, evaluations: Iterable[tuple[float, bool]]
) -> list[FiringEpisode]:
    """Turn (time, expression-is-true) evaluations into firing episodes.

    As in Prometheus: when the expression becomes true the alert is pending; it fires once the expression has been
    true at every evaluation for ``for_seconds``; it stops when the expression is false at an evaluation.
    """
    episodes: list[FiringEpisode] = []
    pending_since: float | None = None
    firing_since: float | None = None
    last_true = 0.0
    for when, is_true in evaluations:
        if is_true:
            if pending_since is None:
                pending_since = when
            last_true = when
            if firing_since is None and when - pending_since >= rule.for_seconds:
                firing_since = when
            continue
        if firing_since is not None:
            episodes.append(FiringEpisode(rule.name, rule.severity, firing_since, last_true + step_seconds))
        pending_since = firing_since = None
    if firing_since is not None:
        episodes.append(FiringEpisode(rule.name, rule.severity, firing_since, last_true + step_seconds))
    return episodes
```

`firing_episodes` re-implements, in a few lines, how Prometheus turns "the expression is true at this evaluation" into notifications. It walks the evaluations in time order. When the expression becomes true the alert is *pending* (`pending_since`). If it has been true at every evaluation for `for_seconds`, it starts *firing* (`firing_since`). When the expression turns false: if it was firing, an episode from `firing_since` to `last_true + step_seconds` is recorded; pending and firing are reset (`pending_since = firing_since = None` assigns both). After the loop, a still-open episode is closed. The same function is what a unit test drives with simple patterns of 0s and 1s.

### `domain/traffic.py`: a week of traffic

```python
"""A seeded model of a week of traffic for a small service, with planted incidents."""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

TICK_SECONDS = 30
SLOW_THRESHOLD_SECONDS = 0.3
```

The simulation uses `math` (sine, square root, exponent), `random` (seeded generator), a time step of 30 seconds and a latency threshold of 0.3 seconds, the same as the SLO.

```python
@dataclass(frozen=True, slots=True)
class Incident:
    """Something that happens during the week; ``user_facing`` marks the ones a user would feel."""

    name: str
    start: timedelta
    duration: timedelta
    user_facing: bool
    error_rate: float | None = None
    cpu_percent: float | None = None

    @property
    def end(self) -> timedelta:
        """When the incident stops."""
        return self.start + self.duration

    def active_at(self, offset: timedelta) -> bool:
        """True while ``offset`` falls inside the incident."""
        return self.start <= offset < self.end
```

An `Incident` is something that happens during the week: a name, when it starts and how long it lasts (`timedelta`s, offsets from the start of the week), whether users would feel it (`user_facing`), and optionally an error rate or a CPU level. `end` and `active_at(offset)` are small helpers (`start <= offset < end`).

```python
@dataclass(frozen=True, slots=True)
class TrafficProfile:
    """The service's ordinary behavior."""

    requests_per_minute: float = 500.0
    diurnal_swing: float = 0.3
    baseline_error_rate: float = 0.0001
    slow_share: float = 0.02
    health_checks_per_minute: float = 4.0
    cpu_baseline_percent: float = 38.0
    cpu_noise_percent: float = 5.0
    cpu_bursts_per_day: int = 6
    cpu_burst_minutes: int = 3
    cpu_burst_percent: float = 88.0
```

`TrafficProfile` is the service's ordinary behavior with defaults: 500 requests a minute, a daily swing of 30 percent, a baseline error rate of 0.01 percent, 2 percent slow requests, 4 health checks a minute, and CPU around 38 percent with noise, six short bursts a day of three minutes at 88 percent.

```python
@dataclass(frozen=True, slots=True)
class Tick:
    """What happened in one 30-second interval."""

    offset: timedelta
    ok_requests: int
    error_requests: int
    slow_requests: int
    health_checks: int
    cpu_percent: float

    @property
    def requests(self) -> int:
        """All valid requests (health checks are not valid requests)."""
        return self.ok_requests + self.error_requests
```

A `Tick` is what happened in one 30-second interval: counts of ok, error and slow requests, health checks and CPU. `requests` is ok plus errors (health checks are not valid requests).

```python
def _poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    if mean >= 30:
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    limit, product, count = math.exp(-mean), rng.random(), 0
    while product > limit:
        count += 1
        product *= rng.random()
    return count
```

`_poisson` draws a random count whose average is `mean`. Real request arrivals are close to a **Poisson distribution**. For a mean of 30 or more it uses a normal approximation (`rng.gauss(mean, sqrt(mean))`, rounded and never below 0). For small means it uses Knuth's exact method: multiply random numbers until the product drops below `e^(−mean)`, counting the multiplications.

```python
def week_incidents() -> tuple[Incident, ...]:
    """The lesson's planted incidents, plus a 90-second blip that the fast-burn page must ignore."""
    return (
        Incident("full-outage", timedelta(minutes=1800), timedelta(minutes=10), True, error_rate=1.0),
        Incident("partial-degradation", timedelta(minutes=4320), timedelta(minutes=120), True, error_rate=0.03),
        Incident("cpu-spike-no-impact", timedelta(minutes=6000), timedelta(minutes=5), False, cpu_percent=95.0),
        Incident("blip-90s", timedelta(minutes=8100), timedelta(seconds=90), False, error_rate=1.0),
    )
```

`week_incidents()` is the planted story, all offsets in minutes from the start of the week: a **full outage** of 10 minutes (every request fails) at minute 1,800; a **partial degradation** of two hours at 3 percent errors at minute 4,320; a **CPU spike** of 5 minutes at 95 percent that no user feels; and a **blip** of 90 seconds of 100 percent errors that the fast-burn page must ignore.

```python
def _cpu_burst_starts(rng: random.Random, profile: TrafficProfile, days: int) -> list[timedelta]:
    """Ordinary batch jobs: a few short CPU bursts a day that no user ever notices."""
    starts: list[timedelta] = []
    for day in range(days):
        for _ in range(profile.cpu_bursts_per_day):
            starts.append(timedelta(days=day, minutes=rng.randrange(0, 24 * 60)))
    return starts
```

`_cpu_burst_starts` picks, for each day, six random minutes for ordinary batch jobs: bursts that raise CPU but never hurt users. They exist to make the CPU alert noisy, like real life.

```python
def simulate(profile: TrafficProfile, incidents: tuple[Incident, ...], days: int, seed: int) -> Iterator[Tick]:
    """Yield one tick per 30 simulated seconds for ``days`` days; the same arguments give the same ticks."""
    rng = random.Random(seed)
    bursts = _cpu_burst_starts(rng, profile, days)
    burst_length = timedelta(minutes=profile.cpu_burst_minutes)
    ticks = days * 86400 // TICK_SECONDS
    per_tick = TICK_SECONDS / 60
    for index in range(ticks):
        offset = timedelta(seconds=index * TICK_SECONDS)
        hour = offset.total_seconds() / 3600 % 24
        load = 1 + profile.diurnal_swing * math.sin(2 * math.pi * (hour - 9) / 24)
        total = _poisson(rng, profile.requests_per_minute * per_tick * load)
        active = [incident for incident in incidents if incident.active_at(offset)]
        error_rate = next((i.error_rate for i in active if i.error_rate is not None), profile.baseline_error_rate)
        errors = rng.binomialvariate(total, error_rate) if total else 0
        slow = rng.binomialvariate(total - errors, profile.slow_share) if total - errors else 0
        cpu = rng.gauss(profile.cpu_baseline_percent + 8 * load, profile.cpu_noise_percent)
        if any(start <= offset < start + burst_length for start in bursts):
            cpu = rng.gauss(profile.cpu_burst_percent, 2.0)
        spike = next((i.cpu_percent for i in active if i.cpu_percent is not None), None)
        if spike is not None:
            cpu = rng.gauss(spike, 1.5)
```

`simulate` is a **generator** (a function that `yield`s values one at a time instead of returning a list). It creates the random generator from the seed, so the same arguments always give the same week. For each of 20,160 ticks (7 days × 86,400 s ÷ 30 s) it computes the hour of the day and a load factor from a sine wave (`1 + swing × sin(2π(hour − 9)/24)`, peaking in the afternoon), draws the total requests, finds the incidents active now, picks the error rate (the first incident that sets one, else the baseline), draws the errors from a **binomial** distribution (`rng.binomialvariate(total, error_rate)`: how many of `total` independent requests fail with that probability; this method exists since Python 3.12), then the slow requests among the successful ones, and the CPU (Gaussian noise around a baseline that moves with load, replaced by a burst or by an incident's level). `next((... for ...), default)` takes the first matching item or a default.

```python
        yield Tick(
            offset=offset,
            ok_requests=total - errors,
            error_requests=errors,
            slow_requests=slow,
            health_checks=_poisson(rng, profile.health_checks_per_minute * per_tick),
            cpu_percent=min(max(cpu, 1.0), 100.0),
        )
```

It yields the tick. `min(max(cpu, 1.0), 100.0)` **clamps** CPU to the range 1 to 100.

### `domain/pages.py`: judging notifications

```python
"""Classify what the pager received against the incidents that were planted."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from slo_lab.domain.traffic import Incident

GRACE = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Page:
    """One notification the fake pager received, at ``offset`` into the replayed week."""

    alert: str
    severity: str
    offset: timedelta
```

A `Page` is one notification the fake pager received, with its offset into the replayed week. `GRACE = timedelta(minutes=15)` is how long after an incident ends an alert still counts as caused by it.

```python
@dataclass(frozen=True, slots=True)
class IncidentCoverage:
    """Whether (and how fast) a user-facing incident produced an alert."""

    incident: str
    first_alert: str | None
    first_severity: str | None
    delay: timedelta | None
    first_page_delay: timedelta | None

    @property
    def detected(self) -> bool:
        """True when at least one alert fired for the incident."""
        return self.first_alert is not None
```

`IncidentCoverage` records, for a user-facing incident, the first alert that fired, its severity, the delay from the start of the incident, and the delay of the first *page* (a ticket may come first and still not wake anyone). `detected` is true when any alert fired.

```python
@dataclass(frozen=True, slots=True)
class PageReport:
    """The totals the lesson asks for."""

    total: int
    actionable: int
    by_alert: dict[str, int]
    false_alarm_causes: dict[str, int]
    coverage: tuple[IncidentCoverage, ...]

    @property
    def actionable_share(self) -> float:
        """Actionable notifications over all notifications (1.0 when there were none)."""
        return self.actionable / self.total if self.total else 1.0

    @property
    def every_user_facing_incident_detected(self) -> bool:
        """True when each user-facing incident produced at least one alert."""
        return all(item.detected for item in self.coverage)
```

`PageReport` has the totals the lesson asks for: all notifications, the actionable ones, counts per alert, the causes of false alarms and the coverage. `actionable_share` is actionable over total, or 1.0 when there were none. `every_user_facing_incident_detected` uses `all(...)`, true when every element of the sequence is true.

```python
def _matching(page: Page, incident: Incident) -> bool:
    return incident.start <= page.offset <= incident.end + GRACE
```

A page *matches* an incident when its time lies between the incident's start and its end plus the grace period.

```python
def classify(pages: list[Page], incidents: tuple[Incident, ...]) -> PageReport:
    """An alert is actionable when it starts inside (or just after) a user-facing incident."""
    user_facing = [incident for incident in incidents if incident.user_facing]
    quiet = [incident for incident in incidents if not incident.user_facing]
    causes: Counter[str] = Counter()
    actionable = 0
    for page in pages:
        if any(_matching(page, incident) for incident in user_facing):
            actionable += 1
            continue
        matched = next((incident for incident in quiet if _matching(page, incident)), None)
        causes[matched.name if matched else "ordinary baseline noise"] += 1
    coverage = []
    for incident in user_facing:
        hits = sorted((page for page in pages if _matching(page, incident)), key=lambda page: page.offset)
        first = hits[0] if hits else None
        first_page = next((page for page in hits if page.severity == "page"), None)
        coverage.append(
            IncidentCoverage(
                incident.name,
                first.alert if first else None,
                first.severity if first else None,
                first.offset - incident.start if first else None,
                first_page.offset - incident.start if first_page else None,
            )
        )
    return PageReport(
        total=len(pages),
        actionable=actionable,
        by_alert=dict(Counter(page.alert for page in pages)),
        false_alarm_causes=dict(causes),
        coverage=tuple(coverage),
    )
```

`classify` splits the incidents into user-facing and quiet. A page is **actionable** when it matches a user-facing incident. Otherwise it is a false alarm, and its cause is the quiet incident it matches (for example the CPU spike) or the phrase "ordinary baseline noise". A `Counter` counts the causes and the pages per alert. For each user-facing incident it sorts the matching pages by time, takes the first one and the first with severity `page`, and builds the coverage record with the delays (`first.offset - incident.start` is a `timedelta`).

### `domain/checkout.py`: the request the service accepts

```python
"""The checkout request the service accepts and what it can end in."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from slo_lab.domain.errors import DomainError

_SKU = re.compile(r"^[A-Za-z0-9-]{1,32}$")
MAX_LINES = 20
MAX_QUANTITY = 100
```

`_SKU = re.compile(r"^[A-Za-z0-9-]{1,32}$")` is a regular expression for a product code: 1 to 32 letters, digits or dashes (see `regular-expressions.md`). `MAX_LINES` and `MAX_QUANTITY` are limits.

```python
class InvalidCheckoutError(DomainError):
    """The request body is not a valid checkout."""


class CheckoutFailedError(DomainError):
    """The checkout failed on the server side (the simulated 5xx)."""


class Simulation(StrEnum):
    """Lab-only switch that makes a request fail or run slowly, so the scenario can plant known incidents."""

    NONE = "none"
    ERROR = "error"
    SLOW = "slow"
```

Two named errors and `Simulation`, a `StrEnum` with three values (`none`, `error`, `slow`). The simulation is a **lab-only switch**: a request can ask to fail or run slowly so the scenarios can plant known incidents; the service refuses it unless `ALLOW_SIMULATION` is on.

```python
@dataclass(frozen=True, slots=True)
class OrderLine:
    """One line of the basket."""

    sku: str
    quantity: int

    def __post_init__(self) -> None:
        """Validate the SKU shape and the quantity range."""
        if not _SKU.fullmatch(self.sku):
            raise InvalidCheckoutError("sku must be 1 to 32 letters, digits or dashes")
        if not 1 <= self.quantity <= MAX_QUANTITY:
            raise InvalidCheckoutError(f"quantity must be between 1 and {MAX_QUANTITY}")
```

`OrderLine` validates the SKU with `fullmatch` and the quantity range (`1 <= quantity <= 100`).

```python
@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    """A basket with optional contact and payment details and an optional lab simulation."""

    lines: tuple[OrderLine, ...]
    customer_email: str | None = None
    card_number: str | None = None
    simulation: Simulation = Simulation.NONE

    def __post_init__(self) -> None:
        """A checkout needs between 1 and ``MAX_LINES`` lines."""
        if not 1 <= len(self.lines) <= MAX_LINES:
            raise InvalidCheckoutError(f"a checkout needs between 1 and {MAX_LINES} lines")


@dataclass(frozen=True, slots=True)
class Receipt:
    """What a successful checkout returns."""

    reference: str
    item_count: int
```

`CheckoutRequest` is a basket (1 to 20 lines) with an optional email, an optional card number and a simulation (default none). `Receipt` is what a success returns.

### `application/ports.py`

```python
"""The boundaries the use cases talk through; infrastructure supplies the implementations."""

from __future__ import annotations

from typing import Protocol

from slo_lab.domain.pages import Page


class RangeEvaluator(Protocol):
    """Evaluates a PromQL alert expression over a time range against stored (replayed) data."""

    def true_at(self, expression: str, start: float, end: float, step_seconds: float) -> set[float]:
        """The evaluation times (Unix seconds) at which the expression returned at least one series."""
        ...


class Annotator(Protocol):
    """Adds attributes to the span of the request being handled."""

    def set(self, key: str, value: str) -> None:
        """Attach ``key`` = ``value`` to the current span."""
        ...


class Delay(Protocol):
    """Waits; injected so tests do not sleep."""

    def wait(self, seconds: float) -> None:
        """Block for ``seconds``."""
        ...


class Reference(Protocol):
    """Creates order references; injected so tests are deterministic."""

    def next(self) -> str:
        """A new reference such as ``ord-000042``."""
        ...


class PageStore(Protocol):
    """The fake pager's record of what it was paged with."""

    def add(self, page: Page, received_at: float) -> None:
        """Record one page."""
        ...
```

Five protocols. `RangeEvaluator.true_at` evaluates a PromQL expression over a time range and returns the times at which it produced at least one series. `Annotator.set` attaches an attribute to the current span. `Delay.wait` waits (injected so tests do not sleep). `Reference.next` makes order references (injected so tests are deterministic). `PageStore.add` records a page.

### `application/place_checkout.py`: the naive use case

```python
"""The service's use case: accept a basket and answer, failing or slowing down when the lab asks it to.

This use case is deliberately naive about personal data. It puts the customer's email on the span and writes the card
number into a log line, which is the leak the Collector's redaction processor exists to stop.
"""

from __future__ import annotations

import logging

from slo_lab.application.ports import Annotator, Delay, Reference
from slo_lab.domain.checkout import CheckoutFailedError, CheckoutRequest, Receipt, Simulation

_log = logging.getLogger(__name__)
```

The docstring is blunt: this use case is **deliberately naive** about personal data. It puts the customer's email on the span and writes the card number into a log line. That is the leak that the gateway's redaction exists to stop. (The values are test values, never real.)

```python
class PlaceCheckout:
    """Handles one checkout."""

    def __init__(self, annotator: Annotator, delay: Delay, references: Reference, slow_seconds: float) -> None:
        """Wire the use case to its collaborators."""
        self._annotator = annotator
        self._delay = delay
        self._references = references
        self._slow_seconds = slow_seconds

```

The constructor takes the annotator, a delay, a reference generator and the slow time.

```python
    def execute(self, request: CheckoutRequest) -> Receipt:
        """Return a receipt, or raise ``CheckoutFailedError`` for a simulated server error."""
        if request.customer_email:
            self._annotator.set("customer.email", request.customer_email)
        if request.card_number:
            self._annotator.set("payment.card", request.card_number)
            _log.info(f"payment received from {request.customer_email or 'guest'} with card {request.card_number}")
        if request.simulation is Simulation.SLOW:
            self._delay.wait(self._slow_seconds)
        if request.simulation is Simulation.ERROR:
            _log.error("checkout failed", extra={"lines": len(request.lines)})
            raise CheckoutFailedError("the payment provider did not answer")
        receipt = Receipt(self._references.next(), sum(line.quantity for line in request.lines))
        _log.info("checkout completed", extra={"reference": receipt.reference, "items": receipt.item_count})
        return receipt
```

If there is an email, it is set as the span attribute `customer.email`. If there is a card number, it becomes `payment.card` **and** is written into a log message with an f-string. A `SLOW` simulation waits `slow_seconds`. An `ERROR` simulation logs an error line and raises `CheckoutFailedError`. Otherwise it makes a receipt (`sum(...)` of the quantities) and logs one line with the reference. The leak is intentional and the redaction is tested against it.

### `application/rule_builder.py`: rules from definitions

```python
"""Generate burn-rate alert rules from SLO definitions and a policy, so thresholds are derived and never typed."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from slo_lab.domain.alerts import AlertRule
from slo_lab.domain.burn_rate import BurnRatePolicy, BurnRateRule, prometheus_duration
from slo_lab.domain.slo import Slo

_KIND = {"page": "FastBurn", "ticket": "SlowBurn"}


def alert_name(slo: Slo, rule: BurnRateRule) -> str:
    """For example ``AvailabilityBudgetFastBurn``."""
    return f"{slo.name.title()}Budget{_KIND[rule.severity]}"
```

`_KIND` maps severity to a word: `page` is `FastBurn`, `ticket` is `SlowBurn`. `alert_name` builds names such as `AvailabilityBudgetFastBurn` (`str.title()` capitalizes the first letter).

```python
def expression(slo: Slo, rule: BurnRateRule) -> str:
    """Both windows must show a bad-event ratio above the threshold."""
    threshold = f"{rule.error_ratio_threshold(slo):.6g}"
    long_ratio = slo.error_ratio_expression(prometheus_duration(rule.long_window))
    short_ratio = slo.error_ratio_expression(prometheus_duration(rule.short_window))
    return f"{long_ratio} > {threshold}\nand\n{short_ratio} > {threshold}"
```

`expression` formats the threshold with `:.6g` (six significant digits, no trailing zeros: `0.072`) and joins two comparisons with a line break, `and`, and a line break: both windows must show a bad-event ratio above the threshold.

```python
def build_alert_rules(slos: Sequence[Slo], policy: BurnRatePolicy) -> list[AlertRule]:
    """One alert per (SLO, rule): page rules first, then tickets."""
    return [
        AlertRule(alert_name(slo, rule), expression(slo, rule), 0.0, rule.severity)
        for slo in slos
        for rule in policy.rules
    ]
```

`build_alert_rules` is a **nested comprehension**: one `AlertRule` for each SLO and each rule of the policy, with a `for` of 0 because the two windows already do the job of smoothing.

```python
def rule_file_document(slos: Sequence[Slo], policy: BurnRatePolicy) -> dict[str, Any]:
    """The Prometheus rule file for the policy as plain data, ready to be written as YAML."""
    rules: list[dict[str, Any]] = []
    for slo in slos:
        for rule in policy.rules:
            consumed = rule.budget_consumed(slo)
            rules.append(
                {
                    "alert": alert_name(slo, rule),
                    "expr": expression(slo, rule),
                    "labels": {"severity": rule.severity, "slo": slo.name},
                    "annotations": {
                        "summary": (
                            f"{slo.name.capitalize()} error budget burning at {rule.burn_rate:g}x: "
                            f"{consumed:.1%} of the {slo.window_days}-day budget in "
                            f"{prometheus_duration(rule.long_window)}."
                        ),
                        "runbook": (
                            f"Check the {slo.name} SLI panel, open an error or slow trace from the last "
                            f"{prometheus_duration(rule.short_window)}, and roll back or fix the cause."
                        ),
                    },
                }
            )
    return {"groups": [{"name": f"slo-burn-rate-{policy.name}", "rules": rules}]}
```

`rule_file_document` builds the Prometheus rule file as plain data (dicts and lists), ready to be written as YAML: for each SLO and rule, an alert with labels (`severity` and `slo`) and two annotations: a summary that states how fast the budget burns and how much of it is spent in the long window (`{consumed:.1%}` formats a fraction as a percentage with one decimal), and a runbook sentence. The group is named after the policy.

### `application/replay_alerts.py` and `compare.py`

```python
"""Replay alert rules over stored history and report when each one would have notified the pager."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from slo_lab.application.ports import RangeEvaluator
from slo_lab.domain.alerts import AlertRule, firing_episodes
from slo_lab.domain.pages import Page


class ReplayAlerts:
    """Evaluates each rule's expression at every step with real PromQL, then applies Prometheus's for-duration rule."""

    def __init__(self, evaluator: RangeEvaluator) -> None:
        """Take the evaluator that runs PromQL over the stored week."""
        self._evaluator = evaluator

    def run(self, rules: Sequence[AlertRule], start: float, end: float, step_seconds: float) -> list[Page]:
        """Return one page per firing episode, ordered by time."""
        steps = [start + index * step_seconds for index in range(int((end - start) // step_seconds) + 1)]
        pages: list[Page] = []
        for rule in rules:
            true_times = self._evaluator.true_at(rule.expression, start, end, step_seconds)
            evaluations = [(when, when in true_times) for when in steps]
            for episode in firing_episodes(rule, step_seconds, evaluations):
                pages.append(Page(rule.name, rule.severity, timedelta(seconds=episode.started_at - start)))
        return sorted(pages, key=lambda page: page.offset)
```

`ReplayAlerts.run` builds the list of evaluation times from `start` to `end` in steps (`range(int((end - start) // step_seconds) + 1)`), asks the evaluator at which of them each rule's expression is true, pairs each step with a boolean, feeds the pairs to `firing_episodes` and turns each episode into a `Page` at its offset. The pages of all rules are sorted by time. The evaluator is a port: in the lab it is a real Prometheus; in a test it is a stub that returns fixed times.

```python
"""The before-and-after comparison the lesson's step 4 asks for."""

from __future__ import annotations

from dataclasses import dataclass

from slo_lab.domain.pages import PageReport


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two alert sets judged against the same planted incidents."""

    before: PageReport
    after: PageReport

    @property
    def fewer_notifications(self) -> bool:
        """True when the second set notified less often."""
        return self.after.total < self.before.total

    @property
    def nothing_real_missed(self) -> bool:
        """True when the second set still alerted on every user-facing incident."""
        return self.after.every_user_facing_incident_detected

    @property
    def improved(self) -> bool:
        """Quieter, more actionable and nothing real missed."""
        return (
            self.fewer_notifications
            and self.nothing_real_missed
            and self.after.actionable_share > self.before.actionable_share
        )
```

`Comparison` holds two `PageReport`s: before and after. `fewer_notifications`, `nothing_real_missed` and `improved` are properties; `improved` requires all three: quieter, nothing real missed, and a better actionable share.

### `infrastructure/settings.py` and `runtime.py`

```python
"""Configuration read once from the environment and validated (twelve-factor)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class SettingsError(ValueError):
    """An environment variable is missing or has an unusable value."""


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError as error:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from error
    if not low <= value <= high:
        raise SettingsError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _flag(env: Mapping[str, str], name: str) -> bool:
    raw = env.get(name, "false").strip().lower()
    if raw not in {"true", "false"}:
        raise SettingsError(f"{name} must be true or false, got {raw!r}")
    return raw == "true"


def _level(env: Mapping[str, str]) -> str:
    level = env.get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise SettingsError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR, got {level!r}")
    return level
```

Settings are read once, at start-up, from **environment variables** (named text values that a process inherits from the program that started it; the twelve-factor way to configure a service). `SettingsError` inherits from `ValueError`. `_number(env, name, default, low, high)` reads `env.get(name)`, uses the default when the variable is missing or empty, converts the text with `float(...)`, turns a conversion failure into a `SettingsError` with `from error` (keeping the cause), and rejects values outside `low <= value <= high` (a chained comparison). `_flag` accepts only `true` or `false` after `.strip().lower()`: a misspelled flag is an error, not a silent "off", which matters for `ALLOW_SIMULATION`. `_level` upper-cases `LOG_LEVEL` and checks it against a set of four names. `Mapping[str, str]` is the type of `os.environ` and of a plain dict, so a test can pass a dict.

```python
@dataclass(frozen=True, slots=True)
class ServiceSettings:
    """What the checkout service reads from its environment."""

    service_name: str
    bind_host: str
    port: int
    allow_simulation: bool
    slow_seconds: float
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ServiceSettings:
        """Build the settings, raising ``SettingsError`` for an invalid variable."""
        return cls(
            service_name=env.get("OTEL_SERVICE_NAME", "checkout"),
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=int(_number(env, "SERVICE_PORT", 8080, 1, 65535)),
            allow_simulation=_flag(env, "ALLOW_SIMULATION"),
            slow_seconds=_number(env, "SLOW_SECONDS", 1.4, 0.0, 30.0),
            log_level=_level(env),
        )


@dataclass(frozen=True, slots=True)
class PagerSettings:
    """What the fake pager reads from its environment."""

    bind_host: str
    port: int
    pages_file: str
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PagerSettings:
        """Build the settings, raising ``SettingsError`` for an invalid variable."""
        return cls(
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=int(_number(env, "PAGER_PORT", 8090, 1, 65535)),
            pages_file=env.get("PAGES_FILE", "/data/pages.jsonl"),
            log_level=_level(env),
        )
```

`ServiceSettings` (service name, bind host, port 8080, whether simulation is allowed, the slow delay of 1.4 seconds, log level) and `PagerSettings` (bind host, port 8090, the file where pages are kept, log level), each with a `from_env` class method.

```python
"""Small runtime adapters: a real delay and a counter-based reference generator."""

from __future__ import annotations

import itertools
import time


class SleepDelay:
    """Waits with ``time.sleep``."""

    def wait(self, seconds: float) -> None:
        """Block for ``seconds``."""
        time.sleep(seconds)


class SequentialReferences:
    """``ord-000001``, ``ord-000002`` and so on; thread-safe because ``itertools.count`` is."""

    def __init__(self) -> None:
        """Start at 1."""
        self._counter = itertools.count(1)

    def next(self) -> str:
        """The next reference."""
        return f"ord-{next(self._counter):06d}"
```

`SleepDelay` waits with `time.sleep`. `SequentialReferences` makes `ord-000001`, `ord-000002`, ... with `itertools.count(1)`, an endless counter; `next(counter)` is safe to call from several threads, and `:06d` pads with zeros to six digits.

## Try it yourself

```bash
.venv/bin/python -c "
from datetime import timedelta
from slo_lab.domain.slo import Slo
from slo_lab.domain.burn_rate import BurnRateRule
slo = Slo('availability', 0.995, 30, 'x', 'g[__WINDOW__]', 'v[__WINDOW__]')
rule = BurnRateRule('page', 14.4, timedelta(hours=1), timedelta(minutes=5))
print(slo.budget_minutes(), slo.budget_requests(500), rule.budget_consumed(slo), rule.error_ratio_threshold(slo), rule.hours_to_exhaustion(slo))"
```

Expected: `216.0000000000002 108000.0000000001 0.02 0.07200000000000006 50.0`. Floating-point numbers carry tiny rounding noise (the budget is 216 minutes, the threshold 0.072); this is why the tests compare with `pytest.approx`.

```bash
.venv/bin/python -c "
from slo_lab.domain.traffic import TrafficProfile, simulate, week_incidents
ticks = list(simulate(TrafficProfile(), week_incidents(), 7, 20261027))
print(len(ticks), sum(t.requests for t in ticks), sum(t.error_requests for t in ticks))"
```

Expected: `20160 5039145 6816`: 20,160 ticks, about 5.04 million requests and 6,816 errors, the same numbers every time (the seed fixes the week).

```bash
.venv/bin/python -c "
from slo_lab.domain.alerts import AlertRule, firing_episodes
rule = AlertRule('A', 'x', 120.0, 'page')
print(firing_episodes(rule, 60.0, [(i * 60.0, f) for i, f in enumerate([0, 1, 1, 1, 1, 0])]))"
```

Expected: one episode starting at 180.0 and ending at 300.0: pending at 60 s, firing after two more minutes.

```bash
.venv/bin/slo-rules --check; echo "exit $?"
```

Expected: `exit 0`: the committed rule files match what the definitions generate.

```bash
.venv/bin/python -m pytest tests/unit/domain tests/unit/application -q
```

Expected: all pass.

## Mistakes people make

- **A mutable default argument.** Writing `def f(items=[])` shares one list between all calls. Use `None` and create the list inside the function. The code in this repository either uses immutable defaults (`()`, `None`, numbers) or `field(default_factory=dict)` in dataclasses.
- **Catching everything.** `except Exception:` hides bugs. This repository catches named errors and lets the rest travel up to one place that logs them and returns a 500 answer.
- **Comparing floats with `==`.** Floating-point numbers carry tiny rounding errors. Tests here compare with ranges or with exact values that are safe (for example `0.25` is exactly representable).
- **Using the wall clock to measure durations.** `time.time()` can jump when the system clock is corrected. Code that measures how long something took uses `time.monotonic()`, which only moves forward.
- **Forgetting that `bool` is an `int`.** In Python `True == 1`. The `OrderId` check rejects booleans on purpose.
- **Installing packages into the system Python.** Use a virtual environment. `make install` creates `.venv` for you.

## Check yourself

1. What is the difference between an image and a container? *An image is the read-only package; a container is a running process started from it.*
2. Why does a Dockerfile copy the dependency file before the source code? *So Docker can reuse the cached install layer when only the source changed.*
3. What happens to files a container writes when the container is removed? *They disappear, unless they were written to a volume or a bind mount.*
4. What does a digest give you that a tag does not? *A tag can be moved to a different image later; a digest always names the same bytes.*
5. Which signal does `docker stop` send first, and what comes after the grace period? *`SIGTERM`; then `SIGKILL`.*

## Further reading

- Python 3.12 documentation: https://docs.python.org/3.12/
- The tutorial: https://docs.python.org/3.12/tutorial/index.html
- The standard library index: https://docs.python.org/3.12/library/index.html
- Type hints (`typing`): https://docs.python.org/3.12/library/typing.html
