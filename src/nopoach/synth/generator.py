"""Seeded gravity-model employment histories with separately retained truth.

Employment rows always enter storage through import_records(). Coverage
is a stable person/employer/occupation mask of whole spells, not a new draw
each year: a missing employer can obscure one endpoint of an observed move.
No network or real data is used.
"""

import json
from dataclasses import asdict, dataclass
from datetime import date
from math import dist, exp, pi
from typing import TypedDict
from uuid import uuid4

import numpy as np

from nopoach.imports.loader import ImportResult, import_records
from nopoach.imports.schema import ProvenanceClass
from nopoach.market.coverage import Headcount, store_workforce_input
from nopoach.market.define import Geography, Market
from nopoach.resolve.employers import EmployerResolver
from nopoach.store.workspace import Workspace
from nopoach.synth.scenarios import Scenario


class _SyntheticCount(TypedDict):
    employer_key: str
    employer_name: str
    year: int
    occupation_codes: list[str]
    value: int
    as_of: str
    source: str


@dataclass(frozen=True)
class Move:
    person_key: str
    source: str
    destination: str
    occupation_code: str
    year: int
    provenance_method: str = "synthetic"


@dataclass(frozen=True)
class GeneratedMarket:
    scenario: Scenario
    seed: int
    rows: tuple[dict[str, str], ...]
    true_rows: tuple[dict[str, str], ...]
    moves: tuple[Move, ...]

    @property
    def true_employer_count(self) -> int:
        return len(self.scenario.employers)

    @property
    def true_person_count(self) -> int:
        return sum(employer.size for employer in self.scenario.employers)


def generate_market(scenario: Scenario, *, seed: int) -> GeneratedMarket:
    """Simulate a fixed cohort, with at most one job-to-job move per worker/year.

    Destination gravity is occupational headcount / other eligible headcount,
    multiplied by exp(-distance / scale) and min(1, destination_pay/source_pay).
    That share times the occupation's annual move rate gives a per-worker
    hazard. Effects multiply hazards without reallocating suppressed moves.
    Initial occupational counts use largest-remainder rounding. Employer sizes
    then evolve with moves; initial sizes remain the gravity attraction masses.
    Positions start in the preceding year and moves happen each January 1.
    The independent coverage stream cannot alter workers or planted moves.
    """
    movement_seed, coverage_seed = np.random.SeedSequence(seed).spawn(2)
    rng = np.random.default_rng(movement_seed)
    coverage_rng = np.random.default_rng(coverage_seed)
    employers = scenario.employers
    occupations = scenario.occupations
    employer_index = {employer.key: i for i, employer in enumerate(employers)}
    current_list: list[int] = []
    occupation_list: list[int] = []
    masses = np.zeros((len(employers), len(occupations)), dtype=float)
    for i, employer in enumerate(employers):
        expected = np.array(employer.occupation_shares) * employer.size
        counts = np.floor(expected).astype(int)
        remainder = employer.size - int(counts.sum())
        order = np.argsort(-(expected - counts), kind="stable")
        counts[order[:remainder]] += 1
        masses[i] = counts
        for o, count in enumerate(counts):
            current_list.extend([i] * int(count))
            occupation_list.extend([o] * int(count))
    current = np.array(current_list, dtype=int)
    worker_occupations = np.array(occupation_list, dtype=int)
    started = np.full(len(current), scenario.start_year - 1, dtype=int)
    visibility = coverage_rng.random((len(current), len(employers)))
    fractions = np.ones((len(employers), len(occupations)), dtype=float)
    for mask in scenario.coverage:
        for o, occupation in enumerate(occupations):
            if mask.occupation_code is None or mask.occupation_code == occupation.code:
                fractions[employer_index[mask.employer], o] *= mask.fraction

    # Hazards are fixed by declared market structure; yearly effects and current
    # worker exposures are separate, so a freeze is not a planted pair effect.
    hazards = np.zeros((len(employers), len(occupations), len(employers)))
    for source, employer in enumerate(employers):
        for o, occupation in enumerate(occupations):
            other_mass = float(masses[:, o].sum() - masses[source, o])
            if other_mass == 0:
                continue
            for destination, partner in enumerate(employers):
                if source == destination:
                    continue
                hazards[source, o, destination] = (
                    occupation.yearly_move_rate
                    * masses[destination, o]
                    / other_mass
                    * exp(
                        -dist(employer.location, partner.location)
                        / scenario.distance_scale_km
                    )
                    * min(1, partner.pay / employer.pay)
                )

    rows: list[dict[str, str]] = []
    observed: list[dict[str, str]] = []
    moves: list[Move] = []

    def spell(worker: int, end_year: int | None) -> None:
        employer_id = int(current[worker])
        occupation_id = int(worker_occupations[worker])
        employer = employers[employer_id]
        occupation = occupations[occupation_id]
        row = {
            "person_key": f"synthetic-worker-{worker:07d}",
            "person_name": f"Synthetic Worker {worker:07d}",
            "employer_name": employer.name,
            "employer_id": employer.key,
            "employer_id_type": f"synthetic:{scenario.name}:seed:{seed}",
            "title": occupation.title,
            "occupation_code": occupation.code,
            "location": (
                f"Synthetic coordinates {employer.location[0]}, "
                f"{employer.location[1]} km"
            ),
            "cbsa": "SYNTHETIC",
            # Invented centroids: map fixture kilometres at the equator to degrees.
            "latitude": str(employer.location[1] / (6371 * pi / 180)),
            "longitude": str(employer.location[0] / (6371 * pi / 180)),
            "start_date": f"{int(started[worker]):04d}-01-01",
            "end_date": "" if end_year is None else f"{end_year:04d}-12-31",
            "start_precision": "day",
            "end_precision": "unknown" if end_year is None else "day",
            "provenance_method": "synthetic",
            "provenance_class": "synthetic",
        }
        rows.append(row)
        if visibility[worker, employer_id] < fractions[employer_id, occupation_id]:
            observed.append(row.copy())

    for year in range(scenario.start_year, scenario.end_year + 1):
        yearly = hazards.copy()
        for effect in scenario.pair_effects:
            if year >= effect.start_year:
                source, destination = (
                    employer_index[effect.source],
                    employer_index[effect.destination],
                )
                yearly[source, :, destination] *= effect.factor
                if effect.mutual:
                    yearly[destination, :, source] *= effect.factor
        for freeze in scenario.hiring_freezes:
            if year >= freeze.start_year:
                yearly[:, :, employer_index[freeze.employer]] *= freeze.factor
        draws = rng.random(len(current))
        cumulative = np.cumsum(yearly[current, worker_occupations], axis=1)
        destinations = (draws[:, None] >= cumulative).sum(axis=1)
        for worker_index in np.flatnonzero(destinations < len(employers)):
            w = int(worker_index)
            destination = int(destinations[w])
            spell(w, year - 1)
            moves.append(
                Move(
                    f"synthetic-worker-{w:07d}",
                    employers[int(current[w])].key,
                    employers[destination].key,
                    occupations[int(worker_occupations[w])].code,
                    year,
                )
            )
            current[w] = destination
            started[w] = year
    for worker in range(len(current)):
        spell(worker, None)
    return GeneratedMarket(scenario, seed, tuple(observed), tuple(rows), tuple(moves))


def import_market(workspace: Workspace, market: GeneratedMarket) -> ImportResult:
    """Import only visible employment spells, preserving synthetic labels.

    AE4's invented filing/clause facts use the existing workspace tables and a separate
    synthetic receipt hashing the clause fixture. They do not affect worker
    histories. Employers absent from visible data have no imported filing.
    """
    source = f"synthetic:{market.scenario.name}:seed:{market.seed}"
    result = import_records(
        workspace,
        market.rows,
        source=source,
        provenance_class=ProvenanceClass.SYNTHETIC,
        person_key_namespace=source,
    )
    scenario = market.scenario
    resolved = dict(
        workspace.connection.execute(
            "SELECT external_id, employer_id FROM employer_aliases "
            "WHERE external_id_type = ?",
            [source.casefold()],
        ).fetchall()
    )
    visible_employers = set(resolved)
    codes = frozenset(o.code for o in scenario.occupations)
    selections = {codes} | {frozenset({code}) for code in codes}
    counts: list[_SyntheticCount] = [
        {
            "employer_key": employer.key,
            "employer_name": employer.name,
            "year": year,
            "occupation_codes": sorted(selection),
            "value": sum(
                r["employer_id"] == employer.key
                and r["occupation_code"] in selection
                and r["start_date"] <= f"{year:04d}-01-01"
                and (not r["end_date"] or r["end_date"] >= f"{year:04d}-01-01")
                for r in market.true_rows
            ),
            "as_of": f"{year:04d}-01-01",
            "source": "synthetic",
        }
        for employer in scenario.employers
        for year in range(scenario.start_year, scenario.end_year + 1)
        for selection in sorted(selections, key=lambda value: tuple(sorted(value)))
    ]
    count_receipt = workspace.provenance.append(
        source=source,
        url="file:synthetic-denominators",
        method="IMPORT",
        body=json.dumps(counts, sort_keys=True).encode(),
        collection_method="synthetic",
    )
    resolver = EmployerResolver(workspace)
    for spec in scenario.employers:
        if spec.key not in resolved:
            resolved[spec.key] = resolver.resolve(
                spec.name,
                provenance_id=count_receipt.id,
                employer_id=spec.key,
                employer_id_type=source,
            ).entity_id
    for count in counts:
        definition = Market(
            frozenset(count["occupation_codes"]),
            Geography("metro", "SYNTHETIC"),
            scenario.start_year,
            scenario.end_year,
        )
        value = count["value"]
        store_workforce_input(
            workspace,
            resolved[count["employer_key"]],
            count["year"],
            definition,
            kind="imported",
            headcount=Headcount(value, value, value, "synthetic"),
            as_of=date.fromisoformat(count["as_of"]),
            provenance_id=count_receipt.id,
        )
    for clause in market.scenario.clauses:
        if clause.employer_a not in visible_employers:
            continue
        employer = workspace.connection.execute(
            "SELECT employer_id FROM employer_aliases "
            "WHERE external_id = ? AND external_id_type = ? LIMIT 1",
            [clause.employer_a, source.casefold()],
        ).fetchone()
        if employer is None:
            continue
        receipt = workspace.provenance.append(
            source=source,
            url="file:synthetic-clause",
            method="IMPORT",
            body=json.dumps(asdict(clause), sort_keys=True).encode(),
            collection_method="synthetic",
        )
        filing_id = str(uuid4())
        workspace.connection.execute(
            "INSERT INTO filings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                filing_id,
                employer[0],
                "synthetic_contract",
                f"{clause.start_year:04d}-01-01",
                f"synthetic-{filing_id}",
                "file:synthetic-clause",
                receipt.id,
            ],
        )
        workspace.connection.execute(
            "INSERT INTO clauses VALUES (?, ?, ?, ?, ?, ?)",
            [
                str(uuid4()),
                filing_id,
                clause.text,
                "mutual_non_solicit",
                f"synthetic:{clause.employer_a}:{clause.employer_b}; "
                f"{clause.start_year}-{clause.end_year}",
                receipt.id,
            ],
        )
    return result
