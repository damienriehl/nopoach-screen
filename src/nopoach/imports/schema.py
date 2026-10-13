"""Employment import v1: identical column names in UTF-8 CSV and Parquet.

Required columns: person_key, employer_name, title, location, start_date,
end_date, start_precision, end_precision. Empty end_date means ongoing/unknown.
Optional columns: person_name, employer_id, employer_id_type, occupation_code.
IDs have types cik, crd, ein, or a vendor namespace (default: source name).
Dates use YYYY, YYYY-MM, or YYYY-MM-DD with matching year/month/day precision;
empty dates require unknown precision. Starts use the first day of a period and
ends the last; precision remains on every position, with original values retained
in imported_rows. Vendor column maps are canonical -> input.

Source name and R24 provenance class are required import arguments, never inferred
from the filename or vendor. Person keys default to a source-scoped namespace;
callers may declare a shared provider namespace when keys really are shared.
Use template_csv() for the CSV header; Parquet uses the same nullable string
columns. import_records() is the common programmatic/UI/synthetic entry point.

Companion tables extend U1 facts without duplicating employers/people/positions.
Raw input and row number remain available for accepted AND rejected rows.
"""

from enum import StrEnum
from io import BytesIO

import duckdb


class ProvenanceClass(StrEnum):
    # Test/demo data only; never a recommendable source of real observations.
    SYNTHETIC = "synthetic"
    FIRST_PARTY_LINKEDIN = "first_party_linkedin"
    GOVERNMENT_ADMINISTRATIVE = "government_or_administrative_records"
    LICENSED_ORIGINAL = "licensed_from_original_source"
    CONSENT_BASED = "consent_based"
    THIRD_PARTY_UNCLEAR = "third_party_web_aggregation_unclear_or_possibly_unlawful"


REQUIRED_COLUMNS = (
    "person_key",
    "employer_name",
    "title",
    "location",
    "start_date",
    "end_date",
    "start_precision",
    "end_precision",
)
OPTIONAL_COLUMNS = ("person_name", "employer_id", "employer_id_type", "occupation_code")
COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
VENDOR_ALIASES = {
    "person_key": ("worker_id", "person_id", "profile_id"),
    "person_name": ("full_name", "name"),
    "employer_name": ("company", "company_name", "organization"),
    "employer_id": ("company_id", "organization_id"),
    "title": ("job_title", "position_title"),
    "occupation_code": ("soc", "soc_code"),
    "location": ("city", "job_location"),
    "start_date": ("start", "date_started"),
    "end_date": ("end", "date_ended"),
}

# Deliberately small, auditable keyword-to-SOC table. Specific phrases precede
# broad titles; unsupported titles stay unmapped and contribute to coverage.
TITLE_SOC = (
    ("software developer", "15-1252"),
    ("software engineer", "15-1252"),
    ("computer programmer", "15-1251"),
    ("registered nurse", "29-1141"),
    ("accountant", "13-2011"),
    ("auditor", "13-2011"),
    ("human resources specialist", "13-1071"),
    ("lawyer", "23-1011"),
    ("attorney", "23-1011"),
)


def template_csv() -> str:
    """A data-free template; dates and precision are documented above."""
    return ",".join(COLUMNS) + "\n"


def template_parquet() -> bytes:
    """Data-free Parquet template with the same nullable string columns as CSV."""
    import pyarrow as arrow  # type: ignore[import-untyped]
    import pyarrow.parquet as parquet  # type: ignore[import-untyped]

    output = BytesIO()
    table = arrow.table(
        {column: arrow.array([], type=arrow.string()) for column in COLUMNS}
    )
    parquet.write_table(table, output)
    return output.getvalue()


def initialize_import_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Lazily extend the existing workspace; safe on reopening older U1 data."""
    classes = ",".join(f"'{item.value}'" for item in ProvenanceClass)
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS imports (
            id VARCHAR PRIMARY KEY,
            source VARCHAR NOT NULL,
            provenance_class VARCHAR NOT NULL CHECK (provenance_class IN ({classes})),
            person_key_namespace VARCHAR NOT NULL,
            provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
        );
        CREATE TABLE IF NOT EXISTS imported_rows (
            import_id VARCHAR NOT NULL REFERENCES imports(id),
            row_number INTEGER NOT NULL,
            source VARCHAR NOT NULL,
            provenance_class VARCHAR NOT NULL CHECK (provenance_class IN ({classes})),
            raw_json JSON NOT NULL,
            position_id VARCHAR REFERENCES positions(id),
            rejection_reason VARCHAR,
            location VARCHAR,
            provenance_id VARCHAR NOT NULL REFERENCES provenance(id),
            PRIMARY KEY (import_id, row_number),
            CHECK ((position_id IS NULL) <> (rejection_reason IS NULL))
        );
        CREATE TABLE IF NOT EXISTS employer_aliases (
            id VARCHAR PRIMARY KEY,
            employer_id VARCHAR NOT NULL REFERENCES employers(id),
            name VARCHAR NOT NULL,
            normalized_name VARCHAR NOT NULL,
            external_id VARCHAR,
            external_id_type VARCHAR,
            evidence_json JSON NOT NULL,
            provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
        );
        CREATE TABLE IF NOT EXISTS person_keys (
            id VARCHAR PRIMARY KEY,
            person_id VARCHAR NOT NULL REFERENCES people(id),
            namespace VARCHAR NOT NULL,
            person_key VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            normalized_name VARCHAR,
            employment_signature VARCHAR,
            evidence_json JSON NOT NULL,
            provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
        );
        CREATE TABLE IF NOT EXISTS parent_link_metadata (
            link_id VARCHAR PRIMARY KEY REFERENCES employer_parents(id),
            origin VARCHAR NOT NULL CHECK (origin IN ('sec_ex21', 'manual_override')),
            reason VARCHAR,
            recorded_order BIGINT NOT NULL
        );
    """)
