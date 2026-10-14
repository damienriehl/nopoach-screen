import json
from datetime import date

import pyarrow as pa

from nopoach.backtest.run import CaseDefinition, load_cases, run_backtest
from nopoach.collect.sec_people import parse_filing
from nopoach.imports.loader import import_records
from nopoach.market.define import Employer, Geography, Market
from nopoach.signals.clauses import ClauseDocument, store_document
from nopoach.store.workspace import Workspace
from nopoach.synth.generator import generate_market
from nopoach.synth.scenarios import named_scenario


def load_scenario(workspace, name):
    """Use U2 ground-truth identities at the resolved U3 boundary."""
    scenario = named_scenario(name)
    generated = generate_market(scenario, seed=42)
    receipt = workspace.provenance.append(
        source="synthetic",
        url="file:backtest-fixture",
        method="IMPORT",
        body=b"invented market",
        collection_method="synthetic",
    )
    db = workspace.connection
    db.register("fixture", pa.Table.from_pylist(list(generated.rows)))
    db.register("truth", pa.Table.from_pylist(list(generated.true_rows)))
    db.execute(
        "INSERT INTO employers SELECT DISTINCT employer_id, employer_name, "
        "NULL, ? FROM fixture",
        [receipt.id],
    )
    db.execute(
        "INSERT INTO people SELECT DISTINCT person_key, person_name, ? FROM fixture",
        [receipt.id],
    )
    db.execute(
        "CREATE TEMP TABLE spells AS SELECT 'fixture-' || row_number() OVER () "
        "AS id, * FROM fixture"
    )
    db.execute(
        "INSERT INTO positions SELECT id, person_key, employer_id, title, "
        "occupation_code, start_date::DATE, nullif(end_date, '')::DATE, "
        "start_precision, end_precision, ? FROM spells",
        [receipt.id],
    )
    db.execute(
        "INSERT INTO position_worksites SELECT id, cbsa, latitude::DOUBLE, "
        "longitude::DOUBLE, 'synthetic', 'supplied_cbsa_centroid', ? FROM spells",
        [receipt.id],
    )
    for year in range(2015, 2025):
        db.execute(
            "INSERT INTO workforce_inputs SELECT 'count-' || employer_id || ?, "
            "employer_id, ?, ?, 'metro', 'SYNTHETIC', 'imported', count(*), "
            "count(*), count(*), 'synthetic', ?::DATE, ? FROM truth WHERE "
            "start_date::DATE <= ?::DATE AND (end_date = '' OR nullif(end_date, "
            "'')::DATE >= ?::DATE) GROUP BY employer_id",
            [
                str(year),
                year,
                json.dumps([scenario.occupations[0].code]),
                f"{year}-01-01",
                receipt.id,
                f"{year}-01-01",
                f"{year}-01-01",
            ],
        )
    db.execute("DROP TABLE spells")
    db.unregister("fixture")
    db.unregister("truth")
    return Market(
        frozenset({scenario.occupations[0].code}),
        Geography("metro", "SYNTHETIC"),
        2015,
        2024,
    )


def synthetic_case(name="AE1"):
    poultry = name == "AE3"
    return CaseDefinition(
        id=name,
        name="Invented backtest case",
        parties=("Synthetic Amber Poultry", "Synthetic Birch Poultry")
        if poultry
        else ("Synthetic Amber Hospital", "Synthetic Birch Hospital"),
        occupation="Invented workers",
        geography="Invented metro",
        agreement_start_year=2020,
        agreement_end_year=2024,
        disclosure_date=date(2025, 1, 1),
        known_pairs=(("Synthetic Amber Poultry", "Synthetic Birch Poultry"),)
        if poultry
        else (("Synthetic Amber Hospital", "Synthetic Birch Hospital"),),
        sources=(),
        notes="Synthetic ground truth.",
    )


def test_public_case_catalog():
    cases = {case.id: case for case in load_cases()}
    assert set(cases) == {
        "high-tech",
        "animation-workers",
        "knorr-wabtec",
        "seaman-duke",
        "patel",
    }
    assert cases["high-tech"].disclosure_date == date(2010, 9, 24)
    assert cases["knorr-wabtec"].disclosure_date == date(2018, 4, 3)
    assert cases["patel"].disclosure_date == date(2021, 12, 9)
    assert "acquitt" in cases["patel"].notes.lower()
    assert ("Apple", "Google") in cases["high-tech"].known_pairs
    assert ("Adobe Systems", "Intel") not in cases["high-tech"].known_pairs
    for case in cases.values():
        assert case.sources and case.occupation and case.geography
        assert all(a in case.parties and b in case.parties for a, b in case.known_pairs)


def test_planted_pair_rank_percentile_and_readable_workspace_report(tmp_path):
    with Workspace.open(tmp_path / "synthetic") as workspace:
        market = load_scenario(workspace, "AE1")
        report = run_backtest(workspace, {"AE1": market}, cases=(synthetic_case(),))
        result = report.cases[0]
        pair = result.pairs[0]
        assert (
            pair.status,
            pair.ranking,
            pair.rank,
            pair.population,
            pair.percentile,
        ) == ("ranked", "mutual", 1, 15, 100.0)
        assert "flow" in pair.pair.fired_signals
        assert result.sufficiency.flow.status == "sufficient"
        assert result.partial_screen
        assert report.json_path.is_relative_to(workspace.root)
        assert report.markdown_path.is_relative_to(workspace.root)
        stored = json.loads(report.json_path.read_text())
        assert stored["cases"][0]["pairs"][0]["rank"] == 1
        text = report.markdown_path.read_text()
        assert "Synthetic Amber Hospital" in text
        assert "1 / 15" in text and "100.00" in text
        assert "record-availability" in text
        assert "insufficient" in text  # Missing bulk-feed completeness is disclosed.
        assert "Synthetic ground truth" in text


def test_insufficient_coverage_has_no_rank(tmp_path):
    with Workspace.open(tmp_path / "sparse") as workspace:
        market = load_scenario(workspace, "AE3")
        report = run_backtest(
            workspace, {"AE3": market}, cases=(synthetic_case("AE3"),)
        )
        pair = report.cases[0].pairs[0]
        assert pair.status == "insufficient data"
        assert pair.rank is pair.percentile is pair.ranking is None
        assert "coverage" in pair.reason
        assert "insufficient data" in report.markdown_path.read_text()


def add_sec_director(workspace, employer, cik, owner, filed):
    body = f"""<ownershipDocument><issuer><issuerCik>{cik}</issuerCik>
    <issuerName>{employer}</issuerName></issuer><reportingOwner>
    <reportingOwnerId><rptOwnerCik>{owner}</rptOwnerCik>
    <rptOwnerName>Synthetic Director {owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
    </reportingOwner></ownershipDocument>""".encode()
    rows = parse_filing(
        body, form_type="4", filed_on=filed, cik=cik, employer_name=employer
    )
    imported = import_records(
        workspace,
        rows,
        source="SEC fixture",
        provenance_class="synthetic",
        person_key_namespace="sec_people",
    )
    assert imported.rejects == ()
    return imported.position_ids


def test_high_tech_sec_signals_and_strict_disclosure_cutoff(tmp_path):
    case = next(case for case in load_cases() if case.id == "high-tech")
    with Workspace.open(tmp_path / "sec") as workspace:
        past_ids, future_ids = [], []
        for employer, cik in (("Apple", "1"), ("Google", "2")):
            past_ids.extend(
                add_sec_director(workspace, employer, cik, "9001", "2009-01-01")
            )
            for owner, filed in (("9002", "2010-09-24"), ("9003", "2011-01-01")):
                future_ids.extend(
                    add_sec_director(workspace, employer, cik, owner, filed)
                )
        employers = tuple(
            Employer(*r)
            for r in workspace.connection.execute(
                "SELECT id, name FROM employers ORDER BY name"
            ).fetchall()
        )
        receipt = workspace.provenance.append(
            source="synthetic",
            url="file:sec-exhibit",
            method="IMPORT",
            body=b"invented clauses",
        )
        for filed in (date(2008, 1, 1), date(2010, 9, 24), date(2011, 1, 1)):
            store_document(
                workspace,
                ClauseDocument(
                    "Apple and Google agree that neither party shall hire the "
                    "other party's employees during the term of this agreement.",
                    receipt,
                    filed,
                    "EX-10",
                    employers[0],
                ),
                employers,
            )
        market = Market(frozenset({"15-1132"}), Geography("metro", "41940"), 2005, 2011)
        report = run_backtest(workspace, {case.id: market}, cases=(case,))
        result = report.cases[0]
        pair = next(p for p in result.pairs if p.parties == ("Apple", "Google"))
        assert result.cutoff == date(2010, 9, 23)
        assert pair.status == "insufficient data"
        assert pair.rank is pair.percentile is None
        assert pair.pair.fired_signals == ("clauses", "ties")
        assert len(pair.pair.ties.ties) == 1
        assert pair.pair.ties.ties[0].tie_type == "shared_director"
        assert len(pair.pair.clauses.clauses) == 1
        assert pair.pair.clauses.clauses[0].filed_on == date(2008, 1, 1)
        assert result.sufficiency.flow.status == "insufficient"
        assert not set(future_ids).intersection(
            p for ref in result.references for p in ref.position_ids
        )
        assert set(past_ids).issubset(
            p for ref in result.references for p in ref.position_ids
        )
        assert workspace.connection.execute(
            "SELECT count(*) FROM positions"
        ).fetchone() == (6,)
        parameters = json.loads(
            workspace.connection.execute(
                "SELECT parameters_json FROM screens WHERE id = ?", [result.screen_id]
            ).fetchone()[0]
        )
        assert parameters["as_of"] == "2010-09-23"
        missing = next(p for p in result.pairs if p.parties == ("Google", "Intel"))
        assert missing.status == "insufficient data" and missing.rank is None
