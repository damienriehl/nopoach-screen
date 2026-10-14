"""Offline known-case backtests through the same pipeline as interactive screens.

Callers supply explicit Market definitions: a case's public geographic scope
must not silently become a particular metro or a list limited to defendants.
Example: run_backtest(workspace, {"high-tech": market}). No collection occurs.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml

from nopoach.market.define import Market
from nopoach.market.sufficiency import SignalSufficiency
from nopoach.pipeline import PairReferences, ScreenResult, run_screen
from nopoach.rank.aggregate import RankedPair
from nopoach.resolve.employers import normalize_employer
from nopoach.store.workspace import Workspace

LIMITATION = (
    "Cutoffs use employment and event dates only; record-availability dating "
    "is not implemented. A later-obtained record describing an earlier event "
    "can enter the run. These results do not establish historical detectability."
)
PERCENTILE_METHOD = (
    "Ranks are 1-based within the pipeline's visible mutual or one-way list, "
    "including comparison employers, with pipeline ordering unchanged. "
    "Percentile = 100 * (population - rank) / (population - 1); "
    "a singleton is 100. Higher is better. Insufficient pairs omitted by the "
    "pipeline are not in that denominator. Partial rankings are not full-market ranks."
)


@dataclass(frozen=True)
class CaseDefinition:
    id: str
    name: str
    parties: tuple[str, ...]
    occupation: str
    geography: str
    agreement_start_year: int
    agreement_end_year: int
    disclosure_date: date
    known_pairs: tuple[tuple[str, str], ...]
    sources: tuple[str, ...]
    notes: str


def load_cases(path: str | Path | None = None) -> tuple[CaseDefinition, ...]:
    """Read public facts, not collected records or a claimed market definition."""
    source = Path(path) if path is not None else Path(__file__).with_name("cases.yaml")
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    return tuple(
        CaseDefinition(
            **{
                **row,
                "parties": tuple(row["parties"]),
                "known_pairs": tuple(tuple(pair) for pair in row["known_pairs"]),
                "sources": tuple(row["sources"]),
                "disclosure_date": date.fromisoformat(str(row["disclosure_date"])),
            }
        )
        for row in data["cases"]
    )


@dataclass(frozen=True)
class PairResult:
    parties: tuple[str, str]
    status: Literal["ranked", "insufficient data"]
    ranking: Literal["mutual", "one_way"] | None
    rank: int | None
    population: int
    percentile: float | None
    reason: str
    pair: RankedPair | None


@dataclass(frozen=True)
class CaseResult:
    case: CaseDefinition
    market: Market
    cutoff: date
    screen_id: str
    pairs: tuple[PairResult, ...]
    sufficiency: SignalSufficiency
    partial_screen: bool
    references: tuple[PairReferences, ...]
    ranking_method: str


@dataclass(frozen=True)
class BacktestReport:
    cases: tuple[CaseResult, ...]
    json_path: Path
    markdown_path: Path


def _pair_result(
    parties: tuple[str, str],
    screen: ScreenResult,
    employers: Mapping[str, tuple[str, ...]],
    party_ids: Mapping[str, str],
) -> PairResult:
    ids = []
    for name in parties:
        matches = (
            (party_ids[name],)
            if name in party_ids
            else employers.get(normalize_employer(name), ())
        )
        if len(matches) != 1:
            return PairResult(
                parties,
                "insufficient data",
                None,
                None,
                0,
                None,
                f"Party {name} is absent or ambiguous; "
                "supply its resolved employer ID.",
                None,
            )
        ids.append(matches[0])
    key = frozenset(ids)
    for ranking, pairs in (("mutual", screen.mutual), ("one_way", screen.one_way)):
        for rank, pair in enumerate(pairs, 1):
            if frozenset((pair.employer_a_id, pair.employer_b_id)) == key:
                reliability = next(
                    (
                        p
                        for p in screen.sufficiency.pairs
                        if frozenset((p.employer_a_id, p.employer_b_id)) == key
                    ),
                    None,
                )
                sufficient = bool(reliability and reliability.reliable) or any(
                    verdict.status == "sufficient"
                    for verdict in (screen.sufficiency.clauses, screen.sufficiency.ties)
                )
                if not sufficient:
                    return PairResult(
                        parties,
                        "insufficient data",
                        None,
                        None,
                        0,
                        None,
                        "; ".join(
                            (
                                reliability.reason
                                if reliability
                                else screen.sufficiency.flow.reason,
                                screen.sufficiency.clauses.reason,
                                screen.sufficiency.ties.reason,
                            )
                        ),
                        pair,
                    )
                population = len(pairs)
                percentile = (
                    100.0
                    if population == 1
                    else 100 * (population - rank) / (population - 1)
                )
                return PairResult(
                    parties,
                    "ranked",
                    "mutual" if ranking == "mutual" else "one_way",
                    rank,
                    population,
                    percentile,
                    "Partial screen; see per-signal sufficiency verdicts."
                    if screen.partial_screen
                    else "Screen data is sufficient.",
                    pair,
                )
    reason = next(
        (
            p.reason
            for p in screen.unreliable
            if frozenset((p.pair.employer_a_id, p.pair.employer_b_id)) == key
        ),
        "The pair is outside the selected market or has insufficient data.",
    )
    return PairResult(parties, "insufficient data", None, None, 0, None, reason, None)


def run_backtest(
    workspace: Workspace,
    markets: Mapping[str, Market],
    *,
    cases: Sequence[CaseDefinition] | None = None,
    party_ids: Mapping[str, str] | None = None,
) -> BacktestReport:
    """Run selected case IDs with explicit markets; write only to the workspace.

    Market selections retain all supplied comparison employers. Public party
    names match conservatively through employer normalization; callers may supply
    resolved IDs for aliases or ambiguities without mutating source records.
    The pipeline owns event filtering and signal/reliability behavior, including
    keeping independent clause/tie leads visible when flow is insufficient.
    """
    catalog = {c.id: c for c in (load_cases() if cases is None else cases)}
    selected = [(catalog[case_id], market) for case_id, market in markets.items()]
    employers: dict[str, tuple[str, ...]] = {}
    for identifier, name in workspace.connection.execute(
        "SELECT id, name FROM employers ORDER BY id"
    ).fetchall():
        normalized = normalize_employer(name)
        employers[normalized] = (*employers.get(normalized, ()), identifier)
    results = []
    for case, market in selected:
        cutoff = case.disclosure_date - timedelta(days=1)
        screen = run_screen(workspace, market, as_of=cutoff)
        results.append(
            CaseResult(
                case,
                market,
                cutoff,
                screen.screen_id,
                tuple(
                    _pair_result(p, screen, employers, party_ids or {})
                    for p in case.known_pairs
                ),
                screen.sufficiency,
                screen.partial_screen,
                screen.references,
                screen.method,
            )
        )
    # Workspace.open already enforces an out-of-repository root. Resolve the
    # output directory as well: a symlink must not redirect case results to git.
    directory = (workspace.root / "backtests").resolve()
    if not directory.is_relative_to(workspace.root):
        raise ValueError("Backtest reports must remain inside the workspace")
    directory.mkdir(mode=0o700, exist_ok=True)
    stem = str(uuid4())
    report = BacktestReport(
        tuple(results), directory / f"{stem}.json", directory / f"{stem}.md"
    )
    payload = {
        "version": 1,
        "limitation": LIMITATION,
        "percentile_method": PERCENTILE_METHOD,
        "cases": [
            {**asdict(result), "market": json.loads(result.market.to_json())}
            for result in results
        ],
    }
    report.json_path.write_text(
        json.dumps(payload, default=_json_value, indent=2) + "\n", encoding="utf-8"
    )
    report.markdown_path.write_text(_markdown(report), encoding="utf-8")
    return report


def _json_value(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _markdown(report: BacktestReport) -> str:
    lines = ["# Known-case backtest", "", LIMITATION, "", PERCENTILE_METHOD, ""]
    for result in report.cases:
        case = result.case
        market = result.market
        lines.extend(
            [
                f"## {case.name}",
                "",
                f"Disclosure: {case.disclosure_date}; event cutoff: {result.cutoff}.",
                f"Public case scope: {case.occupation}; {case.geography}. "
                f"Reported period: {case.agreement_start_year}–"
                f"{case.agreement_end_year}.",
                f"Selected market: {', '.join(sorted(market.occupation_codes))}; "
                f"{market.geography.kind} {market.geography.code}; "
                f"{market.start_year}–{min(market.end_year, result.cutoff.year)}.",
                f"Screen: {result.screen_id}; partial: {result.partial_screen}.",
                case.notes,
                "",
                result.ranking_method,
                "",
                "### Sufficiency",
                "",
            ]
        )
        for name in ("flow", "clauses", "ties"):
            verdict = getattr(result.sufficiency, name)
            lines.append(f"- {name}: {verdict.status} — {verdict.reason}")
        lines.append("")
        for pair in result.pairs:
            lines.extend([f"### {pair.parties[0]} / {pair.parties[1]}", ""])
            if pair.rank is None:
                lines.append(f"insufficient data: {pair.reason}")
            else:
                lines.append(
                    f"{pair.ranking}: rank {pair.rank} / {pair.population}; "
                    f"percentile {pair.percentile:.2f}. {pair.reason}"
                )
            if pair.pair is not None:
                lines.extend(["", pair.pair.summary, ""])
                lines.extend(
                    f"- {s.key}: score {s.score:.4f}; "
                    f"weight {s.weight:.2f}; fired {s.fired}"
                    for s in pair.pair.signals
                )
                if pair.pair.ties:
                    lines.extend(
                        f"- Tie: {tie.tie_type}; score {tie.score:.4f}; "
                        f"positions {', '.join(p.id for p in tie.positions)}"
                        for tie in pair.pair.ties.ties
                    )
                if pair.pair.clauses:
                    lines.extend(
                        f"- Clause: {clause.id}; filed {clause.filed_on}; {clause.text}"
                        for clause in pair.pair.clauses.clauses
                    )
            lines.append("")
        lines.extend(["Public-record sources:", ""])
        lines.extend(f"- {source}" for source in case.sources)
        lines.append("")
    return "\n".join(lines)
