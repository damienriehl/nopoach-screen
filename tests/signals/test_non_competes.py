from datetime import date

from nopoach.market.define import Employer
from nopoach.signals.clauses import (
    ClauseDocument,
    extract_non_competes,
    non_competes,
    score_pair,
    store_document,
)
from nopoach.store.workspace import Workspace

EMPLOYERS = (Employer("alpha", "Synthetic Alpha"), Employer("beta", "Synthetic Beta"))


def document(workspace: Workspace, text: str) -> ClauseDocument:
    receipt = workspace.provenance.append(
        source="Synthetic contract",
        url="https://example.invalid/synthetic-contract",
        method="IMPORT",
        body=text.encode(),
    )
    return ClauseDocument(
        text,
        receipt,
        date(2022, 1, 3),
        "synthetic",
        EMPLOYERS[0],
        "invented-contract.txt",
    )


def test_extracts_exact_attributed_worker_restriction(tmp_path):
    text = (
        "Registered nurses shall not accept employment with a competitor of "
        "Synthetic Alpha for two years after termination."
    )
    with Workspace.open(tmp_path / "data") as workspace:
        (finding,) = extract_non_competes(document(workspace, text), EMPLOYERS)
        assert finding.clause_type == "non_compete"
        assert finding.employer_ids == ("alpha",)
        assert finding.scope == "registered nurses"
        assert finding.duration == "two years after termination"
        assert finding.text == text[finding.start : finding.end]
        assert finding.pairs == ()


def test_non_compete_persists_but_never_scores(tmp_path):
    text = (
        "Employee shall not provide services to a competitive business of "
        "Synthetic Alpha for 12 months after termination."
    )
    with Workspace.open(tmp_path / "data") as workspace:
        employer_receipt = workspace.provenance.append(
            source="Synthetic employers", url="", method="IMPORT", body=b"synthetic"
        )
        for employer in EMPLOYERS:
            workspace.connection.execute(
                "INSERT INTO employers VALUES (?, ?, NULL, ?)",
                [employer.id, employer.name, employer_receipt.id],
            )
        assert store_document(workspace, document(workspace, text), EMPLOYERS) == ()
        (finding,) = non_competes(workspace)
        assert finding.text == text
        assert score_pair(workspace, "alpha", "beta").clauses == ()


def test_near_misses_and_ambiguous_employer_do_not_extract(tmp_path):
    examples = (
        "Employee may work for a competitor of Synthetic Alpha.",
        "Employee shall not solicit customers of Synthetic Alpha.",
        "Franchisee shall not compete with Synthetic Alpha.",
        (
            "Employee shall not work for a competitor of Synthetic Alpha or "
            "Synthetic Beta."
        ),
    )
    with Workspace.open(tmp_path / "data") as workspace:
        for text in examples:
            assert extract_non_competes(document(workspace, text), EMPLOYERS) == ()
