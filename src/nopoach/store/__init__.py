"""Local workspace storage and source provenance."""

from pathlib import Path

import duckdb


def connect(path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    """Disable extension installation/loading before any SQL executes."""
    return duckdb.connect(
        str(path),
        config={
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )


def connect_read_only(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open an existing database without permitting DuckDB to create or alter it."""
    database = Path(path)
    if not database.is_file():
        raise FileNotFoundError(database)
    return duckdb.connect(
        str(database),
        read_only=True,
        config={
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )
