"""Dated parent links on U1's table, with provenance and lawyer overrides.

Intervals are inclusive. The most recently recorded active manual override wins;
otherwise the most recent effective SEC link wins. Resolution keeps the original
employer separate and returns the parent path so a dossier can name its view.
"""

from datetime import date
from typing import Any, Literal
from uuid import uuid4

from nopoach.imports.schema import initialize_import_schema
from nopoach.store.workspace import Workspace


class ParentResolver:
    def __init__(self, workspace: Workspace) -> None:
        self.connection = workspace.connection
        initialize_import_schema(self.connection)

    def add_link(
        self,
        employer_id: str,
        parent_id: str,
        *,
        valid_from: date,
        provenance_id: str,
        valid_to: date | None = None,
        origin: Literal["sec_ex21", "manual_override"] = "sec_ex21",
        reason: str | None = None,
    ) -> str:
        if valid_to is not None and valid_to < valid_from:
            raise ValueError("valid_to before valid_from")
        if origin == "manual_override" and not (reason and reason.strip()):
            raise ValueError("Manual overrides require a reason")
        if origin not in {"sec_ex21", "manual_override"}:
            raise ValueError("Unknown parent-link origin")
        link_id = str(uuid4())
        self.connection.execute("BEGIN TRANSACTION")
        try:
            order = self.connection.execute(
                "SELECT coalesce(max(recorded_order), 0) + 1 FROM parent_link_metadata"
            ).fetchone()
            assert order is not None
            self.connection.execute(
                "INSERT INTO employer_parents VALUES (?, ?, ?, ?, ?, ?)",
                [link_id, employer_id, parent_id, valid_from, valid_to, provenance_id],
            )
            self.connection.execute(
                "INSERT INTO parent_link_metadata VALUES (?, ?, ?, ?)",
                [link_id, origin, reason, order[0]],
            )
            # The active graph changes only at interval boundaries. Check every
            # boundary before committing a link that could corrupt parent views.
            boundaries = {valid_from}
            for start, end in self.connection.execute(
                "SELECT valid_from, valid_to FROM employer_parents"
            ).fetchall():
                if start >= valid_from and (valid_to is None or start <= valid_to):
                    boundaries.add(start)
                if end is not None and end < date.max:
                    after_end = date.fromordinal(end.toordinal() + 1)
                    if after_end >= valid_from and (
                        valid_to is None or after_end <= valid_to
                    ):
                        boundaries.add(after_end)
            for boundary in boundaries:
                self.ultimate_parent(employer_id, boundary)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return link_id

    def evidence(self, employer_id: str, as_of: date) -> list[dict[str, Any]]:
        path: list[dict[str, Any]] = []
        seen = set()
        current = employer_id
        while True:
            if current in seen:
                raise ValueError("Parent cycle in dated ownership links")
            seen.add(current)
            link = self.connection.execute(
                "SELECT p.id, p.parent_id, p.valid_from, p.valid_to, p.provenance_id, "
                "coalesce(m.origin, 'sec_ex21'), m.reason FROM employer_parents p "
                "LEFT JOIN parent_link_metadata m ON m.link_id = p.id "
                "WHERE p.employer_id = ? AND p.valid_from <= ? "
                "AND (p.valid_to IS NULL OR p.valid_to >= ?) "
                "ORDER BY (m.origin = 'manual_override') DESC NULLS LAST, "
                "CASE WHEN m.origin = 'manual_override' THEN m.recorded_order END "
                "DESC NULLS LAST, p.valid_from DESC, m.recorded_order DESC NULLS LAST, "
                "p.id LIMIT 1",
                [current, as_of, as_of],
            ).fetchone()
            if link is None:
                return path
            path.append(
                {
                    "link_id": link[0],
                    "employer_id": current,
                    "parent_id": link[1],
                    "valid_from": link[2],
                    "valid_to": link[3],
                    "provenance_id": link[4],
                    "origin": link[5],
                    "reason": link[6],
                }
            )
            current = link[1]

    def ultimate_parent(self, employer_id: str, as_of: date) -> str:
        path = self.evidence(employer_id, as_of)
        return str(path[-1]["parent_id"]) if path else employer_id
