"""Initial workspace schema; all facts reference their collection evidence."""

import duckdb

# All TIMESTAMP columns store UTC (timezone-free on disk). This avoids
# DuckDB's optional pytz dependency when reading TIMESTAMPTZ into Python.
# No DuckDB extensions are needed by this schema or the fetch client.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS provenance (
    id VARCHAR PRIMARY KEY,
    source VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    collected_at TIMESTAMP NOT NULL,
    method VARCHAR NOT NULL,
    collection_method VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL CHECK (length(sha256) = 64),
    status_code INTEGER
);
CREATE TABLE IF NOT EXISTS employers (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    cik VARCHAR,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
);
CREATE TABLE IF NOT EXISTS employer_parents (
    id VARCHAR PRIMARY KEY,
    employer_id VARCHAR NOT NULL REFERENCES employers(id),
    parent_id VARCHAR NOT NULL REFERENCES employers(id),
    valid_from DATE NOT NULL,
    valid_to DATE,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE TABLE IF NOT EXISTS people (
    id VARCHAR PRIMARY KEY,
    name VARCHAR,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
);
CREATE TABLE IF NOT EXISTS positions (
    id VARCHAR PRIMARY KEY,
    person_id VARCHAR NOT NULL REFERENCES people(id),
    employer_id VARCHAR NOT NULL REFERENCES employers(id),
    title VARCHAR,
    occupation_code VARCHAR,
    start_date DATE,
    end_date DATE,
    start_precision VARCHAR NOT NULL
        CHECK (start_precision IN ('day', 'month', 'year', 'unknown')),
    end_precision VARCHAR NOT NULL
        CHECK (end_precision IN ('day', 'month', 'year', 'unknown')),
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE TABLE IF NOT EXISTS filings (
    id VARCHAR PRIMARY KEY,
    employer_id VARCHAR NOT NULL REFERENCES employers(id),
    form_type VARCHAR NOT NULL,
    filed_on DATE NOT NULL,
    accession_number VARCHAR,
    url VARCHAR NOT NULL,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
);
CREATE TABLE IF NOT EXISTS clauses (
    id VARCHAR PRIMARY KEY,
    filing_id VARCHAR NOT NULL REFERENCES filings(id),
    text VARCHAR NOT NULL,
    clause_type VARCHAR NOT NULL,
    location VARCHAR,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
);
CREATE TABLE IF NOT EXISTS ties (
    id VARCHAR PRIMARY KEY,
    person_id VARCHAR NOT NULL REFERENCES people(id),
    employer_a_id VARCHAR NOT NULL REFERENCES employers(id),
    employer_b_id VARCHAR NOT NULL REFERENCES employers(id),
    tie_type VARCHAR NOT NULL,
    start_date DATE,
    end_date DATE,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
);
CREATE TABLE IF NOT EXISTS markets (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    occupation_code VARCHAR,
    geography VARCHAR NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id),
    CHECK (end_date >= start_date)
);
CREATE TABLE IF NOT EXISTS screens (
    id VARCHAR PRIMARY KEY,
    market_id VARCHAR NOT NULL REFERENCES markets(id),
    created_at TIMESTAMP NOT NULL,
    parameters_json JSON NOT NULL,
    results_json JSON NOT NULL,
    provenance_id VARCHAR NOT NULL REFERENCES provenance(id)
);
"""


def initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the unit's tables without loading any extensions."""
    connection.execute(SCHEMA_SQL)
