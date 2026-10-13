"""Tests for lodestar.industry_map: the curated CSV lookup, the SIC
fallback, and the "never silently lump into Other" guarantee."""

from __future__ import annotations

import pytest

from lodestar.industry_map import _sic_division, load_peer_groups


def _write_csv(tmp_path, rows: str):
    p = tmp_path / "industry_map.csv"
    p.write_text("ticker,industry,sector\n" + rows)
    return p


def test_curated_lookup(tmp_path):
    path = _write_csv(tmp_path, "AAPL,Technology Hardware,Information Technology\n")
    pg = load_peer_groups(["AAPL"], map_path=path)
    assert pg.industry["AAPL"] == "Technology Hardware"
    assert pg.sector["AAPL"] == "Information Technology"
    assert pg.source["AAPL"] == "industry_map.csv"


def test_sic_fallback_used_when_not_in_curated_csv(tmp_path):
    path = _write_csv(tmp_path, "AAPL,Technology Hardware,Information Technology\n")
    pg = load_peer_groups(
        ["AAPL", "TSM"], map_path=path,
        sic_lookup={"TSM": ("3674", "Semiconductors & Related Devices")},
    )
    assert pg.source["TSM"] == "SIC fallback"
    assert pg.industry["TSM"] == "Semiconductors & Related Devices"
    assert pg.sector["TSM"] == "SIC: Manufacturing"


def test_truly_unmapped_ticker_is_labeled_not_hidden(tmp_path):
    """A name with no curated entry AND no SIC data must still get an
    explicit label -- never silently dropped, never silently lumped in
    with something else."""
    path = _write_csv(tmp_path, "AAPL,Technology Hardware,Information Technology\n")
    pg = load_peer_groups(["AAPL", "ZZZZ"], map_path=path, sic_lookup={})
    assert pg.source["ZZZZ"] == "unmapped"
    assert pg.industry["ZZZZ"] == "Unmapped"
    assert pg.sector["ZZZZ"] == "Unmapped"


def test_missing_required_column_raises(tmp_path):
    p = tmp_path / "industry_map.csv"
    p.write_text("ticker,industry\nAAPL,Technology Hardware\n")  # no sector column
    with pytest.raises(ValueError, match="missing columns"):
        load_peer_groups(["AAPL"], map_path=p)


def test_duplicate_ticker_raises(tmp_path):
    path = _write_csv(
        tmp_path,
        "AAPL,Technology Hardware,Information Technology\n"
        "AAPL,Something Else,Some Sector\n",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_peer_groups(["AAPL"], map_path=path)


def test_missing_file_raises_not_silently_empty(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_peer_groups(["AAPL"], map_path=tmp_path / "does_not_exist.csv")


def test_ticker_matching_is_case_insensitive(tmp_path):
    path = _write_csv(tmp_path, "aapl,Technology Hardware,Information Technology\n")
    pg = load_peer_groups(["AAPL"], map_path=path)
    assert pg.industry["AAPL"] == "Technology Hardware"


@pytest.mark.parametrize("sic,expected", [
    ("3674", "SIC: Manufacturing"),
    ("6021", "SIC: Financials"),
    ("2911", "SIC: Manufacturing"),
    ("7372", "SIC: Services"),
    (None, "Unmapped"),
    ("not-a-number", "Unmapped"),
])
def test_sic_division_boundaries(sic, expected):
    assert _sic_division(sic) == expected


# ---------------------------------------------------------------------------
# canonical_sector — the fix for a live scoring bug.
# ---------------------------------------------------------------------------
def test_canonical_sector_strips_the_fallback_marker():
    """The "SIC: " prefix exists so a reader can SEE that a grouping came
    from a coarse fallback. That visibility is worth keeping — but it must
    never change what a label MEANS."""
    from lodestar.industry_map import canonical_sector

    assert canonical_sector("Financials") == "financials"
    assert canonical_sector("SIC: Financials") == "financials"
    assert canonical_sector("  SIC: Financials  ") == "financials"
    assert canonical_sector("Real Estate") == "real estate"
    assert canonical_sector("SIC: Real Estate") == "real estate"
    assert canonical_sector(None) == ""
    assert canonical_sector("") == ""


def test_the_financials_gate_fires_regardless_of_where_the_label_came_from():
    """THE REGRESSION THIS EXISTS FOR.

    `is_financials_sector` compared sector.lower() == "financials", and
    "SIC: Financials".lower() is "sic: financials". The gate silently stopped
    firing for every name that arrived through the SIC fallback rather than
    the curated map.

    It was live: 454 of 503 names were on the fallback, so 87 banks and
    insurers had the INDUSTRIAL ROIC formula applied to a deposit-funded
    balance sheet. MetLife scored 844% ROIC, Northern Trust 91%, Citigroup
    34% — and every one of them then fed the Quality bucket's peer z-scores,
    so the damage was not confined to those names.
    """
    import pandas as pd

    from lodestar.metrics.base import TickerContext
    from lodestar.metrics.finance import is_financials_sector

    def ctx(sector):
        return TickerContext(
            ticker="X", as_of="2026-08-10", cik=1, fundamentals=pd.DataFrame(),
            prices=pd.DataFrame(), sector=sector, industry="Banks")

    assert is_financials_sector(ctx("Financials"))
    assert is_financials_sector(ctx("SIC: Financials")), (
        "a bank grouped by SIC fallback escaped the financials gate")
    assert is_financials_sector(ctx("financials"))
    assert not is_financials_sector(ctx("Information Technology"))
    assert not is_financials_sector(ctx("SIC: Manufacturing"))


def test_reits_are_not_lumped_into_financials():
    """SIC's single 6000-6799 division covers Finance, Insurance AND Real
    Estate. The split matters: ROIC is gated off for financials because
    "debt + equity - cash" is not coherent invested capital for a
    DEPOSIT-FUNDED balance sheet. A REIT is not deposit-funded — it is an
    asset-heavy, debt-funded property owner — so gating it would remove a
    metric that is valid for it.
    """
    from lodestar.industry_map import _sic_division, canonical_sector

    assert canonical_sector(_sic_division("6020")) == "financials"   # bank
    assert canonical_sector(_sic_division("6311")) == "financials"   # life insurance
    assert canonical_sector(_sic_division("6798")) == "real estate"  # REIT
    assert canonical_sector(_sic_division("6500")) == "real estate"
