# Running on files

`detecttrace init` and `detecttrace check` run DetectTrace on files on disk. [`detecttrace demo`](#demo) runs the same checks on bundled synthetic data. `init` proposes a configuration from your traces and verdicts. `check` reads that configuration and writes one self-contained HTML dashboard that opens without a network.

To upload files in a browser instead, with no configuration file to write, see [Running locally in your browser](ui.md). For a service that a Collector feeds, see [Running as a service](serve.md).

## Contents

- [Propose a configuration](#propose-a-configuration)
- [Check](#check)
- [Demo](#demo)
- [Writing the configuration by hand](#writing-the-configuration-by-hand)
- [Inputs](#inputs)

## Propose a configuration

`detecttrace init` reads your traces and verdicts, detects the trace format, and proposes the attribute mapping and the label map. It shows how many agent runs each proposal covers, lists the tools your agent called per alert class, and asks you to confirm or change each field.

```sh
uv run detecttrace init --traces traces/ --verdicts verdicts.csv
```

It writes `detecttrace.yaml` and one example checklist, `checklists/<class>.yaml.example`. The example lists the tools the agent called, most called first. It has no effect until you edit it down to the calls your playbook requires and rename it to `.yaml`. A checklist copied from what the agent happened to do would show a completeness of about 100% for a playbook nobody wrote.

`init` maps only unambiguous verdict labels (`TP`, `true positive`, `TruePositive`, `FP`, `benign`, `BenignPositive` and similar). It asks about every other label. With `--yes` it leaves them unmapped and says so.

| Option | What it does |
|---|---|
| `--traces PATH` | A trace file or a folder of files. Required. |
| `--verdicts PATH` | The analyst verdict CSV. Required. |
| `--config PATH` | Where to write the configuration. Default: `detecttrace.yaml`. |
| `--set KEY=VALUE` | Override one proposal, for example `--set mapping.case_id=soc.case.id` or `--set "label_map.Closed - Benign=benign"`. Repeatable. |
| `--yes` | Take every proposal without asking. Stops with exit code 1 if the case ID or the agent verdict can't be found, or if no verdict label can be mapped. |
| `--dry-run` | Print the configuration and the example checklist, and write nothing. |
| `--force` | Replace an existing configuration or example checklist. |
| `--quiet` | Print nothing on success. Errors still go to stderr. |

## Check

`detecttrace check` reads the configuration (`detecttrace.yaml` by default) and writes the dashboard to the configured `output` file.

```sh
uv run detecttrace check --config detecttrace.yaml
```

| Option | What it does |
|---|---|
| `--config PATH` | The configuration file. Default: `detecttrace.yaml`. |
| `--out PATH` | Where to write the HTML dashboard, instead of `output` from the configuration. |
| `--json PATH` | Also write the results as JSON. For tests and debugging; the format may change before 1.0. |
| `--force` | Replace an existing file that DetectTrace didn't write. It replaces its own earlier output without this. |
| `--strict` | Exit with code 1 when any input is invalid. |
| `--quiet` | Print nothing on success. Errors still go to stderr. |

Exit codes:

- `0`: the dashboard was written.
- `1`: a configuration, input or usage error, an output file that can't or mustn't be replaced (see `--force`), no case could be scored, or any invalid input with `--strict`.
- `2`: an internal error.

## Demo

`detecttrace demo` runs every check on the bundled synthetic data and writes the dashboard to `detecttrace-demo.html`.

```sh
uv run detecttrace demo
```

| Option | What it does |
|---|---|
| `--out PATH` | Where to write the HTML dashboard. Default: `detecttrace-demo.html`. |
| `--json PATH` | Also write the results as JSON. For tests and debugging; the format may change before 1.0. |
| `--force` | Replace an existing file that DetectTrace didn't write. It replaces its own earlier output without this. |
| `--quiet` | Print nothing on success. Errors still go to stderr. |

## Writing the configuration by hand

You can also write the configuration by hand. Paths are relative to the file:

```yaml
traces:
  path: traces/            # a file or a folder of rotated files
  format: otlp_jsonl       # or otlp_json, or langfuse
verdicts:
  path: verdicts.csv
label_map:                 # your labels -> true_positive, false_positive, benign
  TP: true_positive
  FP: false_positive
  Closed - Benign: benign
agent_label_map:           # optional: the agent's labels, if they differ from the analysts'
  Malicious: true_positive
mapping:                   # optional: your attribute names, if they differ from the defaults
  case_id: soc.case.id
checklists: checklists/    # one YAML file per alert class
output: detecttrace-dashboard.html
dashboard:
  max_detail_cases: 2000   # how many notable cases keep their tool calls in the dashboard
```

Every `mapping` key and its default is in [Trace attributes](attributes.md).

## Inputs

### Traces

OTLP JSON files: JSON lines or a single document, uncompressed, gzip, or zstd (install the `zstd` extra). DetectTrace uses the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/). A case is an `invoke_agent` span, and tool calls are `execute_tool` spans. Extra attributes carry what the standard doesn't: `detecttrace.case_id`, `detecttrace.alert_class`, and `detecttrace.verdict`, plus an optional `detecttrace.prompt_version` for the version split. If your traces use other attribute names, map them in the configuration's `mapping` section. `init` proposes the mapping for you. The full specification is in [Trace attributes](attributes.md).

To get trace files, you have three options:

- **An OpenTelemetry Collector.** Add the contrib `file` exporter as a second exporter in your traces pipeline. Your existing backend still gets the data. Keep the exporter's default JSON format: its `compression` and `format: proto` options produce files DetectTrace can't read.
- **No Collector.** Install the `otel` extra and add `FileSpanExporter` to your agent. It writes OTLP JSON lines. `{date}` and `{pid}` in the path give one file per day and per process. The folder must exist.

  ```python
  from opentelemetry.sdk.trace import TracerProvider
  from opentelemetry.sdk.trace.export import BatchSpanProcessor

  from detecttrace.otel import FileSpanExporter

  provider = TracerProvider()
  provider.add_span_processor(BatchSpanProcessor(FileSpanExporter("traces/{date}-{pid}.jsonl")))
  ```

- **Langfuse.** Set `format: langfuse` and point `traces.path` at an export of observations: pages from the v2 observations API (request the `io` field group, which holds the tool arguments), or a blob storage export in JSON or JSONL (the `observations_v2/` folder). The Langfuse Python SDK exports only some spans by default. If tool calls are missing, set `should_export_span=lambda span: True`. DetectTrace warns when an export has agent runs but no tool calls.

The OpenTelemetry SDK's console exporter doesn't write OTLP JSON. DetectTrace recognizes its output and says so. Details for each source are in [Trace sources](trace-sources.md).

### Verdicts

A CSV with `case_id`, `alert_class`, and `verdict`. The label map converts your team's labels into three verdicts: `true_positive`, `false_positive`, and `benign`. Matching ignores case and extra spaces. Add `agent_label_map` if the agent uses different labels from the analysts. DetectTrace never guesses a label; unmapped labels are reported.

Every metric treats the analyst verdict as the truth, so keep the analysts blind to the agent's verdict. See [Keep the analysts blind](../README.md#keep-the-analysts-blind).

### Checklists

One YAML file per alert class, written by you from your own playbook.

An item is satisfied when the case has a successful call to that tool and every argument rule passes. Argument rules: `equals`, `in`, `exists`, `matches`, `min`, `max`, `min_duration`, and `kql_min_ago`. Without a checklist, an alert class gets verdict metrics only. Files ending in `.yaml.example` are ignored, and `check` notes each one. The format and every rule, with examples, are in [Checklists](checklists.md).
