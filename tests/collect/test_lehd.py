import csv
import gzip
import io
from dataclasses import replace

import pytest

from nopoach.analytical_inputs import list_bundles, read_bundle
from nopoach.collect.lehd import ParserLimits, import_j2j, parse_j2j
from nopoach.store.workspace import Workspace


def metadata():
    return {
        "source": "Synthetic public J2JOD table",
        "schema_version": "V4.14.0",
        "release": "R2023Q4",
        "release_date": "2023-12-31",
        "geography": "00123",
        "ownercode": "A00",
        "ownercode_orig": "A00",
        "availability": {
            "geo_level": "B",
            "geography": "00123",
            "geo_level_orig": "B",
            "geography_orig": "00123",
            "start_year": 2020,
            "start_quarter": 1,
            "end_year": 2022,
            "end_quarter": 4,
        },
    }


def rows():
    return [
        dict(
            periodicity="Q",
            seasonadj="U",
            geo_level="B",
            geography="00123",
            ind_level="A",
            industry="00",
            ownercode="A00",
            sex="0",
            agegrp="A00",
            race="A0",
            ethnicity="A0",
            education="E0",
            firmage="0",
            firmsize="0",
            year=str(year),
            quarter=str(quarter),
            agg_level="592897",
            geo_level_orig="B",
            geography_orig="00123",
            ind_level_orig="A",
            industry_orig="00",
            ownercode_orig="A00",
            firmage_orig="0",
            firmsize_orig="0",
            EE=str(10 * (year - 2019)),
            AQHire="5",
            sEE="1",
            sAQHire="1",
        )
        for year in (2020, 2021, 2022)
        for quarter in (1, 2, 3, 4)
    ]


def csv_body(records):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=records[0].keys())
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode()


def test_csv_gzip_retains_codes_dimensions_flags_and_exact_bytes(tmp_path):
    body = csv_body(rows())
    parsed = parse_j2j(body, metadata())
    assert len(parsed.records) == 12
    assert parsed.records[0].dimensions["geography"] == "00123"
    assert parsed.records[0].ee.value == 10
    assert parsed.records[0].ee.status == "1"
    assert parse_j2j(gzip.compress(body), metadata()).records == parsed.records
    with Workspace.open(tmp_path / "data") as workspace:
        ref = import_j2j(workspace, gzip.compress(body), metadata())
        bundle = read_bundle(workspace, ref)
        assert gzip.decompress(bundle.body) == body
        assert bundle.metadata["release"] == "R2023Q4"
        assert bundle.metadata["parser_settings"]["max_rows"] == 1000000
        assert import_j2j(workspace, bundle.body, metadata()) == ref


@pytest.mark.parametrize(
    "value,flag,reason",
    [
        ("0", "1", None),
        ("", "1", "absent"),
        ("", "5", "suppressed"),
        ("", "-2", "unavailable"),
        ("7", "9", "distorted"),
        ("-1", "1", "invalid"),
        ("nan", "1", "invalid"),
        ("9", "unknown", "unknown status"),
    ],
)
def test_indicator_absence_suppression_and_unknown_flags_are_not_zero(
    value, flag, reason
):
    records = rows()
    records[0].update(EE=value, sEE=flag)
    indicator = parse_j2j(csv_body(records), metadata()).records[0].ee
    assert indicator.reason == reason
    assert indicator.value == (0 if reason is None else None)
    assert indicator.raw_value == value and indicator.status == flag


@pytest.mark.parametrize(
    "field,value",
    [
        ("seasonadj", "S"),
        ("periodicity", "A"),
        ("industry", "51"),
        ("sex", "1"),
        ("ownercode_orig", "A05"),
        ("agg_level", "1"),
    ],
)
def test_unsupported_target_tabulations_are_rejected(field, value):
    records = rows()
    records[0][field] = value
    with pytest.raises(ValueError, match="tabulation"):
        parse_j2j(csv_body(records), metadata())


def test_unrelated_directions_are_excluded_not_added_to_same_metro():
    records = rows()
    unrelated = {**records[0], "geography_orig": "00456", "EE": "999999"}
    parsed = parse_j2j(csv_body([*records, unrelated]), metadata())
    assert len(parsed.records) == 12
    assert parsed.excluded_rows == 1


def test_duplicate_cells_are_deduplicated_but_conflicts_are_rejected():
    records = rows()
    assert len(parse_j2j(csv_body([*records, records[0]]), metadata()).records) == 12
    with pytest.raises(ValueError, match="conflicting duplicate"):
        parse_j2j(csv_body([*records, {**records[0], "EE": "999"}]), metadata())


def test_mixed_release_and_unknown_replay_version_are_rejected():
    records = [{**row, "release": "R2023Q4"} for row in rows()]
    records[-1]["release"] = "R2024Q1"
    with pytest.raises(ValueError, match="Mixed"):
        parse_j2j(csv_body(records), metadata())
    with pytest.raises(ValueError, match="parser version"):
        parse_j2j(csv_body(rows()), {**metadata(), "parser_version": "unknown"})


def test_invalid_metadata_does_not_replace_a_valid_bundle(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        ref = import_j2j(workspace, csv_body(rows()), metadata())
        for key, value in (
            ("schema_version", "V99"),
            ("release", ["a", "b"]),
            ("release_date", "unknown"),
            ("availability", {}),
        ):
            with pytest.raises(ValueError):
                import_j2j(workspace, csv_body(rows()), {**metadata(), key: value})
        assert list_bundles(workspace) == (ref,)


@pytest.mark.parametrize(
    "limits",
    [
        replace(ParserLimits(), max_raw_bytes=10),
        replace(ParserLimits(), max_decompressed_bytes=40),
        replace(ParserLimits(), max_rows=2),
        replace(ParserLimits(), max_field_bytes=4),
        replace(ParserLimits(), max_retained_cells=2),
    ],
)
def test_parser_resource_limits_apply_to_gzip(limits):
    with pytest.raises(ValueError, match="limit"):
        parse_j2j(gzip.compress(csv_body(rows())), metadata(), limits=limits)


@pytest.mark.network_off
def test_parser_and_importer_make_no_network_requests(tmp_path, monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("Network access attempted")

    monkeypatch.setattr(socket, "create_connection", blocked)
    with Workspace.open(tmp_path / "data") as workspace:
        import_j2j(workspace, csv_body(rows()), metadata())
