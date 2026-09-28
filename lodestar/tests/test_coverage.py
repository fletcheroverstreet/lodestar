"""Hand-computed tests for bucket coverage and renormalization."""

from __future__ import annotations

from lodestar.scoring.coverage import bucket_subscore


def test_full_coverage_hand_computed():
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    zscores = {"a": 1.0, "b": -0.5, "c": 2.0}
    result = bucket_subscore("T", "quality", zscores, weights, coverage_floor=0.5)
    # subscore = 0.5*1.0 + 0.3*(-0.5) + 0.2*2.0 = 0.5 - 0.15 + 0.4 = 0.75
    assert abs(result.subscore - 0.75) < 1e-9
    assert result.coverage == 1.0
    assert result.low_confidence is False


def test_partial_coverage_renormalizes_hand_computed():
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    zscores = {"a": 1.0, "b": None, "c": 2.0}  # b missing
    result = bucket_subscore("T", "quality", zscores, weights, coverage_floor=0.5)
    # available weight = 0.5+0.2=0.7; coverage = 0.7/1.0=0.7
    assert abs(result.coverage - 0.7) < 1e-9
    # renormalized: a -> 0.5/0.7, c -> 0.2/0.7
    # subscore = (0.5/0.7)*1.0 + (0.2/0.7)*2.0 = 0.714286 + 0.571429 = 1.285714
    assert abs(result.subscore - (0.5 / 0.7 * 1.0 + 0.2 / 0.7 * 2.0)) < 1e-9
    assert result.low_confidence is False  # 0.7 >= default floor 0.5


def test_coverage_below_floor_flags_low_confidence():
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    zscores = {"a": None, "b": None, "c": 2.0}  # only c available -> coverage 0.2
    result = bucket_subscore("T", "quality", zscores, weights, coverage_floor=0.5)
    assert abs(result.coverage - 0.2) < 1e-9
    assert result.low_confidence is True
    assert result.subscore == 2.0  # the one available metric, fully renormalized to weight 1.0


def test_zero_coverage_gives_none_subscore_not_zero():
    """A bucket with NOTHING computable must report subscore=None
    (dropped from the composite), never a fabricated neutral 0 -- 0 and
    "no data" must never be indistinguishable."""
    weights = {"a": 0.5, "b": 0.5}
    zscores = {"a": None, "b": None}
    result = bucket_subscore("T", "quality", zscores, weights, coverage_floor=0.5)
    assert result.subscore is None
    assert result.coverage == 0.0
    assert result.low_confidence is True
    assert result.metric_weights_used == {}
