import numpy as np

from nopoach.signals.shrink import (
    benjamini_hochberg,
    estimate_prior,
    placebo_pvalue,
    shrink_ratio,
)


def test_small_zero_count_shrinks_toward_norm_with_interval():
    prior = estimate_prior([40, 39, 41, 38, 42], [40] * 5)
    result = shrink_ratio(0, 0.1, prior)
    assert result.ratio > 0.95
    assert result.lower < 1 < result.upper
    assert shrink_ratio(2, 200, prior).ratio < 0.5


def test_bh_accounts_for_all_comparisons():
    assert benjamini_hochberg([0.001, 0.02, 0.2, 1]) == [
        0.004,
        0.04,
        0.26666666666666666,
        1,
    ]


def test_placebo_tail_counts_extreme_pairs_and_is_never_zero():
    placebo = np.array([0, 1, 2, 3])
    assert placebo_pvalue(4, placebo) == 0.2
    assert placebo_pvalue(2, placebo) == 0.6
