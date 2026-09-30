# DetectTrace POC

Agent assurance for AI SOC agents. DetectTrace reads your agent's OpenTelemetry traces and your analysts' verdicts, then shows how the agent compares with the analysts, per alert class and per prompt version, and which investigation steps the agent skips.

> **Status: in development.** `detecttrace demo` and `detecttrace check` run every check and write one offline HTML dashboard. `detecttrace init` proposes a configuration from your traces and verdicts. Traces can come from OTLP JSON files or a Langfuse export. The first PyPI release is being prepared.

## The problem

AI SOC agents now triage and close real alerts. Teams that build their own agents usually run one in shadow mode on one alert class, then compare the agent's verdict with the analyst's. That agreement rate is often the only number they track.

One agreement number hides three problems:

1. **Base rates.** Most identity alerts are benign. An agent that always says "benign" can agree 85% of the time and add nothing.
2. **Wrong reasons.** An agent can reach the right verdict while it skips steps your incident response playbook requires. The verdict matches, but the investigation didn't happen.
3. **Change.** After a prompt or model change, one alert class can get worse while the overall average stays flat.

The most costly mistake is also the one agreement hides best: the agent closes a real attack as benign.

## What the POC gives you

A local command-line tool that turns traces and verdicts into a view that goes beyond one agreement number.

- **Verdict agreement with chance corrected.** Agreement rate and Cohen's kappa per alert class, with a 3 × 3 confusion matrix. Kappa shows when high agreement comes only from the base rate. Chance-corrected agreement is Cohen's kappa: 0 means no better than chance, 1 means perfect agreement.
- **Dangerous false closes.** Cases where the analyst said true positive and the agent said benign or false positive, as a count with the case IDs.
- **Evidence completeness.** You write a short checklist of the tool calls your playbook requires for an alert class. DetectTrace checks each case's successful tool calls against it and reports the share of items satisfied, per case and per class. A step is reported as failed (the call errored), not called, or called with the wrong arguments.
- **Skipped steps by version.** How often each checklist step was skipped, per prompt or model version. For example: "MFA check skipped: 5% in v1, 45% in v2."
- **Weekly trend.** Per alert class, split by version, with the number of cases each week.
- **Honest uncertainty.** Every value carries its number of cases (n) and a 95% confidence interval. Small samples are marked as such.
- **Case detail.** Each case with the agent verdict, the analyst verdict and checklist coverage, and, for notable cases (dangerous closes, disagreements, failed calls, missed steps), its tool calls.
- **Data notes.** Bad input is reported, not hidden: orphan traces and verdicts, duplicates, unmapped labels, and tool arguments that couldn't be read.

It shows the numbers. It doesn't explain why a number changed, check whether the agent's conclusions are supported by its tool results, or fail builds. Results are labeled as self-reported.

## Who it's for

Engineers who build their own AI SOC agent and have:

- Agent traces in OpenTelemetry, emitted directly or through a tool such as Langfuse.
- Analyst verdicts for the same cases, usually from a shadow-mode period.
- An incident response playbook for the alert class.

If you use a closed third-party agent and can't get its traces, this tool can't help you.

## Try the demo

Requires Python 3.11 or later and [uv](https://docs.astral.sh/uv/). DetectTrace isn't on PyPI yet, so run it from a clone:

```sh
git clone https://github.com/jamesbower/detecttrace.git
cd detecttrace
uv sync
uv run detecttrace demo
```

The demo runs on about 200 synthetic cases in two identity alert classes over six weeks. Prompt v1 runs in weeks 1 and 2, and v2 from week 3. On impossible travel, v2 often skips the MFA check and narrows the sign-in lookback, so evidence completeness drops and a few threats are closed as benign. OAuth consent doesn't change, so it serves as the control. The results show the drop, not the cause.

It prints a short summary and writes `detecttrace-demo.html`, a self-contained dashboard that opens in any browser without a network connection. Running the demo again replaces its own earlier page.

## Run it on your data

### 1. Propose a configuration

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

### 2. Check

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

Exit codes: `0` when the dashboard was written, `1` for a configuration, input or usage error, an output file that can't or mustn't be replaced (see `--force`), when no case could be scored, or for any invalid input with `--strict`, and `2` for an internal error.

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
checklists: checklists/    # one YAML file per alert class
output: detecttrace-dashboard.html
```

### Inputs

**Traces.** OTLP JSON files: JSON lines or a single document, uncompressed, gzip, or zstd (install the `zstd` extra). DetectTrace uses the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/). A case is an `invoke_agent` span, and tool calls are `execute_tool` spans. Extra attributes carry what the standard doesn't: `detecttrace.case_id`, `detecttrace.alert_class`, and `detecttrace.verdict`, plus an optional `detecttrace.prompt_version` for the version split. If your traces use other attribute names, map them in the configuration's `mapping` section. `init` proposes the mapping for you.

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

The OpenTelemetry SDK's console exporter doesn't write OTLP JSON. DetectTrace recognizes its output and says so.

**Verdicts.** A CSV with `case_id`, `alert_class`, and `verdict`. The label map converts your team's labels into three verdicts: `true_positive`, `false_positive`, and `benign`. Matching ignores case and extra spaces. Add `agent_label_map` if the agent uses different labels from the analysts. DetectTrace never guesses a label; unmapped labels are reported.

**Checklists.** One YAML file per alert class, written by you from your own playbook. For example, an excerpt from the demo:

```yaml
alert_class: impossible_travel
items:
  - id: signin_history
    tool: get_signin_logs
    args:
      range: { min_duration: 24h }
  - id: signin_query
    tool: query_sentinel
    args:
      query: { kql_min_ago: 24h }
  - id: mfa_check
    tool: check_mfa_status
  - id: location_history
    tool: get_user_locations
    args:
      lookback_days: { min: 30 }
```

An item is satisfied when the case has a successful call to that tool and every argument rule passes. Argument rules: `equals`, `in`, `exists`, `matches`, `min`, `max`, `min_duration`, and `kql_min_ago`. Without a checklist, an alert class gets verdict metrics only. Files ending in `.yaml.example` are ignored, and `check` notes each one.

## Privacy

Everything runs on your machine. DetectTrace makes no network calls and collects no usage statistics; the test suite fails if the package imports a network library or opens a connection. Tool results are never copied into the output. The dashboard does include every scored case's ID, alert class, prompt version and verdicts, plus the tool names and arguments of up to `dashboard.max_detail_cases` notable cases (2,000 by default), so treat it like the traces it came from before you share it.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

## License

MIT. See [LICENSE](LICENSE).
