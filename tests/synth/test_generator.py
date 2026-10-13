from collections import Counter
from dataclasses import replace

import pytest

from nopoach.store.workspace import Workspace
from nopoach.synth.generator import generate_market, import_market
from nopoach.synth.scenarios import (
    CoverageMask,
    Employer,
    HiringFreeze,
    Occupation,
    PairEffect,
    Scenario,
    named_scenario,
)


def flow(market, source, destination, years):
    return sum(
        move.source == source and move.destination == destination and move.year in years
        for move in market.moves
    )


def test_seed_is_reproducible():
    scenario = named_scenario("AE1")
    assert generate_market(scenario, seed=123) == generate_market(scenario, seed=123)
    assert (
        generate_market(scenario, seed=123).moves
        != generate_market(scenario, seed=124).moves
    )


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_mutual_break_cuts_both_directions_from_2020(seed):
    scenario = named_scenario("AE1")
    market = generate_market(scenario, seed=seed)
    baseline = generate_market(replace(scenario, pair_effects=()), seed=seed)
    assert [m for m in market.moves if m.year < 2020] == [
        m for m in baseline.moves if m.year < 2020
    ]
    for source, destination in (
        ("hospital-a", "hospital-b"),
        ("hospital-b", "hospital-a"),
    ):
        before = flow(market, source, destination, range(2015, 2020)) / 5
        after = flow(market, source, destination, range(2020, 2025)) / 5
        assert 30 <= before <= 50
        assert 0.015 <= after / before <= 0.10
        assert all(
            flow(market, source, destination, [year]) <= 8 for year in range(2020, 2025)
        )


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_one_way_policy_only_cuts_hiring_by_b(seed):
    market = generate_market(named_scenario("AE2"), seed=seed)
    assert flow(market, "hospital-a", "hospital-b", range(2020, 2025)) <= 25
    assert flow(market, "hospital-b", "hospital-a", range(2020, 2025)) >= 120


def test_coverage_mask_preserves_ground_truth_and_obscures_about_eight_percent():
    scenario = named_scenario("AE3")
    assert scenario.occupations[0].code == "51-3022"
    assert "Poultry" in scenario.employers[0].name
    market = generate_market(scenario, seed=42)
    complete = generate_market(replace(scenario, coverage=()), seed=42)
    assert market.true_rows == complete.true_rows
    assert market.moves == complete.moves
    initial = [r for r in market.rows if r["start_date"] == "2014-01-01"]
    visible = sum(r["employer_id"] == "processor-a" for r in initial)
    assert 60 <= visible <= 100  # 8% of the 1,000 initial workers, binomial tolerance.
    assert sum(r["employer_id"] == "processor-b" for r in initial) == 1000


def test_occupation_mask_only_hides_the_specified_occupation():
    scenario = Scenario(
        name="occupation-mask",
        start_year=2020,
        end_year=2020,
        employers=(Employer("a", "Synthetic Alpha", 100, (0, 0), (0.5, 0.5)),),
        occupations=(
            Occupation("29-1141", "Registered Nurse", 0.04),
            Occupation("13-2011", "Accountant", 0.04),
        ),
        coverage=(CoverageMask("a", 0, "29-1141"),),
    )
    market = generate_market(scenario, seed=2)
    assert len(market.true_rows) == 100
    assert len(market.rows) == 50
    assert {r["occupation_code"] for r in market.rows} == {"13-2011"}


def test_all_generated_rows_and_moves_are_labeled_synthetic():
    market = generate_market(named_scenario("AE1"), seed=42)
    assert market.rows
    assert all(r["provenance_method"] == "synthetic" for r in market.true_rows)
    assert all(r["provenance_class"] == "synthetic" for r in market.rows)
    assert all(m.provenance_method == "synthetic" for m in market.moves)


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_hiring_freeze_reduces_incoming_moves_from_every_partner(seed):
    scenario = named_scenario("hiring_freeze")
    assert scenario.pair_effects == ()
    assert scenario.hiring_freezes == (HiringFreeze("hospital-a", 2018, 0.05),)
    market = generate_market(scenario, seed=seed)
    for partner in ("hospital-b", "hospital-c"):
        before = flow(market, partner, "hospital-a", range(2015, 2018)) / 3
        after = flow(market, partner, "hospital-a", range(2018, 2025)) / 7
        assert before >= 10
        assert after / before < 0.15
        assert flow(market, "hospital-a", partner, range(2018, 2025)) > 40


def test_gravity_respects_occupation_geography_and_pay():
    base = named_scenario("AE1")
    normal = generate_market(replace(base, pair_effects=()), seed=42)
    separated = generate_market(named_scenario("geographic_separation"), seed=42)
    pay_gap = generate_market(named_scenario("pay_gap"), seed=42)
    assert len(separated.moves) < len(normal.moves) / 20
    assert (
        flow(pay_gap, "hospital-a", "hospital-b", range(2015, 2025))
        < flow(pay_gap, "hospital-b", "hospital-a", range(2015, 2025)) / 2
    )
    disjoint = replace(
        base,
        pair_effects=(),
        employers=(
            replace(base.employers[0], occupation_shares=(1, 0)),
            replace(base.employers[1], occupation_shares=(0, 1)),
        ),
        occupations=(base.occupations[0], Occupation("13-2011", "Accountant", 0.04)),
    )
    assert generate_market(disjoint, seed=42).moves == ()


def test_dense_web_suppresses_all_large_pairs():
    scenario = named_scenario("dense_web")
    assert len(scenario.pair_effects) == 15
    assert all(effect.mutual for effect in scenario.pair_effects)
    market = generate_market(scenario, seed=42)
    assert (
        sum(m.year >= 2020 for m in market.moves)
        < sum(m.year < 2020 for m in market.moves) / 8
    )


def test_ae4_has_clause_and_normal_flow():
    scenario = named_scenario("AE4")
    assert scenario.pair_effects == ()
    assert len(scenario.clauses) == 1
    clause = scenario.clauses[0]
    assert (clause.start_year, clause.end_year) == (2020, 2021)
    market = generate_market(scenario, seed=42)
    assert flow(market, "hospital-a", "hospital-b", range(2020, 2025)) >= 150


def test_import_resolves_true_counts_and_retains_every_synthetic_receipt(tmp_path):
    scenario = replace(
        named_scenario("AE4"),
        employers=tuple(replace(e, size=3) for e in named_scenario("AE4").employers),
        start_year=2019,
        end_year=2021,
        occupations=(Occupation("29-1141", "Registered Nurse", 0.5),),
    )
    market = generate_market(scenario, seed=42)
    assert len({r["person_key"] for r in market.true_rows}) == 6
    with Workspace.open(tmp_path / "data") as workspace:
        result = import_market(workspace, market)
        assert result.rejects == ()
        assert result.accepted_count == len(market.rows)
        assert workspace.connection.execute(
            "SELECT count(*) FROM employers"
        ).fetchone() == (2,)
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (6,)
        assert workspace.connection.execute(
            "SELECT DISTINCT p.collection_method, r.provenance_class "
            "FROM imported_rows r JOIN provenance p ON p.id = r.provenance_id"
        ).fetchall() == [("synthetic", "synthetic")]
        assert workspace.connection.execute(
            "SELECT c.text, p.collection_method FROM clauses c "
            "JOIN provenance p ON p.id = c.provenance_id"
        ).fetchone() == (scenario.clauses[0].text, "synthetic")
        persisted = workspace.connection.execute(
            "SELECT k.person_key, a.external_id, x.start_date, x.end_date "
            "FROM positions x JOIN person_keys k ON k.person_id = x.person_id "
            "JOIN employer_aliases a ON a.employer_id = x.employer_id "
            "GROUP BY ALL"
        ).fetchall()
        assert len(persisted) == len(market.rows)
    # Moves really belong to successive spells, with one move per person/year.
    assert max(Counter((m.person_key, m.year) for m in market.moves).values()) == 1


def test_nondefault_mutual_factor_is_applied():
    scenario = replace(
        named_scenario("AE1"),
        pair_effects=(PairEffect("hospital-a", "hospital-b", 2020, 0.5),),
    )
    market = generate_market(scenario, seed=42)
    for source, destination in (
        ("hospital-a", "hospital-b"),
        ("hospital-b", "hospital-a"),
    ):
        assert (
            0.35
            < flow(market, source, destination, range(2020, 2025))
            / flow(market, source, destination, range(2015, 2020))
            < 0.65
        )
