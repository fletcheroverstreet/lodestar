"""Accounting quality bucket — this bucket DEDUCTS.

Four metrics: accrual_ratio, cash_earnings_divergence, beneish_m_score,
dso_dio_trend. Every metric here is oriented so a HIGHER value means
CLEANER books (the project-wide "higher is better" convention applies
here too — see MetricResult's docstring). The asymmetric treatment (a
bad score subtracts at full weight, a good score earns only capped
credit) is applied to the BUCKET-level z-score in scoring/composite.py,
not to these individual metrics.
"""

from __future__ import annotations

from ..config import Config
from .base import MetricResult, TickerContext, ols_slope, safe_divide, ttm

BUCKET = "accounting_quality"


def _net_operating_assets(ctx: TickerContext, period_end: str | None) -> float | None:
    """NOA = (assets - cash) - (liabilities - total debt): operating
    assets minus operating liabilities, excluding the purely financial
    items (cash, debt) that aren't part of core operations."""
    def _at(concept: str) -> float | None:
        s = ctx.series(concept).dropna()
        if len(s) == 0:
            return None
        if period_end is not None:
            s = s[s.index <= period_end]
        return float(s.iloc[-1]) if len(s) else None

    assets = _at("assets")
    liabilities = _at("liabilities")
    cash = _at("cash")
    if assets is None or liabilities is None or cash is None:
        return None
    debt = (_at("debt_long") or 0.0) + (_at("debt_short") or 0.0)
    return (assets - cash) - (liabilities - debt)


def accrual_ratio(ctx: TickerContext, cfg: Config) -> MetricResult:
    """-(ΔNOA / average NOA) year-over-year (4 quarters apart). Negated
    because a HIGH raw accrual ratio (earnings growing faster than the
    operating asset base would suggest, i.e. built on accruals rather
    than cash) is a LOW-quality signal — flipping the sign keeps this
    metric on the project-wide "higher is better" convention.
    """
    if len(ctx.fundamentals.index) < 5:
        return MetricResult(name="accrual_ratio", bucket=BUCKET, value=None,
                             note="fewer than 5 quarters of balance-sheet history")

    end_period = ctx.fundamentals.index[-1]
    begin_period = ctx.fundamentals.index[-5]
    noa_now = _net_operating_assets(ctx, end_period)
    noa_then = _net_operating_assets(ctx, begin_period)
    if noa_now is None or noa_then is None:
        return MetricResult(name="accrual_ratio", bucket=BUCKET, value=None)

    avg_noa = (noa_now + noa_then) / 2.0
    if avg_noa == 0:
        return MetricResult(name="accrual_ratio", bucket=BUCKET, value=None,
                             note="average net operating assets is zero")

    raw_ratio = (noa_now - noa_then) / avg_noa
    return MetricResult(
        name="accrual_ratio", bucket=BUCKET, value=-raw_ratio,
        raw_inputs={"noa_now": noa_now, "noa_then": noa_then, "avg_noa": avg_noa,
                    "raw_accrual_ratio": raw_ratio},
    )


def cash_earnings_divergence(ctx: TickerContext, cfg: Config) -> MetricResult:
    """(TTM CFO - TTM net income) / TTM revenue. Already higher-is-better
    as defined: CFO exceeding reported net income (a positive gap) is a
    favourable quality signal (earnings are cash-backed, not just
    accounting constructs), so no sign flip is needed here, unlike
    accrual_ratio and beneish_m_score. The TREND in the gap (is it
    widening or narrowing) is reported in raw_inputs as supporting
    context, per the spec, but doesn't get its own weight slot — the
    level is the primary signal config.yaml weights.
    """
    cfo = ttm(ctx.series("cfo"))
    net_income = ttm(ctx.series("net_income"))
    revenue = ttm(ctx.series("revenue"))
    if cfo is None or net_income is None or revenue is None or revenue == 0:
        return MetricResult(name="cash_earnings_divergence", bucket=BUCKET, value=None,
                             raw_inputs={"cfo_ttm": cfo, "net_income_ttm": net_income,
                                         "revenue_ttm": revenue})

    gap_now = (cfo - net_income) / revenue

    # Trend: same gap computed 4 quarters earlier, if enough history exists.
    trend_note = None
    if len(ctx.fundamentals.index) >= 8:
        prior_period = ctx.fundamentals.index[-5]
        prior_ctx = TickerContext(
            ticker=ctx.ticker, as_of=prior_period, cik=ctx.cik,
            fundamentals=ctx.fundamentals[ctx.fundamentals.index <= prior_period],
            prices=ctx.prices, sector=ctx.sector, industry=ctx.industry,
        )
        prior_cfo = ttm(prior_ctx.series("cfo"))
        prior_ni = ttm(prior_ctx.series("net_income"))
        prior_rev = ttm(prior_ctx.series("revenue"))
        if prior_cfo is not None and prior_ni is not None and prior_rev:
            gap_then = (prior_cfo - prior_ni) / prior_rev
            trend_note = f"gap {gap_then:+.4f} -> {gap_now:+.4f} over the trailing year"

    return MetricResult(
        name="cash_earnings_divergence", bucket=BUCKET, value=gap_now,
        raw_inputs={"cfo_ttm": cfo, "net_income_ttm": net_income, "revenue_ttm": revenue},
        note=trend_note,
    )


def beneish_m_score(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Beneish M-score, computed on whatever of its eight inputs are
    available, TTM-now vs. TTM-one-year-ago (the quarterly analogue of
    the model's original annual design):

        DSRI - days-sales-in-receivables index   (have: AR, revenue)
        GMI  - gross margin index                (have: gross_profit, revenue)
        AQI  - asset quality index                NOT AVAILABLE (needs PP&E
                                                    and current assets;
                                                    finlake has neither
                                                    concept)
        SGI  - sales growth index                (have: revenue)
        DEPI - depreciation index                 NOT AVAILABLE (needs D&A;
                                                    FINLAKE-FINDINGS.md F14)
        SGAI - SG&A index                        (have: sganda, revenue)
        LVGI - leverage index                    (approximated: total
                                                    liabilities / total
                                                    assets, in place of the
                                                    textbook current-
                                                    liabilities + long-term-
                                                    debt version — flagged)
        TATA - total accruals to total assets    (approximated: (net income
                                                    - CFO) / total assets,
                                                    a common practical
                                                    substitute for the
                                                    textbook working-capital
                                                    version, which needs
                                                    current-asset/liability
                                                    detail finlake doesn't
                                                    expose)

    Never fabricates a missing component: the two unavailable inputs
    (AQI, DEPI) are dropped from the weighted sum entirely, not defaulted
    to a neutral value, and which components were actually used is always
    reported in raw_inputs. The final score is negated so a HIGHER metric
    value means a LOWER (safer) M-score, matching the project-wide sign
    convention.
    """
    if len(ctx.fundamentals.index) < 5:
        return MetricResult(name="beneish_m_score", bucket=BUCKET, value=None,
                             note="fewer than 5 quarters of history")

    end = ctx.fundamentals.index[-1]
    begin = ctx.fundamentals.index[-5]

    def _at(concept: str, period_end: str) -> float | None:
        s = ctx.series(concept).dropna()
        if len(s) == 0:
            return None
        s = s[s.index <= period_end]
        return float(s.iloc[-1]) if len(s) else None

    ar_now, ar_then = _at("accounts_receivable", end), _at("accounts_receivable", begin)
    rev_now, rev_then = _at("revenue", end), _at("revenue", begin)
    gp_now, gp_then = _at("gross_profit", end), _at("gross_profit", begin)
    sga_now, sga_then = _at("sganda", end), _at("sganda", begin)
    liab_now, liab_then = _at("liabilities", end), _at("liabilities", begin)
    assets_now, assets_then = _at("assets", end), _at("assets", begin)
    ni_now = ttm(ctx.series("net_income"))
    cfo_now = ttm(ctx.series("cfo"))

    components: dict[str, float] = {}

    if ar_now and ar_then and rev_now and rev_then:
        dsri = (ar_now / rev_now) / (ar_then / rev_then) if rev_now and rev_then else None
        if dsri is not None:
            components["DSRI"] = 0.920 * dsri

    if gp_now and rev_now and gp_then and rev_then:
        margin_now = gp_now / rev_now
        margin_then = gp_then / rev_then
        if margin_now:
            components["GMI"] = 0.528 * (margin_then / margin_now)

    if rev_now and rev_then:
        components["SGI"] = 0.892 * (rev_now / rev_then)

    if sga_now is not None and sga_then is not None and rev_now and rev_then:
        sgai_now = sga_now / rev_now
        sgai_then = sga_then / rev_then
        if sgai_then:
            components["SGAI"] = -0.172 * (sgai_now / sgai_then)

    if liab_now and assets_now and liab_then and assets_then:
        lvgi_now = liab_now / assets_now
        lvgi_then = liab_then / assets_then
        if lvgi_then:
            components["LVGI"] = -0.327 * (lvgi_now / lvgi_then)

    if ni_now is not None and cfo_now is not None and assets_now:
        tata = (ni_now - cfo_now) / assets_now
        components["TATA"] = 4.679 * tata

    if not components:
        return MetricResult(name="beneish_m_score", bucket=BUCKET, value=None,
                             note="none of the six computable Beneish inputs were available")

    # -4.84 is the model's own intercept, present regardless of which
    # components are available (it isn't a per-component term to drop).
    raw_score = -4.84 + sum(components.values())
    return MetricResult(
        name="beneish_m_score", bucket=BUCKET, value=-raw_score,
        raw_inputs={"components_used": sorted(components), "raw_m_score": raw_score,
                    **{f"weighted_{k}": v for k, v in components.items()}},
        substitution=f"partial_m_score_{len(components)}_of_6_computable_inputs",
        note="AQI and DEPI are never computable (finlake has no PP&E/"
             "current-assets/D&A concepts); LVGI and TATA use practical "
             "substitutes for their textbook definitions",
    )


def dso_dio_trend(ctx: TickerContext, cfg: Config) -> MetricResult:
    """-(OLS slope of DSO + DIO combined, over the trailing window).
    Negated because a RISING day-count (slower collections, slower-
    moving inventory) is the unfavourable direction.

        DSO = (AR / revenue) * 91.25   (quarterly analogue of *365/4)
        DIO = (inventory / cost_of_revenue) * 91.25
    """
    window = cfg.bucket("accounting_quality").param("dso_dio_window_quarters")
    ar = ctx.series("accounts_receivable")
    revenue = ctx.series("revenue")
    inventory = ctx.series("inventory")
    cogs = ctx.series("cost_of_revenue")

    dso = (ar / revenue * 91.25) if len(ar) and len(revenue) else None
    dio = (inventory / cogs * 91.25) if len(inventory) and len(cogs) else None

    parts = [s for s in (dso, dio) if s is not None]
    if not parts:
        return MetricResult(name="dso_dio_trend", bucket=BUCKET, value=None,
                             note="neither DSO nor DIO computable (missing AR/revenue "
                                  "or inventory/cost_of_revenue)")

    combined = parts[0].copy()
    for p in parts[1:]:
        combined = combined.add(p, fill_value=0.0)
    combined = combined.dropna().iloc[-window:]

    slope = ols_slope(combined)
    if slope is None:
        return MetricResult(name="dso_dio_trend", bucket=BUCKET, value=None,
                             note="fewer than 2 usable DSO/DIO points in the window")

    return MetricResult(
        name="dso_dio_trend", bucket=BUCKET, value=-slope,
        raw_inputs={"n_quarters": len(combined), "dso_dio_first": float(combined.iloc[0]),
                    "dso_dio_last": float(combined.iloc[-1]),
                    "has_dso": dso is not None, "has_dio": dio is not None},
    )


METRICS = {
    "accrual_ratio": accrual_ratio,
    "cash_earnings_divergence": cash_earnings_divergence,
    "beneish_m_score": beneish_m_score,
    "dso_dio_trend": dso_dio_trend,
}
