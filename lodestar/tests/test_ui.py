"""Tests for the UI's pure logic (color/sign helpers, chart construction).
The Streamlit rendering itself is verified in a live browser during
development -- tokens applying, all views rendering against real
persisted runs -- which isn't practically re-runnable as a pytest; what's
testable here is the plain-Python logic behind the visuals.
"""

from __future__ import annotations

import re

import pytest
import pandas as pd

from lodestar.ui.charts import _bucket_bar_chart
from lodestar.ui.theme import (
    RED, RED_WASH, TEAL, TEAL_WASH, chip, html_table, score_bar, signed,
)


# ------------------------------------------------------------------ signed
def test_signed_positive_is_green_and_carries_a_plus():
    html = signed(1.5)
    assert "ls-pos" in html
    assert "+1.50" in html, "sign must be explicit so direction survives greyscale"


def test_signed_negative_is_red_and_carries_a_minus():
    html = signed(-0.25)
    assert "ls-neg" in html
    assert "-0.25" in html


def test_signed_zero_is_neutral():
    assert "ls-flat" in signed(0.0)


def test_signed_none_renders_a_dash_not_a_crash():
    html = signed(None)
    assert "—" in html, "a missing value must look missing, not render as 'None'"
    assert "ls-flat" in html


def test_signed_respects_format_and_suffix():
    assert "+12.3%" in signed(12.34, fmt="+.1f", suffix="%")


def test_signed_nan_renders_a_dash_not_the_string_nan():
    """Regression: pandas coerces None back to NaN inside a float column,
    so normalising a DataFrame with .where(notna, None) does not survive
    the round trip for numeric columns. Before this was fixed the UI
    rendered a literal '+nan' in every empty cell of the news and growth
    columns -- a missing value dressed up as data."""
    html = signed(float("nan"))
    assert "—" in html
    assert "nan" not in html.lower()


def test_score_bar_nan_renders_an_empty_track():
    html = score_bar(float("nan"))
    assert "ls-bar-track" in html
    assert "ls-bar-fill" not in html


def test_html_table_nan_in_a_text_column_renders_a_dash():
    cols = [{"key": "s", "label": "S"}]
    html = html_table(cols, [{"s": float("nan")}])
    assert "—" in html
    assert "nan" not in html.lower()


def test_html_table_every_cell_kind_handles_missing_values():
    """Each cell kind has its own rendering branch, so each needs its own
    missing-value guard -- 'sym' and 'rank' were both leaking a literal
    'nan' into the ratings table (best/worst ticker for a group with only
    one scored name) after 'num' and 'text' had already been fixed."""
    cols = [
        {"key": "t", "label": "T"},
        {"key": "n", "label": "N", "kind": "num", "fmt": ".2f"},
        {"key": "s", "label": "S", "kind": "sym"},
        {"key": "r", "label": "R", "kind": "rank"},
    ]
    for missing in (None, float("nan")):
        html = html_table(cols, [{"t": missing, "n": missing, "s": missing, "r": missing}])
        assert "nan" not in html.lower(), f"{missing!r} leaked through a cell kind"
        assert "None" not in html, f"{missing!r} leaked through a cell kind"
        assert html.count("—") == 4, "every one of the four cell kinds must show a dash"


# -------------------------------------------------------------------- chip
def test_chip_buy_is_teal_and_sell_is_red():
    buy = chip("STRONG BUY")
    sell = chip("STRONG SELL")
    assert TEAL in buy and TEAL_WASH in buy
    assert RED in sell and RED_WASH in sell
    assert "STRONG BUY" in buy


def test_chip_unknown_label_falls_back_without_crashing():
    html = chip("SOMETHING UNEXPECTED")
    assert "SOMETHING UNEXPECTED" in html
    assert "ls-chip" in html


# ------------------------------------------------------------------ charts
def test_bucket_bar_chart_encodes_sign_by_color():
    df = pd.DataFrame({
        "bucket": ["quality", "value", "growth"],
        "composite_contribution": [0.5, -0.3, 0.0],
    })
    fig = _bucket_bar_chart(df)
    colors = fig.data[0].marker.color
    assert set(colors) <= {TEAL, RED}
    # zero is treated as non-negative, matching the >= 0 convention used
    # throughout scoring (zscore.py's peer-group-of-one -> z=0.0).
    assert colors[-1] == TEAL or colors[0] == TEAL


def test_bucket_bar_chart_labels_carry_explicit_signs():
    df = pd.DataFrame({
        "bucket": ["quality", "value"],
        "composite_contribution": [0.5, -0.3],
    })
    fig = _bucket_bar_chart(df)
    assert all(t.startswith(("+", "-")) for t in fig.data[0].text)


# --------------------------------------------------------------- score_bar
def _bar_geometry(html: str) -> tuple[float, float]:
    """(left, width) in px from a rendered bar.

    Parsed rather than string-matched against a hardcoded constant: the track
    width is a design value that is allowed to change, while the PROPERTIES
    below — direction encodes sign, outliers clamp — are the things that must
    never change. A test pinned to "left:34.0px" fails on a deliberate
    restyle and says nothing about whether the encoding still works.
    """
    left = float(re.search(r"left:([\d.]+)px", html).group(1))
    width = float(re.search(r"width:([\d.]+)px", html).group(1))
    return left, width


def test_score_bar_grows_right_for_positive_and_left_for_negative():
    """Direction is a second, redundant encoding of sign -- the bar's
    offset from the center axis must differ for +/- values, so the table
    still reads correctly in greyscale."""
    pos, neg = score_bar(1.0), score_bar(-1.0)
    assert TEAL in pos and RED in neg

    pos_left, pos_width = _bar_geometry(pos)
    neg_left, neg_width = _bar_geometry(neg)

    # A positive bar starts AT the centre axis and extends right; a negative
    # one ends at the axis, so it starts a full bar-width to the left.
    centre = pos_left
    assert neg_left == pytest.approx(centre - neg_width), (
        "a negative bar does not end at the centre axis, so direction no "
        "longer encodes sign")
    assert pos_width == pytest.approx(neg_width), (
        "equal magnitudes rendered different widths")


def test_score_bar_clamps_beyond_scale_instead_of_overflowing():
    """An outlier must not blow the cell out. At and beyond `scale`, the bar
    is a full half-track and stops growing."""
    _, at_scale = _bar_geometry(score_bar(2.0, scale=2.0))
    _, huge = _bar_geometry(score_bar(99.0, scale=2.0))
    _, half = _bar_geometry(score_bar(1.0, scale=2.0))

    assert huge == pytest.approx(at_scale), "beyond-scale values kept growing"
    assert at_scale == pytest.approx(half * 2), (
        "the bar is not linear in value below the clamp")


def test_score_bar_none_renders_an_empty_track():
    html = score_bar(None)
    assert "ls-bar-track" in html
    assert "ls-bar-fill" not in html


# -------------------------------------------------------------- html_table
def test_html_table_renders_missing_values_as_a_dash():
    cols = [{"key": "a", "label": "A", "kind": "num", "fmt": ".2f"},
            {"key": "b", "label": "B"}]
    rows = [{"a": None, "b": None}]
    html = html_table(cols, rows)
    assert "—" in html
    assert "None" not in html, "a missing value must never render as the string 'None'"
    assert "nan" not in html.lower()


def test_html_table_html_cells_are_inserted_verbatim():
    cols = [{"key": "x", "label": "X", "kind": "html"}]
    rows = [{"x": '<span class="ls-pos">+1.00</span>'}]
    html = html_table(cols, rows)
    assert '<span class="ls-pos">+1.00</span>' in html
    assert "&lt;span" not in html, "html cells must not be escaped"


def test_html_table_numeric_columns_are_right_aligned():
    cols = [{"key": "n", "label": "N", "kind": "num", "fmt": ".1f"}]
    html = html_table(cols, [{"n": 1.0}])
    assert 'class="num"' in html


def test_bucket_bar_chart_sorts_by_contribution():
    df = pd.DataFrame({
        "bucket": ["a", "b", "c"],
        "composite_contribution": [0.9, -0.9, 0.1],
    })
    fig = _bucket_bar_chart(df)
    # sorted ascending, so the most negative bar comes first
    assert list(fig.data[0].y) == ["b", "c", "a"]


# ---------------------------------------------------------------------------
# NaN truthiness in the view layer.
# ---------------------------------------------------------------------------
def test_event_label_renders_missing_as_a_dash_not_the_text_nan():
    """Found by looking at the rendered page, not by a failing test.

    pandas gives a missing `event_class` as float('nan'), and NaN is TRUTHY in
    Python — so `if value:` passes it through and `str(nan)` renders the
    literal text "nan" in the Type column. It was live on three pages at once.

    html_table already guards its own cells, but a caller that FORMATS a value
    before handing it over bypasses that guard entirely and has to do its own.
    """
    from lodestar.ui.views import company, news, overview

    for module in (company, news, overview):
        assert module._event_label(float("nan")) == "—", module.__name__
        assert module._event_label(None) == "—", module.__name__
        assert module._event_label("earnings") == "Earnings", module.__name__
        # An unknown class is titled rather than dropped — a new event type
        # added upstream should still read as something.
        assert module._event_label("spin_off") == "Spin Off", module.__name__


def test_no_view_renders_a_bare_nan_through_truthiness():
    """A standing guard on the pattern rather than the instance: `if x` on a
    possibly-NaN pandas value is always wrong here."""
    import pathlib

    views = pathlib.Path(__file__).resolve().parent.parent / "lodestar" / "ui" / "views"
    offenders = []
    for path in views.glob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if 'if a.get("event_class")' in stripped and "is_missing" not in stripped:
                offenders.append(f"{path.name}:{i}")
    assert not offenders, (
        f"truthiness check on a possibly-NaN value: {offenders}")


def test_translucent_produces_rgba_not_eight_digit_hex():
    """Plotly rejects 8-digit hex outright, which CSS accepts happily — so a
    colour that works everywhere else in the theme took the whole price chart
    down with a ValueError. One palette, converted at the boundary."""
    from lodestar.ui.charts import translucent

    assert translucent("#2dd4bf", 0.08) == "rgba(45,212,191,0.08)"
    assert translucent("2dd4bf", 1) == "rgba(45,212,191,1)"
    assert not translucent(TEAL, 0.5).startswith("#")


# ---------------------------------------------------------------------------
# Clickable tickers
# ---------------------------------------------------------------------------
def test_ticker_link_points_at_the_company_page():
    from lodestar.ui.theme import ticker_link

    html = ticker_link("aapl")
    assert 'href="?page=Company&ticker=AAPL"' in html
    assert ">AAPL<" in html, "the symbol should display uppercase"
    assert 'target="_self"' in html, (
        "without target=_self some browsers open the embedded frame's links "
        "in a new tab")


def test_ticker_link_handles_missing():
    from lodestar.ui.theme import ticker_link

    assert ticker_link(None) == "—"
    assert ticker_link(float("nan")) == "—"


def test_ticker_links_splits_a_joined_list():
    """News rows carry several symbols in one field. Linking the joined
    string would produce one dead link to a symbol that does not exist."""
    from lodestar.ui.theme import ticker_links

    html = ticker_links("AAPL,MSFT")
    assert 'ticker=AAPL' in html and 'ticker=MSFT' in html
    assert html.count("<a") == 2

    many = ticker_links("A,B,C,D,E,F", limit=2)
    assert many.count("<a") == 2
    assert "+4" in many, "the overflow count should say how many are hidden"


def test_ticker_cells_sort_on_the_symbol_not_the_markup():
    """The cell contains an anchor tag; sorting on rendered text would order
    by '<a class=...' and put every row in the same place."""
    cols = [{"key": "t", "label": "Ticker", "kind": "ticker"}]
    html = html_table(cols, [{"t": "MSFT"}])
    assert 'data-sort="MSFT"' in html


def test_every_view_links_its_ticker_column():
    """A standing guard: a new table that renders a symbol as plain text is
    a dead end for the reader."""
    import pathlib

    views = pathlib.Path(__file__).resolve().parent.parent / "lodestar" / "ui" / "views"
    plain = []
    for path in views.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in ('"label": "Ticker", "kind": "sym"',
                       '"label": "Best", "kind": "sym"',
                       '"label": "Worst", "kind": "sym"'):
            if marker in text:
                plain.append(f"{path.name}: {marker}")
    assert not plain, f"unlinked ticker columns: {plain}"


# ---------------------------------------------------------------------------
# News source citation
# ---------------------------------------------------------------------------
def test_source_label_names_both_the_publisher_and_the_feed():
    """Two different facts, and each matters. The publisher is who is
    accountable for the claim; the feed is how it reached us and what its
    reliability weight was based on. A Reuters story that arrived through an
    aggregator is still a Reuters story, and citing only the aggregator would
    hide the byline that carries the credibility."""
    from lodestar.ui.views import news

    both = news._source_label("Reuters", "google_news")
    assert "Reuters" in both and "Google News" in both and "via" in both

    # When the byline IS the feed, don't say it twice.
    same = news._source_label("Yahoo Finance", "yahoo")
    assert same.count("Yahoo Finance") == 1
    assert "via" not in same


def test_source_label_falls_back_to_the_feed_when_there_is_no_byline():
    from lodestar.ui.views import news

    assert news._source_label(None, "sec") == "SEC EDGAR"
    assert news._source_label(float("nan"), "yahoo") == "Yahoo Finance"
    assert news._source_label("", "google_news") == "Google News"


def test_every_news_table_cites_its_source():
    """A headline without an attribution is a claim with no author."""
    import pathlib

    views = pathlib.Path(__file__).resolve().parent.parent / "lodestar" / "ui" / "views"
    for name in ("news.py", "company.py", "overview.py"):
        text = (views / name).read_text(encoding="utf-8")
        assert "_source_label(" in text, f"{name} renders news without citing a source"
        assert '{"key": "source", "label": "Source"' in text, (
            f"{name} has no Source column")
