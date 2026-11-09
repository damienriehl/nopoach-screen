"""Bounded, pre-period directed degree-corrected Poisson block proposals.

This experimental local optimizer does not identify an economically correct
market. A fitted group must be reviewed and confirmed before screening.
"""

import calendar
import json
from collections import Counter
from collections.abc import Sequence, Set
from dataclasses import asdict, dataclass, replace
from datetime import date
from hashlib import sha256
from typing import Any, Literal

import numpy as np
import scipy  # type: ignore[import-untyped]
from numpy.typing import NDArray
from scipy.special import gammaln, xlogy  # type: ignore[import-untyped]

from nopoach.analytical_inputs import ClusterSettings
from nopoach.market.coverage import CoverageReport
from nopoach.market.define import FlowDefinedMarket
from nopoach.signals.flow import ResolvedPosition, build_moves

METHOD = "directed-degree-corrected-poisson-ipf-v1"
LIMITATIONS = (
    "Experimental pre-period partition, not a validated economic market.",
    "Earlier mobility may itself reflect restrictions or missing records.",
    "The optimizer reports a local optimum; group count is user chosen.",
    "Collection receipts do not establish historical availability.",
)


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _canonical(labels: Sequence[int]) -> tuple[int, ...]:
    mapping: dict[int, int] = {}
    return tuple(mapping.setdefault(int(label), len(mapping)) for label in labels)


@dataclass(frozen=True)
class BlockFit:
    labels: tuple[int, ...]
    expected: tuple[tuple[float, ...], ...]
    sending_factors: tuple[float, ...]
    receiving_factors: tuple[float, ...]
    block_rates: tuple[tuple[float, ...], ...]
    log_likelihood: float
    residual: float
    converged: bool
    sweeps: int
    likelihood_history: tuple[float, ...]


def fit_partition(
    counts: Sequence[Sequence[float]] | NDArray[np.float64],
    labels: Sequence[int],
    *,
    eligible: Sequence[Sequence[bool]] | NDArray[np.bool_] | None = None,
    max_sweeps: int = 200,
    tolerance: float = 1e-6,
) -> BlockFit:
    """Fit sender, receiver and block margins with self-dyad exposure excluded."""
    y = np.asarray(counts, dtype=float)
    if any(
        not isinstance(label, (int, np.integer)) or isinstance(label, bool) or label < 0
        for label in labels
    ):
        raise ValueError("Partition labels must be nonnegative integers")
    assignment = _canonical(labels)
    n = len(assignment)
    if (
        y.shape != (n, n)
        or n == 0
        or n > 64
        or not np.all(np.isfinite(y))
        or np.any(y < 0)
        or np.any(y != np.floor(y))
        or not 1 <= max_sweeps <= 200
        or not np.isfinite(tolerance)
        or tolerance <= 0
    ):
        raise ValueError("Supply a finite square count matrix and fitting settings")
    mask = (
        np.ones((n, n), dtype=bool)
        if eligible is None
        else np.asarray(eligible, dtype=bool).copy()
    )
    if mask.shape != y.shape:
        raise ValueError("Dyad eligibility must match the count matrix")
    np.fill_diagonal(mask, False)
    if np.any(y[~mask] != 0):
        raise ValueError("Positive counts cannot occur in ineligible or self dyads")
    z = np.asarray(assignment)
    k = max(assignment) + 1
    if k > 4:
        raise ValueError("At most four groups are supported")
    block = z[:, None] * k + z[None, :]
    with np.errstate(over="ignore"):
        rows, columns = y.sum(axis=1), y.sum(axis=0)
    if not np.all(np.isfinite(rows)) or not np.all(np.isfinite(columns)):
        raise ValueError("Count totals exceed the numeric fitting range")
    block_counts = np.bincount(
        block.ravel(), weights=y.ravel(), minlength=k * k
    ).reshape(k, k)
    sending, receiving, rates = np.ones(n), np.ones(n), np.ones((k, k))

    def expected() -> NDArray[np.float64]:
        return np.asarray(
            sending[:, None] * receiving[None, :] * rates[z[:, None], z[None, :]] * mask
        )

    def likelihood(mu: NDArray[np.float64]) -> float:
        return float(np.sum(xlogy(y[mask], mu[mask]) - mu[mask] - gammaln(y[mask] + 1)))

    def scale(
        target: NDArray[np.float64], actual: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        return np.divide(target, actual, out=np.zeros_like(target), where=actual > 0)

    mu = expected()
    history = [likelihood(mu)]
    residual = float("inf")
    for _sweep in range(1, max_sweeps + 1):
        sending *= scale(rows, mu.sum(axis=1))
        mu = expected()
        receiving *= scale(columns, mu.sum(axis=0))
        mu = expected()
        block_expected = np.bincount(
            block.ravel(), weights=mu.ravel(), minlength=k * k
        ).reshape(k, k)
        rates *= scale(block_counts, block_expected)
        # Fix the redundant factor scale without changing any fitted dyad mean.
        for group in range(k):
            indices = z == group
            for factors, axis in ((sending, 0), (receiving, 1)):
                mean = float(factors[indices].mean())
                if mean > 0:
                    factors[indices] /= mean
                    if axis == 0:
                        rates[group, :] *= mean
                    else:
                        rates[:, group] *= mean
        mu = expected()
        history.append(likelihood(mu))
        block_expected = np.bincount(
            block.ravel(), weights=mu.ravel(), minlength=k * k
        ).reshape(k, k)
        residual = max(
            float(np.max(np.abs(actual - target) / np.maximum(1, target)))
            for actual, target in (
                (mu.sum(axis=1), rows),
                (mu.sum(axis=0), columns),
                (block_expected, block_counts),
            )
        )
        if residual <= tolerance:
            break
    return BlockFit(
        assignment,
        tuple(tuple(float(v) for v in row) for row in mu),
        tuple(float(v) for v in sending),
        tuple(float(v) for v in receiving),
        tuple(tuple(float(v) for v in row) for row in rates),
        history[-1],
        residual,
        residual <= tolerance,
        _sweep,
        tuple(history),
    )


@dataclass(frozen=True)
class ExcludedEmployer:
    employer_id: str
    reason: str


@dataclass(frozen=True)
class TrainingGraph:
    employers: tuple[str, ...]
    counts: tuple[tuple[int, ...], ...]
    unassigned: tuple[ExcludedEmployer, ...]
    input_digest: str
    move_count: int
    source_references: tuple[str, ...]
    position_ids: tuple[str, ...] = ()


def _bounds(value: date, precision: str) -> tuple[date, date]:
    if precision == "year":
        return date(value.year, 1, 1), date(value.year, 12, 31)
    if precision == "month":
        return date(value.year, value.month, 1), date(
            value.year, value.month, calendar.monthrange(value.year, value.month)[1]
        )
    return value, value


def training_graph(
    market: FlowDefinedMarket,
    positions: Sequence[ResolvedPosition],
    coverage: CoverageReport,
    settings: ClusterSettings,
) -> TrainingGraph:
    """Hash only the declared pre-period graph and its pre-period coverage."""
    if settings.pre_end_year >= market.start_year:
        raise ValueError("Training must end strictly before the assessment starts")
    pre = replace(
        market,
        start_year=settings.pre_start_year,
        end_year=settings.pre_end_year,
        employer_ids=market.candidate_employer_ids,
        added_employer_ids=frozenset(),
        removed_employer_ids=frozenset(),
    )
    if not isinstance(coverage.market, FlowDefinedMarket) or (
        coverage.market.population_id != market.population_id
        or coverage.market.source_provenance_ids != market.source_provenance_ids
        or coverage.market.candidate_employer_ids != market.candidate_employer_ids
    ):
        raise ValueError(
            "Training coverage must describe the declared independent population"
        )
    first, last = (
        date(settings.pre_start_year, 1, 1),
        date(settings.pre_end_year, 12, 31),
    )
    counts: Counter[tuple[str, str]] = Counter()
    retained: list[dict[str, Any]] = []
    for move in build_moves(pre, positions):
        if move.end_precision not in {
            "day",
            "month",
            "year",
        } or move.start_precision not in {"day", "month", "year"}:
            continue
        end_bounds = _bounds(move.ended_on, move.end_precision)
        start_bounds = _bounds(move.started_on, move.start_precision)
        if (
            first <= min(end_bounds[0], start_bounds[0])
            and max(end_bounds[1], start_bounds[1]) <= last
        ):
            counts[move.source_id, move.destination_id] += 1
            retained.append(asdict(move))
    # JSON identity uses event dates, not the collection date or later position ends.
    for evidence in retained:
        evidence["ended_on"] = evidence["ended_on"].isoformat()
        evidence["started_on"] = evidence["started_on"].isoformat()
    known = {
        (r.employer.id, r.year): r
        for r in coverage.employers
        if settings.pre_start_year <= r.year <= settings.pre_end_year
        and r.employer.id in market.candidate_employer_ids
    }
    reasons: dict[str, str] = {}
    coverage_identity = []
    sources = set(market.source_provenance_ids)
    for employer in sorted(market.candidate_employer_ids):
        for year in range(settings.pre_start_year, settings.pre_end_year + 1):
            row = known.get((employer, year))
            if row is None or row.fraction is None:
                reasons[employer] = "Pre-period population coverage is unknown."
            headcount = row.estimate.headcount if row and row.estimate else None
            if headcount:
                sources.add(headcount.source)
            coverage_identity.append(
                (
                    employer,
                    year,
                    row.visible_workers if row else None,
                    {k: v for k, v in asdict(headcount).items() if k != "source"}
                    if headcount
                    else None,
                )
            )
    supported = set(market.candidate_employer_ids) - reasons.keys()
    while supported:
        edges = (
            (i, j)
            for (i, j), count in counts.items()
            if count and i in supported and j in supported
        )
        sending_ids, receiving_ids = set(), set()
        for i, j in edges:
            sending_ids.add(i)
            receiving_ids.add(j)
        removed = supported - (sending_ids & receiving_ids)
        if not removed:
            break
        for employer in removed:
            reasons[employer] = (
                "Both sending and receiving pre-period support are required."
            )
        supported -= removed
    names = tuple(sorted(supported))
    retained.sort(
        key=lambda m: (
            m["person_id"],
            m["origin_position_id"],
            m["destination_position_id"],
        )
    )
    digest = _digest(
        {
            "method": METHOD,
            "population_id": market.population_id,
            "universe": sorted(market.candidate_employer_ids),
            "sources": sorted(market.source_provenance_ids),
            "period": [first.isoformat(), last.isoformat()],
            "moves": retained,
            "coverage": coverage_identity,
        }
    )
    return TrainingGraph(
        names,
        tuple(tuple(counts[a, b] for b in names) for a in names)
        if len(names) <= settings.max_employers
        else (),
        tuple(ExcludedEmployer(e, reasons[e]) for e in sorted(reasons)),
        digest,
        sum(
            count
            for (a, b), count in counts.items()
            if a in supported and b in supported
        ),
        tuple(sorted(sources)),
        tuple(
            sorted(
                {
                    str(move[key])
                    for move in retained
                    for key in ("origin_position_id", "destination_position_id")
                }
            )
        ),
    )


@dataclass(frozen=True)
class ClusterProposal:
    status: Literal["fitted", "unavailable", "incomplete"]
    reason: str
    groups: tuple[tuple[str, ...], ...]
    unassigned: tuple[ExcludedEmployer, ...]
    universe: tuple[str, ...]
    settings: ClusterSettings
    input_digest: str
    digest: str
    log_likelihood: float | None
    one_group_log_likelihood: float | None
    residual: float | None
    restart_agreement: float | None
    refits: int
    move_count: int
    source_references: tuple[str, ...]
    population_id: str = ""
    source_provenance_ids: tuple[str, ...] = ()
    training_position_ids: tuple[str, ...] = ()
    method: str = METHOD
    numpy_version: str = np.__version__
    scipy_version: str = scipy.__version__
    limitations: tuple[str, ...] = LIMITATIONS


class _Incomplete(Exception):
    pass


def _neighbors(labels: tuple[int, ...], groups: int) -> list[tuple[int, ...]]:
    candidates = set()
    sizes = Counter(labels)
    for i, old in enumerate(labels):
        if sizes[old] > 3:
            for new in range(groups):
                if new != old:
                    changed = list(labels)
                    changed[i] = new
                    candidates.add(_canonical(changed))
        for j in range(i + 1, len(labels)):
            if labels[j] != old:
                changed = list(labels)
                changed[i], changed[j] = changed[j], changed[i]
                candidates.add(_canonical(changed))
    return sorted(candidates - {labels})


def propose_clusters(
    market: FlowDefinedMarket,
    positions: Sequence[ResolvedPosition],
    coverage: CoverageReport,
    settings: ClusterSettings,
    *,
    source_references: Sequence[str] = (),
) -> ClusterProposal:
    """Fit a bounded local optimum without consulting downstream screen scores."""
    graph: TrainingGraph | None = None
    reference: float | None = None
    refits = 0

    def result(
        status: Literal["fitted", "unavailable", "incomplete"],
        reason: str,
        groups: tuple[tuple[str, ...], ...] = (),
        likelihood: float | None = None,
        residual: float | None = None,
        agreement: float | None = None,
    ) -> ClusterProposal:
        proposal = ClusterProposal(
            status,
            reason,
            groups,
            graph.unassigned if graph else (),
            tuple(sorted(market.candidate_employer_ids)),
            settings,
            graph.input_digest if graph else "",
            "",
            likelihood,
            reference,
            residual,
            agreement,
            refits,
            graph.move_count if graph else 0,
            tuple(
                sorted(
                    set(source_references)
                    | (set(graph.source_references) if graph else set())
                )
            ),
            population_id=market.population_id,
            source_provenance_ids=tuple(sorted(market.source_provenance_ids)),
            training_position_ids=graph.position_ids if graph else (),
        )
        return replace(proposal, digest=_digest(asdict(proposal)))

    try:
        graph = training_graph(market, positions, coverage, settings)
    except ValueError as exc:
        return result("unavailable", str(exc))
    n = len(graph.employers)
    if n > settings.max_employers:
        return result(
            "unavailable", "Supported employers exceed the explicit fitting budget."
        )
    if n < 3 * settings.groups:
        return result(
            "unavailable", "Each group requires at least three supported employers."
        )
    cache: dict[tuple[int, ...], tuple[float, float]] = {}

    def fit(labels: tuple[int, ...]) -> tuple[float, float]:
        nonlocal refits
        if labels in cache:
            return cache[labels]
        if refits >= settings.candidate_refits:
            raise _Incomplete(
                "Candidate refit budget exhausted; review a smaller population."
            )
        refits += 1
        fixed = fit_partition(
            graph.counts,
            labels,
            max_sweeps=settings.fitting_sweeps,
            tolerance=settings.tolerance,
        )
        if not fixed.converged:
            raise _Incomplete(
                "A fixed-partition fit did not converge within the fitting budget."
            )
        cache[labels] = fixed.log_likelihood, fixed.residual
        return cache[labels]

    try:
        reference = fit((0,) * n)[0]
        if settings.groups == 1:
            return result(
                "fitted",
                "One-group reference only; no reliable subdivision is claimed. "
                "Local optimum.",
                (graph.employers,),
                reference,
                cache[(0,) * n][1],
                1.0,
            )
        rng = np.random.default_rng(settings.seed)
        outcomes = []
        for _ in range(settings.restarts):
            labels = _canonical(
                rng.permutation(np.arange(n) % settings.groups).tolist()
            )
            value = fit(labels)
            converged = False
            for _ in range(settings.sweeps):
                best_labels, best = labels, value
                for candidate in _neighbors(labels, settings.groups):
                    candidate_fit = fit(candidate)
                    if candidate_fit[0] > best[0] + 1e-8:
                        best_labels, best = candidate, candidate_fit
                if best_labels == labels:
                    converged = True
                    break
                labels, value = best_labels, best
            if not converged:
                raise _Incomplete(
                    "Partition sweep budget exhausted before a local optimum."
                )
            outcomes.append((labels, value))
        labels, best = min(outcomes, key=lambda item: (-round(item[1][0], 8), item[0]))
        groups = tuple(
            tuple(
                graph.employers[i] for i, label in enumerate(labels) if label == group
            )
            for group in range(settings.groups)
        )
        agreement = sum(outcome[0] == labels for outcome in outcomes) / len(outcomes)
        return result(
            "fitted",
            "Experimental local optimum; review membership and exclusions "
            "before confirming.",
            groups,
            best[0],
            best[1],
            agreement,
        )
    except _Incomplete as exc:
        return result("incomplete", str(exc))


def validate_proposal(
    proposal: ClusterProposal,
    market: FlowDefinedMarket,
    positions: Sequence[ResolvedPosition],
    coverage: CoverageReport,
) -> None:
    """Reject stale graph evidence without repeating the expensive partition search."""
    _check_identity(proposal, market)
    if (
        proposal.status != "fitted"
        or training_graph(market, positions, coverage, proposal.settings).input_digest
        != proposal.input_digest
    ):
        raise ValueError(
            "The training graph changed or the proposal is incomplete; "
            "review a new proposal"
        )


def _check_identity(proposal: ClusterProposal, market: FlowDefinedMarket) -> None:
    if (
        proposal.population_id != market.population_id
        or proposal.source_provenance_ids != tuple(sorted(market.source_provenance_ids))
        or proposal.universe != tuple(sorted(market.candidate_employer_ids))
    ):
        raise ValueError("Cluster proposal describes a different population")
    if proposal.digest != _digest({**asdict(proposal), "digest": ""}):
        raise ValueError("Cluster proposal integrity check failed")


def confirm_group(
    proposal: ClusterProposal,
    market: FlowDefinedMarket,
    group: int,
    *,
    add: Set[str] = frozenset(),
    remove: Set[str] = frozenset(),
) -> FlowDefinedMarket:
    _check_identity(proposal, market)
    if (
        proposal.status != "fitted"
        or not 0 <= group < len(proposal.groups)
        or tuple(sorted(market.candidate_employer_ids)) != proposal.universe
    ):
        raise ValueError("Confirm a fitted group from this population")
    fitted = frozenset(proposal.groups[group])
    added = frozenset((market.added_employer_ids | add) - remove)
    removed = frozenset((market.removed_employer_ids - add) | remove)
    final = (fitted | added) - removed
    if len(final) < 2:
        raise ValueError("Confirm at least two employers for pair screening")
    return replace(
        market,
        employer_ids=final,
        cluster_digest=proposal.digest,
        fitted_employer_ids=fitted,
        added_employer_ids=added,
        removed_employer_ids=removed,
    )


@dataclass(frozen=True)
class ClusterSelection:
    universe: tuple[str, ...]
    fitted_employer_ids: tuple[str, ...]
    final_employer_ids: tuple[str, ...]
    added_employer_ids: tuple[str, ...]
    removed_employer_ids: tuple[str, ...]
    excluded_employer_ids: tuple[str, ...]
    unassigned: tuple[ExcludedEmployer, ...]
    cross_boundary_pair_count: int
    omitted_pair_count: int


def selection_summary(
    proposal: ClusterProposal, market: FlowDefinedMarket
) -> ClusterSelection:
    _check_identity(proposal, market)
    if (
        market.cluster_digest != proposal.digest
        or tuple(sorted(market.fitted_employer_ids)) not in proposal.groups
        or market.employer_ids
        != (market.fitted_employer_ids | market.added_employer_ids)
        - market.removed_employer_ids
    ):
        raise ValueError("Confirmed market and proposal differ")
    excluded = market.candidate_employer_ids - market.employer_ids
    n, selected = len(market.candidate_employer_ids), len(market.employer_ids)
    return ClusterSelection(
        proposal.universe,
        tuple(sorted(market.fitted_employer_ids)),
        tuple(sorted(market.employer_ids)),
        tuple(sorted(market.added_employer_ids)),
        tuple(sorted(market.removed_employer_ids)),
        tuple(sorted(excluded)),
        proposal.unassigned,
        selected * len(excluded),
        n * (n - 1) // 2 - selected * (selected - 1) // 2,
    )
