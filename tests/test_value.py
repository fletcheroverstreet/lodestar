"""Hand-computed tests for the Value bucket."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import load_config
from lodestar.metrics.base import TickerContext
from lodestar.metrics.value import ev_ebit, fcf_yield, pb

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _idx(n=4, start="2025-03-31"):
    return [d.date().isoformat() for d in pd.date_range(start, periods=n, freq="QE")]


def _ctx(df: pd.DataFrame, px: pd.DataFrame, sector="Information Technology") -> TickerContext:
    return TickerContext(ticker="TEST", as_of="2026-01-01", cik=1, fundamentals=df,
                          prices=px, sector=sector, industry="i")


def _px(price=20.0, date="2025-12-31"):
    return pd.DataFrame({"date": [date], "close": [price], "adj_close": [price]})


# ------------------------------------------------------------------- ev_ebit
def test_ev_ebit_uses_earnings_yield_when_healthy(cfg):
    idx = _idx()
    df = pd.DataFrame({
        "operating_income": [30, 30, 30, 30],  # ttm = 120
        "revenue": [100, 100, 100, 100],       # ebit margin = 120/400 = 0.30, well above the floor
        "shares_outstanding": [1_000_000] * 4,
        "debt_long": [0] * 4, "debt_short": [0] * 4, "cash": [0] * 4,
    }, index=idx)
    ctx = _ctx(df, _px(price=10.0))  # market cap = 10,000,000; EV = 10,000,000 (no debt/cash)
    result = ev_ebit(ctx, cfg)
    # EBIT ttm / EV = 120 / 10,000,000
    assert abs(result.value - 120 / 10_000_000) < 1e-12
    assert result.substitution is None


def test_ev_ebit_substitutes_sales_yield_when_ebit_non_positive(cfg):
    idx = _idx()
    df = pd.DataFrame({
        "operating_income": [-5, -5, -5, -5],  # ttm = -20, non-positive
        "revenue": [100, 100, 100, 100],       # ttm = 400
        "shares_outstanding": [1_000_000] * 4,
        "debt_long": [0] * 4, "debt_short": [0] * 4, "cash": [0] * 4,
    }, index=idx)
    ctx = _ctx(df, _px(price=10.0))  # EV = 10,000,000
    result = ev_ebit(ctx, cfg)
    assert abs(result.value - 400 / 10_000_000) < 1e-12
    assert "ev_sales_substituted" in result.substitution


def test_ev_ebit_substitutes_sales_yield_when_margin_below_floor(cfg):
    idx = _idx()
    # EBIT ttm = 4 (positive!) but revenue ttm = 400 -> margin = 4/400 = 0.01,
    # below the configured floor of 0.03 -- must still substitute.
    df = pd.DataFrame({
        "operating_income": [1, 1, 1, 1],
        "revenue": [100, 100, 100, 100],
        "shares_outstanding": [1_000_000] * 4,
        "debt_long": [0] * 4, "debt_short": [0] * 4, "cash": [0] * 4,
    }, index=idx)
    ctx = _ctx(df, _px(price=10.0))
    result = ev_ebit(ctx, cfg)
    assert "ev_sales_substituted" in result.substitution
    assert abs(result.value - 400 / 10_000_000) < 1e-12


def test_ev_ebit_none_without_ev():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx()
    df = pd.DataFrame({"operating_income": [30] * 4, "revenue": [100] * 4}, index=idx)
    ctx = _ctx(df, pd.DataFrame())  # no price data -> no EV
    result = ev_ebit(ctx, cfg_local)
    assert result.value is None


# ------------------------------------------------------------------ fcf_yield
def test_fcf_yield_hand_computed(cfg):
    idx = _idx()
    df = pd.DataFrame({
        "cfo": [50, 50, 50, 50], "capex": [10, 10, 10, 10],
        "shares_outstanding": [1_000_000] * 4,
        "debt_long": [0] * 4, "debt_short": [0] * 4, "cash": [0] * 4,
    }, index=idx)
    ctx = _ctx(df, _px(price=8.0))  # EV = 8,000,000
    result = fcf_yield(ctx, cfg)
    # FCF ttm = (50-10)*4 = 160; yield = 160/8,000,000
    assert abs(result.value - 160 / 8_000_000) < 1e-12


# ------------------------------------------------------------------------ pb
def test_pb_computed_for_financials(cfg):
    idx = _idx()
    df = pd.DataFrame({
        "equity": [500_000_000] * 4, "shares_outstanding": [10_000_000] * 4,
    }, index=idx)
    ctx = _ctx(df, _px(price=60.0), sector="Financials")
    result = pb(ctx, cfg)
    # market cap = 10,000,000 * 60 = 600,000,000
    # book/market = 500,000,000 / 600,000,000 = 0.8333...
    assert abs(result.value - (500_000_000 / 600_000_000)) < 1e-9


def test_pb_not_applicable_outside_financials(cfg):
    idx = _idx()
    df = pd.DataFrame({
        "equity": [500_000_000] * 4, "shares_outstanding": [10_000_000] * 4,
    }, index=idx)
    ctx = _ctx(df, _px(price=60.0), sector="Information Technology")
    result = pb(ctx, cfg)
    assert result.value is None
    assert result.substitution == "not_applicable_non_financials"
