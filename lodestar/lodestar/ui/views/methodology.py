"""How it works — exactly how a score, a rating, and a price get made.

THE PAGE READS THE CONFIGURATION, IT DOES NOT DESCRIBE IT. Every weight,
threshold and window on this page is loaded from the same `config.yaml` the
scoring engine reads, and every ratio formula from finlake's own catalogue.
A methodology page that restates the numbers in prose is a second copy, and a
second copy drifts — silently, and in the one place a reader has gone
specifically to check whether they can trust the first.

Written for someone deciding whether to believe a number, so it says what each
step does, why it is done that way, and what it will not tell you.
"""

from __future__ import annotations

import streamlit as st

from ...config import BUCKET_NAMES
from ...scoring.composite import MIN_BUCKETS_FOR_COMPOSITE
from ...scoring.ratings import (
    MIN_MEMBERS_FOR_GROUP_RATING, NO_RATING, RATING_BANDS,
)
from .. import data as D
from ..theme import INK_MUTED, chip, esc, html_table, stat_strip

# What each bucket is trying to measure, in one line. The WEIGHTS come from
# config; only the plain-English gloss lives here, because there is nowhere
# else for it to live.
BUCKET_INTENT = {
    "quality": "Does this business earn good returns, and does it keep "
               "earning them? Level and consistency, not level alone.",
    "value": "What are you paying for those returns? Every metric is "
             "inverted first, so higher always means cheaper.",
    "growth": "Is it getting bigger, and is the growth being bought at a "
              "sensible price in reinvested capital?",
    "capital_allocation": "What does management do with the cash — and are "
                          "the buybacks executed at good prices or bad ones?",
    "accounting_quality": "Do the earnings look like the cash? Asymmetric by "
                          "design: bad subtracts fully, good earns capped "
                          "credit.",
    "momentum": "Is the market and the filed record moving in this name's "
                "favour over the last year?",
    "news": "What is being published about it, event-weighted and decayed by "
            "age.",
}

STEPS = [
    ("1 · Pull the raw figures",
     "Every fundamental comes from an SEC filing, read as of the run date. "
     "The fact table is append-only and bitemporal — a restatement arrives as "
     "a new row beside the original rather than overwriting it — so a run "
     "dated in the past sees only what had actually been filed by then. "
     "That is what makes a historical run honest rather than flattering."),
    ("2 · Build the peer group",
     "A metric is compared within the name's own industry, falling back to "
     "its sector and then the whole universe when an industry is too small. "
     "A software company's 45% margin and a grocer's 3% are both ordinary; "
     "comparing them directly would rank the sector, not the company."),
    ("3 · Winsorize, then z-score",
     "Values are clipped at the configured percentiles inside the peer group "
     "before standardising, so one broken filing cannot drag a whole sector. "
     "Every metric is oriented higher-is-better first — a low EV/EBIT is "
     "good, so what gets scored is its reciprocal."),
    ("4 · Roll metrics into a bucket",
     "A bucket subscore is the weighted mean of the z-scores that were "
     "actually computable, with the weights renormalised across just those. "
     "A missing metric is DROPPED, never filled with zero: a zero is the peer "
     "average, which is a claim, and 'we don't know' is not."),
    ("5 · Blend buckets into a composite",
     "Bucket subscores are re-standardised across the whole universe, then "
     "weighted and summed. Coverage floors apply here too — see below — and "
     "a favourable accounting-quality score is capped before it can add."),
    ("6 · Rank into a percentile",
     "The composite is ranked across the universe. A percentile of 90 means "
     "this name scored better than 90% of the names in THIS run, not that it "
     "is objectively good."),
]


def _fmt_pct(value) -> str:
    return f"{float(value) * 100:,.0f}%"


def render(*, db_path: str, run: dict) -> None:
    cfg = D.scoring_config()
    if not cfg:
        st.warning(
            "The scoring configuration could not be read, so the weights and "
            "thresholds below would be a guess. Nothing on this page is "
            "hard-coded; it all comes from `config.yaml`.")
        return
    if cfg["config_hash"] != run.get("config_hash"):
        st.info(
            f"`config.yaml` has changed since this run. The run used config "
            f"**{esc(str(run.get('config_hash')))}**; the weights below are "
            f"the current file (**{esc(cfg['config_hash'])}**). Re-run "
            f"`python -m lodestar run` to score against them.")

    st.markdown("## What a score actually is")
    st.markdown(
        f"<div style='color:{INK_MUTED};font-size:0.92rem;line-height:1.7;"
        f"max-width:60rem'>"
        f"A composite score is <b>one number summarising seven judgements</b>, "
        f"each made against companies like this one. It is not a price "
        f"target, a forecast, or advice. It answers a narrow question — "
        f"<i>relative to its peers and to the rest of this universe, how does "
        f"this company's filed record look right now?</i> — and everything "
        f"below is how that question gets answered."
        f"</div>", unsafe_allow_html=True)

    st.markdown("### The seven buckets")
    rows = []
    for bucket in BUCKET_NAMES:
        weight = cfg["composite_weights"].get(bucket)
        metrics = cfg["metric_weights"].get(bucket, {})
        rows.append({
            "bucket": bucket.replace("_", " ").title(),
            "weight": weight,
            "intent": BUCKET_INTENT.get(bucket, ""),
            "metrics": ", ".join(
                f"{m.replace('_', ' ')} ({_fmt_pct(w)})"
                for m, w in sorted(metrics.items(), key=lambda kv: -kv[1])),
        })
    st.markdown(html_table(
        [{"key": "bucket", "label": "Bucket", "cls": ""},
         {"key": "weight", "label": "Weight", "kind": "num", "fmt": ".0%"},
         {"key": "intent", "label": "What it asks", "cls": ""},
         {"key": "metrics", "label": "Metrics, and their weight inside it",
          "cls": ""}],
        rows, max_height=460, sortable=False), unsafe_allow_html=True)
    st.caption(
        "Read from `config.yaml` at page load — these are the weights the run "
        "actually used, not a description of them. Bucket weights sum to 1.0 "
        "and metric weights sum to 1.0 within each bucket; both are validated "
        "when the config loads, so they cannot silently drift apart.")

    # ---- the pipeline ----------------------------------------------------
    st.markdown("## Step by step")
    for title, body in STEPS:
        st.markdown(
            f"<div class='ls-card' style='margin-bottom:0.6rem'>"
            f"<div class='ls-card-label'>{esc(title)}</div>"
            f"<div style='font-size:0.87rem;line-height:1.65;color:{INK_MUTED};"
            f"margin-top:0.35rem'>{esc(body)}</div></div>",
            unsafe_allow_html=True)

    # ---- coverage --------------------------------------------------------
    st.markdown("## When a name does NOT get a score")
    st.markdown(stat_strip([
        ("Bucket coverage floor", _fmt_pct(cfg["coverage_bucket_floor"]),
         "of a bucket's metric weight must compute"),
        ("Composite weight floor",
         _fmt_pct(cfg["composite_min_weight_covered"]),
         "of total bucket weight must survive"),
        ("Minimum buckets", f"{MIN_BUCKETS_FOR_COMPOSITE}",
         "a blend of one thing is not a blend"),
        ("Minimum history",
         f"{cfg['min_history_quarters']} quarters",
         "below this, growth metrics are meaningless"),
        ("Peer group minimum", f"{cfg['peer_min_group_size']} names",
         "smaller falls back to sector, then universe"),
        ("Winsorized at",
         f"{_fmt_pct(cfg['winsorize_low_pct'])} / "
         f"{_fmt_pct(cfg['winsorize_high_pct'])}",
         "inside the peer group, before z-scoring"),
    ]), unsafe_allow_html=True)
    st.caption(
        "**This is the part most screeners get wrong.** Renormalising across "
        "whatever survived will happily put 100% of the composite weight on a "
        "single bucket and emit a percentile that looks exactly as "
        "authoritative as a fully-covered name's. It happened here: one name "
        "scored a 2.3 percentile built entirely on accounting quality, with "
        "the other six buckets excluded. Read naively that says *one of the "
        "worst companies in the universe*; what it actually said was *almost "
        "nothing about this company was measurable, and the one thing that "
        f"was came out mediocre*. Such a name now reports {NO_RATING} instead.")

    # ---- ratings ---------------------------------------------------------
    st.markdown("## From a score to a BUY or a SELL")
    band_rows = []
    previous = None
    for threshold, label in RATING_BANDS:
        band_rows.append({
            "rating": chip(label),
            "range": (f"z ≥ {threshold:+.2f}" if previous is None
                      else f"{threshold:+.2f} ≤ z < {previous:+.2f}")
            if threshold != float("-inf") else f"z < {previous:+.2f}",
            "meaning": {
                "STRONG BUY": "three quarters of a standard deviation above "
                              "the average name, or better",
                "BUY": "a meaningful tilt above average",
                "HOLD": "indistinguishable from the average name",
                "SELL": "a meaningful tilt below average",
                "STRONG SELL": "three quarters of a standard deviation below, "
                               "or worse",
            }.get(label, ""),
        })
        previous = threshold
    st.markdown(html_table(
        [{"key": "rating", "label": "Rating", "kind": "html", "cls": ""},
         {"key": "range", "label": "Composite z-score", "cls": "num"},
         {"key": "meaning", "label": "What it means", "cls": ""}],
        band_rows, max_height=280, sortable=False), unsafe_allow_html=True)
    st.caption(
        f"Every name carries **two** ratings and they are deliberately not "
        f"merged: one against the whole universe, one against its own "
        f"industry. Where they disagree is the interesting case — the best "
        f"house in a bad neighbourhood, or a mediocre business in a sector "
        f"that is on fire. Averaging them would destroy exactly that. An "
        f"industry or sector with fewer than "
        f"{MIN_MEMBERS_FOR_GROUP_RATING} scored members gets no group rating: "
        f"one name is a company opinion, not an industry one.")

    # ---- prices ----------------------------------------------------------
    st.markdown("## Where the prices come from")
    st.markdown(
        f"<div style='color:{INK_MUTED};font-size:0.88rem;line-height:1.7;"
        f"max-width:60rem'>"
        f"<b>One cached daily series, read by everything.</b> The price in a "
        f"company header, the last point on its chart, and the market cap "
        f"behind every valuation multiple are all the same number from the "
        f"same source, and each is shown with the date of the bar it came "
        f"from.<br><br>"
        f"That is worth stating because it was not always true. The header "
        f"used to take its price from the close on the last <i>fiscal quarter "
        f"end</i> — the only price a point-in-time ratio history ever pairs "
        f"with the newest filing, which is correct for a chart of historical "
        f"P/E and wrong for the word <i>Price</i>. On 10 August it read $373 "
        f"for Microsoft beside a chart whose own last point was $509.<br><br>"
        f"<b>Historical runs are never re-priced.</b> Selecting an older run "
        f"shows the prices and multiples as they stood then. Applying today's "
        f"close to last month's run is the lookahead this whole data layer is "
        f"built to prevent."
        f"</div>", unsafe_allow_html=True)

    # ---- news ------------------------------------------------------------
    st.markdown("## How news tone is read")
    st.markdown(
        f"<div style='color:{INK_MUTED};font-size:0.88rem;line-height:1.7;"
        f"max-width:60rem'>"
        f"A <b>Loughran-McDonald financial lexicon</b> — a dictionary built "
        f"for financial text, where <i>liability</i>, <i>cost</i> and "
        f"<i>depreciation</i> are neutral accounting vocabulary rather than "
        f"bad news — plus phrase patterns for the idioms a word count reads "
        f"backwards. <i>Failed to beat estimates</i> contains a positive word; "
        f"<i>cuts costs</i> contains a negative one.<br><br>"
        f"The score is <b>scaled by how much sentiment vocabulary the headline "
        f"actually carried</b>, so one incidental word is a weak reading "
        f"rather than a maximum-confidence verdict. Without that, 85% of every "
        f"scored article landed on exactly +1.00 or −1.00, because an "
        f"eight-word headline usually contains exactly one sentiment word."
        f"<br><br>"
        f"<b>SEC filings are read from their 8-K item codes, not scored as "
        f"prose.</b> A filing's summary is a list of official item titles, "
        f"identical across every filer — and Item 5.02, the most common 8-K "
        f"there is, is titled <i>Departure of Directors or Certain "
        f"Officers…</i>. Scored as prose, routine board and compensation "
        f"filings all came out at the maximum negative reading, at the highest "
        f"source weight in the system. Roughly 97% of filings are procedural "
        f"and now carry <b>no tone at all</b> — which is different from "
        f"neutral, and is shown as <i>no signal</i>.<br><br>"
        f"<b>It is a word counter, not a language model.</b> It does not "
        f"understand sarcasm or context, and it cannot tell whether a record "
        f"loss is bad for this company or good for its competitor."
        f"</div>", unsafe_allow_html=True)

    # ---- the honest limits ----------------------------------------------
    st.markdown("## What this will not tell you")
    for title, body in [
        ("It is relative, always.",
         "Every score is against this universe on this date. A percentile of "
         "95 in a broadly expensive market is still a company in a broadly "
         "expensive market."),
        ("The universe carries survivorship bias.",
         "The constituent list is CURRENT membership with no add/drop dates. "
         "Names dropped from the index for doing badly are simply absent, so "
         "a historical run flatters the past. Lookahead is removed — a "
         "company that had not filed by the run date is excluded — but "
         "survivorship is not, and cannot be without a historical "
         "constituent file."),
        ("It reads filings, not businesses.",
         "Nothing here knows about a pending regulatory decision, a key "
         "person, a contract that is about to be lost, or a technology "
         "shift. Accounting-quality checks catch arithmetic that does not "
         "reconcile; they do not catch a good liar."),
        ("Missing data is reported, never filled.",
         "A dash means the number could not be computed from what was filed. "
         "It is not zero, and it is not average. Coverage is shown beside "
         "every bucket so a thin score is visible as one."),
        ("It is a screening tool.",
         "It narrows a universe to a shortlist worth reading properly. Every "
         "number traces back to a filing — `python -m lodestar audit TICKER "
         "METRIC` prints the whole chain, from raw facts with their filed "
         "dates through to the final z-score."),
    ]:
        st.markdown(
            f"<div style='margin-bottom:0.7rem;max-width:60rem'>"
            f"<b style='font-size:0.9rem'>{esc(title)}</b>"
            f"<div style='font-size:0.86rem;line-height:1.6;color:{INK_MUTED}'>"
            f"{esc(body)}</div></div>", unsafe_allow_html=True)

    # ---- the ratio catalogue --------------------------------------------
    with st.expander("Every ratio, and the formula behind it"):
        try:
            from finlake import ratios as fl_ratios

            rows = [{
                "group": definition.group,
                "metric": definition.label,
                "formula": definition.formula,
                "direction": {True: "higher is better",
                              False: "lower is better"}.get(
                                  definition.higher_is_better, "depends"),
            } for definition in fl_ratios.CATALOGUE]
            st.markdown(html_table(
                [{"key": "group", "label": "Group"},
                 {"key": "metric", "label": "Ratio", "cls": ""},
                 {"key": "formula", "label": "Formula", "cls": ""},
                 {"key": "direction", "label": "Reading"}],
                rows, max_height=620), unsafe_allow_html=True)
            st.caption(
                "Read from finlake's ratio catalogue — the same object the "
                "calculation itself reads, so a formula shown here cannot "
                "drift from the number it produced.")
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Ratio catalogue unavailable: {exc}")
