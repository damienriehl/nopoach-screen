"""Private, reproducible validation packets around the existing backtest API."""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import duckdb

from nopoach.backtest.metadata import (
    input_identity,
    software_identity,
    workspace_input_identity,
)
from nopoach.backtest.run import CaseDefinition, load_cases, run_backtest
from nopoach.market.define import Market
from nopoach.store.workspace import Workspace

MEMBERS = (
    "request.json",
    "sources.json",
    "environment.json",
    "report.json",
    "report.md",
    "review.md",
)


@dataclass(frozen=True)
class SourceSupplement:
    receipt_id: str
    provider: str | None = None
    dataset: str | None = None
    version: str | None = None
    use_basis: str | None = None
    provenance_class: str | None = None
    event_date: str | None = None
    acquired_at: str | None = None
    availability_evidence: str | None = None


@dataclass(frozen=True)
class PacketResult:
    path: Path
    finalized: bool
    manifest_sha256: str | None
    reason: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    integrity: Literal["verified", "failed"]
    inputs: Literal["matched", "different", "unavailable", "not checked"]
    compatibility: Literal["matched", "different", "unavailable", "not checked"]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayResult:
    status: Literal["reproduced", "different", "unavailable", "failed"]
    packet: PacketResult | None
    differences: tuple[str, ...] = ()


def _dump(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, default=_json_value) + "\n"
    ).encode()


def _json_value(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _case(case: CaseDefinition) -> dict[str, Any]:
    value = asdict(case)
    value["disclosure_date"] = case.disclosure_date.isoformat()
    return value


def _receipt_inventory(
    workspace: Workspace, supplements: Sequence[SourceSupplement]
) -> dict[str, Any]:
    records = {record.id: record for record in workspace.provenance.records()}
    supplied: dict[str, SourceSupplement] = {}
    for item in supplements:
        if item.receipt_id in supplied:
            raise ValueError(f"Duplicate supplement for receipt {item.receipt_id}")
        if item.receipt_id not in records:
            raise ValueError(f"Unknown receipt {item.receipt_id}")
        supplied[item.receipt_id] = item
    classes: dict[str, set[str]] = {}
    if "imports" in workspace.table_names():
        for receipt, declared in workspace.connection.execute(
            "SELECT provenance_id, provenance_class FROM imports"
        ).fetchall():
            classes.setdefault(receipt, set()).add(declared)
    rows = []
    for identifier, record in sorted(records.items()):
        supplement = supplied.get(identifier)
        declared = sorted(classes.get(identifier, set()))
        conflict = bool(
            supplement
            and supplement.provenance_class
            and declared
            and supplement.provenance_class not in declared
        )
        rows.append(
            {
                "receipt_id": identifier,
                "stored": asdict(record),
                "stored_classes": declared,
                "supplement": asdict(supplement) if supplement else None,
                "supplement_status": "conflict"
                if conflict
                else ("supplied" if supplement else "unknown"),
                "synthetic": record.collection_method == "synthetic"
                or "synthetic" in declared,
            }
        )
    return {"version": 1, "receipts": rows}


def create_packet(
    workspace: Workspace,
    markets: Mapping[str, Market],
    *,
    cases: Sequence[CaseDefinition] | None = None,
    party_ids: Mapping[str, str] | None = None,
    supplements: Sequence[SourceSupplement] = (),
    evaluation_protocol: Mapping[str, Any] | None = None,
) -> PacketResult:
    """Run and capture a packet, finalizing the manifest only for stable inputs."""
    if not markets or len(markets) != len(set(markets)):
        raise ValueError("Packet requests require unique, non-empty case selections")
    selected_cases = tuple(load_cases() if cases is None else cases)
    catalog = {case.id: case for case in selected_cases}
    if set(markets) - set(catalog):
        raise ValueError("Unknown selected case")
    inventory = _receipt_inventory(workspace, supplements)
    before = input_identity(workspace.connection)
    if before.status != "available":
        raise ValueError(before.reason or "Input identity unavailable")
    software = software_identity()
    base = (workspace.root / "exports" / "validation").resolve()
    if not base.is_relative_to(workspace.root):
        raise ValueError("Validation packets must remain inside the workspace")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = base / str(uuid4())
    destination.mkdir(mode=0o700)
    request = {
        "version": 1,
        "cases": [_case(catalog[key]) for key in markets],
        "markets": [
            {"case_id": key, "market": json.loads(value.to_json())}
            for key, value in markets.items()
        ],
        "party_ids": dict(party_ids or {}),
        "evaluation_protocol": evaluation_protocol,
        "protocol_status": "supplied" if evaluation_protocol else "unknown",
    }
    try:
        report = run_backtest(workspace, markets, cases=cases, party_ids=party_ids)
        after = input_identity(workspace.connection)
        if after.sha256 != before.sha256:
            return PacketResult(
                destination, False, None, "Retained inputs changed during capture"
            )
        sources = {**inventory, "input_identity": asdict(before)}
        files = {
            "request.json": _dump(request),
            "sources.json": _dump(sources),
            "environment.json": _dump(software),
            "report.json": report.json_path.read_bytes(),
            "report.md": report.markdown_path.read_bytes(),
            "review.md": _review(request, sources, report.json_path),
        }
        for name, content in files.items():
            (destination / name).write_bytes(content)
        manifest = {
            "version": 1,
            "comparison_version": 1,
            "request_sha256": hashlib.sha256(files["request.json"]).hexdigest(),
            "members": {
                name: {
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for name, content in files.items()
            },
        }
        encoded = _dump(manifest)
        (destination / "manifest.json").write_bytes(encoded)
        return PacketResult(destination, True, hashlib.sha256(encoded).hexdigest())
    except BaseException as error:
        (destination / "INCOMPLETE.txt").write_text(
            f"Packet capture did not finalize: {type(error).__name__}\n",
            encoding="utf-8",
        )
        raise


def _review(request: dict[str, Any], sources: dict[str, Any], report: Path) -> bytes:
    receipts = sources["receipts"]
    unknown = sum(row["supplement_status"] == "unknown" for row in receipts)
    synthetic = sum(bool(row["synthetic"]) for row in receipts)
    protocol = request["protocol_status"]
    text = f"""# Validation packet review

This private packet identifies a software run; it does not establish historical
availability, empirical accuracy, legal clearance, or an enforcement lead.

- Evaluation protocol: {protocol}
- Retained receipts: {len(receipts)}
- Receipts without an operator supplement: {unknown}
- Synthetic receipts: {synthetic}
- Report source: {report.name}

Integrity, retained-input compatibility, and replay agreement are separate checks.
Unclear-collection aggregator data is restricted to historical method validation.
"""
    return text.encode()


def _load_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def verify_packet(
    packet: str | Path,
    *,
    workspace: str | Path | None = None,
    check_software: bool = False,
) -> VerificationResult:
    root = Path(packet)
    errors: list[str] = []
    expected = set(MEMBERS) | {"manifest.json"}
    try:
        if root.is_symlink() or not root.is_dir():
            raise ValueError("packet is not a regular directory")
        actual = {path.name for path in root.iterdir()}
        if actual != expected:
            raise ValueError("packet member inventory differs")
        manifest = _load_json(root / "manifest.json")
        if manifest.get("version") != 1 or manifest.get("comparison_version") != 1:
            raise ValueError("unsupported manifest version")
        if set(manifest.get("members", {})) != set(MEMBERS):
            raise ValueError("manifest member inventory differs")
        for name in MEMBERS:
            path = root / name
            if (
                path.is_symlink()
                or not path.is_file()
                or path.resolve().parent != root.resolve()
            ):
                raise ValueError(f"unsafe packet member: {name}")
            body = path.read_bytes()
            item = manifest["members"][name]
            if (
                len(body) != item["bytes"]
                or hashlib.sha256(body).hexdigest() != item["sha256"]
            ):
                raise ValueError(f"changed packet member: {name}")
        _load_json(root / "request.json")
        sources = _load_json(root / "sources.json")
        environment = _load_json(root / "environment.json")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return VerificationResult("failed", "not checked", "not checked", (str(error),))
    inputs: Literal["matched", "different", "unavailable", "not checked"] = (
        "not checked"
    )
    if workspace is not None:
        try:
            current = workspace_input_identity(workspace)
            captured = sources["input_identity"]
            inputs = (
                "matched" if current.sha256 == captured.get("sha256") else "different"
            )
            if current.status != "available":
                inputs = "unavailable"
        except (OSError, ValueError, duckdb.Error) as error:
            errors.append(str(error))
            inputs = "unavailable"
    compatibility: Literal["matched", "different", "unavailable", "not checked"] = (
        "not checked"
    )
    if check_software:
        try:
            compatibility = (
                "matched"
                if software_identity()["compatibility_sha256"]
                == environment["compatibility_sha256"]
                else "different"
            )
        except (KeyError, OSError):
            compatibility = "unavailable"
    return VerificationResult("verified", inputs, compatibility, tuple(errors))


def replay_packet(packet: str | Path, workspace: Workspace) -> ReplayResult:
    verification = verify_packet(packet, check_software=True)
    try:
        sources = _load_json(Path(packet) / "sources.json")
        inputs_match = (
            input_identity(workspace.connection).sha256
            == sources["input_identity"]["sha256"]
        )
    except (OSError, ValueError, KeyError, duckdb.Error):
        inputs_match = False
    if (
        verification.integrity != "verified"
        or verification.compatibility != "matched"
        or not inputs_match
    ):
        return ReplayResult("unavailable", None, verification.errors)
    try:
        request = _load_json(Path(packet) / "request.json")
        cases = tuple(_case_from_json(item) for item in request["cases"])
        markets = {
            item["case_id"]: Market.from_json(json.dumps(item["market"]))
            for item in request["markets"]
        }
        created = create_packet(
            workspace,
            markets,
            cases=cases,
            party_ids=request.get("party_ids", {}),
            evaluation_protocol=request.get("evaluation_protocol"),
        )
        old = _load_json(Path(packet) / "report.json")
        new = _load_json(created.path / "report.json")
        differences = compare_reports(old, new)
        return ReplayResult(
            "reproduced" if not differences else "different", created, differences
        )
    except BaseException as error:
        return ReplayResult("failed", None, (f"{type(error).__name__}: {error}",))


def _case_from_json(value: dict[str, Any]) -> CaseDefinition:
    return CaseDefinition(
        **{
            **value,
            "parties": tuple(value["parties"]),
            "known_pairs": tuple(tuple(pair) for pair in value["known_pairs"]),
            "sources": tuple(value["sources"]),
            "disclosure_date": date.fromisoformat(value["disclosure_date"]),
        }
    )


def compare_reports(old: Any, new: Any) -> tuple[str, ...]:
    """Compare substantive report fields, ignoring generated screen identifiers."""
    differences: list[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if path.endswith(".screen_id"):
            return
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                differences.append(f"{path}: fields differ")
                return
            for key in sorted(a):
                walk(a[key], b[key], f"{path}.{key}")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                differences.append(f"{path}: length differs")
                return
            for index, (left, right) in enumerate(zip(a, b, strict=True)):
                walk(left, right, f"{path}[{index}]")
        elif (
            isinstance(a, float)
            and isinstance(b, float)
            and math.isfinite(a)
            and math.isfinite(b)
        ):
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
                differences.append(f"{path}: value differs")
        elif a != b:
            differences.append(f"{path}: value differs")

    walk(old, new, "$.")
    return tuple(differences)
