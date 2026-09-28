"""The `runs` store: every `lodestar run` writes a complete, self-
contained snapshot here. This is what makes score-momentum and rank-
change possible (project spec §5), what a run comparison diffs between,
and — just as important — what the Streamlit UI reads. The UI never
computes (project spec §6): everything it shows, including full per-
name attribution, has to already be sitting in this database.

Schema, four tables:
  runs          — one row per run: as_of, config hash, universe.
  run_composite — one row per ticker per run: percentile, composite score.
  run_buckets   — one row per (ticker, bucket) per run: subscore, coverage,
                  weight actually used, contribution to composite.
  run_metrics   — one row per (ticker, bucket, metric) per run: raw value,
                  z-score, peer group used, substitution flags,
                  contribution. This is the full audit trail.

`run_id` is deterministic: as_of + config_hash. Running the same as-of
date against the same config twice produces the SAME run_id and
overwrites in place — matching the project's determinism guarantee
("same as-of + same config hash = identical output") rather than
accumulating duplicate history for no reason.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config import BUCKET_NAMES, Config
from .scoring.attribution import TickerAttribution
from .scoring.bucket import TickerBucketResult
from .scoring.composite import CompositeResult

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "runs" / "lodestar.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    as_of          TEXT NOT NULL,
    config_hash    TEXT NOT NULL,
    universe_size  INTEGER NOT NULL,
    universe_source TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_composite (
    run_id      TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    sector      TEXT,
    industry    TEXT,
    percentile  REAL,
    composite_raw REAL,
    rank_change REAL,
    data_as_of  TEXT,
    PRIMARY KEY (run_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_run_composite_run ON run_composite (run_id);

CREATE TABLE IF NOT EXISTS run_buckets (
    run_id                TEXT NOT NULL,
    ticker                TEXT NOT NULL,
    bucket                TEXT NOT NULL,
    subscore              REAL,
    coverage              REAL NOT NULL,
    low_confidence        INTEGER NOT NULL,
    weight_configured     REAL NOT NULL,
    weight_used           REAL NOT NULL,
    z_universe            REAL,
    composite_contribution REAL NOT NULL,
    was_capped            INTEGER NOT NULL,
    PRIMARY KEY (run_id, ticker, bucket)
);
CREATE INDEX IF NOT EXISTS idx_run_buckets_run ON run_buckets (run_id);

CREATE TABLE IF NOT EXISTS run_metrics (
    run_id            TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    bucket            TEXT NOT NULL,
    metric            TEXT NOT NULL,
    raw_value         REAL,
    winsorized_value  REAL,
    z                 REAL,
    peer_level        TEXT,
    peer_group_size   INTEGER,
    substitution      TEXT,
    note              TEXT,
    contribution       REAL,
    PRIMARY KEY (run_id, ticker, bucket, metric)
);
CREATE INDEX IF NOT EXISTS idx_run_metrics_run ON run_metrics (run_id);

-- Valuation ratios for every name, snapshotted with the run.
--
-- The UI READS; it never computes. Deriving these on demand broke that: the
-- screener called finlake's ratio engine once per ticker at ~150ms each,
-- which is 77 SECONDS to draw a 503-name table, on every cold load. Ratios
-- are cheap to store and expensive to recompute, and they belong to the run
-- anyway -- a screener showing a P/E from a different moment than the score
-- beside it would be quietly inconsistent.
--
-- Deliberately a flat, wide row rather than the long (ticker, metric, value)
-- shape used by run_metrics: this is read as a whole table to render a grid,
-- so a wide row is one query instead of a pivot.
CREATE TABLE IF NOT EXISTS run_ratios (
    run_id           TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    price            REAL,
    market_cap       REAL,
    enterprise_value REAL,
    pe_ttm           REAL,
    pe_forward       REAL,
    peg              REAL,
    ps               REAL,
    pb               REAL,
    ev_ebitda        REAL,
    fcf_yield        REAL,
    dividend_yield   REAL,
    gross_margin     REAL,
    operating_margin REAL,
    net_margin       REAL,
    roe              REAL,
    roic             REAL,
    debt_to_equity   REAL,
    current_ratio    REAL,
    PRIMARY KEY (run_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_run_ratios_run ON run_ratios (run_id);
"""

# The columns run_ratios stores, in order. Kept as a list so the writer and
# the reader cannot drift apart.
RATIO_COLUMNS = [
    "price", "market_cap", "enterprise_value", "pe_ttm", "pe_forward", "peg",
    "ps", "pb", "ev_ebitda", "fcf_yield", "dividend_yield", "gross_margin",
    "operating_margin", "net_margin", "roe", "roic", "debt_to_equity",
    "current_ratio",
]


def run_id_for(as_of: str, config_hash: str) -> str:
    return f"{as_of}__{config_hash}"


@contextmanager
def connect(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_run(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    cfg: Config,
    universe_source: str,
    bucket_results: dict[str, dict[str, TickerBucketResult]],
    composite: dict[str, CompositeResult],
    attributions: dict[str, TickerAttribution],
    sector_of: dict[str, str],
    industry_of: dict[str, str],
) -> str:
    """Write a complete run snapshot. Overwrites in place if a run with
    the same (as_of, config_hash) already exists — see the module
    docstring on why that's the right behaviour, not a bug."""
    rid = run_id_for(as_of, cfg.config_hash)
    tickers = list(composite)

    conn.execute("DELETE FROM runs WHERE run_id = ?", (rid,))
    conn.execute("DELETE FROM run_composite WHERE run_id = ?", (rid,))
    conn.execute("DELETE FROM run_buckets WHERE run_id = ?", (rid,))
    conn.execute("DELETE FROM run_metrics WHERE run_id = ?", (rid,))

    conn.execute(
        "INSERT INTO runs (run_id, as_of, config_hash, universe_size, "
        "universe_source, created_at) VALUES (?,?,?,?,?,?)",
        (rid, as_of, cfg.config_hash, len(tickers), universe_source,
         dt.datetime.now().isoformat()),
    )

    for t in tickers:
        comp = composite[t]
        attr = attributions.get(t)
        conn.execute(
            "INSERT INTO run_composite (run_id, ticker, sector, industry, "
            "percentile, composite_raw, rank_change, data_as_of) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid, t, sector_of.get(t), industry_of.get(t), comp.percentile,
             comp.composite_raw, attr.rank_change if attr else None,
             attr.data_as_of if attr else None),
        )

        for bucket in BUCKET_NAMES:
            result = bucket_results[bucket][t]
            sub = result.subscore
            attr_bucket = attr.buckets[bucket] if attr else None
            conn.execute(
                "INSERT INTO run_buckets (run_id, ticker, bucket, subscore, "
                "coverage, low_confidence, weight_configured, weight_used, "
                "z_universe, composite_contribution, was_capped) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, t, bucket, sub.subscore, sub.coverage, int(sub.low_confidence),
                 cfg.composite_weights[bucket],
                 attr_bucket.weight_used if attr_bucket else 0.0,
                 attr_bucket.z_universe if attr_bucket else None,
                 attr_bucket.composite_contribution if attr_bucket else 0.0,
                 int(attr_bucket.was_capped) if attr_bucket else 0),
            )

            for metric_name, mresult in result.metric_results.items():
                zres = result.zscore_results[metric_name]
                driver = _find_driver(attr, metric_name) if attr else None
                conn.execute(
                    "INSERT INTO run_metrics (run_id, ticker, bucket, metric, "
                    "raw_value, winsorized_value, z, peer_level, peer_group_size, "
                    "substitution, note, contribution) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, t, bucket, metric_name, mresult.value, zres.winsorized_value,
                     zres.z, zres.peer_level, zres.peer_group_size, mresult.substitution,
                     mresult.note, driver.contribution if driver else None),
                )

    return rid


def _find_driver(attr: TickerAttribution, metric_name: str):
    for d in attr.top_drivers_up + attr.top_drivers_down:
        if d.metric == metric_name:
            return d
    return None


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    as_of: str
    config_hash: str
    universe_size: int
    universe_source: str
    created_at: str


def list_runs(conn: sqlite3.Connection) -> list[RunSummary]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY as_of DESC, created_at DESC"
    ).fetchall()
    return [RunSummary(**dict(r)) for r in rows]


def latest_run(conn: sqlite3.Connection) -> RunSummary | None:
    runs = list_runs(conn)
    return runs[0] if runs else None


def load_composite_table(conn: sqlite3.Connection, run_id: str):
    """Everything the ranked-table view needs, one row per ticker."""
    import pandas as pd

    composite_rows = conn.execute(
        "SELECT * FROM run_composite WHERE run_id = ?", (run_id,)
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in composite_rows])
    if df.empty:
        return df

    bucket_rows = conn.execute(
        "SELECT ticker, bucket, subscore, coverage, low_confidence "
        "FROM run_buckets WHERE run_id = ?", (run_id,)
    ).fetchall()
    bucket_df = pd.DataFrame([dict(r) for r in bucket_rows])
    if not bucket_df.empty:
        wide_sub = bucket_df.pivot(index="ticker", columns="bucket", values="subscore")
        wide_sub.columns = [f"{c}_sub" for c in wide_sub.columns]
        wide_cov = bucket_df.pivot(index="ticker", columns="bucket", values="coverage")
        wide_cov.columns = [f"{c}_cov" for c in wide_cov.columns]
        df = df.merge(wide_sub, on="ticker", how="left").merge(wide_cov, on="ticker", how="left")

    return df.sort_values("percentile", ascending=False, na_position="last")


def diff_runs(conn: sqlite3.Connection, run_id_a: str, run_id_b: str):
    """What moved between two runs, and (at the bucket level) why —
    the project spec's run-comparison view. `run_id_a` is treated as the
    earlier run, `run_id_b` the later one, regardless of argument order
    (sorted by each run's own as_of internally) — a positive
    percentile_change means the name's rank improved from a to b.
    """
    import pandas as pd

    meta = {
        r["run_id"]: r["as_of"]
        for r in conn.execute(
            "SELECT run_id, as_of FROM runs WHERE run_id IN (?, ?)", (run_id_a, run_id_b)
        ).fetchall()
    }
    if len(meta) < 2:
        raise ValueError(f"one or both runs not found: {run_id_a!r}, {run_id_b!r}")
    earlier, later = sorted(meta, key=lambda rid: meta[rid])

    a = load_composite_table(conn, earlier)
    b = load_composite_table(conn, later)
    if a.empty or b.empty:
        return pd.DataFrame()

    merged = a.merge(b, on="ticker", how="outer", suffixes=("_before", "_after"))
    merged["percentile_change"] = merged["percentile_after"] - merged["percentile_before"]

    bucket_cols = [f"{b_}_sub" for b_ in BUCKET_NAMES]
    for col in bucket_cols:
        before_col, after_col = f"{col}_before", f"{col}_after"
        if before_col in merged.columns and after_col in merged.columns:
            merged[f"{col}_change"] = merged[after_col] - merged[before_col]

    merged.attrs["earlier_run_id"] = earlier
    merged.attrs["later_run_id"] = later
    merged.attrs["earlier_as_of"] = meta[earlier]
    merged.attrs["later_as_of"] = meta[later]
    return merged.sort_values("percentile_change", ascending=False, na_position="last")


def load_prior_percentiles(conn: sqlite3.Connection, ticker: str, *, before_as_of: str,
                            limit: int = 2) -> tuple[float, ...]:
    """The last `limit` completed runs' composite percentile for one
    ticker, strictly before `before_as_of`, oldest first — exactly the
    shape momentum.rank_change() needs (TickerContext.prior_rank_history).
    """
    rows = conn.execute(
        "SELECT rc.percentile FROM run_composite rc "
        "JOIN runs r ON r.run_id = rc.run_id "
        "WHERE rc.ticker = ? AND r.as_of < ? AND rc.percentile IS NOT NULL "
        "ORDER BY r.as_of DESC LIMIT ?",
        (ticker, before_as_of, limit),
    ).fetchall()
    percentiles = [r["percentile"] for r in rows]
    return tuple(reversed(percentiles))


def save_ratios(conn: sqlite3.Connection, run_id: str,
                rows: dict[str, dict]) -> int:
    """Snapshot each name's valuation ratios alongside the run.

    `rows` maps ticker -> the flat dict finlake's `ratios_latest` returns.
    Only the columns in RATIO_COLUMNS are kept; anything else it returns is
    ignored rather than silently dropped into a column that does not exist.
    """
    payload = []
    for ticker, values in rows.items():
        payload.append(
            (run_id, ticker) + tuple(values.get(c) for c in RATIO_COLUMNS))
    if not payload:
        return 0
    placeholders = ",".join("?" * (len(RATIO_COLUMNS) + 2))
    conn.executemany(
        f"INSERT OR REPLACE INTO run_ratios "
        f"(run_id, ticker, {','.join(RATIO_COLUMNS)}) "
        f"VALUES ({placeholders})", payload)
    conn.commit()
    return len(payload)


def load_ratios(conn: sqlite3.Connection, run_id: str):
    """The ratio snapshot for a run, as a DataFrame indexed by nothing in
    particular — the screener merges it onto the composite table by ticker."""
    import pandas as pd

    rows = conn.execute(
        f"SELECT ticker, {','.join(RATIO_COLUMNS)} FROM run_ratios "
        f"WHERE run_id = ?", (run_id,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows])
