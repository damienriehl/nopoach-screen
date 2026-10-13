from datetime import date, timedelta

import pytest

from nopoach.catalog.recommend import load_catalog, recommend_sources
from nopoach.imports.schema import ProvenanceClass
from nopoach.market.coverage import (
    Headcount,
    MarketWorker,
    coverage_report,
    estimate_workforce,
)
from nopoach.market.define import Employer, Geography, Market
from nopoach.market.sufficiency import signal_sufficiency

AS_OF = date(2026, 10, 13)


def market_coverage(*, visible=8, occupation="51-3022", denominator_known=True):
    market = Market(frozenset({occupation}), Geography("state", "MN"), 2024, 2024)
    employers = [Employer("a", "Synthetic Amber Poultry"), Employer("b", "Synthetic B")]
    estimates = (
        [
            estimate_workforce(
                e.id, 2024, market, imported=Headcount(100, 100, 100, "synthetic truth")
            )
            for e in employers
        ]
        if denominator_known
        else []
    )
    workers = [
        MarketWorker(f"{e.id}-{i}", e.id, occupation, market.geography, 2024)
        for e in employers
        for i in range(visible if e.id == "a" else 40)
    ]
    report = coverage_report(market, employers, estimates, workers)
    verdicts = signal_sufficiency(
        report,
        {("a", "b"): 100, ("b", "a"): 100},
        clause_feeds=[],
        tie_feeds=[],
    )
    return report, verdicts


def test_ae3_insufficient_flow_recommends_state_records_first_and_renders():
    report, verdicts = market_coverage()
    assert verdicts.flow.status == "insufficient"
    assert report.employers[0].fraction == 0.08
    recommendations = recommend_sources(report, verdicts, as_of=AS_OF)
    assert recommendations[0].source.id == "state-ui-wage-records"
    assert len(recommendations) == 9
    assert recommendations[1].source.id == "linkedin"
    assert all(row.cost for row in recommendations)
    assert all(row.provenance_explanation for row in recommendations)
    assert all(row.staleness_notice is None for row in recommendations)
    rendered = "\n\n".join(row.render() for row in recommendations)
    assert "staff time" in rendered
    assert "quote required" in rendered
    assert "$1,250" in rendered and "unverified" in rendered
    assert "51-3022" in rendered and "MN" in rendered and "2024" in rendered
    assert "8.0%" in rendered and "Synthetic Amber Poultry" in rendered
    assert "aggregate" in rendered and "cannot directly fill" in rendered


def test_ae6_aggregator_explains_provenance_for_an_enforcement_lead():
    report, verdicts = market_coverage()
    row = next(
        r
        for r in recommend_sources(report, verdicts, as_of=AS_OF)
        if r.source.id == "revelio"
    )
    assert row.source.provenance_class == ProvenanceClass.THIRD_PARTY_UNCLEAR
    assert row.provenance_label == (
        "third-party web aggregation with unclear or possibly unlawful collection"
    )
    assert row.provenance_explanation == (
        "Collection and licensing are unclear, so the agency must establish lawful "
        "provenance before using these data for an enforcement lead."
    )
    assert row.provenance_label in row.render()
    assert row.provenance_explanation in row.render()


def test_sufficient_flow_yields_no_paid_recommendations_even_with_feed_gaps():
    report, verdicts = market_coverage(visible=40)
    assert verdicts.flow.status == "sufficient"
    assert recommend_sources(report, verdicts, as_of=AS_OF) == []


def test_restricted_and_synthetic_sources_are_never_recommended():
    report, verdicts = market_coverage()
    catalog = load_catalog()
    synthetic = catalog.sources[0].model_copy(
        update={"id": "synthetic", "provenance_class": ProvenanceClass.SYNTHETIC}
    )
    catalog = catalog.model_copy(update={"sources": (*catalog.sources, synthetic)})
    ids = {r.source.id for r in recommend_sources(report, verdicts, catalog=catalog)}
    assert {"census-lehd", "work-number", "synthetic"}.isdisjoint(ids)


@pytest.mark.parametrize("days,stale", [(180, False), (200, True)])
def test_price_and_terms_notice_after_180_days(days, stale):
    report, verdicts = market_coverage()
    catalog = load_catalog()
    source = catalog.sources[0].model_copy(
        update={"last_checked": AS_OF - timedelta(days=days)}
    )
    catalog = catalog.model_copy(update={"sources": (source,)})
    row = recommend_sources(report, verdicts, catalog=catalog, as_of=AS_OF)[0]
    expected = "price and terms may be out of date" if stale else None
    assert row.staleness_notice == expected
    assert ("price and terms may be out of date" in row.render()) == stale


def test_unknown_denominator_is_named_in_market_addition():
    report, verdicts = market_coverage(denominator_known=False)
    row = recommend_sources(report, verdicts, as_of=AS_OF)[0]
    assert "denominator unknown" in row.market_addition


def test_occupation_fit_orders_sources_within_provenance_priority():
    report, verdicts = market_coverage(occupation="15-1252")
    catalog = load_catalog()
    broad = catalog.sources[0].model_copy(
        update={"id": "broad", "occupation_prefixes": ()}
    )
    matching = broad.model_copy(
        update={"id": "professional", "occupation_prefixes": ("15",)}
    )
    other = broad.model_copy(update={"id": "nursing", "occupation_prefixes": ("29",)})
    catalog = catalog.model_copy(
        update={"sources": (other, broad, matching, catalog.sources[2])}
    )
    rows = recommend_sources(report, verdicts, catalog=catalog, as_of=AS_OF)
    assert [r.source.id for r in rows] == [
        "professional",
        "broad",
        "nursing",
        "linkedin",
    ]
    assert "Occupation-specific coverage must be confirmed" in rows[2].market_addition
