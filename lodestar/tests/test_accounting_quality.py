"""Hand-computed tests for the Accounting Quality bucket."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import load_config
from lodestar.metrics.accounting_quality import (
    accrual_ratio, beneish_m_score, cash_earnings_divergence, dso_dio_trend,
)
from lodestar.metrics.base import TickerContext

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _idx(n, start="2024-03-31"):
    return [d.date().isoformat() for d in pd.date_range(start, periods=n, freq="QE")]


def _ctx(df: pd.DataFrame) -> TickerContext:
    return TickerContext(ticker="TEST", as_of="2026-01-01", cik=1, fundamentals=df,
                          prices=pd.DataFrame(), sector="s", industry="i")


# --------------------------------------------------------------- accrual_ratio
def test_accrual_ratio_hand_computed_and_sign_flipped():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(5)
    df = pd.DataFrame({
        "assets": [1000.0, 1000.0, 1000.0, 1000.0, 1200.0],
        "liabilities": [500.0, 500.0, 500.0, 500.0, 500.0],
        "cash": [100.0, 100.0, 100.0, 100.0, 100.0],
        "debt_long": [0.0] * 5, "debt_short": [0.0] * 5,
    }, index=idx)
    result = accrual_ratio(_ctx(df), cfg_local)
    # NOA_then (idx[0]) = (1000-100) - (500-0) = 400
    # NOA_now (idx[4])  = (1200-100) - (500-0) = 600
    # avg NOA = 500; raw accrual ratio = (600-400)/500 = 0.40
    # NOA grew faster than the balance sheet would suggest via cash --
    # a HIGH raw accrual ratio is a LOW-quality signal, so the metric
    # (higher = better/cleaner) must be the negative of it: -0.40
    assert abs(result.raw_inputs["raw_accrual_ratio"] - 0.40) < 1e-9
    assert abs(result.value - (-0.40)) < 1e-9


def test_accrual_ratio_none_with_insufficient_history():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(3)
    df = pd.DataFrame({"assets": [1000.0] * 3, "liabilities": [500.0] * 3,
                        "cash": [100.0] * 3}, index=idx)
    result = accrual_ratio(_ctx(df), cfg_local)
    assert result.value is None


# --------------------------------------------------- cash_earnings_divergence
def test_cash_earnings_divergence_hand_computed(cfg):
    idx = _idx(4)
    df = pd.DataFrame({
        "cfo": [60.0] * 4, "net_income": [40.0] * 4, "revenue": [500.0] * 4,
    }, index=idx)
    result = cash_earnings_divergence(_ctx(df), cfg)
    # cfo ttm=240, ni ttm=160, revenue ttm=2000
    # gap = (240-160)/2000 = 80/2000 = 0.04
    assert abs(result.value - 0.04) < 1e-9


def test_cash_earnings_divergence_includes_trend_note_when_history_allows(cfg):
    idx = _idx(8)
    # Gap widens from year 1 to year 2: CFO grows relative to NI.
    cfo = [40.0] * 4 + [60.0] * 4
    ni = [40.0] * 4 + [40.0] * 4
    revenue = [500.0] * 8
    df = pd.DataFrame({"cfo": cfo, "net_income": ni, "revenue": revenue}, index=idx)
    result = cash_earnings_divergence(_ctx(df), cfg)
    assert result.note is not None and "->" in result.note


# ------------------------------------------------------------- beneish_m_score
def test_beneish_m_score_hand_computed_partial():
    """All six computable components present; AQI and DEPI are never
    computable (no PP&E/current-assets/D&A concepts) and are excluded
    from the weighted sum, not defaulted to zero or one.

    Independently computed here from the published Beneish coefficients
    (not by calling into the module's own arithmetic):

    DSRI = (150/1200)/(100/1000) = 0.125/0.10 = 1.25          * 0.920  =  1.150000
    GMI  = (400/1000)/(480/1200) = 0.40/0.40  = 1.00           * 0.528  =  0.528000
    SGI  = 1200/1000             = 1.20                        * 0.892  =  1.070400
    SGAI = (110/1200)/(100/1000) = 0.0916667/0.10 = 0.916667   * -0.172 = -0.157667
    LVGI = (720/1200)/(600/1000) = 0.60/0.60  = 1.00           * -0.327 = -0.327000
    TATA = (200-250)/1200        = -0.0416667                  * 4.679  = -0.194958

    sum of weighted components = 1.150000 + 0.528000 + 1.070400
                                  - 0.157667 - 0.327000 - 0.194958
                                = 2.068775
    raw M-score = -4.84 + 2.068775 = -2.771225
    metric value (negated) = 2.771225
    """
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(5)
    df = pd.DataFrame({
        "accounts_receivable": [100.0, 100.0, 100.0, 100.0, 150.0],
        "revenue": [1000.0, 1000.0, 1000.0, 1000.0, 1200.0],
        "gross_profit": [400.0, 400.0, 400.0, 400.0, 480.0],
        "sganda": [100.0, 100.0, 100.0, 100.0, 110.0],
        "liabilities": [600.0, 600.0, 600.0, 600.0, 720.0],
        "assets": [1000.0, 1000.0, 1000.0, 1000.0, 1200.0],
        "net_income": [50.0, 50.0, 50.0, 50.0, 50.0],  # ttm = 200
        "cfo": [62.5, 62.5, 62.5, 62.5, 62.5],           # ttm = 250
    }, index=idx)
    result = beneish_m_score(_ctx(df), cfg_local)

    assert result.value is not None
    assert sorted(result.raw_inputs["components_used"]) == [
        "DSRI", "GMI", "LVGI", "SGAI", "SGI", "TATA"
    ]
    expected_raw = -4.84 + (1.150000 + 0.528000 + 1.070400 - 0.157667 - 0.327000 - 0.194958)
    assert abs(result.raw_inputs["raw_m_score"] - expected_raw) < 1e-4
    assert abs(result.value - (-expected_raw)) < 1e-4
    assert result.substitution == "partial_m_score_6_of_6_computable_inputs"


def test_beneish_m_score_none_when_nothing_computable():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(5)
    df = pd.DataFrame({"assets": [1000.0] * 5}, index=idx)  # nothing usable
    result = beneish_m_score(_ctx(df), cfg_local)
    assert result.value is None


# ----------------------------------------------------------------- dso_dio_trend
def test_dso_dio_trend_hand_computed_worsening_is_negative():
    """AR/revenue held such that DSO rises steadily (collections
    slowing); inventory/cost_of_revenue omitted so only DSO drives the
    combined series. A worsening (rising) trend must produce a NEGATIVE
    metric value under the higher-is-better convention."""
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(16)
    revenue = [1000.0] * 16
    ar = [100.0 + 5.0 * i for i in range(16)]  # DSO rises every quarter
    df = pd.DataFrame({"accounts_receivable": ar, "revenue": revenue}, index=idx)
    result = dso_dio_trend(_ctx(df), cfg_local)
    assert result.value is not None
    assert result.value < 0, "DSO is rising (worsening) -- metric must be negative"
    assert result.raw_inputs["has_dso"] is True
    assert result.raw_inputs["has_dio"] is False


def test_dso_dio_trend_improving_collections_is_positive():
    cfg_local = load_config(REPO_CONFIG)
    idx = _idx(16)
    revenue = [1000.0] * 16
    ar = [200.0 - 5.0 * i for i in range(16)]  # DSO falls every quarter
    df = pd.DataFrame({"accounts_receivable": ar, "revenue": revenue}, index=idx)
    result = dso_dio_trend(_ctx(df), cfg_local)
    assert result.value is not None
    assert result.value > 0
