"""KTD7 Gamma-Poisson empirical Bayes and Monte Carlo placebo tails.

The prior is centered on the market rate (one after PPML). Its strength is
estimated from excess pair-rate variance, bounded at 30 expected moves so a
large affected network cannot make the prior infinitely confident. Intervals
condition on fitted expected flow; they do not include gravity-fit uncertainty.
Placebos are simulated null pair trajectories, not real historical controls.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import gamma  # type: ignore[import-untyped]


@dataclass(frozen=True)
class RateRatio:
    ratio: float
    lower: float
    upper: float
    observed: float
    expected: float
    prior_strength: float


def estimate_prior(observed: Sequence[float], expected: Sequence[float]) -> float:
    y, e = np.asarray(observed, dtype=float), np.asarray(expected, dtype=float)
    keep = e > 0
    variance = float(np.sum((y[keep] - e[keep]) ** 2 - y[keep]))
    exposure = float(np.sum(e[keep] ** 2))
    return min(30.0, max(1.0, exposure / variance)) if variance > 0 else 30.0


def shrink_ratio(observed: float, expected: float, prior: float) -> RateRatio:
    shape, rate = observed + prior, expected + prior
    lower, upper = gamma.ppf([0.025, 0.975], a=shape, scale=1 / rate)
    return RateRatio(
        shape / rate, float(lower), float(upper), observed, expected, prior
    )


def placebo_pvalue(statistic: float, placebo_statistics: np.ndarray) -> float:
    """Add-one randomization tail: finite simulations never imply p=0."""
    return float(
        (1 + np.count_nonzero(placebo_statistics >= statistic))
        / (len(placebo_statistics) + 1)
    )


def benjamini_hochberg(pvalues: Sequence[float]) -> list[float]:
    count = len(pvalues)
    order = sorted(range(count), key=lambda i: pvalues[i])
    adjusted = [1.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        index = order[rank - 1]
        running = min(running, pvalues[index] * count / rank)
        adjusted[index] = running
    return adjusted
