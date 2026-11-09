"""Immutable local analytical uploads and frozen screen selections.

Domain importers validate their content before publication. Original bytes and
declarations live outside the application, with receipt and content hashes.
This detects accidental changes, not rewriting by the workspace's OS owner.
"""

import json
import os
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from nopoach.store.workspace import Workspace

BundleKind = Literal["j2j", "contracts", "flow_population"]
KINDS = {"j2j", "contracts", "flow_population"}
MAX_RAW_BYTES = 64 * 1024 * 1024
MAX_NORMALIZED_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


@dataclass(frozen=True)
class BundleRef:
    kind: BundleKind
    bundle_id: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError("Unsupported analytical input kind")
        if not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (self.bundle_id, self.manifest_sha256)
        ):
            raise ValueError("Invalid analytical bundle identifier or digest")


@dataclass(frozen=True)
class ClusterSettings:
    pre_start_year: int
    pre_end_year: int
    groups: int = 2
    seed: int = 0
    restarts: int = 4
    sweeps: int = 20
    fitting_sweeps: int = 200
    candidate_refits: int = 4096
    tolerance: float = 1e-6
    max_employers: int = 64

    def __post_init__(self) -> None:
        integer_settings = (
            self.pre_start_year,
            self.pre_end_year,
            self.groups,
            self.seed,
            self.restarts,
            self.sweeps,
            self.fitting_sweeps,
            self.candidate_refits,
            self.max_employers,
        )
        if (
            any(type(value) is not int for value in integer_settings)
            or not 1 <= self.pre_start_year <= self.pre_end_year <= 9999
            or not 1 <= self.groups <= 4
            or not 0 <= self.seed < 2**32
            or not 1 <= self.restarts <= 4
            or not 1 <= self.sweeps <= 20
            or not 1 <= self.fitting_sweeps <= 200
            or not 1 <= self.candidate_refits <= 4096
            or not 1 <= self.max_employers <= 64
            or not isfinite(self.tolerance)
            or not 0 < self.tolerance <= 0.01
        ):
            raise ValueError("Invalid or unsupported cluster settings")


@dataclass(frozen=True)
class AnalyticalInputs:
    j2j: BundleRef | None = None
    contracts: BundleRef | None = None
    flow_population: BundleRef | None = None
    cluster_settings: ClusterSettings | None = None

    def __post_init__(self) -> None:
        for kind in KINDS:
            ref = getattr(self, kind)
            if ref is not None and ref.kind != kind:
                raise ValueError("Analytical selection and bundle kind disagree")

    def to_json(self) -> str:
        return _json({"version": 1, **asdict(self)})

    @classmethod
    def from_json(cls, value: str) -> "AnalyticalInputs":
        data = json.loads(value)
        if not isinstance(data, dict) or data.get("version", 1) != 1:
            raise ValueError("Unsupported analytical selection version")
        if set(data) - {*KINDS, "version", "cluster_settings"}:
            raise ValueError("Unknown analytical selection field")
        try:
            return cls(
                **{
                    kind: BundleRef(**data[kind]) if data.get(kind) else None
                    for kind in KINDS
                },
                cluster_settings=ClusterSettings(**data["cluster_settings"])
                if data.get("cluster_settings")
                else None,
            )
        except TypeError as exc:
            raise ValueError("Invalid analytical selection") from exc


@dataclass(frozen=True)
class AnalyticalBundle:
    reference: BundleRef
    body: bytes
    normalized_text: str | None
    metadata_json: str
    provenance_id: str
    raw_sha256: str
    normalized_sha256: str | None
    transformation: str | None

    @property
    def metadata(self) -> dict[str, Any]:
        """Return a copy so caller mutation cannot change frozen declarations."""
        return cast(dict[str, Any], json.loads(self.metadata_json))


def _contained(workspace: Workspace, *parts: str) -> Path:
    path = workspace.root
    for part in parts:
        if part in {"", ".", ".."} or Path(part).name != part:
            raise ValueError("Invalid analytical storage component")
        path = path / part
        if path.is_symlink():
            raise ValueError("Analytical storage cannot contain a symlink")
    if not path.resolve().is_relative_to(workspace.root.resolve()):
        raise ValueError("Analytical storage must remain inside its workspace")
    return path


def _read(path: Path, limit: int) -> bytes:
    if path.is_symlink():
        raise ValueError("Analytical storage cannot contain a symlink")
    try:
        with path.open("rb") as stream:
            body = stream.read(limit + 1)
    except OSError as exc:
        raise ValueError("Analytical input is missing or unreadable") from exc
    if len(body) > limit:
        raise ValueError("Analytical input exceeds its byte limit")
    return body


def _write(path: Path, body: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: manifest[key]
        for key in (
            "version",
            "kind",
            "raw_sha256",
            "normalized_sha256",
            "metadata",
            "transformation",
        )
    }


def publish_bundle(
    workspace: Workspace,
    *,
    kind: BundleKind,
    body: bytes,
    metadata: dict[str, Any],
    normalized_text: str | None = None,
    transformation: str | None = None,
) -> BundleRef:
    """Publish a domain-validated upload atomically; identical input reuses its ID.

    Interrupted staging directories remain unselectable. No existing content is
    overwritten or deleted. Source declarations are immutable JSON data.
    """
    if kind not in KINDS:
        raise ValueError("Unsupported analytical input kind")
    if not isinstance(body, bytes) or len(body) > MAX_RAW_BYTES:
        raise ValueError("Analytical input requires bytes within the raw byte limit")
    normalized = (
        normalized_text.encode("utf-8") if normalized_text is not None else None
    )
    if normalized is not None and (
        len(normalized) > MAX_NORMALIZED_BYTES or not transformation
    ):
        raise ValueError(
            "Normalized text needs a transformation and must fit its byte limit"
        )
    if normalized is None and transformation is not None:
        raise ValueError("A transformation requires normalized text")
    if not isinstance(metadata, dict):
        raise ValueError("Analytical source declarations must be an object")
    manifest = {
        "version": 1,
        "kind": kind,
        "raw_sha256": _digest(body),
        "normalized_sha256": _digest(normalized) if normalized is not None else None,
        "metadata": json.loads(_json(metadata)),
        "transformation": transformation,
    }
    identity = _json(manifest).encode()
    if len(identity) > MAX_MANIFEST_BYTES - 512:
        raise ValueError("Analytical declarations exceed the manifest byte limit")
    identifier = _digest(identity)
    folder = _contained(workspace, "analysis", "inputs")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = _contained(workspace, "analysis", "inputs", identifier)
    if destination.exists():
        ref = _reference(workspace, identifier)
        read_bundle(workspace, ref)
        return ref
    staging = _contained(workspace, "analysis", "inputs", f".pending-{uuid4().hex}")
    staging.mkdir(mode=0o700)
    _write(staging / "raw.bin", body)
    if normalized is not None:
        _write(staging / "normalized.txt", normalized)
    receipt = workspace.provenance.append(
        source=f"analytical_{kind}",
        url=f"file:analysis/inputs/{identifier}",
        method="IMPORT",
        body=body,
        collection_method="agency_import",
    )
    manifest.update(bundle_id=identifier, provenance_id=receipt.id)
    encoded = _json(manifest).encode()
    _write(staging / "manifest.json", encoded)
    os.rename(staging, destination)
    return BundleRef(kind, identifier, _digest(encoded))


def _reference(workspace: Workspace, identifier: str) -> BundleRef:
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise ValueError("Invalid analytical bundle identifier")
    encoded = _read(
        _contained(workspace, "analysis", "inputs", identifier, "manifest.json"),
        MAX_MANIFEST_BYTES,
    )
    try:
        manifest = json.loads(encoded)
        return BundleRef(manifest["kind"], identifier, _digest(encoded))
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Analytical manifest integrity check failed") from exc


def read_bundle(workspace: Workspace, ref: BundleRef) -> AnalyticalBundle:
    """Verify the pinned manifest, raw bytes, normalized text and provenance."""
    encoded = _read(
        _contained(workspace, "analysis", "inputs", ref.bundle_id, "manifest.json"),
        MAX_MANIFEST_BYTES,
    )
    if _digest(encoded) != ref.manifest_sha256:
        raise ValueError("Analytical manifest integrity check failed")
    try:
        manifest = json.loads(encoded)
        if manifest["version"] != 1:
            raise ValueError("Unsupported analytical bundle version")
        if (
            manifest["kind"] != ref.kind
            or manifest["bundle_id"] != ref.bundle_id
            or _digest(_json(_identity(manifest)).encode()) != ref.bundle_id
        ):
            raise ValueError("Analytical manifest integrity check failed")
        body = _read(
            _contained(workspace, "analysis", "inputs", ref.bundle_id, "raw.bin"),
            MAX_RAW_BYTES,
        )
        normalized = None
        if manifest["normalized_sha256"] is not None:
            normalized = _read(
                _contained(
                    workspace, "analysis", "inputs", ref.bundle_id, "normalized.txt"
                ),
                MAX_NORMALIZED_BYTES,
            )
        if _digest(body) != manifest["raw_sha256"] or (
            normalized is not None
            and _digest(normalized) != manifest["normalized_sha256"]
        ):
            raise ValueError("Analytical content integrity check failed")
        receipt = next(
            (
                r
                for r in workspace.provenance.records()
                if r.id == manifest["provenance_id"]
            ),
            None,
        )
        if receipt is None or receipt.sha256 != manifest["raw_sha256"]:
            raise ValueError("Analytical provenance integrity check failed")
        return AnalyticalBundle(
            ref,
            body,
            normalized.decode("utf-8") if normalized is not None else None,
            _json(manifest["metadata"]),
            manifest["provenance_id"],
            manifest["raw_sha256"],
            manifest["normalized_sha256"],
            manifest["transformation"],
        )
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Analytical manifest integrity check failed") from exc


def list_bundles(
    workspace: Workspace, *, kind: BundleKind | None = None
) -> tuple[BundleRef, ...]:
    """List verified publications strictly, rejecting any inventory integrity error."""
    references, issues = inspect_bundles(workspace, kind=kind)
    if issues:
        raise ValueError("; ".join(issues))
    return references


def inspect_bundles(
    workspace: Workspace, *, kind: BundleKind | None = None
) -> tuple[tuple[BundleRef, ...], tuple[str, ...]]:
    """Return healthy references and visible per-version errors for recovery UIs."""
    if kind is not None and kind not in KINDS:
        raise ValueError("Unsupported analytical input kind")
    folder = _contained(workspace, "analysis", "inputs")
    if not folder.exists():
        return (), ()
    result = []
    issues = []
    for path in sorted(folder.iterdir()):
        if not re.fullmatch(r"[0-9a-f]{64}", path.name):
            continue
        try:
            ref = _reference(workspace, path.name)
            read_bundle(workspace, ref)
        except (ValueError, OSError) as exc:
            issues.append(f"Input version {path.name} integrity check failed: {exc}")
            continue
        if kind is None or ref.kind == kind:
            result.append(ref)
    return tuple(result), tuple(issues)
