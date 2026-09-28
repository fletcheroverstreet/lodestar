"""Winsorization: clip a metric's values at the 1st/99th percentile
(configurable) WITHIN a peer group, before any standardization. One
broken filing must not dominate a sector.
"""

from __future__ import annotations

import pandas as pd


def winsorize(values: pd.Series, *, low_pct: float, high_pct: float) -> pd.Series:
    """Clip `values` to [low_pct, high_pct] quantiles of itself.

    NaN entries pass through unchanged (they're handled as missing data
    by the coverage layer, not touched here). With fewer than 2 non-null
    values, there's nothing meaningful to clip against, so the values are
    returned unchanged — this is also what keeps winsorizing well-defined
    at the "peer group of one" edge case the project spec calls out.
    """
    clean = values.dropna()
    if len(clean) < 2:
        return values.copy()

    lo = clean.quantile(low_pct)
    hi = clean.quantile(high_pct)
    return values.clip(lower=lo, upper=hi)
