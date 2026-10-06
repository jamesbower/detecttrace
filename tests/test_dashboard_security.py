"""Hostile trace and verdict content must stay inert data on the page.

Each payload is planted in every field the page shows (case ID, alert class, prompt version,
tool name, tool arguments, analyst and agent labels, checklist item) and run through the real
pipeline, so escaping is tested from input file to rendered HTML. The page's script builds
every element from the two JSON blocks; the browser tests check what it shows.
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

from detecttrace.dashboard import render_dashboard, render_waiting_page
from detecttrace.dashboard_view import build_view, to_view_json
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.served_page import ServedPage, WaitingCounts
from detecttrace.summary import to_visible_text

DEMO_GOLDEN = Path(__file__).parent / "fixtures" / "demo" / "expected.json"
DEMO_GOLDEN_HTML = Path(__file__).parent / "fixtures" / "demo" / generate.GOLDEN_HTML_NAME
DASHBOARD_SOURCE = Path(__file__).resolve().parent.parent / "dashboard" / "src"
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


def data_block(page: Node, block_id: str) -> str:
    return page.find(has_tag("script", id=block_id)).text()


def without_results_block(html: str) -> str:
    return re.sub(
        r'<script type="application/json" id="dt-results">.*?</script>', "", html, flags=re.S
    )


def list_strings(value: object) -> list[str]:
    """Every string in a JSON value, keys aside."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in list_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in list_strings(item)]
    return []


def find_executable_script(page: Node) -> str:
    return page.find(
        lambda node: node.tag == "script" and node.attrs.get("type") == "module"
    ).text()


def find_policy(page: Node) -> str:
    meta = page.find(has_tag("meta", **{"http-equiv": "Content-Security-Policy"}))
    return meta.attrs["content"] or ""


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
    assert [script.attrs.get("type") for script in scripts] == [
        "module",
        "application/json",
        "application/json",
    ]


def test_the_page_closes_exactly_its_three_script_elements(hostile: tuple[Any, str]) -> None:
    assert hostile[1].count("</script") == 3


def test_no_image_element_is_added(hostile_page: Node) -> None:
    assert hostile_page.find_all(has_tag("img")) == []


def test_no_event_handler_attribute_is_added(hostile_page: Node) -> None:
    handlers = [
        name for node in hostile_page.iter() for name in node.attrs if name.startswith("on")
    ]
    assert handlers == []


def test_the_script_is_the_packaged_one(hostile_page: Node) -> None:
    assert find_executable_script(hostile_page) == find_executable_script(demo_page())


# The data blocks


def test_the_results_block_parses_back_to_the_exact_results(
    hostile: tuple[Any, str], hostile_page: Node
) -> None:
    assert json.loads(data_block(hostile_page, "dt-results")) == hostile[0]


def test_the_view_block_parses_back_to_the_exact_view(
    hostile: tuple[Any, str], hostile_page: Node
) -> None:
    assert json.loads(data_block(hostile_page, "dt-view")) == to_view_json(build_view(hostile[0]))


@pytest.mark.parametrize("block_id", ["dt-view", "dt-results"])
@pytest.mark.parametrize(
    "fragment",
    ["<", ">", "&", "</script", "<!--", LINE_SEPARATOR, PARAGRAPH_SEPARATOR],
    ids=["lt", "gt", "amp", "end-tag", "comment", "line-separator", "paragraph-separator"],
)
def test_a_data_block_never_holds_the_raw_text(
    hostile_page: Node, block_id: str, fragment: str
) -> None:
    assert fragment not in data_block(hostile_page, block_id)


# Text the view prepares


@pytest.mark.parametrize("field", ["class", "version", "item", "case"])
def test_the_view_holds_a_label_in_its_visible_form(
    hostile_page: Node, payload: str, field: str
) -> None:
    visible = to_visible_text(planted_fields(payload)[field])
    strings = list_strings(json.loads(data_block(hostile_page, "dt-view")))
    assert [text for text in strings if visible in text] != []


@pytest.mark.parametrize("character", [ESCAPE, BELL, RIGHT_TO_LEFT_OVERRIDE])
def test_control_and_bidi_characters_show_as_visible_escapes(
    hostile: tuple[Any, str], character: str
) -> None:
    assert character not in without_results_block(hostile[1])


def test_a_tool_result_never_appears(hostile: tuple[Any, str]) -> None:
    assert PLANTED_RESULT not in hostile[1]


# No network

# React's built code names the SVG, MathML, XLink and XML namespaces and links its error
# decoder in messages; none of them is ever requested.
INERT_URLS = {
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/XML/1998/namespace",
    "https://react.dev/errors/",
}
EXTERNAL_REFERENCES = [
    r"""\b(?:src|href|action|srcset|poster|data)\s*=\s*["'`]?\s*(?:https?:)?//""",
    r"""url\(\s*["']?\s*(?:https?:)?//""",
    r"@import",
    r"""\b(?:fetch|open|sendBeacon|EventSource|WebSocket)\(\s*["'`](?:https?:|wss?:)?//""",
]


@pytest.fixture(scope="module", params=["offline", "served", "waiting"])
def any_html(request: pytest.FixtureRequest) -> str:
    if request.param == "offline":
        return demo_html()
    if request.param == "served":
        return served_html()
    return waiting_html()


def test_no_reference_leaves_the_page(hostile_page: Node) -> None:
    references = [
        value
        for node in hostile_page.iter()
        for name, value in node.attrs.items()
        if name in ("href", "src", "action", "srcset", "xlink:href") and value is not None
    ]
    assert [value for value in references if not value.startswith("#")] == []


@pytest.mark.parametrize("pattern", EXTERNAL_REFERENCES)
def test_the_page_loads_no_external_resource(any_html: str, pattern: str) -> None:
    assert re.findall(pattern, any_html) == []


def test_every_url_in_the_page_is_an_inert_constant(any_html: str) -> None:
    assert set(re.findall(r"""(?:https?:)?//[\w.-]+[^\s"'`)]*""", any_html)) - INERT_URLS == set()


def test_a_hostile_page_names_no_url_of_its_own(hostile: tuple[Any, str]) -> None:
    urls = set(re.findall(r"""(?:https?:)?//[\w.-]+[^\s"'`)]*""", hostile[1]))
    assert urls - INERT_URLS == set()


@pytest.mark.parametrize("fragment", ["@import", "url(", "@font-face"])
def test_the_stylesheet_loads_nothing(fragment: str) -> None:
    assert fragment not in demo_page().find(has_tag("style")).text()


@pytest.mark.parametrize(
    "sink",
    [
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "dangerouslySetInnerHTML",
        "eval(",
        "Function(",
        'setTimeout("',
        "setTimeout(`",
        'setAttribute("on',
        ".onclick",
    ],
)
def test_the_dashboard_source_uses_no_html_or_code_sink(sink: str) -> None:
    sources = sorted(
        path
        for path in DASHBOARD_SOURCE.rglob("*.ts*")
        if ".test." not in path.name and path.name != "test-fixtures.ts"
    )
    assert [path.name for path in sources if sink in path.read_text(encoding="utf-8")] == []


# Content Security Policy


def test_the_policy_allows_exactly_the_inline_blocks() -> None:
    page = demo_page()
    style = page.find(has_tag("style")).text()
    script = find_executable_script(page)
    assert find_policy(page) == (
        f"default-src 'none'; script-src '{to_hash(script)}'; style-src '{to_hash(style)}'; "
        "base-uri 'none'; form-action 'none'"
    )


def test_the_policy_comes_right_after_the_charset() -> None:
    head = demo_page().find(has_tag("head"))
    elements = [node for node in head.children if isinstance(node, Node)]
    assert [(node.tag, sorted(node.attrs)) for node in elements[:2]] == [
        ("meta", ["charset"]),
        ("meta", ["content", "http-equiv"]),
    ]


@pytest.mark.parametrize(
    "source", ["'unsafe-inline'", "'unsafe-eval'", "frame-ancestors", "img-src", "connect-src"]
)
def test_the_offline_policy_has_no_loose_ignored_or_network_source(source: str) -> None:
    assert source not in find_policy(demo_page())


def test_the_hashes_hold_for_a_hostile_page(hostile_page: Node) -> None:
    script = find_executable_script(hostile_page)
    assert f"script-src '{to_hash(script)}'" in find_policy(hostile_page)


# Served mode

SERVED = ServedPage(generation=7, updated_at="2026-10-05T12:00:00.000000Z", held_back_cases=0)
WAITING_COUNTS = WaitingCounts(span_count=3, case_count=0, held_back_count=1, verdict_count=2)


@cache
def served_html() -> str:
    return render_dashboard(json.loads(DEMO_GOLDEN.read_text(encoding="utf-8")), served=SERVED)


@cache
def served_page() -> Node:
    return parse_html(served_html())


@cache
def waiting_html() -> str:
    return render_waiting_page(WAITING_COUNTS, [], SERVED)


@cache
def ui_html() -> str:
    return render_dashboard(
        json.loads(DEMO_GOLDEN.read_text(encoding="utf-8")), served=SERVED, mode="ui"
    )


@cache
def ui_waiting_html() -> str:
    return render_waiting_page(WAITING_COUNTS, [], SERVED, mode="ui")


def served_view(held_back_cases: int) -> Any:
    served = ServedPage(7, "2026-10-05T12:00:00.000000Z", held_back_cases)
    page = parse_html(
        render_dashboard(json.loads(DEMO_GOLDEN.read_text(encoding="utf-8")), served=served)
    )
    return json.loads(data_block(page, "dt-view"))


def test_an_offline_page_is_byte_identical_to_the_golden_page() -> None:
    page = render_dashboard(json.loads(DEMO_GOLDEN.read_text(encoding="utf-8")), served=None)
    assert generate.normalize_dashboard(page) == DEMO_GOLDEN_HTML.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "html",
    [served_html, waiting_html, ui_html, ui_waiting_html],
    ids=["served", "waiting", "ui", "ui_waiting"],
)
def test_a_served_page_allows_its_inline_blocks_and_requests_to_its_own_server(html: Any) -> None:
    page = parse_html(html())
    style = page.find(has_tag("style")).text()
    script = find_executable_script(page)
    assert find_policy(page) == (
        f"default-src 'none'; script-src '{to_hash(script)}'; style-src '{to_hash(style)}'; "
        "base-uri 'none'; form-action 'none'; connect-src 'self'"
    )


@pytest.mark.parametrize(
    ("html", "mode"),
    [(served_html, "served"), (waiting_html, "served"), (ui_html, "ui"), (ui_waiting_html, "ui")],
    ids=["served", "waiting", "ui", "ui_waiting"],
)
def test_a_served_page_tells_the_dashboard_its_mode(html: Any, mode: str) -> None:
    assert json.loads(data_block(parse_html(html()), "dt-view"))["mode"] == mode


def test_an_offline_page_tells_the_dashboard_it_is_offline() -> None:
    assert json.loads(data_block(demo_page(), "dt-view"))["mode"] == "offline"


def test_a_served_page_runs_the_same_script_as_an_offline_page() -> None:
    assert find_executable_script(served_page()) == find_executable_script(demo_page())


def test_a_served_page_carries_its_generation() -> None:
    served = json.loads(data_block(served_page(), "dt-view"))["served"]
    assert (served["generation"], served["updated_at"]) == (7, "2026-10-05T12:00:00.000000Z")


@pytest.mark.parametrize(
    ("held_back_cases", "line"),
    [
        (1, "1 case still settling is not counted yet."),
        (1234, "1,234 cases still settling are not counted yet."),
    ],
)
def test_a_served_page_says_how_many_cases_are_still_settling(
    held_back_cases: int, line: str
) -> None:
    assert served_view(held_back_cases)["served"]["held_back_text"] == line


def test_a_served_page_with_nothing_settling_does_not_mention_settling() -> None:
    assert served_view(0)["served"]["held_back_text"] is None


def test_a_served_page_does_not_claim_to_make_no_requests() -> None:
    assert "makes no network requests" not in served_view(0)["header"]["footer_text"]


def test_a_served_page_has_no_event_handler_attribute() -> None:
    handlers = [
        name for node in served_page().iter() for name in node.attrs if name.startswith("on")
    ]
    assert handlers == []
