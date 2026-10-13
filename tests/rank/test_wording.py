import ast
import re
from pathlib import Path

import pytest

from nopoach.rank import wording

# Scan source strings as well as all future templates; keep the policy in tests,
# outside the user-facing wording module that it checks.
BANNED = re.compile(
    r"\b(?:collud(?:e|ed|es|ing)|collusion|conspir(?:ed|acy)|"
    r"agreement\s+(?:exists|existed|is\s+proven|was\s+proven)|"
    r"(?:firms|employers|companies|they)\s+(?:have\s+)?agreed|"
    r"(?:proven|illegal)\s+agreement)\b",
    re.I,
)


def user_facing_strings(root):
    for path in sorted(root.rglob("*")):
        if path.suffix == ".py":
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    yield path, node.value
                elif isinstance(node, ast.JoinedStr):
                    yield (
                        path,
                        "".join(
                            value.value
                            if isinstance(value, ast.Constant)
                            else "{value}"
                            for value in node.values
                        ),
                    )
        elif path.is_file() and "templates" in path.relative_to(root).parts:
            yield path, path.read_text(encoding="utf-8")


def test_every_source_string_and_template_passes_the_lead_wording_scan():
    root = Path(__file__).parents[2] / "src" / "nopoach"
    violations = [
        (str(path), text)
        for path, text in user_facing_strings(root)
        if BANNED.search(text)
    ]
    assert violations == []


def test_scan_automatically_includes_future_nested_templates(tmp_path):
    directory = tmp_path / "templates" / "dossiers"
    directory.mkdir(parents=True)
    template = directory / "pair.html"
    template.write_text("An agreement exists.")
    assert [
        (path, bool(BANNED.search(text)))
        for path, text in user_facing_strings(tmp_path)
    ] == [(template, True)]


@pytest.mark.parametrize("one_way", [False, True])
def test_fired_pair_summary_warrants_further_inquiry(one_way):
    summary = wording.pair_summary(
        "Synthetic A", "Synthetic B", ("flow", "clauses"), one_way=one_way
    )
    assert "warrants further inquiry" in summary
    assert not BANNED.search(summary)


def test_no_signal_summary_does_not_claim_an_inquiry_pattern():
    summary = wording.pair_summary("Synthetic A", "Synthetic B", (), one_way=False)
    assert "No screening signals fired" in summary
