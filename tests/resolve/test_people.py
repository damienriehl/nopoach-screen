import pytest

from nopoach.imports.loader import import_records
from nopoach.imports.schema import ProvenanceClass
from nopoach.resolve.people import PersonResolver
from nopoach.store.workspace import Workspace


def row(key, **changes):
    return {
        "person_key": key,
        "person_name": "Synthetic Person A",
        "employer_name": "Synthetic Works",
        "title": "Accountant",
        "location": "Synthetic City",
        "start_date": "2010",
        "start_precision": "year",
        "end_date": "",
        "end_precision": "unknown",
        **changes,
    }


def test_cross_source_merge_keeps_match_evidence_and_both_records(tmp_path):
    folder = tmp_path / "data"
    with Workspace.open(folder) as workspace:
        for source, key in [("synthetic-A", "key-1"), ("synthetic-B", "key-1")]:
            result = import_records(
                workspace,
                [row(key)],
                source=source,
                provenance_class=ProvenanceClass.CONSENT_BASED,
            )
            assert result.accepted_count == 1
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (1,)
        person_id = workspace.connection.execute("SELECT id FROM people").fetchone()[0]
        evidence = PersonResolver(workspace).evidence(person_id)
        assert len(evidence) == 2
        assert evidence[1]["source"] == "synthetic-B"
        assert evidence[1]["evidence"]["matched_on"] == ["name_and_employment"]
        assert evidence[1]["evidence"]["engine"] == "splink-5-duckdb"
        assert workspace.connection.execute(
            "SELECT count(*) FROM positions"
        ).fetchone() == (2,)
    with Workspace.open(folder) as reopened:
        assert len(PersonResolver(reopened).evidence(person_id)) == 2


@pytest.mark.parametrize("second_source", ["synthetic-A", "synthetic-B"])
def test_namesakes_with_different_provider_keys_stay_separate(tmp_path, second_source):
    with Workspace.open(tmp_path / "data") as workspace:
        for source, key in [("synthetic-A", "key-1"), (second_source, "key-2")]:
            result = import_records(
                workspace,
                [row(key)],
                source=source,
                provenance_class=ProvenanceClass.SYNTHETIC,
            )
            assert result.accepted_count == 1
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (2,)
        assert workspace.connection.execute(
            "SELECT count(DISTINCT person_id) FROM person_keys"
        ).fetchone() == (2,)
        assert workspace.connection.execute(
            "SELECT count(*) FROM positions GROUP BY person_id"
        ).fetchall() == [(1,), (1,)]


def test_name_and_employment_match_kept_when_stored_key_is_absent(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        result = import_records(
            workspace,
            [row("key-1")],
            source="synthetic-A",
            provenance_class=ProvenanceClass.SYNTHETIC,
        )
        assert result.accepted_count == 1
        person_id = workspace.connection.execute("SELECT id FROM people").fetchone()[0]
        # Characterize an existing keyless observation; imports still require keys.
        workspace.connection.execute("UPDATE person_keys SET person_key = ''")
        result = import_records(
            workspace,
            [row("key-2")],
            source="synthetic-B",
            provenance_class=ProvenanceClass.SYNTHETIC,
        )
        assert result.accepted_count == 1
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (1,)
        evidence = PersonResolver(workspace).evidence(person_id)
        assert evidence[1]["evidence"]["matched_on"] == ["name_and_employment"]

        # The keyless alias must not bridge two conflicting non-empty keys.
        result = import_records(
            workspace,
            [row("key-3")],
            source="synthetic-C",
            provenance_class=ProvenanceClass.SYNTHETIC,
        )
        assert result.accepted_count == 1
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (2,)
        assert len(PersonResolver(workspace).evidence(person_id)) == 2


def test_source_scoped_keys_shared_namespace_and_within_source(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        for source in ("synthetic-A", "synthetic-B"):
            import_records(
                workspace,
                [
                    row("same-key", person_name="", title="Engineer"),
                    row("same-key", person_name="", title="Manager"),
                ],
                source=source,
                provenance_class=ProvenanceClass.LICENSED_ORIGINAL,
            )
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (2,)
        for source in ("synthetic-C", "synthetic-D"):
            import_records(
                workspace,
                [row("global-key", person_name="")],
                source=source,
                person_key_namespace="synthetic-common-provider",
                provenance_class=ProvenanceClass.LICENSED_ORIGINAL,
            )
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (3,)
