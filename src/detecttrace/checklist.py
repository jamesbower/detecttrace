"""Load checklist YAML files: the tool calls a playbook requires for one alert class."""

import math
import os
import re
import stat
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import ErrorDetails
from yaml.composer import ComposerError
from yaml.constructor import ConstructorError, SafeConstructor
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node, ScalarNode

from detecttrace.config import normalize_label
from detecttrace.durations import parse_duration
from detecttrace.model import InputFileError, describe_os_error

# [0-9] rather than \d: \d also matches non-ASCII digits, which int() would accept.
_PATH = re.compile(r"[^.\[\]]+((?:\.[^.\[\]]+|\[[0-9]+\])*)")
_PATH_PART = re.compile(r"\.([^.\[\]]+)|\[([0-9]+)\]")
_YAML_SUFFIXES = (".yaml", ".yml")
# Real checklists are a few KB, and PyYAML's pure-Python parser takes seconds per megabyte.
_MAX_FILE_BYTES = 1 << 20
_MAX_ECHO_CHARS = 60
_JSON_VALUE_TAGS = frozenset({"list", "dict", "str", "bool", "int", "float"})


class ChecklistFileError(InputFileError):
    """A checklist path or file cannot be used: missing, unreadable, or invalid."""


def parse_path(text: str) -> tuple[str | int, ...]:
    """Split an argument path such as `filters[0].field` into keys and list indexes; `$` is the top level."""
    if text == "$":
        return ()
    match = _PATH.fullmatch(text)
    if match is None:
        raise ValueError(
            f"path '{_shorten(text)}' is not valid; use '$' or dot notation with list indexes, like a.b[0]"
        )
    first = text[: match.start(1)]
    parts: list[str | int] = [first]
    for key, index in _PATH_PART.findall(match[1]):
        parts.append(int(index) if index else key)
    return tuple(parts)


class ArgRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    equals: JsonValue = None
    in_: list[JsonValue] | None = Field(default=None, alias="in")
    exists: StrictBool | None = None
    matches: str | None = None
    min_duration: str | None = None
    kql_min_ago: str | None = None
    min: int | float | None = None
    max: int | float | None = None
    start: str | None = None
    end: str | None = None

    @field_validator("equals", "in_")
    @classmethod
    def _check_no_nan(cls, value: JsonValue) -> JsonValue:
        # Arguments are compared strictly, and NaN never equals anything, so the rule could never pass.
        if _contains_nan(value):
            raise ValueError("NaN never matches any argument value; remove it")
        return value

    @field_validator("matches")
    @classmethod
    def _check_regex(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as error:
                raise ValueError(f"not a valid regular expression: {error}") from None
        return value

    @field_validator("min_duration", "kql_min_ago")
    @classmethod
    def _check_duration(cls, value: str | None) -> str | None:
        if value is not None and parse_duration(value) is None:
            raise ValueError(
                f"'{_shorten(value)}' is not a duration; use a form like 24h, 7d or PT24H"
            )
        return value

    @field_validator("min", "max", mode="before")
    @classmethod
    def _check_number(cls, value: object) -> object:
        # Checked before Pydantic's int | float union, which would accept "24" and true.
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("must be a number")
        if math.isnan(value):
            raise ValueError("must be a number, not NaN")
        return value

    @field_validator("start", "end")
    @classmethod
    def _check_path(cls, value: str | None) -> str | None:
        if value is not None:
            parse_path(value)
        return value

    @model_validator(mode="after")
    def _check_rules(self) -> "ArgRule":
        given = self.model_fields_set
        # Only `equals` gives null a meaning; for the others it would silently disable the rule.
        for name in sorted(given - {"equals"}):
            if getattr(self, name) is None:
                raise ValueError(f"'{_yaml_name(name)}' must not be null")
        if not given - {"start", "end"}:
            raise ValueError(
                "no rule given; use equals, in, exists, matches, min_duration, kql_min_ago, min or max"
            )
        if ("start" in given) != ("end" in given):
            raise ValueError("'start' and 'end' must be given together")
        if "start" in given and "min_duration" not in given:
            raise ValueError("'start' and 'end' are only used with 'min_duration'")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"'min' ({self.min}) is greater than 'max' ({self.max})")
        return self


class ChecklistItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tool: str
    args: dict[str, ArgRule] = Field(default_factory=dict)

    @field_validator("id", "tool")
    @classmethod
    def _check_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("args")
    @classmethod
    def _check_paths(cls, value: dict[str, ArgRule]) -> dict[str, ArgRule]:
        for path in value:
            parse_path(path)
        return value


class Checklist(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_class: str
    items: tuple[ChecklistItem, ...]

    @field_validator("alert_class")
    @classmethod
    def _check_class(cls, value: str) -> str:
        if not normalize_label(value):
            raise ValueError("must not be empty")
        return value

    @field_validator("items")
    @classmethod
    def _check_items(cls, value: tuple[ChecklistItem, ...]) -> tuple[ChecklistItem, ...]:
        if not value:
            raise ValueError("needs at least one item")
        seen: set[str] = set()
        for item in value:
            if item.id in seen:
                raise ValueError(f"duplicate item id '{_shorten(item.id)}'")
            seen.add(item.id)
        return value


def load_checklists(path: Path) -> dict[str, Checklist]:
    """Load every checklist at `path` (a file or a folder), keyed by the normalized alert class.

    A folder is read recursively in POSIX relative-path order; only *.yaml and *.yml
    files count, and hidden files and folders are skipped. An empty folder gives {}.
    Any unusable file raises ChecklistFileError naming the file and the problem.
    """
    if not path.exists():
        raise ChecklistFileError(
            f"Checklist path not found: {path}. Check checklists in detecttrace.yaml."
        )
    checklists: dict[str, Checklist] = {}
    sources: dict[str, str] = {}
    for file_path, subject in _list_checklist_files(path):
        checklist = _load_file(file_path, subject)
        key = normalize_label(checklist.alert_class)
        if key in sources:
            raise ChecklistFileError(
                f"{subject}: alert class '{_shorten(checklist.alert_class)}' already has a checklist "
                f"in {sources[key]}. Keep one file per alert class."
            )
        sources[key] = subject
        checklists[key] = checklist
    return checklists


def _list_checklist_files(path: Path) -> list[tuple[Path, str]]:
    """Return (file, subject) pairs; the subject is the POSIX path relative to `path`."""
    if not path.is_dir():
        return [(path, path.name)]

    def report(error: OSError) -> None:
        folder = Path(error.filename)
        subject = "." if folder == path else folder.relative_to(path).as_posix()
        raise ChecklistFileError(
            f"{subject}: checklist folder cannot be read: {describe_os_error(error)}"
        )

    files: list[tuple[Path, str]] = []
    # os.walk, not Path.rglob: rglob on 3.11 silently skips folders it cannot list.
    # Symlinked subfolders are not followed (os.walk's default), as in the trace loader,
    # so a link back to a parent folder cannot loop forever.
    for folder, folder_names, file_names in os.walk(path, onerror=report):
        folder_names[:] = sorted(name for name in folder_names if not name.startswith("."))
        for name in file_names:
            if name.startswith(".") or not name.endswith(_YAML_SUFFIXES):
                continue
            file_path = Path(folder, name)
            files.append((file_path, file_path.relative_to(path).as_posix()))
    return sorted(files, key=lambda item: item[1])


def _load_file(file_path: Path, subject: str) -> Checklist:
    try:
        # One stat for both checks; like Path.is_file() it follows symlinks, and it keeps a
        # FIFO or device named *.yaml from blocking or streaming forever.
        status = file_path.stat()
        if not stat.S_ISREG(status.st_mode):
            raise ChecklistFileError(f"{subject}: not a regular file")
        if status.st_size > _MAX_FILE_BYTES:
            raise ChecklistFileError(f"{subject}: larger than 1 MiB; checklists are a few KB")
        text = file_path.read_bytes().decode("utf-8-sig")
    except OSError as error:
        raise ChecklistFileError(f"{subject}: cannot be read: {describe_os_error(error)}") from None
    except UnicodeDecodeError:
        raise ChecklistFileError(f"{subject}: not valid UTF-8 text") from None
    try:
        document = yaml.load(text, Loader=_CoreSchemaLoader)
    except RecursionError:
        raise ChecklistFileError(f"{subject}: nested too deeply") from None
    except yaml.MarkedYAMLError as error:
        where = f" (line {error.problem_mark.line + 1})" if error.problem_mark else ""
        reason = error.problem or error.context or "invalid YAML"
        raise ChecklistFileError(f"{subject}: {reason}{where}") from None
    except yaml.YAMLError as error:
        raise ChecklistFileError(f"{subject}: invalid YAML: {error}") from None
    if not isinstance(document, dict):
        raise ChecklistFileError(
            f"{subject}: expected a mapping with 'alert_class' and 'items' at the top level"
        )
    try:
        return Checklist.model_validate(document)
    except ValidationError as error:
        lines = [_describe_validation_error(detail) for detail in error.errors()]
        raise ChecklistFileError(f"{subject}: invalid checklist\n  " + "\n  ".join(lines)) from None


def _describe_validation_error(detail: ErrorDetails) -> str:
    message = str(detail["msg"]).removeprefix("Value error, ")
    location = ".".join(str(part) for part in _drop_json_value_tags(detail["loc"]))
    return f"{location}: {message}" if location else message


def _drop_json_value_tags(location: tuple[int | str, ...]) -> list[int | str]:
    # JsonValue is a tagged union, so Pydantic puts a type tag such as "dict" before every key
    # or index inside `equals` and `in` values. Tags and keys alternate, so a user key that
    # happens to be named "dict" is kept.
    parts = list(location)
    if len(parts) < 5 or parts[0] != "items" or parts[2] != "args":
        return parts
    start = {"equals": 5, "in": 6}.get(str(parts[4]))
    if start is None:
        return parts
    inner = [
        part
        for offset, part in enumerate(parts[start:])
        if offset % 2 or part not in _JSON_VALUE_TAGS
    ]
    return parts[:start] + inner


def _contains_nan(value: JsonValue) -> bool:
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(value, list):
        return any(_contains_nan(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_nan(item) for item in value.values())
    return False


def _shorten(text: str) -> str:
    # Values are echoed in error messages; a long one would bury the reason.
    if len(text) <= _MAX_ECHO_CHARS:
        return text
    return text[:_MAX_ECHO_CHARS] + "…"


def _yaml_name(field_name: str) -> str:
    return "in" if field_name == "in_" else field_name


# YAML loading

# PyYAML calls re.match, so each pattern is anchored at the end; \Z, unlike $, rejects a trailing newline.
_BOOL = re.compile(r"(?:true|True|TRUE|false|False|FALSE)\Z")
_NULL = re.compile(r"(?:null|Null|NULL|~|)\Z")
_INT = re.compile(r"(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)\Z")
_FLOAT = re.compile(
    r"(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)\Z"
)


class _CoreSchemaLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 core-schema scalars, no aliases, and no duplicate keys.

    PyYAML follows YAML 1.1, where `no` is false, `12:30` is 750 and `2026-09-01` is a
    date. Checklist values are compared with JSON arguments, so only JSON types may come out.
    """

    # A fresh table on the subclass: add_implicit_resolver would otherwise extend a copy of
    # SafeLoader's YAML 1.1 table, and mutating that table would change yaml.safe_load.
    yaml_implicit_resolvers: dict[Any, Any] = {}  # noqa: RUF012 - PyYAML class-level registry

    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        # Aliases make exponential documents ("billion laughs") possible; checklists never need them.
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
