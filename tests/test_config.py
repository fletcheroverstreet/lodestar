"""Tests for lodestar.config. Uses pytest (finlake's own tests use a
manual runner for its own historical reasons; lodestar uses pytest
throughout, per the project's requirements.txt)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lodestar.config import BUCKET_NAMES, load_config

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def test_real_config_loads_and_validates():
    """The actual config.yaml shipped with the repo must load cleanly —
    this is the config every other test and the CLI itself depends on."""
    cfg = load_config(REPO_CONFIG)
    assert set(cfg.composite_weights) == set(BUCKET_NAMES)
    assert abs(sum(cfg.composite_weights.values()) - 1.0) < 1e-9
    for name in BUCKET_NAMES:
        bucket = cfg.bucket(name)
        if bucket.metric_weights:
            assert abs(sum(bucket.metric_weights.values()) - 1.0) < 1e-9, (
                f"{name}.metrics does not sum to 1.0: {bucket.metric_weights}"
            )


def test_bucket_param_access():
    cfg = load_config(REPO_CONFIG)
    assert cfg.bucket("quality").param("roic_stability_window_quarters") == 20
    assert cfg.bucket("value").param("ebit_margin_floor") == 0.03


def test_bucket_param_missing_key_raises():
    cfg = load_config(REPO_CONFIG)
    with pytest.raises(KeyError):
        cfg.bucket("quality").param("does_not_exist")


def test_unknown_bucket_raises():
    cfg = load_config(REPO_CONFIG)
    with pytest.raises(KeyError):
        cfg.bucket("not_a_real_bucket")


def test_config_hash_is_stable_and_content_sensitive(tmp_path):
    """Same file content -> same hash (twice). Different content ->
    different hash. This is the determinism guarantee the project spec
    requires: same as-of + same config hash = identical output."""
    original = REPO_CONFIG.read_text()

    p1 = tmp_path / "a.yaml"
    p1.write_text(original)
    cfg1 = load_config(p1)
    cfg1b = load_config(p1)
    assert cfg1.config_hash == cfg1b.config_hash

    p2 = tmp_path / "b.yaml"
    p2.write_text(original.replace("roic: 0.30", "roic: 0.29\n    roic_fudge: 0.01"))
    # (this edit alone would break the weight-sum validation, so just
    # check the hash differs on ANY textual change, using a smaller edit)
    p3 = tmp_path / "c.yaml"
    p3.write_text(original.replace('bucket_floor: 0.5', 'bucket_floor: 0.4'))
    cfg3 = load_config(p3)
    assert cfg3.config_hash != cfg1.config_hash


def test_weights_must_sum_to_one(tmp_path):
    bad = REPO_CONFIG.read_text().replace("bucket_floor: 0.5", "bucket_floor: 0.6")
    # sabotage composite weights so they no longer sum to 1.0
    bad = bad.replace("quality: 0.20", "quality: 0.99")
    p = tmp_path / "bad.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="must sum to 1.0"):
        load_config(p)


def test_composite_weights_must_cover_every_bucket(tmp_path):
    text = REPO_CONFIG.read_text()
    # Remove the "news: 0.10" line and give its weight to quality instead,
    # so the sum still validates but a bucket is missing.
    text = text.replace("news: 0.10", "").replace("quality: 0.20", "quality: 0.30")
    p = tmp_path / "missing_bucket.yaml"
    p.write_text(text)
    with pytest.raises(ValueError, match="exactly the buckets"):
        load_config(p)


def test_constituents_path_is_relative_to_the_config_file(tmp_path, monkeypatch):
    """A path written inside config.yaml means "relative to config.yaml".

    It used to be resolved against the process's working directory, so the
    shipped `../finlake/universe/sp500_ndx.csv` only pointed at the real file
    when lodestar was launched from its own folder. Started from anywhere else
    — the parent directory, a scheduled task — the nightly re-score died on a
    FileNotFoundError for a file that was sitting right there.
    """
    config_dir = tmp_path / "lodestar"
    universe_dir = tmp_path / "finlake" / "universe"
    config_dir.mkdir()
    universe_dir.mkdir(parents=True)
    (universe_dir / "u.csv").write_text("ticker\nAAPL\n")

    text = REPO_CONFIG.read_text().replace(
        "constituents_file: ../finlake/universe/sp500_ndx.csv",
        "constituents_file: ../finlake/universe/u.csv")
    assert "../finlake/universe/u.csv" in text, "precondition: key not found"
    cfg_path = config_dir / "config.yaml"
    cfg_path.write_text(text)

    # Run from somewhere unrelated to both folders.
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    cfg = load_config(cfg_path)
    assert cfg.universe_constituents_file == (universe_dir / "u.csv").resolve()
    assert cfg.universe_constituents_file.exists()


def test_an_absolute_constituents_path_is_left_alone(tmp_path):
    target = (tmp_path / "anywhere.csv").resolve()
    target.write_text("ticker\nAAPL\n")
    text = REPO_CONFIG.read_text().replace(
        "constituents_file: ../finlake/universe/sp500_ndx.csv",
        f"constituents_file: '{target.as_posix()}'")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(text)

    assert load_config(cfg_path).universe_constituents_file == target


def test_the_shipped_config_points_at_a_real_file_from_any_directory(
        tmp_path, monkeypatch):
    """The layout the READMEs document: finlake and lodestar cloned side by
    side. Guards the real config.yaml, not a synthetic one."""
    monkeypatch.chdir(tmp_path)
    cfg = load_config(REPO_CONFIG)
    if cfg.universe_constituents_file is None:
        pytest.skip("config.yaml has no constituents file")
    sibling = REPO_CONFIG.parent.parent / "finlake"
    if not sibling.exists():
        pytest.skip("finlake is not cloned next to lodestar")
    assert cfg.universe_constituents_file.exists(), (
        f"config.yaml points at {cfg.universe_constituents_file}, which does "
        f"not exist")
