# Screening method

This document describes the implemented screening conventions for lawyers,
analysts and reviewers who need to reproduce or explain a lead. The method
identifies patterns worth investigating. It does not determine whether employers
made an agreement, whether a restriction is enforceable, or whether conduct
violates the law. Scores are not probabilities of an agreement. An absence of
signals does not establish an absence of conduct.

## Define the comparison before reading the result

By default, the user selects occupation codes, a state or metropolitan area,
and years, then confirms competing employers. Employer and parent identities
are resolved before screening; retained source and import records make those decisions
reviewable. A market is a screening comparison chosen by the user, not a legal
market-definition finding. Keep excluded employers and the reasons for exclusion
with the work. Changing the market can change expected flows and ranks.

The input must be lawfully held records with declared provenance. Bulk public
collection covers the requested market and period rather than fetching only a
suspicious pair. A source receipt retains its location, retrieval time, method,
status and raw-content SHA-256. Imported rows retain their source declaration,
row numbers and raw-file receipt. These record where an observation came from;
they do not independently establish that it is accurate or legally usable.

### Optional flow-defined comparison

The alternative flow-defined mode starts with a declared population of held
records: selected source receipts and candidate employers, independent of
occupation or geography. It needs workforce counts for that same population.
Occupation/metro denominators cannot substitute for them. Training and
assessment preserve full person spell sequences, so an excluded intervening job
never becomes a shortcut transition. Unknown occupation or geography does not
itself remove an otherwise known in-population move.

A directed degree-corrected Poisson block model proposes groups using a period
that ends before assessment starts. Its expected count for an eligible ordered
pair is the product of a sender factor, a receiver factor and the rate between
their assigned groups. Self-pairs contribute neither observed nor expected
counts. Iterative proportional fitting estimates the factors for a fixed
assignment; seeded restarts and a bounded search over employer moves and swaps
seek a local optimum. This is not a claim of the economically correct market
or an exact replication of a published empirical result.

Training uses only moves whose entire date interval falls within the earlier
period, with eligible coverage and source identities. Employers without known
coverage or both sending and receiving support remain unassigned with reasons.
The default is two groups, seed zero and four restarts; one to four groups are
supported, with at least three supported employers per group. Limits are 64
employers, 20 search sweeps, 200 fitting sweeps and 4,096 candidate refits, with
default convergence tolerance `1e-6`. Exceeding a limit or failing convergence
produces an incomplete or unavailable proposal, without sampling the universe.
A one-group fit supplies a reference, not evidence of a subdivision.

The user reviews and confirms a group and can add or remove employers within
the declared universe. Results retain fitted and final membership, manual
changes, excluded and unassigned employers, and counts of unevaluated pairs.
Excluded pairs are not observations of ordinary mobility. The saved proposal
includes input and proposal digests, settings, likelihoods, convergence,
restart agreement and dependency versions. Later-period moves cannot select
the earlier grouping. The pre-period may itself contain suppressed mobility;
temporal separation does not establish an unbiased comparison.

Flow-defined scoring keeps the existing gravity, shrinkage and reliability
rules below. It derives occupation shares from attributable records and
distances from supplied coordinates using spherical centroids and geodesic
distances. Missing or partial covariate support is reported explicitly. The
ordinary occupation/geography mode remains available when clustering cannot
produce a usable proposal.

## Check what the data can see

For each employer and year, coverage compares distinct visible workers with an
estimated occupation-specific workforce in ordinary mode, or the declared
record population's workforce in flow-defined mode. Unknown denominators
remain unknown.
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
employers, in the selected occupation/geography or declared flow population,
with a nonnegative gap of at most 90 days. Intervening jobs outside the market
are not bridged.
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
Occupation overlap is the sum of the smaller employer share in each in-scope
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
necessary evidence or supported applicability is absent. The optional
individual-restriction extractor and reviewed-population check are described
below. Without supplied review evidence, non-compete prevalence remains
untestable. The checks never remove a pair or reduce its rank.
LCA offered wages are a visa-worker proxy rather than a workforce census.

## Optional analytical inputs

All three inputs are local imports with source declarations. The application
retains original bytes, hashes, provenance receipts and versioned manifests in
the external workspace, under `analysis/inputs/`. Contract text also retains
the normalized representation and transformation version. An accepted bundle
is immutable; a replacement creates a separately selectable version. Hashes
are checked before use. Invalid or interrupted imports do not replace a valid
selection. The import interface keeps healthy versions selectable when another
version is damaged, and retains a visible integrity warning for the damaged
version. The interface provides labeled declaration fields; users do not need
to author a metadata JSON file.

### Flow-population workforce counts

Declare a population identifier (`population_id`), `description` and `source`,
then select its record source receipts (`provenance_ids`) and candidate employers
(`employer_ids`). Employer IDs must refer to employers
in the workspace. Supply a UTF-8 CSV with exactly these columns:

| Column | Meaning |
| --- | --- |
| `employer_id` | Workspace employer ID, included in the declared candidate universe. |
| `year` | Calendar year for the count. Supply earlier training years and assessment years as needed. |
| `value`, `lower`, `upper` | Estimated workforce and its bounds for this population; an empty `upper` means unknown. |
| `source` | Source of this employer/year estimate. |
| `as_of` | ISO date (`YYYY-MM-DD`) for the count's availability. Future counts are excluded by the relevant cutoff. |

The file limit is 4 MiB and 10,000 rows. Exact duplicate employer/year counts
are deduplicated; conflicting counts must be resolved before import. Missing
counts remain unknown. Replacing a population or its underlying source records
requires a new proposal review; it cannot reuse a proposal merely because the
population label or final employer list is unchanged.

### Public Census J2J temporal context

Import a local CSV or gzip file using supported schema **V4.14.0**. Declare the
source, release (for example, `R2023Q4`), release date, five-digit CBSA,
destination and origin ownership codes (`A00` or `A05`), and the selected
same-CBSA pair's available start/end year and quarter. Confirm workplace
geography only when the job histories' geography has that meaning; an unknown
meaning makes the comparison unavailable.

The corresponding declaration fields are `source`, `schema_version`, `release`,
`release_date`, `geography`, `ownercode`, `ownercode_orig` and
`workplace_geography` (`workplace` only when affirmed). The `availability`
declaration contains `geo_level`, `geography`, `geo_level_orig`, `geography_orig`,
`start_year`, `start_quarter`, `end_year` and `end_quarter`; the interface fills
the same-CBSA geography fields from the selected CBSA.

The parser requires one quarterly, unadjusted, nonoverlapping total tabulation:

| Dimension columns | Required values |
| --- | --- |
| `periodicity`, `seasonadj` | `Q`, `U` |
| `geo_level`, `geo_level_orig` | `B`, `B` |
| `geography`, `geography_orig` | The same declared five-digit CBSA, retaining leading zeros. |
| `ownercode`, `ownercode_orig` | The declared ownership codes. |
| `ind_level`, `ind_level_orig`; `industry`, `industry_orig` | `A`, `A`; `00`, `00` |
| `sex`, `agegrp`, `race`, `ethnicity`, `education` | `0`, `A00`, `A0`, `A0`, `E0` |
| `firmage`, `firmsize`, `firmage_orig`, `firmsize_orig` | All `0` |
| `agg_level` | `592897` |

The file also needs `year`, `quarter`, `EE`, `AQHire`, `sEE` and `sAQHire`.
Other columns are allowed; a supplied `release` column must match the declaration.
Rows for other geographic pairs are counted as excluded. Unsupported strata
within the target pair, mixed releases and conflicting duplicate quarters are
rejected. Exact duplicates count once. Limits are 64 MiB each for raw and
decompressed bytes, one million rows, 65,536 bytes per field and 4,000 retained
quarter cells. ZIP files and automatic downloads are not supported.

For each public year, the check sums `EE + AQHire` across all four quarters.
Both indicators must have status `1` and finite nonnegative counts in every
quarter. A status-1 zero is zero; absent, suppressed, unavailable or invalid
components exclude the whole year, with reasons retained. The comparison uses
the same complete-year intersection for public counts and all modeled
directional expectations, including unflagged pairs and zero expectations.
It requires at least two complete common years, complete modeled-pair support
and positive finite totals in both series.

The display reports raw annual totals, each year's share of its own series'
common-year total, and their percentage-point difference. A successful result
is **context only**. Occupation coverage, selected employers, main-job inference,
transition-gap definitions and ownership/population differences remain visible.
Census totals never replace or rescale employer-pair expected counts, establish
calibration, or alter scores or sufficiency. State and flow-defined scopes,
unknown workplace geography and releases after a historical cutoff produce an
unavailable result. Partial years are excluded from the common-year series.
Public J2J aggregates are distinct from
the restricted LEHD microdata listed in the source catalog.

### Individual non-compete review

The local text rules identify candidate restrictions on an employee's work for
a competitor after employment. They retain exact normalized-text offsets,
employer/document attribution, stated duration, exceptions, negation and
uncertainty. Candidate extraction is separate from the filed employer-pair
clause signal and never adds a signal or ranking contribution.

Declare the source, review-population description, sampling/coverage basis and
assessment scope. Supply each employer's declared unique-worker count, leaving
it unknown when unsupported, and optionally the review date. Counts describe
the reviewed population; a filing sample or a supplied count does not establish
workforce representativeness. Use workspace employer IDs and the scope shown
for the current market. Ordinary scope encodes geography and sorted occupation
codes; flow scope binds the population identifier, source receipts and candidate
universe. A different record population needs its own matching review.

The declaration fields are `source`, `population_description`, `sampling_basis`,
`scope`, employer-specific `declared_workers` and optional `reviewed_on`.

Supply a UTF-8 CSV with exactly these columns:

| Column | Meaning |
| --- | --- |
| `document_id`, `employer_id` | Source-document identifier and workspace employer ID. |
| `text` | Full document text for extraction. CSV quoting preserves commas and line breaks. |
| `document_date` | ISO date; unknown dates cannot establish historical eligibility. |
| `scope` | The review's matching assessment scope. |
| `worker_key` | Local identifier for deduplicating a worker within an employer; missing keys prevent a complete population. It is omitted from dossier prose. |
| `effective_from`, `effective_to` | ISO dates establishing applicability for the full assessment period. `ongoing` explicitly declares an open end; a blank end is unknown. |
| `documents_complete` | `true` or `false`: whether the worker's entire applicable document set was reviewed. |
| `review` | `positive`, `negative`, `uncertain` or `unreviewed`; blank is treated as unreviewed. |

Limits are 8 MiB, 10,000 rows and 65,536 characters per field. Text normalization
changes CRLF/CR line endings to LF, with transformation and original bytes
retained. Exact duplicate documents count once; repeated worker keys identify
one worker, and conflicting versions or amendments remain unresolved.

A testable employer population needs every declared worker to have one
unambiguous reviewed outcome, complete applicable documents, matching scope and
an effective interval covering the full assessment period. A positive review
requires a candidate restriction. An extraction miss is never automatically
negative: a negative requires explicit complete-document review without a
positive candidate. Missing keys or denominators, uncertain applicability,
conflicting reviews and future-dated evidence keep prevalence untestable.

The provisional descriptive criterion is **at least 50% of reviewed workers**
with an applicable candidate restriction. The pair's explanation passes if
either complete employer population meets it; it fails only when both complete
populations fall below it. Otherwise it is not testable. Results retain the
numerator, denominator, threshold, resolved/unresolved counts, population limits
and source references. They make no finding about enforceability or causation,
and cannot establish general workforce prevalence.

### Frozen results and changed inputs

A run freezes its market, selected bundle versions and proposal/settings before
scoring. It computes the analytical checks once and retains their results and
source declarations in the screen snapshot. HTML and Word dossiers use that
snapshot, including when no pair fires. Changes to workspace,
market, worker imports, parent overrides or the flow population invalidate
dependent proposals and results. Changing public or contract inputs invalidates
the result without changing the training graph. Failed imports preserve the
previous valid selection. Retry and partial acceptance retain selected versions
and recheck integrity; changed training inputs require another proposal review.
Confirmation is tied to the proposal displayed in the form; a form left open
from an older proposal cannot confirm its replacement. Reopening a current
proposal retains the previously confirmed group and manual edits.
If a failed run retains an older result, it is labeled as a prior screen and
exports only that screen's evidence.

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
pre-disclosure availability and retain that limitation in reports. Optional
checks also enforce declared public-release, document, review and effective
dates; collection receipts still do not establish historical availability. Missing
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
