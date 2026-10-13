"""Computes one factor bucket across the WHOLE universe at once.

Z-scoring is inherently cross-sectional — a ticker's z-score depends on
every peer's value for that metric — so this can't be done one ticker at
a time the way metric computation itself can. This module is the seam
between the two: metrics/*.py knows how to compute one ticker's raw
value; scoring/bucket.py knows how to turn a whole universe of raw
values into peer-group-relative scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..config import Config
from ..metrics import (
    accounting_quality, capital_allocation, growth, momentum, news, quality, value,
)
from ..metrics.base import MetricResult, TickerContext
from .coverage import BucketSubscore, bucket_subscore
from .zscore import ZScoreResult, zscore_metric

BUCKET_METRICS: dict[str, dict[str, Any]] = {
    "quality": quality.METRICS,
    "value": value.METRICS,
    "growth": growth.METRICS,
    "capital_allocation": capital_allocation.METRICS,
    "accounting_quality": accounting_quality.METRICS,
    "momentum": momentum.METRICS,
    "news": news.METRICS,
}


@dataclass(frozen=True)
class TickerBucketResult:
    ticker: str
    bucket: str
    metric_results: dict[str, MetricResult]  # raw output straight from the metric function
    zscore_results: dict[str, ZScoreResult]  # peer-group-relative, one per metric
    subscore: BucketSubscore


def compute_bucket(
    bucket_name: str,
    contexts: dict[str, TickerContext],
    cfg: Config,
    industry_of: dict[str, str],
    sector_of: dict[str, str],
    *,
    extra_kwargs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, TickerBucketResult]:
    """Compute every metric in `bucket_name` for every ticker in
    `contexts`, z-score each metric cross-sectionally, then roll each
    ticker's available metric z-scores up into a bucket subscore.

    `extra_kwargs`, keyed by ticker, is for metrics that need something
    beyond (ctx, cfg) — today, only news_sentiment's pre-fetched
    NewsSignal (news.py's metric takes `signal=` as a keyword-only arg).
    """
    metric_fns = BUCKET_METRICS[bucket_name]
    bucket_cfg = cfg.bucket(bucket_name)
    extra_kwargs = extra_kwargs or {}

    raw: dict[str, dict[str, MetricResult]] = {}
    for ticker, ctx in contexts.items():
        kwargs = extra_kwargs.get(ticker, {})
        raw[ticker] = {name: fn(ctx, cfg, **kwargs) for name, fn in metric_fns.items()}

    zscores: dict[str, dict[str, ZScoreResult]] = {}
    for metric_name in metric_fns:
        values = pd.Series({t: raw[t][metric_name].value for t in contexts}, dtype=float)
        zscores[metric_name] = zscore_metric(
            values, industry_of, sector_of,
            min_group_size=cfg.peer_min_group_size,
            winsorize_low_pct=cfg.winsorize_low_pct,
            winsorize_high_pct=cfg.winsorize_high_pct,
        )

    results: dict[str, TickerBucketResult] = {}
    for ticker in contexts:
        metric_z = {m: zscores[m][ticker].z for m in metric_fns}
        sub = bucket_subscore(
            ticker, bucket_name, metric_z, bucket_cfg.metric_weights,
            coverage_floor=cfg.coverage_bucket_floor,
        )
        results[ticker] = TickerBucketResult(
            ticker=ticker, bucket=bucket_name,
            metric_results={m: raw[ticker][m] for m in metric_fns},
            zscore_results={m: zscores[m][ticker] for m in metric_fns},
            subscore=sub,
        )
    return results
