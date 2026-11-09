import pytest

from nopoach.backtest.metadata import input_identity, workspace_input_identity
from nopoach.store.workspace import Workspace


def test_identity_is_stable_and_ignores_screen_outputs(tmp_path):
    with Workspace.open(tmp_path / "identity") as workspace:
        first = input_identity(workspace.connection)
        assert first.status == "available"
        assert first.sha256
        workspace.connection.execute(
            "INSERT INTO provenance VALUES "
            "('output', 'screen', 'file:screen', now(), 'SCREEN', "
            "'offline_screen', ?, NULL)",
            ["0" * 64],
        )
        assert input_identity(workspace.connection).sha256 == first.sha256


def test_read_only_identity_does_not_create_or_change_storage(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        workspace_input_identity(missing)
    assert not missing.exists()

    root = tmp_path / "existing"
    with Workspace.open(root) as workspace:
        expected = input_identity(workspace.connection)
    database = root / "workspace.duckdb"
    log = root / "provenance.jsonl"
    before = (database.read_bytes(), log.read_bytes())
    assert workspace_input_identity(root).sha256 == expected.sha256
    assert (database.read_bytes(), log.read_bytes()) == before


def test_optional_missing_differs_from_empty(tmp_path):
    with Workspace.open(tmp_path / "optional") as workspace:
        missing = input_identity(workspace.connection)
        workspace.connection.execute(
            "CREATE TABLE employer_aliases(alias VARCHAR, employer_id VARCHAR)"
        )
        empty = input_identity(workspace.connection)
        assert missing.sha256 != empty.sha256


def test_unexpected_persistent_table_is_explicitly_unsupported(tmp_path):
    with Workspace.open(tmp_path / "unsupported") as workspace:
        workspace.connection.execute("CREATE TABLE surprise(value INTEGER)")
        identity = input_identity(workspace.connection)
        assert identity.status == "unsupported"
        assert "surprise" in (identity.reason or "")
