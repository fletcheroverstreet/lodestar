"""Hand-computed tests for the Quality bucket."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import load_config
from lodestar.metrics.base import TickerContext
from lodestar.metrics.quality import (
    fcf_conversion, gross_margin_trend, interest_coverage, roic, roic_stability,
)

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _ctx(df: pd.DataFrame, as_of: str = "2026-01-01") -> TickerContext:
    return TickerContext(ticker="TEST", as_of=as_of, cik=1, fundamentals=df,
                          prices=pd.DataFrame(), sector="s", industry="i")


def _quarterly_index(n: int, start="2021-03-31"):
    return [d.date().isoformat() for d in pd.date_range(start, periods=n, freq="QE")]


# ---------------------------------------------------------------------- roic
def test_roic_hand_computed(cfg):
    idx = _quarterly_index(4)
    df = pd.DataFrame({
        "operating_income": [50, 50, 50, 50],
        "pretax_income": [50, 50, 50, 50],
        "tax_expense": [10, 10, 10, 10],
        "debt_long": [0] * 4, "debt_short": [0] * 4,
        "equity": [1000] * 4, "cash": [0] * 4,
    }, index=idx)
    result = roic(_ctx(df), cfg)
    # EBIT ttm = 200, tax rate = 10/50 = 0.20, NOPAT = 200*0.80 = 160
    # invested capital = 0+0+1000-0-0 = 1000
    # ROIC = 160/1000 = 0.16
    assert result.value is not None
    assert abs(result.value - 0.16) < 1e-9


def test_roic_none_without_invested_capital():
    cfg_local = load_config(REPO_CONFIG)
    idx = _quarterly_index(4)
    df = pd.DataFrame({"operating_income": [50] * 4}, index=idx)  # no balance sheet data
    result = roic(_ctx(df), cfg_local)
    assert result.value is None


# --------------------------------------------------------- roic_stability
def test_roic_stability_perfectly_consistent_roic(cfg):
    """8 quarters of IDENTICAL ROIC -> stdev = 0 -> undefined (not
    infinite), per roic_stability's explicit zero-variance guard."""
    idx = _quarterly_index(8)
    df = pd.DataFrame({
        "operating_income": [50] * 8, "pretax_income": [50] * 8,
        "tax_expense": [10] * 8, "equity": [1000] * 8, "cash": [0] * 8,
    }, index=idx)
    result = roic_stability(_ctx(df), cfg)
    assert result.value is None
    assert "zero variance" in (result.note or "")


def test_roic_stability_varying_roic_hand_computed(cfg):
    """8 quarters of steadily RAMPING operating income (not alternating --
    a period-2 alternation is exactly smoothed away by a 4-quarter TTM
    sum, which would make this test accidentally check the zero-variance
    path instead of the one it's meant to). Effective tax rate is
    engineered to be exactly 20% every quarter, so NOPAT TTM = EBIT TTM *
    0.8 and invested capital is held constant, giving a hand-checkable
    ROIC sequence.

    EBIT (quarterly): 40,42,44,46,48,50,52,54
    TTM EBIT (5 windows): 172, 180, 188, 196, 204
      (40+42+44+46, 42+44+46+48, 44+46+48+50, 46+48+50+52, 48+50+52+54)
    NOPAT TTM = TTM EBIT * 0.8: 137.6, 144.0, 150.4, 156.8, 163.2
    Invested capital constant at 1000, so ROIC = NOPAT/1000:
      0.1376, 0.1440, 0.1504, 0.1568, 0.1632
    mean = 0.15040 (sum / 5); sample stdev (ddof=1) of that 5-point
    arithmetic sequence with common difference 0.0064: computed and
    compared below, not re-derived from the function under test.
    """
    idx = _quarterly_index(8)
    op_income = [40, 42, 44, 46, 48, 50, 52, 54]
    df = pd.DataFrame({
        "operating_income": op_income,
        "pretax_income": op_income,
        "tax_expense": [oi * 0.20 for oi in op_income],  # exactly 20% effective rate each quarter
        "equity": [1000] * 8, "cash": [0] * 8,
    }, index=idx)
    result = roic_stability(_ctx(df), cfg)
    assert result.value is not None
    assert result.raw_inputs["n_quarters"] == 5

    expected_roic = [0.1376, 0.1440, 0.1504, 0.1568, 0.1632]
    expected_mean = sum(expected_roic) / 5
    variance = sum((r - expected_mean) ** 2 for r in expected_roic) / (5 - 1)
    expected_std = variance ** 0.5

    assert abs(result.raw_inputs["roic_mean"] - expected_mean) < 1e-9
    assert abs(result.raw_inputs["roic_std"] - expected_std) < 1e-9
    assert abs(result.value - expected_mean / expected_std) < 1e-9


def test_roic_stability_none_with_too_little_history(cfg):
    idx = _quarterly_index(3)
    df = pd.DataFrame({"operating_income": [50, 50, 50], "equity": [1000] * 3,
                        "cash": [0] * 3}, index=idx)
    result = roic_stability(_ctx(df), cfg)
    assert result.value is None


# ----------------------------------------------------- gross_margin_trend
def test_gross_margin_trend_hand_computed_improving_margin(cfg):
    """Revenue flat at 1000/quarter; gross profit rises 400,410,420,...
    so TTM margin rises steadily. Slope should be positive."""
    idx = _quarterly_index(16)
    revenue = [1000] * 16
    gross_profit = [400 + 10 * i for i in range(16)]
    df = pd.DataFrame({"revenue": revenue, "gross_profit": gross_profit}, index=idx)
    result = gross_margin_trend(_ctx(df), cfg)
    assert result.value is not None
    assert result.value > 0, "margin is rising every quarter -- slope must be positive"


def test_gross_margin_trend_flat_margin_gives_near_zero_slope(cfg):
    idx = _quarterly_index(16)
    df = pd.DataFrame({"revenue": [1000] * 16, "gross_profit": [400] * 16}, index=idx)
    result = gross_margin_trend(_ctx(df), cfg)
    assert result.value is not None
    assert abs(result.value) < 1e-6


def test_gross_margin_trend_unavailable_without_gross_profit(cfg):
    idx = _quarterly_index(16)
    df = pd.DataFrame({"revenue": [1000] * 16}, index=idx)  # no gross_profit at all
    result = gross_margin_trend(_ctx(df), cfg)
    assert result.value is None


# --------------------------------------------------------- fcf_conversion
def test_fcf_conversion_hand_computed(cfg):
    idx = _quarterly_index(4)
    df = pd.DataFrame({
        "cfo": [100, 100, 100, 100], "capex": [20, 20, 20, 20],
        "net_income": [200, 200, 200, 200],
    }, index=idx)
    result = fcf_conversion(_ctx(df), cfg)
    # FCF ttm = (100-20)*4 = 320; net income ttm = 800; 320/800 = 0.40
    assert abs(result.value - 0.40) < 1e-9


def test_fcf_conversion_falls_back_to_fcf_over_ebitda_when_net_income_non_positive(cfg):
    """Loss-making company: FCF/net income would be negative and
    meaningless, so the spec's FCF/EBITDA fallback fires instead
    (live since finlake 0.3.0 added the D&A concept)."""
    idx = _quarterly_index(4)
    df = pd.DataFrame({
        "cfo": [100, 100, 100, 100], "capex": [20, 20, 20, 20],
        "net_income": [-10, -10, -10, -10],
        "operating_income": [15, 15, 15, 15],
        "depreciation_amortization": [10, 10, 10, 10],
    }, index=idx)
    result = fcf_conversion(_ctx(df), cfg)
    # FCF ttm = (100-20)*4 = 320
    # EBITDA ttm = EBIT(15*4=60) + D&A(10*4=40) = 100
    # 320 / 100 = 3.2
    assert result.value is not None
    assert abs(result.value - 3.2) < 1e-9
    assert "fcf_ebitda_substituted" in result.substitution


def test_fcf_conversion_unavailable_when_net_income_and_ebitda_both_non_positive(cfg):
    """A company losing money AND with negative EBITDA has no meaningful
    denominator at all -- reported missing, never sign-flipped."""
    idx = _quarterly_index(4)
    df = pd.DataFrame({
        "cfo": [100, 100, 100, 100], "capex": [20, 20, 20, 20],
        "net_income": [-10, -10, -10, -10],
        "operating_income": [-50, -50, -50, -50],
        "depreciation_amortization": [10, 10, 10, 10],
    }, index=idx)
    result = fcf_conversion(_ctx(df), cfg)
    # EBITDA = -200 + 40 = -160, non-positive
    assert result.value is None


# ------------------------------------------------------- interest_coverage
def test_interest_coverage_hand_computed(cfg):
    idx = _quarterly_index(4)
    df = pd.DataFrame({
        "operating_income": [50, 50, 50, 50],
        "interest_expense": [5, 5, 5, 5],
    }, index=idx)
    result = interest_coverage(_ctx(df), cfg)
    # EBIT ttm = 200, interest ttm = 20 -> 200/20 = 10.0
    assert abs(result.value - 10.0) < 1e-9


def test_interest_coverage_none_without_interest_expense(cfg):
    idx = _quarterly_index(4)
    df = pd.DataFrame({"operating_income": [50] * 4}, index=idx)
    result = interest_coverage(_ctx(df), cfg)
    assert result.value is None
