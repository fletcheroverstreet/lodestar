"""Hand-computed tests for the Growth bucket."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import load_config
from lodestar.metrics.base import TickerContext
from lodestar.metrics.growth import (
    fcf_cagr, incremental_margin, reinvestment_rate, revenue_cagr_3y, revenue_cagr_5y,
)

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _idx(n, start="2020-03-31"):
    return [d.date().isoformat() for d in pd.date_range(start, periods=n, freq="QE")]


def _ctx(df: pd.DataFrame, as_of="2026-01-01") -> TickerContext:
    return TickerContext(ticker="TEST", as_of=as_of, cik=1, fundamentals=df,
                          prices=pd.DataFrame(), sector="s", industry="i")


# --------------------------------------------------------- revenue CAGR
def test_revenue_cagr_3y_and_5y_hand_computed(cfg):
    """24 quarters (6 years), revenue flat WITHIN each year at
    100 * 1.1^(year-1) per quarter, so year-end TTM values are exactly:
      Y1=400, Y2=440, Y3=484, Y4=532.4, Y5=585.64, Y6=644.204
    (each year's TTM is 10% above the last, by construction).
    3y CAGR = (Y4/Y1)^(1/3) - 1 = (532.4/400)^(1/3) - 1 = 1.1 - 1 = 0.10
    5y CAGR = (Y6/Y1)^(1/5) - 1 = (644.204/400)^(1/5) - 1 = 1.1 - 1 = 0.10
    (1.331 = 1.1^3 and 1.61051 = 1.1^5 exactly, so both come out to a
    clean 10% -- not a coincidence, chosen so the hand check is exact.)
    """
    idx = _idx(24)
    revenue = []
    for year in range(6):
        revenue += [100.0 * (1.1 ** year)] * 4
    df = pd.DataFrame({"revenue": revenue}, index=idx)
    ctx = _ctx(df)

    r3 = revenue_cagr_3y(ctx, cfg)
    assert r3.value is not None
    assert abs(r3.value - 0.10) < 1e-6

    r5 = revenue_cagr_5y(ctx, cfg)
    assert r5.value is not None
    assert abs(r5.value - 0.10) < 1e-6


def test_revenue_cagr_3y_none_with_insufficient_history(cfg):
    idx = _idx(8)  # only 2 years -- not enough for a 3y-apart comparison
    df = pd.DataFrame({"revenue": [100.0] * 8}, index=idx)
    result = revenue_cagr_3y(_ctx(df), cfg)
    assert result.value is None


def test_revenue_cagr_flags_a_late_filed_endpoint_as_possible_restatement(cfg):
    idx = _idx(16)
    revenue = [100.0] * 16
    filed = [f"{d[:4]}-{'04' if i % 4 == 0 else '07'}-01" for i, d in enumerate(idx)]
    # revenue_cagr_3y's 3-years-ago TTM window ENDS at idx[3] (the 4th raw
    # quarter -- ttm_series' first output point, since it takes 4 raw
    # quarters to produce one TTM value). Make THAT quarter's filed date
    # absurdly late -- a real quarterly filing is never ~800 days after
    # its own period_end.
    filed[3] = "2022-08-01"  # idx[3] = 2020-12-31 -> ~843 days late
    df = pd.DataFrame({"revenue": revenue, "revenue__filed": filed}, index=idx)
    result = revenue_cagr_3y(_ctx(df), cfg)
    assert result.raw_inputs["begin_period"] == idx[3]
    assert result.substitution == "possible_restatement_in_window"
    assert "filed" in (result.note or "")


# -------------------------------------------------------------- fcf_cagr
def test_fcf_cagr_positive_base_hand_computed(cfg):
    idx = _idx(16)
    # CFO flat at 150/quarter, capex flat at 50/quarter within each year,
    # except capex declines each year so FCF grows cleanly.
    # Year FCF (ttm) chosen as 400, 440, 484, 532.4 (again a clean 10%/yr).
    cfo = [150.0] * 16
    # FCF_ttm = CFO_ttm - capex_ttm = 600 - capex_ttm(year) = target
    # year1 target 400 -> capex_ttm=200 -> 50/quarter
    # year2 target 440 -> capex_ttm=160 -> 40/quarter
    # year3 target 484 -> capex_ttm=116 -> 29/quarter
    # year4 target 532.4 -> capex_ttm=67.6 -> 16.9/quarter
    capex = [50.0] * 4 + [40.0] * 4 + [29.0] * 4 + [16.9] * 4
    df = pd.DataFrame({"cfo": cfo, "capex": capex}, index=idx)
    result = fcf_cagr(_ctx(df), cfg)
    assert result.value is not None
    assert abs(result.value - 0.10) < 1e-6
    assert result.substitution is None


def test_fcf_cagr_negative_base_uses_revenue_scaled_fallback(cfg):
    idx = _idx(16)
    # FCF ttm at year1 is negative (cash-burning early-stage-like name),
    # positive by year4.
    cfo = [10.0] * 4 + [10.0] * 4 + [40.0] * 4 + [60.0] * 4
    capex = [30.0] * 4 + [20.0] * 4 + [10.0] * 4 + [5.0] * 4
    revenue = [200.0] * 16
    df = pd.DataFrame({"cfo": cfo, "capex": capex, "revenue": revenue}, index=idx)
    result = fcf_cagr(_ctx(df), cfg)
    assert result.substitution == "fcf_cagr_scaled_by_revenue_fallback"
    # FCF ttm year1 = (10-30)*4 = -80 (the base, confirmed <= 0)
    assert result.raw_inputs["fcf_ttm_begin"] == -80.0


# ------------------------------------------------------- incremental_margin
def test_incremental_margin_hand_computed(cfg):
    """12 quarters. EBIT and revenue both flat within each of 3 "years"
    of 4 quarters, window=8 quarters (2 years) per config.yaml default.
    Year1 TTM: EBIT=200, revenue=1000. Year3 TTM: EBIT=280, revenue=1200.
    incremental margin = (280-200)/(1200-1000) = 80/200 = 0.40
    """
    idx = _idx(12)
    ebit = [50.0] * 4 + [60.0] * 4 + [70.0] * 4
    revenue = [250.0] * 4 + [280.0] * 4 + [300.0] * 4
    df = pd.DataFrame({"operating_income": ebit, "revenue": revenue}, index=idx)
    result = incremental_margin(_ctx(df), cfg)
    assert result.value is not None
    # TTM EBIT: y1=200, y2=240, y3=280; TTM revenue: y1=1000,y2=1120,y3=1200
    # 8-quarter-window (2 years) comparison: end=y3 TTM, begin=y1 TTM
    assert abs(result.value - ((280 - 200) / (1200 - 1000))) < 1e-9


# ------------------------------------------------------- reinvestment_rate
def test_reinvestment_rate_unavailable_without_da_concept(cfg):
    idx = _idx(4)
    df = pd.DataFrame({
        "capex": [10.0] * 4, "operating_income": [50.0] * 4,
        "pretax_income": [50.0] * 4, "tax_expense": [10.0] * 4, "equity": [1000.0] * 4,
    }, index=idx)
    result = reinvestment_rate(_ctx(df), cfg)
    assert result.value is None
    assert result.substitution == "blocked_missing_da_concept"


def test_reinvestment_rate_hand_computed_when_da_available(cfg):
    """Once a D&A concept exists, the formula itself must be correct --
    tested here by injecting a synthetic depreciation_amortization
    column, ready for the day FINLAKE-FINDINGS.md F14 lands."""
    idx = _idx(5)
    df = pd.DataFrame({
        "capex": [30.0] * 5,
        "depreciation_amortization": [10.0] * 5,
        "operating_income": [100.0] * 5, "pretax_income": [100.0] * 5,
        "tax_expense": [20.0] * 5, "equity": [1000.0] * 5,
        "accounts_receivable": [200.0, 200.0, 200.0, 200.0, 250.0],
        "inventory": [0.0] * 5,
        "accounts_payable": [0.0] * 5,
    }, index=idx)
    result = reinvestment_rate(_ctx(df), cfg)
    # capex ttm = 30*4=120, da ttm = 10*4=40
    # NOPAT ttm: EBIT ttm=400, tax rate=20/100=0.20, NOPAT=400*0.8=320
    # delta NWC = AR[-1] - AR[-5] = 250 - 200 = 50 (inventory/AP both flat at 0)
    # reinvestment rate = (120 - 40 + 50) / 320 = 130/320 = 0.40625
    assert result.value is not None
    assert abs(result.value - (130 / 320)) < 1e-9


def test_reinvestment_rate_guarded_when_nopat_non_positive(cfg):
    idx = _idx(5)
    df = pd.DataFrame({
        "capex": [30.0] * 5, "depreciation_amortization": [10.0] * 5,
        "operating_income": [-100.0] * 5, "pretax_income": [-100.0] * 5,
        "tax_expense": [0.0] * 5, "equity": [1000.0] * 5,
    }, index=idx)
    result = reinvestment_rate(_ctx(df), cfg)
    assert result.value is None
