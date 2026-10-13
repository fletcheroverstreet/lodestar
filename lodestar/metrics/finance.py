"""Composite financial building blocks shared across multiple buckets.

EBIT, NOPAT, invested capital, and enterprise value are each used by at
least two of the seven factor buckets. Computing them once here means
Quality's ROIC and Value's EV/EBIT can never quietly drift into using two
different definitions of the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Config
from .base import TickerContext, market_cap, ttm


@dataclass(frozen=True)
class ComponentResult:
    """A named intermediate financial figure, how it was computed, and
    what fed it — the same shape as MetricResult but for a building block
    used INSIDE a metric, not a metric itself."""

    value: float | None
    substitution: str | None
    raw_inputs: dict[str, Any]


def is_financials_sector(ctx: TickerContext) -> bool:
    """True for banks, brokers, and insurers — see DEC-002 in lodestar's
    vault (ROIC/invested-capital is gated off for financials, the same way
    the project spec already gates P/B ON for financials only).

    Compares the CANONICAL sector, not the raw label. Lowercasing alone was
    not enough: the SIC fallback prefixes its labels with "SIC: " so a reader
    can see the grouping is coarse, and "SIC: Financials".lower() is
    "sic: financials", which does not equal "financials". The gate silently
    stopped firing for every name that arrived through the fallback rather
    than the curated map.

    That was live and expensive. 454 of 503 names were on the fallback, so 87
    banks and insurers had the INDUSTRIAL ROIC formula applied to a
    deposit-funded balance sheet: MetLife scored 844% ROIC, Northern Trust
    91%, Citigroup 34%. All of them then fed the Quality bucket's peer
    z-scores, so the damage was not confined to the names themselves.
    """
    from ..industry_map import canonical_sector

    return canonical_sector(ctx.sector) == "financials"


def ebit_ttm(ctx: TickerContext) -> ComponentResult:
    """TTM EBIT.

    Prefers TTM(operating_income). When a filer has no discrete
    operating-income line — see DEC-005 in finlake's vault, which
    removed a fallback that silently substituted pretax income here —
    falls back to TTM(pretax_income) + TTM(interest_expense). That sum
    is algebraically the same quantity operating income would be for a
    company with no other non-operating items, so it's a reasonable
    named proxy — but it IS a proxy, so it's flagged rather than
    returned indistinguishably from a real reported operating-income
    figure.
    """
    op = ttm(ctx.series("operating_income"))
    if op is not None:
        return ComponentResult(value=op, substitution=None,
                                raw_inputs={"operating_income_ttm": op})

    pretax = ttm(ctx.series("pretax_income"))
    interest = ttm(ctx.series("interest_expense"))
    if pretax is not None and interest is not None:
        return ComponentResult(
            value=pretax + interest,
            substitution="ebit_proxy_pretax_plus_interest",
            raw_inputs={"pretax_income_ttm": pretax, "interest_expense_ttm": interest},
        )
    return ComponentResult(value=None, substitution=None, raw_inputs={})


def effective_tax_rate(ctx: TickerContext, cfg: Config) -> ComponentResult:
    """TTM tax_expense / TTM pretax_income, bounded to
    [tax_rate_min, tax_rate_max]. See DEC-001 in lodestar's vault: this
    ratio is unstable near zero pretax income (a small tax charge
    against near-zero pretax income can produce a rate of 300% or
    -150%, neither a real effective tax rate). Falls back to
    default_tax_rate, flagged, whenever pretax income is <= 0 or the
    computed rate falls outside the configured bounds.
    """
    params = cfg.bucket("quality").params
    lo, hi, default = (
        params["tax_rate_min"], params["tax_rate_max"], params["default_tax_rate"]
    )

    pretax = ttm(ctx.series("pretax_income"))
    tax = ttm(ctx.series("tax_expense"))
    raw: dict[str, Any] = {"pretax_income_ttm": pretax, "tax_expense_ttm": tax}

    if pretax is None or tax is None or pretax <= 0:
        return ComponentResult(
            value=default, substitution="default_tax_rate_no_pretax_income", raw_inputs=raw
        )

    rate = tax / pretax
    if rate < lo or rate > hi:
        raw["computed_rate_before_bounding"] = rate
        return ComponentResult(
            value=default, substitution="default_tax_rate_out_of_bounds", raw_inputs=raw
        )
    return ComponentResult(value=rate, substitution=None, raw_inputs=raw)


def nopat_ttm(ctx: TickerContext, cfg: Config) -> ComponentResult:
    """NOPAT = EBIT * (1 - effective tax rate), TTM."""
    ebit = ebit_ttm(ctx)
    if ebit.value is None:
        return ComponentResult(value=None, substitution=ebit.substitution,
                                raw_inputs=ebit.raw_inputs)

    tax = effective_tax_rate(ctx, cfg)
    nopat = ebit.value * (1.0 - tax.value)
    subs = [s for s in (ebit.substitution, tax.substitution) if s]
    return ComponentResult(
        value=nopat,
        substitution=",".join(subs) if subs else None,
        raw_inputs={**ebit.raw_inputs, **tax.raw_inputs, "effective_tax_rate": tax.value},
    )


def invested_capital(ctx: TickerContext, *, period_end: str | None = None) -> ComponentResult:
    """Total debt + total equity - cash and short-term investments.

    A level, not a TTM figure — differencing a balance sheet turns a
    level into a change, which finlake's own quarterize.py already
    refuses to do for exactly this reason. Pass `period_end` to get the
    invested capital AS OF a specific historical quarter (needed for a
    ROIC time series); omit it for the latest available figure.

    Missing debt components (debt_long / debt_short individually) are
    treated as zero — common in practice for a company with no
    short-term borrowings — and flagged when that happened, rather than
    either failing the whole calculation or silently pretending the
    value is known. Equity and cash are required; without them there is
    no invested-capital figure at all.
    """
    def _value_at(concept: str) -> float | None:
        s = ctx.series(concept).dropna()
        if len(s) == 0:
            return None  # nothing to filter; also avoids comparing an
            # empty int64 RangeIndex (series() default when a concept is
            # entirely absent) against a string period_end below.
        if period_end is not None:
            s = s[s.index <= period_end]
        return float(s.iloc[-1]) if len(s) else None

    equity = _value_at("equity")
    cash = _value_at("cash")
    if equity is None or cash is None:
        return ComponentResult(
            value=None, substitution=None,
            raw_inputs={"equity": equity, "cash": cash, "period_end": period_end},
        )

    debt_long = _value_at("debt_long")
    debt_short = _value_at("debt_short")
    sti = _value_at("short_term_investments") or 0.0
    debt_defaulted = debt_long is None or debt_short is None
    total_debt = (debt_long or 0.0) + (debt_short or 0.0)

    value = total_debt + equity - cash - sti
    return ComponentResult(
        value=value,
        substitution="debt_component_defaulted_zero" if debt_defaulted else None,
        raw_inputs={
            "debt_long": debt_long, "debt_short": debt_short, "equity": equity,
            "cash": cash, "short_term_investments": sti, "period_end": period_end,
        },
    )


def enterprise_value(ctx: TickerContext) -> ComponentResult:
    """Market cap + total debt - cash and short-term investments, as of
    ctx.as_of. Market cap uses the latest shares_outstanding on/before
    as_of and the closing price on/before as_of — two independently
    time-stamped facts, which is why they're each fetched "as of" rather
    than assumed to already line up."""
    shares = ctx.latest("shares_outstanding")
    price = ctx.price_as_of(ctx.as_of)
    mcap = market_cap(shares, price)

    if mcap is None:
        return ComponentResult(
            value=None, substitution=None,
            raw_inputs={"shares_outstanding": shares, "price": price},
        )

    cash = ctx.latest("cash")
    if cash is None:
        return ComponentResult(
            value=None, substitution=None,
            raw_inputs={"shares_outstanding": shares, "price": price,
                        "market_cap": mcap, "cash": cash},
        )

    debt_long = ctx.latest("debt_long")
    debt_short = ctx.latest("debt_short")
    sti = ctx.latest("short_term_investments") or 0.0
    debt_defaulted = debt_long is None or debt_short is None
    total_debt = (debt_long or 0.0) + (debt_short or 0.0)

    value = mcap + total_debt - cash - sti
    return ComponentResult(
        value=value,
        substitution="debt_component_defaulted_zero" if debt_defaulted else None,
        raw_inputs={
            "shares_outstanding": shares, "price": price, "market_cap": mcap,
            "debt_long": debt_long, "debt_short": debt_short, "cash": cash,
            "short_term_investments": sti,
        },
    )
