"""Index-selected SEC contract exhibits, collected for a whole labor market.

Uses U4's daily form-index reader without modifying its person-history forms.
Fetches filing detail listings, then only EX-10/EX-2 and primary 8-K bodies;
never downloads the full-filing daily feed. The caller supplies the full resolved
competitor set AND parents, before any local employer-list narrowing (KTD4).
All HTTP passes through U1's shared SEC fair-access budget and receipt log.
"""

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from nopoach.collect.fetch import FetchClient, FetchedResponse
from nopoach.collect.sec_people import FilingEntry
from nopoach.collect.sec_people import parse_form_index as read_people_index
from nopoach.market.define import Employer
from nopoach.signals.clauses import Clause, ClauseDocument, full_text, store_document
from nopoach.store.workspace import Workspace

logger = logging.getLogger(__name__)
FORMS = frozenset({"8-K", "10-K", "10-Q", "S-4", "DEFM14A"})


@dataclass(frozen=True)
class FilingDocument:
    form_type: str
    url: str


@dataclass(frozen=True)
class CollectionReport:
    documents: tuple[ClauseDocument, ...]
    clauses: tuple[Clause, ...]
    fetched_provenance_ids: tuple[str, ...]
    disk_bytes: int
    skipped: tuple[str, ...] = ()


def daily_index_urls(start: date, end: date) -> tuple[str, ...]:
    """List weekday daily form indexes, inclusive; SEC holidays may return 404."""
    urls = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            quarter = (current.month - 1) // 3 + 1
            urls.append(
                f"https://www.sec.gov/Archives/edgar/daily-index/{current.year}/"
                f"QTR{quarter}/form.{current:%Y%m%d}.idx"
            )
        if current == end:
            break
        current += timedelta(days=1)
    return tuple(urls)


def parse_form_index(body: bytes, market_ciks: Iterable[str]) -> list[FilingEntry]:
    """Adapt U4's fixed-column reader to U8 forms, preserving original form type.

    Only the form column is normalized for the shared parser; company/CIK/date/
    archive path parsing and local market membership remain owned by that reader.
    """
    entries: list[FilingEntry] = []
    ciks = tuple(market_ciks)
    pattern = re.compile(r"^(8-K|10-K|10-Q|S-4|DEFM14A)\s+")
    for line in body.decode("utf-8-sig").splitlines():
        match = pattern.match(line.strip())
        if match:
            normalized = pattern.sub("8-K ", line.strip(), count=1)
            entries.extend(
                replace(entry, form_type=match[1])
                for entry in read_people_index(normalized.encode(), ciks)
            )
    return entries


class _DocumentTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, tuple[str, ...]]] = []
        self.parts: list[str] = []
        self.links: list[str] = []
        self.in_row = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.parts = []
            self.links = []
        if tag == "a" and self.in_row:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.in_row:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self.in_row:
            self.rows.append((" ".join(self.parts), tuple(self.links)))
            self.in_row = False


def select_documents(
    body: bytes, listing_url: str, form_type: str
) -> tuple[FilingDocument, ...]:
    parser = _DocumentTable()
    parser.feed(body.decode("utf-8-sig", errors="replace"))
    selected = {}
    for text, links in parser.rows:
        kind = re.search(r"(?<![\w-])(EX-(?:10|2)(?:\.\d+)?|8-K)(?![\w.-])", text)
        if kind and links and (kind[1] != "8-K" or form_type == "8-K"):
            url = urljoin(listing_url, links[0])
            selected[url] = FilingDocument(kind[1], url)
    return tuple(selected.values())


def retain_response(workspace: Workspace, response: FetchedResponse) -> None:
    """Keep the original, receipt-hashed bytes in the external workspace."""
    folder = workspace.root / "filings"
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / f"{response.provenance.sha256}.raw"
    if not path.exists():
        path.write_bytes(response.body)
        path.chmod(0o600)


def retained_disk_bytes(workspace: Workspace) -> int:
    folder = workspace.root / "filings"
    return (
        sum(path.stat().st_size for path in folder.iterdir()) if folder.exists() else 0
    )


def collect_market(
    workspace: Workspace,
    fetch: FetchClient,
    *,
    index_urls: Iterable[str],
    market_employers: Mapping[str, Employer],
) -> CollectionReport:
    """Collect all resolved market employers/parents, storing each selected file.

    The mapping is CIK -> resolved Employer (including parents); no pair input
    exists. Missing holiday indexes are reported, never mistaken for empty data.
    """
    if fetch.provenance is not workspace.provenance:
        raise ValueError("Fetch client must use the workspace provenance log")
    employers = {str(int(cik)): employer for cik, employer in market_employers.items()}
    documents = []
    clauses: list[Clause] = []
    receipts = []
    skipped = []
    seen_indexes: set[str] = set()
    seen_filings: set[str] = set()
    seen_documents: set[str] = set()
    for url in index_urls:
        if url in seen_indexes:
            continue
        seen_indexes.add(url)
        try:
            index = fetch.fetch(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            logger.warning("Unavailable SEC daily index (possible holiday): %s", url)
            skipped.append(url)
            continue
        retain_response(workspace, index)
        receipts.append(index.provenance.id)
        for entry in parse_form_index(index.body, employers):
            if entry.url in seen_filings:
                continue
            seen_filings.add(entry.url)
            listing_url = entry.url.removesuffix(".txt") + "-index.html"
            listing = fetch.fetch(listing_url)
            retain_response(workspace, listing)
            receipts.append(listing.provenance.id)
            accession = entry.url.rsplit("/", 1)[-1].removesuffix(".txt")
            selected = select_documents(listing.body, listing_url, entry.form_type)
            if not selected:
                logger.warning("No U8 documents in SEC filing listing: %s", listing_url)
                skipped.append(listing_url)
            for selected_doc in selected:
                if selected_doc.url in seen_documents:
                    continue
                seen_documents.add(selected_doc.url)
                response = fetch.fetch(selected_doc.url)
                retain_response(workspace, response)
                receipts.append(response.provenance.id)
                document = ClauseDocument(
                    text=full_text(response.body),
                    provenance=response.provenance,
                    filed_on=date.fromisoformat(entry.filed_on),
                    form_type=selected_doc.form_type,
                    issuer=employers[entry.cik],
                    document_name=selected_doc.url.rsplit("/", 1)[-1],
                    accession_number=accession,
                )
                documents.append(document)
                clauses.extend(
                    store_document(workspace, document, tuple(employers.values()))
                )
    disk_bytes = retained_disk_bytes(workspace)
    logger.info(
        "SEC clause collection: %d files, %d bytes retained", len(receipts), disk_bytes
    )
    return CollectionReport(
        tuple(documents), tuple(clauses), tuple(receipts), disk_bytes, tuple(skipped)
    )
