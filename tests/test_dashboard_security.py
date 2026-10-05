"""Hostile trace and verdict content must stay inert text on the page.

Each payload is planted in every field the page shows (case ID, alert class, prompt version,
tool name, tool arguments, analyst and agent labels, checklist item) and run through the real
pipeline, so escaping is tested from input file to rendered HTML.
"""

import base64
import csv
import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import generate
import pytest
from builders import otlp_document, otlp_span, span_hex, write_jsonl
from html_tree import Node, has_tag, parse_html

from detecttrace.dashboard import ServedPage, render_dashboard
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.summary import to_visible_text

DEMO_GOLDEN = Path(__file__).parent / "fixtures" / "demo" / "expected.json"
DEMO_GOLDEN_HTML = Path(__file__).parent / "fixtures" / "demo" / generate.GOLDEN_HTML_NAME
LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)
ESCAPE = chr(0x1B)
BELL = chr(0x07)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
PAYLOADS = [
    "</script><script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '" onmouseover="x',
    f"a{LINE_SEPARATOR}b{PARAGRAPH_SEPARATOR}c",
    f"a{ESCAPE}[31m{RIGHT_TO_LEFT_OVERRIDE}b{BELL}",
    # Entity text must stay literal: decoded once too often, it would become a real tag.
    "&lt;script&gt;alert(1)&lt;/script&gt;",
]
PAYLOAD_IDS = ["script", "img", "attribute", "separators", "control_and_bidi", "entity"]
PLANTED_RESULT = "PLANTED_TOOL_RESULT_7f3a"
CONFIG = """\
traces: {path: traces}
verdicts: {path: verdicts.csv}
checklists: checklists
output: dashboard.html
label_map: {TP: true_positive, FP: false_positive, Benign: benign}
"""


@pytest.fixture(scope="module", params=PAYLOADS, ids=PAYLOAD_IDS)
def payload(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="module")
def hostile(payload: str, tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, str]:
    """The results object and the page for a run with `payload` in every shown field."""
    config_path = write_hostile_run(tmp_path_factory.mktemp("hostile"), payload)
    results = run_check(load_run_config(config_path), config_path).results
    return results, render_dashboard(results)


@pytest.fixture(scope="module")
def hostile_page(hostile: tuple[Any, str]) -> Node:
    return parse_html(hostile[1])


def write_hostile_run(folder: Path, payload: str) -> Path:
    fields = planted_fields(payload)
    cases = [
        (fields["case"], fields["version"], fields["agent"], fields["tool"]),
        # A plain case beside it, so the run always has a case both sides give a verdict for.
        ("DT-OK", "v1", "Benign", "get_signin_logs"),
    ]
    documents = []
    for number, (case_id, version, agent_label, tool) in enumerate(cases, start=1):
        trace_id = f"{number:032x}"
        root = span_hex(number * 2)
        documents.append(
            otlp_document(
                [
                    otlp_span(
                        root,
                        trace_id=trace_id,
                        name="invoke_agent triage",
                        attributes={
                            "gen_ai.operation.name": "invoke_agent",
                            "detecttrace.case_id": case_id,
                            "detecttrace.alert_class": fields["class"],
                            "detecttrace.verdict": agent_label,
                            "detecttrace.prompt_version": version,
                        },
                    ),
                    otlp_span(
                        span_hex(number * 2 + 1),
                        root,
                        trace_id=trace_id,
                        name=f"execute_tool {tool}",
                        attributes={
                            "gen_ai.operation.name": "execute_tool",
                            "gen_ai.tool.name": tool,
                            "gen_ai.tool.call.arguments": fields["arguments"],
                            "gen_ai.tool.call.result": PLANTED_RESULT,
                        },
                    ),
                ]
            )
        )
    (folder / "traces").mkdir()
    write_jsonl(folder / "traces" / "batch.jsonl", documents)
    with (folder / "verdicts.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["case_id", "alert_class", "verdict"])
        writer.writerow([fields["case"], fields["class"], fields["analyst"]])
        writer.writerow(["DT-OK", fields["class"], "TP"])
    (folder / "checklists").mkdir()
    # JSON strings are valid YAML double-quoted scalars, escapes included.
    (folder / "checklists" / "hostile.yaml").write_text(
        f"alert_class: {json.dumps(fields['class'])}\n"
        "items:\n"
        f"  - {{id: {json.dumps(fields['item'])}, tool: {json.dumps(fields['tool'])}}}\n"
        "  - {id: never, tool: never_called}\n",
        encoding="utf-8",
    )
    config_path = folder / "detecttrace.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    return config_path


def planted_fields(payload: str) -> dict[str, str]:
    # A prefix per field keeps the values distinct, so each can be found on its own.
    fields = {
        name: f"{name}{payload}"
        for name in ("case", "class", "version", "tool", "item", "analyst", "agent")
    }
    fields["arguments"] = json.dumps({"q": f"arg{payload}"}, ensure_ascii=False)
    return fields


def results_block(page: Node) -> str:
    return page.find(has_tag("script", id="dt-results")).text()


def without_results_block(html: str) -> str:
    return re.sub(
        r'<script type="application/json" id="dt-results">.*?</script>', "", html, flags=re.S
    )


@cache
def demo_html() -> str:
    return render_dashboard(json.loads(DEMO_GOLDEN.read_text(encoding="utf-8")))


@cache
def demo_page() -> Node:
    return parse_html(demo_html())


def to_hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


# The payload reaches the page (guards the checks below)


@pytest.mark.parametrize(
    ("field", "find"),
    [
        ("case", lambda results: results["case_rows"]["columns"]["case_id"]),
        ("class", lambda results: [entry["alert_class"] for entry in results["classes"]]),
        ("version", lambda results: results["classes"][0]["versions"]),
        (
            "tool",
            lambda results: [call["tool"] for d in results["case_detail"] for call in d["calls"]],
        ),
        (
            "arguments",
            lambda results: [c["arguments"] for d in results["case_detail"] for c in d["calls"]],
        ),
        ("item", lambda results: results["classes"][0]["checklist_item_ids"]),
    ],
)
def test_the_planted_value_is_in_the_results(
    hostile: tuple[Any, str], payload: str, field: str, find: Any
) -> None:
    assert planted_fields(payload)[field] in find(hostile[0])


@pytest.mark.parametrize("field", ["analyst", "agent"])
def test_the_planted_label_is_in_a_data_note(
    hostile: tuple[Any, str], payload: str, field: str
) -> None:
    messages = " ".join(note["message"] for note in hostile[0]["data_notes"])
    assert planted_fields(payload)[field] in messages


# Nothing executes


def test_no_script_element_is_added(hostile_page: Node) -> None:
    scripts = hostile_page.find_all(has_tag("script"))
    assert [script.attrs.get("type") for script in scripts] == ["application/json", None]


def test_no_image_element_is_added(hostile_page: Node) -> None:
    assert hostile_page.find_all(has_tag("img")) == []


def test_no_event_handler_attribute_is_added(hostile_page: Node) -> None:
    handlers = [
        name for node in hostile_page.iter() for name in node.attrs if name.startswith("on")
    ]
    assert handlers == []


def test_the_script_is_the_packaged_one(hostile_page: Node) -> None:
    script = hostile_page.find(lambda node: node.tag == "script" and "type" not in node.attrs)
    assert (
        script.text()
        == demo_page().find(lambda node: node.tag == "script" and "type" not in node.attrs).text()
    )


# The results block


def test_the_results_block_parses_back_to_the_exact_results(
    hostile: tuple[Any, str], hostile_page: Node
) -> None:
    assert json.loads(results_block(hostile_page)) == hostile[0]


@pytest.mark.parametrize("character", ["<", ">", "&", LINE_SEPARATOR, PARAGRAPH_SEPARATOR])
def test_the_results_block_never_holds_the_raw_character(
    hostile_page: Node, character: str
) -> None:
    assert character not in results_block(hostile_page)


# Server-rendered text


@pytest.mark.parametrize(
    ("field", "find"),
    [
        ("class", lambda page: page.find(has_tag("h3", id="class-0-v-h")).text()),
        ("version", lambda page: page.find_all(lambda node: "vkey" in node.classes())[1].text()),
        (
            "item",
            lambda page: (
                page.find(lambda node: node.tag == "table" and "skip-t" in node.classes())
                .find(has_tag("code"))
                .text()
            ),
        ),
        (
            "case",
            lambda page: (
                page.find(lambda node: "examples" in node.classes()).find(has_tag("code")).text()
            ),
        ),
    ],
)
def test_a_server_rendered_label_shows_as_text(
    hostile_page: Node, payload: str, field: str, find: Any
) -> None:
    assert find(hostile_page) == to_visible_text(planted_fields(payload)[field])


@pytest.mark.parametrize("character", [ESCAPE, BELL, RIGHT_TO_LEFT_OVERRIDE])
def test_control_and_bidi_characters_show_as_visible_escapes(
    hostile: tuple[Any, str], character: str
) -> None:
    assert character not in without_results_block(hostile[1])


def test_a_tool_result_never_appears(hostile: tuple[Any, str]) -> None:
    assert PLANTED_RESULT not in hostile[1]


# No network


def test_no_reference_leaves_the_page_but_the_banner_link(hostile_page: Node) -> None:
    references = [
        value
        for node in hostile_page.iter()
        for name, value in node.attrs.items()
        if name in ("href", "src", "action", "srcset", "xlink:href") and value is not None
    ]
    assert [value for value in references if not value.startswith("#")] == [
        "https://detecttrace.ai"
    ]


def test_no_url_appears_but_the_banner_link(hostile: tuple[Any, str]) -> None:
    assert re.findall(r"(?:https?:)?//[\w.-]+", hostile[1]) == ["https://detecttrace.ai"]


@pytest.mark.parametrize("fragment", ["@import", "url(", "@font-face"])
def test_the_stylesheet_loads_nothing(fragment: str) -> None:
    assert fragment not in demo_page().find(has_tag("style")).text()


# Content Security Policy


def test_the_policy_allows_exactly_the_inline_blocks() -> None:
    page = demo_page()
    style = page.find(has_tag("style")).text()
    script = page.find(lambda node: node.tag == "script" and "type" not in node.attrs).text()
    policy = page.find(has_tag("meta", **{"http-equiv": "Content-Security-Policy"})).attrs[
        "content"
    ]
    assert policy == (
        f"default-src 'none'; script-src '{to_hash(script)}'; style-src '{to_hash(style)}'; "
        "img-src data:; base-uri 'none'; form-action 'none'"
    )


def test_the_policy_comes_right_after_the_charset() -> None:
    head = demo_page().find(has_tag("head"))
    elements = [node for node in head.children if isinstance(node, Node)]
    assert [(node.tag, sorted(node.attrs)) for node in elements[:2]] == [
        ("meta", ["charset"]),
        ("meta", ["content", "http-equiv"]),
    ]


@pytest.mark.parametrize("source", ["'unsafe-inline'", "'unsafe-eval'", "frame-ancestors"])
def test_the_policy_has_no_loose_or_ignored_source(source: str) -> None:
    policy = demo_page().find(has_tag("meta", **{"http-equiv": "Content-Security-Policy"}))
    assert source not in (policy.attrs["content"] or "")


def test_the_hashes_hold_for_a_hostile_page(hostile_page: Node) -> None:
    policy = hostile_page.find(has_tag("meta", **{"http-equiv": "Content-Security-Policy"}))
    script = hostile_page.find(lambda node: node.tag == "script" and "type" not in node.attrs)
    assert f"script-src '{to_hash(script.text())}'" in (policy.attrs["content"] or "")


# Served mode

SERVE_SCRIPT = (
    Path(__file__).resolve().parent.parent / "src/detecttrace/templates/dashboard-serve.js"
)
SERVED = ServedPage(generation=7, updated_at="2026-10-05T12:00:00.000000Z")


@cache
def served_html() -> str:
    return render_dashboard(json.loads(DEMO_GOLDEN.read_text(encoding="utf-8")), served=SERVED)


@cache
def served_page() -> Node:
    return parse_html(served_html())


def test_an_offline_page_is_byte_identical_to_the_golden_page() -> None:
    page = render_dashboard(json.loads(DEMO_GOLDEN.read_text(encoding="utf-8")), served=None)
    assert generate.normalize_dashboard(page) == DEMO_GOLDEN_HTML.read_text(encoding="utf-8")


def test_a_served_page_allows_both_scripts_and_requests_to_its_own_server() -> None:
    page = served_page()
    style = page.find(has_tag("style")).text()
    first, second = (
        node.text()
        for node in page.find_all(lambda node: node.tag == "script" and "type" not in node.attrs)
    )
    policy = page.find(has_tag("meta", **{"http-equiv": "Content-Security-Policy"})).attrs[
        "content"
    ]
    assert policy == (
        f"default-src 'none'; script-src '{to_hash(first)}' '{to_hash(second)}'; "
        f"style-src '{to_hash(style)}'; img-src data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'none'"
    )


def test_a_served_page_adds_exactly_the_serve_script() -> None:
    scripts = served_page().find_all(has_tag("script"))
    assert [script.text() for script in scripts[2:]] == [SERVE_SCRIPT.read_text(encoding="utf-8")]


def test_a_served_page_carries_its_generation() -> None:
    region = served_page().find(has_tag("div", id="dt-serve"))
    assert (region.attrs["data-generation"], region.attrs["data-updated-at"]) == (
        "7",
        "2026-10-05T12:00:00.000000Z",
    )


def test_a_served_page_does_not_claim_to_make_no_requests() -> None:
    assert "makes no network requests" not in served_page().find(has_tag("footer")).text()


def test_a_served_page_has_no_event_handler_attribute() -> None:
    handlers = [
        name for node in served_page().iter() for name in node.attrs if name.startswith("on")
    ]
    assert handlers == []


def test_a_served_page_names_no_url_but_the_banner_link() -> None:
    assert re.findall(r"(?:https?:)?//[\w.-]+", served_html()) == ["https://detecttrace.ai"]


@pytest.mark.parametrize(
    "sink",
    [
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "Function(",
        'setTimeout("',
        'setAttribute("on',
        ".onclick",
    ],
)
def test_the_serve_script_uses_no_html_or_code_sink(sink: str) -> None:
    assert sink not in SERVE_SCRIPT.read_text(encoding="utf-8")
