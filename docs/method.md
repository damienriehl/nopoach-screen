# Screening method

This document describes the implemented screening conventions for lawyers,
analysts and reviewers who need to reproduce or explain a lead. The method
identifies patterns worth investigating. It does not determine whether employers
made an agreement, whether a restriction is enforceable, or whether conduct
violates the law. Scores are not probabilities of an agreement. An absence of
signals does not establish an absence of conduct.

## Define the comparison before reading the result

The user selects occupation codes, a state or metropolitan area, and years,
then confirms competing employers. Employer and parent identities are resolved
before screening; retained source and import records make those decisions
reviewable. A market is a screening comparison chosen by the user, not a legal
market-definition finding. Keep excluded employers and the reasons for exclusion
with the work. Changing the market can change expected flows and ranks.

The input must be lawfully held records with declared provenance. Bulk public
collection covers the requested market and period rather than fetching only a
suspicious pair. A source receipt retains its location, retrieval time, method,
status and raw-content SHA-256. Imported rows retain their source declaration,
row numbers and raw-file receipt. These record where an observation came from;
they do not independently establish that it is accurate or legally usable.

## Check what the data can see

For each employer and year, coverage compares distinct visible workers with an
estimated occupation-specific workforce. Unknown denominators remain unknown.
Aggregate BLS or DOL context is not a substitute for individual job histories.
Professional-profile histories can miss workers and misdate moves; occupational
and employer representation can vary systematically.

The current flow gate requires at least **20% estimated coverage for every
employer/year** in the pair and at least **30 expected directional move
opportunities** across the period in each direction. The sample requirement
uses expected opportunities, not the observed moves: an actual zero must not
hide the very shortfall being screened. These thresholds are provisional.
Unknown coverage is unreliable. Unreliable pairs remain identified as such and
do not fire a flow signal. Filed-clause and leadership-feed sufficiency requires
evidence that every expected publishing date in the period was collected;
an index alone or an unspecified calendar does not prove completeness.

If signals are insufficient, the interface explains the gaps and recommends
sources with cost and provenance classes. The user must explicitly accept a
partial screen to proceed. Acceptance does not improve the data or its
reliability, and the dossier retains the partial-screen notice.

## Signal 1: fewer worker moves than expected

A worker move joins successive resolved employment spells at different
employers, in the selected occupation and geography, with a nonnegative gap
of at most 90 days. Intervening jobs outside the market are not bridged.
Unknown endpoints do not prove a move. Month-only and year-only dates retain
their precision and a possible move-year interval; they are not exact dates.
Employer coordinates and geography crosswalks must be supplied rather than
guessed from names or free-text addresses.

For every ordered employer pair and year, a Poisson gravity model estimates
how many moves to expect from the supplied market data. “Ordered” means that
A to B and B to A are separate observations. The model uses separate effects
for origin employer/year and destination employer/year, plus distance and
occupation overlap. Employer/year effects capture market-observed differences
in sending and receiving workers, including employer size and general hiring
changes. There is no separate measured-headcount offset. Constant or absorbed
covariates add no information and are omitted from the fit. Zero occupation
overlap yields zero expected flow; zero-margin cells are handled as zero
expectations rather than forcing a positive fit.

In notation, for supported cells the model is
`log(expected moves A→B in year t) = origin(A,t) + destination(B,t)
+ distance coefficient × distance(A,B) + overlap coefficient × log(overlap(A,B))`.
Occupation overlap is the sum of the smaller employer share in each selected
occupation. Coefficients are estimated by Poisson pseudo-maximum likelihood
using `pyfixest.fepois`. Expected moves are a comparison under this model,
not an estimate of how many workers an unlawful agreement prevented from moving.

Small counts fluctuate. A Gamma-Poisson empirical Bayes calculation pulls an
observed/expected ratio toward 1, the fitted market rate. The prior strength
is estimated from excess variance in annual pair rates and is bounded between
1 and 30 expected moves. The displayed ratio is
`(observed + prior strength) / (expected + prior strength)`. For an arithmetic
example, 10 observed moves, 50 expected and prior strength 10 give a ratio of
20/60, about 0.33. The displayed 95% interval is a Gamma posterior interval
conditional on the fitted expected counts. It excludes uncertainty in the
gravity fit, coverage estimates, identity resolution and missing records.
It is not a probability interval for an agreement.

The screen also scans candidate years for a sustained downward break. It
compares a constant rate with a lower post-break rate. Date brackets express
timing uncertainty, not an exact start date or cause. Only a break that survives
the same screening decision is reported. A reported break's ratio uses its
post-break period; otherwise the ratio uses the full window.

By default, 1,999 simulated Poisson trajectories at the fitted expected counts
serve as model-based placebos. Each runs the same shrinkage and break search.
The tail probability counts simulations at least as extreme as the observed
statistic, with one added to numerator and denominator so finite simulation
never reports zero. These are simulated null trajectories, not historical
control employers or an independent validation dataset.

Benjamini-Hochberg adjustment covers all tested pair directions in the market.
A direction fires only if its adjusted value is at most **0.10** and its
shrunken ratio is below **0.5**, with the pair passing the reliability gate.
The false-discovery convention addresses multiple comparisons under statistical
assumptions; it is not a guarantee that 90% of leads reflect agreements.
Model misspecification, dependent comparisons and incomplete histories can
invalidate that interpretation. Seed, placebo count and model settings are
retained for reproducibility.

Both directions firing yields possible mutual flow suppression. One firing
direction is listed separately as a **possible unilateral no-hire policy**;
it cannot by itself support a claim of a mutual agreement. The mutual flow
score uses the smaller directional shortfall, so its stronger direction cannot
mask a weak reverse direction.

## Signal 2: filed employee restrictions

The clause extractor identifies employee hiring/solicitation restrictions in
filed documents, including relevant public filings and supplied franchise
disclosures. The result retains exact text offsets, excerpts, source receipts,
filing dates, binding category, duration and scope where stated. Each pair's
clause contribution is 1 when a filed restriction is present, independent of
clause count. A binding category records what the text says; it is not an
adjudication of enforceability. Customer/vendor restrictions and negated
language require the extractor's context rules. Analysts must read the source
language and relationship before treating the clause as support for a lead.
“No filed clauses found” refers only to the collected period and records.

## Signal 3: executive and board ties

Resolved people and positions identify shared directors, executive moves,
sequential leadership and former coworker links. Current shared directors
carry base weight 4, executive moves 2, sequential leadership 1, and former
coworkers or other leadership links 0.5. Historical shared directors carry 1.
Recency multiplies this by `1 / (1 + years since the last tie date)` at the
screen period's end; undated links receive a factor of 0.5. Repeated filings
of one continuous link do not create independent ties. Dates retain precision,
and missing endpoints are not extended indefinitely. Evidence can establish a
filing-date snapshot without establishing a precise tenure or overlap.

The sum of tie weights is converted to `raw score / (raw score + 4)` for
ranking. A raw score of 4 therefore contributes a normalized value of 0.5.
These choices are transparent screening conventions. A personal or governance
connection does not itself establish an agreement or information exchange.

## Combine evidence and consider explanations

The combined score is **50% flow + 30% filed restrictions + 20% ties**.
Missing or non-firing flow contributes zero; missing signals do not redistribute
weights. Pairs with at least two firing signals rank before other pairs, then
by descending score, with employer IDs breaking ties. Reliable one-way flow
appears exclusively in its separate ranking, even if another signal is present.
Pairs with no firing signals remain visible. Because sources can overlap,
multiple signals must not be treated as statistically independent proof.

Five descriptive checks consider geographic separation, pay gaps, occupation
mismatch, generally low lateral hiring, and individual non-compete prevalence.
“Passed” means supplied data supports a possible innocent explanation; “failed”
means the check did not support it, without ruling it out. “Not testable” means
necessary evidence or an extractor is absent. Version 1 has no non-compete
prevalence extractor. The checks never remove a pair or reduce its rank.
LCA offered wages are a visa-worker proxy rather than a workforce census.

## Review, reproduce and use in further inquiry

The dossier includes observed/expected flow charts, reported break brackets,
verbatim clause excerpts, leadership evidence, explanation results, coverage,
source classes and the method. Record-set IDs link to the saved screen's
parameters and whole-market input references; row and receipt IDs trace
individual evidence. Retain the reviewed build, SBOM, release hashes, workspace,
market decisions, seed and input receipts under agency records policy.

Historical-case backtests must use only information available before the public
disclosure cutoff. The current harness enforces employment and event dates, not
the date on which a record became available; analysts must independently support
pre-disclosure availability and retain that limitation in reports. Missing
parties, histories or other required evidence yield
“insufficient data,” not a guessed rank. Actual historical-case runs and the
live pilot await lawfully supplied person-level records. Synthetic scenarios
exercise planted mutual suppression, one-way suppression and thin coverage,
but do not demonstrate sensitivity, specificity or field accuracy.

Person-level histories for those historical markets may come from third-party
aggregators whose collection is unclear. Such data is used only to validate the
method in historical backtests. It never feeds a live screen or lead, and any
backtest result reported from it names the vendor, the dataset and its
provenance class.

Before using a lead to justify agency process, a lawyer must evaluate source
lawfulness and completeness, identity and market choices, alternative
explanations, and the cited underlying evidence. The screen is one input to
that judgment. Reproduce the result in the agency workspace and assess its
limitations rather than presenting a ranking as a finding of liability.
