# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The first release.

### Added

- `detecttrace demo` runs on bundled synthetic data and writes an example dashboard.
- `detecttrace check` reads your traces and analyst verdicts and writes one offline HTML dashboard, and optionally the results as JSON.
- `detecttrace init` looks at your data and proposes a configuration file and an example checklist.
- Trace inputs: OTLP JSON and JSON Lines files, plain or compressed with gzip or zstd (zstd needs the `zstd` extra), a single file or a folder of rotated files, and Langfuse v4 observation exports.
- Verdict inputs: a CSV file of analyst verdicts, with label mapping to true positive, false positive and benign.
- `FileSpanExporter` (the `otel` extra) writes your agent's OpenTelemetry spans straight to OTLP JSON Lines files, without a Collector.
- Dashboard sections: results by alert class and version, checklist steps the agent skipped, a weekly trend, agent verdicts against analyst verdicts, cases, data notes, and what the dashboard does not tell you. Every value shows its number of cases and a 95% confidence interval.
- No network calls: everything runs locally, and the dashboard is a single self-contained HTML file.
