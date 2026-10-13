"""Compare — two to six names side by side.

Every figure is pulled through the same accessors the Company page uses, so a
number here always matches the number there. Comparison is where an
inconsistency between two code paths shows up as a contradiction on screen,
so there is only one code path.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...config import BUCKET_NAMES
from .. import charts
from .. import data as D
from ..theme import esc, html_table, is_missing, logo_mark, score_bar, signed

ROWS: list[tuple[str, str, str]] = [
    ("price", "Price", "$"),
    ("market_cap", "Market cap", "$$"),
    ("enterprise_value", "Enterprise value", "$$"),
    ("pe_ttm", "P/E (trailing)", "x"),
    ("pe_forward", "P/E (forward)", "x"),
    ("peg", "PEG", "x"),
    ("ps", "P/S", "x"),
    ("pb", "P/B", "x"),
    ("ev_ebitda", "EV/EBITDA", "x"),
    ("fcf_yield", "FCF yield", "%"),
    ("dividend_yield", "Dividend yield", "%"),
    ("gross_margin", "Gross margin", "%"),
    ("operating_margin", "Operating margin", "%"),
    ("net_margin", "Net margin", "%"),
    ("roe", "Return on equity", "%"),
    ("roic", "Return on invested capital", "%"),
    ("current_ratio", "Current ratio", "x"),
    ("debt_to_equity", "Debt / equity", "x"),
    ("interest_coverage", "Interest coverage", "x"),
    ("altman_z", "Altman Z-score", "x"),
]


def _fmt(value, unit: str) -> str:
    if is_missing(value):
        return "—"
    if unit == "%":
        return f"{value * 100:,.1f}%"
    if unit == "x":
        return f"{value:,.2f}×"
    if unit == "$":
        return f"${value:,.2f}"
    if unit == "$$":
        v = float(value)
        for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(v) >= cutoff:
                return f"${v / cutoff:,.1f}{suffix}"
        return f"${v:,.0f}"
    return f"{value:,.2f}"


def render(*, db_path: str, run: dict) -> None:
    table = D.composite_table(db_path, run["run_id"])
    if table.empty:
        st.warning("This run has no scored names.")
        return

    tickers = sorted(table["ticker"].dropna().unique())
    default = [t for t in ("AAPL", "MSFT", "NVDA") if t in tickers][:3]
    picked = st.multiselect(
        "Compare (2–6 names)", tickers, default=default, max_selections=6,
        key="cmp_tickers")
    if len(picked) < 2:
        st.info("Pick at least two names to compare.")
        return

    # Live only when the run IS the present — a historical run re-priced at
    # today's close is the lookahead the point-in-time design exists to
    # prevent. Same rule as the Company page, so the two cannot disagree.
    current = D.is_current(db_path, run)
    latest = {t: D.latest_ratios(t, as_of=run["as_of"], live=current)
              for t in picked}
    snaps = {t: D.market_snapshot(t) for t in picked}

    # ---- identity row ---------------------------------------------------
    cols = st.columns(len(picked))
    for col, ticker in zip(cols, picked):
        with col:
            row = table[table["ticker"] == ticker]
            pct = row["percentile"].iloc[0] if len(row) else None
            snap = snaps[ticker]
            st.markdown(
                f'<div class="ls-card">'
                f'<div style="display:flex;align-items:center;gap:8px;'
                f'margin-bottom:2px">'
                f'{logo_mark(ticker, D.logo_uri(ticker, size=22), size=22)}'
                f'<span class="ls-card-label" style="margin:0">'
                f'{esc(ticker)}</span></div>'
                f'<div class="ls-score" style="font-size:2.25rem">'
                f'{f"{pct:.0f}" if not is_missing(pct) else "—"}</div>'
                f'<div class="ls-score-sub">composite percentile</div>'
                f'<div class="ls-card-note">'
                f'{esc(snap.get("profile_industry") or "")}</div>'
                f'</div>', unsafe_allow_html=True)

    # ---- the comparison table -------------------------------------------
    rows = []
    for key, label, unit in ROWS:
        row = {"metric": label}
        values = {t: latest[t].get(key) for t in picked}
        present = [v for v in values.values() if not is_missing(v)]
        # Mark the best value per row, where "best" is unambiguous. For a
        # valuation multiple lower is better; for a margin or return higher
        # is. Leaving it unmarked where direction is arguable (PEG on
        # negative growth, debt for a REIT) is better than asserting one.
        lower_better = key in {"pe_ttm", "pe_forward", "peg", "ps", "pb",
                               "ev_ebitda", "debt_to_equity"}
        best = None
        if len(present) > 1:
            best = min(present) if lower_better else max(present)
        for t in picked:
            value = values[t]
            text = _fmt(value, unit)
            if best is not None and not is_missing(value) and value == best:
                text = f'<b style="color:#2dd4bf">{text}</b>'
            row[t] = text
        rows.append(row)

    columns = [{"key": "metric", "label": "", "cls": ""}]
    columns += [{"key": t, "label": t, "kind": "html", "cls": "num"}
                for t in picked]
    st.markdown(html_table(columns, rows, max_height=760, sortable=False),
                unsafe_allow_html=True)
    st.caption(
        "The stronger value in each row is highlighted where the direction is "
        "unambiguous — lower is better for a valuation multiple, higher for a "
        "margin or a return. Rows where it depends on context are left "
        "unmarked rather than given a false verdict.")

    # ---- bucket scores side by side --------------------------------------
    st.markdown("## Where the scores differ")
    bucket_rows = []
    for bucket in BUCKET_NAMES:
        row = {"bucket": bucket.replace("_", " ").title()}
        for t in picked:
            sub = table[table["ticker"] == t]
            value = sub[f"{bucket}_sub"].iloc[0] if len(sub) else None
            row[t] = signed(value, fmt="+.2f")
            row[f"bar_{t}"] = score_bar(value)
        bucket_rows.append(row)

    columns = [{"key": "bucket", "label": "Bucket", "cls": ""}]
    for t in picked:
        columns.append({"key": t, "label": t, "kind": "html", "cls": "num"})
        columns.append({"key": f"bar_{t}", "label": "", "kind": "html"})
    st.markdown(html_table(columns, bucket_rows, max_height=400, sortable=False),
                unsafe_allow_html=True)
    st.caption(
        "Subscores are in standard deviations within each name's own peer "
        "group — so a +1.0 for a bank and a +1.0 for a chipmaker both mean "
        "'one standard deviation better than its own peers', not that the two "
        "are equally good businesses.")

    # ---- revenue history -------------------------------------------------
    st.markdown("## Revenue, five fiscal years")
    frame = {}
    for t in picked:
        stmt = D.statement(t, "income", "annual", 7, run["as_of"])
        if stmt.empty or "revenue" not in stmt.index:
            continue
        period_cols = [c for c in stmt.columns
                       if c not in ("label", "indent", "is_subtotal",
                                    "is_derived", "formula")]
        # INCOMPLETE YEARS ARE DROPPED, not plotted. The newest fiscal year is
        # still running for most companies and is running by a different
        # amount for each — Apple's FY2026 held three quarters while
        # Microsoft's held four. Plotted together, Apple's line falls off a
        # cliff in the final period and the chart reads as a collapse in the
        # business rather than a difference in year-ends.
        quarters = stmt.attrs.get("quarters_in_period") or {}
        complete = [c for c in period_cols if quarters.get(str(c), 4) >= 4]
        if not complete:
            continue
        frame[t] = pd.Series(
            [stmt.loc["revenue", c] for c in complete],
            index=[str(c) for c in complete])
    if frame:
        st.plotly_chart(charts.multi_line(pd.DataFrame(frame)),
                        width="stretch", config={"displayModeBar": False})
        st.caption(
            "Fiscal years, each company's own — so the x-axis is a label, not "
            "a date. Years still in progress are omitted rather than drawn "
            "short, which would read as a collapse in the business.")
