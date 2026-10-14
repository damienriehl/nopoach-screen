"""User-chosen data folders outside the application source/install tree."""

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import duckdb

from nopoach.store import connect
from nopoach.store.provenance import ProvenanceLog
from nopoach.store.schema import initialize_schema


def _application_root() -> Path:
    module = Path(__file__).resolve()
    for directory in module.parents:
        if (directory / "pyproject.toml").is_file():
            return directory
    # An installed wheel lives under site-packages/nopoach/store/.
    return module.parents[2]


@dataclass
class Workspace:
    root: Path
    database_path: Path
    parquet_path: Path
    provenance: ProvenanceLog
    connection: duckdb.DuckDBPyConnection

    @classmethod
    def open(cls, folder: str | Path) -> "Workspace":
        root = Path(folder).expanduser().resolve()
        if root.is_relative_to(_application_root()):
            raise ValueError(
                "Choose a workspace outside the app source or install tree"
            )
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        parquet = root / "parquet"
        parquet.mkdir(mode=0o700, exist_ok=True)
        database = root / "workspace.duckdb"
        connection = connect(database)
        try:
            initialize_schema(connection)
            provenance = ProvenanceLog(root / "provenance.jsonl", connection)
        except BaseException:
            connection.close()
            raise
        return cls(root, database, parquet, provenance, connection)

    def table_names(self) -> set[str]:
        return {row[0] for row in self.connection.execute("SHOW TABLES").fetchall()}

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
