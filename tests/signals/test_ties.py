import json
import os
from datetime import date
from pathlib import Path

import pytest

from nopoach.collect.fetch import FetchClient
from nopoach.collect.form990 import parse_return
from nopoach.collect.sec_people import parse_filing
from nopoach.imports.loader import import_records
from nopoach.imports.schema import ProvenanceClass
from nopoach.signals.ties import score_pair
from nopoach.store.workspace import Workspace

FIXTURES = Path(__file__).parents[1] / "fixtures"


def position(person, employer, title, start, end, **extra):
    return {
        "person_key": person,
        "person_name": person + " Synthetic",
        "employer_name": employer,
        "title": title,
        "location": "Synthetic City",
        "start_date": start,
        "end_date": end,
        "start_precision": "year" if start else "unknown",
        "end_precision": "year" if end else "unknown",
        **extra,
    }


def load(workspace, rows):
    result = import_records(
        workspace,
        rows,
        source="synthetic-ties",
        provenance_class=ProvenanceClass.GOVERNMENT_ADMINISTRATIVE,
    )
    assert result.accepted_count == len(rows)
    return dict(
        workspace.connection.execute("SELECT name, id FROM employers").fetchall()
    )


def screen(workspace, employers, start=2006, end=2008):
    return score_pair(
        workspace,
        employers["Synthetic Alpha"],
        employers["Synthetic Beta"],
        start_date=date(start, 1, 1),
        end_date=date(end, 12, 31),
    )


def test_shared_director_overlap_dates_and_traceable_score(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Robin", "Synthetic Alpha", "Director", "2005", "2008"),
                position("Robin", "Synthetic Beta", "Director", "2006", "2010"),
            ],
        )
        result = screen(workspace, employers)
        assert len(result.ties) == 1
        tie = result.ties[0]
        assert tie.tie_type == "shared_director"
        assert (tie.start_date, tie.end_date) == (date(2006, 1, 1), date(2008, 12, 31))
        assert tie.base_weight == 4.0
        assert tie.recency_factor == 1.0
        assert tie.score == result.score == 4.0
        assert len(tie.person_ids) == 1
        assert {p.title for p in tie.positions} == {"Director"}
        assert {p.start_precision for p in tie.positions} == {"year"}
        for evidence in tie.positions:
            assert evidence.person_id == tie.person_ids[0]
            assert workspace.connection.execute(
                "SELECT provenance_id FROM positions WHERE id = ?", [evidence.id]
            ).fetchone() == (evidence.provenance_id,)
            record = evidence.records[0]
            assert record.raw["person_key"] == "Robin"
            assert workspace.connection.execute(
                "SELECT position_id FROM imported_rows "
                "WHERE import_id = ? AND row_number = ?",
                [record.import_id, record.row_number],
            ).fetchone() == (evidence.id,)


def test_nonoverlapping_director_has_sequential_link(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Robin", "Synthetic Alpha", "Director", "2000", "2004"),
                position("Robin", "Synthetic Beta", "Director", "2009", "2011"),
            ],
        )
        result = screen(workspace, employers, 2000, 2011)
        assert [tie.tie_type for tie in result.ties] == ["sequential_link"]
        assert (result.ties[0].start_date, result.ties[0].end_date) == (
            date(2004, 12, 31),
            date(2009, 1, 1),
        )
        assert [p.employer_name for p in result.ties[0].positions] == [
            "Synthetic Alpha",
            "Synthetic Beta",
        ]
        assert 0 < result.score < 4


def test_ceo_move_retains_direction_and_event_dates(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Alex", "Synthetic Alpha", "CEO", "2000", "2006"),
                position(
                    "Alex", "Synthetic Beta", "Chief Executive Officer", "2007", "2008"
                ),
            ],
        )
        result = screen(workspace, employers)
        tie = result.ties[0]
        assert tie.tie_type == "executive_move"
        assert (tie.start_date, tie.end_date) == (date(2006, 12, 31), date(2007, 1, 1))
        assert [p.employer_name for p in tie.positions] == [
            "Synthetic Alpha",
            "Synthetic Beta",
        ]
        assert tie.base_weight == 2.0
        reversed_result = score_pair(
            workspace,
            employers["Synthetic Beta"],
            employers["Synthetic Alpha"],
            start_date=date(2006, 1, 1),
            end_date=date(2008, 12, 31),
        )
        assert reversed_result == result


def test_form990_shared_director_and_board_chair_in_2019(tmp_path):
    body = (FIXTURES / "form990" / "return.xml").read_bytes()
    rows = []
    for ein, name in [
        ("111111111", "Synthetic Alpha"),
        ("222222222", "Synthetic Beta"),
    ]:
        filing = body.replace(b"123456789", ein.encode()).replace(
            b"Synthetic Hospital", name.encode()
        )
        for row in parse_return(filing):
            # Explicit, fixture-only identity reconciliation, not a name matcher.
            row["person_key"] = row["person_name"]
            rows.append(row)
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(workspace, rows)
        result = screen(workspace, employers, 2019, 2019)
        directors = [tie for tie in result.ties if tie.tie_type == "shared_director"]
        assert len(directors) == 2
        assert all(
            (tie.start_date, tie.end_date) == (date(2019, 1, 1), date(2019, 12, 31))
            for tie in directors
        )
        assert any(
            "Board Chair" in [p.title for p in tie.positions] for tie in directors
        )
        assert all(
            p.records[0].raw["tax_year"] == "2019"
            for t in directors
            for p in t.positions
        )


def test_sec_proxy_fixture_produces_executive_move(tmp_path):
    rows = parse_filing(
        (FIXTURES / "sec" / "proxy.html").read_bytes(),
        form_type="DEF 14A",
        filed_on="2020-04-01",
        cik="1111",
        employer_name="Synthetic Market Works",
    )
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(workspace, rows)
        result = screen(workspace, employers, 2010, 2020)
        assert [tie.tie_type for tie in result.ties] == ["executive_move"]
        assert result.ties[0].start_date == date(2012, 12, 31)
        assert result.ties[0].end_date == date(2013, 1, 1)
        assert {p.records[0].raw["form_type"] for p in result.ties[0].positions} == {
            "DEF 14A"
        }


def test_form4_dates_are_observations_and_original_receipts_survive(tmp_path):
    body = (
        (FIXTURES / "sec" / "form4.xml")
        .read_bytes()
        .replace(b"<isDirector>0</isDirector>", b"<isDirector>1</isDirector>")
    )
    with Workspace.open(tmp_path / "data") as workspace:
        rows = []
        for cik, company in [("1111", "Synthetic Alpha"), ("2222", "Synthetic Beta")]:
            filing = body.replace(b"0000001111", cik.encode()).replace(
                b"Synthetic Market Works", company.encode()
            )
            receipt = workspace.provenance.append(
                source="Synthetic SEC",
                url=f"https://www.sec.gov/Archives/{cik}.xml",
                method="GET",
                body=filing,
                collection_method="public_http_fetch",
            )
            parsed = parse_filing(
                filing,
                form_type="4",
                filed_on="2020-02-03",
                cik=cik,
                employer_name=company,
            )
            rows.extend(
                dict(row, source_provenance_id=receipt.id, source_url=receipt.url)
                for row in parsed
            )
        employers = load(workspace, rows)
        result = screen(workspace, employers, 2020, 2020)
        tie = result.ties[0]
        assert tie.tie_type == "shared_director"
        assert tie.start_date == tie.end_date == date(2020, 2, 3)
        assert all(p.start_date is p.end_date is None for p in tie.positions)
        assert {p.date_basis for p in tie.positions} == {"filing_observation"}
        assert (
            len({p.records[0].raw["source_provenance_id"] for p in tie.positions}) == 2
        )


def test_executives_who_previously_worked_together(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Alex", "Synthetic Alpha", "CEO", "2006", "2008"),
                position("Casey", "Synthetic Beta", "President", "2006", "2008"),
                position("Alex", "Synthetic Prior", "Engineer", "2000", "2004"),
                position("Casey", "Synthetic Prior", "Accountant", "2002", "2005"),
            ],
        )
        result = screen(workspace, employers)
        assert [t.tie_type for t in result.ties] == ["former_coworkers"]
        tie = result.ties[0]
        assert len(tie.person_ids) == 2
        assert len(tie.positions) == 4
        assert (tie.start_date, tie.end_date) == (date(2002, 1, 1), date(2004, 12, 31))
        assert tie.base_weight == 0.5


def test_pair_with_no_shared_people_or_history_scores_zero(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Alex", "Synthetic Alpha", "CEO", "2006", "2008"),
                position("Casey", "Synthetic Beta", "CEO", "2006", "2008"),
            ],
        )
        result = screen(workspace, employers)
        assert result.score == 0.0
        assert result.ties == ()


def test_type_and_recency_weights_are_explainable(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Robin", "Synthetic Alpha", "Director", "2006", "2008"),
                position("Robin", "Synthetic Beta", "Director", "2006", "2008"),
                position("Alex", "Synthetic Alpha", "CEO", "2000", "2006"),
                position("Alex", "Synthetic Beta", "CEO", "2007", "2008"),
            ],
        )
        recent = screen(workspace, employers)
        old = screen(workspace, employers, 2018, 2018)
        assert recent.score == pytest.approx(4.0 + 2 / (1 + 730 / 365.25))
        assert old.score < recent.score
        assert all(t.score == t.base_weight * t.recency_factor for t in old.ties)
        assert sum(t.score for t in old.ties) == old.score
        assert (
            next(t for t in old.ties if t.tie_type == "shared_director").base_weight
            == 1.0
        )


def test_duplicate_filing_observations_do_not_inflate_score(tmp_path):
    rows = [
        position("Robin", "Synthetic Alpha", "Director", "2006", "2008"),
        position("Robin", "Synthetic Beta", "Director", "2006", "2008"),
    ]
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(workspace, rows + rows)
        result = screen(workspace, employers)
        assert result.score == 4.0
        assert len(result.ties) == 1
        assert len(result.ties[0].positions) == 4


def test_overlapping_filing_observations_describe_one_board_tie(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Robin", "Synthetic Alpha", "Director", "2006", "2008"),
                position("Robin", "Synthetic Beta", "Director", "2006", "2008"),
                position("Robin", "Synthetic Alpha", "Director", "2006", "2007"),
                position("Robin", "Synthetic Beta", "Director", "2007", "2008"),
            ],
        )
        result = screen(workspace, employers)
        assert result.score == 4.0
        assert len(result.ties) == 1
        assert len(result.ties[0].positions) == 4
        assert (result.ties[0].start_date, result.ties[0].end_date) == (
            date(2006, 1, 1),
            date(2008, 12, 31),
        )


def test_former_coworkers_need_not_hold_later_executive_roles_simultaneously(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Alex", "Synthetic Alpha", "CEO", "2006", "2007"),
                position("Casey", "Synthetic Beta", "CEO", "2008", "2008"),
                position("Alex", "Synthetic Prior", "Engineer", "2000", "2004"),
                position("Casey", "Synthetic Prior", "Engineer", "2000", "2004"),
            ],
        )
        result = screen(workspace, employers)
        assert [t.tie_type for t in result.ties] == ["former_coworkers"]
        assert (result.ties[0].start_date, result.ties[0].end_date) == (
            date(2000, 1, 1),
            date(2004, 12, 31),
        )


def test_unknown_dates_keep_link_without_claiming_board_overlap(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position("Robin", "Synthetic Alpha", "Director", "", ""),
                position("Robin", "Synthetic Beta", "Director", "", ""),
            ],
        )
        tie = screen(workspace, employers).ties[0]
        assert tie.tie_type == "leadership_link"
        assert tie.start_date is tie.end_date is None
        assert tie.score == 0.25


def test_future_moves_and_nonboard_director_titles_are_not_board_ties(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        employers = load(
            workspace,
            [
                position(
                    "Robin",
                    "Synthetic Alpha",
                    "Director of Engineering",
                    "2006",
                    "2008",
                ),
                position(
                    "Robin", "Synthetic Beta", "Director of Engineering", "2006", "2008"
                ),
                position("Alex", "Synthetic Alpha", "CEO", "2006", "2008"),
                position("Alex", "Synthetic Beta", "CEO", "2010", "2012"),
            ],
        )
        assert screen(workspace, employers).score == 0.0


@pytest.mark.live
def test_live_sec_ties_match_host_hand_checked_sample(tmp_path):
    """Host supplies SEC_CONTACT and U9_TIES_LIVE_SAMPLE (JSON path).

    Manifest: start_date/end_date (ISO), employer_a_name/employer_b_name,
    filings [{url, form_type, filed_on, cik, employer_name, resolved_keys}], and
    expected_ties [{tie_type, person_names (sorted), start_date, end_date}].
    resolved_keys maps parser person_key to a host-confirmed canonical key;
    it is an explicit assertion of identity checked against those filings.
    Keep the manifest and fetched bodies outside the repository. Expected ties
    must be independently checked against the filings before running this test.
    """
    manifest_path = os.environ.get("U9_TIES_LIVE_SAMPLE")
    contact = os.environ.get("SEC_CONTACT")
    if not manifest_path or not contact:
        pytest.skip("Host must supply U9_TIES_LIVE_SAMPLE and SEC_CONTACT")
    sample = json.loads(Path(manifest_path).read_text())
    assert sample["expected_ties"], "The live sample must contain a known tie"
    with Workspace.open(tmp_path / "data") as workspace:
        rows = []
        with FetchClient(workspace.provenance, contact=contact) as fetch:
            for filing in sample["filings"]:
                response = fetch.fetch(filing["url"])
                receipt = response.provenance
                parsed = parse_filing(
                    response.body,
                    form_type=filing["form_type"],
                    filed_on=filing["filed_on"],
                    cik=filing["cik"],
                    employer_name=filing["employer_name"],
                )
                for row in parsed:
                    row["person_key"] = filing["resolved_keys"].get(
                        row["person_key"], row["person_key"]
                    )
                    row.update(
                        source_provenance_id=receipt.id,
                        source_url=receipt.url,
                        source_sha256=receipt.sha256,
                        source_collected_at=receipt.collected_at.isoformat(),
                        source_collection_method=receipt.collection_method,
                    )
                    rows.append(row)
        imported = import_records(
            workspace,
            rows,
            source="SEC EDGAR host sample",
            person_key_namespace="host-confirmed-sec-ties",
            provenance_class=ProvenanceClass.GOVERNMENT_ADMINISTRATIVE,
        )
        assert imported.accepted_count == len(rows)
        employers = dict(
            workspace.connection.execute("SELECT name, id FROM employers").fetchall()
        )
        result = score_pair(
            workspace,
            employers[sample["employer_a_name"]],
            employers[sample["employer_b_name"]],
            start_date=date.fromisoformat(sample["start_date"]),
            end_date=date.fromisoformat(sample["end_date"]),
        )
        actual = [
            {
                "tie_type": tie.tie_type,
                "person_names": sorted({p.person_name for p in tie.positions}),
                "start_date": tie.start_date.isoformat() if tie.start_date else None,
                "end_date": tie.end_date.isoformat() if tie.end_date else None,
            }
            for tie in result.ties
        ]
        assert sorted(actual, key=json.dumps) == sorted(
            sample["expected_ties"], key=json.dumps
        )
        assert all(
            p.records[0].raw["source_provenance_id"]
            for t in result.ties
            for p in t.positions
        )
