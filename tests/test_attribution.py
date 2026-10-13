"""Hand-computed tests for attribution's metric-contribution decomposition
and top-driver selection."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import BUCKET_NAMES, load_config
from lodestar.metrics.base import MetricResult, TickerContext
from lodestar.scoring.attribution import MetricDriver, compute_attribution, _split_top_drivers
from lodestar.scoring.bucket import TickerBucketResult
from lodestar.scoring.composite import compute_composite
from lodestar.scoring.coverage import BucketSubscore
from lodestar.scoring.zscore import ZScoreResult

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _bucket_result(ticker, bucket, subscore, metric_zs: dict[str, float],
                    metric_weights_used: dict[str, float], low_confidence=False) -> TickerBucketResult:
    metric_results = {
        m: MetricResult(name=m, bucket=bucket, value=1.23) for m in metric_zs
    }
    zscore_results = {
        m: ZScoreResult(ticker=ticker, raw_value=1.23, winsorized_value=1.23, z=z,
                         peer_level="industry", peer_group_size=8, peer_mean=0.0, peer_std=1.0)
        for m, z in metric_zs.items()
    }
    return TickerBucketResult(
        ticker=ticker, bucket=bucket, metric_results=metric_results, zscore_results=zscore_results,
        subscore=BucketSubscore(ticker=ticker, bucket=bucket, subscore=subscore,
                                 coverage=1.0, low_confidence=low_confidence,
                                 metric_weights_used=metric_weights_used),
    )


def test_metric_contribution_is_an_exact_linear_decomposition(cfg):
    """4 tickers, one bucket (quality) with two metrics, weights 0.6/0.4.
    Every other bucket is given a distinct-but-simple subscore per
    ticker (not all-identical, so re-standardization doesn't neutralize
    them per the zero-variance handling in composite.py) -- what matters
    for THIS test is only that quality's per-metric contributions sum to
    EXACTLY quality's own composite contribution, which is a property
    that must hold regardless of what the other buckets contain.
    """
    tickers = ["t1", "t2", "t3", "t4"]
    bucket_results: dict[str, dict[str, TickerBucketResult]] = {}

    # quality: two metrics, hand-picked z's and weights (0.6 roic, 0.4
    # fcf_conversion), subscore = 0.6*z_roic + 0.4*z_fcf for each ticker.
    roic_z = {"t1": 1.0, "t2": -0.5, "t3": 2.0, "t4": 0.0}
    fcf_z = {"t1": 0.5, "t2": 1.0, "t3": -1.0, "t4": 0.2}
    bucket_results["quality"] = {
        t: _bucket_result(t, "quality", 0.6 * roic_z[t] + 0.4 * fcf_z[t],
                           {"roic": roic_z[t], "fcf_conversion": fcf_z[t]},
                           {"roic": 0.6, "fcf_conversion": 0.4})
        for t in tickers
    }
    for bucket in BUCKET_NAMES:
        if bucket == "quality":
            continue
        vals = {"t1": 0.1, "t2": 0.3, "t3": -0.2, "t4": 0.05}
        bucket_results[bucket] = {
            t: _bucket_result(t, bucket, vals[t], {"x": vals[t]}, {"x": 1.0})
            for t in tickers
        }

    composite = compute_composite(bucket_results, cfg)
    ctx = TickerContext(ticker="t1", as_of="2026-01-01", cik=1,
                         fundamentals=pd.DataFrame(), prices=pd.DataFrame(), sector="s", industry="i")

    attr = compute_attribution("t1", "2026-01-01", bucket_results, composite, cfg, ctx=ctx)
    quality_attr = attr.buckets["quality"]

    roic_driver = next(d for d in attr.top_drivers_up + attr.top_drivers_down if d.metric == "roic")
    fcf_driver = next(
        (d for d in attr.top_drivers_up + attr.top_drivers_down if d.metric == "fcf_conversion"),
        None,
    )
    # roic + fcf_conversion contributions must sum to EXACTLY quality's
    # own composite_contribution (the whole point of the linear
    # decomposition claimed in attribution.py's module docstring).
    total = roic_driver.contribution + (fcf_driver.contribution if fcf_driver else 0.0)
    assert abs(total - quality_attr.composite_contribution) < 1e-9


def test_low_confidence_bucket_has_zero_weight_and_contributes_no_drivers(cfg):
    tickers = ["t1", "t2", "t3", "t4"]
    bucket_results: dict[str, dict[str, TickerBucketResult]] = {}
    for bucket in BUCKET_NAMES:
        vals = {"t1": None, "t2": 0.3, "t3": -0.2, "t4": 0.05}
        bucket_results[bucket] = {
            t: _bucket_result(t, bucket, vals[t] if bucket == "quality" else 0.1,
                               {"x": vals[t] if bucket == "quality" else 0.1}, {"x": 1.0},
                               low_confidence=(bucket == "quality" and t == "t1"))
            for t in tickers
        }
    composite = compute_composite(bucket_results, cfg)
    ctx = TickerContext(ticker="t1", as_of="2026-01-01", cik=1,
                         fundamentals=pd.DataFrame(), prices=pd.DataFrame(), sector="s", industry="i")
    attr = compute_attribution("t1", "2026-01-01", bucket_results, composite, cfg, ctx=ctx)

    assert attr.buckets["quality"].weight_used == 0.0
    assert attr.buckets["quality"].low_confidence is True
    assert not any(d.bucket == "quality" for d in attr.top_drivers_up + attr.top_drivers_down)


# ------------------------------------------------------------- top drivers
def test_split_top_drivers_hand_computed():
    drivers = [
        MetricDriver(metric=f"m{i}", bucket="quality", z=0.0, raw_value=None, contribution=c)
        for i, c in enumerate([5.0, 3.0, 1.0, -1.0, -3.0, -5.0])
    ]
    up, down = _split_top_drivers(drivers, top_n=3)
    assert [d.contribution for d in up] == [5.0, 3.0, 1.0]
    assert [d.contribution for d in down] == [-5.0, -3.0, -1.0]


def test_split_top_drivers_no_overlap_with_few_drivers():
    drivers = [
        MetricDriver(metric="a", bucket="quality", z=0.0, raw_value=None, contribution=2.0),
        MetricDriver(metric="b", bucket="quality", z=0.0, raw_value=None, contribution=-1.0),
    ]
    up, down = _split_top_drivers(drivers, top_n=3)
    up_names = {d.metric for d in up}
    down_names = {d.metric for d in down}
    assert not (up_names & down_names), "the same driver must never appear in both lists"


def test_split_top_drivers_empty_list():
    up, down = _split_top_drivers([], top_n=3)
    assert up == [] and down == []


# --------------------------------------------------------------- data_as_of
def test_data_as_of_reads_latest_filed_date_from_context():
    df = pd.DataFrame({
        "revenue": [1.0, 2.0], "revenue__filed": ["2025-01-01", "2025-06-15"],
        "net_income": [1.0, 2.0], "net_income__filed": ["2025-02-01", "2025-07-20"],
    }, index=["2024-12-31", "2025-03-31"])
    ctx = TickerContext(ticker="T", as_of="2026-01-01", cik=1, fundamentals=df,
                         prices=pd.DataFrame(), sector="s", industry="i")
    cfg = load_config(REPO_CONFIG)
    bucket_results = {
        b: {"T": _bucket_result("T", b, 0.1, {"x": 0.1}, {"x": 1.0})}
        for b in BUCKET_NAMES
    }
    composite = compute_composite(bucket_results, cfg)
    attr = compute_attribution("T", "2026-01-01", bucket_results, composite, cfg, ctx=ctx)
    assert attr.data_as_of == "2025-07-20"


def test_data_as_of_none_without_context():
    cfg = load_config(REPO_CONFIG)
    bucket_results = {
        b: {"T": _bucket_result("T", b, 0.1, {"x": 0.1}, {"x": 1.0})}
        for b in BUCKET_NAMES
    }
    composite = compute_composite(bucket_results, cfg)
    attr = compute_attribution("T", "2026-01-01", bucket_results, composite, cfg, ctx=None)
    assert attr.data_as_of is None
