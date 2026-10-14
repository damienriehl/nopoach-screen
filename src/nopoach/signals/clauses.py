"""Local employee-clause rules and durable, provenance-backed pair evidence.

No flow input is required: a filed restriction is a separate screening signal.
Binding labels describe the text, not a legal conclusion about enforceability.
Unknown parties/durations remain unknown; no employer match is guessed. Raw
public documents stay in the user's external workspace, never in the repo.
"""

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
from html.parser import HTMLParser
from itertools import combinations
from pathlib import Path
from typing import Literal

from nopoach.market.define import Employer
from nopoach.rank.pairs import pair_key
from nopoach.store.provenance import ProvenanceRecord
from nopoach.store.workspace import Workspace

Binding = Literal["mutual", "one_sided", "unknown", "brand_wide"]
_EMPLOYEE = re.compile(
    r"\b(?:employees?|workers?|personnel|staff|senior managers|managers|"
    r"executives|engineers|officers|person\b[^.]*?\bemployed)\b",
    re.I,
)
_RESTRICTION = re.compile(
    r"\b(?:"
    r"(?:shall|will|must|may|agree(?:s)?\s+to)\s+(?:not|never)\s+"
    r"(?:directly\s+(?:or\s+indirectly\s+)?|indirectly\s+)?"
    r"(?:solicit|hire|employ|recruit|induce|entice)\b[^.]*|"
    r"neither\s+party\s+(?:shall|will|may)\s+"
    r"(?:not\s+)?(?:solicit|hire|employ|recruit)\b[^.]*|"
    r"(?:prohibited|prohibits?|forbidden)\s+(?:from\s+)?"
    r"(?:soliciting|hiring|employing|recruiting)\b[^.]*|"
    r"(?:no[- ]hire|non[- ]solicitation\s+of\s+employees)\b[^.]*"
    r")",
    re.I,
)
_MUTUAL = re.compile(
    r"\b(?:neither party|each party|both parties|either party|the parties)"
    r"\s+(?:shall|will|may|must|agree(?:s)?)\b",
    re.I,
)
_DURATION = re.compile(
    r"\b(?:for\s+)?((?:\d+(?:\s*\(\d+\))?|one|two|three|four|five|six|"
    r"twelve|twenty[- ]four)(?:\s*\(\d+\))?[ -]+(?:years?|months?|days?)"
    r"(?:\s+period)?"
    r"(?:\s+(?:after|following|from)\s+(?:the\s+)?"
    r"(?:termination|expiration|expiry|effective date))?)\b|"
    r"\b(during\s+the\s+term(?:\s+of\s+(?:this|the)\s+agreement)?)\b",
    re.I,
)
_CARVEOUT = re.compile(
    r"[^.\n]*(?:general\s+(?:solicitations?|advertis(?:ing|ements?))|"
    r"unsolicited\s+(?:applications?|applicants?))[^.\n]*(?:\.|$)",
    re.I,
)


@dataclass(frozen=True)
class ClauseDocument:
    text: str
    provenance: ProvenanceRecord
    filed_on: date
    form_type: str
    issuer: Employer
    document_name: str = ""
    accession_number: str = ""
    brand: str | None = None


@dataclass(frozen=True)
class Clause:
    id: str
    text: str
    filed_on: date
    clause_type: str
    parties: tuple[str, ...]
    employer_ids: tuple[str, ...]
    bound_employer_ids: tuple[str, ...]
    binding: Binding
    duration: str | None
    scope: str | None
    carve_outs: tuple[str, ...]
    brand: str | None
    document_name: str
    start: int
    end: int
    provenance: ProvenanceRecord

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Form pairs locally, including visibly labeled one-sided evidence."""
        return tuple(combinations(sorted(set(self.employer_ids)), 2))


@dataclass(frozen=True)
class ClauseSignal:
    employer_a_id: str
    employer_b_id: str
    clauses: tuple[Clause, ...]

    @property
    def has_mutual_clause(self) -> bool:
        return any(c.binding in {"mutual", "brand_wide"} for c in self.clauses)


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n\n")
        if tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def full_text(body: bytes) -> str:
    """Read the complete HTML/plaintext body, preserving clause blocks."""
    text = body.decode("utf-8-sig", errors="replace")
    if re.search(r"<(?:html|body|p|div|table)\b", text, re.I):
        parser = _PlainText()
        parser.feed(text)
        text = "".join(parser.parts)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _aliases(text: str, employers: Sequence[Employer]) -> dict[str, Employer]:
    aliases = {}
    for employer in employers:
        aliases[employer.name] = employer
        match = re.search(
            re.escape(employer.name) + r'\s*\(["“](.+?)["”]\)',
            text,
            re.I,
        )
        if match:
            aliases[match[1]] = employer
    return aliases


def _mentions(text: str, aliases: dict[str, Employer]) -> tuple[Employer, ...]:
    found = {}
    for alias, employer in aliases.items():
        if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I):
            found[employer.id] = employer
    return tuple(sorted(found.values(), key=lambda employer: employer.id))


def _employee_target(restriction: str) -> bool:
    employee = _EMPLOYEE.search(restriction)
    if employee is None:
        return False
    if re.search(r"\bemployees?\s+as\s+(?:customers?|clients?)\b", restriction, re.I):
        return False
    prefix = restriction[: employee.start()]
    # A customer restriction performed through staff is not an employee clause.
    # A combined restriction on "customers or employees" still covers workers.
    last_object = re.split(r"\b(?:or|and)\b", prefix, flags=re.I)[-1]
    customer = re.search(
        r"\b(?:customers?|clients?)\b(?!['’]s\b|\s+(?:service|support)\b)",
        last_object,
        re.I,
    )
    return customer is None


def _scope(restriction: str) -> str | None:
    group = re.search(
        r"\b(senior managers|managers|executives|engineers|officers)\b",
        restriction,
        re.I,
    )
    if group:
        return group[1].lower()
    named = re.search(
        r"\b(?:employees|personnel|staff)\s+(?:assigned to|working on|in)\s+"
        r"([^.;]+?)(?=\s+(?:during|for|after|following)\b|[.;]|$)",
        restriction,
        re.I,
    )
    if named:
        return named[0].strip()
    if _EMPLOYEE.search(restriction):
        return "all employees"
    return None


def extract_clauses(
    document: ClauseDocument,
    employers: Sequence[Employer],
    *,
    franchisees: Sequence[Employer] = (),
) -> tuple[Clause, ...]:
    """Extract operative employee restrictions from every document block.

    Employers are the full resolved market; defined aliases and explicit party
    names link evidence locally. A brand-wide restriction must actually refer to
    other franchisees, rather than merely a franchisor's own staff.
    """
    text = document.text
    aliases = _aliases(text, employers)
    first = _RESTRICTION.search(text)
    preamble = text[: first.start()] if first else text
    preamble_parties = _mentions(preamble, aliases)
    clauses = []
    blocks = list(re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\s*\Z)", text, re.S))
    for index, block in enumerate(blocks):
        end = block.end()
        if index + 1 < len(blocks):
            following = blocks[index + 1]
            if _CARVEOUT.search(following[0]) and not _RESTRICTION.search(following[0]):
                end = following.end()
        original = text[block.start() : end]
        restrictions = [
            m
            for m in _RESTRICTION.finditer(original)
            if _employee_target(m[0])
            and not re.search(
                r"(?:not|no longer|neither party is)\s+(?:subject|bound).*?"
                r"(?:no[- ]hire|"
                r"non[- ]solicitation)",
                original[: m.end()],
                re.I,
            )
        ]
        if not restrictions:
            continue
        restrictive_text = " ".join(m[0] for m in restrictions)
        participants = {p.id: p for p in preamble_parties}
        participants.update({p.id: p for p in _mentions(original, aliases)})
        parties = tuple(participants[key] for key in sorted(participants))
        bound = set()
        mutual = any(
            _MUTUAL.search(original[original.rfind(".", 0, m.start()) + 1 : m.end()])
            for m in restrictions
        )
        for restriction in restrictions:
            prefix = original[: restriction.start()]
            # The closest named actor before shall/will binds that employer.
            actors = []
            for alias, employer in aliases.items():
                for match in re.finditer(
                    r"(?<!\w)" + re.escape(alias) + r"(?!\w)",
                    prefix,
                    re.I,
                ):
                    actors.append((match.end(), employer.id))
            if actors:
                bound.add(max(actors)[1])
        if mutual:
            bound = {p.id for p in parties}
        binding: Binding = "mutual" if mutual else "one_sided"
        if len(bound) > 1 and len(bound) == len(parties):
            binding = "mutual"
        if not bound and not mutual:
            binding = "unknown"
        brand_wide = bool(
            document.brand
            and re.search(
                r"\b(?:another|other|any)\s+franchisees?\b", restrictive_text, re.I
            )
        )
        if brand_wide:
            binding = "brand_wide"
            parties = tuple(sorted(franchisees, key=lambda p: p.id))
            bound = {p.id for p in parties}
        durations = list(_DURATION.finditer(original))
        # Prefer an explicit post-termination duration over "during the term".
        duration_match = next((m for m in durations if m[1]), None)
        if duration_match is None and durations:
            duration_match = durations[0]
        duration = (duration_match[1] or duration_match[2]) if duration_match else None
        verbs = restrictive_text.casefold()
        hires = bool(
            re.search(r"\b(?:hire|hiring|employ|employing|no[- ]hire)\b", verbs)
        )
        solicits = bool(re.search(r"solicit|recruit|induce|entice", verbs))
        clause_type = (
            "combined" if hires and solicits else "no_hire" if hires else "non_solicit"
        )
        identifier = sha256(
            (
                f"{document.issuer.id}:{document.provenance.url}:"
                f"{document.provenance.sha256}:{document.document_name}:{block.start()}"
            ).encode()
        ).hexdigest()
        clauses.append(
            Clause(
                id=identifier,
                text=original,
                filed_on=document.filed_on,
                clause_type=clause_type,
                parties=tuple(p.name for p in parties),
                employer_ids=tuple(p.id for p in parties),
                bound_employer_ids=tuple(sorted(bound)),
                binding=binding,
                duration=duration,
                scope=_scope(restrictive_text),
                carve_outs=tuple(m[0].strip() for m in _CARVEOUT.finditer(original)),
                brand=document.brand if brand_wide else None,
                document_name=document.document_name,
                start=block.start(),
                end=end,
                provenance=document.provenance,
            )
        )
    return tuple(clauses)


def _initialize(workspace: Workspace) -> None:
    workspace.connection.execute(
        "CREATE TABLE IF NOT EXISTS clause_details ("
        "clause_id VARCHAR PRIMARY KEY REFERENCES clauses(id), "
        "details_json JSON NOT NULL)"
    )


def store_document(
    workspace: Workspace,
    document: ClauseDocument,
    employers: Sequence[Employer],
    *,
    franchisees: Sequence[Employer] = (),
) -> tuple[Clause, ...]:
    """Persist structured evidence beside workspace filings/clauses.

    clause_details JSON version 1 stores dates, bindings, resolved parties,
    source receipt and offsets in the retained full-text document. Duplicate
    document content/source locations are idempotent, retaining first receipts.
    """
    _initialize(workspace)
    clauses = extract_clauses(document, employers, franchisees=franchisees)
    filing_id = sha256(
        f"{document.issuer.id}:{document.provenance.url}:{document.provenance.sha256}".encode()
    ).hexdigest()
    workspace.connection.execute("BEGIN TRANSACTION")
    stored = []
    try:
        workspace.connection.execute(
            "INSERT INTO filings VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            [
                filing_id,
                document.issuer.id,
                document.form_type,
                document.filed_on,
                document.accession_number,
                document.provenance.url,
                document.provenance.id,
            ],
        )
        for clause in clauses:
            existing = workspace.connection.execute(
                "SELECT details_json FROM clause_details WHERE clause_id = ?",
                [clause.id],
            ).fetchone()
            if existing:
                stored.append(_decode(existing[0]))
                continue
            workspace.connection.execute(
                "INSERT INTO clauses VALUES (?, ?, ?, ?, ?, ?)",
                [
                    clause.id,
                    filing_id,
                    clause.text,
                    clause.clause_type,
                    f"{clause.document_name}:{clause.start}-{clause.end}",
                    clause.provenance.id,
                ],
            )
            workspace.connection.execute(
                "INSERT INTO clause_details VALUES (?, ?)",
                [
                    clause.id,
                    json.dumps({"version": 1, "clause": asdict(clause)}, default=str),
                ],
            )
            stored.append(clause)
        workspace.connection.execute("COMMIT")
    except BaseException:
        workspace.connection.execute("ROLLBACK")
        raise
    return tuple(stored)


def _decode(encoded: str) -> Clause:
    payload = json.loads(encoded)
    if payload["version"] != 1:
        raise ValueError("Unsupported clause evidence version")
    data = payload["clause"]
    data["filed_on"] = date.fromisoformat(data["filed_on"])
    data["provenance"] = ProvenanceRecord.from_json(data["provenance"])
    for key in ("parties", "employer_ids", "bound_employer_ids", "carve_outs"):
        data[key] = tuple(data[key])
    return Clause(**data)


def score_pair(
    workspace: Workspace, employer_a_id: str, employer_b_id: str
) -> ClauseSignal:
    """Return traceable clause evidence; multi-signal ranking is separate."""
    return score_pairs(workspace, ((employer_a_id, employer_b_id),))[0]


def score_pairs(
    workspace: Workspace, pairs: Sequence[tuple[str, str]]
) -> tuple[ClauseSignal, ...]:
    """Decode filed evidence once, keeping clause-ID order for each pair."""
    if not pairs:
        return ()
    _initialize(workspace)
    buckets: dict[tuple[str, str], list[Clause]] = defaultdict(list)
    for (encoded,) in workspace.connection.execute(
        "SELECT details_json FROM clause_details ORDER BY clause_id"
    ).fetchall():
        clause = _decode(encoded)
        for pair in clause.pairs:
            buckets[pair_key(*pair)].append(clause)
    return tuple(
        ClauseSignal(*pair, tuple(buckets.get(pair, ())))
        for a, b in pairs
        for pair in (pair_key(a, b),)
    )


@dataclass(frozen=True)
class EvaluationResult:
    count: int
    positives: int
    negatives: int
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.positives if self.positives else 0.0


def evaluate_corpus(directory: Path) -> EvaluationResult:
    """Read outside-repo labels.json: [{text, positive, accession_number}].

    Each record is an independently labeled public clause or near-miss excerpt.
    accession_number is the host's citation; synthetic harness tests can omit
    it. Corpus size, mixed labels and thresholds are enforced by the eval test.
    No files, copied excerpts or predictions are written into the repository.
    """
    labels = json.loads((directory / "labels.json").read_text(encoding="utf-8"))
    positives = tp = fp = fn = 0
    for record in labels:
        if not isinstance(record["positive"], bool):
            raise ValueError("Corpus positive labels must be boolean")
        text = record["text"]
        receipt = ProvenanceRecord(
            id="evaluation",
            source="external evaluation corpus",
            url=record.get("accession_number", ""),
            collected_at=datetime.fromisoformat("2020-01-01T00:00:00+00:00"),
            method="READ",
            collection_method="local_labeled_evaluation",
            sha256=sha256(text.encode()).hexdigest(),
        )
        doc = ClauseDocument(
            text, receipt, date(2020, 1, 1), "evaluation", Employer("", "")
        )
        predicted = bool(extract_clauses(doc, ()))
        positive = record["positive"]
        positives += positive
        tp += predicted and positive
        fp += predicted and not positive
        fn += not predicted and positive
    return EvaluationResult(len(labels), positives, len(labels) - positives, tp, fp, fn)
