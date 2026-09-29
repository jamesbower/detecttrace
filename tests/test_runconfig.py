import sys
from pathlib import Path

import pytest

from detecttrace.config import Config, MappingConfig
from detecttrace.model import InputFileError, Verdict
from detecttrace.runconfig import ConfigFileError, RunConfig, load_run_config

APPENDIX_EXAMPLE = """\
# detecttrace.yaml
traces:
  path: ./traces/            # files or a folder
  format: otlp_jsonl         # otlp_jsonl | otlp_json | langfuse
verdicts:
  path: ./verdicts.csv
mapping:
  case_id: detecttrace.case_id
  alert_class: detecttrace.alert_class
  verdict: detecttrace.verdict
  prompt_version: detecttrace.prompt_version
  prompt_version_lookup: root_then_resource   # or: descendant
  operation:
    attribute: gen_ai.operation.name
    agent_value: invoke_agent
    tool_value: execute_tool
    span_name_fallback: true
label_map:
  "TP": true_positive
  "Malicious": true_positive
  "FP": false_positive
  "Benign": benign
  "Closed - Benign": benign
agent_label_map: {}          # optional; overrides label_map for agent verdicts
checklists: ./checklists/
output: ./detecttrace-dashboard.html
dashboard:
  max_detail_cases: 2000
telemetry: false
"""

MINIMAL = "traces: {path: traces}\nverdicts: {path: verdicts.csv}\n"


def _write(folder: Path, text: str) -> Path:
    path = folder / "detecttrace.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path: Path, text: str) -> RunConfig:
    return load_run_config(_write(tmp_path, text))


def _load_error(tmp_path: Path, text: str) -> str:
    with pytest.raises(ConfigFileError) as error:
        _load(tmp_path, text)
    return str(error.value)


def test_config_file_error_is_an_input_file_error() -> None:
    assert issubclass(ConfigFileError, InputFileError)


def test_example_traces_path_is_under_the_config_folder(tmp_path: Path) -> None:
    assert _load(tmp_path, APPENDIX_EXAMPLE).traces.path == tmp_path / "traces"


def test_example_verdicts_path_is_under_the_config_folder(tmp_path: Path) -> None:
    assert _load(tmp_path, APPENDIX_EXAMPLE).verdicts.path == tmp_path / "verdicts.csv"


def test_example_checklists_path_is_under_the_config_folder(tmp_path: Path) -> None:
    assert _load(tmp_path, APPENDIX_EXAMPLE).checklists == tmp_path / "checklists"


def test_example_output_path_is_under_the_config_folder(tmp_path: Path) -> None:
    assert _load(tmp_path, APPENDIX_EXAMPLE).output == tmp_path / "detecttrace-dashboard.html"


def test_example_label_map_is_normalized(tmp_path: Path) -> None:
    assert _load(tmp_path, APPENDIX_EXAMPLE).label_map == {
        "tp": Verdict.TRUE_POSITIVE,
        "malicious": Verdict.TRUE_POSITIVE,
        "fp": Verdict.FALSE_POSITIVE,
        "benign": Verdict.BENIGN,
        "closed - benign": Verdict.BENIGN,
    }


def test_example_gives_the_m1_config(tmp_path: Path) -> None:
    config = _load(tmp_path, APPENDIX_EXAMPLE).to_config()
    assert config == Config(
        label_map={
            "TP": Verdict.TRUE_POSITIVE,
            "Malicious": Verdict.TRUE_POSITIVE,
            "FP": Verdict.FALSE_POSITIVE,
            "Benign": Verdict.BENIGN,
            "Closed - Benign": Verdict.BENIGN,
        }
    )


def test_to_config_gives_a_plain_config(tmp_path: Path) -> None:
    assert type(_load(tmp_path, MINIMAL).to_config()) is Config


def test_relative_paths_ignore_the_working_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_folder = tmp_path / "project"
    config_folder.mkdir()
    monkeypatch.chdir(tmp_path)
    assert _load(config_folder, MINIMAL).traces.path == config_folder / "traces"


def test_relative_config_path_still_gives_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, MINIMAL)
    monkeypatch.chdir(tmp_path)
    assert load_run_config(Path("detecttrace.yaml")).verdicts.path == tmp_path / "verdicts.csv"


def test_checklists_omitted_gives_none(tmp_path: Path) -> None:
    assert _load(tmp_path, MINIMAL).checklists is None


def test_defaults_match_the_documented_example(tmp_path: Path) -> None:
    assert _load(tmp_path, MINIMAL) == _load(tmp_path, APPENDIX_EXAMPLE).model_copy(
        update={"checklists": None, "label_map": {}}
    )


def test_prompt_version_lookup_is_read(tmp_path: Path) -> None:
    config = _load(tmp_path, MINIMAL + "mapping: {prompt_version_lookup: descendant}\n")
    assert config.mapping == MappingConfig(prompt_version_lookup="descendant")


def test_tool_attributes_are_read(tmp_path: Path) -> None:
    config = _load(
        tmp_path, MINIMAL + "mapping: {tool_name: tool.name, tool_arguments: tool.parameters}\n"
    )
    assert config.mapping == MappingConfig(tool_name="tool.name", tool_arguments="tool.parameters")


def test_non_text_tool_name_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, MINIMAL + "mapping: {tool_name: 5}\n")
    assert "mapping.tool_name: Input should be a valid string" in message


def test_max_detail_cases_is_read(tmp_path: Path) -> None:
    config = _load(tmp_path, MINIMAL + "dashboard: {max_detail_cases: 50}\n")
    assert config.dashboard.max_detail_cases == 50


def test_otlp_json_format_is_accepted(tmp_path: Path) -> None:
    config = _load(tmp_path, "traces: {path: t, format: otlp_json}\nverdicts: {path: v.csv}\n")
    assert config.traces.format == "otlp_json"


def test_telemetry_true_is_accepted(tmp_path: Path) -> None:
    assert _load(tmp_path, MINIMAL + "telemetry: true\n").telemetry is True


def test_unknown_key_names_the_key_and_the_file(tmp_path: Path) -> None:
    message = _load_error(tmp_path, MINIMAL + "outptu: x.html\n")
    assert message.startswith(f"{tmp_path / 'detecttrace.yaml'}: invalid configuration\n  outptu:")


def test_unknown_nested_key_names_its_location(tmp_path: Path) -> None:
    message = _load_error(tmp_path, MINIMAL + "dashboard: {max_cases: 5}\n")
    assert "\n  dashboard.max_cases: Extra inputs are not permitted" in message


def test_unknown_mapping_key_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, MINIMAL + "mapping: {caseid: x}\n")
    assert "\n  mapping.caseid: Extra inputs are not permitted" in message


def test_langfuse_format_is_not_supported_yet(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "traces: {path: t, format: langfuse}\nverdicts: {path: v}\n")
    assert "traces.format: 'langfuse' is not supported yet" in message


def test_unknown_format_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "traces: {path: t, format: csv}\nverdicts: {path: v}\n")
    assert "\n  traces.format: " in message


def test_missing_traces_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "verdicts: {path: v}\n")
    assert "\n  traces: Field required" in message


def test_colliding_label_map_keys_give_the_m1_message(tmp_path: Path) -> None:
    message = _load_error(
        tmp_path, MINIMAL + "label_map: {Benign: benign, 'benign ': false_positive}\n"
    )
    assert message.endswith(
        "\n  label_map: labels 'Benign' and 'benign ' are the same after normalization "
        "but map to 'benign' and 'false_positive'. Keep only one of them."
    )


def test_colliding_agent_label_map_keys_are_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, MINIMAL + "agent_label_map: {FP: benign, fp: false_positive}\n")
    assert "same after normalization" in message


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (MINIMAL + "telemetry: false\ntelemetry: true\n", r"duplicate key 'telemetry' \(line 4\)"),
        ("traces: &t {path: t}\nverdicts: *t\n", "aliases are not allowed"),
        (MINIMAL + "output: !!python/object:os.system x\n", "could not determine a constructor"),
        (MINIMAL + "telemetry: yes\n", "telemetry: Input should be a valid boolean"),
        (
            MINIMAL + "dashboard: {max_detail_cases: -1}\n",
            "max_detail_cases: Input should be greater",
        ),
        ("- a\n- b\n", "expected a mapping with 'traces' and 'verdicts'"),
        ("", "expected a mapping with 'traces' and 'verdicts'"),
    ],
    ids=["duplicate-key", "alias", "python-tag", "telemetry-yes", "negative-max", "list", "empty"],
)
def test_unusable_content_raises(tmp_path: Path, text: str, reason: str) -> None:
    with pytest.raises(ConfigFileError, match=rf"(?s)detecttrace\.yaml: .*{reason}"):
        _load(tmp_path, text)


def test_file_over_one_mebibyte_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match=r"detecttrace\.yaml: larger than 1 MiB"):
        _load(tmp_path, MINIMAL + "# " + "x" * (1 << 20))


def test_non_utf8_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "detecttrace.yaml"
    path.write_bytes(MINIMAL.encode() + b"output: caf\xe9\n")
    with pytest.raises(ConfigFileError, match=r"detecttrace\.yaml: not valid UTF-8 text"):
        load_run_config(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match=r"missing\.yaml: cannot be read"):
        load_run_config(tmp_path / "missing.yaml")


def test_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match=r": not a regular file"):
        load_run_config(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX absolute paths")
def test_absolute_paths_are_kept(tmp_path: Path) -> None:
    config = _load(tmp_path, "traces: {path: /srv/traces}\nverdicts: {path: v}\n")
    assert config.traces.path == Path("/srv/traces")


@pytest.mark.parametrize(
    "text",
    [
        MINIMAL + "mapping: {operation: {span_name_fallback: yes}}\n",
        MINIMAL + "mapping: {operation: {span_name_fallback: 1}}\n",
        MINIMAL + "mapping: {operation: {span_name_fallback: 'true'}}\n",
    ],
    ids=["yes", "one", "quoted-true"],
)
def test_span_name_fallback_must_be_a_real_boolean(tmp_path: Path, text: str) -> None:
    with pytest.raises(
        ConfigFileError, match="span_name_fallback: Input should be a valid boolean"
    ):
        _load(tmp_path, text)


@pytest.mark.parametrize(
    "value",
    ["'10'", "10.0", "true"],
    ids=["quoted", "float", "boolean"],
)
def test_max_detail_cases_must_be_a_real_integer(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigFileError, match="max_detail_cases: Input should be a valid integer"):
        _load(tmp_path, MINIMAL + f"dashboard: {{max_detail_cases: {value}}}\n")


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("traces: {path: ''}\nverdicts: {path: v}\n", "traces.path"),
        ("traces: {path: t}\nverdicts: {path: '  '}\n", "verdicts.path"),
        (MINIMAL + "checklists: ''\n", "checklists"),
        (MINIMAL + "output: ' '\n", "output"),
    ],
    ids=["traces", "verdicts", "checklists", "output"],
)
def test_empty_path_is_rejected(tmp_path: Path, text: str, location: str) -> None:
    with pytest.raises(ConfigFileError, match=rf"\n  {location}: path is empty"):
        _load(tmp_path, text)


@pytest.mark.parametrize("output", ["/", ".", "..", "results/..", "'results/.'"])
def test_output_without_a_file_name_is_rejected(tmp_path: Path, output: str) -> None:
    with pytest.raises(ConfigFileError, match=r"\n  output: path must end in a file name"):
        _load(tmp_path, MINIMAL + f"output: {output}\n")


def test_output_ending_in_a_file_name_is_accepted(tmp_path: Path) -> None:
    assert _load(tmp_path, MINIMAL + "output: ../results/run.html\n").output.name == "run.html"


def test_dot_path_is_the_config_folder(tmp_path: Path) -> None:
    assert _load(tmp_path, "traces: {path: .}\nverdicts: {path: v}\n").traces.path == tmp_path


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("traces: {path: ~/traces}\nverdicts: {path: v}\n", "traces.path"),
        ("traces: {path: t}\nverdicts: {path: '~'}\n", "verdicts.path"),
        (MINIMAL + "checklists: '~/checklists'\n", "checklists"),
        (MINIMAL + "output: '~/out.html'\n", "output"),
    ],
    ids=["traces", "verdicts", "checklists", "output"],
)
def test_tilde_as_the_first_part_is_rejected(tmp_path: Path, text: str, location: str) -> None:
    with pytest.raises(
        ConfigFileError,
        match=rf"\n  {location}: `~` is not expanded; "
        "write the full path or a path relative to this file",
    ):
        _load(tmp_path, text)


def test_tilde_inside_a_name_is_an_ordinary_name(tmp_path: Path) -> None:
    config = _load(tmp_path, "traces: {path: ~traces/a}\nverdicts: {path: v}\n")
    assert config.traces.path == tmp_path / "~traces" / "a"


def test_run_config_is_frozen(tmp_path: Path) -> None:
    config = _load(tmp_path, MINIMAL)
    with pytest.raises(ValueError, match="frozen"):
        config.telemetry = True  # type: ignore[misc]
