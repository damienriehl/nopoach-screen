"""Local, bounded review-corpus imports; worker identifiers stay in the workspace."""

import csv
import io
import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal, cast

from nopoach.analytical_inputs import BundleRef, publish_bundle, read_bundle
from nopoach.store.workspace import Workspace

Review = Literal["positive", "negative", "uncertain", "unreviewed"]
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_FIELD_CHARS = 65536
COLUMNS = (
    "document_id",
    "employer_id",
    "text",
    "document_date",
    "scope",
    "worker_key",
    "effective_from",
    "effective_to",
    "documents_complete",
    "review",
)
TRANSFORMATION = "utf8-newlines-contract-documents-v1"


@dataclass(frozen=True)
class ContractRecord:
    document_id: str
    employer_id: str
    text: str
    document_date: date | None
    scope: str
    worker_key: str
    effective_from: date | None
    effective_to: date | None
    ongoing: bool
    documents_complete: bool
    review: Review


@dataclass(frozen=True)
class ContractCorpus:
    source: str
    population_description: str
    sampling_basis: str
    scope: str
    declared_workers: tuple[tuple[str, int], ...]
    records: tuple[ContractRecord, ...]
    reviewed_on: date | None = None
    provenance_id: str = ""
    bundle_id: str = ""


def _date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_contracts(body: bytes, metadata: dict[str, Any]) -> ContractCorpus:
    """Parse a declared review roster without treating a text miss as a negative.

    ``documents_complete`` affirms review of the worker's entire applicable
    document set, not merely that this excerpt was read. An empty effective end
    is unknown; ``ongoing`` explicitly declares an open-ended interval.
    """
    if len(body) > MAX_BYTES:
        raise ValueError("Contract CSV exceeds the 8 MiB byte limit")
    declarations = ("source", "population_description", "sampling_basis", "scope")
    if any(
        not isinstance(metadata.get(k), str) or not metadata[k].strip()
        for k in declarations
    ):
        raise ValueError(
            "Contract review needs source, population, sampling basis and scope"
        )
    counts = metadata.get("declared_workers", {})
    if not isinstance(counts, dict) or any(
        not isinstance(k, str) or not k.strip() or type(v) is not int or v < 1
        for k, v in counts.items()
    ):
        raise ValueError("Declared worker counts must be positive integers by employer")
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig"), newline=""))
        if (
            reader.fieldnames is None
            or len(reader.fieldnames) != len(set(reader.fieldnames))
            or set(reader.fieldnames) != set(COLUMNS)
        ):
            raise ValueError("Contract CSV requires the documented, unique columns")
        records = []
        seen: set[ContractRecord] = set()
        for number, row in enumerate(reader, 1):
            if number > MAX_ROWS:
                raise ValueError("Contract CSV exceeds the 10000-row limit")
            if None in row or any(
                v is None or len(v) > MAX_FIELD_CHARS for v in row.values()
            ):
                raise ValueError(
                    "Contract CSV has missing fields or exceeds its field limit"
                )
            values = {
                k: v.strip()
                if k != "text"
                else v.replace("\r\n", "\n").replace("\r", "\n")
                for k, v in row.items()
            }
            if (
                not values["document_id"]
                or not values["employer_id"]
                or not values["text"]
            ):
                raise ValueError(
                    "Every contract needs a document ID, employer ID and text"
                )
            review = values["review"] or "unreviewed"
            complete = values["documents_complete"].lower()
            if review not in {
                "positive",
                "negative",
                "uncertain",
                "unreviewed",
            } or complete not in {"true", "false"}:
                raise ValueError(
                    "Use an explicit review classification and true/false completeness"
                )
            record = ContractRecord(
                values["document_id"],
                values["employer_id"],
                values["text"],
                _date(values["document_date"]),
                values["scope"],
                values["worker_key"],
                _date(values["effective_from"]),
                _date(values["effective_to"])
                if values["effective_to"] != "ongoing"
                else None,
                values["effective_to"] == "ongoing",
                complete == "true",
                cast(Review, review),
            )
            if (
                record.effective_from
                and record.effective_to
                and record.effective_from > record.effective_to
            ):
                raise ValueError("Contract effective interval is reversed")
            if record not in seen:
                records.append(record)
                seen.add(record)
        reviewed_on = _date(str(metadata.get("reviewed_on") or ""))
    except (UnicodeError, csv.Error, TypeError) as exc:
        raise ValueError("Invalid UTF-8 contract review CSV or declarations") from exc
    return ContractCorpus(
        metadata["source"].strip(),
        metadata["population_description"].strip(),
        metadata["sampling_basis"].strip(),
        metadata["scope"].strip(),
        tuple(sorted(counts.items())),
        tuple(records),
        reviewed_on,
    )


def _normalized(corpus: ContractCorpus) -> str:
    return json.dumps(
        [
            {"employer_id": r.employer_id, "document_id": r.document_id, "text": r.text}
            for r in corpus.records
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def import_contracts(
    workspace: Workspace, body: bytes, metadata: dict[str, Any]
) -> BundleRef:
    """Validate before publishing; failed uploads cannot replace a valid bundle."""
    corpus = parse_contracts(body, metadata)
    return publish_bundle(
        workspace,
        kind="contracts",
        body=body,
        metadata=metadata,
        normalized_text=_normalized(corpus),
        transformation=TRANSFORMATION,
    )


def load_contracts(workspace: Workspace, reference: BundleRef) -> ContractCorpus:
    if reference.kind != "contracts":
        raise ValueError("Select a contract-review bundle")
    bundle = read_bundle(workspace, reference)
    corpus = parse_contracts(bundle.body, bundle.metadata)
    if bundle.transformation != TRANSFORMATION or bundle.normalized_text != _normalized(
        corpus
    ):
        raise ValueError("Contract normalization integrity check failed")
    return replace(
        corpus, provenance_id=bundle.provenance_id, bundle_id=reference.bundle_id
    )
