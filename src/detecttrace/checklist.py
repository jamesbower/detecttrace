"""Load checklist YAML files: the tool calls a playbook requires for one alert class."""

import math
import os
import re
from pathlib import Path

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

from detecttrace.config import normalize_label
from detecttrace.durations import parse_duration
from detecttrace.model import InputFileError, describe_os_error
from detecttrace.yaml12 import Yaml12Error, load_yaml12

# [0-9] rather than \d: \d also matches non-ASCII digits, which int() would accept.
_PATH = re.compile(r"[^.\[\]]+((?:\.[^.\[\]]+|\[[0-9]+\])*)")
_PATH_PART = re.compile(r"\.([^.\[\]]+)|\[([0-9]+)\]")
_YAML_SUFFIXES = (".yaml", ".yml")
EXAMPLE_SUFFIX = ".yaml.example"
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
    if first == "$":
        # `$.query` would look up a key named "$" and never match; paths are already relative
        # to the top level.
        if text.startswith("$."):
            raise ValueError(
                f"path '{_shorten(text)}' is not valid; use '{_shorten(text[2:])}', "
                f"not '{_shorten(text)}'"
            )
        raise ValueError(
            f"path '{_shorten(text)}' is not valid; '$' stands alone for the top level"
        )
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

    @field_validator("in_")
    @classmethod
    def _check_not_empty(cls, value: list[JsonValue] | None) -> list[JsonValue] | None:
        if value == []:
            raise ValueError("is empty, so no value can match; list the allowed values")
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
        # An infinite bound either passes every number or none, so it is always a typo.
        if math.isinf(value):
            raise ValueError("must be a finite number")
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

    A folder is read recursively in POSIX relative-path order; only *.yaml and *.yml (any case)
    files count, and hidden files and folders are skipped. An empty folder gives {}.
    Any unusable file raises ChecklistFileError naming the file and the problem.
    """
    if not path.exists():
        raise ChecklistFileError(
            f"Checklist path not found: {path}. Check checklists in detecttrace.yaml."
        )
    checklists: dict[str, Checklist] = {}
    sources: dict[str, str] = {}
    files = [(path, path.name)] if not path.is_dir() else _list_folder(path, _YAML_SUFFIXES)
    for file_path, subject in files:
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


def find_inactive_checklists(path: Path) -> list[str]:
    """The *.yaml.example files (any case) in the checklist folder `path`, as POSIX paths
    relative to it, in sorted order; hidden files and folders are skipped.

    `init` writes its example checklist this way, and it is not measured until renamed, so
    check notes each one. A single checklist file has none.
    """
    if not path.is_dir():
        return []
    return [subject for _, subject in _list_folder(path, (EXAMPLE_SUFFIX,))]


def _list_folder(path: Path, suffixes: tuple[str, ...]) -> list[tuple[Path, str]]:
    """Return (file, subject) pairs; the subject is the POSIX path relative to `path`."""

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
            if name.startswith(".") or not name.lower().endswith(suffixes):
                continue
            file_path = Path(folder, name)
            files.append((file_path, file_path.relative_to(path).as_posix()))
    return sorted(files, key=lambda item: item[1])


def _load_file(file_path: Path, subject: str) -> Checklist:
    try:
        document = load_yaml12(file_path, max_bytes=_MAX_FILE_BYTES, what="checklists are a few KB")
    except Yaml12Error as error:
        raise ChecklistFileError(f"{subject}: {error.detail}") from None
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
