"""Local, explicitly scoped workforce counts for independent flow populations."""

import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Any

from nopoach.analytical_inputs import BundleRef, publish_bundle, read_bundle
from nopoach.market.coverage import Headcount, WorkforceEstimate
from nopoach.market.define import FlowDefinedMarket
from nopoach.store.workspace import Workspace

MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10000


@dataclass(frozen=True)
class PopulationCount:
    employer_id: str
    year: int
    headcount: Headcount
    as_of: date


@dataclass(frozen=True)
class FlowPopulation:
    population_id: str
    description: str
    provenance_ids: frozenset[str]
    employer_ids: frozenset[str]
    counts: tuple[PopulationCount, ...]
    reference: BundleRef | None = None
    receipt_id: str = ""

    def estimates(
        self, market: FlowDefinedMarket, *, as_of: date | None = None
    ) -> tuple[WorkforceEstimate, ...]:
        if (
            market.population_id != self.population_id
            or market.source_provenance_ids != self.provenance_ids
            or market.candidate_employer_ids != self.employer_ids
        ):
            raise ValueError("Headcount declaration does not match the flow population")
        return tuple(
            WorkforceEstimate(
                row.employer_id,
                row.year,
                frozenset(),
                None,
                Headcount(
                    row.headcount.value,
                    row.headcount.lower,
                    row.headcount.upper,
                    f"{row.headcount.source} [{self.receipt_id}]",
                ),
                self.population_id,
            )
            for row in self.counts
            if market.start_year <= row.year <= market.end_year
            and (as_of is None or row.as_of <= as_of)
        )


def parse_population(body: bytes, metadata: dict[str, Any]) -> FlowPopulation:
    if len(body) > MAX_BYTES:
        raise ValueError("Population CSV exceeds the 4 MiB limit")
    for key in ("population_id", "description", "source"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise ValueError(f"Population declaration requires {key}")
    for key in ("provenance_ids", "employer_ids"):
        if (
            not isinstance(metadata.get(key), list)
            or not metadata[key]
            or any(not isinstance(v, str) or not v.strip() for v in metadata[key])
        ):
            raise ValueError(f"Population declaration requires {key}")
    employer_ids = frozenset(metadata["employer_ids"])
    rows: dict[tuple[str, int], PopulationCount] = {}
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
        required = {"employer_id", "year", "value", "lower", "upper", "source", "as_of"}
        if (
            not reader.fieldnames
            or set(reader.fieldnames) != required
            or len(reader.fieldnames) != len(required)
        ):
            raise ValueError(
                "Population CSV needs employer, year, value, lower, upper, "
                "source, as_of"
            )
        for number, row in enumerate(reader, 1):
            if number > MAX_ROWS:
                raise ValueError("Population CSV exceeds 10000 rows")
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Population CSV row has the wrong field count")
            employer, year = row["employer_id"], int(row["year"])
            if employer not in employer_ids or not 1 <= year <= 9999:
                raise ValueError("Count must name a declared employer and valid year")
            count = PopulationCount(
                employer,
                year,
                Headcount(
                    float(row["value"]),
                    float(row["lower"]),
                    float(row["upper"]) if row["upper"] else None,
                    row["source"],
                ),
                date.fromisoformat(row["as_of"]),
            )
            row_key = employer, year
            if row_key in rows and rows[row_key] != count:
                raise ValueError(
                    "Conflicting population headcounts; review the conflict first"
                )
            rows[row_key] = count
    except (UnicodeError, csv.Error, TypeError) as exc:
        raise ValueError("Invalid UTF-8 population CSV") from exc
    return FlowPopulation(
        metadata["population_id"],
        metadata["description"],
        frozenset(metadata["provenance_ids"]),
        employer_ids,
        tuple(rows[k] for k in sorted(rows)),
    )


def import_population(
    workspace: Workspace, body: bytes, metadata: dict[str, Any]
) -> BundleRef:
    parse_population(body, metadata)
    return publish_bundle(
        workspace, kind="flow_population", body=body, metadata=metadata
    )


def load_population(workspace: Workspace, reference: BundleRef) -> FlowPopulation:
    from dataclasses import replace

    if reference.kind != "flow_population":
        raise ValueError("Select a flow population bundle")
    bundle = read_bundle(workspace, reference)
    return replace(
        parse_population(bundle.body, bundle.metadata),
        reference=reference,
        receipt_id=bundle.provenance_id,
    )
