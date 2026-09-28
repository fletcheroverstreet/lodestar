"""Data health — what is complete, what is missing, what disagrees.

Shown in the product rather than kept for debugging. A hub built on a
partially-loaded cache should say so plainly: a blank chart with no
explanation reads as a broken app, while "412 of 503 names have fundamentals"
reads as a build still in progress. And a number that two independent sources
disagree about is worth seeing before you act on it.

The rule the checks follow: a failed check REPORTS, it never repairs.
"""

from __future__ import annotations

import streamlit as st

from .. import data as D
from ..theme import esc, html_table, stat_strip

SEVERITY_COLORS = {
    "critical": "critical", "serious": "serious",
    "warning": "warning", "info": "good",
}


@st.cache_data(ttl=900, show_spinner=False)
def _check(tickers: tuple[str, ...]) -> list[dict]:
    """Run the quality checks. Cached — this reads every statement."""
    from finlake import quality

    out = []
    for ticker in tickers:
        findings = quality.check_ticker(ticker)
        out.append({
            "ticker": ticker,
            "score": quality.score_ticker(findings),
            "critical": sum(1 for f in findings if f.severity == "critical"),
            "serious": sum(1 for f in findings if f.severity == "serious"),
            "warning": sum(1 for f in findings if f.severity == "warning"),
            "findings": [
                {"severity": f.severity, "check": f.check,
                 "period": f.period, "message": f.message}
                for f in findings],
        })
    return out


def render(*, db_path: str, run: dict) -> None:
    cov = D.coverage_summary()
    st.markdown("## What the cache holds")
    st.markdown(stat_strip([
        ("Companies", f"{cov.get('companies_with_facts') or 0:,}"),
        ("Financial facts", f"{cov.get('facts') or 0:,}"),
        ("Filings indexed", f"{cov.get('filings') or 0:,}"),
        ("News articles", f"{cov.get('news_articles') or 0:,}"),
        ("Macro observations", f"{cov.get('macro_observations') or 0:,}"),
        ("Market snapshots", f"{cov.get('market_snapshots') or 0:,}"),
    ]), unsafe_allow_html=True)
    if cov.get("last_fetch"):
        st.caption(f"Most recent fetch: {esc(cov['last_fetch'])}")

    # ---- refresh status -------------------------------------------------
    st.markdown("## Is it current?")
    try:
        from finlake import refresh, store

        with store.session() as conn:
            status = refresh.status(conn)
        rows = [{
            "task": r["task"],
            "every": r["every"],
            "last_success": r["last_success"] or "never",
            "status": r["last_status"],
            "due": "yes" if r["due"] else "",
            "note": r["note"],
        } for r in status]
        st.markdown(html_table(
            [{"key": "task", "label": "Task", "cls": ""},
             {"key": "every", "label": "Every", "cls": "num"},
             {"key": "last_success", "label": "Last success", "cls": "num"},
             {"key": "status", "label": "Status"},
             {"key": "due", "label": "Due now"},
             {"key": "note", "label": "What it does"}],
            rows, max_height=320), unsafe_allow_html=True)
        st.caption(
            "Start the background refresher with "
            "`python -m finlake refresh --daemon`. It only updates what is "
            "actually due, and after the machine has been off it works out "
            "what it missed and catches up rather than waiting a full "
            "interval.")
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Refresh status unavailable: {exc}")

    # ---- per-name quality ------------------------------------------------
    st.markdown("## Does the data check out?")
    st.caption(
        "Accounting identities the filer cannot violate — assets equal "
        "liabilities plus equity, the cash-flow sections sum to the reported "
        "change in cash, share counts are positive. A violation is therefore "
        "OUR parsing error, not their reporting, which makes these the "
        "sharpest checks available. **Nothing here is silently corrected.** A "
        "failed check is reported so the number on screen always matches the "
        "filing it came from.")

    table = D.composite_table(db_path, run["run_id"])
    tickers = sorted(table["ticker"].dropna().unique()) if not table.empty else []
    if not tickers:
        st.info("No names in this run to check.")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        sample = st.selectbox(
            "How many to check", [25, 50, 100, 250, "All"], index=0,
            key="health_n",
            help="Each check reads a company's full statement history, so "
                 "checking every name takes a minute or two.")
    n = len(tickers) if sample == "All" else int(sample)

    with st.spinner(f"Checking {n} companies..."):
        results = _check(tuple(tickers[:n]))

    scores = [r["score"] for r in results]
    st.markdown(stat_strip([
        ("Checked", f"{len(results)}"),
        ("Median score", f"{sorted(scores)[len(scores) // 2]:.0f}/100"),
        ("Clean", f"{sum(1 for s in scores if s == 100)}"),
        ("With critical findings",
         f"{sum(1 for r in results if r['critical'])}"),
        ("With serious findings",
         f"{sum(1 for r in results if r['serious'])}"),
    ]), unsafe_allow_html=True)

    rows = [{
        "ticker": r["ticker"],
        "score": r["score"],
        "critical": r["critical"],
        "serious": r["serious"],
        "warning": r["warning"],
        "top_finding": (r["findings"][0]["message"][:90]
                        if r["findings"] else "clean"),
    } for r in sorted(results, key=lambda x: x["score"])]
    st.markdown(html_table(
        [{"key": "ticker", "label": "Ticker", "kind": "ticker"},
         {"key": "score", "label": "Score", "kind": "num", "fmt": ".0f"},
         {"key": "critical", "label": "Critical", "kind": "num", "fmt": ".0f"},
         {"key": "serious", "label": "Serious", "kind": "num", "fmt": ".0f"},
         {"key": "warning", "label": "Warning", "kind": "num", "fmt": ".0f"},
         {"key": "top_finding", "label": "Most severe finding", "cls": ""}],
        rows, max_height=520), unsafe_allow_html=True)

    worst = [r for r in results if r["findings"]]
    if worst:
        with st.expander("Every finding, in full"):
            for r in sorted(worst, key=lambda x: x["score"])[:40]:
                st.markdown(f"**{esc(r['ticker'])}** — {r['score']:.0f}/100")
                for f in r["findings"][:8]:
                    where = f" [{f['period']}]" if f["period"] else ""
                    st.markdown(
                        f"<div style='font-size:0.82rem;margin-left:1rem'>"
                        f"<code>{esc(f['severity'])}</code>{esc(where)} "
                        f"{esc(f['message'])}</div>", unsafe_allow_html=True)
