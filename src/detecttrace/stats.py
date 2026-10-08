"""Statistics for verdict agreement, Cohen's kappa, and evidence completeness. Standard library only."""

import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# The 97.5% standard normal quantile, correctly rounded. `NormalDist().inv_cdf(0.975)` is not
# the same float on every CPython build: the C version on arm64 macOS gives one a unit in the
# last place below what Linux, Windows and the pure-Python version give, and neither is
# correctly rounded. That moved interval bounds by a bit between platforms.
Z_95 = 1.9599639845400543
BOOTSTRAP_RESAMPLES = 1_000
# Fewer valid resamples than this and the interval is not shown.
BOOTSTRAP_MIN_VALID = 900
# A fixed seed and a fresh generator per interval keep numbers reproducible
# and independent of the order intervals are computed in.
BOOTSTRAP_SEED = 20260928

ConfusionMatrix = Sequence[Sequence[int]]


@dataclass(frozen=True, slots=True)
class Interval:
    low: float
    high: float


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    interval: Interval | None
    dropped: int  # resamples whose statistic was undefined


def wilson_interval(successes: int, n: int) -> Interval | None:
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be between 0 and n, got {successes} of {n}")
    if n == 0:
        return None
    p = successes / n
    z2 = Z_95 * Z_95
    denominator = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    half = Z_95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    # The bound at an observed 0 or 1 is exactly 0 or 1; computed, it can land a rounding
    # step short on some platforms, and the page would then read ">99%" instead of "100%".
    low = 0.0 if successes == 0 else max(0.0, center - half)
    high = 1.0 if successes == n else min(1.0, center + half)
    return Interval(low, high)


def cohens_kappa(matrix: ConfusionMatrix) -> float | None:
    """Kappa, or None when it is undefined (no cases, or both sides always give one verdict)."""
    n, rows, columns = _margins(matrix)
    if n == 0:
        return None
    # Integer arithmetic so "p_e = 1" is detected exactly: expected / n² is p_e.
    expected = sum(row * column for row, column in zip(rows, columns, strict=True))
    if expected == n * n:
        return None
    observed = sum(matrix[i][i] for i in range(len(matrix)))
    return (observed * n - expected) / (n * n - expected)


def _kappa_standard_error(matrix: ConfusionMatrix, kappa: float) -> float:
    """Large-sample standard error of kappa-hat (Fleiss, Cohen & Everitt 1969), not the one under kappa = 0."""
    n, rows, columns = _margins(matrix)
    size = len(matrix)
    row_p = [row / n for row in rows]  # analyst marginals
    column_p = [column / n for column in columns]  # agent marginals
    p_e = sum(r * c for r, c in zip(row_p, column_p, strict=True))
    a = sum(
        matrix[i][i] / n * _square(1 - (row_p[i] + column_p[i]) * (1 - kappa)) for i in range(size)
    )
    b = _square(1 - kappa) * sum(
        matrix[i][j] / n * _square(column_p[i] + row_p[j])
        for i in range(size)
        for j in range(size)
        if i != j
    )
    c = _square(kappa - p_e * (1 - kappa))
    variance = (a + b - c) / (n * _square(1 - p_e))
    return math.sqrt(max(variance, 0.0))


def _square(x: float) -> float:
    # A product, not `x ** 2`: a float power calls the platform's pow(), which isn't correctly
    # rounded everywhere, while a product is, so results are the same float on every platform.
    return x * x


def kappa_analytic_interval(matrix: ConfusionMatrix) -> Interval | None:
    kappa = cohens_kappa(matrix)
    if kappa is None:
        return None
    half = Z_95 * _kappa_standard_error(matrix, kappa)
    return Interval(max(-1.0, kappa - half), min(1.0, kappa + half))


def t_quantile_975(df: int) -> float:
    """97.5% quantile of Student's t by Cornish-Fisher expansion; accurate to 1e-4 for df >= 29."""
    z = Z_95
    # Products, not `**`: a float power calls the platform's pow(), which isn't correctly
    # rounded everywhere, while + - * / are, so the quantile is the same float on every platform.
    # `df**n` below is an exact integer power.
    z2 = z * z
    g1 = z * (z2 + 1) / 4
    g2 = z * ((5 * z2 + 16) * z2 + 3) / 96
    g3 = z * (((3 * z2 + 19) * z2 + 17) * z2 - 15) / 384
    g4 = z * ((((79 * z2 + 776) * z2 + 1482) * z2 - 1920) * z2 - 945) / 92160
    return z + g1 / df + g2 / df**2 + g3 / df**3 + g4 / df**4


def mean_t_interval(values: Sequence[float]) -> Interval:
    """95% t-interval for the mean.

    Defined from two values, but the t quantile is only accurate to 1e-5 from df >= 10;
    callers use it from n >= 30.
    """
    n = len(values)
    if n < 2:
        raise ValueError("a t-interval needs at least two values")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("a t-interval needs finite values, got NaN or infinity")
    mean = statistics.fmean(values)
    half = t_quantile_975(n - 1) * statistics.stdev(values) / math.sqrt(n)
    return Interval(mean - half, mean + half)


def percentile_bootstrap(n: int, statistic: Callable[[list[int]], float | None]) -> BootstrapResult:
    """Resample indices 0..n-1; `statistic` returns None or NaN when undefined for a resample."""
    if n < 1:
        raise ValueError("bootstrap needs at least one case")
    rng = random.Random(BOOTSTRAP_SEED)
    indices = range(n)
    estimates = [
        value
        for value in (statistic(rng.choices(indices, k=n)) for _ in range(BOOTSTRAP_RESAMPLES))
        if value is not None and not math.isnan(value)
    ]
    dropped = BOOTSTRAP_RESAMPLES - len(estimates)
    if len(estimates) < BOOTSTRAP_MIN_VALID:
        return BootstrapResult(None, dropped)
    cuts = statistics.quantiles(estimates, n=40, method="inclusive")
    return BootstrapResult(Interval(cuts[0], cuts[-1]), dropped)


def _margins(matrix: ConfusionMatrix) -> tuple[int, list[int], list[int]]:
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        raise ValueError("confusion matrix must be square")
    if any(count < 0 for row in matrix for count in row):
        raise ValueError("confusion matrix counts must not be negative")
    rows = [sum(row) for row in matrix]
    columns = [sum(matrix[i][j] for i in range(size)) for j in range(size)]
    return sum(rows), rows, columns
