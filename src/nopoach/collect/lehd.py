"""Offline Census J2JOD inputs for one explicitly declared metro tabulation.

Schema and flags: https://lehd.ces.census.gov/data/schema/j2j_latest/
The supported schema is pinned to V4.14.0; no runtime downloads occur.
Public counts describe regional mobility, never employer-pair opportunities.
"""

import csv
import gzip
import io
import re
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from typing import Any

from nopoach.analytical_inputs import BundleRef, publish_bundle
from nopoach.store.workspace import Workspace

SCHEMA_VERSION = "V4.14.0"
PARSER_VERSION = "j2jod-metro-v1"
TOTAL_DIMENSIONS = {
    "periodicity": "Q",
    "seasonadj": "U",
    "geo_level": "B",
    "geo_level_orig": "B",
    "ind_level": "A",
    "industry": "00",
    "ind_level_orig": "A",
    "industry_orig": "00",
    "sex": "0",
    "agegrp": "A00",
    "race": "A0",
    "ethnicity": "A0",
    "education": "E0",
    "firmage": "0",
    "firmsize": "0",
    "firmage_orig": "0",
    "firmsize_orig": "0",
    "agg_level": "592897",
}
DIMENSIONS = (
    *TOTAL_DIMENSIONS,
    "geography",
    "geography_orig",
    "ownercode",
    "ownercode_orig",
)


@dataclass(frozen=True)
class ParserLimits:
    max_raw_bytes: int = 64 * 1024 * 1024
    max_decompressed_bytes: int = 64 * 1024 * 1024
    max_rows: int = 1_000_000
    max_field_bytes: int = 65_536
    max_retained_cells: int = 4_000

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in asdict(self).values()):
            raise ValueError("Parser limits must be positive integers")
        if self.max_field_bytes > 65_536:
            raise ValueError("Field limit cannot exceed 65536 bytes")


@dataclass(frozen=True)
class J2JMetadata:
    source: str
    schema_version: str
    release: str
    release_date: date
    geography: str
    ownercode: str
    ownercode_orig: str
    availability_start: tuple[int, int]
    availability_end: tuple[int, int]


@dataclass(frozen=True)
class Indicator:
    raw_value: str
    status: str
    value: float | None
    reason: str | None


@dataclass(frozen=True)
class J2JRecord:
    year: int
    quarter: int
    dimension_values: tuple[tuple[str, str], ...]
    ee: Indicator
    aqhire: Indicator

    @property
    def dimensions(self) -> dict[str, str]:
        return dict(self.dimension_values)


@dataclass(frozen=True)
class J2JData:
    metadata: J2JMetadata
    records: tuple[J2JRecord, ...]
    excluded_rows: int
    duplicate_rows: int
    parser_version: str = PARSER_VERSION


def _metadata(values: Mapping[str, Any]) -> J2JMetadata:
    if not isinstance(values, Mapping):
        raise ValueError("J2J metadata must be an object")
    try:
        if values["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported J2J schema version; require V4.14.0")
        source, release, geo = (values[k] for k in ("source", "release", "geography"))
        if (
            not isinstance(source, str)
            or not source.strip()
            or not isinstance(release, str)
            or not re.fullmatch(r"R?\d{4}Q[1-4]", release)
            or not isinstance(geo, str)
            or not re.fullmatch(r"\d{5}", geo)
        ):
            raise ValueError("Declare one J2J source, release and five-digit CBSA")
        owners = values["ownercode"], values["ownercode_orig"]
        if any(owner not in {"A00", "A05"} for owner in owners):
            raise ValueError("Unsupported J2J ownership tabulation")
        availability = values["availability"]
        if any(
            availability[k] != value
            for k, value in {
                "geo_level": "B",
                "geo_level_orig": "B",
                "geography": geo,
                "geography_orig": geo,
            }.items()
        ):
            raise ValueError(
                "Pair availability must describe the selected same-CBSA cell"
            )
        start = availability["start_year"], availability["start_quarter"]
        end = availability["end_year"], availability["end_quarter"]
        if any(type(v) is not int for v in (*start, *end)) or not (
            1 <= start[0] <= end[0] <= 9999
            and 1 <= start[1] <= 4
            and 1 <= end[1] <= 4
            and start <= end
        ):
            raise ValueError("Invalid J2J pair availability window")
        return J2JMetadata(
            source.strip(),
            SCHEMA_VERSION,
            release,
            date.fromisoformat(values["release_date"]),
            geo,
            *owners,
            start,
            end,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            "Incomplete or invalid J2J source and availability metadata"
        ) from exc


def _indicator(raw: str, status: str) -> Indicator:
    if status != "1":
        reason = {
            "5": "suppressed",
            "11": "suppressed",
            "-1": "unavailable",
            "-2": "unavailable",
            "6": "derived status",
            "7": "distorted",
            "9": "distorted",
            "10": "aggregate status",
            "12": "distorted",
        }.get(status, "unknown status")
        return Indicator(raw, status, None, reason)
    if not raw:
        return Indicator(raw, status, None, "absent")
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not isfinite(value) or value < 0:
        return Indicator(raw, status, None, "invalid")
    return Indicator(raw, status, value, None)


def _lines(stream: io.BytesIO | gzip.GzipFile, limits: ParserLimits) -> Iterator[str]:
    consumed = 0
    while line := stream.readline(limits.max_decompressed_bytes + 1):
        consumed += len(line)
        if consumed > limits.max_decompressed_bytes:
            raise ValueError("J2J decompressed-byte limit exceeded")
        yield line.decode("utf-8-sig" if consumed == len(line) else "utf-8")


DEFAULT_LIMITS = ParserLimits()


def parse_j2j(
    body: bytes, metadata: Mapping[str, Any], *, limits: ParserLimits = DEFAULT_LIMITS
) -> J2JData:
    """Read CSV or gzip, retaining one nonoverlapping same-metro total series.

    Other geographic pairs are counted as excluded. Unsupported strata in the
    target pair are rejected, rather than added to a total. Exact repeats count
    once; conflicting cells invalidate the upload. All original bytes remain in
    the immutable bundle when imported.
    """
    declaration = _metadata(metadata)
    if metadata.get("parser_version", PARSER_VERSION) != PARSER_VERSION:
        raise ValueError("Unsupported J2J parser version")
    if metadata.get("parser_settings", asdict(limits)) != asdict(limits):
        raise ValueError("J2J parser settings differ from the retained input version")
    if not isinstance(body, bytes) or len(body) > limits.max_raw_bytes:
        raise ValueError("J2J raw-byte limit exceeded")
    if body.startswith(b"PK"):
        raise ValueError("J2J input must be CSV or gzip, not a ZIP archive")
    stream: io.BytesIO | gzip.GzipFile = (
        gzip.GzipFile(fileobj=io.BytesIO(body))
        if body.startswith(b"\x1f\x8b")
        else io.BytesIO(body)
    )
    records: dict[tuple[int, int], J2JRecord] = {}
    excluded = duplicate = 0
    expected = {
        **TOTAL_DIMENSIONS,
        "geography": declaration.geography,
        "geography_orig": declaration.geography,
        "ownercode": declaration.ownercode,
        "ownercode_orig": declaration.ownercode_orig,
    }
    try:
        with stream:
            reader = csv.DictReader(_lines(stream, limits), strict=True)
            header = reader.fieldnames
            required = {
                *DIMENSIONS,
                "year",
                "quarter",
                "EE",
                "AQHire",
                "sEE",
                "sAQHire",
            }
            if (
                not header
                or len(header) != len(set(header))
                or not required <= set(header)
            ):
                raise ValueError(
                    "J2J CSV requires unique schema dimensions "
                    "and EE/AQHire status columns"
                )
            if any(len(field.encode()) > limits.max_field_bytes for field in header):
                raise ValueError("J2J field-size limit exceeded")
            for number, row in enumerate(reader, 1):
                if number > limits.max_rows:
                    raise ValueError("J2J row limit exceeded")
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("J2J row does not match its CSV header")
                if any(
                    len(value.encode()) > limits.max_field_bytes
                    for value in row.values()
                ):
                    raise ValueError("J2J field-size limit exceeded")
                if any(
                    row.get(k) != declaration.release for k in ("release",) if k in row
                ):
                    raise ValueError("Mixed J2J releases are unsupported")
                if any(
                    row[k] != value
                    for k, value in {
                        "geo_level": "B",
                        "geo_level_orig": "B",
                        "geography": declaration.geography,
                        "geography_orig": declaration.geography,
                    }.items()
                ):
                    excluded += 1
                    continue
                mismatches = [k for k, value in expected.items() if row[k] != value]
                if mismatches:
                    raise ValueError(
                        "Unsupported or overlapping J2J tabulation: "
                        + ", ".join(mismatches)
                    )
                year, quarter = int(row["year"]), int(row["quarter"])
                if not 1 <= year <= 9999 or not 1 <= quarter <= 4:
                    raise ValueError("Invalid J2J year or quarter")
                record = J2JRecord(
                    year,
                    quarter,
                    tuple((k, row[k]) for k in DIMENSIONS),
                    _indicator(row["EE"], row["sEE"].strip()),
                    _indicator(row["AQHire"], row["sAQHire"].strip()),
                )
                key = year, quarter
                if key in records:
                    if records[key] != record:
                        raise ValueError("J2J conflicting duplicate quarter")
                    duplicate += 1
                else:
                    records[key] = record
                    if len(records) > limits.max_retained_cells:
                        raise ValueError("J2J retained-cell limit exceeded")
    except (csv.Error, UnicodeError, OSError, EOFError, zlib.error) as exc:
        raise ValueError(f"Invalid or oversized J2J CSV/gzip input: {exc}") from exc
    return J2JData(
        declaration, tuple(records[k] for k in sorted(records)), excluded, duplicate
    )


def import_j2j(
    workspace: Workspace, body: bytes, metadata: dict[str, Any]
) -> BundleRef:
    """Validate before publishing; a failed import never replaces a prior bundle."""
    limits = ParserLimits()
    parse_j2j(body, metadata, limits=limits)
    return publish_bundle(
        workspace,
        kind="j2j",
        body=body,
        metadata={
            **metadata,
            "parser_version": PARSER_VERSION,
            "parser_settings": asdict(limits),
        },
    )
