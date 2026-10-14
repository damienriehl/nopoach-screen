"""Source-scoped person keys and conservative cross-source employment evidence."""

import json
from typing import Any
from uuid import uuid4

from nopoach.imports.schema import initialize_import_schema
from nopoach.resolve.employers import Resolution, fixed_matches, normalize_name
from nopoach.store.workspace import Workspace


class PersonResolver:
    def __init__(self, workspace: Workspace) -> None:
        self.connection = workspace.connection
        initialize_import_schema(self.connection)

    def resolve(
        self,
        person_key: str,
        *,
        source: str,
        provenance_id: str,
        name: str | None = None,
        namespace: str | None = None,
        employment_signature: str | None = None,
    ) -> Resolution:
        if not person_key.strip():
            raise ValueError("person_key is required")
        namespace = namespace or source
        normalized = normalize_name(name) if name else ""
        signature = employment_signature or ""
        aliases = self.connection.execute(
            "SELECT id, person_id, namespace, person_key, normalized_name, "
            "employment_signature FROM person_keys ORDER BY rowid"
        ).fetchall()
        # A keyless alias cannot bridge people with conflicting provider keys.
        conflicting = {
            person_id
            for _, person_id, _, stored_key, _, _ in aliases
            if stored_key and stored_key != person_key
        }
        key = json.dumps([namespace, person_key])
        identity = (
            json.dumps([normalized, signature]) if normalized and signature else ""
        )
        records = [{"unique_id": "incoming", "key": key, "identity": identity}]
        entities = {}
        for (
            alias_id,
            person_id,
            stored_namespace,
            stored_key,
            stored_name,
            stored_sig,
        ) in aliases:
            if person_id in conflicting:
                continue
            unique_id = "candidate:" + alias_id
            entities[unique_id] = person_id
            records.append(
                {
                    "unique_id": unique_id,
                    "key": json.dumps([stored_namespace, stored_key]),
                    "identity": json.dumps([stored_name, stored_sig])
                    if stored_name and stored_sig
                    else "",
                }
            )
        matches = []
        for field in ("key", "identity"):
            matches.extend(
                fixed_matches(
                    records, [field], [f"l.{field} = r.{field} AND l.{field} <> ''"]
                )
            )
        matches = [
            match
            for match in matches
            if "incoming" in (match["unique_id_l"], match["unique_id_r"])
        ]
        matched_entities = {
            entities[
                match["unique_id_r"]
                if match["unique_id_l"] == "incoming"
                else match["unique_id_l"]
            ]
            for match in matches
        }
        if len(matched_entities) > 1:
            raise ValueError("Ambiguous person match; reconcile person keys")
        evidence: dict[str, Any] = {
            "engine": "splink-5-duckdb",
            "model": "fixed_exact_v1",
            "matched_on": [],
        }
        if matched_entities:
            person_id = matched_entities.pop()
            # Prefer declared identity keys when both forms of evidence match.
            best = next(
                (match for match in matches if match.get("key_l") == key),
                max(matches, key=lambda match: match["match_probability"]),
            )
            evidence.update(
                matched_on=[
                    "person_key" if best.get("key_l") == key else "name_and_employment"
                ],
                matched_alias_ids=[
                    match["unique_id_r"]
                    if match["unique_id_l"] == "incoming"
                    else match["unique_id_l"]
                    for match in matches
                ],
                match_probability=float(best["match_probability"]),
                match_weight=float(best["match_weight"]),
            )
        else:
            person_id = str(uuid4())
            self.connection.execute(
                "INSERT INTO people VALUES (?, ?, ?)",
                [person_id, name or None, provenance_id],
            )
        self.connection.execute(
            "INSERT INTO person_keys VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                str(uuid4()),
                person_id,
                namespace,
                person_key,
                source,
                normalized or None,
                signature or None,
                json.dumps(evidence),
                provenance_id,
            ],
        )
        return Resolution(person_id, evidence)

    def evidence(self, person_id: str) -> list[dict[str, Any]]:
        """Return every observation and the evidence for its canonical assignment."""
        rows = self.connection.execute(
            "SELECT namespace, person_key, source, normalized_name, "
            "employment_signature, evidence_json, provenance_id "
            "FROM person_keys WHERE person_id = ? ORDER BY rowid",
            [person_id],
        ).fetchall()
        return [
            {
                "namespace": namespace,
                "person_key": key,
                "source": source,
                "normalized_name": name,
                "employment_signature": signature,
                "evidence": json.loads(evidence),
                "provenance_id": provenance,
            }
            for namespace, key, source, name, signature, evidence, provenance in rows
        ]
