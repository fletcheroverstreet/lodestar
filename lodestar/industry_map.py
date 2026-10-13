"""Peer-group assignment.

GICS is licensed and finlake only has SIC codes from EDGAR (see DEC note
in lodestar's vault on the industry-mapping decision). The fix: a
hand-curated `industry_map.csv` at the repo root (ticker -> industry ->
sector) that gets edited by hand as the universe grows, with a SIC-code
fallback for anything not yet in that file. A name is never silently
lumped into "Other" — every ticker gets a real industry/sector label and
a recorded source for that label, so a thin or wrong peer group is
visible in the output, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_MAP_PATH = Path(__file__).resolve().parent.parent / "industry_map.csv"

REQUIRED_COLUMNS = {"ticker", "industry", "sector"}


@dataclass(frozen=True)
class PeerGroups:
    """Resolved industry/sector for a set of tickers, plus where each
    assignment came from."""

    industry: dict[str, str]
    sector: dict[str, str]
    source: dict[str, str]  # ticker -> "industry_map.csv" | "SIC fallback" | "unmapped"

    def summary(self) -> pd.DataFrame:
        """One row per ticker: industry, sector, and where the assignment
        came from. Useful for the "show me the mapping coverage" checkpoint."""
        rows = [
            {"ticker": t, "industry": self.industry[t], "sector": self.sector[t],
             "source": self.source[t]}
            for t in sorted(self.industry)
        ]
        return pd.DataFrame(rows)


def load_peer_groups(
    tickers: list[str],
    *,
    map_path: Path = DEFAULT_MAP_PATH,
    sic_lookup: dict[str, tuple[str | None, str | None]] | None = None,
) -> PeerGroups:
    """Resolve industry/sector for every ticker in `tickers`.

    `sic_lookup` maps ticker -> (sic_code, sic_description), used only for
    tickers not present in the curated CSV. Typically built from
    finlake.universe()'s `sic`/`sic_desc` columns by the caller.
    """
    curated = _load_curated(map_path)

    industry: dict[str, str] = {}
    sector: dict[str, str] = {}
    source: dict[str, str] = {}
    sic_lookup = sic_lookup or {}

    for t in tickers:
        t = t.upper()
        if t in curated:
            industry[t], sector[t] = curated[t]
            source[t] = "industry_map.csv"
        elif t in sic_lookup and sic_lookup[t][0]:
            sic_code, sic_desc = sic_lookup[t]
            industry[t] = sic_desc or f"SIC {sic_code}"
            sector[t] = _sic_division(sic_code)
            source[t] = "SIC fallback"
        else:
            industry[t] = "Unmapped"
            sector[t] = "Unmapped"
            source[t] = "unmapped"

    return PeerGroups(industry=industry, sector=sector, source=source)


def _load_curated(map_path: Path) -> dict[str, tuple[str, str]]:
    if not map_path.exists():
        raise FileNotFoundError(
            f"industry_map.csv not found at {map_path}. This file is "
            f"required (peer grouping needs it) -- it is not optional "
            f"like the universe constituents file."
        )
    df = pd.read_csv(map_path, dtype=str)
    missing_cols = REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        raise ValueError(f"{map_path} is missing columns: {sorted(missing_cols)}")

    df["ticker"] = df["ticker"].str.upper().str.strip()
    dupes = df["ticker"][df["ticker"].duplicated()]
    if len(dupes):
        raise ValueError(
            f"{map_path} has duplicate ticker rows: {sorted(set(dupes))}"
        )

    return {
        row["ticker"]: (row["industry"].strip(), row["sector"].strip())
        for _, row in df.iterrows()
    }


# Coarse SIC-division buckets, used only as a last-resort SECTOR label for
# names missing from industry_map.csv. Deliberately coarse — SIC divisions
# don't map cleanly onto GICS sectors, and pretending otherwise would be
# false precision. This exists so an unmapped name still gets a defensible
# fallback grouping instead of "Other", not so it gets an accurate one;
# the honest fix is always to add the name to industry_map.csv by hand.
_SIC_DIVISIONS: list[tuple[int, int, str]] = [
    (100, 999, "SIC: Agriculture"),
    (1000, 1499, "SIC: Mining"),
    (1500, 1799, "SIC: Construction"),
    (2000, 3999, "SIC: Manufacturing"),
    (4000, 4999, "SIC: Utilities & Transportation"),
    (5000, 5199, "SIC: Wholesale Trade"),
    (5200, 5999, "SIC: Retail Trade"),
    # SIC's single 6000-6799 "Finance, Insurance and Real Estate" division is
    # split here. GICS has treated Real Estate as its own sector since 2016,
    # and the split is not cosmetic: ROIC is gated OFF for financials because
    # "total debt + equity - cash" is not coherent invested capital for a
    # DEPOSIT-FUNDED balance sheet (DEC-002). A REIT is not deposit-funded —
    # it is an asset-heavy, debt-funded property owner, and invested capital
    # means the ordinary thing for it. Lumping the two would gate a metric off
    # for names it is valid for.
    (6000, 6499, "SIC: Financials"),      # banks, credit, brokers, insurance
    (6500, 6599, "SIC: Real Estate"),
    (6798, 6798, "SIC: Real Estate"),     # REITs specifically
    (6600, 6799, "SIC: Financials"),      # other holding/investment offices
    (7000, 8999, "SIC: Services"),
    (9100, 9999, "SIC: Public Administration"),
]

# The "SIC: " prefix exists so a reader can SEE that a grouping came from a
# coarse fallback rather than the curated map. That visibility is worth
# keeping — but it must never change what a label MEANS.
#
# It did. `is_financials_sector` compares sector.lower() == "financials", and
# "SIC: Financials".lower() is "sic: financials", so the gate silently stopped
# firing for every name that arrived through the fallback. With 42 names in
# the curated map and 454 on fallback, that meant 87 banks and insurers were
# scored with the INDUSTRIAL ROIC formula — the exact failure DEC-002 exists
# to prevent. MetLife came out at 844% ROIC, Northern Trust at 91%, Citigroup
# at 34%, and every one of those fed the Quality bucket's peer z-scores.
#
# The fix separates the display label from the canonical meaning. Compare on
# `canonical_sector()`, never on the raw string.
_FALLBACK_PREFIX = "SIC: "


def canonical_sector(sector: str | None) -> str:
    """The sector's meaning, independent of how it was derived.

    Strips the fallback marker and normalises case, so a gate written against
    "financials" fires whether the label came from the curated map or the SIC
    fallback. Every sector comparison in scoring must go through this.
    """
    if not sector:
        return ""
    text = str(sector).strip()
    if text.startswith(_FALLBACK_PREFIX):
        text = text[len(_FALLBACK_PREFIX):]
    return text.strip().lower()


def _sic_division(sic_code: str | None) -> str:
    if not sic_code or not str(sic_code).isdigit():
        return "Unmapped"
    n = int(sic_code)
    for lo, hi, label in _SIC_DIVISIONS:
        if lo <= n <= hi:
            return label
    return "Unmapped"
