import csv
import io
from datetime import date

import pytest

from nopoach.imports.noncompetes import (
    import_contracts,
    load_contracts,
    parse_contracts,
)
from nopoach.signals.noncompetes import assess_contracts
from nopoach.store.workspace import Workspace

FIELDS = (
    "document_id",
    "employer_id",
    "text",
    "document_date",
    "scope",
    "worker_key",
    "effective_from",
    "effective_to",
    "documents_complete",
    "review",
)
TEXT = (
    "Employee shall not work for a competitor for 12 months "
    "following termination of employment."
)


def metadata(counts=None):
    return dict(
        source="Synthetic contracts",
        population_description="Reviewed synthetic workers",
        sampling_basis=(
            "All workers in a declared synthetic review roster; "
            "not workforce representative"
        ),
        scope="ordinary:synthetic",
        declared_workers=counts if counts is not None else {"a": 10},
    )


def body(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def row(worker="worker-0", review="positive", **changes):
    return dict(
        document_id=f"doc-{worker}",
        employer_id="a",
        text=TEXT
        if review == "positive"
        else "This employee contract contains no post-employment restriction.",
        document_date="2019-01-01",
        scope="ordinary:synthetic",
        worker_key=worker,
        effective_from="2020-01-01",
        effective_to="ongoing",
        documents_complete="true",
        review=review,
        **changes,
    )


def assess(rows, counts=None, **kwargs):
    return assess_contracts(
        parse_contracts(body(rows), metadata(counts)),
        scope="ordinary:synthetic",
        start=date(2020, 1, 1),
        end=date(2021, 12, 31),
        **kwargs,
    )


def test_complete_six_of_ten_and_duplicate_documents_count_workers_once():
    rows = [row(f"w-{i}", "positive" if i < 6 else "negative") for i in range(10)]
    result = assess(rows + [rows[0]])
    (population,) = result.employers
    assert population.complete
    assert population.numerator == 6 and population.denominator == 10
    assert population.reviewed_workers == 10
    assert population.supported is True
    assert len(result.candidates) == 6
    assert "not workforce representative" in population.reason
    assert "worker_key" not in repr(result)


def test_complete_positive_review_with_permissive_exception_remains_testable():
    document = row()
    document["text"] = (
        "Employee shall not work for a competitor for 12 months following "
        "termination of employment, except that Employee may work for a "
        "competitor with written consent."
    )
    result = assess([document], {"a": 1})
    (population,) = result.employers
    assert population.complete and population.supported is True
    assert population.numerator == population.denominator == 1
    assert population.unresolved_workers == 0
    assert result.candidates[0].exceptions == (
        "except that Employee may work for a competitor with written consent",
    )


def test_conflicting_amendments_and_missing_applicability_are_unresolved():
    positive = row("same")
    negative = {**row("same", "negative"), "document_id": "amendment"}
    result = assess([positive, negative], {"a": 1})
    assert not result.employers[0].complete
    assert "conflict" in result.employers[0].reason
    no_effective_date = {**positive, "effective_from": ""}
    assert not assess([no_effective_date], {"a": 1}).employers[0].complete
    assert (
        not assess([{**positive, "effective_to": ""}], {"a": 1}).employers[0].complete
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "Ordinary employment agreement."},
        {"worker_key": ""},
        {"scope": "different scope"},
        {"documents_complete": "false"},
        {"review": "unreviewed"},
        {"document_date": "2024-01-01"},
    ],
)
def test_incomplete_unknown_scope_or_future_input_cannot_test(changes):
    result = assess([{**row(), **changes}], {"a": 1}, as_of=date(2021, 12, 31))
    assert not result.employers[0].complete
    assert result.employers[0].supported is None


def test_explicit_negative_needs_complete_applicable_document_set_and_denominator():
    assert assess([row(review="negative")], {"a": 1}).employers[0].supported is False
    assert (
        not assess(
            [{**row(review="negative"), "documents_complete": "false"}], {"a": 1}
        )
        .employers[0]
        .complete
    )
    assert not assess([row(review="negative")], {"a": 2}).employers[0].complete
    result = assess_contracts(
        parse_contracts(body([row()]), metadata({})),
        scope="ordinary:synthetic",
        start=date(2020, 1, 1),
        end=date(2021, 12, 31),
    )
    assert result.employers[0].denominator is None
    assert not result.employers[0].complete


def test_bytes_and_normalized_documents_survive_immutable_import(tmp_path):
    raw = body([{**row(), "text": TEXT + "\r\n<script>synthetic</script>"}])
    with Workspace.open(tmp_path / "data") as workspace:
        reference = import_contracts(workspace, raw, metadata({"a": 1}))
        corpus = load_contracts(workspace, reference)
        assert corpus.records[0].text == TEXT + "\n<script>synthetic</script>"
        assert import_contracts(workspace, raw, metadata({"a": 1})) == reference
        assert corpus.provenance_id
        assert corpus.bundle_id == reference.bundle_id


def test_invalid_metadata_and_bounded_csv_are_rejected_without_publication(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        before = workspace.provenance.records()
        with pytest.raises(ValueError):
            import_contracts(workspace, body([row()]), metadata({"a": -1}))
        assert workspace.provenance.records() == before
    with pytest.raises(ValueError, match="column"):
        parse_contracts(b"text,text\na,b\n", metadata())


def test_different_text_versions_under_one_document_id_need_resolution():
    original = row()
    revised = {**original, "text": TEXT.replace("12 months", "24 months")}
    result = assess([original, revised], {"a": 1})
    assert not result.employers[0].complete
    assert "conflict" in result.employers[0].reason


def test_conflicting_and_negative_candidates_cannot_support_positive_review():
    result = assess(
        [
            {
                **row(),
                "text": (
                    "Employee is not prohibited from working for a competitor "
                    "after termination."
                ),
            }
        ],
        {"a": 1},
    )
    assert not result.employers[0].complete
    assert result.candidates[0].classification == "negated"


def test_unknown_document_date_and_future_review_stay_unavailable():
    assert not assess([{**row(), "document_date": ""}], {"a": 1}).employers[0].complete
    corpus = parse_contracts(
        body([row()]), {**metadata({"a": 1}), "reviewed_on": "2024-01-01"}
    )
    result = assess_contracts(
        corpus,
        scope="ordinary:synthetic",
        start=date(2020, 1, 1),
        end=date(2021, 12, 31),
        as_of=date(2021, 12, 31),
    )
    assert not result.employers[0].complete


@pytest.mark.parametrize("positive, supported", [(0, False), (4, False), (5, True)])
def test_complete_population_threshold_uses_unique_reviewed_workers(
    positive, supported
):
    rows = [
        row(f"w-{i}", "positive" if i < positive else "negative") for i in range(10)
    ]
    population = assess(rows).employers[0]
    assert population.complete
    assert population.numerator == positive
    assert population.supported is supported
