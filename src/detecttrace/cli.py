"""The `detecttrace` command line: `check`, `demo` and `--version`.

Exit codes: 0 when results were written and at least one case was scored; 1 for input the
run cannot use, a usage error, an output path that can't be written, no scored case, or
`--strict` with invalid input; 2 for an internal error.
"""

import os
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from detecttrace import __version__
from detecttrace.model import InputFileError
from detecttrace.pipeline import RunResult, run_check
from detecttrace.results import is_results_file, write_results_json
from detecttrace.runconfig import RunConfig, load_run_config
from detecttrace.summary import (
    IssueExample,
    Severity,
    SummaryLine,
    coverage_lines,
    has_invalid_input,
    to_terminal_text,
)

DEMO_FOLDER = "demo_data"
DEMO_OUTPUT = Path("detecttrace-demo.json")
SELF_REPORTED = "Self-reported. Not verified by DetectTrace."
NOTHING_WRITTEN = "Nothing was written."
# Typer 0.2x bundles Click as a private module and older releases depend on Click itself;
# both export BadParameter, whose base class is Click's UsageError.
_USAGE_ERROR: type[Exception] = typer.BadParameter.__mro__[1]


class _UsageErrorExitsOne(TyperGroup):
    """Click exits 2 on a usage error, but 2 is reserved for internal errors here."""

    def make_context(self, *args: Any, **kwargs: Any) -> Any:
        with _usage_errors_exit_one():
            return super().make_context(*args, **kwargs)

    def invoke(self, ctx: Any) -> Any:
        # Subcommand options are parsed here, after the group's own context exists.
        with _usage_errors_exit_one():
            return super().invoke(ctx)


@contextmanager
def _usage_errors_exit_one() -> Iterator[None]:
    try:
        yield
    except _USAGE_ERROR as error:
        # Known only as type[Exception]: Click's import path differs between Typer releases.
        error.exit_code = 1  # type: ignore[attr-defined]
        raise


app = typer.Typer(
    cls=_UsageErrorExitsOne,
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

QuietOption = Annotated[
    bool, typer.Option("--quiet", help="Print nothing on success; errors still go to stderr.")
]
OutOption = Annotated[Path | None, typer.Option("--out", help="Where to write the results JSON.")]


def _print_version(is_requested: bool) -> None:
    if is_requested:
        typer.echo(f"detecttrace {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Print the version and exit."
        ),
    ] = False,
) -> None:
    """Compare an AI SOC agent's traces with analyst verdicts."""


@app.command()
def check(
    config: Annotated[Path, typer.Option("--config", help="The configuration file.")] = Path(
        "detecttrace.yaml"
    ),
    out: OutOption = None,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit 1 when any input is invalid.")
    ] = False,
    quiet: QuietOption = False,
) -> None:
    """Run every check on your traces and verdicts and write the results."""
    _exit_with(lambda: _check(config, out, is_strict=strict, is_quiet=quiet))


@app.command()
def demo(out: OutOption = None, quiet: QuietOption = False) -> None:
    """Run every check on the bundled synthetic dataset and write the results."""
    _exit_with(lambda: _demo(out, is_quiet=quiet))


def _exit_with(body: Callable[[], int]) -> None:
    try:
        code = body()
    except InputFileError as error:
        _echo_error(str(error))
        typer.echo(NOTHING_WRITTEN, err=True)
        code = 1
    except Exception as error:
        typer.echo(
            f"detecttrace: internal error ({type(error).__name__}). Please report it.", err=True
        )
        if os.environ.get("DETECTTRACE_DEBUG") == "1":
            typer.echo(traceback.format_exc(), err=True, nl=False)
        code = 2
    raise typer.Exit(code)


def _check(config_path: Path, out: Path | None, *, is_strict: bool, is_quiet: bool) -> int:
    config = load_run_config(config_path)
    target = config.output.with_suffix(".json") if out is None else out
    return _run(config, config_path, target, is_strict=is_strict, is_quiet=is_quiet)


def _demo(out: Path | None, *, is_quiet: bool) -> int:
    folder = files("detecttrace") / DEMO_FOLDER
    if not (folder / "detecttrace.yaml").is_file():
        _echo_error("The demo data is missing from this installation. Reinstall detecttrace.")
        return 1
    # A real folder, because the demo configuration names its inputs by relative path.
    with as_file(folder) as demo_folder:
        config_path = demo_folder / "detecttrace.yaml"
        config = load_run_config(config_path)
        target = DEMO_OUTPUT if out is None else out
        return _run(config, config_path, target, is_strict=False, is_quiet=is_quiet)


def _run(
    config: RunConfig, config_path: Path, target: Path, *, is_strict: bool, is_quiet: bool
) -> int:
    # Checked first so a long run never ends in a lost result; the folder is never created,
    # since a typo in --out would otherwise leave stray folders around.
    if not target.parent.is_dir():
        _echo_error(f"Output folder not found: {target.parent}. Create it or choose another --out.")
        return 1
    # Only a file this tool wrote may be replaced, so a mistyped path can't destroy other data.
    if (target.exists() or target.is_symlink()) and not is_results_file(target):
        _echo_error(
            f"{target} exists and wasn't written by detecttrace; delete it or choose another path."
        )
        return 1
    run = run_check(config, config_path)
    if run.case_count == 0:
        _echo_error(
            f"No case could be scored: no trace matched a verdict. {NOTHING_WRITTEN} "
            "See the summary below."
        )
        _echo_summary(run, config_path.name, is_err=True)
        return 1
    try:
        write_results_json(run.results, target)
    except OSError as error:
        # Exit 1, not 2: a full disk or a read-only folder is the user's to fix, not a bug.
        _echo_error(
            f"Could not write the results to {target}: {error.strerror or error}. "
            + NOTHING_WRITTEN
        )
        return 1
    if not is_quiet:
        _echo_classes(run)
        _echo_summary(run, config_path.name, is_err=False)
        typer.echo(f"Results written to {to_terminal_text(str(target), limit=None)}.")
        typer.echo(SELF_REPORTED)
    if is_strict and has_invalid_input(run.issues):
        if is_quiet:
            # Otherwise the only output would be an error that doesn't say what is invalid.
            invalid = [line for line in run.summary if line.severity is Severity.INVALID_INPUT]
            _echo_summary_lines(invalid, is_err=True)
        _echo_error("Some input is invalid and --strict is set. The results were still written.")
        return 1
    return 0


def _echo_error(message: str) -> None:
    # Messages quote paths and YAML text from the input; escaped line by line, since
    # configuration and checklist errors list one problem per line.
    lines = [to_terminal_text(line, limit=None) for line in message.split("\n")]
    typer.echo("Error: " + "\n".join(lines), err=True)


def _echo_classes(run: RunResult) -> None:
    for report in run.report.classes:
        count = report.overall.case_count
        versions = [
            "(no version)" if version is None else to_terminal_text(version)
            for version in report.shown_versions
        ]
        other_count = len(report.other_versions)
        if other_count:
            versions.append(f"{other_count} other {'version' if other_count == 1 else 'versions'}")
        noun = "case" if count == 1 else "cases"
        typer.echo(
            f"{to_terminal_text(report.alert_class)}: {count:,} {noun}; "
            f"versions: {', '.join(versions)}"
        )


def _echo_summary(run: RunResult, config_name: str, *, is_err: bool) -> None:
    for line in coverage_lines(run.coverage, config_name):
        typer.echo(line.message, err=is_err)
    _echo_summary_lines(run.summary, is_err=is_err)


def _echo_summary_lines(lines: list[SummaryLine], *, is_err: bool) -> None:
    for line in lines:
        typer.echo(line.terminal_message, err=is_err)
        for example in line.examples:
            typer.echo(f"    {_to_example_text(example)}", err=is_err)


def _to_example_text(example: IssueExample) -> str:
    subject = to_terminal_text(example.subject)
    return subject if example.detail is None else f"{subject}: {to_terminal_text(example.detail)}"
