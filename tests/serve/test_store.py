import gzip
import math
import random
import sqlite3
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from detecttrace import otlp
from detecttrace.jsontext import parse_json_text
from detecttrace.model import Issue, IssueKind, Span, VerdictRow
from detecttrace.runconfig import ConfigFileError, TraceFormat, load_run_config
from detecttrace.serve.store import (
    SCHEMA_VERSION,
    AddSpansResult,
    PutVerdictsResult,
    Snapshot,
    Store,
    StoreCounts,
    StoreIntegrityError,
    StoreVersionError,
    _is_corruption,  # pyright: ignore[reportPrivateUsage]
)
from detecttrace.traces import load_spans

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures"
COLLECTOR_BODIES = sorted((FIXTURE_ROOT / "collector_real").glob("*.json.gz"))


def _find_trace_inputs() -> list[tuple[Path, TraceFormat]]:
    inputs: list[tuple[Path, TraceFormat]] = []
    for config_path in sorted(FIXTURE_ROOT.rglob("detecttrace.yaml")):
        try:
            config = load_run_config(config_path)
        except ConfigFileError:
            continue  # a fixture about a bad configuration has no traces to read
        inputs.append((config.traces.path, config.traces.format))
    return inputs


TRACE_INPUTS = _find_trace_inputs()
TRACE_IDS = [str(path.relative_to(FIXTURE_ROOT)) for path, _ in TRACE_INPUTS]

ATTRIBUTE_VALUES = [
    pytest.param(2**63 - 1, id="int64-max"),
    pytest.param(-(2**63), id="int64-min"),
    pytest.param(2**64 - 1, id="uint64-max"),
    pytest.param(math.nan, id="nan"),
    pytest.param(math.inf, id="inf"),
    pytest.param(-math.inf, id="minus-inf"),
    pytest.param(-0.0, id="minus-zero"),
    pytest.param(0.1, id="float"),
    pytest.param([], id="empty-list"),
    pytest.param({"a": [1, {"b": None}]}, id="nested"),
    pytest.param({"b": 1, "a": 2}, id="key-order"),
    pytest.param("ü✓中", id="non-ascii"),
    pytest.param(True, id="true"),
    pytest.param(1, id="one"),
    pytest.param(None, id="none"),
    pytest.param("", id="empty-string"),
    pytest.param([True, 1, 1.0, "1"], id="mixed-list"),
]

THREAD_BATCHES = [
    [
        Span("0" * 31 + "1", f"{index:016x}", None, "tool", 1, 2, False, {}, {})
        for index in range(start, start + 50)
    ]
    for start in range(0, 400, 50)
]


def make_clock(start: int = 1_000) -> Callable[[], int]:
    ticks: Iterator[int] = iter(range(start, start + 1_000_000))
    return lambda: next(ticks)


def make_span(
    span_id: str = "00000000000000a1",
    *,
    trace_id: str = "0" * 31 + "1",
    attributes: dict[str, object] | None = None,
    resource_attributes: dict[str, object] | None = None,
    parent_span_id: str | None = None,
    start_ns: int = 1_700_000_000_000_000_000,
    end_ns: int = 1_700_000_001_000_000_000,
    is_error: bool = False,
) -> Span:
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name="invoke_agent triage",
        start_ns=start_ns,
        end_ns=end_ns,
        is_error=is_error,
        attributes={"gen_ai.operation.name": "invoke_agent"} if attributes is None else attributes,
        resource_attributes={"service.name": "soc-agent"}
        if resource_attributes is None
        else resource_attributes,
    )


def make_verdict(case_id: str = "CASE-1", label: str = "TP") -> VerdictRow:
    return VerdictRow(case_id=case_id, alert_class="Impossible travel", label=label, line_number=0)


def sort_spans(spans: list[Span]) -> list[Span]:
    return sorted(spans, key=lambda span: (span.trace_id, span.span_id))


def read_collector_body(path: Path) -> list[Span]:
    document = parse_json_text(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return list(otlp.OtlpParser().parse_document(document, path.name, None, []))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "detecttrace.db"


@pytest.fixture
def store(db_path: Path) -> Iterator[Store]:
    opened = Store.open(db_path, now_ns=make_clock())
    yield opened
    opened.close()


def create_populated_file(db_path: Path) -> None:
    store = Store.open(db_path, now_ns=make_clock())
    spans = [
        make_span(f"{index:016x}", attributes={"payload": "x" * 200, "index": index})
        for index in range(400)
    ]
    store.add_spans(spans, [])
    store.close()


# Round trip


@pytest.mark.parametrize(("path", "format"), TRACE_INPUTS, ids=TRACE_IDS)
def test_fixture_spans_round_trip_exactly(store: Store, path: Path, format: TraceFormat) -> None:
    spans, _ = load_spans(path, format=format)
    store.add_spans(spans, [])

    stored = store.read_inputs().spans

    assert repr(sort_spans(stored)) == repr(sort_spans(spans))


@pytest.mark.parametrize("path", COLLECTOR_BODIES, ids=[path.name for path in COLLECTOR_BODIES])
def test_collector_body_spans_round_trip_exactly(store: Store, path: Path) -> None:
    spans = read_collector_body(path)
    store.add_spans(spans, [])

    stored = store.read_inputs().spans

    assert repr(sort_spans(stored)) == repr(sort_spans(spans))


def test_collector_bodies_hold_spans() -> None:
    assert [len(read_collector_body(path)) > 0 for path in COLLECTOR_BODIES] == [True, True]


@pytest.mark.parametrize("value", ATTRIBUTE_VALUES)
def test_attribute_value_round_trips_exactly(store: Store, value: object) -> None:
    span = make_span(attributes={"before": "x", "value": value, "after": 2})
    store.add_spans([span], [])

    stored = store.read_inputs().spans

    assert repr(stored) == repr([span])


@pytest.mark.parametrize("value", ATTRIBUTE_VALUES)
def test_resource_attribute_value_round_trips_exactly(store: Store, value: object) -> None:
    span = make_span(resource_attributes={"service.name": "agent", "value": value})
    store.add_spans([span], [])

    stored = store.read_inputs().spans

    assert repr(stored) == repr([span])


def test_true_comes_back_as_a_bool(store: Store) -> None:
    store.add_spans([make_span(attributes={"flag": True})], [])

    stored = store.read_inputs().spans[0].attributes["flag"]

    assert type(stored) is bool


@pytest.mark.parametrize(
    ("start_ns", "end_ns"),
    [
        pytest.param(0, 0, id="zero"),
        pytest.param(2**63 - 1, 2**63, id="across-signed-limit"),
        pytest.param(2**64 - 1, 2**64 - 1, id="uint64-max"),
    ],
)
def test_timestamps_round_trip_exactly(store: Store, start_ns: int, end_ns: int) -> None:
    span = make_span(start_ns=start_ns, end_ns=end_ns)
    store.add_spans([span], [])

    stored = store.read_inputs().spans

    assert stored == [span]


def test_parent_and_error_flag_round_trip(store: Store) -> None:
    span = make_span(parent_span_id="00000000000000ff", is_error=True)
    store.add_spans([span], [])

    stored = store.read_inputs().spans

    assert repr(stored) == repr([span])


def test_data_survives_close_and_reopen(db_path: Path) -> None:
    span = make_span()
    first = Store.open(db_path)
    first.add_spans([span], [])
    first.close()

    reopened = Store.open(db_path)
    stored = reopened.read_inputs().spans
    reopened.close()

    assert stored == [span]


# Resources


def test_equal_resource_attributes_share_one_row(store: Store, db_path: Path) -> None:
    store.add_spans([make_span("00000000000000a1"), make_span("00000000000000a2")], [])

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT count(*) FROM resources").fetchone()[0]

    assert count == 1


def test_resource_attribute_order_makes_a_separate_row(store: Store, db_path: Path) -> None:
    store.add_spans(
        [
            make_span("00000000000000a1", resource_attributes={"a": 1, "b": 2}),
            make_span("00000000000000a2", resource_attributes={"b": 2, "a": 1}),
        ],
        [],
    )

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT count(*) FROM resources").fetchone()[0]

    assert count == 2


# Duplicates


def test_new_spans_are_accepted(store: Store) -> None:
    result = store.add_spans([make_span("00000000000000a1"), make_span("00000000000000a2")], [])

    assert result == AddSpansResult(accepted=2, duplicates=0, conflicts=0)


def test_identical_span_counts_as_duplicate(store: Store) -> None:
    store.add_spans([make_span()], [])

    result = store.add_spans([make_span()], [])

    assert result == AddSpansResult(accepted=0, duplicates=1, conflicts=0)


def test_identical_span_is_stored_once(store: Store) -> None:
    store.add_spans([make_span()], [])
    store.add_spans([make_span()], [])

    assert store.read_counts().span_count == 1


def test_identical_span_with_nan_counts_as_duplicate(store: Store) -> None:
    store.add_spans([make_span(attributes={"score": math.nan})], [])

    result = store.add_spans([make_span(attributes={"score": math.nan})], [])

    assert result.duplicates == 1


def test_reordered_attributes_count_as_duplicate_as_the_file_loader_does(store: Store) -> None:
    store.add_spans([make_span(attributes={"a": 1, "b": 2})], [])

    result = store.add_spans([make_span(attributes={"b": 2, "a": 1})], [])

    assert result.duplicates == 1


def test_differing_span_with_same_key_counts_as_conflict(store: Store) -> None:
    store.add_spans([make_span(attributes={"a": 1})], [])

    result = store.add_spans([make_span(attributes={"a": 2})], [])

    assert result == AddSpansResult(accepted=0, duplicates=0, conflicts=1)


def test_conflicting_span_is_not_stored(store: Store) -> None:
    first = make_span(attributes={"a": 1})
    store.add_spans([first], [])
    store.add_spans([make_span(attributes={"a": 2})], [])

    assert store.read_inputs().spans == [first]


def test_conflicting_span_is_reported(store: Store) -> None:
    store.add_spans([make_span(attributes={"a": 1})], [])
    store.add_spans([make_span(attributes={"a": 2})], [])

    issues = [stored.issue for stored in store.read_inputs().issues]

    assert issues == [
        Issue(
            IssueKind.CONFLICTING_DUPLICATE_SPAN,
            "OTLP/HTTP ingest",
            f"{'0' * 31}1/00000000000000a1",
        )
    ]


def test_conflict_within_one_batch_keeps_the_first(store: Store) -> None:
    first = make_span(attributes={"a": 1})

    store.add_spans([first, make_span(attributes={"a": 2})], [])

    assert store.read_inputs().spans == [first]


def test_duplicate_within_one_batch_is_counted(store: Store) -> None:
    result = store.add_spans([make_span(), make_span()], [])

    assert result == AddSpansResult(accepted=1, duplicates=1, conflicts=0)


def test_retried_identical_batch_leaves_generation_unchanged(store: Store) -> None:
    batch = [make_span("00000000000000a1"), make_span("00000000000000a2")]
    store.add_spans(batch, [])
    before = store.generation()

    store.add_spans(batch, [])

    assert store.generation() == before


def test_retried_batch_with_a_parser_issue_leaves_generation_unchanged(store: Store) -> None:
    issue = Issue(IssueKind.INVALID_ATTRIBUTE, "OTLP/HTTP ingest", "attribute has no key")
    store.add_spans([make_span()], [issue])
    before = store.generation()

    store.add_spans([make_span()], [issue])

    assert store.generation() == before


def test_retried_batch_with_a_conflict_leaves_generation_unchanged(store: Store) -> None:
    store.add_spans([make_span(attributes={"a": 1})], [])
    store.add_spans([make_span(attributes={"a": 2})], [])
    before = store.generation()

    store.add_spans([make_span(attributes={"a": 2})], [])

    assert store.generation() == before


def test_retried_batch_still_counts_its_issue_again(store: Store) -> None:
    issue = Issue(IssueKind.INVALID_ATTRIBUTE, "OTLP/HTTP ingest", "attribute has no key")
    store.add_spans([], [issue])
    store.add_spans([], [issue])

    assert store.read_inputs().issues[0].count == 2


def test_new_issue_after_a_repeated_one_adds_one_to_generation(store: Store) -> None:
    repeated = Issue(IssueKind.INVALID_SPAN, "OTLP/HTTP ingest", "span has no trace ID")
    store.add_spans([], [repeated])
    before = store.generation()

    store.add_spans([], [repeated, Issue(IssueKind.INVALID_SPAN, "OTLP/HTTP ingest", "other")])

    assert store.generation() == before + 1


def test_repeated_add_issues_leaves_generation_unchanged(store: Store) -> None:
    issue = Issue(IssueKind.INVALID_SPAN, "OTLP/HTTP ingest", "span has no trace ID")
    store.add_issues([issue])
    before = store.generation()

    store.add_issues([issue])

    assert store.generation() == before


# Verdicts


def test_put_verdicts_counts_new_cases_as_added(store: Store) -> None:
    result = store.put_verdicts([make_verdict("CASE-1"), make_verdict("CASE-2")], "analysts")

    assert result == PutVerdictsResult(added=2, replaced=0, unchanged=0)


def test_put_verdicts_counts_a_changed_verdict_as_replaced(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP"), make_verdict("CASE-2", "TP")], "analysts")

    result = store.put_verdicts(
        [make_verdict("CASE-1", "FP"), make_verdict("CASE-2", "TP")], "analysts"
    )

    assert result == PutVerdictsResult(added=0, replaced=1, unchanged=1)


def test_put_verdicts_counts_a_second_row_for_a_case_as_replacing_the_first(
    store: Store,
) -> None:
    result = store.put_verdicts(
        [make_verdict("CASE-1", "TP"), make_verdict("CASE-1", "FP")], "analysts"
    )

    assert result == PutVerdictsResult(added=1, replaced=1, unchanged=0)


def test_new_verdict_is_read_back_without_a_line_number(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")

    assert store.read_inputs().verdict_rows == [make_verdict("CASE-1", "TP")]


def test_verdict_for_same_case_replaces_the_old_one(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    store.put_verdicts([make_verdict("CASE-1", "FP")], "ops")

    assert store.read_inputs().verdict_rows == [make_verdict("CASE-1", "FP")]


def test_replaced_verdict_moves_to_history(db_path: Path) -> None:
    store = Store.open(db_path, now_ns=make_clock(5_000))
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    store.put_verdicts([make_verdict("CASE-1", "FP")], "ops")
    store.close()

    with sqlite3.connect(db_path) as connection:
        history = connection.execute(
            "SELECT case_id, alert_class, label, received_at_ns, replaced_at_ns, token_name"
            " FROM verdict_history"
        ).fetchall()

    assert history == [("CASE-1", "Impossible travel", "TP", 5_000, 5_001, "analysts")]


def test_replacing_verdict_keeps_the_new_token_name(store: Store, db_path: Path) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    store.put_verdicts([make_verdict("CASE-1", "FP")], "ops")

    with sqlite3.connect(db_path) as connection:
        token_name = connection.execute("SELECT token_name FROM verdicts").fetchone()[0]

    assert token_name == "ops"


def test_identical_verdict_repost_adds_no_history(store: Store, db_path: Path) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    store.put_verdicts([make_verdict("CASE-1", "TP")], "ops")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT count(*) FROM verdict_history").fetchone()[0]

    assert count == 0


def test_identical_verdict_repost_leaves_generation_unchanged(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    before = store.generation()

    store.put_verdicts([make_verdict("CASE-1", "TP")], "ops")

    assert store.generation() == before


def test_identical_verdict_repost_keeps_the_first_token_name(store: Store, db_path: Path) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    store.put_verdicts([make_verdict("CASE-1", "TP")], "ops")

    with sqlite3.connect(db_path) as connection:
        token_name = connection.execute("SELECT token_name FROM verdicts").fetchone()[0]

    assert token_name == "analysts"


def test_identical_verdict_repost_is_counted_as_unchanged(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")

    result = store.put_verdicts([make_verdict("CASE-1", "TP")], "ops")

    assert result == PutVerdictsResult(added=0, replaced=0, unchanged=1)


def test_repost_with_a_new_alert_class_replaces_the_verdict(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    moved = VerdictRow("CASE-1", "Suspicious sign-in", "TP", 0)

    store.put_verdicts([moved], "ops")

    assert store.read_inputs().verdict_rows == [moved]


def test_repost_with_a_new_alert_class_writes_history(store: Store, db_path: Path) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    store.put_verdicts([VerdictRow("CASE-1", "Suspicious sign-in", "TP", 0)], "ops")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT count(*) FROM verdict_history").fetchone()[0]

    assert count == 1


def test_mixed_verdict_batch_adds_one_to_generation(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP"), make_verdict("CASE-2", "TP")], "analysts")
    before = store.generation()

    store.put_verdicts([make_verdict("CASE-1", "FP"), make_verdict("CASE-2", "TP")], "ops")

    assert store.generation() == before + 1


def test_failed_put_verdicts_leaves_history_unchanged(store: Store, db_path: Path) -> None:
    store.put_verdicts([make_verdict("CASE-1", "TP")], "analysts")
    unbindable = VerdictRow("CASE-2", "Impossible travel", cast(str, {"not": "text"}), 0)

    with pytest.raises(sqlite3.Error):
        store.put_verdicts([make_verdict("CASE-1", "FP"), unbindable], "ops")
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT count(*) FROM verdict_history").fetchone()[0]

    assert count == 0


def test_verdicts_are_read_in_case_id_order(store: Store) -> None:
    store.put_verdicts([make_verdict("CASE-2"), make_verdict("CASE-1")], "analysts")

    case_ids = [row.case_id for row in store.read_inputs().verdict_rows]

    assert case_ids == ["CASE-1", "CASE-2"]


# Issues


def test_repeated_issue_is_counted_in_one_row(db_path: Path) -> None:
    store = Store.open(db_path, now_ns=make_clock(7_000))
    issue = Issue(IssueKind.INVALID_SPAN, "OTLP/HTTP ingest", "span has no trace ID")
    store.add_issues([issue])
    store.add_issues([issue])

    stored = store.read_inputs().issues
    store.close()

    assert [(item.count, item.first_seen_ns, item.last_seen_ns) for item in stored] == [
        (2, 7_000, 7_001)
    ]


def test_parser_issues_given_with_spans_are_stored(store: Store) -> None:
    issue = Issue(IssueKind.INVALID_ATTRIBUTE, "OTLP/HTTP ingest", "attribute has no key")

    store.add_spans([], [issue])

    assert [stored.issue for stored in store.read_inputs().issues] == [issue]


def test_issues_are_read_in_kind_subject_detail_order(store: Store) -> None:
    store.add_issues(
        [
            Issue(IssueKind.INVALID_SPAN, "b", "2"),
            Issue(IssueKind.INVALID_SPAN, "b", "1"),
            Issue(IssueKind.INVALID_ATTRIBUTE, "z", "9"),
        ]
    )

    issues = [stored.issue for stored in store.read_inputs().issues]

    assert issues == [
        Issue(IssueKind.INVALID_ATTRIBUTE, "z", "9"),
        Issue(IssueKind.INVALID_SPAN, "b", "1"),
        Issue(IssueKind.INVALID_SPAN, "b", "2"),
    ]


def test_spans_are_read_in_key_order(store: Store) -> None:
    store.add_spans([make_span("00000000000000b2"), make_span("00000000000000a1")], [])

    span_ids = [span.span_id for span in store.read_inputs().spans]

    assert span_ids == ["00000000000000a1", "00000000000000b2"]


# Generation


def test_new_store_starts_at_generation_zero(store: Store) -> None:
    assert store.generation() == 0


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda store: store.add_spans([make_span()], []), id="add_spans"),
        pytest.param(
            lambda store: store.add_spans([], [Issue(IssueKind.INVALID_SPAN, "s", "d")]),
            id="add_spans-issues-only",
        ),
        pytest.param(
            lambda store: store.put_verdicts([make_verdict("A"), make_verdict("B")], "analysts"),
            id="put_verdicts",
        ),
        pytest.param(
            lambda store: store.add_issues(
                [Issue(IssueKind.INVALID_SPAN, "s", "1"), Issue(IssueKind.INVALID_SPAN, "s", "2")]
            ),
            id="add_issues",
        ),
    ],
)
def test_changing_write_adds_one_to_generation(
    store: Store, write: Callable[[Store], object]
) -> None:
    write(store)

    assert store.generation() == 1


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda store: store.add_spans([], []), id="add_spans"),
        pytest.param(lambda store: store.put_verdicts([], "analysts"), id="put_verdicts"),
        pytest.param(lambda store: store.add_issues([]), id="add_issues"),
        pytest.param(
            lambda store: store.write_snapshot(Snapshot(0, 1, "<html>", "{}")),
            id="write_snapshot",
        ),
    ],
)
def test_write_that_changes_no_input_leaves_generation(
    store: Store, write: Callable[[Store], object]
) -> None:
    write(store)

    assert store.generation() == 0


def test_failed_add_spans_leaves_generation_unchanged(store: Store) -> None:
    unencodable = make_span("00000000000000a2", attributes={"bad": {1, 2}})

    with pytest.raises(TypeError):
        store.add_spans([make_span("00000000000000a1"), unencodable], [])

    assert store.generation() == 0


def test_failed_add_spans_stores_nothing(store: Store) -> None:
    unencodable = make_span("00000000000000a2", attributes={"bad": {1, 2}})

    with pytest.raises(TypeError):
        store.add_spans([make_span("00000000000000a1"), unencodable], [])

    assert store.read_counts().span_count == 0


def test_failed_put_verdicts_leaves_generation_unchanged(store: Store) -> None:
    unbindable = VerdictRow("CASE-2", "Impossible travel", cast(str, {"not": "text"}), 0)

    with pytest.raises(sqlite3.Error):
        store.put_verdicts([make_verdict("CASE-1"), unbindable], "analysts")

    assert store.generation() == 0


def test_read_inputs_reports_the_generation(store: Store) -> None:
    store.add_spans([make_span()], [])
    store.put_verdicts([make_verdict()], "analysts")

    assert store.read_inputs().generation == 2


# Counts and snapshot


def test_counts_of_a_new_store(store: Store) -> None:
    assert store.read_counts() == StoreCounts(
        generation=0, span_count=0, verdict_count=0, last_ingest_ns=None
    )


def test_counts_after_writes(db_path: Path) -> None:
    store = Store.open(db_path, now_ns=make_clock(9_000))
    store.add_spans([make_span("00000000000000a1"), make_span("00000000000000a2")], [])
    store.put_verdicts([make_verdict()], "analysts")

    counts = store.read_counts()
    store.close()

    assert counts == StoreCounts(generation=2, span_count=2, verdict_count=1, last_ingest_ns=9_001)


def test_new_store_has_no_snapshot(store: Store) -> None:
    assert store.read_snapshot() is None


def test_snapshot_round_trips(store: Store) -> None:
    snapshot = Snapshot(generation=3, finished_at_ns=42, html="<p>ü</p>", results_json='{"a":1}')
    store.write_snapshot(snapshot)

    assert store.read_snapshot() == snapshot


def test_later_snapshot_replaces_the_earlier(store: Store) -> None:
    store.write_snapshot(Snapshot(1, 10, "old", "{}"))
    store.write_snapshot(Snapshot(2, 20, "new", "{}"))

    assert store.read_snapshot() == Snapshot(2, 20, "new", "{}")


# Clearing, changes and the trace family


def fill_store(store: Store) -> None:
    store.add_spans([make_span()], [Issue(IssueKind.CONFLICTING_DUPLICATE_SPAN, "x", "y")])
    store.put_verdicts([make_verdict(label="TP")], "analysts")
    store.put_verdicts([make_verdict(label="FP")], "analysts")
    store.write_snapshot(Snapshot(3, 10, "<html>", "{}"))
    store.set_trace_family("langfuse")


def test_clear_removes_spans_verdicts_and_issues(store: Store) -> None:
    fill_store(store)

    store.clear()

    inputs = store.read_inputs()
    assert (inputs.spans, inputs.verdict_rows, inputs.issues) == ([], [], [])


@pytest.mark.parametrize(
    "table", ["spans", "resources", "verdicts", "verdict_history", "ingest_issues", "snapshot"]
)
def test_clear_empties_every_data_table(store: Store, db_path: Path, table: str) -> None:
    fill_store(store)

    store.clear()

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_clear_removes_the_snapshot(store: Store) -> None:
    fill_store(store)

    store.clear()

    assert store.read_snapshot() is None


def test_clear_advances_the_generation(store: Store) -> None:
    fill_store(store)

    store.clear()

    assert store.generation() == 4


def test_clear_forgets_the_trace_family(store: Store) -> None:
    fill_store(store)

    store.clear()

    assert store.read_trace_family() is None


def test_mark_changed_advances_the_generation(store: Store) -> None:
    store.mark_changed()

    assert store.generation() == 1


def test_mark_changed_changes_no_data(store: Store) -> None:
    fill_store(store)
    before = store.read_inputs()

    store.mark_changed()

    after = store.read_inputs()
    assert (after.spans, after.verdict_rows, after.issues, store.read_snapshot()) == (
        before.spans,
        before.verdict_rows,
        before.issues,
        Snapshot(3, 10, "<html>", "{}"),
    )


def test_new_store_has_no_trace_family(store: Store) -> None:
    assert store.read_trace_family() is None


def test_trace_family_round_trips(store: Store) -> None:
    store.set_trace_family("otlp")

    assert store.read_trace_family() == "otlp"


def test_setting_the_trace_family_leaves_the_generation(store: Store) -> None:
    store.set_trace_family("otlp")

    assert store.generation() == 0


def test_add_spans_records_the_trace_family_given(store: Store) -> None:
    store.add_spans([make_span()], [], trace_family="langfuse")

    assert store.read_trace_family() == "langfuse"


def test_failed_add_spans_records_no_trace_family(store: Store) -> None:
    unencodable = make_span("00000000000000a2", attributes={"bad": {1, 2}})

    with pytest.raises(TypeError):
        store.add_spans([unencodable], [], trace_family="otlp")

    assert store.read_trace_family() is None


def test_put_verdicts_counts_the_issues_given(store: Store) -> None:
    issue = Issue(IssueKind.INVALID_VERDICT_ROW, "verdicts.csv", "line 3")

    store.put_verdicts([make_verdict()], "upload", issues=[issue])

    assert [stored.issue for stored in store.read_inputs().issues] == [issue]


def test_put_verdicts_with_only_a_new_issue_adds_one_to_generation(store: Store) -> None:
    issue = Issue(IssueKind.INVALID_VERDICT_ROW, "verdicts.csv", "line 3")

    store.put_verdicts([], "upload", issues=[issue])

    assert store.generation() == 1


def test_failed_put_verdicts_stores_no_issues(store: Store) -> None:
    unbindable = VerdictRow("CASE-2", "Impossible travel", cast(str, {"not": "text"}), 0)
    issue = Issue(IssueKind.INVALID_VERDICT_ROW, "verdicts.csv", "line 3")

    with pytest.raises(sqlite3.Error):
        store.put_verdicts([unbindable], "upload", issues=[issue])

    assert store.read_inputs().issues == []


# Migrations and integrity


def test_new_file_gets_the_current_schema_version(store: Store, db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert version == SCHEMA_VERSION == 1


def test_file_from_an_unknown_older_version_is_refused(db_path: Path) -> None:
    Store.open(db_path).close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE meta SET value = 0 WHERE key = 'schema_version'")
    connection.close()

    with pytest.raises(StoreVersionError, match="no upgrade from it"):
        Store.open(db_path)


def create_foreign_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE other (id INTEGER)")
    connection.close()


def test_read_only_store_refuses_a_database_without_detecttrace_data(db_path: Path) -> None:
    create_foreign_database(db_path)

    with pytest.raises(StoreVersionError, match="holds no detecttrace data yet"):
        Store.open_read_only(db_path)


def test_database_without_detecttrace_data_names_no_version(db_path: Path) -> None:
    create_foreign_database(db_path)

    with pytest.raises(StoreVersionError) as raised:
        Store.open_read_only(db_path)

    assert "None" not in str(raised.value)


def test_file_from_a_newer_version_is_refused(db_path: Path) -> None:
    Store.open(db_path).close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE meta SET value = 2 WHERE key = 'schema_version'")
    connection.close()

    with pytest.raises(StoreVersionError, match=r"version 2.*version 1"):
        Store.open(db_path)


def test_corrupted_file_is_refused_with_restore_advice(db_path: Path) -> None:
    create_populated_file(db_path)
    with sqlite3.connect(db_path) as connection:
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    connection.close()
    garbage = random.Random(7).randbytes(page_size)
    # A data page near the end: opening never reads it, so only quick_check can catch it.
    with db_path.open("r+b") as file:
        file.seek(db_path.stat().st_size - 2 * page_size)
        file.write(garbage)

    with pytest.raises(StoreIntegrityError, match=r"\.backup"):
        Store.open(db_path)


def test_file_that_is_not_a_database_is_refused(db_path: Path) -> None:
    db_path.write_text("not a database, just some text\n" * 200, encoding="utf-8")

    with pytest.raises(StoreIntegrityError, match=r"\.backup"):
        Store.open(db_path)


def test_integrity_error_names_the_file(db_path: Path) -> None:
    db_path.write_text("not a database, just some text\n" * 200, encoding="utf-8")

    with pytest.raises(StoreIntegrityError, match=r"detecttrace\.db"):
        Store.open(db_path)


def error_with_code(code: int) -> sqlite3.DatabaseError:
    error = sqlite3.DatabaseError("simulated")
    error.sqlite_errorcode = code
    return error


@pytest.mark.parametrize(
    ("code", "is_corruption"),
    [
        pytest.param(sqlite3.SQLITE_CORRUPT, True, id="corrupt"),
        pytest.param(sqlite3.SQLITE_CORRUPT | (1 << 8), True, id="corrupt-extended"),
        pytest.param(sqlite3.SQLITE_NOTADB, True, id="not-a-database"),
        pytest.param(sqlite3.SQLITE_BUSY, False, id="busy"),
        pytest.param(sqlite3.SQLITE_BUSY | (2 << 8), False, id="busy-extended"),
        pytest.param(sqlite3.SQLITE_FULL, False, id="full"),
    ],
)
def test_error_code_classification(code: int, is_corruption: bool) -> None:
    assert _is_corruption(error_with_code(code)) is is_corruption


@pytest.mark.parametrize("version", [0, 2])
def test_read_only_store_refuses_another_schema_version(db_path: Path, version: int) -> None:
    Store.open(db_path).close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (version,))
    connection.close()

    with pytest.raises(StoreVersionError, match=f"version {version}"):
        Store.open_read_only(db_path)


# Read-only access


def test_read_only_store_sees_committed_data(store: Store, db_path: Path) -> None:
    store.add_spans([make_span()], [])

    reader = Store.open_read_only(db_path)
    spans = reader.read_inputs().spans
    reader.close()

    assert spans == [make_span()]


def test_read_only_store_cannot_write(store: Store, db_path: Path) -> None:
    reader = Store.open_read_only(db_path)

    with pytest.raises(sqlite3.OperationalError):
        reader._connection.execute("INSERT INTO meta (key, value) VALUES ('x', 1)")  # pyright: ignore[reportPrivateUsage]
    reader.close()


def test_read_only_store_is_not_writable(store: Store, db_path: Path) -> None:
    reader = Store.open_read_only(db_path)

    with pytest.raises(sqlite3.OperationalError):
        reader.check_writable()
    reader.close()


def test_checking_writability_changes_no_data(store: Store) -> None:
    store.add_spans([make_span()], [])
    before = store.read_counts()

    store.check_writable()

    assert store.read_counts() == before


# Files and durability


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes do not apply on Windows")
def test_new_database_file_is_private(store: Store, db_path: Path) -> None:
    assert db_path.stat().st_mode & 0o777 == 0o600


def test_commits_wait_for_the_disk(store: Store) -> None:
    connection = store._connection  # pyright: ignore[reportPrivateUsage]

    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_busy_timeout_is_set(store: Store) -> None:
    connection = store._connection  # pyright: ignore[reportPrivateUsage]

    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_foreign_keys_are_enforced(store: Store) -> None:
    connection = store._connection  # pyright: ignore[reportPrivateUsage]

    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def fill_until_full(store: Store) -> None:
    connection = store._connection  # pyright: ignore[reportPrivateUsage]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    connection.execute(f"PRAGMA max_page_count = {page_count + 1}")
    store.add_spans([make_span(attributes={"payload": "x" * 200_000})], [])


def test_full_disk_error_is_raised_as_itself(store: Store) -> None:
    with pytest.raises(sqlite3.OperationalError, match="full"):
        fill_until_full(store)


def test_store_writes_again_after_a_failed_commit(store: Store) -> None:
    connection = store._connection  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(sqlite3.IntegrityError), store._write() as now:  # pyright: ignore[reportPrivateUsage]
        # Deferred, so the missing resource fails the COMMIT rather than the INSERT.
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute(
            "INSERT INTO spans (trace_id, span_id, parent_span_id, name, start_ns, end_ns,"
            " is_error, attributes, resource_id, received_at_ns)"
            " VALUES ('t', 's', NULL, 'n', 1, 2, 0, '{}', 999, ?)",
            (now,),
        )

    result = store.add_spans([make_span()], [])

    assert result.accepted == 1


def test_an_error_inside_a_read_is_raised_as_itself(store: Store) -> None:
    with pytest.raises(ValueError, match="read failed"), store._read() as connection:  # pyright: ignore[reportPrivateUsage]
        # As a full disk or an I/O error can, the failure has already ended the transaction.
        connection.execute("ROLLBACK")
        raise ValueError("read failed")


def test_store_writes_again_after_a_full_disk(store: Store) -> None:
    with pytest.raises(sqlite3.OperationalError):
        fill_until_full(store)
    store._connection.execute("PRAGMA max_page_count = 1073741823")  # pyright: ignore[reportPrivateUsage]

    result = store.add_spans([make_span("00000000000000b1")], [])

    assert result.accepted == 1


def test_journal_is_write_ahead(store: Store) -> None:
    connection = store._connection  # pyright: ignore[reportPrivateUsage]

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_concurrent_writers_from_threads_all_land(store: Store) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda batch: store.add_spans(batch, []), THREAD_BATCHES))

    assert store.read_counts().span_count == 400
