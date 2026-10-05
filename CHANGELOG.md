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
- Dashboard sections: results by alert class and version, checklist steps the agent skipped, a weekly trend, agent verdicts against analyst verdicts, cases, data notes, and what the dashboard does not tell you. Every value shows its number of cases and a 95% confidence interval.
- `detecttrace serve` runs the dashboard as a long-running service for a team. It receives spans over OTLP/HTTP as JSON (plain or gzip) from an OpenTelemetry Collector's `otlphttp` exporter, stores them in SQLite, recomputes the dashboard in the background, and serves it to holders of a read token, along with `/api/status`, `/api/results.json` and an unauthenticated `/healthz`. Tool results are dropped on arrival.
- A verdict API, `POST /api/verdicts`, takes analyst verdicts as JSON or CSV, up to 10,000 rows per request, with each rejected row and its reason in the answer.
- `detecttrace token` creates an access token with the role `ingest`, `verdicts` or `read`, and prints the configuration entry that holds its hash.
- A container image, built from the repository with `docker build`, and a `compose.yaml` that runs the service with an OpenTelemetry Collector.

### Changed

- Network policy: `demo`, `check` and `init` read files and write one self-contained HTML page that opens without a network. `serve` listens on the network, stores data in SQLite, and shows the dashboard to people with a read token. DetectTrace itself never sends data anywhere: no telemetry, no update checks.
