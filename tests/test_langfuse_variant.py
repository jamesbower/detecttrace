"""The generated Langfuse variant has the real export's shape and reads like the same cases as OTLP.

formats/langfuse holds the demo's impossible travel cases in weeks 2-4 as Langfuse rows, and
formats/otlp_subset holds the same cases as OTLP, so any difference in their results that isn't
the Langfuse duplicate rows is a reader or generator bug.
"""

import gzip
import json
from pathlib import Path
from typing import Any

import fixture_specs
import pytest

from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config

FORMATS_DIR = fixture_specs.FIXTURE_ROOT / "formats"
LANGFUSE_DIR = FORMATS_DIR / "langfuse"
REAL_DIR = fixture_specs.FIXTURE_ROOT / "langfuse_real"
METADATA_PREFIXES = {"attributes", "resourceAttributes", "scope"}


def run_results(folder: Path) -> dict[str, Any]:
    config_path = folder / fixture_specs.CONFIG_NAME
    return run_check(load_run_config(config_path), config_path).results


@pytest.fixture(scope="module")
def api_page() -> dict[str, Any]:
    return json.loads((LANGFUSE_DIR / "traces" / "api_page.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def blob_rows() -> list[dict[str, Any]]:
    text = gzip.decompress((LANGFUSE_DIR / "traces" / "blob.jsonl.gz").read_bytes())
    return [json.loads(line) for line in text.decode("utf-8").splitlines()]


@pytest.fixture(scope="module")
def real_api_page() -> dict[str, Any]:
    return json.loads((REAL_DIR / "v2_all_fields_page1.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def real_blob_rows() -> list[dict[str, Any]]:
    text = (REAL_DIR / "blob_observations_v2.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


@pytest.fixture(scope="module")
def langfuse_results() -> dict[str, Any]:
    return run_results(LANGFUSE_DIR)


@pytest.fixture(scope="module")
def otlp_results() -> dict[str, Any]:
    return run_results(FORMATS_DIR / "otlp_subset")


def test_api_page_has_the_real_page_keys(
    api_page: dict[str, Any], real_api_page: dict[str, Any]
) -> None:
    assert (list(api_page), list(api_page["meta"])) == (
        list(real_api_page),
        list(real_api_page["meta"]),
    )


def test_api_rows_have_the_real_api_fields_in_the_real_order(
    api_page: dict[str, Any], real_api_page: dict[str, Any]
) -> None:
    assert {tuple(row) for row in api_page["data"]} == {tuple(row) for row in real_api_page["data"]}


def test_blob_rows_have_the_real_blob_fields_in_the_real_order(
    blob_rows: list[dict[str, Any]], real_blob_rows: list[dict[str, Any]]
) -> None:
    assert {tuple(row) for row in blob_rows} == {tuple(row) for row in real_blob_rows}


def test_metadata_keys_use_only_the_real_attribute_prefixes(
    api_page: dict[str, Any], blob_rows: list[dict[str, Any]]
) -> None:
    rows = [*api_page["data"], *blob_rows]

    assert {key.partition(".")[0] for row in rows for key in row["metadata"]} == METADATA_PREFIXES


def test_blob_metadata_values_are_all_strings(blob_rows: list[dict[str, Any]]) -> None:
    assert {type(value) for row in blob_rows for value in row["metadata"].values()} == {str}


def test_blob_repeats_three_rows_exactly(blob_rows: list[dict[str, Any]]) -> None:
    texts = [json.dumps(row) for row in blob_rows]

    assert len(texts) - len(set(texts)) == 3


def test_blob_repeats_one_more_row_with_only_updated_at_changed(
    blob_rows: list[dict[str, Any]],
) -> None:
    texts = [json.dumps({**row, "updated_at": None}) for row in blob_rows]

    assert len(texts) - len(set(texts)) == 4


def test_langfuse_results_equal_the_otlp_subset_results_except_data_notes(
    langfuse_results: dict[str, Any], otlp_results: dict[str, Any]
) -> None:
    # data_notes is the only difference: the Langfuse blob export repeats rows.
    assert {**langfuse_results, "data_notes": None} == {**otlp_results, "data_notes": None}


def test_langfuse_data_notes_hold_only_the_four_repeated_rows(
    langfuse_results: dict[str, Any],
) -> None:
    assert [(note["kind"], note["count"]) for note in langfuse_results["data_notes"]] == [
        ("duplicate_span", 4)
    ]


def test_otlp_subset_has_no_data_notes(otlp_results: dict[str, Any]) -> None:
    assert otlp_results["data_notes"] == []
