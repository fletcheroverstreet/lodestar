"""Hand-computed tests for the finance-math primitives in
lodestar.metrics.base. Every expected value here is computed by hand in
the test (or in a comment), not by re-running the function under test."""

from __future__ import annotations

import pandas as pd
import pytest

from lodestar.metrics.base import (
    TickerContext, cagr, market_cap, ols_slope, safe_divide, ttm, ttm_series,
)


# --------------------------------------------------------------------- ttm
def test_ttm_sums_the_last_four_quarters():
    s = pd.Series([10, 20, 30, 40, 50], index=["q1", "q2", "q3", "q4", "q5"])
    # last 4 = 20+30+40+50 = 140
    assert ttm(s) == 140


def test_ttm_none_with_fewer_than_four_quarters():
    s = pd.Series([10, 20, 30], index=["q1", "q2", "q3"])
    assert ttm(s) is None


def test_ttm_requires_all_four_non_null():
    s = pd.Series([10, None, 30, 40, 50], index=["q1", "q2", "q3", "q4", "q5"])
    # dropna leaves [10, 30, 40, 50] -- last 4 of THAT = 10+30+40+50 = 130.
    # This documents ttm()'s actual (simple) behaviour: it drops NaNs
    # first, then takes the last 4 of what's left. The gap-aware version
    # that refuses to bridge a hole is ttm_series(), used for anything
    # that needs a full trailing history rather than one latest figure.
    assert ttm(s) == 130


def test_ttm_custom_quarter_count():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8], index=[f"q{i}" for i in range(8)])
    assert ttm(s, quarters=8) == sum(range(1, 9))


# ------------------------------------------------------------- ttm_series
def _q_index(n: int, start="2020-03-31"):
    """n consecutive calendar-quarter end dates starting at `start`."""
    dates = pd.date_range(start=start, periods=n, freq="QE")
    return [d.date().isoformat() for d in dates]


def test_ttm_series_rolling_sum_of_consecutive_quarters():
    idx = _q_index(6)  # 2020-03-31 .. 2021-06-30
    s = pd.Series([10, 20, 30, 40, 50, 60], index=idx)
    out = ttm_series(s, quarters=4)
    # windows: [10,20,30,40]=100 -> idx[3]; [20,30,40,50]=140 -> idx[4];
    #          [30,40,50,60]=180 -> idx[5]
    assert list(out.values) == [100, 140, 180]
    assert list(out.index) == [idx[3], idx[4], idx[5]]


def test_ttm_series_skips_a_window_containing_nan():
    idx = _q_index(6)
    s = pd.Series([10, 20, None, 40, 50, 60], index=idx)
    out = ttm_series(s, quarters=4)
    # window ending idx[3] = [10,20,None,40] -> has NaN -> skipped
    # window ending idx[4] = [20,None,40,50] -> has NaN -> skipped
    # window ending idx[5] = [None,40,50,60] -> has NaN -> skipped
    assert len(out) == 0


def test_ttm_series_skips_a_window_with_a_missing_row_gap():
    """A quarter missing from the index entirely (not NaN, just absent)
    must not be silently bridged by summing across the gap."""
    idx = _q_index(3) + _q_index(3, start="2022-03-31")  # 3 quarters, then
    # a ~9-month jump, then 3 more -- simulates a dropped middle quarter
    s = pd.Series([10, 20, 30, 100, 110, 120], index=idx)
    out = ttm_series(s, quarters=4)
    # The only 4-row window straddling the gap is idx[2:6], which spans
    # from 2020-09-30 to 2022-06-30 -- far more than max_gap_days apart
    # between idx[2] and idx[3] -- so it must be excluded.
    assert len(out) == 0


def test_ttm_series_too_short_returns_empty():
    s = pd.Series([1, 2, 3], index=["a", "b", "c"])
    assert len(ttm_series(s, quarters=4)) == 0


# ------------------------------------------------------------------- cagr
def test_cagr_hand_computed():
    # 100 -> 133.1 over 3 years = 10% CAGR exactly:
    # 100 * 1.10^3 = 100 * 1.331 = 133.1
    result = cagr(100.0, 133.1, 3)
    assert result is not None
    assert abs(result - 0.10) < 1e-9


def test_cagr_none_when_base_is_zero_or_negative():
    assert cagr(0.0, 100.0, 3) is None
    assert cagr(-50.0, 100.0, 3) is None


def test_cagr_none_when_years_non_positive():
    assert cagr(100.0, 150.0, 0) is None


def test_cagr_none_when_end_is_negative_not_a_complex_number():
    """Positive base, negative end: end/begin is negative, and raising a
    negative number to a fractional power (1/years) has no real result.
    Python returns a complex number here instead of raising -- this must
    come back as an honest None, not crash three call frames up trying
    to float() a complex value (a real bug this test would have caught
    before it needed a live end-to-end run to surface)."""
    assert cagr(100.0, -50.0, 3) is None


def test_cagr_zero_end_is_exactly_negative_100_percent():
    result = cagr(100.0, 0.0, 1)
    assert result == -1.0


def test_cagr_handles_a_decline():
    # 100 -> 81 over 2 years = -10% CAGR (0.9^2 = 0.81)
    result = cagr(100.0, 81.0, 2)
    assert abs(result - (-0.10)) < 1e-9


# --------------------------------------------------------------- ols_slope
def test_ols_slope_perfectly_linear_series():
    # y = 2x + 5 exactly -> slope must be 2.0
    y = pd.Series([5, 7, 9, 11, 13])
    assert abs(ols_slope(y) - 2.0) < 1e-9


def test_ols_slope_hand_computed_noisy_series():
    # x = [0,1,2,3], y = [1,2,4,3]
    # x_mean=1.5, y_mean=2.5
    # cov terms: (0-1.5)(1-2.5)=2.25; (1-1.5)(2-2.5)=0.25;
    #            (2-1.5)(4-2.5)=0.75; (3-1.5)(3-2.5)=0.75
    # numerator = 2.25+0.25+0.75+0.75 = 4.0
    # var terms: 2.25+0.25+0.25+2.25 = 5.0
    # slope = 4.0 / 5.0 = 0.8
    y = pd.Series([1, 2, 4, 3])
    assert abs(ols_slope(y) - 0.8) < 1e-9


def test_ols_slope_none_with_fewer_than_two_points():
    assert ols_slope(pd.Series([5.0])) is None
    assert ols_slope(pd.Series(dtype=float)) is None


# ----------------------------------------------------------- safe_divide
def test_safe_divide_normal():
    assert safe_divide(10.0, 4.0) == 2.5


def test_safe_divide_none_on_zero_denominator():
    assert safe_divide(10.0, 0.0) is None


def test_safe_divide_none_on_missing_input():
    assert safe_divide(None, 5.0) is None
    assert safe_divide(5.0, None) is None


# ------------------------------------------------------------- market_cap
def test_market_cap_hand_computed():
    assert market_cap(1_000_000.0, 25.50) == 25_500_000.0


def test_market_cap_none_on_missing_input():
    assert market_cap(None, 25.0) is None
    assert market_cap(1000.0, None) is None


# --------------------------------------------------------- TickerContext
def test_ticker_context_series_missing_concept_returns_empty_series():
    ctx = TickerContext(
        ticker="TEST", as_of="2026-01-01", cik=1,
        fundamentals=pd.DataFrame({"revenue": [1.0, 2.0]}, index=["q1", "q2"]),
        prices=pd.DataFrame(), sector="s", industry="i",
    )
    s = ctx.series("net_income")  # not in the frame
    assert len(s) == 0


def test_ticker_context_latest_and_latest_period_end():
    ctx = TickerContext(
        ticker="TEST", as_of="2026-01-01", cik=1,
        fundamentals=pd.DataFrame(
            {"revenue": [100.0, None, 300.0]}, index=["2025-03-31", "2025-06-30", "2025-09-30"]
        ),
        prices=pd.DataFrame(), sector="s", industry="i",
    )
    assert ctx.latest("revenue") == 300.0
    assert ctx.latest_period_end("revenue") == "2025-09-30"


def test_ticker_context_price_as_of_uses_last_trading_day_on_or_before():
    ctx = TickerContext(
        ticker="TEST", as_of="2026-01-01", cik=1,
        fundamentals=pd.DataFrame(),
        prices=pd.DataFrame({
            "date": ["2026-01-02", "2026-01-05", "2026-01-06"],
            "close": [10.0, 11.0, 12.0],
            "adj_close": [10.5, 11.5, 12.5],
        }),
        sector="s", industry="i",
    )
    # 2026-01-05 is on/before the requested date -- must use it, not
    # 2026-01-06 (which is after) and not 2026-01-02 (stale).
    assert ctx.price_as_of("2026-01-05") == 11.5
    # A weekend/holiday with no bar: falls back to the prior trading day.
    assert ctx.price_as_of("2026-01-04") == 10.5


def test_ticker_context_price_as_of_none_when_no_data():
    ctx = TickerContext(
        ticker="TEST", as_of="2026-01-01", cik=1,
        fundamentals=pd.DataFrame(), prices=pd.DataFrame(),
        sector="s", industry="i",
    )
    assert ctx.price_as_of("2026-01-01") is None


# ------------------------------------------------------- TickerContext.vwap
def test_ticker_context_vwap_hand_computed():
    # Same fixture and hand computation as test_prices.py's finlake-level
    # VWAP test, to confirm this local copy matches the formula exactly:
    # Day1 typical=(10+8+9.5)/3=9.1666667, vol=100
    # Day2 typical=(14+10+11)/3=11.6666667, vol=300
    # VWAP = (916.66667 + 3500.0) / 400 = 11.0416667
    ctx = TickerContext(
        ticker="TEST", as_of="2024-01-04", cik=1, fundamentals=pd.DataFrame(),
        prices=pd.DataFrame({
            "date": ["2024-01-02", "2024-01-03"],
            "high": [10, 14], "low": [8, 10], "close": [9.5, 11], "volume": [100, 300],
            "adj_high": [10, 14], "adj_low": [8, 10], "adj_close": [9.5, 11],
            "adj_volume": [100, 300],
        }),
        sector="s", industry="i",
    )
    v = ctx.vwap("2024-01-01", "2024-01-04")
    assert abs(v - 11.0416667) < 1e-5


def test_ticker_context_vwap_respects_window():
    ctx = TickerContext(
        ticker="TEST", as_of="2024-06-02", cik=1, fundamentals=pd.DataFrame(),
        prices=pd.DataFrame({
            "date": ["2024-01-02", "2024-06-01"],
            "adj_high": [10, 500], "adj_low": [8, 500], "adj_close": [9, 500],
            "adj_volume": [100, 999],
        }),
        sector="s", industry="i",
    )
    v = ctx.vwap("2024-01-01", "2024-01-04")
    assert abs(v - 9.0) < 1e-9  # the 2024-06-01 bar must be excluded


def test_ticker_context_vwap_none_with_no_bars_in_window():
    ctx = TickerContext(
        ticker="TEST", as_of="2024-01-01", cik=1,
        fundamentals=pd.DataFrame(), prices=pd.DataFrame(),
        sector="s", industry="i",
    )
    assert ctx.vwap("2024-01-01", "2024-01-04") is None
