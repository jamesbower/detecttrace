"""JSON text shared by the trace readers: decoding, and the types of untyped input."""

import json
import re
from collections.abc import Callable
from typing import Any

from detecttrace.model import IssueKind

# Strict UTF-8 decoding rejects raw surrogates, so only a JSON escape can produce one.
# A valid pair decodes to one character, so any surrogate left over is a lone one.
_MAY_HOLD_SURROGATE = re.compile(r"\\u[dD][89a-fA-F]")
_SURROGATE = re.compile("[\ud800-\udfff]")

# Trace JSON is untyped input: Any is the honest type until a parser validates its fields.
Json = Any
# How a parser reports one problem: its kind and a detail naming where it is.
Report = Callable[[IssueKind, str], None]


def parse_json_text(text: str) -> Json | None:
    """Decode JSON text, or return None when it is not valid JSON or nests too deep."""
    # A str, never bytes: json.loads on bytes would also accept UTF-16 and UTF-32.
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return _replace_surrogates(document) if _MAY_HOLD_SURROGATE.search(text) else document


def _replace_surrogates(document: Json) -> Json:
    """Replace lone surrogates in every string and key with U+FFFD, in place where possible.

    A lone surrogate can't be encoded as UTF-8, so one left in a case ID or argument would
    make the results write fail. The walk uses a stack, since a document can nest as deep
    as the JSON parser allows.
    """
    if isinstance(document, str):
        return _SURROGATE.sub("\ufffd", document)
    stack = [document]
    while stack:
        container = stack.pop()
        if isinstance(container, list):
            for index, value in enumerate(container):
                if isinstance(value, str):
                    container[index] = _SURROGATE.sub("\ufffd", value)
                elif isinstance(value, list | dict):
                    stack.append(value)
        elif isinstance(container, dict):
            items = list(container.items())
            container.clear()
            for key, value in items:
                if isinstance(value, str):
                    value = _SURROGATE.sub("\ufffd", value)
                elif isinstance(value, list | dict):
                    stack.append(value)
                container[_SURROGATE.sub("\ufffd", key)] = value
    return document
