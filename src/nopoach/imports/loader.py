"""One local-file/programmatic employment loader for the UI and synthetic data.

No collection, credentials, or network calls occur here. R24 classes describe
provenance; selecting one does not certify that a file is lawfully held. The
agency supplies its lawful files and labels; all rows retain that declaration.
"""

import calendar
import csv
import io
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from nopoach.imports.schema import (
    COLUMNS,
    REQUIRED_COLUMNS,
    TITLE_SOC,
    VENDOR_ALIASES,
    ProvenanceClass,
    initialize_import_schema,
)
from nopoach.resolve.employers import EmployerResolver, normalize_name
from nopoach.resolve.people import PersonResolver
from nopoach.store.workspace import Workspace


@dataclass(frozen=True)
class RowReject:
    row_number: int
    reason: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ImportResult:
    import_id: str
    provenance_id: str
    position_ids: tuple[str, ...]
    rejects: tuple[RowReject, ...]
    unmapped_titles: Mapping[str, int]

    @property
    def accepted_count(self) -> int:
        return len(self.position_ids)


def column_mapping(
    columns: Sequence[str],
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve documented vendor aliases, with canonical -> vendor overrides."""
    overrides = overrides or {}
    unknown = set(overrides) - set(COLUMNS)
    if unknown:
        raise ValueError(f"Unknown canonical columns: {', '.join(sorted(unknown))}")
    if len(set(columns)) != len(columns):
        raise ValueError("Duplicate input column names")
    mapping = {}
    for column in COLUMNS:
        if column in overrides:
            candidates = [overrides[column]] if overrides[column] in columns else []
        elif column in columns:
            candidates = [column]
        else:
            candidates = [
                alias for alias in VENDOR_ALIASES.get(column, ()) if alias in columns
            ]
        if len(candidates) > 1:
            raise ValueError(f"Ambiguous column {column}; choose a column mapper")
        if candidates:
            mapping[column] = candidates[0]
    missing = [column for column in REQUIRED_COLUMNS if column not in mapping]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Column mapper assigns one input column to multiple fields")
    return mapping


def _metadata(
    source: str, provenance_class: ProvenanceClass | str | None
) -> ProvenanceClass:
    if not source.strip():
        raise ValueError("A source name is required")
    if not provenance_class:
        raise ValueError("Choose a provenance class before importing")
    try:
        return ProvenanceClass(provenance_class)
    except ValueError as exc:
        raise ValueError("Unknown provenance class") from exc


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _date(value: str, precision: str, field: str) -> date | None:
    if not value:
        if precision != "unknown":
            raise ValueError(f"{field}: empty date requires unknown precision")
        return None
    patterns = {"year": r"\d{4}", "month": r"\d{4}-\d{2}", "day": r"\d{4}-\d{2}-\d{2}"}
    if precision not in patterns or not re.fullmatch(patterns[precision], value):
        raise ValueError(f"{field}: date does not match {precision!r} precision")
    try:
        parts = [int(part) for part in value.split("-")]
        year = parts[0]
        month = parts[1] if len(parts) > 1 else (12 if field == "end_date" else 1)
        day = (
            parts[2]
            if len(parts) > 2
            else (calendar.monthrange(year, month)[1] if field == "end_date" else 1)
        )
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"{field}: invalid calendar date") from exc


def _validated(row: Mapping[str, Any]) -> dict[str, Any]:
    missing = [column for column in REQUIRED_COLUMNS if column not in row]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    if "__extra_columns__" in row:
        raise ValueError("CSV row field count does not match its header")
    data: dict[str, Any] = {column: _text(row.get(column)) for column in COLUMNS}
    for column in ("person_key", "employer_name", "title", "location"):
        if not data[column]:
            raise ValueError(f"{column} is required")
    if not normalize_name(data["employer_name"]):
        raise ValueError("employer_name must contain letters or digits")
    for field in ("start_date", "end_date"):
        data[field] = _date(
            data[field], data[field.replace("date", "precision")], field
        )
    if (
        data["start_date"]
        and data["end_date"]
        and data["end_date"] < data["start_date"]
    ):
        raise ValueError("end_date before start_date")
    code = data["occupation_code"]
    if code and not re.fullmatch(r"\d{2}-\d{4}", code):
        raise ValueError("occupation_code must use SOC format NN-NNNN")
    if not code:
        title = normalize_name(data["title"])
        for keyword, soc in TITLE_SOC:
            if re.search(r"\b" + re.escape(keyword) + r"\b", title):
                data["occupation_code"] = soc
                break
    return data


def import_file(
    workspace: Workspace,
    path: str | Path,
    *,
    source: str,
    provenance_class: ProvenanceClass | str | None,
    column_map: Mapping[str, str] | None = None,
    person_key_namespace: str | None = None,
) -> ImportResult:
    """Import a UTF-8 CSV or string-column Parquet, preserving file-byte evidence."""
    _metadata(source, provenance_class)
    path = Path(path)
    body = path.read_bytes()
    raw_rows: list[dict[str, Any]]
    if path.suffix.casefold() == ".csv":
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
        columns = reader.fieldnames or []
        mapping = column_mapping(columns, column_map)
        raw_rows = []
        for raw in reader:
            raw_rows.append(
                {
                    key if key is not None else "__extra_columns__": value
                    for key, value in raw.items()
                }
            )
    elif path.suffix.casefold() == ".parquet":
        # Parse the exact bytes hashed in the receipt, avoiding a second file read.
        import pyarrow.parquet as parquet  # type: ignore[import-untyped]

        table = parquet.read_table(io.BytesIO(body))
        columns = table.column_names
        mapping = column_mapping(columns, column_map)
        raw_rows = table.to_pylist()
    else:
        raise ValueError("Import format must be CSV or Parquet")
    mapped_rows = [
        {canonical: raw[vendor] for canonical, vendor in mapping.items()}
        for raw in raw_rows
    ]
    for raw, mapped in zip(raw_rows, mapped_rows, strict=True):
        if "__extra_columns__" in raw:
            mapped["__extra_columns__"] = raw["__extra_columns__"]
        if path.suffix.casefold() == ".csv" and any(
            value is None for value in raw.values()
        ):
            mapped["__extra_columns__"] = "CSV row has fewer fields than its header"
    return _import(
        workspace,
        mapped_rows,
        raw_rows,
        source=source,
        provenance_class=provenance_class,
        person_key_namespace=person_key_namespace,
        body=body,
        url=path.resolve().as_uri(),
    )


def import_records(
    workspace: Workspace,
    records: Iterable[Mapping[str, Any]],
    *,
    source: str,
    provenance_class: ProvenanceClass | str | None,
    person_key_namespace: str | None = None,
) -> ImportResult:
    """Canonical rows in; durable rows/rejects plus coverage counts out.

    The caller supplies synthetic or lawfully held records. A shared person-key
    namespace is an explicit assertion of provider-level identity, not guessing.
    """
    _metadata(source, provenance_class)
    rows = [dict(row) for row in records]
    body = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return _import(
        workspace,
        rows,
        rows,
        source=source,
        provenance_class=provenance_class,
        person_key_namespace=person_key_namespace,
        body=body,
        url="file:programmatic-records",
    )


def _import(
    workspace: Workspace,
    rows: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    source: str,
    provenance_class: ProvenanceClass | str | None,
    person_key_namespace: str | None,
    body: bytes,
    url: str,
) -> ImportResult:
    classification = _metadata(source, provenance_class)
    namespace = person_key_namespace if person_key_namespace is not None else source
    if not namespace.strip():
        raise ValueError("person_key_namespace must not be blank")
    initialize_import_schema(workspace.connection)
    employers = EmployerResolver(workspace)
    people = PersonResolver(workspace)
    receipt = workspace.provenance.append(
        source=source,
        url=url,
        method="IMPORT",
        body=body,
        collection_method="agency_import",
    )
    import_id = str(uuid4())
    connection = workspace.connection
    connection.execute(
        "INSERT INTO imports VALUES (?, ?, ?, ?, ?)",
        [import_id, source, classification.value, namespace, receipt.id],
    )
    positions = []
    rejects = []
    unmapped: Counter[str] = Counter()
    for number, (row, raw) in enumerate(zip(rows, raw_rows, strict=True), start=1):
        connection.execute("BEGIN TRANSACTION")
        try:
            data = _validated(row)
            employer = employers.resolve(
                data["employer_name"],
                provenance_id=receipt.id,
                employer_id=data["employer_id"] or None,
                employer_id_type=data["employer_id_type"] or source,
            )
            signature = (
                json.dumps(
                    [
                        employer.entity_id,
                        normalize_name(data["title"]),
                        normalize_name(data["location"]),
                        str(data["start_date"]),
                        data["start_precision"],
                    ]
                )
                if data["start_date"]
                else None
            )
            person = people.resolve(
                data["person_key"],
                name=data["person_name"] or None,
                source=source,
                provenance_id=receipt.id,
                namespace=namespace,
                employment_signature=signature,
            )
            position_id = str(uuid4())
            connection.execute(
                "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    position_id,
                    person.entity_id,
                    employer.entity_id,
                    data["title"],
                    data["occupation_code"] or None,
                    data["start_date"],
                    data["end_date"],
                    data["start_precision"],
                    data["end_precision"],
                    receipt.id,
                ],
            )
            connection.execute(
                "INSERT INTO imported_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    import_id,
                    number,
                    source,
                    classification.value,
                    json.dumps(raw, default=str),
                    position_id,
                    None,
                    data["location"],
                    receipt.id,
                ],
            )
            connection.execute("COMMIT")
        except ValueError as exc:
            connection.execute("ROLLBACK")
            rejection = RowReject(number, str(exc), dict(raw))
            rejects.append(rejection)
            connection.execute(
                "INSERT INTO imported_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    import_id,
                    number,
                    source,
                    classification.value,
                    json.dumps(raw, default=str),
                    None,
                    rejection.reason,
                    _text(row.get("location")),
                    receipt.id,
                ],
            )
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        else:
            positions.append(position_id)
            if not data["occupation_code"]:
                unmapped[data["title"]] += 1
    return ImportResult(
        import_id, receipt.id, tuple(positions), tuple(rejects), dict(unmapped)
    )
