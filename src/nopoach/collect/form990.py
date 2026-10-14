"""IRS Form 990 Part VII public e-file histories for a whole nonprofit market.

Source basis: IRS makes exempt-organization returns publicly available in bulk:
https://www.irs.gov/charities-non-profits/form-990-series-downloads.
The caller provides bulk CSV/JSON index URLs and a fetch client with explicitly
vetted IRS/bulk-storage hosts, never an authenticated client. Entire indexes are
retrieved before locally matching the full market's EIN set (including parents).

Part VII is a tax-year observation, not an exact appointment/departure date.
Year-precision dates retain that distinction in date_basis and tax_year. Names
are EIN-scoped identities; equal names at separate nonprofits are not merged
without independent identity evidence. Source-file receipts are linked in
imported raw_json, alongside separate normalized-row import provenance.
"""

import csv
import io
import json
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urljoin

from nopoach.collect.fetch import FetchClient
from nopoach.collect.sec_people import (
    CollectionReport,
    _child_text,
    _import_fetched,
    _row,
)
from nopoach.store.workspace import Workspace

logger = logging.getLogger(__name__)
_ROLE_TAGS = {
    "director": "IndividualTrusteeOrDirectorInd",
    "officer": "OfficerInd",
    "key_employee": "KeyEmployeeInd",
}


def _ein(value: str) -> str:
    return value.replace("-", "").strip().zfill(9)


def parse_return(body: bytes) -> list[dict[str, Any]]:
    """Read the namespaced IRS990 Part VII Section A groups, not contractors."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError("Malformed Form 990 XML") from exc
    header = next(
        (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "ReturnHeader"),
        None,
    )
    if header is None:
        raise ValueError("Form 990 has no ReturnHeader")
    ein = _child_text(header, "EIN")
    year = _child_text(header, "TaxYr")
    name = " ".join(
        filter(
            None,
            [
                _child_text(header, "BusinessNameLine1Txt"),
                _child_text(header, "BusinessNameLine2Txt"),
            ],
        )
    )
    if not ein or not name or not year.isdigit() or len(year) != 4:
        raise ValueError("Form 990 lacks filer EIN, business name, or tax year")
    rows = []
    for group in root.iter():
        if group.tag.rsplit("}", 1)[-1] != "Form990PartVIISectionAGrp":
            continue
        roles = [
            role
            for role, tag in _ROLE_TAGS.items()
            if _child_text(group, tag).lower() in {"true", "1", "x"}
        ]
        if not roles:
            continue
        person = _child_text(group, "PersonNm")
        if not person:
            raise ValueError("Form 990 Part VII person has no name")
        title = _child_text(group, "TitleTxt") or "; ".join(roles)
        row = _row(f"{_ein(ein)}:{person.casefold()}", person, name, title)
        row.update(
            {
                "employer_id": _ein(ein),
                "employer_id_type": "ein",
                "start_date": year,
                "end_date": year,
                "start_precision": "year",
                "end_precision": "year",
                "tax_year": year,
                "date_basis": "tax_year_observation",
                "role_flags": roles,
            }
        )
        rows.append(row)
    return rows


def _index_rows(body: bytes) -> list[dict[str, Any]]:
    text = body.decode("utf-8-sig")
    if text.lstrip().startswith(("{", "[")):
        data = json.loads(text)
        if isinstance(data, list):
            return [dict(row) for row in data]
        return [dict(row) for group in data.values() for row in group]
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def collect_market(
    workspace: Workspace,
    fetch: FetchClient,
    *,
    index_urls: Iterable[str],
    market_employers: Mapping[str, str],
) -> CollectionReport:
    """Read entire bulk indexes; retrieve Part VII for every market nonprofit.

    Supports IRS CSV index columns and historical FilingsYYYY JSON indexes.
    The fetch client must use this workspace's provenance log. An explicit URL
    in an index is still subject to the fetch host policy, including redirect targets.
    """
    if fetch.provenance is not workspace.provenance:
        raise ValueError("Fetch client must use the workspace provenance log")
    employers = {_ein(ein): name for ein, name in market_employers.items()}
    imports = []
    skipped = []
    fetched = []
    seen = set()
    for index_url in index_urls:
        index = fetch.fetch(index_url)
        fetched.append(index.provenance.id)
        for entry in _index_rows(index.body):
            ein = _ein(str(entry.get("EIN", "")))
            form = str(entry.get("RETURN_TYPE", entry.get("FormType", "")))
            if ein not in employers or form.upper() != "990":
                continue
            object_id = entry.get("OBJECT_ID", entry.get("ObjectId", ""))
            url = str(entry.get("URL", "")) or urljoin(
                index_url, str(object_id) + "_public.xml"
            )
            if url in seen:
                continue
            seen.add(url)
            response = fetch.fetch(url)
            fetched.append(response.provenance.id)
            try:
                rows = parse_return(response.body)
                if not rows:
                    raise ValueError(
                        "No Part VII officers/directors/key employees found"
                    )
                if any(row["employer_id"] != ein for row in rows):
                    raise ValueError("Form 990 filer EIN disagrees with bulk index")
            except ValueError as exc:
                logger.warning("Skipped Form 990 filing %s: %s", url, exc)
                skipped.append(url)
                continue
            result = _import_fetched(
                workspace,
                rows,
                response,
                source="IRS Form 990",
                namespace="irs_form990",
            )
            imports.append(result)
            for reject in result.rejects:
                logger.warning(
                    "Rejected Form 990 row %s:%s: %s",
                    url,
                    reject.row_number,
                    reject.reason,
                )
    return CollectionReport(tuple(imports), tuple(skipped), tuple(fetched))
