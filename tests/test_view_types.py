"""The committed dashboard/src/view.d.ts is what scripts/generate_view_types.py writes."""

from collections.abc import Mapping
from dataclasses import is_dataclass
from pathlib import Path
from typing import Literal

import generate_view_types
import pytest

from detecttrace import dashboard_view
from detecttrace.dashboard_view import StripView


def test_the_committed_view_types_match_the_generated_ones() -> None:
    committed = generate_view_types.OUTPUT_PATH.read_text(encoding="utf-8")

    assert committed == generate_view_types.render_view_types()


def test_every_public_view_dataclass_gets_a_type() -> None:
    public = {
        name
        for name, value in vars(dashboard_view).items()
        if isinstance(value, type)
        and is_dataclass(value)
        and value.__module__ == dashboard_view.__name__
        and not name.startswith("_")
    }

    assert {view_class.__name__ for view_class in generate_view_types.list_view_classes()} == public


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (tuple[str, ...], "ReadonlyArray<string>"),
        (tuple[float | None, ...], "ReadonlyArray<number | null>"),
        (tuple[tuple[str, ...], ...], "ReadonlyArray<ReadonlyArray<string>>"),
        (Mapping[str, str], "Readonly<Record<string, string>>"),
        (StripView | None, "StripView | null"),
        (Literal["offline", "ui"], '"offline" | "ui"'),
    ],
)
def test_an_annotation_maps_to_its_typescript_type(annotation: object, expected: str) -> None:
    assert generate_view_types.to_typescript(annotation, StripView) == expected


def test_an_unsupported_annotation_names_its_class() -> None:
    with pytest.raises(TypeError, match="StripView"):
        generate_view_types.to_typescript(dict[str, int], StripView)


@pytest.fixture
def output_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "view.d.ts"
    monkeypatch.setattr(generate_view_types, "OUTPUT_PATH", path)
    return path


def test_check_passes_for_a_current_file(output_path: Path) -> None:
    output_path.write_text(generate_view_types.render_view_types(), encoding="utf-8")

    assert generate_view_types.main(["--check"]) == 0


def test_check_fails_for_a_stale_file(output_path: Path) -> None:
    output_path.write_text("export type Stale = {};\n", encoding="utf-8")

    assert generate_view_types.main(["--check"]) == 1


def test_check_fails_for_a_missing_file(output_path: Path) -> None:
    assert generate_view_types.main(["--check"]) == 1
