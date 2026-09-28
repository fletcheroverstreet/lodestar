"""Quality bucket: profitability level AND consistency, not level alone.

Five metrics: ROIC, ROIC stability, gross margin trend, FCF conversion,
interest coverage. See the project's methodology write-up in README.md
for the plain-English version of each formula.
"""

from __future__ import annotations

import pandas as pd

from ..config import Config
from .base import MetricResult, TickerContext, ols_slope, safe_divide, ttm, ttm_series
from .finance import ebit_ttm, invested_capital, is_financials_sector, nopat_ttm

BUCKET = "quality"


def _roic_series(ctx: TickerContext, cfg: Config, *, quarters: int) -> pd.Series:
    """TTM ROIC computed at each of the trailing `quarters` quarter-ends
    that have both a valid NOPAT-TTM and an invested-capital figure as
    of that date. Shared by roic() (uses the last point) and
    roic_stability() (uses the whole series)."""
    # NOPAT needs both EBIT (which itself may fall back to pretax+interest)
    # and the effective tax rate -- both TTM quantities -- so it can't be
    # built from a single ttm_series() call the way a plain concept can.
    # Compute it quarter by quarter using the same nopat_ttm() building
    # block the point-in-time roic() metric uses, just walking backward
    # through history instead of stopping at the latest quarter.
    period_ends = ctx.fundamentals.index.tolist() if not ctx.fundamentals.empty else []
    period_ends = period_ends[-quarters:] if quarters else period_ends

    values: list[float] = []
    index: list[str] = []
    for period_end in period_ends:
        # A historical NOPAT "as of" a past quarter would require re-
        # running fundamentals() with that as_of date to respect point-
        # in-time bounds strictly. For a stability metric over a 20-
        # quarter window, that's 20x the finlake calls per name --
        # acceptable for a Sunday-night batch run, not for this in-memory
        # helper. Approximation used here: truncate today's already-PIT-
        # correct fundamentals frame to rows up to `period_end` before
        # computing NOPAT and invested capital, which is correct for
        # every quarter except one that was itself later restated -- and
        # a restated quarter would show up as a big swing in the
        # resulting stability score, which is visible, not silently wrong.
        truncated = ctx.fundamentals[ctx.fundamentals.index <= period_end]
        trunc_ctx = TickerContext(
            ticker=ctx.ticker, as_of=period_end, cik=ctx.cik,
            fundamentals=truncated, prices=ctx.prices,
            sector=ctx.sector, industry=ctx.industry,
        )
        nopat = nopat_ttm(trunc_ctx, cfg)
        ic = invested_capital(trunc_ctx, period_end=period_end)
        if nopat.value is None or ic.value is None or ic.value == 0:
            continue
        values.append(nopat.value / ic.value)
        index.append(period_end)

    return pd.Series(values, index=index, dtype=float)


def roic(ctx: TickerContext, cfg: Config) -> MetricResult:
    """ROIC = NOPAT / invested capital, TTM (latest point).

    Not applicable to financials (banks, brokers): "total debt + equity -
    cash" isn't a coherent invested-capital figure for a leveraged
    financial intermediary funded mainly by deposits, not the kind of
    debt XBRL tags as such — confirmed against real data during the
    build (JPM has no debt_long tag at all; the industrial-style formula
    computed on what little debt data JPM does have produced a ~98%
    "ROIC", not a real number). See DEC-002 in lodestar's vault, and the
    same treatment the project spec already gives P/B in reverse (gated
    ON for financials only, here gated OFF).
    """
    if is_financials_sector(ctx):
        return MetricResult(name="roic", bucket=BUCKET, value=None,
                             substitution="not_applicable_financials_sector",
                             note="invested-capital framework doesn't fit a "
                                  "deposit-funded balance sheet; see DEC-002")

    nopat = nopat_ttm(ctx, cfg)
    ic = invested_capital(ctx)
    value = safe_divide(nopat.value, ic.value) if ic.value != 0 else None
    subs = [s for s in (nopat.substitution, ic.substitution) if s]
    return MetricResult(
        name="roic", bucket=BUCKET, value=value,
        raw_inputs={"nopat_ttm": nopat.value, "invested_capital": ic.value,
                    **nopat.raw_inputs},
        substitution=",".join(subs) if subs else None,
    )


def roic_stability(ctx: TickerContext, cfg: Config) -> MetricResult:
    """mean(ROIC) / stdev(ROIC) over the trailing window. Rewards
    consistency, not just level -- a company with ROIC steady at 15% every
    quarter scores better here than one that swings between 5% and 25%
    even if both average 15%. Not applicable to financials -- see roic()."""
    if is_financials_sector(ctx):
        return MetricResult(name="roic_stability", bucket=BUCKET, value=None,
                             substitution="not_applicable_financials_sector",
                             note="see DEC-002")

    window = cfg.bucket("quality").param("roic_stability_window_quarters")
    series = _roic_series(ctx, cfg, quarters=window)
    if len(series) < 4:  # need at least a few points for a meaningful stdev
        return MetricResult(name="roic_stability", bucket=BUCKET, value=None,
                             note=f"only {len(series)} quarterly ROIC points available, need >= 4")

    mean, std = float(series.mean()), float(series.std(ddof=1))
    if std == 0:
        return MetricResult(
            name="roic_stability", bucket=BUCKET, value=None,
            raw_inputs={"roic_mean": mean, "roic_std": std, "n_quarters": len(series)},
            note="zero variance in ROIC over the window -- ratio undefined",
        )
    return MetricResult(
        name="roic_stability", bucket=BUCKET, value=mean / std,
        raw_inputs={"roic_mean": mean, "roic_std": std, "n_quarters": len(series)},
    )


def gross_margin_trend(ctx: TickerContext, cfg: Config) -> MetricResult:
    """OLS slope of TTM gross margin over the trailing window, in basis
    points per quarter. Unavailable for filers with no discrete
    gross_profit line (common for banks) -- that's expected, not a bug;
    "gross margin" isn't a coherent concept for a bank's income statement."""
    window = cfg.bucket("quality").param("gross_margin_trend_window_quarters")
    gp_ttm = ttm_series(ctx.series("gross_profit"))
    rev_ttm = ttm_series(ctx.series("revenue"))
    common_idx = gp_ttm.index.intersection(rev_ttm.index)
    if len(common_idx) < 2:
        return MetricResult(name="gross_margin_trend", bucket=BUCKET, value=None,
                             note="insufficient gross_profit/revenue history")

    margin = (gp_ttm.loc[common_idx] / rev_ttm.loc[common_idx]).sort_index()
    margin = margin.iloc[-window:]
    slope = ols_slope(margin)
    if slope is None:
        return MetricResult(name="gross_margin_trend", bucket=BUCKET, value=None,
                             note="fewer than 2 usable margin points in the window")

    slope_bps = slope * 10_000.0  # margin is a fraction; 1 bp = 0.0001
    return MetricResult(
        name="gross_margin_trend", bucket=BUCKET, value=slope_bps,
        raw_inputs={"n_quarters": len(margin), "margin_first": float(margin.iloc[0]),
                    "margin_last": float(margin.iloc[-1])},
    )


def fcf_conversion(ctx: TickerContext, cfg: Config) -> MetricResult:
    """TTM FCF / TTM net income. FCF = CFO - capex.

    When net income <= 0 the ratio isn't meaningful (dividing by a loss
    inverts the sign of the whole metric), so the spec's fallback is
    FCF / EBITDA, with EBITDA = EBIT + D&A. That fallback is live as of
    finlake 0.3.0, which added the D&A concept (FINLAKE-FINDINGS.md F14);
    it's always flagged as a substitution so a fallback-derived value is
    never mistaken for the primary one.
    """
    cfo = ttm(ctx.series("cfo"))
    capex = ttm(ctx.series("capex"))
    net_income = ttm(ctx.series("net_income"))
    if cfo is None or capex is None:
        return MetricResult(name="fcf_conversion", bucket=BUCKET, value=None,
                             raw_inputs={"cfo_ttm": cfo, "capex_ttm": capex})

    fcf = cfo - capex
    if net_income is not None and net_income > 0:
        return MetricResult(
            name="fcf_conversion", bucket=BUCKET, value=fcf / net_income,
            raw_inputs={"cfo_ttm": cfo, "capex_ttm": capex, "fcf_ttm": fcf,
                        "net_income_ttm": net_income},
        )

    # Fallback: FCF / EBITDA, where EBITDA = EBIT + D&A.
    ebit = ebit_ttm(ctx)
    da = ttm(ctx.series("depreciation_amortization"))
    raw = {"cfo_ttm": cfo, "capex_ttm": capex, "fcf_ttm": fcf,
           "net_income_ttm": net_income, "ebit_ttm": ebit.value, "da_ttm": da}

    if ebit.value is None or da is None:
        return MetricResult(
            name="fcf_conversion", bucket=BUCKET, value=None, raw_inputs=raw,
            note="net income <= 0 and EBITDA unavailable (missing EBIT or D&A)",
        )

    ebitda = ebit.value + da
    raw["ebitda_ttm"] = ebitda
    if ebitda <= 0:
        return MetricResult(
            name="fcf_conversion", bucket=BUCKET, value=None, raw_inputs=raw,
            note="net income <= 0 and EBITDA <= 0 -- no meaningful "
                 "denominator; reported missing rather than sign-flipped",
        )

    subs = [s for s in ("fcf_ebitda_substituted", ebit.substitution) if s]
    return MetricResult(
        name="fcf_conversion", bucket=BUCKET, value=fcf / ebitda, raw_inputs=raw,
        substitution=",".join(subs),
        note="net income <= 0; used FCF/EBITDA instead of FCF/net income",
    )


def interest_coverage(ctx: TickerContext, cfg: Config) -> MetricResult:
    """EBIT / interest expense, TTM."""
    ebit = ebit_ttm(ctx)
    interest = ttm(ctx.series("interest_expense"))
    value = safe_divide(ebit.value, interest)
    return MetricResult(
        name="interest_coverage", bucket=BUCKET, value=value,
        raw_inputs={"ebit_ttm": ebit.value, "interest_expense_ttm": interest},
        substitution=ebit.substitution,
    )


METRICS = {
    "roic": roic,
    "roic_stability": roic_stability,
    "gross_margin_trend": gross_margin_trend,
    "fcf_conversion": fcf_conversion,
    "interest_coverage": interest_coverage,
}
