"""Whole-document franchise parsing and host-configured public portal adapters.

TODO(U8_WISCONSIN_TERMS): host records permitted listing/document retrieval.
TODO(U8_MINNESOTA_TERMS): host records permitted listing/document retrieval.
TODO(U8_CALIFORNIA_TERMS): host records permitted listing/document retrieval.
TODO(U8_INDIANA_TERMS): host records permitted listing/document retrieval.

No candidate portal's permission or HTML shape has been verified offline.
Adapters default off and require an explicit dated terms receipt. The host must
configure listing URLs, an actual listing decoder, and permitted FetchClient
hosts/rates after checking terms. No login, account or platform scraping exists.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin

from pypdf import PdfReader

from nopoach.collect.edgar_index import (
    CollectionReport,
    retain_response,
    retained_disk_bytes,
)
from nopoach.collect.fetch import FetchClient, FetchPolicyError
from nopoach.market.define import Employer
from nopoach.signals.clauses import Clause, ClauseDocument, full_text, store_document
from nopoach.store.provenance import ProvenanceRecord
from nopoach.store.workspace import Workspace

PORTALS = frozenset({"wisconsin", "minnesota", "california", "indiana"})


@dataclass(frozen=True)
class PortalConfig:
    portal: str
    terms_checked: bool = False
    terms_url: str = ""
    terms_checked_on: date | None = None


@dataclass(frozen=True)
class FDDEntry:
    brand: str
    url: str
    filed_on: date | None = None


@dataclass(frozen=True)
class PortalListing:
    entries: tuple[FDDEntry, ...]
    next_urls: tuple[str, ...] = ()


class _ListingLinks(HTMLParser):
    """Small adapter contract; the host supplies a decoder for actual portals."""

    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.entries: list[FDDEntry] = []
        self.next_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        href = attributes.get("href")
        if tag != "a" or not href:
            return
        url = urljoin(self.url, href)
        if "next" in (attributes.get("rel") or "").split():
            self.next_urls.append(url)
        brand = attributes.get("data-brand")
        if brand:
            filed = attributes.get("data-filed-on")
            self.entries.append(
                FDDEntry(brand, url, date.fromisoformat(filed) if filed else None)
            )


def parse_listing(body: bytes, url: str) -> PortalListing:
    """Reference HTML listing format; not a claim about any state portal."""
    parser = _ListingLinks(url)
    parser.feed(body.decode("utf-8-sig", errors="replace"))
    return PortalListing(tuple(parser.entries), tuple(parser.next_urls))


def parse_document(
    body: bytes,
    *,
    provenance: ProvenanceRecord,
    filed_on: date,
    franchisor: Employer,
    brand: str,
    document_name: str = "",
) -> ClauseDocument:
    """Extract all pages/attachments of a single FDD, never only Items 15/17.

    HTML/plaintext uses the shared full-text parser. Public portal PDFs use
    pypdf for in-process text extraction. Image-only PDFs fail visibly rather
    than pretending no clause was present. OCR is not part of this unit; the
    host can supply lawfully extracted full text instead.
    """
    if body.startswith(b"%PDF-"):
        reader = PdfReader(BytesIO(body))
        text = "\n\n".join(page.extract_text() for page in reader.pages)
        if not text.strip():
            raise ValueError("FDD PDF has no extractable text; requires host OCR")
    else:
        text = full_text(body)
    return ClauseDocument(
        text=text,
        provenance=provenance,
        filed_on=filed_on,
        form_type="FDD",
        issuer=franchisor,
        document_name=document_name,
        brand=brand,
    )


class PortalAdapter:
    """One small configured adapter per candidate, disabled until terms checked."""

    def __init__(
        self,
        config: PortalConfig,
        *,
        listing_parser: Callable[[bytes, str], PortalListing] = parse_listing,
    ) -> None:
        if config.portal not in PORTALS:
            raise ValueError("Unknown candidate FDD portal")
        self.config = config
        self.listing_parser = listing_parser

    def collect_market(
        self,
        workspace: Workspace,
        fetch: FetchClient,
        *,
        listing_urls: Iterable[str],
        brands: Mapping[str, tuple[Employer, Sequence[Employer]]],
        filed_on: date,
    ) -> CollectionReport:
        """Traverse full portal listings, selecting all market brands locally.

        filed_on is the host-supplied disclosure date when absent from listings;
        it is never silently replaced by the collection date. Attached contracts
        inside each fetched FDD are parsed in full. If a portal supplies separate
        attachment links, its host-verified decoder includes them as FDD entries.
        """
        if not (
            self.config.terms_checked
            and self.config.terms_url
            and self.config.terms_checked_on
        ):
            raise FetchPolicyError(
                f"{self.config.portal} portal terms check not recorded"
            )
        if fetch.provenance is not workspace.provenance:
            raise ValueError("Fetch client must use the workspace provenance log")
        pending = list(listing_urls)
        seen = set()
        entries: list[FDDEntry] = []
        receipts = []
        while pending:
            url = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            response = fetch.fetch(url)
            retain_response(workspace, response)
            receipts.append(response.provenance.id)
            listing = self.listing_parser(response.body, url)
            entries.extend(listing.entries)
            pending.extend(listing.next_urls)
        documents = []
        clauses: list[Clause] = []
        seen_documents = set()
        employers = tuple(brand[0] for brand in brands.values())
        for entry in entries:
            if entry.brand not in brands or entry.url in seen_documents:
                continue
            seen_documents.add(entry.url)
            response = fetch.fetch(entry.url)
            retain_response(workspace, response)
            receipts.append(response.provenance.id)
            franchisor, franchisees = brands[entry.brand]
            document = parse_document(
                response.body,
                provenance=response.provenance,
                filed_on=entry.filed_on or filed_on,
                franchisor=franchisor,
                brand=entry.brand,
                document_name=entry.url.rsplit("/", 1)[-1],
            )
            documents.append(document)
            clauses.extend(
                store_document(workspace, document, employers, franchisees=franchisees)
            )
        return CollectionReport(
            tuple(documents),
            tuple(clauses),
            tuple(receipts),
            retained_disk_bytes(workspace),
        )
