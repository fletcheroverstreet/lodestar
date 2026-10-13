"""Value bucket: is the stock cheap relative to its own economics.

Every metric here is a YIELD (earnings/EV, FCF/EV, book/market), not a
multiple — a yield is naturally "higher is better", which is the
project-wide sign convention (see MetricResult's docstring). A multiple
like EV/EBIT would need inverting after the fact; computing the yield
directly avoids that step and the sign-flip bugs it invites.
"""

from __future__ import annotations

from ..config import Config
from .base import MetricResult, TickerContext, safe_divide, ttm
from .finance import ebit_ttm, enterprise_value, is_financials_sector

BUCKET = "value"


def ev_ebit(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Earnings yield = TTM EBIT / EV — the inverted, higher-is-better
    form of EV/EBIT.

    Substitutes a sales yield (TTM revenue / EV) when TTM EBIT <= 0, or
    when the EBIT margin (EBIT/revenue) falls below `ebit_margin_floor`
    — EV/EBIT gets noisy and can even flip sign near-zero EBIT, which
    would make a barely-profitable company look like the cheapest name
    in the peer group. Reported as ONE metric slot with a substitution
    flag (see DEC-003 in lodestar's vault), not two separately-weighted
    metrics — a name never gets credit for both.
    """
    ev = enterprise_value(ctx)
    ebit = ebit_ttm(ctx)
    revenue = ttm(ctx.series("revenue"))
    floor = cfg.bucket("value").param("ebit_margin_floor")

    if ev.value is None or ev.value == 0:
        return MetricResult(name="ev_ebit", bucket=BUCKET, value=None,
                             raw_inputs={"ev": ev.value})

    margin = safe_divide(ebit.value, revenue)
    use_ebit = (
        ebit.value is not None and ebit.value > 0
        and margin is not None and margin >= floor
    )

    if use_ebit:
        return MetricResult(
            name="ev_ebit", bucket=BUCKET, value=ebit.value / ev.value,
            raw_inputs={"ebit_ttm": ebit.value, "ev": ev.value, "ebit_margin": margin},
            substitution=ev.substitution,
        )

    if revenue is None or revenue <= 0:
        return MetricResult(
            name="ev_ebit", bucket=BUCKET, value=None,
            raw_inputs={"ebit_ttm": ebit.value, "revenue_ttm": revenue, "ev": ev.value,
                        "ebit_margin": margin},
            note="EBIT unusable and revenue also unavailable/non-positive",
        )

    subs = [s for s in ("ev_sales_substituted", ev.substitution) if s]
    return MetricResult(
        name="ev_ebit", bucket=BUCKET, value=revenue / ev.value,
        raw_inputs={"ebit_ttm": ebit.value, "revenue_ttm": revenue, "ev": ev.value,
                    "ebit_margin": margin},
        substitution=",".join(subs),
        note="EBIT <= 0 or below the margin floor; substituted revenue/EV",
    )


def fcf_yield(ctx: TickerContext, cfg: Config) -> MetricResult:
    """TTM FCF / EV. FCF = CFO - capex."""
    ev = enterprise_value(ctx)
    cfo = ttm(ctx.series("cfo"))
    capex = ttm(ctx.series("capex"))
    if ev.value is None or ev.value == 0 or cfo is None or capex is None:
        return MetricResult(name="fcf_yield", bucket=BUCKET, value=None,
                             raw_inputs={"cfo_ttm": cfo, "capex_ttm": capex, "ev": ev.value})

    fcf = cfo - capex
    return MetricResult(
        name="fcf_yield", bucket=BUCKET, value=fcf / ev.value,
        raw_inputs={"cfo_ttm": cfo, "capex_ttm": capex, "fcf_ttm": fcf, "ev": ev.value},
        substitution=ev.substitution,
    )


def pb(ctx: TickerContext, cfg: Config) -> MetricResult:
    """Book/market = total equity / market cap — the inverted,
    higher-is-better form of P/B.

    Gated to the financials sector only, per the project spec: "P/B,
    gated to financials only. It's noise everywhere else." A bank's book
    value is close to its economic capital base (loans and securities
    marked at or near fair value); a semiconductor company's book value
    is mostly historical-cost PP&E and doesn't mean the same thing.
    """
    if not is_financials_sector(ctx):
        return MetricResult(name="pb", bucket=BUCKET, value=None,
                             substitution="not_applicable_non_financials",
                             note="P/B only computed for the financials sector")

    equity = ctx.latest("equity")
    shares = ctx.latest("shares_outstanding")
    price = ctx.price_as_of(ctx.as_of)
    if equity is None or shares is None or price is None:
        return MetricResult(name="pb", bucket=BUCKET, value=None,
                             raw_inputs={"equity": equity, "shares_outstanding": shares,
                                         "price": price})

    market_cap_val = shares * price
    if market_cap_val == 0:
        return MetricResult(name="pb", bucket=BUCKET, value=None,
                             raw_inputs={"equity": equity, "market_cap": market_cap_val})

    return MetricResult(
        name="pb", bucket=BUCKET, value=equity / market_cap_val,
        raw_inputs={"equity": equity, "shares_outstanding": shares, "price": price,
                    "market_cap": market_cap_val},
    )


METRICS = {
    "ev_ebit": ev_ebit,
    "fcf_yield": fcf_yield,
    "pb": pb,
}
