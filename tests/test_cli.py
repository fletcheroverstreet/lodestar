"""Tests for the CLI's pure logic -- not the I/O-heavy command handlers
themselves (those are exercised directly against real data via
`python -m lodestar run`/`show`/`audit`, which is a stronger check than
a mocked unit test would be for orchestration code like this)."""

from __future__ import annotations

from pathlib import Path

from lodestar import persistence
from lodestar.cli import _price_start, _resolve_run, build_parser, _find_bucket_for_metric
from lodestar.config import load_config
from lodestar.scoring.bucket import BUCKET_METRICS

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def test_price_start_goes_back_far_enough_for_the_252_day_lookback():
    start = _price_start("2026-08-08")
    # 11 years back; just check it's well before the momentum window needs.
    assert start < "2016-01-01"


def test_find_bucket_for_metric_known():
    assert _find_bucket_for_metric("roic") == "quality"
    assert _find_bucket_for_metric("buyback_timing") == "capital_allocation"
    assert _find_bucket_for_metric("news_sentiment") == "news"


def test_find_bucket_for_metric_unknown_returns_none():
    assert _find_bucket_for_metric("not_a_real_metric") is None


def test_every_bucket_metric_is_findable():
    """Guards against a future bucket module forgetting to export a
    metric in its METRICS dict, or a naming mismatch between
    config.yaml and a metric function -- would show up here first."""
    for bucket, metrics in BUCKET_METRICS.items():
        for metric_name in metrics:
            assert _find_bucket_for_metric(metric_name) == bucket


def test_resolve_run_defaults_to_latest(tmp_path):
    db_path = tmp_path / "test.db"
    with persistence.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, as_of, config_hash, universe_size, "
            "universe_source, created_at) VALUES (?,?,?,?,?,?)",
            ("run1", "2026-01-01", "hash1", 1, "test", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO runs (run_id, as_of, config_hash, universe_size, "
            "universe_source, created_at) VALUES (?,?,?,?,?,?)",
            ("run2", "2026-06-01", "hash2", 1, "test", "2026-06-01T00:00:00"),
        )
        result = _resolve_run(conn, None)
    assert result.run_id == "run2"


def test_resolve_run_by_explicit_id(tmp_path):
    db_path = tmp_path / "test.db"
    with persistence.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, as_of, config_hash, universe_size, "
            "universe_source, created_at) VALUES (?,?,?,?,?,?)",
            ("run1", "2026-01-01", "hash1", 1, "test", "2026-01-01T00:00:00"),
        )
        result = _resolve_run(conn, "run1")
    assert result.run_id == "run1"


def test_resolve_run_unknown_id_returns_none(tmp_path):
    db_path = tmp_path / "test.db"
    with persistence.connect(db_path) as conn:
        result = _resolve_run(conn, "does-not-exist")
    assert result is None


def test_cli_parser_builds_all_four_subcommands():
    parser = build_parser()
    args = parser.parse_args(["run", "--as-of", "2026-08-08"])
    assert args.command == "run"
    assert args.as_of == "2026-08-08"

    args = parser.parse_args(["show", "MU"])
    assert args.command == "show" and args.ticker == "MU"

    args = parser.parse_args(["audit", "MU", "roic"])
    assert args.command == "audit" and args.metric == "roic"

    args = parser.parse_args(["ui"])
    assert args.command == "ui"
