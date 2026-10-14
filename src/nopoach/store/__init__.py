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
