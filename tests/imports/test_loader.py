import csv
from datetime import date

import duckdb
import pytest

from nopoach.imports.loader import import_file, import_records
from nopoach.imports.schema import ProvenanceClass, template_csv
from nopoach.store.workspace import Workspace

CLASS = ProvenanceClass.GOVERNMENT_ADMINISTRATIVE


def row(**changes):
    return {
        "person_key": "synthetic-1",
        "employer_name": "Synthetic Works",
        "title": "Software Developer",
        "location": "Synthetic City",
        "start_date": "2010",
        "start_precision": "year",
        "end_date": "2012-06",
        "end_precision": "month",
        **changes,
    }


def test_vendor_csv_provenance_and_coverage(tmp_path):
    path = tmp_path / "vendor.csv"
    path.write_text(
        "worker_id,company,job_title,city,start,start_precision,end,end_precision\n"
        "synthetic-1,Synthetic Works,Software Developer,Synthetic City,"
        "2010,year,2012-06,month\n"
        "synthetic-2,Synthetic Works,Unmapped Synthetic Title,Synthetic City,"
        "2010,year,,unknown\n"
    )
    with Workspace.open(tmp_path / "data") as workspace:
        result = import_file(
            workspace, path, source="Synthetic vendor", provenance_class=CLASS
        )
        assert result.accepted_count == 2
        assert result.rejects == ()
        assert result.unmapped_titles == {"Unmapped Synthetic Title": 1}
        stored = workspace.connection.execute(
            "SELECT r.source, r.provenance_class, p.collection_method, "
            "p.collected_at, x.occupation_code, x.start_date, x.end_date "
            "FROM imported_rows r JOIN provenance p ON p.id = r.provenance_id "
            "JOIN positions x ON x.id = r.position_id ORDER BY r.row_number"
        ).fetchall()
        assert stored[0][:3] == ("Synthetic vendor", CLASS.value, "agency_import")
        assert stored[1][:3] == stored[0][:3]
        assert stored[0][3] is not None
        assert stored[0][4:] == ("15-1252", date(2010, 1, 1), date(2012, 6, 30))
        assert len(workspace.provenance.records()) == 1


def test_missing_column_and_provenance_refused(tmp_path):
    path = tmp_path / "missing.csv"
    path.write_text("person_key,title\nsynthetic-1,Engineer\n")
    with Workspace.open(tmp_path / "data") as workspace:
        with pytest.raises(ValueError, match="employer_name"):
            import_file(workspace, path, source="synthetic", provenance_class=CLASS)
        with pytest.raises(ValueError, match="provenance class"):
            import_records(
                workspace, [row()], source="synthetic", provenance_class=None
            )
        assert workspace.provenance.records() == ()


def test_rejects_reported_and_retained_while_valid_rows_import(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        result = import_records(
            workspace,
            [row(end_date="2009", end_precision="year"), row(person_key="synthetic-2")],
            source="synthetic",
            provenance_class=CLASS,
        )
        assert result.accepted_count == 1
        assert result.rejects[0].row_number == 1
        assert "end_date before start_date" in result.rejects[0].reason
        assert workspace.connection.execute(
            "SELECT row_number, rejection_reason FROM imported_rows "
            "WHERE position_id IS NULL"
        ).fetchone() == (1, result.rejects[0].reason)
        assert workspace.connection.execute(
            "SELECT count(*) FROM positions"
        ).fetchone() == (1,)


def test_template_mapper_parquet_and_programmatic_entry(tmp_path):
    header = next(csv.reader(template_csv().splitlines()))
    assert "employer_id_type" in header
    path = tmp_path / "fixture.parquet"
    connection = duckdb.connect()
    connection.execute(
        "COPY (SELECT 'synthetic-1' AS key, 'Synthetic Works' AS employer_name, "
        "'Accountant' AS title, 'Synthetic City' AS location, '2010-01-02' AS "
        "start_date, 'day' AS start_precision, NULL::VARCHAR AS end_date, "
        "'unknown' AS end_precision) TO ? (FORMAT PARQUET)",
        [str(path)],
    )
    connection.close()
    with Workspace.open(tmp_path / "data") as workspace:
        result = import_file(
            workspace,
            path,
            source="synthetic",
            provenance_class=CLASS,
            column_map={"person_key": "key"},
        )
        assert result.accepted_count == 1
        assert workspace.connection.execute(
            "SELECT occupation_code FROM positions"
        ).fetchone() == ("13-2011",)
        result = import_records(
            workspace,
            [row(person_key="synthetic-2", occupation_code="15-1211")],
            source="synthetic",
            provenance_class=CLASS,
        )
        assert result.unmapped_titles == {}
        assert workspace.connection.execute(
            "SELECT count(*) FROM people"
        ).fetchone() == (2,)


def test_parquet_template_uses_the_documented_layout():
    import io

    import pyarrow.parquet as parquet

    from nopoach.imports.schema import COLUMNS, template_parquet

    table = parquet.read_table(io.BytesIO(template_parquet()))
    assert table.column_names == list(COLUMNS)
    assert table.num_rows == 0


def test_synthetic_records_retain_synthetic_class_and_collection_method(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        result = import_records(
            workspace, [row()], source="Synthetic demo", provenance_class="synthetic"
        )
        assert result.accepted_count == 1
        assert result.rejects == ()
        assert workspace.connection.execute(
            "SELECT i.provenance_class, r.provenance_class, p.collection_method "
            "FROM imports i JOIN imported_rows r ON r.import_id = i.id "
            "JOIN provenance p ON p.id = r.provenance_id"
        ).fetchone() == ("synthetic", "synthetic", "synthetic")
        assert workspace.provenance.records()[0].collection_method == "synthetic"


def test_optional_cbsa_centroid_persisted_from_csv_and_programmatic_rows(tmp_path):
    path = tmp_path / "geo.csv"
    path.write_text(
        "person_key,employer_name,title,location,start_date,start_precision,"
        "end_date,end_precision,cbsa,latitude,longitude\n"
        "geo-1,Synthetic Works,Software Developer,Fixture,2010,year,,unknown,"
        "99999,44.9,-93.2\n"
    )
    with Workspace.open(tmp_path / "data") as workspace:
        first = import_file(workspace, path, source="fixture", provenance_class=CLASS)
        second = import_records(
            workspace,
            [
                row(
                    person_key="geo-2", cbsa="99999", latitude="44.9", longitude="-93.2"
                ),
                row(person_key="unknown"),
            ],
            source="fixture",
            provenance_class=CLASS,
        )
        assert first.accepted_count == 1 and second.accepted_count == 2
        sites = workspace.connection.execute(
            "SELECT cbsa, latitude, longitude, source, method, provenance_id "
            "FROM position_worksites ORDER BY position_id"
        ).fetchall()
        assert len(sites) == 2
        assert all(
            r[:5] == ("99999", 44.9, -93.2, "fixture", "supplied_cbsa_centroid")
            for r in sites
        )
        assert {r[5] for r in sites} == {first.provenance_id, second.provenance_id}
