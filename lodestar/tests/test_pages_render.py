"""Every page in the navigation actually renders.

WHY THIS EXISTS. `app.py` dispatches "How it works" to
`lodestar.ui.views.methodology`, and that module did not exist. The import
raised, `app.py`'s contained-error handler caught it, and the page showed a
red box — in the navigation, reachable, and broken, with nothing failing
anywhere else. Nothing in the suite touched the router, so nothing noticed.

The contained-error handler is right: one page failing should not take the hub
down. But it also means a broken page is invisible to everything except a
human opening it, which is exactly the kind of thing a test should do instead.

Each page is rendered against a tiny purpose-built run, so this is fast and
does not depend on the user's real cache — see conftest.py for why that
matters more than usual here.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "lodestar" / "ui" / "app.py"

# Every label in app.py's PAGES dict. Kept as a literal rather than imported,
# so adding a page to the router without adding it here is caught by the
# completeness test below rather than silently skipped.
PAGES = [
    "Overview", "Company", "Screener", "Buy / sell ratings", "Compare",
    "News", "Macro", "How it works", "Data health",
]


def test_every_page_in_the_router_is_covered_here():
    """The list above must not drift from the router's own."""
    source = APP.read_text(encoding="utf-8")
    for label in PAGES:
        assert f'"{label}"' in source, f"{label!r} is no longer a page"
    # And nothing in the router is missing from the list.
    start = source.index("PAGES: dict[str, str] = {")
    block = source[start:source.index("}", start)]
    declared = [line.split('"')[1] for line in block.splitlines()
                if line.strip().startswith('"')]
    assert sorted(declared) == sorted(PAGES), (
        f"the router declares {sorted(declared)}, this test covers "
        f"{sorted(PAGES)} — a page was added without test coverage")


@pytest.fixture(scope="module")
def runs_db(tmp_path_factory) -> str:
    """A minimal but structurally complete runs database.

    Two names so peer statistics, ratings and the diff between runs all have
    something to work on; the pages are being checked for *rendering*, not for
    numerical output, which every other test file covers.
    """
    from lodestar import persistence

    path = tmp_path_factory.mktemp("runs") / "lodestar.db"
    with persistence.connect(path) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, as_of, config_hash, universe_size, "
            "universe_source, created_at) VALUES (?,?,?,?,?,?)",
            ("2026-08-10__testhash", "2026-08-10", "testhash", 2,
             "test fixture", "2026-08-10T00:00:00"))
        for ticker, sector, industry, raw, pct in (
                ("AAA", "Information Technology", "Software", 0.9, 90.0),
                ("BBB", "Information Technology", "Software", -0.4, 10.0)):
            conn.execute(
                "INSERT INTO run_composite (run_id, ticker, sector, industry, "
                "composite_raw, percentile, data_as_of) VALUES (?,?,?,?,?,?,?)",
                ("2026-08-10__testhash", ticker, sector, industry, raw, pct,
                 "2026-06-30"))
            for bucket in ("quality", "value", "growth", "capital_allocation",
                           "accounting_quality", "momentum", "news"):
                conn.execute(
                    "INSERT INTO run_buckets (run_id, ticker, bucket, "
                    "subscore, coverage, low_confidence, weight_configured, "
                    "weight_used, z_universe, composite_contribution, "
                    "was_capped) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("2026-08-10__testhash", ticker, bucket, raw, 1.0, 0,
                     0.14, 0.14, raw, raw * 0.14, 0))
        conn.commit()
    return str(path)


@pytest.fixture
def fixture_db(runs_db, monkeypatch, tmp_path):
    """Point app.py at the fixture database, and away from the real one.

    `app.args = ["--db", ...]` LOOKS like the way to do this and is not.
    `AppTest.args` is the argument tuple for scripts built with
    `AppTest.from_function`; `from_file` ignores it entirely. `app.py` reads
    `--db` from `sys.argv`, never saw it, and fell back to
    `persistence.DEFAULT_DB_PATH` - the developer's REAL runs database. So
    every test in this file rendered live data: they passed on the machine
    that had a scoring run on disk, and failed on a clean checkout that has
    none.

    `DEFAULT_DB_PATH` is redirected as well, so a future regression in how
    `--db` reaches the app renders an empty database and fails
    `test_the_pages_read_the_fixture_not_the_real_database` loudly, rather
    than quietly reading - and opening for writing - the real one again.
    """
    from lodestar import persistence

    monkeypatch.setattr(sys, "argv", [str(APP), "--db", runs_db])
    monkeypatch.setattr(persistence, "DEFAULT_DB_PATH",
                        tmp_path / "must-not-be-read.db")
    return runs_db


def test_the_pages_read_the_fixture_not_the_real_database(fixture_db):
    """The run's as-of date appears in every page header, and the fixture's
    is 2026-08-10 - a date no real run in this project carries."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=120)
    app.query_params["page"] = "Overview"
    app.run()

    assert not app.exception
    markup = "".join(m.value for m in app.markdown)
    assert "2026-08-10" in markup, (
        "the app did not render the fixture run - it read some other "
        "database")


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_error(page, fixture_db):
    """A page must render. An empty page is fine — several legitimately show
    'no data cached' against a fixture — but an EXCEPTION is not, and neither
    is `app.py`'s contained-error box, which is how a missing module surfaced.

    Navigated by QUERY PARAM, because that is now how the product navigates:
    the section bar is a row of links, so a click is a new query string
    rather than a widget change.
    """
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=120)
    app.query_params["page"] = page
    app.run()

    assert not app.exception, (
        f"{page!r} raised: {[str(e.value)[:300] for e in app.exception]}")
    errors = [e.value for e in app.error]
    hit = [e for e in errors if "hit an error" in e or "ModuleNotFound" in e]
    assert not hit, f"{page!r} rendered app.py's contained-error box: {hit}"


def test_the_section_bar_marks_exactly_one_tab_active(fixture_db):
    """The bar is hand-rendered HTML rather than a widget, so nothing else
    guarantees the active state tracks the page actually being shown."""
    from streamlit.testing.v1 import AppTest

    for page in ("Overview", "Screener", "Macro"):
        app = AppTest.from_file(str(APP), default_timeout=120)
        app.query_params["page"] = page
        app.run()

        markup = "".join(m.value for m in app.markdown)
        # The RENDERED anchor, not the CSS selector — the injected stylesheet
        # legitimately mentions the class several times.
        rendered = 'class="ls-tab ls-tab-active"'
        assert markup.count(rendered) == 1, (
            f"{page!r} highlighted {markup.count(rendered)} tabs")
        active = markup.split(rendered)[1]
        assert page in active[:400], f"the wrong tab is marked for {page!r}"


def test_an_unknown_page_falls_back_to_overview(fixture_db):
    """A stale bookmark or a hand-edited URL must land somewhere real."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=120)
    app.query_params["page"] = "Nonsense"
    app.run()
    assert not app.exception
    markup = "".join(m.value for m in app.markdown)
    assert "ls-tab-active" in markup
