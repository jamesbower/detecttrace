from pathlib import Path

import pytest

from detecttrace.yaml12 import Yaml12Error, load_yaml12

ONE_MEBIBYTE = 1 << 20


def _load(tmp_path: Path, text: str) -> object:
    path = tmp_path / "x.yaml"
    path.write_text(text, encoding="utf-8")
    return load_yaml12(path, max_bytes=ONE_MEBIBYTE, what="test files are small")


def _error(tmp_path: Path, text: str) -> Yaml12Error:
    with pytest.raises(Yaml12Error) as error:
        _load(tmp_path, text)
    return error.value


def test_yaml_12_scalars_load_as_json_types(tmp_path: Path) -> None:
    document = _load(tmp_path, "a: no\nb: 12:30\nc: 2026-09-01\nd: true\ne: 0x1A\nf: ~\n")
    assert document == {"a": "no", "b": "12:30", "c": "2026-09-01", "d": True, "e": 26, "f": None}


def test_error_gives_the_line(tmp_path: Path) -> None:
    assert _error(tmp_path, "a: 1\nb: 2\na: 3\n").line == 3


def test_error_gives_the_reason_without_the_line(tmp_path: Path) -> None:
    assert _error(tmp_path, "a: 1\na: 2\n").reason == "duplicate key 'a'"


def test_error_gives_the_file(tmp_path: Path) -> None:
    assert _error(tmp_path, "a: 1\na: 2\n").path == tmp_path / "x.yaml"


def test_error_detail_joins_reason_and_line(tmp_path: Path) -> None:
    assert _error(tmp_path, "a: 1\na: 2\n").detail == "duplicate key 'a' (line 2)"


def test_error_without_a_line_gives_no_line(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_bytes(b"a: caf\xe9\n")
    with pytest.raises(Yaml12Error) as error:
        load_yaml12(path, max_bytes=ONE_MEBIBYTE, what="test files are small")
    assert error.value.line is None


def test_size_error_uses_the_description(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text("a: " + "x" * ONE_MEBIBYTE, encoding="utf-8")
    with pytest.raises(Yaml12Error, match=r"larger than 1 MiB; test files are small"):
        load_yaml12(path, max_bytes=ONE_MEBIBYTE, what="test files are small")
