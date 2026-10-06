"""What `detecttrace serve` tells a page it renders: the snapshot's identity and its counts."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServedPage:
    """What a page from `detecttrace serve` knows about itself, to tell when newer results exist."""

    generation: int  # the stored input's generation the page was computed from
    updated_at: str  # when it was computed, ISO 8601 in UTC, as the status route reports it
    held_back_cases: int  # cases still inside the settle window, so not counted on the page


@dataclass(frozen=True, slots=True)
class WaitingCounts:
    span_count: int
    case_count: int  # cases whose trace has settled
    held_back_count: int  # cases still inside the settle window
    verdict_count: int
