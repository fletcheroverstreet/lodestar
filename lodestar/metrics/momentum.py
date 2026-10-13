"""Momentum bucket: price trend, fundamental revision, and rank trend.

Three metrics: price_12_1, fundamental_revision, rank_change.
"""

from __future__ import annotations

import datetime as dt

from ..adapter import get_fundamentals
from ..config import Config
from .base import MetricResult, TickerContext, safe_divide, ttm

BUCKET = "momentum"


def _price_n_trading_days_before(ctx: TickerContext, n: int) -> float | None:
    """The closing (split-adjusted) price `n` TRADING days before the
    last cached trading day on or before ctx.as_of. Trading days, not
    calendar days -- ctx.prices already contains only real trading
    sessions, so this is a positional lookback, not a date-arithmetic
    one (which would need to skip weekends/holidays itself)."""
    if ctx.prices.empty:
        return None
    window = ctx.prices[ctx.prices["date"] <= ctx.as_of].sort_values("date")
    if len(window) <= n:
        return None
    col = "adj_close" if "adj_close" in window.columns else "close"
    return float(window[col].iloc[-1 - n])


def price_12_1(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Return from t-252 trading days to t-21 trading days -- the
    standard "12 minus 1 month" momentum construction, which deliberately
    excludes the most recent month (where short-term reversal, not
    momentum, tends to dominate)."""
    p_252 = _price_n_trading_days_before(ctx, 252)
    p_21 = _price_n_trading_days_before(ctx, 21)
    if p_252 is None or p_21 is None or p_252 == 0:
        return MetricResult(name="price_12_1", bucket=BUCKET, value=None,
                             raw_inputs={"price_t_minus_252": p_252, "price_t_minus_21": p_21})

    value = (p_21 - p_252) / p_252
    return MetricResult(
        name="price_12_1", bucket=BUCKET, value=value,
        raw_inputs={"price_t_minus_252": p_252, "price_t_minus_21": p_21},
    )


def fundamental_revision(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Proxy for consensus estimate-revision breadth.

    finlake now captures consensus estimates and how they have moved (its
    `estimates` and `estimate_trend` tables), but this metric has not been
    switched over to them yet. Until it is, it measures revisions to FILED
    numbers instead: TTM EPS and TTM revenue as known TODAY vs. as known
    `revision_lookback_days` ago, using finlake's bitemporal `filed`
    column. A positive value means more/better information became
    public in the lookback window (new filings, or upward restatements)
    — the same underlying idea as an analyst raising estimates, just
    built from what actually got filed rather than a forecast. See
    DEC-007 in lodestar's vault.

    This needs a SECOND finlake.fundamentals() call at an earlier as_of
    — not a local truncation of the current frame, because the current
    frame only carries each period's LATEST-as-of-today value and filed
    date, not the full restatement history that would be needed to
    reconstruct exactly what was known at an earlier date for periods
    that have since been revised.
    """
    lookback_days = cfg.bucket("momentum").param("revision_lookback_days")
    earlier_as_of = (
        dt.date.fromisoformat(ctx.as_of) - dt.timedelta(days=lookback_days)
    ).isoformat()

    earlier = get_fundamentals(ctx.ticker, as_of=earlier_as_of, years=3)
    if earlier.frame.empty:
        return MetricResult(name="fundamental_revision", bucket=BUCKET, value=None,
                             note=f"no fundamentals data as of {earlier_as_of}")

    eps_now = ctx.latest("eps_diluted")  # a rate, not summed -- use latest reported, not a TTM sum
    eps_then = float(earlier.frame["eps_diluted"].dropna().iloc[-1]) \
        if "eps_diluted" in earlier.frame.columns and earlier.frame["eps_diluted"].notna().any() else None

    rev_now = ttm(ctx.series("revenue"))
    rev_then = ttm(earlier.frame["revenue"]) if "revenue" in earlier.frame.columns else None

    revisions = []
    if eps_now is not None and eps_then not in (None, 0):
        revisions.append((eps_now - eps_then) / abs(eps_then))
    if rev_now is not None and rev_then not in (None, 0):
        revisions.append((rev_now - rev_then) / abs(rev_then))

    if not revisions:
        return MetricResult(
            name="fundamental_revision", bucket=BUCKET, value=None,
            raw_inputs={"eps_now": eps_now, "eps_then": eps_then,
                        "revenue_ttm_now": rev_now, "revenue_ttm_then": rev_then},
        )

    value = sum(revisions) / len(revisions)
    return MetricResult(
        name="fundamental_revision", bucket=BUCKET, value=value,
        raw_inputs={"eps_now": eps_now, "eps_then": eps_then,
                    "revenue_ttm_now": rev_now, "revenue_ttm_then": rev_then,
                    "earlier_as_of": earlier_as_of},
        substitution="proxy_fundamental_revision_not_analyst_estimates",
        note="finlake has no estimates feed (ISSUE-003); this uses filed-date "
             "bitemporality instead of consensus-estimate revisions",
    )


def rank_change(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Change in composite percentile rank between the two most recent
    COMPLETED prior runs — never the run currently being computed, which
    would be circular (its own composite doesn't exist yet while its
    bucket metrics, including this one, are being computed). See
    DEC-006 in lodestar's vault.

    Unavailable for the first two runs of a universe, honestly, since
    there's nothing to compare yet.
    """
    history = ctx.prior_rank_history
    if len(history) < 2:
        return MetricResult(name="rank_change", bucket=BUCKET, value=None,
                             note=f"only {len(history)} prior run(s) on record; need >= 2")

    # history is oldest-first; [-1] is the most recent completed prior
    # run, [-2] the one before that. A positive value means the name's
    # rank improved between those two runs.
    value = history[-1] - history[-2]
    return MetricResult(
        name="rank_change", bucket=BUCKET, value=value,
        raw_inputs={"most_recent_prior_percentile": history[-1],
                    "second_most_recent_prior_percentile": history[-2]},
    )


METRICS = {
    "price_12_1": price_12_1,
    "fundamental_revision": fundamental_revision,
    "rank_change": rank_change,
}
