"""Read a YAML file with YAML 1.2 core-schema scalars, so only JSON types come out.

Duplicate keys, aliases and tags outside the core schema are errors, and files are size-capped.
"""

import math
import re
import stat
from pathlib import Path
from typing import Any

import yaml
from yaml.composer import ComposerError
from yaml.constructor import ConstructorError, SafeConstructor
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node, ScalarNode

from detecttrace.model import describe_os_error

_MAX_ECHO_CHARS = 60


class Yaml12Error(Exception):
    """A YAML file cannot be used; `detail` is the reason plus the line when known."""

    def __init__(self, path: Path, reason: str, line: int | None = None) -> None:
        self.path = path
        self.reason = reason
        self.line = line
        super().__init__(f"{path}: {self.detail}")

    @property
    def detail(self) -> str:
        return self.reason if self.line is None else f"{self.reason} (line {self.line})"


def load_yaml12(path: Path, *, max_bytes: int, what: str) -> object:
    """Load one YAML document. `what` ends the size error, e.g. "checklists are a few KB"."""
    try:
        # One stat for both checks; like Path.is_file() it follows symlinks, and it keeps a
        # FIFO or device named *.yaml from blocking or streaming forever.
        status = path.stat()
        if not stat.S_ISREG(status.st_mode):
            raise Yaml12Error(path, "not a regular file")
        if status.st_size > max_bytes:
            raise Yaml12Error(path, f"larger than {_describe_size(max_bytes)}; {what}")
        text = path.read_bytes().decode("utf-8-sig")
    except OSError as error:
        raise Yaml12Error(path, f"cannot be read: {describe_os_error(error)}") from None
    except UnicodeDecodeError:
        raise Yaml12Error(path, "not valid UTF-8 text") from None
    try:
        return yaml.load(text, Loader=_CoreSchemaLoader)
    except RecursionError:
        raise Yaml12Error(path, "nested too deeply") from None
    except yaml.MarkedYAMLError as error:
        line = error.problem_mark.line + 1 if error.problem_mark else None
        raise Yaml12Error(path, error.problem or error.context or "invalid YAML", line) from None
    except yaml.YAMLError as error:
        raise Yaml12Error(path, f"invalid YAML: {error}") from None


def _describe_size(size: int) -> str:
    mebibytes, remainder = divmod(size, 1 << 20)
    return f"{mebibytes} MiB" if mebibytes and not remainder else f"{size:,} bytes"


def _shorten(text: str) -> str:
    # Keys and values are echoed in error messages; a long one would bury the reason.
    if len(text) <= _MAX_ECHO_CHARS:
        return text
    return text[:_MAX_ECHO_CHARS] + "…"


# PyYAML calls re.match, so each pattern is anchored at the end; \Z, unlike $, rejects a trailing newline.
_BOOL = re.compile(r"(?:true|True|TRUE|false|False|FALSE)\Z")
_NULL = re.compile(r"(?:null|Null|NULL|~|)\Z")
_INT = re.compile(r"(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)\Z")
_FLOAT = re.compile(
    r"(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)\Z"
)
_SURROGATE = re.compile("[\ud800-\udfff]")


class _CoreSchemaLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 core-schema scalars, no aliases, and no duplicate keys.

    PyYAML follows YAML 1.1, where `no` is false, `12:30` is 750 and `2026-09-01` is a
    date. Values are compared with JSON data, so only JSON types may come out.
    """

    # A fresh table on the subclass: add_implicit_resolver would otherwise extend a copy of
    # SafeLoader's YAML 1.1 table, and mutating that table would change yaml.safe_load.
    yaml_implicit_resolvers: dict[Any, Any] = {}  # noqa: RUF012 - PyYAML class-level registry

    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        # Aliases make exponential documents ("billion laughs") possible; these files never need them.
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise ComposerError(None, None, "aliases are not allowed", event.start_mark)
        return super().compose_node(parent, index)

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                is_duplicate = key in seen
            except TypeError:
                continue  # SafeConstructor reports the unhashable key.
            if is_duplicate:
                raise ConstructorError(
                    None, None, f"duplicate key '{_shorten(str(key))}'", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)

    def construct_scalar(self, node: ScalarNode | MappingNode) -> Any:
        # A "\ud800" escape yields a lone surrogate, which no UTF-8 output can encode.
        value = super().construct_scalar(node)
        return _SURROGATE.sub("\ufffd", value) if isinstance(value, str) else value

    # The constructors re-check the text because an explicit tag such as `!!int` skips the
    # implicit resolvers, and Python's parsers accept YAML 1.1 forms like 1_000 and infinity.

    def construct_core_bool(self, node: ScalarNode) -> bool:
        text = str(self.construct_scalar(node))
        if not _BOOL.match(text):
            raise ConstructorError(
                None,
                None,
                f"'{_shorten(text)}' is not a boolean; use true or false",
                node.start_mark,
            )
        return text.lower() == "true"

    def construct_core_int(self, node: ScalarNode) -> int:
        text = str(self.construct_scalar(node))
        if not _INT.match(text):
            raise ConstructorError(
                None, None, f"'{_shorten(text)}' is not an integer", node.start_mark
            )
        try:
            if text.startswith("0o"):
                return int(text[2:], 8)
            if text.startswith("0x"):
                return int(text[2:], 16)
            return int(text)
        except ValueError:
            # Python refuses decimal strings over 4300 digits.
            raise ConstructorError(
                None, None, "integer has too many digits", node.start_mark
            ) from None

    def construct_core_float(self, node: ScalarNode) -> float:
        text = str(self.construct_scalar(node))
        if not _FLOAT.match(text):
            raise ConstructorError(
                None, None, f"'{_shorten(text)}' is not a float", node.start_mark
            )
        lowered = text.lower()
        if lowered.endswith(".inf"):
            return -math.inf if lowered.startswith("-") else math.inf
        if lowered == ".nan":
            return math.nan
        return float(lowered)


_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL, list("tTfF"))
_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:null", _NULL, ["n", "N", "~", ""])
_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:int", _INT, list("-+0123456789"))
_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT, list("-+.0123456789"))

# Only JSON types: explicit tags such as !!timestamp, !!binary or !!set fail as undefined.
_CoreSchemaLoader.yaml_constructors = {
    "tag:yaml.org,2002:null": SafeConstructor.construct_yaml_null,
    "tag:yaml.org,2002:bool": _CoreSchemaLoader.construct_core_bool,
    "tag:yaml.org,2002:int": _CoreSchemaLoader.construct_core_int,
    "tag:yaml.org,2002:float": _CoreSchemaLoader.construct_core_float,
    "tag:yaml.org,2002:str": SafeConstructor.construct_yaml_str,
    "tag:yaml.org,2002:seq": SafeConstructor.construct_yaml_seq,
    "tag:yaml.org,2002:map": SafeConstructor.construct_yaml_map,
    None: SafeConstructor.construct_undefined,
}
