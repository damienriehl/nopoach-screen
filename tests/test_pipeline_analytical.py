import json
import runpy
from dataclasses import replace
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

from nopoach.analytical_inputs import AnalyticalInputs, ClusterSettings
from nopoach.collect.lehd import import_j2j
from nopoach.imports.flow_population import import_population
from nopoach.imports.noncompetes import import_contracts
from nopoach.market.clusters import confirm_group
from nopoach.market.define import Geography, Market, scope_key
from nopoach.pipeline import propose_flow_market, run_screen
from nopoach.store.workspace import Workspace


def load_analytical_fixture(workspace, *, cross_geographies=False):
    helper = runpy.run_path(str(Path(__file__).parent / "market/test_clusters.py"))
    _, training, _ = helper["fixture_network"]()
    receipt = workspace.provenance.append(
        source="Synthetic analytical histories",
        url="file:synthetic-history",
        method="IMPORT",
        body=b"synthetic analytical fixture",
        collection_method="synthetic",
    )
    positions = list(training)
    for year in (2020, 2021):
        positions.extend(
            replace(
                p,
                id=f"{year}-{p.id}",
                person_id=f"{year}-{p.person_id}",
                start_date=date(year - 1, 1, 1)
                if p.id.endswith("old")
                else date(year, 3, 2),
                end_date=date(year, 3, 1) if p.id.endswith("old") else None,
            )
            for p in training
        )
    rows = [
        dict(
            id=p.id,
            person=p.person_id,
            employer=p.employer_id,
            occupation=p.occupation_code,
            start=p.start_date,
            end=p.end_date,
            start_precision=p.start_precision,
            end_precision=p.end_precision,
            cbsa=("11111" if p.employer_id < "d" else "22222")
            if cross_geographies
            else "00123",
            latitude=40 + ord(p.employer_id) - ord("a"),
            longitude=-100.0,
        )
        for p in positions
    ]
    connection = workspace.connection
    connection.register("analytical_fixture", pa.Table.from_pylist(rows))
    try:
        connection.execute(
            "INSERT INTO employers SELECT DISTINCT employer, "
            "'Synthetic ' || employer, NULL, ? FROM analytical_fixture",
            [receipt.id],
        )
        connection.execute(
            "INSERT INTO people SELECT DISTINCT person, 'Synthetic worker', ? "
            "FROM analytical_fixture",
            [receipt.id],
        )
        connection.execute(
            "INSERT INTO positions SELECT id, person, employer, 'Synthetic role', "
            'occupation, start, "end", start_precision, end_precision, ? '
            "FROM analytical_fixture",
            [receipt.id],
        )
        connection.execute(
            "INSERT INTO position_worksites SELECT id, cbsa, latitude, longitude, "
            "'synthetic', 'supplied_cbsa_centroid', ? FROM analytical_fixture",
            [receipt.id],
        )
    finally:
        connection.unregister("analytical_fixture")
    body = "employer_id,year,value,lower,upper,source,as_of\n" + "".join(
        f"{employer},{year},200,200,200,Synthetic count,{year}-01-01\n"
        for employer in "abcdef"
        for year in range(2015, 2022)
    )
    population = import_population(
        workspace,
        body.encode(),
        {
            "population_id": "synthetic",
            "description": "Synthetic cross-boundary population",
            "provenance_ids": [receipt.id],
            "employer_ids": list("abcdef"),
            "source": "Synthetic counts",
        },
    )
    market = Market(
        frozenset({"29-1141", "15-1252"}), Geography("metro", "00123"), 2020, 2021
    )
    return market, population


def public_input(workspace, *, workplace=True, release_date="2023-12-31"):
    helper = runpy.run_path(str(Path(__file__).parent / "collect/test_lehd.py"))
    metadata = helper["metadata"]()
    metadata.update(
        release_date=release_date,
        workplace_geography="workplace" if workplace else "unknown",
    )
    return import_j2j(workspace, helper["csv_body"](helper["rows"]()), metadata)


def contracts_input(workspace, market, *, document_date="2019-01-01"):
    helper = runpy.run_path(str(Path(__file__).parent / "imports/test_noncompetes.py"))
    rows = []
    for employer in "ab":
        row = helper["row"](f"private-worker-key-{employer}")
        row.update(
            employer_id=employer,
            scope=scope_key(market),
            document_id=f"synthetic-contract-{employer}",
            document_date=document_date,
        )
        rows.append(row)
    metadata = helper["metadata"]({"a": 1, "b": 1})
    metadata.update(scope=scope_key(market))
    return import_contracts(workspace, helper["body"](rows), metadata)


@pytest.fixture
def ordinary(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        market, population = load_analytical_fixture(workspace)
        yield workspace, market, population


def test_optional_context_uses_all_pairs_and_freezes_sources_without_new_tables(
    ordinary,
):
    workspace, market, _ = ordinary
    inputs = AnalyticalInputs(
        j2j=public_input(workspace), contracts=contracts_input(workspace, market)
    )
    before = workspace.table_names()
    result = run_screen(workspace, market, analytical_inputs=inputs)
    assert workspace.table_names() == before
    assert not result.mutual and result.unreliable
    context = result.analytical
    assert context.public_context.status == "context_only"
    assert [row.public_count for row in context.public_context.annual] == [60, 100]
    all_pairs = [row.pair for row in result.unreliable]
    expected = sum(
        y.expected
        for pair in all_pairs
        for direction in pair.flow.directions
        for y in direction.years
    )
    assert context.public_context.modeled_total == pytest.approx(expected)
    assert len(context.sources) == 2
    pair = next(
        pair
        for pair in all_pairs
        if pair.employer_a_id == "a" and pair.employer_b_id == "b"
    )
    assert pair.explanations[-1].status == "passed"
    parameters, summary = workspace.connection.execute(
        "SELECT parameters_json, results_json FROM screens WHERE id = ?",
        [result.screen_id],
    ).fetchone()
    parameters, summary = json.loads(parameters), json.loads(summary)
    assert (
        parameters["analytical_inputs"]["contracts"]["bundle_id"]
        == inputs.contracts.bundle_id
    )
    assert summary["analytical"]["public_context"]["status"] == "context_only"
    assert summary["analytical"]["sources"][0]["manifest_sha256"]
    assert "private-worker-key" not in json.dumps(summary["analytical"])


def test_missing_geography_and_future_optional_evidence_remain_unavailable(ordinary):
    workspace, market, _ = ordinary
    inputs = AnalyticalInputs(
        j2j=public_input(workspace, workplace=False),
        contracts=contracts_input(workspace, market, document_date="2023-01-01"),
    )
    current = run_screen(workspace, market, analytical_inputs=inputs)
    assert "geography" in current.analytical.public_context.reason
    historical = run_screen(
        workspace, market, as_of=date(2021, 12, 31), analytical_inputs=inputs
    )
    assert not historical.analytical.contracts.candidates
    assert not historical.analytical.contracts.employers[0].complete
    future_release = replace(inputs, j2j=public_input(workspace))
    historical = run_screen(
        workspace, market, as_of=date(2021, 12, 31), analytical_inputs=future_release
    )
    assert "release date" in historical.analytical.public_context.reason


def test_input_corruption_fails_new_screen_but_prior_snapshot_is_unchanged(ordinary):
    workspace, market, _ = ordinary
    ref = contracts_input(workspace, market)
    inputs = AnalyticalInputs(contracts=ref)
    result = run_screen(workspace, market, analytical_inputs=inputs)
    old = result.analytical.contracts.candidates[0].text
    path = workspace.root / "analysis/inputs" / ref.bundle_id / "raw.bin"
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        run_screen(workspace, market, analytical_inputs=inputs)
    assert result.analytical.contracts.candidates[0].text == old


def test_confirmed_flow_market_uses_independent_population_and_rejects_stale_graph(
    tmp_path,
):
    with Workspace.open(tmp_path / "data") as workspace:
        _, population = load_analytical_fixture(workspace, cross_geographies=True)
        inputs = AnalyticalInputs(
            flow_population=population, cluster_settings=ClusterSettings(2015, 2019)
        )
        market, proposal = propose_flow_market(
            workspace, inputs, start_year=2020, end_year=2021
        )
        assert proposal.status == "fitted", proposal.reason
        confirmed = confirm_group(proposal, market, 0, add={"d"})
        with pytest.raises(ValueError, match="proposal"):
            run_screen(workspace, confirmed, analytical_inputs=inputs)
        result = run_screen(
            workspace, confirmed, analytical_inputs=inputs, cluster_proposal=proposal
        )
        assert result.analytical.cluster_selection.cross_boundary_pair_count == 8
        assert result.analytical.cluster_selection.excluded_employer_ids == ("e", "f")
        assert {row.employer.id for row in result.coverage.employers} == {
            "a",
            "b",
            "c",
            "d",
        }
        pairs = result.mutual + tuple(row.pair for row in result.unreliable)
        assert any(
            year.observed > 0
            for pair in pairs
            for direction in pair.flow.directions
            for year in direction.years
        )
        workspace.connection.execute(
            "UPDATE positions SET end_date = '2016-01-01' WHERE id = ?",
            [next(p for p in proposal.training_position_ids if p.endswith("old"))],
        )
        with pytest.raises(ValueError, match="training graph|proposal"):
            run_screen(
                workspace,
                confirmed,
                analytical_inputs=inputs,
                cluster_proposal=proposal,
            )


def test_empty_selection_preserves_default_behavior_and_saved_shape(ordinary):
    workspace, market, _ = ordinary
    default = run_screen(workspace, market)
    selected = run_screen(workspace, market, analytical_inputs=AnalyticalInputs())
    assert default.analytical is None
    assert default.mutual == selected.mutual
    assert default.one_way == selected.one_way
    assert default.unreliable == selected.unreliable
    assert default.coverage == selected.coverage
    assert default.sufficiency == selected.sufficiency
    parameters, results = workspace.connection.execute(
        "SELECT parameters_json, results_json FROM screens WHERE id = ?",
        [default.screen_id],
    ).fetchone()
    assert "analytical_inputs" not in json.loads(parameters)
    assert "analytical" not in json.loads(results)


def test_empty_market_keeps_selected_context_and_sources_without_pair_references(
    tmp_path,
):
    with Workspace.open(tmp_path / "data") as workspace:
        market = Market(frozenset({"29-1141"}), Geography("metro", "00123"), 2020, 2021)
        inputs = AnalyticalInputs(j2j=public_input(workspace))
        result = run_screen(workspace, market, analytical_inputs=inputs)
        assert not result.mutual and not result.one_way and not result.references
        assert result.analytical.public_context.status == "unavailable"
        assert len(result.analytical.sources) == 1
        saved = workspace.connection.execute(
            "SELECT results_json FROM screens WHERE id = ?", [result.screen_id]
        ).fetchone()[0]
        assert (
            json.loads(saved)["analytical"]["sources"][0]["bundle_id"]
            == inputs.j2j.bundle_id
        )


def test_flow_population_replacement_and_settings_cannot_reuse_proposal(tmp_path):
    from nopoach.analytical_inputs import read_bundle

    with Workspace.open(tmp_path / "data") as workspace:
        _, population = load_analytical_fixture(workspace, cross_geographies=True)
        inputs = AnalyticalInputs(
            flow_population=population, cluster_settings=ClusterSettings(2015, 2019)
        )
        market, proposal = propose_flow_market(
            workspace, inputs, start_year=2020, end_year=2021
        )
        confirmed = confirm_group(proposal, market, 0)
        bundle = read_bundle(workspace, population)
        other = import_population(
            workspace, bundle.body, {**bundle.metadata, "source": "Revised declaration"}
        )
        for replacement in (
            replace(inputs, flow_population=other),
            replace(inputs, cluster_settings=ClusterSettings(2015, 2019, seed=1)),
        ):
            with pytest.raises(ValueError, match="proposal"):
                run_screen(
                    workspace,
                    confirmed,
                    analytical_inputs=replacement,
                    cluster_proposal=proposal,
                )
        with pytest.raises(ValueError, match="proposal"):
            run_screen(
                workspace,
                replace(confirmed, cluster_digest="stale"),
                analytical_inputs=inputs,
                cluster_proposal=proposal,
            )


def test_flow_unknown_geography_and_missing_covariates_stay_explicit(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        _, population = load_analytical_fixture(workspace, cross_geographies=True)
        inputs = AnalyticalInputs(
            flow_population=population, cluster_settings=ClusterSettings(2015, 2019)
        )
        market, proposal = propose_flow_market(
            workspace, inputs, start_year=2020, end_year=2021
        )
        confirmed = confirm_group(proposal, market, 0, add={"d"})
        workspace.connection.execute(
            "UPDATE position_worksites SET cbsa = '', "
            "latitude = NULL, longitude = NULL "
            "WHERE position_id IN (SELECT id FROM positions WHERE employer_id = 'a')"
        )
        workspace.connection.execute(
            "UPDATE positions SET occupation_code = '' WHERE employer_id = 'b' "
            "AND (id LIKE '2020-%' OR id LIKE '2021-%')"
        )
        workspace.connection.execute(
            "UPDATE position_worksites SET latitude = NULL, longitude = NULL "
            "WHERE position_id IN (SELECT id FROM positions WHERE employer_id = 'c' "
            "AND id LIKE '%old')"
        )
        result = run_screen(
            workspace, confirmed, analytical_inputs=inputs, cluster_proposal=proposal
        )
        assert any(
            row.employer.id == "a" and row.visible_workers > 0
            for row in result.coverage.employers
        )
        omissions = result.analytical.covariate_omissions
        assert any(
            "Synthetic a:" in reason and "centroid" in reason for reason in omissions
        )
        assert any(
            "Synthetic b:" in reason and "known occupations" in reason
            for reason in omissions
        )
        assert any(
            "Synthetic c:" in reason and "supplied coordinates" in reason
            for reason in omissions
        )
        # Earlier source histories still establish occupation support for b.
        pairs = result.mutual + tuple(row.pair for row in result.unreliable)
        pair = next(
            p for p in pairs if p.employer_a_id == "a" and p.employer_b_id == "b"
        )
        assert pair.flow is None
        assert pair.explanations[0].status == "not_testable"


def test_flow_covariates_use_source_membership_and_spherical_centroids(tmp_path):
    from nopoach.analytical_context import flow_explanation_inputs
    from nopoach.imports.flow_population import load_population
    from nopoach.market.define import Employer, FlowDefinedMarket
    from nopoach.pipeline import _resolved_positions

    with Workspace.open(tmp_path / "data") as workspace:
        _, ref = load_analytical_fixture(workspace, cross_geographies=True)
        population = load_population(workspace, ref)
        market = FlowDefinedMarket(
            2020,
            2021,
            population.population_id,
            population.description,
            population.provenance_ids,
            population.employer_ids,
            population.employer_ids,
        )
        workspace.connection.execute(
            "UPDATE position_worksites SET latitude = 0, longitude = "
            "CASE WHEN position_id LIKE '%old' THEN 179 ELSE -179 END"
        )
        positions = _resolved_positions(workspace)
        data, locations, omissions = flow_explanation_inputs(
            workspace,
            positions,
            (Employer("a", "Synthetic a"),),
            market,
            date(2021, 12, 31),
        )
        assert abs(locations["a"][1]) > 170
        assert data["a"].worksites_latlon == ((0, -179), (0, 179))
        assert not omissions
        outsider = tuple(
            replace(p, provenance_id="unrelated-source") for p in positions
        )
        data, locations, omissions = flow_explanation_inputs(
            workspace,
            outsider,
            (Employer("a", "Synthetic a"),),
            market,
            date(2021, 12, 31),
        )
        assert not locations and data["a"].occupation_shares is None
        assert len(omissions) == 2


def test_proposal_wrapper_ignores_only_later_event_changes(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        _, population = load_analytical_fixture(workspace, cross_geographies=True)
        inputs = AnalyticalInputs(
            flow_population=population, cluster_settings=ClusterSettings(2015, 2019)
        )
        _, first = propose_flow_market(
            workspace, inputs, start_year=2020, end_year=2021
        )
        workspace.connection.execute(
            "UPDATE positions SET start_date = '2022-01-01' WHERE id LIKE '2021-%new'"
        )
        _, second = propose_flow_market(
            workspace, inputs, start_year=2020, end_year=2021
        )
        assert second.input_digest == first.input_digest
        assert second.groups == first.groups
        assert second.digest == first.digest
