import io
import os
import socket
from decimal import Decimal
from hashlib import sha256
from zipfile import ZipFile

import httpx
import pytest

from nopoach.collect.bls import BLS_HOSTS, collect_oews, collect_qcew
from nopoach.collect.fetch import FetchClient, OfflineError
from nopoach.store.provenance import ProvenanceLog

OEWS = (
    b"AREA,AREA_TITLE,AREA_TYPE,PRIM_STATE,NAICS,I_GROUP,OCC_CODE,"
    b"O_GROUP,TOT_EMP,EMP_PRSE,PCT_TOTAL,A_MEAN\n"
    b"""
99999,Synthetic metro,4,MN,000000,cross-industry,15-1252,detailed,12000,2.5,,100000
99999,Synthetic metro,4,MN,000000,cross-industry,15-1253,detailed,**,**,,*
88888,Synthetic other metro,4,WI,000000,cross-industry,15-1252,detailed,5000,3,,90000
99,National,1,US,541500,industry,15-1252,detailed,80000,1,20.00,110000
99,National,1,US,541500,industry,00-0000,total,400000,0.5,100.00,90000
""".lstrip(b"\n")
)
QCEW = (
    b"area_fips,own_code,industry_code,agglvl_code,size_code,year,qtr,"
    b"disclosure_code,annual_avg_emplvl,total_annual_wages\n"
    b"""
27000,5,5415,53,0,2025,A,,10000,800000000
27001,5,5415,73,0,2025,A,N,0,0
55000,5,5415,53,0,2025,A,,7000,490000000
""".lstrip(b"\n")
)


def fetch_client(tmp_path, body):
    return FetchClient(
        ProvenanceLog(tmp_path / "provenance.jsonl"),
        contact="test@example.invalid",
        allowed_hosts=BLS_HOSTS,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body)
        ),
    )


def test_oews_metro_denominator_staffing_share_and_provenance(tmp_path):
    with fetch_client(tmp_path, OEWS) as client:
        rows = collect_oews(client, "https://www.bls.gov/synthetic-oews.csv")
        assert len(rows) == 5
        metro = rows[0]
        assert metro.area_code == "99999"
        assert metro.soc_code == "15-1252"
        assert metro.employment == 12000
        assert metro.employment_relative_standard_error == Decimal("2.5")
        assert metro.annual_mean_wage == Decimal("100000")
        assert metro.naics == "000000"
        assert metro.staffing_share is None
        assert rows[1].employment is None
        assert rows[1].annual_mean_wage is None
        assert rows[3].naics == "541500"
        assert rows[3].staffing_share == Decimal("0.2")
        assert rows[4].soc_code == "00-0000"
        assert all(row.provenance == client.provenance.records()[0] for row in rows)
        assert metro.provenance.sha256 == sha256(OEWS).hexdigest()


def test_oews_state_filter_is_local_after_bulk_fetch(tmp_path):
    with fetch_client(tmp_path, OEWS) as client:
        rows = collect_oews(client, "https://www.bls.gov/synthetic.csv", state="MN")
        assert [row.soc_code for row in rows] == ["15-1252", "15-1253"]
        assert client.provenance.records()[0].sha256 == sha256(OEWS).hexdigest()


def test_industry_staffing_share_from_all_occupations_total(tmp_path):
    body = (
        b"AREA,AREA_TITLE,AREA_TYPE,PRIM_STATE,NAICS,I_GROUP,OCC_CODE,"
        b"O_GROUP,TOT_EMP,EMP_PRSE,A_MEAN\n"
        b"""
99,National,1,US,541500,industry,15-1252,detailed,80000,1,110000
99,National,1,US,541500,industry,00-0000,total,400000,0.5,90000
""".lstrip(b"\n")
    )
    with fetch_client(tmp_path, body) as client:
        rows = collect_oews(client, "https://www.bls.gov/synthetic-staffing.csv")
        assert rows[0].staffing_share == Decimal("0.2")
        assert (
            rows[0].staffing_share_method == "occupation_employment / all_occupations"
        )


def test_qcew_annual_zip_layout_state_filter_and_suppression(tmp_path):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("2025.annual.singlefile.csv", QCEW)
    with fetch_client(tmp_path, stream.getvalue()) as client:
        rows = collect_qcew(
            client, "https://data.bls.gov/synthetic.zip", state_fips="27"
        )
        assert len(rows) == 2
        assert rows[0].area_fips == "27000"
        assert rows[0].industry_code == "5415"
        assert rows[0].ownership_code == "5"
        assert rows[0].aggregation_level == "53"
        assert rows[0].year == 2025
        assert rows[0].quarter == "A"
        assert rows[0].annual_average_employment == 10000
        assert rows[0].total_annual_wages == Decimal("800000000")
        assert rows[1].annual_average_employment is None
        assert rows[1].total_annual_wages is None
        assert rows[0].provenance == client.provenance.records()[0]


def test_qcew_quarterly_layout(tmp_path):
    body = (
        b"area_fips,own_code,industry_code,agglvl_code,size_code,year,qtr,"
        b"disclosure_code,month1_emplvl,month2_emplvl,month3_emplvl,"
        b"total_qtrly_wages\n"
        b"""
27000,5,5415,53,0,2025,1,,100,110,120,3000000
""".lstrip(b"\n")
    )
    with fetch_client(tmp_path, body) as client:
        (row,) = collect_qcew(client, "https://data.bls.gov/synthetic.csv")
        assert row.monthly_employment == (100, 110, 120)
        assert row.total_quarterly_wages == Decimal("3000000")
        assert row.annual_average_employment is None


@pytest.mark.parametrize(
    "kind,column",
    [("oews", "OCC_CODE"), ("qcew", "industry_code"), ("qcew", "annual_avg_emplvl")],
)
def test_missing_bls_column_names_file_and_column(tmp_path, kind, column):
    body = OEWS if kind == "oews" else QCEW
    header = body.splitlines()[0].decode().split(",")
    missing = ",".join(name for name in header if name != column).encode() + b"\n"
    with fetch_client(tmp_path, missing) as client:
        collector = collect_oews if kind == "oews" else collect_qcew
        with pytest.raises(ValueError, match=f"synthetic-{kind}.csv.*{column}"):
            collector(client, f"https://www.bls.gov/synthetic-{kind}.csv")
        assert len(client.provenance.records()) == 1


@pytest.mark.network_off
@pytest.mark.parametrize("collector", [collect_oews, collect_qcew])
def test_bls_offline_does_not_open_socket(tmp_path, monkeypatch, collector):
    def forbidden(*args, **kwargs):
        pytest.fail("Collector opened a socket")

    with FetchClient(
        ProvenanceLog(tmp_path / "provenance.jsonl"),
        contact="test@example.invalid",
        allowed_hosts=BLS_HOSTS,
        offline=True,
    ) as client:
        monkeypatch.setattr(socket, "socket", forbidden)
        with pytest.raises(OfflineError):
            collector(client, "https://www.bls.gov/synthetic.csv")
        assert client.provenance.records() == ()


@pytest.mark.live
def test_live_bls_state_files_record_provenance(tmp_path):
    oews_url = os.environ.get("NOPOACH_BLS_OEWS_URL")
    qcew_url = os.environ.get("NOPOACH_BLS_QCEW_URL")
    contact = os.environ.get("NOPOACH_LIVE_CONTACT")
    state = os.environ.get("NOPOACH_LIVE_STATE")
    fips = os.environ.get("NOPOACH_LIVE_STATE_FIPS")
    if not all((oews_url, qcew_url, contact, state, fips)):
        pytest.skip("Host must supply BLS bulk URLs, contact, state and state FIPS")
    log = ProvenanceLog(tmp_path / "provenance.jsonl")
    with FetchClient(log, contact=contact, allowed_hosts=BLS_HOSTS) as client:
        oews = collect_oews(client, oews_url, state=state)
        qcew = collect_qcew(client, qcew_url, state_fips=fips)
        assert oews and qcew
        assert all(row.state == state.upper() for row in oews)
        assert all(row.area_fips.startswith(fips) for row in qcew)
        assert all(
            row.provenance.id in {r.id for r in log.records()} for row in [*oews, *qcew]
        )
    print(log.path.read_text())
