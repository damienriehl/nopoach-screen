"""Entirely invented markets and their planted ground truth (KTD14).

Coordinates are planar kilometres, pay is a relative index, and occupation
shares are in the same order as Scenario.occupations. Sizes describe initial
headcounts; the closed worker cohort can redistribute over subsequent years.
These fixtures are not calibrated estimates of any real employer or market.
"""

from dataclasses import dataclass, replace
from itertools import combinations
from math import isclose, isfinite


@dataclass(frozen=True)
class Occupation:
    code: str
    title: str
    yearly_move_rate: float


@dataclass(frozen=True)
class Employer:
    key: str
    name: str
    size: int
    location: tuple[float, float]
    occupation_shares: tuple[float, ...]
    pay: float = 1.0


@dataclass(frozen=True)
class PairEffect:
    """Multiply source -> destination hazards starting in start_year.

    Mutual effects also multiply the reverse direction. A factor of .05 means
    95% fewer expected moves, conditional on the current workforce exposure.
    """

    source: str
    destination: str
    start_year: int
    factor: float
    mutual: bool = True


@dataclass(frozen=True)
class HiringFreeze:
    """Reduce incoming hires from ALL partners; departures remain possible."""

    employer: str
    start_year: int
    factor: float


@dataclass(frozen=True)
class CoverageMask:
    employer: str
    fraction: float
    occupation_code: str | None = None


@dataclass(frozen=True)
class FiledClause:
    employer_a: str
    employer_b: str
    start_year: int
    end_year: int
    text: str
    provenance_method: str = "synthetic"


@dataclass(frozen=True)
class Scenario:
    name: str
    start_year: int
    end_year: int
    employers: tuple[Employer, ...]
    occupations: tuple[Occupation, ...]
    distance_scale_km: float = 50.0
    pair_effects: tuple[PairEffect, ...] = ()
    hiring_freezes: tuple[HiringFreeze, ...] = ()
    coverage: tuple[CoverageMask, ...] = ()
    clauses: tuple[FiledClause, ...] = ()

    def __post_init__(self) -> None:
        # These are public fixture inputs: invalid probabilities must not become
        # silently mislabelled stored data or misleading statistical ground truth.
        keys = {employer.key for employer in self.employers}
        codes = {occupation.code for occupation in self.occupations}
        if not keys or len(keys) != len(self.employers) or not codes:
            raise ValueError("Employers and occupations need distinct, nonempty keys")
        if len(codes) != len(self.occupations):
            raise ValueError("Occupation codes must be distinct")
        if not 2 <= self.start_year <= self.end_year <= 9998:
            raise ValueError("Years must be ordered and support adjacent spell dates")
        if not isfinite(self.distance_scale_km) or self.distance_scale_km <= 0:
            raise ValueError("distance_scale_km must be positive and finite")
        probabilities = [o.yearly_move_rate for o in self.occupations]
        for employer in self.employers:
            if employer.size <= 0 or not isfinite(employer.pay) or employer.pay <= 0:
                raise ValueError("Employer size and pay must be positive")
            if not all(isfinite(x) for x in employer.location):
                raise ValueError("Coordinates must be finite")
            shares = employer.occupation_shares
            if len(shares) != len(self.occupations) or not isclose(sum(shares), 1):
                raise ValueError(
                    "Occupation shares must match occupations and sum to 1"
                )
            probabilities.extend(shares)
        for effect in self.pair_effects:
            if effect.source not in keys or effect.destination not in keys:
                raise ValueError("Pair effects must name scenario employers")
            if effect.source == effect.destination:
                raise ValueError("Pair effects require distinct employers")
            probabilities.append(effect.factor)
        for freeze in self.hiring_freezes:
            if freeze.employer not in keys:
                raise ValueError("Hiring freezes must name scenario employers")
            probabilities.append(freeze.factor)
        for mask in self.coverage:
            if mask.employer not in keys or (
                mask.occupation_code is not None and mask.occupation_code not in codes
            ):
                raise ValueError(
                    "Coverage masks must name scenario employers/occupations"
                )
            probabilities.append(mask.fraction)
        if any(not isfinite(p) or not 0 <= p <= 1 for p in probabilities):
            raise ValueError(
                "Rates, shares, coverage and suppression factors must be 0..1"
            )
        for clause in self.clauses:
            if clause.employer_a not in keys or clause.employer_b not in keys:
                raise ValueError("Clauses must name scenario employers")


SCENARIO_NAMES = (
    "AE1",
    "AE2",
    "AE3",
    "AE4",
    "hiring_freeze",
    "dense_web",
    "geographic_separation",
    "pay_gap",
)


def named_scenario(name: str) -> Scenario:
    """Return a fresh acceptance/demo fixture, without generating or importing it."""
    base = Scenario(
        name=name,
        start_year=2015,
        end_year=2024,
        employers=(
            Employer("hospital-a", "Synthetic Amber Hospital", 1000, (0, 0), (1.0,)),
            Employer("hospital-b", "Synthetic Birch Hospital", 1000, (0, 0), (1.0,)),
        ),
        occupations=(Occupation("29-1141", "Registered Nurse", 0.04),),
    )
    comparisons = tuple(
        Employer(
            f"comparison-{i}",
            f"Synthetic Comparison Hospital {i}",
            1000,
            (0, 0),
            (1.0,),
        )
        for i in range(1, 5)
    )
    if name in {"AE1", "AE2"}:
        # Destination shares divide hazards across all partners. Preserve the
        # original ~40 annual moves per pair with five rather than one partner.
        base = replace(
            base,
            employers=base.employers + comparisons,
            occupations=(Occupation("29-1141", "Registered Nurse", 0.20),),
        )
    if name == "AE1":
        return replace(
            base, pair_effects=(PairEffect("hospital-a", "hospital-b", 2020, 0.05),)
        )
    if name == "AE2":
        return replace(
            base,
            pair_effects=(
                PairEffect("hospital-a", "hospital-b", 2020, 0.05, mutual=False),
            ),
        )
    if name == "AE3":
        return replace(
            base,
            employers=(
                replace(
                    base.employers[0], key="processor-a", name="Synthetic Amber Poultry"
                ),
                replace(
                    base.employers[1], key="processor-b", name="Synthetic Birch Poultry"
                ),
            ),
            occupations=(Occupation("51-3022", "Meat and Poultry Trimmer", 0.04),),
            coverage=(CoverageMask("processor-a", 0.08),),
        )
    if name == "AE4":
        return replace(
            base,
            clauses=(
                FiledClause(
                    "hospital-a",
                    "hospital-b",
                    2020,
                    2021,
                    "SYNTHETIC OUTSOURCING CONTRACT: Synthetic Amber Hospital and "
                    "Synthetic Birch Hospital agree that during 2020 and 2021, "
                    "neither party shall "
                    "solicit or hire the other party's employees. This invented clause "
                    "is test data; worker flow is deliberately unaffected.",
                ),
            ),
        )
    if name == "hiring_freeze":
        return replace(
            base,
            employers=base.employers
            + (
                Employer(
                    "hospital-c", "Synthetic Cedar Hospital", 1000, (0, 0), (1.0,)
                ),
            )
            + comparisons,
            occupations=(Occupation("29-1141", "Registered Nurse", 0.24),),
            hiring_freezes=(HiringFreeze("hospital-a", 2018, 0.05),),
        )
    if name == "dense_web":
        employers = base.employers + tuple(
            Employer(
                f"hospital-{key}", f"Synthetic {label} Hospital", 1000, (0, 0), (1.0,)
            )
            for key, label in (
                ("c", "Cedar"),
                ("d", "Dahlia"),
                ("e", "Elm"),
                ("f", "Fir"),
            )
        )
        return replace(
            base,
            employers=employers + comparisons,
            occupations=(Occupation("29-1141", "Registered Nurse", 0.36),),
            pair_effects=tuple(
                PairEffect(a.key, b.key, 2020, 0.05)
                for a, b in combinations(employers, 2)
            ),
        )
    if name == "geographic_separation":
        return replace(
            base,
            employers=(
                base.employers[0],
                replace(base.employers[1], location=(250, 0)),
            ),
        )
    if name == "pay_gap":
        return replace(
            base, employers=(base.employers[0], replace(base.employers[1], pay=0.2))
        )
    raise ValueError(f"Unknown scenario {name!r}; choose from {SCENARIO_NAMES}")
