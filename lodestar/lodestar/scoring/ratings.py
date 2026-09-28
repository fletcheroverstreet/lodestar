"""Buy/sell ratings at three levels: individual stock, industry, sector.

Every rating derives from the SAME composite z-scores the ranking engine
already produces, so they all inherit its peer-relative standardization,
coverage discipline, and attribution. A rating is a readable label on top
of a number you can already trace back to a filing — not a separate
opinion layer with its own logic.

Two different questions get answered per stock, and they are genuinely
different:

  `rating`         — how does this name score against the WHOLE universe?
                     (absolute standing; a great semiconductor company
                     and a great bank both read BUY)
  `peer_rating`    — how does it score against its own INDUSTRY peers?
                     (relative standing; the best house in a bad
                     neighbourhood reads BUY on peer_rating and may still
                     read SELL on the absolute one)

Both are reported. A name where they disagree is exactly the interesting
case — a cheap name in an expensive industry, or vice versa — and
collapsing them into one number would hide that.

HONEST SCOPE — this is not a live quote board. lodestar has no real-time
market feed: finlake caches daily OHLCV bars to disk, and fundamentals
arrive only when a company files. A rating reflects the data as of the
run that produced it, and changes when you re-run after new prices or
filings land — daily at best, not tick-by-tick.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Rating thresholds on a composite z-score. Deliberately conservative: a
# composite z is already universe-relative, so +0.75 means this name sits
# three quarters of a standard deviation above the average name in the
# whole universe — a meaningful tilt, not noise.
RATING_BANDS: list[tuple[float, str]] = [
    (0.75, "STRONG BUY"),
    (0.25, "BUY"),
    (-0.25, "HOLD"),
    (-0.75, "SELL"),
    (float("-inf"), "STRONG SELL"),
]

NO_RATING = "INSUFFICIENT DATA"

# A group (industry or sector) with fewer scored members than this
# doesn't get a rating — one or two names is a company opinion, not a
# group one.
MIN_MEMBERS_FOR_GROUP_RATING = 2


def band_for(score: float | None) -> str:
    """The rating label for a composite z-score."""
    if score is None or pd.isna(score):
        return NO_RATING
    for threshold, label in RATING_BANDS:
        if score >= threshold:
            return label
    return "STRONG SELL"


# --------------------------------------------------------------- per stock
@dataclass(frozen=True)
class NameRating:
    ticker: str
    industry: str
    sector: str
    score: float | None
    percentile: float | None
    rating: str            # vs. the whole universe
    peer_rating: str       # vs. this name's own industry
    peer_z: float | None   # z-score of this name within its industry
    peer_rank: int | None  # 1 = best in its industry
    peer_count: int


def rate_names(composite_table: pd.DataFrame) -> list[NameRating]:
    """One rating per ticker. Needs the shape
    persistence.load_composite_table returns: one row per ticker with
    `ticker`, `industry`, `sector`, `composite_raw`, `percentile`."""
    if composite_table.empty:
        return []

    out: list[NameRating] = []
    for industry, group in composite_table.groupby("industry", dropna=False):
        scored = group[group["composite_raw"].notna()]
        # Within-industry standardization: how does this name look
        # against the peers it actually competes with, independent of
        # whether the whole industry is in or out of favour.
        if len(scored) >= 2:
            mean = float(scored["composite_raw"].mean())
            std = float(scored["composite_raw"].std(ddof=1))
        else:
            mean, std = 0.0, 0.0

        ranked = scored.sort_values("composite_raw", ascending=False)
        rank_of = {t: i + 1 for i, t in enumerate(ranked["ticker"])}

        for _, row in group.iterrows():
            score = row["composite_raw"]
            score = None if pd.isna(score) else float(score)
            peer_z = ((score - mean) / std) if (score is not None and std > 0) else None
            out.append(NameRating(
                ticker=str(row["ticker"]),
                industry=str(industry),
                sector=str(row.get("sector", "")),
                score=score,
                percentile=None if pd.isna(row.get("percentile")) else float(row["percentile"]),
                rating=band_for(score),
                peer_rating=band_for(peer_z) if peer_z is not None else NO_RATING,
                peer_z=peer_z,
                peer_rank=rank_of.get(str(row["ticker"])),
                peer_count=len(scored),
            ))

    return sorted(out, key=lambda r: (r.score if r.score is not None else float("-inf")),
                   reverse=True)


# ------------------------------------------------------- per group (shared)
@dataclass(frozen=True)
class GroupRating:
    """A rating for an industry or a sector — same shape either way."""

    group: str
    level: str             # "industry" | "sector"
    parent: str            # the sector an industry belongs to; "" for a sector
    rating: str
    mean_score: float | None
    median_score: float | None
    best_ticker: str | None
    worst_ticker: str | None
    member_count: int
    scored_count: int
    dispersion: float | None  # stdev of member scores; high = stock-picker's group
    buy_count: int            # members rated BUY or STRONG BUY
    sell_count: int           # members rated SELL or STRONG SELL
    as_of: str


def _rate_groups(
    composite_table: pd.DataFrame, *, by: str, level: str, as_of: str,
) -> list[GroupRating]:
    if composite_table.empty or by not in composite_table.columns:
        return []

    ratings: list[GroupRating] = []
    for group_name, group in composite_table.groupby(by, dropna=True):
        scored = group[group["composite_raw"].notna()]
        parent = ""
        if level == "industry" and "sector" in group.columns and len(group):
            parent = str(group["sector"].iloc[0])

        member_labels = [band_for(float(s)) for s in scored["composite_raw"]]
        buy_count = sum(1 for m in member_labels if m in ("BUY", "STRONG BUY"))
        sell_count = sum(1 for m in member_labels if m in ("SELL", "STRONG SELL"))

        if len(scored) < MIN_MEMBERS_FOR_GROUP_RATING:
            ratings.append(GroupRating(
                group=str(group_name), level=level, parent=parent, rating=NO_RATING,
                mean_score=None, median_score=None, best_ticker=None, worst_ticker=None,
                member_count=len(group), scored_count=len(scored), dispersion=None,
                buy_count=buy_count, sell_count=sell_count, as_of=as_of,
            ))
            continue

        mean_score = float(scored["composite_raw"].mean())
        ordered = scored.sort_values("composite_raw", ascending=False)
        ratings.append(GroupRating(
            group=str(group_name), level=level, parent=parent,
            rating=band_for(mean_score), mean_score=mean_score,
            median_score=float(scored["composite_raw"].median()),
            best_ticker=str(ordered["ticker"].iloc[0]),
            worst_ticker=str(ordered["ticker"].iloc[-1]),
            member_count=len(group), scored_count=len(scored),
            dispersion=float(scored["composite_raw"].std(ddof=1)),
            buy_count=buy_count, sell_count=sell_count, as_of=as_of,
        ))

    return sorted(ratings,
                   key=lambda r: (r.mean_score if r.mean_score is not None else float("-inf")),
                   reverse=True)


def rate_industries(composite_table: pd.DataFrame, *, as_of: str) -> list[GroupRating]:
    return _rate_groups(composite_table, by="industry", level="industry", as_of=as_of)


def rate_sectors(composite_table: pd.DataFrame, *, as_of: str) -> list[GroupRating]:
    return _rate_groups(composite_table, by="sector", level="sector", as_of=as_of)


# ------------------------------------------------------------------ frames
def names_to_frame(ratings: list[NameRating]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker": r.ticker, "rating": r.rating, "score": r.score,
            "percentile": r.percentile, "peer_rating": r.peer_rating,
            "peer_z": r.peer_z, "peer_rank": r.peer_rank, "peer_count": r.peer_count,
            "industry": r.industry, "sector": r.sector,
        }
        for r in ratings
    ])


def groups_to_frame(ratings: list[GroupRating]) -> pd.DataFrame:
    label = ratings[0].level if ratings else "group"
    return pd.DataFrame([
        {
            label: r.group, "rating": r.rating, "mean_score": r.mean_score,
            "median_score": r.median_score, "dispersion": r.dispersion,
            "buys": r.buy_count, "sells": r.sell_count,
            "best": r.best_ticker, "worst": r.worst_ticker,
            "members": r.member_count, "scored": r.scored_count,
            **({"sector": r.parent} if r.level == "industry" else {}),
        }
        for r in ratings
    ])
