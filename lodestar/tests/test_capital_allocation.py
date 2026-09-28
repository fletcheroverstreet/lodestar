"""Hand-computed tests for the Capital Allocation bucket -- the one the
project spec calls out to get right (buyback_timing)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import load_config
from lodestar.metrics.base import TickerContext
from lodestar.metrics.capital_allocation import (
    buyback_timing, buyback_yield, incremental_roic, ma_returns, share_count_change_3y,
)

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _idx(n, start="2023-03-31"):
    return [d.date().isoformat() for d in pd.date_range(start, periods=n, freq="QE")]


def _ctx(df: pd.DataFrame, px: pd.DataFrame | None = None, as_of="2026-01-01",
         sector="Information Technology") -> TickerContext:
    return TickerContext(ticker="TEST", as_of=as_of, cik=1, fundamentals=df,
                          prices=px if px is not None else pd.DataFrame(),
                          sector=sector, industry="i")


# --------------------------------------------------------------- buyback_timing
def test_buyback_timing_blocked_without_shares_repurchased_concept(cfg):
    idx = _idx(4)
    df = pd.DataFrame({"buybacks": [100.0] * 4}, index=idx)
    result = buyback_timing(_ctx(df), cfg)
    assert result.value is None
    assert result.substitution == "blocked_missing_shares_repurchased_concept"


def test_buyback_timing_hand_computed_bought_below_vwap(cfg):
    """Company spends $100 buying 10 shares/quarter for 4 quarters
    (avg price = $10/share) while the market's VWAP over that same
    window was $12.50 -- bought at a discount, so the metric must be
    positive: (12.50 - 10.00) / 12.50 = 0.20"""
    idx = _idx(4)
    df = pd.DataFrame({
        "buybacks": [100.0, 100.0, 100.0, 100.0],
        "shares_repurchased": [10.0, 10.0, 10.0, 10.0],
    }, index=idx)
    px = pd.DataFrame({
        "date": idx,
        "adj_high": [12.5] * 4, "adj_low": [12.5] * 4, "adj_close": [12.5] * 4,
        "adj_volume": [1000] * 4,
    })
    result = buyback_timing(_ctx(df, px), cfg)
    # total $ = 400, total shares = 40, avg price = 10.0; VWAP = 12.5
    assert abs(result.value - 0.20) < 1e-9
    assert result.raw_inputs["avg_repurchase_price"] == 10.0
    assert result.raw_inputs["market_vwap"] == 12.5


def test_buyback_timing_hand_computed_bought_above_vwap_is_negative(cfg):
    """Bought at $15/share while market VWAP was $12.50 -- buying the
    top, so the metric must be negative: (12.50-15.00)/12.50 = -0.20"""
    idx = _idx(4)
    df = pd.DataFrame({
        "buybacks": [150.0] * 4, "shares_repurchased": [10.0] * 4,
    }, index=idx)
    px = pd.DataFrame({
        "date": idx, "adj_high": [12.5] * 4, "adj_low": [12.5] * 4,
        "adj_close": [12.5] * 4, "adj_volume": [1000] * 4,
    })
    result = buyback_timing(_ctx(df, px), cfg)
    assert abs(result.value - (-0.20)) < 1e-9


def test_buyback_timing_none_when_no_shares_actually_repurchased(cfg):
    idx = _idx(4)
    df = pd.DataFrame({"buybacks": [0.0] * 4, "shares_repurchased": [0.0] * 4}, index=idx)
    result = buyback_timing(_ctx(df), cfg)
    assert result.value is None


def test_buyback_timing_rejects_implausible_implied_price(cfg):
    """Real bug this guards against, caught in the smoke run: SPG's
    share-count tag implied an average repurchase price of ~$14,900
    against a stock trading near $122, because the dollar figure and the
    share count describe different things. Unguarded that produced a
    buyback_timing of -51.5, which would have dominated the whole
    capital-allocation bucket's z-scores for the entire universe."""
    idx = _idx(4)
    df = pd.DataFrame({
        "buybacks": [175_000_000.0] * 4,
        "shares_repurchased": [11_759.0] * 4,   # implies ~$14,900/share
    }, index=idx)
    px = pd.DataFrame({
        "date": idx, "adj_high": [122.0] * 4, "adj_low": [122.0] * 4,
        "adj_close": [122.0] * 4, "adj_volume": [1000] * 4,
    })
    result = buyback_timing(_ctx(df, px), cfg)
    assert result.value is None
    assert result.substitution == "implausible_repurchase_price_vs_market"
    assert result.raw_inputs["price_ratio_vs_vwap"] > 100


def test_buyback_timing_ignores_quarters_missing_one_side(cfg):
    """A quarter with repurchase dollars but no share count (or a zero
    count) can't contribute to an average price -- including one side of
    it would skew the ratio. Only quarters with BOTH non-zero count."""
    idx = _idx(4)
    df = pd.DataFrame({
        # Only the last two quarters have both sides populated.
        "buybacks": [500.0, 0.0, 100.0, 100.0],
        "shares_repurchased": [0.0, 20.0, 10.0, 10.0],
    }, index=idx)
    px = pd.DataFrame({
        "date": idx, "adj_high": [10.0] * 4, "adj_low": [10.0] * 4,
        "adj_close": [10.0] * 4, "adj_volume": [1000] * 4,
    })
    result = buyback_timing(_ctx(df, px), cfg)
    # Only quarters 3 and 4 count: $200 / 20 shares = $10.00/share,
    # exactly the market VWAP -> timing value of 0.0 (neither good nor bad).
    assert result.raw_inputs["quarters_used"] == 2
    assert result.raw_inputs["avg_repurchase_price"] == 10.0
    assert abs(result.value - 0.0) < 1e-9


# ---------------------------------------------------------------- buyback_yield
def test_buyback_yield_hand_computed(cfg):
    idx = _idx(4)
    df = pd.DataFrame({
        "buybacks": [25.0] * 4,  # ttm = 100
        "shares_outstanding": [1_000_000] * 4,
    }, index=idx)
    px = pd.DataFrame({"date": [idx[-1]], "close": [10.0], "adj_close": [10.0]})
    result = buyback_yield(_ctx(df, px), cfg)
    # market cap = 10,000,000; yield = 100/10,000,000
    assert abs(result.value - (100 / 10_000_000)) < 1e-12


# ----------------------------------------------------- share_count_change_3y
def test_share_count_change_3y_hand_computed_reduction():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(13)
    shares = [1_000_000] * 13
    shares[0] = 1_100_000  # 3 years ago, more shares than today
    df = pd.DataFrame({"shares_diluted": shares}, index=idx)
    result = share_count_change_3y(_ctx(df), cfg_local)
    # reduction = (1,100,000 - 1,000,000) / 1,100,000 = 0.0909...
    assert abs(result.value - (100_000 / 1_100_000)) < 1e-9


def test_share_count_change_3y_negative_when_diluted():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(13)
    shares = [1_000_000] * 13
    shares[0] = 900_000  # 3 years ago, FEWER shares -- dilution since then
    df = pd.DataFrame({"shares_diluted": shares}, index=idx)
    result = share_count_change_3y(_ctx(df), cfg_local)
    assert result.value < 0


# --------------------------------------------------------------- incremental_roic
def test_incremental_roic_not_applicable_for_financials(cfg):
    idx = _idx(13)
    df = pd.DataFrame({"operating_income": [50.0] * 13, "equity": [1000.0] * 13,
                        "cash": [0.0] * 13}, index=idx)
    result = incremental_roic(_ctx(df, sector="Financials"), cfg)
    assert result.value is None
    assert result.substitution == "not_applicable_financials_sector"


def test_incremental_roic_hand_computed():
    """16 quarters: incremental_roic (window=12 quarters, i.e. 3 years)
    compares the TTM ending at index[3] ("begin") against the TTM ending
    at index[15] ("end", the latest row) -- computing a TTM at index[3]
    itself needs the 4 quarters index[0..3], which is why this fixture
    needs 16 rows, not 13: the begin anchor needs its OWN 4-quarter
    lookback to exist inside the fixture too."""
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(16)
    df = pd.DataFrame({
        "operating_income": [20.0] * 4 + [40.0] * 12,
        "pretax_income": [20.0] * 4 + [40.0] * 12,
        "tax_expense": [4.0] * 4 + [8.0] * 12,  # exactly 20% every quarter
        "equity": [1000.0] * 16, "cash": [0.0] * 16,
    }, index=idx)
    result = incremental_roic(_ctx(df), cfg_local)
    # begin TTM (index[3]): EBIT=20*4=80, NOPAT=80*0.8=64, IC=1000
    # end TTM (index[15]):   EBIT=40*4=160, NOPAT=160*0.8=128, IC=1000
    # incremental ROIC = (128-64)/(1000-1000) -> division by zero -> None
    # (IC held constant on purpose; assert that exact, honest behaviour
    # instead of a fabricated number)
    assert result.raw_inputs["nopat_then"] == 64.0
    assert result.raw_inputs["nopat_now"] == 128.0
    assert result.value is None


def test_incremental_roic_hand_computed_non_degenerate():
    """Same NOPAT construction as above, but invested capital also
    changes between the two anchors, so the division is well-defined and
    checkable by hand."""
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(16)
    df = pd.DataFrame({
        "operating_income": [20.0] * 4 + [40.0] * 12,
        "pretax_income": [20.0] * 4 + [40.0] * 12,
        "tax_expense": [4.0] * 4 + [8.0] * 12,  # exactly 20% every quarter
        "equity": [1000.0] * 4 + [1500.0] * 12,
        "cash": [0.0] * 16,
    }, index=idx)
    result = incremental_roic(_ctx(df), cfg_local)
    # begin (index[3]): NOPAT=64, IC = equity(1000) - cash(0) = 1000
    # end (index[15]):  NOPAT=128, IC = equity(1500) - cash(0) = 1500
    # incremental ROIC = (128-64) / (1500-1000) = 64/500 = 0.128
    assert result.value is not None
    assert abs(result.value - 0.128) < 1e-9


# --------------------------------------------------------------------- ma_returns
def test_ma_returns_not_applicable_without_goodwill_growth(cfg):
    idx = _idx(13)
    df = pd.DataFrame({"goodwill": [500.0] * 13, "intangible_assets": [200.0] * 13},
                       index=idx)
    result = ma_returns(_ctx(df), cfg)
    assert result.value is None
    assert result.substitution == "not_applicable_no_ma_activity"


def test_ma_returns_fires_and_flags_low_confidence_when_goodwill_grew():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(16)
    goodwill = [500.0] * 4 + [800.0] * 12  # grew well past the 2% threshold
    df = pd.DataFrame({
        "goodwill": goodwill, "intangible_assets": [0.0] * 16,
        "operating_income": [20.0] * 4 + [40.0] * 12,
        "pretax_income": [20.0] * 4 + [40.0] * 12,
        "tax_expense": [4.0] * 4 + [8.0] * 12,
        "equity": [1000.0] * 4 + [1500.0] * 12, "cash": [0.0] * 16,
    }, index=idx)
    result = ma_returns(_ctx(df), cfg_local)
    assert result.raw_inputs["goodwill_growth_3y"] > 0.02
    assert result.substitution == "low_confidence_ma_proxy"
    # incremental ROIC underneath is the same well-defined 0.128 computed
    # in test_incremental_roic_hand_computed_non_degenerate above, and
    # this fixture has no impairments, so no drag is subtracted.
    assert result.raw_inputs["impairment_drag"] == 0.0
    assert abs(result.value - 0.128) < 1e-9


def test_ma_returns_subtracts_goodwill_impairment_drag():
    """A company that grew goodwill and then wrote some of it back off
    must score WORSE than the identical company that didn't -- the
    impairment is the clearest available evidence a deal didn't earn its
    cost."""
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(16)
    base = {
        "goodwill": [500.0] * 4 + [800.0] * 12,
        "intangible_assets": [0.0] * 16,
        "operating_income": [20.0] * 4 + [40.0] * 12,
        "pretax_income": [20.0] * 4 + [40.0] * 12,
        "tax_expense": [4.0] * 4 + [8.0] * 12,
        "equity": [1000.0] * 4 + [1500.0] * 12, "cash": [0.0] * 16,
    }
    clean = ma_returns(_ctx(pd.DataFrame(base, index=idx)), cfg_local)

    impaired_df = pd.DataFrame(
        {**base, "goodwill_impairment": [0.0] * 12 + [80.0] * 4}, index=idx
    )
    impaired = ma_returns(_ctx(impaired_df), cfg_local)

    # impairment total over the trailing 12 quarters = 80*4 = 320,
    # goodwill base = 800 -> drag = 320/800 = 0.40
    assert abs(impaired.raw_inputs["impairment_drag"] - 0.40) < 1e-9
    assert abs(impaired.value - (0.128 - 0.40)) < 1e-9
    assert impaired.value < clean.value
