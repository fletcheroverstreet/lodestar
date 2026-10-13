"""Shared types and finance-math helpers used by every factor bucket.

Every metric function in metrics/*.py takes a TickerContext and a
BucketConfig and returns a MetricResult. Keeping that contract uniform is
what lets scoring/bucket.py treat all ~30 metrics identically (coverage,
weighting, winsorizing) without a special case per metric.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import BucketConfig


@dataclass(frozen=True)
class MetricResult:
    """One metric's output for one company on one run.

    PROJECT-WIDE CONVENTION: every metric's `value` is oriented so higher
    always means better standing on that factor, in every bucket, with no
    exceptions. This is the project spec's explicit instruction for Value
    ("invert everything so higher always means cheaper/better before
    z-scoring") applied uniformly everywhere, because scoring/zscore.py
    treats every metric identically — a single sign convention is what
    makes that possible without a per-metric special case. For Value,
    that means feeding an earnings/sales YIELD (EBIT/EV, not EV/EBIT)
    into the metric, not a raw multiple. For Accounting quality, that
    means inverting accrual ratio and Beneish M-score so a HIGHER value
    means CLEANER books — the bucket's asymmetric penalty (full weight
    for bad, capped credit for good) is then applied to the bucket-level
    z-score in scoring/composite.py, not to individual metric sign.

    `value` is None when the metric could not be computed at all — a
    missing metric is DROPPED by the scoring layer, never imputed as
    zero (see the project spec, scoring mechanics step 5).

    `raw_inputs` holds every intermediate number that fed the final
    value, keyed by name, so the audit command can print a full
    computation chain back to the underlying filing.

    `substitution` is set when a fallback calculation was used instead of
    the primary one (e.g. EV/Sales instead of EV/EBIT, or a capped tax
    rate) — always reported, never silent.
    """

    name: str
    bucket: str
    value: float | None
    raw_inputs: dict[str, Any] = field(default_factory=dict)
    substitution: str | None = None
    note: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class TickerContext:
    """Everything a metric function needs for one ticker on one run,
    already resolved to point-in-time-correct data as of `as_of`."""

    ticker: str
    as_of: str
    cik: int | None
    fundamentals: pd.DataFrame  # quarterly, index = period_end (str, ascending), with __derived/__filed columns
    prices: pd.DataFrame  # daily bars with adj_ columns, up to and including as_of
    sector: str
    industry: str
    # Composite percentile rank (0-100) from each of the last few PRIOR
    # runs, oldest first, most recent last. Populated by the scoring
    # orchestrator from persistence/runs.py — empty by default, and empty
    # for the first run or two ever, since there's nothing to compare
    # against yet. Used only by momentum.rank_change(). See DEC-006 in
    # lodestar's vault: this compares the two most recent COMPLETED
    # prior runs to each other, never the run currently being computed
    # against itself, which would be circular (a composite score can't
    # depend on its own rank change before it exists).
    prior_rank_history: tuple[float, ...] = ()

    def series(self, concept: str) -> pd.Series:
        """The quarterly series for one concept, or an empty Series if
        finlake never had data for it. Always returns a Series (never
        raises), so metric code can call .dropna(), TTM helpers, etc.
        uniformly whether or not the concept is covered."""
        if concept not in self.fundamentals.columns:
            return pd.Series(dtype=float)
        return self.fundamentals[concept]

    def latest(self, concept: str) -> float | None:
        """Most recent non-null value for a concept, or None."""
        s = self.series(concept).dropna()
        return float(s.iloc[-1]) if len(s) else None

    def latest_period_end(self, concept: str) -> str | None:
        s = self.series(concept).dropna()
        return str(s.index[-1]) if len(s) else None

    def price_as_of(self, date: str) -> float | None:
        """Closing (split-adjusted) price on the last trading day at or
        before `date`. None if there's no cached price data at all, or
        none on/before that date."""
        if self.prices.empty:
            return None
        window = self.prices[self.prices["date"] <= date]
        if window.empty:
            return None
        col = "adj_close" if "adj_close" in window.columns else "close"
        return float(window.sort_values("date")[col].iloc[-1])

    def vwap(self, start: str, end: str, *, price: str = "typical") -> float | None:
        """Dollar-weighted average price over [start, end], computed
        locally from `self.prices` — deliberately NOT a second call out
        to finlake.vwap(): this context's prices frame is already fetched
        and already point-in-time correct, so recomputing the same
        formula here (identical to finlake.sources.prices.vwap) avoids a
        redundant fetch and, just as importantly, lets a metric using
        this be unit-tested with a synthetic `prices` frame instead of
        needing a real finlake cache. Returns None with no bars in the
        window.
        """
        if self.prices.empty:
            return None
        window = self.prices[(self.prices["date"] >= start) & (self.prices["date"] <= end)]
        if window.empty:
            return None

        prefix = "adj_" if "adj_close" in window.columns else ""
        if price == "typical":
            p = (window[f"{prefix}high"] + window[f"{prefix}low"] + window[f"{prefix}close"]) / 3.0
        elif price == "close":
            p = window[f"{prefix}close"]
        else:
            raise ValueError(f"unknown price basis {price!r}; use 'typical' or 'close'")

        vol = window[f"{prefix}volume"]
        total_vol = float(vol.sum())
        if total_vol <= 0:
            return None
        return float((p * vol).sum() / total_vol)


# ---------------------------------------------------------------------------
# Finance-math helpers. Every one of these is deliberately simple and
# explicit rather than a clever one-liner, because the whole point of this
# project is being able to audit any number by hand.
# ---------------------------------------------------------------------------

def ttm(series: pd.Series, *, quarters: int = 4) -> float | None:
    """Trailing-N-quarter (default: twelve-month) sum of the MOST RECENT
    `quarters` values in the series, requiring all of them to be present.

    A partial sum (e.g. 3 quarters treated as if it were 4) would
    understate the true trailing total and silently corrupt every ratio
    built on it — so this returns None rather than guessing, exactly
    like finlake's own quarterize.py refuses to fabricate a missing
    quarter.
    """
    clean = series.dropna()
    if len(clean) < quarters:
        return None
    return float(clean.iloc[-quarters:].sum())


def ttm_series(series: pd.Series, *, quarters: int = 4, max_gap_days: int = 130) -> pd.Series:
    """A rolling trailing-N-quarter sum, one value per quarter-end where
    it's computable — used for trend/stability metrics that need a
    HISTORY of TTM values (ROIC stability, gross margin trend, buyback
    windows), not just the latest one.

    Drops NaN FIRST, then windows over the cleaned series. This matters:
    `series` typically comes from TickerContext.series(concept), which
    reads a column out of finlake.fundamentals()'s DataFrame — and that
    frame's index is the UNION of period-ends across every concept
    requested, not just this one. A concept that's missing at a
    particular quarter (because THAT concept wasn't reported then, even
    though some OTHER concept was) shows up as a NaN row that has
    nothing to do with this concept's own reporting cadence. Windowing
    over the raw (non-dropna'd) series would let those unrelated-concept
    artifacts poison nearly every window's NaN check — which is exactly
    what happened before this was fixed: real, fully-reported revenue
    history for CVX collapsed from 40 usable quarters to 9 TTM points,
    because ~10 OTHER concepts' gaps were injecting NaN rows into
    revenue's positional windows.

    Two safeguards remain, now applied to the CLEANED series' own dates:
      1. A NaN inside a window (still possible if a concept genuinely
         has a hole in its own history) means "skip it", not "sum 3".
      2. Only windows where every consecutive pair of period-ends is at
         most `max_gap_days` apart are used — this catches a quarter
         that's missing from THIS concept's own history entirely (not
         just NaN within it), which a plain rolling-window sum can't see.
    `max_gap_days=130` gives headroom above finlake's own widest single-
    quarter classification (120 days, for 17-week fiscal quarters) plus
    slack, while staying well below the ~180+ day gap a genuinely
    skipped quarter would produce.
    """
    clean = series.dropna()
    if len(clean) < quarters:
        return pd.Series(dtype=float)

    out_values: list[float] = []
    out_index: list[str] = []
    for end in range(quarters - 1, len(clean)):
        window = clean.iloc[end - quarters + 1: end + 1]
        dates = [dt.date.fromisoformat(str(d)) for d in window.index]
        consecutive_ok = all(
            (dates[i + 1] - dates[i]).days <= max_gap_days
            for i in range(len(dates) - 1)
        )
        if not consecutive_ok:
            continue
        out_values.append(float(window.sum()))
        out_index.append(str(window.index[-1]))

    return pd.Series(out_values, index=out_index, dtype=float)


def cagr(begin: float | None, end: float | None, years: float) -> float | None:
    """Compound annual growth rate from `begin` to `end` over `years`.

    Undefined (returns None) when `begin` <= 0 — you cannot take a real
    root of a negative or zero base. ALSO undefined when `end` < 0: with
    a positive base, a negative ending value makes end/begin negative,
    and raising a negative number to a fractional power (1/years) has no
    real-valued result (Python silently returns a complex number instead
    of raising, which is worse — this guard turns that into an honest
    None). `end == 0` is fine and means exactly -100%.

    Callers that need a growth figure even when the base is non-positive
    (e.g. FCF CAGR, per the project spec) must use a different formula
    for that case and flag it; this function deliberately does not paper
    over either undefined case.
    """
    if begin is None or end is None or begin <= 0 or end < 0 or years <= 0:
        return None
    return float((end / begin) ** (1.0 / years) - 1.0)


def ols_slope(y: pd.Series) -> float | None:
    """Ordinary-least-squares slope of `y` against its own position index
    (0, 1, 2, ...) — "how much does this move, per period, on average".
    Used for the gross-margin-trend metric (slope in margin-points per
    quarter). Returns None with fewer than 2 points."""
    clean = y.dropna()
    if len(clean) < 2:
        return None
    x = np.arange(len(clean), dtype=float)
    y_vals = clean.to_numpy(dtype=float)
    # Textbook OLS slope: cov(x, y) / var(x). Written out explicitly
    # (not np.polyfit) so every step is auditable by hand.
    x_mean, y_mean = x.mean(), y_vals.mean()
    numerator = float(((x - x_mean) * (y_vals - y_mean)).sum())
    denominator = float(((x - x_mean) ** 2).sum())
    if denominator == 0:
        return None
    return numerator / denominator


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    """Division that returns None (never raises, never returns inf/nan)
    when either input is missing or the denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def market_cap(shares_outstanding: float | None, price: float | None) -> float | None:
    if shares_outstanding is None or price is None:
        return None
    return shares_outstanding * price
