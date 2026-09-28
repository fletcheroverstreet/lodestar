"""Hand-computed tests for the Momentum bucket."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.adapter import Fundamentals
from lodestar.config import load_config
from lodestar.metrics.base import TickerContext
from lodestar.metrics.momentum import fundamental_revision, price_12_1, rank_change

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _ctx(df=None, px=None, as_of="2026-01-01", prior_ranks=()) -> TickerContext:
    return TickerContext(
        ticker="TEST", as_of=as_of, cik=1,
        fundamentals=df if df is not None else pd.DataFrame(),
        prices=px if px is not None else pd.DataFrame(),
        sector="s", industry="i", prior_rank_history=prior_ranks,
    )


# ------------------------------------------------------------------ price_12_1
def test_price_12_1_hand_computed():
    # 300 trading days of daily bars ending on the as_of date. Price is
    # flat at 100 except two marked days so the exact lookback prices are
    # unambiguous: t-252 = 80.0, t-21 = 120.0.
    n = 300
    dates = pd.bdate_range(end="2026-01-01", periods=n)
    closes = [100.0] * n
    closes[-1 - 252] = 80.0
    closes[-1 - 21] = 120.0
    df = pd.DataFrame({
        "date": [d.date().isoformat() for d in dates],
        "close": closes, "adj_close": closes,
    })
    result = price_12_1(_ctx(px=df, as_of="2026-01-01"), load_config(REPO_CONFIG))
    # (120 - 80) / 80 = 0.50
    assert abs(result.value - 0.50) < 1e-9
    assert result.raw_inputs["price_t_minus_252"] == 80.0
    assert result.raw_inputs["price_t_minus_21"] == 120.0


def test_price_12_1_none_with_insufficient_history():
    n = 100  # fewer than 252 trading days
    dates = pd.bdate_range(end="2026-01-01", periods=n)
    df = pd.DataFrame({"date": [d.date().isoformat() for d in dates],
                        "close": [100.0] * n, "adj_close": [100.0] * n})
    result = price_12_1(_ctx(px=df, as_of="2026-01-01"), load_config(REPO_CONFIG))
    assert result.value is None


# ------------------------------------------------------- fundamental_revision
def test_fundamental_revision_hand_computed(cfg, monkeypatch):
    now_df = pd.DataFrame({
        "eps_diluted": [1.0, 1.0, 1.0, 1.5],
        "revenue": [100.0, 100.0, 100.0, 130.0],
    }, index=["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"])
    ctx = _ctx(df=now_df, as_of="2026-01-01")

    earlier_df = pd.DataFrame({
        "eps_diluted": [1.0, 1.0, 1.0, 1.0],
        "revenue": [100.0, 100.0, 100.0, 100.0],
    }, index=["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"])

    def fake_get_fundamentals(ticker, *, as_of, years=3):
        return Fundamentals(ticker=ticker, cik=1, as_of=as_of, frame=earlier_df)

    monkeypatch.setattr("lodestar.metrics.momentum.get_fundamentals", fake_get_fundamentals)

    result = fundamental_revision(ctx, cfg)
    # eps: now=1.5 (latest reported), then=1.0 -> (1.5-1.0)/1.0 = 0.50
    # revenue ttm: now = 1+1+1+1.3(00s)=430, then = 400 -> (430-400)/400=0.075
    # average = (0.50 + 0.075)/2 = 0.2875
    assert abs(result.value - 0.2875) < 1e-9
    assert result.substitution == "proxy_fundamental_revision_not_analyst_estimates"


def test_fundamental_revision_none_when_earlier_snapshot_empty(cfg, monkeypatch):
    ctx = _ctx(df=pd.DataFrame({"eps_diluted": [1.0]}, index=["2025-12-31"]))

    def fake_get_fundamentals(ticker, *, as_of, years=3):
        return Fundamentals(ticker=ticker, cik=None, as_of=as_of, frame=pd.DataFrame())

    monkeypatch.setattr("lodestar.metrics.momentum.get_fundamentals", fake_get_fundamentals)
    result = fundamental_revision(ctx, cfg)
    assert result.value is None


# -------------------------------------------------------------- rank_change
def test_rank_change_hand_computed_improvement(cfg):
    # percentile ranks from the last 3 completed runs, oldest first
    ctx = _ctx(prior_ranks=(40.0, 55.0, 70.0))
    result = rank_change(ctx, cfg)
    # most recent two: 55.0 -> 70.0, change = +15.0
    assert result.value == 15.0


def test_rank_change_hand_computed_decline(cfg):
    ctx = _ctx(prior_ranks=(80.0, 60.0))
    result = rank_change(ctx, cfg)
    assert result.value == -20.0


def test_rank_change_none_on_first_run(cfg):
    ctx = _ctx(prior_ranks=())
    result = rank_change(ctx, cfg)
    assert result.value is None


def test_rank_change_none_on_second_run(cfg):
    ctx = _ctx(prior_ranks=(50.0,))
    result = rank_change(ctx, cfg)
    assert result.value is None
