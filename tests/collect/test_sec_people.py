import json
import os
import socket
from datetime import date
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from nopoach.collect.fetch import FetchClient, OfflineError
from nopoach.collect.sec_people import collect_market, parse_filing, parse_form_index
from nopoach.store.workspace import Workspace

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sec"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/2020/QTR1/form.20200203.idx"


def test_form4_officer_and_filing_date_not_invented_start():
    rows = parse_filing(
        (FIXTURES / "form4.xml").read_bytes(),
        form_type="4",
        filed_on="2020-02-03",
        cik="1111",
        employer_name="Synthetic Market Works",
    )
    assert len(rows) == 1
    assert rows[0]["person_key"] == "9001"
    assert rows[0]["person_name"] == "Alex Synthetic"
    assert rows[0]["employer_id"] == "1111"
    assert rows[0]["title"] == "Chief Financial Officer"
    assert rows[0]["filed_on"] == "2020-02-03"
    assert rows[0]["start_precision"] == "unknown"
    assert rows[0]["start_date"] == ""


@pytest.mark.parametrize("form_type", ["3", "4", "5"])
def test_insider_submission_selects_xml_document(form_type):
    body = (FIXTURES / "form4.xml").read_bytes()
    submission = (
        b"<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>"
        + form_type.encode()
        + b"\n<TEXT>\n"
        + body
        + b"\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>"
    )
    rows = parse_filing(
        submission,
        form_type=form_type,
        filed_on="2020-02-03",
        cik="1111",
        employer_name="Synthetic Market Works",
    )
    assert rows[0]["person_name"] == "Alex Synthetic"


def test_item502_departure_uses_effective_date():
    rows = parse_filing(
        (FIXTURES / "departure.html").read_bytes(),
        form_type="8-K",
        filed_on="2020-03-03",
        cik="1111",
        employer_name="Synthetic Market Works",
    )
    assert len(rows) == 1
    assert rows[0]["person_name"] == "Alex Synthetic"
    assert rows[0]["title"] == "Chief Financial Officer"
    assert rows[0]["end_date"] == "2020-03-31"
    assert rows[0]["end_precision"] == "day"
    assert rows[0]["start_precision"] == "unknown"


def test_proxy_prior_employers_have_year_precision():
    rows = parse_filing(
        (FIXTURES / "proxy.html").read_bytes(),
        form_type="DEF 14A",
        filed_on="2020-04-01",
        cik="1111",
        employer_name="Synthetic Market Works",
    )
    prior = [row for row in rows if row["end_date"]]
    assert [
        (row["employer_name"], row["start_date"], row["end_date"]) for row in prior
    ] == [
        ("Synthetic Alpha", "2010", "2012"),
        ("Synthetic Beta", "2013", "2015"),
        ("Synthetic Gamma", "2016", "2017"),
    ]
    assert all(
        row["start_precision"] == row["end_precision"] == "year" for row in prior
    )
    assert all(row["person_name"] == "Alex Synthetic" for row in prior)
    assert all(row["employer_id"] == "" for row in prior)


def test_form_index_selects_full_market_and_parents():
    entries = parse_form_index((FIXTURES / "form.idx").read_bytes(), {"1111", "2222"})
    assert [entry.form_type for entry in entries] == ["4", "8-K", "DEF 14A", "4"]
    assert {entry.cik for entry in entries} == {"1111", "2222"}


@pytest.mark.network_off
def test_batch_logs_malformed_and_retains_every_source_receipt(
    tmp_path, caplog, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("A collector opened a real socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    bodies = {
        INDEX_URL: (FIXTURES / "form.idx").read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/1111/synthetic-form4.txt": (
            FIXTURES / "form4.xml"
        ).read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/1111/synthetic-departure.txt": (
            FIXTURES / "departure.html"
        ).read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/2222/synthetic-proxy.txt": (
            FIXTURES / "proxy.html"
        ).read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/1111/malformed.txt": b"<broken",
    }

    def handler(request):
        return httpx.Response(200, content=bodies[str(request.url)])

    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="fixture@example.invalid",
            transport=httpx.MockTransport(handler),
        ) as fetch:
            report = collect_market(
                workspace,
                fetch,
                index_urls=[INDEX_URL],
                market_employers={
                    "1111": "Synthetic Market Works",
                    "2222": "Synthetic Market Parent",
                },
            )
        assert report.accepted_count == 6
        assert len(report.skipped) == 1
        assert "malformed.txt" in caplog.text
        receipts = {
            r.url: r for r in workspace.provenance.records() if r.method == "GET"
        }
        assert set(receipts) == set(bodies)
        for url, body in bodies.items():
            assert receipts[url].sha256 == sha256(body).hexdigest()
        raw_rows = workspace.connection.execute(
            "SELECT raw_json FROM imported_rows"
        ).fetchall()
        for (raw,) in raw_rows:
            row = json.loads(raw)
            receipt = receipts[row["source_url"]]
            assert row["source_provenance_id"] == receipt.id
            assert row["source_sha256"] == receipt.sha256
            assert row["source_collection_method"] == "public_http_fetch"
            assert row["source_collected_at"] == receipt.collected_at.isoformat()
        assert workspace.connection.execute(
            "SELECT end_date FROM positions WHERE end_precision = 'day'"
        ).fetchone() == (date(2020, 3, 31),)


@pytest.mark.network_off
def test_sec_offline_rejects_before_socket(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline collection opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance, contact="fixture@example.invalid", offline=True
        ) as fetch:
            with pytest.raises(OfflineError):
                collect_market(
                    workspace,
                    fetch,
                    index_urls=[INDEX_URL],
                    market_employers={"1111": "Synthetic Market Works"},
                )
        assert workspace.provenance.records() == ()


@pytest.mark.live
def test_live_edgar_market_provenance(tmp_path):
    """Host supplies a daily form index, full market/parents, and declared contact."""
    index = os.environ.get("SEC_U4_FORM_INDEX_URL")
    employers = os.environ.get("SEC_U4_MARKET_CIKS")
    contact = os.environ.get("SEC_CONTACT")
    if not all((index, employers, contact)):
        pytest.skip(
            "Host must set SEC_U4_FORM_INDEX_URL, SEC_U4_MARKET_CIKS, SEC_CONTACT"
        )
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(workspace.provenance, contact=contact, offline=False) as fetch:
            report = collect_market(
                workspace,
                fetch,
                index_urls=[index],
                market_employers=json.loads(employers),
            )
        assert report.accepted_count > 0
        records = workspace.provenance.records()
        assert any(record.url == index for record in records)
        assert len([r for r in records if r.method == "GET"]) > 1
        assert all(len(r.sha256) == 64 and r.collected_at for r in records)
        print(workspace.provenance.path.read_text())


def test_proxy_director_biography_with_issuer_reference():
    rows = parse_filing(
        b"<p>Robin Synthetic has been a director of our company since 2018.</p>",
        form_type="DEF 14A",
        filed_on="2020-04-01",
        cik="1111",
        employer_name="Synthetic Market Works",
    )
    assert len(rows) == 1
    assert rows[0]["person_name"] == "Robin Synthetic"
    assert rows[0]["employer_name"] == "Synthetic Market Works"
    assert rows[0]["employer_id"] == "1111"
    assert rows[0]["title"] == "director"
    assert rows[0]["start_date"] == "2018"
    assert rows[0]["start_precision"] == "year"


def test_item502_appointment_uses_effective_date():
    rows = parse_filing(
        b"<p>Item 5.02 Certain Officers</p>"
        b"<p>On April 2, 2020, Casey Synthetic was appointed as Chief Executive "
        b"Officer of Synthetic Market Works, effective April 15, 2020.</p>",
        form_type="8-K",
        filed_on="2020-04-03",
        cik="1111",
        employer_name="Synthetic Market Works",
    )
    assert len(rows) == 1
    assert rows[0]["start_date"] == "2020-04-15"
    assert rows[0]["start_precision"] == "day"
    assert rows[0]["end_precision"] == "unknown"


@pytest.mark.network_off
def test_collector_requires_workspace_fetch_receipts(tmp_path):
    from nopoach.store.provenance import ProvenanceLog

    def forbidden(request):
        pytest.fail("Mismatched provenance reached the transport")

    with Workspace.open(tmp_path / "data") as workspace:
        separate_log = ProvenanceLog(tmp_path / "other-receipts.jsonl")
        with FetchClient(
            separate_log,
            contact="fixture@example.invalid",
            transport=httpx.MockTransport(forbidden),
        ) as fetch:
            with pytest.raises(ValueError, match="workspace provenance"):
                collect_market(
                    workspace,
                    fetch,
                    index_urls=[INDEX_URL],
                    market_employers={"1111": "Synthetic Market Works"},
                )
