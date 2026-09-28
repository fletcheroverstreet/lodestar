"""One company, in full.

The centrepiece of the hub, and the page that has to carry the most without
becoming a wall of numbers. The ordering is deliberate — headline figures
first, then valuation, then the statements, then the score, then news — so a
reader gets the answer before the evidence, and the evidence is there when
they want it. Everything below the fold sits in expanders.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from ...config import BUCKET_NAMES
from .. import charts
from .. import data as D
from ..theme import (
    INK_MUTED, RED, TEAL, TONE_NEUTRAL_BAND, card, chip, esc, html_table,
    is_missing, logo_mark, pill, score_bar, signed, stat_strip, tone_chip,
)

# Ratios shown in the valuation panel, in reading order. Trailing and forward
# sit adjacent on purpose: the gap between them IS the market's growth
# expectation, and separating them hides the comparison.
VALUATION_ROWS = [
    ("pe_ttm", "P/E (trailing)", "x"),
    ("pe_forward", "P/E (forward)", "x"),
    ("peg", "PEG", "x"),
    ("ps", "P/S", "x"),
    ("pb", "P/B", "x"),
    ("p_fcf", "P/FCF", "x"),
    ("ev_ebitda", "EV/EBITDA", "x"),
    ("ev_ebit", "EV/EBIT", "x"),
    ("ev_sales", "EV/Sales", "x"),
    ("earnings_yield", "Earnings yield", "%"),
    ("fcf_yield", "FCF yield", "%"),
    ("dividend_yield", "Dividend yield", "%"),
    ("buyback_yield", "Buyback yield", "%"),
    ("payout_ratio", "Payout ratio", "%"),
]

PROFITABILITY_ROWS = [
    ("gross_margin", "Gross margin", "%"),
    ("operating_margin", "Operating margin", "%"),
    ("net_margin", "Net margin", "%"),
    ("ebitda_margin", "EBITDA margin", "%"),
    ("fcf_margin", "FCF margin", "%"),
    ("roe", "Return on equity", "%"),
    ("roa", "Return on assets", "%"),
    ("roic", "Return on invested capital", "%"),
    ("roce", "Return on capital employed", "%"),
]

HEALTH_ROWS = [
    ("current_ratio", "Current ratio", "x"),
    ("quick_ratio", "Quick ratio", "x"),
    ("debt_to_equity", "Debt / equity", "x"),
    ("debt_to_assets", "Debt / assets", "x"),
    ("net_debt_to_ebitda", "Net debt / EBITDA", "x"),
    ("interest_coverage", "Interest coverage", "x"),
    ("altman_z", "Altman Z-score", "x"),
    ("asset_turnover", "Asset turnover", "x"),
    ("inventory_turnover", "Inventory turnover", "x"),
    ("dso", "Days sales outstanding", "d"),
    ("dio", "Days inventory outstanding", "d"),
    ("cash_conversion_cycle", "Cash conversion cycle", "d"),
]

# How many quarterly observations a "5-year range" needs before it is one.
# Four is a year — enough for the range to have crossed a full seasonal cycle,
# and low enough that a recently-listed company still gets context.
MIN_RANGE_OBSERVATIONS = 4



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


def _formula_for(key: str) -> str | None:
    """How a ratio is computed, straight from finlake's own catalogue.

    Read from the source the calculation uses rather than restated here, so
    the tooltip and the number can never disagree. Returns None when the
    catalogue is unavailable, in which case the label simply renders plain.
    """
    try:
        from finlake import ratios as fl_ratios

        definition = fl_ratios.explain(key)
        return definition.formula if definition else None
    except Exception:  # noqa: BLE001
        return None


def _money(value, *, decimals: int = 0) -> str:
    """Compact currency. A hub shows $4.14T, not 4142000000000."""
    if is_missing(value):
        return "—"
    v = float(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= cutoff:
            return f"${v / cutoff:,.{2 if abs(v) < 10 * cutoff else 1}f}{suffix}"
    return f"${v:,.{decimals}f}"


def _fmt_ratio(value, unit: str) -> str:
    if is_missing(value):
        return "—"
    if unit == "%":
        return f"{value * 100:,.1f}%"
    if unit == "d":
        return f"{value:,.0f} d"
    return f"{value:,.2f}×"


def _ratio_table(latest: dict, hist: pd.DataFrame,
                 rows: list[tuple[str, str, str]]) -> str:
    """A ratio block with its own 5-year range beside each current value.

    The range is the point. "P/E 32" means nothing alone; "P/E 32, and this
    company has traded between 21 and 38 over five years" is a judgement a
    reader can actually make. A multiple without its own history is a number
    pretending to be context.
    """
    out = []
    for key, label, unit in rows:
        # Hovering a metric name shows how it is computed. The formula comes
        # from finlake's ratio catalogue — the same object the calculation
        # reads — so the explanation cannot drift from the number the way a
        # hand-written second copy would.
        formula = _formula_for(key)
        name = (f'<span title="{esc(formula)}" style="border-bottom:1px dotted '
                f'#33507f;cursor:help">{esc(label)}</span>'
                if formula else esc(label))
        value = latest.get(key)
        series = hist[key].dropna() if key in hist.columns else pd.Series(dtype=float)
        window = series.tail(20)

        # A "range" needs something to range over. Forward P/E only exists
        # from the day the market source first ran, so one observation was
        # rendering as "15.89× – 15.89×" with a 5-year median of 15.89× —
        # three columns of apparent history from a single data point, which
        # is the most confident-looking way to present almost nothing.
        if len(window) < MIN_RANGE_OBSERVATIONS:
            out.append({
                "metric": name,
                "value": _fmt_ratio(value, unit),
                "sort_value": None if is_missing(value) else float(value),
                "median": "—",
                "range": (f'<span class="ls-pill">{len(window)} of '
                          f'{MIN_RANGE_OBSERVATIONS} periods</span>'
                          if len(window) else "—"),
                "bar": "",
            })
            continue

        lo, hi = window.min(), window.max()
        median = window.median()

        position = None
        if (not is_missing(value) and not is_missing(lo) and not is_missing(hi)
                and hi > lo):
            # Where the current value sits in its own range, as -1..+1 around
            # the midpoint, so the inline bar reads as cheap/expensive versus
            # this company's own history rather than versus other companies.
            position = ((value - lo) / (hi - lo) - 0.5) * 2

        out.append({
            "metric": name,
            "value": _fmt_ratio(value, unit),
            "sort_value": None if is_missing(value) else float(value),
            "median": _fmt_ratio(median, unit),
            "range": (f"{_fmt_ratio(lo, unit)} – {_fmt_ratio(hi, unit)}"
                      if not is_missing(lo) else "—"),
            "bar": score_bar(position, scale=1.0) if position is not None else "",
        })

    return html_table(
        [{"key": "metric", "label": "Metric", "kind": "html", "cls": ""},
         {"key": "value", "label": "Current", "cls": "num", "sort": "sort_value"},
         {"key": "median", "label": "5y median", "cls": "num"},
         {"key": "range", "label": "5y range", "cls": "num"},
         {"key": "bar", "label": "vs. own range", "kind": "html"}],
        out, max_height=520)


def _statement_table(stmt: pd.DataFrame, *, scale: float = 1e6) -> str:
    """A financial statement, columns newest-last, figures in millions.

    Incomplete fiscal years are MARKED IN THE COLUMN HEADER. The newest year
    is always still running and the oldest is usually clipped by the lookback
    window, so both routinely carry two or three quarters of flow. Printed in
    the same row as four-quarter figures, a nine-month revenue number is
    understated by roughly a quarter and looks entirely ordinary — Apple's
    FY2026 column showed $364bn beside a complete FY2025 of $416bn, which
    reads as a collapse rather than as a year with a quarter left to run.
    """
    period_cols = [c for c in stmt.columns
                   if c not in ("label", "indent", "is_subtotal",
                                "is_derived", "formula")]
    quarters = stmt.attrs.get("quarters_in_period") or {}
    rows = []
    for key, r in stmt.iterrows():
        indent = "&nbsp;" * 4 * int(r.get("indent") or 0)
        name = esc(r["label"])
        if r.get("is_subtotal"):
            name = f"<b>{name}</b>"
        if r.get("is_derived"):
            # A derived line is marked, always. Presenting a computed figure
            # as though the company filed it is the quiet kind of dishonesty.
            name += (f' <span class="ls-pill" title="{esc(r.get("formula") or "")}">'
                     f'derived</span>')
        row = {"line": f"{indent}{name}"}
        for c in period_cols:
            value = r.get(c)
            per_share = key in ("eps_basic", "eps_diluted", "dividends_per_share")
            if is_missing(value):
                row[str(c)] = "—"
            elif per_share:
                row[str(c)] = f"{value:,.2f}"
            else:
                row[str(c)] = f"{value / scale:,.0f}"
        rows.append(row)

    columns = [{"key": "line", "label": "", "kind": "html", "cls": ""}]
    for c in period_cols:
        n = quarters.get(str(c))
        label = str(c)
        if n is not None and n < 4:
            label = f"{label} ({n}Q)"
        columns.append({"key": str(c), "label": label, "cls": "num"})
    return html_table(columns, rows, max_height=680, sortable=False)


def _staleness(as_of: str | None, *, warn_days: int) -> str | None:
    """How old a date is, in words, once it is old enough to matter.

    Returns None while the data is current, so a fresh page carries no
    warnings at all and a stale one is impossible to miss. Weekends count: a
    Friday close read on Sunday is the newest close there is, not stale data.
    """
    if not as_of:
        return None
    try:
        age = (dt.date.today() - dt.date.fromisoformat(str(as_of)[:10])).days
    except ValueError:
        return None
    if age <= warn_days:
        return None
    return f"{age} days old"


def _header(ticker: str, latest: dict, snap: dict, comp: pd.Series | None,
            quote: dict) -> None:
    name = snap.get("profile_name") or ticker
    sector = snap.get("profile_sector")
    industry = snap.get("profile_industry")

    # The logo anchors the page — it is the one place on the whole hub where
    # a mark is large enough to actually be recognised, so it sits at the
    # title's own optical size rather than as an afterthought beside it.
    # `align-items:center` on this row and `baseline` on the text inside it:
    # the mark aligns to the block, the words align to each other.
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.85rem;'
        f'flex-wrap:wrap;margin-bottom:0.5rem">'
        f'{logo_mark(ticker, D.logo_uri(ticker, size=44), size=44)}'
        f'<span style="display:flex;align-items:baseline;gap:0.75rem;'
        f'flex-wrap:wrap">'
        f'<span style="font-size:1.9rem;font-weight:800;letter-spacing:-0.03em">'
        f'{esc(ticker)}</span>'
        f'<span style="font-size:1.05rem;color:{INK_MUTED};font-weight:500">'
        f'{esc(name)}</span>'
        f'{pill(sector) if sector else ""}{pill(industry) if industry else ""}'
        f'</span></div>', unsafe_allow_html=True)

    # THE PRICE COMES FROM THE QUOTE, and the quote is the last bar of the
    # very series the chart below draws. The header used to read
    # `latest["price"]`, which is the close on the last FISCAL QUARTER END —
    # so the number at the top of the page and the last point on the chart
    # underneath it were routinely months and tens of percent apart.
    price = quote.get("price") or latest.get("price") or snap.get("price")
    price_date = quote.get("as_of") or latest.get("price_as_of")
    change_pct = quote.get("change_pct")

    week_hi, week_lo = snap.get("week52_high"), snap.get("week52_low")

    # Every date the panel depends on, said out loud. A live price against
    # eight-month-old fundamentals is a real condition and the only way a
    # reader can allow for it is if the page admits to it.
    fundamentals_as_of = latest.get("fundamentals_as_of")
    price_note = None
    if price_date:
        stale = _staleness(price_date, warn_days=4)
        price_note = f"close {price_date}" + (f" · {stale}" if stale else "")
        if change_pct is not None:
            price_note = (f"{change_pct:+.2%} · " + price_note)

    st.markdown(stat_strip([
        ("Price", _money(price, decimals=2), price_note),
        ("Market cap", _money(latest.get("market_cap") or snap.get("market_cap"))),
        ("Enterprise value", _money(latest.get("enterprise_value"))),
        ("P/E trailing", _fmt_ratio(latest.get("pe_ttm"), "x"),
         f"on TTM to {fundamentals_as_of}" if fundamentals_as_of else None),
        ("P/E forward", _fmt_ratio(latest.get("pe_forward"), "x")),
        ("52-week range",
         f"{_money(week_lo, decimals=0)} – {_money(week_hi, decimals=0)}"
         if week_lo else "—"),
        ("Composite percentile",
         f"{comp['percentile']:.0f}" if comp is not None
         and not is_missing(comp.get("percentile")) else "—"),
    ]), unsafe_allow_html=True)

    fundamentals_stale = _staleness(fundamentals_as_of, warn_days=135)
    if fundamentals_stale:
        st.caption(
            f"⚠ The newest complete trailing-twelve-month period for this "
            f"name ends **{fundamentals_as_of}** ({fundamentals_stale}). "
            f"Multiples above pair today's price with those figures, which is "
            f"the standard convention — but a company more than one quarter "
            f"late is usually a filing that has not been fetched yet. Run "
            f"`python -m finlake refresh --only filings`.")


def _analyst_view(ticker: str, snap: dict, quote: dict, latest: dict) -> None:
    """Where analysts think the stock goes, and over what horizon.

    THE HORIZON IS THE POINT. A sell-side price target is a TWELVE-MONTH
    target — that is the convention every publishing analyst works to, and it
    is not optional context. The header used to print it as "Analyst target"
    with an upside percentage and no timeframe at all, which reads as "worth
    this now" rather than "worth this in a year", and those imply opposite
    trades.

    The six-month figure is INTERPOLATED, from the twelve-month target, and is
    labelled as such everywhere it appears. Nobody publishes a six-month
    consensus. Interpolating one is still useful — it answers "if the street
    is right and the stock walks there evenly, where is it at the halfway
    point" — but presenting a derived number as a second, independent analyst
    opinion would be an invention, so it says where it came from.

    Geometric, not linear: a path to +20% over a year passes through +9.5% at
    six months, not +10%. Small at these magnitudes, wrong at any magnitude,
    and free to do correctly.
    """
    target = snap.get("target_mean")
    price = quote.get("price") or latest.get("price") or snap.get("price")
    if is_missing(target) or not price:
        return

    total_return = target / price - 1.0
    # (1 + r)^(1/2) - 1 — half the compounded path, not half the return.
    half_return = (1.0 + total_return) ** 0.5 - 1.0 if total_return > -1 else None
    half_target = price * (1.0 + half_return) if half_return is not None else None

    n_analysts = snap.get("n_analysts")
    rec = (snap.get("recommendation_key") or "").replace("_", " ").title()
    rec_mean = snap.get("recommendation_mean")

    st.markdown("###### Where the street thinks it goes")
    st.markdown(stat_strip([
        ("Target · 6 months", _money(half_target, decimals=0),
         f"{half_return:+.1%} from here · interpolated"
         if half_return is not None else None),
        ("Target · 12 months", _money(target, decimals=0),
         f"{total_return:+.1%} from here · consensus"),
        ("Range · 12 months",
         f"{_money(snap.get('target_low'), decimals=0)} – "
         f"{_money(snap.get('target_high'), decimals=0)}"
         if not is_missing(snap.get("target_low")) else "—",
         "low to high of published targets"),
        ("Consensus", rec or "—",
         f"{n_analysts:.0f} analysts" if not is_missing(n_analysts) else None),
        ("Rating scale",
         f"{rec_mean:,.2f}" if not is_missing(rec_mean) else "—",
         "1 = strong buy · 5 = strong sell"),
        ("Next earnings", _next_earnings(ticker) or "—",
         "the date the thesis gets tested"),
    ]), unsafe_allow_html=True)
    st.caption(
        "Published price targets are **12-month** by convention. The 6-month "
        "figure is not a separate consensus — nobody publishes one — it is "
        "the halfway point of that same 12-month path, compounded, shown so "
        "the timeframe is explicit rather than assumed. Targets are what "
        "analysts say, not what the stock will do; the spread between low and "
        "high is usually the more informative number.")


def _next_earnings(ticker: str) -> str | None:
    """The next scheduled earnings date, with days remaining."""
    row = D.next_event(ticker, "earnings")
    if not row:
        return None
    try:
        days = (dt.date.fromisoformat(row) - dt.date.today()).days
    except ValueError:
        return None
    if days < 0:
        return None
    return f"{row} ({days}d)"


def _pick_a_company(table: pd.DataFrame, *, count: int = 12) -> None:
    """What the page shows before a company is chosen.

    A SHORTLIST, NOT AN EMPTY PANEL. The search box alone answers "how do I
    use this" and nothing else; landing on a blank page is the moment a
    reader wonders whether something is broken. The run's own highest-scoring
    names are already computed and are the most likely thing anyone wants
    next, so they are the prompt.
    """
    st.markdown(
        f"<div style='color:{INK_MUTED};font-size:0.9rem;margin:0.5rem 0 1rem'>"
        f"Search above for any of the {len(table):,} names in this run — or "
        f"start with the highest scoring."
        f"</div>", unsafe_allow_html=True)

    scored = table[table["percentile"].notna()].sort_values(
        "percentile", ascending=False).head(count)
    if scored.empty:
        return

    logos = D.logo_uris(scored["ticker"].dropna(), size=28)
    cards = []
    for _, row in scored.iterrows():
        symbol = str(row["ticker"])
        cards.append(
            f'<a class="ls-pick" target="_self" '
            f'href="?page=Company&ticker={esc(symbol)}">'
            f'{logo_mark(symbol, logos.get(symbol.upper()), size=28)}'
            f'<span class="ls-pick-sym">{esc(symbol)}</span>'
            f'<span class="ls-pick-pct">{row["percentile"]:.0f}</span>'
            f'</a>')
    st.markdown(f'<div class="ls-picks">{"".join(cards)}</div>',
                unsafe_allow_html=True)
    st.caption(
        "Ranked by composite percentile in this run. Every ticker anywhere in "
        "the hub is a link to its own page, so this is only a starting point.")


def render(*, db_path: str, run: dict) -> None:
    table = D.composite_table(db_path, run["run_id"])
    if table.empty:
        st.warning("This run has no scored names.")
        return

    tickers = sorted(table["ticker"].dropna().unique())

    # A ticker arriving from the URL (someone clicked a symbol elsewhere) is
    # pre-selected via session state. Streamlit raises if a widget's stored
    # value is not among its options, so a symbol this run never scored is
    # dropped rather than allowed to break the page.
    wanted = st.session_state.get("company_ticker")
    if wanted and wanted not in tickers:
        del st.session_state["company_ticker"]
        st.warning(f"{esc(str(wanted))} is not in this run's universe.")

    sel_l, sel_r = st.columns([2, 3])
    with sel_l:
        # `index=None` is what makes the placeholder show. A selectbox with a
        # default displays that symbol, which tells a reader what is selected
        # but not what the control is FOR — and on a page reached from the
        # tab bar with nothing chosen yet, "A" reads as a company someone
        # picked rather than as the first item in an alphabetical list.
        ticker = st.selectbox(
            "Ticker", tickers, key="company_ticker", index=None,
            placeholder="Search for a ticker…",
            label_visibility="collapsed")

    if not ticker:
        _pick_a_company(table)
        return

    comp = table[table["ticker"] == ticker]
    comp_row = comp.iloc[0] if len(comp) else None

    # A historical run stays historical. Re-pricing a run dated last month at
    # today's close is the same lookahead the whole point-in-time design
    # exists to prevent, so the live quote is only used when the run IS the
    # present.
    current = D.is_current(db_path, run)
    latest = D.latest_ratios(ticker, as_of=run["as_of"], live=current)
    snap = D.market_snapshot(ticker)
    quote = D.quote(ticker) if current else {}
    _header(ticker, latest, snap, comp_row, quote)
    if not current:
        st.caption(
            f"Viewing the run dated **{esc(run['as_of'])}**, so prices and "
            f"multiples are as they stood then — not today's. Switch to the "
            f"newest run for live figures.")
    _analyst_view(ticker, snap, quote, latest)

    # ---- price + business summary -------------------------------------
    left, right = st.columns([3, 2])
    with left:
        st.markdown("###### Price")
        start = (dt.date.fromisoformat(run["as_of"])
                 - dt.timedelta(days=365 * 5)).isoformat()
        px = D.prices(ticker, start, run["as_of"])
        if px.empty:
            st.caption("No cached price history for this name.")
        else:
            st.plotly_chart(charts.price_chart(px), width="stretch",
                            config={"displayModeBar": False})
    with right:
        st.markdown("###### The business")
        summary = snap.get("profile_summary")
        if summary:
            st.markdown(
                f'<div class="ls-card" style="max-height:300px;overflow:auto">'
                f'<div style="font-size:0.85rem;line-height:1.6;color:{INK_MUTED}">'
                f'{esc(summary[:1200])}</div></div>', unsafe_allow_html=True)
        else:
            st.caption("No company profile cached yet.")

    # ---- valuation, profitability, health ------------------------------
    hist = D.ratios(ticker, "ttm", run["as_of"])
    st.markdown("## Valuation")
    st.caption(
        "Each ratio is shown beside this company's own 5-year range. A "
        "multiple without its own history is a number pretending to be "
        "context — 32× means nothing until you know the name has traded "
        "between 21× and 38×.")
    st.markdown(_ratio_table(latest, hist, VALUATION_ROWS),
                unsafe_allow_html=True)
    if is_missing(latest.get("pe_forward")):
        st.caption(
            "Forward P/E is blank: it needs a consensus EPS estimate, and the "
            "SEC publishes none. Run the market loader "
            "(`--market`) to fetch estimates for this name.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("## Profitability")
        st.markdown(_ratio_table(latest, hist, PROFITABILITY_ROWS),
                    unsafe_allow_html=True)
    with c2:
        st.markdown("## Financial health")
        st.markdown(_ratio_table(latest, hist, HEALTH_ROWS),
                    unsafe_allow_html=True)

    # ---- statements ----------------------------------------------------
    st.markdown("## Financial statements")
    s1, s2, s3 = st.columns([1, 1, 2])
    with s1:
        kind_label = st.selectbox(
            "Statement", ["Income statement", "Balance sheet", "Cash flow"],
            key="stmt_kind")
    with s2:
        freq_label = st.selectbox("Period", ["Annual", "Quarterly", "TTM"],
                                  key="stmt_freq")
    with s3:
        periods_back = st.slider("Periods shown", 4, 20, 8, key="stmt_periods")

    kind = {"Income statement": "income", "Balance sheet": "balance",
            "Cash flow": "cash_flow"}[kind_label]
    freq = {"Annual": "annual", "Quarterly": "quarterly", "TTM": "ttm"}[freq_label]
    stmt = D.statement(ticker, kind, freq, periods_back, run["as_of"])
    if stmt.empty:
        st.caption("No statement data resolved for this name and period.")
    else:
        st.markdown(_statement_table(stmt), unsafe_allow_html=True)
        partial = [k for k, v in (stmt.attrs.get("quarters_in_period") or {}).items()
                   if v < 4]
        fy_month = stmt.attrs.get("fiscal_year_end_month")
        fy_name = (dt.date(2000, int(fy_month), 1).strftime("%B")
                   if fy_month else None)
        st.caption(
            "Figures in millions of USD, except per-share amounts. Lines "
            "marked *derived* were computed from other filed lines — hover "
            "for the formula."
            + (f" Annual columns are FISCAL years: this company's ends in "
               f"**{fy_name}**, taken from its own SEC registration rather "
               f"than assumed." if fy_name else "")
            + (f" **{', '.join(str(p) for p in partial)}** "
               f"{'is' if len(partial) == 1 else 'are'} incomplete — marked "
               f"(nQ) in the header — so those totals are not comparable with "
               f"the full years beside them." if partial else ""))

    # ---- estimates and surprises ---------------------------------------
    est = D.estimates(ticker)
    surprises = D.earnings_surprises(ticker)
    if not est.empty or not surprises.empty:
        st.markdown("## What analysts expect")
        e1, e2 = st.columns(2)
        with e1:
            if not est.empty:
                rows = []
                labels = {"0q": "Current quarter", "+1q": "Next quarter",
                          "0y": "Current year", "+1y": "Next year"}
                for _, r in est.iterrows():
                    rows.append({
                        "period": labels.get(r["period"], r["period"]),
                        "metric": "EPS" if r["metric"] == "eps" else "Revenue",
                        "consensus": (f"{r['avg']:,.2f}" if r["metric"] == "eps"
                                      else _money(r["avg"])),
                        "range": (f"{r['low']:,.2f} – {r['high']:,.2f}"
                                  if r["metric"] == "eps"
                                  else f"{_money(r['low'])} – {_money(r['high'])}"),
                        "analysts": r["n_analysts"],
                        "growth": signed(r["growth"], fmt="+.1%")
                        if not is_missing(r["growth"]) else "—",
                    })
                st.markdown(html_table(
                    [{"key": "period", "label": "Period", "cls": ""},
                     {"key": "metric", "label": "Metric"},
                     {"key": "consensus", "label": "Consensus", "cls": "num"},
                     {"key": "range", "label": "Low – high", "cls": "num"},
                     {"key": "analysts", "label": "Analysts", "kind": "num", "fmt": ".0f"},
                     {"key": "growth", "label": "Growth", "kind": "html", "cls": "num"}],
                    rows, max_height=320), unsafe_allow_html=True)
        with e2:
            if not surprises.empty:
                st.markdown("###### Earnings surprise history")
                rows = [{
                    "quarter": r["quarter"],
                    "actual": f"{r['eps_actual']:,.2f}" if not is_missing(r["eps_actual"]) else "—",
                    "estimate": f"{r['eps_estimate']:,.2f}" if not is_missing(r["eps_estimate"]) else "—",
                    "surprise": signed(r["surprise_pct"], fmt="+.1%"),
                } for _, r in surprises.iterrows()]
                st.markdown(html_table(
                    [{"key": "quarter", "label": "Quarter", "cls": ""},
                     {"key": "actual", "label": "Actual", "cls": "num"},
                     {"key": "estimate", "label": "Estimate", "cls": "num"},
                     {"key": "surprise", "label": "Surprise", "kind": "html", "cls": "num"}],
                    rows, max_height=320), unsafe_allow_html=True)

    # ---- positioning: short interest and ownership ----------------------
    if any(snap.get(k) is not None for k in
           ("shares_short", "pct_institutions", "short_pct_float")):
        st.markdown("## Positioning")
        st.caption(
            "Who owns it and who is betting against it. Short interest is "
            "reported twice a month, so it lags; the trend matters more than "
            "the level.")
        prior, now = snap.get("shares_short_prior"), snap.get("shares_short")
        change = ((now / prior - 1) if now and prior else None)
        st.markdown(stat_strip([
            ("Short % of float",
             f"{snap['short_pct_float'] * 100:,.2f}%"
             if snap.get("short_pct_float") else "—"),
            ("Days to cover",
             f"{snap['short_ratio']:,.1f}" if snap.get("short_ratio") else "—"),
            ("Shares short",
             f"{now / 1e6:,.1f}M" if now else "—",
             f"{change:+.1%} vs. prior month" if change is not None else None),
            ("Held by institutions",
             f"{snap['pct_institutions'] * 100:,.1f}%"
             if snap.get("pct_institutions") else "—"),
            ("Held by insiders",
             f"{snap['pct_insiders'] * 100:,.2f}%"
             if snap.get("pct_insiders") else "—"),
        ]), unsafe_allow_html=True)

    # ---- the score ------------------------------------------------------
    st.markdown("## Why it scores what it scores")
    if comp_row is None:
        st.caption("This name was not scored in this run.")
    else:
        sc1, sc2 = st.columns([1, 2])
        with sc1:
            pct = comp_row.get("percentile")
            st.markdown(
                f'<div class="ls-card">'
                f'<div class="ls-card-label">composite percentile</div>'
                f'<div class="ls-score">'
                f'{f"{pct:.0f}" if not is_missing(pct) else "—"}</div>'
                f'<div class="ls-score-sub">{esc(comp_row.get("sector") or "")} · '
                f'{esc(comp_row.get("industry") or "")}</div>'
                f'<div class="ls-score-sub">data as of '
                f'{esc(comp_row.get("data_as_of") or "—")}</div>'
                f'</div>', unsafe_allow_html=True)
        with sc2:
            bdf = D.buckets_for(db_path, run["run_id"], ticker)
            if not bdf.empty:
                st.markdown("###### Bucket contributions to the composite")
                st.plotly_chart(charts.bucket_bar_chart(bdf), width="stretch",
                                config={"displayModeBar": False})

        bdf = D.buckets_for(db_path, run["run_id"], ticker)
        if not bdf.empty:
            bdf = bdf.set_index("bucket").reindex(list(BUCKET_NAMES)).reset_index()
            rows = []
            for _, r in bdf.iterrows():
                flags = []
                if r.get("low_confidence"):
                    flags.append("low coverage")
                if r.get("was_capped"):
                    flags.append("capped")
                rows.append({
                    "bucket": str(r["bucket"]).replace("_", " ").title(),
                    "subscore": signed(r.get("subscore"), fmt="+.3f"),
                    "sort_sub": r.get("subscore"),
                    "coverage": r.get("coverage"),
                    "weight": r.get("weight_used"),
                    "contribution": signed(r.get("composite_contribution"), fmt="+.4f"),
                    "bar": score_bar(r.get("composite_contribution"), scale=0.5),
                    "flags": ", ".join(flags) or None,
                })
            st.markdown(html_table(
                [{"key": "bucket", "label": "Bucket", "cls": ""},
                 {"key": "subscore", "label": "Subscore", "kind": "html",
                  "cls": "num", "sort": "sort_sub"},
                 {"key": "coverage", "label": "Coverage", "kind": "num", "fmt": ".0%"},
                 {"key": "weight", "label": "Weight used", "kind": "num", "fmt": ".3f"},
                 {"key": "contribution", "label": "Contribution", "kind": "html", "cls": "num"},
                 {"key": "bar", "label": "", "kind": "html"},
                 {"key": "flags", "label": "Flags"}],
                rows, max_height=360), unsafe_allow_html=True)

        with st.expander("Every metric behind these buckets"):
            mdf = D.metrics_for(db_path, run["run_id"], ticker)
            if mdf.empty:
                st.caption("No metric detail stored for this name.")
            else:
                rows = [{
                    "bucket": str(r.get("bucket")).replace("_", " ").title(),
                    "metric": str(r.get("metric")).replace("_", " "),
                    "raw_value": r.get("raw_value"),
                    "z": signed(r.get("z"), fmt="+.2f"),
                    "sort_z": r.get("z"),
                    "peer_level": r.get("peer_level"),
                    "peers": r.get("peer_group_size"),
                    "substitution": r.get("substitution"),
                } for _, r in mdf.iterrows()]
                st.markdown(html_table(
                    [{"key": "bucket", "label": "Bucket"},
                     {"key": "metric", "label": "Metric", "cls": ""},
                     {"key": "raw_value", "label": "Raw", "kind": "num", "fmt": ",.4f"},
                     {"key": "z", "label": "z", "kind": "html", "cls": "num",
                      "sort": "sort_z"},
                     {"key": "peer_level", "label": "Compared against"},
                     {"key": "peers", "label": "Peers", "kind": "num", "fmt": ".0f"},
                     {"key": "substitution", "label": "Substitution / flag"}],
                    rows, max_height=480), unsafe_allow_html=True)
                st.caption(
                    f"Every number here traces back to a filing. "
                    f"`python -m lodestar audit {ticker} roic` prints the full "
                    f"chain: raw facts with their filed dates, the "
                    f"intermediates, the peer statistics, and the final z.")

    # ---- news ------------------------------------------------------------
    st.markdown("## News")
    articles = D.news(ticker, 60, run["as_of"])
    if articles.empty:
        st.caption("No news cached for this name in the last 60 days.")
    else:
        scored = articles["sentiment"].dropna()
        n_pos = int((scored > TONE_NEUTRAL_BAND).sum())
        n_neg = int((scored < -TONE_NEUTRAL_BAND).sum())
        n_neu = int(len(scored) - n_pos - n_neg)
        st.markdown(stat_strip([
            ("Articles (60d)", f"{len(articles):,}"),
            ("Overall tone",
             tone_chip(scored.mean()) if len(scored) else tone_chip(None),
             f"across {len(scored):,} scored"),
            ("Positive", f"{n_pos:,}"),
            ("Neutral", f"{n_neu:,}"),
            ("Negative", f"{n_neg:,}"),
            ("No signal", f"{len(articles) - len(scored):,}"),
            ("Sources", f"{articles['source'].nunique()}"),
        ]), unsafe_allow_html=True)

        rows = []
        for _, a in articles.head(40).iterrows():
            title = esc(a["title"])
            if a.get("url"):
                title = (f'<a href="{esc(a["url"])}" target="_blank" '
                         f'style="color:inherit">{title}</a>')
            rows.append({
                "date": a["published_at"],
                "title": title,
                "event": _event_label(a.get("event_class")),
                "tone": tone_chip(a["sentiment"],
                                  scored_text=a.get("summary")),
                "sort_tone": a["sentiment"],
                "source": _source_label(a.get("publisher"), a.get("source")),
            })
        st.markdown(html_table(
            [{"key": "date", "label": "Date", "cls": "num"},
             {"key": "title", "label": "Headline", "kind": "html", "cls": ""},
             {"key": "source", "label": "Source", "kind": "html"},
             {"key": "event", "label": "Type"},
             {"key": "tone", "label": "Tone", "kind": "html", "cls": "num",
              "sort": "sort_tone"}],
            rows, max_height=520), unsafe_allow_html=True)
        st.caption(
            "Tone is a Loughran-McDonald financial-lexicon score plus phrase "
            "patterns, not a language model — it reads words and idioms from "
            "a dictionary built for financial text, and scales the result by "
            "how much sentiment vocabulary the headline actually carried. "
            "**no signal** means none was found at all, which is different "
            "from neutral. SEC filings are read from their 8-K item codes "
            "instead: most items are procedural and carry no tone.")
