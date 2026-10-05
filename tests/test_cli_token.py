from pathlib import Path

import yaml
from typer.testing import CliRunner, Result

from detecttrace import cli
from detecttrace.serve.auth import hash_token
from detecttrace.serve.config import load_serve_config


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(cli.app, ["token", *args])


def _stdout_lines(result: Result) -> list[str]:
    return result.stdout.splitlines()


def test_prints_the_token_on_its_own_line() -> None:
    result = _invoke("--role", "read", "--name", "team")

    assert len(_stdout_lines(result)[0]) >= 43


def test_entry_hash_matches_the_printed_token() -> None:
    result = _invoke("--role", "read", "--name", "team")

    lines = _stdout_lines(result)
    entry = yaml.safe_load(lines[1])[0]
    assert entry["hash"] == hash_token(lines[0])


def test_entry_carries_the_given_name() -> None:
    result = _invoke("--role", "verdicts", "--name", "soar.prod")

    assert yaml.safe_load(_stdout_lines(result)[1])[0]["name"] == "soar.prod"


def test_says_the_token_is_shown_once() -> None:
    result = _invoke("--role", "ingest", "--name", "collector")

    assert "shown only once" in result.stderr


def test_success_exits_zero() -> None:
    assert _invoke("--role", "ingest", "--name", "collector").exit_code == 0


def test_two_runs_print_different_tokens() -> None:
    first = _invoke("--role", "read", "--name", "team")
    second = _invoke("--role", "read", "--name", "team")

    assert _stdout_lines(first)[0] != _stdout_lines(second)[0]


def test_unknown_role_exits_one() -> None:
    assert _invoke("--role", "admin", "--name", "team").exit_code == 1


def test_unknown_role_says_which_roles_exist() -> None:
    assert "ingest, verdicts, read" in _invoke("--role", "admin", "--name", "team").stderr


def test_unknown_role_prints_no_token() -> None:
    assert _invoke("--role", "admin", "--name", "team").stdout == ""


def test_bad_name_exits_one() -> None:
    assert _invoke("--role", "read", "--name", "has space").exit_code == 1


def test_bad_name_says_what_is_allowed() -> None:
    assert "--name must be 1 to 64" in _invoke("--role", "read", "--name", "a" * 65).stderr


def test_missing_option_exits_one() -> None:
    assert _invoke("--role", "read").exit_code == 1


def test_printed_entry_with_a_numeric_looking_name_loads_as_that_name(tmp_path: Path) -> None:
    result = _invoke("--role", "read", "--name", "2026")
    entry = _stdout_lines(result)[1]
    other = '- {name: "other", hash: "sha256:' + "a" * 64 + '"}'
    config_path = tmp_path / "serve.yaml"
    config_path.write_text(
        "serve: {database: x.db}\n"
        f"tokens:\n  ingest:\n    {other}\n  verdicts:\n    {other}\n  read:\n    {entry}\n",
        encoding="utf-8",
    )

    assert load_serve_config(config_path).tokens.read[0].name == "2026"
