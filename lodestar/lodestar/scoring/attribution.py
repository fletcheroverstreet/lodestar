"""Per-name attribution: the composite score's full decomposition into
bucket and metric contributions, and the top drivers pushing a name up
or down. "A rank I can't explain is a rank I won't act on" — the project
spec's own framing for why this exists.

Metric-level contribution math: composite = Σ_bucket weight_used_b *
bucket_z_b, and bucket_z_b = (bucket_subscore_b - mean_b) / std_b, and
bucket_subscore_b = Σ_metric metric_weight_used_m * metric_z_m (a linear
sum, weights renormalized to sum to 1.0 across available metrics).
Substituting and rearranging:

    bucket_z_b = Σ_metric weight_m * (metric_z_m - mean_b) / std_b

so each metric's contribution to the bucket (before scaling by the
bucket's own composite weight) is weight_m * (metric_z_m - mean_b) /
std_b — the metric's OWN z relative to the bucket's universe-wide mean,
not the raw z alone. Using z_m alone (without subtracting mean_b) would
leave a constant, unattributed remainder of mean_b/std_b per unit
weight, and the driver list would silently fail to sum to the number
it's supposed to explain — this was a real bug caught by
tests/test_attribution.py before it shipped: the fix apportions the
mean-centering term across metrics by weight (each metric gets a share
of the "distance from an average company" baseline proportional to how
much it counts), which is a defensible allocation and, more importantly,
is an EXACT decomposition — the per-metric contributions for a bucket
always sum to precisely that bucket's own composite_contribution.

One caveat, reported rather than hidden: when the accounting-quality
bucket's z was capped (a favourable score, capped per DEC-005), the
metric-level contributions below are computed from the UNCAPPED z (the
true linear decomposition of what accounting quality would have
contributed without the cap) — they will not sum to exactly that
bucket's capped composite_contribution. `was_capped` on the bucket
attribution flags this explicitly rather than silently rescaling the
per-metric numbers to force an exact match that would misrepresent each
metric's actual z-score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import Config
from ..metrics.base import TickerContext
from .bucket import TickerBucketResult
from .composite import CompositeResult


@dataclass(frozen=True)
class MetricDriver:
    metric: str
    bucket: str
    z: float
    raw_value: float | None
    contribution: float  # linear composite-point contribution (see module docstring)


@dataclass(frozen=True)
class BucketAttribution:
    bucket: str
    subscore: float | None
    coverage: float
    low_confidence: bool
    peer_levels: dict[str, str | None]  # metric -> peer level used
    z_universe: float | None  # re-standardized bucket z across the universe
    weight_configured: float
    weight_used: float  # 0.0 if excluded by the coverage floor
    composite_contribution: float
    was_capped: bool


@dataclass(frozen=True)
class TickerAttribution:
    ticker: str
    as_of: str
    composite_percentile: float | None
    composite_raw: float | None
    rank_change: float | None
    buckets: dict[str, BucketAttribution]
    top_drivers_up: list[MetricDriver]
    top_drivers_down: list[MetricDriver]
    substitution_flags: dict[str, str] = field(default_factory=dict)
    data_as_of: str | None = None  # most recent filed date across this ticker's fundamentals


def compute_attribution(
    ticker: str,
    as_of: str,
    bucket_results: dict[str, dict[str, TickerBucketResult]],
    composite: dict[str, CompositeResult],
    cfg: Config,
    *,
    ctx: TickerContext | None = None,
    top_n: int = 3,
) -> TickerAttribution:
    comp = composite[ticker]

    bucket_attrs: dict[str, BucketAttribution] = {}
    drivers: list[MetricDriver] = []
    substitution_flags: dict[str, str] = {}

    for bucket_name, per_ticker in bucket_results.items():
        result = per_ticker[ticker]
        sub = result.subscore
        z_universe = comp.bucket_z_universe.get(bucket_name)
        weight_used = comp.bucket_weights_used.get(bucket_name, 0.0)
        was_capped = (
            bucket_name == "accounting_quality"
            and comp.accounting_penalty_capped
            and z_universe is not None
        )

        bucket_attrs[bucket_name] = BucketAttribution(
            bucket=bucket_name, subscore=sub.subscore, coverage=sub.coverage,
            low_confidence=sub.low_confidence,
            peer_levels={m: r.peer_level for m, r in result.zscore_results.items()},
            z_universe=z_universe, weight_configured=cfg.composite_weights[bucket_name],
            weight_used=weight_used,
            composite_contribution=(weight_used * z_universe) if z_universe is not None else 0.0,
            was_capped=was_capped,
        )

        for metric_name, mresult in result.metric_results.items():
            if mresult.substitution:
                substitution_flags[metric_name] = mresult.substitution

        if weight_used == 0.0:
            continue

        mean, std = _bucket_universe_mean_std(bucket_results[bucket_name])
        if std == 0.0:
            continue

        for metric_name, metric_weight in sub.metric_weights_used.items():
            zres = result.zscore_results[metric_name]
            if zres.z is None:
                continue
            # (z_m - mean) rather than z_m alone: bucket_z = (subscore -
            # mean) / std, and subscore = Σ weight_m * z_m, so the mean
            # term has to be apportioned across metrics (by weight) for
            # per-metric contributions to sum to EXACTLY the bucket's own
            # composite_contribution — using z_m alone would leave a
            # constant, unattributed remainder equal to weight_used *
            # mean / std, and the driver list would silently not add up
            # to the number it's supposed to explain.
            contribution = weight_used * metric_weight * (zres.z - mean) / std
            drivers.append(MetricDriver(
                metric=metric_name, bucket=bucket_name, z=zres.z,
                raw_value=zres.raw_value, contribution=contribution,
            ))

    top_up, top_down = _split_top_drivers(drivers, top_n)

    return TickerAttribution(
        ticker=ticker, as_of=as_of,
        composite_percentile=comp.percentile, composite_raw=comp.composite_raw,
        rank_change=_rank_change_value(bucket_results, ticker),
        buckets=bucket_attrs, top_drivers_up=top_up, top_drivers_down=top_down,
        substitution_flags=substitution_flags,
        data_as_of=_latest_filed_date(ctx),
    )


def _bucket_universe_mean_std(per_ticker: dict[str, TickerBucketResult]) -> tuple[float, float]:
    """The same mean/std composite.py used to re-standardize this
    bucket's subscore across the universe — recomputed here (cheap, a
    few dozen numbers) rather than threaded through from composite.py,
    to keep attribution.py's only inputs the same bucket_results/
    composite dicts every other caller already has on hand."""
    subscores = pd.Series(
        {t: r.subscore.subscore for t, r in per_ticker.items() if not r.subscore.low_confidence},
        dtype=float,
    ).dropna()
    if len(subscores) < 2:
        return 0.0, 0.0
    return float(subscores.mean()), float(subscores.std(ddof=1))


def _split_top_drivers(
    drivers: list[MetricDriver], top_n: int
) -> tuple[list[MetricDriver], list[MetricDriver]]:
    ordered_desc = sorted(drivers, key=lambda d: d.contribution, reverse=True)
    top_up = ordered_desc[:top_n]
    remainder = ordered_desc[top_n:]
    top_down = list(reversed(remainder[-top_n:])) if remainder else []
    return top_up, top_down


def _rank_change_value(
    bucket_results: dict[str, dict[str, TickerBucketResult]], ticker: str
) -> float | None:
    momentum_results = bucket_results.get("momentum")
    if not momentum_results:
        return None
    metric_results = momentum_results[ticker].metric_results
    rank_change_result = metric_results.get("rank_change")
    return rank_change_result.value if rank_change_result else None


def _latest_filed_date(ctx: TickerContext | None) -> str | None:
    """The most recent `filed` date across every concept in this
    ticker's fundamentals frame — a simple, honest "how fresh is the
    underlying data" figure, read directly from finlake's own
    provenance columns (every concept pulled with include_provenance
    gets a `<concept>__filed` column) rather than trying to reverse-
    engineer it from each metric's own raw_inputs, whose keys vary too
    much across metrics to map back to a concept reliably.
    """
    if ctx is None or ctx.fundamentals.empty:
        return None
    filed_cols = [c for c in ctx.fundamentals.columns if c.endswith("__filed")]
    if not filed_cols:
        return None
    latest: str | None = None
    for col in filed_cols:
        values = ctx.fundamentals[col].dropna()
        if len(values) == 0:
            continue
        col_max = str(values.max())
        if latest is None or col_max > latest:
            latest = col_max
    return latest
