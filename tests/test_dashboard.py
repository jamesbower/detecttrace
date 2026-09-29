import json
import os
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from html_tree import Node, has_tag, parse_html

from detecttrace import __version__
from detecttrace.dashboard import (
    is_dashboard_file,
    render_dashboard,
    to_script_json,
    write_dashboard,
)
from detecttrace.dashboard_view import LIMITS, format_percent
from detecttrace.pipeline import run_check
from detecttrace.results import write_results_json
from detecttrace.runconfig import load_run_config

FIXTURES = Path(__file__).parent / "fixtures"
DEMO_GOLDEN = FIXTURES / "demo" / "expected.json"
# Fixtures whose input can't be used at all end in an error, not a page.
UNUSABLE = {"colliding_label_map_keys", "missing_required_column"}
FIXTURE_CONFIGS = sorted(
    path.relative_to(FIXTURES).parent.as_posix()
    for path in FIXTURES.rglob("detecttrace.yaml")
    if path.parent.name not in UNUSABLE
)
PART_TITLES = [
    "header",
    "banner",
    "By alert class and version",
    "Checklist steps the agent skipped",
    "Weekly trend",
    "Agent verdict against analyst verdict",
    "Cases",
    "Data notes",
    "What this dashboard does not tell you",
]
BANNER_TEXT = (
    "Self-reported. Not verified by DetectTrace. These numbers come from your own traces and "
    "verdicts, computed on your machine. If you must show results to a client, a CISO, or an "
    "auditor, you need verified results: detecttrace.ai"
)


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
def fixture_results(name: str) -> dict[str, object]:
    config_path = (FIXTURES / name / "detecttrace.yaml").absolute()
    return run_check(load_run_config(config_path), config_path).results


@cache
def fixture_page(name: str) -> Node:
    return demo_page() if name == "demo" else parse_html(render_dashboard(fixture_results(name)))


def version_entry(class_index: int, version: str) -> Any:
    entries = demo_results()["classes"][class_index]["by_version"]
    return next(entry for entry in entries if entry["version"] == version)


def part_title(node: Node) -> str | None:
    if node.tag == "header":
        return "header"
    if node.tag == "aside" and "banner" in node.classes():
        return "banner"
    return node.text() if node.tag == "h2" else None


def version_rows(page: Node, class_index: int) -> dict[str, list[str]]:
    """The versions table of one class: row label to its cell texts."""
    table = page.find(
        lambda node: (
            node.tag == "table"
            and node.find_all(has_tag("caption", id=f"class-{class_index}-v-cap")) != []
        )
    )
    body = table.find(has_tag("tbody"))
    return {
        _row_label(row.find(has_tag("th"))): [cell.text() for cell in row.find_all(has_tag("td"))]
        for row in body.find_all(has_tag("tr"))
    }


def _row_label(header: Node) -> str:
    # The label comes first; a "Few cases." note may follow it in the same cell.
    first = header.children[0]
    return first if isinstance(first, str) else first.text()


def section(page: Node, heading_id: str) -> Node:
    return page.find(
        lambda node: node.tag == "section" and node.attrs.get("aria-labelledby") == heading_id
    )


# Structure


def test_the_page_has_its_nine_parts_in_order() -> None:
    titles = [part_title(node) for node in demo_page().iter()]
    assert [title for title in titles if title is not None] == PART_TITLES


def test_the_page_language_is_english() -> None:
    assert demo_page().find(has_tag("html")).attrs["lang"] == "en"


def test_the_skip_link_leads_to_the_main_content() -> None:
    link = demo_page().find(lambda node: "skip-link" in node.classes())
    assert (link.attrs["href"], demo_page().find(has_tag("main")).attrs["id"]) == ("#main", "main")


def test_every_section_is_labelled_by_its_heading() -> None:
    page = demo_page()
    ids = {node.attrs.get("id") for node in page.iter()}
    labels = [node.attrs.get("aria-labelledby") for node in page.find_all(has_tag("section"))]
    assert [label for label in labels if label not in ids] == []


def test_every_table_has_a_caption() -> None:
    tables = demo_page().find_all(has_tag("table"))
    assert [table for table in tables if not table.find_all(has_tag("caption"))] == []


def test_every_header_cell_has_a_scope() -> None:
    cells = demo_page().find_all(has_tag("th"))
    assert [cell.text() for cell in cells if cell.attrs.get("scope") not in ("col", "row")] == []


def test_the_banner_text_is_exact() -> None:
    banner = demo_page().find(lambda node: "banner" in node.classes())
    assert " ".join(banner.text().split()) == BANNER_TEXT


def test_the_banner_link_opens_without_referrer_or_opener() -> None:
    link = demo_page().find(has_tag("a", href="https://detecttrace.ai"))
    assert link.attrs["rel"] == "noopener noreferrer"


@pytest.mark.parametrize(("term", "text"), LIMITS)
def test_the_limits_section_states_each_limit(term: str, text: str) -> None:
    limits = section(demo_page(), "s-limits")
    assert f"{term}{text}" in limits.text()


def test_the_case_table_explains_it_needs_javascript() -> None:
    note = demo_page().find(has_tag("noscript"))
    assert note.text() == "The case table needs JavaScript."


def test_the_case_table_shows_a_status_line_until_the_script_runs() -> None:
    status = section(demo_page(), "s-cases").find(has_tag("p", id="case-status"))
    assert status.text() == (
        "Loading the case table… If this message stays, the table script didn't run."
    )


def test_the_case_table_caption_is_short() -> None:
    caption = demo_page().find(has_tag("caption", id="cases-cap"))
    assert caption.text() == "Cases, newest week first, then by case ID."


def test_the_case_table_explains_how_to_open_a_case() -> None:
    hint = section(demo_page(), "s-cases").find(lambda node: "cases-hint" in node.classes())
    assert hint.text() == "Select a case ID to open its details."


def test_the_case_table_starts_hidden_until_the_script_runs() -> None:
    assert "hidden" in demo_page().find(has_tag("div", id="case-ui")).attrs


def test_each_class_has_a_filter_by_its_index_in_the_strings_table() -> None:
    buttons = demo_page().find_all(
        lambda node: node.tag == "button" and "data-filter" in node.attrs
    )
    assert [button.attrs["data-filter"] for button in buttons] == [
        "all",
        "disagreements",
        "dangerous",
        "class:0",
        "class:6",
    ]


def test_the_hollow_marker_legend_uses_the_neutral_style() -> None:
    item = demo_page().find(
        lambda node: node.tag == "li" and node.text() == "Hollow marker: fewer than 10 cases"
    )
    marker = item.find(lambda node: "mk" in node.classes())
    assert marker.classes() == ["mk", "c-none", "is-few-point"]


@cache
def low_coverage_page() -> Node:
    results = json.loads(json.dumps(demo_results()))
    results["totals"]["coverage"].update(verdicts_matched=1, verdicts_low=True)
    return parse_html(render_dashboard(results))


def test_the_low_coverage_alert_links_to_the_data_notes() -> None:
    link = low_coverage_page().find(lambda node: "cov-alert" in node.classes()).find(has_tag("a"))
    assert link.attrs["href"] == "#s-notes"


def test_the_low_coverage_alert_target_exists() -> None:
    targets = low_coverage_page().find_all(lambda node: node.attrs.get("id") == "s-notes")
    assert [node.tag for node in targets] == ["h2"]


def test_the_generator_marker_names_this_version() -> None:
    meta = demo_page().find(has_tag("meta", name="generator"))
    assert meta.attrs["content"] == f"detecttrace {__version__}"


# The demo's numbers, in the right sections


@pytest.mark.parametrize(("class_index", "version"), [(0, "v1"), (0, "v2"), (1, "v1"), (1, "v2")])
def test_completeness_per_version_matches_the_results(class_index: int, version: str) -> None:
    mean = version_entry(class_index, version)["metrics"]["completeness"]["mean"]
    assert version_rows(demo_page(), class_index)[version][1].startswith(format_percent(mean))


@pytest.mark.parametrize("class_index", [0, 1])
def test_the_case_count_per_class_matches_the_results(class_index: int) -> None:
    overall = demo_results()["classes"][class_index]["overall"]
    assert version_rows(demo_page(), class_index)["All versions"][0] == str(overall["case_count"])


@pytest.mark.parametrize(
    ("class_index", "expected"), [(0, "3 dangerous false closes"), (1, "1 dangerous false close")]
)
def test_the_confusion_matrix_names_the_dangerous_false_closes(
    class_index: int, expected: str
) -> None:
    card = demo_page().find(has_tag("article", **{"aria-labelledby": f"class-{class_index}-c-h"}))
    assert card.find(lambda node: "sub" in node.classes()).text().endswith(expected)


def test_a_dangerous_cell_with_cases_is_outlined_and_flagged() -> None:
    cell = section(demo_page(), "s-confusion").find(
        lambda node: node.tag == "td" and node.text().startswith("3 ")
    )
    assert (cell.classes(), cell.text()) == (
        ["h-off-1", "is-danger"],
        "3 (dangerous cell)dangerous",
    )


def test_a_dangerous_cell_without_cases_is_outlined_but_not_flagged() -> None:
    table = section(demo_page(), "s-confusion").find(has_tag("table"))
    first_row = table.find(has_tag("tbody")).find(has_tag("tr"))
    cell = first_row.find_all(has_tag("td"))[1]
    assert (cell.classes(), cell.text()) == (["h-0", "is-danger"], "0 (dangerous cell)")


def test_no_confusion_cell_replaces_its_content_with_a_label() -> None:
    cells = section(demo_page(), "s-confusion").find_all(has_tag("td"))
    assert [cell.text() for cell in cells if "aria-label" in cell.attrs] == []


def test_the_confusion_matrix_corner_names_both_axes() -> None:
    header_row = section(demo_page(), "s-confusion").find(has_tag("thead")).find(has_tag("tr"))
    corner = next(node for node in header_row.children if isinstance(node, Node))
    assert (corner.tag, corner.text()) == ("th", "Analyst verdict by agent verdict")


def test_the_cases_section_counts_every_case() -> None:
    lede = section(demo_page(), "s-cases").find(lambda node: "lede" in node.classes())
    assert lede.text().startswith("Filters apply to all 201 cases.")


def test_the_data_notes_show_coverage() -> None:
    coverage = section(demo_page(), "s-notes").find(has_tag("ul"))
    assert [item.text() for item in coverage.find_all(has_tag("li"))] == [
        "201 of 201 verdicts matched a trace (100%).",
        "201 of 201 traces matched a verdict (100%).",
    ]


def test_the_trend_chart_has_a_hidden_table_with_the_same_weeks() -> None:
    trend = section(demo_page(), "s-trend")
    hidden = trend.find(lambda node: "visually-hidden" in node.classes() and node.tag == "div")
    weeks = [
        row.find(has_tag("th")).text()
        for row in hidden.find(has_tag("tbody")).find_all(has_tag("tr"))
    ]
    assert weeks == ["2026-W32", "2026-W33", "2026-W34", "2026-W35", "2026-W36", "2026-W37"]


def test_the_trend_chart_draws_one_line_per_version() -> None:
    chart = section(demo_page(), "s-trend").find(lambda node: "chart" in node.classes())
    paths = chart.find_all(has_tag("path"))
    assert [path.attrs["class"] for path in paths] == [
        "series-all",
        "series c-1 d-1",
        "series c-2 d-2",
    ]


# Labels and small samples


def test_no_version_cases_get_their_own_row() -> None:
    assert list(version_rows(fixture_page("edge/versions/no_version"), 0)) == [
        "All versions",
        "v1",
        "(no version)",
    ]


def test_pooled_versions_are_named_in_their_row() -> None:
    labels = list(version_rows(fixture_page("edge/versions/more_than_six"), 0))
    assert labels[-1] == "(other versions: v2, v5)"


def test_pooled_versions_are_explained_under_the_table() -> None:
    notes = fixture_page("edge/versions/more_than_six").find_all(
        lambda node: "card-note" in node.classes()
    )
    assert (
        notes[0].text().endswith("v2, v5 have the fewest cases here and are pooled as one group.")
    )


def test_a_small_sample_row_carries_the_few_cases_note() -> None:
    rows = section(fixture_page("edge/kappa/fewer_than_ten_cases"), "s-versions").find_all(
        has_tag("tr")
    )
    few = [row for row in rows if "is-few" in row.classes()]
    assert [row.find(lambda node: "few-note" in node.classes()).text() for row in few] == [
        "Few cases.",
        "Few cases.",
    ]


def test_a_class_without_a_checklist_says_so_instead_of_a_chart() -> None:
    trend = section(fixture_page("edge/versions/ab_split"), "s-trend")
    figure = trend.find(has_tag("figure"))
    assert figure.find(has_tag("p")).text() == "No checklist for this class."


@pytest.mark.parametrize("name", FIXTURE_CONFIGS)
def test_every_fixture_renders_a_page(name: str) -> None:
    assert render_dashboard(fixture_results(name)).startswith("<!DOCTYPE html>")


# Inline blocks and styling


@pytest.mark.parametrize(
    "name", ["demo", "edge/versions/more_than_six", "edge/kappa/fewer_than_ten_cases"]
)
def test_no_element_has_a_style_attribute(name: str) -> None:
    assert [node.tag for node in fixture_page(name).iter() if "style" in node.attrs] == []


def test_the_page_has_one_stylesheet() -> None:
    assert len(demo_page().find_all(has_tag("style"))) == 1


def test_the_page_has_one_executable_script() -> None:
    scripts = demo_page().find_all(has_tag("script"))
    assert [script.attrs.get("type") for script in scripts] == ["application/json", None]


def test_the_page_links_no_stylesheet_or_script_file() -> None:
    assert demo_page().find_all(lambda node: node.tag == "link" or "src" in node.attrs) == []


def test_the_results_block_holds_the_full_results() -> None:
    block = demo_page().find(has_tag("script", id="dt-results"))
    assert json.loads(block.text()) == demo_results()


def test_rendering_is_deterministic() -> None:
    assert render_dashboard(demo_results()) == demo_html()


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
