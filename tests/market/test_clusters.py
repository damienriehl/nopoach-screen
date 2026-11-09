import json
from dataclasses import asdict, replace
from datetime import date
from itertools import permutations

import numpy as np
import pytest

from nopoach.analytical_inputs import ClusterSettings
from nopoach.market.clusters import (
    confirm_group,
    fit_partition,
    propose_clusters,
    selection_summary,
    validate_proposal,
)
from nopoach.market.coverage import Headcount, WorkforceEstimate, coverage_report
from nopoach.market.define import Employer, FlowDefinedMarket, Geography
from nopoach.signals.flow import ResolvedPosition


def fixture_network(*, sink=False):
    names = tuple("abcdef")
    market = FlowDefinedMarket(
        2020,
        2021,
        "synthetic",
        "Synthetic cross-boundary population",
        frozenset({"source"}),
        frozenset(names),
        frozenset(names),
    )
    positions = []
    factors = [1, 2, 3, 1, 2, 3]
    for i, j in permutations(range(6), 2):
        rate = (18 if i < 3 <= j else 1) if sink else (18 if i // 3 == j // 3 else 1)
        for count in range(factors[i] * factors[j] * rate):
            key = f"{i}-{j}-{count}"
            positions.extend(
                [
                    ResolvedPosition(
                        key + "-old",
                        key,
                        names[i],
                        "29-1141",
                        Geography("metro", "11111"),
                        date(2015, 1, 1),
                        date(2017, 3, 1),
                        "day",
                        "day",
                        "source",
                    ),
                    ResolvedPosition(
                        key + "-new",
                        key,
                        names[j],
                        "15-1252",
                        Geography("metro", "22222"),
                        date(2017, 3, 2),
                        None,
                        "day",
                        "unknown",
                        "source",
                    ),
                ]
            )
    return market, tuple(positions), coverage(market)


def coverage(market, unknown=()):
    pre = replace(
        market,
        start_year=2015,
        end_year=2019,
        employer_ids=market.candidate_employer_ids,
        added_employer_ids=frozenset(),
        removed_employer_ids=frozenset(),
    )
    employers = tuple(Employer(e, f"Synthetic {e}") for e in sorted(pre.employer_ids))
    estimates = [
        WorkforceEstimate(
            e.id,
            y,
            frozenset(),
            None,
            None if e.id in unknown else Headcount(100, 100, 100, "Synthetic count"),
            market.population_id,
        )
        for e in employers
        for y in range(2015, 2020)
    ]
    return coverage_report(pre, employers, estimates)


def test_fixed_partition_uses_directed_off_diagonal_exposure():
    counts = np.ones((3, 3)) - np.eye(3)
    fitted = fit_partition(counts, [0, 0, 0])
    assert fitted.converged
    assert fitted.log_likelihood == pytest.approx(-6)
    assert np.asarray(fitted.expected) == pytest.approx(counts)
    assert np.diag(fitted.expected).tolist() == [0, 0, 0]
    assert fitted.residual <= 1e-6


def test_ipf_preserves_multiplicity_direction_and_nondecreasing_likelihood():
    counts = np.array([[0, 2, 3, 1], [4, 0, 5, 2], [8, 3, 0, 7], [2, 6, 1, 0]])
    fitted = fit_partition(counts, [0, 0, 1, 1])
    expected = np.asarray(fitted.expected)
    assert fitted.converged
    assert expected.sum(axis=0) == pytest.approx(counts.sum(axis=0), rel=1e-6)
    assert expected.sum(axis=1) == pytest.approx(counts.sum(axis=1), rel=1e-6)
    assert np.diff(fitted.likelihood_history).min() >= -1e-8
    assert expected[0, 1] != expected[1, 0]
    assert len(fitted.sending_factors) == len(fitted.receiving_factors) == 4
    assert len(fitted.block_rates) == 2


@pytest.mark.parametrize("sink", [False, True])
def test_planted_blocks_recover_coassignment_with_heterogeneous_degrees(sink):
    market, positions, report = fixture_network(sink=sink)
    proposal = propose_clusters(market, positions, report, ClusterSettings(2015, 2019))
    assert proposal.status == "fitted", proposal.reason
    assert proposal.groups == (("a", "b", "c"), ("d", "e", "f"))
    assert proposal.log_likelihood > proposal.one_group_log_likelihood
    assert "local optimum" in proposal.reason
    assert proposal.residual <= proposal.settings.tolerance
    assert "dense" not in proposal.reason
    json.dumps(asdict(proposal), allow_nan=False)


def test_shuffle_and_later_moves_preserve_fit_and_input_digest():
    market, positions, report = fixture_network()
    settings = ClusterSettings(2015, 2019)
    first = propose_clusters(market, positions, report, settings)
    future = tuple(
        replace(
            p,
            id="future-" + p.id,
            person_id="future-" + p.person_id,
            start_date=date(2021, 1, 1),
            end_date=date(2021, 1, 2),
        )
        for p in positions[:2]
    )
    second = propose_clusters(
        market, tuple(reversed(positions)) + future, report, settings
    )
    assert second.groups == first.groups
    assert second.input_digest == first.input_digest
    assert second.digest == first.digest
    validate_proposal(first, market, positions + future, report)
    with pytest.raises(ValueError, match="changed"):
        validate_proposal(first, market, positions[2:], report)


def test_confirmation_keeps_manual_edits_universe_and_omitted_pairs():
    market, positions, report = fixture_network()
    proposal = propose_clusters(market, positions, report, ClusterSettings(2015, 2019))
    confirmed = confirm_group(proposal, market, 0, add={"d"}, remove={"c"})
    assert confirmed.fitted_employer_ids == frozenset({"a", "b", "c"})
    assert confirmed.employer_ids == frozenset({"a", "b", "d"})
    assert confirm_group(proposal, confirmed, 0) == confirmed
    summary = selection_summary(proposal, confirmed)
    assert summary.cross_boundary_pair_count == 9
    assert summary.omitted_pair_count == 12
    assert summary.excluded_employer_ids == ("c", "e", "f")
    assert summary.universe == tuple("abcdef")


def test_support_and_resource_failures_never_fabricate_assignments():
    market, positions, report = fixture_network()
    settings = ClusterSettings(2015, 2019)
    unknown = propose_clusters(
        market, positions, coverage(market, unknown={"f"}), settings
    )
    assert unknown.status == "unavailable"
    assert any(
        e.employer_id == "f" and "coverage" in e.reason for e in unknown.unassigned
    )
    empty = propose_clusters(market, (), report, settings)
    assert empty.status == "unavailable" and not empty.groups
    exhausted = propose_clusters(
        market, positions, report, replace(settings, candidate_refits=1)
    )
    assert exhausted.status == "incomplete" and not exhausted.groups
    too_large = propose_clusters(
        market, positions, report, replace(settings, max_employers=5)
    )
    assert too_large.status == "unavailable" and not too_large.groups
    overlapping = propose_clusters(
        market, positions, report, ClusterSettings(2015, 2020)
    )
    assert overlapping.status == "unavailable" and not overlapping.groups
    with pytest.raises(ValueError):
        confirm_group(exhausted, market, 0)


def test_intervening_jobs_and_date_bounds_are_not_training_moves():
    market, positions, report = fixture_network()
    base = positions[:2]
    middle = replace(
        base[0],
        id="middle",
        employer_id="outside",
        start_date=date(2017, 3, 1),
        end_date=date(2017, 3, 1),
    )
    interrupted = propose_clusters(
        market, (*base, middle), report, ClusterSettings(2015, 2019, groups=1)
    )
    assert interrupted.status == "unavailable"
    crossing = tuple(
        replace(
            p,
            start_precision="year",
            start_date=date(2015, 1, 1),
            end_date=date(2014, 12, 31),
        )
        if i == 0
        else p
        for i, p in enumerate(base)
    )
    assert (
        propose_clusters(market, crossing, report, ClusterSettings(2015, 2019)).status
        == "unavailable"
    )


def test_masked_dyads_have_neither_observations_nor_exposure():
    counts = [[0, 1, 0], [0, 0, 1], [1, 0, 0]]
    eligible = np.asarray(counts, dtype=bool)
    fitted = fit_partition(counts, [0, 0, 0], eligible=eligible)
    assert fitted.log_likelihood == pytest.approx(-3)
    assert np.asarray(fitted.expected) == pytest.approx(np.asarray(counts))
    with pytest.raises(ValueError, match="ineligible"):
        fit_partition(np.ones((3, 3)) - np.eye(3), [0, 0, 0], eligible=eligible)


def test_nonconvergence_and_one_group_reference_are_explicit():
    market, positions, report = fixture_network()
    incomplete = propose_clusters(
        market, positions, report, ClusterSettings(2015, 2019, fitting_sweeps=1)
    )
    assert incomplete.status == "incomplete" and not incomplete.groups
    assert "converge" in incomplete.reason
    reference = propose_clusters(
        market, positions, report, ClusterSettings(2015, 2019, groups=1)
    )
    assert reference.status == "fitted"
    assert reference.groups == (tuple("abcdef"),)
    assert "no reliable subdivision" in reference.reason


def test_isolated_employers_remain_visible_outside_fitted_members():
    market, positions, _ = fixture_network()
    market = replace(
        market,
        candidate_employer_ids=frozenset("abcdefgh"),
        employer_ids=frozenset("abcdefgh"),
    )
    proposal = propose_clusters(
        market, positions, coverage(market), ClusterSettings(2015, 2019)
    )
    assert proposal.status == "fitted"
    assert {e.employer_id for e in proposal.unassigned} == {"g", "h"}
    assert proposal.universe == tuple("abcdefgh")
    chosen = confirm_group(proposal, market, 0)
    assert selection_summary(proposal, chosen).cross_boundary_pair_count == 15


def test_numeric_training_identity_is_independent_of_later_bundle_receipt():
    market, positions, report = fixture_network()
    changed = replace(
        report,
        employers=tuple(
            replace(
                row,
                estimate=replace(
                    row.estimate,
                    headcount=replace(
                        row.estimate.headcount,
                        source="Same synthetic count [new-receipt]",
                    ),
                ),
            )
            for row in report.employers
        ),
    )
    original = propose_clusters(market, positions, report, ClusterSettings(2015, 2019))
    updated = propose_clusters(market, positions, changed, ClusterSettings(2015, 2019))
    assert original.input_digest == updated.input_digest
    assert original.groups == updated.groups
    assert original.source_references != updated.source_references
    assert original.digest != updated.digest


def test_confirmation_rejects_another_population_and_tampered_proposal():
    market, positions, report = fixture_network()
    proposal = propose_clusters(market, positions, report, ClusterSettings(2015, 2019))
    with pytest.raises(ValueError, match="population"):
        confirm_group(proposal, replace(market, population_id="different"), 0)
    with pytest.raises(ValueError, match="integrity"):
        confirm_group(
            replace(proposal, groups=tuple(reversed(proposal.groups))), market, 0
        )
