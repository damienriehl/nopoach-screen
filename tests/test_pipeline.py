import json
from datetime import date

import pytest

from nopoach.market.define import Geography, Market
from nopoach.pipeline import run_screen
from nopoach.store.workspace import Workspace
from nopoach.synth.generator import generate_market
from nopoach.synth.scenarios import named_scenario


def load_resolved_scenario(workspace, scenario):
    """Load the entire U2 fixture at U3's resolved boundary, without mocking signals.

    Loader/generator contracts have separate real-import tests. Running Splink
    separately for every one of these thousands of already identified workers
    would test U3's performance rather than this workspace-to-screen boundary.
    """
    import pyarrow as pa

    generated = generate_market(scenario, seed=42)
    receipt = workspace.provenance.append(
        source="synthetic",
        url="file:resolved-scenario",
        method="IMPORT",
        body=json.dumps(generated.rows, sort_keys=True).encode(),
        collection_method="synthetic",
    )
    connection = workspace.connection
    connection.register("fixture_rows", pa.Table.from_pylist(list(generated.rows)))
    connection.register("true_rows", pa.Table.from_pylist(list(generated.true_rows)))
    try:
        connection.execute(
            "INSERT INTO employers SELECT DISTINCT employer_id, employer_name, NULL, ? "
            "FROM fixture_rows",
            [receipt.id],
        )
        connection.execute(
            "INSERT INTO people SELECT DISTINCT person_key, person_name, ? "
            "FROM fixture_rows",
            [receipt.id],
        )
        connection.execute(
            "CREATE TEMP TABLE resolved_rows AS SELECT 'fixture-' || "
            "row_number() OVER () "
            "AS id, * FROM fixture_rows"
        )
        connection.execute(
            "INSERT INTO positions SELECT id, person_key, employer_id, title, "
            "occupation_code, start_date::DATE, nullif(end_date, '')::DATE, "
            "start_precision, end_precision, ? FROM resolved_rows",
            [receipt.id],
        )
        connection.execute(
            "INSERT INTO position_worksites SELECT id, cbsa, latitude::DOUBLE, "
            "longitude::DOUBLE, 'synthetic', 'supplied_cbsa_centroid', ? "
            "FROM resolved_rows",
            [receipt.id],
        )
        for year in range(scenario.start_year, scenario.end_year + 1):
            connection.execute(
                "INSERT INTO workforce_inputs SELECT 'count-' || employer_id || ?, "
                "employer_id, ?, ?, 'metro', 'SYNTHETIC', 'imported', "
                "count(*), count(*), count(*), 'synthetic', ?::DATE, ? "
                "FROM true_rows WHERE start_date::DATE <= ?::DATE AND "
                "(end_date = '' OR nullif(end_date, '')::DATE >= ?::DATE) "
                "GROUP BY employer_id",
                [
                    str(year),
                    year,
                    json.dumps(sorted(o.code for o in scenario.occupations)),
                    f"{year}-01-01",
                    receipt.id,
                    f"{year}-01-01",
                    f"{year}-01-01",
                ],
            )
    finally:
        connection.execute("DROP TABLE IF EXISTS resolved_rows")
        connection.unregister("fixture_rows")
        connection.unregister("true_rows")


@pytest.fixture(scope="module")
def ae1(tmp_path_factory):
    with Workspace.open(tmp_path_factory.mktemp("pipeline-ae1")) as workspace:
        scenario = named_scenario("AE1")
        load_resolved_scenario(workspace, scenario)
        market = Market(
            frozenset({"29-1141"}), Geography("metro", "SYNTHETIC"), 2015, 2024
        )
        yield workspace, market


def planted(pair):
    return {pair.employer_a_name, pair.employer_b_name} == {
        "Synthetic Amber Hospital",
        "Synthetic Birch Hospital",
    }


def test_ae1_first_mutual_has_2020_break_and_traceable_records(ae1):
    workspace, market = ae1
    result = run_screen(workspace, market)
    pair = result.mutual[0]
    assert planted(pair)
    assert pair.flow.classification == "mutual_suppression"
    for direction in pair.flow.directions:
        assert (
            direction.break_result.year_interval[0]
            <= 2020
            <= direction.break_result.year_interval[1]
        )
        move = next(move for year in direction.years for move in year.moves)
        assert workspace.connection.execute(
            "SELECT count(*) FROM positions WHERE id IN (?, ?)",
            [move.origin_position_id, move.destination_position_id],
        ).fetchone() == (2,)
    references = next(
        r
        for r in result.references
        if r.employer_a_id == pair.employer_a_id
        and r.employer_b_id == pair.employer_b_id
    )
    assert references.position_ids and references.provenance_ids
    assert result.sufficiency.flow.status == "sufficient"
    assert result.partial_screen  # No bulk feed completeness calendar is stored.
    assert result.recommendations
    assert "not remedied" in result.recommendations[0].market_addition


def test_as_of_2019_excludes_future_events_and_has_no_planted_break(ae1):
    workspace, market = ae1
    cutoff = date(2019, 12, 31)
    result = run_screen(workspace, market, as_of=cutoff)
    assert result.as_of == cutoff
    pair = next(p for p in result.mutual if planted(p))
    assert all(d.break_result is None for d in pair.flow.directions)
    assert all(
        y.year <= 2019
        for p in result.mutual + result.one_way
        for d in p.flow.directions
        for y in d.years
    )
    assert all(
        move.started_on <= cutoff and move.ended_on <= cutoff
        for p in result.mutual + result.one_way
        for d in p.flow.directions
        for y in d.years
        for move in y.moves
    )
    future_positions = {
        r[0]
        for r in workspace.connection.execute(
            "SELECT id FROM positions WHERE start_date > ?", [cutoff]
        ).fetchall()
    }
    assert not future_positions.intersection(
        p for refs in result.references for p in refs.position_ids
    )
    # Screening must not censor or delete source workspace facts.
    assert future_positions
    assert workspace.connection.execute(
        "SELECT count(*) FROM positions WHERE start_date > ?", [cutoff]
    ).fetchone()[0] == len(future_positions)


def test_screen_persists_inputs_cutoff_and_summary(ae1):
    workspace, market = ae1
    result = run_screen(
        workspace, market, as_of=date(2019, 12, 31), accept_partial=True
    )
    parameters, summary = workspace.connection.execute(
        "SELECT parameters_json, results_json FROM screens WHERE id = ?",
        [result.screen_id],
    ).fetchone()
    parameters = json.loads(parameters)
    summary = json.loads(summary)
    assert parameters["as_of"] == "2019-12-31"
    assert parameters["accept_partial"] is True
    assert Market.from_json(json.dumps(parameters["market"])) == market
    assert summary["mutual_count"] == len(result.mutual)
    assert summary["partial_screen"] is True
    assert summary["sufficiency"]["flow"]["status"] == result.sufficiency.flow.status


def test_ae2_pair_only_in_one_way_list(tmp_path):
    with Workspace.open(tmp_path / "ae2") as workspace:
        scenario = named_scenario("AE2")
        load_resolved_scenario(workspace, scenario)
        market = Market(
            frozenset({"29-1141"}), Geography("metro", "SYNTHETIC"), 2015, 2024
        )
        result = run_screen(workspace, market)
        assert any(planted(p) for p in result.one_way)
        assert not any(planted(p) for p in result.mutual)


def test_ae3_insufficient_with_wage_records_first(tmp_path):
    with Workspace.open(tmp_path / "ae3") as workspace:
        scenario = named_scenario("AE3")
        load_resolved_scenario(workspace, scenario)
        market = Market(
            frozenset({"51-3022"}), Geography("metro", "SYNTHETIC"), 2015, 2024
        )
        result = run_screen(workspace, market)
        assert result.sufficiency.flow.status == "insufficient"
        assert result.recommendations[0].source.id == "state-ui-wage-records"
        assert result.unreliable
        assert "coverage" in result.unreliable[0].reason
        assert result.mutual == () and result.one_way == ()


def test_workspace_clause_and_tie_evidence_respects_event_cutoff(tmp_path):
    from nopoach.imports.loader import import_records
    from nopoach.market.define import Employer
    from nopoach.signals.clauses import ClauseDocument, store_document

    with Workspace.open(tmp_path / "dated") as workspace:
        rows = []
        for key, name, title, started in (
            ("worker-a", "Synthetic A", "Registered Nurse", "2014-01-01"),
            ("worker-b", "Synthetic B", "Registered Nurse", "2014-01-01"),
            ("director", "Synthetic A", "Director", "2018-01-01"),
            ("director", "Synthetic B", "Director", "2018-01-01"),
            ("future-director", "Synthetic A", "Director", "2021-01-01"),
            ("future-director", "Synthetic B", "Director", "2021-01-01"),
        ):
            rows.append(
                {
                    "person_key": key,
                    "employer_name": name,
                    "title": title,
                    "location": "Fixture",
                    "cbsa": "SYNTHETIC",
                    "latitude": "0",
                    "longitude": "0",
                    "start_date": started,
                    "end_date": "2024-12-31",
                    "start_precision": "day",
                    "end_precision": "day",
                }
            )
        import_records(
            workspace, rows, source="synthetic", provenance_class="synthetic"
        )
        employers = tuple(
            Employer(*r)
            for r in workspace.connection.execute(
                "SELECT id, name FROM employers ORDER BY id"
            ).fetchall()
        )
        receipt = workspace.provenance.append(
            source="synthetic", url="file:contract", method="IMPORT", body=b"contract"
        )
        store_document(
            workspace,
            ClauseDocument(
                "Synthetic A and Synthetic B agree that neither party shall hire "
                "the other party's employees during the term of this agreement.",
                receipt,
                date(2020, 1, 1),
                "synthetic_contract",
                employers[0],
            ),
            employers,
        )
        market = Market(
            frozenset({"29-1141"}), Geography("metro", "SYNTHETIC"), 2015, 2024
        )
        full = run_screen(workspace, market)
        assert full.mutual[0].clauses.clauses
        assert len(full.mutual[0].ties.ties) == 2
        past = run_screen(workspace, market, as_of=date(2019, 12, 31))
        pair = past.mutual[0]  # The independent shared-director lead remains visible.
        assert pair.clauses.clauses == ()
        assert len(pair.ties.ties) == 1
        assert all(
            p.start_date <= date(2019, 12, 31) and p.end_date is None
            for tie in pair.ties.ties
            for p in tie.positions
        )
        assert past.references[0].clause_ids == ()


def test_real_generator_import_reaches_pipeline_with_independent_clause(tmp_path):
    from dataclasses import replace

    from nopoach.synth.generator import import_market

    scenario = named_scenario("AE4")
    scenario = replace(
        scenario,
        employers=tuple(replace(e, size=3) for e in scenario.employers),
        start_year=2019,
        end_year=2021,
    )
    with Workspace.open(tmp_path / "real-import") as workspace:
        imported = import_market(workspace, generate_market(scenario, seed=42))
        assert imported.rejects == ()
        market = Market(
            frozenset({"29-1141"}), Geography("metro", "SYNTHETIC"), 2019, 2021
        )
        result = run_screen(workspace, market)
        assert result.mutual[0].clauses.has_mutual_clause
        assert result.references[0].clause_ids and result.references[0].filing_ids
        past = run_screen(workspace, market, as_of=date(2019, 12, 31))
        assert all(not p.pair.clauses.clauses for p in past.unreliable)


def test_period_scoped_lca_and_firm_wide_hires_supply_explanations(tmp_path):
    import httpx

    from nopoach.collect.dol import collect_dol
    from nopoach.collect.fetch import FetchClient
    from nopoach.imports.loader import import_records
    from nopoach.resolve.employers import EmployerResolver

    with Workspace.open(tmp_path / "explanations") as workspace:
        rows = []
        for key, name, code, started, longitude in (
            ("a", "Synthetic A", "29-1141", "2014-01-01", "0"),
            ("b", "Synthetic B", "29-1141", "2014-01-01", "3"),
            ("new-accountant", "Synthetic A", "13-2011", "2018-01-01", "0"),
        ):
            rows.append(
                {
                    "person_key": key,
                    "employer_name": name,
                    "title": "Fixture",
                    "occupation_code": code,
                    "location": "Fixture",
                    "cbsa": "88888" if name == "Synthetic B" else "99999",
                    "latitude": "0",
                    "longitude": longitude,
                    "start_date": started,
                    "end_date": "",
                    "start_precision": "day",
                    "end_precision": "unknown",
                }
            )
        import_records(
            workspace, rows, source="synthetic", provenance_class="synthetic"
        )
        before = run_screen(
            workspace,
            Market(frozenset({"29-1141"}), Geography("metro", "99999"), 2015, 2019),
        )
        geography = next(
            check
            for check in before.unreliable[0].pair.explanations
            if check.key == "geographic_separation"
        )
        assert geography.status == "passed"
        assert geography.measurements["distance_km"] == pytest.approx(333.58478)
        body = (
            b"CASE_NUMBER,EMPLOYER_NAME,SOC_CODE,WORKSITE_CITY,WORKSITE_STATE,"
            b"WORKSITE_POSTAL_CODE,WAGE_RATE_OF_PAY_FROM,WAGE_RATE_OF_PAY_TO,"
            b"WAGE_UNIT_OF_PAY,TOTAL_WORKER_POSITIONS,DECISION_DATE,"
            b"EMPLOYMENT_BEGIN_DATE,EMPLOYMENT_END_DATE\n"
            b"A-old,Synthetic A,29-1141,Fixture,ZZ,00000,900000,,Year,"
            b"1,2001-01-01,2001-01-01,2002-12-31\n"
            b"B-old,Synthetic B,29-1141,Fixture,ZZ,00000,900000,,Year,"
            b"1,2001-01-01,2001-01-01,2002-12-31\n"
            b"A-current,Synthetic A,29-1141,Fixture,ZZ,00000,100000,,Year,"
            b"1,2018-01-01,2018-01-01,2021-12-31\n"
            b"B-current,Synthetic B,29-1141,Fixture,ZZ,00000,150000,,Year,"
            b"1,2018-01-01,2018-01-01,2021-12-31\n"
            b"A-future,Synthetic A,29-1141,Fixture,ZZ,00000,900000,,Year,"
            b"1,2021-01-01,2021-01-01,2022-12-31\n"
        )
        with FetchClient(
            workspace.provenance,
            contact="test@example.invalid",
            allowed_hosts={"fixture.invalid": 1},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=body)
            ),
        ) as client:
            collect_dol(
                client,
                "https://fixture.invalid/lca.csv",
                EmployerResolver(workspace),
                program="LCA",
                metro_lookup=lambda city, state, postal: "99999",
            )
        market = Market(frozenset({"29-1141"}), Geography("metro", "99999"), 2015, 2024)
        result = run_screen(workspace, market, as_of=date(2019, 12, 31))
        pair = result.unreliable[0].pair
        checks = {check.key: check for check in pair.explanations}
        # The shared LCA worksite adds a footprint even though position CBSA differs.
        assert checks["geographic_separation"].status == "failed"
        assert checks["geographic_separation"].measurements["distance_km"] == 0
        assert checks["pay_gap"].status == "passed"
        assert checks["pay_gap"].measurements["relative_gap"] == pytest.approx(1 / 3)
        assert checks["occupational_mismatch"].measurements["overlap"] == 0.5
        key = "a_rate" if pair.employer_a_name == "Synthetic A" else "b_rate"
        exposure = (
            (date(2019, 12, 31) - date(2015, 1, 1)).days
            + 1
            + (date(2019, 12, 31) - date(2018, 1, 1)).days
            + 1
        ) / 365.25
        assert checks["low_lateral_hiring"].measurements[key] == pytest.approx(
            1 / exposure
        )
        assert len(result.references[0].dol_record_ids) == 4


def test_cutoff_censors_month_precision_ends_in_pair_evidence(tmp_path):
    from nopoach.imports.loader import import_records

    with Workspace.open(tmp_path / "month-cutoff") as workspace:
        rows = [
            {
                "person_key": "director",
                "employer_name": name,
                "title": "Director",
                "location": "Fixture",
                "start_date": "2018",
                "start_precision": "year",
                "end_date": "2019-06",
                "end_precision": "month",
            }
            for name in ("Synthetic A", "Synthetic B")
        ]
        import_records(
            workspace, rows, source="synthetic", provenance_class="synthetic"
        )
        market = Market(frozenset({"29-1141"}), Geography("metro", "99999"), 2018, 2020)
        result = run_screen(workspace, market, as_of=date(2019, 6, 15))
        tie = result.mutual[0].ties.ties[0]
        assert all(
            p.end_date is None and p.end_precision == "unknown" for p in tie.positions
        )
        assert all(
            record.raw["end_date"] == "" for p in tie.positions for record in p.records
        )


def import_site(workspace, key, name, metro, longitude, *, filed="2018-01-01"):
    from nopoach.imports.loader import import_records

    result = import_records(
        workspace,
        [
            {
                "person_key": key,
                "employer_name": name,
                "title": "Director",
                "occupation_code": "29-1141",
                "location": "Synthetic site",
                "cbsa": metro,
                "latitude": "0",
                "longitude": str(longitude),
                "start_date": "2018-01-01",
                "start_precision": "day",
                "end_date": "",
                "end_precision": "unknown",
                "filed_on": filed,
            }
        ],
        source="synthetic",
        provenance_class="synthetic",
    )
    assert result.rejects == ()
    return result


def store_test_lca(
    workspace,
    employer,
    receipt,
    case,
    metro,
    *,
    decision="2018-01-01",
    begin="2018-01-01",
    end="2021-12-31",
    state="ZZ",
):
    from dataclasses import asdict
    from decimal import Decimal

    from nopoach.collect.dol import DolRecord

    record = DolRecord(
        "LCA",
        case,
        "Synthetic employer",
        employer,
        "29-1141",
        "Synthetic site",
        state,
        "00000",
        metro,
        Decimal(100000),
        None,
        "Year",
        Decimal(100000),
        None,
        10,
        "synthetic.csv",
        receipt,
        date.fromisoformat(decision),
        date.fromisoformat(begin),
        date.fromisoformat(end),
    )
    workspace.connection.execute(
        "INSERT INTO dol_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            case,
            employer,
            "LCA",
            case,
            record.decision_date,
            record.employment_begin_date,
            record.employment_end_date,
            json.dumps({"version": 1, "record": asdict(record)}, default=str),
            receipt.id,
        ],
    )


@pytest.mark.parametrize("spanning", [False, True])
def test_cutoff_filters_dated_inputs_censors_spanning_records_and_preserves_source(
    tmp_path,
    spanning,
):
    from nopoach.collect.dol import load_dol_records
    from nopoach.market.coverage import Headcount, store_workforce_input
    from nopoach.pipeline import _filtered_workspace

    cutoff = date(2019, 6, 15)
    record_end = date(2021, 12, 31) if spanning else cutoff
    market = Market(frozenset({"29-1141"}), Geography("metro", "99999"), 2018, 2021)
    with Workspace.open(tmp_path / "cutoff-inputs") as workspace:
        past = import_site(workspace, "director", "Synthetic A", "99999", 0)
        import_site(workspace, "director", "Synthetic B", "99999", 1)
        future = import_site(
            workspace,
            "future-filing",
            "Synthetic A",
            "99999",
            0,
            filed="2019-06-16",
        )
        db = workspace.connection
        a, b = [
            r[0]
            for r in db.execute("SELECT id FROM employers ORDER BY name").fetchall()
        ]
        person = db.execute(
            "SELECT person_id FROM positions WHERE id = ?", [past.position_ids[0]]
        ).fetchone()[0]
        receipt = next(
            r for r in workspace.provenance.records() if r.id == past.provenance_id
        )
        counts = {
            label: store_workforce_input(
                workspace,
                a,
                2019,
                market,
                kind="imported",
                headcount=Headcount(value, value, value, "synthetic count"),
                as_of=when,
                provenance_id=receipt.id,
            )
            for label, when, value in (
                ("past", cutoff, 20),
                ("future", date(2019, 6, 16), 99),
            )
        }
        for case, decision, begin in (
            ("spanning", "2019-06-15", "2019-06-15"),
            ("future-decision", "2019-06-16", "2018-01-01"),
            ("future-start", "2018-01-01", "2019-06-16"),
        ):
            store_test_lca(
                workspace,
                a,
                receipt,
                case,
                "99999",
                decision=decision,
                begin=begin,
                end=record_end.isoformat() if case == "spanning" else "2021-12-31",
            )
        for label, started in (
            ("spanning-tie", cutoff),
            ("future-tie", date(2019, 6, 16)),
        ):
            db.execute(
                "INSERT INTO ties VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    label,
                    person,
                    a,
                    b,
                    "shared_director",
                    started,
                    record_end if label == "spanning-tie" else date(2021, 12, 31),
                    receipt.id,
                ],
            )
        tables = (
            "positions",
            "position_worksites",
            "imported_rows",
            "workforce_inputs",
            "dol_records",
            "ties",
        )
        original = {
            t: db.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in tables
        }
        for as_of in (None, cutoff):
            view = _filtered_workspace(workspace, as_of)
            try:
                expected_imports = set(past.position_ids + future.position_ids)
                if as_of:
                    expected_imports -= set(future.position_ids)
                actual = {
                    r[0]
                    for r in view.connection.execute(
                        "SELECT position_id FROM imported_rows"
                    ).fetchall()
                }
                assert (
                    actual.intersection(set(past.position_ids + future.position_ids))
                    == expected_imports
                )
                assert {
                    r[0]
                    for r in view.connection.execute(
                        "SELECT id FROM workforce_inputs"
                    ).fetchall()
                } == ({counts["past"]} if as_of else set(counts.values()))
                records = load_dol_records(view)
                assert {r.case_number for r in records} == (
                    {"spanning"}
                    if as_of
                    else {"spanning", "future-decision", "future-start"}
                )
                assert next(
                    r for r in records if r.case_number == "spanning"
                ).employment_end_date == (cutoff if as_of else record_end)
                assert view.connection.execute(
                    "SELECT id, end_date FROM ties ORDER BY id"
                ).fetchall() == (
                    [("spanning-tie", None if spanning else cutoff)]
                    if as_of
                    else [
                        ("future-tie", date(2021, 12, 31)),
                        ("spanning-tie", record_end),
                    ]
                )
            finally:
                view.close()
        screen = run_screen(workspace, market, as_of=cutoff)
        ref = screen.references[0]
        assert ref.dol_record_ids == ("spanning",)
        assert ref.tie_ids == ("spanning-tie",)
        assert ref.workforce_input_ids == (counts["past"],)
        assert not set(future.position_ids).intersection(ref.position_ids)
        assert {
            t: db.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in tables
        } == original


@pytest.mark.parametrize(
    "kind, use_lca", [("metro", False), ("metro", True), ("state", True)]
)
def test_gravity_uses_mean_in_market_sites_and_explanations_keep_all_sites(
    tmp_path,
    monkeypatch,
    kind,
    use_lca,
):
    from math import dist

    import nopoach.pipeline as pipeline

    with Workspace.open(tmp_path / "gravity-sites") as workspace:
        imported = import_site(workspace, "director", "Synthetic A", "99999", 0)
        import_site(workspace, "a-second", "Synthetic A", "99999", 2)
        import_site(workspace, "a-outside", "Synthetic A", "88888", -10)
        import_site(
            workspace, "director", "Synthetic B", "66666" if use_lca else "99999", 4
        )
        import_site(workspace, "b-outside", "Synthetic B", "77777", -10)
        # This employer has no site in the selected market.
        import_site(workspace, "c-outside", "Synthetic C", "77777", -10)
        db = workspace.connection
        ids = dict(db.execute("SELECT name, id FROM employers").fetchall())
        receipt = next(
            r for r in workspace.provenance.records() if r.id == imported.provenance_id
        )
        if use_lca:
            # In metro mode B's LCA supplies its local footprint; out-of-market
            # LCA evidence must stay available only to explanations.
            store_test_lca(workspace, ids["Synthetic B"], receipt, "b-local", "99999")
            store_test_lca(
                workspace, ids["Synthetic B"], receipt, "b-outside", "77777", state="YY"
            )
            if kind == "state":
                for case, metro in (("a-west", "99999"), ("a-east", "88888")):
                    store_test_lca(workspace, ids["Synthetic A"], receipt, case, metro)
        captured = []
        original_flow = pipeline.screen_flow

        def observe_flow(market, positions, employers, coverage):
            captured.extend(employers)
            return original_flow(market, positions, employers, coverage)

        monkeypatch.setattr(pipeline, "screen_flow", observe_flow)
        market = Market(
            frozenset({"29-1141"}),
            Geography(kind, "ZZ" if kind == "state" else "99999"),
            2018,
            2020,
        )
        result = run_screen(workspace, market)
        locations = {e.id: e.location_km for e in captured}
        assert set(locations) == {ids["Synthetic A"], ids["Synthetic B"]}
        # At latitude zero one degree of longitude is 111.19492664455873 km.
        # Metro A is at 1 degree, B at 4 (positions) or 1 (LCA).
        # State A averages its two LCA metro sites (1 and -10), B is at 1.
        degrees = 5.5 if kind == "state" else 0 if use_lca else 3
        assert dist(
            locations[ids["Synthetic A"]], locations[ids["Synthetic B"]]
        ) == pytest.approx(degrees * 111.19492664455873)
        pair = next(
            p
            for p in result.mutual
            if {p.employer_a_name, p.employer_b_name} == {"Synthetic A", "Synthetic B"}
        )
        check = next(c for c in pair.explanations if c.key == "geographic_separation")
        assert check.measurements["distance_km"] == 0  # Shared outside site retained.
