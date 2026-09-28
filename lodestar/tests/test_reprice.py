"""Re-pricing a run's stored valuation ratios to the latest close.

WHAT THIS FIXES. `run_ratios` is snapshotted when a run executes, and every
price-based figure in it was computed at the close on each name's last FISCAL
QUARTER END. So the screener's market caps were stale by up to a quarter —
and by a DIFFERENT amount for every company, depending on when its quarter
happened to fall. That reached the market-cap band filter (names sorted into
the wrong size bucket), the maximum-P/E filter, the P/E and yield columns, and
the total market cap at the top of the page.

Every ratio involved is linear in price, so re-pricing is exact arithmetic on
stored numbers, not a re-derivation. These tests pin that exactness.
"""

from __future__ import annotations

import pandas as pd
import pytest

from lodestar.ui.data import reprice

# One name, priced at 50 in the snapshot and 80 now: a factor of 1.6.
# Market cap 1,000 with 400 of net debt, so enterprise value is 1,400.
SNAPSHOT = pd.DataFrame([{
    "ticker": "AAA",
    "price": 50.0,
    "market_cap": 1_000.0,
    "enterprise_value": 1_400.0,
    "pe_ttm": 10.0,
    "pe_forward": 8.0,
    "ps": 2.0,
    "pb": 4.0,
    "peg": 1.0,
    "ev_ebitda": 14.0,
    "fcf_yield": 0.05,
    "dividend_yield": 0.02,
    # Fundamentals: these must not move at all.
    "gross_margin": 0.40,
    "operating_margin": 0.25,
    "roic": 0.18,
    "debt_to_equity": 0.5,
}])

QUOTES = {"AAA": {"price": 80.0, "as_of": "2026-08-10"}}
FACTOR = 80.0 / 50.0


def _row(frame):
    return frame.iloc[0]


def test_price_and_market_cap_move_by_the_price_ratio():
    row = _row(reprice(SNAPSHOT, QUOTES))
    assert row["price"] == pytest.approx(80.0)
    assert row["market_cap"] == pytest.approx(1_000.0 * FACTOR)


def test_multiples_scale_up_and_yields_scale_down():
    """A multiple is price over a filed figure and a yield is its reciprocal,
    so they move in opposite directions. Scaling both the same way is the
    error that makes a stock look cheaper AND higher-yielding as it rallies.
    """
    row = _row(reprice(SNAPSHOT, QUOTES))
    for key in ("pe_ttm", "pe_forward", "ps", "pb", "peg"):
        assert row[key] == pytest.approx(SNAPSHOT.iloc[0][key] * FACTOR), key
    for key in ("fcf_yield", "dividend_yield"):
        assert row[key] == pytest.approx(SNAPSHOT.iloc[0][key] / FACTOR), key


def test_enterprise_value_reprices_only_its_equity_half():
    """Net debt is a filed figure. Scaling the whole of enterprise value by
    the price ratio marks a company's debt to market as its shares move,
    which overstates EV for anything leveraged — here it would give 2,240
    against a correct 2,000.
    """
    row = _row(reprice(SNAPSHOT, QUOTES))
    equity_delta = 1_000.0 * (FACTOR - 1.0)        # +600
    assert row["enterprise_value"] == pytest.approx(1_400.0 + equity_delta)
    assert row["enterprise_value"] == pytest.approx(2_000.0)
    assert row["enterprise_value"] != pytest.approx(1_400.0 * FACTOR)


def test_ev_ebitda_follows_enterprise_value_not_the_price():
    """Same reason: EV/EBITDA rises by the EV ratio (2000/1400), not by the
    price ratio (1.6)."""
    row = _row(reprice(SNAPSHOT, QUOTES))
    assert row["ev_ebitda"] == pytest.approx(14.0 * (2_000.0 / 1_400.0))


def test_pure_fundamentals_do_not_move_when_the_stock_does():
    row = _row(reprice(SNAPSHOT, QUOTES))
    for key in ("gross_margin", "operating_margin", "roic", "debt_to_equity"):
        assert row[key] == pytest.approx(SNAPSHOT.iloc[0][key]), key


def test_the_basis_each_row_ended_up_on_is_recorded():
    """So the page can say which close it is showing rather than implying
    'now'."""
    assert _row(reprice(SNAPSHOT, QUOTES))["price_as_of"] == "2026-08-10"


def test_a_name_with_no_quote_keeps_its_snapshot_values():
    """Partial coverage must not blank the row or mix two bases silently."""
    out = reprice(SNAPSHOT, {"ZZZ": {"price": 12.0, "as_of": "2026-08-10"}})
    row = _row(out)
    assert row["price"] == pytest.approx(50.0)
    assert row["market_cap"] == pytest.approx(1_000.0)
    assert row["pe_ttm"] == pytest.approx(10.0)
    assert pd.isna(row["price_as_of"])


def test_a_zero_or_missing_snapshot_price_is_left_alone():
    """There is no ratio to scale by. Dividing anyway produces infinities
    that sort straight to the top of a 'most expensive' screen."""
    frame = SNAPSHOT.copy()
    frame.loc[0, "price"] = 0.0
    row = _row(reprice(frame, QUOTES))
    assert row["market_cap"] == pytest.approx(1_000.0)
    assert row["pe_ttm"] == pytest.approx(10.0)


def test_an_empty_frame_or_no_quotes_is_a_no_op():
    assert reprice(pd.DataFrame(), QUOTES).empty
    unchanged = reprice(SNAPSHOT, {})
    assert unchanged["market_cap"].iloc[0] == pytest.approx(1_000.0)


def test_the_newest_run_is_current_whatever_the_date_says(tmp_path):
    """WHICH RUN, NOT WHICH DAY.

    The first version of this test asked whether the run's date was today. It
    is wrong in the ordinary case rather than an edge case: you score
    overnight and read it the next morning, so the newest run is almost always
    dated yesterday. That flipped the entire hub back to quarter-end pricing —
    the bug all of this exists to fix — and Microsoft's header returned to
    $373 the morning after a correct run.

    A run is historical only when a NEWER one exists and you deliberately
    select the older one.
    """
    from lodestar import persistence
    from lodestar.ui import data as D

    path = tmp_path / "runs.db"
    with persistence.connect(path) as conn:
        for run_id, as_of in (("2020-01-01__old", "2020-01-01"),
                              ("2026-08-10__new", "2026-08-10")):
            conn.execute(
                "INSERT INTO runs (run_id, as_of, config_hash, universe_size, "
                "universe_source, created_at) VALUES (?,?,?,?,?,?)",
                (run_id, as_of, "h", 1, "test", f"{as_of}T00:00:00"))
        conn.commit()

    D.list_runs.clear()
    newest = {"run_id": "2026-08-10__new", "as_of": "2026-08-10"}
    older = {"run_id": "2020-01-01__old", "as_of": "2020-01-01"}

    assert D.is_current(str(path), newest) is True, (
        "the newest run was treated as historical, which turns off live "
        "pricing for the whole hub")
    assert D.is_current(str(path), older) is False, (
        "a deliberately-selected older run must keep its own prices")


def test_repricing_is_idempotent_against_the_same_close():
    """Running it twice must not compound. Streamlit reruns the whole script
    on every interaction, so anything that mutated cumulatively here would
    drift a little further with each click."""
    once = reprice(SNAPSHOT, QUOTES)
    twice = reprice(once, QUOTES)
    assert twice["market_cap"].iloc[0] == pytest.approx(once["market_cap"].iloc[0])
    assert twice["pe_ttm"].iloc[0] == pytest.approx(once["pe_ttm"].iloc[0])
