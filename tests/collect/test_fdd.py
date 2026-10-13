import ast
import inspect
import socket
from datetime import date
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from nopoach.collect import fdd
from nopoach.collect.fdd import PortalAdapter, PortalConfig, parse_document
from nopoach.collect.fetch import FetchClient, FetchPolicyError
from nopoach.market.define import Employer
from nopoach.signals.clauses import extract_clauses
from nopoach.store.workspace import Workspace

FIXTURES = Path(__file__).parents[1] / "fixtures" / "clauses"
FRANCHISOR = Employer("brand", "Synthetic Brand")
FRANCHISEES = tuple(Employer(str(i), f"Synthetic Franchise {i}") for i in range(3))


def test_attached_agreement_is_read_and_all_franchisee_pairs_are_linked(tmp_path):
    body = (FIXTURES / "franchise.html").read_bytes()
    with Workspace.open(tmp_path / "data") as workspace:
        receipt = workspace.provenance.append(
            source="synthetic-fdd",
            url="https://portal.example.invalid/fdd.html",
            method="GET",
            body=body,
        )
        doc = parse_document(
            body,
            provenance=receipt,
            filed_on=date(2020, 1, 1),
            franchisor=FRANCHISOR,
            brand="Synthetic Brand",
        )
        (clause,) = extract_clauses(doc, (FRANCHISOR,), franchisees=FRANCHISEES)
        assert "Exhibit A: Franchise Agreement" in doc.text
        assert clause.binding == "brand_wide"
        assert clause.brand == "Synthetic Brand"
        assert clause.employer_ids == ("0", "1", "2")
        assert clause.pairs == (("0", "1"), ("0", "2"), ("1", "2"))
        assert clause.duration == "during the term of this agreement"


@pytest.mark.parametrize("portal", ["wisconsin", "minnesota", "california", "indiana"])
@pytest.mark.network_off
def test_portals_default_off_until_host_records_terms_check(tmp_path, portal):
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance, contact="fixture@example.invalid"
        ) as fetch:
            adapter = PortalAdapter(PortalConfig(portal))
            with pytest.raises(FetchPolicyError, match="terms"):
                adapter.collect_market(
                    workspace,
                    fetch,
                    listing_urls=["https://portal.example.invalid/list"],
                    brands={"Synthetic Brand": (FRANCHISOR, FRANCHISEES)},
                    filed_on=date(2020, 1, 1),
                )
        assert workspace.provenance.records() == ()


@pytest.mark.network_off
def test_enabled_adapter_reads_full_listing_before_local_brand_selection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        socket, "socket", lambda *a, **kw: pytest.fail("real socket opened")
    )
    bodies = {
        "https://portal.example.invalid/list": (
            b'<a data-brand="Synthetic Brand" href="/fdd.html">FDD</a>'
            b'<a data-brand="Other" href="/other.html">Other</a>'
            b'<a rel="next" href="/page2">Next</a>'
        ),
        "https://portal.example.invalid/page2": (
            b'<a data-brand="Synthetic Brand" href="/second.html">FDD</a>'
        ),
        "https://portal.example.invalid/fdd.html": (
            FIXTURES / "franchise.html"
        ).read_bytes(),
        "https://portal.example.invalid/second.html": b"No employee restriction here.",
    }
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=bodies[str(request.url)])

    with Workspace.open(tmp_path / "data") as workspace:
        seed = workspace.provenance.append(
            source="synthetic", url="", method="IMPORT", body=b"seed"
        )
        for employer in (FRANCHISOR, *FRANCHISEES):
            workspace.connection.execute(
                "INSERT INTO employers VALUES (?, ?, NULL, ?)",
                [employer.id, employer.name, seed.id],
            )
        with FetchClient(
            workspace.provenance,
            contact="fixture@example.invalid",
            allowed_hosts={"portal.example.invalid": 10000},
            transport=httpx.MockTransport(handler),
        ) as fetch:
            report = PortalAdapter(
                PortalConfig(
                    "wisconsin",
                    terms_checked=True,
                    terms_url="https://portal.example.invalid/terms",
                    terms_checked_on=date(2026, 10, 13),
                )
            ).collect_market(
                workspace,
                fetch,
                listing_urls=["https://portal.example.invalid/list"],
                brands={"Synthetic Brand": (FRANCHISOR, FRANCHISEES)},
                filed_on=date(2020, 1, 1),
            )
        assert set(seen) == set(bodies)
        assert len(report.clauses) == 1
        assert len(report.documents) == 2
        assert len(report.fetched_provenance_ids) == 4
        assert report.disk_bytes > 0


def synthetic_pdf(pages):
    """Small synthetic text PDF; the second page models an attached contract."""
    writer = PdfWriter()
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        if not lines:
            continue
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        operators = ["BT /F1 10 Tf 40 740 Td 14 TL"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            operators.append(f"({escaped}) Tj T*")
        stream = DecodedStreamObject()
        stream.set_data(("\n".join([*operators, "ET"]) + "\n").encode())
        page.replace_contents(stream)
    body = BytesIO()
    writer.write(body)
    return body.getvalue()


def test_pdf_attachment_with_person_employed_wording_is_brand_wide(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", "")
    body = synthetic_pdf(
        [
            ["Item 15: Participation. Item 17: See attached franchise agreement."],
            [
                "Exhibit A: Franchise Agreement",
                "You will not employ or seek to employ any person who is at the time",
                "employed by us or by any other franchisee during the term "
                "of this agreement.",
            ],
        ]
    )
    with Workspace.open(tmp_path / "data") as workspace:
        receipt = workspace.provenance.append(
            source="synthetic-fdd",
            url="https://portal.example.invalid/fdd.pdf",
            method="GET",
            body=body,
        )
        doc = parse_document(
            body,
            provenance=receipt,
            filed_on=date(2020, 1, 1),
            franchisor=FRANCHISOR,
            brand="Synthetic Brand",
        )
        (clause,) = extract_clauses(doc, (FRANCHISOR,), franchisees=FRANCHISEES)
        assert clause.binding == "brand_wide"
        assert clause.pairs == (("0", "1"), ("0", "2"), ("1", "2"))
        assert clause.scope == "all employees"
        assert "Item 15" in doc.text and "Exhibit A" in doc.text
        assert "You will not employ or seek to employ any person" in doc.text
        assert doc.provenance == receipt
        assert doc.filed_on == date(2020, 1, 1)
        assert doc.form_type == "FDD"
        assert doc.issuer == FRANCHISOR
        assert doc.brand == "Synthetic Brand"


def test_fdd_module_has_no_subprocess_or_pdftotext_dependency():
    source = inspect.getsource(fdd)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all(alias.name != "subprocess" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "subprocess"
    assert "pdftotext" not in source


def test_empty_text_pdf_requires_host_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    body = synthetic_pdf([[]])
    with Workspace.open(tmp_path / "data") as workspace:
        receipt = workspace.provenance.append(
            source="synthetic-fdd",
            url="https://portal.example.invalid/empty.pdf",
            method="GET",
            body=body,
        )
        with pytest.raises(
            ValueError,
            match="^FDD PDF has no extractable text; requires host OCR$",
        ):
            parse_document(
                body,
                provenance=receipt,
                filed_on=date(2020, 1, 1),
                franchisor=FRANCHISOR,
                brand="Synthetic Brand",
            )
