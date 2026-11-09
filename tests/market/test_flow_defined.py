import json
from dataclasses import replace
from datetime import date

import pytest

from nopoach.market.coverage import (
    Headcount,
    MarketWorker,
    WorkforceEstimate,
    coverage_report,
)
from nopoach.market.define import (
    Employer,
    FlowDefinedMarket,
    Geography,
    Market,
    geodesic_km,
    market_from_json,
    scope_description,
)
from nopoach.signals.flow import ResolvedPosition, build_moves


def market():
    return FlowDefinedMarket(
        2020,
        2021,
        "synthetic-population",
        "All supplied synthetic workers",
        frozenset({"source"}),
        frozenset({"a", "b", "c"}),
        frozenset({"a", "b"}),
        "synthetic-proposal",
        frozenset({"a", "b"}),
    )


def position(identifier, employer, occupation, metro, start, end, source="source"):
    return ResolvedPosition(
        identifier,
        "worker",
        employer,
        occupation,
        Geography("metro", metro) if metro else None,
        start,
        end,
        "day",
        "day",
        source,
    )


def test_independent_population_crosses_ordinary_boundaries_without_bridging():
    rows = (
        position("one", "a", "29-1141", "11111", date(2019, 1, 1), date(2020, 1, 1)),
        position("two", "b", "15-1252", "22222", date(2020, 1, 2), None),
    )
    assert len(build_moves(market(), rows)) == 1
    ordinary = Market(frozenset({"29-1141"}), Geography("metro", "11111"), 2020, 2021)
    assert not build_moves(ordinary, rows)
    middle = position(
        "middle", "c", "", None, date(2020, 1, 1), date(2020, 1, 1), "other"
    )
    assert not build_moves(market(), (rows[0], middle, rows[1]))
    assert (
        len(
            build_moves(
                market(),
                (replace(rows[0], occupation_code="", geography=None), rows[1]),
            )
        )
        == 1
    )


def test_population_coverage_deduplicates_and_rejects_other_denominators():
    scope = market()
    employers = (Employer("a", "Synthetic A"), Employer("b", "Synthetic B"))
    workers = (
        MarketWorker("worker", "a", "", None, 2020, "source"),
        MarketWorker(
            "worker", "a", "15-1252", Geography("metro", "22222"), 2020, "source"
        ),
        MarketWorker("excluded", "a", "", None, 2020, "other"),
    )
    estimates = [
        WorkforceEstimate(
            e.id,
            year,
            frozenset(),
            None,
            Headcount(2, 2, 2, "synthetic"),
            scope.population_id,
        )
        for e in employers
        for year in (2020, 2021)
    ]
    report = coverage_report(scope, employers, estimates, workers)
    assert report.employers[0].visible_workers == 1
    assert report.employers[0].fraction == 0.5
    wrong = [replace(e, population_id="other") for e in estimates]
    assert (
        coverage_report(scope, employers, wrong, workers).employers[0].fraction is None
    )


def test_tagged_definition_keeps_universe_and_manual_edits():
    scope = market().edit_employers(add={"c"}, remove={"b"})
    assert market_from_json(scope.to_json()) == scope
    assert scope.candidate_employer_ids == frozenset({"a", "b", "c"})
    assert scope.fitted_employer_ids == frozenset({"a", "b"})
    assert scope.employer_ids == frozenset({"a", "c"})
    assert "All supplied synthetic workers" in scope_description(scope)
    old = Market(frozenset({"29-1141"}), Geography("metro", "11111"), 2020, 2021)
    assert json.loads(old.to_json())["version"] == 1
    assert market_from_json(old.to_json()) == old
    with pytest.raises(ValueError):
        scope.edit_employers(add={"outside"})


def test_geodesic_distance_is_symmetric_and_handles_antimeridian():
    assert geodesic_km((0, 179), (0, -179)) == pytest.approx(222.39, abs=0.02)
    assert geodesic_km((40, -100), (50, 10)) == geodesic_km((50, 10), (40, -100))


def test_independent_population_recommendations_keep_use_limits():
    from nopoach.catalog.recommend import recommend_sources
    from nopoach.market.sufficiency import signal_sufficiency

    report = coverage_report(
        market(), (Employer("a", "Synthetic A"), Employer("b", "Synthetic B")), ()
    )
    verdict = signal_sufficiency(report, {}, clause_feeds=(), tie_feeds=())
    recommendations = recommend_sources(report, verdict)
    assert recommendations
    assert all(row.source.allowed_use == "leads" for row in recommendations)
    assert all(
        "independent population is unknown" in row.market_addition
        for row in recommendations
    )
