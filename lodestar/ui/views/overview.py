"""Overview — the market right now, and what moved.

The landing page. It answers three questions in order: what is the macro
backdrop, where is money working, and what happened since the last run. Each
one links into a deeper page rather than trying to be that page.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...scoring.ratings import groups_to_frame, rate_sectors
from .. import charts
from .. import data as D
from ..theme import (
    esc, html_table, is_missing, score_bar, signed, stat_strip, tone_chip,
)

# The macro series a reader wants at a glance, with how to format each.
MACRO_TILES = [
    ("DGS10", "10-year Treasury", "%"),
    ("DGS2", "2-year Treasury", "%"),
    ("T10Y2Y", "10y − 2y spread", "%"),
    ("FEDFUNDS", "Fed funds", "%"),
    ("CPIAUCSL", "CPI (index)", "idx"),
    ("UNRATE", "Unemployment", "%"),
    ("VIXCLS", "VIX", "n"),
    ("BAMLH0A0HYM2", "High-yield spread", "%"),
]

CURVE_POINTS = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("10Y", "DGS10"),
                ("30Y", "DGS30")]



EVENT_LABELS = {
    "earnings": "Earnings", "guidance": "Guidance", "ma": "M&A",
    "legal": "Legal / regulatory", "management": "Management change",
    "capital_return": "Dividends & buybacks", "analyst": "Analyst action",
    "product": "Product / partnership",
}


# How each feed is named when we cite it.
SOURCE_NAMES = {
    "sec": "SEC EDGAR", "yahoo": "Yahoo Finance",
    "google_news": "Google News", "yfinance": "Yahoo Finance",
}


def _source_label(publisher, source) -> str:
    """Who wrote it, and which feed it came through.

    Both, because they are different facts and each matters. The publisher is
    who is accountable for the claim; the feed is how it reached us and what
    its reliability weight was based on. A Reuters story that arrived via an
    aggregator is still a Reuters story, and saying only "Google News" would
    hide the byline that actually carries the credibility.
    """
    feed = SOURCE_NAMES.get(source, str(source or "").replace("_", " ").title())
    if is_missing(publisher) or not str(publisher).strip():
        return esc(feed) or "—"
    who = str(publisher).strip()
    if not feed or who.lower() == feed.lower():
        return esc(who)
    return f'{esc(who)} <span class="ls-pill">via {esc(feed)}</span>'


def _event_label(value) -> str:
    """Event class as a readable label, with missing rendered as a dash.

    `if value:` is WRONG here and was live: pandas gives a missing
    `event_class` as float('nan'), and NaN is truthy in Python — so the check
    passed and `str(nan)` rendered the literal text "nan" in the Type column.
    The table renderer guards its own cells against this; a caller that
    formats a value BEFORE handing it over has to guard it too.
    """
    if is_missing(value):
        return "—"
    return EVENT_LABELS.get(value, str(value).replace("_", " ").title())


def _latest_macro(series_id: str, as_of: str) -> float | None:
    df = D.macro(series_id, as_of)
    if df.empty or "value" not in df.columns:
        return None
    values = df["value"].dropna()
    return float(values.iloc[-1]) if len(values) else None


def _fmt_macro(value, unit: str) -> str:
    if is_missing(value):
        return "—"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "idx":
        return f"{value:,.1f}"
    return f"{value:,.1f}"


def render(*, db_path: str, run: dict) -> None:
    as_of = run["as_of"]

    # ---- macro backdrop ------------------------------------------------
    st.markdown("## The backdrop")
    tiles = []
    for series_id, label, unit in MACRO_TILES:
        value = _latest_macro(series_id, as_of)
        tiles.append((label, _fmt_macro(value, unit)))
    if any(v != "—" for _l, v in tiles):
        st.markdown(stat_strip(tiles), unsafe_allow_html=True)
    else:
        st.caption(
            "No macro data cached. Set FRED_API_KEY in finlake/.env and run "
            "`python -m finlake refresh --only macro`.")

    curve = [(label, _latest_macro(sid, as_of)) for label, sid in CURVE_POINTS]
    curve = [(label, v) for label, v in curve if v is not None]
    if len(curve) >= 3:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown("###### Treasury yield curve")
            st.plotly_chart(charts.yield_curve(curve), width="stretch",
                            config={"displayModeBar": False})
        with c2:
            spread = _latest_macro("T10Y2Y", as_of)
            st.markdown("###### What the curve is saying")
            if spread is not None:
                inverted = spread < 0
                st.markdown(
                    f'<div class="ls-card">'
                    f'<div class="ls-card-label">10-year minus 2-year</div>'
                    f'<div class="ls-card-value">{signed(spread, fmt="+.2f", suffix="%")}</div>'
                    f'<div class="ls-card-note">'
                    f'{"Inverted — short rates above long. Historically this has "
                       "preceded recessions, with long and variable lags."
                       if inverted else
                       "Positively sloped — the ordinary shape, with long rates "
                       "above short."}'
                    f'</div></div>', unsafe_allow_html=True)

    # ---- where money is working ----------------------------------------
    table = D.composite_table(db_path, run["run_id"])
    if table.empty:
        st.warning("This run has no scored names.")
        return

    st.markdown("## Where the scores are")
    sector_ratings = rate_sectors(table, as_of=as_of)
    sframe = groups_to_frame(sector_ratings)
    rows = []
    for _, r in sframe.iterrows():
        rows.append({
            "sector": r.get("sector"),
            "rating": r.get("rating"),
            "mean": signed(r.get("mean_score"), fmt="+.3f"),
            "sort_mean": r.get("mean_score"),
            "bar": score_bar(r.get("mean_score")),
            "buys": r.get("buys"),
            "sells": r.get("sells"),
            "n": r.get("scored"),
            "spread": r.get("dispersion"),
        })
    st.markdown(html_table(
        [{"key": "sector", "label": "Sector", "cls": ""},
         {"key": "rating", "label": "Rating"},
         {"key": "mean", "label": "Mean score", "kind": "html", "cls": "num",
          "sort": "sort_mean"},
         {"key": "bar", "label": "", "kind": "html", "nosort": True},
         {"key": "buys", "label": "Buys", "kind": "num", "fmt": ".0f"},
         {"key": "sells", "label": "Sells", "kind": "num", "fmt": ".0f"},
         {"key": "n", "label": "Rated", "kind": "num", "fmt": ".0f"},
         {"key": "spread", "label": "Spread", "kind": "num", "fmt": ".3f"}],
        rows, max_height=460), unsafe_allow_html=True)

    # ---- best and worst ------------------------------------------------
    scored = table[table["percentile"].notna()].sort_values(
        "percentile", ascending=False)
    if not scored.empty:
        c1, c2 = st.columns(2)
        for col, label, subset in (
                (c1, "Highest scoring", scored.head(12)),
                (c2, "Lowest scoring", scored.tail(12).iloc[::-1])):
            with col:
                st.markdown(f"###### {label}")
                rows = [{
                    "ticker": r["ticker"],
                    "sector": r.get("sector"),
                    "pctile": r.get("percentile"),
                    "score": signed(r.get("composite_raw"), fmt="+.3f"),
                    "sort_score": r.get("composite_raw"),
                    "bar": score_bar(r.get("composite_raw")),
                } for _, r in subset.iterrows()]
                st.markdown(html_table(
                    [{"key": "ticker", "label": "Ticker", "kind": "ticker"},
                     {"key": "sector", "label": "Sector", "kind": "sector"},
                     {"key": "pctile", "label": "Pctile", "kind": "num", "fmt": ".0f"},
                     {"key": "score", "label": "Score", "kind": "html",
                      "cls": "num", "sort": "sort_score"},
                     {"key": "bar", "label": "", "kind": "html", "nosort": True}],
                    rows, max_height=420,
                    logos=D.logo_uris(subset["ticker"].dropna())),
                    unsafe_allow_html=True)

    # ---- what moved -----------------------------------------------------
    runs = D.list_runs(db_path)
    if len(runs) >= 2:
        st.markdown("## What moved since the last run")
        diff = D.diff_runs(db_path, runs[1]["run_id"], runs[0]["run_id"])
        if diff.empty:
            st.caption("No comparable names between the two most recent runs.")
        else:
            changed = diff["percentile_change"].dropna()
            st.markdown(stat_strip([
                ("Earlier run", diff.attrs.get("earlier_as_of", "—")),
                ("Later run", diff.attrs.get("later_as_of", "—")),
                ("Names moved", f"{int((changed != 0).sum())}"),
                ("Largest move",
                 f"{changed.abs().max():.1f}" if len(changed) else "—"),
            ]), unsafe_allow_html=True)

            movers = diff.reindex(
                diff["percentile_change"].abs().sort_values(
                    ascending=False).index).head(20)
            rows = [{
                "ticker": r.get("ticker"),
                "sector": r.get("sector_after") or r.get("sector_before"),
                "before": r.get("percentile_before"),
                "after": r.get("percentile_after"),
                "change": signed(r.get("percentile_change"), fmt="+.1f"),
                "sort_change": r.get("percentile_change"),
                "bar": score_bar(r.get("percentile_change"), scale=25.0),
            } for _, r in movers.iterrows()]
            st.markdown(html_table(
                [{"key": "ticker", "label": "Ticker", "kind": "ticker"},
                 {"key": "sector", "label": "Sector", "kind": "sector"},
                 {"key": "before", "label": "Before", "kind": "num", "fmt": ".1f"},
                 {"key": "after", "label": "After", "kind": "num", "fmt": ".1f"},
                 {"key": "change", "label": "Change", "kind": "html",
                  "cls": "num", "sort": "sort_change"},
                 {"key": "bar", "label": "", "kind": "html", "nosort": True}],
                rows, max_height=520,
                logos=D.logo_uris(movers["ticker"].dropna())),
                unsafe_allow_html=True)

    # ---- latest news ----------------------------------------------------
    st.markdown("## Latest across the universe")
    articles = D.universe_news(days=3, limit=25, as_of=as_of)
    if articles.empty:
        st.caption("No news cached in the last three days.")
    else:
        rows = []
        for _, a in articles.iterrows():
            title = esc(a["title"])
            if a.get("url"):
                title = (f'<a href="{esc(a["url"])}" target="_blank" '
                         f'style="color:inherit">{title}</a>')
            rows.append({
                "date": a["published_at"],
                "tickers": a["tickers"],
                "title": title,
                "source": _source_label(a.get("publisher"), a.get("source")),
                "event": _event_label(a.get("event_class")),
                "tone": tone_chip(a["sentiment"],
                                  scored_text=a.get("summary")),
                "sort_tone": a["sentiment"],
            })
        st.markdown(html_table(
            [{"key": "date", "label": "Date", "cls": "num"},
             {"key": "tickers", "label": "Tickers", "kind": "tickers"},
             {"key": "title", "label": "Headline", "kind": "html", "cls": ""},
             {"key": "source", "label": "Source", "kind": "html"},
             {"key": "event", "label": "Type"},
             {"key": "tone", "label": "Tone", "kind": "html", "cls": "num",
              "sort": "sort_tone"}],
            rows, max_height=480), unsafe_allow_html=True)
