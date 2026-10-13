"""Thin wrapper over finlake — the only module in lodestar allowed to
import finlake directly.

lodestar calls only finlake's public surface: fundamentals(), prices(),
vwap(), universe(), restatements(). Never finlake.pit, finlake.store, or
finlake.sources — those are finlake's internals, and reaching past the
public surface is exactly how a data-layer upgrade silently breaks a
downstream tool. Keeping the boundary here means a finlake upgrade only
has to be re-verified in one file.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import finlake

# The finlake version this project was built and regression-verified
# against (see FINLAKE-FINDINGS.md). Bump this deliberately, after
# re-running the regression snapshot, never as a side effect of an
# unrelated change.
PINNED_FINLAKE_VERSION = "0.13.0"


def check_finlake_version() -> None:
    """Fail loudly rather than silently running against an unverified
    finlake version. Every public adapter function calls this first."""
    if finlake.__version__ != PINNED_FINLAKE_VERSION:
        raise RuntimeError(
            f"lodestar is pinned to finlake=={PINNED_FINLAKE_VERSION}, but "
            f"the installed finlake reports {finlake.__version__}. The "
            f"findings and regression checks in FINLAKE-FINDINGS.md were "
            f"verified against the pinned version only — update "
            f"PINNED_FINLAKE_VERSION here (and re-verify) before running "
            f"against a different one."
        )


@dataclass(frozen=True)
class Fundamentals:
    """Result of a point-in-time fundamentals pull for one ticker."""

    ticker: str
    cik: int | None
    as_of: str
    frame: pd.DataFrame  # empty if the ticker has no usable data as-of


def get_fundamentals(ticker: str, *, as_of: str, years: int = 10) -> Fundamentals:
    """Ten years (by default) of quarterly fundamentals, as known on
    `as_of`. Always requests provenance columns (__derived, __filed) —
    lodestar's attribution and restated-quarter flagging both need them."""
    check_finlake_version()
    try:
        df = finlake.fundamentals(
            ticker, years=years, as_of=as_of, include_provenance=True
        )
    except KeyError:
        # No CIK for this ticker as of this date -- e.g. a pre-IPO name.
        # An empty frame, not an exception, is the correct honest answer:
        # the caller (universe construction, metric computation) already
        # knows how to treat "no data" as a coverage gap.
        df = pd.DataFrame()

    cik = df.attrs.get("cik") if not df.empty else None
    return Fundamentals(ticker=ticker.upper(), cik=cik, as_of=as_of, frame=df)


def get_prices(
    ticker: str, *, start: str, end: str, adjust: str = "split"
) -> pd.DataFrame:
    check_finlake_version()
    try:
        return finlake.prices(ticker, start=start, end=end, adjust=adjust)
    except FileNotFoundError:
        # No cached price data for this ticker. Same philosophy as above:
        # an empty frame is the honest signal, not a crash.
        return pd.DataFrame()


def get_vwap(
    ticker: str, *, start: str, end: str, adjust: str = "split"
) -> float | None:
    check_finlake_version()
    return finlake.vwap(ticker, start=start, end=end, adjust=adjust)


def get_universe(as_of: str) -> pd.DataFrame:
    """finlake's filing-activity-proxy universe as of a date. See
    lodestar.universe for the constituent-file override and the
    intersection with tickers that actually have cached data."""
    check_finlake_version()
    return finlake.universe(as_of=as_of)


def get_restatements(ticker: str, concept: str, period_end: str) -> pd.DataFrame:
    check_finlake_version()
    return finlake.restatements(ticker, concept, period_end)


def get_news_signal(
    tickers: list[str], *, as_of: str, lookback_days: int = 60,
    half_life_days: float = 20.0,
) -> dict[str, dict]:
    """Aggregate news signal for the whole universe in one call.

    Batched deliberately: scoring 500 names one query at a time would do 500
    passes over the news table for a single run.

    Returns finlake's raw dicts rather than lodestar's NewsSignal — the
    translation happens in metrics/news.py, so the shape finlake returns can
    change without this file's callers caring.
    """
    check_finlake_version()
    try:
        return finlake.news_signal(
            tickers, as_of=as_of, lookback_days=lookback_days,
            half_life_days=half_life_days)
    except Exception:
        # No news table yet, or an unreadable cache. Empty means every ticker
        # reports zero coverage and the bucket drops out — the same honest
        # degradation as having no provider at all, rather than a failed run.
        return {}


def refresh_data_sources(*, limit: int | None = None,
                         on_task=None) -> dict[str, int]:
    """Run whatever finlake refresh tier is due. Rows touched per task.

    Goes through finlake's public surface rather than `finlake.refresh`, so
    the boundary this module exists to hold still holds for the background
    loop — see the note at the top of this file.
    """
    check_finlake_version()
    try:
        return finlake.refresh_once(limit=limit, on_task=on_task)
    except Exception as exc:  # noqa: BLE001
        # A dead feed must not stop the loop that also re-scores. finlake
        # already isolates each task; this catches only a total failure to
        # reach the cache at all.
        return {"error": 0, "note": str(exc)[:200]}  # type: ignore[dict-item]


def data_source_status() -> list[dict]:
    """What each finlake refresh tier last did, and whether it is due."""
    check_finlake_version()
    try:
        return finlake.refresh_status()
    except Exception:
        return []


def data_universe_status() -> dict:
    """How many symbols the refresh tiers keep current, and from where.

    Surfaced in the daemon header because the answer used to be wrong in a way
    nothing reported: the loop swept every ticker with cached facts, which is
    711 instruments — preferred series, warrants and baby bonds included —
    against the 503-name list the hub actually scores.
    """
    check_finlake_version()
    try:
        return finlake.universe_status()
    except Exception:
        return {}


def get_ratios_latest(ticker: str, *, as_of: str, live: bool = False) -> dict:
    """Latest valuation ratios for one name, as a flat dict.

    Used to snapshot ratios alongside a run so the UI can read rather than
    compute them. Returns {} on failure — a name whose ratios cannot be
    derived should leave a gap in the snapshot, not abort a 500-name run.

    `live=True` prices the newest period at the latest close AND uses the
    market source's consolidated share count. The second half is what matters
    for the snapshot: a multi-class filer's `CommonStockSharesOutstanding`
    covers one class, so the filed market cap for Visa came out at $41bn
    against $673bn — and the screener's re-pricing scales that figure by the
    price move, which preserves the error rather than fixing it.

    Only ever passed for a run dated today. A historical run must snapshot
    what was knowable then.
    """
    check_finlake_version()
    try:
        return finlake.ratios_latest(ticker, as_of=as_of, live=live)
    except Exception:
        return {}
