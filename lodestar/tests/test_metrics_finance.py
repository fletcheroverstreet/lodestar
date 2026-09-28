"""Hand-computed tests for the shared financial building blocks
(EBIT, effective tax rate, NOPAT, invested capital, enterprise value)."""

from __future__ import annotations

import pandas as pd
import pytest

from lodestar.config import load_config
from lodestar.metrics.base import TickerContext
from lodestar.metrics.finance import (
    ebit_ttm, effective_tax_rate, enterprise_value, invested_capital, nopat_ttm,
)

from pathlib import Path

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def _ctx(fundamentals: dict, prices: dict | None = None, as_of="2026-01-01") -> TickerContext:
    df = pd.DataFrame(fundamentals) if fundamentals else pd.DataFrame()
    px = pd.DataFrame(prices) if prices else pd.DataFrame()
    return TickerContext(
        ticker="TEST", as_of=as_of, cik=1, fundamentals=df, prices=px,
        sector="Test Sector", industry="Test Industry",
    )


# 4 quarters of data, used throughout this file
Q_INDEX = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]


# ---------------------------------------------------------------- ebit_ttm
def test_ebit_ttm_prefers_operating_income():
    ctx = _ctx({"operating_income": [100, 100, 100, 100]}, prices=None)
    ctx = TickerContext(**{**ctx.__dict__, "fundamentals": pd.DataFrame(
        {"operating_income": [100, 100, 100, 100]}, index=Q_INDEX)})
    result = ebit_ttm(ctx)
    assert result.value == 400
    assert result.substitution is None


def test_ebit_ttm_falls_back_to_pretax_plus_interest():
    df = pd.DataFrame({
        "pretax_income": [80, 80, 80, 80],
        "interest_expense": [20, 20, 20, 20],
    }, index=Q_INDEX)
    ctx = _ctx({})
    ctx = TickerContext(**{**ctx.__dict__, "fundamentals": df})
    result = ebit_ttm(ctx)
    # EBIT = pretax_income + interest_expense = (80+20)*4 = 400
    assert result.value == 400
    assert result.substitution == "ebit_proxy_pretax_plus_interest"
    assert result.raw_inputs["pretax_income_ttm"] == 320
    assert result.raw_inputs["interest_expense_ttm"] == 80


def test_ebit_ttm_none_when_nothing_available():
    ctx = _ctx({})
    result = ebit_ttm(ctx)
    assert result.value is None


# ------------------------------------------------------- effective_tax_rate
def test_effective_tax_rate_normal_case():
    cfg = load_config(REPO_CONFIG)
    df = pd.DataFrame({
        "pretax_income": [100, 100, 100, 100],
        "tax_expense": [21, 21, 21, 21],
    }, index=Q_INDEX)
    ctx = _ctx({})
    ctx = TickerContext(**{**ctx.__dict__, "fundamentals": df})
    result = effective_tax_rate(ctx, cfg)
    assert abs(result.value - 0.21) < 1e-9
    assert result.substitution is None


def test_effective_tax_rate_defaults_when_pretax_income_non_positive():
    cfg = load_config(REPO_CONFIG)
    df = pd.DataFrame({
        "pretax_income": [-10, -10, -10, -10],
        "tax_expense": [1, 1, 1, 1],
    }, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df})
    result = effective_tax_rate(ctx, cfg)
    assert result.value == cfg.bucket("quality").param("default_tax_rate")
    assert result.substitution == "default_tax_rate_no_pretax_income"


def test_effective_tax_rate_defaults_when_out_of_bounds():
    """A huge one-time tax charge against small pretax income produces an
    implausible rate (here: 400/100 = 4.0 = 400%) -- must be capped, not
    used directly."""
    cfg = load_config(REPO_CONFIG)
    df = pd.DataFrame({
        "pretax_income": [25, 25, 25, 25],
        "tax_expense": [100, 100, 100, 100],
    }, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df})
    result = effective_tax_rate(ctx, cfg)
    assert result.value == cfg.bucket("quality").param("default_tax_rate")
    assert result.substitution == "default_tax_rate_out_of_bounds"
    assert result.raw_inputs["computed_rate_before_bounding"] == 4.0


# ------------------------------------------------------------------ nopat
def test_nopat_hand_computed():
    cfg = load_config(REPO_CONFIG)
    df = pd.DataFrame({
        "operating_income": [100, 100, 100, 100],
        "pretax_income": [100, 100, 100, 100],
        "tax_expense": [21, 21, 21, 21],
    }, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df})
    result = nopat_ttm(ctx, cfg)
    # EBIT ttm = 400, tax rate = 21/100 = 0.21
    # NOPAT = 400 * (1 - 0.21) = 400 * 0.79 = 316.0
    assert abs(result.value - 316.0) < 1e-9
    assert result.substitution is None


# ------------------------------------------------------------ invested_capital
def test_invested_capital_hand_computed():
    df = pd.DataFrame({
        "debt_long": [200, 200, 200, 200],
        "debt_short": [50, 50, 50, 50],
        "equity": [500, 500, 500, 500],
        "cash": [80, 80, 80, 80],
        "short_term_investments": [20, 20, 20, 20],
    }, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df})
    result = invested_capital(ctx)
    # 200 + 50 + 500 - 80 - 20 = 650
    assert result.value == 650
    assert result.substitution is None


def test_invested_capital_defaults_missing_debt_component_to_zero_and_flags_it():
    df = pd.DataFrame({
        "equity": [500, 500, 500, 500],
        "cash": [80, 80, 80, 80],
        # no debt_long, no debt_short, no short_term_investments columns at all
    }, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df})
    result = invested_capital(ctx)
    # 0 + 0 + 500 - 80 - 0 = 420
    assert result.value == 420
    assert result.substitution == "debt_component_defaulted_zero"


def test_invested_capital_none_without_equity_or_cash():
    df = pd.DataFrame({"debt_long": [200, 200, 200, 200]}, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df})
    assert invested_capital(ctx).value is None


def test_invested_capital_as_of_a_historical_period_end():
    """A ROIC time series needs invested capital AT EACH historical
    quarter, not just the latest -- verify the period_end filter works."""
    df = pd.DataFrame({
        "equity": [400, 450, 500, 550],
        "cash": [50, 60, 70, 80],
    }, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df})
    result = invested_capital(ctx, period_end="2025-06-30")
    # as of Q2: equity=450, cash=60 -> 0 + 450 - 60 - 0 = 390
    assert result.value == 390


# ------------------------------------------------------------- enterprise_value
def test_enterprise_value_hand_computed():
    df = pd.DataFrame({
        "shares_outstanding": [10_000_000, 10_000_000, 10_000_000, 10_000_000],
        "debt_long": [50_000_000, 50_000_000, 50_000_000, 50_000_000],
        "debt_short": [5_000_000, 5_000_000, 5_000_000, 5_000_000],
        "cash": [20_000_000, 20_000_000, 20_000_000, 20_000_000],
        "short_term_investments": [10_000_000, 10_000_000, 10_000_000, 10_000_000],
    }, index=Q_INDEX)
    px = pd.DataFrame({"date": ["2025-12-31"], "close": [15.0], "adj_close": [15.0]})
    ctx = TickerContext(**{**_ctx({}, as_of="2026-01-01").__dict__,
                            "fundamentals": df, "prices": px})
    result = enterprise_value(ctx)
    # market cap = 10,000,000 * 15.0 = 150,000,000
    # EV = 150,000,000 + 50,000,000 + 5,000,000 - 20,000,000 - 10,000,000
    #    = 175,000,000
    assert result.value == 175_000_000
    assert result.substitution is None
    assert result.raw_inputs["market_cap"] == 150_000_000


def test_enterprise_value_none_without_price_data():
    df = pd.DataFrame({"shares_outstanding": [10_000_000] * 4}, index=Q_INDEX)
    ctx = TickerContext(**{**_ctx({}).__dict__, "fundamentals": df, "prices": pd.DataFrame()})
    assert enterprise_value(ctx).value is None
