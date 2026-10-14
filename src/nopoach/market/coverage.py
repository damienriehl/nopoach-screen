"""Workforce estimates and annual, person-deduplicated coverage of a defined market.

Headcount inputs already describe the selected occupations/geography/year,
except company counts, which need national industry staffing and an explicit
geography allocation. The SEC people collector has no employee-count extractor:
accept SEC 10-K/Form 990 counts from the caller, without adding collection here.
Workers are annual observations with resolved IDs/SOC and caller-crosswalked
geographies; LCA positions are never added to the visible-worker numerator.
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import isfinite
from typing import Literal
from uuid import uuid4

from nopoach.collect.bls import OewsRecord
from nopoach.collect.dol import DolRecord, load_dol_records
from nopoach.market.define import Employer, Geography, Market, soc_code
from nopoach.store.workspace import Workspace

COMPANY_ALLOCATION_UNCERTAINTY = 0.5


@dataclass(frozen=True)
class Headcount:
    value: float
    lower: float
    upper: float | None
    source: str
    uncertainty_basis: str = (
        "Source-supplied bounds, not a calibrated confidence interval"
    )

    def __post_init__(self) -> None:
        if (
            not isfinite(self.value)
            or not isfinite(self.lower)
            or not 0 <= self.lower <= self.value
            or (
                self.upper is not None
                and (not isfinite(self.upper) or self.upper < self.value)
            )
            or not self.source.strip()
        ):
            raise ValueError(
                "Headcount requires finite ordered nonnegative bounds and source"
            )


@dataclass(frozen=True)
class WorkforceEstimate:
    employer_id: str
    year: int
    occupation_codes: frozenset[str]
    geography: Geography
    headcount: Headcount | None


def matching_estimates(
    market: Market, estimates: Iterable[WorkforceEstimate]
) -> dict[tuple[str, int], WorkforceEstimate]:
    result = {}
    for estimate in estimates:
        if (
            estimate.occupation_codes != market.occupation_codes
            or estimate.geography != market.geography
            or not market.start_year <= estimate.year <= market.end_year
        ):
            continue
        key = estimate.employer_id, estimate.year
        if key in result:
            raise ValueError("Supply one KTD16 estimate per employer/year/market")
        result[key] = estimate
    return result


def estimate_workforce(
    employer_id: str,
    year: int,
    market: Market,
    *,
    imported: Headcount | None = None,
    lawyer: Headcount | None = None,
    company: Headcount | None = None,
    staffing: Iterable[OewsRecord] = (),
    industry_code: str | None = None,
    geography_share: Decimal | None = None,
    geography_source: str = "",
    lca: Iterable[DolRecord] = (),
) -> WorkforceEstimate:
    """Apply the fixed chain, returning unknown when required inputs are absent.

    Company allocation uses a declared +/-50% sensitivity band, propagated
    through any company-count bounds; it is not a statistical confidence interval.
    LCA must be the caller's selected-year bulk records: DOL records have no year.
    Its requested positions form a proxy with an unbounded upper uncertainty,
    never a claim of actual employer headcount or visa-worker employment.
    """
    headcount = imported if imported is not None else lawyer
    if headcount is None and company is not None:
        shares = [
            r
            for r in staffing
            if r.area_type == "1"
            and r.naics == industry_code
            and r.soc_code in market.occupation_codes
        ]
        by_soc = {r.soc_code: r for r in shares}
        if len(by_soc) != len(shares):
            raise ValueError(
                "Supply one national staffing cell per occupation/industry"
            )
        if (
            by_soc.keys() == market.occupation_codes
            and all(r.staffing_share is not None for r in shares)
            and geography_share is not None
            and geography_source.strip()
        ):
            share = sum(
                (r.staffing_share for r in shares if r.staffing_share is not None),
                Decimal(0),
            )
            if (
                not geography_share.is_finite()
                or not 0 <= geography_share <= 1
                or not 0 <= share <= 1
            ):
                raise ValueError(
                    "Staffing and geography shares must be finite fractions"
                )
            allocation = float(share * geography_share)
            source = (
                f"{company.source}; BLS industry staffing: "
                + ", ".join(
                    sorted({f"{r.source_file} [{r.provenance.id}]" for r in shares})
                )
                + f"; geography: {geography_source}"
            )
            headcount = Headcount(
                company.value * allocation,
                company.lower * allocation * (1 - COMPANY_ALLOCATION_UNCERTAINTY),
                None
                if company.upper is None
                else company.upper * allocation * (1 + COMPANY_ALLOCATION_UNCERTAINTY),
                source,
                "Company-count bounds plus +/-50% allocation sensitivity; "
                "not a confidence interval",
            )
    if headcount is None:
        footprints: dict[str, DolRecord] = {}
        for row in lca:
            geo = (
                row.worksite_state
                if market.geography.kind == "state"
                else row.worksite_metro
            )
            if (
                row.program == "LCA"
                and row.employer_id == employer_id
                and soc_code(row.soc_code) in market.occupation_codes
                and geo == market.geography.code
                and row.worker_positions is not None
            ):
                footprints[row.case_number] = row
        if footprints:
            headcount = Headcount(
                sum(r.worker_positions or 0 for r in footprints.values()),
                0,
                None,
                "LCA footprint: "
                + ", ".join(
                    sorted(
                        {
                            f"{r.source_file} [{r.provenance.id}]"
                            for r in footprints.values()
                        }
                    )
                ),
                "Requested visa positions, not a workforce census; upper bound unknown",
            )
    return WorkforceEstimate(
        employer_id, year, market.occupation_codes, market.geography, headcount
    )


@dataclass(frozen=True)
class MarketWorker:
    person_id: str
    employer_id: str
    occupation_code: str
    geography: Geography
    year: int


@dataclass(frozen=True)
class EmployerCoverage:
    employer: Employer
    year: int
    visible_workers: int
    estimate: WorkforceEstimate | None
    fraction: float | None

    @property
    def source(self) -> str:
        return (
            self.estimate.headcount.source
            if self.estimate and self.estimate.headcount
            else "denominator unknown"
        )

    @property
    def label(self) -> str:
        return (
            "denominator unknown" if self.fraction is None else f"{self.fraction:.1%}"
        )


@dataclass(frozen=True)
class OverallCoverage:
    year: int
    visible_workers: int
    estimated_workers: float
    fraction: float | None
    unknown_employer_ids: tuple[str, ...]


@dataclass(frozen=True)
class CoverageReport:
    market: Market
    employers: tuple[EmployerCoverage, ...]
    overall: tuple[OverallCoverage, ...]


def coverage_report(
    market: Market,
    employers: Iterable[Employer],
    estimates: Iterable[WorkforceEstimate] | None = None,
    workers: Iterable[MarketWorker] = (),
    *,
    workspace: Workspace | None = None,
    as_of: date | None = None,
) -> CoverageReport:
    """Count distinct visible persons per employer/year and weight the total.

    Overall coverage is unknown if any selected employer lacks a denominator;
    estimated_workers then describes only the known subtotal. Ratios are not
    clipped at 100%, so inconsistent supplied denominators remain visible.
    """
    selected = sorted(
        (
            e
            for e in employers
            if e.id not in market.removed_employer_ids
            and (market.employer_ids is None or e.id in market.employer_ids)
        ),
        key=lambda e: e.id,
    )
    if estimates is None:
        estimates = (
            workspace_estimates(workspace, market, as_of=as_of) if workspace else ()
        )
    known = matching_estimates(market, estimates)
    visible: dict[tuple[str, int], set[str]] = defaultdict(set)
    for worker in workers:
        if (
            worker.occupation_code
            and soc_code(worker.occupation_code) in market.occupation_codes
            and worker.geography == market.geography
            and market.start_year <= worker.year <= market.end_year
        ):
            visible[worker.employer_id, worker.year].add(worker.person_id)
    annual = []
    overall = []
    for year in range(market.start_year, market.end_year + 1):
        unknown = []
        denominator = 0.0
        numerator = 0
        for employer in selected:
            estimate = known.get((employer.id, year))
            headcount = estimate.headcount if estimate else None
            count = len(visible[employer.id, year])
            fraction = None
            if headcount is not None and headcount.value > 0:
                denominator += headcount.value
                fraction = count / headcount.value
            else:
                unknown.append(employer.id)
            numerator += count
            annual.append(EmployerCoverage(employer, year, count, estimate, fraction))
        overall.append(
            OverallCoverage(
                year,
                numerator,
                denominator,
                numerator / denominator if not unknown and denominator > 0 else None,
                tuple(unknown),
            )
        )
    return CoverageReport(market, tuple(annual), tuple(overall))


WorkforceInputKind = Literal[
    "imported", "lawyer", "company", "staffing_share", "geography_share"
]


def store_workforce_input(
    workspace: Workspace,
    employer_id: str,
    year: int,
    market: Market,
    *,
    kind: WorkforceInputKind,
    headcount: Headcount,
    as_of: date,
    provenance_id: str,
) -> str:
    """Persist a dated workforce input; shares cover the declared market's SOC set.

    Company counts are firm-wide. Staffing shares are BLS-derived fractions;
    geography shares are explicit allocations, never inferred from visible jobs.
    Latest dated input of each kind wins, with ID settling same-date entries.
    """
    if kind in {"staffing_share", "geography_share"} and (
        headcount.upper is None or headcount.upper > 1
    ):
        raise ValueError("Workforce shares require bounded fractions")
    identifier = str(uuid4())
    workspace.connection.execute(
        "INSERT INTO workforce_inputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            identifier,
            employer_id,
            year,
            json.dumps(sorted(market.occupation_codes)),
            market.geography.kind,
            market.geography.code,
            kind,
            headcount.value,
            headcount.lower,
            headcount.upper,
            headcount.source,
            as_of,
            provenance_id,
        ],
    )
    return identifier


def workspace_estimates(
    workspace: Workspace, market: Market, *, as_of: date | None = None
) -> tuple[WorkforceEstimate, ...]:
    """Estimate workforces from the latest inputs and period-dated LCA records."""
    inputs: dict[tuple[str, int], dict[str, Headcount]] = defaultdict(dict)
    for row in workspace.connection.execute(
        "SELECT employer_id, year, occupation_codes_json, geography_kind, "
        "geography_code, kind, value, lower_bound, upper_bound, source, "
        "as_of, provenance_id FROM workforce_inputs ORDER BY as_of, id"
    ).fetchall():
        if (
            frozenset(json.loads(row[2])) == market.occupation_codes
            and Geography(row[3], row[4]) == market.geography
            and market.start_year <= row[1] <= market.end_year
            and (as_of is None or row[10] <= as_of)
        ):
            inputs[row[0], row[1]][row[5]] = Headcount(
                row[6], row[7], row[8], f"{row[9]} [{row[11]}]"
            )
    lca = load_dol_records(workspace)
    result = []
    for (employer_id,) in workspace.connection.execute(
        "SELECT id FROM employers ORDER BY id"
    ).fetchall():
        for year in range(market.start_year, market.end_year + 1):
            values = inputs[employer_id, year]
            imported, lawyer = values.get("imported"), values.get("lawyer")
            company, staffing, geography = (
                values.get("company"),
                values.get("staffing_share"),
                values.get("geography_share"),
            )
            if (
                imported is None
                and lawyer is None
                and company
                and staffing
                and geography
            ):
                allocation = staffing.value * geography.value
                imported = Headcount(
                    company.value * allocation,
                    company.lower * allocation * (1 - COMPANY_ALLOCATION_UNCERTAINTY),
                    None
                    if company.upper is None
                    else company.upper
                    * allocation
                    * (1 + COMPANY_ALLOCATION_UNCERTAINTY),
                    f"{company.source}; BLS staffing: {staffing.source}; "
                    f"geography: {geography.source}",
                    "Company-count bounds plus +/-50% allocation sensitivity; "
                    "not a confidence interval",
                )
            records = [
                r
                for r in lca
                if dol_in_period(r, date(year, 1, 1), date(year, 12, 31), as_of=as_of)
            ]
            result.append(
                estimate_workforce(
                    employer_id,
                    year,
                    market,
                    imported=imported,
                    lawyer=lawyer,
                    lca=records,
                )
            )
    return tuple(result)


def dol_in_period(
    record: DolRecord, start: date, end: date, *, as_of: date | None = None
) -> bool:
    """Unknown-dated bulk records cannot establish period-specific footprints/pay."""
    dates = (
        record.decision_date,
        record.employment_begin_date,
    )
    if as_of is not None and any(d is not None and d > as_of for d in dates):
        return False
    if as_of is not None:
        end = min(end, as_of)
    if start > end:
        return False
    if record.employment_begin_date is not None:
        return record.employment_begin_date <= end and (
            record.employment_end_date is None or record.employment_end_date >= start
        )
    return record.decision_date is not None and start <= record.decision_date <= end
