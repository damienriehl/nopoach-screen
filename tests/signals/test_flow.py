from dataclasses import replace
from datetime import date
from itertools import combinations

import pytest

from nopoach.market.coverage import (
    Headcount,
    MarketWorker,
    coverage_report,
    estimate_workforce,
)
from nopoach.market.define import Employer, Geography, Market
from nopoach.signals.flow import (
    FlowEmployer,
    ResolvedPosition,
    build_moves,
    screen_flow,
)
from nopoach.store.workspace import Workspace
from nopoach.synth.generator import generate_market, import_market
from nopoach.synth.scenarios import Employer as SyntheticEmployer
from nopoach.synth.scenarios import named_scenario


def inputs(scenario, seed):
    generated = generate_market(scenario, seed=seed)
    geography = Geography("metro", "SYNTHETIC")
    market = Market(
        frozenset(o.code for o in scenario.occupations),
        geography,
        scenario.start_year,
        scenario.end_year,
    )
    positions = tuple(
        ResolvedPosition(
            str(i),
            r["person_key"],
            r["employer_id"],
            r["occupation_code"],
            geography,
            date.fromisoformat(r["start_date"]),
            date.fromisoformat(r["end_date"]) if r["end_date"] else None,
            r["start_precision"],
            r["end_precision"],
            "synthetic-receipt",
        )
        for i, r in enumerate(generated.rows)
    )
    employers = tuple(
        FlowEmployer(
            e.key,
            e.location,
            dict(
                zip(
                    (o.code for o in scenario.occupations),
                    e.occupation_shares,
                    strict=True,
                )
            ),
        )
        for e in scenario.employers
    )
    estimates = [
        estimate_workforce(
            e.key, year, market, imported=Headcount(e.size, e.size, e.size, "synthetic")
        )
        for e in scenario.employers
        for year in range(market.start_year, market.end_year + 1)
    ]
    workers = [
        MarketWorker(p.person_id, p.employer_id, p.occupation_code, geography, year)
        for p in positions
        for year in range(market.start_year, market.end_year + 1)
        if p.start_date.year <= year and (p.end_date is None or p.end_date.year >= year)
    ]
    report = coverage_report(
        market,
        [Employer(e.key, e.name) for e in scenario.employers],
        estimates,
        workers,
    )
    return generated, market, positions, employers, report


def run(scenario, seed=42):
    _, market, positions, employers, report = inputs(scenario, seed)
    return screen_flow(market, positions, employers, report, seed=seed)


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_ae1_planted_pair_first_and_break_contains_2020(seed):
    result = run(named_scenario("AE1"), seed)
    pair = result.mutual[0]
    assert (pair.employer_a_id, pair.employer_b_id) == ("hospital-a", "hospital-b")
    assert result.pairs[0] == pair
    assert len(result.mutual) / len(result.pairs) <= 0.10
    for direction in pair.directions:
        assert (
            direction.break_result.year_interval[0]
            <= 2020
            <= direction.break_result.year_interval[1]
        )
        assert 30 <= direction.break_result.before_observed_rate <= 50
        assert direction.break_result.after_observed_rate < 5
        assert direction.ratio.lower < direction.ratio.ratio < direction.ratio.upper


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_ae2_one_way_is_separate(seed):
    result = run(named_scenario("AE2"), seed)
    pair = next(
        p
        for p in result.one_way
        if p.employer_a_id == "hospital-a" and p.employer_b_id == "hospital-b"
    )
    assert pair.classification == "one_way_suppression"
    assert pair not in result.mutual
    assert pair.label == "Possible unilateral no-hire policy"


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_hiring_freeze_has_no_pair_break(seed):
    result = run(named_scenario("hiring_freeze"), seed)
    assert all(
        d.break_result is None
        for p in result.pairs
        if "hospital-a" in (p.employer_a_id, p.employer_b_id)
        for d in p.directions
    )


@pytest.mark.parametrize("seed", [7, 42, 123])
def test_dense_web_detects_planted_network(seed):
    result = run(named_scenario("dense_web"), seed)
    planted = set(combinations([f"hospital-{c}" for c in "abcdef"], 2))
    detected = {(p.employer_a_id, p.employer_b_id) for p in result.mutual}
    assert planted <= detected


def test_tiny_zero_pair_is_not_flagged():
    scenario = replace(
        named_scenario("AE1"),
        pair_effects=(),
        employers=named_scenario("AE1").employers
        + (
            SyntheticEmployer("tiny-a", "Synthetic Tiny A", 1, (0, 0), (1,)),
            SyntheticEmployer("tiny-b", "Synthetic Tiny B", 1, (0, 0), (1,)),
        ),
    )
    result = run(scenario)
    pair = next(
        p
        for p in result.pairs
        if p.employer_a_id == "tiny-a" and p.employer_b_id == "tiny-b"
    )
    assert pair.classification == "neither"
    assert all(d.ratio.ratio > 0.9 for d in pair.directions)


def test_distance_explains_low_flow():
    scenario = replace(
        named_scenario("AE1"),
        pair_effects=(),
        employers=tuple(
            replace(e, location=(250, 0))
            if e.key in {"hospital-b", "comparison-3", "comparison-4"}
            else e
            for e in named_scenario("AE1").employers
        ),
    )
    result = run(scenario)
    pair = next(
        p
        for p in result.pairs
        if p.employer_a_id == "hospital-a" and p.employer_b_id == "hospital-b"
    )
    assert pair.classification == "neither"
    assert all(0.5 < d.ratio.ratio < 1.5 for d in pair.directions)
    assert all(sum(y.expected for y in d.years) < 10 for d in pair.directions)


@pytest.mark.parametrize("seed", range(20))
def test_null_flags_at_most_ten_percent(seed):
    result = run(replace(named_scenario("AE1"), pair_effects=()), seed)
    assert (len(result.mutual) + len(result.one_way)) / len(result.pairs) <= 0.10


def test_counts_expand_to_exact_resolved_person_moves():
    generated, market, positions, employers, report = inputs(named_scenario("AE1"), 42)
    result = screen_flow(market, positions, employers, report, seed=42)
    moves = [
        m for p in result.pairs for d in p.directions for y in d.years for m in y.moves
    ]
    assert len(moves) == len(generated.moves)
    assert {(m.person_id, m.source_id, m.destination_id, m.year) for m in moves} == {
        (m.person_key, m.source, m.destination, m.year) for m in generated.moves
    }
    assert all(
        y.observed == len(y.moves)
        for p in result.pairs
        for d in p.directions
        for y in d.years
    )
    assert all(
        m.origin_position_id and m.destination_position_id and m.provenance_ids
        for m in moves
    )


def test_gap_market_and_date_precision_are_preserved():
    _, market, positions, _, _ = inputs(named_scenario("AE1"), 42)
    p = positions[0]
    source = replace(
        p,
        id="old",
        employer_id="hospital-a",
        start_date=date(2018, 1, 1),
        end_date=date(2019, 12, 31),
        end_precision="year",
    )
    destination = replace(
        p,
        id="new",
        employer_id="hospital-b",
        start_date=date(2020, 1, 1),
        end_date=None,
        start_precision="year",
    )
    moves = build_moves(market, (source, destination), max_gap_days=366)
    assert len(moves) == 1
    assert moves[0].year_interval == (2019, 2020)
    assert not build_moves(
        market,
        (source, replace(destination, start_date=date(2022, 1, 1))),
        max_gap_days=90,
    )
    assert not build_moves(
        market, (source, replace(destination, geography=Geography("metro", "OTHER")))
    )


def test_low_coverage_marks_rankings_unreliable():
    _, market, positions, employers, report = inputs(named_scenario("AE1"), 42)
    report = replace(
        report,
        employers=tuple(
            replace(r, fraction=0.08) if r.employer.id == "hospital-a" else r
            for r in report.employers
        ),
    )
    result = screen_flow(market, positions, employers, report, seed=42)
    pair = next(
        p
        for p in result.pairs
        if p.employer_a_id == "hospital-a" and p.employer_b_id == "hospital-b"
    )
    assert not pair.reliable
    assert "coverage" in pair.reliability_reason
    assert pair not in result.mutual and pair not in result.one_way


def test_resolved_workspace_positions_are_supported(tmp_path):
    base = named_scenario("AE1")
    scenario = replace(
        base,
        employers=tuple(replace(e, size=3) for e in base.employers),
        start_year=2019,
        end_year=2021,
    )
    generated = generate_market(scenario, seed=42)
    geography = Geography("metro", "SYNTHETIC")
    with Workspace.open(tmp_path / "data") as workspace:
        import_market(workspace, generated)
        positions = ResolvedPosition.from_workspace(
            workspace,
            geography_by_position={
                row[0]: geography
                for row in workspace.connection.execute(
                    "SELECT id FROM positions"
                ).fetchall()
            },
        )
        market = Market(frozenset({"29-1141"}), geography, 2015, 2024)
        assert len(build_moves(market, positions)) == len(generated.moves)
        assert all(m.provenance_ids for m in build_moves(market, positions))


def test_workspace_adapter_keeps_unmapped_jobs_in_spell_chain(tmp_path):
    base = named_scenario("AE1")
    scenario = replace(
        base,
        employers=tuple(replace(e, size=3) for e in base.employers),
        start_year=2019,
        end_year=2021,
    )
    generated = generate_market(scenario, seed=42)
    geography = Geography("metro", "SYNTHETIC")
    with Workspace.open(tmp_path / "data") as workspace:
        import_market(workspace, generated)
        count = workspace.connection.execute(
            "SELECT count(*) FROM positions"
        ).fetchone()[0]
        positions = ResolvedPosition.from_workspace(workspace, geography_by_position={})
        assert len(positions) == count
        assert all(p.geography is None for p in positions)
        market = Market(frozenset({"29-1141"}), geography, 2019, 2021)
        assert build_moves(market, positions) == ()


def test_coverage_must_describe_the_screened_market():
    _, market, positions, employers, report = inputs(named_scenario("AE1"), 42)
    report = replace(
        report, market=replace(market, geography=Geography("metro", "OTHER"))
    )
    with pytest.raises(ValueError, match="Coverage must describe the screened market"):
        screen_flow(market, positions, employers, report)
