"""CLI entry point.

    python -m lodestar run --as-of 2026-08-09
    python -m lodestar show MU
    python -m lodestar audit MU roic
    python -m lodestar ui
    python -m lodestar daemon        keep everything current, continuously
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import warnings
from pathlib import Path

import pandas as pd

from . import persistence
from .adapter import (
    check_finlake_version, get_fundamentals, get_prices, get_ratios_latest,
)
from .config import BUCKET_NAMES, load_config
from .industry_map import load_peer_groups
from .metrics.base import TickerContext
from .metrics.news import build_provider
from .scoring.attribution import TickerAttribution, compute_attribution
from .scoring.bucket import BUCKET_METRICS, compute_bucket
from .scoring.composite import compute_composite
from .universe import build_universe, print_universe

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

# How far back to pull price history: covers the 252-trading-day
# momentum lookback, the 12-quarter buyback-timing window, and the
# 10-year fundamentals window with room to spare.
PRICE_HISTORY_YEARS = 11


def _price_start(as_of: str) -> str:
    return (dt.date.fromisoformat(as_of) - dt.timedelta(days=365 * PRICE_HISTORY_YEARS)).isoformat()


def _build_contexts(
    tickers: list[str], as_of: str, peer_groups, conn: sqlite3.Connection,
) -> dict[str, TickerContext]:
    contexts: dict[str, TickerContext] = {}
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {t}", end="\r")
        fund = get_fundamentals(t, as_of=as_of, years=10)
        px = get_prices(t, start=_price_start(as_of), end=as_of)
        prior = persistence.load_prior_percentiles(conn, t, before_as_of=as_of)
        contexts[t] = TickerContext(
            ticker=t, as_of=as_of, cik=fund.cik, fundamentals=fund.frame, prices=px,
            sector=peer_groups.sector[t], industry=peer_groups.industry[t],
            prior_rank_history=prior,
        )
    print(" " * 40, end="\r")
    return contexts


def _score_universe(tickers: list[str], as_of: str, cfg, peer_groups, contexts):
    provider = build_provider(cfg)
    news_signal = provider.get_signal(tickers, as_of=as_of)
    extra_kwargs = {t: {"signal": news_signal[t]} for t in tickers}

    covered = sum(1 for s in news_signal.values() if s.coverage > 0)
    print(f"  news: {type(provider).__name__}, "
          f"{covered}/{len(tickers)} names with coverage")

    bucket_results = {}
    for bucket in BUCKET_NAMES:
        kwargs = extra_kwargs if bucket == "news" else None
        bucket_results[bucket] = compute_bucket(
            bucket, contexts, cfg, peer_groups.industry, peer_groups.sector,
            extra_kwargs=kwargs,
        )

    composite = compute_composite(bucket_results, cfg)
    attributions = {
        t: compute_attribution(t, as_of, bucket_results, composite, cfg, ctx=contexts[t])
        for t in tickers
    }
    return bucket_results, composite, attributions


def cmd_run(args: argparse.Namespace) -> None:
    warnings.filterwarnings("ignore")
    as_of = args.as_of or dt.date.today().isoformat()
    cfg = load_config(args.config or DEFAULT_CONFIG_PATH)
    check_finlake_version()

    universe = build_universe(cfg, as_of=as_of)
    print_universe(universe)
    if not universe.tickers:
        print("\nUniverse is empty -- nothing to score.")
        return

    peer_groups = load_peer_groups(universe.tickers, sic_lookup=universe.sic_by_ticker)
    mapping_summary = peer_groups.summary()
    print(f"\nIndustry mapping: {(mapping_summary['source'] == 'industry_map.csv').sum()} "
          f"from industry_map.csv, "
          f"{(mapping_summary['source'] == 'SIC fallback').sum()} from SIC fallback, "
          f"{(mapping_summary['source'] == 'unmapped').sum()} unmapped")

    db_path = Path(args.db) if args.db else persistence.DEFAULT_DB_PATH
    with persistence.connect(db_path) as conn:
        print(f"\nFetching fundamentals + prices for {len(universe.tickers)} names...")
        contexts = _build_contexts(universe.tickers, as_of, peer_groups, conn)

        print("Scoring...")
        bucket_results, composite, attributions = _score_universe(
            universe.tickers, as_of, cfg, peer_groups, contexts
        )

        run_id = persistence.save_run(
            conn, as_of=as_of, cfg=cfg, universe_source=universe.source,
            bucket_results=bucket_results, composite=composite, attributions=attributions,
            sector_of=peer_groups.sector, industry_of=peer_groups.industry,
        )

        # Snapshot valuation ratios with the run so the UI can READ them.
        # Deriving these on demand made the screener recompute 503 names at
        # ~150ms each — 77 seconds to draw one table — and broke the rule
        # that the UI never computes. It also keeps the P/E on screen
        # consistent with the score printed beside it.
        # A run dated today snapshots the LIVE valuation, which prices the
        # newest period at the latest close and uses the market source's
        # consolidated share count. That second part is not cosmetic: a
        # multi-class filer's reported share count covers one class, so Visa's
        # filed market cap was $41bn against a real $673bn — and the
        # screener's re-pricing scales whatever it is given, so a wrong basis
        # stays wrong however fresh the price is. A run dated in the past
        # keeps the point-in-time basis, which is what was knowable then.
        import datetime as _dt

        live_basis = as_of >= _dt.date.today().isoformat()
        print(f"Snapshotting valuation ratios "
              f"({'live basis' if live_basis else 'point-in-time basis'})...")
        ratio_rows = {}
        for i, t in enumerate(universe.tickers, 1):
            print(f"  [{i}/{len(universe.tickers)}] {t}", end="\r")
            try:
                values = get_ratios_latest(t, as_of=as_of, live=live_basis)
            except Exception:
                values = {}
            if values:
                ratio_rows[t] = values
        print(" " * 40, end="\r")
        n_ratios = persistence.save_ratios(conn, run_id, ratio_rows)
        print(f"  {n_ratios} names with valuation ratios")

        table = persistence.load_composite_table(conn, run_id)

    print(f"\nRun saved: {run_id}  (config hash {cfg.config_hash})\n")
    display_cols = ["ticker", "sector", "percentile", "composite_raw"]
    print(table[display_cols].round(2).to_string(index=False))

    scored = table["percentile"].notna().sum()
    print(f"\n{scored}/{len(table)} names produced a composite score "
          f"({len(table) - scored} had insufficient coverage across every bucket).")

    if args.csv:
        table.to_csv(args.csv, index=False)
        print(f"CSV exported to {args.csv}")


def cmd_show(args: argparse.Namespace) -> None:
    db_path = Path(args.db) if args.db else persistence.DEFAULT_DB_PATH
    if not db_path.exists():
        print(f"No runs database found at {db_path}. Run `python -m lodestar run` first.")
        return

    with persistence.connect(db_path) as conn:
        run = _resolve_run(conn, args.run_id)
        if run is None:
            print("No runs found. Run `python -m lodestar run` first.")
            return
        _print_ticker_detail(conn, run, args.ticker.upper())


def _resolve_run(conn: sqlite3.Connection, run_id: str | None) -> persistence.RunSummary | None:
    if run_id:
        rows = [r for r in persistence.list_runs(conn) if r.run_id == run_id]
        return rows[0] if rows else None
    return persistence.latest_run(conn)


def _print_ticker_detail(conn: sqlite3.Connection, run: persistence.RunSummary, ticker: str) -> None:
    comp_row = conn.execute(
        "SELECT * FROM run_composite WHERE run_id = ? AND ticker = ?", (run.run_id, ticker)
    ).fetchone()
    if comp_row is None:
        print(f"{ticker} not found in run {run.run_id} (as of {run.as_of}).")
        return

    print(f"=== {ticker} — run {run.run_id} (as of {run.as_of}) ===")
    pct = comp_row["percentile"]
    print(f"Composite percentile: {pct:.1f}" if pct is not None else "Composite: no score (insufficient coverage)")
    print(f"Composite raw z:      {comp_row['composite_raw']:.4f}" if comp_row["composite_raw"] is not None else "")
    if comp_row["rank_change"] is not None:
        print(f"Rank change (vs. 2 runs ago): {comp_row['rank_change']:+.1f}")
    print(f"Sector / industry:    {comp_row['sector']} / {comp_row['industry']}")
    print(f"Data as of (latest filed fact): {comp_row['data_as_of']}")

    print("\n--- Bucket contributions ---")
    bucket_rows = conn.execute(
        "SELECT * FROM run_buckets WHERE run_id = ? AND ticker = ?", (run.run_id, ticker)
    ).fetchall()
    bdf = pd.DataFrame([dict(r) for r in bucket_rows]).set_index("bucket").reindex(BUCKET_NAMES)
    print(bdf[["subscore", "coverage", "low_confidence", "weight_used",
               "composite_contribution", "was_capped"]].round(3).to_string())

    print("\n--- Top drivers up ---")
    _print_drivers(conn, run.run_id, ticker, ascending=False)
    print("\n--- Top drivers down ---")
    _print_drivers(conn, run.run_id, ticker, ascending=True)

    print("\n--- All metrics ---")
    metric_rows = conn.execute(
        "SELECT * FROM run_metrics WHERE run_id = ? AND ticker = ? ORDER BY bucket, metric",
        (run.run_id, ticker),
    ).fetchall()
    mdf = pd.DataFrame([dict(r) for r in metric_rows])
    if not mdf.empty:
        print(mdf[["bucket", "metric", "raw_value", "z", "peer_level", "peer_group_size",
                    "substitution"]].round(4).to_string(index=False))


def _print_drivers(conn: sqlite3.Connection, run_id: str, ticker: str, *, ascending: bool) -> None:
    rows = conn.execute(
        "SELECT bucket, metric, raw_value, z, contribution FROM run_metrics "
        "WHERE run_id = ? AND ticker = ? AND contribution IS NOT NULL "
        "ORDER BY contribution " + ("ASC" if ascending else "DESC") + " LIMIT 3",
        (run_id, ticker),
    ).fetchall()
    if not rows:
        print("  (none)")
        return
    for r in rows:
        print(f"  {r['bucket']:<20} {r['metric']:<24} "
              f"raw={r['raw_value']:.4f}  z={r['z']:+.3f}  contribution={r['contribution']:+.4f}")


def cmd_audit(args: argparse.Namespace) -> None:
    """Full computation chain for one ticker/metric: raw facts, filed
    dates, intermediate values, winsorized value, peer stats, final z --
    everything needed to check a number against the filing by hand."""
    warnings.filterwarnings("ignore")
    as_of = args.as_of or dt.date.today().isoformat()
    cfg = load_config(args.config or DEFAULT_CONFIG_PATH)
    check_finlake_version()

    bucket = _find_bucket_for_metric(args.metric)
    if bucket is None:
        print(f"Unknown metric {args.metric!r}. Known metrics: "
              f"{sorted(m for mm in BUCKET_METRICS.values() for m in mm)}")
        return

    universe = build_universe(cfg, as_of=as_of)
    if args.ticker.upper() not in universe.tickers:
        print(f"Note: {args.ticker.upper()} is not in the current universe "
              f"({universe.source}); auditing it anyway using finlake directly.")
    tickers = universe.tickers if args.ticker.upper() in universe.tickers else [args.ticker.upper()]

    peer_groups = load_peer_groups(tickers, sic_lookup=universe.sic_by_ticker)
    with persistence.connect(Path(args.db) if args.db else persistence.DEFAULT_DB_PATH) as conn:
        contexts = _build_contexts(tickers, as_of, peer_groups, conn)

    ticker = args.ticker.upper()
    ctx = contexts[ticker]

    print(f"=== AUDIT: {ticker} / {args.metric} (bucket: {bucket}) as of {as_of} ===\n")

    print("--- Raw facts behind this metric (concept: value @ period_end, filed) ---")
    for col in ctx.fundamentals.columns:
        if col.endswith("__derived") or col.endswith("__filed"):
            continue
        series = ctx.fundamentals[col].dropna()
        if len(series) == 0:
            continue
        filed_col = f"{col}__filed"
        print(f"  {col}:")
        for period_end, val in series.tail(8).items():
            filed = ctx.fundamentals.loc[period_end, filed_col] if filed_col in ctx.fundamentals.columns else "?"
            print(f"    {period_end}: {val:,.4f}   (filed {filed})")

    metric_fn = BUCKET_METRICS[bucket][args.metric]
    result = metric_fn(ctx, cfg)
    print(f"\n--- Metric result ---")
    print(f"  value:        {result.value}")
    print(f"  substitution: {result.substitution}")
    print(f"  note:         {result.note}")
    print(f"  raw_inputs:")
    for k, v in result.raw_inputs.items():
        print(f"    {k}: {v}")

    print(f"\n--- Peer-group standardization ---")
    print(f"  Scoring the full universe ({len(tickers)} names) to get real peer statistics...")
    bucket_results, _, _ = _score_universe(tickers, as_of, cfg, peer_groups, contexts)
    zscore_result = bucket_results[bucket][ticker].zscore_results[args.metric]
    print(f"  peer level used:    {zscore_result.peer_level}")
    print(f"  peer group size:    {zscore_result.peer_group_size}")
    print(f"  peer mean (winsorized): {zscore_result.peer_mean}")
    print(f"  peer std (winsorized):  {zscore_result.peer_std}")
    print(f"  winsorized value:   {zscore_result.winsorized_value}")
    print(f"  final z-score:      {zscore_result.z}")


def _find_bucket_for_metric(metric: str) -> str | None:
    for bucket, metrics in BUCKET_METRICS.items():
        if metric in metrics:
            return bucket
    return None


def cmd_ratings(args: argparse.Namespace) -> None:
    """Buy/sell ratings from a persisted run. Reads only -- computes
    nothing beyond the labels themselves, same as the UI."""
    from .scoring.ratings import (
        groups_to_frame, names_to_frame, rate_industries, rate_names, rate_sectors,
    )

    db_path = Path(args.db) if args.db else persistence.DEFAULT_DB_PATH
    if not db_path.exists():
        print(f"No runs database found at {db_path}. Run `python -m lodestar run` first.")
        return

    with persistence.connect(db_path) as conn:
        run = _resolve_run(conn, args.run_id)
        if run is None:
            print("No runs found. Run `python -m lodestar run` first.")
            return
        table = persistence.load_composite_table(conn, run.run_id)

    if table.empty:
        print(f"Run {run.run_id} has no scored names.")
        return

    print(f"=== {args.level.upper()} RATINGS — as of {run.as_of} "
          f"({run.universe_size} names, config {run.config_hash}) ===\n")

    if args.level == "stock":
        df = names_to_frame(rate_names(table))
        cols = ["ticker", "rating", "score", "percentile", "peer_rating",
                "peer_z", "peer_rank", "peer_count", "industry", "sector"]
    elif args.level == "industry":
        df = groups_to_frame(rate_industries(table, as_of=run.as_of))
        cols = list(df.columns)
    else:
        df = groups_to_frame(rate_sectors(table, as_of=run.as_of))
        cols = list(df.columns)

    print(df[cols].round(3).to_string(index=False))
    print("\nRatings reflect the data as of this run. lodestar has no live "
          "market feed -- re-run after new prices or filings land.")


def _silence_streamlit_first_run_prompt() -> None:
    """Pre-answer Streamlit's first-run "Email:" prompt.

    On a machine that has never run Streamlit interactively, `streamlit
    run` blocks on a welcome prompt asking for an email address before
    it will start the server. The command looks like it has hung. The
    prompt is skipped when a credentials file exists, so write the same
    empty-email file Streamlit itself writes when you press Enter.

    Only ever creates the file; never overwrites an existing one, so a
    real email already configured is left alone.
    """
    creds = Path.home() / ".streamlit" / "credentials.toml"
    if creds.exists():
        return
    try:
        creds.parent.mkdir(parents=True, exist_ok=True)
        creds.write_text('[general]\nemail = ""\n', encoding="utf-8")
    except OSError:
        # Not fatal -- worst case the user sees the prompt and presses
        # Enter once. Never let a config-write failure block the UI.
        pass


def cmd_ui(args: argparse.Namespace) -> None:
    import subprocess

    _silence_streamlit_first_run_prompt()
    app_path = Path(__file__).resolve().parent / "ui" / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        # Passed on the command line rather than via a .streamlit/
        # config.toml because the app can be launched from any working
        # directory, and Streamlit resolves that file relative to the
        # cwd -- a repo-level config would be picked up inconsistently.
        "--browser.gatherUsageStats", "false",
    ]
    if args.db:
        cmd += ["--", "--db", args.db]

    # Deliberately does NOT print a URL: Streamlit picks the next free
    # port if 8501 is taken (a stale instance, another app), and prints
    # the real "Local URL" itself a moment later. Printing a guessed
    # port here would contradict it.
    print("Starting the lodestar UI -- it will open in your browser.")
    print("Press Ctrl+C in this window to stop it.\n")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nUI stopped.")


def cmd_daemon(args: argparse.Namespace) -> None:
    """One loop that keeps both halves current — see lodestar/daemon.py."""
    from .daemon import daemon

    daemon(db_path=Path(args.db) if args.db else None,
           config=args.config, poll_seconds=args.poll, limit=args.limit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lodestar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Score the universe as of a date")
    p_run.add_argument("--as-of", help="YYYY-MM-DD, defaults to today")
    p_run.add_argument("--config", help="path to config.yaml")
    p_run.add_argument("--db", help="path to the runs database")
    p_run.add_argument("--csv", help="also export the ranked table to this CSV path")
    p_run.set_defaults(func=cmd_run)

    p_show = sub.add_parser("show", help="Show full attribution for one ticker")
    p_show.add_argument("ticker")
    p_show.add_argument("--run-id", help="defaults to the latest run")
    p_show.add_argument("--db")
    p_show.set_defaults(func=cmd_show)

    p_daemon = sub.add_parser(
        "daemon",
        help="Keep everything current: refresh the data tiers and re-score "
             "the universe once a day. Leave it running.")
    p_daemon.add_argument("--db", help="path to the runs database")
    p_daemon.add_argument("--config", help="path to config.yaml")
    p_daemon.add_argument("--poll", type=int, default=60,
                          help="seconds between ticks (default 60)")
    p_daemon.add_argument("--limit", type=int,
                          help="cap tickers touched per refresh pass")
    p_daemon.set_defaults(func=cmd_daemon)

    p_ui = sub.add_parser("ui", help="Launch the Streamlit app")
    p_ui.add_argument("--db")
    p_ui.set_defaults(func=cmd_ui)

    p_ratings = sub.add_parser(
        "ratings", help="Buy/sell ratings by sector, industry, or individual stock"
    )
    p_ratings.add_argument(
        "level", nargs="?", default="sector",
        choices=["sector", "industry", "stock"],
        help="which level to rate (default: sector)",
    )
    p_ratings.add_argument("--run-id", help="defaults to the latest run")
    p_ratings.add_argument("--db")
    p_ratings.set_defaults(func=cmd_ratings)

    p_audit = sub.add_parser(
        "audit", help="Print the full computation chain for one ticker/metric"
    )
    p_audit.add_argument("ticker")
    p_audit.add_argument("metric")
    p_audit.add_argument("--as-of")
    p_audit.add_argument("--config")
    p_audit.add_argument("--db")
    p_audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> None:
    # Windows' console defaults to a legacy codepage (cp1252) that can't
    # encode an em dash, let alone a non-ASCII company name -- reconfigure
    # to UTF-8 up front rather than either avoiding every non-ASCII
    # character forever or crashing on whichever one slips through first.
    # No-op on platforms where stdout is already UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
