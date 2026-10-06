import json
import os
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from html_tree import Node, has_tag, parse_html

from detecttrace import __version__, dashboard
from detecttrace.dashboard import (
    is_dashboard_file,
    render_dashboard,
    render_waiting_page,
    to_script_json,
    write_dashboard,
)
from detecttrace.dashboard_view import (
    build_served_view,
    build_view,
    build_waiting_view,
    to_view_json,
)
from detecttrace.pipeline import run_check
from detecttrace.results import write_results_json
from detecttrace.runconfig import load_run_config
from detecttrace.served_page import ServedPage, WaitingCounts

FIXTURES = Path(__file__).parent / "fixtures"
DEMO_GOLDEN = FIXTURES / "demo" / "expected.json"
# Fixtures whose input can't be used at all end in an error, not a page.
UNUSABLE = {"colliding_label_map_keys", "missing_required_column"}
FIXTURE_CONFIGS = sorted(
    path.relative_to(FIXTURES).parent.as_posix()
    for path in FIXTURES.rglob("detecttrace.yaml")
    if path.parent.name not in UNUSABLE
)
SERVED = ServedPage(generation=7, updated_at="2026-10-05T12:00:00.000000Z", held_back_cases=3)
WAITING_COUNTS = WaitingCounts(span_count=12, case_count=0, held_back_count=2, verdict_count=5)
SLOTS = [
    "__DT_CSP__",
    "__DT_GENERATOR__",
    '<script type="application/json" id="dt-view"></script>',
    '<script type="application/json" id="dt-results"></script>',
]


@cache
def demo_results() -> Any:
    return json.loads(DEMO_GOLDEN.read_text(encoding="utf-8"))


@cache
def demo_html() -> str:
    return render_dashboard(demo_results())


@cache
def demo_page() -> Node:
    return parse_html(demo_html())


@cache
def served_page() -> Node:
    return parse_html(render_dashboard(demo_results(), served=SERVED))


@cache
def waiting_page() -> Node:
    return parse_html(render_waiting_page(WAITING_COUNTS, [], SERVED))


@cache
def fixture_results(name: str) -> dict[str, object]:
    config_path = (FIXTURES / name / "detecttrace.yaml").absolute()
    return run_check(load_run_config(config_path), config_path).results


def data_block(page: Node, block_id: str) -> str:
    return page.find(has_tag("script", id=block_id)).text()


# Structure


def test_the_page_language_is_english() -> None:
    assert demo_page().find(has_tag("html")).attrs["lang"] == "en"


def test_the_page_explains_it_needs_javascript() -> None:
    assert demo_page().find(has_tag("noscript")).text() == "This dashboard needs JavaScript."


def test_the_page_has_no_self_reported_text() -> None:
    assert "self-reported" not in demo_html().lower()


def test_the_generator_marker_names_this_version() -> None:
    meta = demo_page().find(has_tag("meta", name="generator"))
    assert meta.attrs["content"] == f"detecttrace {__version__}"


@pytest.mark.parametrize("name", FIXTURE_CONFIGS)
def test_every_fixture_renders_a_page(name: str) -> None:
    assert render_dashboard(fixture_results(name)).startswith("<!doctype html>")


def test_an_unknown_schema_version_is_refused() -> None:
    with pytest.raises(ValueError):
        render_dashboard({**demo_results(), "schema_version": 999})


# Inline blocks


@pytest.mark.parametrize("page", [demo_page, served_page, waiting_page], ids=lambda f: f.__name__)
def test_the_page_has_one_stylesheet(page: Any) -> None:
    assert len(page().find_all(has_tag("style"))) == 1


@pytest.mark.parametrize("page", [demo_page, served_page, waiting_page], ids=lambda f: f.__name__)
def test_the_page_has_one_executable_script_and_two_data_blocks(page: Any) -> None:
    scripts = page().find_all(has_tag("script"))
    assert [(script.attrs.get("type"), script.attrs.get("id")) for script in scripts] == [
        ("module", None),
        ("application/json", "dt-view"),
        ("application/json", "dt-results"),
    ]


def test_the_page_links_no_stylesheet_or_script_file() -> None:
    assert demo_page().find_all(lambda node: node.tag == "link" or "src" in node.attrs) == []


def test_no_element_has_a_style_attribute() -> None:
    assert [node.tag for node in demo_page().iter() if "style" in node.attrs] == []


def test_the_results_block_holds_the_full_results() -> None:
    assert json.loads(data_block(demo_page(), "dt-results")) == demo_results()


def test_the_view_block_holds_the_view_of_the_results() -> None:
    assert json.loads(data_block(demo_page(), "dt-view")) == to_view_json(
        build_view(demo_results())
    )


def test_a_served_page_holds_the_served_view() -> None:
    assert json.loads(data_block(served_page(), "dt-view")) == to_view_json(
        build_served_view(demo_results(), SERVED)
    )


def test_a_served_page_holds_the_full_results() -> None:
    assert json.loads(data_block(served_page(), "dt-results")) == demo_results()


def test_the_waiting_page_holds_the_waiting_view() -> None:
    assert json.loads(data_block(waiting_page(), "dt-view")) == to_view_json(
        build_waiting_view(WAITING_COUNTS, [], SERVED)
    )


def test_the_waiting_page_leaves_the_results_block_empty() -> None:
    assert data_block(waiting_page(), "dt-results") == ""


@pytest.mark.parametrize("slot", SLOTS)
def test_every_slot_is_filled(slot: str) -> None:
    assert slot not in demo_html()


def test_a_value_spelling_a_slot_stays_in_its_block() -> None:
    results = json.loads(json.dumps(demo_results()))
    results["case_rows"]["columns"]["case_id"][0] = "__DT_CSP__ __DT_GENERATOR__"
    page = parse_html(render_dashboard(results))
    assert json.loads(data_block(page, "dt-results")) == results


def test_rendering_is_deterministic() -> None:
    assert render_dashboard(demo_results()) == demo_html()


# A broken packaged page


@pytest.fixture
def packaged_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A stand-in for the package's templates folder, read in place of the real one."""
    templates = tmp_path / "templates"
    templates.mkdir()
    real = Path(__file__).resolve().parent.parent / "src" / "detecttrace" / "templates"
    for name in ("dashboard.html", "dashboard.hashes.json"):
        (templates / name).write_text((real / name).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(dashboard, "files", lambda package: tmp_path)
    dashboard._load_page.cache_clear()
    yield templates / "dashboard.html"
    dashboard._load_page.cache_clear()


@pytest.mark.parametrize("slot", SLOTS)
def test_a_page_missing_a_slot_is_refused(packaged_page: Path, slot: str) -> None:
    packaged_page.write_text(
        packaged_page.read_text(encoding="utf-8").replace(slot, ""), encoding="utf-8"
    )
    with pytest.raises(RuntimeError):
        render_dashboard(demo_results())


@pytest.mark.parametrize("slot", SLOTS)
def test_a_page_repeating_a_slot_is_refused(packaged_page: Path, slot: str) -> None:
    packaged_page.write_text(
        packaged_page.read_text(encoding="utf-8").replace(slot, slot + slot), encoding="utf-8"
    )
    with pytest.raises(RuntimeError):
        render_dashboard(demo_results())


def test_hashes_without_a_script_hash_are_refused(packaged_page: Path) -> None:
    (packaged_page.parent / "dashboard.hashes.json").write_text(
        '{"style": "sha256-aThnzQW1Gi63mvi1CJ0yBGU0KcS0COLSdy7NcGZf0cA="}', encoding="utf-8"
    )
    with pytest.raises(RuntimeError):
        render_dashboard(demo_results())


# Embedding


@pytest.mark.parametrize(
    ("character", "escape"),
    [
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ],
)
def test_script_json_escapes_characters_that_could_end_the_block(
    character: str, escape: str
) -> None:
    assert to_script_json({"text": f"a{character}b"}) == f'{{"text":"a{escape}b"}}'


def test_script_json_parses_back_to_the_same_object() -> None:
    data = {"text": '</script><!-- & \u2028\u2029 "quoted"'}
    assert json.loads(to_script_json(data)) == data


def test_script_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        to_script_json({"rate": float("nan")})


# Writing and recognizing the file


def test_write_dashboard_writes_the_page_as_utf8(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.html"
    write_dashboard("<p>café</p>", path)
    assert path.read_bytes() == "<p>café</p>".encode()


def test_write_dashboard_gives_the_mode_a_plain_open_would(tmp_path: Path) -> None:
    umask = os.umask(0)
    os.umask(umask)
    path = tmp_path / "dashboard.html"
    write_dashboard("page", path)
    assert path.stat().st_mode & 0o777 == 0o666 & ~umask


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_write_dashboard_keeps_the_mode_of_the_file_it_replaces(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.html"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o640)
    write_dashboard("new", path)
    assert path.stat().st_mode & 0o777 == 0o640


def test_write_dashboard_replaces_an_earlier_page(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.html"
    write_dashboard("old", path)
    write_dashboard("new", path)
    assert path.read_text(encoding="utf-8") == "new"


def test_write_dashboard_leaves_no_temporary_file(tmp_path: Path) -> None:
    write_dashboard("page", tmp_path / "dashboard.html")
    assert [path.name for path in tmp_path.iterdir()] == ["dashboard.html"]


def test_our_page_is_recognized(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.html"
    write_dashboard(demo_html(), path)
    assert is_dashboard_file(path)


def test_a_lower_case_doctype_and_a_self_closed_marker_are_recognized(tmp_path: Path) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        '<!doctype html><head><meta name="generator" content="detecttrace 0.0.1" /><script>',
        encoding="utf-8",
    )
    assert is_dashboard_file(path)


def test_the_smallest_page_with_our_marker_is_recognized(tmp_path: Path) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        '<!DOCTYPE html><head><meta name="generator" content="detecttrace 0.0.1"></head>',
        encoding="utf-8",
    )
    assert is_dashboard_file(path)


@pytest.mark.parametrize(
    "text",
    [
        "<!DOCTYPE html><html><head><title>Mine</title></head></html>",
        '<meta name="generator" content="Hugo 0.120">',
        "",
        '<!DOCTYPE html><head><meta name="generator" content="detecttrace-like thing"></head>',
        '<!DOCTYPE html><head><meta name="generator" content="detecttrace "></head>',
        '<html><head><meta name="generator" content="detecttrace 0.0.1"></head>',
        ' <!DOCTYPE html><head><meta name="generator" content="detecttrace 0.0.1"></head>',
        '<!DOCTYPE html><head></head><meta name="generator" content="detecttrace 0.0.1">',
        '<!DOCTYPE html><head><meta name="generator" content="detecttrace 0.0.1">',
        '<!DOCTYPE html><head><!-- <meta name="generator" content="Hugo"> --></head>',
        '<!DOCTYPE html><head><script></script><meta name="generator" content="detecttrace 1">',
    ],
    ids=[
        "no-marker",
        "other-generator",
        "empty",
        "look-alike-name",
        "no-version",
        "no-doctype",
        "doctype-not-first",
        "marker-after-head",
        "head-never-ends",
        "other-marker-in-comment",
        "marker-after-script",
    ],
)
def test_a_foreign_file_is_not_recognized(tmp_path: Path, text: str) -> None:
    path = tmp_path / "page.html"
    path.write_text(text, encoding="utf-8")
    assert not is_dashboard_file(path)


def test_our_results_json_is_not_a_dashboard(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    write_results_json(demo_results(), path)
    assert not is_dashboard_file(path)


def test_a_marker_past_the_first_64_kib_is_not_recognized(tmp_path: Path) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        "<!DOCTYPE html><head>"
        + " " * 64 * 1024
        + '<meta name="generator" content="detecttrace 1"></head>',
        encoding="utf-8",
    )
    assert not is_dashboard_file(path)


def test_a_folder_is_not_a_dashboard(tmp_path: Path) -> None:
    assert not is_dashboard_file(tmp_path)


def test_a_missing_file_raises_instead_of_answering(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        is_dashboard_file(tmp_path / "missing.html")
