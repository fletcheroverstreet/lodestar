"""Hand-computed tests for the composite blend: bucket re-standardization,
the coverage-floor exclusion + renormalization, and the asymmetric
accounting-quality penalty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lodestar.config import BUCKET_NAMES, load_config
from lodestar.scoring.bucket import TickerBucketResult
from lodestar.scoring.composite import compute_composite
from lodestar.scoring.coverage import BucketSubscore

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _fake_result(ticker: str, bucket: str, subscore: float | None,
                  low_confidence: bool = False) -> TickerBucketResult:
    return TickerBucketResult(
        ticker=ticker, bucket=bucket, metric_results={}, zscore_results={},
        subscore=BucketSubscore(
            ticker=ticker, bucket=bucket, subscore=subscore,
            coverage=0.0 if subscore is None else 1.0,
            low_confidence=low_confidence, metric_weights_used={},
        ),
    )


def _all_buckets_available(tickers: list[str], subscores: dict[str, dict[str, float]]):
    """subscores[bucket][ticker] -> raw subscore. Every bucket/ticker not
    listed defaults to 0.0 (a neutral filler so re-standardization has
    something to work with)."""
    out: dict[str, dict[str, TickerBucketResult]] = {}
    for bucket in BUCKET_NAMES:
        out[bucket] = {}
        for t in tickers:
            val = subscores.get(bucket, {}).get(t, 0.0)
            out[bucket][t] = _fake_result(t, bucket, val)
    return out


def test_composite_percentile_ranking_hand_computed(cfg):
    """4 tickers, every bucket identical for all of them EXCEPT quality,
    which is strictly increasing t1<t2<t3<t4 -- since every other bucket
    is tied, the composite ranking must follow quality's ranking exactly,
    and with 4 tied-except-one names the percentile ranks are the
    well-known pandas default (average method) values for rank 1..4 of 4:
    25, 50, 75, 100.
    """
    tickers = ["t1", "t2", "t3", "t4"]
    subscores = {"quality": {"t1": 1.0, "t2": 2.0, "t3": 3.0, "t4": 4.0}}
    bucket_results = _all_buckets_available(tickers, subscores)
    results = compute_composite(bucket_results, cfg)

    order = sorted(tickers, key=lambda t: results[t].percentile)
    assert order == ["t1", "t2", "t3", "t4"]
    assert results["t4"].percentile == 100.0
    assert results["t1"].percentile == 25.0


def test_accounting_penalty_caps_a_favourable_score():
    cfg = load_config(REPO_CONFIG)
    cap = cfg.bucket("accounting_quality").param("good_score_cap_z")
    tickers = ["good", "great", "bad", "neutral"]
    # accounting_quality raw subscores chosen so re-standardization
    # (z-score across these 4) produces one clearly ABOVE the cap.
    subscores = {"accounting_quality": {"good": 1.0, "great": 100.0, "bad": -1.0, "neutral": 0.0}}
    bucket_results = _all_buckets_available(tickers, subscores)
    results = compute_composite(bucket_results, cfg)

    great_z = results["great"].bucket_z_universe["accounting_quality"]
    assert great_z > cap, "fixture must actually produce a z above the cap to test capping"
    assert results["great"].accounting_penalty_capped is True

    # The accounting contribution to "great"'s composite must reflect the
    # CAPPED value, not the raw (huge) z. Every other bucket is 0.0 for
    # every ticker in this fixture, so composite_raw for "great" is
    # exactly renorm_weight(accounting) * cap.
    accounting_weight = cfg.composite_weights["accounting_quality"]
    total_weight = sum(cfg.composite_weights.values())  # all 7 buckets available
    expected = (accounting_weight / total_weight) * cap
    assert abs(results["great"].composite_raw - expected) < 1e-6


def test_accounting_penalty_not_capped_when_unfavourable():
    cfg = load_config(REPO_CONFIG)
    tickers = ["ok", "terrible", "neutral", "fine"]
    subscores = {"accounting_quality": {"ok": 0.5, "terrible": -100.0, "neutral": 0.0, "fine": 0.3}}
    bucket_results = _all_buckets_available(tickers, subscores)
    results = compute_composite(bucket_results, cfg)

    terrible_z = results["terrible"].bucket_z_universe["accounting_quality"]
    assert terrible_z < 0
    assert results["terrible"].accounting_penalty_capped is False

    accounting_weight = cfg.composite_weights["accounting_quality"]
    total_weight = sum(cfg.composite_weights.values())
    expected = (accounting_weight / total_weight) * terrible_z
    assert abs(results["terrible"].composite_raw - expected) < 1e-6


def test_bucket_below_coverage_floor_is_excluded_and_weights_renormalize(cfg):
    tickers = ["t1", "t2", "t3", "t4"]
    bucket_results = _all_buckets_available(tickers, {})
    # Give t1 a low-confidence (excluded) quality bucket; every other
    # ticker keeps quality available.
    bucket_results["quality"]["t1"] = _fake_result("t1", "quality", 5.0, low_confidence=True)

    results = compute_composite(bucket_results, cfg)
    assert "quality" not in {
        b for b, z in results["t1"].bucket_z_universe.items() if z is not None
    }
    assert "quality" not in results["t1"].bucket_weights_used
    # The remaining 6 buckets' renormalized weights for t1 must sum to 1.0.
    assert abs(sum(results["t1"].bucket_weights_used.values()) - 1.0) < 1e-9
    # t2 (not excluded anywhere) keeps all 7.
    assert len(results["t2"].bucket_weights_used) == 7


def test_single_surviving_bucket_gets_no_composite(cfg):
    """The composite-level coverage floor (DEC-012). A name where only
    one low-weight bucket survives must NOT be renormalized to 100%
    weight on that bucket and handed a confident-looking percentile.

    Regression: TSM scored a 2.3 percentile built entirely on
    accounting_quality (weight 0.10) with the other six buckets excluded
    -- which reads as 'one of the worst names in the universe' when it
    actually means 'almost nothing was measurable'."""
    tickers = ["t1", "t2", "t3", "t4"]
    bucket_results = _all_buckets_available(tickers, {})
    # t1 keeps only accounting_quality (0.10 of 1.00 total weight).
    for bucket in BUCKET_NAMES:
        if bucket != "accounting_quality":
            bucket_results[bucket]["t1"] = _fake_result(
                "t1", bucket, 0.5, low_confidence=True)

    results = compute_composite(bucket_results, cfg)
    assert results["t1"].composite_raw is None
    assert results["t1"].percentile is None
    assert results["t1"].bucket_weights_used == {}
    # every other name is unaffected and still ranked
    assert results["t2"].percentile is not None


def test_majority_of_bucket_weight_still_scores(cfg):
    """The floor must not be so aggressive it drops normal names. With
    momentum and news excluded (the real state of the current universe),
    0.80 of the weight survives and the name scores."""
    tickers = ["t1", "t2", "t3", "t4"]
    bucket_results = _all_buckets_available(tickers, {"quality": {
        "t1": 1.0, "t2": 0.2, "t3": -0.4, "t4": 0.1}})
    for bucket in ("momentum", "news"):
        bucket_results[bucket]["t1"] = _fake_result(
            "t1", bucket, 0.0, low_confidence=True)

    results = compute_composite(bucket_results, cfg)
    assert results["t1"].composite_raw is not None
    assert abs(sum(results["t1"].bucket_weights_used.values()) - 1.0) < 1e-9
    assert "momentum" not in results["t1"].bucket_weights_used


def test_ticker_with_every_bucket_excluded_gets_no_composite(cfg):
    tickers = ["t1", "t2", "t3"]
    bucket_results = _all_buckets_available(tickers, {})
    for bucket in BUCKET_NAMES:
        bucket_results[bucket]["t1"] = _fake_result("t1", bucket, None, low_confidence=True)

    results = compute_composite(bucket_results, cfg)
    assert results["t1"].composite_raw is None
    assert results["t1"].percentile is None
    # t2/t3 must be unaffected and still ranked against each other.
    assert results["t2"].percentile is not None
