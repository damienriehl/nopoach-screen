import socket
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest

from nopoach.collect.brokercheck import coverage_report, import_regulator_extract
from nopoach.store.workspace import Workspace

FIXTURE = Path(__file__).parents[1] / "fixtures" / "brokercheck" / "regulator.csv"


@pytest.mark.network_off
def test_regulator_extract_dated_firms_and_provenance(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Regulator extract adapter opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    with Workspace.open(tmp_path / "data") as workspace:
        result = import_regulator_extract(workspace, FIXTURE)
        assert result.accepted_count == 2
        assert result.rejects == ()
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (1,)
        rows = workspace.connection.execute(
            "SELECT e.name, x.start_date, x.end_date, "
            "x.start_precision, x.end_precision "
            "FROM positions x JOIN employers e ON e.id=x.employer_id "
            "ORDER BY x.start_date"
        ).fetchall()
        assert rows == [
            (
                "Synthetic Securities",
                date(2010, 2, 1),
                date(2015, 5, 31),
                "month",
                "month",
            ),
            ("Synthetic Finance", date(2015, 6, 1), None, "month", "unknown"),
        ]
        receipt = workspace.provenance.records()[0]
        assert receipt.collection_method == "agency_import"
        assert receipt.sha256 == sha256(FIXTURE.read_bytes()).hexdigest()
        assert receipt.url == FIXTURE.resolve().as_uri()
        assert receipt.source == "FINRA regulator-supplied extract"
        assert receipt.collected_at is not None


def test_coverage_explicitly_reports_import_only_and_pending_terms():
    report = coverage_report()
    assert report["retrieval_mode"] == "regulator_extract_import"
    assert report["automated_collection"] is False
    assert report["terms_review"] == "pending_host_review"
    assert "regulator-supplied" in report["reason"]
