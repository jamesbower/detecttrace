"""The service gives `check`'s results for the same input: the demo and every golden fixture.

Each fixture's traces are posted to /v1/traces one document per request, as a Collector would
send them, and its verdict CSV to /api/verdicts; then `compute_snapshot` runs on the stored
input and its results are compared with `run_check` on the fixture's files.

The only differences allowed, each asserted by its own test or listed below with its reason:

- `source` names the endpoints instead of files, and the service adds a `served` block.
- Data notes are compared by kind and count. Their example subjects name a file and line
  (`traces.jsonl:12`) in `check` and the ingest endpoint in the service, and their example
  details, messages and hints follow from the kind.
- NOTE_KIND_CHANGES: notes about a trace file that has no equivalent in a request.
- REJECTED_LABEL_FIXTURES: the verdict API refuses a row whose label is not in label_map, where
  `check` keeps it as a verdict with an unknown label; the service matches `check` on the CSV
  without those rows.
- REPEATED_CASE_FIXTURES: the verdict API keeps one current verdict per case, where `check`
  reports a repeated case ID. No golden fixture repeats one; a test proves the list complete.
"""

import csv
import gzip
import importlib.util
import io
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import generate
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from detecttrace.conventions import TOOL_CALL_RESULT
from detecttrace.pipeline import run_check
from detecttrace.runconfig import ConfigFileError, RunConfig, load_run_config
from detecttrace.serve.app import create_app
from detecttrace.serve.auth import hash_token
from detecttrace.serve.config import ServeConfig, ServeSection, TokenRoles
from detecttrace.serve.recompute import compute_snapshot, load_recompute_settings
from detecttrace.serve.store import Store
from detecttrace.verdicts import VerdictFileError, read_verdicts
from detecttrace.yaml12 import load_yaml12
from serve.app_support import INGEST_TOKEN, READ_TOKEN, VERDICTS_TOKEN

FIXTURE_ROOT = generate.REPO_ROOT / "tests" / "fixtures"
DEMO_FOLDER = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"
DEMO_GOLDEN = FIXTURE_ROOT / "demo" / generate.GOLDEN_NAME
CONFIG_NAME = "detecttrace.yaml"
# The same folders, found the same way, as test_golden.py's fixture test.
FIXTURE_FOLDERS = sorted(
    path.parent for path in FIXTURE_ROOT.rglob(generate.GOLDEN_NAME) if path != DEMO_GOLDEN
)
# 2100-01-01: after every root span, so no case is held back to settle.
NOW_NS = 4_102_444_800 * 1_000_000_000
NEEDS_ZSTD = pytest.mark.skipif(
    importlib.util.find_spec("zstandard") is None, reason="zstandard is not installed"
)
SKIPPED_FIXTURES = {
    "formats/langfuse": "Langfuse exports are not OTLP JSON, the only format the service reads",
    "formats/console_exporter": (
        "console exporter output is not OTLP JSON; the service refuses every line, so there "
        "are no results to compare"
    ),
}
# These stop `check` with an error instead of giving results; each has its own test below.
REFUSED_FIXTURES = ("verdicts/colliding_label_map_keys", "verdicts/missing_required_column")
# Old kind -> new kind, or None when the note has nothing to stand for in the service.
NOTE_KIND_CHANGES: dict[str, dict[str, str | None]] = {
    # An empty file holds no document, so nothing is sent.
    "edge/broken_files/empty_file": {"empty_file": None},
    # The cut-off line is sent as its own request, which is refused as invalid JSON.
    "edge/broken_files/truncated_last_line": {"truncated_line": "invalid_file"},
    # The store takes an identical copy of a span for a client's retry, which is no problem.
    "edge/loading/same_span_in_two_files": {"duplicate_span": None},
}
REJECTED_LABEL_FIXTURES = ("verdicts/unmapped_labels",)
REPEATED_CASE_FIXTURES: tuple[str, ...] = ()
# A demo verdict and the analyst's later change of mind; FP to TP changes the scores.
ORIGINAL_VERDICT_LINE = "DT-IT-0002,impossible_travel,FP,2026-08-03T16:02:32Z"
REPLACED_VERDICT_LINE = "DT-IT-0002,impossible_travel,TP,2026-08-03T16:02:32Z"
REPLACED_VERDICT_CSV = f"case_id,alert_class,verdict,closed_at\n{REPLACED_VERDICT_LINE}\n"


@dataclass(frozen=True)
class Parity:
    served: dict[str, object]
    expected: dict[str, object]  # check's results, with the allowed differences applied
    check: dict[str, object]  # check's results as they are
    tool_results: list[str]  # every gen_ai.tool.call.result value in the posted traces


def to_name(folder: Path) -> str:
    return "demo" if folder == DEMO_FOLDER else folder.relative_to(FIXTURE_ROOT).as_posix()


def to_param(folder: Path) -> object:
    name = to_name(folder)
    marks = [NEEDS_ZSTD] if "zstd" in name else []
    if name in SKIPPED_FIXTURES:
        marks.append(pytest.mark.skip(reason=SKIPPED_FIXTURES[name]))
    return pytest.param(folder, id=name, marks=marks)


COMPARED = [
    to_param(folder)
    for folder in [DEMO_FOLDER, *FIXTURE_FOLDERS]
    if to_name(folder) not in REFUSED_FIXTURES
]


@pytest.fixture(scope="module", params=COMPARED)
def parity(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Parity:
    folder: Path = request.param
    name = to_name(folder)
    config_path = folder / CONFIG_NAME
    run_config = load_run_config(config_path)
    work = tmp_path_factory.mktemp("parity")
    documents = list(read_trace_documents(run_config.traces.path))
    served, _ = serve_fixture(run_config, config_path, documents, work / "d.db")
    check = run_check(run_config, config_path).results
    if name in REJECTED_LABEL_FIXTURES:
        verdicts = write_mapped_rows_only(run_config, work / "verdicts.csv")
        mapped_config = run_config.model_copy(update={"verdicts": verdicts})
        expected = run_check(mapped_config, config_path).results
    else:
        expected = check
    expected = change_note_kinds(expected, NOTE_KIND_CHANGES.get(name, {}))
    tool_results = sorted({value for text in documents for value in find_tool_results(text)})
    return Parity(served, expected, check, tool_results)


def test_served_results_match_check(parity: Parity) -> None:
    assert normalize(parity.served) == normalize(parity.expected)


def test_served_source_names_the_endpoints(parity: Parity) -> None:
    assert parity.served["source"] == {
        **as_mapping(parity.expected["source"]),
        "traces": "OTLP/HTTP",
        "verdicts": "verdict API",
    }


def test_served_results_hold_no_case_back(parity: Parity) -> None:
    assert as_mapping(parity.served["served"])["held_back_cases"] == 0


def test_served_results_hold_no_tool_result(parity: Parity) -> None:
    assert find_quoted(parity.tool_results, parity.served) == []


def test_check_results_hold_no_tool_result(parity: Parity) -> None:
    assert find_quoted(parity.tool_results, parity.check) == []


def test_demo_traces_carry_tool_results() -> None:
    # Guards the two checks above: they look for values that must really be in the traces.
    documents = read_trace_documents(DEMO_FOLDER / "traces")
    assert any(find_tool_results(text) for text in documents)


def test_verdict_api_rejects_a_row_with_an_unmapped_label(tmp_path: Path) -> None:
    folder = FIXTURE_ROOT / "verdicts" / "unmapped_labels"
    run_config = load_run_config(folder / CONFIG_NAME)
    _, reply = serve_fixture(run_config, folder / CONFIG_NAME, [], tmp_path / "d.db")
    assert reply["rejected"] == [
        {"where": "line 2", "reason": "verdict label 'Escalated' is not in label_map"}
    ]


def test_a_replaced_verdict_gives_check_on_the_final_verdict(tmp_path: Path) -> None:
    config_path = DEMO_FOLDER / CONFIG_NAME
    run_config = load_run_config(config_path)
    documents = list(read_trace_documents(run_config.traces.path))
    served, _ = serve_fixture(
        run_config, config_path, documents, tmp_path / "d.db", [REPLACED_VERDICT_CSV]
    )
    final_csv = tmp_path / "verdicts.csv"
    final_csv.write_text(
        run_config.verdicts.path.read_text(encoding="utf-8").replace(
            ORIGINAL_VERDICT_LINE, REPLACED_VERDICT_LINE
        ),
        encoding="utf-8",
    )
    final_config = run_config.model_copy(
        update={"verdicts": run_config.verdicts.model_copy(update={"path": final_csv})}
    )
    expected = run_check(final_config, config_path).results
    assert normalize(served) == normalize(expected)


def test_rejected_label_fixtures_are_those_with_unmapped_labels() -> None:
    assert [to_name(folder) for folder in FIXTURE_FOLDERS if has_unmapped_label(folder)] == list(
        REJECTED_LABEL_FIXTURES
    )


def test_repeated_case_fixtures_are_those_that_repeat_a_case_id() -> None:
    assert [
        to_name(folder) for folder in [DEMO_FOLDER, *FIXTURE_FOLDERS] if repeats_case_id(folder)
    ] == list(REPEATED_CASE_FIXTURES)


def test_serve_config_refuses_label_map_keys_that_collide() -> None:
    folder = FIXTURE_ROOT / "verdicts" / "colliding_label_map_keys"
    check_problem = capture_error(lambda: load_run_config(folder / CONFIG_NAME)).splitlines()[-1]
    serve_error = capture_error(lambda: ServeConfig.model_validate(to_serve_document(folder)))
    assert check_problem.strip().removeprefix("label_map: ") in serve_error


def test_verdict_api_refuses_a_missing_column_as_check_does(tmp_path: Path) -> None:
    folder = FIXTURE_ROOT / "verdicts" / "missing_required_column"
    run_config = load_run_config(folder / CONFIG_NAME)
    check_error = capture_error(lambda: read_verdicts(run_config.verdicts.path))
    _, reply = serve_fixture(run_config, folder / CONFIG_NAME, [], tmp_path / "d.db")
    assert reply["message"] == check_error.replace(str(run_config.verdicts.path), "verdict API")


def normalize(results: Mapping[str, object]) -> dict[str, object]:
    """Results without what may differ: `source`, `served`, and data notes beyond kind and count.

    `source` and `served` have tests of their own above.
    """
    kept = {key: value for key, value in results.items() if key not in ("source", "served")}
    notes = results["data_notes"]
    assert isinstance(notes, list)
    kept["data_notes"] = [[note["kind"], note["count"]] for note in notes]
    return kept


def change_note_kinds(
    results: dict[str, object], changes: Mapping[str, str | None]
) -> dict[str, object]:
    """`results` with each note of a kind in `changes` given the new kind, or dropped for None."""
    notes = results["data_notes"]
    assert isinstance(notes, list)
    changed = []
    for note in notes:
        kind = changes.get(note["kind"], note["kind"])
        if kind is not None:
            changed.append({**note, "kind": kind})
    return {**results, "data_notes": changed}


def serve_fixture(
    run_config: RunConfig,
    config_path: Path,
    documents: list[bytes],
    database: Path,
    later_verdicts: Sequence[str] = (),
) -> tuple[dict[str, object], dict[str, object]]:
    """Post the documents and the verdict CSV; return the snapshot's results and the verdict reply.

    Each of `later_verdicts` is posted after the CSV, as its own request. The results are
    empty when nothing was posted, since there is no snapshot to take.
    """
    config = to_serve_config(run_config, database)
    store = Store.open(database)
    try:
        with TestClient(create_app(config, store, lambda: None)) as client:
            for document in documents:
                client.post(
                    "/v1/traces",
                    content=document,
                    headers={
                        "Authorization": f"Bearer {INGEST_TOKEN}",
                        "Content-Type": "application/json",
                    },
                )
            verdict_headers = {
                "Authorization": f"Bearer {VERDICTS_TOKEN}",
                "Content-Type": "text/csv; charset=utf-8",
            }
            reply = client.post(
                "/api/verdicts",
                content=run_config.verdicts.path.read_bytes(),
                headers=verdict_headers,
            ).json()
            for text in later_verdicts:
                client.post(
                    "/api/verdicts", content=text.encode("utf-8"), headers=verdict_headers
                ).raise_for_status()
    finally:
        store.close()
    if not documents:
        return {}, reply
    settings = load_recompute_settings(config, config_path)
    outcome = compute_snapshot(database, settings, NOW_NS)
    return json.loads(outcome.snapshot.results_json), reply


def to_serve_config(run_config: RunConfig, database: Path) -> ServeConfig:
    """A serve configuration with the run configuration's shared settings, settling at once."""
    return ServeConfig(
        mapping=run_config.mapping,
        label_map=run_config.label_map,
        agent_label_map=run_config.agent_label_map,
        checklists=run_config.checklists,
        dashboard=run_config.dashboard,
        serve=ServeSection(database=database, settle_seconds=0),
        tokens=TokenRoles.model_validate(create_token_roles()),
    )


def create_token_roles() -> dict[str, object]:
    return {
        "ingest": [{"name": "collector", "hash": hash_token(INGEST_TOKEN)}],
        "verdicts": [{"name": "soar", "hash": hash_token(VERDICTS_TOKEN)}],
        "read": [{"name": "analysts", "hash": hash_token(READ_TOKEN)}],
    }


def to_serve_document(folder: Path) -> dict[str, object]:
    """The fixture's configuration file as a serve configuration document."""
    document = load_yaml12(folder / CONFIG_NAME, max_bytes=1 << 20, what="test configuration")
    assert isinstance(document, dict)
    shared = {key: value for key, value in document.items() if key not in ("traces", "verdicts")}
    return {
        **shared,
        "serve": {"database": "d.db", "settle_seconds": 0},
        "tokens": create_token_roles(),
    }


def read_trace_documents(path: Path) -> Iterator[bytes]:
    """Each OTLP document in the trace files at `path`, as a Collector would post it.

    Files go in the loader's order. A file that is one JSON document is sent whole; any other
    is sent a line at a time, a bad line included, so the service sees what `check` read.
    """
    files = (
        [path]
        if path.is_file()
        else sorted(
            (file for file in path.rglob("*") if file.is_file()),
            key=lambda file: file.relative_to(path).as_posix(),
        )
    )
    for file in files:
        data = decompress(file.read_bytes())
        try:
            json.loads(data)
        except ValueError:
            yield from (line for line in data.splitlines() if line.strip())
        else:
            yield data


def decompress(data: bytes) -> bytes:
    if data.startswith(b"\x1f\x8b"):
        return gzip.decompress(data)
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        import zstandard

        reader = zstandard.ZstdDecompressor().stream_reader(
            io.BytesIO(data), read_across_frames=True
        )
        return reader.read()
    return data


def find_tool_results(document: bytes) -> list[str]:
    """Every string value of a gen_ai.tool.call.result attribute in an OTLP document."""
    found: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if value.get("key") == TOOL_CALL_RESULT:
                found.extend(find_strings(value.get("value")))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    try:
        parsed = json.loads(document)
    except ValueError:
        # A cut-off line holds no attribute the service could store.
        return []
    visit(parsed)
    return found


def find_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from find_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from find_strings(item)


def find_quoted(values: list[str], results: Mapping[str, object]) -> list[str]:
    """The values that appear in the results JSON, as JSON quotes them."""
    text = json.dumps(results, ensure_ascii=False)
    return [value for value in values if json.dumps(value, ensure_ascii=False)[1:-1] in text]


def write_mapped_rows_only(run_config: RunConfig, path: Path) -> object:
    """Write the verdict rows the API accepts, those with a mapped label; return the new section."""
    with run_config.verdicts.path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))
    label = rows[0].index("verdict")
    kept = [rows[0], *(row for row in rows[1:] if run_config.to_analyst_verdict(row[label]))]
    with path.open("w", encoding="utf-8", newline="") as target:
        csv.writer(target).writerows(kept)
    return run_config.verdicts.model_copy(update={"path": path})


def has_unmapped_label(folder: Path) -> bool:
    try:
        run_config = load_run_config(folder / CONFIG_NAME)
        rows, _ = read_verdicts(run_config.verdicts.path)
    except (ConfigFileError, VerdictFileError):
        return False
    return any(run_config.to_analyst_verdict(row.label) is None for row in rows)


def repeats_case_id(folder: Path) -> bool:
    try:
        rows, _ = read_verdicts(load_run_config(folder / CONFIG_NAME).verdicts.path)
    except (ConfigFileError, VerdictFileError):
        return False
    case_ids = [row.case_id for row in rows]
    return len(case_ids) != len(set(case_ids))


def capture_error(action: Callable[[], object]) -> str:
    try:
        action()
    except (ConfigFileError, VerdictFileError, ValidationError) as error:
        return str(error)
    raise AssertionError("expected an error")


def as_mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, dict)
    return value
