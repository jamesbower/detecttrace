"""The two JSON blocks of a rendered dashboard page, read as the page's script reads them."""

import json
from typing import Any

from html_tree import has_tag, parse_html


def read_view(html: str) -> Any:
    return json.loads(_read_block(html, "dt-view"))


def read_results(html: str) -> Any:
    return json.loads(_read_block(html, "dt-results"))


def _read_block(html: str, block_id: str) -> str:
    return parse_html(html).find(has_tag("script", id=block_id)).text()
