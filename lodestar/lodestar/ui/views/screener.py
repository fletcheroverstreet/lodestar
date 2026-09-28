"""The screener — rank and filter the whole universe.

Filters live in one row above the table and are all dropdowns or sliders, so
the whole control surface is visible at once rather than hidden behind a
settings panel. Nothing here recomputes a score: filtering narrows a
completed run.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...config import BUCKET_NAMES
from .. import data as D
from ..theme import chip, esc, html_table, is_missing, score_bar, signed, stat_strip

# Market-cap bands, in dollars. Named rather than numeric because "mid cap"
# is the unit a reader actually thinks in.
CAP_BANDS = {
    "Any size": (0, float("inf")),
    "Mega (> $200B)": (200e9, float("inf")),
    "Large ($10B – $200B)": (10e9, 200e9),
    "Mid ($2B – $10B)": (2e9, 10e9),
    "Small (< $2B)": (0, 2e9),
}

RATING_ORDER = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL",
                "INSUFFICIENT DATA"]


def _valuations(db_path: str, run_id: str, *,
                is_current: bool = True) -> pd.DataFrame:
    """The run's ratio snapshot, re-priced to the latest close.

    READ, not computed. Deriving these live called finlake's ratio engine
    once per ticker at ~150ms — 77 seconds to draw a 503-name table on every
    cold load — and broke the rule that the UI never computes. It also risked
    showing a P/E from a different moment than the score beside it.

    What the snapshot could NOT do is stay current. Every price-based figure
    in it was computed at the close on each name's last fiscal quarter end, so
    market caps were stale by up to a quarter and by a different amount per
    company — enough to sort names into the wrong market-cap band and to make
    the P/E ceiling filter reject the wrong names. `D.reprice` rescales those
    columns by the ratio between the snapshot price and the latest close,
    which is exact arithmetic on stored numbers rather than a recomputation.

    An older run has no snapshot; the screener still works, just without the
    valuation columns, and says so rather than hanging while it backfills.
    """
    snapshot = D.ratios_snapshot(db_path, run_id)
    if snapshot.empty or not is_current:
        # A historical run keeps its own prices. Re-pricing one at today's
        # close is the lookahead the point-in-time design exists to prevent.
        return snapshot
    quotes = D.quotes(tuple(sorted(snapshot["ticker"].dropna().astype(str))))
    return D.reprice(snapshot, quotes)


def _money(v) -> str:
    if is_missing(v):
        return "—"
    v = float(v)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= cutoff:
            return f"${v / cutoff:,.1f}{suffix}"
    return f"${v:,.0f}"


def render(*, db_path: str, run: dict) -> None:
    table = D.composite_table(db_path, run["run_id"])
    if table.empty:
        st.warning("This run has no scored names.")
        return

    live = _valuations(db_path, run["run_id"],
                       is_current=D.is_current(db_path, run))
    if live.empty:
        st.info(
            "This run has no valuation snapshot, so the P/E, margin and "
            "yield columns will be blank. Re-run `python -m lodestar run` to "
            "produce one.")
        df = table.copy()
        for col in ("market_cap", "price", "pe_ttm", "pe_forward", "ev_ebitda",
                    "fcf_yield", "dividend_yield", "gross_margin",
                    "operating_margin", "net_margin", "roe", "roic",
                    "debt_to_equity"):
            df[col] = float("nan")
    else:
        df = table.merge(live, on="ticker", how="left")

    # ---- filters, all visible at once ---------------------------------
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        sectors = sorted(df["sector"].dropna().unique())
        sel_sectors = st.multiselect("Sector", sectors, default=[],
                                      placeholder="All sectors",
                                      key="scr_sector")
    with f2:
        industries = sorted(df["industry"].dropna().unique())
        sel_inds = st.multiselect("Industry", industries, default=[],
                                   placeholder="All industries",
                                   key="scr_industry")
    with f3:
        cap_band = st.selectbox("Market cap", list(CAP_BANDS), key="scr_cap")
    with f4:
        sort_by = st.selectbox(
            "Sort by",
            ["Composite percentile", "P/E forward (low first)",
             "P/E trailing (low first)", "FCF yield (high first)",
             "Dividend yield (high first)", "ROIC (high first)",
             "Operating margin (high first)", "Market cap (large first)"],
            key="scr_sort")

    g1, g2, g3 = st.columns([1, 1, 2])
    with g1:
        min_pct = st.slider("Minimum percentile", 0, 100, 0, key="scr_pct")
    with g2:
        max_pe = st.slider("Maximum P/E (0 = no limit)", 0, 100, 0,
                            key="scr_pe")
    with g3:
        scored_only = st.checkbox(
            "Only names with a composite score", value=False,
            key="scr_scored",
            help="A name is unscored when too little of it was measurable — "
                 "that is reported rather than guessed at.")

    # ---- apply ---------------------------------------------------------
    view = df
    if sel_sectors:
        view = view[view["sector"].isin(sel_sectors)]
    if sel_inds:
        view = view[view["industry"].isin(sel_inds)]
    lo, hi = CAP_BANDS[cap_band]
    if lo > 0 or hi < float("inf"):
        view = view[view["market_cap"].between(lo, hi)]
    if min_pct > 0:
        view = view[view["percentile"].fillna(-1) >= min_pct]
    if max_pe > 0:
        view = view[view["pe_ttm"].notna() & (view["pe_ttm"] <= max_pe)]
    if scored_only:
        view = view[view["percentile"].notna()]

    sort_map = {
        "Composite percentile": ("percentile", False),
        "P/E forward (low first)": ("pe_forward", True),
        "P/E trailing (low first)": ("pe_ttm", True),
        "FCF yield (high first)": ("fcf_yield", False),
        "Dividend yield (high first)": ("dividend_yield", False),
        "ROIC (high first)": ("roic", False),
        "Operating margin (high first)": ("operating_margin", False),
        "Market cap (large first)": ("market_cap", False),
    }
    col, ascending = sort_map[sort_by]
    view = view.sort_values(col, ascending=ascending, na_position="last")

    st.markdown(stat_strip([
        ("Names shown", f"{len(view):,}"),
        ("Scored", f"{int(view['percentile'].notna().sum()):,}"),
        ("Median P/E", f"{view['pe_ttm'].median():,.1f}×"
         if view["pe_ttm"].notna().any() else "—"),
        ("Median FCF yield", f"{view['fcf_yield'].median() * 100:,.2f}%"
         if view["fcf_yield"].notna().any() else "—"),
        ("Total market cap", _money(view["market_cap"].sum())),
    ]), unsafe_allow_html=True)

    # ---- table ---------------------------------------------------------
    rows = []
    for i, (_, r) in enumerate(view.iterrows(), 1):
        row = {
            "rank": i,
            "ticker": r["ticker"],
            "sector": r.get("sector"),
            "percentile": r.get("percentile"),
            "score": signed(r.get("composite_raw"), fmt="+.3f"),
            "sort_score": r.get("composite_raw"),
            "bar": score_bar(r.get("composite_raw")),
            "mcap": _money(r.get("market_cap")),
            "sort_mcap": r.get("market_cap"),
            "pe": None if is_missing(r.get("pe_ttm")) else round(r["pe_ttm"], 1),
            "pe_fwd": None if is_missing(r.get("pe_forward")) else round(r["pe_forward"], 1),
            "fcfy": None if is_missing(r.get("fcf_yield")) else r["fcf_yield"] * 100,
            "opm": None if is_missing(r.get("operating_margin")) else r["operating_margin"] * 100,
            "roic": None if is_missing(r.get("roic")) else r["roic"] * 100,
        }
        for b in BUCKET_NAMES:
            row[b] = signed(r.get(f"{b}_sub"), fmt="+.2f")
            row[f"sort_{b}"] = r.get(f"{b}_sub")
        rows.append(row)

    columns = [
        {"key": "rank", "label": "#", "kind": "rank", "nosort": True},
        {"key": "ticker", "label": "Ticker", "kind": "ticker"},
        {"key": "sector", "label": "Sector", "kind": "sector"},
        {"key": "percentile", "label": "Pctile", "kind": "num", "fmt": ".0f"},
        {"key": "score", "label": "Score", "kind": "html", "cls": "num",
         "sort": "sort_score"},
        {"key": "bar", "label": "", "kind": "html", "nosort": True},
        {"key": "mcap", "label": "Mkt cap", "cls": "num", "sort": "sort_mcap"},
        {"key": "pe", "label": "P/E", "kind": "num", "fmt": ".1f"},
        {"key": "pe_fwd", "label": "P/E fwd", "kind": "num", "fmt": ".1f"},
        {"key": "fcfy", "label": "FCF yld %", "kind": "num", "fmt": ".2f"},
        {"key": "opm", "label": "Op margin %", "kind": "num", "fmt": ".1f"},
        {"key": "roic", "label": "ROIC %", "kind": "num", "fmt": ".1f"},
    ]
    columns += [{"key": b, "label": b.replace("_", " ").title()[:11],
                 "kind": "html", "cls": "num", "sort": f"sort_{b}"}
                for b in BUCKET_NAMES]

    st.markdown(html_table(columns, rows, max_height=680,
                           logos=D.logo_uris(view["ticker"].dropna())),
                unsafe_allow_html=True)

    priced_at = (view["price_as_of"].dropna().mode()
                 if "price_as_of" in view.columns else [])
    priced_note = (
        f" Market caps, P/Es and yields are re-priced to the close of "
        f"**{priced_at.iloc[0]}**; the bucket subscores and percentile are "
        f"from the run itself and do not move between runs."
        if len(priced_at) else "")
    st.caption(
        "Click any column header to sort. Bucket columns are subscores in "
        "standard deviations within the name's peer group; a dash means that "
        "bucket had too little coverage to score, which is reported rather "
        "than filled in with a neutral value." + priced_note)

    csv = view.to_csv(index=False).encode("utf-8")
    st.download_button("Download this screen as CSV", csv,
                       file_name=f"finlake_screen_{run['as_of']}.csv",
                       mime="text/csv", key="scr_csv")
