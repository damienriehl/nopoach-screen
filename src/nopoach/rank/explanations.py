"""Data-supported innocent explanations, reported without changing ranks.

Fixed, provisional conventions: nearest-footprint distance >=250 km, median
within-cell relative offered-pay gap >=25%, occupation-share overlap <=20%,
or either firm's annual external hires / workforce exposure <=5%. These are
screening checks, not estimates that an explanation caused the observed flow.

Callers supply lawfully held data scoped to the screen's period and market.
Coordinates are planar kilometres in one common reference system (as in flow scoring),
not latitude/longitude. Occupation shares are comparable normalized fractions.
External hire rates describe ALL incoming external hires, not just this pair.
LCA records have no year; the caller must select the appropriate bulk files.
No coordinates, occupation shares or external hire rates are inferred from LCA
worker-position counts or missing pair flows. Non-competes are always untested.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import dist
from statistics import median

from nopoach.collect.dol import DolRecord
from nopoach.market.define import soc_code
from nopoach.rank import wording

GEOGRAPHIC_SEPARATION_KM = 250.0
PAY_GAP_THRESHOLD = 0.25
OCCUPATIONAL_OVERLAP_THRESHOLD = 0.20
LOW_EXTERNAL_HIRE_RATE = 0.05


@dataclass(frozen=True)
class EmployerExplanationData:
    worksites_km: tuple[tuple[float, float], ...] = ()
    lca_wages: tuple[DolRecord, ...] = ()
    occupation_shares: Mapping[str, float] | None = None
    external_hire_rate: float | None = None
    provenance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExplanationCheck:
    key: wording.ExplanationKey
    status: wording.ExplanationStatus
    reason: str
    measurements: Mapping[str, float] = field(default_factory=dict)
    provenance_ids: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return wording.EXPLANATION_LABELS[self.key]

    @property
    def status_label(self) -> str:
        return wording.STATUS_LABELS[self.status]


WageCell = tuple[str, str, str | None]


def _wages(
    employer_id: str, rows: Sequence[DolRecord]
) -> dict[WageCell, tuple[float, tuple[str, ...]]]:
    # A case repeated in bulk files contributes once; offers are not headcounts.
    cases = {
        row.case_number: row
        for row in rows
        if row.employer_id == employer_id
        and row.program == "LCA"
        and row.annual_wage_from is not None
        and row.annual_wage_from.is_finite()
        and row.annual_wage_from > 0
    }
    cells: dict[WageCell, list[DolRecord]] = defaultdict(list)
    for row in cases.values():
        cells[soc_code(row.soc_code), row.worksite_state, row.worksite_metro].append(
            row
        )
    return {
        key: (
            float(median(r.annual_wage_from for r in records if r.annual_wage_from)),
            tuple(sorted({r.provenance.id for r in records})),
        )
        for key, records in cells.items()
    }


def _status(supported: bool) -> wording.ExplanationStatus:
    return "passed" if supported else "failed"


def check_explanations(
    employer_a_id: str,
    employer_b_id: str,
    left: EmployerExplanationData,
    right: EmployerExplanationData,
) -> tuple[ExplanationCheck, ...]:
    """Return all five checks, including missing inputs and known proxy limits."""
    sources = tuple(sorted(set(left.provenance_ids + right.provenance_ids)))
    geography = ExplanationCheck(
        "geographic_separation", "not_testable", wording.GEOGRAPHY_UNAVAILABLE
    )
    if left.worksites_km and right.worksites_km:
        distance = min(
            dist(a, b) for a in left.worksites_km for b in right.worksites_km
        )
        geography = ExplanationCheck(
            "geographic_separation",
            _status(distance >= GEOGRAPHIC_SEPARATION_KM),
            wording.geographic_reason(distance, GEOGRAPHIC_SEPARATION_KM),
            {"distance_km": distance, "threshold_km": GEOGRAPHIC_SEPARATION_KM},
            sources,
        )

    pay = ExplanationCheck("pay_gap", "not_testable", wording.PAY_UNAVAILABLE)
    a_wages = _wages(employer_a_id, left.lca_wages)
    b_wages = _wages(employer_b_id, right.lca_wages)
    common = a_wages.keys() & b_wages.keys()
    if common:
        gap = median(
            abs(a_wages[key][0] - b_wages[key][0])
            / max(a_wages[key][0], b_wages[key][0])
            for key in common
        )
        receipts = tuple(
            sorted({p for key in common for p in a_wages[key][1] + b_wages[key][1]})
        )
        pay = ExplanationCheck(
            "pay_gap",
            _status(gap >= PAY_GAP_THRESHOLD),
            wording.pay_reason(gap, PAY_GAP_THRESHOLD, len(common)),
            {
                "relative_gap": gap,
                "threshold": PAY_GAP_THRESHOLD,
                "matched_cells": float(len(common)),
            },
            receipts,
        )

    occupations = ExplanationCheck(
        "occupational_mismatch", "not_testable", wording.OCCUPATIONS_UNAVAILABLE
    )
    if left.occupation_shares and right.occupation_shares:
        overlap = sum(
            min(share, right.occupation_shares.get(code, 0))
            for code, share in left.occupation_shares.items()
        )
        occupations = ExplanationCheck(
            "occupational_mismatch",
            _status(overlap <= OCCUPATIONAL_OVERLAP_THRESHOLD),
            wording.occupation_reason(overlap, OCCUPATIONAL_OVERLAP_THRESHOLD),
            {"overlap": overlap, "threshold": OCCUPATIONAL_OVERLAP_THRESHOLD},
            sources,
        )

    a_rate, b_rate = left.external_hire_rate, right.external_hire_rate
    rates = {
        key: rate
        for key, rate in (("a_rate", a_rate), ("b_rate", b_rate))
        if rate is not None
    }
    low = any(rate <= LOW_EXTERNAL_HIRE_RATE for rate in rates.values())
    lateral_status: wording.ExplanationStatus = (
        "passed" if low else "failed" if len(rates) == 2 else "not_testable"
    )
    lateral = ExplanationCheck(
        "low_lateral_hiring",
        lateral_status,
        wording.lateral_reason(
            employer_a_id, employer_b_id, a_rate, b_rate, LOW_EXTERNAL_HIRE_RATE
        ),
        {**rates, "threshold": LOW_EXTERNAL_HIRE_RATE},
        sources,
    )
    non_competes = ExplanationCheck(
        "non_compete_prevalence", "not_testable", wording.NON_COMPETE_UNAVAILABLE
    )
    return geography, pay, occupations, lateral, non_competes
