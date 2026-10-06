"""The served dashboard in a real browser: sign-in, the new-data bar, and Reload.

Dev-only and run by hand, like the other browser checks:

    uv run --with playwright pytest -m browser -s -q tests/browser/test_served_reload.py

The page asks for newer results every 30 seconds. The test moves the page's clock forward
instead of waiting, so the served script runs exactly as in production.
"""

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from page_addresses import READ_ADDRESS, AddressWalk, walk_page_addresses
from serve.app_support import READ_TOKEN
from serve.served_process import ServedProcess, post_demo_data, start_server

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

RESULT_FILTER = ".case-filters select >> nth=1"
RELOAD = ".status-bar-reload"
# The first demo verdict is FP; TP changes that case's label and so the results.
CHANGED_VERDICT = (
    "case_id,alert_class,verdict,closed_at\nDT-IT-0002,impossible_travel,TP,2026-08-03T16:02:32Z\n"
)
POLL_MS = 30_000
TIMEOUT_MS = 30_000
READ_GENERATION = (
    "() => JSON.parse(document.getElementById('dt-view').textContent).served.generation"
)
# Registered before any page script runs, so a violation during parsing is caught too.
WATCH_SCRIPT = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", function (event) {
  window.__cspViolations.push(event.violatedDirective + " blocked " + event.blockedURI);
}, true);
"""


@dataclass(frozen=True)
class ReloadVisit:
    first_generation: int
    bar_text: str
    filter_before_reload: str
    reloaded_generation: int
    filter_after_reload: str
    address_after_reload: str
    requests: list[str]
    base_url: str
    csp_violations: list[str]
    console_errors: list[str]


@dataclass(frozen=True)
class WaitingVisit:
    title: str
    bar_text: str
    page_links_after_reload: int


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def open_signed_in(
    browser: Any, served: ServedProcess, path: str = ""
) -> tuple[Any, list[str], list[str]]:
    """The page, signed in, with every request it makes and every console error, from the start."""
    context = browser.new_context(http_credentials={"username": "analyst", "password": READ_TOKEN})
    context.add_init_script(WATCH_SCRIPT)
    page = context.new_page()
    requests: list[str] = []
    console_errors: list[str] = []

    def log_console(message: Any) -> None:
        if message.type == "error":
            console_errors.append(message.text)

    page.on("request", lambda request: requests.append(request.url))
    page.on("console", log_console)
    page.on("pageerror", lambda error: console_errors.append(str(error)))
    page.clock.install()
    page.goto(served.base_url + "/" + path)
    page.wait_for_selector("h1")
    return page, requests, console_errors


@pytest.fixture(scope="module")
def reload_visit(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> ReloadVisit:
    served = start_server(tmp_path_factory.mktemp("served"))
    try:
        post_demo_data(served)
        first_status = served.wait_for_generation(1, timeout=60)
        first_generation = first_status["generation"]
        assert isinstance(first_generation, int)
        page, requests, console_errors = open_signed_in(browser, served, "cases")
        page.select_option(RESULT_FILTER, "disagree")
        served.post_verdicts_csv(CHANGED_VERDICT).raise_for_status()
        served.wait_for_generation(first_generation + 1, timeout=60)
        page.clock.fast_forward(POLL_MS)
        page.wait_for_selector(RELOAD, timeout=TIMEOUT_MS)
        bar_text = page.inner_text(".status-bar")
        filter_before_reload = page.input_value(RESULT_FILTER)
        page.click(RELOAD)
        page.wait_for_function(
            f"() => ({READ_GENERATION})() > {first_generation}", timeout=TIMEOUT_MS
        )
        page.wait_for_selector(".cases-count")
        visit = ReloadVisit(
            first_generation=first_generation,
            bar_text=bar_text,
            filter_before_reload=filter_before_reload,
            reloaded_generation=page.evaluate(READ_GENERATION),
            filter_after_reload=page.input_value(RESULT_FILTER),
            address_after_reload=page.evaluate(READ_ADDRESS),
            requests=requests,
            base_url=served.base_url,
            csp_violations=page.evaluate("window.__cspViolations"),
            console_errors=console_errors,
        )
        page.context.close()
    finally:
        served.stop()
    return visit


@pytest.fixture(scope="module")
def address_walk(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> AddressWalk:
    served = start_server(tmp_path_factory.mktemp("served-addresses"))
    try:
        post_demo_data(served)
        served.wait_for_generation(1, timeout=60)
        page, _, _ = open_signed_in(browser, served)
        walk = walk_page_addresses(page, served.base_url)
        page.context.close()
    finally:
        served.stop()
    return walk


@pytest.fixture(scope="module")
def waiting_visit(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> WaitingVisit:
    served = start_server(tmp_path_factory.mktemp("served-waiting"))
    try:
        page, _, _ = open_signed_in(browser, served)
        title = page.text_content("h1")
        post_demo_data(served)
        served.wait_for_generation(1, timeout=60)
        page.clock.fast_forward(POLL_MS)
        page.wait_for_selector(RELOAD, timeout=TIMEOUT_MS)
        bar_text = page.inner_text(".status-bar")
        page.click(RELOAD)
        page.wait_for_selector(".sidebar-link", timeout=TIMEOUT_MS)
        visit = WaitingVisit(title, bar_text, page.locator(".sidebar-link").count())
        page.context.close()
    finally:
        served.stop()
    return visit


def test_the_bar_says_new_data_is_available(reload_visit: ReloadVisit) -> None:
    assert "New data is available." in reload_visit.bar_text


def test_the_bar_leaves_the_filter_set(reload_visit: ReloadVisit) -> None:
    assert reload_visit.filter_before_reload == "disagree"


def test_reload_shows_the_newer_generation(reload_visit: ReloadVisit) -> None:
    assert reload_visit.reloaded_generation > reload_visit.first_generation


def test_reload_keeps_the_filter(reload_visit: ReloadVisit) -> None:
    assert reload_visit.filter_after_reload == "disagree"


def test_reload_keeps_the_page_address(reload_visit: ReloadVisit) -> None:
    assert reload_visit.address_after_reload == "/cases?result=disagree"


def test_each_sidebar_link_opens_its_page_address(address_walk: AddressWalk) -> None:
    assert address_walk.link_addresses == {
        "Overview": "/",
        "Versions": "/versions",
        "Skipped steps": "/skipped",
        "Weekly trend": "/trends",
        "Verdict matrix": "/verdicts",
        "Cases": "/cases",
        "Data": "/data",
        "Limits": "/limits",
    }


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


def test_the_served_page_asks_only_its_own_server(reload_visit: ReloadVisit) -> None:
    others = [
        url for url in reload_visit.requests if not url.startswith(reload_visit.base_url + "/")
    ]
    assert others == []


def test_the_served_page_asks_for_its_status(reload_visit: ReloadVisit) -> None:
    assert reload_visit.base_url + "/api/status" in reload_visit.requests


def test_the_served_page_reports_no_csp_violation(reload_visit: ReloadVisit) -> None:
    assert reload_visit.csp_violations == []


def test_the_served_page_logs_no_console_error(reload_visit: ReloadVisit) -> None:
    assert reload_visit.console_errors == []


def test_the_served_waiting_page_says_nothing_can_be_scored_yet(
    waiting_visit: WaitingVisit,
) -> None:
    assert waiting_visit.title == "Nothing to score yet"


def test_the_waiting_page_offers_new_data(waiting_visit: WaitingVisit) -> None:
    assert "New data is available." in waiting_visit.bar_text


def test_reloading_the_waiting_page_shows_the_dashboard(waiting_visit: WaitingVisit) -> None:
    assert waiting_visit.page_links_after_reload == 8
