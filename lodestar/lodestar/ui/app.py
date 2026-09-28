"""The hub — Streamlit entry point.

Launch with `python -m lodestar ui`, or directly:
`streamlit run lodestar/ui/app.py`.

STRUCTURE. One router here, one module per page under `views/`. Navigation is
a single row of tabs across the top: anything in the product is reachable in
one click, and the active tab always says which section you are in. Depth
lives inside a page (expanders, dropdowns), never in the navigation.

THE TABS ARE LINKS, NOT A WIDGET. Each is an anchor to `?page=...`, the same
mechanism every clickable ticker already uses, which means the URL is the
state: a section is linkable, bookmarkable, and survives a refresh. It also
means only the active page renders. `st.tabs` would look the same and build
every section's contents on every run — across nine pages including a
500-row screener, each click would pay for all of them.

THE UI READS; IT NEVER COMPUTES. Streamlit re-executes this entire script on
every interaction — every dropdown change, every checkbox. Scoring 500 names
takes minutes, so a Run button here would re-rank the universe on a filter
click and hang the browser. Scoring happens in `python -m lodestar run`; this
app loads what that produced. `ui/data.py` holds every read, cached.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_LODESTAR_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_LODESTAR_ROOT))

# The finlake PACKAGE root, ahead of everything else.
#
# Streamlit runs with the launch directory on sys.path, and that directory
# contains a bare `finlake/` FOLDER (the sibling repo). Python treats a
# folder with no __init__.py as a namespace package, so `import finlake`
# resolved to an empty stub with no __file__ and none of the API — and every
# finlake-backed panel in the hub (statements, ratios, news, macro, market
# data) silently rendered as "no data cached".
#
# Putting the real package's parent first makes `import finlake` find
# finlake/finlake/__init__.py regardless of where the app was launched from.
_FINLAKE_ROOT = _LODESTAR_ROOT.parent / "finlake"
if (_FINLAKE_ROOT / "finlake" / "__init__.py").exists():
    sys.path.insert(0, str(_FINLAKE_ROOT))

from lodestar import persistence  # noqa: E402
from lodestar.ui import data as D  # noqa: E402
from lodestar.ui.theme import (  # noqa: E402
    INK_MUTED, empty_state, esc, inject_css, nav_tabs,
)

st.set_page_config(page_title="finlake — equity research hub",
                   layout="wide", page_icon="📊",
                   initial_sidebar_state="collapsed")
st.markdown(inject_css(), unsafe_allow_html=True)

# Page label -> (module attribute, one-line description for the caption).
PAGES: dict[str, str] = {
    "Overview": "the market right now, and what moved",
    "Company": "one name in full — statements, valuation, news, score",
    "Screener": "rank and filter the whole universe",
    "Buy / sell ratings": "ratings for every stock, industry, and sector",
    "Compare": "two to six names side by side",
    "News": "everything published across the universe",
    "Macro": "rates, inflation, growth, and the yield curve",
    "How it works": "exactly how the scores and ratings are calculated",
    "Data health": "what is complete, what is missing, what disagrees",
}


def _db_path() -> Path:
    if "--db" in sys.argv:
        return Path(sys.argv[sys.argv.index("--db") + 1])
    return persistence.DEFAULT_DB_PATH


def _no_run_yet(db_path: Path) -> None:
    """What a first-time user sees. Names the exact command to run.

    An empty state that only says "no data" leaves someone stuck; this is the
    one screen where being specific matters most.
    """
    st.markdown(
        '<div class="ls-brandbar"><span class="ls-wordmark">fin<span>lake</span>'
        '</span></div>', unsafe_allow_html=True)
    st.markdown(empty_state(
        "No scoring run on record",
        "This app reads completed runs from disk — it never scores anything "
        "itself, because Streamlit re-runs the whole page on every click and "
        "ranking 500 companies takes minutes. Build the data first, then "
        "score it.",
    ), unsafe_allow_html=True)
    st.markdown("###### 1 · Build the data (once, then it stays current)")
    st.code("python finlake/scripts/build.py --universe-file "
            "finlake/universe/sp500_ndx.csv --all", language="bash")
    st.markdown("###### 2 · Score the universe")
    st.code("python -m lodestar run", language="bash")
    st.markdown("###### 3 · Keep it up to date in the background")
    st.code("python -m finlake refresh --daemon", language="bash")
    st.caption(f"Looked for a runs database at: {db_path}")

    cov = D.coverage_summary()
    if cov.get("companies_with_facts"):
        st.caption(
            f"The data cache already holds "
            f"{cov['companies_with_facts']:,} companies and "
            f"{cov.get('facts', 0):,} facts — only the scoring run is missing.")


def _apply_query_params() -> None:
    """Let a URL drive the page and the selected ticker.

    This is what makes every ticker in the product clickable: a symbol in any
    table is an ordinary link to `?page=Company&ticker=XYZ`, and this reads it
    back. Widgets take their value from session state, so the state has to be
    set BEFORE the selectbox is constructed — after that Streamlit owns the
    key and assigning to it raises.

    Applied once per distinct URL. Without that guard the query string would
    keep overriding the dropdowns on every rerun, so a user who clicked
    through to a company could never then change the page.
    """
    params = st.query_params
    page, ticker = params.get("page"), params.get("ticker")
    stamp = f"{page}|{ticker}"
    if stamp == st.session_state.get("_applied_query"):
        return
    st.session_state["_applied_query"] = stamp

    if page in PAGES:
        st.session_state["active_page"] = page
    if ticker:
        st.session_state["company_ticker"] = str(ticker).strip().upper()

    # A sector arriving from a clicked chip pre-filters the screener.
    sector = params.get("sector")
    if sector:
        st.session_state["scr_sector"] = [str(sector)]


def _active_page() -> str:
    """Which section to render.

    The URL is the source of truth — the tab bar is a row of links, so a
    click IS a new query string. Session state is the fallback for a reload
    with no parameters, and Overview is the default, which is where the hub
    should open.
    """
    page = st.query_params.get("page")
    if page in PAGES:
        return page
    remembered = st.session_state.get("active_page")
    return remembered if remembered in PAGES else "Overview"


def main() -> None:
    db_path = _db_path()
    runs = D.list_runs(str(db_path))
    if not runs:
        _no_run_yet(db_path)
        return

    _apply_query_params()

    # ---- header: identity and run selector ----------------------------
    head_l, head_r = st.columns([3, 2])
    with head_l:
        st.markdown(
            '<div class="ls-brandbar" style="border-bottom:none;'
            'padding-bottom:0">'
            '<span class="ls-wordmark">fin<span>lake</span></span>'
            '<span class="ls-context">equity research hub</span>'
            '</div>', unsafe_allow_html=True)
    with head_r:
        labels = {f"{r['as_of']}  ·  {r['universe_size']} names": r
                  for r in runs}
        run = labels[st.selectbox("Run", list(labels), key="active_run",
                                   label_visibility="collapsed")]

    page = _active_page()

    # ---- the section bar ----------------------------------------------
    # The selected ticker rides along, so switching to Company from anywhere
    # lands on the company already in hand rather than on an empty page.
    carry = {}
    held = st.session_state.get("company_ticker")
    if held:
        carry["ticker"] = held
    st.markdown(nav_tabs(list(PAGES), page, extra_params=carry),
                unsafe_allow_html=True)

    # Freshness, stated rather than implied. A hub that looks live but is
    # reading a week-old cache is worse than one that says how old it is.
    #
    # `freshness()` rather than `coverage_summary()`: the router only ever
    # wanted this one field, and the full summary counts rows in a 27-million
    # row table — every page in the hub used to sit behind two minutes of
    # scanning to print a timestamp.
    fetched = (D.freshness() or "")[:16].replace("T", " ")
    freshness = f" · data last fetched {fetched}" if fetched else ""

    st.markdown(
        f'<div class="ls-context" style="margin:-0.75rem 0 1.25rem 0">'
        f'{esc(PAGES[page])} · as of {esc(run["as_of"])} · '
        f'{run["universe_size"]} names · config {esc(run["config_hash"])}'
        f'{esc(freshness)}</div>', unsafe_allow_html=True)

    # A broken data layer is not a coverage gap. Without this banner every
    # panel just says "no data cached" and the hub looks empty rather than
    # broken — which is a much harder problem to notice or diagnose.
    layer_error = D.data_layer_error()
    if layer_error:
        st.error(f"**The data layer is not available**, so financial "
                 f"statements, ratios, news and macro will all be blank. "
                 f"This is not missing data — it is a broken import.\n\n"
                 f"{layer_error}")

    # ---- dispatch -----------------------------------------------------
    # Imported inside the branch so one page failing to import cannot take
    # the whole app down with it, and so a page nobody opened costs nothing.
    ctx = {"db_path": str(db_path), "run": run}
    try:
        if page == "Overview":
            from lodestar.ui.views import overview
            overview.render(**ctx)
        elif page == "Company":
            from lodestar.ui.views import company
            company.render(**ctx)
        elif page == "Screener":
            from lodestar.ui.views import screener
            screener.render(**ctx)
        elif page == "Buy / sell ratings":
            from lodestar.ui.views import ratings
            ratings.render(**ctx)
        elif page == "Compare":
            from lodestar.ui.views import compare
            compare.render(**ctx)
        elif page == "News":
            from lodestar.ui.views import news
            news.render(**ctx)
        elif page == "Macro":
            from lodestar.ui.views import macro
            macro.render(**ctx)
        elif page == "How it works":
            from lodestar.ui.views import methodology
            methodology.render(**ctx)
        elif page == "Data health":
            from lodestar.ui.views import health
            health.render(**ctx)
    except Exception as exc:  # noqa: BLE001
        # A page that raises shows a contained error rather than a blank
        # screen, and the rest of the hub stays usable.
        st.error(f"This page hit an error: {type(exc).__name__}: {exc}")
        with st.expander("Details"):
            import traceback
            st.code(traceback.format_exc())

    st.markdown(
        f'<div style="margin-top:2rem;padding-top:1rem;border-top:1px solid '
        f'#22375c;color:{INK_MUTED};font-size:0.75rem">'
        f'Screening tool, not investment advice. Every number traces to a '
        f'filing — <code>python -m lodestar audit TICKER METRIC</code> prints '
        f'the full chain. Estimates are third-party consensus; news sentiment '
        f'is a lexicon score, not a model.</div>',
        unsafe_allow_html=True)


main()
