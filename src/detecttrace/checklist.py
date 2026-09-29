"""Load checklist YAML files: the tool calls a playbook requires for one alert class."""

import math
import os
import re
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
from yaml.composer import ComposerError
from yaml.constructor import ConstructorError, SafeConstructor
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node, ScalarNode

from detecttrace.config import normalize_label
from detecttrace.durations import parse_duration
from detecttrace.model import InputFileError

# [0-9] rather than \d: \d also matches non-ASCII digits, which int() would accept.
_PATH = re.compile(r"[^.\[\]]+((?:\.[^.\[\]]+|\[[0-9]+\])*)")
_PATH_PART = re.compile(r"\.([^.\[\]]+)|\[([0-9]+)\]")
_YAML_SUFFIXES = (".yaml", ".yml")


class ChecklistFileError(InputFileError):
    """A checklist path or file cannot be used: missing, unreadable, or invalid."""


def parse_path(text: str) -> tuple[str | int, ...]:
    """Split an argument path such as `filters[0].field` into keys and list indexes; `$` is the top level."""
    if text == "$":
        return ()
    match = _PATH.fullmatch(text)
    if match is None:
        raise ValueError(
            f"path '{text}' is not valid; use '$' or dot notation with list indexes, like a.b[0]"
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
            raise ValueError(f"'{value}' is not a duration; use a form like 24h, 7d or PT24H")
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
                raise ValueError(f"duplicate item id '{item.id}'")
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
                f"{subject}: alert class '{checklist.alert_class}' already has a checklist "
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
            f"{subject}: checklist folder cannot be read: {_describe_os_error(error)}"
        )

    files: list[tuple[Path, str]] = []
    # os.walk, not Path.rglob: rglob on 3.11 silently skips folders it cannot list.
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
        text = file_path.read_bytes().decode("utf-8-sig")
    except OSError as error:
        raise ChecklistFileError(
            f"{subject}: cannot be read: {_describe_os_error(error)}"
        ) from None
    except UnicodeDecodeError:
        raise ChecklistFileError(f"{subject}: not valid UTF-8 text") from None
    try:
        document = yaml.load(text, Loader=_CoreSchemaLoader)
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


def _describe_validation_error(detail: Any) -> str:
    message = str(detail["msg"]).removeprefix("Value error, ")
    location = ".".join(str(part) for part in detail["loc"])
    return f"{location}: {message}" if location else message


def _describe_os_error(error: OSError) -> str:
    # str(error) embeds the absolute path, which must not reach the dashboard.
    return error.strerror or type(error).__name__


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
                raise ConstructorError(None, None, f"duplicate key '{key}'", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)

    def construct_core_int(self, node: ScalarNode) -> int:
        text = str(self.construct_scalar(node))
        if text.startswith("0o"):
            return int(text[2:], 8)
        if text.startswith("0x"):
            return int(text[2:], 16)
        return int(text)

    def construct_core_float(self, node: ScalarNode) -> float:
        text = str(self.construct_scalar(node)).lower()
        if text.endswith(".inf"):
            return -math.inf if text.startswith("-") else math.inf
        if text == ".nan":
            return math.nan
        return float(text)


_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL, list("tTfF"))
_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:null", _NULL, ["n", "N", "~", ""])
_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:int", _INT, list("-+0123456789"))
_CoreSchemaLoader.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT, list("-+.0123456789"))

# Only JSON types: explicit tags such as !!timestamp, !!binary or !!set fail as undefined.
_CoreSchemaLoader.yaml_constructors = {
    "tag:yaml.org,2002:null": SafeConstructor.construct_yaml_null,
    "tag:yaml.org,2002:bool": SafeConstructor.construct_yaml_bool,
    "tag:yaml.org,2002:int": _CoreSchemaLoader.construct_core_int,
    "tag:yaml.org,2002:float": _CoreSchemaLoader.construct_core_float,
    "tag:yaml.org,2002:str": SafeConstructor.construct_yaml_str,
    "tag:yaml.org,2002:seq": SafeConstructor.construct_yaml_seq,
    "tag:yaml.org,2002:map": SafeConstructor.construct_yaml_map,
    None: SafeConstructor.construct_undefined,
}
