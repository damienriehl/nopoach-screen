"""Read-only, aggregate identities for validation packets.

The identities deliberately contain no source rows.  They identify the retained
logical inputs and installed implementation, rather than mutable database bytes.
"""

import hashlib
import importlib.metadata
import json
import platform
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nopoach.store import connect_read_only

CORE_TABLES = frozenset(
    {
        "employers",
        "employer_parents",
        "people",
        "positions",
        "position_worksites",
        "workforce_inputs",
        "dol_records",
        "filings",
        "clauses",
        "ties",
    }
)
OPTIONAL_TABLES = frozenset(
    {
        "imports",
        "imported_rows",
        "employer_aliases",
        "person_keys",
        "parent_link_metadata",
        "clause_details",
    }
)
IGNORED_TABLES = frozenset({"markets", "screens", "provenance"})


@dataclass(frozen=True)
class TableIdentity:
    name: str
    present: bool
    columns: tuple[tuple[str, str], ...]
    rows: int
    sha256: str | None


@dataclass(frozen=True)
class InputIdentity:
    version: int
    status: str
    sha256: str | None
    tables: tuple[TableIdentity, ...]
    reason: str | None = None


def _value(value: Any) -> Any:
    if value is None:
        return {"null": True}
    if isinstance(value, (date, datetime)):
        return {"type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, float):
        return {"type": "float", "value": value.hex()}
    if isinstance(value, str):
        # JSON columns compare by meaning while ordinary strings remain literal.
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {"type": "str", "value": value}
        return {"type": "str", "value": value, "json": parsed}
    return {"type": type(value).__name__, "value": value}


def _batches(cursor: duckdb.DuckDBPyConnection) -> Iterator[tuple[Any, ...]]:
    while rows := cursor.fetchmany(512):
        yield from rows


def input_identity(connection: duckdb.DuckDBPyConnection) -> InputIdentity:
    """Fingerprint all supported persisted inputs in deterministic row order."""
    names = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    unsupported = names - CORE_TABLES - OPTIONAL_TABLES - IGNORED_TABLES
    missing = CORE_TABLES - names
    if missing or unsupported:
        reason = "; ".join(
            part
            for part in (
                f"missing core tables: {', '.join(sorted(missing))}" if missing else "",
                f"unsupported tables: {', '.join(sorted(unsupported))}"
                if unsupported
                else "",
            )
            if part
        )
        return InputIdentity(1, "unsupported", None, (), reason)
    tables: list[TableIdentity] = []
    aggregate = hashlib.sha256()
    for name in sorted(CORE_TABLES | OPTIONAL_TABLES):
        if name not in names:
            item = TableIdentity(name, False, (), 0, None)
        else:
            quoted = '"' + name.replace('"', '""') + '"'
            info = connection.execute(f"DESCRIBE {quoted}").fetchall()
            columns = tuple((str(row[0]), str(row[1])) for row in info)
            # Sorting every column is conservative and preserves duplicate rows.
            order = ", ".join(str(index) for index in range(1, len(columns) + 1))
            digest = hashlib.sha256()
            count = 0
            cursor = connection.execute(f"SELECT * FROM {quoted} ORDER BY {order}")
            for row in _batches(cursor):
                digest.update(
                    json.dumps(
                        [_value(v) for v in row], sort_keys=True, separators=(",", ":")
                    ).encode()
                )
                digest.update(b"\n")
                count += 1
            item = TableIdentity(name, True, columns, count, digest.hexdigest())
        encoded = json.dumps(asdict(item), sort_keys=True, separators=(",", ":"))
        aggregate.update(encoded.encode())
        tables.append(item)
    return InputIdentity(1, "available", aggregate.hexdigest(), tuple(tables))


def workspace_input_identity(root: str | Path) -> InputIdentity:
    """Inspect an existing workspace without initializing or repairing it."""
    connection = connect_read_only(Path(root) / "workspace.duckdb")
    try:
        return input_identity(connection)
    finally:
        connection.close()


def software_identity() -> dict[str, Any]:
    """Identify executable package sources, resources, runtime and distributions."""
    package = Path(__file__).resolve().parents[1]
    files: dict[str, str] = {}
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".yaml"}:
            files[path.relative_to(package).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    dependencies: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            dependencies[name.lower()] = distribution.version
    payload: dict[str, Any] = {
        "version": 1,
        "package_version": importlib.metadata.version("nopoach-screen"),
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "platform": platform.platform(),
        "dependencies": dict(sorted(dependencies.items())),
        "files": files,
    }
    compatible = {
        key: payload[key]
        for key in (
            "version",
            "package_version",
            "python",
            "implementation",
            "dependencies",
            "files",
        )
    }
    payload["compatibility_sha256"] = hashlib.sha256(
        json.dumps(compatible, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload
