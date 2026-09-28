"""Growth bucket: revenue growth, FCF growth, incremental profitability,
and how much of profit gets reinvested.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import Config
from .base import MetricResult, TickerContext, cagr, safe_divide, ttm, ttm_series
from .finance import ebit_ttm, nopat_ttm

BUCKET = "growth"

# A quarterly filing more than this many days after its own period_end is
# unusual enough to suggest a restatement (a normal 10-Q/10-K is filed
# 30-90 days out; even a late filer rarely exceeds a year). Used to flag
# -- not exclude -- a CAGR endpoint built on a quarter like that. See
# DEC-004 in lodestar's vault (restated-quarter detection before a CAGR).
RESTATEMENT_FILED_LAG_DAYS = 400


def _restated_endpoint_flag(ctx: TickerContext, concept: str, period_end: str | None) -> str | None:
    """None if the quarter at `period_end` looks like a normal filing;
    otherwise a short string naming the concept and period for the flag."""
    if period_end is None:
        return None
    filed_col = f"{concept}__filed"
    if filed_col not in ctx.fundamentals.columns or period_end not in ctx.fundamentals.index:
        return None
    filed = ctx.fundamentals.loc[period_end, filed_col]
    if filed is None or pd.isna(filed):
        return None
    lag_days = (dt.date.fromisoformat(str(filed)) - dt.date.fromisoformat(period_end)).days
    if lag_days > RESTATEMENT_FILED_LAG_DAYS:
        return f"{concept}@{period_end}_filed_{lag_days}d_late"
    return None


def _ttm_n_quarters_ago(ctx: TickerContext, concept: str, quarters_ago: int) -> tuple[float | None, str | None]:
    """The TTM value of `concept` ending `quarters_ago` quarters before the
    latest available one, plus the period_end it ended on (for the
    restatement check and audit trail)."""
    series = ttm_series(ctx.series(concept))
    if len(series) <= quarters_ago:
        return None, None
    row = series.iloc[-(quarters_ago + 1)]
    period_end = str(series.index[-(quarters_ago + 1)])
    return float(row), period_end


def revenue_cagr_3y(ctx: TickerContext, cfg: Config) -> MetricResult:
    return _revenue_cagr(ctx, years=3, quarters_ago=12, name="revenue_cagr_3y")


def revenue_cagr_5y(ctx: TickerContext, cfg: Config) -> MetricResult:
    return _revenue_cagr(ctx, years=5, quarters_ago=20, name="revenue_cagr_5y")


def _revenue_cagr(ctx: TickerContext, *, years: int, quarters_ago: int, name: str) -> MetricResult:
    """CAGR on TTM revenue endpoints, `years` apart."""
    end_series = ttm_series(ctx.series("revenue"))
    if len(end_series) == 0:
        return MetricResult(name=name, bucket=BUCKET, value=None)

    end_value = float(end_series.iloc[-1])
    end_period = str(end_series.index[-1])
    begin_value, begin_period = _ttm_n_quarters_ago(ctx, "revenue", quarters_ago)

    result = cagr(begin_value, end_value, years)
    flags = [
        f for f in (
            _restated_endpoint_flag(ctx, "revenue", begin_period),
            _restated_endpoint_flag(ctx, "revenue", end_period),
        ) if f
    ]
    return MetricResult(
        name=name, bucket=BUCKET, value=result,
        raw_inputs={"revenue_ttm_begin": begin_value, "revenue_ttm_end": end_value,
                    "begin_period": begin_period, "end_period": end_period},
        substitution="possible_restatement_in_window" if flags else None,
        note="; ".join(flags) if flags else None,
    )


def fcf_cagr(ctx: TickerContext, cfg: Config) -> MetricResult:
    """CAGR on TTM FCF (CFO - capex), 3 years apart. Undefined when the
    base is <= 0 (a negative or zero base has no real CAGR) — the spec's
    fallback in that case is the change in FCF scaled by average revenue
    over the window, flagged."""
    cfo_series = ttm_series(ctx.series("cfo"))
    capex_series = ttm_series(ctx.series("capex"))
    common = cfo_series.index.intersection(capex_series.index)
    if len(common) == 0:
        return MetricResult(name="fcf_cagr", bucket=BUCKET, value=None)

    fcf_series = (cfo_series.loc[common] - capex_series.loc[common]).sort_index()
    if len(fcf_series) <= 12:
        return MetricResult(name="fcf_cagr", bucket=BUCKET, value=None,
                             note="fewer than 3 years of TTM FCF history")

    end_value = float(fcf_series.iloc[-1])
    begin_value = float(fcf_series.iloc[-13])

    if begin_value > 0:
        return MetricResult(
            name="fcf_cagr", bucket=BUCKET, value=cagr(begin_value, end_value, 3),
            raw_inputs={"fcf_ttm_begin": begin_value, "fcf_ttm_end": end_value},
        )

    revenue_series = ttm_series(ctx.series("revenue"))
    common_rev = revenue_series.index.intersection(fcf_series.index[-13:])
    avg_revenue = float(revenue_series.loc[common_rev].mean()) if len(common_rev) else None
    if not avg_revenue:
        return MetricResult(
            name="fcf_cagr", bucket=BUCKET, value=None,
            raw_inputs={"fcf_ttm_begin": begin_value, "fcf_ttm_end": end_value},
            note="FCF base <= 0 and no revenue available to scale the fallback",
        )

    return MetricResult(
        name="fcf_cagr", bucket=BUCKET, value=(end_value - begin_value) / avg_revenue,
        raw_inputs={"fcf_ttm_begin": begin_value, "fcf_ttm_end": end_value,
                    "avg_revenue_ttm": avg_revenue},
        substitution="fcf_cagr_scaled_by_revenue_fallback",
        note="FCF base <= 0; used (ΔFCF / average revenue) instead of a true CAGR",
    )


def incremental_margin(ctx: TickerContext, cfg: Config) -> MetricResult:
    """ΔTTM EBIT / ΔTTM revenue over the configured trailing window
    (default 8 quarters) — how much of each new dollar of revenue drops
    to operating profit."""
    window = cfg.bucket("growth").param("incremental_margin_window_quarters")
    ebit_series = ttm_series(ctx.series("operating_income"))
    revenue_series = ttm_series(ctx.series("revenue"))
    common = ebit_series.index.intersection(revenue_series.index)
    if len(common) <= window:
        return MetricResult(name="incremental_margin", bucket=BUCKET, value=None,
                             note=f"fewer than {window + 1} common TTM EBIT/revenue points")

    ordered = sorted(common)
    end, begin = ordered[-1], ordered[-(window + 1)]
    d_ebit = float(ebit_series.loc[end] - ebit_series.loc[begin])
    d_revenue = float(revenue_series.loc[end] - revenue_series.loc[begin])
    value = safe_divide(d_ebit, d_revenue)
    return MetricResult(
        name="incremental_margin", bucket=BUCKET, value=value,
        raw_inputs={"ebit_ttm_begin": float(ebit_series.loc[begin]),
                    "ebit_ttm_end": float(ebit_series.loc[end]),
                    "revenue_ttm_begin": float(revenue_series.loc[begin]),
                    "revenue_ttm_end": float(revenue_series.loc[end]),
                    "begin_period": begin, "end_period": end},
    )


def reinvestment_rate(ctx: TickerContext, cfg: Config) -> MetricResult:
    """(capex - D&A + ΔNWC) / NOPAT, guarded when NOPAT <= 0.

    Currently unavailable against real data: D&A has no finlake concept
    yet (FINLAKE-FINDINGS.md F14). Reported honestly as missing rather
    than computed with D&A silently omitted, which would change what the
    ratio actually measures, not just its precision.
    """
    if "depreciation_amortization" not in ctx.fundamentals.columns:
        return MetricResult(
            name="reinvestment_rate", bucket=BUCKET, value=None,
            substitution="blocked_missing_da_concept",
            note="needs a D&A concept finlake doesn't expose yet (FINLAKE-FINDINGS.md F14)",
        )

    capex = ttm(ctx.series("capex"))
    da = ttm(ctx.series("depreciation_amortization"))
    nopat = nopat_ttm(ctx, cfg)

    ar = ctx.latest("accounts_receivable") or 0.0
    inv = ctx.latest("inventory") or 0.0
    ap = ctx.latest("accounts_payable") or 0.0
    nwc_now = ar + inv - ap

    if capex is None or da is None or nopat.value is None or nopat.value <= 0:
        return MetricResult(
            name="reinvestment_rate", bucket=BUCKET, value=None,
            raw_inputs={"capex_ttm": capex, "da_ttm": da, "nopat_ttm": nopat.value},
            note="guarded: NOPAT <= 0 or a required input missing",
        )

    # ΔNWC over the same trailing four quarters as the TTM figures above.
    ar_series = ctx.series("accounts_receivable").dropna()
    inv_series = ctx.series("inventory").dropna()
    ap_series = ctx.series("accounts_payable").dropna()
    if len(ar_series) < 5 or len(ap_series) < 5:
        d_nwc = 0.0  # not enough history to compute a change; treat as flat
    else:
        nwc_series = (ar_series.reindex(ar_series.index, fill_value=0.0)
                      + inv_series.reindex(ar_series.index, fill_value=0.0)
                      - ap_series.reindex(ar_series.index, fill_value=0.0))
        d_nwc = float(nwc_series.iloc[-1] - nwc_series.iloc[-5])

    value = (capex - da + d_nwc) / nopat.value
    return MetricResult(
        name="reinvestment_rate", bucket=BUCKET, value=value,
        raw_inputs={"capex_ttm": capex, "da_ttm": da, "delta_nwc": d_nwc,
                    "nopat_ttm": nopat.value, "nwc_now": nwc_now},
    )


METRICS = {
    "revenue_cagr_3y": revenue_cagr_3y,
    "revenue_cagr_5y": revenue_cagr_5y,
    "fcf_cagr": fcf_cagr,
    "incremental_margin": incremental_margin,
    "reinvestment_rate": reinvestment_rate,
}
