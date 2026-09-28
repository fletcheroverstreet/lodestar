"""Everything the UI reads, in one place, cached.

Two rules this module exists to enforce.

**The UI reads; it never computes.** Streamlit re-executes the entire script
on every interaction — every dropdown change, every checkbox. A scoring run
over 500 names takes minutes, so anything that triggered one from the UI
would hang the browser on a filter click. Scoring happens in
`python -m lodestar run`; this module loads what that produced.

**Every read is cached, and the cache key is explicit.** Without caching,
switching one dropdown re-reads the whole runs database and re-derives every
statement on screen. `st.cache_data` keys on the function arguments, so every
accessor here takes plain, hashable arguments (strings, ints) and never a
connection or a DataFrame.

The accessors return empty frames rather than raising when data is missing.
A hub is used while its cache is still filling — a half-built universe should
render the names it has with honest gaps, not a stack trace.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

from .. import persistence
from ..adapter import check_finlake_version

# Cache lifetimes. Fundamentals change when a company files -- quarterly at
# most -- so they can sit for a long time. Quotes are refreshed by the
# background loader and should not be pinned for more than a minute or the
# "live" panels quietly go stale behind a cache that looks fine.
TTL_SLOW = 3600      # statements, ratios, run tables
TTL_FAST = 30        # quotes and anything user-visible as "live"


class DataLayerUnavailable(RuntimeError):
    """finlake could not be imported properly — not merely missing data.

    Kept distinct from an empty result on purpose. Every accessor below
    degrades to an empty frame when a *ticker* has no data, which is the
    right behaviour for a cache that is still filling. But a broken data
    LAYER is not a coverage gap, and rendering it as one produces a hub that
    calmly reports "no data cached" on every panel while the cache is full.

    That happened: Streamlit runs with its launch directory on sys.path, that
    directory contains a bare `finlake/` folder, Python treated it as a
    namespace package, and `import finlake` returned an empty stub. Statements,
    ratios, news, macro and market data all went blank at once — with 15
    million facts sitting on disk.
    """


def _finlake():
    """Imported lazily, and checked for the shadowing failure above.

    Deliberately not a module-level import: Streamlit imports this file on
    every rerun, and a version mismatch should surface as a clear message in
    the app rather than an import-time crash that shows a blank page.
    """
    import finlake

    # A namespace-package stub has no __file__ and none of the API. Checking
    # for a function we actually use catches both that and a partial install.
    if getattr(finlake, "__file__", None) is None or not hasattr(finlake, "macro"):
        raise DataLayerUnavailable(
            "`import finlake` resolved to an empty namespace package rather "
            "than the real library — usually a bare `finlake/` directory "
            "shadowing it on sys.path. Launch the app via "
            "`python -m lodestar ui`, or add the finlake repo root to "
            "PYTHONPATH."
        )
    check_finlake_version()
    return finlake


def data_layer_error() -> str | None:
    """The data layer's problem, if it has one — for the UI to display.

    Surfaced rather than swallowed: 'the library is broken' and 'this company
    has no filings' must never look the same on screen.
    """
    try:
        _finlake()
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


# ---------------------------------------------------------------------------
# Runs (lodestar's own output)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def list_runs(db_path: str) -> list[dict]:
    if not Path(db_path).exists():
        return []
    with persistence.connect(Path(db_path)) as conn:
        return [
            {"run_id": r.run_id, "as_of": r.as_of, "config_hash": r.config_hash,
             "universe_size": r.universe_size, "universe_source": r.universe_source}
            for r in persistence.list_runs(conn)
        ]


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def composite_table(db_path: str, run_id: str) -> pd.DataFrame:
    """The ranked table for one run — the backbone of the screener."""
    with persistence.connect(Path(db_path)) as conn:
        return persistence.load_composite_table(conn, run_id)


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def buckets_for(db_path: str, run_id: str, ticker: str) -> pd.DataFrame:
    with persistence.connect(Path(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM run_buckets WHERE run_id = ? AND ticker = ?",
            (run_id, ticker),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def metrics_for(db_path: str, run_id: str, ticker: str) -> pd.DataFrame:
    with persistence.connect(Path(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM run_metrics WHERE run_id = ? AND ticker = ? "
            "ORDER BY bucket, metric", (run_id, ticker),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def ratios_snapshot(db_path: str, run_id: str) -> pd.DataFrame:
    """Valuation ratios for every name in a run, as snapshotted by the run.

    The screener's backbone. Reading a stored table instead of re-deriving
    500 names is the difference between a page that draws instantly and one
    that takes 77 seconds.
    """
    with persistence.connect(Path(db_path)) as conn:
        try:
            return persistence.load_ratios(conn, run_id)
        except Exception:
            return pd.DataFrame()


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def diff_runs(db_path: str, earlier: str, later: str) -> pd.DataFrame:
    with persistence.connect(Path(db_path)) as conn:
        return persistence.diff_runs(conn, earlier, later)


# ---------------------------------------------------------------------------
# Company data (finlake)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def statement(ticker: str, kind: str, freq: str, periods_back: int,
              as_of: str | None = None) -> pd.DataFrame:
    """One assembled financial statement, ready to render."""
    try:
        return _finlake().statement(
            ticker, kind, freq=freq, periods_back=periods_back, as_of=as_of)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def ratios(ticker: str, freq: str = "ttm", as_of: str | None = None) -> pd.DataFrame:
    try:
        return _finlake().ratio_history(ticker, freq=freq, as_of=as_of)
    except Exception:
        return pd.DataFrame()


def is_current(db_path: str, run: dict) -> bool:
    """True when this run is the newest on record — the hub's "now" view.

    THE LIVE PRICE MUST NOT LEAK INTO A HISTORICAL RUN. Re-pricing a run
    dated last month at today's close is exactly the lookahead the `filed`
    column and the whole point-in-time design exist to prevent: it shows what
    the market believes now against what was knowable then, and the resulting
    "backtest" is meaningless in the flattering direction. So `live` is a
    property of the RUN, not a global setting.

    THE TEST IS "IS THIS THE NEWEST RUN", NOT "IS IT DATED TODAY". The first
    version compared the run's date against the wall clock, which is wrong in
    the ordinary case rather than an edge case: you score overnight and read
    it the next morning, so the newest run is almost always dated yesterday.
    That silently switched the whole hub back to quarter-end pricing — the
    exact bug this was all built to fix — and Microsoft's header returned to
    $373 the day after a correct run.

    A run only becomes historical when a NEWER one exists and you deliberately
    select the older one from the run picker. Which is the real question being
    asked, and it does not depend on the clock at all.
    """
    runs = list_runs(db_path)
    if not runs:
        return True
    return str(run.get("run_id")) == str(runs[0].get("run_id"))


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def latest_ratios(ticker: str, as_of: str | None = None,
                  live: bool = True) -> dict:
    """Newest ratios, priced AT THE LATEST CLOSE rather than at quarter end.

    `live=True` is the whole point. Without it every price-based figure on the
    company page — the headline price, market cap, enterprise value, P/E, P/S,
    P/B, EV/EBITDA, the yields — is computed against the close on the last
    fiscal quarter end, because that is the only price a point-in-time ratio
    history ever pairs with the newest fundamentals row.

    That is correct for a HISTORY and wrong for a header, and the gap is not
    small: on 10 August the page showed Microsoft at $373 (30 June) beside a
    chart whose own last point was $509, with a market cap a trillion dollars
    light and every multiple 27% too cheap.

    `live=False` restores the point-in-time basis, and the company page
    passes it for any run dated in the past — see `is_current`.

    `live_valuation` degrades to the point-in-time value when no bar is
    cached, so a name with no price history still renders.
    """
    try:
        return _finlake().ratios_latest(ticker, as_of=as_of, live=live)
    except Exception:
        return {}


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def quote(ticker: str) -> dict:
    """Latest cached bar: price, its date, and the move from the prior close.

    Reads the same series `prices()` returns, so the header and the chart
    cannot disagree — which they did, for the reason in `latest_ratios`.
    """
    try:
        return _finlake().quote(ticker) or {}
    except Exception:
        return {}


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def quotes(tickers: tuple[str, ...]) -> dict:
    """`quote` for many names, for the screener.

    Takes a TUPLE, not a list: `st.cache_data` hashes its arguments, and a
    list is unhashable — the accessor would fall back to re-reading 500
    parquet files on every rerun of the page.
    """
    try:
        return _finlake().quotes(list(tickers))
    except Exception:
        return {}


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def prices(ticker: str, start: str, end: str) -> pd.DataFrame:
    try:
        return _finlake().prices(ticker, start=start, end=end, adjust="split")
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def news(ticker: str, days: int = 60, as_of: str | None = None) -> pd.DataFrame:
    try:
        return _finlake().news(ticker, days=days, as_of=as_of)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def macro(series_id: str, as_of: str | None = None) -> pd.DataFrame:
    try:
        return _finlake().macro(series_id, as_of=as_of)
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Market snapshot, profile, and the rarer signals
# ---------------------------------------------------------------------------
def _finlake_db() -> Path:
    from finlake import config as fl_config

    return fl_config.DB_PATH


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def market_snapshot(ticker: str) -> dict:
    """Latest quote, key stats, short interest, ownership, and analyst view.

    One query per table rather than a join: these are independent snapshots
    that can each be missing, and a join would drop the whole row when any
    one of them is absent — which is exactly the case for a name the market
    source has no coverage for.
    """
    import sqlite3

    path = _finlake_db()
    if not path.exists():
        return {}
    out: dict = {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        for table in ("market_snapshot", "short_interest", "ownership",
                      "analyst_targets"):
            row = conn.execute(
                f"SELECT * FROM {table} WHERE ticker = ? "
                f"ORDER BY as_of DESC LIMIT 1", (ticker.upper(),),
            ).fetchone()
            if row:
                out.update({k: v for k, v in dict(row).items()
                            if k not in ("ticker",)})
        prof = conn.execute(
            "SELECT * FROM profile WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
        if prof:
            out.update({f"profile_{k}": v for k, v in dict(prof).items()})
        conn.close()
    except Exception:
        return out
    return out


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def estimates(ticker: str) -> pd.DataFrame:
    """Consensus EPS and revenue estimates, newest capture only."""
    import sqlite3

    path = _finlake_db()
    if not path.exists():
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        df = pd.read_sql_query(
            "SELECT * FROM estimates WHERE ticker = ? AND as_of = "
            "(SELECT MAX(as_of) FROM estimates WHERE ticker = ?)",
            conn, params=(ticker.upper(), ticker.upper()))
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def next_event(ticker: str, kind: str = "earnings") -> str | None:
    """The next scheduled date of one kind, or None if none is upcoming.

    Bounded to today or later in SQL rather than filtered afterwards: the
    calendar table keeps past events, and "next earnings: 2025-10-29" printed
    in August 2026 is worse than showing nothing.
    """
    import sqlite3

    path = _finlake_db()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT event_date FROM earnings_calendar "
            "WHERE ticker = ? AND kind = ? AND event_date >= ? "
            "ORDER BY event_date LIMIT 1",
            (ticker.upper(), kind, dt.date.today().isoformat()),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def earnings_surprises(ticker: str) -> pd.DataFrame:
    import sqlite3

    path = _finlake_db()
    if not path.exists():
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        df = pd.read_sql_query(
            "SELECT quarter, eps_actual, eps_estimate, surprise_pct "
            "FROM earnings_history WHERE ticker = ? ORDER BY quarter",
            conn, params=(ticker.upper(),))
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def universe_news(days: int = 7, limit: int = 200,
                  as_of: str | None = None) -> pd.DataFrame:
    """The newest articles across every ticker, for the news page."""
    import sqlite3

    path = _finlake_db()
    if not path.exists():
        return pd.DataFrame()
    as_of = as_of or dt.date.today().isoformat()
    since = (dt.date.fromisoformat(as_of) - dt.timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        df = pd.read_sql_query(
            """SELECT a.published_at, a.title, a.url, a.source, a.publisher,
                      a.event_class, a.sentiment, a.summary,
                      GROUP_CONCAT(t.ticker) AS tickers
               FROM news_articles a
               JOIN news_tickers t ON t.id = a.id
               WHERE a.published_at <= ? AND a.published_at >= ?
               GROUP BY a.id
               ORDER BY a.published_at DESC, a.source_weight DESC
               LIMIT ?""",
            conn, params=(as_of, since, limit))
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def freshness() -> str | None:
    """When anything was last fetched. One indexed lookup.

    SPLIT OUT OF `coverage_summary` BECAUSE THE ROUTER RUNS ON EVERY PAGE and
    only wanted this one field. `coverage_summary` counts rows in `facts`, and
    `facts` is now 27 million rows: `COUNT(DISTINCT cik)` takes 92 seconds and
    `COUNT(*)` another 25. Every page in the hub sat behind two minutes of
    table scanning to print a timestamp in the header.
    """
    import sqlite3

    path = _finlake_db()
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute("SELECT MAX(fetched_at) FROM fetch_log").fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def coverage_summary() -> dict:
    """What the cache actually contains — for the data-health readout.

    Surfaced in the UI rather than kept for debugging: a hub built on a
    partially-loaded cache should say so plainly. A blank chart with no
    explanation reads as a broken app; "412 of 503 names have fundamentals"
    reads as a build still in progress.

    EVERY COUNT HERE AVOIDS SCANNING `facts`, which holds 27 million rows.
    `COUNT(DISTINCT cik)` over it took 92 seconds and `COUNT(*)` 25 more —
    on a page that only wanted a headline figure. Two substitutions, both
    exact rather than approximate:

      * companies with facts: ask, for each of the ~8,000 known securities,
        whether ANY fact exists. That is 8,000 index seeks against
        `idx_facts_pit`, which starts with `cik`, and it returns in 0.1s.
      * total facts: `MAX(rowid)`. The table is append-only and nothing is
        ever deleted from it — that is the central design guarantee, stated
        at the top of `store.py` — so the highest rowid IS the row count.
        Instant, and it stops being true only if someone deletes a fact,
        which would be a far larger problem than a wrong count.
    """
    import sqlite3

    path = _finlake_db()
    if not path.exists():
        return {}
    out: dict = {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for label, sql in (
            ("companies_with_facts",
             "SELECT COUNT(*) FROM securities s "
             "WHERE EXISTS (SELECT 1 FROM facts f WHERE f.cik = s.cik)"),
            ("facts", "SELECT MAX(rowid) FROM facts"),
            ("filings", "SELECT COUNT(*) FROM filings"),
            ("news_articles", "SELECT COUNT(*) FROM news_articles"),
            ("macro_observations", "SELECT COUNT(*) FROM macro"),
            ("market_snapshots", "SELECT COUNT(DISTINCT ticker) FROM market_snapshot"),
        ):
            try:
                out[label] = conn.execute(sql).fetchone()[0]
            except Exception:
                out[label] = None
        row = conn.execute("SELECT MAX(fetched_at) FROM fetch_log").fetchone()
        out["last_fetch"] = row[0] if row else None
        conn.close()
    except Exception:
        return out
    return out


# How each snapshotted ratio scales when the price moves. Every one of these
# is linear in price, so re-pricing a stored row is exact arithmetic and not
# an approximation:
#
#   +1  multiply by the price ratio   (a multiple: price over a filed figure)
#   -1  divide by it                  (a yield: a filed figure over price)
#
# Anything absent is a pure fundamental — margins, ROE, ROIC, leverage — and
# does not move when the stock does. Enterprise value is handled separately
# because only its equity component reprices; the debt does not.
_PRICE_EXPONENT = {
    "price": 1, "market_cap": 1,
    "pe_ttm": 1, "pe_forward": 1, "peg": 1, "ps": 1, "pb": 1,
    "fcf_yield": -1, "dividend_yield": -1,
}


def reprice(frame: pd.DataFrame, quotes_by_ticker: dict) -> pd.DataFrame:
    """Rescale a run's stored valuation ratios to the latest close.

    WHY THE SCREENER NEEDS THIS. `run_ratios` is snapshotted when the run
    executes, and every price-based figure in it was computed at the close on
    each name's last FISCAL QUARTER END — up to three months stale, and stale
    by a different amount for every company depending on when its quarter
    happened to end. That reached the market-cap band filter (names sorted
    into the wrong size bucket), the P/E ceiling, the P/E and yield columns,
    and the "total market cap" figure at the top of the page.

    The UI still does not COMPUTE anything in the sense the rule means — no
    scoring engine, no ratio derivation, no filing lookups. It multiplies
    stored numbers by a scalar. That is the cheap half of the trade the rule
    exists to protect: deriving 503 names live was the 77-second cold load.

    Names with no cached quote keep their snapshot values, and `price_as_of`
    records which basis each row ended up on so the page can say so.
    """
    if frame.empty or not quotes_by_ticker:
        return frame

    out = frame.copy()
    scale = out["ticker"].map(
        lambda t: (quotes_by_ticker.get(str(t).upper()) or {}).get("price"))
    scale = pd.to_numeric(scale, errors="coerce")
    base = pd.to_numeric(out.get("price"), errors="coerce")
    # A snapshot price of zero or missing gives no ratio to scale by; those
    # rows stay exactly as stored rather than becoming infinities.
    factor = (scale / base.where(base > 0)).where(scale > 0)

    equity_delta = None
    if "market_cap" in out.columns:
        old_cap = pd.to_numeric(out["market_cap"], errors="coerce")
        equity_delta = old_cap * (factor - 1.0)

    for column, exponent in _PRICE_EXPONENT.items():
        if column not in out.columns:
            continue
        values = pd.to_numeric(out[column], errors="coerce")
        moved = values * (factor if exponent == 1 else 1.0 / factor)
        out[column] = moved.where(factor.notna(), values)

    # Enterprise value moves only by the change in the equity slice; net debt
    # is a filed figure and does not reprice. Scaling EV by the price ratio
    # would silently mark a leveraged company's debt to market.
    if equity_delta is not None and "enterprise_value" in out.columns:
        ev = pd.to_numeric(out["enterprise_value"], errors="coerce")
        out["enterprise_value"] = (ev + equity_delta).where(factor.notna(), ev)
        if "ev_ebitda" in out.columns:
            old_ev = ev.where(ev.abs() > 0)
            ratio = (ev + equity_delta) / old_ev
            value = pd.to_numeric(out["ev_ebitda"], errors="coerce")
            out["ev_ebitda"] = (value * ratio).where(
                factor.notna() & ratio.notna(), value)

    out["price_as_of"] = out["ticker"].map(
        lambda t: (quotes_by_ticker.get(str(t).upper()) or {}).get("as_of"))
    return out


# ---------------------------------------------------------------------------
# Company logos
#
# Served as data URIs rather than files. Streamlit can host a static
# directory, which would let the browser cache each logo once, but it needs a
# server flag set — and if the app is ever launched without it every logo
# becomes a broken-image icon, which is a far worse failure than a slightly
# heavier page. A data URI works however the app was started.
#
# Size is chosen per context and encoded at that size, not scaled by CSS from
# one master: a 128px master inlined 500 times is 3.2MB of base64, the same
# logos at table size are 420KB. The encoded strings are memoised for the life
# of the process, so a Streamlit rerun — which re-executes the whole script on
# every click — pays for this exactly once.
# ---------------------------------------------------------------------------
_LOGO_URI_MEMO: dict[tuple[str, int], str | None] = {}


def logo_uri(ticker: str, *, size: int = 22) -> str | None:
    """A company's logo as a data URI at `size` px, or None if uncached."""
    key = (str(ticker).upper(), int(size))
    if key in _LOGO_URI_MEMO:
        return _LOGO_URI_MEMO[key]

    uri = None
    try:
        import base64
        import io

        from finlake.sources import logos as logo_src

        path = logo_src.logo_path(key[0])
        if path is not None:
            from PIL import Image

            image = Image.open(path).convert("RGBA")
            image.thumbnail((size * 2, size * 2), Image.LANCZOS)  # 2x for retina
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            uri = f"data:image/png;base64,{encoded}"
    except Exception:
        uri = None

    _LOGO_URI_MEMO[key] = uri
    if len(_LOGO_URI_MEMO) > 4000:
        _LOGO_URI_MEMO.clear()
    return uri


def logo_uris(tickers, *, size: int = 22) -> dict[str, str | None]:
    """`logo_uri` for many names, for a table."""
    return {str(t).upper(): logo_uri(str(t), size=size) for t in tickers}


@st.cache_data(ttl=TTL_SLOW, show_spinner=False)
def scoring_config() -> dict:
    """The scoring configuration, flattened for display.

    Returned as plain data rather than the `Config` object because
    `st.cache_data` pickles what it caches and a frozen dataclass holding a
    Path round-trips badly. The methodology page reads THIS rather than
    restating any weight in prose — a second copy of a weight is a second copy
    that drifts, in the one place a reader goes to check the first.
    """
    from pathlib import Path as _Path

    from ..config import BUCKET_NAMES, load_config

    path = _Path(__file__).resolve().parents[2] / "config.yaml"
    if not path.exists():
        return {}
    try:
        cfg = load_config(path)
    except Exception:
        return {}
    return {
        "config_hash": cfg.config_hash,
        "composite_weights": dict(cfg.composite_weights),
        "metric_weights": {b: dict(cfg.bucket(b).metric_weights)
                           for b in BUCKET_NAMES},
        "coverage_bucket_floor": cfg.coverage_bucket_floor,
        "composite_min_weight_covered": cfg.composite_min_weight_covered,
        "min_history_quarters": cfg.universe_min_history_quarters,
        "peer_min_group_size": cfg.peer_min_group_size,
        "winsorize_low_pct": cfg.winsorize_low_pct,
        "winsorize_high_pct": cfg.winsorize_high_pct,
    }


def clear_caches() -> None:
    """Drop every cached read. Wired to the UI's refresh control, so a user
    who knows the loader just ran can see the new data without restarting."""
    st.cache_data.clear()
