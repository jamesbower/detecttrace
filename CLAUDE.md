# DetectTrace POC

Command-line tool that reads an AI SOC agent's OTLP traces and analyst verdicts and writes one offline HTML dashboard. Private context (scope rules, spec): `notes/project-context.md` and `notes/detecttrace-poc-prd.md`, gitignored and local only. Read them before adding a feature. Plan: `tasks/todo.md`.

## Commands

    uv sync                              # install deps
    uv run pytest tests/test_x.py        # one test file (prefer over full suite)
    uv run pytest                        # full suite
    uv run ruff check --fix . && uv run ruff format .
    uv run pyright

## Conventions

- Offline: no network calls anywhere except opt-in usage statistics.
- Python 3.11 floor: no 3.12-only syntax (`type X = ...`, `def f[T]`). Ruff and Pyright target 3.11.
- Internal records are slotted dataclasses (speed at 50k cases); Pydantic only for configuration.
- GenAI convention names live only in `src/detecttrace/conventions.py`.
- No NumPy or pandas. Stdlib parsing and metrics.
- Every stage returns `(results, issues)`; bad input is reported, never fatal, unless nothing can be scored.
- Output is deterministic: sort by stable keys, fixed seeds, POSIX path order.

## Domain

- Case: an `invoke_agent` root span with a case ID, joined to analyst verdict rows.
- Verdicts: true_positive, false_positive, benign. Dangerous false close: analyst TP, agent FP or benign.
- Evidence completeness: share of checklist items satisfied by successful tool calls.

## Don'ts

- Never embed tool results in the dashboard.
- Never commit anything from `notes/`.
- Synthetic data uses only example.com and documentation IP/AS ranges.
