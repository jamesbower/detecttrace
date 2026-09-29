"""The `detecttrace` command line: `check`, `demo` and `--version`.

Exit codes: 0 when results were written and at least one case was scored; 1 for input the
run cannot use, no scored case, or `--strict` with invalid input; 2 for an internal error.
"""

import os
import traceback
from collections.abc import Callable
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated

import typer

from detecttrace import __version__
from detecttrace.model import InputFileError
from detecttrace.pipeline import RunResult, run_check
from detecttrace.results import write_results_json
from detecttrace.runconfig import RunConfig, load_run_config
from detecttrace.summary import coverage_lines, has_invalid_input

DEMO_FOLDER = "demo_data"
DEMO_OUTPUT = Path("detecttrace-demo.json")
SELF_REPORTED = "Self-reported. Not verified by DetectTrace."

app = typer.Typer(add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False)

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
    run = run_check(config, config_path)
    if run.case_count == 0:
        _echo_error("No case could be scored: no trace matched a verdict. See the summary below.")
        _echo_summary(run, config_path.name, is_err=True)
        return 1
    try:
        write_results_json(run.results, target)
    except OSError as error:
        _echo_error(f"Could not write the results to {target}: {error.strerror or error}.")
        return 1
    if not is_quiet:
        _echo_classes(run)
        _echo_summary(run, config_path.name, is_err=False)
        typer.echo(f"Results written to {target}.")
        typer.echo(SELF_REPORTED)
    if is_strict and has_invalid_input(run.issues):
        _echo_error("Some input is invalid and --strict is set. The results were still written.")
        return 1
    return 0


def _echo_error(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)


def _echo_classes(run: RunResult) -> None:
    for report in run.report.classes:
        count = report.overall.case_count
        versions = [
            "(no version)" if version is None else version for version in report.shown_versions
        ]
        if report.other_versions:
            versions.append(f"{len(report.other_versions)} other versions")
        noun = "case" if count == 1 else "cases"
        typer.echo(f"{report.alert_class}: {count:,} {noun}; versions: {', '.join(versions)}")


def _echo_summary(run: RunResult, config_name: str, *, is_err: bool) -> None:
    for line in [*coverage_lines(run.coverage, config_name), *run.summary]:
        typer.echo(line.message, err=is_err)
        for example in line.examples:
            typer.echo(f"    {example}", err=is_err)
