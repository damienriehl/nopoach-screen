import json
import os
from datetime import date
from pathlib import Path

import pytest

from nopoach.market.define import Employer
from nopoach.signals.clauses import (
    ClauseDocument,
    evaluate_corpus,
    extract_clauses,
    score_pair,
    store_document,
)
from nopoach.store.workspace import Workspace

FIXTURES = Path(__file__).parents[1] / "fixtures" / "clauses"
EMPLOYERS = (Employer("alpha", "Synthetic Alpha"), Employer("beta", "Synthetic Beta"))


def document(workspace, text, **kwargs):
    receipt = workspace.provenance.append(
        source="Synthetic filing",
        url="https://www.sec.gov/Archives/synthetic-ex10.htm",
        method="GET",
        body=text.encode(),
    )
    return ClauseDocument(
        text=text,
        provenance=receipt,
        filed_on=date(2020, 2, 3),
        form_type="EX-10.1",
        issuer=EMPLOYERS[0],
        document_name="synthetic-ex10.htm",
        **kwargs,
    )


def test_ae4_mutual_clause_keeps_parties_term_scope_and_carveout(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        doc = document(workspace, (FIXTURES / "outsourcing.txt").read_text())
        (clause,) = extract_clauses(doc, EMPLOYERS)
        assert clause.parties == ("Synthetic Alpha", "Synthetic Beta")
        assert clause.employer_ids == ("alpha", "beta")
        assert clause.binding == "mutual"
        assert clause.duration == "two years after termination"
        assert clause.scope == "all employees"
        assert clause.filed_on == date(2020, 2, 3)
        assert clause.carve_outs == (
            "This restriction does not apply to general solicitations not directed "
            "at the other party's employees.",
        )
        assert doc.text[clause.start : clause.end] == clause.text
        assert clause.provenance == doc.provenance
        assert clause.pairs == (("alpha", "beta"),)


@pytest.mark.parametrize(
    "text",
    [
        "Neither party shall solicit customers of the other party.",
        "Employees serve customers. Neither party shall solicit customers.",
        "Neither party shall solicit customers through its employees.",
        "Neither party shall solicit employees as customers.",
        "The parties may employ or solicit employees without restriction.",
        "Neither party is subject to a no-hire restriction on employees.",
    ],
)
def test_customer_and_permitted_hiring_are_not_employee_restrictions(tmp_path, text):
    with Workspace.open(tmp_path / "data") as workspace:
        assert extract_clauses(document(workspace, text), EMPLOYERS) == ()


def test_one_sided_named_group_and_defined_party_alias(tmp_path):
    text = (
        'Agreement between Synthetic Alpha ("Customer") and Synthetic Beta '
        '("Supplier").\n\nSupplier shall not hire Customer\'s senior managers '
        "for 12 months after expiration."
    )
    with Workspace.open(tmp_path / "data") as workspace:
        (clause,) = extract_clauses(document(workspace, text), EMPLOYERS)
        assert clause.binding == "one_sided"
        assert clause.parties == ("Synthetic Alpha", "Synthetic Beta")
        assert clause.bound_employer_ids == ("beta",)
        assert clause.scope == "senior managers"
        assert clause.duration == "12 months after expiration"


def test_two_explicit_obligations_are_mutual_without_neither_phrase(tmp_path):
    text = (
        "Synthetic Alpha shall not hire employees of Synthetic Beta. "
        "Synthetic Beta shall not solicit employees of Synthetic Alpha."
    )
    with Workspace.open(tmp_path / "data") as workspace:
        (clause,) = extract_clauses(document(workspace, text), EMPLOYERS)
        assert clause.binding == "mutual"
        assert clause.bound_employer_ids == ("alpha", "beta")


def test_unresolved_parties_remain_visible_without_inventing_links(tmp_path):
    with Workspace.open(tmp_path / "data") as workspace:
        text = "Neither party shall hire employees of the other party."
        (clause,) = extract_clauses(document(workspace, text), EMPLOYERS)
        assert clause.employer_ids == ()
        assert clause.pairs == ()


def test_stored_clause_and_pair_signal_survive_reopen_without_flow_input(tmp_path):
    folder = tmp_path / "data"
    with Workspace.open(folder) as workspace:
        receipt = workspace.provenance.append(
            source="synthetic-employers", url="", method="IMPORT", body=b"synthetic"
        )
        for employer in EMPLOYERS:
            workspace.connection.execute(
                "INSERT INTO employers VALUES (?, ?, NULL, ?)",
                [employer.id, employer.name, receipt.id],
            )
        doc = document(workspace, (FIXTURES / "outsourcing.txt").read_text())
        stored = store_document(workspace, doc, EMPLOYERS)
        assert len(stored) == 1
        assert store_document(workspace, doc, EMPLOYERS) == stored
    with Workspace.open(folder) as workspace:
        signal = score_pair(workspace, "alpha", "beta")
        assert signal.has_mutual_clause
        assert len(signal.clauses) == 1
        assert signal.clauses[0] == stored[0]
        assert score_pair(workspace, "beta", "alpha") == signal
        assert score_pair(workspace, "alpha", "unrelated").clauses == ()
        assert workspace.connection.execute(
            "SELECT count(*) FROM filings"
        ).fetchone() == (1,)
        assert workspace.connection.execute(
            "SELECT count(*) FROM clauses"
        ).fetchone() == (1,)


def test_local_real_clause_evaluation():
    corpus = os.environ.get("NOPOACH_CLAUSE_CORPUS")
    if not corpus:
        pytest.skip(
            "NOPOACH_CLAUSE_CORPUS unset; host must supply >=60 real public clauses"
        )
    result = evaluate_corpus(Path(corpus))
    assert result.count >= 60
    assert result.positives > 0 and result.negatives > 0
    assert result.precision >= 0.9
    assert result.recall >= 0.8


def test_evaluation_harness_scores_independently_labeled_synthetic_cases(tmp_path):
    records = [
        {
            "text": "Neither party shall solicit employees of the other party.",
            "positive": True,
        },
        {
            "text": "Neither party shall solicit customers of the other party.",
            "positive": False,
        },
    ]
    (tmp_path / "labels.json").write_text(json.dumps(records))
    result = evaluate_corpus(tmp_path)
    assert (result.count, result.positives, result.negatives) == (2, 1, 1)
    assert result.precision == result.recall == 1.0


def test_one_sided_binding_is_not_changed_by_carveout_for_either_party(tmp_path):
    text = (
        "Synthetic Alpha shall not hire employees of Synthetic Beta. "
        "General solicitations made by either party are excluded."
    )
    with Workspace.open(tmp_path / "data") as workspace:
        (clause,) = extract_clauses(document(workspace, text), EMPLOYERS)
        assert clause.binding == "one_sided"
        assert clause.bound_employer_ids == ("alpha",)


def test_named_employee_scope_does_not_expand_to_all_employees(tmp_path):
    text = (
        "Neither party shall solicit employees assigned to the Transition Team "
        "during the two-year period following termination."
    )
    with Workspace.open(tmp_path / "data") as workspace:
        (clause,) = extract_clauses(document(workspace, text), EMPLOYERS)
        assert clause.scope == "employees assigned to the Transition Team"
        assert clause.duration == "two-year period following termination"


def test_general_solicitation_carveout_in_following_paragraph(tmp_path):
    text = (
        "Neither party shall solicit employees of the other party.\n\n"
        "This restriction does not apply to general solicitations."
    )
    with Workspace.open(tmp_path / "data") as workspace:
        doc = document(workspace, text)
        (clause,) = extract_clauses(doc, EMPLOYERS)
        assert clause.carve_outs == (
            "This restriction does not apply to general solicitations.",
        )
        assert doc.text[clause.start : clause.end] == clause.text
