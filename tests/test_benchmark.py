import time

import pytest
from scale import SCALE_CHECKLISTS, make_scale_cases

from detecttrace.metrics import compute_metrics

GATE_SECONDS = 20


@pytest.mark.benchmark
def test_metrics_stage_meets_the_20_second_gate():
    cases = make_scale_cases()

    started = time.perf_counter()
    compute_metrics(cases, SCALE_CHECKLISTS)
    elapsed = time.perf_counter() - started

    print(f"compute_metrics on {len(cases)} cases: {elapsed:.2f} s")
    assert elapsed <= GATE_SECONDS
