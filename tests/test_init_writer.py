import dataclasses
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from detecttrace import cli
from detecttrace.checklist import Checklist, load_checklists
from detecttrace.config import MappingConfig, normalize_label
from detecttrace.init_proposal import (
    REQUIRED_LABELS,
    FieldProposal,
    MappingProposal,
    OperationProposal,
    OrphanSummary,
    Proposal,
    propose_init,
)
from detecttrace.init_writer import (
    InitDraft,
    OverrideError,
    RoundTripError,
    apply_overrides,
    check_round_trip,
    choose_example_class,
    create_draft,
    render_config_yaml,
    render_example_checklist,
    to_checklist_file_name,
)
from detecttrace.model import Verdict
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.traces import load_spans
from detecttrace.verdicts import read_verdicts
from detecttrace.yaml12 import parse_yaml12

DEMO = Path(cli.__file__).parent / cli.DEMO_FOLDER
PRINTABLE = {chr(code) for code in range(0x20, 0x7F)} | {"\n"}

BASE = InitDraft(
    trace_format="otlp_jsonl",
    traces_path="traces",
    verdicts_path="verdicts.csv",
    checklists_path="checklists",
    output="detecttrace-dashboard.html",
    max_detail_cases=2000,
    mapping=MappingConfig(),
    label_map={"TP": Verdict.TRUE_POSITIVE},
    agent_label_map={},
    unmapped_analyst_labels=("Closed - Benign",),
    unmapped_agent_labels=(),
    missing_required=(),
    agent_run_count=3,
    coverage={"case_id": '"detecttrace.case_id" on 3 of 3 agent runs (detecttrace attribute)'},
    notes=("no prompt version found; every case will show as (no version)",),
    example_class="impossible_travel",
    example_tools=("get_signin_logs",),
    example_tool_count=1,
)

HOSTILE_LABELS = [
    "yes",
    "no",
    "null",
    "~",
    "true",
    "1e3",
    "0x10",
    "#x",
    ": x",
    "- x",
    "'",
    '"',
    "\\",
    "\\ud83d\\ude00",
    "a\nb",
    "a\rb",
    "a\tb",
    "a\x00b",
    "a\x7fb",
    "a\u0085b",
    "a\u2028b",
    "a\u2029b",
    "\ufeff",
    "é",
    "😀",
    "{a: b}",
    "[x]",
    "!!int 1",
    "&a",
    "*a",
    "%YAML",
    "---",
    "k" * 1021,
    "a" * 1_000_000,
    " TP ",
]


def draft(**changes: Any) -> InitDraft:
    return dataclasses.replace(BASE, **changes)


def parse(text: str) -> dict[str, Any]:
    document = parse_yaml12(text, Path("detecttrace.yaml"))
    assert isinstance(document, dict)
    return document


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "detecttrace.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# Rendering and the round trip


@pytest.mark.parametrize("label", HOSTILE_LABELS, ids=range(len(HOSTILE_LABELS)))
def test_hostile_label_is_written_with_its_exact_spelling(label: str) -> None:
    text = render_config_yaml(draft(label_map={label: Verdict.BENIGN}))

    assert parse(text)["label_map"] == {label: "benign"}


@pytest.mark.parametrize("label", HOSTILE_LABELS, ids=range(len(HOSTILE_LABELS)))
def test_hostile_label_loads_as_check_stores_it(tmp_path: Path, label: str) -> None:
    path = write_config(tmp_path, render_config_yaml(draft(label_map={label: Verdict.BENIGN})))

    assert load_run_config(path).label_map == {normalize_label(label): Verdict.BENIGN}


@pytest.mark.parametrize("label", HOSTILE_LABELS, ids=range(len(HOSTILE_LABELS)))
def test_hostile_label_passes_the_round_trip_check(label: str) -> None:
    hostile = draft(label_map={label: Verdict.BENIGN})

    check_round_trip(render_config_yaml(hostile), hostile)


@pytest.mark.parametrize("label", HOSTILE_LABELS[:-3], ids=range(len(HOSTILE_LABELS) - 3))
def test_rendered_text_is_printable_ascii(label: str) -> None:
    text = render_config_yaml(
        draft(label_map={label: Verdict.BENIGN}, unmapped_analyst_labels=(label,))
    )

    assert set(text) <= PRINTABLE


UNMAPPED_INJECTIONS = [
    "x\ntelemetry: true",
    "x\rtelemetry: true",
    "x\u0085telemetry: true",
    "x\u2028telemetry: true",
    "x\u2029telemetry: true",
    "x\x00telemetry: true",
    "\t",
    " ",
    "\u0085",
]


@pytest.mark.parametrize("label", UNMAPPED_INJECTIONS, ids=range(len(UNMAPPED_INJECTIONS)))
def test_unmapped_label_cannot_add_keys(label: str) -> None:
    text = render_config_yaml(draft(unmapped_analyst_labels=(label,)))

    assert "telemetry" not in parse(text)


@pytest.mark.parametrize("label", UNMAPPED_INJECTIONS, ids=range(len(UNMAPPED_INJECTIONS)))
def test_unmapped_label_stays_on_one_comment_line(label: str) -> None:
    plain = render_config_yaml(draft(unmapped_analyst_labels=("plain",)))

    text = render_config_yaml(draft(unmapped_analyst_labels=(label,)))

    assert text.count("\n") == plain.count("\n")


def test_unmapped_label_is_written_as_a_comment() -> None:
    text = render_config_yaml(draft(unmapped_analyst_labels=("Closed - Benign",)))

    assert '  # "Closed - Benign": ""' in text.splitlines()


def test_unmapped_labels_without_mapped_ones_still_load(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, render_config_yaml(draft(label_map={}, unmapped_analyst_labels=("x",)))
    )

    assert load_run_config(path).label_map == {}


def test_hostile_note_cannot_add_keys() -> None:
    text = render_config_yaml(draft(notes=("key \u2028telemetry: true",)))

    assert "telemetry" not in parse(text)


def test_hostile_coverage_text_cannot_add_keys() -> None:
    text = render_config_yaml(draft(coverage={"case_id": "x\ntelemetry: true"}))

    assert "telemetry" not in parse(text)


def test_hostile_example_class_cannot_add_keys() -> None:
    text = render_config_yaml(draft(example_class="x\u2028telemetry: true"))

    assert "telemetry" not in parse(text)


def test_header_names_the_inactive_example_checklist() -> None:
    text = render_config_yaml(BASE)

    assert "# checklists/impossible_travel.yaml.example is inactive until renamed to .yaml." in (
        text.splitlines()
    )


def test_default_mapping_writes_no_mapping_key() -> None:
    document = parse(render_config_yaml(BASE))

    assert "mapping" not in document


def test_mapping_writes_only_changed_keys() -> None:
    mapping = MappingConfig.model_validate(
        {"case_id": "soc.case", "operation": {"span_name_fallback": False}}
    )

    document = parse(render_config_yaml(draft(mapping=mapping)))

    assert document["mapping"] == {
        "case_id": "soc.case",
        "operation": {"span_name_fallback": False},
    }


def test_empty_agent_label_map_is_left_out() -> None:
    document = parse(render_config_yaml(BASE))

    assert "agent_label_map" not in document


def test_rendering_is_deterministic() -> None:
    assert render_config_yaml(BASE) == render_config_yaml(draft())


def test_changed_value_fails_the_round_trip_check() -> None:
    text = render_config_yaml(BASE).replace('"true_positive"', '"benign"')

    with pytest.raises(RoundTripError):
        check_round_trip(text, BASE)


def test_changed_label_spelling_fails_the_round_trip_check() -> None:
    text = render_config_yaml(BASE).replace('"TP"', '"tp"')

    with pytest.raises(RoundTripError):
        check_round_trip(text, BASE)


def test_unloadable_text_fails_the_round_trip_check() -> None:
    with pytest.raises(RoundTripError):
        check_round_trip(render_config_yaml(BASE) + "traces: {}\n", BASE)


def test_invalid_configuration_fails_the_round_trip_check() -> None:
    text = render_config_yaml(BASE).replace('"otlp_jsonl"', '"xml"')

    with pytest.raises(RoundTripError):
        check_round_trip(text, draft(trace_format="xml"))


def test_oversized_text_fails_the_round_trip_check() -> None:
    huge = draft(label_map={"a" * (1 << 20): Verdict.BENIGN})

    with pytest.raises(RoundTripError):
        check_round_trip(render_config_yaml(huge), huge)


# --set


SET_CASES: list[tuple[str, Callable[[InitDraft], object], object]] = [
    ("mapping.case_id=soc.case", lambda d: d.mapping.case_id, "soc.case"),
    ("mapping.alert_class=soc.class", lambda d: d.mapping.alert_class, "soc.class"),
    ("mapping.verdict=soc.verdict", lambda d: d.mapping.verdict, "soc.verdict"),
    ("mapping.prompt_version=soc.v", lambda d: d.mapping.prompt_version, "soc.v"),
    (
        "mapping.prompt_version_lookup=descendant",
        lambda d: d.mapping.prompt_version_lookup,
        "descendant",
    ),
    ("mapping.tool_name=tool", lambda d: d.mapping.tool_name, "tool"),
    ("mapping.tool_arguments=args", lambda d: d.mapping.tool_arguments, "args"),
    ("mapping.operation.attribute=kind", lambda d: d.mapping.operation.attribute, "kind"),
    ("mapping.operation.agent_value=AGENT", lambda d: d.mapping.operation.agent_value, "AGENT"),
    ("mapping.operation.tool_value=TOOL", lambda d: d.mapping.operation.tool_value, "TOOL"),
    (
        "mapping.operation.span_name_fallback=false",
        lambda d: d.mapping.operation.span_name_fallback,
        False,
    ),
    ("traces.format=langfuse", lambda d: d.trace_format, "langfuse"),
    ("traces.path=data/traces", lambda d: d.traces_path, "data/traces"),
    ("verdicts.path=data/v.csv", lambda d: d.verdicts_path, "data/v.csv"),
    ("checklists=playbooks", lambda d: d.checklists_path, "playbooks"),
    ("output=out/report.html", lambda d: d.output, "out/report.html"),
    ("dashboard.max_detail_cases=500", lambda d: d.max_detail_cases, 500),
    ("label_map.Closed - Benign=benign", lambda d: d.label_map["Closed - Benign"], Verdict.BENIGN),
    ("label_map.a.b=benign", lambda d: d.label_map["a.b"], Verdict.BENIGN),
    ("label_map.null=benign", lambda d: d.label_map["null"], Verdict.BENIGN),
    (
        "agent_label_map.Bad=true_positive",
        lambda d: d.agent_label_map["Bad"],
        Verdict.TRUE_POSITIVE,
    ),
    ("mapping.case_id=a=b", lambda d: d.mapping.case_id, "a=b"),
    ("mapping.case_id=null", lambda d: d.mapping.case_id, "null"),
    ("output=true", lambda d: d.output, "true"),
]


@pytest.mark.parametrize(("argument", "read", "expected"), SET_CASES, ids=[c[0] for c in SET_CASES])
def test_set_changes_the_value(
    argument: str, read: Callable[[InitDraft], object], expected: object
) -> None:
    assert read(apply_overrides(BASE, [argument])) == expected


@pytest.mark.parametrize("argument", [c[0] for c in SET_CASES])
def test_set_value_survives_the_round_trip(argument: str) -> None:
    changed = apply_overrides(BASE, [argument])

    check_round_trip(render_config_yaml(changed), changed)


@pytest.mark.parametrize(
    "argument",
    [
        "foo=bar",
        "mapping.nope=x",
        "mapping=x",
        "mapping.operation=x",
        "label_map=benign",
        "traces=x",
        "=x",
        "telemetry=true",
        "Mapping.case_id=x",
    ],
)
def test_unknown_set_key_is_rejected_by_name(argument: str) -> None:
    key = argument.partition("=")[0]

    with pytest.raises(OverrideError, match=re.escape(f"unknown key '{key}'")):
        apply_overrides(BASE, [argument])


def test_unknown_set_key_error_lists_the_valid_keys() -> None:
    with pytest.raises(OverrideError, match=re.escape("mapping.operation.span_name_fallback")):
        apply_overrides(BASE, ["foo=bar"])


def test_set_without_equals_is_rejected() -> None:
    with pytest.raises(OverrideError, match="expected key=value"):
        apply_overrides(BASE, ["mapping.case_id"])


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ("traces.format=xml", "traces.format: Input should be"),
        ("dashboard.max_detail_cases=-1", "greater than or equal to 0"),
        ("dashboard.max_detail_cases=abc", "valid integer"),
        ("dashboard.max_detail_cases=true", "valid integer"),
        ("mapping.prompt_version_lookup=x", "mapping.prompt_version_lookup"),
        ("mapping.operation.span_name_fallback=maybe", "valid boolean"),
        ("label_map.x=maybe", "label_map.x"),
        ("label_map.=benign", "empty after normalization"),
        ("label_map. =benign", "empty after normalization"),
        ("traces.path=", "path is empty"),
        ("traces.path=~/x", "not expanded"),
        ("output=results/", "must end in a file name"),
        ("mapping.case_id=\udc80", "not valid text"),
    ],
)
def test_bad_set_value_gives_the_model_message(argument: str, message: str) -> None:
    with pytest.raises(OverrideError, match=message):
        apply_overrides(BASE, [argument])


def test_set_case_id_satisfies_the_missing_field() -> None:
    missing = draft(missing_required=("case_id", "verdict"))

    assert apply_overrides(missing, ["mapping.case_id=soc.case"]).missing_required == ("verdict",)


def test_set_verdict_satisfies_the_missing_field() -> None:
    missing = draft(missing_required=("case_id", "verdict"))

    assert apply_overrides(missing, ["mapping.verdict=soc.v"]).missing_required == ("case_id",)


def test_set_label_satisfies_missing_labels() -> None:
    missing = draft(label_map={}, missing_required=(REQUIRED_LABELS,))

    assert apply_overrides(missing, ["label_map.x=benign"]).missing_required == ()


def test_set_label_removes_it_from_the_unmapped_labels() -> None:
    changed = apply_overrides(BASE, ["label_map.closed  -  BENIGN=benign"])

    assert changed.unmapped_analyst_labels == ()


def test_set_agent_label_removes_it_from_the_unmapped_agent_labels() -> None:
    changed = apply_overrides(
        draft(unmapped_agent_labels=("Bad",)), ["agent_label_map.bad=true_positive"]
    )

    assert changed.unmapped_agent_labels == ()


def test_set_label_replaces_another_spelling_of_it() -> None:
    changed = apply_overrides(BASE, ["label_map.tp=false_positive"])

    assert changed.label_map == {"tp": Verdict.FALSE_POSITIVE}


def test_set_mapping_field_marks_its_coverage() -> None:
    changed = apply_overrides(BASE, ["mapping.case_id=soc.case"])

    assert changed.coverage["case_id"] == "set with --set"


def test_later_set_wins() -> None:
    changed = apply_overrides(BASE, ["output=a.html", "output=b.html"])

    assert changed.output == "b.html"


# Example checklist


@pytest.mark.parametrize(
    ("alert_class", "expected"),
    [
        ("impossible_travel", "impossible_travel"),
        ("Impossible Travel", "impossible_travel"),
        ("oauth-consent", "oauth-consent"),
        ("../../x", "x"),
        ("..", "example"),
        (".", "example"),
        ("", "example"),
        ("   ", "example"),
        ("日本語", "example"),
        ("café", "caf"),
        ("a/b\\c", "a_b_c"),
        ("a" * 200, "a" * 64),
        ("a" * 63 + " b", "a" * 63),
        (".hidden", "hidden"),
        ("-x-", "x"),
        ("a\x00b\nc", "a_b_c"),
        ("con", "con_checklist"),
        ("CON", "con_checklist"),
        ("nul", "nul_checklist"),
        ("com1", "com1_checklist"),
        ("lpt9", "lpt9_checklist"),
        ("console", "console"),
    ],
)
def test_checklist_file_name(alert_class: str, expected: str) -> None:
    assert to_checklist_file_name(alert_class) == expected + ".yaml.example"


def load_example(tmp_path: Path, alert_class: str, tools: tuple[str, ...]) -> Checklist:
    folder = tmp_path / "checklists"
    folder.mkdir()
    (folder / "x.yaml").write_text(
        render_example_checklist(alert_class, tools, len(tools)), encoding="utf-8"
    )
    return load_checklists(folder)[normalize_label(alert_class)]


@pytest.mark.parametrize("alert_class", ["impossible_travel", 'x: "y"\n# z', "yes", "😀"])
def test_example_checklist_keeps_the_alert_class(tmp_path: Path, alert_class: str) -> None:
    checklist = load_example(tmp_path, alert_class, ("t",))

    assert checklist.alert_class == alert_class


def test_example_checklist_keeps_tool_names(tmp_path: Path) -> None:
    tools = ("get: logs", "null", "a\nb", "😀")

    checklist = load_example(tmp_path, "c", tools)

    assert tuple(item.tool for item in checklist.items) == tools


def test_example_checklist_has_no_argument_rules(tmp_path: Path) -> None:
    checklist = load_example(tmp_path, "c", ("a", "b"))

    assert [item.args for item in checklist.items] == [{}, {}]


def test_example_checklist_ids_are_safe_and_unique(tmp_path: Path) -> None:
    checklist = load_example(tmp_path, "c", ("A B", "a b", "a_b", "a_b_2", "日本", "..."))

    assert tuple(item.id for item in checklist.items) == (
        "a_b",
        "a_b_2",
        "a_b_3",
        "a_b_2_2",
        "tool",
        "tool_2",
    )


def test_example_checklist_skips_blank_tool_names(tmp_path: Path) -> None:
    checklist = load_example(tmp_path, "c", ("  ", "a"))

    assert len(checklist.items) == 1


def test_example_checklist_caps_its_items(tmp_path: Path) -> None:
    checklist = load_example(tmp_path, "c", tuple(f"tool{n:03}" for n in range(60)))

    assert len(checklist.items) == 50


def test_example_checklist_says_how_many_tools_are_left_out() -> None:
    text = render_example_checklist("c", tuple(f"tool{n:03}" for n in range(60)), 60)

    assert "# 10 more tools are not listed." in text.splitlines()


def test_example_checklist_counts_tools_left_out_of_the_proposal_too() -> None:
    text = render_example_checklist("c", tuple(f"tool{n:03}" for n in range(200)), 250)

    assert "# 200 more tools are not listed." in text.splitlines()


def test_example_checklist_does_not_claim_to_list_every_tool() -> None:
    text = render_example_checklist("c", ("a",), 1)

    assert "every tool" not in text


def test_config_header_does_not_claim_the_example_lists_every_tool() -> None:
    assert "every tool" not in render_config_yaml(BASE)


def test_draft_keeps_the_example_class_tool_count() -> None:
    created = create_draft(
        proposal({"c": 1}, {"c": ("a", "b")}, tool_counts={"c": 250}),
        config_path=Path("detecttrace.yaml"),
        traces_path=Path("traces"),
        verdicts_path=Path("verdicts.csv"),
    )

    assert created.example_tool_count == 250


def test_example_checklist_without_tools_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one tool"):
        render_example_checklist("c", (" ",), 1)


def test_example_checklist_file_is_not_loaded_before_renaming(tmp_path: Path) -> None:
    (tmp_path / to_checklist_file_name("c")).write_text(
        render_example_checklist("c", ("a",), 1), encoding="utf-8"
    )

    assert load_checklists(tmp_path) == {}


def proposal(
    counts: dict[str, int],
    tools: dict[str, tuple[str, ...]],
    tool_counts: dict[str, int] | None = None,
) -> Proposal:
    field = FieldProposal("detecttrace.case_id", "detecttrace attribute", 1, 1)
    return Proposal(
        trace_format="otlp_jsonl",
        agent_run_count=1,
        mapping=MappingProposal(
            field,
            field,
            field,
            field,
            field,
            field,
            OperationProposal("gen_ai.operation.name", "invoke_agent", "execute_tool", True, "x"),
        ),
        label_map={},
        agent_label_map={},
        unmapped_analyst_labels=(),
        unmapped_agent_labels=(),
        trace_cases=[],
        tool_names_by_class=tools,
        tool_counts_by_class={name: len(names) for name, names in tools.items()}
        if tool_counts is None
        else tool_counts,
        case_counts_by_class=counts,
        traces_without_verdict=OrphanSummary(0, ()),
        verdicts_without_trace=OrphanSummary(0, ()),
        missing_required=(),
        notes=(),
    )


def test_example_class_has_the_most_cases() -> None:
    chosen = choose_example_class(proposal({"a": 1, "b": 3}, {"a": ("t",), "b": ("t",)}))

    assert chosen == "b"


def test_example_class_tie_goes_to_the_normalized_name_order() -> None:
    chosen = choose_example_class(proposal({"b": 2, "A": 2}, {"b": ("t",), "A": ("t",)}))

    assert chosen == "A"


def test_example_class_skips_classes_without_tools() -> None:
    chosen = choose_example_class(proposal({"a": 1, "b": 3}, {"a": ("t",), "b": ()}))

    assert chosen == "a"


def test_no_example_class_without_tools() -> None:
    assert choose_example_class(proposal({"a": 1}, {"a": ()})) is None


def test_draft_without_example_class_has_no_checklists_path(tmp_path: Path) -> None:
    created = create_draft(
        proposal({}, {}),
        config_path=tmp_path / "detecttrace.yaml",
        traces_path=tmp_path / "traces",
        verdicts_path=tmp_path / "verdicts.csv",
    )

    assert created.checklists_path is None


def test_draft_paths_are_relative_to_the_config_file(tmp_path: Path) -> None:
    created = create_draft(
        proposal({}, {}),
        config_path=tmp_path / "config" / "detecttrace.yaml",
        traces_path=tmp_path / "data" / "traces",
        verdicts_path=tmp_path / "verdicts.csv",
    )

    assert (created.traces_path, created.verdicts_path) == ("../data/traces", "../verdicts.csv")


# The demo, end to end


@pytest.fixture(scope="module")
def demo_draft(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, InitDraft]:
    folder = tmp_path_factory.mktemp("init")
    shutil.copytree(DEMO / "traces", folder / "traces")
    shutil.copy(DEMO / "verdicts.csv", folder / "verdicts.csv")
    spans, _ = load_spans(folder / "traces")
    rows, _ = read_verdicts(folder / "verdicts.csv")
    config_path = folder / "detecttrace.yaml"
    created = create_draft(
        propose_init(spans, "otlp_jsonl", rows),
        config_path=config_path,
        traces_path=folder / "traces",
        verdicts_path=folder / "verdicts.csv",
    )
    assert created.example_class is not None
    config_path.write_text(render_config_yaml(created), encoding="utf-8")
    (folder / "checklists").mkdir()
    (folder / "checklists" / "example.yaml").write_text(
        render_example_checklist(
            created.example_class, created.example_tools, created.example_tool_count
        ),
        encoding="utf-8",
    )
    return config_path, created


def test_demo_config_passes_the_round_trip_check(demo_draft: tuple[Path, InitDraft]) -> None:
    config_path, created = demo_draft

    check_round_trip(config_path.read_text(encoding="utf-8"), created)


def test_demo_config_scores_the_same_cases_as_the_demo(
    demo_draft: tuple[Path, InitDraft],
) -> None:
    config_path, _ = demo_draft
    demo_path = DEMO / "detecttrace.yaml"
    expected = run_check(load_run_config(demo_path), demo_path).case_count

    assert run_check(load_run_config(config_path), config_path).case_count == expected
