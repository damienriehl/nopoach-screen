"""Directional, evidence-backed missing worker flow (U7).

The caller supplies U3-resolved positions, crosswalked position geographies,
planar employer coordinates in kilometres, occupation shares, and U6 coverage.
No geography is guessed from free-text locations. PPML fits all ordered pairs
and years with separate origin/year and destination/year effects: reversing a
pair is a different observation, never a symmetric pooling of move counts.
Size and firm-wide hiring shocks enter the employer/year effects. Structural
zero overlap pairs have zero expected flow. Unknown coverage stays unreliable.

Ratios/intervals condition on the gravity fit. Model-based placebo pairs scan
exactly the same breaks and shrinkage statistic; BH covers all pair directions.
These provisional thresholds need lawful real-data backtests before a pilot.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from itertools import combinations, permutations
from math import dist, log
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyfixest as pf
from scipy.special import xlogy  # type: ignore[import-untyped]

from nopoach.market.coverage import CoverageReport
from nopoach.market.define import Geography, Market, soc_code
from nopoach.market.sufficiency import signal_sufficiency
from nopoach.signals.breaks import FlowBreak, break_candidates, detect_break
from nopoach.signals.shrink import (
    RateRatio,
    benjamini_hochberg,
    estimate_prior,
    placebo_pvalue,
    shrink_ratio,
)
from nopoach.store.workspace import Workspace


@dataclass(frozen=True)
class ResolvedPosition:
    id: str
    person_id: str
    employer_id: str
    occupation_code: str
    geography: Geography | None
    start_date: date | None
    end_date: date | None
    start_precision: str
    end_precision: str
    provenance_id: str

    @classmethod
    def from_workspace(
        cls, workspace: Workspace, *, geography_by_position: Mapping[str, Geography]
    ) -> tuple["ResolvedPosition", ...]:
        """Keep unknown-geography jobs so missing crosswalks cannot bridge spells."""
        return tuple(
            cls(
                row[0],
                row[1],
                row[2],
                row[3] or "",
                geography_by_position.get(row[0]),
                *row[4:],
            )
            for row in workspace.connection.execute(
                "SELECT id, person_id, employer_id, occupation_code, "
                "start_date, end_date, start_precision, end_precision, "
                "provenance_id FROM positions ORDER BY id"
            ).fetchall()
        )


@dataclass(frozen=True)
class FlowEmployer:
    id: str
    location_km: tuple[float, float]
    occupation_shares: Mapping[str, float]


@dataclass(frozen=True)
class PersonMove:
    person_id: str
    source_id: str
    destination_id: str
    year: int
    year_interval: tuple[int, int]
    ended_on: date
    started_on: date
    end_precision: str
    start_precision: str
    origin_position_id: str
    destination_position_id: str
    provenance_ids: tuple[str, ...]


@dataclass(frozen=True)
class YearFlow:
    year: int
    observed: int
    expected: float
    ratio: RateRatio
    moves: tuple[PersonMove, ...]


@dataclass(frozen=True)
class DirectionFlow:
    source_id: str
    destination_id: str
    years: tuple[YearFlow, ...]
    ratio: RateRatio
    break_result: FlowBreak | None
    statistic: float
    pvalue: float
    qvalue: float
    suppressed: bool


@dataclass(frozen=True)
class PairFlow:
    employer_a_id: str
    employer_b_id: str
    directions: tuple[DirectionFlow, DirectionFlow]
    classification: Literal["mutual_suppression", "one_way_suppression", "neither"]
    label: str
    score: float
    reliable: bool
    reliability_reason: str


@dataclass(frozen=True)
class FlowScreen:
    pairs: tuple[PairFlow, ...]
    mutual: tuple[PairFlow, ...]
    one_way: tuple[PairFlow, ...]
    model: str
    prior_strength: float
    fdr: float
    placebo_count: int
    seed: int


def _in_market(position: ResolvedPosition, market: Market) -> bool:
    return bool(position.occupation_code) and (
        soc_code(position.occupation_code) in market.occupation_codes
        and position.geography == market.geography
        and position.employer_id not in market.removed_employer_ids
        and (market.employer_ids is None or position.employer_id in market.employer_ids)
    )


def build_moves(
    market: Market, positions: Sequence[ResolvedPosition], *, max_gap_days: int = 90
) -> tuple[PersonMove, ...]:
    """Successive spells only; excluded/out-of-market jobs cannot be bridged.

    Year/month-only endpoints use the reported bounds, retain their precision,
    and widen the reported move-year interval. Unknown dates do not prove moves.
    """
    people: dict[str, list[ResolvedPosition]] = defaultdict(list)
    for position in positions:
        people[position.person_id].append(position)
    moves = []
    for person_id, spells in sorted(people.items()):
        spells.sort(
            key=lambda p: (p.start_date or date.min, p.end_date or date.max, p.id)
        )
        for origin, destination in zip(spells, spells[1:], strict=False):
            if not (_in_market(origin, market) and _in_market(destination, market)):
                continue
            end, start = origin.end_date, destination.start_date
            if (
                end is None
                or start is None
                or origin.end_precision == "unknown"
                or destination.start_precision == "unknown"
                or origin.employer_id == destination.employer_id
            ):
                continue
            gap = (start - end).days
            if (
                not 0 <= gap <= max_gap_days
                or not market.start_year <= start.year <= market.end_year
            ):
                continue
            interval = (min(end.year, start.year), start.year)
            moves.append(
                PersonMove(
                    person_id,
                    origin.employer_id,
                    destination.employer_id,
                    start.year,
                    interval,
                    end,
                    start,
                    origin.end_precision,
                    destination.start_precision,
                    origin.id,
                    destination.id,
                    tuple(sorted({origin.provenance_id, destination.provenance_id})),
                )
            )
    return tuple(moves)


def _statistics(y: np.ndarray, e: np.ndarray, prior: float) -> np.ndarray:
    totals = np.atleast_2d(y).sum(axis=1)
    expected = float(e.sum())
    ratio = (totals + prior) / (expected + prior)
    # One-sided Poisson likelihood loss, using the shrunken rate in both
    # observed and placebo series. Break scan includes candidate selection.
    suppressed = 2 * (xlogy(totals, ratio) - expected * (ratio - 1))
    suppressed = np.where(ratio < 1, np.maximum(0, suppressed), 0)
    breaks = break_candidates(y, e)
    return np.asarray(np.maximum(suppressed, breaks.max(axis=1)))


def _gravity(frame: Any) -> np.ndarray:
    """PPML estimates ordered (directional) flows with employer/year effects."""
    active = frame[(frame.overlap > 0)].copy()
    expected = np.zeros(len(frame))
    if active.observed.sum() == 0:
        return expected
    # Poisson separation: zero-margin employer/year cells have exactly zero
    # fitted expectation. Remove them before fitting, keeping them in output.
    active = active[
        (active.groupby("origin_year")["observed"].transform("sum") > 0)
        & (active.groupby("destination_year")["observed"].transform("sum") > 0)
    ]
    # Constant covariates (or covariates exactly absorbed by employer effects)
    # carry no identifying information. pyfixest rejects a design in which
    # every covariate is collinear, so fit its equivalent FE-only formula.
    effects = pd.get_dummies(active[["origin_year", "destination_year"]], dtype=float)
    covariates = active[["distance_km", "log_overlap"]].to_numpy(dtype=float)
    coefficients = np.linalg.lstsq(effects.to_numpy(), covariates, rcond=None)[0]
    residuals = covariates - effects.to_numpy() @ coefficients
    identified = [
        name
        for i, name in enumerate(("distance_km", "log_overlap"))
        if np.linalg.norm(residuals[:, i]) > 1e-8
    ]
    rhs = " + ".join(identified) if identified else "1"
    model = pf.fepois(
        f"observed ~ {rhs} | origin_year + destination_year",
        data=active,
        vcov="iid",
        fixef_rm="none",
        separation_check=[],
    )
    expected[active.index.to_numpy()] = model.predict(type="response")
    return expected


def screen_flow(
    market: Market,
    positions: Sequence[ResolvedPosition],
    employers: Sequence[FlowEmployer],
    coverage: CoverageReport,
    *,
    max_gap_days: int = 90,
    seed: int = 0,
    placebo_count: int = 1999,
    fdr: float = 0.10,
) -> FlowScreen:
    if coverage.market != market:
        raise ValueError("Coverage must describe the screened market")
    selected = sorted(
        (
            e
            for e in employers
            if e.id not in market.removed_employer_ids
            and (market.employer_ids is None or e.id in market.employer_ids)
        ),
        key=lambda e: e.id,
    )
    moves = build_moves(market, positions, max_gap_days=max_gap_days)
    counts: dict[tuple[str, str, int], list[PersonMove]] = defaultdict(list)
    for move in moves:
        counts[move.source_id, move.destination_id, move.year].append(move)
    years = tuple(range(market.start_year, market.end_year + 1))
    rows = []
    for a, b in permutations(selected, 2):
        overlap = sum(
            min(a.occupation_shares.get(o, 0), b.occupation_shares.get(o, 0))
            for o in market.occupation_codes
        )
        for year in years:
            rows.append(
                dict(
                    source=a.id,
                    destination=b.id,
                    year=year,
                    observed=len(counts[a.id, b.id, year]),
                    overlap=overlap,
                    log_overlap=log(overlap) if overlap else 0,
                    distance_km=dist(a.location_km, b.location_km),
                    origin_year=f"{a.id}:{year}",
                    destination_year=f"{b.id}:{year}",
                )
            )
    frame = pd.DataFrame(rows)
    if not rows:
        return FlowScreen(
            (), (), (), "pyfixest fepois PPML", 30, fdr, placebo_count, seed
        )
    frame["expected"] = _gravity(frame)
    grouped = list(frame.groupby(["source", "destination"], sort=True))
    # Estimate heterogeneity on yearly rates, the same temporal resolution as
    # the break scan. Pooling entire histories would erase a post-break network
    # and over-shrink it merely because its earlier years were normal.
    prior = estimate_prior(frame.observed.to_list(), frame.expected.to_list())
    rng = np.random.default_rng(seed)
    directions = []
    effective = {}
    for (source, destination), group in grouped:
        y, e = (
            group.observed.to_numpy(dtype=float),
            group.expected.to_numpy(dtype=float),
        )
        change = detect_break(years, y, e)
        start = change.split_index if change else 0
        ratio = shrink_ratio(float(y[start:].sum()), float(e[start:].sum()), prior)
        statistic = float(_statistics(y, e, prior)[0])
        placebo = rng.poisson(e, size=(placebo_count, len(e)))
        pvalue = placebo_pvalue(statistic, _statistics(placebo, e, prior))
        annual = tuple(
            YearFlow(
                year,
                int(y[i]),
                float(e[i]),
                shrink_ratio(float(y[i]), float(e[i]), prior),
                tuple(counts[source, destination, year]),
            )
            for i, year in enumerate(years)
        )
        directions.append(
            DirectionFlow(
                source, destination, annual, ratio, change, statistic, pvalue, 1, False
            )
        )
        # Expected directional opportunities, not the suppressed outcome count.
        effective[source, destination] = float(e.sum())
    qvalues = benjamini_hochberg([d.pvalue for d in directions])
    directional = {}
    for d, q in zip(directions, qvalues, strict=True):
        suppressed = q <= fdr and d.ratio.ratio < 0.5
        # A scan maximum is a candidate, not a reported break. Apply the same
        # placebo/FDR/shrinkage decision before giving it investigative weight.
        change = d.break_result if suppressed else None
        ratio = (
            d.ratio
            if change
            else shrink_ratio(
                sum(y.observed for y in d.years),
                sum(y.expected for y in d.years),
                prior,
            )
        )
        directional[d.source_id, d.destination_id] = replace(
            d, qvalue=q, suppressed=suppressed, break_result=change, ratio=ratio
        )
    reliability = {
        (p.employer_a_id, p.employer_b_id): p
        for p in signal_sufficiency(
            coverage, effective, clause_feeds=(), tie_feeds=()
        ).pairs
    }
    pairs = []
    for a, b in combinations(selected, 2):
        ds = directional[a.id, b.id], directional[b.id, a.id]
        reliable = reliability.get((a.id, b.id))
        ok = reliable is not None and reliable.reliable
        reason = (
            reliable.reason
            if reliable
            else "Coverage evidence not supplied for this pair."
        )
        flags = sum(d.suppressed for d in ds) if ok else 0
        classification: Literal[
            "mutual_suppression", "one_way_suppression", "neither"
        ] = (
            "mutual_suppression"
            if flags == 2
            else "one_way_suppression"
            if flags == 1
            else "neither"
        )
        label = (
            "Possible mutual flow suppression"
            if flags == 2
            else "Possible unilateral no-hire policy"
            if flags == 1
            else "Neither"
        )
        score = (
            min(1 - d.ratio.ratio for d in ds)
            if flags == 2
            else max(1 - d.ratio.ratio for d in ds)
        )
        pairs.append(
            PairFlow(a.id, b.id, ds, classification, label, max(0, score), ok, reason)
        )
    ordered = tuple(
        sorted(pairs, key=lambda p: (-p.score, p.employer_a_id, p.employer_b_id))
    )
    return FlowScreen(
        ordered,
        tuple(p for p in ordered if p.classification == "mutual_suppression"),
        tuple(p for p in ordered if p.classification == "one_way_suppression"),
        "pyfixest fepois PPML: origin/year + destination/year; "
        "distance + occupation overlap",
        prior,
        fdr,
        placebo_count,
        seed,
    )
