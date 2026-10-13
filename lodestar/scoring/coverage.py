"""Bucket coverage: how much of a bucket's metric weight was actually
computable for a given ticker, and the subscore built from whatever did
compute.

A missing metric is DROPPED, never imputed as zero (project spec,
scoring mechanics step 5) — the bucket subscore is a weighted mean of
only the AVAILABLE metrics' z-scores, with weights renormalized across
just those. Coverage is reported alongside the subscore so a thin score
is visible, not indistinguishable from a well-covered one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BucketSubscore:
    ticker: str
    bucket: str
    subscore: float | None  # weighted mean of available metric z-scores; None with zero coverage
    coverage: float  # sum(available metric weights) / sum(all metric weights), 0.0-1.0
    low_confidence: bool  # coverage < the configured floor
    metric_weights_used: dict[str, float]  # renormalized weights actually applied


def bucket_subscore(
    ticker: str,
    bucket: str,
    metric_zscores: dict[str, float | None],  # metric name -> z (None if that metric was missing)
    metric_weights: dict[str, float],  # metric name -> configured weight (sums to 1.0)
    *,
    coverage_floor: float,
) -> BucketSubscore:
    total_weight = sum(metric_weights.values())
    available = {
        m: w for m, w in metric_weights.items()
        if metric_zscores.get(m) is not None
    }
    available_weight = sum(available.values())
    coverage = available_weight / total_weight if total_weight else 0.0

    if not available:
        return BucketSubscore(
            ticker=ticker, bucket=bucket, subscore=None, coverage=0.0,
            low_confidence=True, metric_weights_used={},
        )

    renormalized = {m: w / available_weight for m, w in available.items()}
    subscore = sum(renormalized[m] * metric_zscores[m] for m in available)

    return BucketSubscore(
        ticker=ticker, bucket=bucket, subscore=subscore, coverage=coverage,
        low_confidence=coverage < coverage_floor, metric_weights_used=renormalized,
    )
