import math
import random

import pytest

from detecttrace.stats import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    Interval,
    cohens_kappa,
    kappa_analytic_interval,
    kappa_standard_error,
    mean_t_interval,
    percentile_bootstrap,
    t_quantile_975,
    wilson_interval,
)

# Golden values from an independent implementation, statsmodels 0.15.0:
#   uvx --with statsmodels python -c "
#   from statsmodels.stats.inter_rater import cohens_kappa
#   for t in (...): r = cohens_kappa(t); print(t, r.kappa, r.std_kappa, r.kappa_low, r.kappa_upp)"
# std_kappa is the large-sample standard error of kappa-hat, not the one under kappa = 0.
# (matrix, std_kappa, kappa_low, kappa_upp)
STATSMODELS_GOLDEN = [
    (
        [[20, 5, 0], [3, 50, 2], [1, 4, 15]],
        0.061592630247581805,
        0.6213270946439516,
        0.862765768640657,
    ),
    (
        [[40, 2, 1], [5, 30, 4], [0, 3, 15]],
        0.055769619888115234,
        0.6541004936508968,
        0.872713386475286,
    ),
    (
        [[90, 5, 0], [4, 1, 0], [0, 0, 0]],
        0.16210094084989635,
        -0.18309662131046883,
        0.4523273905412402,
    ),
]

ONE_VERDICT_ONLY = [[10, 0, 0], [0, 0, 0], [0, 0, 0]]


def test_wilson_low_bound_matches_known_value() -> None:
    interval = wilson_interval(8, 10)

    assert interval is not None and interval.low == pytest.approx(0.4902, abs=1e-4)


def test_wilson_high_bound_matches_known_value() -> None:
    interval = wilson_interval(8, 10)

    assert interval is not None and interval.high == pytest.approx(0.9433, abs=1e-4)


def test_wilson_all_successes_reaches_one_without_exceeding_it() -> None:
    interval = wilson_interval(10, 10)

    assert (
        interval is not None
        and interval.high == pytest.approx(1.0, abs=1e-12)
        and interval.high <= 1.0
    )


def test_wilson_no_successes_reaches_zero_without_going_below_it() -> None:
    interval = wilson_interval(0, 10)

    assert (
        interval is not None
        and interval.low == pytest.approx(0.0, abs=1e-12)
        and interval.low >= 0.0
    )


def test_wilson_with_no_cases_is_none() -> None:
    assert wilson_interval(0, 0) is None


def test_wilson_with_more_successes_than_cases_raises() -> None:
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_kappa_matches_hand_computed_value() -> None:
    assert cohens_kappa([[20, 5, 0], [3, 50, 2], [1, 4, 15]]) == pytest.approx(4315 / 5815)


def test_kappa_is_none_when_chance_agreement_is_certain() -> None:
    assert cohens_kappa(ONE_VERDICT_ONLY) is None


def test_kappa_with_no_cases_is_none() -> None:
    assert cohens_kappa([[0, 0, 0], [0, 0, 0], [0, 0, 0]]) is None


def test_kappa_of_perfect_agreement_is_one() -> None:
    assert cohens_kappa([[5, 0, 0], [0, 5, 0], [0, 0, 5]]) == 1.0


def test_kappa_with_negative_count_raises() -> None:
    with pytest.raises(ValueError):
        cohens_kappa([[5, -1], [0, 5]])


def test_kappa_with_non_square_matrix_raises() -> None:
    with pytest.raises(ValueError):
        cohens_kappa([[5, 0, 0], [0, 5, 0]])


@pytest.mark.parametrize(("matrix", "std_kappa", "low", "high"), STATSMODELS_GOLDEN)
def test_kappa_standard_error_matches_statsmodels(
    matrix: list[list[int]], std_kappa: float, low: float, high: float
) -> None:
    kappa = cohens_kappa(matrix)

    assert kappa is not None and kappa_standard_error(matrix, kappa) == pytest.approx(
        std_kappa, rel=1e-9
    )


@pytest.mark.parametrize(("matrix", "std_kappa", "low", "high"), STATSMODELS_GOLDEN)
def test_kappa_interval_low_matches_statsmodels(
    matrix: list[list[int]], std_kappa: float, low: float, high: float
) -> None:
    interval = kappa_analytic_interval(matrix)

    assert interval is not None and interval.low == pytest.approx(low, rel=1e-9)


@pytest.mark.parametrize(("matrix", "std_kappa", "low", "high"), STATSMODELS_GOLDEN)
def test_kappa_interval_high_matches_statsmodels(
    matrix: list[list[int]], std_kappa: float, low: float, high: float
) -> None:
    interval = kappa_analytic_interval(matrix)

    assert interval is not None and interval.high == pytest.approx(high, rel=1e-9)


def test_kappa_interval_is_clipped_at_one() -> None:
    # kappa-hat = 0.90498 and SE = 0.09231, so the unclipped upper bound is 1.08591.
    interval = kappa_analytic_interval([[10, 0], [1, 10]])

    assert interval is not None and interval.high == 1.0


def test_kappa_interval_is_none_when_chance_agreement_is_certain() -> None:
    assert kappa_analytic_interval(ONE_VERDICT_ONLY) is None


@pytest.mark.parametrize(
    ("df", "expected"),
    [(29, 2.04523), (30, 2.04227), (60, 2.00030), (120, 1.97993), (1000, 1.96234)],
)
def test_t_quantile_matches_table(df: int, expected: float) -> None:
    assert t_quantile_975(df) == pytest.approx(expected, abs=1e-4)


def test_mean_t_interval_with_zero_variance_is_a_point() -> None:
    assert mean_t_interval([0.5] * 30) == Interval(0.5, 0.5)


def test_mean_t_interval_low_matches_known_data() -> None:
    interval = mean_t_interval([0.0, 1.0] * 15)

    assert interval.low == pytest.approx(0.5 - 2.04523 * 0.50855 / math.sqrt(30), abs=1e-4)


def test_bootstrap_is_deterministic() -> None:
    def statistic(sample: list[int]) -> float:
        return sum(sample) / len(sample)

    assert percentile_bootstrap(20, statistic) == percentile_bootstrap(20, statistic)


def _none_when_zero_drawn(sample: list[int]) -> float | None:
    return None if 0 in sample else 1.0


def _count_resamples_containing_zero(n: int) -> int:
    rng = random.Random(BOOTSTRAP_SEED)
    return sum(0 in rng.choices(range(n), k=n) for _ in range(BOOTSTRAP_RESAMPLES))


def test_bootstrap_reports_dropped_resamples() -> None:
    expected = _count_resamples_containing_zero(5)

    assert percentile_bootstrap(5, _none_when_zero_drawn).dropped == expected


def test_bootstrap_gives_no_interval_when_too_few_resamples_are_valid() -> None:
    assert percentile_bootstrap(10, lambda sample: None).interval is None


def test_bootstrap_of_constant_statistic_is_a_point() -> None:
    assert percentile_bootstrap(10, lambda sample: 0.7).interval == Interval(0.7, 0.7)
