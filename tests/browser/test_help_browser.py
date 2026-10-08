"""The term pop-ups and the Help page in a real browser: opening, closing, layout, links, axe,
CSP and network, on the offline demo page and in a real `detecttrace ui`.

Dev-only and run by hand, like the other browser checks:

    uv run --with playwright pytest -m browser -s -q tests/browser/test_help_browser.py
"""

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from page_addresses import READ_ADDRESS
from serve.served_process import DEMO_FOLDER, start_ui
from serve.test_ui_parity import upload_and_confirm
from test_dashboard_browser import (  # noqa: F401 (axe_source is a fixture)
    DEMO_RESULTS,
    WCAG_TAGS,
    Visit,
    axe_source,
    visiting,
)

from detecttrace.dashboard import render_dashboard, write_dashboard

# Playwright is not a project dependency, so Pyright can't see its types; pages and
# browsers are typed `Any` here.
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed; run `uv run --with playwright pytest -m browser tests/browser`",
)

pytestmark = pytest.mark.browser

KAPPA_LABEL = "Chance-corrected agreement (κ)"
KAPPA_SECTION = "chance-corrected-agreement"
# The Help sections the offline page shows: "using-the-app" is the ui app's only.
OFFLINE_SECTION_IDS = [
    "how-it-works",
    "pages",
    "evidence-completeness",
    "verdict-agreement",
    "chance-corrected-agreement",
    "dangerous-false-closes",
    "reading-the-numbers",
    "more",
]
# Where a term's name opens a pop-up: (page title, the trigger's container, its term label).
HOVER_TARGETS = [
    ("Overview", ".version-table th", "Verdict agreement"),
    ("Versions", ".version-table th", "Dangerous false closes"),
    ("Weekly trend", "figcaption", "Evidence completeness"),
]
HOVER_IDS = ["overview header", "versions header", "trend caption"]
READ_FOCUSED_ID = "() => document.activeElement.id"
READ_FOCUSED_TEXT = "() => document.activeElement.textContent"
FOCUSED_ID_IS = "id => document.activeElement.id === id"


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("help-browser") / "demo.html"
    write_dashboard(render_dashboard(DEMO_RESULTS), path)
    return path


def find_trigger(page: Any, container: str, label: str) -> Any:
    """The visible button that names `label` in a `container`; a hidden class panel has one too."""
    return (
        page.locator(container)
        .get_by_role("button", name=label, exact=True)
        .filter(visible=True)
        .first
    )


def find_popup(page: Any, label: str) -> Any:
    return page.get_by_role("group", name=label, exact=True)


def open_kappa_by_focus(visit: Visit) -> Any:
    """Focus the Overview's κ header and wait for its pop-up to finish fading in; return the
    pop-up."""
    find_trigger(visit.page, ".version-table th", KAPPA_LABEL).focus()
    popup = find_popup(visit.page, KAPPA_LABEL)
    popup.wait_for()
    # Visible starts at opacity 0: mid-fade, axe reads the link's colour blended into the panel.
    popup.evaluate("el => Promise.all(el.getAnimations().map(animation => animation.finished))")
    return popup


# Opening and closing


@pytest.mark.parametrize(("title", "container", "label"), HOVER_TARGETS, ids=HOVER_IDS)
def test_hover_opens_the_pop_up(
    browser: Any, demo_path: Path, title: str, container: str, label: str
) -> None:
    with visiting(browser, demo_path) as visit:
        visit.open(title)
        find_trigger(visit.page, container, label).hover()
        sync_api.expect(find_popup(visit.page, label)).to_be_visible()


def tab_to_agreement(page: Any) -> Any:
    """Focus the completeness header and wait for its pop-up, then Tab past the pop-up's link
    to the agreement header; return the agreement pop-up."""
    find_trigger(page, ".version-table th", "Evidence completeness").focus()
    find_popup(page, "Evidence completeness").wait_for()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    return find_popup(page, "Verdict agreement")


def test_tab_onto_a_term_opens_its_pop_up(browser: Any, demo_path: Path) -> None:
    with visiting(browser, demo_path) as visit:
        sync_api.expect(tab_to_agreement(visit.page)).to_be_visible()


@dataclass(frozen=True)
class KeyStates:
    focus_after_tab: str
    popups_after_escape: int
    focus_after_escape: str


@pytest.fixture(scope="module")
def key_states(browser: Any, demo_path: Path) -> KeyStates:
    """Tab to the agreement header, then Escape."""
    with visiting(browser, demo_path) as visit:
        page = visit.page
        popup = tab_to_agreement(page)
        popup.wait_for()
        focus_after_tab = page.evaluate(READ_FOCUSED_TEXT)
        page.keyboard.press("Escape")
        popup.wait_for(state="detached")
        return KeyStates(
            focus_after_tab=focus_after_tab,
            popups_after_escape=page.locator(".term-hint-popup").count(),
            focus_after_escape=page.evaluate(READ_FOCUSED_TEXT),
        )


def test_tab_moves_focus_to_the_next_term(key_states: KeyStates) -> None:
    assert key_states.focus_after_tab == "Verdict agreement"


def test_escape_closes_the_pop_up(key_states: KeyStates) -> None:
    assert key_states.popups_after_escape == 0


def test_escape_returns_focus_to_the_term(key_states: KeyStates) -> None:
    assert key_states.focus_after_escape == "Verdict agreement"


def test_a_tap_opens_the_pop_up(browser: Any, demo_path: Path) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 900}, has_touch=True)
    try:
        page = context.new_page()
        page.goto(demo_path.as_uri() + "#/")
        find_trigger(page, ".version-table th", KAPPA_LABEL).tap()
        sync_api.expect(find_popup(page, KAPPA_LABEL)).to_be_visible()
    finally:
        context.close()


# More in Help


@dataclass(frozen=True)
class Landing:
    address: str
    focused_id: str


def follow_more_in_help(page: Any, read_address: str) -> Landing:
    """From the open κ pop-up, follow "More in Help" and wait until its heading has focus."""
    find_popup(page, KAPPA_LABEL).get_by_role("link", name="More in Help").click()
    page.wait_for_function(FOCUSED_ID_IS, arg=KAPPA_SECTION)
    return Landing(page.evaluate(read_address), page.evaluate(READ_FOCUSED_ID))


@pytest.fixture(scope="module")
def offline_landing(browser: Any, demo_path: Path) -> Landing:
    with visiting(browser, demo_path) as visit:
        open_kappa_by_focus(visit)
        # The file's own path comes before the hash; only the hash is the dashboard's.
        return follow_more_in_help(visit.page, "() => location.hash")


@pytest.fixture(scope="module")
def ui_landing(browser: Any, tmp_path_factory: pytest.TempPathFactory) -> Landing:
    """The same in a real `detecttrace ui`, on the demo data uploaded and confirmed beforehand."""
    if sys.platform == "win32":
        pytest.skip("stops the server with SIGTERM")
    folder = tmp_path_factory.mktemp("ui-help")
    (folder / "data").mkdir()
    confirmed = upload_and_confirm(DEMO_FOLDER, folder / "data")
    assert confirmed.saved is not None, "the demo configuration was not saved"
    served = start_ui(folder, folder / "data", ["--no-open"], dict(os.environ))
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.goto(served.base_url + "/versions")
        find_trigger(page, ".version-table th", KAPPA_LABEL).focus()
        find_popup(page, KAPPA_LABEL).wait_for()
        return follow_more_in_help(page, READ_ADDRESS)
    finally:
        context.close()
        served.stop()


def test_more_in_help_opens_the_terms_section_offline(offline_landing: Landing) -> None:
    assert offline_landing.address == f"#/help#{KAPPA_SECTION}"


def test_more_in_help_focuses_the_terms_heading_offline(offline_landing: Landing) -> None:
    assert offline_landing.focused_id == KAPPA_SECTION


def test_more_in_help_opens_the_terms_section_in_the_app(ui_landing: Landing) -> None:
    assert ui_landing.address == f"/help#{KAPPA_SECTION}"


def test_more_in_help_focuses_the_terms_heading_in_the_app(ui_landing: Landing) -> None:
    assert ui_landing.focused_id == KAPPA_SECTION


# Layout


@dataclass(frozen=True)
class PhoneLayout:
    popup_box: dict[str, float]
    viewport: dict[str, int]
    scroll_width: int
    client_width: int


@pytest.fixture(scope="module")
def phone_layout(browser: Any, demo_path: Path) -> PhoneLayout:
    """The Overview on a 390px phone with the κ pop-up open."""
    with visiting(browser, demo_path, width=390) as visit:
        popup = open_kappa_by_focus(visit)
        scroll_width, client_width = visit.page.evaluate(
            "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        return PhoneLayout(
            popup.bounding_box(), visit.page.viewport_size, scroll_width, client_width
        )


def test_an_open_pop_up_fits_a_390px_screen(phone_layout: PhoneLayout) -> None:
    box = phone_layout.popup_box
    screen = phone_layout.viewport
    is_inside = {
        "left": box["x"] >= 0,
        "top": box["y"] >= 0,
        "right": box["x"] + box["width"] <= screen["width"],
        "bottom": box["y"] + box["height"] <= screen["height"],
    }
    assert is_inside == {"left": True, "top": True, "right": True, "bottom": True}


def test_an_open_pop_up_never_scrolls_the_page_sideways(phone_layout: PhoneLayout) -> None:
    assert phone_layout.scroll_width <= phone_layout.client_width


# Help page


@pytest.mark.parametrize("section_id", OFFLINE_SECTION_IDS)
def test_a_contents_link_focuses_its_section(
    browser: Any, demo_path: Path, section_id: str
) -> None:
    with visiting(browser, demo_path) as visit:
        visit.open("Help")
        contents = visit.page.get_by_role("navigation", name="On this page")
        contents.locator(f'a[href="#/help#{section_id}"]').click()
        visit.page.wait_for_function(FOCUSED_ID_IS, arg=section_id)
        focused_id = visit.page.evaluate(READ_FOCUSED_ID)
    assert focused_id == section_id


# Accessibility, CSP and network


@pytest.fixture(scope="module")
def axe_violations(
    request: pytest.FixtureRequest, browser: Any, demo_path: Path
) -> dict[str, list[Any]]:
    """The violated rules on Help, and on the Overview with the κ pop-up open."""
    # By name: the imported fixture can't be a parameter without shadowing its import.
    axe_script: str = request.getfixturevalue("axe_source")
    violations: dict[str, list[Any]] = {}
    # The page's CSP rightly blocks the injected axe script; only this run lifts it.
    with visiting(browser, demo_path, bypass_csp=True) as visit:
        visit.page.add_script_tag(content=axe_script)
        open_kappa_by_focus(visit)
        violations["overview with a pop-up"] = run_axe(visit.page)
        visit.open("Help")
        violations["help"] = run_axe(visit.page)
    return violations


def run_axe(page: Any) -> list[Any]:
    results = page.evaluate(
        "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", WCAG_TAGS
    )
    return results["violations"]


@pytest.mark.parametrize("state", ["help", "overview with a pop-up"])
def test_axe_finds_no_violation(axe_violations: dict[str, list[Any]], state: str) -> None:
    assert [violation["id"] for violation in axe_violations[state]] == []


@pytest.fixture(scope="module")
def hint_visit(browser: Any, demo_path: Path) -> Iterator[Visit]:
    """The offline demo after opening a pop-up on each page that has one, following "More in
    Help" and a contents link on Help."""
    with visiting(browser, demo_path) as visit:
        for title, container, label in HOVER_TARGETS:
            visit.open(title)
            find_trigger(visit.page, container, label).hover()
            find_popup(visit.page, label).wait_for()
        visit.open("Overview")
        open_kappa_by_focus(visit)
        follow_more_in_help(visit.page, READ_ADDRESS)
        contents = visit.page.get_by_role("navigation", name="On this page")
        contents.locator('a[href="#/help#more"]').click()
        visit.page.wait_for_function(FOCUSED_ID_IS, arg="more")
        yield visit


def test_the_pop_ups_and_help_report_no_csp_violation(hint_visit: Visit) -> None:
    assert hint_visit.csp_violations() == []


def test_the_pop_ups_and_help_request_nothing_but_the_page(hint_visit: Visit) -> None:
    assert hint_visit.requests == [hint_visit.url]
