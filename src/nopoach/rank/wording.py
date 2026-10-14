"""Central wording for ranking leads and describing explanation checks (U10)."""

from collections.abc import Sequence
from typing import Literal

SignalKey = Literal["flow", "clauses", "ties"]
ExplanationKey = Literal[
    "geographic_separation",
    "pay_gap",
    "occupational_mismatch",
    "low_lateral_hiring",
    "non_compete_prevalence",
]
ExplanationStatus = Literal["passed", "failed", "not_testable"]

SIGNAL_LABELS: dict[SignalKey, str] = {
    "flow": "Missing worker flow",
    "clauses": "Filed employee restriction",
    "ties": "Executive and board ties",
}
EXPLANATION_LABELS: dict[ExplanationKey, str] = {
    "geographic_separation": "Geographic separation",
    "pay_gap": "Pay gap",
    "occupational_mismatch": "Occupational mismatch",
    "low_lateral_hiring": "Low lateral hiring",
    "non_compete_prevalence": "Non-compete prevalence",
}
STATUS_LABELS: dict[ExplanationStatus, str] = {
    "passed": "Passed",
    "failed": "Failed",
    "not_testable": "Not testable",
}
MUTUAL_LABEL = "Lead for further inquiry"
ONE_WAY_LABEL = "Possible unilateral no-hire policy"
NO_SIGNAL_LABEL = "No screening signals fired"
RANK_METHOD = (
    "Fixed weights: worker flow 50%, filed employee restrictions 30%, "
    "executive and board ties 20%. Filed restrictions score 1 when present; "
    "ties score raw / (raw + 4). Unavailable or non-firing flow scores 0. "
    "Missing signals do not redistribute weights. Pairs with at least two "
    "signals rank first, then by combined score and employer IDs. One-way flow "
    "suppression ranks separately. Scores are screening heuristics, not "
    "probabilities. Explanation checks do not change the rank."
)
EXPLANATION_METHOD = (
    "Passed means the supplied data supports a possible innocent explanation; "
    "failed means the check did not support that explanation, without ruling it "
    "out. Not testable means the necessary data or extractor is unavailable. "
    "Thresholds are provisional screening conventions, not validated findings."
)
GEOGRAPHY_UNAVAILABLE = (
    "Worksite coordinates for both employer footprints were not supplied; "
    "geographic separation is not testable."
)
PAY_UNAVAILABLE = (
    "Both employers need comparable annual LCA wages for the same occupation "
    "and geography; the supplied records do not support that comparison."
)
OCCUPATIONS_UNAVAILABLE = (
    "Occupation shares for both employers were not supplied; occupational "
    "mismatch is not testable."
)
NON_COMPETE_UNAVAILABLE = (
    "Non-compete prevalence is not testable in version 1: "
    "no non-compete extractor is built."
)
RATE_UNAVAILABLE = "unavailable"


def pair_summary(
    employer_a: str,
    employer_b: str,
    signals: Sequence[SignalKey],
    *,
    one_way: bool,
) -> str:
    if not signals:
        return (
            f"{employer_a} / {employer_b}: No screening signals fired in the "
            "supplied evidence. Review data sufficiency and explanation checks."
        )
    labels = ", ".join(SIGNAL_LABELS[key] for key in signals)
    direction = f"{ONE_WAY_LABEL}. " if one_way else ""
    return (
        f"{employer_a} / {employer_b}: {direction}The pattern warrants further "
        f"inquiry. Screening signals: {labels}. Review the tested and untested "
        "innocent explanations before pursuing this lead."
    )


def geographic_reason(distance_km: float, threshold_km: float) -> str:
    return (
        f"The nearest worksites in the supplied footprints are {distance_km:.1f} "
        f"km apart; the geographic-separation threshold is {threshold_km:g} km. "
        "This comparison covers only the supplied worksites."
    )


def pay_reason(gap: float, threshold: float, cells: int) -> str:
    return (
        f"Median relative gap in annual LCA offered wages is {gap:.1%} across "
        f"{cells} matched occupation/geography cells; the pay-gap threshold is "
        f"{threshold:.0%}. Each cell compares median annual wage lower bounds "
        "and divides the difference by the higher median. LCA offered wages "
        "are a visa-worker proxy, not a workforce census."
    )


def occupation_reason(overlap: float, threshold: float) -> str:
    return (
        f"Occupational overlap is {overlap:.1%}, using the sum of the smaller "
        f"share for each occupation; mismatch is supported at overlap of "
        f"{threshold:.0%} or less."
    )


def lateral_reason(
    employer_a: str,
    employer_b: str,
    rate_a: float | None,
    rate_b: float | None,
    threshold: float,
) -> str:
    a = RATE_UNAVAILABLE if rate_a is None else f"{rate_a:.1%}"
    b = RATE_UNAVAILABLE if rate_b is None else f"{rate_b:.1%}"
    return (
        f"Overall annual external hire rates: {employer_a}: {a}; "
        f"{employer_b}: {b}. Low lateral hiring is supported if either "
        f"employer's rate is {threshold:.0%} or less. These are firm-wide rates, "
        "not the flow between this pair. An unavailable rate remains untested."
    )


# Dossier presentation belongs here alongside the ranking language. Evidence
# values and verbatim source excerpts are supplied by the screen, not invented.
DOSSIER_TITLE = "Employer pair screening dossier"
DOSSIER_HEADINGS = {
    "summary": "Plain language summary",
    "flow": "Worker flow",
    "clauses": "Filed clause excerpts",
    "ties": "Executive and board ties",
    "explanations": "Explanation checks",
    "coverage": "Coverage and sufficiency",
    "sources": "Data sources",
    "method": "Method note",
}
PARTIAL_SCREEN_BANNER = (
    "Partial screen. Some signals have insufficient data or collection evidence. "
    "Review the coverage and sufficiency section before pursuing this lead. "
    "Accepting a partial screen does not make those signals reliable."
)
NO_FILED_CLAUSES = "no filed clauses found in the collected period"
NO_TIES = "No executive or board ties found in the supplied records."
NO_FLOW = "Worker flow could not be estimated from the supplied records."
FLOW_DESCRIPTION = (
    "Each panel shows workers moving from one employer to the other by year. "
    "Observed moves are counted in the supplied job histories. Expected moves "
    "are the model's comparison for this market, accounting for employer size, "
    "hiring changes, distance and occupation overlap. A shaded interval brackets "
    "a detected downward break; it does not establish an exact date or cause."
)
FLOW_AXIS_YEAR = "Year"
FLOW_AXIS_MOVES = "Worker moves per year"
FLOW_OBSERVED = "Observed moves"
FLOW_EXPECTED = "Expected moves"
COVERAGE_DESCRIPTION = (
    "Coverage compares distinct visible workers with estimated workers at each "
    "employer in each year. Unknown denominators remain unknown. The fraction "
    "describes the supplied data, not all workers who changed jobs."
)
COVERAGE_COLUMNS = (
    "Employer",
    "Year",
    "Visible workers",
    "Estimated workers",
    "Coverage",
)
SOURCE_CLASS_UNKNOWN = "not recorded"
SYNTHETIC_NOTICE = (
    "Synthetic records demonstrate the method and cannot support an enforcement lead."
)
RECORD_SET_NOTE = (
    "The chart cites a stored screen record set. In this workspace, find the "
    "screens row by its ID and read parameters_json.references for the input "
    "record IDs, market and cutoff. The whole market is cited because expected "
    "flow uses other employers too. Other citations name a workspace table and "
    "record ID. Provenance receipts retain source locations and content hashes."
)
DOSSIER_METHOD = (
    "The screen compares three independent signals to identify leads for further "
    "inquiry. A low number of moves can have innocent explanations, including "
    "distance, pay differences, different occupations, low hiring overall and "
    "individual non-compete restrictions. The checks describe what the supplied "
    "data can test; further evidence is needed to assess a lead. Expected flow "
    "uses a Poisson model with separate origin and destination effects by year. "
    "Break detection looks for a sustained drop, and shrinkage and multiple "
    "comparison adjustment reduce unstable flags. Model and ranking thresholds "
    "are provisional and require real-data validation."
)


def dossier_scope(
    employer_a: str,
    employer_b: str,
    occupations: Sequence[str],
    geography_kind: str,
    geography_code: str,
    start_year: int,
    end_year: int,
    as_of: str | None,
) -> str:
    cutoff = f" Event cutoff: {as_of}." if as_of else ""
    return (
        f"Employers: {employer_a} and {employer_b}. Occupation codes: "
        f"{', '.join(occupations)}. Geography: {geography_kind} {geography_code}. "
        f"Screen period: {start_year}–{end_year}.{cutoff}"
    )


def flow_direction(source: str, destination: str) -> str:
    return f"{source} to {destination}"


def flow_caption(
    record_set: str,
    breaks: Sequence[tuple[str, tuple[int, int] | None]],
) -> str:
    intervals = "; ".join(
        f"{direction}: {interval[0]}–{interval[1]}"
        if interval is not None
        else f"{direction}: no downward break detected"
        for direction, interval in breaks
    )
    return (
        "Figure 1. Observed and expected worker moves. Break interval: "
        f"{intervals}. Record set: {record_set}."
    )


def flow_break_description(
    direction: str, before: float, after: float, interval: tuple[int, int]
) -> str:
    return (
        f"{direction}: observed moves averaged {before:.1f} per year before the "
        f"break and {after:.1f} after it. The break interval is "
        f"{interval[0]}–{interval[1]}."
    )


def dossier_citation(kind: str, identifier: str) -> str:
    return f"{kind}:{identifier}"


def clause_citation(
    clause_id: str,
    document_name: str,
    filed_on: str,
    start: int,
    end: int,
    binding: str,
    duration: str | None,
    scope: str | None,
    receipt_id: str,
    url: str,
) -> str:
    return (
        f"Filed document: {document_name or 'unnamed document'}; filed on {filed_on}; "
        f"text characters {start}–{end}; binding described in the text: {binding}; "
        f"duration: {duration or 'not stated'}; scope: {scope or 'not stated'}. "
        f"Citations: clauses:{clause_id}; provenance:{receipt_id}; {url}. "
        "The excerpt describes filed language, not a finding about the pair."
    )


def tie_description(kind: str, start: str | None, end: str | None) -> str:
    return (
        f"Link: {kind.replace('_', ' ')}. Reported period: "
        f"{start or 'unknown'} to {end or 'unknown'}."
    )


def tie_position(
    name: str,
    employer: str,
    title: str | None,
    precision: str,
    basis: str,
    position_id: str,
    receipt_id: str,
) -> str:
    return (
        f"{name}; {employer}; {title or 'title unknown'}. Date precision: "
        f"{precision}; basis: {basis.replace('_', ' ')}. "
        f"Citations: positions:{position_id}; provenance:{receipt_id}."
    )


def explanation_entry(
    label: str, status: str, reason: str, receipts: Sequence[str]
) -> str:
    citation = "; ".join(f"provenance:{r}" for r in receipts)
    return f"{label} — {status}. {reason}" + (
        f" Citations: {citation}." if citation else ""
    )


def sufficiency_entry(signal: str, status: str, reason: str) -> str:
    return f"{signal}: {status}. {reason}"


def source_entry(
    source: str,
    provenance_class: str,
    collection_method: str,
    url: str,
    collected_at: str,
    digest: str,
    receipt_id: str,
) -> str:
    return (
        f"Source: {source}. Provenance class: {provenance_class}. Collection method: "
        f"{collection_method}. Collected at: {collected_at}. Location: {url}. "
        f"Content SHA256: {digest}. Citation: provenance:{receipt_id}."
    )
