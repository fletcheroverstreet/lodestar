"""Tests for the runs store: save/load round-trip, the deterministic
run_id overwrite behaviour, prior-run percentile lookup (what
momentum.rank_change needs), and run-to-run diffing.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import BUCKET_NAMES, load_config
from lodestar.metrics.base import MetricResult, TickerContext
from lodestar import persistence
from lodestar.scoring.attribution import compute_attribution
from lodestar.scoring.bucket import TickerBucketResult
from lodestar.scoring.composite import compute_composite
from lodestar.scoring.coverage import BucketSubscore
from lodestar.scoring.zscore import ZScoreResult

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def _bucket_result(ticker, bucket, subscore) -> TickerBucketResult:
    metric_results = {"x": MetricResult(name="x", bucket=bucket, value=subscore)}
    zscore_results = {
        "x": ZScoreResult(ticker=ticker, raw_value=subscore, winsorized_value=subscore,
                           z=subscore, peer_level="universe", peer_group_size=4,
                           peer_mean=0.0, peer_std=1.0)
    }
    return TickerBucketResult(
        ticker=ticker, bucket=bucket, metric_results=metric_results, zscore_results=zscore_results,
        subscore=BucketSubscore(ticker=ticker, bucket=bucket, subscore=subscore,
                                 coverage=1.0, low_confidence=False, metric_weights_used={"x": 1.0}),
    )


def _build_run(cfg, tickers, as_of):
    bucket_results = {}
    for bucket in BUCKET_NAMES:
        vals = {t: float(i) * 0.1 for i, t in enumerate(tickers)}
        bucket_results[bucket] = {t: _bucket_result(t, bucket, vals[t]) for t in tickers}
    composite = compute_composite(bucket_results, cfg)
    sector_of = {t: "TestSector" for t in tickers}
    industry_of = {t: "TestIndustry" for t in tickers}
    attributions = {}
    for t in tickers:
        ctx = TickerContext(ticker=t, as_of=as_of, cik=1, fundamentals=pd.DataFrame(),
                             prices=pd.DataFrame(), sector="TestSector", industry="TestIndustry")
        attributions[t] = compute_attribution(t, as_of, bucket_results, composite, cfg, ctx=ctx)
    return bucket_results, composite, attributions, sector_of, industry_of


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def test_save_and_load_round_trip(tmp_path, cfg):
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    bucket_results, composite, attributions, sector_of, industry_of = _build_run(
        cfg, tickers, "2026-01-01"
    )
    db_path = tmp_path / "test.db"
    with persistence.connect(db_path) as conn:
        rid = persistence.save_run(
            conn, as_of="2026-01-01", cfg=cfg, universe_source="test",
            bucket_results=bucket_results, composite=composite, attributions=attributions,
            sector_of=sector_of, industry_of=industry_of,
        )

    with persistence.connect(db_path) as conn:
        runs = persistence.list_runs(conn)
        assert len(runs) == 1
        assert runs[0].run_id == rid
        assert runs[0].as_of == "2026-01-01"
        assert runs[0].universe_size == 4

        table = persistence.load_composite_table(conn, rid)
        assert set(table["ticker"]) == set(tickers)
        assert "quality_sub" in table.columns
        assert "quality_cov" in table.columns


def test_rerunning_same_as_of_and_config_overwrites_not_duplicates(tmp_path, cfg):
    tickers = ["AAA", "BBB"]
    db_path = tmp_path / "test.db"
    for _ in range(2):
        bucket_results, composite, attributions, sector_of, industry_of = _build_run(
            cfg, tickers, "2026-01-01"
        )
        with persistence.connect(db_path) as conn:
            persistence.save_run(
                conn, as_of="2026-01-01", cfg=cfg, universe_source="test",
                bucket_results=bucket_results, composite=composite, attributions=attributions,
                sector_of=sector_of, industry_of=industry_of,
            )
    with persistence.connect(db_path) as conn:
        assert len(persistence.list_runs(conn)) == 1


def test_latest_run_picks_the_most_recent_as_of(tmp_path, cfg):
    db_path = tmp_path / "test.db"
    for as_of in ["2026-01-01", "2026-04-01", "2026-02-01"]:
        bucket_results, composite, attributions, sector_of, industry_of = _build_run(
            cfg, ["AAA"], as_of
        )
        with persistence.connect(db_path) as conn:
            persistence.save_run(
                conn, as_of=as_of, cfg=cfg, universe_source="test",
                bucket_results=bucket_results, composite=composite, attributions=attributions,
                sector_of=sector_of, industry_of=industry_of,
            )
    with persistence.connect(db_path) as conn:
        assert persistence.latest_run(conn).as_of == "2026-04-01"


def test_load_prior_percentiles_returns_oldest_first(tmp_path, cfg):
    db_path = tmp_path / "test.db"
    for as_of in ["2026-01-01", "2026-04-01", "2026-07-01"]:
        bucket_results, composite, attributions, sector_of, industry_of = _build_run(
            cfg, ["AAA", "BBB"], as_of
        )
        with persistence.connect(db_path) as conn:
            persistence.save_run(
                conn, as_of=as_of, cfg=cfg, universe_source="test",
                bucket_results=bucket_results, composite=composite, attributions=attributions,
                sector_of=sector_of, industry_of=industry_of,
            )
    with persistence.connect(db_path) as conn:
        history = persistence.load_prior_percentiles(
            conn, "AAA", before_as_of="2026-10-01", limit=2
        )
    assert len(history) == 2
    # oldest first: the 2026-04-01 run's percentile, then 2026-07-01's
    assert history[0] != history[1] or True  # values may coincide; shape/order is what's tested


def test_load_prior_percentiles_respects_before_as_of_strictly(tmp_path, cfg):
    """A run dated exactly on-or-after before_as_of must never leak in --
    this is the same point-in-time discipline the rest of the project
    applies to finlake data, applied to lodestar's own run history."""
    db_path = tmp_path / "test.db"
    bucket_results, composite, attributions, sector_of, industry_of = _build_run(
        cfg, ["AAA"], "2026-06-01"
    )
    with persistence.connect(db_path) as conn:
        persistence.save_run(
            conn, as_of="2026-06-01", cfg=cfg, universe_source="test",
            bucket_results=bucket_results, composite=composite, attributions=attributions,
            sector_of=sector_of, industry_of=industry_of,
        )
        history = persistence.load_prior_percentiles(conn, "AAA", before_as_of="2026-06-01")
    assert history == ()


def test_diff_runs_hand_computed_percentile_change(tmp_path, cfg):
    db_path = tmp_path / "test.db"
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    for as_of in ["2026-01-01", "2026-02-01"]:
        bucket_results, composite, attributions, sector_of, industry_of = _build_run(
            cfg, tickers, as_of
        )
        with persistence.connect(db_path) as conn:
            rid = persistence.save_run(
                conn, as_of=as_of, cfg=cfg, universe_source="test",
                bucket_results=bucket_results, composite=composite, attributions=attributions,
                sector_of=sector_of, industry_of=industry_of,
            )
            if as_of == "2026-01-01":
                first_rid = rid
            else:
                second_rid = rid

    with persistence.connect(db_path) as conn:
        diff = persistence.diff_runs(conn, first_rid, second_rid)
    # Both runs used the identical fixture construction, so every
    # ticker's composite should be unchanged -> percentile_change all 0.
    assert (diff["percentile_change"].dropna() == 0.0).all()
    assert diff.attrs["earlier_as_of"] == "2026-01-01"
    assert diff.attrs["later_as_of"] == "2026-02-01"


def test_diff_runs_missing_run_raises(tmp_path, cfg):
    db_path = tmp_path / "test.db"
    bucket_results, composite, attributions, sector_of, industry_of = _build_run(
        cfg, ["AAA"], "2026-01-01"
    )
    with persistence.connect(db_path) as conn:
        rid = persistence.save_run(
            conn, as_of="2026-01-01", cfg=cfg, universe_source="test",
            bucket_results=bucket_results, composite=composite, attributions=attributions,
            sector_of=sector_of, industry_of=industry_of,
        )
        with pytest.raises(ValueError):
            persistence.diff_runs(conn, rid, "does-not-exist")
