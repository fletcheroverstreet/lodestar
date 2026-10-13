"""Universe construction: which tickers get scored on a given run.

Two sources:
  1. finlake.universe(as_of=...) — the filing-activity-proxy investable
     universe (see ISSUE-004 in finlake's vault, F10 in
     FINLAKE-FINDINGS.md). Honest, but not real index membership.
  2. An optional constituents CSV (config.yaml: universe.constituents_file)
     — when set, this REPLACES finlake's proxy entirely, on the theory
     that a user who supplies a real constituent list wants exactly those
     names, not a further-filtered subset of finlake's filing-activity
     guess.

Either way, the resolved universe is printed at the start of every run.
The project spec is explicit about this: a ranked table is not
trustworthy if you don't know exactly what universe produced it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import adapter
from .config import Config

# EDGAR electronic filing was phased in between 1993 and 1996; before that
# there is no machine-readable filing to find, for any company. An as-of date
# earlier than this is not "a date finlake hasn't built yet", it is a date
# where the underlying record cannot exist — which is the difference between
# an incomplete cache and a claim about the past.
EDGAR_EPOCH = "1993-01-01"


@dataclass(frozen=True)
class UniverseResult:
    as_of: str
    tickers: list[str]
    source: str
    finlake_universe_size: int
    # ticker -> (sic_code, sic_description), from finlake.universe()'s own
    # columns. Used as industry_map's SIC fallback input; empty for any
    # ticker that came only from a constituents file finlake has never
    # seen (no submissions/facts built for it yet).
    sic_by_ticker: dict[str, tuple[str | None, str | None]]


def build_universe(config: Config, *, as_of: str) -> UniverseResult:
    finlake_df = adapter.get_universe(as_of)
    finlake_tickers = (
        sorted(finlake_df["ticker"].tolist()) if not finlake_df.empty else []
    )
    sic_by_ticker: dict[str, tuple[str | None, str | None]] = {}
    if not finlake_df.empty:
        for _, row in finlake_df.iterrows():
            sic_by_ticker[str(row["ticker"]).upper()] = (row.get("sic"), row.get("sic_desc"))

    if config.universe_constituents_file is not None:
        listed = _read_constituents(config.universe_constituents_file)
        # MEMBERSHIP comes from the constituents file (DEC-011: a user who
        # supplies a real constituent list wants those names, not a
        # further-filtered subset of finlake's filing-activity guess).
        #
        # EXISTENCE still does not. A company that had not filed with the SEC
        # by `as_of` cannot appear in a screen dated `as_of`, whatever any
        # constituent list says — that is the point-in-time guarantee the
        # whole data layer is built on, and it is not a membership opinion.
        #
        # Without this bound a 1990 run returned all 503 of TODAY'S S&P 500,
        # including companies that IPO'd thirty years later. Constituent
        # files are current-membership snapshots with no add/drop dates, so
        # the lookahead is invisible: every name looks like a legitimate
        # index member, because it is one — now.
        #
        # This removes lookahead. It does NOT remove survivorship bias: names
        # dropped from the index for doing badly are simply absent from the
        # file. That limitation is real, is stated in the file's own header,
        # and is reported in `source` so a run never quietly implies
        # otherwise.
        # The test of a name is "could it have existed on this date", NOT
        # "has finlake built data for it". Those are different questions and
        # conflating them breaks one guarantee or the other:
        #
        #   in the as-of universe        -> it was filing then. Keep.
        #   absent as-of, present today  -> it exists NOW but was not filing
        #                                   on that date. Provable lookahead.
        #                                   Drop.
        #   absent from both             -> finlake has never heard of it, so
        #                                   there is no evidence either way.
        #                                   Keep (DEC-011) and let the
        #                                   coverage floor exclude it
        #                                   visibly, rather than having a
        #                                   ticker vanish from the user's own
        #                                   list for an unstated reason.
        filing_on_date = set(finlake_tickers)
        latest = max(as_of, dt.date.today().isoformat())
        known_at_all = filing_on_date | _tickers_known_to_finlake(latest)

        if as_of < EDGAR_EPOCH:
            # Nothing was filed electronically before EDGAR, so "finlake has
            # no record of this ticker" cannot mean "not built yet" here — it
            # means the filing does not exist and never will. Passing unknown
            # tickers through on a pre-EDGAR date would hand back today's
            # index membership as though it were 1990's.
            tickers = []
        else:
            tickers = [t for t in listed
                       if t in filing_on_date or t not in known_at_all]
        dropped = len(listed) - len(tickers)
        source = f"constituents file: {config.universe_constituents_file}"
        if dropped:
            source += (f" (−{dropped} of {len(listed)}: not yet filing "
                       f"on {as_of})")
    else:
        tickers = finlake_tickers
        source = "finlake.universe() (filing-activity proxy — see ISSUE-004)"

    return UniverseResult(
        as_of=as_of,
        tickers=tickers,
        source=source,
        finlake_universe_size=len(finlake_tickers),
        sic_by_ticker=sic_by_ticker,
    )


def _tickers_known_to_finlake(as_of: str) -> set[str]:
    """Every ticker finlake has any record of, as of the latest date.

    Used only to tell "this company did not exist yet" apart from "finlake has
    no data for this company". Cached per date because `build_universe` is
    called once per run but the answer is identical within one.
    """
    df = adapter.get_universe(as_of)
    if df.empty:
        return set()
    return {str(t).upper() for t in df["ticker"].tolist()}


def _read_constituents(path: Path) -> list[str]:
    """Tickers from a constituents CSV.

    `comment="#"` because a constituents file needs to carry provenance with
    it — which index, captured when, and above all whether it has add/drop
    dates. A current-membership list applied to a past date reintroduces
    survivorship bias, and that warning belongs in the file rather than only
    in whatever documentation happens to be nearby. Without this the header
    comments are parsed as data and the whole file fails to load.
    """
    df = pd.read_csv(path, comment="#")
    if "ticker" not in df.columns:
        raise ValueError(f"constituents file {path} must have a 'ticker' column")
    tickers = df["ticker"].dropna().astype(str).str.strip().str.upper()
    return sorted(t for t in tickers.unique().tolist() if t)


def print_universe(result: UniverseResult) -> None:
    """Print exactly what the universe is — required at the start of
    every `lodestar run`, per the project spec."""
    print(f"Universe as of {result.as_of}: {len(result.tickers)} names")
    print(f"  source: {result.source}")
    if result.source.startswith("constituents file"):
        print(f"  (finlake.universe() would have returned "
              f"{result.finlake_universe_size} names on its own)")
    print(f"  {', '.join(result.tickers)}")
