import os
import socket
import stat
from pathlib import Path

import duckdb
import pytest

from nopoach.store.workspace import Workspace


def test_create_and_reopen_workspace(tmp_path):
    folder = tmp_path / "synthetic-market"
    with Workspace.open(folder) as workspace:
        assert workspace.database_path == folder / "workspace.duckdb"
        assert workspace.database_path.is_file()
        assert workspace.parquet_path.is_dir()
        assert workspace.provenance.path.is_file()
        tables = {
            row[0] for row in workspace.connection.execute("SHOW TABLES").fetchall()
        }
        assert tables == {
            "position_worksites",
            "workforce_inputs",
            "dol_records",
            "employers",
            "employer_parents",
            "people",
            "positions",
            "filings",
            "clauses",
            "ties",
            "markets",
            "screens",
            "provenance",
        }
        record = workspace.provenance.append(
            source="synthetic-import",
            url="file:synthetic.csv",
            method="IMPORT",
            body=b"synthetic",
            collection_method="agency_import",
        )
        workspace.connection.execute(
            "INSERT INTO employers VALUES ('e1', 'Synthetic Employer', NULL, ?)",
            [record.id],
        )
    with Workspace.open(folder) as reopened:
        assert reopened.connection.execute("SELECT name FROM employers").fetchone() == (
            "Synthetic Employer",
        )
        assert reopened.provenance.records() == (record,)


def test_refuse_source_tree_and_symlink(tmp_path):
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="outside.*(source|install)"):
        Workspace.open(root / "workspace")
    alias = tmp_path / "source-alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="outside.*(source|install)"):
        Workspace.open(alias / "workspace")
    assert not (root / "workspace").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode contract")
def test_new_workspace_is_private(tmp_path):
    with Workspace.open(tmp_path / "private") as workspace:
        assert stat.S_IMODE(workspace.root.stat().st_mode) == 0o700


@pytest.mark.network_off
def test_open_disables_extensions_and_opens_no_socket(tmp_path, monkeypatch):
    def no_socket(*args, **kwargs):
        pytest.fail("Workspace.open attempted to open a socket")

    monkeypatch.setattr(socket, "socket", no_socket)
    with Workspace.open(tmp_path / "offline") as workspace:
        for setting in ("autoinstall_known_extensions", "autoload_known_extensions"):
            assert workspace.connection.execute(
                f"SELECT current_setting('{setting}')"
            ).fetchone() == (False,)
        # The companion host-owned test checks sockets at the OS boundary.


def test_every_fact_requires_existing_provenance(tmp_path):
    with Workspace.open(tmp_path / "facts") as workspace:
        for table in (
            "employers",
            "employer_parents",
            "people",
            "positions",
            "filings",
            "clauses",
            "ties",
            "markets",
            "screens",
        ):
            column = workspace.connection.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = ? AND column_name = 'provenance_id'",
                [table],
            ).fetchone()
            assert column == ("NO",)
        with pytest.raises(duckdb.ConstraintException):
            workspace.connection.execute(
                "INSERT INTO employers VALUES "
                "('e1', 'Synthetic Employer', NULL, 'absent')"
            )
        record = workspace.provenance.append(
            source="synthetic",
            url="file:fixture",
            method="IMPORT",
            body=b"fixture",
            collection_method="agency_import",
        )
        workspace.connection.execute(
            "INSERT INTO employers VALUES ('e1', 'Synthetic Employer', NULL, ?)",
            [record.id],
        )
        workspace.connection.execute(
            "INSERT INTO people VALUES ('p1', 'Synthetic Person', ?)",
            [record.id],
        )
        workspace.connection.execute(
            "INSERT INTO positions VALUES "
            "('pos1', 'p1', 'e1', 'Engineer', '15-1252', '2020-01-01', "
            "NULL, 'year', 'unknown', ?)",
            [record.id],
        )
        assert workspace.connection.execute(
            "SELECT start_precision, end_precision FROM positions"
        ).fetchone() == ("year", "unknown")


@pytest.mark.live
def test_host_workspace_open_has_no_os_network(tmp_path):
    """Host runs under an OS network trace/deny harness to observe native DuckDB."""
    with Workspace.open(tmp_path / "host-offline") as workspace:
        assert workspace.connection.execute("SELECT 42").fetchone() == (42,)
