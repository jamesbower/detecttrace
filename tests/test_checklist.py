import math
import os
import sys
from pathlib import Path

import pytest
import yaml

from detecttrace.checklist import ChecklistFileError, load_checklists, parse_path
from detecttrace.model import InputFileError

needs_permissions = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that the current user cannot bypass",
)

EXAMPLE = """\
alert_class: impossible_travel
items:
  - id: signin_history
    tool: get_signin_logs
    args:
      range: { min_duration: 24h }
  - id: signin_query
    tool: query_sentinel
    args:
      query: { kql_min_ago: 24h }
  - id: mfa_check
    tool: check_mfa_status
  - id: ip_reputation
    tool: get_ip_reputation
  - id: audit_window
    tool: get_audit_logs
    args:
      window: { min_duration: 24h, start: start, end: end }
  - id: device_window
    tool: get_device_events
    args:
      $: { min_duration: 24h, start: start_time, end: end_time }
  - id: tenant_scope
    tool: get_signin_logs
    args:
      tenant: { in: ["example-prod", "example-dev"] }
"""


def _write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _one_item(args: str) -> str:
    return f"alert_class: phishing\nitems:\n  - id: a\n    tool: t\n    args:\n{args}"


def _rule(tmp_path: Path, rule: str):
    path = _write(tmp_path, "c.yaml", _one_item(f"      x: {rule}\n"))
    return load_checklists(path)["phishing"].items[0].args["x"]


# Loading


def test_loads_the_documented_example_with_item_ids_in_order(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", EXAMPLE)
    checklist = load_checklists(path)["impossible_travel"]
    assert [item.id for item in checklist.items] == [
        "signin_history",
        "signin_query",
        "mfa_check",
        "ip_reputation",
        "audit_window",
        "device_window",
        "tenant_scope",
    ]


def test_loads_every_class_file_in_a_folder_under_normalized_keys(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", "alert_class: Phishing\nitems:\n  - {id: a, tool: t}\n")
    _write(tmp_path, "sub/b.yml", "alert_class: Impossible  Travel\nitems:\n  - {id: a, tool: t}\n")
    assert sorted(load_checklists(tmp_path)) == ["impossible travel", "phishing"]


def test_normalizes_the_alert_class_key(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "c.yaml", 'alert_class: " Impossible_Travel "\nitems:\n  - {id: a, tool: t}\n'
    )
    assert list(load_checklists(path)) == ["impossible_travel"]


def test_keeps_the_alert_class_as_written(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", "alert_class: Phishing\nitems:\n  - {id: a, tool: t}\n")
    assert load_checklists(path)["phishing"].alert_class == "Phishing"


def test_skips_hidden_files_and_folders(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", "alert_class: phishing\nitems:\n  - {id: a, tool: t}\n")
    _write(tmp_path, ".hidden.yaml", "not: [valid")
    _write(tmp_path, ".git/config.yaml", "not: [valid")
    assert list(load_checklists(tmp_path)) == ["phishing"]


def test_skips_files_that_are_not_yaml(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", "alert_class: phishing\nitems:\n  - {id: a, tool: t}\n")
    _write(tmp_path, "README.md", "not: [valid")
    assert list(load_checklists(tmp_path)) == ["phishing"]


def test_empty_folder_means_no_checklists(tmp_path: Path) -> None:
    assert load_checklists(tmp_path) == {}


def test_item_without_args_has_no_rules(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", "alert_class: phishing\nitems:\n  - {id: a, tool: t}\n")
    assert load_checklists(path)["phishing"].items[0].args == {}


def test_reads_a_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_bytes(b"\xef\xbb\xbfalert_class: phishing\nitems:\n  - {id: a, tool: t}\n")
    assert list(load_checklists(path)) == ["phishing"]


# File errors


def test_checklist_file_error_is_an_input_file_error() -> None:
    assert issubclass(ChecklistFileError, InputFileError)


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ChecklistFileError, match="not found"):
        load_checklists(tmp_path / "missing")


def test_yaml_syntax_error_names_the_file_and_line(tmp_path: Path) -> None:
    _write(tmp_path, "sub/bad.yaml", "alert_class: phishing\nitems: [\n  - a: b\n")
    with pytest.raises(ChecklistFileError, match=r"sub/bad\.yaml.*line \d+"):
        load_checklists(tmp_path)


def test_duplicate_yaml_key_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "c.yaml", "alert_class: phishing\nitems:\n  - id: a\n    tool: t\n    tool: u\n"
    )
    with pytest.raises(ChecklistFileError, match="duplicate key"):
        load_checklists(path)


@pytest.mark.parametrize(
    "text",
    [
        "alert_class: phishing\nitems:\n  - {id: a, too: t}\n",
        _one_item("      x: { regex: a }\n"),
        "alert_class: phishing\nitems:\n  - {id: a, tool: t}\nextra: 1\n",
        _one_item("      x: { in_: [a] }\n"),
    ],
    ids=["unknown-field", "unknown-rule", "unknown-top-level", "python-field-name"],
)
def test_unknown_field_or_rule_raises(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, "c.yaml", text)
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(path)


def test_duplicate_item_ids_raise(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "c.yaml",
        "alert_class: phishing\nitems:\n  - {id: a, tool: t}\n  - {id: a, tool: u}\n",
    )
    with pytest.raises(ChecklistFileError, match="duplicate item id"):
        load_checklists(path)


@pytest.mark.parametrize(
    "text",
    ["alert_class: phishing\nitems: []\n", "alert_class: phishing\n"],
    ids=["empty-list", "missing"],
)
def test_checklist_without_items_raises(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, "c.yaml", text)
    with pytest.raises(ChecklistFileError, match="items"):
        load_checklists(path)


@pytest.mark.parametrize(
    "text",
    [
        "alert_class: phishing\nitems:\n  - {id: ' ', tool: t}\n",
        "alert_class: phishing\nitems:\n  - {id: a, tool: ''}\n",
        "alert_class: '  '\nitems:\n  - {id: a, tool: t}\n",
        "alert_class: 12\nitems:\n  - {id: a, tool: t}\n",
    ],
    ids=["blank-id", "blank-tool", "blank-class", "numeric-class"],
)
def test_blank_or_non_string_names_raise(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, "c.yaml", text)
    with pytest.raises(ChecklistFileError):
        load_checklists(path)


def test_two_files_for_the_same_class_name_both_files(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", "alert_class: Phishing\nitems:\n  - {id: a, tool: t}\n")
    _write(tmp_path, "b/c.yml", "alert_class: ' phishing '\nitems:\n  - {id: a, tool: t}\n")
    with pytest.raises(ChecklistFileError, match=r"b/c\.yml.*a\.yaml"):
        load_checklists(tmp_path)


@pytest.mark.parametrize("text", ["- a\n- b\n", "", "# only a comment\n", "just text\n"])
def test_top_level_that_is_not_a_mapping_raises(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, "c.yaml", text)
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(path)


def test_non_utf8_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_bytes(b"alert_class: caf\xe9\n")
    with pytest.raises(ChecklistFileError, match="UTF-8"):
        load_checklists(path)


def test_folder_named_like_a_yaml_file_is_read_as_a_folder(tmp_path: Path) -> None:
    _write(tmp_path, "x.yaml/c.yaml", "alert_class: phishing\nitems:\n  - {id: a, tool: t}\n")
    assert list(load_checklists(tmp_path)) == ["phishing"]


def test_broken_symlink_raises(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").symlink_to(tmp_path / "missing.yaml")
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(tmp_path)


@needs_permissions
def test_unreadable_file_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", "alert_class: phishing\nitems:\n  - {id: a, tool: t}\n")
    path.chmod(0)
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(tmp_path)


@needs_permissions
def test_unreadable_subfolder_raises(tmp_path: Path) -> None:
    folder = tmp_path / "sub"
    _write(folder, "c.yaml", "alert_class: phishing\nitems:\n  - {id: a, tool: t}\n")
    folder.chmod(0)
    try:
        with pytest.raises(ChecklistFileError, match="sub"):
            load_checklists(tmp_path)
    finally:
        folder.chmod(0o755)


def test_pydantic_errors_are_condensed_to_location_and_message(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", _one_item("      x: { min: '24' }\n"))
    with pytest.raises(ChecklistFileError, match=r"items\.0\.args\.x\.min: "):
        load_checklists(path)


# Hostile input


def test_yaml_aliases_are_rejected(tmp_path: Path) -> None:
    bomb = (
        "a: &a [x, x, x, x, x, x, x, x, x]\n"
        "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        "d: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
        "e: &e [*d, *d, *d, *d, *d, *d, *d, *d, *d]\n"
        "f: &f [*e, *e, *e, *e, *e, *e, *e, *e, *e]\n"
        "g: &g [*f, *f, *f, *f, *f, *f, *f, *f, *f]\n"
        "h: &h [*g, *g, *g, *g, *g, *g, *g, *g, *g]\n"
        "i: &i [*h, *h, *h, *h, *h, *h, *h, *h, *h]\n"
    )
    path = _write(tmp_path, "c.yaml", bomb)
    with pytest.raises(ChecklistFileError, match="alias"):
        load_checklists(path)


def test_explicit_timestamp_tag_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", _one_item("      x: { equals: !!timestamp 2026-09-01 }\n"))
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(path)


def test_python_object_tag_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", "!!python/object/apply:os.system [echo hi]\n")
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(path)


def test_five_megabyte_file_loads(tmp_path: Path) -> None:
    value = "x" * 5_000_000
    rule = _rule(tmp_path, f"{{ equals: {value} }}")
    assert rule.equals == value


def test_five_megabyte_invalid_file_raises_a_short_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "c.yaml", "x" * 5_000_000)
    with pytest.raises(ChecklistFileError) as error:
        load_checklists(path)
    assert len(str(error.value)) < 500


def test_catastrophic_regex_is_accepted_at_load(tmp_path: Path) -> None:
    # Matching time is not bounded here; that is the evidence stage's concern.
    assert _rule(tmp_path, "{ matches: '(a+)+$' }").matches == "(a+)+$"


# Rules


def test_unquoted_date_stays_a_string(tmp_path: Path) -> None:
    assert _rule(tmp_path, "{ equals: 2026-09-01 }").equals == "2026-09-01"


def test_equals_null_is_distinguishable_from_absent(tmp_path: Path) -> None:
    assert "equals" in _rule(tmp_path, "{ equals: null }").model_fields_set


def test_in_keeps_yaml_11_booleans_as_strings(tmp_path: Path) -> None:
    assert _rule(tmp_path, "{ in: [on, off, no, yes] }").in_ == ["on", "off", "no", "yes"]


@pytest.mark.parametrize(("text", "expected"), [("True", True), ("FALSE", False)])
def test_core_schema_booleans_load(tmp_path: Path, text: str, expected: bool) -> None:
    assert _rule(tmp_path, f"{{ equals: {text} }}").equals is expected


@pytest.mark.parametrize(
    "text",
    ["12:30", "no", "yes", "on", "off", "1_000", "tRuE", "0b101", "0x", "1e", ".Inf5", "nan"],
)
def test_yaml_11_scalars_stay_strings(tmp_path: Path, text: str) -> None:
    assert _rule(tmp_path, f"{{ equals: {text} }}").equals == text


@pytest.mark.parametrize("text", ["null", "Null", "NULL", "~", ""])
def test_core_schema_nulls_load(tmp_path: Path, text: str) -> None:
    assert _rule(tmp_path, f"{{ equals: {text} }}").equals is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0x1A", 26),
        ("1e3", 1000.0),
        (".inf", math.inf),
        ("-.INF", -math.inf),
        ("0o17", 15),
        ("-5", -5),
        ("+7", 7),
        ("010", 10),
        ("1.5", 1.5),
        (".5", 0.5),
        ("2.", 2.0),
    ],
)
def test_core_schema_numbers_load(tmp_path: Path, text: str, expected: float) -> None:
    assert _rule(tmp_path, f"{{ equals: {text} }}").equals == expected


def test_nan_loads_as_a_float(tmp_path: Path) -> None:
    assert repr(_rule(tmp_path, "{ equals: .NaN }").equals) == "nan"


def test_min_and_max_load(tmp_path: Path) -> None:
    rule = _rule(tmp_path, "{ min: 24, max: 48.5 }")
    assert (rule.min, rule.max) == (24, 48.5)


def test_exists_loads(tmp_path: Path) -> None:
    assert _rule(tmp_path, "{ exists: false }").exists is False


def test_in_accepts_nested_json_values(tmp_path: Path) -> None:
    assert _rule(tmp_path, "{ in: [[1, 2], {a: null}] }").in_ == [[1, 2], {"a": None}]


def test_kql_min_ago_loads(tmp_path: Path) -> None:
    assert _rule(tmp_path, "{ kql_min_ago: 7d }").kql_min_ago == "7d"


@pytest.mark.parametrize(
    "rule",
    [
        "{ matches: '(' }",
        "{ min_duration: 24 }",
        "{ min_duration: soon }",
        "{ kql_min_ago: soon }",
        "{ kql_min_ago: 24 }",
        "{ min_duration: 24h, start: s }",
        "{ min_duration: 24h, end: e }",
        "{ equals: 1, start: s, end: e }",
        "{ min_duration: 24h, start: 'a..b', end: e }",
        "{ min: true }",
        "{ min: '24' }",
        "{ min: 48, max: 24 }",
        "{ max: .nan }",
        "{ exists: 'yes' }",
        "{ exists: 1 }",
        "{ in: a }",
        "{ in: null }",
        "{ matches: null }",
        "{}",
        "null",
    ],
)
def test_invalid_rule_raises(tmp_path: Path, rule: str) -> None:
    path = _write(tmp_path, "c.yaml", _one_item(f"      x: {rule}\n"))
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(path)


@pytest.mark.parametrize(
    "key", ["'a..b'", "'a[x]'", "'[0]'", "''", "'a.'", "'a['", "'a]'", "'a[-1]'", "1"]
)
def test_bad_path_key_raises(tmp_path: Path, key: str) -> None:
    path = _write(tmp_path, "c.yaml", _one_item(f"      {key}: {{ exists: true }}\n"))
    with pytest.raises(ChecklistFileError, match=r"c\.yaml"):
        load_checklists(path)


# Paths


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$", ()),
        ("a", ("a",)),
        ("a.b[0].c", ("a", "b", 0, "c")),
        ("filters[10]", ("filters", 10)),
        ("a[0][1]", ("a", 0, 1)),
        ("time range.$x", ("time range", "$x")),
    ],
)
def test_parse_path_reads_valid_paths(text: str, expected: tuple[str | int, ...]) -> None:
    assert parse_path(text) == expected


@pytest.mark.parametrize(
    "text", ["", "a..b", "a[x]", "[0]", "a.", "a[", "a]", "a[-1]", ".a", "a[٣]"]
)
def test_parse_path_rejects_invalid_paths(text: str) -> None:
    with pytest.raises(ValueError, match="path"):
        parse_path(text)


# The loader must not change PyYAML's own SafeLoader.


def test_safe_loader_still_reads_yaml_11_booleans() -> None:
    assert yaml.safe_load("no") is False
