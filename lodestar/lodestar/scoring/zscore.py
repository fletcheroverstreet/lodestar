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


def _peer_members(
    ticker: str, usable: pd.Series, group_of: dict[str, str]
) -> list[str]:
    """Tickers (from `usable`'s index) sharing `ticker`'s group label at
    the given grouping (industry_of or sector_of)."""
    label = group_of.get(ticker)
    if label is None:
        return []
    return [t for t in usable.index if group_of.get(t) == label]


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
    usable = values.dropna()

    industry_peers = _peer_members(ticker, usable, industry_of)
    if len(industry_peers) >= min_group_size:
        return "industry", industry_peers

    sector_peers = _peer_members(ticker, usable, sector_of)
    if len(sector_peers) >= min_group_size:
        return "sector", sector_peers

    return "universe", list(usable.index)


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

    for ticker in values.index:
        raw = values[ticker]
        raw = None if pd.isna(raw) else float(raw)

        if raw is None:
            results[ticker] = ZScoreResult(
                ticker=ticker, raw_value=None, winsorized_value=None, z=None,
                peer_level=None, peer_group_size=0, peer_mean=None, peer_std=None,
            )
            continue

        level, peers = resolve_peer_group(
            ticker, values, industry_of, sector_of, min_group_size=min_group_size
        )
        peer_values = values.loc[peers]
        winsorized = winsorize(peer_values, low_pct=winsorize_low_pct, high_pct=winsorize_high_pct)
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

        mean = float(winsorized.mean())
        std = float(winsorized.std(ddof=1))
        z = 0.0 if std == 0 else (w_value - mean) / std

        results[ticker] = ZScoreResult(
            ticker=ticker, raw_value=raw, winsorized_value=w_value, z=z,
            peer_level=level, peer_group_size=len(peers), peer_mean=mean, peer_std=std,
        )

    return results
