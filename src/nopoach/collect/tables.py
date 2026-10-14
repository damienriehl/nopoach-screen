"""Shared public bulk-table readers and numeric parsing."""

import csv
import io
import posixpath
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile, is_zipfile

from nopoach.collect.fetch import FetchedResponse

_XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_EVENT_COLUMNS = {
    "DECISION_DATE",
    "CASE_DECISION_DATE",
    "EMPLOYMENT_BEGIN_DATE",
    "EMPLOYMENT_END_DATE",
    "BEGIN_DATE",
    "END_DATE",
}


class SourceSchemaError(ValueError):
    """A bulk source no longer supplies a required column."""


@dataclass(frozen=True)
class Table:
    filename: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def _table(filename: str, rows: Sequence[Sequence[str]]) -> Table:
    if not rows:
        return Table(filename, (), ())
    columns = tuple(value.strip().lstrip("\ufeff") for value in rows[0])
    return Table(
        filename,
        columns,
        tuple(
            {
                column: row[index].strip() if index < len(row) else ""
                for index, column in enumerate(columns)
            }
            for row in rows[1:]
            if any(value.strip() for value in row)
        ),
    )


def xlsx_tables(archive: ZipFile, filename: str) -> Iterator[Table]:
    epoch = date(1899, 12, 30)
    if "xl/workbook.xml" in archive.namelist():
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        properties = workbook.find(f"{_XML_NS}workbookPr")
        if properties is not None and properties.get("date1904") in {"1", "true"}:
            epoch = date(1904, 1, 1)
    strings: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(node.itertext()) for node in root.findall(f"{_XML_NS}si")]
    for sheet in sorted(archive.namelist()):
        if not sheet.startswith("xl/worksheets/") or not sheet.endswith(".xml"):
            continue
        rows: list[list[str]] = []
        root = ElementTree.fromstring(archive.read(sheet))
        for element in root.iter(f"{_XML_NS}row"):
            cells: dict[int, str] = {}
            for cell in element.findall(f"{_XML_NS}c"):
                reference = cell.attrib["r"]
                index = 0
                for letter in reference:
                    if letter.isalpha():
                        index = index * 26 + ord(letter.upper()) - ord("A") + 1
                value = cell.findtext(f"{_XML_NS}v", default="")
                if cell.get("t") == "s":
                    value = strings[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.iter(f"{_XML_NS}t")
                    )
                elif (
                    value
                    and rows
                    and index <= len(rows[0])
                    and rows[0][index - 1] in _EVENT_COLUMNS
                    and cell.get("t") in {None, "n"}
                ):
                    # XLSX stores actual date cells as days in its workbook epoch.
                    value = (epoch + timedelta(days=float(value))).isoformat()
                cells[index - 1] = value
            if cells:
                rows.append([cells.get(index, "") for index in range(max(cells) + 1)])
        yield _table(f"{filename}:{sheet}", rows)


def tables(body: bytes, filename: str) -> Iterator[Table]:
    """Read public CSV/TSV, zipped tables, and XLSX without extra dependencies."""
    stream = io.BytesIO(body)
    if is_zipfile(stream):
        with ZipFile(stream) as archive:
            if any(name.startswith("xl/worksheets/") for name in archive.namelist()):
                yield from xlsx_tables(archive, filename)
            else:
                members = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".csv", ".txt", ".xlsx"))
                ]
                if not members:
                    raise SourceSchemaError(f"{filename}: no CSV/TSV/XLSX data table")
                for name in members:
                    yield from tables(archive.read(name), f"{filename}:{name}")
        return
    text = body.decode("utf-8-sig")
    header = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in header else ","
    yield _table(filename, list(csv.reader(io.StringIO(text), delimiter=delimiter)))


def require(table: Table, columns: Sequence[str]) -> None:
    for column in columns:
        if column not in table.columns:
            raise SourceSchemaError(
                f"{table.filename}: missing required column {column}"
            )


def number(value: str, filename: str, column: str) -> Decimal | None:
    # BLS nondisclosure/top-coding symbols are unknown, never zero.
    if value.strip() in {"", "*", "**", "#", "~", "N/A"}:
        return None
    try:
        number = Decimal(value.replace(",", ""))
        if not number.is_finite():
            raise InvalidOperation
        return number
    except InvalidOperation as error:
        raise ValueError(
            f"{filename}: invalid numeric value in column {column}"
        ) from error


def count(value: str, filename: str, column: str) -> int | None:
    parsed = number(value, filename, column)
    if parsed is None:
        return None
    if parsed != parsed.to_integral_value() or parsed < 0:
        raise ValueError(f"{filename}: invalid employment count in column {column}")
    return int(parsed)


def filename(response: FetchedResponse) -> str:
    return unquote(posixpath.basename(urlsplit(response.provenance.url).path))
