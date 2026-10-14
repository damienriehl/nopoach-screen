"""Whole-file OFLC LCA/PERM disclosure collection; employer footprints, not a census.

Public bulk disclosures: https://www.dol.gov/agencies/eta/foreign-labor/performance
Reuse policy: https://www.dol.gov/general/aboutdol/copyright
Pass DOL_HOSTS through FetchClient's allowed_hosts configuration (merge with
other collectors' hosts when needed). No accounts or employer-specific requests.
Metro assignment requires the caller's geography crosswalk; city names alone
are not metro codes. An unmapped worksite retains a None metro.
"""

import csv
import io
import json
import posixpath
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Literal
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile, is_zipfile

from nopoach.collect.fetch import FetchClient, FetchedResponse
from nopoach.resolve.employers import EmployerResolver
from nopoach.store.provenance import ProvenanceRecord
from nopoach.store.workspace import Workspace

DOL_HOSTS = {"www.dol.gov": 1.0, "dol.gov": 1.0}
MetroLookup = Callable[[str, str, str], str | None]
_XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_EVENT_COLUMNS = {
    "DECISION_DATE",
    "CASE_DECISION_DATE",
    "EMPLOYMENT_BEGIN_DATE",
    "EMPLOYMENT_END_DATE",
    "BEGIN_DATE",
    "END_DATE",
}


class SourceSchemaError(ValueError):
    """A bulk source no longer supplies a required column."""


@dataclass(frozen=True)
class _Table:
    filename: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def _table(filename: str, rows: Sequence[Sequence[str]]) -> _Table:
    if not rows:
        return _Table(filename, (), ())
    columns = tuple(value.strip().lstrip("\ufeff") for value in rows[0])
    return _Table(
        filename,
        columns,
        tuple(
            {
                column: row[index].strip() if index < len(row) else ""
                for index, column in enumerate(columns)
            }
            for row in rows[1:]
            if any(value.strip() for value in row)
        ),
    )


def _xlsx_tables(archive: ZipFile, filename: str) -> Iterator[_Table]:
    epoch = date(1899, 12, 30)
    if "xl/workbook.xml" in archive.namelist():
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        properties = workbook.find(f"{_XML_NS}workbookPr")
        if properties is not None and properties.get("date1904") in {"1", "true"}:
            epoch = date(1904, 1, 1)
    strings: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(node.itertext()) for node in root.findall(f"{_XML_NS}si")]
    for sheet in sorted(archive.namelist()):
        if not sheet.startswith("xl/worksheets/") or not sheet.endswith(".xml"):
            continue
        rows: list[list[str]] = []
        root = ElementTree.fromstring(archive.read(sheet))
        for element in root.iter(f"{_XML_NS}row"):
            cells: dict[int, str] = {}
            for cell in element.findall(f"{_XML_NS}c"):
                reference = cell.attrib["r"]
                index = 0
                for letter in reference:
                    if letter.isalpha():
                        index = index * 26 + ord(letter.upper()) - ord("A") + 1
                value = cell.findtext(f"{_XML_NS}v", default="")
                if cell.get("t") == "s":
                    value = strings[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.iter(f"{_XML_NS}t")
                    )
                elif (
                    value
                    and rows
                    and index <= len(rows[0])
                    and rows[0][index - 1] in _EVENT_COLUMNS
                    and cell.get("t") in {None, "n"}
                ):
                    # XLSX stores actual date cells as days in its workbook epoch.
                    value = (epoch + timedelta(days=float(value))).isoformat()
                cells[index - 1] = value
            if cells:
                rows.append([cells.get(index, "") for index in range(max(cells) + 1)])
        yield _table(f"{filename}:{sheet}", rows)


def _tables(body: bytes, filename: str) -> Iterator[_Table]:
    """Read public CSV/TSV, zipped tables, and XLSX without extra dependencies."""
    stream = io.BytesIO(body)
    if is_zipfile(stream):
        with ZipFile(stream) as archive:
            if any(name.startswith("xl/worksheets/") for name in archive.namelist()):
                yield from _xlsx_tables(archive, filename)
            else:
                members = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".csv", ".txt", ".xlsx"))
                ]
                if not members:
                    raise SourceSchemaError(f"{filename}: no CSV/TSV/XLSX data table")
                for name in members:
                    yield from _tables(archive.read(name), f"{filename}:{name}")
        return
    text = body.decode("utf-8-sig")
    header = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in header else ","
    yield _table(filename, list(csv.reader(io.StringIO(text), delimiter=delimiter)))


def _require(table: _Table, columns: Sequence[str]) -> None:
    for column in columns:
        if column not in table.columns:
            raise SourceSchemaError(
                f"{table.filename}: missing required column {column}"
            )


def _number(value: str, filename: str, column: str) -> Decimal | None:
    # BLS nondisclosure/top-coding symbols are unknown, never zero.
    if value.strip() in {"", "*", "**", "#", "~", "N/A"}:
        return None
    try:
        number = Decimal(value.replace(",", ""))
        if not number.is_finite():
            raise InvalidOperation
        return number
    except InvalidOperation as error:
        raise ValueError(
            f"{filename}: invalid numeric value in column {column}"
        ) from error


def _count(value: str, filename: str, column: str) -> int | None:
    number = _number(value, filename, column)
    if number is None:
        return None
    if number != number.to_integral_value() or number < 0:
        raise ValueError(f"{filename}: invalid employment count in column {column}")
    return int(number)


def _filename(response: FetchedResponse) -> str:
    return unquote(posixpath.basename(urlsplit(response.provenance.url).path))


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
    tables = list(_tables(response.body, _filename(response)))
    # Check schema across the file before writing any employer-resolution facts.
    for table in tables:
        _require(table, required)
    records = []
    for table in tables:
        for row in table.rows:
            worksite_state = row["WORKSITE_STATE"].upper()
            if state and worksite_state != state.upper():
                continue
            low = _number(
                row[f"{wage_prefix}_FROM"], table.filename, f"{wage_prefix}_FROM"
            )
            high = _number(
                row[f"{wage_prefix}_TO"], table.filename, f"{wage_prefix}_TO"
            )
            workers = (
                _count(
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
        receipt = data["provenance"]
        receipt["collected_at"] = datetime.fromisoformat(receipt["collected_at"])
        data["provenance"] = ProvenanceRecord(**receipt)
        result.append(DolRecord(**data))
    return tuple(result)
