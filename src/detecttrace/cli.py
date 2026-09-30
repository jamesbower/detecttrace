"""The `detecttrace` command line: `init`, `check`, `demo` and `--version`.

`init` reads sample traces and verdicts, proposes a configuration, asks about it in a
terminal (or takes every proposal with `--yes`), and writes detecttrace.yaml with an
inactive example checklist; `--dry-run` prints them instead. It never replaces an existing
file without `--force`.

`check` and `demo` write the HTML dashboard, and with `--json` also the results JSON. An
existing file is replaced only when detecttrace wrote it, or with `--force`.

Exit codes: 0 when results were written and at least one case was scored, or when `init`
wrote, printed, or was told not to write; 1 for input the run cannot use, a usage error, an
output path that can't be written, no scored case, `--strict` with invalid input, and for
`init` a missing required setting, an existing file without `--force`, or no terminal
without `--yes`; 2 for an internal error.
"""

import os
import stat
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from typer.core import TyperGroup

from detecttrace import __version__
from detecttrace.checklist import EXAMPLE_SUFFIX
from detecttrace.dashboard import is_dashboard_file, render_dashboard, write_dashboard
from detecttrace.files import write_text_atomically
from detecttrace.init_proposal import (
    REQUIRED_FIELDS,
    REQUIRED_LABELS,
    OrphanSummary,
    Proposal,
    propose_init,
)
from detecttrace.init_writer import (
    FIELD_NAMES,
    SET_HELP,
    InitDraft,
    OverrideError,
    apply_overrides,
    check_round_trip,
    create_draft,
    describe_field,
    find_set_trace_format,
    render_config_yaml,
    render_example_checklist,
    set_label,
    to_checklist_file_name,
)
from detecttrace.langfuse import find_missing_tool_calls
from detecttrace.model import InputFileError, Issue, IssueKind, Span, Verdict, VerdictRow
from detecttrace.pipeline import RunResult, run_check
from detecttrace.results import is_results_file, write_results_json
from detecttrace.runconfig import RunConfig, TraceFormat, load_run_config
from detecttrace.summary import (
    IssueExample,
    Severity,
    SummaryLine,
    coverage_lines,
    has_invalid_input,
    summarize_issues,
    to_terminal_text,
)
from detecttrace.traces import detect_format, load_spans
from detecttrace.verdicts import read_verdicts

DEMO_FOLDER = "demo_data"
DEMO_OUTPUT = Path("detecttrace-demo.html")
SELF_REPORTED = "Self-reported. Not verified by DetectTrace."
NOTHING_WRITTEN = "Nothing was written."
NO_TERMINAL = "init needs a terminal to ask questions. Run init in a terminal, or use --yes."
_MAX_SHOWN_TOOLS = 10
_LEAVE_UNMAPPED = ""
_TRACES_HINT = "Check --traces."
_VERDICTS_HINT = "Check --verdicts."
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
def init(
    traces: Annotated[
        Path, typer.Option("--traces", help="A sample trace file or folder, as for check.")
    ],
    verdicts: Annotated[Path, typer.Option("--verdicts", help="The analyst verdict CSV file.")],
    config: Annotated[
        Path, typer.Option("--config", help="Where to write the configuration.")
    ] = Path("detecttrace.yaml"),
    sets: Annotated[
        list[str] | None,
        typer.Option("--set", metavar="KEY=VALUE", help=f"Override a proposal. {SET_HELP}"),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Take every proposal without asking questions.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Print the configuration and example checklist; write nothing."
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace an existing configuration or example checklist."),
    ] = False,
    quiet: QuietOption = False,
) -> None:
    """Propose a configuration from sample traces and verdicts, and write it."""
    options = _InitOptions(
        traces=traces,
        verdicts=verdicts,
        config=config,
        sets=sets or [],
        is_yes=yes,
        is_dry_run=dry_run,
        is_force=force,
        is_quiet=quiet,
    )
    _exit_with(lambda: _init(options))


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


@dataclass(frozen=True, slots=True)
class _InitOptions:
    traces: Path
    verdicts: Path
    config: Path
    sets: list[str]
    is_yes: bool
    is_dry_run: bool
    is_force: bool
    is_quiet: bool


def _is_interactive() -> bool:
    return sys.stdin.isatty()


class _InitStop(Exception):
    """init has said why it stops; it exits with `code`."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Sample:
    spans: list[Span]
    rows: list[VerdictRow]
    trace_format: TraceFormat
    trace_issues: list[Issue]
    verdict_issues: list[Issue]


def _init(options: _InitOptions) -> int:
    try:
        is_interactive = _check_init_can_start(options)
        sample = _load_sample(options)
        proposal, draft = _propose(sample, options)
        if not options.is_quiet:
            _echo_found(proposal, draft, sample, options)
        if is_interactive:
            draft = _ask(sample, options, proposal, draft)
        _check_required(draft, options)
        return _emit(draft, options, is_interactive=is_interactive)
    except _InitStop as stop:
        return stop.code


def _check_init_can_start(options: _InitOptions) -> bool:
    """Whether init asks questions; stops when it cannot ask and has no --yes, or cannot
    write the configuration. Checked before the inputs are read, so CI never waits on a long
    read to learn this.
    """
    is_interactive = not options.is_yes and _is_interactive()
    if not options.is_yes and not is_interactive:
        _stop(NO_TERMINAL)
    if not options.is_dry_run:
        config_path = options.config
        problem = _find_init_folder_problem(config_path) or _find_init_target_problem(
            config_path, is_force=options.is_force
        )
        if problem is not None:
            _stop(problem)
    return is_interactive


def _load_sample(options: _InitOptions) -> _Sample:
    config_name = options.config.name
    traces_text = _to_path_text(options.traces)
    # A format set with --set is how the configuration will read the traces, so the proposal
    # reads them that way too.
    trace_format = find_set_trace_format(options.sets)
    if trace_format is None:
        trace_format, format_issues = detect_format(options.traces, path_hint=_TRACES_HINT)
        if trace_format is None:
            _echo_error(
                f"No OTLP JSON or Langfuse export found in {traces_text}. {NOTHING_WRITTEN}"
            )
            _echo_summary_lines(summarize_issues(format_issues, config_name), is_err=True)
            raise _InitStop(1)
    spans, trace_issues = load_spans(options.traces, format=trace_format, path_hint=_TRACES_HINT)
    if not spans:
        _echo_error(
            f"No span could be read from {traces_text} as {trace_format}. {NOTHING_WRITTEN}"
        )
        _echo_summary_lines(summarize_issues(trace_issues, config_name), is_err=True)
        raise _InitStop(1)
    rows, verdict_issues = read_verdicts(options.verdicts, path_hint=_VERDICTS_HINT)
    return _Sample(spans, rows, trace_format, trace_issues, verdict_issues)


def _propose(sample: _Sample, options: _InitOptions) -> tuple[Proposal, InitDraft]:
    proposal = propose_init(sample.spans, sample.trace_format, sample.rows)
    try:
        draft = apply_overrides(_create_draft(proposal, options), options.sets)
        proposal, draft = _repropose(sample, options, proposal, draft)
    except OverrideError as error:
        _stop(f"{error} {NOTHING_WRITTEN}")
    example_path = _to_example_path(draft, options.config)
    if example_path is not None and not options.is_dry_run:
        problem = _find_init_target_problem(example_path, is_force=options.is_force)
        if problem is not None:
            _stop(problem)
    return proposal, draft


def _ask(sample: _Sample, options: _InitOptions, proposal: Proposal, draft: InitDraft) -> InitDraft:
    try:
        asked = _ask_mapping(draft, proposal)
        proposal, draft = _repropose(sample, options, proposal, asked)
        if draft is not asked:
            _echo_orphans(proposal)
        return _ask_labels(draft)
    except typer.Abort:
        typer.echo(f"\nStopped. {NOTHING_WRITTEN}", err=True)
        raise _InitStop(1) from None


def _check_required(draft: InitDraft, options: _InitOptions) -> None:
    if draft.missing_required:
        _stop(_describe_missing(draft.missing_required))
    unmapped = len(draft.unmapped_analyst_labels) + len(draft.unmapped_agent_labels)
    if unmapped and not options.is_quiet:
        noun = "label is" if unmapped == 1 else "labels are"
        typer.echo(
            f"Warning: {unmapped:,} {noun} not mapped. They are written commented out, and "
            "check reports them as unmapped until you map them.",
            err=True,
        )


def _emit(draft: InitDraft, options: _InitOptions, *, is_interactive: bool) -> int:
    """Print the files with --dry-run; otherwise confirm when interactive, and write them."""
    config_path = options.config
    text = render_config_yaml(draft)
    # A mismatch is a bug in init, not in the input: the exception makes it exit 2.
    check_round_trip(text, draft)
    example_path = _to_example_path(draft, config_path)
    example = None
    if example_path is not None and draft.example_class is not None:
        example = (
            example_path,
            render_example_checklist(
                draft.example_class, draft.example_tools, draft.example_tool_count
            ),
        )
    if options.is_dry_run:
        typer.echo(text, nl=False)
        if example is not None:
            # A YAML document marker, so the output still reads as YAML. The path is as the
            # configuration gives it: relative to the configuration file, or as set.
            shown = f"{draft.checklists_path}/{example[0].name}"
            typer.echo(f"--- # {to_terminal_text(shown, limit=None)}")
            typer.echo(example[1], nl=False)
        return 0
    if is_interactive:
        try:
            is_confirmed = _confirm_write(draft, config_path, example_path)
        except typer.Abort:
            typer.echo(f"\nStopped. {NOTHING_WRITTEN}", err=True)
            return 1
        if not is_confirmed:
            typer.echo(NOTHING_WRITTEN, err=True)
            return 0
    return _write_init_files(config_path, text, example, options)


def _stop(message: str) -> NoReturn:
    _echo_error(message)
    raise _InitStop(1)


def _create_draft(proposal: Proposal, options: _InitOptions) -> InitDraft:
    return create_draft(
        proposal,
        config_path=options.config,
        traces_path=options.traces,
        verdicts_path=options.verdicts,
    )


def _repropose(
    sample: _Sample, options: _InitOptions, proposal: Proposal, draft: InitDraft
) -> tuple[Proposal, InitDraft]:
    """Propose again through the draft's mapping when the user changed it, since the example
    checklist, labels, orphans and coverage all depend on it; the draft is returned unchanged
    otherwise.

    The rebuilt draft takes the --set arguments again, so settings and labels set there
    survive, then the draft's mapping, so keys entered at a prompt win over --set.
    """
    if draft.mapping == proposal.mapping.to_mapping_config():
        return proposal, draft
    proposal = propose_init(sample.spans, proposal.trace_format, sample.rows, mapping=draft.mapping)
    rebuilt = _create_draft(proposal, options)
    # The coverage comes from the new proposal: --set would describe a set key without it.
    reapplied = apply_overrides(rebuilt, options.sets)
    return proposal, replace(reapplied, mapping=draft.mapping, coverage=rebuilt.coverage)


def _to_example_path(draft: InitDraft, config_path: Path) -> Path | None:
    if draft.example_class is None or draft.checklists_path is None:
        return None
    folder = config_path.parent / draft.checklists_path
    return folder / to_checklist_file_name(draft.example_class)


def _find_init_folder_problem(config_path: Path) -> str | None:
    # Not created: a typo in --config would otherwise leave stray folders around.
    folder = config_path.parent
    if folder.is_dir():
        return None
    return f"Folder not found: {_to_path_text(folder)}. Create it or choose another --config."


def _find_init_target_problem(target: Path, *, is_force: bool) -> str | None:
    """Why init must not write `target`, or None. init's files carry no marker to tell them
    from a hand-edited one, so any existing file needs `--force`, and a folder is never
    replaced.
    """
    target_text = _to_path_text(target)
    if target.is_dir():
        return f"{target_text} is a folder; choose another path. --force never replaces a folder."
    if not is_force and (target.exists() or target.is_symlink()):
        return f"{target_text} exists; use --force to replace it. {NOTHING_WRITTEN}"
    return None


def _echo_found(
    proposal: Proposal, draft: InitDraft, sample: _Sample, options: _InitOptions
) -> None:
    def echo(text: str = "") -> None:
        typer.echo(text, err=True)

    echo(
        f"Traces: {proposal.trace_format}, {proposal.agent_run_count:,} agent runs. "
        f"Verdicts: {len(sample.rows):,} rows."
    )
    echo("Mapping:")
    for name in FIELD_NAMES:
        field = getattr(proposal.mapping, name)
        echo(f"  {name}: {describe_field(name, field, partial(to_terminal_text, limit=None))}")
    operation = proposal.mapping.operation
    echo(
        f"  operation: {to_terminal_text(operation.attribute, limit=None)} = "
        f"{to_terminal_text(operation.agent_value)} for agent runs, "
        f"{to_terminal_text(operation.tool_value)} for tool calls ({operation.source})"
    )
    if proposal.notes:
        echo("Notes:")
        for note in proposal.notes:
            echo(f"  - {to_terminal_text(note, limit=None)}")
    echo("Tools by alert class:")
    if not proposal.tool_names_by_class:
        echo("  (none found)")
    for alert_class, tools in proposal.tool_names_by_class.items():
        count = proposal.case_counts_by_class[alert_class]
        shown = [to_terminal_text(tool) for tool in tools[:_MAX_SHOWN_TOOLS]]
        tool_count = proposal.tool_counts_by_class[alert_class]
        if tool_count > len(shown):
            shown.append(f"and {tool_count - len(shown):,} more")
        noun = "case" if count == 1 else "cases"
        echo(
            f"  {to_terminal_text(alert_class)} ({count:,} {noun}): "
            f"{', '.join(shown) if shown else '(no tool calls)'}"
        )
    # From the draft, not the proposal, so labels mapped with --set show as mapped.
    for title, mapped, unmapped in (
        ("Analyst labels:", draft.label_map, draft.unmapped_analyst_labels),
        (
            "Agent labels not in the verdict file:",
            draft.agent_label_map,
            draft.unmapped_agent_labels,
        ),
    ):
        if mapped or unmapped:
            echo(title)
        for label, verdict in mapped.items():
            echo(f"  {to_terminal_text(label)}: {verdict.value}")
        for label in unmapped:
            echo(f"  {to_terminal_text(label)}: not mapped")
    _echo_orphans(proposal)
    trace_issues = list(sample.trace_issues)
    missing_tool_calls = find_missing_tool_calls(
        proposal.trace_format, proposal.trace_cases, options.traces.name
    )
    if missing_tool_calls is not None:
        trace_issues.append(missing_tool_calls)
    problems = summarize_issues(trace_issues + sample.verdict_issues, options.config.name)
    if problems:
        echo("Input problems (check reports them too):")
        _echo_summary_lines(problems, is_err=True)


def _echo_orphans(proposal: Proposal) -> None:
    for title, orphans in (
        ("Traces without a verdict", proposal.traces_without_verdict),
        ("Verdicts without a trace", proposal.verdicts_without_trace),
    ):
        typer.echo(_describe_orphans(title, orphans), err=True)


def _describe_orphans(title: str, orphans: OrphanSummary) -> str:
    if not orphans.count:
        return f"{title}: 0"
    examples = [to_terminal_text(case_id) for case_id in orphans.examples]
    if orphans.count > len(examples):
        examples.append("...")
    return f"{title}: {orphans.count:,} ({', '.join(examples)})"


def _ask_mapping(draft: InitDraft, proposal: Proposal) -> InitDraft:
    typer.echo("Press Enter to keep each proposal, or type the attribute to use.", err=True)
    unchanged = proposal.mapping.to_mapping_config()
    for name in FIELD_NAMES:
        is_required = name in draft.missing_required
        current = getattr(draft.mapping, name)
        # A field not found holds its default key, which the user never chose.
        is_not_found = getattr(proposal.mapping, name).value is None and current == getattr(
            unchanged, name
        )
        if is_required:
            shown = "not found, required"
        elif is_not_found:
            shown = "not found"
        else:
            shown = to_terminal_text(current, limit=None)
        while True:
            answer = typer.prompt(f"{name} [{shown}]", default="", show_default=False, err=True)
            answer = answer.strip()
            if not answer:
                if not is_required:
                    break
                typer.echo(f"{name} is required: type the attribute that holds it.", err=True)
                continue
            if answer == current and not is_required:
                break
            try:
                draft = apply_overrides(draft, [f"mapping.{name}={answer}"])
            except OverrideError as error:
                _echo_error(str(error))
                continue
            break
    return draft


def _ask_labels(draft: InitDraft) -> InitDraft:
    choices = "true_positive, false_positive or benign"
    questions = [("label_map", "Analyst", label) for label in draft.unmapped_analyst_labels]
    questions += [("agent_label_map", "Agent", label) for label in draft.unmapped_agent_labels]
    if questions:
        typer.echo(f"Map each label to {choices}, or press Enter to leave it unmapped.", err=True)
    for area, side, label in questions:
        while True:
            answer = typer.prompt(
                f"{side} label '{to_terminal_text(label)}'",
                default=_LEAVE_UNMAPPED,
                show_default=False,
                err=True,
            )
            answer = answer.strip().lower()
            if answer == _LEAVE_UNMAPPED:
                break
            if answer in {verdict.value for verdict in Verdict}:
                draft = set_label(draft, area, label, Verdict(answer))
                break
            typer.echo(f"Answer {choices}, or press Enter to leave it unmapped.", err=True)
    return draft


def _describe_missing(missing: tuple[str, ...]) -> str:
    fixes = {
        **{
            name: f"{name}: set it with --set mapping.{name}=<attribute>"
            for name in REQUIRED_FIELDS
        },
        REQUIRED_LABELS: (
            f"{REQUIRED_LABELS}: no verdict label could be mapped; map one with "
            "--set label_map.<label>=true_positive (or false_positive, benign)"
        ),
    }
    lines = [fixes[name] for name in missing]
    return "\n".join([f"Required settings are missing. {NOTHING_WRITTEN}", *lines])


def _confirm_write(draft: InitDraft, config_path: Path, example_path: Path | None) -> bool:
    def echo(text: str) -> None:
        typer.echo(text, err=True)

    echo("Summary:")
    for name in FIELD_NAMES:
        echo(f"  {name}: {to_terminal_text(getattr(draft.mapping, name), limit=None)}")
    unmapped = len(draft.unmapped_analyst_labels) + len(draft.unmapped_agent_labels)
    echo(
        f"  labels mapped: {len(draft.label_map) + len(draft.agent_label_map):,}; not mapped: {unmapped:,}"
    )
    if example_path is not None:
        echo(f"  example checklist: {_to_path_text(example_path)} (inactive until renamed)")
    return typer.confirm(f"Write {_to_path_text(config_path)}?", default=True, err=True)


def _write_init_files(
    config_path: Path, text: str, example: tuple[Path, str] | None, options: _InitOptions
) -> int:
    # Checked again: the questions may have taken a while.
    targets = [config_path] if example is None else [example[0], config_path]
    for target in targets:
        problem = _find_init_target_problem(target, is_force=options.is_force)
        if problem is not None:
            _echo_error(problem)
            return 1
    # The checklist first: a configuration naming a checklists folder that holds nothing
    # would make check fail.
    writes = [(config_path, text)] if example is None else [example, (config_path, text)]
    written: list[Path] = []
    for target, content in writes:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomically(content, target)
        except OSError as error:
            outcome = (
                NOTHING_WRITTEN if not written else f"Only {_to_path_text(written[0])} was written."
            )
            _echo_error(
                f"Could not write {_to_path_text(target)}: {error.strerror or error}. {outcome}"
            )
            return 1
        written.append(target)
    if options.is_quiet:
        return 0
    typer.echo(f"Wrote {' and '.join(_to_path_text(path) for path in written)}.")
    if len(written) > 1:
        example_path = written[0]
        active = example_path.name.removesuffix(EXAMPLE_SUFFIX) + ".yaml"
        typer.echo(
            f"Next: edit {_to_path_text(example_path)} to keep only the calls the playbook "
            f"requires, rename it to {active}, then run {_to_check_command(config_path)}."
        )
    else:
        typer.echo(f"Next: run {_to_check_command(config_path)}.")
    return 0


def _to_check_command(config_path: Path) -> str:
    if config_path == Path("detecttrace.yaml"):
        return "detecttrace check"
    return f"detecttrace check --config {_to_path_text(config_path)}"


def _to_path_text(path: Path) -> str:
    return to_terminal_text(path.as_posix(), limit=None)
