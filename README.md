# DetectTrace

![DetectTrace's Overview page on synthetic demo data: case count, period, coverage, dangerous false closes, and results by prompt version with 95% intervals.](docs/images/overview.png)

Agent assurance for AI SOC agents. DetectTrace reads your agent's OpenTelemetry or Langfuse traces and your analysts' verdicts, then shows how the agent compares with the analysts, per alert class and per prompt version, and which investigation steps the agent skips.

> **Status: in development.** DetectTrace isn't on PyPI yet, so run it from a clone. The first PyPI release is being prepared.

## The problem

AI SOC agents now triage and close real alerts. Teams that build their own agents usually run one in shadow mode on one alert class, then compare the agent's verdict with the analyst's. That agreement rate is often the only number they track.

One agreement number hides three problems:

1. **Base rates.** Most identity alerts are benign. An agent that always says "benign" can agree 85% of the time and add nothing.
2. **Wrong reasons.** An agent can reach the right verdict while it skips steps your incident response playbook requires. The verdict matches, but the investigation didn't happen.
3. **Change.** After a prompt or model change, one alert class can get worse while the overall average stays flat.

The most costly mistake is also the one agreement hides best: the agent closes a real attack as benign.

## What DetectTrace gives you

A command-line tool that turns traces and verdicts into a view that goes beyond one agreement number. Run it on files, as a local app in your browser, or as a service that your Collector feeds.

- **Verdict agreement with chance corrected.** Agreement rate and Cohen's kappa, the chance-corrected agreement, per alert class, with a 3 × 3 confusion matrix. Kappa is 0 for no better than chance and 1 for perfect agreement, so it shows when high agreement comes only from the base rate.
- **Dangerous false closes.** Cases where the analyst said true positive and the agent said benign or false positive, as a count with the case IDs.
- **Evidence completeness.** You write a short checklist of the tool calls your playbook requires for an alert class. DetectTrace checks each case's successful tool calls against it and reports the share of items satisfied, per case and per class. A step is reported as failed (the call errored), not called, or called with the wrong arguments.
- **Skipped steps by version.** How often each checklist step was skipped, per prompt or model version. For example: "MFA check skipped: 5% in v1, 45% in v2."
- **Weekly trend.** Per alert class, split by version, with the number of cases each week.
- **Honest uncertainty.** Every value carries its number of cases (n). Agreement, chance-corrected agreement and evidence completeness also carry a 95% confidence interval. Small samples are marked as such. The definitions are in [docs/metrics.md](docs/metrics.md).
- **Case detail.** Each case with the agent verdict, the analyst verdict and checklist coverage, and, for notable cases (dangerous closes, disagreements, failed calls, missed steps), its tool calls.
- **Data notes.** Bad input is reported, not hidden: orphan traces and verdicts, duplicates, unmapped labels, and tool arguments that couldn't be read. They are on the dashboard's Data page.

It shows the numbers. It doesn't explain why a number changed, check whether the agent's conclusions are supported by its tool results, or fail builds.

## Who it's for

Engineers who build their own AI SOC agent and have:

- Agent traces in OpenTelemetry, emitted directly or through a tool such as Langfuse.
- Analyst verdicts for the same cases, usually from a shadow-mode period.
- An incident response playbook for the alert class.

If you use a closed third-party agent and can't get its traces, this tool can't help you.

## Try the demo

Requires Python 3.11 or later and [uv](https://docs.astral.sh/uv/). Run it from a clone:

```sh
git clone https://github.com/jamesbower/detecttrace.git
cd detecttrace
uv sync
uv run detecttrace demo
```

The demo runs on about 200 synthetic cases in two identity alert classes over six weeks. Prompt v1 runs in weeks 1 and 2, and v2 from week 3. On impossible travel, v2 often skips the MFA check and narrows the sign-in lookback, so evidence completeness drops and a few threats are closed as benign. OAuth consent doesn't change, so it serves as the control. The results show the drop, not the cause.

It prints a short summary and writes `detecttrace-demo.html`, a self-contained dashboard that opens in any browser without a network connection. Running the demo again replaces its own earlier page.

The dashboard has nine pages: Overview, Versions, Skipped steps, Weekly trend, Verdict matrix, Cases, Data, Limits and Help. What each one shows is in [docs/dashboard.md](docs/dashboard.md).

![The Weekly trend page for impossible_travel: evidence completeness per week, v1 against v2, with a dashed rule where each version starts.](docs/images/trends.png)

![The Cases page with one dangerous false close open: its tool calls and the two checklist steps it missed.](docs/images/cases.png)

## Ways to run it

| Command | What it does | Docs |
|---|---|---|
| `demo` | Runs every check on synthetic data and writes one dashboard file. | [docs/cli.md](docs/cli.md#demo) |
| `ui` | Runs the checks as a local app in your browser: upload files, confirm the configuration, see the dashboard. | [docs/ui.md](docs/ui.md) |
| `check` | Reads a configuration and your files, and writes one self-contained HTML dashboard. `init` proposes the configuration. | [docs/cli.md](docs/cli.md) |
| `serve` | Runs the dashboard as a service for a team, fed by an OpenTelemetry Collector and a verdict API. | [docs/serve.md](docs/serve.md) |

## Run it on your data

### In your browser

`detecttrace ui` runs DetectTrace as a small app on your computer, with no configuration file to write. From a clone:

```sh
uv sync --extra serve
uv run detecttrace ui
```

It opens `http://127.0.0.1:4321/` in your browser. [Upload](docs/images/data-upload.png) your traces, verdict CSV and checklists, [confirm](docs/images/data-configuration.png) the configuration DetectTrace proposes, and the dashboard appears when the results are ready. The options, accepted files, the data folder and the security model are in [docs/ui.md](docs/ui.md).

### On files

```sh
uv run detecttrace init --traces traces/ --verdicts verdicts.csv
uv run detecttrace check --config detecttrace.yaml
```

`init` reads your traces and verdicts and proposes a configuration, `detecttrace.yaml`, plus one example checklist. The example has no effect until you edit it down to the calls your playbook requires and rename it to `.yaml`. A checklist copied from what the agent happened to do would show a completeness of about 100% for a playbook nobody wrote.

`check` reads the configuration and writes the dashboard. The options, exit codes and a hand-written configuration are in [docs/cli.md](docs/cli.md). The inputs are in brief below, and in full in [docs/cli.md](docs/cli.md#inputs).

### Inputs

**Traces.** OTLP JSON files, written by an OpenTelemetry Collector's `file` exporter or by `FileSpanExporter` in your agent, or a Langfuse export. See [docs/trace-sources.md](docs/trace-sources.md) for each source and [docs/attributes.md](docs/attributes.md) for the attributes your spans need.

**Verdicts.** A CSV with `case_id`, `alert_class` and `verdict`, and a label map that converts your team's labels into `true_positive`, `false_positive` and `benign`.

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

An item is satisfied when the case has a successful call to that tool and every argument rule passes. Every rule, with examples, is in [docs/checklists.md](docs/checklists.md).

### Keep the analysts blind

Every metric treats the analyst verdict as the truth, so how analysts reach it matters. If they can see the agent's verdict before they decide, they tend to agree with it, and agreement, chance-corrected agreement and dangerous false closes all look better than they are. During shadow mode, hide the agent's verdict until the analyst has decided. DetectTrace can't detect this bias in the data, so it can't correct for it afterwards.

## Run as a service

`detecttrace serve` keeps the dashboard up to date for a team. Your OpenTelemetry Collector sends it spans over OTLP/HTTP, a verdict API takes analyst verdicts as JSON or CSV, and anyone with a read token opens the dashboard in a browser. Setup, tokens, configuration, every endpoint and the limits are in [docs/serve.md](docs/serve.md).

## Privacy

DetectTrace itself never sends data anywhere: no telemetry, no usage statistics, no update checks.

- **Files.** `demo`, `check` and `init` read files and write one self-contained HTML page that opens without a network. The page makes no network requests, and its Content-Security-Policy lets the browser load nothing from outside the file.
- **`ui`** listens on `127.0.0.1` only and keeps what you upload in its data folder (`~/.detecttrace` by default): the spans, the verdicts, the configuration, the checklists and the latest results, in a SQLite database readable by you only. See [docs/ui.md](docs/ui.md#security).
- **`serve`** listens on the network, stores spans and verdicts in a SQLite database, and shows the dashboard only to people with a read token. See [docs/serve.md](docs/serve.md#privacy).

Tool results are never copied into the output, and `ui` and `serve` drop them before anything is stored. The dashboard does include every scored case's ID, alert class, prompt version and verdicts, plus the tool names and arguments of up to `dashboard.max_detail_cases` notable cases (2,000 by default), so treat it like the traces it came from before you share it.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

The dashboard's source is a React app in `dashboard/`. You need Node only to change it; see [dashboard/README.md](dashboard/README.md).

## License

MIT. See [LICENSE](LICENSE).
