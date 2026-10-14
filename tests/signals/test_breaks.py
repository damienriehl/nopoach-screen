from nopoach.signals.breaks import detect_break


def test_poisson_residual_break_has_rates_interval_and_persistence():
    result = detect_break(tuple(range(2015, 2025)), [40] * 5 + [2] * 5, [40] * 10)
    assert result is not None
    assert result.year_interval == (2019, 2020)
    assert result.before_observed_rate == 40
    assert result.after_observed_rate == 2
    assert result.before_ratio == 1
    assert result.after_ratio == 0.05


def test_freeze_residuals_transient_drop_and_tiny_counts_have_no_break():
    years = tuple(range(2015, 2025))
    assert detect_break(years, [40] * 3 + [2] * 7, [40] * 3 + [2] * 7) is None
    assert detect_break(years, [40] * 9 + [2], [40] * 10) is None
    assert detect_break(years, [1] * 5 + [0] * 5, [1] * 10) is None


def test_two_post_periods_suffice_and_later_noise_does_not_erase_persistence():
    result = detect_break((2019, 2020, 2021), [40, 2, 2], [40, 40, 40])
    assert result is not None
    assert result.year_interval == (2019, 2020)
    noisy = detect_break(
        tuple(range(2015, 2025)), [40] * 5 + [2, 2, 25, 2, 2], [40] * 10
    )
    assert noisy is not None
    assert noisy.year_interval == (2019, 2020)
