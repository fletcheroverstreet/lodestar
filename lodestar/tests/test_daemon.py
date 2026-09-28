"""The background loop's scheduling decisions.

The loop itself is a `while True` around two calls and is not worth a test;
WHEN it decides to re-score is, because getting that wrong is silent in both
directions. Too eager and the machine scores 500 names continuously for no
benefit; too reluctant and the hub shows live prices beside rankings from
whenever someone last remembered to run the command — which is the state this
whole module exists to remove.
"""

from __future__ import annotations

import datetime as dt

import pytest

from lodestar import daemon, persistence


def _runs_db(tmp_path, *, created_at: str | None):
    """A runs database whose newest run finished at `created_at`."""
    path = tmp_path / "runs.db"
    with persistence.connect(path) as conn:
        if created_at is not None:
            conn.execute(
                "INSERT INTO runs (run_id, as_of, config_hash, universe_size, "
                "universe_source, created_at) VALUES (?,?,?,?,?,?)",
                ("r1", created_at[:10], "h", 1, "test", created_at))
        conn.commit()
    return path


def test_a_cache_that_has_never_been_scored_is_due_immediately():
    """Whatever the hour. A freshly built cache with no run shows an empty
    hub, and waiting until tonight to fix that would be obtuse."""
    import tempfile
    from pathlib import Path

    path = _runs_db(Path(tempfile.mkdtemp()), created_at=None)
    for hour in (0, 9, 23):
        assert daemon.rescore_is_due(path, now=dt.datetime(2026, 8, 11, hour))


def test_a_fresh_run_is_not_re_scored(tmp_path):
    """Scoring 500 names is minutes of solid CPU, and the inputs it depends on
    — filed fundamentals — change when a company files, not when a price
    moves. The UI re-prices what it reads, so a score from this morning is
    already being shown against this minute's price."""
    recent = (dt.datetime(2026, 8, 11, 20)
              - dt.timedelta(hours=2)).isoformat()
    path = _runs_db(tmp_path, created_at=recent)
    assert not daemon.rescore_is_due(path, now=dt.datetime(2026, 8, 11, 20))


def test_a_stale_run_waits_for_the_close(tmp_path):
    """US equities close at 16:00 ET. Scoring against the day's final closes
    is worth more than scoring against a partial session, so a due re-score
    still waits rather than firing at whatever time the machine booted."""
    stale = (dt.datetime(2026, 8, 11) - dt.timedelta(days=2)).isoformat()
    path = _runs_db(tmp_path, created_at=stale)

    assert not daemon.rescore_is_due(path, now=dt.datetime(2026, 8, 11, 10))
    assert daemon.rescore_is_due(path, now=dt.datetime(2026, 8, 11, 19))


def test_the_age_threshold_is_under_a_full_day(tmp_path):
    """A threshold of exactly 24h drifts: each run starts a little later than
    the last, and after a few days the nightly score has walked into the
    following morning and stops happening at all on the intended day."""
    assert daemon.RESCORE_AFTER_HOURS < 24
    assert daemon.RESCORE_AFTER_HOURS >= 12, (
        "below half a day the loop would re-score twice in one evening")


def test_a_missing_runs_database_is_due_not_a_crash(tmp_path):
    assert daemon.newest_run_age_hours(tmp_path / "nope.db") is None
    assert daemon.rescore_is_due(tmp_path / "nope.db")


def test_the_daemon_reaches_finlake_only_through_the_adapter():
    """`adapter.py` is the one module allowed to import finlake — that
    boundary is what lets the data layer be upgraded and re-verified in one
    place. A background loop is exactly the kind of code that quietly reaches
    past it for convenience."""
    source = (daemon.__file__)
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "import finlake" not in text
    assert "from finlake" not in text
    assert "from .adapter import" in text


def test_the_injected_clock_governs_the_age_too(tmp_path):
    """A HALF-APPLIED INJECTION IS NOT AN INJECTION. `rescore_is_due` took a
    `now` and used it only for the hour-of-day check; the AGE was still
    measured against wall time. So a test could pin the hour and not the
    calendar, and `test_a_fresh_run_is_not_re_scored` passed on the day it was
    written and began failing two days later — reporting a defect in the
    scheduler that did not exist, while the one that did went unnoticed.
    """
    scored_at = dt.datetime(2026, 8, 11, 18)
    path = _runs_db(tmp_path, created_at=scored_at.isoformat())

    two_hours_later = scored_at + dt.timedelta(hours=2)
    assert daemon.newest_run_age_hours(path, now=two_hours_later) == pytest.approx(2.0)
    assert not daemon.rescore_is_due(path, now=two_hours_later)

    # 26 hours on: past the age threshold, and after the close on the next
    # day, so both conditions are satisfied by the injected clock alone.
    much_later = scored_at + dt.timedelta(hours=26)
    assert daemon.newest_run_age_hours(path, now=much_later) == pytest.approx(26.0)
    assert daemon.rescore_is_due(path, now=much_later)


def test_the_tier_report_carries_the_coverage_note():
    """The daemon's per-tier line is now the ONLY thing that says a data
    source has stopped answering — the provider's own 404 narration is
    suppressed. A callback that cannot accept the note would be swallowed by
    the refresh loop's error guard and the line would silently vanish."""
    import inspect

    src = inspect.getsource(daemon.daemon)
    assert "def _report(name, rows, seconds, note=None)" in src, (
        "the tier callback no longer accepts the coverage note")
