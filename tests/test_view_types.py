"""The committed dashboard/src/view.d.ts is what scripts/generate_view_types.py writes."""

from dataclasses import is_dataclass

import generate_view_types

from detecttrace import dashboard_view


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
