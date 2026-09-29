---
paths:
  - "**/*.py"
  - "**/pyproject.toml"
---

# Python 3.11+

## Naming (PEP 8)

- `snake_case` for functions, variables, modules, and packages. File names never use hyphens. `PascalCase` classes, `SCREAMING_SNAKE` constants, `_leading_underscore` for internal names.
- Booleans `is_` / `has_` / `should_` / `can_`. Factories `create_*`, converters `to_*`.

## Types and idioms

- Type-hint public functions with syntax that runs on Python 3.11: `list[str]`, `X | None`. No `type X = ...` statements and no `def f[T]` generics (3.12 only). Ruff targets `py311` and Pyright `3.11`, so they reject 3.12 syntax. No `typing.List` / `Optional` / `Union` in new code.
- `pathlib.Path` over `os.path`. f-strings over `%` and `.format()`. `dataclass` over ad-hoc dicts for structured data.
- Catch specific exceptions; never bare `except:`. No mutable default arguments. `logging`, not `print`, outside scripts.

## Tooling: uv + ruff + pyright

- Run tools through uv: `uv run pytest`, `uv run ruff check --fix`, `uv run ruff format`, `uv run pyright`. Fix type errors; don't silence them with `# type: ignore` or `Any` unless there's no alternative, and say why in a comment.
- Keep a `[tool.ruff]` section in `pyproject.toml`: the format-on-save hook only runs ruff when one exists.
- Dependencies: `uv add <pkg>` (`uv add --dev` for tools). Never `pip install`, never hand-edit `uv.lock`.
- Tests: pytest with plain `assert`, fixtures over `setUp`, `@pytest.mark.parametrize` instead of loops.
