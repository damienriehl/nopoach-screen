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
