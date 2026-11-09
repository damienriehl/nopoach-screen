"""Individual restriction candidates and explicitly scoped reviewed prevalence.

Text rules emit evidence for review, never legal conclusions. A negative outcome
requires complete applicable-document review; an unmatched sentence says nothing
about the worker's restrictions. Reviewed populations are not workforce samples
unless the supplier establishes that separately.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from nopoach.imports.noncompetes import ContractCorpus, ContractRecord

THRESHOLD = 0.5
METHOD = "individual-restriction-rules-v1"
LIMITATION = (
    "Descriptive review-population check only; no inference of workforce prevalence, "
    "legal enforceability or causation. Historical availability of supplied documents "
    "is not established by collection receipts."
)
_COMPETITION = re.compile(
    r"\b(?:competitor|competitors|competing|competitive|competition|compete|"
    r"non[- ]?compete)\b",
    re.I,
)
_ACTIVITY = re.compile(
    r"\b(?:work(?:ing)?|join|employ(?:ed|ment)?|engage|serve|compete)\b", re.I
)
_POST = re.compile(
    r"\b(?:(?:after|following|upon)\b.{0,90}\b"
    r"(?:termination|separation|cessation|employment (?:ends|ceases|terminates))"
    r"|post[- ]employment)\b",
    re.I,
)
_ACTOR = re.compile(r"\b(?:employee|executive|worker|you)\b", re.I)
_DUTY = re.compile(
    r"\b(?:shall not|may not|must not|will not|agrees? not|prohibited|"
    r"restrict(?:ed|ion)|non[- ]?compete)\b",
    re.I,
)
_NEGATED = re.compile(
    r"\b(?:not(?: be)? prohibited|not(?: be)? restricted|no restriction|"
    r"no non[- ]?compete|"
    r"may work|free to (?:work|join|compete))\b",
    re.I,
)
_DURATION = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|twelve|eighteen|twenty[- ]four)"
    r"\s+(?:days?|months?|years?)\b",
    re.I,
)
_EXCEPTION = re.compile(r"\b(?:except|unless|provided that)\b[^.!?]*", re.I)


@dataclass(frozen=True)
class NonCompeteCandidate:
    id: str
    employer_id: str
    document_id: str
    document_date: str
    full_text: str
    start: int
    end: int
    text: str
    classification: Literal["candidate", "negated", "uncertain"]
    duration: tuple[str, ...]
    exceptions: tuple[str, ...]
    uncertainty: tuple[str, ...]
    review: str = "unreviewed"
    provenance_ids: tuple[str, ...] = ()


def extract_noncompetes(
    text: str, *, employer_id: str, document_id: str, document_date: str = ""
) -> tuple[NonCompeteCandidate, ...]:
    """Retain exact sentence offsets in already-normalized source text."""
    if len(text) > 65536:
        raise ValueError("Contract text exceeds the 65536-character field limit")
    candidates = []
    for match in re.finditer(r"[^.!?]+(?:[.!?]|$)", text):
        sentence = match.group().strip()
        if not (
            _COMPETITION.search(sentence)
            and _ACTIVITY.search(sentence)
            and _POST.search(sentence)
        ):
            continue
        negated = bool(_NEGATED.search(sentence))
        if not (_DUTY.search(sentence) or negated):
            continue
        # Solicitation alone is not a ban on the individual's subsequent work.
        if re.search(r"\bsolicit", sentence, re.I) and not re.search(
            r"\b(?:work|working|join|employed|compete)\b", sentence, re.I
        ):
            continue
        actor = bool(_ACTOR.search(sentence))
        if not actor and re.search(
            r"\b(?:seller|buyer|sale of (?:the )?business|closing)\b", sentence, re.I
        ):
            continue
        first = match.start() + len(match.group()) - len(match.group().lstrip())
        last = match.end() - len(match.group()) + len(match.group().rstrip())
        uncertainty: tuple[str, ...] = (
            "Scope and date applicability requires explicit document review.",
        )
        if not actor:
            uncertainty += ("Individual employee actor is ambiguous.",)
        classification: Literal["candidate", "negated", "uncertain"] = (
            "negated" if negated else "candidate" if actor else "uncertain"
        )
        identity = f"{employer_id}\0{document_id}\0{text}\0{first}\0{last}"
        candidates.append(
            NonCompeteCandidate(
                sha256(identity.encode()).hexdigest(),
                employer_id,
                document_id,
                document_date,
                text,
                first,
                last,
                text[first:last],
                classification,
                tuple(m.group() for m in _DURATION.finditer(sentence)),
                tuple(m.group().rstrip(".,; ") for m in _EXCEPTION.finditer(sentence)),
                uncertainty,
            )
        )
    return tuple(candidates)


@dataclass(frozen=True)
class EmployerPrevalence:
    employer_id: str
    population_description: str
    sampling_basis: str
    scope: str
    numerator: int
    denominator: int | None
    reviewed_workers: int
    unresolved_workers: int
    complete: bool
    supported: bool | None
    reason: str
    evidence_ids: tuple[str, ...]
    provenance_ids: tuple[str, ...]
    threshold: float = THRESHOLD


@dataclass(frozen=True)
class ContractAssessment:
    employers: tuple[EmployerPrevalence, ...]
    candidates: tuple[NonCompeteCandidate, ...]
    limitations: tuple[str, ...] = (LIMITATION,)
    method: str = METHOD


def assess_contracts(
    corpus: "ContractCorpus",
    *,
    scope: str,
    start: date,
    end: date,
    as_of: date | None = None,
    provenance_id: str = "",
) -> ContractAssessment:
    """Require complete scoped review for each declared worker, then count once."""
    if start > end:
        raise ValueError("Assessment interval is reversed")
    cutoff = as_of or date.max
    source = provenance_id or corpus.provenance_id
    sources = (source,) if source else ()
    by_employer: dict[str, list[ContractRecord]] = defaultdict(list)
    candidate_map: dict[str, NonCompeteCandidate] = {}
    per_record: dict[ContractRecord, tuple[NonCompeteCandidate, ...]] = {}
    for record in corpus.records:
        by_employer[record.employer_id].append(record)
        evidence = (
            ()
            if record.document_date is None or record.document_date > cutoff
            else extract_noncompetes(
                record.text,
                employer_id=record.employer_id,
                document_id=record.document_id,
                document_date=record.document_date.isoformat(),
            )
        )
        per_record[record] = evidence
        for candidate in evidence:
            old = candidate_map.get(candidate.id)
            review = (
                record.review
                if old is None or old.review == record.review
                else "conflicting"
            )
            candidate_map[candidate.id] = replace(
                candidate, review=review, provenance_ids=sources
            )
    counts = dict(corpus.declared_workers)
    populations = []
    for employer in sorted(by_employer.keys() | counts.keys()):
        records = by_employer[employer]
        workers: dict[str, list[ContractRecord]] = defaultdict(list)
        problems: set[str] = set()
        if corpus.scope != scope:
            problems.add("declared population scope does not match assessment")
        if corpus.reviewed_on and corpus.reviewed_on > cutoff:
            problems.add("review date is after the historical cutoff")
        for record in records:
            if record.worker_key:
                workers[record.worker_key].append(record)
            else:
                problems.add("worker keys are missing")
        positive = reviewed = unresolved = 0
        evidence_ids: set[str] = set()
        for documents in workers.values():
            outcomes: set[str] = set()
            worker_problems: set[str] = set()
            versions: dict[
                str, set[tuple[str, date | None, date | None, date | None]]
            ] = defaultdict(set)
            for record in documents:
                versions[record.document_id].add(
                    (
                        record.text,
                        record.document_date,
                        record.effective_from,
                        record.effective_to,
                    )
                )
            if any(len(values) > 1 for values in versions.values()):
                worker_problems.add("conflicting versions of the same document")
            for record in documents:
                if record.scope != scope:
                    worker_problems.add("document scope does not match assessment")
                if record.document_date is None or record.document_date > cutoff:
                    worker_problems.add("document date is unknown or after the cutoff")
                if (
                    record.effective_from is None
                    or record.effective_from > start
                    or (
                        not record.ongoing
                        and (record.effective_to is None or record.effective_to < end)
                    )
                ):
                    worker_problems.add(
                        "effective interval does not establish applicability "
                        "for the full period"
                    )
                if not record.documents_complete:
                    worker_problems.add(
                        "complete applicable-document review was not affirmed"
                    )
                extracted = per_record[record]
                positive_candidates = [
                    c for c in extracted if c.classification == "candidate"
                ]
                if record.review == "positive" and positive_candidates:
                    outcomes.add("positive")
                    evidence_ids.update(c.id for c in positive_candidates)
                elif record.review == "negative" and not positive_candidates:
                    outcomes.add("negative")
                else:
                    worker_problems.add(
                        "review or individual restriction evidence remains unresolved"
                    )
            if len(outcomes) > 1:
                worker_problems.add("conflicting document reviews or amendments")
            if worker_problems or len(outcomes) != 1:
                unresolved += 1
                problems.update(worker_problems)
            else:
                reviewed += 1
                positive += outcomes == {"positive"}
        denominator = counts.get(employer)
        if denominator is None:
            problems.add("no declared unique-worker denominator")
        elif len(workers) != denominator:
            problems.add("unique-worker records do not match the declared denominator")
        complete = not problems and denominator is not None and reviewed == denominator
        supported = (
            positive / denominator >= THRESHOLD if complete and denominator else None
        )
        status = (
            "Complete reviewed population"
            if complete
            else "Prevalence not testable: " + "; ".join(sorted(problems))
        )
        reason = (
            f"{status}. {positive} applicable positive reviews / "
            f"{denominator if denominator is not None else 'unknown'} "
            "declared workers; "
            f"{reviewed} resolved, {unresolved} unresolved. "
            f"Threshold: {THRESHOLD:.0%}. "
            f"Population: {corpus.population_description}. "
            f"Basis: {corpus.sampling_basis}. {LIMITATION}"
        )
        populations.append(
            EmployerPrevalence(
                employer,
                corpus.population_description,
                corpus.sampling_basis,
                scope,
                positive,
                denominator,
                reviewed,
                unresolved,
                complete,
                supported,
                reason,
                tuple(sorted(evidence_ids)),
                sources,
            )
        )
    return ContractAssessment(
        tuple(populations), tuple(candidate_map[k] for k in sorted(candidate_map))
    )
