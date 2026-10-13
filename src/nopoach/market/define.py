"""Explicit occupation/geography markets and repeatable competitor selection.

O*NET related-occupation scores are supplied from a lawfully held table; this
module neither collects O*NET nor invents similarity from SOC code proximity.
JSON preserves manual additions/removals for the caller's screen parameters.
"""

import json
import re
from collections.abc import Iterable, Set
from dataclasses import dataclass, replace
from math import isfinite
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from nopoach.market.coverage import WorkforceEstimate


def soc_code(code: str) -> str:
    """Map an O*NET detailed code to the SOC used by U3/U5."""
    if not re.fullmatch(r"\d{2}-\d{4}(?:\.\d{2})?", code):
        raise ValueError("Occupation must use SOC NN-NNNN or O*NET NN-NNNN.NN")
    return code[:7]


@dataclass(frozen=True)
class Geography:
    kind: Literal["state", "metro"]
    code: str

    def __post_init__(self) -> None:
        if self.kind not in {"state", "metro"} or not self.code.strip():
            raise ValueError("Geography requires a state or metro and a code")
        object.__setattr__(self, "code", self.code.strip().upper())


@dataclass(frozen=True)
class Market:
    occupation_codes: frozenset[str]
    geography: Geography
    start_year: int
    end_year: int
    employer_ids: frozenset[str] | None = None
    added_employer_ids: frozenset[str] = frozenset()
    removed_employer_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if (
            not self.occupation_codes
            or not 1 <= self.start_year <= self.end_year <= 9999
        ):
            raise ValueError("Market requires occupations and an ordered year window")
        object.__setattr__(
            self,
            "occupation_codes",
            frozenset(soc_code(c) for c in self.occupation_codes),
        )

    def edit_employers(
        self, *, add: Set[str] = frozenset(), remove: Set[str] = frozenset()
    ) -> "Market":
        """Return selection state; a simultaneous removal takes precedence."""
        narrowed = self.employer_ids
        if narrowed is not None:
            narrowed = frozenset((narrowed | add) - remove)
        return replace(
            self,
            employer_ids=narrowed,
            added_employer_ids=frozenset((self.added_employer_ids | add) - remove),
            removed_employer_ids=frozenset((self.removed_employer_ids - add) | remove),
        )

    def to_json(self) -> str:
        """Serializable state for U1 screens.parameters_json, with no DB write."""
        return json.dumps(
            {
                "version": 1,
                "occupation_codes": sorted(self.occupation_codes),
                "geography": {"kind": self.geography.kind, "code": self.geography.code},
                "start_year": self.start_year,
                "end_year": self.end_year,
                "employer_ids": None
                if self.employer_ids is None
                else sorted(self.employer_ids),
                "added_employer_ids": sorted(self.added_employer_ids),
                "removed_employer_ids": sorted(self.removed_employer_ids),
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: str) -> "Market":
        data = json.loads(value)
        if data["version"] != 1:
            raise ValueError("Unsupported market definition version")
        return cls(
            frozenset(data["occupation_codes"]),
            Geography(**data["geography"]),
            data["start_year"],
            data["end_year"],
            None if data["employer_ids"] is None else frozenset(data["employer_ids"]),
            frozenset(data["added_employer_ids"]),
            frozenset(data["removed_employer_ids"]),
        )


@dataclass(frozen=True)
class OccupationSimilarity:
    occupation_code: str
    related_code: str
    score: float
    source: str


@dataclass(frozen=True)
class OccupationSuggestion:
    code: str
    score: float
    source: str


def suggest_occupations(
    market: Market, similarities: Iterable[OccupationSimilarity]
) -> tuple[OccupationSuggestion, ...]:
    suggestions: dict[str, OccupationSuggestion] = {}
    for row in similarities:
        if soc_code(row.occupation_code) not in market.occupation_codes:
            continue
        if not isfinite(row.score) or not 0 <= row.score <= 1 or not row.source.strip():
            raise ValueError("O*NET similarity needs a finite 0..1 score and source")
        code = soc_code(row.related_code)
        if code in market.occupation_codes:
            continue
        suggestion = OccupationSuggestion(code, row.score, row.source)
        previous = suggestions.get(code)
        if previous is None or (-row.score, row.source) < (
            -previous.score,
            previous.source,
        ):
            suggestions[code] = suggestion
    return tuple(sorted(suggestions.values(), key=lambda s: (-s.score, s.code)))


@dataclass(frozen=True)
class Employer:
    id: str
    name: str


@dataclass(frozen=True)
class Competitor:
    employer: Employer
    estimate: "WorkforceEstimate | None"
    manually_added: bool


@dataclass(frozen=True)
class CompetitorProposal:
    competitors: tuple[Competitor, ...]
    needs_employer_entry: bool
    reason: str


def propose_competitors(
    market: Market,
    employers: Iterable[Employer],
    estimates: Iterable["WorkforceEstimate"],
    *,
    size_floor: float = 1,
) -> CompetitorProposal:
    """Rank latest in-window estimates; retain explicit choices across reruns."""
    from nopoach.market.coverage import matching_estimates

    if not isfinite(size_floor) or size_floor < 0:
        raise ValueError("Size floor must be finite and nonnegative")
    latest: dict[str, WorkforceEstimate] = {}
    for candidate in matching_estimates(market, estimates).values():
        previous = latest.get(candidate.employer_id)
        if previous is None or candidate.year > previous.year:
            latest[candidate.employer_id] = candidate
    competitors = []
    estimated = 0
    for employer in employers:
        if employer.id in market.removed_employer_ids:
            continue
        if market.employer_ids is not None and employer.id not in market.employer_ids:
            continue
        estimate = latest.get(employer.id)
        headcount = estimate.headcount if estimate else None
        above_floor = headcount is not None and headcount.value >= size_floor
        explicit = (
            employer.id in market.added_employer_ids or market.employer_ids is not None
        )
        if above_floor:
            estimated += 1
        if above_floor or explicit:
            competitors.append(Competitor(employer, estimate, explicit))
    competitors.sort(
        key=lambda c: (
            -(
                c.estimate.headcount.value
                if c.estimate and c.estimate.headcount
                else -1
            ),
            c.employer.id,
        )
    )
    sparse = estimated < 2
    return CompetitorProposal(
        tuple(competitors),
        sparse,
        "Workforce estimates are too sparse to propose competitors; enter employers."
        if sparse
        else "Competitors are ordered by estimated workforce in this market.",
    )
