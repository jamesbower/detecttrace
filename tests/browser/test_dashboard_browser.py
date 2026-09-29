"""The dashboard in a real browser: accessibility, keyboard, filters, layout, CSP and network.

Dev-only and run by hand. Playwright and axe-core are not project dependencies:

    uv run --with playwright python -m playwright install chromium
    uv run --with playwright pytest -m browser -s -q tests/browser

axe-core is downloaded once, checked against a pinned hash, and cached outside the repository.
"""

import copy
import hashlib
import json
import re
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from scale import write_scale_dataset
from test_dashboard_security import PAYLOAD_IDS, PAYLOADS, planted_fields, write_hostile_run

from detecttrace.dashboard import render_dashboard, write_dashboard
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.summary import to_visible_text

# Playwright is not a project dependency, so Pyright can't see its types; pages and
# browsers are typed `Any` here.
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed; run `uv run --with playwright pytest -m browser tests/browser`",
)

pytestmark = pytest.mark.browser

AXE_VERSION = "4.13.0"
AXE_SHA256 = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"
AXE_URL = f"https://cdn.jsdelivr.net/npm/axe-core@{AXE_VERSION}/axe.min.js"
AXE_CACHE = Path.home() / ".cache" / "detecttrace-dev" / f"axe-core-{AXE_VERSION}.min.js"
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
DEMO_RESULTS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "demo" / "expected.json").read_text(encoding="utf-8")
)
WIDTHS = [1280, 360]
PAGE_SIZE = 100
FIRST_RENDER_TIMEOUT_MS = 180_000

# Registered before any page script runs, so a violation during parsing is caught too.
WATCH_SCRIPT = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", function (event) {
  window.__cspViolations.push(
    event.violatedDirective + " blocked " + (event.blockedURI || "inline") + ": " + event.sample
  );
}, true);
"""

FOCUS_PROBE = """() => {
  const el = document.activeElement;
  const style = getComputedStyle(el);
  const hasRing = (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0)
    || style.boxShadow !== "none";
  let name;
  if (el.matches(".skip-link")) name = "skip link";
  else if (el.matches(".cov-alert a")) name = "coverage link";
  else if (el.matches(".banner a")) name = "banner link";
  else if (el.matches("#case-filters button")) name = "filter " + el.dataset.filter;
  else if (el.matches(".row-toggle")) {
    name = "row toggle " + Array.from(document.querySelectorAll(".row-toggle")).indexOf(el);
  } else if (el.matches("#case-more")) name = "show more";
  else name = el.tagName.toLowerCase() + " " + (el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || el.id);
  return [name, hasRing];
}"""


def count_matching(key: str) -> int:
    """The cases a filter should match, computed from the results as the metrics do."""
    columns = DEMO_RESULTS["case_rows"]["columns"]
    codes = DEMO_RESULTS["case_rows"]["verdict_codes"]
    unknown = DEMO_RESULTS["case_rows"]["unknown_verdict_code"]
    pairs = list(zip(columns["analyst"], columns["agent"], strict=True))
    if key == "disagreements":
        return sum(1 for a, b in pairs if unknown not in (a, b) and a != b)
    if key == "dangerous":
        return sum(
            1
            for a, b in pairs
            if unknown not in (a, b)
            and codes[a] == "true_positive"
            and codes[b] in ("false_positive", "benign")
        )
    if key.startswith("class:"):
        return columns["class_index"].count(int(key.removeprefix("class:")))
    return len(pairs)


FILTER_KEYS = [
    "all",
    "disagreements",
    "dangerous",
    *(
        f"class:{index}"
        for index in sorted(set(DEMO_RESULTS["case_rows"]["columns"]["class_index"]))
    ),
]


@dataclass
class Visit:
    page: Any
    console_errors: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)

    def csp_violations(self) -> list[str]:
        return self.page.evaluate("window.__cspViolations")


@contextmanager
def visiting(
    browser: Any, path: Path, *, width: int = 1280, bypass_csp: bool = False
) -> Iterator[Visit]:
    """Open `path` with every request other than the page itself logged and blocked."""
    url = path.as_uri()
    context = browser.new_context(viewport={"width": width, "height": 900}, bypass_csp=bypass_csp)
    context.add_init_script(WATCH_SCRIPT)

    def block_others(route: Any) -> None:
        if route.request.url == url:
            route.continue_()
        else:
            route.abort()

    context.route("**/*", block_others)
    page = context.new_page()
    visit = Visit(page)

    def log_request(request: Any) -> None:
        if request.url != url:
            visit.requests.append(request.url)

    def log_console(message: Any) -> None:
        if message.type == "error":
            visit.console_errors.append(message.text)

    def dismiss_dialog(dialog: Any) -> None:
        visit.dialogs.append(f"{dialog.type}: {dialog.message}")
        dialog.dismiss()

    page.on("request", log_request)
    page.on("console", log_console)
    page.on("pageerror", lambda error: visit.console_errors.append(str(error)))
    page.on("dialog", dismiss_dialog)
    try:
        page.goto(url)
        yield visit
    finally:
        context.close()


def open_first_dangerous_case(page: Any) -> None:
    # Dangerous cases are among the detail cases, so the opened row has tool calls to show.
    page.click('#case-filters button[data-filter="dangerous"]')
    page.click(".row-toggle >> nth=0")


# Fixtures


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("browser") / "demo.html"
    write_dashboard(render_dashboard(DEMO_RESULTS), path)
    return path


@pytest.fixture(scope="module")
def low_coverage_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The demo with a low-coverage warning, so the header carries its link, and the other
    safety notes (a true positive without an agent verdict, dropped kappa resamples) that the
    demo data never produces, so axe checks them too."""
    results = copy.deepcopy(DEMO_RESULTS)
    results["totals"]["coverage"].update(verdicts_total=500, verdicts_low=True)
    first_class = results["classes"][0]
    first_class["overall"]["true_positives_without_agent_verdict"] = ["DT-IT-0001"]
    first_class["by_version"][0]["metrics"]["true_positives_without_agent_verdict"] = ["DT-IT-0001"]
    first_class["overall"]["kappa"]["dropped_resamples"] = 87
    path = tmp_path_factory.mktemp("browser") / "low_coverage.html"
    write_dashboard(render_dashboard(results), path)
    return path


@pytest.fixture(scope="module")
def axe_source() -> str:
    if not AXE_CACHE.exists():
        with urllib.request.urlopen(AXE_URL, timeout=60) as response:
            data = response.read()
        # Checked before caching, so a bad download is never reused.
        assert hashlib.sha256(data).hexdigest() == AXE_SHA256, f"unexpected hash from {AXE_URL}"
        AXE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        AXE_CACHE.write_bytes(data)
    data = AXE_CACHE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == AXE_SHA256, f"{AXE_CACHE} changed; delete it"
    return data.decode("utf-8")


@pytest.fixture(scope="module", params=["demo", "low coverage"])
def axe_violations(
    request: pytest.FixtureRequest,
    browser: Any,
    axe_source: str,
    demo_path: Path,
    low_coverage_path: Path,
) -> list[Any]:
    path = demo_path if request.param == "demo" else low_coverage_path
    # The page's CSP rightly blocks the injected axe script; only this run lifts it.
    with visiting(browser, path, bypass_csp=True) as visit:
        open_first_dangerous_case(visit.page)
        visit.page.add_script_tag(content=axe_source)
        results = visit.page.evaluate(
            "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", WCAG_TAGS
        )
    print(
        f"\naxe-core {AXE_VERSION} on {request.param}: {len(results['passes'])} rules passed, "
        f"{len(results['violations'])} violated, {len(results['incomplete'])} need review "
        f"({', '.join(sorted(rule['id'] for rule in results['incomplete']))})"
    )
    return results["violations"]


@pytest.fixture(scope="module")
def tab_walk(browser: Any, low_coverage_path: Path) -> list[tuple[str, bool]]:
    """Each element Tab reaches from the top, up to "Show 100 more", and whether it shows a ring."""
    walk: list[tuple[str, bool]] = []
    with visiting(browser, low_coverage_path) as visit:
        for _ in range(500):
            visit.page.keyboard.press("Tab")
            name, has_ring = visit.page.evaluate(FOCUS_PROBE)
            walk.append((name, has_ring))
            if name == "show more":
                break
    return walk


@pytest.fixture(scope="module")
def demo_visit(browser: Any, demo_path: Path) -> Iterator[tuple[Visit, dict[str, str]]]:
    """The demo after using every filter, "Show 100 more" and a row, with the count per filter."""
    counts: dict[str, str] = {}
    with visiting(browser, demo_path) as visit:
        for key in FILTER_KEYS:
            visit.page.click(f'#case-filters button[data-filter="{key}"]')
            counts[key] = visit.page.text_content("#case-count")
        visit.page.click('#case-filters button[data-filter="all"]')
        visit.page.click("#case-more")
        open_first_dangerous_case(visit.page)
        yield visit, counts


@dataclass(frozen=True)
class RowStates:
    case_id: str
    expanded_after_enter: str
    detail_hidden_after_enter: bool
    tools_shown: list[str]
    expanded_after_second_enter: str
    detail_hidden_after_second_enter: bool


@pytest.fixture(scope="module")
def row_states(browser: Any, demo_path: Path) -> RowStates:
    with visiting(browser, demo_path) as visit:
        page = visit.page
        page.click('#case-filters button[data-filter="dangerous"]')
        toggle = page.locator(".row-toggle").first
        toggle.focus()
        page.keyboard.press("Enter")
        # The toggle names its detail row only once that row exists, after the first open.
        detail = page.locator(f"#{toggle.get_attribute('aria-controls')}")
        opened = (toggle.get_attribute("aria-expanded"), detail.is_hidden())
        tools = detail.locator(".call-tool").all_text_contents()
        page.keyboard.press("Enter")
        return RowStates(
            case_id=toggle.text_content(),
            expanded_after_enter=opened[0],
            detail_hidden_after_enter=opened[1],
            tools_shown=tools,
            expanded_after_second_enter=toggle.get_attribute("aria-expanded"),
            detail_hidden_after_second_enter=detail.is_hidden(),
        )


@dataclass(frozen=True)
class Layout:
    scroll_width: int
    client_width: int


@pytest.fixture(scope="module")
def screenshot_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("screenshots")


@pytest.fixture(scope="module", params=WIDTHS, ids=[f"{width}px" for width in WIDTHS])
def layout(
    request: pytest.FixtureRequest, browser: Any, demo_path: Path, screenshot_folder: Path
) -> Layout:
    width = request.param
    with visiting(browser, demo_path, width=width) as visit:
        open_first_dangerous_case(visit.page)
        sizes = visit.page.evaluate(
            "() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]"
        )
        shot = screenshot_folder / f"demo-{width}px.png"
        visit.page.screenshot(path=str(shot), full_page=True)
    print(f"\nscreenshot at {width} px: {shot}")
    return Layout(*sizes)


@dataclass(frozen=True)
class HostileState:
    payload: str
    dialogs: list[str]
    script_types: list[str]
    image_count: int
    body_text: str
    csp_violations: list[str]
    console_errors: list[str]


@pytest.fixture(scope="module", params=PAYLOADS, ids=PAYLOAD_IDS)
def hostile_state(
    request: pytest.FixtureRequest, browser: Any, tmp_path_factory: pytest.TempPathFactory
) -> HostileState:
    folder = tmp_path_factory.mktemp("hostile")
    config_path = write_hostile_run(folder, request.param)
    path = folder / "hostile.html"
    write_dashboard(
        render_dashboard(run_check(load_run_config(config_path), config_path).results), path
    )
    with visiting(browser, path) as visit:
        page = visit.page
        # Hovering and opening every row brings each script-built string onto the page.
        for toggle in page.locator(".row-toggle").all():
            toggle.hover()
            toggle.click()
        return HostileState(
            payload=request.param,
            dialogs=visit.dialogs,
            script_types=page.evaluate("Array.from(document.scripts, s => s.type)"),
            image_count=page.evaluate("document.images.length"),
            body_text=page.inner_text("body"),
            csp_violations=visit.csp_violations(),
            console_errors=visit.console_errors,
        )


# Accessibility


def test_axe_finds_no_wcag_22_aa_violation(axe_violations: list[Any]) -> None:
    print(json.dumps(axe_violations, indent=2))
    assert [violation["id"] for violation in axe_violations] == []


# Keyboard


def test_tab_starts_at_the_skip_link(tab_walk: list[tuple[str, bool]]) -> None:
    assert tab_walk[0][0] == "skip link"


@pytest.mark.parametrize(
    "target",
    [
        "skip link",
        "coverage link",
        "banner link",
        *(f"filter {key}" for key in FILTER_KEYS),
        "row toggle 0",
        "show more",
    ],
)
def test_tab_reaches(tab_walk: list[tuple[str, bool]], target: str) -> None:
    assert target in [name for name, _ in tab_walk]


def test_tab_reaches_every_shown_row_toggle(tab_walk: list[tuple[str, bool]]) -> None:
    assert sum(1 for name, _ in tab_walk if name.startswith("row toggle ")) == PAGE_SIZE


def test_every_focused_element_shows_a_focus_ring(tab_walk: list[tuple[str, bool]]) -> None:
    assert [name for name, has_ring in tab_walk if not has_ring] == []


# Filters and rows


@pytest.mark.parametrize("key", FILTER_KEYS)
def test_a_filter_sets_the_count_line(demo_visit: tuple[Visit, dict[str, str]], key: str) -> None:
    total = count_matching(key)
    noun = "case" if total == 1 else "cases"
    assert demo_visit[1][key] == f"Showing {min(total, PAGE_SIZE)} of {total} matching {noun}."


def test_enter_marks_the_row_expanded(row_states: RowStates) -> None:
    assert row_states.expanded_after_enter == "true"


def test_enter_shows_the_detail_row(row_states: RowStates) -> None:
    assert row_states.detail_hidden_after_enter is False


def test_the_detail_row_lists_the_case_tool_calls(row_states: RowStates) -> None:
    detail = next(d for d in DEMO_RESULTS["case_detail"] if d["case_id"] == row_states.case_id)
    assert row_states.tools_shown == [to_visible_text(call["tool"]) for call in detail["calls"]]


def test_a_second_enter_marks_the_row_collapsed(row_states: RowStates) -> None:
    assert row_states.expanded_after_second_enter == "false"


def test_a_second_enter_hides_the_detail_row(row_states: RowStates) -> None:
    assert row_states.detail_hidden_after_second_enter is True


# Layout


def test_the_page_never_scrolls_sideways(layout: Layout) -> None:
    assert layout.scroll_width <= layout.client_width


# CSP and network


def test_the_demo_reports_no_csp_violation(demo_visit: tuple[Visit, dict[str, str]]) -> None:
    assert demo_visit[0].csp_violations() == []


def test_the_demo_logs_no_console_error(demo_visit: tuple[Visit, dict[str, str]]) -> None:
    assert demo_visit[0].console_errors == []


def test_the_demo_requests_nothing_but_itself(demo_visit: tuple[Visit, dict[str, str]]) -> None:
    assert demo_visit[0].requests == []


def test_the_violation_listener_catches_an_inline_style(browser: Any, tmp_path: Path) -> None:
    html = render_dashboard(DEMO_RESULTS).replace(
        '<main id="main"', '<main style="color:red" id="main"', 1
    )
    path = tmp_path / "styled.html"
    path.write_text(html, encoding="utf-8")
    with visiting(browser, path) as visit:
        assert visit.csp_violations() != []


def test_the_request_log_catches_a_remote_image(browser: Any, tmp_path: Path) -> None:
    html = re.sub(
        r'<meta http-equiv="Content-Security-Policy"[^>]*>', "", render_dashboard(DEMO_RESULTS)
    )
    html = html.replace("</footer>", '<img src="https://example.com/x.png" alt=""></footer>', 1)
    path = tmp_path / "remote.html"
    path.write_text(html, encoding="utf-8")
    with visiting(browser, path) as visit:
        assert visit.requests == ["https://example.com/x.png"]


# Hostile content


def test_a_hostile_page_opens_no_dialog(hostile_state: HostileState) -> None:
    assert hostile_state.dialogs == []


def test_a_hostile_page_adds_no_script(hostile_state: HostileState) -> None:
    assert hostile_state.script_types == ["application/json", ""]


def test_a_hostile_page_adds_no_image(hostile_state: HostileState) -> None:
    assert hostile_state.image_count == 0


def test_a_hostile_page_reports_no_csp_violation(hostile_state: HostileState) -> None:
    assert hostile_state.csp_violations == []


def test_a_hostile_page_logs_no_console_error(hostile_state: HostileState) -> None:
    assert hostile_state.console_errors == []


@pytest.mark.parametrize("field_name", ["case", "class", "version", "tool", "arguments", "item"])
def test_a_hostile_value_shows_as_text(hostile_state: HostileState, field_name: str) -> None:
    value = planted_fields(hostile_state.payload)[field_name]
    assert to_visible_text(value) in hostile_state.body_text


# Scale


@pytest.fixture(scope="module")
def scale_page(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    folder = tmp_path_factory.mktemp("scale")
    config_path = write_scale_dataset(folder)
    run = run_check(load_run_config(config_path), config_path)
    path = folder / "dashboard.html"
    write_dashboard(render_dashboard(run.results), path)
    print(f"\n{run.case_count} cases, {path.stat().st_size / 1_000_000:.2f} MB of HTML")
    return path, run.case_count


def test_the_50000_case_page_renders_its_first_view(
    browser: Any, scale_page: tuple[Path, int]
) -> None:
    path, case_count = scale_page
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    started = time.perf_counter()
    page.goto(path.as_uri(), wait_until="commit")
    page.wait_for_function(
        "() => document.getElementById('case-count')?.textContent",
        polling="raf",
        timeout=FIRST_RENDER_TIMEOUT_MS,
    )
    elapsed = time.perf_counter() - started
    count_text = page.text_content("#case-count")
    context.close()
    # Reported, not gated: the time depends on the machine.
    print(f"\n50,000-case page: count line shown {elapsed:.2f} s after navigation started")
    assert count_text == f"Showing {PAGE_SIZE} of {case_count:,} matching cases."
