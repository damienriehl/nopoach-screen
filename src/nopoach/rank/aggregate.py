"""Transparent fixed-weight ranking of all selected employer pairs (KTD8).

Flow carries .50, clauses .30, ties .20, without missing-signal redistribution.
U7's reliable, flagged score already lies in [0, 1]; an unflagged/unreliable
flow contributes zero. Any U8 filed restriction scores 1, regardless of binding
or clause count. U9's nonnegative raw tie score is normalized as x/(x+4), with
4 the weight of a current shared director. Evidence counts do not masquerade
as probabilities. These fixed conventions need lawful real-data backtesting.

Sort by the two-or-more-signals tier, then descending weighted score, then IDs.
Reliable one-way flow suppression belongs exclusively to the separate list,
even when the pair has a mutual filed clause. The mutual list contains all
remaining pairs, including clause-only leads and pairs with no fired signals.
Explanation checks are descriptive and never filter or penalize a lead.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import fsum

from nopoach.market.define import Employer
from nopoach.rank import wording
from nopoach.rank.explanations import (
    EmployerExplanationData,
    ExplanationCheck,
    check_explanations,
)
from nopoach.signals.clauses import ClauseSignal
from nopoach.signals.flow import FlowScreen, PairFlow
from nopoach.signals.ties import PairTies

FLOW_WEIGHT = 0.50
CLAUSE_WEIGHT = 0.30
TIE_WEIGHT = 0.20
TIE_SCALE = 4.0


@dataclass(frozen=True)
class SignalScore:
    key: wording.SignalKey
    score: float
    weight: float
    fired: bool

    @property
    def contribution(self) -> float:
        return self.score * self.weight

    @property
    def label(self) -> str:
        return wording.SIGNAL_LABELS[self.key]


@dataclass(frozen=True)
class RankedPair:
    employer_a_id: str
    employer_b_id: str
    employer_a_name: str
    employer_b_name: str
    score: float
    signals: tuple[SignalScore, ...]
    flow: PairFlow | None
    clauses: ClauseSignal | None
    ties: PairTies | None
    explanations: tuple[ExplanationCheck, ...]
    label: str
    summary: str

    @property
    def fired_signals(self) -> tuple[wording.SignalKey, ...]:
        return tuple(s.key for s in self.signals if s.fired)


@dataclass(frozen=True)
class RankedScreen:
    mutual: tuple[RankedPair, ...]
    one_way: tuple[RankedPair, ...]
    method: str = wording.RANK_METHOD
    explanation_method: str = wording.EXPLANATION_METHOD


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _order(pair: RankedPair) -> tuple[bool, float, str, str]:
    return (
        len(pair.fired_signals) < 2,
        -pair.score,
        pair.employer_a_id,
        pair.employer_b_id,
    )


def rank_pairs(
    employers: Sequence[Employer],
    *,
    flow: FlowScreen | None = None,
    clauses: Sequence[ClauseSignal] = (),
    ties: Sequence[PairTies] = (),
    explanation_data: Mapping[str, EmployerExplanationData] | None = None,
) -> RankedScreen:
    """Combine U7/U8/U9 outputs; callers supply U2-selected resolved employers.

    Supply at most one result per signal/pair, from the same market and period.
    All combinations of selected employers appear exactly once, including
    pairs absent from signal inputs. Retain original evidence and reliability
    reasons for dossiers; absent inputs do not assert complete collection.
    """
    flows = (
        {_pair_key(p.employer_a_id, p.employer_b_id): p for p in flow.pairs}
        if flow is not None
        else {}
    )
    filed = {_pair_key(p.employer_a_id, p.employer_b_id): p for p in clauses}
    linked = {_pair_key(p.employer_a_id, p.employer_b_id): p for p in ties}
    data = explanation_data or {}
    mutual: list[RankedPair] = []
    one_way: list[RankedPair] = []
    for a, b in combinations(sorted(employers, key=lambda e: e.id), 2):
        key = a.id, b.id
        worker_flow, clause, tie = flows.get(key), filed.get(key), linked.get(key)
        flow_fired = bool(
            worker_flow
            and worker_flow.reliable
            and worker_flow.classification != "neither"
        )
        unilateral = bool(
            flow_fired
            and worker_flow
            and worker_flow.classification == "one_way_suppression"
        )
        clause_fired = bool(clause and clause.clauses)
        tie_score = tie.score / (tie.score + TIE_SCALE) if tie else 0.0
        scores = (
            SignalScore(
                "flow",
                worker_flow.score if flow_fired and worker_flow else 0,
                FLOW_WEIGHT,
                flow_fired,
            ),
            SignalScore("clauses", float(clause_fired), CLAUSE_WEIGHT, clause_fired),
            SignalScore("ties", tie_score, TIE_WEIGHT, tie_score > 0),
        )
        fired = tuple(s.key for s in scores if s.fired)
        pair = RankedPair(
            a.id,
            b.id,
            a.name,
            b.name,
            fsum(s.contribution for s in scores),
            scores,
            worker_flow,
            clause,
            tie,
            check_explanations(
                a.id,
                b.id,
                data.get(a.id, EmployerExplanationData()),
                data.get(b.id, EmployerExplanationData()),
            ),
            wording.ONE_WAY_LABEL
            if unilateral
            else wording.MUTUAL_LABEL
            if fired
            else wording.NO_SIGNAL_LABEL,
            wording.pair_summary(a.name, b.name, fired, one_way=unilateral),
        )
        (one_way if unilateral else mutual).append(pair)
    return RankedScreen(
        tuple(sorted(mutual, key=_order)), tuple(sorted(one_way, key=_order))
    )
