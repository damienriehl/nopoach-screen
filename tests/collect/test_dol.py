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


@pytest.mark.parametrize("program", ["LCA", "PERM"])
def test_dol_dates_and_parsed_records_persist(tmp_path, program):
    from datetime import date

    from nopoach.collect.dol import load_dol_records

    columns = LCA_COLUMNS + [
        "DECISION_DATE",
        "EMPLOYMENT_BEGIN_DATE",
        "EMPLOYMENT_END_DATE",
    ]
    rows = [LCA_ROWS[0] + ["01/15/2020", "2020-02-01", "2022-01-31"]]
    if program == "PERM":
        columns = [
            c.replace("WAGE_RATE_OF_PAY", "WAGE_OFFER").replace(
                "WAGE_UNIT_OF_PAY", "WAGE_OFFER_UNIT_OF_PAY"
            )
            for c in columns
        ]
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts=DOL_HOSTS,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=csv_bytes(columns, rows))
            ),
        ) as client:
            parsed = collect_dol(
                client,
                "https://www.dol.gov/synthetic.csv",
                EmployerResolver(workspace),
                program=program,
            )
        assert parsed[0].decision_date == date(2020, 1, 15)
        assert parsed[0].employment_begin_date == date(2020, 2, 1)
        assert parsed[0].employment_end_date == date(2022, 1, 31)
        assert load_dol_records(workspace) == tuple(parsed)


@pytest.mark.parametrize("epoch_flag,serial", [("0", "43845"), ("1", "42383")])
def test_xlsx_numeric_decision_date_persists_with_workbook_epoch(
    tmp_path, epoch_flag, serial
):
    from datetime import date
    from xml.etree import ElementTree

    from nopoach.collect.dol import load_dol_records

    original = xlsx_bytes(LCA_COLUMNS + ["DECISION_DATE"], [LCA_ROWS[0] + ["date"]])
    stream = io.BytesIO()
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(io.BytesIO(original)) as source, ZipFile(stream, "w") as output:
        for name in source.namelist():
            body = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(body)
                cell = next(c for c in root.iter(f"{namespace}c") if c.get("r") == "K2")
                cell.attrib.pop("t")
                cell.find(f"{namespace}v").text = serial
                body = ElementTree.tostring(root)
            output.writestr(name, body)
        output.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<workbookPr date1904="{epoch_flag}"/></workbook>',
        )
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts=DOL_HOSTS,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=stream.getvalue())
            ),
        ) as client:
            records = collect_dol(
                client,
                "https://www.dol.gov/synthetic-lca.xlsx",
                EmployerResolver(workspace),
                program="LCA",
            )
        assert records[0].decision_date == date(2020, 1, 15)
        assert load_dol_records(workspace)[0].decision_date == date(2020, 1, 15)
