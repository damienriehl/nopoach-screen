from nopoach.signals.noncompetes import extract_noncompetes


def test_exact_employee_restriction_span_terms_and_exception():
    text = (
        "Background. Employee shall not work for a competing business for 12 months "
        "following termination of employment, except with written consent. End."
    )
    (candidate,) = extract_noncompetes(text, employer_id="a", document_id="contract")
    assert candidate.text == text[candidate.start : candidate.end]
    assert candidate.employer_id == "a"
    assert candidate.document_id == "contract"
    assert candidate.classification == "candidate"
    assert candidate.duration == ("12 months",)
    assert candidate.exceptions == ("except with written consent",)
    assert candidate.full_text == text
    assert "applicability" in " ".join(candidate.uncertainty)


def test_negated_and_ambiguous_language_cannot_supply_positive_review():
    negated = (
        "Employee is not prohibited from working for a competitor after termination."
    )
    (candidate,) = extract_noncompetes(negated, employer_id="a", document_id="n")
    assert candidate.classification == "negated"
    ambiguous = "The party shall not work for a competitor after termination."
    (candidate,) = extract_noncompetes(ambiguous, employer_id="a", document_id="u")
    assert candidate.classification == "uncertain"


def test_solicitation_customer_and_commercial_covenants_are_not_worker_restrictions():
    for text in (
        "Employee shall not solicit customers after termination.",
        "Employee shall not solicit employees of a competitor after termination.",
        "Seller shall not compete with Buyer for five years "
        "after closing the sale of the business.",
        "Employee shall work for the company during employment.",
    ):
        assert extract_noncompetes(text, employer_id="a", document_id="n") == ()


def test_script_like_source_is_retained_as_text_and_offsets_are_exact():
    text = (
        '<script>alert("synthetic")</script>\n'
        "Employee shall not join a competitor after termination."
    )
    (candidate,) = extract_noncompetes(text, employer_id="a", document_id="n")
    assert candidate.full_text == text
    assert candidate.text == text[candidate.start : candidate.end]


def test_employment_start_is_not_a_post_employment_restriction():
    text = "Employee shall not work for a competitor after employment begins."
    assert extract_noncompetes(text, employer_id="a", document_id="n") == ()


def test_permission_inside_an_exception_preserves_the_operative_restriction():
    for introduction in ("except that", "unless", "provided that"):
        text = (
            "Employee shall not work for a competitor for 12 months following "
            f"termination of employment, {introduction} Employee may work for a "
            "competitor with written consent."
        )
        (candidate,) = extract_noncompetes(text, employer_id="a", document_id="n")
        assert candidate.classification == "candidate"
        assert candidate.text == text[candidate.start : candidate.end] == text
        assert candidate.exceptions == (
            f"{introduction} Employee may work for a competitor with written consent",
        )
        assert candidate.duration == ("12 months",)


def test_exception_does_not_invent_a_post_employment_prohibition():
    for text in (
        "Employee shall not work for a competitor during employment, except that "
        "following termination of employment Employee may work for a competitor.",
        "Employee is not prohibited from working for a competitor following "
        "termination of employment, except that Employee may work with consent.",
    ):
        (candidate,) = extract_noncompetes(text, employer_id="a", document_id="n")
        assert candidate.classification == "negated"
