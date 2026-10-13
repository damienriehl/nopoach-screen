import contextlib
import io
import json
from datetime import date
from pathlib import Path

from nopoach.market.coverage import (
    Headcount,
    MarketWorker,
    coverage_report,
    estimate_workforce,
)
from nopoach.market.define import Employer, Geography, Market
from nopoach.market.sufficiency import (
    MIN_COVERAGE,
    MIN_EFFECTIVE_MOVES,
    FeedCoverage,
    signal_sufficiency,
)
from nopoach.synth.generator import generate_market
from nopoach.synth.scenarios import named_scenario


def synthetic_report(name):
    generated = generate_market(named_scenario(name), seed=42)
    market = Market(
        frozenset(o.code for o in generated.scenario.occupations),
        Geography("state", "MN"),
        2024,
        2024,
    )
    employers = [Employer(e.key, e.name) for e in generated.scenario.employers]
    # Year-end truth is independently counted from U2's complete spell history.
    counts = {
        e.id: len(
            {
                r["person_key"]
                for r in generated.true_rows
                if r["employer_id"] == e.id and not r["end_date"]
            }
        )
        for e in employers
    }
    estimates = [
        estimate_workforce(
            e.id,
            2024,
            market,
            imported=Headcount(
                counts[e.id], counts[e.id], counts[e.id], "synthetic truth"
            ),
        )
        for e in employers
    ]
    workers = [
        MarketWorker(
            r["person_key"],
            r["employer_id"],
            r["occupation_code"],
            market.geography,
            2024,
        )
        for r in generated.rows
        if not r["end_date"]
    ]
    return coverage_report(market, employers, estimates, workers)


def complete_feeds():
    return [
        FeedCoverage(
            "EDGAR", frozenset({date(2024, 1, 2)}), frozenset({date(2024, 1, 2)})
        )
    ]


def test_ae3_thin_employer_makes_pairs_and_flow_unreliable():
    result = signal_sufficiency(
        synthetic_report("AE3"),
        {("processor-a", "processor-b"): 100, ("processor-b", "processor-a"): 100},
        clause_feeds=complete_feeds(),
        tie_feeds=complete_feeds(),
    )
    assert result.flow.status == "insufficient"
    assert "Synthetic Amber Poultry" in result.flow.reason
    assert "20%" in result.flow.reason
    assert all(not pair.reliable for pair in result.pairs)
    assert result.clauses.status == result.ties.status == "sufficient"


def test_full_coverage_and_both_directions_at_threshold_are_sufficient():
    assert MIN_COVERAGE == 0.2
    assert MIN_EFFECTIVE_MOVES == 30
    result = signal_sufficiency(
        synthetic_report("AE4"),
        {("hospital-a", "hospital-b"): 30, ("hospital-b", "hospital-a"): 30},
        clause_feeds=complete_feeds(),
        tie_feeds=complete_feeds(),
    )
    assert result.flow.status == "sufficient"
    assert result.pairs[0].reliable


def test_missing_feed_dates_and_missing_effective_direction_are_named():
    feeds = [
        FeedCoverage(
            "EDGAR",
            frozenset({date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)}),
            frozenset({date(2024, 1, 3)}),
        )
    ]
    result = signal_sufficiency(
        synthetic_report("AE4"),
        {("hospital-a", "hospital-b"): 100},
        clause_feeds=feeds,
        tie_feeds=[],
    )
    assert result.clauses.status == "insufficient"
    assert "EDGAR" in result.clauses.reason
    assert (
        "2024-01-02" in result.clauses.reason and "2024-01-04" in result.clauses.reason
    )
    assert result.ties.status == "insufficient"
    assert "not supplied" in result.ties.reason
    assert result.flow.status == "insufficient"
    assert "Synthetic Birch Hospital -> Synthetic Amber Hospital" in result.flow.reason
    assert "unknown" in result.flow.reason


def test_unknown_denominator_marks_pair_with_that_reason():
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2024, 2024)
    employers = [Employer("a", "Synthetic A"), Employer("b", "Synthetic B")]
    result = signal_sufficiency(
        coverage_report(market, employers, [], []),
        {("a", "b"): 100, ("b", "a"): 100},
        clause_feeds=[],
        tie_feeds=[],
    )
    assert result.flow.status == "insufficient"
    assert not result.pairs[0].reliable
    assert "Synthetic A" in result.pairs[0].reason
    assert "denominator unknown" in result.pairs[0].reason


def test_each_year_must_clear_coverage_and_effective_moves_are_not_pooled():
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2023, 2024)
    employers = [Employer("a", "Synthetic A"), Employer("b", "Synthetic B")]
    estimates = [
        estimate_workforce(e.id, y, market, lawyer=Headcount(100, 100, 100, "entered"))
        for e in employers
        for y in (2023, 2024)
    ]
    workers = [
        MarketWorker(f"{e.id}-{i}", e.id, "15-1252", market.geography, y)
        for e in employers
        for y in (2023, 2024)
        for i in range(20 if y == 2024 else 8)
    ]
    result = signal_sufficiency(
        coverage_report(market, employers, estimates, workers),
        {("a", "b"): 29, ("b", "a"): 100},
        clause_feeds=[],
        tie_feeds=[],
    )
    assert "2023" in result.flow.reason
    assert "29" in result.pairs[0].reason


def test_no_employer_pairs_is_insufficient():
    market = Market(frozenset({"15-1252"}), Geography("state", "MN"), 2024, 2024)
    result = signal_sufficiency(
        coverage_report(market, [], [], []), {}, clause_feeds=[], tie_feeds=[]
    )
    assert result.flow.status == "insufficient"
    assert "two employers" in result.flow.reason


def test_threshold_calibration_notebook_is_reproducible():
    notebook = json.loads(
        Path(__file__).with_name("threshold_calibration.ipynb").read_text()
    )
    assert notebook["nbformat"] == 4
    namespace = {}
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(
                compile("".join(cell["source"]), f"calibration-cell-{index}", "exec"),
                namespace,
            )
        assert cell["outputs"] == [
            {
                "name": "stdout",
                "output_type": "stream",
                "text": output.getvalue().splitlines(True),
            }
        ]
    results = {row["case"]: row for row in namespace["results"]}
    assert results["thin_8_percent"]["false_admissions"] == 0
    assert results["too_few_moves"]["admitted"] == 0
    assert results["full_coverage"]["admitted"] == 20
