from dataclasses import replace
from datetime import date
from decimal import Decimal

import httpx
import pytest

from nopoach.collect.bls import parse_oews
from nopoach.collect.dol import parse_dol
from nopoach.collect.fetch import FetchClient
from nopoach.market.coverage import (
    Headcount,
    MarketWorker,
    WorkforceEstimate,
    coverage_report,
    estimate_workforce,
)
from nopoach.market.define import Employer, Geography, Market
from nopoach.resolve.employers import EmployerResolver
from nopoach.store.workspace import Workspace


def test_fixed_headcount_chain_and_unknown():
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2024, 2024)
    imported = Headcount(500, 480, 520, "agency imported headcounts")
    entered = Headcount(300, 250, 350, "lawyer entered")
    assert (
        estimate_workforce(
            "a", 2024, market, imported=imported, lawyer=entered
        ).headcount
        == imported
    )
    assert estimate_workforce("a", 2024, market, lawyer=entered).headcount == entered
    assert estimate_workforce("a", 2024, market).headcount is None


def test_company_count_with_bls_staffing_and_geography_has_source_and_band(tmp_path):
    body = (
        b"AREA,AREA_TITLE,AREA_TYPE,PRIM_STATE,NAICS,I_GROUP,OCC_CODE,"
        b"O_GROUP,TOT_EMP,EMP_PRSE,PCT_TOTAL,A_MEAN\n"
        b"99,National,1,US,541500,industry,15-1252,detailed,80000,1,20,110000\n"
        b"99,National,1,US,541500,industry,15-1253,detailed,40000,1,10,110000\n"
    )
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts={"fixture.invalid": 10},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=body)
            ),
        ) as client:
            staffing = parse_oews(client.fetch("https://fixture.invalid/staffing.csv"))
    market = Market(
        frozenset({"15-1252", "15-1253"}), Geography("metro", "99999"), 2024, 2024
    )
    result = estimate_workforce(
        "a",
        2024,
        market,
        company=Headcount(
            10000, 10000, 10000, "SEC 10-K 2024 human-capital disclosure"
        ),
        staffing=staffing,
        industry_code="541500",
        geography_share=Decimal("0.25"),
        geography_source="lawyer supplied worksite allocation",
    )
    assert result.headcount.value == pytest.approx(750)
    assert result.headcount.lower == pytest.approx(375)
    assert result.headcount.upper == pytest.approx(1125)
    assert "SEC 10-K" in result.headcount.source
    assert "staffing.csv" in result.headcount.source
    assert "worksite allocation" in result.headcount.source
    assert "50%" in result.headcount.uncertainty_basis
    assert "confidence interval" in result.headcount.uncertainty_basis


def test_lca_fallback_filters_occupation_geography_and_duplicates(tmp_path):
    body = (
        b"CASE_NUMBER,EMPLOYER_NAME,SOC_CODE,WORKSITE_CITY,WORKSITE_STATE,"
        b"WORKSITE_POSTAL_CODE,WAGE_RATE_OF_PAY_FROM,WAGE_RATE_OF_PAY_TO,"
        b"WAGE_UNIT_OF_PAY,TOTAL_WORKER_POSITIONS\n"
        b"X,Synthetic A,15-1252,Fixture,MN,00000,100000,,Year,12\n"
        b"Y,Synthetic A,15-1252,Fixture,WI,00000,100000,,Year,99\n"
        b"Z,Synthetic A,29-1141,Fixture,MN,00000,100000,,Year,99\n"
    )
    with Workspace.open(tmp_path / "data") as workspace:
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts={"fixture.invalid": 10},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=body)
            ),
        ) as client:
            records = parse_dol(
                client.fetch("https://fixture.invalid/lca.csv"),
                EmployerResolver(workspace),
                program="LCA",
            )
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2024, 2024)
    result = estimate_workforce(
        records[0].employer_id, 2024, market, lca=records + [records[0]]
    )
    assert result.headcount.value == 12
    assert result.headcount.lower == 0
    assert result.headcount.upper is None
    assert "LCA footprint" in result.headcount.source
    assert "not a workforce census" in result.headcount.uncertainty_basis
    unknown_metro = replace(market, geography=Geography("metro", "99999"))
    assert (
        estimate_workforce(
            records[0].employer_id, 2024, unknown_metro, lca=records
        ).headcount
        is None
    )


def test_annual_coverage_deduplicates_workers_filters_market_and_uses_weighted_total():
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2023, 2024)
    employers = [Employer("a", "Synthetic A"), Employer("b", "Synthetic B")]
    estimates = [
        WorkforceEstimate(
            e.id,
            y,
            market.occupation_codes,
            market.geography,
            Headcount(
                100 if e.id == "a" else 300,
                100 if e.id == "a" else 300,
                100 if e.id == "a" else 300,
                f"headcount {y}",
            ),
        )
        for e in employers
        for y in (2023, 2024)
    ]
    workers = [
        MarketWorker(f"a-{i}", "a", "15-1252", market.geography, 2024) for i in range(8)
    ]
    workers += [
        MarketWorker(f"b-{i}", "b", "15-1252", market.geography, 2024)
        for i in range(150)
    ]
    workers += [
        workers[0],
        replace(workers[0], person_id="elsewhere", geography=Geography("state", "WI")),
        replace(workers[0], person_id="wrong-soc", occupation_code="29-1141"),
    ]
    report = coverage_report(market, employers, estimates, workers)
    a = next(c for c in report.employers if c.employer.id == "a" and c.year == 2024)
    assert a.visible_workers == 8
    assert a.fraction == 0.08
    assert a.source == "headcount 2024"
    assert a.label == "8.0%"
    assert report.overall[0].fraction == 0
    assert report.overall[1].fraction == 158 / 400


def test_unknown_denominator_is_visible_and_does_not_create_false_overall_coverage():
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2024, 2024)
    employers = [Employer("a", "Synthetic A"), Employer("b", "Synthetic B")]
    estimates = [
        estimate_workforce(
            "a", 2024, market, lawyer=Headcount(100, 100, 100, "entered")
        )
    ]
    report = coverage_report(market, employers, estimates, [])
    assert report.employers[1].label == "denominator unknown"
    assert report.overall[0].fraction is None
    assert report.overall[0].unknown_employer_ids == ("b",)


@pytest.mark.parametrize(
    "value,low,high", [(float("nan"), 0, 100), (100, 110, 120), (-1, 0, 0)]
)
def test_invalid_public_estimates_cannot_manufacture_reliability(value, low, high):
    with pytest.raises(ValueError):
        Headcount(value, low, high, "synthetic invalid")


def test_workspace_ktd16_inputs_used_when_estimates_omitted(tmp_path):
    from datetime import date

    from nopoach.market.coverage import store_workforce_input

    market = Market(frozenset({"15-1252"}), Geography("metro", "99999"), 2024, 2024)
    with Workspace.open(tmp_path / "data") as workspace:
        receipt = workspace.provenance.append(
            source="synthetic", url="file:counts", method="IMPORT", body=b"counts"
        )
        employer = (
            EmployerResolver(workspace)
            .resolve("Synthetic Company", provenance_id=receipt.id)
            .entity_id
        )
        for kind, value in (
            ("company", 10000),
            ("staffing_share", 0.2),
            ("geography_share", 0.25),
        ):
            store_workforce_input(
                workspace,
                employer,
                2024,
                market,
                kind=kind,
                headcount=Headcount(value, value, value, "synthetic"),
                as_of=date(2024, 1, 1),
                provenance_id=receipt.id,
            )
        employers = [Employer(employer, "Synthetic Company")]
        report = coverage_report(market, employers, workers=(), workspace=workspace)
        assert report.employers[0].estimate.headcount.value == 500
        assert report.employers[0].estimate.headcount.lower == 250
        assert report.employers[0].estimate.headcount.upper == 750
        store_workforce_input(
            workspace,
            employer,
            2024,
            market,
            kind="lawyer",
            headcount=Headcount(300, 250, 350, "entered"),
            as_of=date(2024, 2, 1),
            provenance_id=receipt.id,
        )
        report = coverage_report(market, employers, workers=(), workspace=workspace)
        assert report.employers[0].estimate.headcount.value == 300

        past = coverage_report(
            market, employers, workers=(), workspace=workspace, as_of=date(2024, 1, 1)
        )
        assert past.employers[0].estimate.headcount.value == 500


@pytest.mark.parametrize(
    "future_field", [None, "decision_date", "employment_begin_date"]
)
def test_dol_cutoff_retains_spanning_period_but_excludes_future_events(
    tmp_path,
    future_field,
):
    from nopoach.collect.dol import DolRecord
    from nopoach.market.coverage import dol_in_period

    with Workspace.open(tmp_path / "dol-cutoff") as workspace:
        receipt = workspace.provenance.append(
            source="synthetic", url="file:lca", method="IMPORT", body=b"synthetic"
        )
        record = DolRecord(
            "LCA",
            "spanning",
            "Synthetic A",
            "a",
            "15-1252",
            "Fixture",
            "MN",
            "00000",
            "99999",
            None,
            None,
            "Year",
            None,
            None,
            12,
            "synthetic.csv",
            receipt,
            date(2019, 6, 15),
            date(2019, 6, 15),
            date(2021, 12, 31),
        )
        if future_field:
            record = replace(record, **{future_field: date(2019, 6, 16)})
        assert dol_in_period(record, date(2019, 1, 1), date(2019, 12, 31))
        assert dol_in_period(
            record, date(2019, 1, 1), date(2019, 12, 31), as_of=date(2019, 6, 15)
        ) is (future_field is None)
        assert not dol_in_period(
            record, date(2019, 6, 16), date(2020, 12, 31), as_of=date(2019, 6, 15)
        )
