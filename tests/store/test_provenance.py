from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from nopoach.store.provenance import AppendOnlyError, ProvenanceLog
from nopoach.store.workspace import Workspace


def test_provenance_is_durable_and_immutable(tmp_path):
    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    before = datetime.now(UTC)
    record = log.append(
        source="synthetic",
        url="https://data.sec.gov/synthetic.json",
        method="GET",
        body=b'{"synthetic":true}',
    )
    assert record.url == "https://data.sec.gov/synthetic.json"
    assert record.source == "synthetic"
    assert record.method == "GET"
    assert record.collection_method == "public_http_fetch"
    assert before <= record.collected_at <= datetime.now(UTC)
    assert record.sha256 == sha256(b'{"synthetic":true}').hexdigest()
    assert ProvenanceLog(log.path).records() == (record,)
    with pytest.raises(FrozenInstanceError):
        record.method = "EDIT"
    original = log.path.read_bytes()
    with pytest.raises(AppendOnlyError, match="existing"):
        log.append_record(replace(record, method="EDIT"))
    assert log.path.read_bytes() == original
    next_record = log.append(
        source="synthetic",
        url="file:second",
        method="IMPORT",
        body=b"second",
        collection_method="agency_import",
    )
    assert log.path.read_bytes().startswith(original)
    assert log.records() == (record, next_record)


def test_workspace_recovers_log_written_before_database_insert(tmp_path):
    folder = tmp_path / "recovery"
    with Workspace.open(folder) as workspace:
        log_path = workspace.provenance.path
    record = ProvenanceLog(log_path).append(
        source="synthetic",
        url="file:recovery",
        method="IMPORT",
        body=b"fixture",
        collection_method="agency_import",
    )
    with Workspace.open(folder) as workspace:
        assert workspace.connection.execute(
            "SELECT sha256 FROM provenance WHERE id = ?", [record.id]
        ).fetchone() == (sha256(b"fixture").hexdigest(),)


def test_workspace_rejects_log_truncated_below_database(tmp_path):
    folder = tmp_path / "truncated"
    with Workspace.open(folder) as workspace:
        log_path = workspace.provenance.path
        workspace.provenance.append(
            source="synthetic", url="file:retained", method="IMPORT", body=b"first"
        )
        retained = log_path.read_bytes()
        workspace.provenance.append(
            source="synthetic", url="file:removed", method="IMPORT", body=b"second"
        )
    log_path.write_bytes(retained)
    with pytest.raises(AppendOnlyError, match="removed from log"):
        Workspace.open(folder)
    assert log_path.read_bytes() == retained


def test_workspace_rejects_duplicate_receipt_in_log(tmp_path):
    folder = tmp_path / "duplicated"
    with Workspace.open(folder) as workspace:
        log_path = workspace.provenance.path
        workspace.provenance.append(
            source="synthetic", url="file:duplicate", method="IMPORT", body=b"fixture"
        )
        original = log_path.read_bytes()
    log_path.write_bytes(original + original)
    with pytest.raises(AppendOnlyError, match="Duplicate existing"):
        Workspace.open(folder)
    assert log_path.read_bytes() == original + original


@pytest.mark.parametrize("recover_prefix", [False, True])
def test_startup_rejects_altered_database_receipt(tmp_path, recover_prefix):
    with Workspace.open(tmp_path / "altered") as workspace:
        first = workspace.provenance.append(
            source="synthetic", url="file:first", method="IMPORT", body=b"first"
        )
        second = workspace.provenance.append(
            source="synthetic", url="file:second", method="IMPORT", body=b"second"
        )
        original = workspace.provenance.path.read_bytes()
        if recover_prefix:
            workspace.connection.execute(
                "DELETE FROM provenance WHERE id = ?", [first.id]
            )
        workspace.connection.execute(
            "UPDATE provenance SET method = 'EDIT' WHERE id = ?", [second.id]
        )
        with pytest.raises(AppendOnlyError, match="Cannot edit"):
            ProvenanceLog(workspace.provenance.path, workspace.connection)
        assert (
            workspace.connection.execute(
                "SELECT * FROM provenance WHERE id = ?", [first.id]
            ).fetchone()
            == first.database_values()
        )
        assert workspace.connection.execute(
            "SELECT method FROM provenance WHERE id = ?", [second.id]
        ).fetchone() == ("EDIT",)
        assert workspace.provenance.path.read_bytes() == original


def test_append_rejects_conflicting_database_mirror(tmp_path):
    with Workspace.open(tmp_path / "mirror") as workspace:
        first = workspace.provenance.append(
            source="synthetic", url="file:first", method="IMPORT", body=b"fixture"
        )
        incoming = replace(first, id="synthetic-conflict", url="file:conflict")
        conflicting = replace(incoming, method="EDIT")
        workspace.connection.execute(
            "INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            conflicting.database_values(),
        )
        with pytest.raises(AppendOnlyError, match="Cannot edit"):
            workspace.provenance.append_record(incoming)
        assert (
            workspace.connection.execute(
                "SELECT * FROM provenance WHERE id = ?", [incoming.id]
            ).fetchone()
            == conflicting.database_values()
        )
        assert workspace.provenance.records() == (first, incoming)
