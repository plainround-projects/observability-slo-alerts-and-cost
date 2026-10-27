from datetime import timedelta

from slo_lab.domain.traffic import TICK_SECONDS, Incident, TrafficProfile, simulate, week_incidents


def _run(days=1, incidents=(), seed=1, profile=None):
    return list(simulate(profile or TrafficProfile(), tuple(incidents), days, seed))


def test_a_week_has_20160_ticks_of_30_seconds():
    assert TICK_SECONDS == 30
    ticks = _run(days=7)
    assert len(ticks) == 7 * 86400 // 30
    assert ticks[1].offset - ticks[0].offset == timedelta(seconds=30)


def test_the_same_seed_gives_the_same_week_and_another_seed_a_different_one():
    assert _run(seed=3) == _run(seed=3)
    assert _run(seed=3) != _run(seed=4)


def test_ordinary_traffic_is_about_500_requests_a_minute_with_almost_no_errors():
    ticks = _run(days=1, seed=5)
    per_minute = sum(tick.requests for tick in ticks) / (24 * 60)
    errors = sum(tick.error_requests for tick in ticks)
    assert 450 <= per_minute <= 550
    assert errors / sum(tick.requests for tick in ticks) < 0.001
    assert all(tick.health_checks >= 0 for tick in ticks)


def test_a_full_outage_makes_every_request_fail_only_while_it_lasts():
    outage = Incident("outage", timedelta(hours=1), timedelta(minutes=10), True, error_rate=1.0)
    ticks = _run(incidents=[outage], seed=6)
    inside = [t for t in ticks if outage.active_at(t.offset)]
    outside = [t for t in ticks if not outage.active_at(t.offset)]
    assert len(inside) == 20
    assert all(t.ok_requests == 0 and t.error_requests > 0 for t in inside)
    assert sum(t.error_requests for t in outside) < 0.001 * sum(t.requests for t in outside)


def test_a_partial_degradation_fails_about_three_percent():
    degradation = Incident("deg", timedelta(hours=2), timedelta(hours=2), True, error_rate=0.03)
    inside = [t for t in _run(incidents=[degradation], seed=7) if degradation.active_at(t.offset)]
    ratio = sum(t.error_requests for t in inside) / sum(t.requests for t in inside)
    assert 0.025 < ratio < 0.035


def test_a_cpu_spike_changes_cpu_and_nothing_else():
    spike = Incident("spike", timedelta(hours=3), timedelta(minutes=5), False, cpu_percent=95.0)
    ticks = _run(incidents=[spike], seed=8)
    inside = [t for t in ticks if spike.active_at(t.offset)]
    assert all(t.cpu_percent > 85 for t in inside)
    assert sum(t.error_requests for t in inside) <= 2


def test_ordinary_cpu_bursts_exist_but_ordinary_cpu_stays_below_80():
    ticks = _run(days=2, seed=9)
    high = [t for t in ticks if t.cpu_percent > 80]
    assert 0 < len(high) < len(ticks) * 0.1
    assert all(1.0 <= t.cpu_percent <= 100.0 for t in ticks)


def test_the_planted_week_has_the_lessons_three_incidents_and_a_90_second_blip():
    incidents = {incident.name: incident for incident in week_incidents()}
    assert set(incidents) == {"full-outage", "partial-degradation", "cpu-spike-no-impact", "blip-90s"}
    assert incidents["full-outage"].user_facing
    assert incidents["partial-degradation"].error_rate == 0.03
    assert not incidents["cpu-spike-no-impact"].user_facing
    assert incidents["blip-90s"].duration == timedelta(seconds=90)
    assert incidents["full-outage"].end == timedelta(minutes=1810)


def test_small_means_use_the_exact_poisson_sampler():
    profile = TrafficProfile(requests_per_minute=10.0)
    ticks = _run(days=1, profile=profile, seed=10)
    assert 8 <= sum(t.requests for t in ticks) / (24 * 60) <= 12
