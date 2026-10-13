import csv
import io
import os
import socket
from decimal import Decimal
from hashlib import sha256
from zipfile import ZipFile

import httpx
import pytest

from nopoach.collect.dol import DOL_HOSTS, collect_dol
from nopoach.collect.fetch import FetchClient, OfflineError
from nopoach.resolve.employers import EmployerResolver
from nopoach.store.workspace import Workspace

LCA_COLUMNS = [
    "CASE_NUMBER",
    "EMPLOYER_NAME",
    "SOC_CODE",
    "WORKSITE_CITY",
    "WORKSITE_STATE",
    "WORKSITE_POSTAL_CODE",
    "WAGE_RATE_OF_PAY_FROM",
    "WAGE_RATE_OF_PAY_TO",
    "WAGE_UNIT_OF_PAY",
    "TOTAL_WORKER_POSITIONS",
]
LCA_ROWS = [
    [
        "SYNTHETIC-1",
        "Synthetic North LLC",
        "15-1252",
        "Synthetic City",
        "MN",
        "00001",
        "50",
        "60",
        "Hour",
        "3",
    ],
    [
        "SYNTHETIC-2",
        "Synthetic South LLC",
        "15-1252",
        "Synthetic Town",
        "WI",
        "00002",
        "90000",
        "",
        "Year",
        "2",
    ],
]


def csv_bytes(columns=LCA_COLUMNS, rows=LCA_ROWS):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(columns)
    writer.writerows(rows)
    return stream.getvalue().encode()


def test_lca_bulk_local_state_filter_resolution_metro_and_wage(tmp_path):
    body = csv_bytes()
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=body)

    with Workspace.open(tmp_path / "data") as workspace:
        resolver = EmployerResolver(workspace)
        existing_receipt = workspace.provenance.append(
            source="synthetic", url="file:synthetic", method="IMPORT", body=b"test"
        )
        existing = resolver.resolve(
            "Synthetic North", provenance_id=existing_receipt.id
        ).entity_id
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts=DOL_HOSTS,
            transport=httpx.MockTransport(handler),
        ) as client:
            records = collect_dol(
                client,
                "https://www.dol.gov/synthetic-lca.csv",
                resolver,
                program="LCA",
                state="MN",
                metro_lookup=lambda city, state, postal: (
                    "99999"
                    if (city, state, postal) == ("Synthetic City", "MN", "00001")
                    else None
                ),
            )
        assert requests == ["https://www.dol.gov/synthetic-lca.csv"]
        assert len(records) == 1
        row = records[0]
        assert row.employer_id == existing
        assert row.employer_name == "Synthetic North LLC"
        assert row.soc_code == "15-1252"
        assert row.worksite_metro == "99999"
        assert row.wage_from == Decimal("50")
        assert row.wage_to == Decimal("60")
        assert row.annual_wage_from == Decimal("104000")
        assert row.annual_wage_to == Decimal("124800")
        assert row.worker_positions == 3
        assert row.provenance == workspace.provenance.records()[-1]
        assert row.provenance.sha256 == sha256(body).hexdigest()
        assert row.provenance.collection_method == "public_http_fetch"
        assert resolver.evidence(existing)[-1]["provenance_id"] == row.provenance.id


def xlsx_bytes(columns, rows):
    # Synthetic OOXML with shared strings, as bulk XLSX uses.
    from xml.sax.saxutils import escape

    strings = columns + [str(value) for row in rows for value in row]
    shared = "".join(f"<si><t>{escape(value)}</t></si>" for value in strings)
    sheets = []
    for index, row in enumerate([columns, *rows], 1):
        cells = []
        for column, value in enumerate(row):
            ref = f"{chr(65 + column)}{index}"
            position = strings.index(str(value))
            cells.append(f'<c r="{ref}" t="s"><v>{position}</v></c>')
        sheets.append(f'<row r="{index}">{"".join(cells)}</row>')
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared}</sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheets)}</sheetData></worksheet>',
        )
    return stream.getvalue()


def test_perm_xlsx_and_unknown_metro(tmp_path):
    columns = [
        "CASE_NUMBER",
        "EMPLOYER_NAME",
        "SOC_CODE",
        "WORKSITE_CITY",
        "WORKSITE_STATE",
        "WORKSITE_POSTAL_CODE",
        "WAGE_OFFER_FROM",
        "WAGE_OFFER_TO",
        "WAGE_OFFER_UNIT_OF_PAY",
    ]
    body = xlsx_bytes(
        columns,
        [
            [
                "SYNTHETIC-PERM",
                "Synthetic North LLC",
                "15-1252",
                "Synthetic City",
                "MN",
                "00001",
                "8000",
                "",
                "Month",
            ],
        ],
    )
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts=DOL_HOSTS,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=body)
            ),
        ) as client:
            (row,) = collect_dol(
                client,
                "https://www.dol.gov/synthetic-perm.xlsx",
                EmployerResolver(workspace),
                program="PERM",
            )
        assert row.program == "PERM"
        assert row.case_number == "SYNTHETIC-PERM"
        assert row.annual_wage_from == Decimal("96000")
        assert row.wage_to is None
        assert row.worker_positions is None
        assert row.worksite_metro is None


def test_missing_dol_column_names_source_and_column_before_resolution(tmp_path):
    body = csv_bytes([name for name in LCA_COLUMNS if name != "SOC_CODE"], [])
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts=DOL_HOSTS,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=body)
            ),
        ) as client:
            with pytest.raises(ValueError, match="synthetic-lca.csv.*SOC_CODE"):
                collect_dol(
                    client,
                    "https://www.dol.gov/synthetic-lca.csv",
                    EmployerResolver(workspace),
                    program="LCA",
                )
        assert len(workspace.provenance.records()) == 1
        assert (
            workspace.connection.execute("SELECT count(*) FROM employers").fetchone()[0]
            == 0
        )


@pytest.mark.network_off
def test_dol_offline_does_not_open_socket(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Collector opened a socket")

    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts=DOL_HOSTS,
            offline=True,
        ) as client:
            monkeypatch.setattr(socket, "socket", forbidden)
            with pytest.raises(OfflineError):
                collect_dol(
                    client,
                    "https://www.dol.gov/synthetic-lca.csv",
                    EmployerResolver(workspace),
                    program="LCA",
                )
        assert workspace.provenance.records() == ()


@pytest.mark.live
def test_live_dol_state_files_record_provenance(tmp_path):
    urls = [os.environ.get(f"NOPOACH_DOL_{program}_URL") for program in ("LCA", "PERM")]
    contact = os.environ.get("NOPOACH_LIVE_CONTACT")
    state = os.environ.get("NOPOACH_LIVE_STATE")
    if not all(urls) or not contact or not state:
        pytest.skip("Host must supply DOL LCA/PERM bulk URLs, contact and state")
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance, contact=contact, allowed_hosts=DOL_HOSTS
        ) as client:
            for program, url in zip(("LCA", "PERM"), urls, strict=True):
                rows = collect_dol(
                    client,
                    url,
                    EmployerResolver(workspace),
                    program=program,
                    state=state,
                )
                assert rows
                assert all(row.worksite_state == state.upper() for row in rows)
                assert all(
                    row.provenance.id in {r.id for r in workspace.provenance.records()}
                    for row in rows
                )
        print(workspace.provenance.path.read_text())
