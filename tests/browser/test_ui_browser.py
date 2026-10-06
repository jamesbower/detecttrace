"""The local app (`detecttrace ui`) in a real browser: upload, configure, results, clear.

Dev-only and run by hand, like the other browser checks:

    uv run --with playwright pytest -m browser -s -q tests/browser/test_ui_browser.py

One visit drives a real `detecttrace ui` process as a reader would: it uploads the demo files
through the page's file inputs, clears each card's results, maps the labels the proposal can't
place, confirms, waits for the page to reload with the results, and clears the data again.

The page asks for newer results every 5 seconds. The test pauses the page's clock once the
page has loaded, so the configured page can be checked before it reloads, and moves the clock
forward one poll once the server has results, so the page's script runs exactly as in
production. axe waits on timers, so the clock runs while axe does and only then.
"""

import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from page_addresses import READ_ADDRESS, AddressWalk, walk_page_addresses
from serve.served_process import DEMO_FOLDER, ServedProcess, start_ui
from serve.test_ui_parity import DEMO_LABEL_CHOICES
from test_dashboard_browser import WCAG_TAGS, axe_source  # noqa: F401 (a fixture)

from detecttrace.dashboard import render_dashboard, write_dashboard
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config

# Playwright is not a project dependency, so Pyright can't see its types; pages and
# browsers are typed `Any` here.
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed; run `uv run --with playwright pytest -m browser tests/browser`",
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(sys.platform == "win32", reason="stops the server with SIGTERM"),
]

DEMO_TRACE_FILES = sorted((DEMO_FOLDER / "traces").glob("*.jsonl.gz"))
DEMO_CHECKLIST_FILES = sorted((DEMO_FOLDER / "checklists").glob("*.yaml"))
POLL_MS = 5_000
# Generous: the first recompute starts a fresh worker process before it scores 200 cases.
RESULTS_SECONDS = 60
TIMEOUT_MS = 30_000
NOTES_STEP = "#data-step-notes"
CLEAR_OPENER = ".upload-totals > .data-button-danger"
CLEAR_DIALOG = "dialog.clear-dialog"
# Every control a reader must reach with Tab before confirming, by its accessible name.
KEYBOARD_TARGETS = [
    "Add trace files",
    "Add verdict files",
    "Add checklist files",
    "Clear all data",
    "Confirm",
]
# The name a reader hears for the focused element: its label, else its text.
FOCUS_NAME = """() => {
  const el = document.activeElement;
  if (el === null || el === document.body) return "body";
  const label = el.labels && el.labels.length > 0 ? el.labels[0].textContent : null;
  return (label ?? el.textContent ?? "").replace(/\\s+/g, " ").trim();
}"""
# Registered before any page script runs, on every page the context opens, so a violation
# during parsing or after a reload is caught too.
WATCH_SCRIPT = """
document.addEventListener("securitypolicyviolation", function (event) {
  window.__reportCspViolation(
    event.violatedDirective + " blocked " + (event.blockedURI || "inline") + ": " + event.sample
  );
}, true);
"""
PAGE_ADDRESSES = {
    "Overview": "/",
    "Versions": "/versions",
    "Skipped steps": "/skipped",
    "Weekly trend": "/trends",
    "Verdict matrix": "/verdicts",
    "Cases": "/cases",
    "Data": "/data",
    "Limits": "/limits",
}
EMPTY_STATE = {
    "is_configured": False,
    "can_configure": False,
    "has_results": False,
    "span_count_text": "0 spans stored.",
    "verdict_count_text": "0 verdicts stored.",
    "trace_family_text": None,
    "checklist_classes": [],
    "checklist_classes_text": "None yet",
    "checklist_error_text": None,
}


@dataclass
class UiVisit:
    base_url: str
    opening_address: str = ""
    stored_texts: dict[str, list[str]] = field(default_factory=dict)
    offered_labels: list[str] = field(default_factory=list)
    focus_after_upload: str = ""
    tab_walk: list[str] = field(default_factory=list)
    saved_text: str = ""
    focus_after_reload: str = ""
    announced_after_reload: str = ""
    kpi_text: str = ""
    alias_address: str = ""
    address_walk: AddressWalk | None = None
    is_dialog_open_after_enter: bool = False
    focus_in_dialog: str = ""
    is_dialog_open_after_escape: bool = True
    focus_after_escape: str = ""
    config_steps_after_clear: int = -1
    state_after_clear: dict[str, object] = field(default_factory=dict)
    axe: dict[str, list[Any]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    csp_violations: list[str] = field(default_factory=list)
    probe_violations: list[str] = field(default_factory=list)  # from the probe after the visit
    console_errors: list[str] = field(default_factory=list)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def ui_visit(
    request: pytest.FixtureRequest, browser: Any, tmp_path_factory: pytest.TempPathFactory
) -> UiVisit:
    # By name: the imported fixture can't be a parameter without shadowing its import.
    axe_script: str = request.getfixturevalue("axe_source")
    folder = tmp_path_factory.mktemp("ui-browser")
    served = start_ui(folder, folder / "data", ["--no-open"], dict(os.environ))
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        visit = UiVisit(served.base_url)
        context.expose_function(
            "__reportCspViolation", lambda text: visit.csp_violations.append(text)
        )
        context.add_init_script(WATCH_SCRIPT)
        page = context.new_page()
        watch(page, visit)
        page.clock.install()
        page.goto(served.base_url + "/")
        page.wait_for_selector("#data-step-upload")
        visit.opening_address = page.evaluate(READ_ADDRESS)
        # Paused, the page never polls by itself; the test moves its clock to the poll.
        pause_clock(page)
        visit.axe["empty"] = run_axe(page, axe_script)

        upload_demo_files(page, visit)
        choose_labels(page, visit)
        visit.tab_walk = walk_with_tab(page)

        page.get_by_role("button", name="Confirm").click()
        page.get_by_role("button", name="Change configuration").wait_for()
        visit.saved_text = page.inner_text(".config-saved")
        visit.axe["configured"] = run_axe(page, axe_script)

        served.wait_for_generation(1, timeout=RESULTS_SECONDS)
        page.clock.fast_forward(POLL_MS)
        # The page reloads by itself once its poll sees the results.
        page.wait_for_selector(NOTES_STEP, timeout=TIMEOUT_MS)
        page.wait_for_function("() => document.activeElement !== document.body")
        visit.focus_after_reload = page.evaluate(FOCUS_NAME)
        visit.announced_after_reload = page.inner_text(".data-steps > [role=status]")
        visit.axe["with notes"] = run_axe(page, axe_script)

        open_page(page, "Overview")
        visit.kpi_text = page.inner_text(".overview-kpis")
        page.goto(served.base_url + "/#/data-notes")
        page.wait_for_selector(NOTES_STEP)
        visit.alias_address = page.evaluate(READ_ADDRESS)
        visit.address_walk = walk_addresses(browser, served)

        try_clear_dialog_keys(page, visit)
        clear_all_data(page, served, visit)
        visit.probe_violations = probe_csp_watch(context, served, visit)
    finally:
        context.close()
        served.stop()
    return visit


def walk_addresses(browser: Any, served: ServedProcess) -> AddressWalk:
    """Walk the page addresses in a context of its own, whose clock runs and whose requests
    stay out of the visit's."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        return walk_page_addresses(context.new_page(), served.base_url)
    finally:
        context.close()


def watch(page: Any, visit: UiVisit) -> None:
    def log_console(message: Any) -> None:
        if message.type == "error":
            visit.console_errors.append(message.text)

    page.on("request", lambda request: visit.requests.append(request.url))
    page.on("console", log_console)
    page.on("pageerror", lambda error: visit.console_errors.append(str(error)))


def upload_demo_files(page: Any, visit: UiVisit) -> None:
    """Choose each card's demo files in its focused file input, wait for every file's outcome,
    then clear the outcomes, which would otherwise hold the page's reload."""
    for title, label, files in (
        ("Traces", "Add trace files", DEMO_TRACE_FILES),
        ("Verdicts", "Add verdict files", [DEMO_FOLDER / "verdicts.csv"]),
        ("Checklists", "Add checklist files", DEMO_CHECKLIST_FILES),
    ):
        card = page.locator(".upload-card", has=page.get_by_role("heading", name=title))
        card.get_by_label(label).focus()
        card.get_by_label(label).set_input_files(files)
        card.locator(".upload-outcome").nth(len(files) - 1).wait_for()
        visit.stored_texts[title] = card.locator(".upload-stored").all_inner_texts()
        if title == "Traces":
            visit.focus_after_upload = page.evaluate(FOCUS_NAME)
        card.get_by_role("button", name=re.compile(r"^Clear results")).click()


def choose_labels(page: Any, visit: UiVisit) -> None:
    """Map each analyst label the proposal leaves open, as the demo's configuration does."""
    group = page.locator("fieldset", has=page.locator("legend", has_text="Analyst labels"))
    group.locator("select").first.wait_for()
    visit.offered_labels = group.locator("label code").all_inner_texts()
    for label, verdict in DEMO_LABEL_CHOICES.items():
        page.get_by_role("combobox", name=label, exact=True).select_option(verdict)


def walk_with_tab(page: Any) -> list[str]:
    """The name of each element Tab reaches from the top of the page until it leaves it."""
    # From the page's title: a blurred element would leave Tab's starting point where it was.
    page.focus(".page-head-title")
    walk: list[str] = []
    for _ in range(200):
        page.keyboard.press("Tab")
        name = page.evaluate(FOCUS_NAME)
        if name == "body":
            break
        walk.append(name)
    return walk


def run_axe(page: Any, source: str) -> list[Any]:
    """The violated rules on the page; the clock runs only while axe does."""
    # Evaluated, not added as a script tag, so the page's CSP stays in force for the whole visit.
    page.evaluate(source)
    page.clock.resume()
    results = page.evaluate(
        "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", WCAG_TAGS
    )
    pause_clock(page)
    return results["violations"]


def pause_clock(page: Any) -> None:
    """Stop the page's clock a second ahead of its own time, so it never goes back."""
    now_ms = page.evaluate("Date.now()")
    page.clock.pause_at(datetime.fromtimestamp(now_ms / 1000 + 1, tz=UTC))


def open_page(page: Any, title: str) -> None:
    """Open a page as a reader would, by its sidebar link."""
    page.get_by_role("navigation", name="Pages").get_by_role("link", name=title, exact=True).click()
    # The app names the page in the tab title once it has rendered.
    page.wait_for_function("title => document.title.startsWith(title + ' ·')", arg=title)


def try_clear_dialog_keys(page: Any, visit: UiVisit) -> None:
    """Open the clear dialog with Enter and close it with Escape, noting where focus goes."""
    page.focus(CLEAR_OPENER)
    page.keyboard.press("Enter")
    visit.is_dialog_open_after_enter = page.evaluate(
        f"document.querySelector('{CLEAR_DIALOG}').open"
    )
    visit.focus_in_dialog = page.evaluate(FOCUS_NAME)
    page.keyboard.press("Escape")
    visit.is_dialog_open_after_escape = page.evaluate(
        f"document.querySelector('{CLEAR_DIALOG}').open"
    )
    visit.focus_after_escape = page.evaluate(FOCUS_NAME)


def clear_all_data(page: Any, served: ServedProcess, visit: UiVisit) -> None:
    page.click(CLEAR_OPENER)
    with page.expect_navigation():
        page.click(f"{CLEAR_DIALOG} .data-button-danger")
    page.wait_for_selector("#data-step-upload")
    # The totals are read after the page loads; wait until they show what was stored.
    page.get_by_text("0 spans stored.").wait_for()
    visit.config_steps_after_clear = page.locator("#data-step-config").count()
    visit.state_after_clear = httpx.get(served.base_url + "/api/ui/state", timeout=10).json()


def probe_csp_watch(context: Any, served: ServedProcess, visit: UiVisit) -> list[str]:
    """Break the CSP once on a page of its own, after the visit; return what the watch reported.

    The page is not the visit's, so its requests and console error stay out of the visit's.
    """
    before = len(visit.csp_violations)
    page = context.new_page()
    page.goto(served.base_url + "/")
    page.wait_for_selector("#data-step-upload")
    page.evaluate(
        """() => new Promise(resolve => {
          document.addEventListener("securitypolicyviolation", () => resolve(), {once: true});
          document.body.setAttribute("style", "color: red");
        })"""
    )
    page.close()
    reported = visit.csp_violations[before:]
    del visit.csp_violations[before:]
    return reported


@pytest.fixture(scope="module")
def offline_kpi_text(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> str:
    """The Overview's KPI text on the offline demo page, `check`'s dashboard for the demo."""
    config_path = DEMO_FOLDER / "detecttrace.yaml"
    path = tmp_path_factory.mktemp("offline") / "demo.html"
    write_dashboard(
        render_dashboard(run_check(load_run_config(config_path), config_path).results), path
    )
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.goto(path.as_uri() + "#/")
        page.wait_for_selector(".overview-kpis")
        return page.inner_text(".overview-kpis")
    finally:
        context.close()


def test_the_app_opens_on_the_data_page(ui_visit: UiVisit) -> None:
    assert ui_visit.opening_address == "/data"


@pytest.mark.parametrize(
    ("title", "texts"),
    [
        pytest.param(
            "Traces",
            ["791 spans added.", "754 spans added.", "584 spans added."],
            id="traces",
        ),
        pytest.param("Verdicts", ["201 verdicts added."], id="verdicts"),
        pytest.param(
            "Checklists",
            [
                "Checklist saved for alert class 'impossible_travel'.",
                "Checklist saved for alert class 'oauth_consent'.",
            ],
            id="checklists",
        ),
    ],
)
def test_each_upload_card_shows_what_was_stored(
    ui_visit: UiVisit, title: str, texts: list[str]
) -> None:
    assert ui_visit.stored_texts[title] == texts


def test_focus_stays_on_the_file_input_through_several_files(ui_visit: UiVisit) -> None:
    assert ui_visit.focus_after_upload == "Add trace files"


def test_the_reload_returns_focus_to_the_configuration_step(ui_visit: UiVisit) -> None:
    assert ui_visit.focus_after_reload == "2 · Configuration"


def test_the_reload_says_the_dashboard_was_updated(ui_visit: UiVisit) -> None:
    assert ui_visit.announced_after_reload == "The dashboard was updated."


def test_the_form_offers_the_labels_the_proposal_cannot_place(ui_visit: UiVisit) -> None:
    assert sorted(ui_visit.offered_labels) == sorted(DEMO_LABEL_CHOICES)


@pytest.mark.parametrize("name", KEYBOARD_TARGETS)
def test_tab_reaches_each_control(ui_visit: UiVisit, name: str) -> None:
    assert name in ui_visit.tab_walk


def test_confirming_says_the_dashboard_is_being_computed(ui_visit: UiVisit) -> None:
    assert ui_visit.saved_text == "Configuration saved. The dashboard is being computed."


def test_the_overview_kpis_match_the_offline_demo_page(
    ui_visit: UiVisit, offline_kpi_text: str
) -> None:
    assert ui_visit.kpi_text == offline_kpi_text


def test_the_old_data_notes_address_lands_on_the_data_page(ui_visit: UiVisit) -> None:
    assert ui_visit.alias_address == "/data"


@pytest.fixture(scope="module")
def address_walk(ui_visit: UiVisit) -> AddressWalk:
    assert ui_visit.address_walk is not None
    return ui_visit.address_walk


def test_each_sidebar_link_opens_its_page_address(address_walk: AddressWalk) -> None:
    assert address_walk.link_addresses == PAGE_ADDRESSES


def test_back_returns_to_the_page_before(address_walk: AddressWalk) -> None:
    assert address_walk.address_after_back == "/data"


def test_forward_returns_to_the_page_after(address_walk: AddressWalk) -> None:
    assert address_walk.address_after_forward == "/limits"


def test_picking_a_class_puts_it_in_the_page_address(address_walk: AddressWalk) -> None:
    assert address_walk.address_before_reload == "/versions?class=class-1"


def test_a_reload_keeps_the_page_address(address_walk: AddressWalk) -> None:
    assert address_walk.address_after_reload == "/versions?class=class-1"


def test_a_reload_keeps_the_picked_class(address_walk: AddressWalk) -> None:
    assert address_walk.class_after_reload == address_walk.class_before_reload


def test_an_old_hash_address_moves_to_its_page_path(address_walk: AddressWalk) -> None:
    assert address_walk.address_from_old_hash == "/versions?class=class-1"


def test_the_old_data_notes_path_lands_on_the_data_page(address_walk: AddressWalk) -> None:
    assert address_walk.address_from_old_path == "/data"


def test_enter_opens_the_clear_dialog(ui_visit: UiVisit) -> None:
    assert ui_visit.is_dialog_open_after_enter


def test_the_clear_dialog_focuses_cancel_first(ui_visit: UiVisit) -> None:
    assert ui_visit.focus_in_dialog == "Cancel"


def test_escape_closes_the_clear_dialog(ui_visit: UiVisit) -> None:
    assert not ui_visit.is_dialog_open_after_escape


def test_escape_returns_focus_to_the_clear_button(ui_visit: UiVisit) -> None:
    assert ui_visit.focus_after_escape == "Clear all data"


def test_clearing_reloads_to_the_empty_data_page(ui_visit: UiVisit) -> None:
    assert ui_visit.config_steps_after_clear == 0


def test_clearing_leaves_nothing_stored(ui_visit: UiVisit) -> None:
    assert ui_visit.state_after_clear == EMPTY_STATE


@pytest.mark.parametrize("state", ["empty", "configured", "with notes"])
def test_axe_finds_no_violation_on_the_data_page(ui_visit: UiVisit, state: str) -> None:
    assert [violation["id"] for violation in ui_visit.axe[state]] == []


def test_the_page_asks_only_its_own_server(ui_visit: UiVisit) -> None:
    others = [url for url in ui_visit.requests if not url.startswith(ui_visit.base_url + "/")]
    assert others == []


def test_the_page_asks_its_server(ui_visit: UiVisit) -> None:
    # Guards the check above: it must have requests to look at.
    assert ui_visit.base_url + "/api/ui/state" in ui_visit.requests


def test_the_page_reports_no_csp_violation(ui_visit: UiVisit) -> None:
    assert ui_visit.csp_violations == []


def test_the_csp_watch_reports_a_violation(ui_visit: UiVisit) -> None:
    # Guards the check above: the watch must report a violation the page really has.
    assert [text.split(" ")[0] for text in ui_visit.probe_violations] == ["style-src-attr"]


def test_the_page_logs_no_console_error(ui_visit: UiVisit) -> None:
    assert ui_visit.console_errors == []
