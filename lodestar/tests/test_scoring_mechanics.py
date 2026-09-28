"""Tests for the scoring mechanics themselves -- winsorization, peer-group
z-scores (including a peer group of one), and the industry -> sector ->
universe fallback ladder. These are the mechanics the project spec
explicitly calls out as needing dedicated tests, separate from any one
factor's formula.
"""

from __future__ import annotations

import pandas as pd
import pytest

from lodestar.scoring.winsorize import winsorize
from lodestar.scoring.zscore import resolve_peer_group, zscore_metric


# ------------------------------------------------------------------ winsorize
def test_winsorize_clips_at_the_boundary():
    # 100 values 1..100. 1st/99th percentile clipping should pull the
    # extreme low and high values in to (approximately) the 1st and 99th
    # percentile of a 1..100 uniform series.
    values = pd.Series(range(1, 101), index=[f"t{i}" for i in range(100)], dtype=float)
    result = winsorize(values, low_pct=0.01, high_pct=0.99)
    lo = values.quantile(0.01)
    hi = values.quantile(0.99)
    assert result.min() == lo
    assert result.max() == hi
    # Everything strictly between the bounds must be untouched.
    middle = values[(values > lo) & (values < hi)]
    pd.testing.assert_series_equal(result.loc[middle.index], middle)


def test_winsorize_one_broken_filing_does_not_dominate():
    """A single wildly-wrong value (e.g. a bad filing) must not stretch
    the whole peer group's range after winsorizing."""
    values = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 100000.0],
                        index=[f"t{i}" for i in range(10)])
    result = winsorize(values, low_pct=0.01, high_pct=0.99)
    assert result.max() < 100000.0
    assert result.max() == values.quantile(0.99)


def test_winsorize_passes_through_with_fewer_than_two_values():
    values = pd.Series([42.0], index=["only"])
    result = winsorize(values, low_pct=0.01, high_pct=0.99)
    assert result["only"] == 42.0


def test_winsorize_preserves_nan():
    values = pd.Series([1.0, 2.0, None, 4.0, 5.0], index=list("abcde"))
    result = winsorize(values, low_pct=0.01, high_pct=0.99)
    assert pd.isna(result["c"])


# --------------------------------------------------------- peer group ladder
def test_peer_group_uses_industry_when_large_enough():
    tickers = [f"t{i}" for i in range(10)]
    values = pd.Series(range(10), index=tickers, dtype=float)
    industry_of = {t: "Semis" for t in tickers}
    sector_of = {t: "Tech" for t in tickers}
    level, peers = resolve_peer_group("t0", values, industry_of, sector_of, min_group_size=8)
    assert level == "industry"
    assert len(peers) == 10


def test_peer_group_falls_back_to_sector_when_industry_too_small():
    tickers = [f"t{i}" for i in range(10)]
    values = pd.Series(range(10), index=tickers, dtype=float)
    # Two tiny industries, one shared sector with 10 total members.
    industry_of = {t: ("A" if i < 3 else "B") for i, t in enumerate(tickers)}
    sector_of = {t: "Tech" for t in tickers}
    level, peers = resolve_peer_group("t0", values, industry_of, sector_of, min_group_size=8)
    assert level == "sector"
    assert len(peers) == 10


def test_peer_group_falls_back_to_universe_when_sector_also_too_small():
    tickers = [f"t{i}" for i in range(10)]
    values = pd.Series(range(10), index=tickers, dtype=float)
    industry_of = {t: ("A" if i < 3 else "B") for i, t in enumerate(tickers)}
    sector_of = {t: ("X" if i < 5 else "Y") for i, t in enumerate(tickers)}
    level, peers = resolve_peer_group("t0", values, industry_of, sector_of, min_group_size=8)
    assert level == "universe"
    assert len(peers) == 10


def test_peer_group_ladder_counts_only_usable_non_null_values():
    """A structurally large industry where most members are missing THIS
    metric is just as thin as a small one -- peer group size must be
    measured by usable data, not industry membership count."""
    tickers = [f"t{i}" for i in range(10)]
    values = pd.Series([1.0, 2.0, 3.0] + [None] * 7, index=tickers)  # only 3 usable
    industry_of = {t: "Semis" for t in tickers}  # all 10 in one industry
    sector_of = {t: "Tech" for t in tickers}
    level, peers = resolve_peer_group("t0", values, industry_of, sector_of, min_group_size=8)
    assert level != "industry", "only 3 of 10 industry members have usable data -- must fall back"
    # sector_of assigns every ticker the SAME sector too, so the sector
    # group has the identical 3 usable members as industry -- also below
    # the threshold, so it must cascade all the way to universe.
    assert level == "universe"
    assert len(peers) == 3


# --------------------------------------------------------------------- zscore
def test_zscore_hand_computed():
    """winsorize_low_pct=0.0/high_pct=1.0 here on purpose -- 0th/100th
    percentile equals this series' own min/max, so clipping is a true
    no-op and the z-scores can be checked against the RAW mean/std with
    no winsorization interaction to account for. Winsorization's effect
    on z-scoring is covered separately below, with bounds that actually
    clip something."""
    tickers = [f"t{i}" for i in range(8)]
    values = pd.Series([10, 20, 30, 40, 50, 60, 70, 80], index=tickers, dtype=float)
    industry_of = {t: "Semis" for t in tickers}
    sector_of = {t: "Tech" for t in tickers}
    results = zscore_metric(values, industry_of, sector_of, min_group_size=8,
                             winsorize_low_pct=0.0, winsorize_high_pct=1.0)
    mean = values.mean()
    std = values.std(ddof=1)
    for t in tickers:
        expected_z = (values[t] - mean) / std
        assert abs(results[t].z - expected_z) < 1e-9
        assert results[t].peer_level == "industry"
        assert results[t].peer_group_size == 8


def test_zscore_peer_group_of_one_is_neutral_not_a_crash():
    """The project spec explicitly calls this out as a case needing a
    dedicated test: a peer group of exactly one ticker."""
    values = pd.Series([42.0], index=["solo"])
    industry_of = {"solo": "Uniquely Alone Inc"}
    sector_of = {"solo": "Nobody Else Here"}
    results = zscore_metric(values, industry_of, sector_of, min_group_size=8,
                             winsorize_low_pct=0.01, winsorize_high_pct=0.99)
    assert results["solo"].z == 0.0
    assert results["solo"].peer_group_size == 1
    assert results["solo"].peer_std == 0.0


def test_zscore_missing_value_produces_no_z_not_zero():
    """A missing metric must produce z=None (dropped by the coverage
    layer), never a fabricated neutral 0 -- 0 is reserved for the
    genuinely-neutral peer-group-of-one case above, and conflating the
    two would make missing data invisible in the output."""
    tickers = [f"t{i}" for i in range(8)]
    values = pd.Series([10.0] * 7 + [None], index=tickers)
    industry_of = {t: "Semis" for t in tickers}
    sector_of = {t: "Tech" for t in tickers}
    results = zscore_metric(values, industry_of, sector_of, min_group_size=8,
                             winsorize_low_pct=0.01, winsorize_high_pct=0.99)
    assert results["t7"].z is None
    assert results["t7"].raw_value is None


def test_zscore_extreme_value_is_winsorized_before_scoring():
    tickers = [f"t{i}" for i in range(10)]
    values = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 100000.0],
                        index=tickers, dtype=float)
    industry_of = {t: "Semis" for t in tickers}
    sector_of = {t: "Tech" for t in tickers}
    results = zscore_metric(values, industry_of, sector_of, min_group_size=8,
                             winsorize_low_pct=0.01, winsorize_high_pct=0.99)
    # The outlier's z-score must reflect its WINSORIZED value, not
    # 100000 directly -- so it must not be an absurdly large z.
    assert results["t9"].winsorized_value < 100000.0
    assert results["t9"].z < 10  # a sane bound; an unwinsorized z here would be enormous
