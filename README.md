# lodestar

A cross-industry equity ranking engine and research hub. Scores every stock
in an investable universe by blending fundamental data with news signal, in a
way that survives comparing a bank to a semiconductor company.

One command produces a ranked table of the whole universe, sector-neutral,
with per-name attribution explaining exactly which factor buckets drove each
rank. A nine-page web app is how you read it.

Tier 1 of an equity research stack. Sits directly on top of **finlake**, the
point-in-time data layer, which is published as its own repository.

```powershell
python -m lodestar daemon          # keep the data current, re-score nightly
python -m lodestar ui              # the hub
python -m lodestar run             # score the universe now
python -m lodestar show MU         # full attribution for one name
python -m lodestar audit MU roic   # every number behind one metric
```

---

## Install

Python 3.12 or newer. lodestar needs finlake, and expects the two cloned side
by side in one folder:

```
your-folder/
├── finlake/
└── lodestar/
```

From `your-folder`, with both repositories cloned into it:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1               # macOS / Linux: source .venv/bin/activate
pip install -e finlake -e lodestar
pip install -r lodestar\requirements.txt   # adds pytest
```

**Why side by side:** `config.yaml` reads the constituent list from
`../finlake/universe/sp500_ndx.csv`, resolved relative to `config.yaml`
itself — so it works whichever folder you run a command from. If you lay the
two out differently, change that one line.

lodestar pins the exact finlake version it was verified against
(`adapter.PINNED_FINLAKE_VERSION`, currently **0.13.0**) and refuses to run
against a different one. That's deliberate: the findings and regression checks
in `FINLAKE-FINDINGS.md` were verified against the pinned version only.

**Credentials live in finlake.** Copy `finlake/.env.example` to
`finlake/.env` and fill in `SEC_USER_AGENT` (required — the SEC returns 403
without a real contact address) and `FRED_API_KEY` (free; it powers the Macro
page). lodestar needs no credentials of its own. Don't put a second `.env` in
the lodestar folder: finlake reads the first `.env` it finds, starting from
the directory you run the command in, so a copy there would shadow the real
one.

Then build the data once, score it, and open the hub:

```powershell
cd finlake
python scripts\build.py --universe-file universe\sp500_ndx.csv --all   # 1-2 hours, 10 GB+
cd ..\lodestar
python -m lodestar run
python -m lodestar ui
```

---

## Keeping it current

Two commands, and only the first needs to keep running:

```powershell
python -m lodestar daemon    # leave this open — refreshes data, re-scores nightly
python -m lodestar ui        # the hub itself
```

The daemon drives finlake's refresh tiers on their own cadences (batched
quotes every 3 minutes, news every 5, EDGAR every 10, analyst data every 6
hours, macro daily) **and** re-scores the universe once a day, after 18:00
local time. Its start-up header says how many symbols it is maintaining and
where that list came from.

**Nothing else is needed daily.** The scores are the only part that waits for
a re-run, and even then prices do not: the hub re-prices whatever run it is
showing against the latest close, so a score from last night is displayed
against this minute's price. What a re-score folds in is new *filings*, which
is why once a day is the right cadence rather than continuously.

If the daemon has been off, it works out what it missed on start and catches
up rather than waiting a full interval. `python -m finlake refresh --status`
shows what each tier last did.

---

## The two ideas that make it work

**1. Every comparison happens inside a peer group.** A semiconductor
company's 60% ROIC and a utility's 6% are not comparable numbers. Every
metric is z-scored *within its own industry* before anything is blended,
with an industry → sector → universe fallback ladder when a peer group
is too thin. The output tells you which level was actually used for each
name, so you know when a score is standing on thin ground.

**2. A missing number is missing, never zero.** If a metric can't be
computed, it is dropped and the remaining weights renormalize — it never
enters the average as a neutral 0, which would quietly pull a name
toward the middle and look like data. Coverage is reported per bucket,
and a bucket below the coverage floor is excluded from the composite
entirely rather than scored on fragments.

---

## Running

```powershell
# Score the universe as of today. Writes a full run snapshot to runs/lodestar.db.
python -m lodestar run

# ...as of a past date, and export the ranked table
python -m lodestar run --as-of 2026-08-09 --csv ranked.csv

# Full attribution for one name
python -m lodestar show MU

# Buy/sell ratings: sector, industry, or individual stock
python -m lodestar ratings sector
python -m lodestar ratings stock

# The full computation chain for one ticker/metric -- every raw fact
# with its filed date, every intermediate, the peer stats, the final z
python -m lodestar audit MU roic

# Read the results
python -m lodestar ui
```

`--as-of` uses finlake's `filed` column, so re-running a past date
reproduces what you would have seen that morning: no lookahead, no
later-IPO contamination. Same as-of date + same config hash = identical
output, byte for byte.

---

## Factor methodology

Seven buckets. Each produces a subscore and a coverage figure. Weights
live in `config.yaml`; nothing below is hardcoded.

Every metric is oriented so **higher always means better** — including
Value (yields, not multiples) and Accounting quality (inverted, so
higher means cleaner books). One sign convention everywhere is what lets
the scoring layer treat all 26 metrics identically without special cases.

### Quality (20%)
| Metric | Formula |
|---|---|
| ROIC | NOPAT / invested capital, TTM. NOPAT = EBIT × (1 − effective tax rate); invested capital = total debt + total equity − cash − short-term investments |
| ROIC stability | mean(ROIC) / stdev(ROIC) over 20 trailing quarters — rewards consistency, not just level |
| Gross margin trend | OLS slope of TTM gross margin over 12 quarters, in bps/quarter |
| FCF conversion | TTM FCF / TTM net income. When net income ≤ 0, falls back to FCF / EBITDA, flagged |
| Interest coverage | EBIT / interest expense, TTM |

ROIC and ROIC stability are **not computed for financials**. "Total debt
+ equity − cash" is not a coherent invested-capital figure for a
deposit-funded balance sheet — the industrial formula produced a ~98%
"ROIC" for JPM before this was gated off.

### Value (20%)
| Metric | Formula |
|---|---|
| EV/EBIT | Earnings yield = TTM EBIT / EV (the inverted, higher-is-better form). EV = market cap + total debt − cash − short-term investments |
| FCF yield | TTM FCF / EV |
| P/B | Book/market = equity / market cap. **Financials only** — it's noise everywhere else |

EV/Sales is a *substitution inside* the EV/EBIT slot, used when TTM EBIT
≤ 0 or the EBIT margin falls below a floor (EV/EBIT gets noisy and can
flip sign near zero EBIT). It is not a separately weighted metric — a
name never gets credit for both.

Where a company's debt cannot be resolved from its filings, EV is left
**missing** rather than computed as if the company had no debt — so the EV
metrics drop out for that name and the bucket renormalizes, instead of
ranking a heavily indebted company as cheap.

### Growth (15%)
Revenue CAGR 3y and 5y on TTM endpoints · FCF CAGR (when the base is ≤ 0,
falls back to ΔFCF scaled by average revenue, flagged) · incremental
margin (Δ TTM EBIT / Δ TTM revenue over 8 quarters) · reinvestment rate
((capex − D&A + ΔNWC) / NOPAT, guarded when NOPAT ≤ 0).

CAGR endpoints are checked for restatement: a quarter filed more than
400 days after its own period-end is flagged, since a normal 10-Q/10-K
files 30–90 days out.

### Capital allocation (15%)
| Metric | Formula |
|---|---|
| Buyback timing | Dollar-weighted average repurchase price over 12 quarters vs. the market's VWAP over the same window. Buying below your own VWAP scores well |
| Buyback yield | TTM repurchases / market cap |
| Share count change | 3y reduction in diluted shares |
| Incremental ROIC | Δ NOPAT / Δ invested capital over 3 years |
| M&A returns | Low-confidence proxy: goodwill/intangibles growth vs. subsequent incremental ROIC, less impairments taken |

Buyback timing carries a **plausibility guard**: only quarters reporting
both a dollar amount and a share count contribute, and the implied
average price must fall within ⅓×–3× of the market VWAP. A company can
time buybacks well or badly; it cannot transact at 3× the market price.
A ratio that extreme means the two XBRL tags measure different things —
this caught SPG implying a $14,900/share repurchase price on a ~$122
stock, which would otherwise have dominated the entire bucket's z-scores.

### Accounting quality (10%) — this bucket deducts
Balance-sheet accrual ratio · cash-vs-earnings divergence · Beneish
M-score · DSO/DIO trend.

**Asymmetric by design**: a bad accounting score subtracts from the
composite at full weight; a good one earns capped credit (0.5σ by
default). Clean books are the baseline, not an edge.

The Beneish M-score is computed as a genuine **partial score** on 6 of
its 8 components. AQI and DEPI are never computable from the available
concepts and are *dropped from the weighted sum entirely* — never
defaulted to a neutral value. Which components were used is always
reported.

### Momentum (10%)
Price 12-1 (t−252 to t−21 trading days) · fundamental revision · rank
change.

finlake now captures consensus estimates, but lodestar doesn't read them
yet, so **estimate-revision breadth is proxied**: TTM EPS and revenue as
known today vs. as known 90 days ago, using finlake's bitemporal `filed`
column. That captures restatements and the arrival of new filings — the
honest version of the same idea, and always flagged as a proxy.

### News signal (10%)
Read from finlake's multi-source news store: headlines from several feeds,
deduplicated across sources so one syndicated story is not counted five
times, scored with the Loughran–McDonald financial sentiment lexicon,
weighted by event type and source reliability, and decayed with a 20-day
half-life over a 60-day window.

A name with no articles reports **zero coverage** — never a fabricated
neutral score — so the bucket drops out for that name and the remaining
weights renormalize. Set `news.provider: null` in `config.yaml` to switch
the bucket off entirely.

---

## Scoring mechanics

In this exact order:

1. **Peer group assignment** — industry from a hand-curated
   `industry_map.csv`, with SIC-derived fallback. Nothing is silently
   lumped into "Other"; every name records where its label came from.
2. **Winsorize** at the 1st/99th percentile *within the peer group*, so
   one broken filing can't dominate a sector.
3. **Z-score within peer group.** Fewer than 8 usable peers falls back
   to sector, then universe. Peer size counts names with a *usable value
   for that metric*, not just industry membership — a large industry
   where only two members report a metric is just as thin.
4. **Bucket subscore** = weighted mean of available metric z-scores,
   weights renormalized across only what computed.
5. **Coverage** = available metric weight / total metric weight.
6. **Coverage floor** (0.5) — below it, the bucket is marked
   low-confidence, excluded, and composite weights renormalize.
7. **Composite** = Σ (bucket weight × re-standardized bucket z), with
   the accounting penalty applied asymmetrically, re-ranked 0–100.

---

## Buy / sell ratings

Three levels, all derived from the same composite scores — a rating is a
readable label on a number you can already trace back to a filing, not a
second opinion layer.

- **Per stock**: two ratings, deliberately not merged. `rating` is
  against the whole universe; `peer_rating` is against the name's own
  industry, with its rank inside that industry. Where they disagree is
  the interesting case — the best house in a bad neighbourhood.
- **Per industry / per sector**: mean member score → rating band, plus
  buy/sell member counts and dispersion, so you can tell a consensus
  from an average of extremes.

Groups with fewer than two scored members report INSUFFICIENT DATA
rather than a one-name "industry opinion".

**Prices are live; scores are nightly.** With the daemon running, quotes
refresh every three minutes and the hub re-prices every valuation figure
against the latest close. The ratings themselves come from the most recent
scoring run, and move when the nightly re-score folds in new filings.

---

## The interface

**CLI** for scoring. **Streamlit** for reading the results.

The UI *reads*; it never computes. No button anywhere triggers a scoring
run — Streamlit re-executes its whole script on every interaction, so a
Run button would re-rank the universe on every filter change and hang
the browser for minutes. The app loads a completed run from disk. If no
run exists, the empty state names the command to go run.

Nine pages, one click apart:

| Page | What it's for |
|---|---|
| Overview | the market right now, and what moved |
| Company | one name in full — statements, valuation, news, score |
| Screener | rank and filter the whole universe |
| Buy / sell ratings | ratings for every stock, industry, and sector |
| Compare | two to six names side by side |
| News | everything published across the universe |
| Macro | rates, inflation, growth, and the yield curve |
| How it works | exactly how the scores and ratings are calculated |
| Data health | what is complete, what is missing, what disagrees |

Every page is a URL (`?page=Screener`), so a section is linkable,
bookmarkable, and survives a refresh.

Design notes: deep navy surfaces, one accent pair (teal positive / red
negative) used only where it carries meaning, and every number in a
tabular monospace so columns align down the page. Colour is never the
only signal — signed values always carry an explicit `+`/`−` and bars
grow left or right from a zero axis, so the tables still read correctly
in greyscale or with red/green colour blindness.

---

## Verifying a number by hand

The whole project is built to be audited. `audit` prints the full chain
for any ticker/metric:

```powershell
python -m lodestar audit MU roic
```

Every raw fact with its `filed` date → the metric's own intermediates →
the winsorized value → peer group level, size, mean, and stdev → the
final z-score. Enough to tie any number back to a filing yourself.

---

## Tests

```powershell
python -m pytest -q
```

279 tests. Every factor formula has hand-computed fixtures — the expected
values are worked out in the test (or its comment), never by re-running the
implementation. Dedicated tests cover the mechanics the methodology depends
on: winsorization at the boundary, z-scores in a peer group of one, coverage
renormalization, the peer-group fallback ladder, the asymmetric accounting
penalty, and a point-in-time test proving a run dated before a company's IPO
can't include it.

**Nothing in the suite touches your real data.** `conftest.py` points
finlake at a temporary cache and refuses to run if that fails, and the UI
tests render a purpose-built runs database rather than yours.

---

## Limitations

Read these before trusting a number.

**The universe is today's S&P 500 + Nasdaq-100.** `config.yaml` points at
finlake's constituent list (503 names). It has no add/drop dates, so a
historical `--as-of` run scores *today's* members — survivorship bias, since
the names dropped from the index for doing badly are exactly the ones
missing. Clear `universe.constituents_file` to fall back to finlake's
filing-activity proxy instead.

**Historical runs lean on inferred ticker history.** The SEC publishes no
ticker history, so finlake extends each ticker's validity window back to the
company's first filing and marks it as inferred. A ticker that once belonged
to a different company is where that goes wrong.

**A thin name gets no score, not a confident-looking bad one.** The
coverage floor works per bucket; a second floor applies to the composite
itself (at least 2 surviving buckets, and 25% of total bucket weight).
Without it, a name whose only surviving bucket was accounting quality
was renormalized to 100% weight on that one bucket and handed a 2.3
percentile — which reads as "one of the worst names in the universe"
when it means "almost nothing here was measurable". Names below the
floor appear with a blank score.

**A company that re-registers with the SEC loses its history.** A
holding-company reorganization mints a new SEC identifier and leaves the old
filings behind, and finlake follows one identifier per ticker. Exxon Mobil is
the current case: after its 2026 re-registration it has too little history to
score.

**Foreign private issuers filing 20-F annually get almost no coverage.**
A company that files annually publishes essentially no quarterly XBRL —
TSM's individual quarters carry 1–3 facts against 200–450 for a full year.
There is no quarterly chain to reconstruct, and the coverage floor correctly
excludes these names rather than scoring them on fragments.

**Some metrics cover a minority of names.** Buyback timing needs both
repurchase dollars and a share count in the same quarter, and must survive
the plausibility guard: 48 of 503 names on the latest run. The EV metrics are
missing wherever debt can't be resolved (about 40 names). Thin coverage
reported honestly beats a fabricated number.

**News is headline-level.** Sentiment is read from headlines and feed
summaries, not full article text, and depends on free RSS feeds.

**M&A returns is a low-confidence proxy** and weighted accordingly. XBRL
has no deal-level IRR; this infers from goodwill growth, subsequent
incremental ROIC, and impairments taken.

**This is not investment advice.** It's a screening tool that ranks
names by a documented, auditable formula. Every number is traceable, and
none of them know anything about the future.

---

## Repo layout

```
lodestar/
├── config.yaml           every weight, threshold, and window
├── industry_map.csv      hand-curated ticker -> industry -> sector
├── FINLAKE-FINDINGS.md   every finding against the data layer, classified
├── lodestar/
│   ├── adapter.py        the only module that imports finlake
│   ├── universe.py       universe construction + constituent override
│   ├── industry_map.py   peer groups, SIC fallback
│   ├── config.py         typed config loader, config hashing
│   ├── persistence.py    the runs store (SQLite)
│   ├── daemon.py         the background loop: refresh + nightly re-score
│   ├── cli.py            run / show / ratings / audit / daemon / ui
│   ├── metrics/          base, finance, and the 7 buckets
│   ├── scoring/          winsorize, zscore, coverage, bucket,
│   │                     composite, attribution, ratings
│   └── ui/               the nine-page Streamlit hub
├── scripts/              build_industry_map.py
└── tests/                279 tests
```

Comments in the code cite design decisions as `DEC-nnn` and `ISSUE-nnn`.
Those refer to the author's private design log, which is not part of this
repository; the comments are written to stand on their own.

## License

MIT — see [LICENSE](LICENSE).
