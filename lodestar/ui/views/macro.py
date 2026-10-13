"""Macro — rates, inflation, growth, and the yield curve.

Every series here is point-in-time. The value shown for a past date is the
value that was PUBLISHED then, not today's revised figure — Q1 2020 real GDP
was first printed at 18,987.9 and reads 20,709.2 today, and a macro signal
backtested on final revisions is using information that did not exist at the
trade date.
"""

from __future__ import annotations

import streamlit as st

from .. import charts
from .. import data as D
from ..theme import esc, html_table, is_missing, signed, stat_strip

GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "Rates & the curve": [
        ("DGS3MO", "3-month Treasury", "%"),
        ("DGS2", "2-year Treasury", "%"),
        ("DGS10", "10-year Treasury", "%"),
        ("DGS30", "30-year Treasury", "%"),
        ("T10Y2Y", "10y − 2y spread", "%"),
        ("T10Y3M", "10y − 3m spread", "%"),
        ("FEDFUNDS", "Fed funds rate", "%"),
        ("MORTGAGE30US", "30-year mortgage", "%"),
    ],
    "Inflation": [
        ("CPIAUCSL", "CPI, all urban", "idx"),
        ("CPILFESL", "Core CPI", "idx"),
        ("PCEPILFE", "Core PCE", "idx"),
    ],
    "Growth & labour": [
        ("GDPC1", "Real GDP", "idx"),
        ("UNRATE", "Unemployment rate", "%"),
        ("PAYEMS", "Nonfarm payrolls", "idx"),
        ("INDPRO", "Industrial production", "idx"),
        ("HOUST", "Housing starts", "idx"),
        ("RSAFS", "Retail sales", "idx"),
        ("UMCSENT", "Consumer sentiment", "idx"),
    ],
    "Markets & risk": [
        ("VIXCLS", "VIX", "n"),
        ("BAMLH0A0HYM2", "High-yield spread", "%"),
        ("DCOILWTICO", "WTI crude", "$"),
        ("DTWEXBGS", "Trade-weighted dollar", "idx"),
    ],
}

CURVE_POINTS = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("10Y", "DGS10"),
                ("30Y", "DGS30")]


def _fmt(value, unit: str) -> str:
    if is_missing(value):
        return "—"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "$":
        return f"${value:,.2f}"
    return f"{value:,.1f}"


def render(*, db_path: str, run: dict) -> None:
    as_of = run["as_of"]

    curve = []
    for label, sid in CURVE_POINTS:
        df = D.macro(sid, as_of)
        if not df.empty and df["value"].notna().any():
            curve.append((label, float(df["value"].dropna().iloc[-1])))
    if not curve:
        st.info(
            "No macro data cached yet. Add a free FRED key to finlake/.env "
            "and run `python -m finlake refresh --only macro`.")
        return

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("###### Treasury yield curve")
        st.plotly_chart(charts.yield_curve(curve), width="stretch",
                        config={"displayModeBar": False})
    with c2:
        st.markdown("###### 10-year Treasury, 10 years of history")
        ten = D.macro("DGS10", as_of)
        if not ten.empty:
            series = ten["value"].dropna().tail(2600)
            st.plotly_chart(
                charts.multi_line(series.to_frame("10-year Treasury")),
                width="stretch", config={"displayModeBar": False})

    for group, items in GROUPS.items():
        st.markdown(f"## {group}")
        rows = []
        for series_id, label, unit in items:
            df = D.macro(series_id, as_of)
            if df.empty or "value" not in df.columns:
                rows.append({"series": label, "latest": "—", "code": series_id})
                continue
            values = df["value"].dropna()
            if values.empty:
                rows.append({"series": label, "latest": "—", "code": series_id})
                continue
            latest = float(values.iloc[-1])
            prior = float(values.iloc[-2]) if len(values) > 1 else None
            year_ago = float(values.iloc[-13]) if len(values) > 13 else None
            rows.append({
                "series": label,
                "latest": _fmt(latest, unit),
                "sort_latest": latest,
                "change": (signed(latest - prior, fmt="+.2f")
                           if prior is not None else "—"),
                "vs_year": (signed(latest - year_ago, fmt="+.2f")
                            if year_ago is not None else "—"),
                "observations": len(values),
                "code": series_id,
            })
        st.markdown(html_table(
            [{"key": "series", "label": "Series", "cls": ""},
             {"key": "latest", "label": "Latest", "cls": "num",
              "sort": "sort_latest"},
             {"key": "change", "label": "vs. prior", "kind": "html", "cls": "num"},
             {"key": "vs_year", "label": "vs. ~1y ago", "kind": "html", "cls": "num"},
             {"key": "observations", "label": "Observations", "kind": "num", "fmt": ",.0f"},
             {"key": "code", "label": "FRED code"}],
            rows, max_height=420), unsafe_allow_html=True)

    st.caption(
        "Every series is stored with its publication vintage, so a historical "
        "query returns the number that was public on that date rather than "
        "today's revision. Real GDP for Q1 2020 was first printed at 18,987.9 "
        "and reads 20,709.2 today — a macro signal tested on final revisions "
        "is using information nobody had at the time. Market rates like "
        "Treasury yields are never revised, so they carry a single vintage.")
