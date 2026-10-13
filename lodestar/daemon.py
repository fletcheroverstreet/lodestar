"""One background loop that keeps the whole hub current.

    python -m lodestar daemon

WHY THIS EXISTS. Keeping the hub up to date took two separate things, and only
one of them was automated. `python -m finlake refresh --daemon` kept the DATA
current — quotes, news, filings, macro — but nothing at all triggered
`python -m lodestar run`, so the composite scores, the buy/sell ratings and
the whole screener quietly aged while the prices beside them stayed live. The
hub looked current and its rankings were from whenever someone last remembered
to re-score.

So there is one command, and it does both.

WHAT IT DOES NOT DO IS RE-SCORE ON EVERY TICK. Scoring 500 names takes several
minutes of solid CPU; running it continuously would heat the laptop for no
benefit, because the inputs a score depends on — filed fundamentals — change
when a company files, not when a price moves. Prices refresh every few minutes
and the UI re-prices what it reads, so a score that is a day old is still
being displayed against this minute's price. The re-score exists to fold in
new FILINGS, and once a day after the close is the right cadence for that.

DIRECTION OF DEPENDENCY IS PRESERVED. This drives finlake through
`adapter.py`, the one module allowed to import it, and through finlake's
public surface rather than its internals. finlake knows nothing about
lodestar, which is what lets the data layer be upgraded on its own.
"""

from __future__ import annotations

import datetime as dt
import time
import traceback
from pathlib import Path

from . import persistence
from .adapter import (
    data_source_status, data_universe_status, refresh_data_sources,
)

# How often the loop wakes up. Polling rather than sleeping until the next
# deadline, for the same reason finlake's own loop does: a laptop suspends,
# the clock jumps, and a computed sleep overshoots by however long the lid was
# closed. Checking cheaply is correct across suspend and costs nothing.
POLL_SECONDS = 60

# A re-score is due when the newest run is older than this. One day: the score
# depends on filed fundamentals, which change when a company files.
RESCORE_AFTER_HOURS = 20

# Local hour after which a re-score may start. US equities close at 16:00 ET,
# and scoring against the day's final closes is worth more than scoring
# against a partial session — so the nightly run waits for the day to finish
# rather than firing at whatever time the machine happened to boot.
RESCORE_NOT_BEFORE_HOUR = 18


def _log(message: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def newest_run_age_hours(db_path: Path, *,
                         now: dt.datetime | None = None) -> float | None:
    """How long since the last scoring run finished, or None if never.

    `now` is injectable for the same reason `rescore_is_due` takes one, and
    passing it there without passing it here was a half-applied injection: the
    caller pinned the hour-of-day check to a fixed clock while the AGE was
    still measured against wall time. A test written that way passes on the
    day it is written and starts failing once the calendar moves past its
    hard-coded date — which is what happened, two days later, and it says
    nothing about the code under test.
    """
    if not Path(db_path).exists():
        return None
    try:
        with persistence.connect(Path(db_path)) as conn:
            runs = persistence.list_runs(conn)
    except Exception:
        return None
    if not runs:
        return None
    stamps = []
    for run in runs:
        raw = getattr(run, "created_at", None) or getattr(run, "as_of", None)
        if not raw:
            continue
        try:
            stamps.append(dt.datetime.fromisoformat(str(raw)))
        except ValueError:
            continue
    if not stamps:
        return None
    return ((now or dt.datetime.now()) - max(stamps)).total_seconds() / 3600.0


def rescore_is_due(db_path: Path, *, now: dt.datetime | None = None) -> bool:
    """Whether the universe should be re-scored on this tick.

    Never scored at all counts as due immediately, whatever the hour — a
    freshly built cache with no run shows an empty hub, and waiting until
    tonight to fix that would be obtuse.
    """
    now = now or dt.datetime.now()
    age = newest_run_age_hours(db_path, now=now)
    if age is None:
        return True
    if age < RESCORE_AFTER_HOURS:
        return False
    return now.hour >= RESCORE_NOT_BEFORE_HOUR


def run_scoring(db_path: Path | None = None, *, config: str | None = None) -> bool:
    """Re-score the universe in-process. True if it completed."""
    from .cli import cmd_run

    class _Args:
        pass

    args = _Args()
    args.as_of = None
    args.config = config
    args.db = str(db_path) if db_path else None
    args.csv = None
    try:
        cmd_run(args)
        return True
    except Exception:
        traceback.print_exc()
        return False


def daemon(*, db_path: Path | None = None, config: str | None = None,
           poll_seconds: int = POLL_SECONDS,
           limit: int | None = None) -> None:
    """Run until interrupted: refresh what is due, re-score once a day."""
    db_path = Path(db_path) if db_path else persistence.DEFAULT_DB_PATH

    _log("lodestar daemon started. Ctrl-C to stop.")
    universe = data_universe_status()
    if universe:
        _log(f"  universe   {universe['count']} symbols — {universe['source']}")
    for row in data_source_status():
        _log(f"  {row['task']:<10} every {row['every']:>5}  — {row['note']}")
    _log(f"  {'re-score':<10} every {RESCORE_AFTER_HOURS}h, "
         f"not before {RESCORE_NOT_BEFORE_HOUR}:00 local")
    age = newest_run_age_hours(db_path)
    _log(f"  newest run: "
         f"{'never scored' if age is None else f'{age:.1f}h old'}")

    while True:
        try:
            # Say what is ABOUT to run, not only what finished. The first pass
            # after a break is a catch-up across every tier — a news sweep
            # alone is several minutes over 500 names — and a loop that prints
            # nothing for half an hour is indistinguishable from a hung one.
            due = [row["task"] for row in data_source_status() if row["due"]]
            if due:
                _log(f"{len(due)} due: {', '.join(due)} ...")

            # Report each tier AS IT LANDS. Reporting only at the end of the
            # pass meant a long task left the loop silent for as long as it
            # took, which is indistinguishable from hung — and the news
            # sweep used to take half an hour.
            #
            # The note is the tier's own coverage line ("486 loaded, 3 no
            # coverage"). It replaced several thousand lines of raw provider
            # 404 output, and it is the thing that makes a source which has
            # stopped answering obvious rather than silent.
            def _report(name, rows, seconds, note=None):
                line = f"  {name}: {rows} in {seconds:.0f}s"
                if note:
                    line += f"  — {note}"
                _log(line)

            results = refresh_data_sources(limit=limit, on_task=_report)
            touched = {k: v for k, v in results.items()
                       if isinstance(v, int) and v}
            if not touched and due:
                _log("refresh pass finished; nothing new.")

            if rescore_is_due(db_path):
                _log("re-scoring the universe...")
                if run_scoring(db_path, config=config):
                    _log("re-score complete.")
                else:
                    # Logged, not raised. A failed score must not stop the
                    # price refresh — a hub with yesterday's rankings and
                    # today's prices is far more useful than a stopped one.
                    _log("re-score FAILED; prices keep refreshing.")
        except KeyboardInterrupt:
            _log("stopped.")
            return
        except Exception:
            # The loop itself must survive anything the tasks did not catch.
            # A daemon that dies overnight is indistinguishable from one that
            # was never started, which is the failure mode this whole command
            # exists to remove.
            traceback.print_exc()

        try:
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            _log("stopped.")
            return
