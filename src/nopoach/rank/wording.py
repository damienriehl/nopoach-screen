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
