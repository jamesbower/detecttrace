"""The served dashboard in a real browser: sign-in, the new-data bar, and Reload.

Dev-only and run by hand, like the other browser checks:

    uv run --with playwright pytest -m browser -s -q tests/browser/test_served_reload.py

The page asks for newer results every 30 seconds. The test moves the page's clock forward
instead of waiting, so the served script runs exactly as in production.
"""

import sys
from dataclasses import dataclass

import pytest
from serve.app_support import READ_TOKEN
from serve.served_process import post_demo_data, start_server

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

FILTER = '#case-filters button[data-filter="disagreements"]'
RELOAD = "#dt-serve button"
# The first demo verdict is FP; TP changes that case's label and so the results.
CHANGED_VERDICT = (
    "case_id,alert_class,verdict,closed_at\nDT-IT-0002,impossible_travel,TP,2026-08-03T16:02:32Z\n"
)
POLL_MS = 30_000
TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class ReloadVisit:
    first_generation: int
    bar_text: str
    filter_pressed: str | None
    reloaded_generation: int


@pytest.fixture(scope="module")
def reload_visit(tmp_path_factory: pytest.TempPathFactory) -> ReloadVisit:
    served = start_server(tmp_path_factory.mktemp("served"))
    try:
        post_demo_data(served)
        first_status = served.wait_for_generation(1, timeout=60)
        first_generation = first_status["generation"]
        assert isinstance(first_generation, int)
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                http_credentials={"username": "analyst", "password": READ_TOKEN}
            )
            page = context.new_page()
            page.clock.install()
            page.goto(served.base_url + "/")
            page.click(FILTER)
            served.post_verdicts_csv(CHANGED_VERDICT).raise_for_status()
            served.wait_for_generation(first_generation + 1, timeout=60)
            page.clock.fast_forward(POLL_MS)
            page.wait_for_selector(RELOAD, timeout=TIMEOUT_MS)
            bar_text = page.inner_text("#dt-serve")
            filter_pressed = page.get_attribute(FILTER, "aria-pressed")
            page.click(RELOAD)
            page.wait_for_function(
                f"Number(document.getElementById('dt-serve').dataset.generation) > {first_generation}",
                timeout=TIMEOUT_MS,
            )
            reloaded_generation = int(page.get_attribute("#dt-serve", "data-generation") or "0")
            browser.close()
    finally:
        served.stop()
    return ReloadVisit(first_generation, bar_text, filter_pressed, reloaded_generation)


def test_the_bar_says_new_data_is_available(reload_visit: ReloadVisit) -> None:
    assert "New data is available." in reload_visit.bar_text


def test_the_bar_leaves_the_filter_set(reload_visit: ReloadVisit) -> None:
    assert reload_visit.filter_pressed == "true"


def test_reload_shows_the_newer_generation(reload_visit: ReloadVisit) -> None:
    assert reload_visit.reloaded_generation > reload_visit.first_generation
