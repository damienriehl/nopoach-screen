from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from nopoach.collect.dol import DolRecord
from nopoach.rank.explanations import EmployerExplanationData, check_explanations
from nopoach.store.provenance import ProvenanceRecord


def wage(employer, amount, *, soc="29-1141", program="LCA", case="synthetic"):
    receipt = ProvenanceRecord(
        id=f"wage-{employer}-{case}",
        source="synthetic LCA",
        url="",
        collected_at=datetime(2020, 1, 1, tzinfo=UTC),
        method="IMPORT",
        collection_method="synthetic",
        sha256="0" * 64,
    )
    return DolRecord(
        program,
        case,
        f"Synthetic {employer}",
        employer,
        soc,
        "Synthetic City",
        "MN",
        "00000",
        "SYNTHETIC",
        Decimal(amount) if amount else None,
        None,
        "year",
        Decimal(amount) if amount else None,
        None,
        1,
        "synthetic.csv",
        receipt,
    )


def checks(left=None, right=None):
    return {
        c.key: c
        for c in check_explanations(
            "a",
            "b",
            left or EmployerExplanationData(),
            right or EmployerExplanationData(),
        )
    }


def test_2000_miles_apart_passes_geographic_explanation():
    result = checks(
        EmployerExplanationData(worksites_km=((0, 0),)),
        EmployerExplanationData(worksites_km=((3218.688, 0),)),
    )["geographic_separation"]
    assert result.status == "passed"
    assert result.measurements["distance_km"] == pytest.approx(3218.688)
    assert "3218.7 km" in result.reason


def test_geography_uses_nearest_worksites_in_the_footprints():
    result = checks(
        EmployerExplanationData(worksites_km=((0, 0), (3200, 0))),
        EmployerExplanationData(worksites_km=((3210, 0),)),
    )["geographic_separation"]
    assert result.status == "failed"
    assert result.measurements["distance_km"] == 10


def test_missing_wages_are_not_testable_with_a_reason():
    result = checks()["pay_gap"]
    assert result.status == "not_testable"
    assert "comparable annual LCA wages" in result.reason


@pytest.mark.parametrize(
    "right_wage, status, gap",
    [
        ("100000", "passed", 0.4),
        ("65000", "failed", 1 / 13),
    ],
)
def test_pay_gap_compares_lca_offers_with_matching_occupation_and_geography(
    right_wage,
    status,
    gap,
):
    left = EmployerExplanationData(lca_wages=(wage("a", "60000"),))
    right = EmployerExplanationData(lca_wages=(wage("b", right_wage),))
    result = checks(left, right)["pay_gap"]
    assert result.status == status
    assert result.measurements["relative_gap"] == pytest.approx(gap)
    assert result.provenance_ids == ("wage-a-synthetic", "wage-b-synthetic")
    assert "offered wages" in result.reason
    assert "workforce census" in result.reason


def test_pay_comparison_does_not_use_perm_or_unmatched_jobs():
    left = EmployerExplanationData(lca_wages=(wage("a", "60000"),))
    for record in (
        wage("b", "100000", program="PERM"),
        wage("b", "100000", soc="15-1252"),
        replace(wage("b", "100000"), worksite_metro="OTHER"),
    ):
        assert (
            checks(left, EmployerExplanationData(lca_wages=(record,)))["pay_gap"].status
            == "not_testable"
        )


@pytest.mark.parametrize(
    "right_shares, status, overlap",
    [
        ({"29-1141": 0.1, "15-1252": 0.9}, "passed", 0.1),
        ({"29-1141": 0.8, "15-1252": 0.2}, "failed", 0.8),
    ],
)
def test_occupational_mismatch_uses_overlap(right_shares, status, overlap):
    result = checks(
        EmployerExplanationData(occupation_shares={"29-1141": 1}),
        EmployerExplanationData(occupation_shares=right_shares),
    )["occupational_mismatch"]
    assert result.status == status
    assert result.measurements["overlap"] == overlap


@pytest.mark.parametrize(
    "rates, status",
    [
        ((0.02, 0.15), "passed"),
        ((0.15, 0.02), "passed"),
        ((0.15, 0.20), "failed"),
        ((None, 0.20), "not_testable"),
        ((0.02, None), "passed"),
    ],
)
def test_low_lateral_hiring_checks_each_firms_overall_external_hire_rate(rates, status):
    result = checks(
        EmployerExplanationData(external_hire_rate=rates[0]),
        EmployerExplanationData(external_hire_rate=rates[1]),
    )["low_lateral_hiring"]
    assert result.status == status
    assert "a" in result.reason and "b" in result.reason
    if rates[1] is None:
        assert "unavailable" in result.reason


def test_all_five_explanations_remain_visible_when_data_is_missing():
    result = checks()
    assert set(result) == {
        "geographic_separation",
        "pay_gap",
        "occupational_mismatch",
        "low_lateral_hiring",
        "non_compete_prevalence",
    }
    assert all(c.status == "not_testable" and c.reason for c in result.values())
    assert "no non-compete extractor" in result["non_compete_prevalence"].reason
