"""Tests for lodestar.universe.

Universe construction IS the integration point with finlake's point-in-time
guarantees, so these exercise it end to end through a real finlake cache
rather than mocking the adapter — a mock would test nothing real here.

The cache is BUILT BY THE TEST, though, and that is a change. These
previously ran against whatever happened to be in `~/.finlake`, asserting on
its contents ("MU is present", "at least 40 companies"), which made them pass
or fail on the state of one machine and gave the suite a live handle on the
user's production data. The finlake suite then destroyed 15.5 million facts
through exactly that handle — see finlake/conftest.py.

A five-company fixture proves the same properties and cannot touch anything.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lodestar.config import load_config
from lodestar.universe import _read_constituents, build_universe

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CONFIG = REPO_ROOT / "config.yaml"

# (ticker, cik, first_filed, last_filed). First-filed dates are what the
# point-in-time bound is tested against, so they straddle useful boundaries:
# nothing exists in 1990, everything exists by 2015.
FIXTURE_COMPANIES = [
    ("AAA", 101, "1996-03-01", "2026-07-01"),
    ("BBB", 102, "2004-06-15", "2026-07-15"),
    ("CCC", 103, "2011-01-20", "2026-06-30"),
    ("DDD", 104, "2014-11-05", "2026-08-01"),
    ("EEE", 105, "2019-02-11", "2026-07-20"),
]


@pytest.fixture(autouse=True)
def finlake_cache():
    """A small finlake cache, in the isolated FINLAKE_HOME conftest set up.

    Autouse so no test in this module can accidentally read a cache it did
    not build.
    """
    from finlake import store

    conn = store.connect()
    store.init_db(conn)
    conn.executescript(
        "DELETE FROM securities; DELETE FROM ticker_map; DELETE FROM facts;")
    for ticker, cik, first, last in FIXTURE_COMPANIES:
        conn.execute(
            "INSERT OR REPLACE INTO securities "
            "(cik, name, sic, sic_desc, first_filed, last_filed) "
            "VALUES (?,?,?,?,?,?)",
            (cik, f"{ticker} Corp", "3674", "Semiconductors", first, last))
        conn.execute(
            "INSERT OR REPLACE INTO ticker_map "
            "(ticker, cik, exchange, valid_from, valid_to) VALUES (?,?,?,?,?)",
            (ticker, cik, "NASDAQ", first, None))
    conn.commit()
    conn.close()
    yield


def _finlake_only(cfg):
    """The repo config ships a constituents file, which REPLACES finlake's own
    universe. These two tests are about that underlying universe, so they
    clear it rather than testing the override by accident."""
    return dataclasses.replace(cfg, universe_constituents_file=None)


def test_universe_as_of_today_returns_the_built_companies():
    cfg = _finlake_only(load_config(REPO_CONFIG))
    result = build_universe(cfg, as_of="2026-08-08")
    assert result.tickers == [t for t, _c, _f, _l in FIXTURE_COMPANIES]
    assert result.tickers == sorted(result.tickers), "tickers should be sorted"
    assert result.sic_by_ticker["AAA"] == ("3674", "Semiconductors")


def test_point_in_time_a_1990_asof_excludes_every_currently_built_company():
    """The point-in-time guarantee, exercised end-to-end through lodestar's
    own universe construction, not just finlake's internal tests. Every
    company in the fixture first filed with the SEC well after 1990 (XBRL
    itself didn't exist before 2009), so a 1990 as-of date must return an
    empty universe."""
    cfg = load_config(REPO_CONFIG)
    result = build_universe(cfg, as_of="1990-01-01")
    assert result.tickers == [], (
        f"a 1990 as-of date leaked companies that couldn't have been "
        f"listed yet: {result.tickers}"
    )


def test_an_intermediate_asof_includes_only_what_had_filed_by_then():
    """The bound is a real filter, not just an all-or-nothing epoch check.
    Three of the five had filed by the start of 2012; two had not."""
    cfg = _finlake_only(load_config(REPO_CONFIG))
    result = build_universe(cfg, as_of="2012-01-01")
    assert result.tickers == ["AAA", "BBB", "CCC"]


def test_constituents_file_replaces_finlake_universe_entirely(tmp_path):
    """When a constituents file is set, it REPLACES finlake's proxy
    universe rather than filtering it -- even for a ticker finlake has
    never heard of, the constituents file wins."""
    constituents = tmp_path / "constituents.csv"
    constituents.write_text("ticker\nAAA\nCCC\nNOTAREALCOMPANYXYZ\n")

    cfg = load_config(REPO_CONFIG)
    cfg = dataclasses.replace(cfg, universe_constituents_file=constituents)
    result = build_universe(cfg, as_of="2026-08-08")
    assert result.tickers == ["AAA", "CCC", "NOTAREALCOMPANYXYZ"]
    assert result.source.startswith("constituents file")
    # finlake's own universe is still recorded alongside, for comparison.
    assert result.finlake_universe_size == len(FIXTURE_COMPANIES)


def test_a_constituents_file_cannot_reintroduce_lookahead(tmp_path):
    """A constituent list is a CURRENT-membership snapshot with no add/drop
    dates, so honouring it unbounded puts companies into a 2012 screen that
    IPO'd years later. Membership comes from the file; existence does not."""
    constituents = tmp_path / "constituents.csv"
    constituents.write_text("ticker\nAAA\nEEE\n")

    cfg = load_config(REPO_CONFIG)
    cfg = dataclasses.replace(cfg, universe_constituents_file=constituents)
    result = build_universe(cfg, as_of="2012-01-01")
    assert result.tickers == ["AAA"], (
        "EEE first filed in 2019 and cannot appear in a 2012 screen, "
        "whatever a current constituent list says")


def test_read_constituents_requires_ticker_column(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("symbol\nMU\n")
    with pytest.raises(ValueError, match="ticker"):
        _read_constituents(bad)


def test_read_constituents_dedupes_and_uppercases(tmp_path):
    p = tmp_path / "constituents.csv"
    p.write_text("ticker\nmu\nMU\nnvda\n")
    assert _read_constituents(p) == ["MU", "NVDA"]
