# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The first release.

### Added

- `detecttrace demo` runs on bundled synthetic data and writes an example dashboard.
- `detecttrace check` reads your traces and analyst verdicts and writes one self-contained HTML dashboard, and optionally the results as JSON.
- `detecttrace init` looks at your data and proposes a configuration file and an example checklist.
- Trace inputs: OTLP JSON and JSON Lines files, plain or compressed with gzip or zstd (zstd needs the `zstd` extra), a single file or a folder of rotated files, and Langfuse v4 observation exports.
- Verdict inputs: a CSV file of analyst verdicts, with label mapping to true positive, false positive and benign.
- `FileSpanExporter` (the `otel` extra) writes your agent's OpenTelemetry spans straight to OTLP JSON Lines files, without a Collector.
- Dashboard pages: an overview, results by version, checklist steps the agent skipped, a weekly trend, agent verdicts against analyst verdicts, cases, data (the coverage lines and the data notes), and what the dashboard does not tell you. Every value shows its number of cases and a 95% confidence interval.
- A Result filter on the Cases page: all cases, disagreements, or dangerous false closes. Result is the table's second column, after Case, on every screen size. The page, the selected alert class and the case filters are kept in the page's address.
- The Overview's dangerous false closes list each class's count out of the analyst's true positives with an agent verdict, as in "6 of 39 true positives".
- Weekly trend points have 95% intervals, built as for versions. They are shown as text in each point's label, the keyboard readout and the table. In `check --json`, each `trend[]` entry has `completeness_interval` and `agreement_interval`.
- Term explanations on the Overview, Versions and Weekly trend pages; a Help page.
- Keyboard navigation of the weekly trend charts: the arrow keys, Home and End move between points.
- Support for forced colors, such as Windows high contrast: trend lines, chart markers and interval strips stay visible.
- `detecttrace serve` runs the dashboard as a long-running service for a team. It receives spans over OTLP/HTTP as JSON (plain or gzip) from an OpenTelemetry Collector's `otlp_http` exporter, stores them in SQLite, recomputes the dashboard in the background, and serves it to holders of a read token, along with `/api/status`, `/api/results.json` and an unauthenticated `/healthz`. Tool results are dropped on arrival.
- A verdict API, `POST /api/verdicts`, takes analyst verdicts as JSON or CSV, up to 10,000 rows per request, with each rejected row and its reason in the answer.
- `detecttrace token` creates an access token with the role `ingest`, `verdicts` or `read`, and prints the configuration entry that holds its hash.
- `detecttrace ui` runs DetectTrace as a local app in the browser, on `127.0.0.1:4321` by default. On the Data page you upload traces (OTLP or Langfuse, up to 256 MiB per file), verdict CSVs (up to 64 MiB) and checklist YAML files, confirm the configuration it proposes from them, and then see the dashboard, which updates as you upload more. It keeps the data in a data folder, `~/.detecttrace` by default (`--data-dir`), listens on loopback only, has no tokens, refuses writes from other websites and requests for other hosts, and drops tool results before anything is stored. Options: `--data-dir`, `--port` and `--no-open`. See `docs/ui.md`.
- A container image, built from the repository with `docker build`, and a `compose.yaml` that runs the service behind an OpenTelemetry Collector. The Collector's receiver requires its own bearer token, so only your agents can send it spans.

### Changed

- The dashboard is a new multi-page React app, with a sidebar of pages and a footer. It shows the same numbers, and it is still one self-contained HTML file that opens without a network. Node is needed only to change the dashboard, not to run DetectTrace.
- The "Data notes" page is now the "Data" page, with a database icon in the sidebar. Its address is `#/data` (`/data` in `ui` and `serve`); links and bookmarks to the old `data-notes` address open it.
- The "Self-reported" banner and the matching summary line from the command line are removed.
- In `detecttrace ui` and `detecttrace serve`, each dashboard page has its own path, such as `/versions?class=class-1`, in place of an address after the `#`. Back and Forward move between pages, and a reload keeps the page and its filters. Old addresses such as `/#/versions` open the matching path. The file from `check` and `demo` keeps its `#/…` addresses.
- The Overview no longer lists the input paths (traces, verdicts, checklists, config). The results JSON still names them in its `source` block.
- `jinja2` is no longer a dependency.
- Network policy: `demo`, `check` and `init` read files and write one self-contained HTML page that opens without a network. `ui` listens on `127.0.0.1` only and keeps what you upload in its data folder. `serve` listens on the network, stores data in SQLite, and shows the dashboard to people with a read token. DetectTrace itself never sends data anywhere: no telemetry, no update checks.
