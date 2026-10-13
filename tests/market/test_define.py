from nopoach.market.coverage import Headcount, WorkforceEstimate
from nopoach.market.define import (
    Employer,
    Geography,
    Market,
    OccupationSimilarity,
    propose_competitors,
    suggest_occupations,
)
from nopoach.synth.scenarios import named_scenario


def test_market_suggestions_do_not_expand_lawyers_selection():
    market = Market(frozenset({"29-1141"}), Geography("state", "MN"), 2020, 2024)
    suggestions = suggest_occupations(
        market,
        [
            OccupationSimilarity("29-1141.00", "29-1171.00", 0.9, "O*NET fixture"),
            OccupationSimilarity("29-1141.00", "29-1141.01", 1, "O*NET fixture"),
            OccupationSimilarity("29-1141.00", "29-2061.00", 0.7, "O*NET fixture"),
        ],
    )
    assert [(s.code, s.score, s.source) for s in suggestions] == [
        ("29-1171", 0.9, "O*NET fixture"),
        ("29-2061", 0.7, "O*NET fixture"),
    ]
    assert market.occupation_codes == frozenset({"29-1141"})


def test_proposals_include_planted_employers_and_keep_removals_on_rerun():
    scenario = named_scenario("dense_web")
    market = Market(frozenset({"29-1141"}), Geography("metro", "99999"), 2024, 2024)
    employers = [Employer(e.key, e.name) for e in scenario.employers]
    employers.append(Employer("small", "Synthetic Small Hospital"))
    employers.append(Employer("manual", "Synthetic Manual Hospital"))
    estimates = [
        WorkforceEstimate(
            e.id,
            2024,
            market.occupation_codes,
            market.geography,
            Headcount(
                1000 if e.id != "small" else 5,
                1000 if e.id != "small" else 5,
                1000 if e.id != "small" else 5,
                "synthetic headcount",
            ),
        )
        for e in employers
        if e.id != "manual"
    ]
    assert {
        p.employer.id
        for p in propose_competitors(
            market, employers, estimates, size_floor=100
        ).competitors
    } == {e.key for e in scenario.employers}
    revised = market.edit_employers(add={"manual"}, remove={"hospital-a"})
    restored = Market.from_json(revised.to_json())
    rerun = propose_competitors(restored, employers, estimates, size_floor=100)
    assert "hospital-a" not in {p.employer.id for p in rerun.competitors}
    assert "manual" in {p.employer.id for p in rerun.competitors}
    assert rerun.competitors[0].estimate.headcount.value == 1000
    assert restored == revised


def test_proposals_rank_by_latest_matching_market_estimate_and_report_sparse():
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2023, 2024)
    employers = [Employer("a", "Synthetic A"), Employer("b", "Synthetic B")]
    estimates = [
        WorkforceEstimate(
            "a",
            2023,
            market.occupation_codes,
            market.geography,
            Headcount(900, 900, 900, "old"),
        ),
        WorkforceEstimate(
            "a",
            2024,
            market.occupation_codes,
            market.geography,
            Headcount(100, 100, 100, "current"),
        ),
        WorkforceEstimate(
            "b",
            2024,
            market.occupation_codes,
            market.geography,
            Headcount(200, 200, 200, "current"),
        ),
        WorkforceEstimate(
            "a",
            2024,
            market.occupation_codes,
            Geography("state", "WI"),
            Headcount(9000, 9000, 9000, "elsewhere"),
        ),
    ]
    assert [
        p.employer.id
        for p in propose_competitors(market, employers, estimates).competitors
    ] == ["b", "a"]
    sparse = propose_competitors(market, employers, [])
    assert sparse.needs_employer_entry
    assert "enter employers" in sparse.reason


def test_optional_employer_narrowing_is_retained():
    market = Market(
        frozenset({"15-1252"}),
        Geography("state", "MN"),
        2024,
        2024,
        employer_ids=frozenset({"a"}),
    )
    revised = market.edit_employers(add={"b"}, remove={"a"})
    assert Market.from_json(revised.to_json()).employer_ids == frozenset({"b"})
