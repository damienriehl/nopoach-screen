"""Explainable leadership links from imported and filed positions.

Scores are screening heuristics, not calibrated probabilities: shared directors
in the requested period weigh 4, executive moves 2, sequential leadership links
1, and former coworkers/other leadership links .5. Historical shared directors
weigh 1. Recency is 1 / (1 + years since the last tie date), measured at period
end with 365.25 days/year; undated links receive .5. Every component is returned.

Year/month bounds retain imported date precision and indicate reported-period overlap,
not proof of exact-day service. Missing bounds are never extended indefinitely:
a filing observation can bound an explicitly reported tenure or supply a dated
snapshot. With no dated evidence, shared leadership is retained as an undated
link, without claiming overlap or a move. The import loader reconciles identities.
Results are read-only; dossiers can serialize the returned dataclasses and follow
position IDs, import row numbers, and original filing receipts in raw records.
"""

import json
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import product
from typing import Any

from nopoach.rank.pairs import pair_key
from nopoach.store.workspace import Workspace


@dataclass(frozen=True)
class RecordEvidence:
    import_id: str
    row_number: int
    provenance_id: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class PositionEvidence:
    id: str
    person_id: str
    person_name: str | None
    employer_id: str
    employer_name: str
    title: str | None
    start_date: date | None
    end_date: date | None
    start_precision: str
    end_precision: str
    provenance_id: str
    records: tuple[RecordEvidence, ...]
    observed_on: date | None
    date_basis: str


@dataclass(frozen=True)
class Tie:
    tie_type: str
    person_ids: tuple[str, ...]
    start_date: date | None
    end_date: date | None
    positions: tuple[PositionEvidence, ...]
    base_weight: float
    recency_factor: float
    score: float


@dataclass(frozen=True)
class PairTies:
    employer_a_id: str
    employer_b_id: str
    start_date: date
    end_date: date
    score: float
    ties: tuple[Tie, ...]


def positions(workspace: Workspace) -> list[PositionEvidence]:
    connection = workspace.connection
    records: dict[str, list[RecordEvidence]] = defaultdict(list)
    # Import tables are created lazily.
    if "imported_rows" in workspace.table_names():
        for position_id, import_id, number, provenance, raw in connection.execute(
            "SELECT position_id, import_id, row_number, provenance_id, raw_json "
            "FROM imported_rows WHERE position_id IS NOT NULL "
            "ORDER BY import_id, row_number"
        ).fetchall():
            records[position_id].append(
                RecordEvidence(import_id, number, provenance, json.loads(raw))
            )
    result = []
    for row in connection.execute(
        "SELECT p.id, p.person_id, people.name, p.employer_id, employers.name, "
        "p.title, p.start_date, p.end_date, p.start_precision, p.end_precision, "
        "p.provenance_id FROM positions p "
        "JOIN people ON people.id = p.person_id "
        "JOIN employers ON employers.id = p.employer_id ORDER BY p.id"
    ).fetchall():
        observations = tuple(records[row[0]])
        dates = [
            date.fromisoformat(str(record.raw["filed_on"]))
            for record in observations
            if record.raw.get("filed_on")
        ]
        observed = max(dates) if dates else None
        if row[6] is not None and row[7] is not None:
            basis = "reported_tenure"
        elif observed and row[6] is not None:
            basis = "tenure_through_filing"
        elif observed and row[7] is None:
            basis = "filing_observation"
        elif row[6] is not None or row[7] is not None:
            basis = "dated_endpoint"
        else:
            basis = "unknown"
        result.append(
            PositionEvidence(
                id=row[0],
                person_id=row[1],
                person_name=row[2],
                employer_id=row[3],
                employer_name=row[4],
                title=row[5],
                start_date=row[6],
                end_date=row[7],
                start_precision=row[8],
                end_precision=row[9],
                provenance_id=row[10],
                records=observations,
                observed_on=observed,
                date_basis=basis,
            )
        )
    return result


def board(position: PositionEvidence) -> bool:
    flags = [
        str(flag).casefold()
        for record in position.records
        for flag in record.raw.get("role_flags", [])
    ]
    if "director" in flags or "trustee" in flags:
        return True
    title = (position.title or "").casefold()
    return bool(
        re.search(r"\b(board|trustee)\b", title)
        or any(
            re.fullmatch(
                r"(?:(?:independent|non executive|non-executive) )?director",
                part.strip(),
            )
            for part in title.split(";")
        )
    )


def executive(position: PositionEvidence) -> bool:
    return bool(
        re.search(
            r"\b(?:ceo|cfo|coo|cto|cio|chro|chief\s+[\w\s]+?\s+officer|"
            r"president|executive vice president|senior vice president|evp|svp)\b",
            position.title or "",
            re.I,
        )
    )


def _span(position: PositionEvidence) -> tuple[date, date] | None:
    start, end = position.start_date, position.end_date
    if start is not None and end is not None:
        return start, end
    if start is not None:
        return start, max(start, position.observed_on or start)
    if end is not None:
        return end, end
    if position.observed_on is not None:
        return position.observed_on, position.observed_on
    return None


def _overlap(
    left: PositionEvidence, right: PositionEvidence
) -> tuple[date, date] | None:
    a, b = _span(left), _span(right)
    if a is None or b is None:
        return None
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return (start, end) if start <= end else None


def score_pair(
    workspace: Workspace,
    employer_a_id: str,
    employer_b_id: str,
    *,
    start_date: date,
    end_date: date,
    precomputed_positions: Sequence[PositionEvidence] | None = None,
) -> PairTies:
    """Score one resolved pair as of period end; retain all supporting positions.

    Direction is in the ordered positions of moves/sequential links. The pair
    itself is canonicalized so reversing the requested firms gives equal output.
    Past ties remain visible with recency decay; future events do not contribute.
    A zero score means no leadership links in the supplied resolved evidence,
    and makes no claim about completeness of collection.
    """
    if end_date < start_date:
        raise ValueError("end_date before start_date")
    if employer_a_id == employer_b_id:
        raise ValueError("A pair requires two distinct employers")
    employer_a_id, employer_b_id = pair_key(employer_a_id, employer_b_id)
    eligible_positions = [
        p
        for p in (
            positions(workspace)
            if precomputed_positions is None
            else precomputed_positions
        )
        if (span := _span(p)) is None or span[0] <= end_date
    ]
    left = [p for p in eligible_positions if p.employer_id == employer_a_id]
    right = [p for p in eligible_positions if p.employer_id == employer_b_id]
    ties: dict[
        tuple[str, tuple[str, ...], str | None, date | None, date | None], Tie
    ] = {}

    def add(
        kind: str,
        evidence: tuple[PositionEvidence, ...],
        dates: tuple[date, date] | None,
    ) -> None:
        first, last = dates if dates else (None, None)
        if first is not None and first > end_date:
            return
        if last is not None:
            last = min(last, end_date)
        people = tuple(sorted({p.person_id for p in evidence}))
        # Independent former workplaces are distinct ties. Repeated filings of
        # the same continuous link retain their records but count only once.
        former_employer = (
            evidence[2].employer_id if kind == "former_coworkers" else None
        )
        group = kind, people, former_employer
        for key, previous in list(ties.items()):
            if key[:3] != group:
                continue
            same_dates = (first, last) == (previous.start_date, previous.end_date)
            overlaps = (
                kind in {"shared_director", "leadership_link", "former_coworkers"}
                and first is not None
                and last is not None
                and previous.start_date is not None
                and previous.end_date is not None
                and max(first, previous.start_date) <= min(last, previous.end_date)
            )
            if not (same_dates or overlaps):
                continue
            if first is not None and previous.start_date is not None:
                first = min(first, previous.start_date)
            if last is not None and previous.end_date is not None:
                last = max(last, previous.end_date)
            by_id = {p.id: p for p in previous.positions + evidence}
            role_order = {
                role: index
                for index, role in enumerate(
                    dict.fromkeys(
                        (p.employer_id, p.person_id)
                        for p in previous.positions + evidence
                    )
                )
            }
            evidence = tuple(
                sorted(
                    by_id.values(),
                    key=lambda p: (role_order[p.employer_id, p.person_id], p.id),
                )
            )
            del ties[key]
        weight = {
            "shared_director": 4.0 if last and last >= start_date else 1.0,
            "executive_move": 2.0,
            "sequential_link": 1.0,
            "former_coworkers": 0.5,
            "leadership_link": 0.5,
        }[kind]
        recency = 1 / (1 + (end_date - last).days / 365.25) if last is not None else 0.5
        ties[group + (first, last)] = Tie(
            kind, people, first, last, evidence, weight, recency, weight * recency
        )

    for a, b in product(left, right):
        if a.person_id != b.person_id:
            continue
        if not ((board(a) or executive(a)) and (board(b) or executive(b))):
            continue
        overlap = _overlap(a, b)
        if board(a) and board(b) and overlap:
            add("shared_director", (a, b), overlap)
            continue
        earlier, later = a, b
        if b.end_date and a.start_date and b.end_date < a.start_date:
            earlier, later = b, a
        if (
            earlier.end_date
            and later.start_date
            and earlier.end_date < later.start_date
        ):
            kind = (
                "executive_move"
                if executive(earlier) and executive(later)
                else "sequential_link"
            )
            add(kind, (earlier, later), (earlier.end_date, later.start_date))
        else:
            add("leadership_link", (a, b), overlap)

    history: dict[str, list[PositionEvidence]] = defaultdict(list)
    for p in eligible_positions:
        history[p.person_id].append(p)
    for a, b in product(left, right):
        if a.person_id == b.person_id or not (executive(a) and executive(b)):
            continue
        if a.start_date is None or b.start_date is None:
            continue
        for old_a, old_b in product(history[a.person_id], history[b.person_id]):
            if old_a.employer_id != old_b.employer_id:
                continue
            together = _overlap(old_a, old_b)
            if together and together[1] < min(a.start_date, b.start_date):
                add("former_coworkers", (a, b, old_a, old_b), together)

    ordered = tuple(
        sorted(
            ties.values(),
            key=lambda t: (
                -t.score,
                t.tie_type,
                t.person_ids,
                t.start_date or date.min,
                t.end_date or date.min,
            ),
        )
    )
    return PairTies(
        employer_a_id,
        employer_b_id,
        start_date,
        end_date,
        math.fsum(t.score for t in ordered),
        ordered,
    )
