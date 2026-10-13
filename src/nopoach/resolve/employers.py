"""Conservative, fixed-weight Splink 5 linkage on DuckDB with retained evidence."""

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import duckdb
from splink import DuckDBAPI, Linker, SettingsCreator
from splink import blocking_rule_library as br
from splink import comparison_library as cl

from nopoach.imports.schema import initialize_import_schema
from nopoach.store.workspace import Workspace


@dataclass(frozen=True)
class Resolution:
    entity_id: str
    evidence: dict[str, Any]


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", text))


def normalize_employer(name: str) -> str:
    words = normalize_name(name).split()
    suffixes = {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "studio",
        "studios",
        "animation",
    }
    while len(words) > 1 and words[-1] in suffixes:
        words.pop()
    return " ".join(words)


def normalize_id(value: str, kind: str) -> str:
    if kind in {"cik", "crd"} and value.isdigit():
        return str(int(value))
    if kind == "ein":
        return value.replace("-", "")
    return value.strip()


def fixed_matches(
    records: Sequence[dict[str, str]], fields: Sequence[str], blocks: Sequence[str]
) -> list[dict[str, Any]]:
    """Run a small deterministic Splink model; no training or network/extension use.

    An exact field match uses m=.999 and u=.0001, with prior=.1 and threshold=.99.
    Probabilities are fixed model scores, not empirically calibrated confidence.
    Callers provide only eligible candidates: conflicting employer IDs and weak
    person evidence never enter the model. All resulting weights are retained.
    """
    if len(records) < 2:
        return []
    connection = duckdb.connect(
        config={
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        }
    )
    try:
        backend = DuckDBAPI(connection)
        frame = backend.register(list(records))
        settings = SettingsCreator(
            link_type="dedupe_only",
            comparisons=[
                cl.ExactMatch(field).configure(
                    m_probabilities=[0.999, 0.001], u_probabilities=[0.0001, 0.9999]
                )
                for field in fields
            ],
            blocking_rules_to_generate_predictions=[
                br.CustomRule(sql) for sql in blocks
            ],
            probability_two_random_records_match=0.1,
        )
        linker = Linker(frame, settings, log_level=40)
        result: list[dict[str, Any]] = linker.inference.predict(
            threshold_match_probability=0.99
        ).as_record_list()
        return result
    finally:
        connection.close()


class EmployerResolver:
    def __init__(self, workspace: Workspace) -> None:
        self.connection = workspace.connection
        initialize_import_schema(self.connection)

    def resolve(
        self,
        name: str,
        *,
        provenance_id: str,
        employer_id: str | None = None,
        employer_id_type: str | None = None,
    ) -> Resolution:
        if not name.strip():
            raise ValueError("employer_name is required")
        if employer_id and not employer_id_type:
            raise ValueError("employer_id_type is required for an employer ID")
        normalized = normalize_employer(name)
        kind = employer_id_type.casefold() if employer_id_type else None
        external_id = normalize_id(employer_id, kind or "") if employer_id else None
        aliases = self.connection.execute(
            "SELECT id, employer_id, normalized_name, external_id, external_id_type "
            "FROM employer_aliases ORDER BY id"
        ).fetchall()
        # Existing collectors may have written U1 employers directly.
        existing = self.connection.execute(
            "SELECT id, name, cik FROM employers ORDER BY id"
        ).fetchall()
        aliases += [
            (
                "employer:" + entity,
                entity,
                normalize_employer(label),
                normalize_id(cik, "cik") if cik else None,
                "cik" if cik else None,
            )
            for entity, label, cik in existing
        ]
        conflicting = {
            entity
            for _, entity, _, value, id_type in aliases
            if external_id and id_type == kind and value and value != external_id
        }
        records = [
            {
                "unique_id": "incoming",
                "name_key": normalized,
                "id_key": f"{kind}:{external_id}" if external_id else "",
            }
        ]
        entities: dict[str, str] = {}
        for alias_id, entity, name_key, value, id_type in aliases:
            if entity in conflicting:
                continue
            unique_id = "candidate:" + alias_id
            entities[unique_id] = entity
            records.append(
                {
                    "unique_id": unique_id,
                    "name_key": name_key,
                    "id_key": f"{id_type}:{value}" if value else "",
                }
            )
        # A matching identifier or normalized name is independently sufficient.
        matches = []
        for field in ("id_key", "name_key"):
            matches.extend(
                fixed_matches(
                    records, [field], [f"l.{field} = r.{field} AND l.{field} <> ''"]
                )
            )
        incoming = [
            match
            for match in matches
            if "incoming" in (match["unique_id_l"], match["unique_id_r"])
        ]
        id_matches = [match for match in incoming if match.get("id_key_l")]
        if id_matches:
            incoming = id_matches
        candidates = {
            entities[
                match["unique_id_r"]
                if match["unique_id_l"] == "incoming"
                else match["unique_id_l"]
            ]
            for match in incoming
        }
        # Multiple distinct existing entities are not silently collapsed.
        if len(candidates) > 1:
            raise ValueError("Ambiguous employer match; supply an employer ID")
        evidence: dict[str, Any] = {
            "engine": "splink-5-duckdb",
            "model": "fixed_exact_v1",
            "normalized_name": normalized,
            "matched_on": [],
        }
        if candidates:
            entity_id = candidates.pop()
            best = max(incoming, key=lambda match: match["match_probability"])
            evidence.update(
                match_probability=float(best["match_probability"]),
                match_weight=float(best["match_weight"]),
                matched_alias_ids=[
                    match["unique_id_r"]
                    if match["unique_id_l"] == "incoming"
                    else match["unique_id_l"]
                    for match in incoming
                ],
            )
            evidence["matched_on"] = [
                field
                for field in ("id_key", "name_key")
                if best.get(field + "_l")
                and best.get(field + "_l") == best.get(field + "_r")
            ]
        else:
            entity_id = str(uuid4())
            self.connection.execute(
                "INSERT INTO employers VALUES (?, ?, ?, ?)",
                [
                    entity_id,
                    name,
                    external_id if kind == "cik" else None,
                    provenance_id,
                ],
            )
        self.connection.execute(
            "INSERT INTO employer_aliases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                str(uuid4()),
                entity_id,
                name,
                normalized,
                external_id,
                kind,
                json.dumps(evidence),
                provenance_id,
            ],
        )
        return Resolution(entity_id, evidence)

    def evidence(self, employer_id: str) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "external_id": value,
                "external_id_type": kind,
                "evidence": json.loads(evidence),
                "provenance_id": provenance,
            }
            for name, value, kind, evidence, provenance in self.connection.execute(
                "SELECT name, external_id, external_id_type, evidence_json, "
                "provenance_id FROM employer_aliases WHERE employer_id = ?",
                [employer_id],
            ).fetchall()
        ]
