"""Composite: blend the seven bucket subscores into one 0-100 percentile
score per ticker, per the project spec's exact order:

  1. Each bucket's subscore (scoring/bucket.py) is a weighted mean of
     peer-group z-scored metrics -- standardized WITHIN each ticker's own
     peer group, but not yet to unit variance across the whole universe.
  2. Re-standardize: z-score each bucket's subscore across the WHOLE
     universe. (Not peer-grouped again — the metric-level z-scoring
     already handled sector-neutrality; re-peer-grouping here would be
     redundant.)
  3. A bucket below the coverage floor is excluded for that ticker, and
     the remaining bucket weights renormalize — per ticker, since
     different names can have different buckets excluded.
  4. Accounting quality is asymmetric: its re-standardized z is capped
     at `good_score_cap_z` before being added when favourable (positive),
     but added at full weight, uncapped, when unfavourable (negative).
     Clean books are the baseline, not an edge case worth rewarding
     heavily. See DEC-005 in lodestar's vault.
  5. Composite = the resulting weighted sum, re-ranked into a 0-100
     percentile across the universe.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pandas as pd

from ..config import Config
from .bucket import TickerBucketResult

ACCOUNTING_BUCKET = "accounting_quality"

# A composite is a blend; a blend of one bucket is not a composite. Not
# configurable because it isn't a tuning knob — it's what the word means.
MIN_BUCKETS_FOR_COMPOSITE = 2


@dataclass(frozen=True)
class CompositeResult:
    ticker: str
    composite_raw: float | None  # the weighted-z sum, before percentile ranking
    percentile: float | None  # 0-100; None only when every bucket was excluded
    bucket_z_universe: dict[str, float | None]  # bucket -> re-standardized z (None if excluded)
    bucket_weights_used: dict[str, float]  # renormalized weights actually applied
    accounting_penalty_capped: bool  # True if a favourable accounting z was capped down


def compute_composite(
    bucket_results: dict[str, dict[str, TickerBucketResult]],  # bucket_name -> ticker -> result
    cfg: Config,
) -> dict[str, CompositeResult]:
    bucket_names = list(bucket_results.keys())
    tickers = list(next(iter(bucket_results.values())).keys())

    bucket_z = _restandardize_buckets_across_universe(bucket_results, bucket_names, tickers)
    good_cap = cfg.bucket("accounting_quality").param("good_score_cap_z")

    min_weight = cfg.composite_min_weight_covered

    results: dict[str, CompositeResult] = {}
    for t in tickers:
        available = {b: bucket_z[b][t] for b in bucket_names if bucket_z[b][t] is not None}
        total_weight = sum(cfg.composite_weights[b] for b in available)

        # Composite-level coverage floor. The per-bucket floor
        # (scoring/coverage.py) stops a BUCKET being scored on fragments,
        # but on its own it does NOT stop the COMPOSITE being scored on
        # fragments: renormalizing across whatever survived will happily
        # put 100% of the composite weight on a single surviving bucket
        # and emit a percentile that looks exactly as authoritative as a
        # fully-covered name's.
        #
        # Found on real data: TSM scored a 2.3 percentile built entirely
        # on accounting_quality, with the other six buckets excluded.
        # Read naively that says "one of the worst names in the
        # universe"; what it actually says is "almost nothing about this
        # company was measurable, and the one thing that was came out
        # mediocre". Those are different claims and the second must not
        # be dressed up as the first.
        #
        # Two conditions, both cheap to explain:
        #   * at least MIN_BUCKETS_FOR_COMPOSITE buckets — a composite is
        #     by definition a blend, and a blend of one thing isn't one.
        #     This is definitional, not tuned to the data.
        #   * at least `min_weight` of the total configured bucket weight
        #     — a secondary guard against a pathological case where two
        #     individually tiny buckets are all that survive.
        #
        # Calibration check against the 44-name universe: exactly one
        # name (TSM) has a single surviving bucket at 0.10 weight; the
        # next-thinnest names (the banks, whose ROIC is gated off by
        # DEC-002, and NEE) have THREE surviving buckets at 0.40-0.45.
        # Nothing sits at two. So this excludes TSM with a wide margin
        # and touches nothing else — an earlier attempt at a 0.5 weight
        # floor alone dropped every bank including the top-ranked name,
        # which is a far worse failure than the one being fixed.
        if (not available
                or len(available) < MIN_BUCKETS_FOR_COMPOSITE
                or total_weight < min_weight):
            results[t] = CompositeResult(
                ticker=t, composite_raw=None, percentile=None,
                bucket_z_universe={b: bucket_z[b][t] for b in bucket_names},
                bucket_weights_used={}, accounting_penalty_capped=False,
            )
            continue

        renorm = {b: cfg.composite_weights[b] / total_weight for b in available}

        composite = 0.0
        capped = False
        for b, z in available.items():
            contribution_z = z
            if b == ACCOUNTING_BUCKET and z > good_cap:
                contribution_z = good_cap
                capped = True
            composite += renorm[b] * contribution_z

        results[t] = CompositeResult(
            ticker=t, composite_raw=composite, percentile=None,
            bucket_z_universe={b: bucket_z[b][t] for b in bucket_names},
            bucket_weights_used=renorm, accounting_penalty_capped=capped,
        )

    return _rank_into_percentiles(results, tickers)


def _restandardize_buckets_across_universe(
    bucket_results: dict[str, dict[str, TickerBucketResult]],
    bucket_names: list[str],
    tickers: list[str],
) -> dict[str, dict[str, float | None]]:
    bucket_z: dict[str, dict[str, float | None]] = {}
    for bucket in bucket_names:
        subscores = pd.Series(
            {t: bucket_results[bucket][t].subscore.subscore for t in tickers}, dtype=float
        )
        # A bucket below the coverage floor is unavailable for THAT
        # ticker, same treatment as a missing metric — excluded, not
        # scored on data too thin to trust.
        for t in tickers:
            if bucket_results[bucket][t].subscore.low_confidence:
                subscores[t] = float("nan")

        clean = subscores.dropna()
        if len(clean) < 2:
            bucket_z[bucket] = {t: None for t in tickers}
            continue

        mean, std = float(clean.mean()), float(clean.std(ddof=1))
        # Zero variance (every available ticker has the identical
        # subscore) means "no differentiating signal", not "missing
        # data" -- z=0.0 (neutral) for the tickers that DO have a
        # subscore, same treatment zscore.py already gives a peer group
        # of one. Only a ticker with NO subscore at all (excluded by the
        # coverage floor, or never computed) gets None here.
        bucket_z[bucket] = {
            t: (((float(subscores[t]) - mean) / std) if std else 0.0) if pd.notna(subscores[t]) else None
            for t in tickers
        }
    return bucket_z


def _rank_into_percentiles(
    results: dict[str, CompositeResult], tickers: list[str]
) -> dict[str, CompositeResult]:
    raw_scores = pd.Series({
        t: results[t].composite_raw for t in tickers if results[t].composite_raw is not None
    })
    if len(raw_scores) == 0:
        return results

    pct = raw_scores.rank(pct=True) * 100.0
    out = dict(results)
    for t in pct.index:
        out[t] = dataclasses.replace(out[t], percentile=float(pct[t]))
    return out
