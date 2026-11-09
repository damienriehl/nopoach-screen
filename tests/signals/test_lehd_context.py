import runpy
from datetime import date
from pathlib import Path

import pytest

from nopoach.collect.lehd import parse_j2j
from nopoach.signals.lehd import ModeledFlow, temporal_context

fixtures = runpy.run_path(str(Path(__file__).parents[1] / "collect/test_lehd.py"))


def inputs():
    rows = fixtures["rows"]()
    metadata = fixtures["metadata"]()
    modeled = tuple(
        ModeledFlow(a, b, year, value)
        for year, value in ((2020, 10), (2021, 30), (2022, 20))
        for a, b in (("a", "b"), ("b", "a"))
    )
    return rows, metadata, modeled


def check(rows, metadata, modeled, **kwargs):
    options = dict(
        geography_kind="metro",
        geography_code="00123",
        start_year=2020,
        end_year=2022,
        employer_ids=("a", "b"),
        workplace_geography_known=True,
        source_reference="synthetic-receipt",
    )
    options.update(kwargs)
    return temporal_context(
        parse_j2j(fixtures["csv_body"](rows), metadata), modeled=modeled, **options
    )


def test_complete_common_years_have_raw_totals_shares_and_scope_limits():
    result = check(*inputs())
    assert result.status == "context_only"
    assert [r.public_count for r in result.annual] == [60, 100, 140]
    assert [r.modeled_count for r in result.annual] == [20, 60, 40]
    assert result.public_total == 300 and result.modeled_total == 120
    assert result.annual[0].public_share == pytest.approx(0.2)
    assert result.annual[0].modeled_share == pytest.approx(1 / 6)
    assert result.annual[0].difference_percentage_points == pytest.approx(10 / 3)
    limits = " ".join(result.scope_differences).lower()
    assert all(
        word in limits
        for word in ("occupation", "employer", "main job", "gap", "ownership")
    )
    assert result.source_reference == "synthetic-receipt"


def test_suppressed_quarter_removes_whole_year_from_both_totals():
    rows, meta, modeled = inputs()
    rows[4]["sAQHire"] = "5"
    result = check(rows, meta, modeled)
    assert result.status == "context_only"
    assert [r.year for r in result.annual] == [2020, 2022]
    assert result.public_total == 200 and result.modeled_total == 60
    assert "suppressed" in result.excluded_years[0].reason
    rows[0]["sEE"] = "-2"
    result = check(rows, meta, modeled)
    assert result.status == "unavailable"
    assert not result.annual and result.public_total is None
    assert "two complete" in result.reason


@pytest.mark.parametrize(
    "changes,phrase",
    [
        ({"geography_kind": "state"}, "metro"),
        ({"geography_kind": "flow"}, "metro"),
        ({"workplace_geography_known": False}, "workplace"),
        ({"geography_code": "00999"}, "metro"),
        ({"as_of": date(2022, 12, 31)}, "release"),
    ],
)
def test_unsupported_scope_and_future_release_are_unavailable(changes, phrase):
    result = check(*inputs(), **changes)
    assert result.status == "unavailable" and phrase in result.reason


def test_partial_cutoff_year_missing_pair_and_zero_totals_remain_unavailable():
    rows, meta, modeled = inputs()
    meta["release_date"] = "2020-01-01"
    result = check(rows, meta, modeled, as_of=date(2021, 6, 30))
    assert result.status == "unavailable"
    assert any("partial" in r.reason for r in result.excluded_years)
    result = check(rows, meta, modeled[:-1])
    assert result.status == "unavailable" and "modeled" in result.reason
    zero = tuple(ModeledFlow(r.source_id, r.destination_id, r.year, 0) for r in modeled)
    result = check(rows, meta, zero)
    assert result.status == "unavailable" and "positive" in result.reason


def test_all_directions_and_zero_expectations_are_retained():
    rows, meta, modeled = inputs()
    extended = tuple(
        ModeledFlow(a, b, year, 0)
        for year in (2020, 2021, 2022)
        for a, b in (("a", "c"), ("c", "a"), ("b", "c"), ("c", "b"))
    )
    result = check(rows, meta, (*modeled, *extended), employer_ids=("a", "b", "c"))
    assert result.status == "context_only" and result.modeled_total == 120
    result = check(rows, meta, modeled, employer_ids=("a", "b", "c"))
    assert result.status == "unavailable" and "modeled" in result.reason


def test_absent_quarter_and_availability_exclusions_share_the_same_year_set():
    rows, meta, modeled = inputs()
    result = check(rows[1:], meta, modeled)
    assert [row.year for row in result.annual] == [2021, 2022]
    assert "absent" in result.excluded_years[0].reason
    meta["availability"]["end_quarter"] = 3
    result = check(rows, meta, modeled)
    assert [row.year for row in result.annual] == [2020, 2021]
    assert "availability" in result.excluded_years[0].reason


def test_zero_public_counts_and_overflow_remain_unavailable():
    rows, meta, modeled = inputs()
    for row in rows:
        row.update(EE="0", AQHire="0")
    assert check(rows, meta, modeled).status == "unavailable"
    for row in rows:
        row.update(EE="1e308", AQHire="1e308")
    result = check(rows, meta, modeled)
    assert result.status == "unavailable" and "finite" in result.reason
