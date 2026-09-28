"""Capital allocation bucket — the one the project spec calls out to get
right: is management deploying cash (buybacks, M&A) well or badly.

Five metrics: buyback_timing, buyback_yield, share_count_change_3y,
incremental_roic, ma_returns.
"""

from __future__ import annotations

import pandas as pd

from ..config import Config
from .base import MetricResult, TickerContext, safe_divide, ttm
from .finance import invested_capital, is_financials_sector, nopat_ttm

BUCKET = "capital_allocation"

# Growth in goodwill + intangibles below this fraction over the M&A-returns
# window is treated as "no real M&A activity", not as data worth scoring --
# see ma_returns() below.
MA_ACTIVITY_THRESHOLD = 0.02

# Plausibility bounds on the implied average repurchase price, as a
# multiple of the market's own VWAP over the same window. A company
# genuinely times buybacks well or badly within maybe +/-30% of the
# average price; it cannot buy its own stock at 3x or 1/3 the market
# price. A ratio outside these bounds means the dollar figure and the
# share-count figure are describing DIFFERENT things, not that timing
# was extraordinary -- see the guard in buyback_timing() below.
BUYBACK_PRICE_PLAUSIBLE_MIN = 1 / 3
BUYBACK_PRICE_PLAUSIBLE_MAX = 3.0


def buyback_timing(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Dollar-weighted average price the company paid for its own
    repurchases over the trailing window, vs. the market's VWAP over that
    SAME window. Buying below the market's own VWAP scores well; buying
    the top scores badly.

        avg_repurchase_price = sum(buybacks $) / sum(shares repurchased)
        value = (market_vwap - avg_repurchase_price) / market_vwap

    A positive value means the company bought at a discount to where the
    stock traded on average (good); negative means it bought at a
    premium (bad, buying the top).

    Currently unavailable against real data: finlake has no
    shares-repurchased-count concept yet (FINLAKE-FINDINGS.md F15 —
    flagged there as the highest-priority Phase C item, given this is
    the metric the project spec calls out as the one to get right).
    Reported honestly as blocked rather than approximated from the
    dollar amount alone, which would silently answer a different,
    less useful question ("did they spend a lot", not "did they buy
    low").
    """
    if "shares_repurchased" not in ctx.fundamentals.columns:
        return MetricResult(
            name="buyback_timing", bucket=BUCKET, value=None,
            substitution="blocked_missing_shares_repurchased_concept",
            note="needs a shares-repurchased-count concept finlake doesn't "
                 "expose yet (FINLAKE-FINDINGS.md F15)",
        )

    window = cfg.bucket("capital_allocation").param("buyback_window_quarters")
    dollars = ctx.series("buybacks").dropna().iloc[-window:]
    shares = ctx.series("shares_repurchased").dropna().iloc[-window:]

    # Only quarters reporting BOTH a dollar amount and a share count, and
    # both non-zero. A quarter with dollars but no share count (or vice
    # versa) can't contribute to an average price, and including one side
    # of it would silently skew the ratio.
    common = [
        p for p in dollars.index.intersection(shares.index)
        if dollars[p] > 0 and shares[p] > 0
    ]
    if not common:
        return MetricResult(
            name="buyback_timing", bucket=BUCKET, value=None,
            note="no quarter in the window reports both repurchase dollars "
                 "and a share count",
        )

    total_dollars = float(dollars.loc[common].sum())
    total_shares = float(shares.loc[common].sum())
    avg_price = total_dollars / total_shares
    start, end = str(min(common)), str(max(common))
    market_vwap = ctx.vwap(start, end)
    if market_vwap is None or market_vwap <= 0:
        return MetricResult(name="buyback_timing", bucket=BUCKET, value=None,
                             raw_inputs={"avg_repurchase_price": avg_price},
                             note="no cached price data over the buyback window")

    raw = {"total_dollars_repurchased": total_dollars,
           "total_shares_repurchased": total_shares,
           "avg_repurchase_price": avg_price, "market_vwap": market_vwap,
           "window_start": start, "window_end": end,
           "price_ratio_vs_vwap": avg_price / market_vwap,
           "quarters_used": len(common)}

    # Plausibility guard. A company cannot actually repurchase its own
    # stock at 3x or 1/3 the price it traded at over the same window --
    # a ratio that extreme means the two XBRL tags are measuring
    # different things, not that timing was extraordinary. Real example
    # this caught: SPG's StockRepurchasedDuringPeriodShares implies an
    # average price of $5,143-$14,906/share against a stock that traded
    # near $122 (the tag appears to cover LP-unit or preferred activity,
    # not the common-stock buyback program those dollars came from).
    # Without this guard that produced a buyback_timing of -51.5, which
    # would have dominated the entire capital-allocation bucket's
    # z-scores for the whole universe.
    ratio = avg_price / market_vwap
    if not (BUYBACK_PRICE_PLAUSIBLE_MIN <= ratio <= BUYBACK_PRICE_PLAUSIBLE_MAX):
        return MetricResult(
            name="buyback_timing", bucket=BUCKET, value=None, raw_inputs=raw,
            substitution="implausible_repurchase_price_vs_market",
            note=f"implied average repurchase price is {ratio:.1f}x the "
                 f"market VWAP over the same window -- the dollar and "
                 f"share-count tags are measuring different things; "
                 f"reported missing rather than scored",
        )

    return MetricResult(
        name="buyback_timing", bucket=BUCKET,
        value=(market_vwap - avg_price) / market_vwap, raw_inputs=raw,
    )


def buyback_yield(ctx: TickerContext, cfg: Config) -> MetricResult:
    """TTM repurchases / market cap."""
    dollars = ttm(ctx.series("buybacks"))
    shares = ctx.latest("shares_outstanding")
    price = ctx.price_as_of(ctx.as_of)
    if dollars is None or shares is None or price is None:
        return MetricResult(name="buyback_yield", bucket=BUCKET, value=None,
                             raw_inputs={"buybacks_ttm": dollars})

    mcap = shares * price
    value = safe_divide(dollars, mcap)
    return MetricResult(name="buyback_yield", bucket=BUCKET, value=value,
                         raw_inputs={"buybacks_ttm": dollars, "market_cap": mcap})


def share_count_change_3y(ctx: TickerContext, cfg: Config) -> MetricResult:
    """% reduction in diluted shares outstanding over 3 years — higher is
    better (more shares retired). A share-count INCREASE (dilution)
    produces a negative value, correctly scored as worse."""
    series = ctx.series("shares_diluted").dropna()
    if len(series) < 13:  # need a point roughly 3 years (12 quarters) back
        return MetricResult(name="share_count_change_3y", bucket=BUCKET, value=None,
                             note="fewer than 3 years of shares_diluted history")

    now, then = float(series.iloc[-1]), float(series.iloc[-13])
    if then <= 0:
        return MetricResult(name="share_count_change_3y", bucket=BUCKET, value=None)

    pct_reduction = (then - now) / then
    return MetricResult(
        name="share_count_change_3y", bucket=BUCKET, value=pct_reduction,
        raw_inputs={"shares_diluted_then": then, "shares_diluted_now": now,
                    "period_then": str(series.index[-13]), "period_now": str(series.index[-1])},
    )


def incremental_roic(ctx: TickerContext, cfg: Config) -> MetricResult:
    """ΔNOPAT / Δinvested capital over the configured window (default 3
    years / 12 quarters) — how much incremental profit each new dollar
    of capital deployed is earning. Not applicable to financials, same
    reasoning as Quality's roic() (see DEC-002 in lodestar's vault)."""
    if is_financials_sector(ctx):
        return MetricResult(name="incremental_roic", bucket=BUCKET, value=None,
                             substitution="not_applicable_financials_sector",
                             note="see DEC-002")

    years = cfg.bucket("capital_allocation").param("incremental_roic_window_years")
    quarters = years * 4
    if ctx.fundamentals.empty or len(ctx.fundamentals.index) <= quarters:
        return MetricResult(name="incremental_roic", bucket=BUCKET, value=None,
                             note=f"fewer than {quarters + 1} quarters of history")

    end_period = ctx.fundamentals.index[-1]
    begin_period = ctx.fundamentals.index[-(quarters + 1)]

    nopat_now = nopat_ttm(ctx, cfg)
    ic_now = invested_capital(ctx, period_end=end_period)

    truncated = ctx.fundamentals[ctx.fundamentals.index <= begin_period]
    past_ctx = TickerContext(ticker=ctx.ticker, as_of=begin_period, cik=ctx.cik,
                              fundamentals=truncated, prices=ctx.prices,
                              sector=ctx.sector, industry=ctx.industry)
    nopat_then = nopat_ttm(past_ctx, cfg)
    ic_then = invested_capital(past_ctx, period_end=begin_period)

    if any(v.value is None for v in (nopat_now, ic_now, nopat_then, ic_then)):
        return MetricResult(
            name="incremental_roic", bucket=BUCKET, value=None,
            raw_inputs={"nopat_now": nopat_now.value, "ic_now": ic_now.value,
                        "nopat_then": nopat_then.value, "ic_then": ic_then.value},
        )

    d_nopat = nopat_now.value - nopat_then.value
    d_ic = ic_now.value - ic_then.value
    value = safe_divide(d_nopat, d_ic)
    return MetricResult(
        name="incremental_roic", bucket=BUCKET, value=value,
        raw_inputs={"nopat_now": nopat_now.value, "ic_now": ic_now.value,
                    "nopat_then": nopat_then.value, "ic_then": ic_then.value,
                    "begin_period": begin_period, "end_period": end_period},
    )


def ma_returns(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Low-confidence M&A-returns proxy: growth in goodwill + intangibles
    (a stand-in for "M&A activity happened") vs. the SAME incremental-ROIC
    figure already computed for capital allocation generally.

    XBRL has no deal-level IRR, so this doesn't try to compute one. It
    only fires for names that actually grew goodwill/intangibles by a
    meaningful amount over the window — for a company with no real M&A
    activity, there is nothing here to evaluate, and reporting a number
    anyway would imply a precision this proxy doesn't have.

    Goodwill impairments taken over the window (finlake 0.3.0's
    `goodwill_impairment` concept, FINLAKE-FINDINGS.md F16) are
    subtracted from the incremental-ROIC figure, scaled by the goodwill
    base: an impairment is management writing off its own acquisition,
    which is the most direct evidence available that a deal didn't earn
    its cost. Still marked low-confidence and given a small weight in
    config.yaml, per the spec's instruction not to pretend to precision
    this proxy doesn't have.
    """
    goodwill = ctx.series("goodwill").dropna()
    intangibles = ctx.series("intangible_assets").dropna()
    if len(goodwill) < 13 and len(intangibles) < 13:
        return MetricResult(name="ma_returns", bucket=BUCKET, value=None,
                             note="insufficient goodwill/intangibles history")

    def _growth(series: pd.Series) -> float:
        if len(series) < 13 or series.iloc[-13] == 0:
            return 0.0
        return float((series.iloc[-1] - series.iloc[-13]) / abs(series.iloc[-13]))

    gw_growth = _growth(goodwill)
    intan_growth = _growth(intangibles)
    combined_growth = max(gw_growth, intan_growth)

    if combined_growth < MA_ACTIVITY_THRESHOLD:
        return MetricResult(
            name="ma_returns", bucket=BUCKET, value=None,
            substitution="not_applicable_no_ma_activity",
            raw_inputs={"goodwill_growth_3y": gw_growth, "intangibles_growth_3y": intan_growth},
            note="goodwill/intangibles didn't grow meaningfully; no M&A "
                 "activity to evaluate a return on",
        )

    inc_roic = incremental_roic(ctx, cfg)
    if inc_roic.value is None:
        return MetricResult(
            name="ma_returns", bucket=BUCKET, value=None,
            raw_inputs={"goodwill_growth_3y": gw_growth,
                        "intangibles_growth_3y": intan_growth, **inc_roic.raw_inputs},
            note="M&A activity detected but incremental ROIC unavailable",
        )

    # Impairments taken over the same trailing window, scaled by the
    # goodwill base they were written off against. Subtracting this from
    # incremental ROIC penalises a company that grew goodwill and then
    # wrote it back off -- the clearest available signal that a deal
    # didn't earn its cost.
    impairments = ctx.series("goodwill_impairment").dropna()
    impairment_total = float(impairments.iloc[-12:].sum()) if len(impairments) else 0.0
    goodwill_base = float(goodwill.iloc[-1]) if len(goodwill) else 0.0
    impairment_drag = (
        impairment_total / goodwill_base if goodwill_base > 0 else 0.0
    )

    return MetricResult(
        name="ma_returns", bucket=BUCKET, value=inc_roic.value - impairment_drag,
        raw_inputs={"goodwill_growth_3y": gw_growth, "intangibles_growth_3y": intan_growth,
                    "incremental_roic": inc_roic.value,
                    "goodwill_impairment_total": impairment_total,
                    "goodwill_base": goodwill_base,
                    "impairment_drag": impairment_drag, **inc_roic.raw_inputs},
        substitution="low_confidence_ma_proxy",
        note="proxy: incremental ROIC over the M&A window, less goodwill "
             "impairments taken (scaled by the goodwill base)",
    )


METRICS = {
    "buyback_timing": buyback_timing,
    "buyback_yield": buyback_yield,
    "share_count_change_3y": share_count_change_3y,
    "incremental_roic": incremental_roic,
    "ma_returns": ma_returns,
}
