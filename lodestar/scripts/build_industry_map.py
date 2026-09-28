#!/usr/bin/env python3
"""Regenerate industry_map.csv for the whole universe.

    python scripts/build_industry_map.py
    python scripts/build_industry_map.py --dry-run

WHY THIS MATTERS MORE THAN A COVERAGE STATISTIC. Peer groups are the
foundation of the methodology: every metric is z-scored *within* its peer
group, and a bad grouping silently changes every score. Before this script,
42 of 503 names came from the curated map and 454 fell back to SIC divisions
— a classification maintained for filing purposes since the 1970s, which puts
Air Products and Nike in the same "Manufacturing" sector.

TWO SOURCES, EACH USED FOR WHAT IT IS GOOD AT:

  sector    from the GICS labels scraped with the S&P constituent list. GICS
            is the classification the industry actually uses, and the sector
            label is load-bearing: `pb_sector` gates P/B ON for financials,
            and DEC-002 gates ROIC OFF for them.
  industry  from the market provider's own industry label, which is far finer
            than a GICS sector ("Semiconductors", "Drug Manufacturers -
            General") and is what makes a peer group meaningful.

EXISTING CURATED ROWS WIN. They were verified against real filings, and
overwriting them with a scraped label would quietly undo that work. This
script only fills in names the curated map does not already cover.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO.parent / "finlake"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

MAP_PATH = REPO / "industry_map.csv"
UNIVERSE_PATH = REPO.parent / "finlake" / "universe" / "sp500_ndx.csv"

# GICS uses "Financial Services" in some vendor feeds and "Financials" in the
# index itself. lodestar's gates compare the canonical sector, and DEC-002
# depends on "financials" specifically, so vendor spellings are normalised
# here rather than left for every comparison downstream to handle.
SECTOR_ALIASES = {
    "financial services": "Financials",
    "financial": "Financials",
    "technology": "Information Technology",
    "information technology": "Information Technology",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "consumer cyclical": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "basic materials": "Materials",
    "materials": "Materials",
    "communication services": "Communication Services",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
}


def normalize_sector(value: str | None) -> str | None:
    if not value:
        return None
    return SECTOR_ALIASES.get(str(value).strip().lower(), str(value).strip())


def read_existing() -> dict[str, tuple[str, str]]:
    if not MAP_PATH.exists():
        return {}
    with open(MAP_PATH, newline="", encoding="utf-8") as fh:
        return {r["ticker"].strip().upper():
                (r["industry"].strip(), r["sector"].strip())
                for r in csv.DictReader(fh) if r.get("ticker")}


def read_universe_sectors() -> dict[str, str]:
    """GICS sector per ticker, from the constituent list."""
    if not UNIVERSE_PATH.exists():
        return {}
    with open(UNIVERSE_PATH, newline="", encoding="utf-8") as fh:
        rows = csv.DictReader(l for l in fh if not l.startswith("#"))
        return {r["ticker"].strip().upper(): normalize_sector(r.get("sector"))
                for r in rows if r.get("ticker") and r.get("sector")}


def read_profiles() -> dict[str, tuple[str | None, str | None]]:
    """(sector, industry) per ticker from the market provider's profile."""
    from finlake import config as fl_config

    if not fl_config.DB_PATH.exists():
        return {}
    conn = sqlite3.connect(f"file:{fl_config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out = {r["ticker"].strip().upper():
           (normalize_sector(r["sector"]), (r["industry"] or "").strip() or None)
           for r in conn.execute(
               "SELECT ticker, sector, industry FROM profile")}
    conn.close()
    return out


def build(dry_run: bool) -> int:
    existing = read_existing()
    gics = read_universe_sectors()
    profiles = read_profiles()

    tickers = sorted(set(existing) | set(gics) | set(profiles))
    if not tickers:
        print("Nothing to build — no universe file and no profiles cached.")
        return 1

    rows: dict[str, tuple[str, str]] = {}
    kept = filled = unresolved = 0
    for ticker in tickers:
        if ticker in existing:
            # Curated rows win: they were verified against real filings.
            rows[ticker] = existing[ticker]
            kept += 1
            continue

        prof_sector, prof_industry = profiles.get(ticker, (None, None))
        # GICS sector first (it is the index's own classification and what
        # the sector gates are written against), profile sector as backup.
        sector = gics.get(ticker) or prof_sector
        # Industry from the provider — much finer than a GICS sector, and
        # fineness is the whole point of a peer group.
        industry = prof_industry or sector
        if not sector or not industry:
            unresolved += 1
            continue
        rows[ticker] = (industry, sector)
        filled += 1

    sectors = Counter(s for _i, s in rows.values())
    industries = Counter(i for i, _s in rows.values())
    thin = sum(1 for _i, n in industries.items() if n < 8)

    print(f"  {kept} curated rows kept, {filled} filled in, "
          f"{unresolved} unresolved")
    print(f"  {len(rows)} tickers · {len(sectors)} sectors · "
          f"{len(industries)} industries")
    print(f"  {thin} industries have fewer than 8 members and will fall back "
          f"to sector when scoring")
    print("\n  sectors:")
    for sector, n in sectors.most_common():
        print(f"    {n:>4}  {sector}")

    financials = [t for t, (_i, s) in rows.items() if s == "Financials"]
    print(f"\n  {len(financials)} names labelled Financials — ROIC is gated "
          f"off and P/B gated on for exactly these (DEC-002)")

    if dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    with open(MAP_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "industry", "sector"])
        for ticker in sorted(rows):
            industry, sector = rows[ticker]
            writer.writerow([ticker, industry, sector])
    print(f"\n  wrote {MAP_PATH}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    sys.exit(build(p.parse_args().dry_run))
