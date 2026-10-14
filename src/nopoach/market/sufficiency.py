"""Explain per-signal sufficiency and mark each affected employer pair.

Thresholds are provisional calibration starting points, not proof of an
agreement. Flow scoring supplies effective directional opportunity samples across the
window (after weighting), not just the potentially suppressed A->B moves:
using the suspicious outcome as the sample size would hide a zero-flow lead.
Flow scoring owns shrinkage/placebos/BH (KTD7); this module only owns reliability gates.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from math import isfinite
from typing import Literal

from nopoach.market.coverage import CoverageReport

MIN_COVERAGE = 0.20
MIN_EFFECTIVE_MOVES = 30


@dataclass(frozen=True)
class Verdict:
    status: Literal["sufficient", "insufficient"]
    reason: str


@dataclass(frozen=True)
class PairReliability:
    employer_a_id: str
    employer_b_id: str
    reliable: bool
    reason: str


@dataclass(frozen=True)
class FeedCoverage:
    """Expected publishing dates in the period, and fully collected dates.

    The collector supplies its calendar (EDGAR excludes nonpublishing days);
    collected means complete bulk documents, not merely a downloaded index.
    Empty expectation is not evidence of full period collection.
    """

    source: str
    expected_dates: frozenset[date]
    collected_dates: frozenset[date]


@dataclass(frozen=True)
class SignalSufficiency:
    flow: Verdict
    clauses: Verdict
    ties: Verdict
    pairs: tuple[PairReliability, ...]


def _feed_verdict(feeds: Sequence[FeedCoverage], report: CoverageReport) -> Verdict:
    if not feeds:
        return Verdict(
            "insufficient", "Bulk feed collection evidence was not supplied."
        )
    gaps = []
    for feed in feeds:
        expected = {
            d
            for d in feed.expected_dates
            if report.market.start_year <= d.year <= report.market.end_year
        }
        if not expected:
            gaps.append(f"{feed.source}: expected period dates not supplied")
        elif missing := expected - feed.collected_dates:
            gaps.append(
                f"{feed.source}: missing "
                + ", ".join(d.isoformat() for d in sorted(missing))
            )
    return (
        Verdict("insufficient", "Bulk feed gaps: " + "; ".join(gaps) + ".")
        if gaps
        else Verdict(
            "sufficient",
            "All expected bulk feed dates for the period were collected without gaps.",
        )
    )


def signal_sufficiency(
    report: CoverageReport,
    effective_moves: Mapping[tuple[str, str], float],
    *,
    clause_feeds: Sequence[FeedCoverage],
    tie_feeds: Sequence[FeedCoverage],
) -> SignalSufficiency:
    """Both directions and every employer/year must meet their thresholds."""
    employers = {row.employer.id: row.employer for row in report.employers}
    failures: dict[str, list[str]] = {key: [] for key in employers}
    for row in report.employers:
        prefix = f"{row.employer.name} ({row.year})"
        if row.fraction is None:
            failures[row.employer.id].append(f"{prefix}: denominator unknown")
        elif row.fraction < MIN_COVERAGE:
            failures[row.employer.id].append(
                f"{prefix}: coverage {row.fraction:.1%} below {MIN_COVERAGE:.0%}"
            )
    pairs = []
    for a, b in combinations(sorted(employers), 2):
        reasons = failures[a] + failures[b]
        for source, destination in ((a, b), (b, a)):
            sample = effective_moves.get((source, destination))
            direction = f"{employers[source].name} -> {employers[destination].name}"
            if sample is None or not isfinite(sample) or sample < 0:
                reasons.append(f"{direction}: effective moves unknown")
            elif sample < MIN_EFFECTIVE_MOVES:
                reasons.append(
                    f"{direction}: {sample:g} effective moves "
                    f"below {MIN_EFFECTIVE_MOVES}"
                )
        pairs.append(
            PairReliability(
                a,
                b,
                not reasons,
                "; ".join(reasons) + "."
                if reasons
                else "Coverage and effective moves meet both directional thresholds.",
            )
        )
    if not pairs:
        flow = Verdict(
            "insufficient", "At least two employers are needed to evaluate worker flow."
        )
    elif any(not p.reliable for p in pairs):
        # Deduplicate repeated employer failures across pairs, preserving detail.
        reasons = list(
            dict.fromkeys(
                reason
                for p in pairs
                if not p.reliable
                for reason in p.reason.rstrip(".").split("; ")
            )
        )
        flow = Verdict(
            "insufficient", "Flow is insufficient: " + "; ".join(reasons) + "."
        )
    else:
        flow = Verdict(
            "sufficient",
            "Every employer/year has at least 20% estimated coverage and "
            "every pair-direction has at least 30 effective moves.",
        )
    return SignalSufficiency(
        flow,
        _feed_verdict(clause_feeds, report),
        _feed_verdict(tie_feeds, report),
        tuple(pairs),
    )
