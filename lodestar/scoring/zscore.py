"""Peer-group z-scoring with the industry -> sector -> universe fallback
ladder (project spec §3.3).

For each metric and each ticker, the peer group is resolved
independently — the SAME metric can use industry-level peers for one
ticker (well-covered) and fall back to sector or universe for another
(thin coverage in its own industry for that specific metric). This is
deliberate: "peer group size" is measured by how many peers have a
USABLE (non-null) value for the metric being scored, not just how many
companies share the industry label — a structurally large industry
where only two members report a given metric is just as thin, for that
metric, as a two-company industry.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .winsorize import winsorize

PeerLevel = str  # "industry" | "sector" | "universe"


@dataclass(frozen=True)
class ZScoreResult:
    ticker: str
    raw_value: float | None
    winsorized_value: float | None
    z: float | None  # None only when raw_value itself was None (missing metric)
    peer_level: PeerLevel | None
    peer_group_size: int
    peer_mean: float | None
    peer_std: float | None


def _members_by_label(usable: pd.Series, group_of: dict[str, str]) -> dict[str, list[str]]:
    """Group label -> tickers (from `usable`'s index, in index order) carrying
    it, at one grouping (industry_of or sector_of). Tickers with no label
    belong to no group."""
    members: dict[str, list[str]] = {}
    for t in usable.index:
        label = group_of.get(t)
        if label is not None:
            members.setdefault(label, []).append(t)
    return members


@dataclass(frozen=True)
class _PeerIndex:
    """Everything the fallback ladder needs for one metric, built once."""

    universe: list[str]
    by_industry: dict[str, list[str]]
    by_sector: dict[str, list[str]]

    @classmethod
    def build(cls, values: pd.Series, industry_of: dict[str, str],
              sector_of: dict[str, str]) -> "_PeerIndex":
        usable = values.dropna()
        return cls(
            universe=list(usable.index),
            by_industry=_members_by_label(usable, industry_of),
            by_sector=_members_by_label(usable, sector_of),
        )

    def resolve(self, ticker: str, industry_of: dict[str, str],
                sector_of: dict[str, str], *, min_group_size: int,
                ) -> tuple[PeerLevel, list[str]]:
        industry_peers = self.by_industry.get(industry_of.get(ticker), [])
        if len(industry_peers) >= min_group_size:
            return "industry", industry_peers

        sector_peers = self.by_sector.get(sector_of.get(ticker), [])
        if len(sector_peers) >= min_group_size:
            return "sector", sector_peers

        return "universe", self.universe


def resolve_peer_group(
    ticker: str,
    values: pd.Series,  # ticker -> raw metric value (NaN for missing), whole universe
    industry_of: dict[str, str],
    sector_of: dict[str, str],
    *,
    min_group_size: int,
) -> tuple[PeerLevel, list[str]]:
    """The industry -> sector -> universe fallback ladder, applied for
    ONE ticker on ONE metric. Returns the level actually used and the
    list of tickers (with usable data) making up that peer group."""
    level, peers = _PeerIndex.build(values, industry_of, sector_of).resolve(
        ticker, industry_of, sector_of, min_group_size=min_group_size
    )
    return level, list(peers)


def zscore_metric(
    values: pd.Series,  # ticker -> raw metric value (NaN for missing), whole universe
    industry_of: dict[str, str],
    sector_of: dict[str, str],
    *,
    min_group_size: int,
    winsorize_low_pct: float,
    winsorize_high_pct: float,
) -> dict[str, ZScoreResult]:
    """z-score every ticker's value for ONE metric, each against its own
    resolved peer group. Steps, per the project spec's exact order:
    winsorize within the peer group, THEN z-score within the same
    (already-winsorized) peer group.
    """
    results: dict[str, ZScoreResult] = {}

    # Every ticker resolving to the same peer group shares that group's
    # winsorized values, mean and stdev, so they are computed once per group
    # rather than once per ticker. Recomputing them per ticker repeated the
    # same quantile/mean/stdev ~500 times per metric -- about 30 seconds of
    # every universe run -- for an identical answer.
    group_stats: dict[tuple[str, ...], tuple[pd.Series, float, float]] = {}
    peer_index = _PeerIndex.build(values, industry_of, sector_of)

    for ticker in values.index:
        raw = values[ticker]
        raw = None if pd.isna(raw) else float(raw)

        if raw is None:
            results[ticker] = ZScoreResult(
                ticker=ticker, raw_value=None, winsorized_value=None, z=None,
                peer_level=None, peer_group_size=0, peer_mean=None, peer_std=None,
            )
            continue

        level, peers = peer_index.resolve(
            ticker, industry_of, sector_of, min_group_size=min_group_size
        )
        key = tuple(peers)
        if key not in group_stats:
            winsorized = winsorize(
                values.loc[peers], low_pct=winsorize_low_pct, high_pct=winsorize_high_pct
            )
            group_stats[key] = (
                winsorized, float(winsorized.mean()), float(winsorized.std(ddof=1))
            )
        winsorized, mean, std = group_stats[key]
        w_value = float(winsorized[ticker])

        if len(peers) < 2:
            # A peer group of one (or zero): no way to establish relative
            # standing. Neutral, not undefined-and-crashing, and not
            # silently defaulted without a record of why.
            results[ticker] = ZScoreResult(
                ticker=ticker, raw_value=raw, winsorized_value=w_value, z=0.0,
                peer_level=level, peer_group_size=len(peers), peer_mean=w_value, peer_std=0.0,
            )
            continue

        z = 0.0 if std == 0 else (w_value - mean) / std

        results[ticker] = ZScoreResult(
            ticker=ticker, raw_value=raw, winsorized_value=w_value, z=z,
            peer_level=level, peer_group_size=len(peers), peer_mean=mean, peer_std=std,
        )

    return results
