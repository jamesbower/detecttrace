# Trace sources

DetectTrace reads trace files from disk. This page covers three ways to get them: an OpenTelemetry Collector, `FileSpanExporter` in your agent, and a Langfuse export. For the attributes your spans need, see [Trace attributes](attributes.md).

| Source | `traces.format` | Point `traces.path` at |
|---|---|---|
| Collector `file` exporter | `otlp_jsonl` (the default) | The exporter's folder |
| `FileSpanExporter` | `otlp_jsonl` | Its folder |
| One OTLP JSON document per file | `otlp_json` | The file or its folder |
| Langfuse | `langfuse` | The saved API pages, or the export's `observations_v2/` folder |

`traces.path` can be one file or a folder. A folder is read with its subfolders, in POSIX path order, skipping hidden files and folders. Files may be plain, gzip or zstd (zstd needs the `zstd` extra). A span that appears in more than one file is read once, and the repeat is reported.

## OpenTelemetry Collector

Add the `file` exporter from the Collector's contrib distribution as a second exporter in your traces pipeline. Your existing backend still gets every span.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
      http:

exporters:
  otlphttp:
    endpoint: https://otel.example.com
  file:
    path: /var/lib/otelcol/detecttrace/traces.jsonl
    rotation:
      max_megabytes: 100
      max_backups: 50

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlphttp, file]
```

- **Keep the default JSON format.** It writes one OTLP JSON export request per line, which is what DetectTrace reads. Don't set `format: proto` or `compression`: both produce files DetectTrace can't read. To save space, compress finished files afterwards with gzip or zstd; DetectTrace reads those.
- **Rotation.** With `rotation`, the exporter renames full files with a timestamp, such as `traces-2026-09-03T09-30-00.000.jsonl`, and starts a new one. Point `traces.path` at the folder to read them all.
- **Files in use.** The exporter may be writing the newest file while DetectTrace reads it. A cut-off last line is skipped and reported as normal for a file still being written.

## `FileSpanExporter`

Without a Collector, add `FileSpanExporter` to your agent. It writes the same OTLP JSON lines as the Collector's `file` exporter. It needs the OpenTelemetry SDK, which the `otel` extra installs (`pip install "detecttrace[otel]"`).

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from detecttrace.otel import FileSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(FileSpanExporter("traces/{date}-{pid}.jsonl")))
trace.set_tracer_provider(provider)
```

- **Path placeholders.** `{date}` is the day in UTC, as `2026-09-30`, for one file per day. `{pid}` is the process ID, for one file per worker process. Write `{{` and `}}` for literal braces. Any other placeholder is an error when the exporter is created. The path is worked out again at every export, so a new day, or a forked child process, moves to a new file.
- **The folder must exist.** The exporter creates files, not folders.
- **One line per export.** Each batch is appended as one line, unbuffered. Processes that share one file on a local disk append whole lines, but `{pid}` avoids sharing.
- **File safety on Linux and macOS.** A new file is created readable and writable by its owner only (mode `0600`). A symbolic link, or anything else that is not a regular file at the path, is refused. **On Windows these protections don't apply:** the exporter follows a symbolic link at its path, and it can't create the file owner-only. Put the trace folder where only the agent's user can write.
- **It never raises into your agent.** When a batch can't be written (a missing folder, a full disk, an export after shutdown), the export returns failure and logs a warning on the `detecttrace.otel` logger. Warnings come at most once a minute and count the failed exports since the last one. After failures, the next successful export (at most once a minute) or shutdown logs how many were missed.

## Langfuse

Set `traces.format: langfuse`. DetectTrace reads observation rows from Langfuse v4 exports. How the rows map to spans is in [Trace attributes](attributes.md#langfuse).

### Observations API

Save the pages of the v2 observations API (`GET /api/public/v2/observations`), one file per page, in one folder. Follow `meta.cursor` until a page has none.

**Request the `io` field group.** It holds each tool call's input, which DetectTrace reads as the tool arguments. Without it, the export has no tool arguments, and long metadata values may be cut short. DetectTrace warns when no row in a file has `input` or `output`. `expandMetadata` alone keeps metadata whole, but it still leaves out tool arguments.

### Blob storage export

A scheduled export of observations writes JSON or JSONL files under `observations_v2/`. Point `traces.path` at that folder, not at the export's root: the `manifests/` and `scores/` folders beside it are not observations.

Exports are cut into time windows. When windows overlap, or you export the same period twice, the same row appears in more than one file. DetectTrace keeps the first copy in file path order and reports the others, with identical copies and differing copies under separate notes.

### Missing tool calls

The Langfuse SDK exports only some spans by default. Its default filter can drop your tool spans before they reach Langfuse, which would make evidence completeness look low with no other sign. Set `should_export_span=lambda span: True` in the SDK's configuration to export every span. DetectTrace warns when a Langfuse export has agent runs but not one tool call.

### Managed prompts

When your agent uses prompts managed in Langfuse, the prompt version is on the generation rows, not on the agent run. [Trace attributes](attributes.md#langfuse-managed-prompts) shows the mapping that reads it.

### Not supported

Trace objects from Langfuse's older traces API (a trace holding an `observations` list) are not read. Each one is reported. Export observations instead.

## Console exporter

The OpenTelemetry SDK's `ConsoleSpanExporter` prints spans in its own format, not OTLP JSON. DetectTrace recognizes a file of its output and reports it once, with a pointer to the Collector `file` exporter and `FileSpanExporter`.
