"""News bucket: event-weighted sentiment, exponentially decayed.

Two providers behind one protocol, selected by `config.yaml: news.provider`.

`FinlakeNewsProvider` is the real one, reading finlake's multi-source news
table — Yahoo, Google News, the market provider's feed, and SEC 8-K filings,
deduplicated across sources, scored with the Loughran-McDonald financial
lexicon, classified by event type, weighted by source reliability, and decayed
exponentially by age.

`NullNewsProvider` remains the default and reports zero coverage for every
ticker, which excludes the bucket from the composite and renormalizes the
remaining weights. That is the correct behaviour for a cache with no news
built yet, and it is honest rather than convenient: a fabricated neutral score
would place every name at exactly the peer-group mean, which reads as a
finding rather than as an absence of one.

DEC-008 predicted that wiring in a real provider would cost one class and one
line of config, with nothing else in lodestar changing. That held.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .. import adapter
from ..config import Config
from .base import MetricResult, TickerContext

BUCKET = "news"


@dataclass(frozen=True)
class NewsSignal:
    """One ticker's news signal for a run.

    `score` is the event-weighted, exponentially-decayed sentiment
    figure itself (higher = more positive news flow), already oriented
    on the project-wide higher-is-better convention. `coverage` is 0.0
    when a provider has nothing for this ticker at all, distinct from a
    genuinely neutral (zero) sentiment score.
    """

    ticker: str
    score: float | None
    event_count: int
    coverage: float  # 0.0-1.0


class NewsSignalProvider(Protocol):
    """Anything that can answer "what's the news signal for these
    tickers, as of this date" implements this."""

    def get_signal(self, tickers: list[str], as_of: str) -> dict[str, NewsSignal]:
        ...


class NullNewsProvider:
    """The only provider that exists today. Reports zero coverage for
    every ticker, honestly — not a fabricated neutral score. Zero
    coverage means the News bucket is excluded from the composite for
    every name and the remaining bucket weights renormalize
    (scoring/coverage.py), exactly the same mechanism used for a single
    missing metric inside any other bucket.
    """

    def get_signal(self, tickers: list[str], as_of: str) -> dict[str, NewsSignal]:
        return {
            t: NewsSignal(ticker=t, score=None, event_count=0, coverage=0.0)
            for t in tickers
        }


class FinlakeNewsProvider:
    """The real provider, reading finlake's multi-source news table.

    finlake does the collection, deduplication across sources, Loughran-
    McDonald sentiment scoring, event classification, source weighting, and
    exponential decay. This class is the translation layer and nothing more —
    which is the point of having had the protocol in place first. Filling a
    10%-weight bucket that has been excluded universe-wide since the project
    started costs one class and one line of config.

    The `score` that arrives is already oriented higher-is-better and already
    in [-1, 1], matching the project-wide sign convention, so no
    re-orientation happens here.
    """

    def __init__(self, *, lookback_days: int = 60, half_life_days: float = 20.0):
        self.lookback_days = lookback_days
        self.half_life_days = half_life_days

    def get_signal(self, tickers: list[str], as_of: str) -> dict[str, NewsSignal]:
        raw = adapter.get_news_signal(
            tickers, as_of=as_of, lookback_days=self.lookback_days,
            half_life_days=self.half_life_days)

        out: dict[str, NewsSignal] = {}
        for ticker in tickers:
            entry = raw.get(ticker) or raw.get(ticker.upper()) or {}
            # A ticker finlake returned nothing for is NOT a neutral reading.
            # Defaulting score to 0.0 here would place a name we have no
            # information about exactly at the peer-group mean, which reads
            # as a real finding rather than as an absence of one.
            out[ticker] = NewsSignal(
                ticker=ticker,
                score=entry.get("score"),
                event_count=int(entry.get("articles") or 0),
                coverage=float(entry.get("coverage") or 0.0),
            )
        return out


def build_provider(cfg: Config) -> NewsSignalProvider:
    """The provider named by `config.yaml: news.provider`.

    `null` (the default) keeps NullNewsProvider, so a cache with no news
    table behaves exactly as before rather than failing. `finlake` switches
    to the real one. Anything else is an error at load time rather than a
    silently empty bucket at scoring time — a typo'd provider name that
    quietly degrades to zero coverage is indistinguishable from having no
    news at all, and would look like a data problem for as long as it took
    someone to notice.
    """
    bucket = cfg.bucket(BUCKET)
    name = bucket.params.get("provider")
    if name in (None, "null", "none"):
        return NullNewsProvider()
    if name == "finlake":
        return FinlakeNewsProvider(
            lookback_days=int(bucket.params.get("lookback_days", 60)),
            half_life_days=float(bucket.params.get("half_life_days", 20)),
        )
    raise ValueError(
        f"unknown news.provider {name!r} in config.yaml. "
        f"Use 'null' (no news, bucket excluded) or 'finlake'."
    )


def news_sentiment(
    ctx: TickerContext, cfg: Config, *, signal: NewsSignal | None = None
) -> MetricResult:
    """The single News bucket metric.

    Takes the pre-fetched NewsSignal for this ticker as a keyword
    argument rather than fetching it itself — a real provider would want
    to batch-fetch signal for the whole universe once per run, not make
    a per-ticker call, so the scoring orchestrator fetches the batch and
    passes each ticker's result in. Defaults to None (unavailable) so
    this still matches every other metric function's (ctx, cfg) calling
    contract when no signal has been wired up.
    """
    if signal is None or signal.score is None:
        return MetricResult(
            name="news_sentiment", bucket=BUCKET, value=None,
            raw_inputs={"event_count": signal.event_count if signal else 0,
                        "coverage": signal.coverage if signal else 0.0},
        )
    return MetricResult(
        name="news_sentiment", bucket=BUCKET, value=signal.score,
        raw_inputs={"event_count": signal.event_count, "coverage": signal.coverage},
    )


METRICS = {"news_sentiment": news_sentiment}
