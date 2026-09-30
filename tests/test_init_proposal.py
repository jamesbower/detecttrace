import shutil
from pathlib import Path

import pytest
from builders import make_span, span_hex

from detecttrace.config import MappingConfig, OperationConfig
from detecttrace.init_proposal import (
    SOURCE_DETECTTRACE,
    SOURCE_LANGFUSE_MANAGED_PROMPT,
    SOURCE_LANGFUSE_PROMPT,
    SOURCE_OPENINFERENCE,
    SOURCE_SET,
    SOURCE_SPAN_NAMES,
    SOURCE_SUFFIX,
    FieldProposal,
    OrphanSummary,
    Proposal,
    propose_init,
)
from detecttrace.model import Span, Verdict, VerdictRow
from detecttrace.runconfig import TraceFormat, load_run_config
from detecttrace.traces import load_spans
from detecttrace.verdicts import read_verdicts

FORMATS = Path(__file__).parent / "fixtures" / "formats"
REAL = Path(__file__).parent / "fixtures" / "langfuse_real"
REAL_API_PAGES = [f"v2_all_fields_page{number}.json" for number in (1, 2, 3, 4)]
MANAGED_PROMPT_PAGES = [f"managed_prompt_v2_all_fields_page{number}.json" for number in (1, 2, 3)]
AGENT: dict[str, object] = {"gen_ai.operation.name": "invoke_agent"}


def trace_id(number: int) -> str:
    return f"{number + 1:032x}"


def run(
    number: int, attributes: dict[str, object], resource: dict[str, object] | None = None
) -> Span:
    """One agent run in its own trace."""
    return make_span(
        span_hex(1),
        name="invoke_agent triage",
        trace_id=trace_id(number),
        attributes={**AGENT, **attributes},
        resource=resource,
    )


def tool(number: int, name: str, attributes: dict[str, object] | None = None) -> Span:
    """A tool call under run `number`."""
    return make_span(
        span_hex(2 + len(name)),
        span_hex(1),
        name=f"execute_tool {name}",
        trace_id=trace_id(number),
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            **(attributes or {}),
        },
    )


def calls(number: int, names: list[str]) -> list[Span]:
    """One tool call per name under run `number`; a name may repeat."""
    return [
        make_span(
            span_hex(100 + index),
            span_hex(1),
            name=f"execute_tool {name}",
            trace_id=trace_id(number),
            attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name},
        )
        for index, name in enumerate(names)
    ]


def runs(total: int, counts: dict[str, int]) -> list[Span]:
    """`total` agent runs; each key in `counts` is on that many of them, the first ones."""
    return [
        run(number, {key: "x" for key, count in counts.items() if number < count})
        for number in range(total)
    ]


def case(number: int, case_id: str, verdict: str = "TP", **attributes: object) -> Span:
    return run(
        number,
        {"detecttrace.case_id": case_id, "detecttrace.verdict": verdict, **attributes},
    )


def row(case_id: str, label: str = "TP", alert_class: str = "impossible_travel") -> VerdictRow:
    return VerdictRow(case_id, alert_class, label, 2)


def propose(
    spans: list[Span],
    rows: list[VerdictRow] | None = None,
    trace_format: TraceFormat = "otlp_jsonl",
) -> Proposal:
    return propose_init(spans, trace_format, [row("DT-1")] if rows is None else rows)


def propose_fixture(name: str) -> Proposal:
    config = load_run_config(FORMATS / name / "detecttrace.yaml")
    spans, _ = load_spans(config.traces.path, format=config.traces.format)
    rows, _ = read_verdicts(config.verdicts.path)
    return propose_init(spans, config.traces.format, rows)


def fixture_mapping(name: str) -> MappingConfig:
    return load_run_config(FORMATS / name / "detecttrace.yaml").mapping


@pytest.fixture
def real_spans(tmp_path: Path) -> list[Span]:
    for name in REAL_API_PAGES:
        shutil.copy(REAL / name, tmp_path / name)
    spans, _ = load_spans(tmp_path, format="langfuse")
    return spans


@pytest.fixture
def real_managed_prompt_spans(tmp_path: Path) -> list[Span]:
    for name in MANAGED_PROMPT_PAGES:
        shutil.copy(REAL / name, tmp_path / name)
    spans, _ = load_spans(tmp_path, format="langfuse")
    return spans


# Preference order


def test_proposes_the_detecttrace_name_when_present() -> None:
    proposal = propose([case(0, "DT-1")])
    assert proposal.mapping.case_id == FieldProposal(
        "detecttrace.case_id", SOURCE_DETECTTRACE, 1, 1
    )


def test_detecttrace_name_wins_over_a_suffix_match_with_more_coverage() -> None:
    spans = runs(10, {"detecttrace.case_id": 6, "soc.case.id": 10})
    assert propose(spans).mapping.case_id.value == "detecttrace.case_id"


def test_suffix_match_wins_when_the_detecttrace_name_is_under_half() -> None:
    spans = runs(10, {"detecttrace.case_id": 4, "soc.case.id": 9})
    assert propose(spans).mapping.case_id == FieldProposal("soc.case.id", SOURCE_SUFFIX, 9, 10)


@pytest.mark.parametrize(("covered", "expected"), [(49, None), (50, "soc.case.id")])
def test_a_proposal_must_cover_half_of_the_agent_runs(covered: int, expected: str | None) -> None:
    spans = runs(100, {"soc.case.id": covered})
    assert propose(spans).mapping.case_id.value == expected


def test_a_candidate_under_half_is_named_in_the_notes() -> None:
    spans = runs(100, {"soc.case.id": 49})
    assert (
        "case_id: soc.case.id is on only 49 of 100 agent runs; not proposed, since it must be "
        "on at least half"
    ) in propose(spans).notes


def test_the_below_half_note_names_the_candidate_with_the_most_coverage() -> None:
    spans = runs(10, {"detecttrace.case_id": 2, "soc.case.id": 4})
    assert (
        "case_id: soc.case.id is on only 4 of 10 agent runs; not proposed, since it must be "
        "on at least half"
    ) in propose(spans).notes


def test_a_field_found_nowhere_has_no_value_and_full_total() -> None:
    assert propose([run(0, {})]).mapping.alert_class == FieldProposal(None, "not found", 0, 1)


@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("case_id", "soc.case_id"),
        ("case_id", "case.id"),
        ("alert_class", "soc.alert.class"),
        ("alert_class", "x.alert_class"),
        ("verdict", "soc.agent.verdict"),
        ("prompt_version", "soc.prompt.version"),
        ("prompt_version", "app.prompt_version"),
    ],
)
def test_suffix_matches_each_known_ending(field: str, key: str) -> None:
    proposal = propose([run(0, {key: "x"})])
    assert getattr(proposal.mapping, field).value == key


def test_suffix_must_start_at_a_dot() -> None:
    assert propose([run(0, {"showcase_id": "DT-1"})]).mapping.case_id.value is None


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["a.longer.case_id", "z.case_id"], "z.case_id"),
        (["b.case_id", "a.case_id"], "a.case_id"),
    ],
)
def test_equal_coverage_picks_the_shortest_then_first_key(keys: list[str], expected: str) -> None:
    proposal = propose([run(0, dict.fromkeys(keys, "DT-1"))])
    assert proposal.mapping.case_id.value == expected


def test_higher_coverage_beats_a_shorter_key() -> None:
    spans = [
        run(0, {"a.case_id": "DT-1", "longer.case_id": "DT-1"}),
        run(1, {"longer.case_id": "DT-2"}),
    ]
    assert propose(spans).mapping.case_id.value == "longer.case_id"


def test_the_runner_up_is_named_in_the_notes() -> None:
    proposal = propose([run(0, {"a.case_id": "DT-1", "b.case_id": "DT-1"})])
    assert "case_id: also found b.case_id on 1 of 1 agent runs" in proposal.notes


def test_alert_class_is_read_from_resource_attributes() -> None:
    spans = [run(0, {}, resource={"soc.alert_class": "impossible_travel"})]
    assert propose(spans).mapping.alert_class.value == "soc.alert_class"


def test_case_id_is_not_read_from_resource_attributes() -> None:
    spans = [run(0, {}, resource={"soc.case_id": "DT-1"})]
    assert propose(spans).mapping.case_id.value is None


@pytest.mark.parametrize("value", ["", "   ", None, ["DT-1"], {"a": 1}, float("nan")])
def test_an_unreadable_value_does_not_count_as_present(value: object) -> None:
    assert propose([run(0, {"soc.case_id": value})]).mapping.case_id.value is None


def test_a_nested_agent_is_not_a_separate_agent_run() -> None:
    nested = make_span(
        span_hex(9), span_hex(1), name="invoke_agent sub", trace_id=trace_id(0), attributes=AGENT
    )
    assert propose([case(0, "DT-1"), nested]).mapping.case_id.total == 1


def sub_agent(number: int, attributes: dict[str, object]) -> Span:
    """An agent span under the orchestrator agent of trace 0."""
    return make_span(
        span_hex(10 + number),
        span_hex(1),
        name="invoke_agent triage",
        trace_id=trace_id(0),
        attributes={**AGENT, **attributes},
    )


def test_agents_with_a_case_id_under_an_orchestrator_are_the_agent_runs() -> None:
    spans = [run(0, {})] + [
        sub_agent(number, {"detecttrace.case_id": f"DT-{number}"}) for number in range(3)
    ]
    assert propose(spans).mapping.case_id == FieldProposal(
        "detecttrace.case_id", SOURCE_DETECTTRACE, 3, 3
    )


def test_an_orchestrated_agent_without_a_case_id_is_an_agent_run() -> None:
    spans = [run(0, {}), sub_agent(0, {"soc.case_id": "DT-0"}), sub_agent(1, {})]
    assert propose(spans).agent_run_count == 2


def test_an_agent_with_another_case_id_inside_a_case_is_a_second_agent_run() -> None:
    spans = [case(0, "DT-1"), sub_agent(0, {"detecttrace.case_id": "DT-2"})]
    assert propose(spans).agent_run_count == 2


def test_an_agent_with_the_same_case_id_inside_its_case_is_not_a_second_agent_run() -> None:
    spans = [case(0, "DT-1"), sub_agent(0, {"detecttrace.case_id": "DT-1"})]
    assert propose(spans).agent_run_count == 1


def test_an_agent_tree_without_a_case_id_is_one_agent_run() -> None:
    assert propose([run(0, {}), sub_agent(0, {})]).agent_run_count == 1


# Langfuse prompt version


def test_langfuse_prompt_version_is_proposed_for_langfuse_input() -> None:
    proposal = propose([run(0, {"langfuse.prompt_version": "3"})], trace_format="langfuse")
    assert proposal.mapping.prompt_version == FieldProposal(
        "langfuse.prompt_version", SOURCE_LANGFUSE_PROMPT, 1, 1
    )


def test_langfuse_prompt_version_beats_a_suffix_match() -> None:
    spans = [run(0, {"langfuse.prompt_version": "3", "a.prompt.version": "v1"})]
    proposal = propose(spans, trace_format="langfuse")
    assert proposal.mapping.prompt_version.value == "langfuse.prompt_version"


def test_detecttrace_prompt_version_beats_langfuse_prompt_version() -> None:
    spans = runs(10, {"detecttrace.prompt_version": 6, "langfuse.prompt_version": 10})
    proposal = propose(spans, trace_format="langfuse")
    assert proposal.mapping.prompt_version.value == "detecttrace.prompt_version"


def test_langfuse_prompt_version_is_only_a_suffix_match_for_otlp_input() -> None:
    proposal = propose([run(0, {"langfuse.prompt_version": "3"})])
    assert proposal.mapping.prompt_version.source == SOURCE_SUFFIX


def test_langfuse_service_version_is_never_a_prompt_version() -> None:
    proposal = propose([run(0, {"langfuse.version": "0.9.0"})], trace_format="langfuse")
    assert proposal.mapping.prompt_version.value is None


# Langfuse managed prompt, linked to a generation under the agent run


def generation(number: int, version: object, index: int = 0) -> Span:
    """A generation under run `number` linked to a managed prompt version, as Langfuse does."""
    return make_span(
        span_hex(50 + index),
        span_hex(1),
        name="chat example-model",
        trace_id=trace_id(number),
        start_ns=index,
        attributes={"langfuse.prompt_name": "soc-triage", "langfuse.prompt_version": version},
    )


def managed_cases(total: int, versioned: int) -> list[Span]:
    """`total` cases; the first `versioned` have a generation linked to prompt version 2."""
    spans = [case(number, f"DT-{number}") for number in range(total)]
    return spans + [generation(number, 2) for number in range(versioned)]


def test_real_managed_prompt_capture_proposes_the_langfuse_version_below_the_agent_run(
    real_managed_prompt_spans: list[Span],
) -> None:
    proposal = propose_init(real_managed_prompt_spans, "langfuse", [])
    assert proposal.mapping.prompt_version == FieldProposal(
        "langfuse.prompt_version", SOURCE_LANGFUSE_MANAGED_PROMPT, 3, 4
    )


def test_real_managed_prompt_capture_maps_with_the_descendant_lookup(
    real_managed_prompt_spans: list[Span],
) -> None:
    proposal = propose_init(real_managed_prompt_spans, "langfuse", [])
    assert proposal.mapping.to_mapping_config() == MappingConfig(
        prompt_version="langfuse.prompt_version", prompt_version_lookup="descendant"
    )


def test_real_managed_prompt_blob_export_proposes_the_descendant_lookup() -> None:
    spans, _ = load_spans(REAL / "managed_prompt_blob_observations_v2.jsonl", format="langfuse")
    proposal = propose_init(spans, "langfuse", [])
    assert proposal.mapping.prompt_version_lookup == "descendant"


def test_an_agent_level_version_wins_over_a_managed_prompt() -> None:
    spans = [case(0, "DT-0", **{"detecttrace.prompt_version": "v1"}), generation(0, 2)]
    proposal = propose(spans, trace_format="langfuse")
    assert proposal.mapping.prompt_version == FieldProposal(
        "detecttrace.prompt_version", SOURCE_DETECTTRACE, 1, 1
    )


def test_an_agent_level_version_keeps_the_default_lookup() -> None:
    spans = [case(0, "DT-0", **{"detecttrace.prompt_version": "v1"}), generation(0, 2)]
    proposal = propose(spans, trace_format="langfuse")
    assert proposal.mapping.prompt_version_lookup == "root_then_resource"


def test_a_managed_prompt_on_half_the_agent_runs_is_proposed() -> None:
    proposal = propose(managed_cases(4, 2), trace_format="langfuse")
    assert proposal.mapping.prompt_version == FieldProposal(
        "langfuse.prompt_version", SOURCE_LANGFUSE_MANAGED_PROMPT, 2, 4
    )


def test_a_managed_prompt_under_half_the_agent_runs_is_not_proposed() -> None:
    proposal = propose(managed_cases(3, 1), trace_format="langfuse")
    assert proposal.mapping.prompt_version.value is None


def test_a_managed_prompt_under_half_the_agent_runs_keeps_the_default_lookup() -> None:
    proposal = propose(managed_cases(3, 1), trace_format="langfuse")
    assert proposal.mapping.prompt_version_lookup == "root_then_resource"


def test_a_managed_prompt_under_half_the_agent_runs_is_noted() -> None:
    proposal = propose(managed_cases(3, 1), trace_format="langfuse")
    assert (
        "prompt_version: langfuse.prompt_version gives a version under only 1 of 3 agent runs; "
        "not proposed, since it must be on at least half" in proposal.notes
    )


def test_two_managed_prompt_versions_in_one_run_give_it_no_version() -> None:
    # check reports two versions under one agent run as a conflict and reads none.
    spans = [case(0, "DT-0"), generation(0, 1), generation(0, 2, index=1)]
    proposal = propose(spans, trace_format="langfuse")
    assert proposal.mapping.prompt_version.value is None


def test_a_managed_prompt_is_not_read_below_the_agent_run_for_otlp_input() -> None:
    proposal = propose(managed_cases(2, 2))
    assert proposal.mapping.prompt_version.value is None


def test_a_given_managed_prompt_mapping_keeps_its_source(
    real_managed_prompt_spans: list[Span],
) -> None:
    mapping = MappingConfig(
        alert_class="soc.kind",
        prompt_version="langfuse.prompt_version",
        prompt_version_lookup="descendant",
    )
    proposal = propose_init(real_managed_prompt_spans, "langfuse", [], mapping=mapping)
    assert proposal.mapping.prompt_version.source == SOURCE_LANGFUSE_MANAGED_PROMPT


def test_a_given_lookup_is_measured_as_set(real_managed_prompt_spans: list[Span]) -> None:
    mapping = MappingConfig(prompt_version="langfuse.prompt_version")
    proposal = propose_init(real_managed_prompt_spans, "langfuse", [], mapping=mapping)
    assert proposal.mapping.prompt_version == FieldProposal(
        "langfuse.prompt_version", SOURCE_SET, 0, 4
    )


def test_a_given_descendant_lookup_is_measured_below_the_agent_run() -> None:
    mapping = MappingConfig(prompt_version="soc.prompt", prompt_version_lookup="descendant")
    below = make_span(
        span_hex(9), span_hex(1), trace_id=trace_id(0), attributes={"soc.prompt": "p"}
    )
    spans = [case(0, "DT-0"), below]
    proposal = propose_init(spans, "otlp_jsonl", [], mapping=mapping)
    assert proposal.mapping.prompt_version == FieldProposal("soc.prompt", SOURCE_SET, 1, 1)


# Operation rule


def test_operation_name_gives_the_default_rule() -> None:
    proposal = propose([case(0, "DT-1")])
    assert proposal.mapping.to_mapping_config().operation == MappingConfig().operation


def test_span_names_give_the_span_name_fallback() -> None:
    span = make_span(
        span_hex(1), name="invoke_agent triage", attributes={"detecttrace.case_id": "DT-1"}
    )
    assert propose([span]).mapping.operation.source == SOURCE_SPAN_NAMES


def test_span_name_fallback_finds_agent_runs() -> None:
    span = make_span(
        span_hex(1), name="invoke_agent triage", attributes={"detecttrace.case_id": "DT-1"}
    )
    assert propose([span]).mapping.case_id.covered == 1


def test_openinference_span_kind_gives_the_openinference_rule() -> None:
    span = make_span(
        span_hex(1), attributes={"openinference.span.kind": "AGENT", "soc.case.id": "DT-1"}
    )
    operation = propose([span]).mapping.operation
    assert (operation.attribute, operation.agent_value, operation.tool_value, operation.source) == (
        "openinference.span.kind",
        "AGENT",
        "TOOL",
        SOURCE_OPENINFERENCE,
    )


def test_openinference_proposes_its_tool_attributes() -> None:
    span = make_span(span_hex(1), attributes={"openinference.span.kind": "AGENT"})
    mapping = propose([span]).mapping
    assert (mapping.tool_name.value, mapping.tool_arguments.value) == ("tool.name", "input.value")


def test_operation_name_on_non_agent_spans_does_not_hide_openinference() -> None:
    spans = [
        make_span(span_hex(1), attributes={"openinference.span.kind": "AGENT"}),
        make_span(
            span_hex(2), span_hex(1), name="chat", attributes={"gen_ai.operation.name": "chat"}
        ),
    ]
    assert propose(spans).mapping.operation.source == SOURCE_OPENINFERENCE


def test_no_operation_signal_finds_no_agent_runs() -> None:
    assert (
        propose(
            [make_span(span_hex(1), attributes={"detecttrace.case_id": "DT-1"})]
        ).agent_run_count
        == 0
    )


def test_tool_name_coverage_counts_tool_calls() -> None:
    spans = [case(0, "DT-1"), tool(0, "get_signin_logs"), tool(0, "x", {"gen_ai.tool.name": ""})]
    assert propose(spans).mapping.tool_name == FieldProposal("gen_ai.tool.name", "default", 1, 2)


def test_tool_argument_coverage_counts_object_arguments() -> None:
    spans = [case(0, "DT-1"), tool(0, "scan", {"gen_ai.tool.call.arguments": {"ip": "192.0.2.1"}})]
    assert propose(spans).mapping.tool_arguments.covered == 1


def test_tool_name_coverage_skips_a_number() -> None:
    spans = [case(0, "DT-1"), tool(0, "scan", {"gen_ai.tool.name": 7})]
    assert propose(spans).mapping.tool_name.covered == 0


# Fixtures


@pytest.mark.parametrize(
    "name",
    [
        "jsonl_rotated",
        "single_document",
        "no_operation_name",
        "openinference",
        "langfuse",
        "file_span_exporter",
    ],
)
def test_fixture_proposal_matches_its_configuration(name: str) -> None:
    assert propose_fixture(name).mapping.to_mapping_config() == fixture_mapping(name)


def test_no_operation_name_fixture_uses_span_names() -> None:
    assert propose_fixture("no_operation_name").mapping.operation.source == SOURCE_SPAN_NAMES


def test_openinference_fixture_case_id_coverage() -> None:
    assert propose_fixture("openinference").mapping.case_id == FieldProposal(
        "soc.case.id", SOURCE_SUFFIX, 201, 201
    )


def test_real_langfuse_capture_proposes_the_default_mapping(real_spans: list[Span]) -> None:
    proposal = propose_init(real_spans, "langfuse", [])
    assert proposal.mapping.to_mapping_config() == MappingConfig()


def test_real_langfuse_capture_prompt_version_coverage(real_spans: list[Span]) -> None:
    proposal = propose_init(real_spans, "langfuse", [])
    assert proposal.mapping.prompt_version == FieldProposal(
        "detecttrace.prompt_version", SOURCE_DETECTTRACE, 3, 4
    )


def test_real_langfuse_capture_keeps_its_format(real_spans: list[Span]) -> None:
    assert propose_init(real_spans, "langfuse", []).trace_format == "langfuse"


# Required fields and notes


@pytest.mark.parametrize(
    ("attributes", "missing"),
    [
        ({"detecttrace.verdict": "TP"}, ("case_id",)),
        ({"detecttrace.case_id": "DT-1"}, ("verdict",)),
        ({}, ("case_id", "verdict")),
        ({"detecttrace.case_id": "DT-1", "detecttrace.verdict": "TP"}, ()),
    ],
)
def test_missing_required_fields_are_named(
    attributes: dict[str, object], missing: tuple[str, ...]
) -> None:
    assert propose([run(0, attributes)]).missing_required == missing


def test_no_mappable_analyst_label_is_missing() -> None:
    proposal = propose([case(0, "DT-1")], [row("DT-1", "Escalated")])
    assert proposal.missing_required == ("verdict labels",)


def test_no_verdict_rows_is_missing_verdict_labels() -> None:
    assert propose([case(0, "DT-1")], []).missing_required == ("verdict labels",)


def test_missing_prompt_version_is_noted() -> None:
    assert (
        "no prompt version found; every case will show as (no version)"
        in propose([case(0, "DT-1")]).notes
    )


def test_missing_alert_class_is_noted() -> None:
    assert (
        "no alert class found on agent runs; alert classes come from the verdict file only"
        in propose([case(0, "DT-1")]).notes
    )


def test_langfuse_runs_without_tool_calls_are_left_to_the_input_problems() -> None:
    proposal = propose([case(0, "DT-1")], trace_format="langfuse")
    assert proposal.notes == (
        "no alert class found on agent runs; alert classes come from the verdict file only",
        "no prompt version found; every case will show as (no version)",
    )


# Label maps


@pytest.mark.parametrize(
    ("label", "verdict"),
    [
        ("true_positive", Verdict.TRUE_POSITIVE),
        ("True Positive", Verdict.TRUE_POSITIVE),
        ("  TRUE   positive ", Verdict.TRUE_POSITIVE),
        ("TruePositive", Verdict.TRUE_POSITIVE),
        ("TP", Verdict.TRUE_POSITIVE),
        ("false_positive", Verdict.FALSE_POSITIVE),
        ("false positive", Verdict.FALSE_POSITIVE),
        ("FalsePositive", Verdict.FALSE_POSITIVE),
        ("fp", Verdict.FALSE_POSITIVE),
        ("Benign", Verdict.BENIGN),
        ("benign_positive", Verdict.BENIGN),
        ("Benign Positive", Verdict.BENIGN),
        ("BenignPositive", Verdict.BENIGN),
    ],
)
def test_fixed_spellings_are_mapped(label: str, verdict: Verdict) -> None:
    assert propose([case(0, "DT-1")], [row("DT-1", label)]).label_map == {label: verdict}


@pytest.mark.parametrize(
    "label", ["Closed - Benign", "Malicious", "true-positive", "T P", "benign-positive", "positive"]
)
def test_other_spellings_are_left_unmapped(label: str) -> None:
    proposal = propose([case(0, "DT-1")], [row("DT-1", label)])
    assert proposal.unmapped_analyst_labels == (label,)


def test_spellings_of_one_label_are_proposed_once() -> None:
    rows = [row("DT-1", "tp"), row("DT-2", "TP"), row("DT-3", "Tp")]
    assert propose([case(0, "DT-1")], rows).label_map == {"TP": Verdict.TRUE_POSITIVE}


def test_unmapped_labels_are_sorted() -> None:
    rows = [row("DT-1", "Malicious"), row("DT-2", "Escalated"), row("DT-3", "Closed - Benign")]
    proposal = propose([case(0, "DT-1")], rows)
    assert proposal.unmapped_analyst_labels == ("Closed - Benign", "Escalated", "Malicious")


def test_agent_label_differing_from_analyst_labels_gets_its_own_map() -> None:
    proposal = propose([case(0, "DT-1", verdict="TruePositive")], [row("DT-1", "TP")])
    assert proposal.agent_label_map == {"TruePositive": Verdict.TRUE_POSITIVE}


def test_agent_label_equal_to_an_analyst_label_is_covered_by_the_label_map() -> None:
    proposal = propose([case(0, "DT-1", verdict="tp")], [row("DT-1", "TP")])
    assert proposal.agent_label_map == {}


def test_agent_label_differing_and_unknown_is_unmapped() -> None:
    proposal = propose([case(0, "DT-1", verdict="Escalate")], [row("DT-1", "TP")])
    assert proposal.unmapped_agent_labels == ("Escalate",)


def test_agent_label_equal_to_an_unmapped_analyst_label_is_asked_once() -> None:
    proposal = propose([case(0, "DT-1", verdict="Malicious")], [row("DT-1", "malicious")])
    assert proposal.unmapped_agent_labels == ()


def test_agent_labels_are_read_through_the_proposed_verdict_attribute() -> None:
    spans = [run(0, {"soc.case_id": "DT-1", "soc.agent.verdict": "FalsePositive"})]
    assert propose(spans, [row("DT-1", "TP")]).agent_label_map == {
        "FalsePositive": Verdict.FALSE_POSITIVE
    }


# Orphans and tool names


def test_traces_without_a_verdict_are_counted_with_sorted_examples() -> None:
    spans = [case(number, f"DT-{number}") for number in range(5)]
    proposal = propose(spans, [row("DT-0")])
    assert proposal.traces_without_verdict == OrphanSummary(4, ("DT-1", "DT-2", "DT-3"))


def test_verdicts_without_a_trace_are_counted() -> None:
    rows = [row("DT-0"), row("DT-9"), row("DT-8")]
    assert propose([case(0, "DT-0")], rows).verdicts_without_trace == OrphanSummary(
        2, ("DT-8", "DT-9")
    )


def test_tool_names_are_listed_per_alert_class() -> None:
    spans = [
        case(0, "DT-0"),
        tool(0, "get_signin_logs"),
        tool(0, "get_user_profile"),
        case(1, "DT-1"),
        tool(1, "get_ip_reputation"),
    ]
    rows = [row("DT-0", alert_class="impossible_travel"), row("DT-1", alert_class="phishing")]
    assert propose(spans, rows).tool_names_by_class == {
        "impossible_travel": ("get_signin_logs", "get_user_profile"),
        "phishing": ("get_ip_reputation",),
    }


def test_tool_names_are_listed_most_called_first() -> None:
    spans = [case(0, "DT-0"), *calls(0, ["b_tool", "c_tool", "c_tool", "a_tool"])]
    assert propose(spans, [row("DT-0")]).tool_names_by_class == {
        "impossible_travel": ("c_tool", "a_tool", "b_tool")
    }


def test_tool_names_listed_are_capped() -> None:
    spans = [case(0, "DT-0"), *calls(0, [f"tool{index:03d}" for index in range(201)])]
    assert len(propose(spans, [row("DT-0")]).tool_names_by_class["impossible_travel"]) == 200


def test_tool_counts_include_the_tools_past_the_cap() -> None:
    spans = [case(0, "DT-0"), *calls(0, [f"tool{index:03d}" for index in range(201)])]
    assert propose(spans, [row("DT-0")]).tool_counts_by_class == {"impossible_travel": 201}


def test_a_case_without_a_verdict_row_is_not_counted() -> None:
    spans = [case(0, "DT-0", **{"detecttrace.alert_class": "malware"}), case(1, "DT-1")]
    assert propose(spans, [row("DT-1")]).case_counts_by_class == {"impossible_travel": 1}


def test_the_verdict_file_alert_class_wins_over_the_trace_one() -> None:
    spans = [case(0, "DT-0", **{"detecttrace.alert_class": "malware"}), tool(0, "scan")]
    rows = [row("DT-0", alert_class="phishing")]
    assert propose(spans, rows).tool_names_by_class == {"phishing": ("scan",)}


def test_the_first_verdict_row_gives_the_alert_class() -> None:
    spans = [case(0, "DT-0"), tool(0, "scan")]
    rows = [row("DT-0", alert_class="phishing"), row("DT-0", alert_class="malware")]
    assert propose(spans, rows).tool_names_by_class == {"phishing": ("scan",)}


def test_cases_are_counted_per_alert_class() -> None:
    spans = [case(number, f"DT-{number}") for number in range(3)]
    rows = [row("DT-0"), row("DT-1"), row("DT-2", alert_class="phishing")]
    assert propose(spans, rows).case_counts_by_class == {"impossible_travel": 2, "phishing": 1}


def test_alert_classes_differing_in_case_are_one_class() -> None:
    spans = [case(0, "DT-0"), case(1, "DT-1")]
    rows = [row("DT-0", alert_class="Phishing"), row("DT-1", alert_class="phishing")]
    assert propose(spans, rows).case_counts_by_class == {"Phishing": 2}


# A given mapping


def test_a_given_key_is_reported_with_its_coverage() -> None:
    spans = [case(0, "DT-1", **{"soc.ticket": "X-1"}), case(1, "DT-2")]
    proposal = propose_init(spans, "otlp_jsonl", [], mapping=MappingConfig(case_id="soc.ticket"))
    assert proposal.mapping.case_id == FieldProposal("soc.ticket", SOURCE_SET, 1, 2)


def test_a_given_key_equal_to_the_detected_one_keeps_its_source() -> None:
    proposal = propose_init([case(0, "DT-1")], "otlp_jsonl", [], mapping=MappingConfig())
    assert proposal.mapping.case_id == FieldProposal(
        "detecttrace.case_id", SOURCE_DETECTTRACE, 1, 1
    )


def test_a_given_default_key_for_a_field_not_found_stays_not_found() -> None:
    proposal = propose_init([case(0, "DT-1")], "otlp_jsonl", [], mapping=MappingConfig())
    assert proposal.mapping.alert_class.value is None


def test_a_given_case_id_key_gives_the_orphans() -> None:
    spans = [case(0, "DT-1", **{"soc.ticket": "X-1"})]
    mapping = MappingConfig(case_id="soc.ticket")
    proposal = propose_init(spans, "otlp_jsonl", [row("DT-1")], mapping=mapping)
    assert proposal.traces_without_verdict == OrphanSummary(1, ("X-1",))


def test_a_given_verdict_key_gives_the_agent_labels() -> None:
    spans = [case(0, "DT-1", **{"soc.outcome": "Escalated"})]
    mapping = MappingConfig(verdict="soc.outcome")
    proposal = propose_init(spans, "otlp_jsonl", [row("DT-1")], mapping=mapping)
    assert proposal.unmapped_agent_labels == ("Escalated",)


def test_a_given_tool_name_key_gives_the_tool_names() -> None:
    spans = [case(0, "DT-1"), tool(0, "scan", {"soc.tool": "lookup"})]
    mapping = MappingConfig(tool_name="soc.tool")
    proposal = propose_init(spans, "otlp_jsonl", [row("DT-1")], mapping=mapping)
    assert proposal.tool_names_by_class == {"impossible_travel": ("lookup",)}


def test_a_given_operation_is_reported_as_set() -> None:
    spans = [run(0, {"soc.kind": "agent", "detecttrace.case_id": "DT-1"})]
    operation = OperationConfig(attribute="soc.kind", agent_value="agent", tool_value="tool")
    proposal = propose_init(spans, "otlp_jsonl", [], mapping=MappingConfig(operation=operation))
    assert proposal.mapping.operation.source == SOURCE_SET


def test_a_given_key_drops_the_detection_note_for_its_field() -> None:
    spans = [case(0, "DT-1", **{"soc.kind": "malware"})]
    mapping = MappingConfig(alert_class="soc.kind")
    proposal = propose_init(spans, "otlp_jsonl", [], mapping=mapping)
    assert (
        "no alert class found on agent runs; alert classes come from the verdict file only"
        not in proposal.notes
    )


# Hostile input


def test_ten_thousand_candidate_keys_pick_the_first_key() -> None:
    keys: dict[str, object] = {f"k{index:05d}.case_id": "DT-1" for index in range(10_000)}
    assert propose([run(0, keys), run(1, keys)]).mapping.case_id.value == "k00000.case_id"


def test_ten_thousand_candidate_keys_give_few_notes() -> None:
    keys: dict[str, object] = {f"k{index:05d}.case_id": "DT-1" for index in range(10_000)}
    assert len(propose([run(0, keys), run(1, keys)]).notes) < 10


def test_a_huge_key_is_never_proposed() -> None:
    spans = [run(0, {"x" * (1 << 20) + ".case_id": "DT-1"})]
    assert propose(spans).mapping.case_id.value is None


def test_a_key_with_control_characters_is_never_proposed() -> None:
    spans = [run(0, {"soc\x1b[31m.case_id": "DT-1"})]
    assert propose(spans).mapping.case_id.value is None


def test_many_unmapped_labels_are_capped() -> None:
    rows = [row(f"DT-{index}", f"label {index}") for index in range(5_000)]
    assert len(propose([case(0, "DT-1")], rows).unmapped_analyst_labels) == 100


def test_capped_unmapped_labels_are_noted() -> None:
    rows = [row(f"DT-{index}", f"label {index}") for index in range(5_000)]
    assert (
        "4,900 more unmapped analyst labels are not listed; check reports every one"
        in propose([case(0, "DT-1")], rows).notes
    )


def test_a_label_with_control_characters_is_kept_exactly() -> None:
    label = "Escalated\x1b[2J\n"
    assert propose([case(0, "DT-1")], [row("DT-1", label)]).unmapped_analyst_labels == (label,)
