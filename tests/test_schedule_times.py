"""C2 — when a schedule runs next: pure functions of the schedule and a time.

Every instant below was checked against zoneinfo. Nothing here may read the
system clock or the machine's own zone.
"""

import pytest

from schedule_support import hourly, schedule, utc


def next_after(block, after, anchor=None):
    from conclave.application.schedules import next_time

    return next_time(block, after, anchor)


def test_an_interval_counts_from_its_anchor():
    anchor = utc(2026, 11, 16, 9, 0)
    assert next_after(hourly(), utc(2026, 11, 16, 10, 30), anchor) == utc(2026, 11, 16, 11, 0)
    assert next_after(hourly(), utc(2026, 11, 16, 11, 0), anchor) == utc(2026, 11, 16, 12, 0), "strictly after"
    assert next_after(hourly(), utc(2026, 11, 16, 8, 0), anchor) == utc(2026, 11, 16, 9, 0), "the anchor is the first time"


def test_an_interval_is_elapsed_time_across_a_clock_change():
    anchor = utc(2026, 3, 28, 23, 0)
    times, after = [], utc(2026, 3, 28, 23, 30)
    for _ in range(4):
        after = next_after(hourly(time_zone="Europe/London"), after, anchor)
        times.append(after)
    assert times == [utc(2026, 3, 29, 0, 0), utc(2026, 3, 29, 1, 0), utc(2026, 3, 29, 2, 0), utc(2026, 3, 29, 3, 0)]


def test_an_interval_starts_at_its_stated_local_start():
    block = hourly(starts_at="2026-11-17T09:00")
    assert next_after(block, utc(2026, 11, 16, 12, 0), utc(2026, 11, 1, 0, 0)) == utc(2026, 11, 17, 9, 0)
    assert next_after(block, utc(2026, 11, 17, 9, 30), utc(2026, 11, 1, 0, 0)) == utc(2026, 11, 17, 10, 0)


def test_daily_times_in_order():
    block = schedule(kind="daily", days=None, times=["09:00", "17:30"])
    assert next_after(block, utc(2026, 11, 16, 12, 0)) == utc(2026, 11, 16, 17, 30)
    assert next_after(block, utc(2026, 11, 16, 17, 30)) == utc(2026, 11, 17, 9, 0)


def test_weekly_days_and_the_wrap_to_next_week():
    block = schedule()  # Mon, Wed, Fri at 09:00 and 17:30, Europe/London
    assert next_after(block, utc(2026, 11, 16, 17, 31)) == utc(2026, 11, 18, 9, 0)
    assert next_after(block, utc(2026, 11, 20, 18, 0)) == utc(2026, 11, 23, 9, 0)
    summer = schedule(days=["mon"], times=["09:00"])
    assert next_after(summer, utc(2026, 7, 1, 0, 0)) == utc(2026, 7, 6, 8, 0), "09:00 BST is 08:00 UTC"


def test_nothing_runs_before_its_start():
    block = schedule(kind="daily", days=None, times=["09:00"], starts_at="2026-12-01T00:00")
    assert next_after(block, utc(2026, 11, 16, 12, 0)) == utc(2026, 12, 1, 9, 0)


@pytest.mark.parametrize("zone, local, day, expected", [
    ("Europe/London", "01:30", (2026, 3, 28, 12, 0), utc(2026, 3, 29, 1, 0)),
    ("America/New_York", "02:30", (2026, 3, 7, 12, 0), utc(2026, 3, 8, 7, 0)),
    ("Australia/Sydney", "02:15", (2026, 10, 3, 0, 0), utc(2026, 10, 3, 16, 0)),
])
def test_a_time_the_clocks_skip_runs_at_the_first_minute_after_it(zone, local, day, expected):
    block = schedule(kind="daily", days=None, times=[local], time_zone=zone)
    assert next_after(block, utc(*day)) == expected


@pytest.mark.parametrize("zone, local, day, first, following", [
    ("Europe/London", "01:30", (2026, 10, 24, 12, 0), utc(2026, 10, 25, 0, 30), utc(2026, 10, 26, 1, 30)),
    ("America/New_York", "01:30", (2026, 10, 31, 12, 0), utc(2026, 11, 1, 5, 30), utc(2026, 11, 2, 6, 30)),
    ("Australia/Sydney", "02:30", (2026, 4, 4, 0, 0), utc(2026, 4, 4, 15, 30), utc(2026, 4, 5, 16, 30)),
])
def test_a_time_the_clocks_repeat_runs_once_at_its_first_occurrence(zone, local, day, first, following):
    block = schedule(kind="daily", days=None, times=[local], time_zone=zone)
    assert next_after(block, utc(*day)) == first
    assert next_after(block, first) == following, "the second occurrence is not a second run"


def test_the_times_in_a_window_are_counted_and_capped():
    from conclave.application.schedules import due_between

    anchor = utc(2026, 11, 16, 9, 0)
    assert due_between(hourly(), utc(2026, 11, 16, 9, 0), utc(2026, 11, 16, 12, 0), anchor) == [
        utc(2026, 11, 16, 10, 0), utc(2026, 11, 16, 11, 0), utc(2026, 11, 16, 12, 0)]
    five_minutes = hourly(every_minutes=5)
    assert len(due_between(five_minutes, anchor, utc(2027, 11, 16, 9, 0), anchor, limit=50)) == 50


def test_the_time_functions_never_read_the_clock(monkeypatch):
    import datetime as real
    from conclave.application import schedules as module

    class NoClock(real.datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("next_time read the clock")

        @classmethod
        def utcnow(cls):
            raise AssertionError("next_time read the clock")

    monkeypatch.setattr(module, "datetime", NoClock)
    assert next_after(schedule(), utc(2026, 11, 16, 12, 0)) == utc(2026, 11, 16, 17, 30)
    assert next_after(hourly(), utc(2026, 11, 16, 12, 0), utc(2026, 11, 16, 9, 0)) == utc(2026, 11, 16, 13, 0)


def test_a_fingerprint_changes_with_timing_not_with_the_request():
    from conclave.application.schedules import timing_fingerprint

    base = timing_fingerprint(schedule())
    assert timing_fingerprint(schedule(request={"task": "Other", "inputs": {"team": "x"}})) == base
    assert timing_fingerprint(schedule(catch_up="latest")) == base, "catch-up changes what is done with a miss, not when"
    for change in ({"times": ["10:00"]}, {"days": ["tue"]}, {"time_zone": "Europe/Paris"}, {"starts_at": "2026-12-01T00:00"}):
        assert timing_fingerprint(schedule(**change)) != base, change
