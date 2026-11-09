"""Annual public mobility context; never employer-pair calibration or a score."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import permutations
from math import fsum, isfinite
from typing import Literal

from nopoach.collect.lehd import J2JData

SCOPE_DIFFERENCES = (
    "Public J2J covers occupations outside the selected occupation scope.",
    "Public regional totals include employers outside the confirmed competitor set.",
    "Census main job inference differs from the supplied employment spell histories.",
    "Quarterly continuous/brief-nonemployment measures use different "
    "gap definitions from the screen.",
    "Declared public ownership and covered worker populations may differ "
    "from the supplied records.",
    "Annual shares describe temporal patterns only; agreement does not "
    "validate expected pair flows.",
    "Source declarations and release dates do not establish when underlying "
    "records were historically available.",
)


@dataclass(frozen=True)
class ModeledFlow:
    source_id: str
    destination_id: str
    year: int
    expected: float


@dataclass(frozen=True)
class AnnualContext:
    year: int
    public_count: float
    modeled_count: float
    public_share: float
    modeled_share: float
    difference_percentage_points: float


@dataclass(frozen=True)
class ExcludedYear:
    year: int
    reason: str


@dataclass(frozen=True)
class J2JContext:
    status: Literal["context_only", "unavailable"]
    reason: str
    annual: tuple[AnnualContext, ...]
    excluded_years: tuple[ExcludedYear, ...]
    scope_differences: tuple[str, ...]
    source_reference: str
    release: str
    release_date: str
    public_total: float | None = None
    modeled_total: float | None = None
    excluded_rows: int = 0


def _total(values: Iterable[float]) -> float:
    try:
        return fsum(values)
    except OverflowError:
        return float("inf")


def temporal_context(
    data: J2JData,
    *,
    geography_kind: str,
    geography_code: str,
    start_year: int,
    end_year: int,
    employer_ids: Sequence[str],
    modeled: Sequence[ModeledFlow],
    workplace_geography_known: bool,
    as_of: date | None = None,
    source_reference: str = "",
) -> J2JContext:
    """Compare shares over exactly the same complete common years.

    Supply all modeled ordered pairs, including unflagged and zero-expectation
    cells. Missing directions cannot be treated as zero or ignored. Public
    missingness removes an entire year from both series.
    """
    meta = data.metadata
    excluded: list[ExcludedYear] = []
    differences = (
        f"Public destination ownership: {meta.ownercode}; "
        f"origin ownership: {meta.ownercode_orig}. "
        f"Same-CBSA total: {meta.geography}; schema {meta.schema_version}.",
        *SCOPE_DIFFERENCES,
    )

    def unavailable(reason: str) -> J2JContext:
        return J2JContext(
            "unavailable",
            reason,
            (),
            tuple(excluded),
            differences,
            source_reference,
            meta.release,
            meta.release_date.isoformat(),
            excluded_rows=data.excluded_rows,
        )

    if geography_kind != "metro" or geography_code != meta.geography:
        return unavailable(
            "A matching single-metro screen is required for public context."
        )
    if not workplace_geography_known:
        return unavailable(
            "The supplied records' workplace geography meaning is unknown."
        )
    if as_of is not None and meta.release_date > as_of:
        return unavailable("The public release date is after the historical cutoff.")
    if not 1 <= start_year <= end_year <= 9999:
        return unavailable("The assessment year window is invalid.")
    pairs = set(permutations(sorted(set(employer_ids)), 2))
    if not pairs:
        return unavailable(
            "At least two employers with complete modeled pairs are required."
        )
    public: dict[int, float] = {}
    records = {(row.year, row.quarter): row for row in data.records}
    for year in range(start_year, end_year + 1):
        reasons = []
        values = []
        if as_of is not None and date(year, 12, 31) > as_of:
            reasons.append("partial year or year after cutoff")
        for quarter in range(1, 5):
            key = year, quarter
            if not meta.availability_start <= key <= meta.availability_end:
                reasons.append(f"Q{quarter}: outside declared pair availability")
                continue
            record = records.get(key)
            if record is None:
                reasons.append(f"Q{quarter}: absent public quarter")
                continue
            for name, indicator in (("EE", record.ee), ("AQHire", record.aqhire)):
                if indicator.value is None:
                    reasons.append(
                        f"Q{quarter} {name}: {indicator.reason} "
                        f"(status {indicator.status})"
                    )
                else:
                    values.append(indicator.value)
        if reasons:
            excluded.append(ExcludedYear(year, "; ".join(reasons)))
        else:
            public[year] = _total(values)
    if len(public) < 2:
        return unavailable("At least two complete common years are required.")
    annual_modeled: dict[int, dict[tuple[str, str], float]] = {
        year: {} for year in public
    }
    for row in modeled:
        if row.year not in public:
            continue
        pair = row.source_id, row.destination_id
        if (
            pair not in pairs
            or pair in annual_modeled[row.year]
            or not isfinite(row.expected)
            or row.expected < 0
        ):
            return unavailable(
                "Invalid, duplicate or out-of-scope modeled pair support."
            )
        annual_modeled[row.year][pair] = row.expected
    if any(set(values) != pairs for values in annual_modeled.values()):
        return unavailable(
            "Incomplete modeled pair support; missing directions are not zero."
        )
    model_totals = {
        year: _total(values.values()) for year, values in annual_modeled.items()
    }
    public_total, modeled_total = _total(public.values()), _total(model_totals.values())
    if (
        not isfinite(public_total)
        or not isfinite(modeled_total)
        or min(public_total, modeled_total) <= 0
    ):
        return unavailable("Both common-year series require finite positive totals.")
    annual = tuple(
        AnnualContext(
            year,
            public[year],
            model_totals[year],
            public[year] / public_total,
            model_totals[year] / modeled_total,
            100 * (public[year] / public_total - model_totals[year] / modeled_total),
        )
        for year in sorted(public)
    )
    return J2JContext(
        "context_only",
        "Context only: each annual share uses its own series' common-year total.",
        annual,
        tuple(excluded),
        differences,
        source_reference,
        meta.release,
        meta.release_date.isoformat(),
        public_total,
        modeled_total,
        data.excluded_rows,
    )
