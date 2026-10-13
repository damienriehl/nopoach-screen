from datetime import UTC, date, datetime

import pytest

from nopoach.market.coverage import (
    Headcount,
    MarketWorker,
    coverage_report,
    estimate_workforce,
)
from nopoach.market.define import Employer, Geography, Market
from nopoach.rank.aggregate import rank_pairs
from nopoach.rank.explanations import EmployerExplanationData
from nopoach.signals.clauses import ClauseDocument, ClauseSignal, extract_clauses
from nopoach.signals.flow import (
    FlowEmployer,
    FlowScreen,
    PairFlow,
    ResolvedPosition,
    screen_flow,
)
from nopoach.signals.ties import PairTies, Tie
from nopoach.store.provenance import ProvenanceRecord
from nopoach.synth.generator import generate_market
from nopoach.synth.scenarios import named_scenario

EMPLOYERS = tuple(Employer(key, f"Synthetic {key.upper()}") for key in "abcd")


def clause_signal(a, b):
    text = (
        f"{a.name} and {b.name}: neither party shall solicit or hire the other "
        "party's employees for two years."
    )
    receipt = ProvenanceRecord(
        "synthetic-clause",
        "synthetic filing",
        "",
        datetime(2020, 1, 1, tzinfo=UTC),
        "IMPORT",
        "synthetic",
        "0" * 64,
    )
    doc = ClauseDocument(text, receipt, date(2020, 1, 1), "EX-10", a)
    return ClauseSignal(a.id, b.id, extract_clauses(doc, (a, b)))


def flow_pair(
    a="a", b="b", score=0.9, classification="mutual_suppression", reliable=True
):
    return PairFlow(a, b, (), classification, "", score, reliable, "Synthetic coverage")


def flow_screen(*pairs):
    return FlowScreen(pairs, (), (), "synthetic", 30, 0.1, 1999, 42)


def test_ae4_clause_only_ranks_below_flow_plus_clause_and_keeps_evidence():
    ab = clause_signal(*EMPLOYERS[:2])
    cd = clause_signal(*EMPLOYERS[2:])
    flow = flow_pair(score=0.2)
    result = rank_pairs(EMPLOYERS, flow=flow_screen(flow), clauses=(ab, cd))
    top = result.mutual[0]
    assert (top.employer_a_id, top.employer_b_id) == ("a", "b")
    assert top.fired_signals == ("flow", "clauses")
    assert top.score == pytest.approx(0.4)
    assert top.flow == flow and top.clauses == ab
    assert "warrants further inquiry" in top.summary
    assert len(top.explanations) == 5
    clause_only = next(p for p in result.mutual if p.clauses == cd)
    assert clause_only.fired_signals == ("clauses",)
    assert clause_only.score == 0.3


def test_multiple_signals_outrank_a_stronger_single_signal():
    tiny_tie = PairTies(
        "a",
        "b",
        date(2019, 1, 1),
        date(2021, 1, 1),
        0.1,
        (Tie("leadership_link", (), None, None, (), 0.5, 0.2, 0.1),),
    )
    result = rank_pairs(
        EMPLOYERS,
        flow=flow_screen(flow_pair("c", "d", 1)),
        clauses=(clause_signal(*EMPLOYERS[:2]),),
        ties=(tiny_tie,),
    )
    assert result.mutual[0].fired_signals == ("clauses", "ties")
    assert result.mutual[0].score < 0.5


def test_fixed_weights_tie_normalization_and_unrenormalized_missing_signals():
    tie = PairTies(
        "a",
        "b",
        date(2019, 1, 1),
        date(2021, 1, 1),
        4,
        (Tie("shared_director", (), date(2019, 1, 1), date(2021, 1, 1), (), 4, 1, 4),),
    )
    result = rank_pairs(EMPLOYERS[:2], ties=(tie,))
    pair = result.mutual[0]
    assert [(s.key, s.score, s.weight, s.fired) for s in pair.signals] == [
        ("flow", 0, 0.5, False),
        ("clauses", 0, 0.3, False),
        ("ties", 0.5, 0.2, True),
    ]
    assert pair.score == pytest.approx(0.1)
    assert pair.ties == tie


def test_one_way_pair_never_enters_mutual_ranking_even_with_a_mutual_clause():
    flow = flow_pair(classification="one_way_suppression")
    result = rank_pairs(
        EMPLOYERS,
        flow=flow_screen(flow),
        clauses=(clause_signal(*EMPLOYERS[:2]),),
    )
    assert len(result.one_way) == 1
    pair = result.one_way[0]
    assert (pair.employer_a_id, pair.employer_b_id) == ("a", "b")
    assert pair.label == "Possible unilateral no-hire policy"
    assert pair not in result.mutual
    assert len(result.mutual) + len(result.one_way) == 6


def test_every_pair_is_ranked_once_in_a_repeatable_order_with_no_signal():
    result = rank_pairs(tuple(reversed(EMPLOYERS)))
    assert [(p.employer_a_id, p.employer_b_id) for p in result.mutual] == [
        ("a", "b"),
        ("a", "c"),
        ("a", "d"),
        ("b", "c"),
        ("b", "d"),
        ("c", "d"),
    ]
    assert all(p.score == 0 and not p.fired_signals for p in result.mutual)
    assert result.one_way == ()


def test_unreliable_flow_is_retained_but_does_not_fire():
    flow = flow_pair(reliable=False)
    result = rank_pairs(EMPLOYERS[:2], flow=flow_screen(flow))
    pair = result.mutual[0]
    assert pair.flow == flow
    assert pair.fired_signals == () and pair.score == 0


def test_neither_flow_does_not_fire_and_canonical_pair_joins_preserve_evidence():
    ab = clause_signal(*reversed(EMPLOYERS[:2]))
    flow = flow_pair("b", "a", classification="neither")
    pair = rank_pairs(EMPLOYERS[:2], flow=flow_screen(flow), clauses=(ab,)).mutual[0]
    assert pair.fired_signals == ("clauses",)
    assert pair.score == 0.3
    assert pair.flow == flow and pair.clauses == ab


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_ae1_real_flow_pipeline_ranks_planted_pair_first_with_explanations(seed):
    scenario = named_scenario("AE1")
    generated = generate_market(scenario, seed=seed)
    geo = Geography("metro", "SYNTHETIC")
    market = Market(frozenset({"29-1141"}), geo, scenario.start_year, scenario.end_year)
    employers = tuple(Employer(e.key, e.name) for e in scenario.employers)
    positions = tuple(
        ResolvedPosition(
            str(i),
            r["person_key"],
            r["employer_id"],
            r["occupation_code"],
            geo,
            date.fromisoformat(r["start_date"]),
            date.fromisoformat(r["end_date"]) if r["end_date"] else None,
            r["start_precision"],
            r["end_precision"],
            "synthetic-receipt",
        )
        for i, r in enumerate(generated.rows)
    )
    flow_employers = tuple(
        FlowEmployer(e.key, e.location, {"29-1141": 1}) for e in scenario.employers
    )
    estimates = tuple(
        estimate_workforce(
            e.key,
            year,
            market,
            imported=Headcount(e.size, e.size, e.size, "synthetic"),
        )
        for e in scenario.employers
        for year in range(scenario.start_year, scenario.end_year + 1)
    )
    workers = tuple(
        MarketWorker(p.person_id, p.employer_id, p.occupation_code, geo, year)
        for p in positions
        for year in range(scenario.start_year, scenario.end_year + 1)
        if p.start_date.year <= year and (p.end_date is None or p.end_date.year >= year)
    )
    coverage = coverage_report(market, employers, estimates, workers)
    screen = screen_flow(market, positions, flow_employers, coverage, seed=seed)
    data = {
        e.id: EmployerExplanationData(
            worksites_km=(e.location_km,),
            occupation_shares=e.occupation_shares,
        )
        for e in flow_employers
    }
    result = rank_pairs(employers, flow=screen, explanation_data=data)
    top = result.mutual[0]
    assert (top.employer_a_id, top.employer_b_id) == ("hospital-a", "hospital-b")
    assert top.fired_signals == ("flow",)
    assert "warrants further inquiry" in top.summary
    assert len(top.explanations) == 5
    assert {c.key: c.status for c in top.explanations} == {
        "geographic_separation": "failed",
        "pay_gap": "not_testable",
        "occupational_mismatch": "failed",
        "low_lateral_hiring": "not_testable",
        "non_compete_prevalence": "not_testable",
    }
    for direction in top.flow.directions:
        assert (
            direction.break_result.year_interval[0]
            <= 2020
            <= direction.break_result.year_interval[1]
        )
        assert 30 <= direction.break_result.before_observed_rate <= 50
        assert direction.break_result.after_observed_rate < 5
