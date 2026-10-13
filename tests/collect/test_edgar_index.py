import calendar
import json
import os
import socket
from datetime import date
from pathlib import Path

import httpx
import pytest

from nopoach.collect.edgar_index import (
    collect_market,
    daily_index_urls,
    parse_form_index,
    select_documents,
)
from nopoach.collect.fetch import FetchClient, OfflineError
from nopoach.market.define import Employer
from nopoach.store.workspace import Workspace

INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/2020/QTR1/form.20200203.idx"
FORMS = ("8-K", "10-K", "10-Q", "S-4", "DEFM14A")


def index_body():
    rows = [
        f"{form:12}Synthetic Works    {100 + i}    2020-02-03    "
        f"edgar/data/{100 + i}/0000000100-20-00000{i}.txt"
        for i, form in enumerate(FORMS)
    ]
    rows += [
        "DEF 14A     Synthetic Proxy    100    2020-02-03    "
        "edgar/data/100/unsupported.txt",
        "10-K     Outside Market    999    2020-02-03    edgar/data/999/outside.txt",
    ]
    return "\n".join(rows).encode()


def detail(form):
    return (
        "<table><tr><td>1</td><td>Body</td><td>"
        f'<a href="body.htm">Body</a></td><td>{form}</td></tr>'
        "<tr><td>2</td><td>Contract</td><td>"
        '<a href="ex10.htm">Contract</a></td><td>EX-10.1</td></tr>'
        "<tr><td>3</td><td>Merger</td><td>"
        '<a href="ex2.htm">Merger</a></td><td>EX-2.1</td></tr>'
        "<tr><td>4</td><td>Subsidiaries</td><td>"
        '<a href="ex21.htm">Subsidiaries</a></td><td>EX-21</td></tr></table>'
    ).encode()


def test_all_required_forms_and_market_parents_are_selected():
    entries = parse_form_index(index_body(), {str(100 + i) for i in range(5)})
    assert [entry.form_type for entry in entries] == list(FORMS)
    assert {entry.cik for entry in entries} == {str(100 + i) for i in range(5)}


def test_month_daily_indexes_and_only_required_documents():
    urls = daily_index_urls(date(2020, 2, 1), date(2020, 2, 29))
    assert len(urls) == 20
    assert urls[0] == INDEX_URL
    assert urls[-1].endswith("form.20200228.idx")
    for form in FORMS:
        selected = select_documents(
            detail(form), "https://www.sec.gov/Archives/test/index.html", form
        )
        assert [d.form_type for d in selected] == (
            ["8-K", "EX-10.1", "EX-2.1"] if form == "8-K" else ["EX-10.1", "EX-2.1"]
        )


@pytest.mark.network_off
def test_market_collection_fetches_all_employers_and_parent_with_traceable_storage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        socket, "socket", lambda *a, **kw: pytest.fail("real socket opened")
    )
    employers = {
        str(100 + i): Employer(str(i), f"Synthetic Company {i}") for i in range(5)
    }
    bodies = {INDEX_URL: index_body()}
    for i, form in enumerate(FORMS):
        base = f"https://www.sec.gov/Archives/edgar/data/{100 + i}/"
        bodies[base + f"0000000100-20-00000{i}-index.html"] = detail(form)
        if form == "8-K":
            bodies[base + "body.htm"] = b"No employment clause in this body."
        bodies[base + "ex10.htm"] = (
            b"Synthetic Company 0 and Synthetic Company 1 agree that neither "
            b"party shall solicit employees of the other party for two years."
        )
        bodies[base + "ex2.htm"] = b"Merger consideration only."
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=bodies[str(request.url)])

    with Workspace.open(tmp_path / "data") as workspace:
        seed = workspace.provenance.append(
            source="synthetic", url="", method="IMPORT", body=b"seed"
        )
        for cik, employer in employers.items():
            workspace.connection.execute(
                "INSERT INTO employers VALUES (?, ?, ?, ?)",
                [employer.id, employer.name, cik, seed.id],
            )
        with FetchClient(
            workspace.provenance,
            contact="fixture@example.invalid",
            transport=httpx.MockTransport(handler),
        ) as fetch:
            report = collect_market(
                workspace,
                fetch,
                index_urls=[INDEX_URL, INDEX_URL],
                market_employers=employers,
            )
        assert set(seen) == set(bodies)
        assert len(seen) == len(set(seen))
        assert len(report.documents) == 11
        assert len(report.clauses) == 5
        assert len(report.fetched_provenance_ids) == 17
        assert {c.provenance.id for c in report.clauses} <= set(
            report.fetched_provenance_ids
        )
        assert all(c.provenance.source == "www.sec.gov" for c in report.clauses)
        assert report.disk_bytes == sum(
            p.stat().st_size for p in (workspace.root / "filings").iterdir()
        )
        assert workspace.connection.execute(
            "SELECT count(*) FROM clauses"
        ).fetchone() == (5,)


@pytest.mark.network_off
def test_offline_collection_never_opens_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(
        socket, "socket", lambda *a, **kw: pytest.fail("real socket opened")
    )
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance, contact="fixture@example.invalid", offline=True
        ) as fetch:
            with pytest.raises(OfflineError):
                collect_market(
                    workspace, fetch, index_urls=[INDEX_URL], market_employers={}
                )
        assert workspace.provenance.records() == ()


@pytest.mark.live
def test_live_month_of_index_selected_market_filings(tmp_path, record_property):
    """Host manifest: year, month, market_employers {CIK: resolved employer name}.

    Host runs with U8_CLAUSES_LIVE_SAMPLE=/outside/repo/sample.json and SEC_CONTACT.
    The full market and parents must be present; never pass a narrowed target pair.
    """
    manifest = os.environ.get("U8_CLAUSES_LIVE_SAMPLE")
    contact = os.environ.get("SEC_CONTACT")
    if not manifest or not contact:
        pytest.skip("Host must supply U8_CLAUSES_LIVE_SAMPLE and SEC_CONTACT")
    sample = json.loads(Path(manifest).read_text())
    year, month = sample["year"], sample["month"]
    with Workspace.open(tmp_path / "live-data") as workspace:
        employers = {}
        receipt = workspace.provenance.append(
            source="host sample market",
            url="",
            method="IMPORT",
            body=b"host-supplied market",
        )
        for cik, name in sample["market_employers"].items():
            employer = Employer(cik, name)
            employers[cik] = employer
            workspace.connection.execute(
                "INSERT INTO employers VALUES (?, ?, ?, ?)",
                [cik, name, cik, receipt.id],
            )
        with FetchClient(workspace.provenance, contact=contact) as fetch:
            report = collect_market(
                workspace,
                fetch,
                index_urls=daily_index_urls(
                    date(year, month, 1),
                    date(year, month, calendar.monthrange(year, month)[1]),
                ),
                market_employers=employers,
            )
        assert report.documents, "Host month must include selected filings"
        receipts = {r.id for r in workspace.provenance.records()}
        assert set(report.fetched_provenance_ids) <= receipts
        assert report.disk_bytes > 0
        record_property("disk_bytes", report.disk_bytes)
        record_property("fetched_files", len(report.fetched_provenance_ids))
