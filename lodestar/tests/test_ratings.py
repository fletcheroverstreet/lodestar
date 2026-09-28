"""Tests for buy/sell ratings at all three levels: stock, industry, sector."""

from __future__ import annotations

import pandas as pd
import pytest

from lodestar.scoring.ratings import (
    NO_RATING, band_for, groups_to_frame, names_to_frame, rate_industries,
    rate_names, rate_sectors,
)


def _table(rows):
    return pd.DataFrame(rows)


def _row(ticker, industry, sector, score, percentile=None):
    return {"ticker": ticker, "industry": industry, "sector": sector,
            "composite_raw": score, "percentile": percentile}


# ------------------------------------------------------------------- bands
@pytest.mark.parametrize("score,expected", [
    (1.5, "STRONG BUY"),
    (0.75, "STRONG BUY"),   # boundary: inclusive
    (0.74, "BUY"),
    (0.25, "BUY"),          # boundary: inclusive
    (0.24, "HOLD"),
    (0.0, "HOLD"),
    (-0.25, "HOLD"),        # boundary: inclusive
    (-0.26, "SELL"),
    (-0.75, "SELL"),        # boundary: inclusive
    (-0.76, "STRONG SELL"),
    (-5.0, "STRONG SELL"),
])
def test_rating_bands_at_boundaries(score, expected):
    assert band_for(score) == expected


def test_band_for_none_is_no_rating():
    assert band_for(None) == NO_RATING
    assert band_for(float("nan")) == NO_RATING


# -------------------------------------------------------------- per stock
def test_name_rating_absolute_and_peer_can_disagree():
    """The interesting case the two-level system exists to surface: a
    name that is mediocre against the whole universe but the BEST in its
    own (weak) industry. Absolute rating and peer rating must disagree,
    not be collapsed into one number."""
    table = _table([
        _row("BEST_OF_BAD", "WeakIndustry", "X", -0.30),
        _row("BAD1", "WeakIndustry", "X", -1.20),
        _row("BAD2", "WeakIndustry", "X", -1.30),
    ])
    ratings = {r.ticker: r for r in rate_names(table)}
    best = ratings["BEST_OF_BAD"]
    # Against the universe: -0.30 is a SELL band.
    assert best.rating == "SELL"
    # Against its own peers: it's well above the industry mean -> BUY-ish.
    assert best.peer_z > 0
    assert best.peer_rating in ("BUY", "STRONG BUY")
    assert best.peer_rank == 1
    assert best.peer_count == 3


def test_name_rating_peer_rank_ordering():
    table = _table([
        _row("A", "Semis", "Tech", 1.0),
        _row("B", "Semis", "Tech", 0.5),
        _row("C", "Semis", "Tech", -0.5),
    ])
    ratings = {r.ticker: r for r in rate_names(table)}
    assert ratings["A"].peer_rank == 1
    assert ratings["B"].peer_rank == 2
    assert ratings["C"].peer_rank == 3


def test_name_rating_unscored_gets_no_rating():
    table = _table([
        _row("A", "Semis", "Tech", 1.0),
        _row("B", "Semis", "Tech", 0.5),
        _row("NODATA", "Semis", "Tech", None),
    ])
    ratings = {r.ticker: r for r in rate_names(table)}
    assert ratings["NODATA"].rating == NO_RATING
    assert ratings["NODATA"].peer_rating == NO_RATING
    assert ratings["NODATA"].score is None


def test_name_rating_single_member_industry_has_no_peer_z():
    """A peer z-score against zero peers is meaningless -- must be None,
    not a fabricated 0.0 that would read as a neutral HOLD."""
    table = _table([_row("ALONE", "Lonely", "X", 1.0)])
    r = rate_names(table)[0]
    assert r.peer_z is None
    assert r.peer_rating == NO_RATING
    assert r.rating == "STRONG BUY"  # absolute rating still works


def test_names_sorted_best_first():
    table = _table([
        _row("LOW", "I", "X", -1.0),
        _row("HIGH", "I", "X", 2.0),
        _row("MID", "I", "X", 0.0),
    ])
    assert [r.ticker for r in rate_names(table)] == ["HIGH", "MID", "LOW"]


# --------------------------------------------------------- industry/sector
def test_industry_rating_hand_computed():
    table = _table([
        _row("A", "Semis", "Tech", 1.0),
        _row("B", "Semis", "Tech", 0.5),
        _row("C", "Semis", "Tech", 0.6),
    ])
    r = rate_industries(table, as_of="2026-08-09")[0]
    # mean = (1.0+0.5+0.6)/3 = 0.70 -> BUY (>= 0.25, < 0.75)
    assert abs(r.mean_score - 0.70) < 1e-9
    assert r.rating == "BUY"
    assert r.median_score == 0.6
    assert r.best_ticker == "A"
    assert r.worst_ticker == "B"
    assert r.parent == "Tech"
    assert r.level == "industry"


def test_sector_rating_aggregates_across_industries():
    table = _table([
        _row("A", "Semis", "Tech", 1.0),
        _row("B", "Software", "Tech", 1.0),
        _row("C", "Banks", "Financials", -1.0),
        _row("D", "Brokers", "Financials", -1.0),
    ])
    sectors = {r.group: r for r in rate_sectors(table, as_of="2026-08-09")}
    assert sectors["Tech"].rating == "STRONG BUY"
    assert sectors["Financials"].rating == "STRONG SELL"
    assert sectors["Tech"].level == "sector"
    assert sectors["Tech"].scored_count == 2


def test_group_buy_sell_counts():
    """A group's rating is its mean, but the buy/sell member counts show
    whether that mean is a consensus or an average of extremes."""
    table = _table([
        _row("A", "Mixed", "X", 2.0),    # STRONG BUY
        _row("B", "Mixed", "X", -2.0),   # STRONG SELL
        _row("C", "Mixed", "X", 0.0),    # HOLD
    ])
    r = rate_industries(table, as_of="2026-08-09")[0]
    assert r.rating == "HOLD"       # mean is 0.0
    assert r.buy_count == 1
    assert r.sell_count == 1
    assert r.dispersion > 1.0       # but the spread is enormous


def test_group_with_too_few_scored_members_gets_no_rating():
    table = _table([
        _row("A", "Lonely", "X", 2.0),
        _row("B", "Lonely", "X", None),
    ])
    r = rate_industries(table, as_of="2026-08-09")[0]
    assert r.rating == NO_RATING
    assert r.mean_score is None
    assert r.member_count == 2
    assert r.scored_count == 1


def test_unscored_members_excluded_from_group_mean():
    """A name with no composite must not drag a group toward zero -- it's
    absent, not neutral. Same principle as coverage.py's missing-metric
    handling."""
    table = _table([
        _row("A", "Semis", "Tech", 1.0),
        _row("B", "Semis", "Tech", 1.0),
        _row("C", "Semis", "Tech", None),
    ])
    r = rate_industries(table, as_of="2026-08-09")[0]
    assert r.mean_score == 1.0  # not 0.667
    assert r.rating == "STRONG BUY"
    assert r.member_count == 3
    assert r.scored_count == 2


def test_groups_sorted_best_first():
    table = _table([
        _row("A", "Bad", "X", -1.0), _row("B", "Bad", "X", -1.0),
        _row("C", "Good", "X", 1.0), _row("D", "Good", "X", 1.0),
    ])
    ratings = rate_industries(table, as_of="2026-08-09")
    assert [r.group for r in ratings] == ["Good", "Bad"]


def test_empty_table_returns_nothing():
    assert rate_names(pd.DataFrame()) == []
    assert rate_industries(pd.DataFrame(), as_of="2026-08-09") == []
    assert rate_sectors(pd.DataFrame(), as_of="2026-08-09") == []


# ------------------------------------------------------------------ frames
def test_frames_have_expected_columns():
    table = _table([
        _row("A", "Semis", "Tech", 1.0, percentile=90.0),
        _row("B", "Semis", "Tech", 0.5, percentile=60.0),
    ])
    ndf = names_to_frame(rate_names(table))
    assert {"ticker", "rating", "peer_rating", "peer_rank", "score"} <= set(ndf.columns)

    idf = groups_to_frame(rate_industries(table, as_of="2026-08-09"))
    assert "industry" in idf.columns and "sector" in idf.columns

    sdf = groups_to_frame(rate_sectors(table, as_of="2026-08-09"))
    assert "sector" in sdf.columns
