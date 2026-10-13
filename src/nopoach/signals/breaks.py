"""Persistent downward Poisson offset changepoints (KTD6).

Expected yearly moves are exposures. Compare two fitted residual rates with
one pooled rate; this removes employer/year hiring shocks already in PPML.
Scan at least 30 observed pre-break moves and two consecutive low post-break
periods. The post-break aggregate ratio must also stay below one half; later
yearly count noise alone does not erase the established persistence.
Intervals bracket the last pre-break and first post-break calendar year.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.special import xlogy  # type: ignore[import-untyped]


@dataclass(frozen=True)
class FlowBreak:
    year_interval: tuple[int, int]
    before_observed_rate: float
    after_observed_rate: float
    before_expected_rate: float
    after_expected_rate: float
    before_ratio: float
    after_ratio: float
    statistic: float
    split_index: int


def break_candidates(observed: np.ndarray, expected: np.ndarray) -> np.ndarray:
    """Same scan/gates for observed series and model-based placebo pairs."""
    y = np.atleast_2d(observed)
    e = expected
    scores = np.zeros((len(y), len(e)))
    for i in range(1, len(e) - 1):
        before, after = y[:, :i].sum(axis=1), y[:, i:].sum(axis=1)
        eb, ea = float(e[:i].sum()), float(e[i:].sum())
        if eb <= 0 or ea <= 0:
            continue
        rb, ra = before / eb, after / ea
        pooled = (before + after) / (eb + ea)
        valid = (
            (before >= 30)
            & (rb > 0.5)
            & (ra < rb * 0.5)
            & (ra < 0.5)
            & np.all(y[:, i : i + 2] < e[i : i + 2] * 0.5, axis=1)
        )
        llr = 2 * (xlogy(before, rb) + xlogy(after, ra) - xlogy(before + after, pooled))
        scores[:, i] = np.where(valid, np.maximum(0, llr), 0)
    return scores


def detect_break(
    years: Sequence[int], observed: Sequence[float], expected: Sequence[float]
) -> FlowBreak | None:
    y, e = np.asarray(observed, dtype=float), np.asarray(expected, dtype=float)
    scores = break_candidates(y, e)[0]
    if not len(scores) or float(scores.max()) <= 0:
        return None
    i = int(scores.argmax())
    return FlowBreak(
        (years[i - 1], years[i]),
        float(y[:i].mean()),
        float(y[i:].mean()),
        float(e[:i].mean()),
        float(e[i:].mean()),
        float(y[:i].sum() / e[:i].sum()),
        float(y[i:].sum() / e[i:].sum()),
        float(scores[i]),
        i,
    )
