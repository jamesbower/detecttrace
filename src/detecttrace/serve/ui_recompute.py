"""The worker pool of `detecttrace ui`, whose settings change when the user saves a configuration.

`serve` reads its settings once, at startup; the app reads them again on every save, so each
run takes the settings confirmed last. A run already going finishes with the ones it started
with.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future
from pathlib import Path

from detecttrace.serve.recompute import RecomputeOutcome, RecomputeSettings, compute_snapshot
from detecttrace.serve.server import WorkerPool


class UiRecompute:
    """Submits recomputes of `database` with the latest settings, replacing a dead worker."""

    def __init__(self, database: Path, create_executor: Callable[[], Executor]) -> None:
        self._database = database
        self._lock = threading.Lock()
        self._settings: RecomputeSettings | None = None
        self._pool = WorkerPool(create_executor, self._start)

    def replace_settings(self, settings: RecomputeSettings) -> None:
        """Use `settings` from the next run on."""
        with self._lock:
            self._settings = settings

    def submit(self) -> Future[RecomputeOutcome]:
        return self._pool.submit()

    def shutdown(self) -> None:
        self._pool.shutdown()

    def _start(self, executor: Executor) -> Future[RecomputeOutcome]:
        with self._lock:
            settings = self._settings
        if settings is None:
            # The app makes no coordinator before the first save, so nothing submits sooner.
            raise RuntimeError("no configuration has been saved yet")
        return executor.submit(compute_snapshot, self._database, settings, time.time_ns())
