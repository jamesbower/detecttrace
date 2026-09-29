from pathlib import Path

import pytest

from detecttrace.files import read_head


def test_read_head_returns_the_first_bytes(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"abcdef")
    assert read_head(path, 4) == b"abcd"


def test_read_head_returns_a_short_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"ab")
    assert read_head(path, 4) == b"ab"


def test_read_head_returns_none_for_a_folder(tmp_path: Path) -> None:
    assert read_head(tmp_path, 4) is None


def test_read_head_raises_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_head(tmp_path / "missing.txt", 4)
