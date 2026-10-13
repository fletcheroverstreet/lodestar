"""News — everything published across the universe.

Four sources, deduplicated so a story carried by five feeds counts once.
Filterable by ticker, event type, source, and tone.
"""

from __future__ import annotations

import streamlit as st

from .. import data as D
from ..theme import (
    TONE_NEUTRAL_BAND, esc, html_table, is_missing, stat_strip, tone_chip,
)

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


def render(*, db_path: str, run: dict) -> None:
    f1, f2, f3 = st.columns([1, 2, 2])
    with f1:
        days = st.selectbox("Window", [1, 3, 7, 14, 30, 60], index=2,
                             format_func=lambda d: f"Last {d} days",
                             key="news_days")
    articles = D.universe_news(days=days, limit=1500, as_of=run["as_of"])
    if articles.empty:
        st.info("No news cached for this window. Run "
                "`python -m finlake refresh --only news` to fetch some.")
        return

    with f2:
        classes = sorted(articles["event_class"].dropna().unique())
        sel_events = st.multiselect(
            "Event type", classes, default=[], placeholder="All event types",
            format_func=lambda c: EVENT_LABELS.get(c, c.replace("_", " ").title()),
            key="news_events")
    with f3:
        tone_band = st.selectbox(
            "Tone", ["Any", "Positive", "Neutral", "Negative",
                     "Scored only", "No sentiment words"], key="news_tone",
            help="Positive and negative use a deadband: a tone inside "
                 "±%.2f is reported as neutral rather than as a weak "
                 "direction the method cannot actually support."
                 % TONE_NEUTRAL_BAND)

    g1, g2 = st.columns([2, 2])
    with g1:
        # Tickers are stored comma-joined per article; split for the filter.
        all_tickers = sorted({
            t for row in articles["tickers"].dropna()
            for t in str(row).split(",") if t})
        sel_tickers = st.multiselect("Ticker", all_tickers, default=[],
                                      placeholder="All tickers",
                                      key="news_tickers")
    with g2:
        sources = sorted(articles["source"].dropna().unique())
        sel_sources = st.multiselect("Source", sources, default=[],
                                      placeholder="All sources",
                                      key="news_sources")

    view = articles
    if sel_events:
        view = view[view["event_class"].isin(sel_events)]
    if sel_sources:
        view = view[view["source"].isin(sel_sources)]
    if sel_tickers:
        wanted = set(sel_tickers)
        view = view[view["tickers"].fillna("").apply(
            lambda s: bool(wanted & set(str(s).split(","))))]
    if tone_band == "Positive":
        view = view[view["sentiment"] > TONE_NEUTRAL_BAND]
    elif tone_band == "Negative":
        view = view[view["sentiment"] < -TONE_NEUTRAL_BAND]
    elif tone_band == "Neutral":
        view = view[view["sentiment"].notna()
                    & view["sentiment"].abs().le(TONE_NEUTRAL_BAND)]
    elif tone_band == "Scored only":
        view = view[view["sentiment"].notna()]
    elif tone_band == "No sentiment words":
        view = view[view["sentiment"].isna()]

    scored = view["sentiment"].dropna()
    n_pos = int((scored > TONE_NEUTRAL_BAND).sum())
    n_neg = int((scored < -TONE_NEUTRAL_BAND).sum())
    n_neu = int(len(scored) - n_pos - n_neg)
    st.markdown(stat_strip([
        ("Articles", f"{len(view):,}"),
        ("Overall tone",
         tone_chip(scored.mean()) if len(scored) else tone_chip(None),
         f"across {len(scored):,} scored"),
        ("Positive", f"{n_pos:,}",
         f"{n_pos / len(scored):.0%}" if len(scored) else None),
        ("Neutral", f"{n_neu:,}",
         f"{n_neu / len(scored):.0%}" if len(scored) else None),
        ("Negative", f"{n_neg:,}",
         f"{n_neg / len(scored):.0%}" if len(scored) else None),
        ("No signal", f"{int(view['sentiment'].isna().sum()):,}",
         "no sentiment vocabulary"),
    ]), unsafe_allow_html=True)

    rows = []
    for _, a in view.head(400).iterrows():
        title = esc(a["title"])
        if a.get("url"):
            title = (f'<a href="{esc(a["url"])}" target="_blank" '
                     f'style="color:inherit">{title}</a>')
        rows.append({
            "date": a["published_at"],
            "tickers": a["tickers"],
            "title": title,
            "event": _event_label(a.get("event_class")),
            "tone": tone_chip(a["sentiment"], scored_text=a.get("summary")),
            "sort_tone": a["sentiment"],
            "source": _source_label(a.get("publisher"), a.get("source")),
        })
    st.markdown(html_table(
        [{"key": "date", "label": "Date", "cls": "num"},
         {"key": "tickers", "label": "Tickers", "kind": "tickers"},
         {"key": "title", "label": "Headline", "kind": "html", "cls": ""},
         {"key": "source", "label": "Source", "kind": "html"},
         {"key": "event", "label": "Type"},
         {"key": "tone", "label": "Tone", "kind": "html", "cls": "num",
          "sort": "sort_tone"}],
        rows, max_height=700), unsafe_allow_html=True)

    st.caption(
        "The same story reaches several feeds within minutes, so articles are "
        "deduplicated on their normalised headline — a syndicated piece counts "
        "once, not five times.\n\n"
        "**How tone is read.** A Loughran-McDonald financial lexicon (where "
        "*liability*, *cost* and *depreciation* are neutral accounting words, "
        "not negative ones), plus a set of phrase patterns for the idioms a "
        "word count reads backwards — *failed to beat* contains a positive "
        "word, *cuts costs* a negative one. The score is scaled by how much "
        "sentiment vocabulary the headline actually carried, so a single "
        "incidental word is a weak reading rather than a maximum-confidence "
        "verdict. **no signal** means no sentiment vocabulary was found at "
        "all, which is not the same as neutral. SEC filings are read from "
        "their 8-K item codes rather than scored as prose: most items are "
        "procedural and carry no tone in either direction.")
