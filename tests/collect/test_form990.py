import json
import socket
from datetime import date
from pathlib import Path

import httpx
import pytest

from nopoach.collect.fetch import FetchClient, OfflineError
from nopoach.collect.form990 import collect_market, parse_return
from nopoach.store.workspace import Workspace

FIXTURES = Path(__file__).parents[1] / "fixtures" / "form990"
INDEX_URL = "https://s3.amazonaws.com/irs-form-990/index_2020.csv"


def test_part_vii_directors_officers_and_tax_year():
    rows = parse_return((FIXTURES / "return.xml").read_bytes())
    assert [row["person_name"] for row in rows] == [
        "Robin Synthetic",
        "Casey Synthetic",
        "Taylor Synthetic",
    ]
    assert [row["title"] for row in rows] == [
        "Director",
        "Board Chair",
        "Chief Executive Officer",
    ]
    assert all(row["employer_name"] == "Synthetic Hospital" for row in rows)
    assert all(row["employer_id"] == "123456789" for row in rows)
    assert all(row["tax_year"] == "2019" for row in rows)
    assert all(row["start_date"] == row["end_date"] == "2019" for row in rows)
    assert all(row["start_precision"] == row["end_precision"] == "year" for row in rows)
    assert rows[1]["role_flags"] == ["director", "officer"]
    assert rows[2]["role_flags"] == ["officer", "key_employee"]


@pytest.mark.network_off
@pytest.mark.parametrize("index_fixture", ["index.csv", "index.json"])
def test_bulk_index_collects_full_market_and_skips_malformed(
    tmp_path, monkeypatch, caplog, index_fixture
):
    def forbidden(*args, **kwargs):
        pytest.fail("Form 990 collection opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    hospital = (FIXTURES / "return.xml").read_bytes()
    university = hospital.replace(b"123456789", b"987654321").replace(
        b"Synthetic Hospital", b"Synthetic University"
    )
    bodies = {
        INDEX_URL: (FIXTURES / index_fixture).read_bytes(),
        "https://s3.amazonaws.com/irs-form-990/synthetic-hospital_public.xml": hospital,
        "https://s3.amazonaws.com/irs-form-990/synthetic-university_public.xml": (
            university
        ),
        "https://s3.amazonaws.com/irs-form-990/malformed_public.xml": b"<broken",
    }

    def handler(request):
        return httpx.Response(200, content=bodies[str(request.url)])

    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="fixture@example.invalid",
            allowed_hosts={"s3.amazonaws.com": 10},
            transport=httpx.MockTransport(handler),
        ) as fetch:
            report = collect_market(
                workspace,
                fetch,
                index_urls=[INDEX_URL],
                market_employers={
                    "12-3456789": "Synthetic Hospital",
                    "987654321": "Synthetic University",
                },
            )
        assert report.accepted_count == 6
        assert len(report.skipped) == 1
        assert "malformed_public.xml" in caplog.text
        receipts = {
            r.id: r for r in workspace.provenance.records() if r.method == "GET"
        }
        assert {r.url for r in receipts.values()} == set(bodies)
        for (raw,) in workspace.connection.execute(
            "SELECT raw_json FROM imported_rows"
        ).fetchall():
            row = json.loads(raw)
            receipt = receipts[row["source_provenance_id"]]
            assert receipt.url == row["source_url"]
            assert row["source_collection_method"] == "public_http_fetch"
        assert workspace.connection.execute(
            "SELECT start_date, end_date, start_precision, end_precision "
            "FROM positions LIMIT 1"
        ).fetchone() == (date(2019, 1, 1), date(2019, 12, 31), "year", "year")


@pytest.mark.network_off
def test_form990_offline_rejects_before_socket(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline Form 990 opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="fixture@example.invalid",
            allowed_hosts={"s3.amazonaws.com": 10},
            offline=True,
        ) as fetch:
            with pytest.raises(OfflineError):
                collect_market(
                    workspace,
                    fetch,
                    index_urls=[INDEX_URL],
                    market_employers={"123456789": "Synthetic Hospital"},
                )


@pytest.mark.network_off
def test_form990_requires_workspace_fetch_receipts(tmp_path):
    from nopoach.store.provenance import ProvenanceLog

    def forbidden(request):
        pytest.fail("Mismatched provenance reached the transport")

    with Workspace.open(tmp_path / "data") as workspace:
        separate_log = ProvenanceLog(tmp_path / "other-receipts.jsonl")
        with FetchClient(
            separate_log,
            contact="fixture@example.invalid",
            allowed_hosts={"s3.amazonaws.com": 10},
            transport=httpx.MockTransport(forbidden),
        ) as fetch:
            with pytest.raises(ValueError, match="workspace provenance"):
                collect_market(
                    workspace,
                    fetch,
                    index_urls=[INDEX_URL],
                    market_employers={"123456789": "Synthetic Hospital"},
                )
