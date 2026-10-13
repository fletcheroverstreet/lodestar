# finlake findings

Running log of everything lodestar hit in finlake, per the amendment
protocol in the project spec. Every entry: what was hit, where, the
evidence, and a verdict — **finlake** (belongs in the data layer, fixed in
a scheduled amendment window), **lodestar** (scoring-specific, handled
here), or **ambiguous** (asked before deciding).

finlake changes only happen in Phase A (before the first line of lodestar
scoring code) and Phase C (after the full smoke run). Between those,
findings get logged here and worked around locally — this file is the
record of which layer was actually responsible for what.

Status key: 🔧 fixed in finlake · 🏗️ handled in lodestar · ❓ resolved,
no action needed · ⏳ logged, deferred to Phase C

---

## Phase A (2026-08-08)

All ten items below were presented as a classified punch list and approved
item-by-item before any finlake edit. Full evidence for each was gathered
by querying `~/.finlake/finlake.db` directly and running finlake's own
test suite / smoke test — not by taking the vault's existing ISSUE notes
at face value.

### F1 — No point-in-time shares-outstanding concept 🔧

**Hit:** designing the Value bucket's EV and the Capital-allocation
bucket's buyback yield, both of which need market cap = shares × price.
finlake only mapped `shares_diluted` (a weighted-average EPS denominator,
wrong timing and wrong quantity for market cap).

**Evidence:** `dei:EntityCommonStockSharesOutstanding` and
`us-gaap:CommonStockSharesOutstanding` both present in the cache across
multiple filers, confirmed via direct SQL, unmapped in `CONCEPTS`.

**Verdict: finlake.** Fixed in finlake v0.2.0 — new `shares_outstanding`
concept. See finlake's `CHANGELOG.md`.

### F2 — No interest_expense / pretax_income / tax_expense concepts 🔧

**Hit:** designing Quality's interest-coverage metric (EBIT / interest
expense) and the NOPAT / effective-tax-rate calculation the ROIC metric
needs.

**Evidence:** `InterestExpense`, `IncomeTaxExpenseBenefit`, and the
pretax-income tag all present in the cache, confirmed via SQL, unmapped.

**Verdict: finlake.** Fixed in finlake v0.2.0 — three new concepts.

### F3 — `operating_income`'s fallback tag was actually pretax income 🔧

**Hit:** sanity-checking what `operating_income` would return for the
bank names in the universe, before building any Quality/Value metric on
top of it.

**Evidence:** direct SQL showed the fallback tag
(`IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems-
NoncontrollingInterest`) firing for JPM, BAC, WFC, GS, MS, SCHW, O, PFE,
XOM, JNJ, and KLAC — the last two despite having real `OperatingIncomeLoss`
data, overridden by a "prefer more periods" heuristic that doesn't check
which tag is actually correct.

**Verdict: finlake — correctness bug, not new scope.** Fixed in v0.2.0;
see finlake's `DEC-005-split-pretax-income-from-operating-income`.
Regression-diffed clean: only those 11 tickers' `operating_income` values
changed, everything else byte-identical against the pre-fix snapshot.

### F4 — No goodwill / intangibles / AR / AP concepts 🔧

**Hit:** designing the Capital-allocation bucket's M&A-returns proxy
(goodwill/intangibles growth vs. subsequent ROIC) and the Accounting-
quality bucket's reinvestment rate and DSO/DIO, all of which the spec
names explicitly.

**Evidence:** `Goodwill`, `FiniteLivedIntangibleAssetsNet`,
`AccountsReceivableNetCurrent`, `AccountsPayableCurrent` all present,
confirmed via SQL, unmapped.

**Verdict: finlake.** Fixed in v0.2.0 — four new concepts.

### F5 — `cash` concept excludes short-term investments 🔧

**Hit:** the spec's invested-capital formula is "total debt + total
equity − cash **and short-term investments**"; finlake's `cash` concept
only covered cash itself.

**Evidence:** `ShortTermInvestments`, `MarketableSecuritiesCurrent`
present, confirmed via SQL, unmapped.

**Verdict: finlake.** Fixed in v0.2.0 — new `short_term_investments`
concept. lodestar sums `cash + short_term_investments` for invested
capital; a filer that only reports the combined
`CashCashEquivalentsAndShortTermInvestments` tag is a known gap, not yet
hit by any name in the current universe — will flag as a new finding if
it surfaces during factor build, not silently work around it.

### F6 — No windowed VWAP helper on the price side 🔧

**Hit:** designing the buyback-timing metric — needs the market's
dollar-weighted average price over the same trailing-12-quarter window
as the company's own repurchases. finlake's price module only exposed
raw bar retrieval.

**Evidence:** read `sources/prices.py` in full — `get_prices()` only.

**Verdict: finlake — generic price math, not scoring-specific.** Fixed in
v0.2.0 — `finlake.vwap()`. See finlake's
`DEC-007-vwap-is-a-generic-price-primitive`.

### F7 — No price data cached at all ❓

**Hit:** checking `~/.finlake/prices/` before relying on any
price-dependent bucket.

**Evidence:** `corp_actions` table empty, prices directory empty —
`scripts/build.py` was never run with `--prices`.

**Verdict: not a code finding.** Operational — price data gets fetched
for the universe as part of Phase 4 (finlake adapter + universe
construction), not a finlake bug.

### F8 — ISSUE-002 (XOM tags) was mis-diagnosed ❓

**Hit:** verifying the "XOM tags unmapped" issue the finlake vault
already documented, before deciding whether to fix it in Phase A.

**Evidence:** "XOM" resolves to CIK 2115436, `first_filed = 2026-07-01`,
274 total cached facts (vs. 32,069 for CVX). The tags it does report
were already mapped. No tag was missing — the entity is just young/thin
in the cache.

**Verdict: ambiguous, resolved via finlake's own vault note.** No finlake
fix — ISSUE-002 closed with the corrected diagnosis (see finlake's
updated `ISSUE-002-xom-unmapped-tags.md`). lodestar-side: this is exactly
what the coverage-floor mechanism (§3.6 of the project spec) exists to
catch — XOM will show up thin/low-confidence in the run output rather
than being silently scored as if the data were complete.

### F9 — WDC negative-quarter bug (ISSUE-001) confirmed and mitigated 🔧

**Hit:** re-running finlake's own `smoke_test.py` during discovery.

**Evidence:** WDC 2023-06-30 revenue = -$3.391B, exactly as
ISSUE-001 already documented.

**Verdict: finlake**, per the project spec's own explicit instruction
("detect implausible quarters, exclude them, and report them"). Fixed in
v0.2.0 with the cheapest of ISSUE-001's own listed options (drop + flag),
scoped to `revenue` only. See finlake's
`DEC-006-non-negative-guard-scoped-to-revenue-only`.

### F10 — `universe()` is a filing-activity proxy (ISSUE-004) ❓

**Hit:** designing lodestar's universe-construction step.

**Evidence:** confirmed accurate against `pit.py`. Also confirmed
`universe()` currently returns almost exactly the 41 already-built
companies, because `securities.first_filed`/`last_filed` are only
populated for CIKs that have had `load_submissions()` run.

**Verdict: finlake — no fix needed, already correctly scoped in
ISSUE-004.** lodestar-side: build the optional constituent-file override
and print the resolved universe at the start of every run, exactly as
the project spec requires. Carried forward as an inherited limitation in
lodestar's own ISSUE notes rather than re-litigated here.

### F11 — Where should `industry_map.csv` live? ❓

**Hit:** peer-group assignment (project spec §3.1) needs an
industry/sector taxonomy finlake doesn't have (it only has SIC codes from
EDGAR; GICS is licensed).

**Evidence:** n/a — greenfield decision, no existing code or convention
either direction.

**Verdict: ambiguous — user's call, made explicitly rather than assumed.**
Decision: starts as a hand-edited CSV inside lodestar for v1 (fast to
build, fast to correct by hand, zero finlake schema risk). Loader
designed so promoting it into finlake as a first-class table later is a
one-file move, not a rewrite, if other tools ever want to reuse peer
groups. See lodestar's own DEC note once written.

### F12 — finlake had no git history or packaging 🔧

**Hit:** trying to follow the amendment protocol itself — "commit
finlake's current state to git on its own branch before the first edit"
and "lodestar pins the new version" are both impossible at zero.

**Evidence:** `git status` → not a repository; no `pyproject.toml` /
`setup.py` anywhere in the tree.

**Verdict: finlake, infrastructural only — no data-layer logic touched.**
`git init`, baseline tagged `v0.1.0`, `pyproject.toml` added, amendment
tagged `v0.2.0`. lodestar pins `finlake==0.2.0`.

---

## Between amendment windows

### F18 — SPG's shares_outstanding data is stale (2009-2012 only) ❓

**Hit:** end-to-end pipeline check surfaced SPG's Value bucket at zero
coverage; traced to `enterprise_value()` unable to get a market cap.

**Evidence:** both `CommonStockSharesOutstanding` and
`EntityCommonStockSharesOutstanding` exist for SPG in the cache, but
only for period-ends in 2009-2012 — nothing since. Outside any
reasonable lookback window (lodestar pulls 10 years back from 2026).

**Verdict: informational, not investigated further.** Low priority: the
coverage mechanism already handles this correctly (SPG's Value bucket
shows honest zero coverage, not a fabricated EV). Worth a look during
Phase C if other REITs show the same pattern (might indicate SPG-
specific tag drift rather than a REIT-wide gap), but not blocking
anything today.

### F13 — CONCEPTS has zero IFRS tag coverage; foreign filers get almost no fundamentals ⏳

**Hit:** building the universe/industry map. TSM showed up in
`finlake.universe()` (a real semiconductor foundry, not a name I'd
manually added), and `finlake.fundamentals("TSM", ...)` returned only
`assets`, `liabilities`, `shares_outstanding` — no revenue, no income, no
cash flow.

**Evidence:** direct SQL against TSM's facts (CIK 1046179) shows it uses
`taxonomy IN ('dei', 'ifrs-full', 'srt')` exclusively — never `us-gaap`.
Every `CONCEPTS` entry, including the nine added in Phase A, only lists
`us-gaap` tag names. `pit.py`'s `PERIODIC_FORMS` already includes
`20-F`/`20-F/A`/`40-F`, so the point-in-time layer was clearly built with
foreign filers in mind — but the tag-fallback map was never extended to
match, so that form support does nothing for fundamentals coverage in
practice.

**Verdict: finlake.** Any tool consuming this data layer hits the same
wall for any 20-F/40-F filer, not just TSM — this is a coverage gap in
the concept map, the same class of problem as the original "no tag
called Revenue" trap finlake's own README describes, just for a taxonomy
nobody added yet. Real fix is adding an IFRS fallback tag (e.g.
`ifrs-full:Revenue`, `ifrs-full:ProfitLossFromOperatingActivities`,
`ifrs-full:ProfitLoss`) to each relevant concept — a bigger lift than the
Phase A additions since it likely touches most of the ~29 concepts, not
a couple.

**Not fixed now** — logged for Phase C, not patched opportunistically
mid-build. lodestar-side workaround: none needed. TSM will simply show
up in the run with very low coverage across every fundamentals-derived
bucket, correctly flagged low-confidence by the coverage floor rather
than silently scored on 3 of ~29 concepts. This is exactly the scenario
the coverage mechanism exists for.

**Phase C resolution — partially fixed, and the diagnosis was
incomplete.** IFRS fallback tags WERE added in v0.3.0 (`Revenue`,
`ProfitLossFromOperatingActivities`, `ProfitLossBeforeTax`, `Equity`,
`CashAndCashEquivalents`, `CashFlowsFromUsedInOperatingActivities`,
`CostOfSales`, and others) and they do now resolve facts that
previously returned nothing at all. But that alone doesn't give TSM
usable fundamentals, because the tag map was only half the problem:

**TSM files 20-F ANNUALLY and publishes essentially no quarterly
XBRL.** Direct SQL: 5,480 facts on 20-F vs. 501 on 6-K, and its flow
periods are 364-365 day full years carrying 200-450 facts each, while
the individual quarters carry **1-3 facts each**. `quarterize_multi`
correctly refuses to emit a 365-day period as a quarter, so there is
simply no quarterly history to reconstruct — not a parsing failure, an
absence of data at the source.

So: the IFRS tags are correct and worth having (any future 20-F filer
that DOES report quarterly detail now works, and TSM's balance-sheet
instants resolve), but foreign private issuers filing annually will
keep showing near-zero coverage in a quarterly-fundamentals tool no
matter what the concept map says. That's a real, permanent limitation
of the 20-F regime, not a finlake bug — recorded in finlake's
`ISSUE-005-foreign-filers-annual-only` and in lodestar's own
limitations section. TSM stays correctly excluded by the coverage
floor.

### F14 — No depreciation & amortization concept ⏳

**Hit:** building the Quality bucket's FCF/EBITDA fallback (used when net
income ≤ 0) and the Growth bucket's reinvestment rate, both of which the
project spec's formulas name D&A in directly.

**Evidence:** `us-gaap:DepreciationDepletionAndAmortization` confirmed
present via SQL (59 periods for MU alone) — genuinely just missed during
the Phase A discovery pass, which focused on the concepts explicitly
named in the spec's formula list and didn't derive every second-order
dependency (EBITDA needs D&A even though "EBITDA" isn't itself a named
concept).

**Verdict: finlake** — additive, same shape as F1/F2/F4/F5. Low
urgency: the metrics that need it degrade gracefully (FCF/EBITDA
fallback reports unavailable rather than fabricating an approximate
D&A; Beneish M-score's DEPI input is just one of the eight the spec
already expects to sometimes be missing, computed as a partial score by
design).

### F15 — No shares-repurchased-count concept — blocks buyback timing ⏳

**Hit:** building the Capital-allocation bucket's buyback-timing metric
— the one the project spec calls out as the one to get right. Computing
"the company's own dollar-weighted average repurchase price" needs
shares repurchased per period, not just total dollars spent
(`buybacks`, already mapped).

**Evidence:** `us-gaap:StockRepurchasedDuringPeriodShares` confirmed
present via SQL during the Phase A discovery pass (F6's evidence
gathering) but never added to `CONCEPTS` — an oversight, not a data gap.
`us-gaap:TreasuryStockAcquiredAverageCostPerShare` is also present for
at least some filers and could serve as a direct alternative when a
filer reports it.

**Verdict: finlake** — additive. **Flagging this as the highest-priority
Phase C item**, given the project spec's explicit emphasis on this
metric. Not fixed now, per the amendment-window discipline (Phase C is
scheduled after the full smoke run, not ad hoc mid-build) — but
`capital_allocation.py`'s `buyback_timing()` is fully implemented and
unit-tested against synthetic fixtures; against the real cache today it
reports `value=None` with `substitution="blocked_missing_shares_repurchased_concept"`
until this lands.

### F16 — No goodwill-impairment concept ⏳

**Hit:** building the Capital-allocation bucket's M&A-returns proxy,
which the spec explicitly asks to include "any goodwill impairments
taken."

**Evidence:** `us-gaap:GoodwillImpairmentLoss` confirmed present via
SQL, unmapped.

**Verdict: finlake** — additive, low urgency. The spec already marks
`ma_returns` as low-confidence/small-weight; it degrades to using
goodwill/intangibles growth alone (without the impairment signal) until
this lands, flagged as a partial computation.

### F17 — `fundamental_revision` structurally can't work yet — not a bug, a data-maturity limitation ❓

**Hit:** building the Momentum bucket's `fundamental_revision` proxy
(TTM EPS/revenue as known today vs. as known 90 days ago, via finlake's
bitemporal `filed` column, per the project spec). Real-data check
against MU returned `value=None` for every ticker tried.

**Evidence:** `sec.resolve_cik(conn, "MU", as_of="2026-05-10")` returns
`None`, while `sec.resolve_cik(conn, "MU")` (no as_of) returns the real
CIK. `ticker_map` has exactly one row for MU: `valid_from=2026-08-07`
(the date this cache was first built), `valid_to=NULL`. Every ticker in
the cache has the same pattern — `load_ticker_map()` sets `valid_from`
to the build date for every mapping it's never seen before, and this
cache has only ever been built once.

**Verdict: not a finlake bug** — this is exactly the limitation
finlake's own README already documents under "Honest limitations":
*"Ticker history starts accumulating when you do... history begins the
first time you run it. Run it on a schedule."* It's correct, documented
behavior, not something to fix. But it has a concrete, universe-wide
consequence for lodestar worth stating plainly: `fundamental_revision`
requires resolving a CIK as of a date `revision_lookback_days` (90) in
the past, and **that will fail for every single name** until
`load_ticker_map()` has been run at least twice, 90+ real days apart.
Until then, this metric will show zero coverage across the whole
universe — which the coverage-floor mechanism will correctly flag
(Momentum's coverage drops, the bucket gets marked low-confidence or
excluded with weight renormalization) rather than silently produce a
misleading "unavailable for a few names" impression. No lodestar
workaround needed or attempted; this resolves itself over calendar
time as the ticker map is rebuilt on a schedule, exactly as finlake's
own docs prescribe. Noted in lodestar's own limitations section too.

**Wider blast radius than first scoped:** confirmed via `lodestar run
--as-of 2026-05-01`, which returned a completely EMPTY universe (0
names), not just a coverage gap on one metric. `finlake.universe()`
itself joins through `ticker_map`, so any `as_of` before the cache's
one and only build date (2026-08-07) resolves zero tickers for the
entire run, not just for `fundamental_revision`. `2026-08-07` and
`2026-08-08` are currently the only two dates that produce a non-empty
universe at all. Same root cause, same non-fix (this is finlake
behaving exactly as its own README describes), but worth knowing
before assuming a wider historical backtest is possible right now.
