"""Whole-file OFLC LCA/PERM disclosure collection; employer footprints, not a census.

Public bulk disclosures: https://www.dol.gov/agencies/eta/foreign-labor/performance
Reuse policy: https://www.dol.gov/general/aboutdol/copyright
Pass DOL_HOSTS through FetchClient's allowed_hosts configuration (merge with
other collectors' hosts when needed). No accounts or employer-specific requests.
Metro assignment requires the caller's geography crosswalk; city names alone
are not metro codes. An unmapped worksite retains a None metro.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from nopoach.collect.fetch import FetchClient, FetchedResponse
from nopoach.collect.tables import SourceSchemaError as SourceSchemaError
from nopoach.collect.tables import count, filename, number, require, tables
from nopoach.resolve.employers import EmployerResolver
from nopoach.store.provenance import ProvenanceRecord
from nopoach.store.workspace import Workspace

DOL_HOSTS = {"www.dol.gov": 1.0, "dol.gov": 1.0}
MetroLookup = Callable[[str, str, str], str | None]


@dataclass(frozen=True)
class DolRecord:
    program: Literal["LCA", "PERM"]
    case_number: str
    employer_name: str
    employer_id: str
    soc_code: str
    worksite_city: str
    worksite_state: str
    worksite_postal_code: str
    worksite_metro: str | None
    wage_from: Decimal | None
    wage_to: Decimal | None
    wage_unit: str
    annual_wage_from: Decimal | None
    annual_wage_to: Decimal | None
    worker_positions: int | None
    source_file: str
    provenance: ProvenanceRecord
    decision_date: date | None = None
    employment_begin_date: date | None = None
    employment_end_date: date | None = None


def _annual_wage(wage: Decimal | None, unit: str) -> Decimal | None:
    """Comparison convention: 40h/week, 52 weeks/year; raw wage/unit retained."""
    factor = {"hour": 2080, "week": 52, "bi-weekly": 26, "month": 12, "year": 1}.get(
        unit.casefold()
    )
    return wage * factor if wage is not None and factor is not None else None


def parse_dol(
    response: FetchedResponse,
    resolver: EmployerResolver,
    *,
    program: Literal["LCA", "PERM"],
    state: str | None = None,
    metro_lookup: MetroLookup | None = None,
) -> list[DolRecord]:
    """Parse an audited bulk response, filter geography locally, resolve employers.

    LCA positions are requested visa positions, not observed workers/headcounts.
    PERM filings supply no worker count. Never use either as a workforce census.
    """
    if program not in {"LCA", "PERM"}:
        raise ValueError("program must be LCA or PERM")
    wage_prefix = "WAGE_RATE_OF_PAY" if program == "LCA" else "WAGE_OFFER"
    wage_unit = "WAGE_UNIT_OF_PAY" if program == "LCA" else "WAGE_OFFER_UNIT_OF_PAY"
    required = [
        "CASE_NUMBER",
        "EMPLOYER_NAME",
        "SOC_CODE",
        "WORKSITE_CITY",
        "WORKSITE_STATE",
        "WORKSITE_POSTAL_CODE",
        f"{wage_prefix}_FROM",
        f"{wage_prefix}_TO",
        wage_unit,
    ]
    if program == "LCA":
        required.append("TOTAL_WORKER_POSITIONS")
    source_tables = list(tables(response.body, filename(response)))
    # Check schema across the file before writing any employer-resolution facts.
    for table in source_tables:
        require(table, required)
    records = []
    for table in source_tables:
        for row in table.rows:
            worksite_state = row["WORKSITE_STATE"].upper()
            if state and worksite_state != state.upper():
                continue
            low = number(
                row[f"{wage_prefix}_FROM"], table.filename, f"{wage_prefix}_FROM"
            )
            high = number(row[f"{wage_prefix}_TO"], table.filename, f"{wage_prefix}_TO")
            workers = (
                count(
                    row["TOTAL_WORKER_POSITIONS"],
                    table.filename,
                    "TOTAL_WORKER_POSITIONS",
                )
                if program == "LCA"
                else None
            )
            city, postal = row["WORKSITE_CITY"], row["WORKSITE_POSTAL_CODE"]
            metro = metro_lookup(city, worksite_state, postal) if metro_lookup else None
            entity = resolver.resolve(
                row["EMPLOYER_NAME"], provenance_id=response.provenance.id
            )
            records.append(
                DolRecord(
                    program,
                    row["CASE_NUMBER"],
                    row["EMPLOYER_NAME"],
                    entity.entity_id,
                    row["SOC_CODE"].split(".")[0],
                    city,
                    worksite_state,
                    postal,
                    metro,
                    low,
                    high,
                    row[wage_unit],
                    _annual_wage(low, row[wage_unit]),
                    _annual_wage(high, row[wage_unit]),
                    workers,
                    table.filename,
                    response.provenance,
                    _event_date(
                        row.get("DECISION_DATE") or row.get("CASE_DECISION_DATE", "")
                    ),
                    _event_date(
                        row.get("EMPLOYMENT_BEGIN_DATE") or row.get("BEGIN_DATE", "")
                    ),
                    _event_date(
                        row.get("EMPLOYMENT_END_DATE") or row.get("END_DATE", "")
                    ),
                )
            )
    for record in records:
        encoded = json.dumps({"version": 1, "record": asdict(record)}, default=str)
        identifier = sha256(
            f"{record.provenance.id}:{record.source_file}:{encoded}".encode()
        ).hexdigest()
        resolver.connection.execute(
            "INSERT INTO dol_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            [
                identifier,
                record.employer_id,
                record.program,
                record.case_number,
                record.decision_date,
                record.employment_begin_date,
                record.employment_end_date,
                encoded,
                record.provenance.id,
            ],
        )
    return records


def collect_dol(
    client: FetchClient,
    url: str,
    resolver: EmployerResolver,
    *,
    program: Literal["LCA", "PERM"],
    state: str | None = None,
    metro_lookup: MetroLookup | None = None,
) -> list[DolRecord]:
    """Fetch the entire chosen disclosure file, then select the state's worksites."""
    return parse_dol(
        client.fetch(url),
        resolver,
        program=program,
        state=state,
        metro_lookup=metro_lookup,
    )


def _event_date(value: str) -> date | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid DOL event date: {value!r}")


def load_dol_records(workspace: Workspace) -> tuple[DolRecord, ...]:
    """Read retained parsed bulk records with event dates and original receipts."""
    result = []
    for (encoded,) in workspace.connection.execute(
        "SELECT details_json FROM dol_records ORDER BY id"
    ).fetchall():
        payload = json.loads(encoded)
        if payload["version"] != 1:
            raise ValueError("Unsupported DOL evidence version")
        data = payload["record"]
        for key in ("wage_from", "wage_to", "annual_wage_from", "annual_wage_to"):
            data[key] = Decimal(data[key]) if data[key] is not None else None
        for key in ("decision_date", "employment_begin_date", "employment_end_date"):
            data[key] = date.fromisoformat(data[key]) if data[key] else None
        data["provenance"] = ProvenanceRecord.from_json(data["provenance"])
        result.append(DolRecord(**data))
    return tuple(result)
