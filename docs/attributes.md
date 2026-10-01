# Trace attributes

This page says which span attributes DetectTrace reads, where it reads them, and how to point it at other names. For how to get trace files, see [Trace sources](trace-sources.md). For how tool calls are scored, see [Checklists](checklists.md).

## Case attributes

A case is one agent run: an `invoke_agent` span that carries a case ID. DetectTrace joins it with the analyst verdict row of the same case ID.

The OpenTelemetry GenAI conventions have no names for a case ID, an alert class, an agent verdict or a prompt version, so DetectTrace defines four:

| Attribute | Mapping key | Read from | Needed for |
|---|---|---|---|
| `detecttrace.case_id` | `case_id` | The agent span only | Everything. An agent span without it is not scored. |
| `detecttrace.alert_class` | `alert_class` | The agent span, then its resource | Nothing. The verdict file's class is used. |
| `detecttrace.verdict` | `verdict` | The agent span only | Verdict agreement and dangerous false closes |
| `detecttrace.prompt_version` | `prompt_version` | The agent span, then its resource; with `prompt_version_lookup: descendant`, then the spans below it | The split by version. Without it, a case shows as "(no version)". |

Notes:

- **Alert class.** The verdict file's `alert_class` decides the class. When the trace gives a different class, the case is reported as a conflict, and the verdict file's value is used. Classes match without regard to case or extra spaces.
- **Agent verdict.** The value goes through `agent_label_map`, then `label_map`, to become `true_positive`, `false_positive` or `benign`. A label with no mapping is reported, never guessed.
- **Resource attributes.** A value set once on the resource (for example `detecttrace.prompt_version` on a service that runs one version) applies to every agent span from that process. An attribute on the agent span wins.

### Values

| Value type | Read as |
|---|---|
| String | The text, with leading and trailing spaces removed |
| Integer or double | An integer reads as its digits (`2` reads `"2"`); a double reads in its shortest form (`1.5` reads `"1.5"`, `2.0` reads `"2.0"`) |
| Boolean | `"true"` or `"false"` |
| Blank string | Missing |
| Array, map, NaN or infinity | Missing, and reported as an attribute that could not be used |

Values longer than 200 characters are cut to 199 characters plus "…", and reported once. The verdict file's values are cut the same way, so a long case ID still joins.

### Prompt and model together

DetectTrace splits results by one version label, read from one attribute. It can't combine two attributes. To see prompt and model changes in the same view, set one label that carries both when the agent run starts:

```python
run.set_attribute("detecttrace.prompt_version", f"{prompt_version}+{model}")
```

`v2+model-a` and `v2+model-b` then show as two versions. Each combination is a separate version, so cases split thinner, and only the six versions with the most cases are shown per class. The rest are grouped as `other`.

To split by model alone, map `prompt_version` to the attribute that holds the model:

```yaml
mapping:
  prompt_version: gen_ai.request.model
```

If the model is set only on the spans below the agent run, add `prompt_version_lookup: descendant`. A run that used two different models then has no version, and it is reported.

## GenAI conventions

DetectTrace reads these names from the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/), pinned to semantic-conventions v1.41.1. Every name below is present from v1.38.0.

| Attribute | Value | Use |
|---|---|---|
| `gen_ai.operation.name` | `invoke_agent` | The span is an agent run |
| `gen_ai.operation.name` | `execute_tool` | The span is a tool call |
| `gen_ai.tool.name` | The tool's name | Matched against the checklist's `tool` |
| `gen_ai.tool.call.arguments` | JSON text, or a map | Checked by the checklist's argument rules |
| `error.type` | Any value | The tool call failed |

**Span name fallback.** When `gen_ai.operation.name` is missing, blank or not a string, the first word of the span name is used instead. So a span named `invoke_agent triage` is an agent run, and `execute_tool get_signin_logs` is a tool call. Set `mapping.operation.span_name_fallback: false` to turn this off.

**Tool name fallback.** When `gen_ai.tool.name` is missing or blank, a tool span named `execute_tool <name>` takes `<name>` as its tool name. Only that form counts. A tool span with no name is reported, and it satisfies no checklist item. Tool names match checklist items exactly, including case.

**Tool arguments.** JSON text such as `{"user": "user1@example.com", "lookback": "P7D"}`, or an OTLP map (`kvlistValue`). Any other type is reported. JSON text that can't be parsed is reported as "arguments could not be read", and argument rules on that call fail.

**Failed tool calls.** A tool call failed when its span status is `ERROR`, or when it has an `error.type` attribute with any value. A failed call never satisfies a checklist item. See [Checklists](checklists.md#outcomes) for how retries count.

## Example

An agent run and one of its tool calls, with the OpenTelemetry Python API:

```python
import json

from opentelemetry import trace

tracer = trace.get_tracer("soc-agent")

with tracer.start_as_current_span("invoke_agent triage") as run:
    run.set_attributes(
        {
            "gen_ai.operation.name": "invoke_agent",
            "detecttrace.case_id": "CASE-1042",
            "detecttrace.alert_class": "impossible_travel",
            "detecttrace.prompt_version": "v2",
        }
    )
    with tracer.start_as_current_span("execute_tool get_signin_logs") as call:
        call.set_attributes(
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "get_signin_logs",
                "gen_ai.tool.call.arguments": json.dumps(
                    {"user": "user1@example.com", "range": "P7D"}
                ),
            }
        )
    run.set_attribute("detecttrace.verdict", "benign")
```

## How spans become cases

DetectTrace walks each trace from its top spans down.

| Situation | What happens |
|---|---|
| An agent span with a case ID, not inside a case | It opens a case. |
| A tool span below it | The tool call belongs to that case. |
| An agent span below it with no case ID, or with the same case ID | A sub-agent. Its tool calls count for the case. |
| An agent span below it with a different case ID | It opens its own case. The tool calls below it count there, not in the outer case. Reported as a nested case. |
| An outer case's ID again, deeper inside a nested case (DT-1 inside DT-2 inside DT-1) | No new case opens. The span is a sub-agent of the innermost case (DT-2), and its tool calls count there. Reported as a nested case. |
| An agent span with no case ID, not inside a case | Reported: "invoke_agent span has no case ID and was not scored". This includes an orchestrator agent above the cases. |
| A tool span not inside any case | Reported as an orphan tool span. Every case in the same trace is reported as incomplete. |
| A span whose parent is not in the files | Walked as a top span. A tool span below it with no case is reported as a broken parent chain. |
| A parent cycle | Reported, and walked once. |
| The same case ID on more than one agent span that aren't nested | One is kept: the latest start, then the latest end, then the highest trace ID, then the highest span ID. The others are reported. |
| The same span (trace ID and span ID) more than once | The first copy is kept, in file path order. Identical copies and copies that differ are reported separately. |

A case's start time is its agent span's start. The weekly trend uses it (see [Metrics](metrics.md#weekly-trend)).

## The `mapping` section

Every key is optional. The defaults read the names above. An unknown key is an error.

| Key | Default | What it names |
|---|---|---|
| `case_id` | `detecttrace.case_id` | The case ID attribute |
| `alert_class` | `detecttrace.alert_class` | The alert class attribute |
| `verdict` | `detecttrace.verdict` | The agent verdict attribute |
| `prompt_version` | `detecttrace.prompt_version` | The prompt or model version attribute |
| `prompt_version_lookup` | `root_then_resource` | Where to look for the version: `root_then_resource` or `descendant` |
| `tool_name` | `gen_ai.tool.name` | The tool name attribute |
| `tool_arguments` | `gen_ai.tool.call.arguments` | The tool arguments attribute |
| `operation.attribute` | `gen_ai.operation.name` | The attribute that says what a span is |
| `operation.agent_value` | `invoke_agent` | Its value on agent spans |
| `operation.tool_value` | `execute_tool` | Its value on tool spans |
| `operation.span_name_fallback` | `true` | Use the span name's first word when the attribute is missing |

With `prompt_version_lookup: descendant`, the version is read from the agent span, then its resource, then every span below the agent span that belongs to the case. When those spans hold more than one distinct version, the case has no version, and it is reported.

This configuration spells out every default:

```yaml
traces:
  path: traces/
verdicts:
  path: verdicts.csv
label_map:
  TP: true_positive
  FP: false_positive
  Benign: benign
mapping:
  case_id: detecttrace.case_id
  alert_class: detecttrace.alert_class
  verdict: detecttrace.verdict
  prompt_version: detecttrace.prompt_version
  prompt_version_lookup: root_then_resource
  tool_name: gen_ai.tool.name
  tool_arguments: gen_ai.tool.call.arguments
  operation:
    attribute: gen_ai.operation.name
    agent_value: invoke_agent
    tool_value: execute_tool
    span_name_fallback: true
```

`detecttrace init` proposes the mapping from your traces. It proposes a key when it is on at least half of the agent runs.

## OpenInference

OpenInference instrumentation marks spans with `openinference.span.kind` (`AGENT`, `TOOL`) instead of `gen_ai.operation.name`, and puts tool arguments in `input.value`. Map them like this, with your own names for the case attributes:

```yaml
traces:
  path: traces/
  format: otlp_jsonl
verdicts:
  path: verdicts.csv
label_map:
  TP: true_positive
  Malicious: true_positive
  FP: false_positive
  Benign: benign
checklists: checklists/
mapping:
  case_id: soc.case.id
  alert_class: soc.alert.class
  verdict: soc.agent.verdict
  prompt_version: soc.prompt.version
  tool_name: tool.name
  tool_arguments: input.value
  operation:
    attribute: openinference.span.kind
    agent_value: AGENT
    tool_value: TOOL
```

`init` recognizes OpenInference spans and proposes the `operation`, `tool_name` and `tool_arguments` keys.

## Langfuse

With `traces.format: langfuse`, DetectTrace reads Langfuse observation rows and turns each row into a span. How to export them is in [Trace sources](trace-sources.md#langfuse). The mapping above then applies to the attributes below.

| Langfuse row | Becomes |
|---|---|
| `id`, `traceId`, `parentObservationId` | The span ID, trace ID and parent. Hex IDs are lowercased; other IDs are kept as given. |
| `startTime`, `endTime` | The span's times. A time without a zone (the blob export's form) is read as UTC. |
| `type: AGENT` | `gen_ai.operation.name: invoke_agent`, unless the row's attributes set it |
| `type: TOOL` | `gen_ai.operation.name: execute_tool`, unless the row's attributes set it |
| `name` of a `TOOL` row | `gen_ai.tool.name`, unless the attribute is set. A name in the `execute_tool <tool>` form goes through the tool name fallback instead. |
| `input` of a `TOOL` row | `gen_ai.tool.call.arguments`, unless the attribute is set |
| `level: ERROR` | A failed span |
| `metadata` key `attributes.<name>` | Span attribute `<name>` |
| `metadata` key `resourceAttributes.<name>` | Resource attribute `<name>` |
| `metadata` keys `scope` and `scope.*` | Ignored |
| Any other `metadata` key | Span attribute `langfuse.metadata.<key>` |
| `promptName` | `langfuse.prompt_name` |
| `promptVersion` | `langfuse.prompt_version` |
| `version` | `langfuse.version` |
| `sessionId` | `langfuse.session_id` |
| `traceName` | `langfuse.trace_name` |

Rows may use the API's camelCase names or the blob export's snake_case names (`trace_id`, `prompt_version` and so on). When a `langfuse.*` attribute and a span attribute of the same name differ, the span attribute is kept, and the row is reported.

So the `detecttrace.*` attributes your agent sets on its spans reach DetectTrace through `metadata`, and the default mapping works unchanged.

### Langfuse-managed prompts

When your agent uses a prompt managed in Langfuse, Langfuse links the prompt's name and version to the generation that used it, not to the agent run. To split results by that version, read it from the spans below the agent run:

```yaml
traces:
  path: langfuse-export/
  format: langfuse
verdicts:
  path: verdicts.csv
label_map:
  TP: true_positive
  FP: false_positive
  Benign: benign
mapping:
  prompt_version: langfuse.prompt_version
  prompt_version_lookup: descendant
```

A version stored as a number reads as its text, so version 2 shows as "2". A case whose generations used two different versions has no version, and it is reported.

`init` proposes this mapping for Langfuse input when no version is found on the agent runs and the managed prompt version is found under at least half of them.

## What is never read

DetectTrace never reads tool results: not `gen_ai.tool.call.result`, and not the `output` field of Langfuse rows. The dashboard holds tool names and arguments for notable cases only, never results.
