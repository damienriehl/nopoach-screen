"""Market-wide SEC executive/director histories through the shared fetch client.

SEC public access/reuse basis: https://www.sec.gov/files/about/webmaster-faq.htm.
The fetch client enforces declared contact and the shared ten-request/second fair-access
limit.
Callers supply daily *form* index URLs and the full resolved competitor/parent
CIK set, before any local employer narrowing. No pair identifiers are accepted.
Only Forms 3/4/5, Item 5.02 of 8-K, and DEF 14A are fetched from these listings.

Ownership XML supplies stable reporting-owner CIKs. Narrative identities are
issuer/name scoped: a name alone is not a claim of cross-employer identity.
Narrative extraction supports explicit named role/date sentences; unsupported
text is reported as skipped, not treated as evidence that no executive exists.
Filing dates describe observations, never fabricated employment start dates.
Original fetched-file receipts remain linked in the import loader's durable raw_json
rows;
The import loader separately records the normalized-row IMPORT receipt with
agency_import.
"""

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from nopoach.collect.fetch import FetchClient, FetchedResponse
from nopoach.imports.loader import ImportResult, import_records
from nopoach.imports.schema import ProvenanceClass
from nopoach.store.workspace import Workspace

logger = logging.getLogger(__name__)
FORMS = {"3", "4", "5", "8-K", "DEF 14A"}
_ARCHIVES = "https://www.sec.gov/Archives/"
_DATE = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}"
)


@dataclass(frozen=True)
class FilingEntry:
    form_type: str
    cik: str
    employer_name: str
    filed_on: str
    url: str


@dataclass(frozen=True)
class CollectionReport:
    imports: tuple[ImportResult, ...]
    skipped: tuple[str, ...]
    fetched_provenance_ids: tuple[str, ...]

    @property
    def accepted_count(self) -> int:
        return sum(result.accepted_count for result in self.imports)


def _identifier(value: str) -> str:
    return str(int(value))


def parse_form_index(
    body: bytes,
    market_ciks: Iterable[str],
    *,
    forms: Iterable[str] = ("DEF 14A", "8-K", "3", "4", "5"),
) -> list[FilingEntry]:
    """Parse the fixed-column daily form index, filtering only after retrieval."""
    ciks = {_identifier(cik) for cik in market_ciks}
    entries = []
    form_pattern = "|".join(re.escape(form) for form in forms)
    pattern = re.compile(
        rf"^({form_pattern})\s+(.+?)\s+(\d+)\s+"
        r"(\d{4}-\d{2}-\d{2})\s+(edgar/data/[^\s]+\.txt)\s*$"
    )
    for line in body.decode("utf-8-sig").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        form, company, cik, filed_on, path = match.groups()
        if _identifier(cik) in ciks:
            entries.append(
                FilingEntry(form, _identifier(cik), company, filed_on, _ARCHIVES + path)
            )
    return entries


def _child_text(root: ET.Element, tag: str) -> str:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == tag:
            return (element.text or "").strip()
    return ""


def _row(
    person_key: str, name: str, company: str, title: str, *, cik: str = ""
) -> dict[str, Any]:
    return {
        "person_key": person_key,
        "person_name": name,
        "employer_name": company,
        "employer_id": cik,
        "employer_id_type": "cik" if cik else "",
        "title": title,
        "location": "Unknown (not reported)",
        "start_date": "",
        "end_date": "",
        "start_precision": "unknown",
        "end_precision": "unknown",
    }


def _primary_document(body: bytes, form_type: str) -> str:
    text = body.decode("utf-8-sig", errors="replace")
    if re.search(r"<DOCUMENT>", text, re.I):
        documents = re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", text, re.I | re.S)
        for document in documents:
            match = re.search(r"<TYPE>\s*([^\r\n<]+)", document, re.I)
            if match and match[1].strip() == form_type:
                content = re.search(r"<TEXT>(.*?)</TEXT>", document, re.I | re.S)
                if content:
                    return content[1].strip()
        raise ValueError("Submission has no matching primary document")
    return text


def _ownership(text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("Malformed SEC ownership XML") from exc
    cik = _identifier(_child_text(root, "issuerCik"))
    company = _child_text(root, "issuerName")
    if not company:
        raise ValueError("Ownership filing has no issuer name")
    rows = []
    for owner in root.iter():
        if owner.tag.rsplit("}", 1)[-1] != "reportingOwner":
            continue
        name = _child_text(owner, "rptOwnerName")
        key = _identifier(_child_text(owner, "rptOwnerCik"))
        roles = []
        if _child_text(owner, "isOfficer").lower() in {"1", "true"}:
            roles.append(_child_text(owner, "officerTitle") or "Officer")
        if _child_text(owner, "isDirector").lower() in {"1", "true"}:
            roles.append("Director")
        if not name:
            raise ValueError("Ownership filing has no reporting-owner name")
        if roles:
            row = _row(key, name, company, "; ".join(roles), cik=cik)
            row["role_flags"] = roles
            row["reported_period"] = _child_text(root, "periodOfReport")
            row["date_basis"] = "filing_observation"
            rows.append(row)
    return rows


class _Blocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "tr", "br", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "tr", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _blocks(text: str) -> list[str]:
    parser = _Blocks()
    parser.feed(text)
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in "".join(parser.parts).splitlines()
        if line.strip()
    ]


def _day(value: str) -> str:
    return datetime.strptime(re.sub(r"\s+", " ", value), "%B %d, %Y").date().isoformat()


def _departures(text: str, cik: str, company: str) -> list[dict[str, Any]]:
    text = " ".join(_blocks(text))
    item = re.search(r"Item\s+5\.02\b(.*?)(?=Item\s+\d+\.\d+\b|$)", text, re.I)
    if not item:
        return []
    rows = []
    pattern = re.compile(
        rf"(?:On\s+(?P<action_date>{_DATE}),?\s+)?"
        r"(?P<name>[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){1,5})\s+"
        r"(?P<action>resigned|retired|departed|was terminated|was appointed|"
        r"was elected|was named)\s+(?:from\s+)?(?:his\s+|her\s+|the\s+)?"
        r"(?:position\s+as\s+|role\s+as\s+|as\s+)?"
        r"(?P<title>.+?)(?=\s+(?:of|at)\s+|,|\.\s|\.$)",
    )
    for match in pattern.finditer(item[1]):
        name = match["name"]
        row = _row(
            f"{cik}:{name.casefold()}", name, company, match["title"].strip(), cik=cik
        )
        # Keep the effective date within this event sentence, rather than
        # accidentally borrowing a later executive's event date.
        tail = item[1][match.end() :]
        sentence_end = re.search(r"\.(?:\s|$)", tail)
        sentence = tail[: sentence_end.start()] if sentence_end else tail
        effective = re.search(rf"effective\s+(?:as of\s+)?({_DATE})", sentence, re.I)
        event_date = effective[1] if effective else match["action_date"]
        field = (
            "start"
            if match["action"] in {"was appointed", "was elected", "was named"}
            else "end"
        )
        if event_date:
            row[f"{field}_date"] = _day(event_date)
            row[f"{field}_precision"] = "day"
        row["date_basis"] = "item_5_02_event"
        rows.append(row)
    return rows


def _biographies(text: str, cik: str, company: str) -> list[dict[str, Any]]:
    rows = []
    pattern = re.compile(
        r"\b(?:as|been)\s+(?:an?\s+)?(?P<title>[^.;]+?)\s+(?:at|of|for|with)\s+"
        r"(?P<company>[^.;]+?)\s+(?:"
        r"from\s+(?P<start>\d{4})\s+(?:to|through|until)\s+(?P<end>\d{4})|"
        r"since\s+(?P<since>\d{4})|"
        r"\((?P<paren_start>\d{4})\s*[-–]\s*(?P<paren_end>\d{4})\))"
    )
    for block in _blocks(text):
        person = re.match(
            r"(?P<name>[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){1,5})"
            r"(?:,\s*\d+,?)?\s+(?:has served|served|was|is|has been)",
            block,
        )
        if not person:
            continue
        name = person["name"]
        for match in pattern.finditer(block):
            employer = match["company"].strip()
            if employer.casefold() in {"our company", "the company", "us"}:
                employer = company
            row = _row(
                f"{cik}:{name.casefold()}",
                name,
                employer,
                match["title"].strip(),
                cik=cik if employer == company else "",
            )
            row["start_date"] = match["start"] or match["since"] or match["paren_start"]
            row["start_precision"] = "year"
            row["end_date"] = match["end"] or match["paren_end"] or ""
            row["end_precision"] = "year" if row["end_date"] else "unknown"
            row["date_basis"] = "proxy_biography"
            rows.append(row)
    return rows


def parse_filing(
    body: bytes, *, form_type: str, filed_on: str, cik: str, employer_name: str
) -> list[dict[str, Any]]:
    """Extract canonical import rows, preserving source-specific observations."""
    if form_type not in FORMS:
        raise ValueError("Unsupported SEC person-history form")
    text = _primary_document(body, form_type)
    if form_type in {"3", "4", "5"}:
        rows = _ownership(text)
    elif form_type == "8-K":
        rows = _departures(text, _identifier(cik), employer_name)
    else:
        rows = _biographies(text, _identifier(cik), employer_name)
    for row in rows:
        row["filed_on"] = filed_on
        row["form_type"] = form_type
    return rows


def _import_fetched(
    workspace: Workspace,
    rows: Sequence[Mapping[str, Any]],
    response: FetchedResponse,
    *,
    source: str,
    namespace: str,
) -> ImportResult:
    receipt = response.provenance
    evidence = {
        "source_provenance_id": receipt.id,
        "source_url": receipt.url,
        "source_sha256": receipt.sha256,
        "source_collected_at": receipt.collected_at.isoformat(),
        "source_collection_method": receipt.collection_method,
        "source": source,
    }
    return import_records(
        workspace,
        [dict(row, **evidence) for row in rows],
        source=source,
        provenance_class=ProvenanceClass.GOVERNMENT_ADMINISTRATIVE,
        person_key_namespace=namespace,
    )


def collect_market(
    workspace: Workspace,
    fetch: FetchClient,
    *,
    index_urls: Iterable[str],
    market_employers: Mapping[str, str],
) -> CollectionReport:
    """Fetch daily form listings once and every matching market/parent filing.

    The supplied fetch client must use this workspace's provenance log. Market
    completeness is owned by the market-definition caller, not a pair screen.
    """
    if fetch.provenance is not workspace.provenance:
        raise ValueError("Fetch client must use the workspace provenance log")
    imports = []
    skipped = []
    fetched = []
    seen = set()
    employers = {_identifier(cik): name for cik, name in market_employers.items()}
    for url in index_urls:
        index = fetch.fetch(url)
        fetched.append(index.provenance.id)
        for entry in parse_form_index(index.body, employers):
            if entry.url in seen:
                continue
            seen.add(entry.url)
            response = fetch.fetch(entry.url)
            fetched.append(response.provenance.id)
            try:
                rows = parse_filing(
                    response.body,
                    form_type=entry.form_type,
                    filed_on=entry.filed_on,
                    cik=entry.cik,
                    employer_name=employers[entry.cik],
                )
                if not rows:
                    raise ValueError("No supported person-role observations found")
                # Insider records are indexed under issuers and reporting owners.
                # The XML issuer is authoritative for local market membership.
                if entry.form_type in {"3", "4", "5"}:
                    rows = [row for row in rows if row["employer_id"] in employers]
                    if not rows:
                        raise ValueError("Ownership issuer is outside the market")
            except ValueError as exc:
                logger.warning("Skipped SEC filing %s: %s", entry.url, exc)
                skipped.append(entry.url)
                continue
            result = _import_fetched(
                workspace, rows, response, source="SEC EDGAR", namespace="sec_people"
            )
            imports.append(result)
            for reject in result.rejects:
                logger.warning(
                    "Rejected SEC row %s:%s: %s",
                    entry.url,
                    reject.row_number,
                    reject.reason,
                )
    return CollectionReport(tuple(imports), tuple(skipped), tuple(fetched))
