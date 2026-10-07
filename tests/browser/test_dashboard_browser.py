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

from detecttrace.dashboard import render_dashboard, render_waiting_page, write_dashboard
from detecttrace.dashboard_view import build_view
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.served_page import ServedPage, WaitingCounts
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
DEMO_VIEW = build_view(DEMO_RESULTS)
# The registered pages, in navigation order: (path, title).
PAGES = [
    ("/", "Overview"),
    ("/versions", "Versions"),
    ("/skipped", "Skipped steps"),
    ("/trends", "Weekly trend"),
    ("/verdicts", "Verdict matrix"),
    ("/cases", "Cases"),
    ("/data", "Data"),
    ("/limits", "Limits"),
    ("/help", "Help"),
]
PAGE_IDS = [title for _, title in PAGES]
WIDTHS = [1280, 360]
FIRST_RENDER_TIMEOUT_MS = 180_000
CASE_COUNT = len(DEMO_RESULTS["case_rows"]["columns"]["case_id"])
CLASS_ANCHORS = [class_view.anchor for class_view in DEMO_VIEW.classes]
CLASS_NAMES = [class_view.name for class_view in DEMO_VIEW.classes]

# Registered before any page script runs, so a violation during parsing is caught too.
WATCH_SCRIPT = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", function (event) {
  window.__cspViolations.push(
    event.violatedDirective + " blocked " + (event.blockedURI || "inline") + ": " + event.sample
  );
}, true);
"""

# A ring counts only if no clip-path cuts it: the element's own clip-path cuts any ring drawn
# outside it, and an ancestor's cuts a ring that reaches past the ancestor's box. An element in
# the top layer, such as an open popover, is drawn outside its ancestors and escapes their clip-paths.
FOCUS_PROBE = """() => {
  const el = document.activeElement;
  const style = getComputedStyle(el);
  const outset = parseFloat(style.outlineOffset) + parseFloat(style.outlineWidth);
  const isClipped = (() => {
    const ring = el.getBoundingClientRect();
    for (let node = el; node instanceof Element;
         node = node.matches(":popover-open, :modal") ? null : node.parentElement) {
      if (getComputedStyle(node).clipPath === "none") continue;
      if (node === el) {
        if (outset > 0) return true;
        continue;
      }
      const box = node.getBoundingClientRect();
      if (ring.left - outset < box.left || ring.top - outset < box.top
          || ring.right + outset > box.right || ring.bottom + outset > box.bottom) return true;
    }
    return false;
  })();
  // A trend point's mark is the ring drawn around it, not an outline.
  const ring = el.querySelector(".trend-point-ring");
  const hasRing = ((style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0)
    || style.boxShadow !== "none"
    || (ring !== null && getComputedStyle(ring).stroke !== "none"
        && getComputedStyle(ring).stroke !== "rgba(0, 0, 0, 0)")) && !isClipped;
  let name;
  if (el === document.body) name = "body";
  else if (el.matches(".skip-link")) name = "skip link";
  else if (el.matches(".sidebar-link")) name = "page " + el.textContent.trim();
  else if (el.matches(".overview-alert a")) name = "coverage link";
  else if (el.matches('[role="tab"]')) name = "class tab " + el.textContent.trim();
  else if (el.matches(".case-toggle")) name = "case " + el.textContent.trim();
  else if (el.matches(".trend-point")) name = "trend point";
  else if (el.matches("select, input")) name = "filter " + el.labels[0].textContent.trim();
  else name = el.tagName.toLowerCase() + "." + (el.getAttribute("class") || "")
    + " " + (el.getAttribute("aria-label") || el.id);
  return [name, hasRing];
}"""


@dataclass
class Visit:
    page: Any
    url: str
    console_errors: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)

    def csp_violations(self) -> list[str]:
        return self.page.evaluate("window.__cspViolations")

    def open(self, title: str) -> None:
        """Open a page as a reader would, by its sidebar link."""
        self.page.get_by_role("navigation", name="Pages").get_by_role(
            "link", name=title, exact=True
        ).click()
        # The app names the page in the tab title once it has rendered.
        self.page.wait_for_function("title => document.title.startsWith(title + ' ·')", arg=title)


@contextmanager
def visiting(
    browser: Any, path: Path, *, hash: str = "", width: int = 1280, bypass_csp: bool = False
) -> Iterator[Visit]:
    """Open `path` with every request logged, and every one other than the page itself blocked."""
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
    visit = Visit(page, url)

    def log_console(message: Any) -> None:
        if message.type == "error":
            visit.console_errors.append(message.text)

    def dismiss_dialog(dialog: Any) -> None:
        visit.dialogs.append(f"{dialog.type}: {dialog.message}")
        dialog.dismiss()

    page.on("request", lambda request: visit.requests.append(request.url))
    page.on("console", log_console)
    page.on("pageerror", lambda error: visit.console_errors.append(str(error)))
    page.on("dialog", dismiss_dialog)
    try:
        page.goto(url + hash)
        page.wait_for_selector("h1")
        yield visit
    finally:
        context.close()


def count_matching(result: str = "all", class_anchor: str | None = None) -> int:
    """The cases a filter should match, computed from the results as the metrics do."""
    columns = DEMO_RESULTS["case_rows"]["columns"]
    codes = DEMO_RESULTS["case_rows"]["verdict_codes"]
    unknown = DEMO_RESULTS["case_rows"]["unknown_verdict_code"]
    class_index = None
    if class_anchor is not None:
        class_index = next(
            f.class_index for f in DEMO_VIEW.cases.class_filters if f.anchor == class_anchor
        )
    rows = zip(columns["analyst"], columns["agent"], columns["class_index"], strict=True)
    return sum(
        1
        for analyst, agent, index in rows
        if (class_index is None or index == class_index)
        and (
            result == "all"
            or (result == "disagree" and unknown not in (analyst, agent) and analyst != agent)
            or (
                result == "dangerous"
                and unknown not in (analyst, agent)
                and codes[analyst] == "true_positive"
                and codes[agent] in ("false_positive", "benign")
            )
        )
    )


def to_count_line(matching: int) -> str:
    return f"Showing {matching:,} of {CASE_COUNT:,} cases."


def read_hash_query(page: Any) -> dict[str, str]:
    query = page.evaluate("location.hash").partition("?")[2]
    return dict(pair.split("=", 1) for pair in query.split("&") if pair)


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
    """The demo with a low-coverage warning, so the overview carries its link, and the other
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
def seven_classes_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The demo with five more classes, past the six a row of tabs holds."""
    results = copy.deepcopy(DEMO_RESULTS)
    for number in range(5):
        extra = copy.deepcopy(DEMO_RESULTS["classes"][0])
        extra["alert_class"] = f"extra_class_{number}"
        results["classes"].append(extra)
        results["case_rows"]["strings"].append(extra["alert_class"])
    path = tmp_path_factory.mktemp("browser") / "seven_classes.html"
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
) -> dict[str, list[Any]]:
    """The violated rules on every page, with a dangerous case open on the cases page."""
    path = demo_path if request.param == "demo" else low_coverage_path
    violations: dict[str, list[Any]] = {}
    # The page's CSP rightly blocks the injected axe script; only this run lifts it.
    with visiting(browser, path, bypass_csp=True) as visit:
        visit.page.add_script_tag(content=axe_source)
        for path_name, title in PAGES:
            visit.open(title)
            if path_name == "/cases":
                visit.page.select_option(".case-filters select >> nth=1", "dangerous")
                visit.page.click(".case-toggle >> nth=0")
            results = visit.page.evaluate(
                "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", WCAG_TAGS
            )
            print(
                f"\naxe-core {AXE_VERSION} on {request.param}, {title}: "
                f"{len(results['passes'])} rules passed, {len(results['violations'])} violated, "
                f"{len(results['incomplete'])} need review"
            )
            violations[title] = results["violations"]
    return violations


@pytest.fixture(scope="module", params=PAGES, ids=PAGE_IDS)
def tab_walk(
    request: pytest.FixtureRequest, browser: Any, low_coverage_path: Path
) -> list[tuple[str, bool]]:
    """Each element Tab reaches from the top of a page until it leaves the page, and whether
    it shows a focus ring."""
    walk: list[tuple[str, bool]] = []
    with visiting(browser, low_coverage_path, hash=f"#{request.param[0]}") as visit:
        for _ in range(600):
            visit.page.keyboard.press("Tab")
            name, has_ring = visit.page.evaluate(FOCUS_PROBE)
            if name == "body":
                break
            walk.append((name, has_ring))
    return walk


@pytest.fixture(scope="module")
def demo_visit(browser: Any, demo_path: Path) -> Iterator[tuple[Visit, dict[str, str]]]:
    """The demo after opening every page, using every case filter and opening a case, with
    the count line per filter."""
    counts: dict[str, str] = {}
    with visiting(browser, demo_path) as visit:
        page = visit.page
        for _, title in PAGES:
            visit.open(title)
        visit.open("Cases")
        for result in ("all", "disagree", "dangerous"):
            page.select_option(".case-filters select >> nth=1", result)
            counts[result] = page.text_content(".cases-count")
        page.select_option(".case-filters select >> nth=1", "all")
        for anchor in CLASS_ANCHORS:
            page.select_option(".case-filters select >> nth=0", anchor)
            counts[anchor] = page.text_content(".cases-count")
        page.select_option(".case-filters select >> nth=0", index=0)
        page.fill(".case-filters input", "dt-it-00")
        counts["search"] = page.text_content(".cases-count")
        page.fill(".case-filters input", "")
        page.select_option(".case-filters select >> nth=1", "dangerous")
        page.click(".case-toggle >> nth=0")
        page.wait_for_selector(".case-detail-row")
        yield visit, counts


@dataclass(frozen=True)
class RowStates:
    case_id: str
    expanded_after_enter: str
    detail_visible_after_enter: bool
    tools_shown: list[str]
    expanded_after_second_enter: str
    detail_rows_after_second_enter: int


@pytest.fixture(scope="module")
def row_states(browser: Any, demo_path: Path) -> RowStates:
    with visiting(browser, demo_path, hash="#/cases?result=dangerous") as visit:
        page = visit.page
        toggle = page.locator(".case-toggle").first
        toggle.focus()
        page.keyboard.press("Enter")
        # aria-controls names the detail row only while it is open, so it exists.
        detail = page.locator(f'[id="{toggle.get_attribute("aria-controls")}"]')
        opened = (toggle.get_attribute("aria-expanded"), detail.is_visible())
        tools = detail.locator(".case-call-tool").all_text_contents()
        page.keyboard.press("Enter")
        return RowStates(
            case_id=toggle.text_content(),
            expanded_after_enter=opened[0],
            detail_visible_after_enter=opened[1],
            tools_shown=tools,
            expanded_after_second_enter=toggle.get_attribute("aria-expanded"),
            detail_rows_after_second_enter=page.locator(".case-detail-row").count(),
        )


@pytest.fixture(scope="module")
def case_tab_order(browser: Any, demo_path: Path) -> list[int]:
    """The row index of each case button Tab reaches from the first, until it leaves them."""
    order: list[int] = []
    with visiting(browser, demo_path, hash="#/cases") as visit:
        visit.page.focus(".case-toggle >> nth=0")
        for _ in range(CASE_COUNT + 5):
            index = visit.page.evaluate(
                "() => document.activeElement.matches('.case-toggle')"
                " ? Number(document.activeElement.closest('tr').getAttribute('aria-rowindex'))"
                " : null"
            )
            if index is None:
                break
            order.append(index)
            visit.page.keyboard.press("Tab")
    return order


@dataclass(frozen=True)
class SkipState:
    focused: str  # the focused element's tag and text
    hash: str


@pytest.fixture(scope="module")
def skip_state(browser: Any, demo_path: Path) -> SkipState:
    with visiting(browser, demo_path, hash="#/versions") as visit:
        visit.page.keyboard.press("Tab")
        visit.page.keyboard.press("Enter")
        return SkipState(
            visit.page.evaluate(
                "`${document.activeElement.tagName} ${document.activeElement.textContent}`"
            ),
            visit.page.evaluate("location.hash"),
        )


@dataclass(frozen=True)
class SidebarState:
    hash: str
    focused_text: str


@pytest.fixture(scope="module")
def sidebar_state(browser: Any, demo_path: Path) -> SidebarState:
    with visiting(browser, demo_path) as visit:
        link = visit.page.get_by_role("navigation", name="Pages").get_by_role(
            "link", name="Cases", exact=True
        )
        link.focus()
        visit.page.keyboard.press("Enter")
        visit.page.wait_for_function("() => document.activeElement.tagName === 'H1'")
        return SidebarState(
            visit.page.evaluate("location.hash"),
            visit.page.evaluate("document.activeElement.textContent"),
        )


@dataclass(frozen=True)
class TabKeys:
    after_right: tuple[str, str, str]  # focused tab, selected tab, hash class
    after_second_right: str  # focused tab
    after_end: str
    after_home: str


@pytest.fixture(scope="module")
def tab_keys(browser: Any, demo_path: Path) -> TabKeys:
    def focused_tab() -> str:
        return page.evaluate("document.activeElement.textContent")

    with visiting(browser, demo_path, hash="#/versions") as visit:
        page = visit.page
        page.focus('[role="tab"][aria-selected="true"]')
        page.keyboard.press("ArrowRight")
        after_right = (
            focused_tab(),
            page.text_content('[role="tab"][aria-selected="true"]'),
            read_hash_query(page).get("class", ""),
        )
        page.keyboard.press("ArrowRight")
        after_second_right = focused_tab()
        page.keyboard.press("End")
        after_end = focused_tab()
        page.keyboard.press("Home")
        return TabKeys(after_right, after_second_right, after_end, focused_tab())


@dataclass(frozen=True)
class TrendKeys:
    first: str
    after_right: str
    after_down: str
    tab_stops: int
    readout: str


@pytest.fixture(scope="module")
def trend_keys(browser: Any, demo_path: Path) -> TrendKeys:
    def focused() -> str:
        return page.evaluate("document.activeElement.getAttribute('aria-label')")

    with visiting(browser, demo_path, hash="#/trends") as visit:
        page = visit.page
        chart = page.locator(".trend-chart").first
        chart.locator('.trend-point[tabindex="0"]').focus()
        first = focused()
        page.keyboard.press("ArrowRight")
        after_right = focused()
        page.keyboard.press("ArrowDown")
        return TrendKeys(
            first=first,
            after_right=after_right,
            after_down=focused(),
            tab_stops=chart.locator('.trend-point[tabindex="0"]').count(),
            readout=chart.locator(".trend-chart-readout").text_content(),
        )


@dataclass(frozen=True)
class Reloaded:
    query_before: dict[str, str]
    query_after: dict[str, str]
    controls_before: list[str]
    controls_after: list[str]
    count_before: str
    count_after: str
    selected_tab_after_page_change: str
    selected_tab_after_reload: str


@pytest.fixture(scope="module")
def reloaded(browser: Any, demo_path: Path) -> Reloaded:
    def read_controls() -> list[str]:
        return page.eval_on_selector_all(
            ".case-filters select, .case-filters input", "els => els.map(e => e.value)"
        )

    with visiting(browser, demo_path, hash="#/cases") as visit:
        page = visit.page
        page.select_option(".case-filters select >> nth=0", CLASS_ANCHORS[0])
        page.select_option(".case-filters select >> nth=1", "disagree")
        page.fill(".case-filters input", "DT-IT")
        query_before, controls_before = read_hash_query(page), read_controls()
        count_before = page.text_content(".cases-count")
        page.reload()
        page.wait_for_selector(".cases-count")
        query_after, controls_after = read_hash_query(page), read_controls()
        count_after = page.text_content(".cases-count")
        visit.open("Versions")
        page.click(f'[role="tab"]:has-text("{CLASS_NAMES[1]}")')
        visit.open("Weekly trend")
        tab_after_page_change = page.text_content('[role="tab"][aria-selected="true"]')
        page.reload()
        page.wait_for_selector('[role="tab"]')
        return Reloaded(
            query_before,
            query_after,
            controls_before,
            controls_after,
            count_before,
            count_after,
            tab_after_page_change,
            page.text_content('[role="tab"][aria-selected="true"]'),
        )


@dataclass(frozen=True)
class ManyClasses:
    tab_count: int
    option_labels: list[str]
    panel_label: str
    hash_class: str


@pytest.fixture(scope="module")
def many_classes(browser: Any, seven_classes_path: Path) -> ManyClasses:
    with visiting(browser, seven_classes_path, hash="#/versions") as visit:
        page = visit.page
        select = page.get_by_label("Alert class", exact=True)
        labels = select.locator("option").all_text_contents()
        select.select_option(label="extra_class_4")
        return ManyClasses(
            tab_count=page.locator('[role="tab"]').count(),
            option_labels=labels,
            panel_label=page.get_attribute(
                f'[id="{select.get_attribute("aria-controls")}"]', "aria-label"
            ),
            hash_class=read_hash_query(page).get("class", ""),
        )


@dataclass(frozen=True)
class Layout:
    scroll_width: int
    client_width: int


@pytest.fixture(scope="module")
def screenshot_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("screenshots")


@pytest.fixture(scope="module", params=WIDTHS, ids=[f"{width}px" for width in WIDTHS])
def layouts(
    request: pytest.FixtureRequest, browser: Any, demo_path: Path, screenshot_folder: Path
) -> dict[str, Layout]:
    width = request.param
    layouts: dict[str, Layout] = {}
    with visiting(browser, demo_path, width=width) as visit:
        for path_name, title in PAGES:
            visit.open(title)
            if path_name == "/cases":
                visit.page.select_option(".case-filters select >> nth=1", "dangerous")
                visit.page.click(".case-toggle >> nth=0")
            sizes = visit.page.evaluate(
                "() => [document.scrollingElement.scrollWidth,"
                " document.scrollingElement.clientWidth]"
            )
            layouts[title] = Layout(*sizes)
            shot = screenshot_folder / f"demo-{path_name.strip('/') or 'overview'}-{width}px.png"
            visit.page.screenshot(path=str(shot), full_page=True)
    print(f"\nscreenshots at {width} px: {screenshot_folder}")
    return layouts


@dataclass(frozen=True)
class HostileState:
    payload: str
    dialogs: list[str]
    script_types: list[str]
    image_count: int
    page_text: str
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
    texts: list[str] = []
    with visiting(browser, path) as visit:
        page = visit.page
        for path_name, title in PAGES:
            visit.open(title)
            if path_name == "/cases":
                # Hovering and opening every row brings each script-built string onto the page.
                for toggle in page.locator(".case-toggle").all():
                    toggle.hover()
                    toggle.click()
            texts.append(page.inner_text("body"))
        return HostileState(
            payload=request.param,
            dialogs=visit.dialogs,
            script_types=page.evaluate("Array.from(document.scripts, s => s.type)"),
            image_count=page.evaluate("document.images.length"),
            page_text="\n".join(texts),
            csp_violations=visit.csp_violations(),
            console_errors=visit.console_errors,
        )


@dataclass(frozen=True)
class WaitingState:
    title: str
    counts: dict[str, str]
    page_links: list[str]
    requests: list[str]
    url: str
    csp_violations: list[str]
    console_errors: list[str]


@pytest.fixture(scope="module")
def waiting_state(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> WaitingState:
    path = tmp_path_factory.mktemp("waiting") / "waiting.html"
    counts = WaitingCounts(span_count=1200, case_count=0, held_back_count=3, verdict_count=5)
    write_dashboard(render_waiting_page(counts, [], ServedPage(0, "", 0)), path)
    with visiting(browser, path) as visit:
        page = visit.page
        terms = page.locator(".waiting-counts dt").all_text_contents()
        values = page.locator(".waiting-counts dd").all_text_contents()
        return WaitingState(
            title=page.text_content("h1"),
            counts=dict(zip(terms, values, strict=True)),
            page_links=page.locator(".sidebar-link").all_text_contents(),
            requests=visit.requests,
            url=visit.url,
            csp_violations=visit.csp_violations(),
            console_errors=visit.console_errors,
        )


# Accessibility


@pytest.mark.parametrize("title", PAGE_IDS)
def test_axe_finds_no_wcag_22_aa_violation(
    axe_violations: dict[str, list[Any]], title: str
) -> None:
    print(json.dumps(axe_violations[title], indent=2))
    assert [violation["id"] for violation in axe_violations[title]] == []


# Keyboard: skip link and sidebar


def test_tab_starts_at_the_skip_link(tab_walk: list[tuple[str, bool]]) -> None:
    assert tab_walk[0][0] == "skip link"


@pytest.mark.parametrize("title", PAGE_IDS)
def test_tab_reaches_every_page_link(tab_walk: list[tuple[str, bool]], title: str) -> None:
    assert f"page {title}" in [name for name, _ in tab_walk]


def test_every_focused_element_shows_a_focus_ring(tab_walk: list[tuple[str, bool]]) -> None:
    assert [name for name, has_ring in tab_walk if not has_ring] == []


@pytest.mark.parametrize(
    ("hash", "target"),
    [
        ("#/", "coverage link"),
        ("#/", f"class tab {CLASS_NAMES[0]}"),
        ("#/versions", f"class tab {CLASS_NAMES[0]}"),
        ("#/trends", "trend point"),
        ("#/cases", "filter Alert class"),
        ("#/cases", "filter Result"),
        ("#/cases", "filter Search case ID"),
    ],
)
def test_tab_reaches(browser: Any, low_coverage_path: Path, hash: str, target: str) -> None:
    names: list[str] = []
    with visiting(browser, low_coverage_path, hash=hash) as visit:
        for _ in range(40):
            visit.page.keyboard.press("Tab")
            names.append(visit.page.evaluate(FOCUS_PROBE)[0])
    assert target in names


def test_the_skip_link_moves_focus_to_the_page_heading(skip_state: SkipState) -> None:
    assert skip_state.focused == "H1 By version"


def test_the_skip_link_keeps_the_route(skip_state: SkipState) -> None:
    assert skip_state.hash == "#/versions"


def test_enter_on_a_page_link_opens_the_page(sidebar_state: SidebarState) -> None:
    assert sidebar_state.hash == "#/cases"


def test_a_new_page_takes_focus_at_its_heading(sidebar_state: SidebarState) -> None:
    assert sidebar_state.focused_text == "Cases"


# Keyboard: class tabs


def test_arrow_right_moves_focus_to_the_next_class_tab(tab_keys: TabKeys) -> None:
    assert tab_keys.after_right[0] == CLASS_NAMES[1]


def test_arrow_right_selects_the_next_class(tab_keys: TabKeys) -> None:
    assert tab_keys.after_right[1] == CLASS_NAMES[1]


def test_arrow_right_puts_the_class_in_the_hash(tab_keys: TabKeys) -> None:
    assert tab_keys.after_right[2] == CLASS_ANCHORS[1]


def test_arrow_right_on_the_last_class_tab_wraps_to_the_first(tab_keys: TabKeys) -> None:
    assert tab_keys.after_second_right == CLASS_NAMES[0]


def test_end_moves_to_the_last_class_tab(tab_keys: TabKeys) -> None:
    assert tab_keys.after_end == CLASS_NAMES[-1]


def test_home_moves_to_the_first_class_tab(tab_keys: TabKeys) -> None:
    assert tab_keys.after_home == CLASS_NAMES[0]


# More classes than tabs


def test_more_than_six_classes_show_no_tabs(many_classes: ManyClasses) -> None:
    assert many_classes.tab_count == 0


def test_more_than_six_classes_are_offered_in_a_drop_down(many_classes: ManyClasses) -> None:
    assert many_classes.option_labels == [
        *CLASS_NAMES,
        *(f"extra_class_{number}" for number in range(5)),
    ]


def test_choosing_a_class_in_the_drop_down_names_the_panel(many_classes: ManyClasses) -> None:
    assert many_classes.panel_label == "extra_class_4"


def test_choosing_a_class_in_the_drop_down_puts_it_in_the_hash(many_classes: ManyClasses) -> None:
    assert many_classes.hash_class != ""


# Keyboard: trend points


def test_the_first_trend_point_is_the_first_week_of_the_first_line(trend_keys: TrendKeys) -> None:
    assert trend_keys.first.startswith("2026-W32, All versions:")


def test_arrow_right_moves_to_the_next_week(trend_keys: TrendKeys) -> None:
    assert trend_keys.after_right.startswith("2026-W33, All versions:")


def test_arrow_down_moves_to_the_next_line(trend_keys: TrendKeys) -> None:
    assert not trend_keys.after_down.startswith("2026-W33, All versions:")


def test_a_chart_keeps_one_tab_stop(trend_keys: TrendKeys) -> None:
    assert trend_keys.tab_stops == 1


def test_the_readout_names_the_focused_point(trend_keys: TrendKeys) -> None:
    assert trend_keys.readout == trend_keys.after_down


# Filters and rows


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("all", count_matching()),
        ("disagree", count_matching("disagree")),
        ("dangerous", count_matching("dangerous")),
        *((anchor, count_matching(class_anchor=anchor)) for anchor in CLASS_ANCHORS),
        (
            "search",
            sum(
                1 for case in DEMO_RESULTS["case_rows"]["columns"]["case_id"] if "DT-IT-00" in case
            ),
        ),
    ],
)
def test_a_filter_sets_the_count_line(
    demo_visit: tuple[Visit, dict[str, str]], key: str, expected: int
) -> None:
    assert demo_visit[1][key] == to_count_line(expected)


def test_tab_reaches_every_case_in_order(case_tab_order: list[int]) -> None:
    # Row index 1 is the header row.
    assert case_tab_order == list(range(2, CASE_COUNT + 2))


def test_enter_marks_the_row_expanded(row_states: RowStates) -> None:
    assert row_states.expanded_after_enter == "true"


def test_enter_shows_the_detail_row(row_states: RowStates) -> None:
    assert row_states.detail_visible_after_enter is True


def test_the_detail_row_lists_the_case_tool_calls(row_states: RowStates) -> None:
    detail = next(d for d in DEMO_RESULTS["case_detail"] if d["case_id"] == row_states.case_id)
    assert row_states.tools_shown == [to_visible_text(call["tool"]) for call in detail["calls"]]


def test_a_second_enter_marks_the_row_collapsed(row_states: RowStates) -> None:
    assert row_states.expanded_after_second_enter == "false"


def test_a_second_enter_removes_the_detail_row(row_states: RowStates) -> None:
    assert row_states.detail_rows_after_second_enter == 0


# The hash keeps filters and the class


def test_the_filters_are_in_the_hash(reloaded: Reloaded) -> None:
    assert reloaded.query_before == {"class": CLASS_ANCHORS[0], "result": "disagree", "q": "DT-IT"}


def test_a_reload_keeps_the_hash(reloaded: Reloaded) -> None:
    assert reloaded.query_after == reloaded.query_before


def test_a_reload_keeps_the_filter_controls(reloaded: Reloaded) -> None:
    assert reloaded.controls_after == reloaded.controls_before


def test_a_reload_keeps_the_count_line(reloaded: Reloaded) -> None:
    assert reloaded.count_after == reloaded.count_before


def test_another_page_keeps_the_selected_class(reloaded: Reloaded) -> None:
    assert reloaded.selected_tab_after_page_change == CLASS_NAMES[1]


def test_a_reload_keeps_the_selected_class(reloaded: Reloaded) -> None:
    assert reloaded.selected_tab_after_reload == CLASS_NAMES[1]


# Layout


@pytest.mark.parametrize("title", PAGE_IDS)
def test_the_page_never_scrolls_sideways(layouts: dict[str, Layout], title: str) -> None:
    assert layouts[title].scroll_width <= layouts[title].client_width


# Each element Tab reaches outside the navigation bar, with its top and bottom, and the bar's top.
BAR_PROBE = """() => {
  const el = document.activeElement;
  const box = el.getBoundingClientRect();
  return [el !== document.body && el.closest(".sidebar") === null,
          el.getAttribute("class") || el.tagName, box.top, box.bottom,
          document.querySelector(".sidebar").getBoundingClientRect().top];
}"""


@pytest.fixture(scope="module")
def narrow_focus_walk(browser: Any, demo_path: Path) -> list[tuple[str, float, float, float]]:
    """Tab through the overview on a 390px phone: each focused element outside the navigation
    bar and where it sits."""
    walk: list[tuple[str, float, float, float]] = []
    with visiting(browser, demo_path, width=390) as visit:
        for _ in range(30):
            visit.page.keyboard.press("Tab")
            is_outside, name, top, bottom, bar_top = visit.page.evaluate(BAR_PROBE)
            if is_outside:
                walk.append((name, top, bottom, bar_top))
    return walk


@pytest.mark.parametrize("name", ["class-tab", "version-table-scroll", "overview-tile"])
def test_tab_on_a_phone_reaches(
    narrow_focus_walk: list[tuple[str, float, float, float]], name: str
) -> None:
    assert name in [focused for focused, *_ in narrow_focus_walk]


def test_focus_on_a_phone_stays_clear_of_the_navigation_bar(
    narrow_focus_walk: list[tuple[str, float, float, float]],
) -> None:
    hidden = [
        name for name, top, bottom, bar_top in narrow_focus_walk if top < 0 or bottom > bar_top
    ]
    assert hidden == []


@pytest.mark.parametrize("width", [390, 360])
def test_each_dangerous_label_shows_its_whole_word(
    browser: Any, demo_path: Path, width: int
) -> None:
    with visiting(browser, demo_path, hash="#/verdicts", width=width) as visit:
        cut = visit.page.evaluate(
            """() => [...document.querySelectorAll(".matrix-flag")].filter((flag) => {
              const cell = flag.closest("td").getBoundingClientRect();
              const box = flag.getBoundingClientRect();
              return flag.scrollWidth > flag.clientWidth
                || box.left < cell.left || box.right > cell.right;
            }).length"""
        )
    assert cut == 0


def test_the_skipped_step_table_fits_its_panel_on_a_390px_phone(
    browser: Any, demo_path: Path
) -> None:
    with visiting(browser, demo_path, hash="#/skipped", width=390) as visit:
        overflow = visit.page.evaluate(
            """() => [...document.querySelectorAll(".heatmap-scroll")]
              .map((area) => area.scrollWidth - area.clientWidth)"""
        )
    assert overflow == [0]


def test_every_navigation_link_fits_a_320px_screen(browser: Any, demo_path: Path) -> None:
    with visiting(browser, demo_path, width=320) as visit:
        outside = visit.page.evaluate(
            """() => [...document.querySelectorAll(".sidebar-link")].filter((link) => {
              const box = link.getBoundingClientRect();
              return box.left < 0 || box.right > document.documentElement.clientWidth;
            }).length"""
        )
    assert outside == 0


def test_a_matrix_that_fits_is_not_a_tab_stop(browser: Any, demo_path: Path) -> None:
    with visiting(browser, demo_path, hash="#/verdicts") as visit:
        stops = visit.page.locator('.matrix-scroll[tabindex="0"]').count()
    assert stops == 0


def test_a_trend_chart_wider_than_a_phone_is_a_tab_stop(browser: Any, demo_path: Path) -> None:
    with visiting(browser, demo_path, hash="#/trends", width=390) as visit:
        stops = visit.page.locator('.trend-chart-scroll[role="region"][tabindex="0"]').count()
    assert stops == 2


# CSP and network


def test_the_demo_reports_no_csp_violation(demo_visit: tuple[Visit, dict[str, str]]) -> None:
    assert demo_visit[0].csp_violations() == []


def test_the_demo_logs_no_console_error(demo_visit: tuple[Visit, dict[str, str]]) -> None:
    assert demo_visit[0].console_errors == []


def test_the_demo_requests_nothing_but_itself(demo_visit: tuple[Visit, dict[str, str]]) -> None:
    assert demo_visit[0].requests == [demo_visit[0].url]


def test_the_violation_listener_catches_an_inline_style(browser: Any, tmp_path: Path) -> None:
    html = render_dashboard(DEMO_RESULTS).replace(
        '<div id="root">', '<div id="root" style="color:red">', 1
    )
    path = tmp_path / "styled.html"
    path.write_text(html, encoding="utf-8")
    with visiting(browser, path) as visit:
        assert visit.csp_violations() != []


def test_the_request_log_catches_a_remote_image(browser: Any, tmp_path: Path) -> None:
    html = re.sub(
        r'<meta http-equiv="Content-Security-Policy"[^>]*>', "", render_dashboard(DEMO_RESULTS)
    )
    html = html.replace("<noscript>", '<img src="https://example.com/x.png" alt=""><noscript>', 1)
    path = tmp_path / "remote.html"
    path.write_text(html, encoding="utf-8")
    with visiting(browser, path) as visit:
        assert visit.requests == [visit.url, "https://example.com/x.png"]


# Hostile content


def test_a_hostile_page_opens_no_dialog(hostile_state: HostileState) -> None:
    assert hostile_state.dialogs == []


def test_a_hostile_page_adds_no_script(hostile_state: HostileState) -> None:
    assert hostile_state.script_types == ["module", "application/json", "application/json"]


def test_a_hostile_page_adds_no_image(hostile_state: HostileState) -> None:
    assert hostile_state.image_count == 0


def test_a_hostile_page_reports_no_csp_violation(hostile_state: HostileState) -> None:
    assert hostile_state.csp_violations == []


def test_a_hostile_page_logs_no_console_error(hostile_state: HostileState) -> None:
    assert hostile_state.console_errors == []


@pytest.mark.parametrize(
    "field_name", ["case", "class", "version", "tool", "arguments", "item", "analyst", "agent"]
)
def test_a_hostile_value_shows_as_text(hostile_state: HostileState, field_name: str) -> None:
    value = planted_fields(hostile_state.payload)[field_name]
    assert to_visible_text(value) in hostile_state.page_text


# The waiting page


def test_the_waiting_page_says_nothing_can_be_scored_yet(waiting_state: WaitingState) -> None:
    assert waiting_state.title == "Nothing to score yet"


def test_the_waiting_page_shows_each_count_under_its_label(waiting_state: WaitingState) -> None:
    assert waiting_state.counts == {
        "Spans received": "1,200",
        "Cases settled": "0",
        "Cases still settling": "3",
        "Verdicts received": "5",
    }


def test_the_waiting_page_links_only_help(waiting_state: WaitingState) -> None:
    assert waiting_state.page_links == ["Help"]


def test_the_waiting_page_reports_no_csp_violation(waiting_state: WaitingState) -> None:
    assert waiting_state.csp_violations == []


def test_the_waiting_page_logs_no_console_error(waiting_state: WaitingState) -> None:
    assert waiting_state.console_errors == []


def test_the_waiting_page_requests_nothing_but_itself(waiting_state: WaitingState) -> None:
    # It asks its server for newer results only after 30 seconds.
    assert waiting_state.requests == [waiting_state.url]


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


def test_the_50000_case_page_renders_its_case_table(
    browser: Any, scale_page: tuple[Path, int]
) -> None:
    path, case_count = scale_page
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    started = time.perf_counter()
    page.goto(path.as_uri() + "#/cases", wait_until="commit")
    page.wait_for_function(
        "() => document.querySelector('.cases-count')?.textContent",
        polling="raf",
        timeout=FIRST_RENDER_TIMEOUT_MS,
    )
    elapsed = time.perf_counter() - started
    count_text = page.text_content(".cases-count")
    context.close()
    # Reported, not gated: the time depends on the machine.
    print(f"\n50,000-case page: count line shown {elapsed:.2f} s after navigation started")
    assert count_text == f"Showing {case_count:,} of {case_count:,} cases."
