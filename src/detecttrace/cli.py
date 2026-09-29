"""The `detecttrace` command line: `check`, `demo` and `--version`.

`check` and `demo` write the HTML dashboard, and with `--json` also the results JSON. An
existing file is replaced only when detecttrace wrote it, or with `--force`.

Exit codes: 0 when results were written and at least one case was scored; 1 for input the
run cannot use, a usage error, an output path that can't be written, no scored case, or
`--strict` with invalid input; 2 for an internal error.
"""

import os
import stat
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from detecttrace import __version__
from detecttrace.dashboard import is_dashboard_file, render_dashboard, write_dashboard
from detecttrace.model import InputFileError, IssueKind
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
DEMO_OUTPUT = Path("detecttrace-demo.html")
SELF_REPORTED = "Self-reported. Not verified by DetectTrace."
NOTHING_WRITTEN = "Nothing was written."
_USAGE_ERROR_EXIT_CODE = 2


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
    # Usage errors are the Typer exceptions that exit 2; Exit and Abort aren't TyperExceptions.
    except typer.TyperException as error:
        if error.exit_code == _USAGE_ERROR_EXIT_CODE:
            error.exit_code = 1
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
OutOption = Annotated[Path | None, typer.Option("--out", help="Where to write the HTML dashboard.")]
JsonOption = Annotated[
    Path | None, typer.Option("--json", help="Also write the results JSON to this path.")
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force", help="Replace an existing file that detecttrace didn't write (never a folder)."
    ),
]


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
    json: JsonOption = None,
    force: ForceOption = False,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit 1 when any input is invalid.")
    ] = False,
    quiet: QuietOption = False,
) -> None:
    """Run every check on your traces and verdicts and write the dashboard."""
    _exit_with(lambda: _check(config, out, json, is_force=force, is_strict=strict, is_quiet=quiet))


@app.command()
def demo(
    out: OutOption = None,
    json: JsonOption = None,
    force: ForceOption = False,
    quiet: QuietOption = False,
) -> None:
    """Run every check on the bundled synthetic dataset and write the dashboard."""
    _exit_with(lambda: _demo(out, json, is_force=force, is_quiet=quiet))


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


def _check(
    config_path: Path,
    out: Path | None,
    json_target: Path | None,
    *,
    is_force: bool,
    is_strict: bool,
    is_quiet: bool,
) -> int:
    config = load_run_config(config_path)
    targets = _Targets(config.output if out is None else out, json_target)
    return _run(
        config, config_path, targets, is_force=is_force, is_strict=is_strict, is_quiet=is_quiet
    )


def _demo(out: Path | None, json_target: Path | None, *, is_force: bool, is_quiet: bool) -> int:
    folder = files("detecttrace") / DEMO_FOLDER
    if not (folder / "detecttrace.yaml").is_file():
        _echo_error("The demo data is missing from this installation. Reinstall detecttrace.")
        return 1
    # A real folder, because the demo configuration names its inputs by relative path.
    with as_file(folder) as demo_folder:
        config_path = demo_folder / "detecttrace.yaml"
        config = load_run_config(config_path)
        targets = _Targets(DEMO_OUTPUT if out is None else out, json_target)
        return _run(
            config, config_path, targets, is_force=is_force, is_strict=False, is_quiet=is_quiet
        )


@dataclass(frozen=True, slots=True)
class _Targets:
    html: Path
    json: Path | None

    def to_list(self) -> list[tuple[Path, str]]:
        """Each path to write with the option that names it."""
        pairs = [(self.html, "--out")]
        if self.json is not None:
            pairs.append((self.json, "--json"))
        return pairs


def _run(
    config: RunConfig,
    config_path: Path,
    targets: _Targets,
    *,
    is_force: bool,
    is_strict: bool,
    is_quiet: bool,
) -> int:
    if targets.json is not None and targets.json.resolve() == targets.html.resolve():
        # Otherwise the JSON would silently replace the dashboard just written.
        _echo_error("The dashboard and the results JSON need two different paths.")
        return 1
    # Checked first so a long run never ends in a lost result; the folder is never created,
    # since a typo in a path would otherwise leave stray folders around.
    for target, option in targets.to_list():
        if not target.parent.is_dir():
            _echo_error(
                f"Output folder not found: {target.parent}. Create it or choose another {option}."
            )
            return 1
        problem = _find_output_problem(target, is_force=is_force)
        if problem is not None:
            _echo_error(problem)
            return 1
    run = run_check(config, config_path)
    if run.coverage.verdicts_total == 0 and not any(
        issue.kind is IssueKind.INVALID_VERDICT_ROW for issue in run.issues
    ):
        # Otherwise the summary blames the case ID mapping for traces that had nothing to match.
        _echo_error(f"The verdict file has no rows. {NOTHING_WRITTEN}")
        return 1
    if run.case_count == 0:
        _echo_error(
            f"No case could be scored: no trace matched a verdict. {NOTHING_WRITTEN} "
            "See the summary below."
        )
        _echo_summary(run, config_path.name, is_err=True)
        return 1
    # Rendered before anything is written, so a rendering error leaves no half-done output.
    html = render_dashboard(run.results)
    # Checked again because the run can take minutes, and another program may have created
    # a file meanwhile.
    for target, _option in targets.to_list():
        problem = _find_output_problem(target, is_force=is_force)
        if problem is not None:
            _echo_error(f"{problem} {NOTHING_WRITTEN}")
            return 1
    writes = [(targets.html, partial(write_dashboard, html))]
    if targets.json is not None:
        writes.append((targets.json, partial(write_results_json, run.results)))
    written: list[Path] = []
    for target, write in writes:
        try:
            write(target)
        except OSError as error:
            # Exit 1, not 2: a full disk or a read-only folder is the user's to fix, not a bug.
            outcome = NOTHING_WRITTEN if not written else f"Only {written[0]} was written."
            _echo_error(f"Could not write {target}: {error.strerror or error}. {outcome}")
            return 1
        written.append(target)
    if not is_quiet:
        _echo_classes(run)
        _echo_summary(run, config_path.name, is_err=False)
        names = [to_terminal_text(str(path), limit=None) for path in written]
        typer.echo(f"Results written to {' and '.join(names)}.")
        typer.echo(SELF_REPORTED)
    if is_strict and has_invalid_input(run.issues):
        if is_quiet:
            # Otherwise the only output would be an error that doesn't say what is invalid.
            invalid = [line for line in run.summary if line.severity is Severity.INVALID_INPUT]
            _echo_summary_lines(invalid, is_err=True)
        _echo_error("Some input is invalid and --strict is set. The results were still written.")
        return 1
    return 0


def _find_output_problem(target: Path, *, is_force: bool) -> str | None:
    """Why `target` must not be replaced, or None when it is free, a file this tool wrote, or
    any file with `is_force`.

    Without `--force` only a file this tool wrote may be replaced, so a mistyped path can't
    destroy other data. A folder is never replaced.
    """
    try:
        mode = target.lstat().st_mode
        if stat.S_ISLNK(mode):
            try:
                mode = target.stat().st_mode
            except FileNotFoundError:
                if is_force:
                    return None
                return f"{target} is a broken link; delete it, choose another path, or use --force."
        if stat.S_ISDIR(mode):
            return f"{target} is a folder; choose another path. --force never replaces a folder."
        if is_force or is_dashboard_file(target) or is_results_file(target):
            return None
    except FileNotFoundError:
        return None
    except OSError as error:
        return (
            f"Could not read {target} to check it before replacing it: {error.strerror or error}."
        )
    return (
        f"{target} exists and wasn't written by detecttrace; delete it, choose another path, "
        "or use --force."
    )


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
        text = line.message if line.hint is None else f"{line.message} {line.hint}"
        typer.echo(text, err=is_err)
    _echo_summary_lines(run.summary, is_err=is_err)


def _echo_summary_lines(lines: list[SummaryLine], *, is_err: bool) -> None:
    for line in lines:
        typer.echo(f"{line.terminal_message} {line.terminal_hint}", err=is_err)
        for example in line.examples:
            typer.echo(f"    {_to_example_text(example)}", err=is_err)


def _to_example_text(example: IssueExample) -> str:
    subject = to_terminal_text(example.subject)
    return subject if example.detail is None else f"{subject}: {to_terminal_text(example.detail)}"
