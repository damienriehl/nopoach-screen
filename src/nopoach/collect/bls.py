"""BLS OEWS/QCEW bulk baselines; market counts and industry staffing shares.

Sources: https://www.bls.gov/oes/tables.htm and
https://www.bls.gov/cew/downloadable-data-files.htm
Public reuse policy: https://www.bls.gov/bls/linksite.htm
Pass BLS_HOSTS via FetchClient.allowed_hosts; no separate network boundary.
Whole files are fetched before any state filter. Geography, NAICS, aggregation,
ownership and size codes remain separate to avoid adding overlapping totals.
These aggregates do not provide employer headcount or geography apportionment;
they are inputs to workforce estimates, not employer workforce estimates themselves.
"""

from dataclasses import dataclass, replace
from decimal import Decimal

from nopoach.collect.fetch import FetchClient, FetchedResponse
from nopoach.collect.tables import count, filename, number, require, tables
from nopoach.store.provenance import ProvenanceRecord

BLS_HOSTS = {
    "www.bls.gov": 1.0,
    "bls.gov": 1.0,
    "download.bls.gov": 1.0,
    "data.bls.gov": 1.0,
}


@dataclass(frozen=True)
class OewsRecord:
    area_code: str
    area_title: str
    area_type: str
    state: str
    naics: str
    industry_group: str
    soc_code: str
    occupation_group: str
    employment: int | None
    employment_relative_standard_error: Decimal | None
    annual_mean_wage: Decimal | None
    staffing_share: Decimal | None
    staffing_share_method: str | None
    source_file: str
    provenance: ProvenanceRecord


def parse_oews(
    response: FetchedResponse, *, state: str | None = None
) -> list[OewsRecord]:
    """Retain occupation denominators and staffing shares (not shares of an employer).

    EMP_PRSE is the published percent relative standard error; it is not a
    confidence interval. PCT_TOTAL is percent of industry employment, divided
    by 100 here. If absent, use the matching all-occupation employment total.
    Suppressed cells remain None, including top-coded wages marked '#'.
    """
    records = []
    for table in tables(response.body, filename(response)):
        require(
            table,
            [
                "AREA",
                "AREA_TITLE",
                "AREA_TYPE",
                "PRIM_STATE",
                "NAICS",
                "I_GROUP",
                "OCC_CODE",
                "O_GROUP",
                "TOT_EMP",
                "EMP_PRSE",
                "A_MEAN",
            ],
        )
        for row in table.rows:
            if state and row["PRIM_STATE"].upper() != state.upper():
                continue
            pct = number(row.get("PCT_TOTAL", ""), table.filename, "PCT_TOTAL")
            industry = row["NAICS"] not in {"000000", "00000", "0000", "0"}
            share = pct / 100 if pct is not None and industry else None
            records.append(
                OewsRecord(
                    row["AREA"],
                    row["AREA_TITLE"],
                    row["AREA_TYPE"],
                    row["PRIM_STATE"].upper(),
                    row["NAICS"],
                    row["I_GROUP"],
                    row["OCC_CODE"],
                    row["O_GROUP"],
                    count(row["TOT_EMP"], table.filename, "TOT_EMP"),
                    number(row["EMP_PRSE"], table.filename, "EMP_PRSE"),
                    number(row["A_MEAN"], table.filename, "A_MEAN"),
                    share,
                    "PCT_TOTAL / 100" if share is not None else None,
                    table.filename,
                    response.provenance,
                )
            )
    totals = {
        (row.area_code, row.area_type, row.naics, row.industry_group): row.employment
        for row in records
        if row.soc_code == "00-0000"
    }
    for index, record in enumerate(records):
        total = totals.get(
            (record.area_code, record.area_type, record.naics, record.industry_group)
        )
        if (
            record.staffing_share is None
            and record.employment is not None
            and total
            and record.naics not in {"000000", "00000", "0000", "0"}
        ):
            records[index] = replace(
                record,
                staffing_share=Decimal(record.employment) / total,
                staffing_share_method="occupation_employment / all_occupations",
            )
    return records


def collect_oews(
    client: FetchClient, url: str, *, state: str | None = None
) -> list[OewsRecord]:
    """Retrieve one whole OEWS table/archive, including industry staffing tables."""
    return parse_oews(client.fetch(url), state=state)


@dataclass(frozen=True)
class QcewRecord:
    area_fips: str
    ownership_code: str
    industry_code: str
    aggregation_level: str
    size_code: str
    year: int
    quarter: str
    disclosure_code: str
    annual_average_employment: int | None
    total_annual_wages: Decimal | None
    monthly_employment: tuple[int | None, int | None, int | None] | None
    total_quarterly_wages: Decimal | None
    source_file: str
    provenance: ProvenanceRecord


def parse_qcew(
    response: FetchedResponse, *, state_fips: str | None = None
) -> list[QcewRecord]:
    """Keep annual/quarterly counts in their original area/ownership/industry cells."""
    records = []
    for table in tables(response.body, filename(response)):
        require(
            table,
            [
                "area_fips",
                "own_code",
                "industry_code",
                "agglvl_code",
                "size_code",
                "year",
                "qtr",
                "disclosure_code",
            ],
        )
        annual = (
            "annual_avg_emplvl" in table.columns
            or "total_annual_wages" in table.columns
        )
        if annual:
            require(table, ["annual_avg_emplvl", "total_annual_wages"])
        else:
            require(
                table,
                [
                    "month1_emplvl",
                    "month2_emplvl",
                    "month3_emplvl",
                    "total_qtrly_wages",
                ],
            )
        for row in table.rows:
            if state_fips and not row["area_fips"].startswith(state_fips):
                continue
            disclosed = row["disclosure_code"] != "N"
            year = count(row["year"], table.filename, "year")
            if year is None:
                raise ValueError(f"{table.filename}: missing value in column year")
            monthly: tuple[int | None, int | None, int | None] | None = None
            if not annual:
                months = [
                    count(row[column], table.filename, column) if disclosed else None
                    for column in ("month1_emplvl", "month2_emplvl", "month3_emplvl")
                ]
                monthly = (months[0], months[1], months[2])
            records.append(
                QcewRecord(
                    row["area_fips"],
                    row["own_code"],
                    row["industry_code"],
                    row["agglvl_code"],
                    row["size_code"],
                    year,
                    row["qtr"],
                    row["disclosure_code"],
                    count(row["annual_avg_emplvl"], table.filename, "annual_avg_emplvl")
                    if annual and disclosed
                    else None,
                    number(
                        row["total_annual_wages"], table.filename, "total_annual_wages"
                    )
                    if annual and disclosed
                    else None,
                    monthly,
                    number(
                        row["total_qtrly_wages"], table.filename, "total_qtrly_wages"
                    )
                    if not annual and disclosed
                    else None,
                    table.filename,
                    response.provenance,
                )
            )
    return records


def collect_qcew(
    client: FetchClient, url: str, *, state_fips: str | None = None
) -> list[QcewRecord]:
    """Fetch a whole QCEW CSV/archive and select a state's cells locally."""
    return parse_qcew(client.fetch(url), state_fips=state_fips)
