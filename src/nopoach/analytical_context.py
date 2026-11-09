import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from math import atan2, cos, degrees, fsum, hypot, radians, sin
from typing import Any, cast

from nopoach.analytical_inputs import AnalyticalInputs, BundleRef, read_bundle
from nopoach.market.clusters import ClusterProposal, ClusterSelection
from nopoach.market.define import Employer, FlowDefinedMarket, scope_contains, soc_code
from nopoach.rank.explanations import EmployerExplanationData
from nopoach.signals.flow import ResolvedPosition
from nopoach.signals.lehd import J2JContext
from nopoach.signals.noncompetes import ContractAssessment
from nopoach.store.workspace import Workspace


@dataclass(frozen=True)
class AnalyticalSource:
    kind: str
    bundle_id: str
    manifest_sha256: str
    raw_sha256: str
    normalized_sha256: str | None
    transformation: str | None
    metadata_json: str
    provenance_id: str
    source: str
    url: str
    collected_at: str
    method: str
    collection_method: str

    @property
    def metadata(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.metadata_json))


@dataclass(frozen=True)
class AnalyticalSnapshot:
    inputs: AnalyticalInputs
    sources: tuple[AnalyticalSource, ...]
    public_context: J2JContext | None = None
    contracts: ContractAssessment | None = None
    cluster_proposal: ClusterProposal | None = None
    cluster_selection: ClusterSelection | None = None
    covariate_omissions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "Optional analytical checks provide context, not findings of liability.",
        "Collection receipts do not establish historical source availability.",
    )
    employer_names: tuple[tuple[str, str], ...] = ()

    def employer_name(self, identifier: str) -> str:
        return dict(self.employer_names).get(identifier, identifier)


def freeze_sources(
    workspace: Workspace, inputs: AnalyticalInputs
) -> tuple[AnalyticalSource, ...]:
    """Verify every selected bundle and retain declarations without raw records."""
    receipts = {r.id: r for r in workspace.provenance.records()}
    result = []
    for ref in (inputs.j2j, inputs.contracts, inputs.flow_population):
        if ref is None:
            continue
        bundle = read_bundle(workspace, ref)
        receipt = receipts[bundle.provenance_id]
        result.append(
            AnalyticalSource(
                ref.kind,
                ref.bundle_id,
                ref.manifest_sha256,
                bundle.raw_sha256,
                bundle.normalized_sha256,
                bundle.transformation,
                bundle.metadata_json,
                receipt.id,
                receipt.source,
                receipt.url,
                receipt.collected_at.isoformat(),
                receipt.method,
                receipt.collection_method,
            )
        )
    return tuple(result)


def population_references(ref: BundleRef, receipt_id: str) -> tuple[str, ...]:
    """Pin the declaration and receipt even if its numeric graph is unchanged."""
    return (
        f"bundle:{ref.bundle_id}:manifest:{ref.manifest_sha256}",
        f"provenance:{receipt_id}",
    )


def _spherical_centroid(points: set[tuple[float, float]]) -> tuple[float, float] | None:
    x = fsum(cos(radians(lat)) * cos(radians(lon)) for lat, lon in points)
    y = fsum(cos(radians(lat)) * sin(radians(lon)) for lat, lon in points)
    z = fsum(sin(radians(lat)) for lat, _ in points)
    if hypot(hypot(x, y), z) <= 1e-12 * len(points):
        return None
    return degrees(atan2(z, hypot(x, y))), degrees(atan2(y, x))


def flow_explanation_inputs(
    workspace: Workspace,
    positions: tuple[ResolvedPosition, ...],
    employers: tuple[Employer, ...],
    market: FlowDefinedMarket,
    end: date,
) -> tuple[
    dict[str, EmployerExplanationData], dict[str, tuple[float, float]], tuple[str, ...]
]:
    """Use supplied population histories and actual supplied worksite coordinates."""
    start = date(market.start_year, 1, 1)
    sites: dict[str, set[tuple[float, float]]] = defaultdict(set)
    receipts: dict[str, set[str]] = defaultdict(set)
    occupations: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    spells: dict[tuple[str, str], list[tuple[date, date]]] = defaultdict(list)
    histories: dict[str, list[ResolvedPosition]] = defaultdict(list)
    worksites = {
        row[0]: row[1:]
        for row in workspace.connection.execute(
            "SELECT position_id, latitude, longitude, provenance_id "
            "FROM position_worksites"
        ).fetchall()
    }
    applicable = set()
    supplied_spells: Counter[str] = Counter()
    missing_occupations: Counter[str] = Counter()
    missing_coordinates: Counter[str] = Counter()
    unknown_dates: Counter[str] = Counter()
    for p in positions:
        histories[p.person_id].append(p)
        if not scope_contains(
            market, p.employer_id, p.occupation_code, p.geography, p.provenance_id
        ):
            continue
        if p.start_date is None:
            unknown_dates[p.employer_id] += 1
            continue
        if p.start_date > end or (p.end_date is not None and p.end_date < start):
            continue
        supplied_spells[p.employer_id] += 1
        applicable.add(p.id)
        receipts[p.employer_id].add(p.provenance_id)
        if p.occupation_code:
            occupations[p.employer_id][soc_code(p.occupation_code)].add(p.person_id)
        else:
            missing_occupations[p.employer_id] += 1
        spells[p.employer_id, p.person_id].append(
            (max(start, p.start_date), min(end, p.end_date or end))
        )
        site = worksites.get(p.id)
        if site is not None and site[0] is not None and site[1] is not None:
            sites[p.employer_id].add((site[0], site[1]))
            receipts[p.employer_id].add(site[2])
        else:
            missing_coordinates[p.employer_id] += 1
    exposure: dict[str, float] = defaultdict(float)
    for (employer_id, _), intervals in spells.items():
        merged: list[tuple[date, date]] = []
        for first, last in sorted(intervals):
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        exposure[employer_id] += (
            sum((last - first).days + 1 for first, last in merged) / 365.25
        )
    hires: dict[str, set[tuple[str, date]]] = defaultdict(set)
    for person, history in histories.items():
        history.sort(key=lambda p: (p.start_date or date.min, p.id))
        for i, p in enumerate(history):
            if (
                p.id in applicable
                and p.start_date is not None
                and start <= p.start_date <= end
                and (i == 0 or history[i - 1].employer_id != p.employer_id)
            ):
                hires[p.employer_id].add((person, p.start_date))
    result = {}
    locations = {}
    omissions = []
    for employer in employers:
        identifier = employer.id
        counts = occupations[identifier]
        total = sum(len(people) for people in counts.values())
        shares = (
            {code: len(people) / total for code, people in counts.items()}
            if total
            else None
        )
        centroid = _spherical_centroid(sites[identifier]) if sites[identifier] else None
        if centroid is None:
            omissions.append(
                f"{employer.name}: no supported supplied worksite centroid; "
                "flow covariates omitted."
            )
        elif missing_coordinates[identifier]:
            known = supplied_spells[identifier] - missing_coordinates[identifier]
            omissions.append(
                f"{employer.name}: centroid and footprint use supplied coordinates "
                f"from {known} of {supplied_spells[identifier]} in-period spells; "
                "spells without coordinates are excluded from geographic covariates."
            )
        if shares is None:
            omissions.append(
                f"{employer.name}: no attributable occupation support; "
                "flow covariates omitted."
            )
        elif missing_occupations[identifier]:
            known = supplied_spells[identifier] - missing_occupations[identifier]
            omissions.append(
                f"{employer.name}: shares use known occupations from {known} of "
                f"{supplied_spells[identifier]} in-period spells; "
                "spells without occupations are excluded from occupation shares."
            )
        if unknown_dates[identifier]:
            omissions.append(
                f"{employer.name}: {unknown_dates[identifier]} supplied spells "
                "have unknown start dates and cannot support in-period covariates."
            )
        if centroid is not None and shares is not None:
            locations[identifier] = centroid
        result[identifier] = EmployerExplanationData(
            occupation_shares=shares,
            external_hire_rate=len(hires[identifier]) / exposure[identifier]
            if exposure[identifier]
            else None,
            provenance_ids=tuple(sorted(receipts[identifier])),
            worksites_latlon=tuple(sorted(sites[identifier])),
        )
    return result, locations, tuple(omissions)
