"""Append-only collection receipts, mirrored into the workspace database.

The public API cannot revise or delete a receipt. The JSONL log is authoritative;
opening a workspace reconciles it with DuckDB after an interrupted append. This
is application-level append-only storage, not protection from the OS owner
rewriting both the database and the log.
"""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4

import duckdb


class AppendOnlyError(ValueError):
    """An existing collection receipt cannot be changed or removed."""


@dataclass(frozen=True)
class ProvenanceRecord:
    id: str
    source: str
    url: str
    collected_at: datetime
    method: str
    collection_method: str
    sha256: str
    status_code: int | None = None

    def database_values(self) -> tuple[object, ...]:
        return (
            self.id,
            self.source,
            self.url,
            self.collected_at.astimezone(UTC).replace(tzinfo=None),
            self.method,
            self.collection_method,
            self.sha256,
            self.status_code,
        )


class ProvenanceLog:
    """One writer per workspace; immutable receipts and append-only file writes."""

    def __init__(
        self,
        path: Path,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> None:
        self.path = path
        self._connection = connection
        self._lock = RLock()
        descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)
        if connection is not None:
            records = self.records()
            logged_ids = {record.id for record in records}
            database_ids = {
                row[0]
                for row in connection.execute("SELECT id FROM provenance").fetchall()
            }
            if not database_ids.issubset(logged_ids):
                raise AppendOnlyError("Existing provenance records removed from log")
            for record in records:
                self._mirror(record)

    def records(self) -> tuple[ProvenanceRecord, ...]:
        with self._lock:
            records = []
            seen = set()
            for line in self.path.read_text(encoding="utf-8").splitlines():
                data = json.loads(line)
                data["collected_at"] = datetime.fromisoformat(data["collected_at"])
                record = ProvenanceRecord(**data)
                if record.id in seen:
                    raise AppendOnlyError("Duplicate existing provenance record")
                seen.add(record.id)
                records.append(record)
            return tuple(records)

    def append(
        self,
        *,
        source: str,
        url: str,
        method: str,
        body: bytes,
        collection_method: str = "public_http_fetch",
        status_code: int | None = None,
    ) -> ProvenanceRecord:
        """Hash raw bytes and durably record their receipt before parsing."""
        record = ProvenanceRecord(
            id=str(uuid4()),
            source=source,
            url=url,
            collected_at=datetime.now(UTC),
            method=method,
            collection_method=collection_method,
            sha256=sha256(body).hexdigest(),
            status_code=status_code,
        )
        self.append_record(record)
        return record

    def append_record(self, record: ProvenanceRecord) -> None:
        """Append a new receipt; even an identical existing ID is rejected."""
        with self._lock:
            if any(existing.id == record.id for existing in self.records()):
                raise AppendOnlyError("Cannot edit an existing provenance record")
            data = asdict(record)
            data["collected_at"] = record.collected_at.isoformat()
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(data, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._mirror(record)

    def _mirror(self, record: ProvenanceRecord) -> None:
        if self._connection is None:
            return
        existing = self._connection.execute(
            "SELECT * FROM provenance WHERE id = ?", [record.id]
        ).fetchone()
        if existing is not None:
            if existing != record.database_values():
                raise AppendOnlyError("Cannot edit an existing provenance record")
        else:
            self._connection.execute(
                "INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                record.database_values(),
            )
