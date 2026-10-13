"""Buy / sell ratings — for individual stocks, industries, and sectors.

A headline page rather than a sub-tab, because it is the question most people
open the hub to answer.

The one idea worth understanding here: **every name carries two ratings and
they are deliberately not merged.** `rating` is against the whole universe;
`peer_rating` is against the name's own industry. Where they disagree is the
interesting case — the best house in a bad neighbourhood, or a mediocre
business in a sector that is on fire. Averaging them into one number destroys
exactly the information a reader came for.
"""

from __future__ import annotations

import streamlit as st

from ...scoring.ratings import (
    groups_to_frame, names_to_frame, rate_industries, rate_names, rate_sectors,
)
from .. import charts
from .. import data as D
from ..theme import (
    chip, esc, html_table, is_missing, score_bar, signed, stat_strip,
)

BUY_RATINGS = ("BUY", "STRONG BUY")
SELL_RATINGS = ("SELL", "STRONG SELL")


def _rating_cards(ratings, *, limit: int = 8, per_row: int = 4) -> None:
    rated = [r for r in ratings if r.mean_score is not None][:limit]
    if not rated:
        return
    cols = st.columns(per_row)
    for i, r in enumerate(rated):
        with cols[i % per_row]:
            st.markdown(
                f'<div class="ls-card" style="padding:0.9rem 1.1rem">'
                f'<div class="ls-card-label">{esc(r.group)}</div>'
                f'{chip(r.rating)}'
                f'<div class="ls-card-value">{signed(r.mean_score)}</div>'
                f'<div class="ls-card-note">{r.buy_count} buy · '
                f'{r.sell_count} sell · {r.scored_count} rated</div>'
                f'</div>', unsafe_allow_html=True)


def _group_table(frame, label_col: str, *, max_height: int = 620,
                 logos: dict | None = None) -> str:
    rows = []
    for _, r in frame.iterrows():
        rows.append({
            "group": r.get(label_col),
            "rating": chip(r.get("rating", "INSUFFICIENT DATA")),
            "sort_rating": r.get("mean_score"),
            "mean": signed(r.get("mean_score"), fmt="+.3f"),
            "bar": score_bar(r.get("mean_score")),
            "median": r.get("median_score"),
            "spread": r.get("dispersion"),
            "buys": r.get("buys"),
            "sells": r.get("sells"),
            "n": r.get("scored"),
            "best": r.get("best"),
            "worst": r.get("worst"),
        })
    return html_table(
        [{"key": "group", "label": label_col.title(), "cls": ""},
         {"key": "rating", "label": "Rating", "kind": "html",
          "sort": "sort_rating"},
         {"key": "mean", "label": "Mean", "kind": "html", "cls": "num",
          "sort": "sort_rating"},
         {"key": "bar", "label": "", "kind": "html", "nosort": True},
         {"key": "median", "label": "Median", "kind": "num", "fmt": "+.3f"},
         {"key": "spread", "label": "Spread", "kind": "num", "fmt": ".3f"},
         {"key": "buys", "label": "Buys", "kind": "num", "fmt": ".0f"},
         {"key": "sells", "label": "Sells", "kind": "num", "fmt": ".0f"},
         {"key": "n", "label": "Rated", "kind": "num", "fmt": ".0f"},
         {"key": "best", "label": "Best", "kind": "ticker"},
         {"key": "worst", "label": "Worst", "kind": "ticker"}],
        rows, max_height=max_height, logos=logos)


def render(*, db_path: str, run: dict) -> None:
    table = D.composite_table(db_path, run["run_id"])
    if table.empty:
        st.warning("No scored names in this run.")
        return

    name_ratings = rate_names(table)
    ndf = names_to_frame(name_ratings)

    counts = ndf["rating"].value_counts()
    st.markdown(stat_strip([
        ("Strong buy", f"{int(counts.get('STRONG BUY', 0))}"),
        ("Buy", f"{int(counts.get('BUY', 0))}"),
        ("Hold", f"{int(counts.get('HOLD', 0))}"),
        ("Sell", f"{int(counts.get('SELL', 0))}"),
        ("Strong sell", f"{int(counts.get('STRONG SELL', 0))}"),
        ("Not rated", f"{int(counts.get('INSUFFICIENT DATA', 0))}"),
    ]), unsafe_allow_html=True)

    st.caption(
        f"Ratings are a readable label on the same composite scores the "
        f"ranking engine already produces — as of {esc(run['as_of'])}. They "
        f"are **not live**: there is no streaming quote feed here, and a "
        f"rating changes when you re-run after new prices or filings land.")

    tab_stocks, tab_ind, tab_sector = st.tabs(
        ["Individual stocks", "Industries", "Sectors"])

    # ---- individual names ---------------------------------------------
    with tab_stocks:
        st.caption(
            "**Rating** is against the whole universe. **Peer rating** is "
            "against the name's own industry. Where the two disagree is the "
            "interesting case — a strong business in a weak industry, or the "
            "best house in a bad neighbourhood. They are kept separate on "
            "purpose; averaging them would destroy that.")

        f1, f2, f3 = st.columns([2, 1, 1])
        with f1:
            sectors = sorted(ndf["sector"].dropna().unique())
            sel = st.multiselect("Sector", sectors, default=[],
                                  placeholder="All sectors",
                                  key="rat_sector")
        with f2:
            band = st.selectbox("Show", ["All", "Buy-rated only",
                                          "Sell-rated only", "Disagreements"],
                                 key="rat_band",
                                 help="Disagreements: the universe rating and "
                                      "the peer rating point different ways.")
        with f3:
            st.markdown("&nbsp;", unsafe_allow_html=True)

        view = ndf[ndf["sector"].isin(sel)] if sel else ndf
        if band == "Buy-rated only":
            view = view[view["rating"].isin(BUY_RATINGS)]
        elif band == "Sell-rated only":
            view = view[view["rating"].isin(SELL_RATINGS)]
        elif band == "Disagreements":
            view = view[
                (view["rating"].isin(BUY_RATINGS)
                 & view["peer_rating"].isin(SELL_RATINGS))
                | (view["rating"].isin(SELL_RATINGS)
                   & view["peer_rating"].isin(BUY_RATINGS))]
            if view.empty:
                st.info(
                    "No name is buy-rated against the universe and sell-rated "
                    "against its own industry, or vice versa, in this run. "
                    "That is a normal outcome, not an error.")

        rows = []
        for _, r in view.iterrows():
            rank = (None if is_missing(r.get("peer_rank"))
                    or is_missing(r.get("peer_count"))
                    else f"{int(r['peer_rank'])} / {int(r['peer_count'])}")
            rows.append({
                "ticker": r.get("ticker"),
                "industry": r.get("industry"),
                "rating": chip(r.get("rating", "INSUFFICIENT DATA")),
                "sort_rating": r.get("score"),
                "score": signed(r.get("score"), fmt="+.3f"),
                "bar": score_bar(r.get("score")),
                "percentile": r.get("percentile"),
                "peer_rating": chip(r.get("peer_rating", "INSUFFICIENT DATA")),
                "sort_peer": r.get("peer_z"),
                "peer_z": signed(r.get("peer_z"), fmt="+.2f"),
                "peer_rank": rank,
            })
        st.markdown(html_table(
            [{"key": "ticker", "label": "Ticker", "kind": "ticker"},
             {"key": "industry", "label": "Industry"},
             {"key": "rating", "label": "Rating", "kind": "html",
              "sort": "sort_rating"},
             {"key": "score", "label": "Score", "kind": "html", "cls": "num",
              "sort": "sort_rating"},
             {"key": "bar", "label": "", "kind": "html", "nosort": True},
             {"key": "percentile", "label": "Pctile", "kind": "num", "fmt": ".0f"},
             {"key": "peer_rating", "label": "Peer rating", "kind": "html",
              "sort": "sort_peer"},
             {"key": "peer_z", "label": "Peer z", "kind": "html", "cls": "num",
              "sort": "sort_peer"},
             {"key": "peer_rank", "label": "Rank in industry"}],
            rows, max_height=640,
            logos=D.logo_uris(view["ticker"].dropna())), unsafe_allow_html=True)

    # ---- industries -----------------------------------------------------
    with tab_ind:
        industry_ratings = rate_industries(table, as_of=run["as_of"])
        _rating_cards(industry_ratings)
        idf = groups_to_frame(industry_ratings)
        st.markdown(_group_table(
            idf, "industry",
            logos=D.logo_uris(
                [t for col in ("best", "worst") if col in idf
                 for t in idf[col].dropna()])), unsafe_allow_html=True)
        st.caption(
            "Spread is the standard deviation of member scores. A high spread "
            "with a neutral rating means the group is a stock-picker's "
            "problem rather than a sector call — and the buy/sell counts show "
            "whether the mean is a consensus or an average of extremes. A "
            "group with fewer than two scored members reports INSUFFICIENT "
            "DATA rather than a one-name opinion dressed up as an industry "
            "view.")
        st.plotly_chart(
            charts.bucket_bar_chart(
                groups_to_frame(industry_ratings)
                .rename(columns={"industry": "bucket",
                                 "mean_score": "composite_contribution"})
                .dropna(subset=["composite_contribution"])),
            width="stretch", config={"displayModeBar": False})

    # ---- sectors --------------------------------------------------------
    with tab_sector:
        sector_ratings = rate_sectors(table, as_of=run["as_of"])
        _rating_cards(sector_ratings)
        sdf = groups_to_frame(sector_ratings)
        st.markdown(_group_table(
            sdf, "sector", max_height=460,
            logos=D.logo_uris(
                [t for col in ("best", "worst") if col in sdf
                 for t in sdf[col].dropna()])), unsafe_allow_html=True)
        st.markdown("###### Score distribution by industry")
        st.plotly_chart(
            charts.distribution_box(table, group_col="industry",
                                    value_col="composite_raw"),
            width="stretch", config={"displayModeBar": False})
        st.caption(
            "Each box is one industry's spread of composite scores. A whole "
            "industry sitting high or low is a sector-wide signal; a wide box "
            "means the individual name matters more than the industry does.")
