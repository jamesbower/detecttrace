"""A walk through a served or ui dashboard's page addresses, as a reader moves between pages.

Both apps route on the path, so each page has an address of its own that a link, Back and
Forward, a reload and an old `/#/…` bookmark all reach.
"""

from dataclasses import dataclass
from typing import Any

READ_ADDRESS = "() => location.pathname + location.search + location.hash"
SELECTED_TAB = "[role=tab][aria-selected=true]"


@dataclass(frozen=True)
class AddressWalk:
    link_addresses: dict[str, str]  # each sidebar link's title, and the address it opened
    address_after_back: str
    address_after_forward: str
    class_before_reload: str
    address_before_reload: str
    address_after_reload: str
    class_after_reload: str
    address_from_old_hash: str
    address_from_old_path: str


def walk_page_addresses(page: Any, base_url: str) -> AddressWalk:
    """Open each page by its sidebar link, go Back and Forward, reload with a class picked, and
    open an old hash address and an old page path. `page` is signed in where it must be."""
    page.goto(base_url + "/")
    page.wait_for_selector(".overview-kpis")
    # Text content, not inner text: the sidebar shows its labels in capitals.
    titles: list[str] = (
        page.get_by_role("navigation", name="Pages").get_by_role("link").all_text_contents()
    )
    link_addresses: dict[str, str] = {}
    for title in titles:
        open_page(page, title)
        link_addresses[title] = page.evaluate(READ_ADDRESS)

    page.go_back()
    wait_for_title(page, titles[-2])
    address_after_back = page.evaluate(READ_ADDRESS)
    page.go_forward()
    wait_for_title(page, titles[-1])
    address_after_forward = page.evaluate(READ_ADDRESS)

    open_page(page, "Versions")
    second_tab = page.get_by_role("tab").nth(1)
    class_before_reload = second_tab.text_content()
    second_tab.click()
    page.wait_for_function("() => location.search !== ''")
    address_before_reload = page.evaluate(READ_ADDRESS)
    page.reload()
    wait_for_title(page, "Versions")
    address_after_reload = page.evaluate(READ_ADDRESS)
    class_after_reload = page.text_content(SELECTED_TAB)

    page.goto(base_url + "/#/versions?class=class-1")
    wait_for_title(page, "Versions")
    address_from_old_hash = page.evaluate(READ_ADDRESS)
    page.goto(base_url + "/data-notes")
    wait_for_title(page, "Data")
    address_from_old_path = page.evaluate(READ_ADDRESS)
    return AddressWalk(
        link_addresses=link_addresses,
        address_after_back=address_after_back,
        address_after_forward=address_after_forward,
        class_before_reload=class_before_reload,
        address_before_reload=address_before_reload,
        address_after_reload=address_after_reload,
        class_after_reload=class_after_reload,
        address_from_old_hash=address_from_old_hash,
        address_from_old_path=address_from_old_path,
    )


def open_page(page: Any, title: str) -> None:
    """Open a page as a reader would, by its sidebar link."""
    page.get_by_role("navigation", name="Pages").get_by_role("link", name=title, exact=True).click()
    wait_for_title(page, title)


def wait_for_title(page: Any, title: str) -> None:
    """Wait until the app names the page in the tab title, which it does once it has rendered."""
    page.wait_for_function("title => document.title.startsWith(title + ' ·')", arg=title)
