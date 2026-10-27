"""A seeded model of a week of traffic for a small service, with planted incidents."""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

TICK_SECONDS = 30
SLOW_THRESHOLD_SECONDS = 0.3


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


def week_incidents() -> tuple[Incident, ...]:
    """The lesson's planted incidents, plus a 90-second blip that the fast-burn page must ignore."""
    return (
        Incident("full-outage", timedelta(minutes=1800), timedelta(minutes=10), True, error_rate=1.0),
        Incident("partial-degradation", timedelta(minutes=4320), timedelta(minutes=120), True, error_rate=0.03),
        Incident("cpu-spike-no-impact", timedelta(minutes=6000), timedelta(minutes=5), False, cpu_percent=95.0),
        Incident("blip-90s", timedelta(minutes=8100), timedelta(seconds=90), False, error_rate=1.0),
    )


def _cpu_burst_starts(rng: random.Random, profile: TrafficProfile, days: int) -> list[timedelta]:
    """Ordinary batch jobs: a few short CPU bursts a day that no user ever notices."""
    starts: list[timedelta] = []
    for day in range(days):
        for _ in range(profile.cpu_bursts_per_day):
            starts.append(timedelta(days=day, minutes=rng.randrange(0, 24 * 60)))
    return starts


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
        yield Tick(
            offset=offset,
            ok_requests=total - errors,
            error_requests=errors,
            slow_requests=slow,
            health_checks=_poisson(rng, profile.health_checks_per_minute * per_tick),
            cpu_percent=min(max(cpu, 1.0), 100.0),
        )
