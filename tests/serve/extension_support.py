"""Snapshot extensions for the recompute tests, at module level so a worker can unpickle them."""

from dataclasses import dataclass

from detecttrace.dashboard import DashboardPage
from detecttrace.pipeline import RunResult
from detecttrace.serve.recompute import Extension


@dataclass(frozen=True, slots=True)
class CountingExtension:
    """Adds the scored case count to the results and the view."""

    page: DashboardPage | None = None

    def extend(self, run: RunResult) -> Extension:
        return Extension(results={"cases": run.case_count}, view={"cases": run.case_count})


@dataclass(frozen=True, slots=True)
class FailingExtension:
    page: DashboardPage | None = None

    def extend(self, run: RunResult) -> Extension:
        raise ValueError("extension broke")


@dataclass(frozen=True, slots=True)
class NanExtension:
    page: DashboardPage | None = None

    def extend(self, run: RunResult) -> Extension:
        return Extension(results={"rate": float("nan")}, view={})
