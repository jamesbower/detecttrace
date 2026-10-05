# Running as a service

`detecttrace serve` runs DetectTrace as a long-running service for a team. Your OpenTelemetry Collector sends it spans over OTLP/HTTP, your case management tool sends it analyst verdicts, and anyone with a read token opens the dashboard in a browser.

It shows the same dashboard as `detecttrace check`, kept up to date as data arrives. It doesn't alert, page anyone, or explain why a number changed. It shows the numbers, as `check` does.

## Contents

- [Install](#install)
- [Tokens](#tokens)
- [Configuration](#configuration)
- [Sending spans from a Collector](#sending-spans-from-a-collector)
- [TLS](#tls)
- [Endpoints](#endpoints)
- [Status codes](#status-codes)
- [How the dashboard updates](#how-the-dashboard-updates)
- [Limits](#limits)
- [Durability and backups](#durability-and-backups)
- [Privacy](#privacy)
- [Operations](#operations)

## Install

### Container

There is no published image yet. Build it from the repository:

```sh
git clone https://github.com/jamesbower/detecttrace.git
cd detecttrace
docker build -t detecttrace .
```

The image runs `detecttrace serve --config /data/detecttrace-serve.yaml` by default, as a non-root user. `/data` is a volume: it holds the configuration and the database. The service listens on port 4320.

`compose.yaml` in the repository starts the service with an OpenTelemetry Collector in front of it:

- The service keeps its data in a named volume, restarts unless you stop it, and runs with a read-only root file system.
- Its port is published on `127.0.0.1:4320` only, so the dashboard is reachable from the host itself and nowhere else.
- The Collector receives your agent's spans and forwards them to `http://detecttrace:4320` over the Compose network, with the ingest token from the `DETECTTRACE_INGEST_TOKEN` environment variable.

```sh
docker compose up -d
```

### Python package

DetectTrace isn't on PyPI yet. Once it is, install the `serve` extra, which adds the web server:

```sh
pip install "detecttrace[serve]"
detecttrace serve --config detecttrace-serve.yaml
```

From a clone, `uv sync --extra serve` does the same.

`--config` defaults to `detecttrace-serve.yaml`. On start, the service prints where it listens, such as `Serving on https://detecttrace.example.com:4320`. It stops cleanly on `SIGTERM`, which is what `docker stop` sends.

It exits with code 1 and one line of explanation when it can't start:

- the configuration is missing or invalid,
- a TLS certificate or key file is missing or unreadable,
- the database was written by a newer DetectTrace,
- the database file is damaged or isn't a DetectTrace database,
- the `serve` extra isn't installed.

## Tokens

Every request except the health check needs an access token. A token has one of three roles:

| Role | What it may do |
|---|---|
| `ingest` | Send spans to `POST /v1/traces`. Give one to each Collector. |
| `verdicts` | Send analyst verdicts to `POST /api/verdicts`. Give one to each tool that sends verdicts. |
| `read` | Open the dashboard and read `/api/status` and `/api/results.json`. |

Create a token with `detecttrace token`:

```sh
detecttrace token --role ingest --name collector-eu
```

It prints the token once, on standard output, followed by the configuration entry for it:

```text
yWd0Wv2l6Q0ZRk0bXvJ4dX7m8u4C3Pq1sN9eHf2aTgM
- {name: "collector-eu", hash: "sha256:5cf60d6c8e5bb7e63e620e4da8070f93904018e82c9d63fa41d10147d17d7cf3"}
```

Copy the token into the tool that will use it, and the entry into the `tokens` section of the configuration, under its role. DetectTrace keeps only the SHA-256 hash, so a lost token can't be recovered: create a new one.

- **One token per tool.** Give every Collector, every verdict sender and every reader its own named token. Then you can revoke one without touching the others. The name may be 1 to 64 letters, digits, `_`, `.` or `-`. It is stored with each verdict the token sends.
- **Rotation.** Add an entry for the new token, restart the service, switch the tool to the new token, then remove the old entry and restart again.
- **Wrong role.** A token of another role gets 403. An unknown or missing token gets 401.

Tools send the token as `Authorization: Bearer <token>`. For the read routes, a browser can also sign in: it prompts for a user name and password, and the password is the read token. The user name is ignored.

## Configuration

The service reads one YAML file. It holds the `serve` and `tokens` sections, plus the same `mapping`, `label_map`, `agent_label_map`, `checklists` and `dashboard` settings as `detecttrace.yaml`. It has no `traces`, `verdicts` or `output`: spans and verdicts arrive over the network, and the dashboard is served, not written.

A complete example, for the container:

```yaml
serve:
  database: detecttrace.db     # relative to this file: /data/detecttrace.db in the container
  host: 0.0.0.0                # every interface inside the container
  port: 4320
  allow_plain_http: true       # TLS ends at a proxy, or the port stays on 127.0.0.1
  settle_seconds: 300
tokens:
  ingest:
    - {name: "collector-eu", hash: "sha256:5cf60d6c8e5bb7e63e620e4da8070f93904018e82c9d63fa41d10147d17d7cf3"}
  verdicts:
    - {name: "case-manager", hash: "sha256:c659e62a968ef077407869c4833e961f019827e9683b837e43306b93506e81ac"}
  read:
    - {name: "soc-team", hash: "sha256:b76bca561f1edb847b12b05019b61e9a3a2e80f1c5655d1480e81aa92beb3b6b"}
    - {name: "detection-eng", hash: "sha256:5e3dc8099b2e0061fac9d979863c08fc090ea88276d7cd2c6c4fa675df4d761e"}
label_map:                     # your labels -> true_positive, false_positive, benign
  TP: true_positive
  FP: false_positive
  Closed - Benign: benign
checklists: checklists/        # one YAML file per alert class
dashboard:
  max_detail_cases: 2000
```

### `serve`

| Key | Default | What it does |
|---|---|---|
| `database` | required | The SQLite database file. Relative paths are relative to the configuration file. It is created if missing, readable and writable by its owner only (mode `0600`). |
| `host` | `127.0.0.1` | The address to listen on. Anything other than a loopback address needs `tls` or `allow_plain_http`. |
| `port` | `4320` | The port to listen on. |
| `tls.certfile` | none | The TLS certificate chain, a PEM file. Relative to the configuration file. |
| `tls.keyfile` | none | The certificate's private key, a PEM file. Relative to the configuration file. |
| `allow_plain_http` | `false` | Listen without TLS on a non-loopback address. See [TLS](#tls). |
| `settle_seconds` | `300` | How long after a case's agent run ends before it counts. See [How the dashboard updates](#how-the-dashboard-updates). |

### `tokens`

`tokens` has three lists: `ingest`, `verdicts` and `read`. Each needs at least one entry, and each entry has a `name` and a `hash` as printed by `detecttrace token`. Within a list, names and hashes must be unique.

### Shared settings

`mapping`, `label_map`, `agent_label_map`, `checklists` and `dashboard.max_detail_cases` work as in `detecttrace.yaml`; see the [README](../README.md) and [Trace attributes](attributes.md). The verdict API rejects a row whose label isn't in `label_map`, so map every label your analysts use before you send verdicts.

Restart the service after you change the configuration.

## Sending spans from a Collector

Add an `otlphttp` exporter that points at the service. It must send JSON: set `encoding: json`. The service doesn't read OTLP protobuf. The Collector's default gzip compression is fine.

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    send_batch_max_size: 8192

exporters:
  otlphttp/detecttrace:
    endpoint: https://detecttrace.example.com:4320
    encoding: json
    headers:
      Authorization: "Bearer ${env:DETECTTRACE_INGEST_TOKEN}"

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/detecttrace]
```

- **The token comes from the environment.** `${env:DETECTTRACE_INGEST_TOKEN}` is the Collector's syntax for an environment variable, so the token stays out of the file.
- **Keep your existing backend.** Add `otlphttp/detecttrace` as a second exporter in the traces pipeline; your other exporters still get every span.
- **Batch size.** `send_batch_max_size` keeps each request well under the [size limit](#limits). A request over it gets 413.
- **Compression.** Leave `compression` unset (gzip) or set it to `none`. Other values, such as `zstd` or `snappy`, get 415.
- **Retries.** The `otlphttp` exporter retries a 503 on its own, after the `Retry-After` the service sends. Sending the same batch twice is safe: a span already stored is ignored.

The spans need the same attributes as for `check`; see [Trace attributes](attributes.md).

## TLS

Tokens and investigation data travel in every request, so the service refuses to listen without TLS on anything but a loopback address (`127.0.0.1`, `::1` or `localhost`). There are two ways to listen beyond the host:

- **TLS in the service.** Set `serve.tls.certfile` and `serve.tls.keyfile`. The service then speaks HTTPS only.

  ```yaml
  serve:
    database: detecttrace.db
    host: 0.0.0.0
    tls:
      certfile: tls/detecttrace.example.com.crt
      keyfile: tls/detecttrace.example.com.key
  ```

- **TLS at a reverse proxy.** Put a proxy such as nginx or Caddy in front, end TLS there, and set `serve.allow_plain_http: true`. Only do this when the plain HTTP hop can't be read by others: the proxy on the same host, or a private container network. The container example above does this: inside the container the service must listen on `0.0.0.0`, and Compose publishes the port on `127.0.0.1` only.

## Endpoints

The examples use `https://detecttrace.example.com:4320` and read tokens from environment variables.

### `GET /healthz`

No token. Answers `200` with `ok` when the database can take writes, and `503` with `unavailable` when it can't. Use it for container and load balancer health checks.

```sh
curl https://detecttrace.example.com:4320/healthz
```

### `POST /v1/traces`

Role `ingest`. One OTLP JSON trace export request, as the Collector's `otlphttp` exporter sends it. `Content-Type` must be `application/json`; `Content-Encoding` may be `gzip`.

```sh
curl https://detecttrace.example.com:4320/v1/traces \
  -H "Authorization: Bearer $DETECTTRACE_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @export.json
```

When every span is stored, the answer is `200` with an empty object:

```json
{}
```

When some spans aren't valid OTLP, the valid ones are stored and the rest are dropped. The answer is still `200`, with a partial success that OTLP clients log as a warning. The dropped spans also appear in the dashboard's data notes.

```json
{
  "partialSuccess": {
    "rejectedSpans": 2,
    "errorMessage": "2 spans were not valid OTLP and were dropped; the dashboard's data notes list them"
  }
}
```

A span already stored is ignored, so a retried batch stores nothing twice. A span with the same trace and span ID as a stored one but different content is not stored; the first copy wins, and the conflict is listed in the data notes.

### `POST /api/verdicts`

Role `verdicts`. Analyst verdicts, as JSON or CSV, up to 10,000 rows per request. Each row has a `case_id`, an `alert_class` and a `verdict` label, the same columns as a verdict CSV file for `check`.

As JSON, with `Content-Type: application/json`:

```json
{
  "verdicts": [
    {"case_id": "INC-1042", "alert_class": "impossible_travel", "verdict": "TP"},
    {"case_id": "INC-1043", "alert_class": "oauth_consent", "verdict": "Closed - Benign"}
  ]
}
```

```sh
curl https://detecttrace.example.com:4320/api/verdicts \
  -H "Authorization: Bearer $DETECTTRACE_VERDICTS_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @verdicts.json
```

As CSV, with `Content-Type: text/csv`. A header row comes first; a byte order mark, as Excel writes, is fine.

```csv
case_id,alert_class,verdict
INC-1042,impossible_travel,TP
INC-1043,oauth_consent,Closed - Benign
```

```sh
curl https://detecttrace.example.com:4320/api/verdicts \
  -H "Authorization: Bearer $DETECTTRACE_VERDICTS_TOKEN" \
  -H "Content-Type: text/csv" \
  --data-binary @verdicts.csv
```

The answer counts the stored rows and lists the rejected ones:

```json
{"accepted": 2, "rejected": []}
```

The body must be UTF-8. A newer verdict for a case replaces the current one; the replaced verdict is kept in the database's history, with the name of the token that sent it. Sending the same verdict again changes nothing, so retries are safe.

**Partial success.** Rows are checked one by one. Good rows are stored even when others are rejected, and the answer is `200`. A row is rejected when a column is missing or empty, a value holds a line break, its label isn't in `label_map`, or its `case_id` already appeared earlier in the same request.

```sh
curl https://detecttrace.example.com:4320/api/verdicts \
  -H "Authorization: Bearer $DETECTTRACE_VERDICTS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"verdicts": [{"case_id": "INC-1044", "alert_class": "impossible_travel", "verdict": "FP"},
                    {"case_id": "INC-1045", "alert_class": "impossible_travel", "verdict": "Escalated"}]}'
```

```json
{
  "accepted": 1,
  "rejected": [
    {"where": "verdicts[1]", "reason": "verdict label 'Escalated' is not in label_map"}
  ]
}
```

A rejected JSON row is named by its position, such as `verdicts[1]`; a rejected CSV row by its line, such as `line 3`.

**Nothing accepted.** When no row is stored, the answer is `422` with the same body, so you can see every reason:

```json
{
  "accepted": 0,
  "rejected": [
    {"where": "verdicts[0]", "reason": "verdict is required"}
  ]
}
```

### `GET /`

Role `read`. The dashboard, the same page `check` writes. Until at least one case can be scored, it is a short "waiting for data" page that counts the spans, cases and verdicts received so far and lists the data notes.

Open it in a browser and enter the read token as the password, or fetch it:

```sh
curl https://detecttrace.example.com:4320/ \
  -H "Authorization: Bearer $DETECTTRACE_READ_TOKEN" \
  -o dashboard.html
```

### `GET /api/status`

Role `read`. The state of the service, as JSON:

```json
{
  "generation": 42,
  "updated_at": "2026-10-05T09:30:12.418203Z",
  "recompute_running": false,
  "last_error": null,
  "last_error_at": null,
  "held_back_cases": 3,
  "span_count": 18342,
  "verdict_count": 251,
  "last_ingest_at": "2026-10-05T09:30:05.006115Z"
}
```

| Field | Meaning |
|---|---|
| `generation` | Goes up by one with every write that changed the stored data. |
| `updated_at` | When the dashboard now served was computed. |
| `recompute_running` | Whether a new dashboard is being computed. |
| `last_error`, `last_error_at` | Why and when the last recompute failed, or `null`. The last good dashboard stays in place. |
| `held_back_cases` | Cases still inside the settle window, not counted yet. |
| `span_count`, `verdict_count` | Spans and verdicts stored. |
| `last_ingest_at` | When stored data last changed. |

```sh
curl https://detecttrace.example.com:4320/api/status \
  -H "Authorization: Bearer $DETECTTRACE_READ_TOKEN"
```

### `GET /api/results.json`

Role `read`. The same results JSON that `check --json` writes, plus a `served` block:

```json
{"served": {"generation": 42, "settle_seconds": 300, "held_back_cases": 3}}
```

Like `check --json`, the format may change before 1.0. Before any case can be scored, it is a short waiting document instead:

```json
{
  "status": "waiting",
  "generation": 7,
  "span_count": 412,
  "case_count": 0,
  "held_back_count": 6,
  "verdict_count": 6,
  "data_notes": []
}
```

### Errors

Every error answer has a small JSON body with a `code` and a `message`, the shape OTLP clients expect:

```json
{"code": 16, "message": "a valid access token is required"}
```

No answer ever repeats the token a client sent.

## Status codes

| Status | When | Retry? |
|---|---|---|
| `400` | The body can't be read at all: not JSON, not an OTLP trace export, broken gzip, not UTF-8, a verdict body without a `verdicts` list, or a CSV without the required columns. A bad trace body is also listed in the data notes. | No. Fix the sender. |
| `401` | The token is missing or unknown. | No. Check the token. |
| `403` | The token is valid but has another role. | No. Use a token of the right role. |
| `413` | The body is over the size limit, or a verdict request has more than 10,000 rows. | No. Send smaller requests; for a Collector, lower `send_batch_max_size`. |
| `415` | The `Content-Type` or `Content-Encoding` isn't accepted, such as OTLP protobuf or zstd. | No. Send JSON (traces) or JSON or CSV (verdicts), plain or gzip. |
| `422` | A verdict request in which no row was accepted. The body lists each reason. | No. Fix the rows. |
| `503` | The database can't take writes right now, or the body didn't arrive within 30 seconds. Comes with `Retry-After: 5`. | Yes, after the `Retry-After` delay. |

## How the dashboard updates

The service recomputes the dashboard in the background: 5 seconds after the last write, and at least once every 60 seconds while data keeps arriving. No request waits for it; the page always shows the last finished dashboard.

**Settling.** A case counts once its agent run's root span ended `settle_seconds` ago (300 by default), because its tool calls and verdict may still be on their way. Its verdict waits with it. Until then it is held back, and the page and `/api/status` say how many cases are held back. Set `settle_seconds` to `0` to count every case at once.

Settling compares the server's clock with the end time the agent reported. A case that ends more than `settle_seconds` in the server's future gets a data note: check that the clock of the host that runs your agent is synchronized, for example with NTP.

**The page never reloads by itself.** When newer results exist, it shows a "New data is available" bar. Reload the page to see them.

**When a recompute fails**, the last good dashboard stays in place, and `/api/status` shows the error. The service tries again on the next write, waiting at least 5 seconds after the failure, doubling with each failure in a row up to 5 minutes. The full error goes to the log.

## Limits

| Limit | Value |
|---|---|
| Trace request body, as received | 16 MiB |
| Trace request body, after gzip decompression | 32 MiB |
| Verdict request body | 4 MiB |
| Verdict rows per request | 10,000 |
| Trace requests handled at once | 4; more wait their turn |
| Verdict requests handled at once | 2; more wait their turn |
| Time to receive one request body | 30 seconds, then 503 |

- **Scale.** The service is designed and benchmarked for about 50,000 cases, the same as `check`.
- **One instance per database.** Run one service per database file. Two services on one file aren't supported.
- **Local storage.** Keep the database on a local disk or a local container volume, not on a network file system such as NFS or SMB. SQLite's locking and durability depend on the local file system.

## Durability and backups

Every write is committed to disk before the service answers. An acknowledged batch of spans or verdicts survives a restart, a crash and a power loss. A client that didn't get an answer can retry: spans already stored are ignored, and a verdict equal to the current one changes nothing.

Nothing is ever deleted automatically. The database grows with every span. To start over, stop the service and move the database file aside.

**Backups.** Copying the file while the service runs can produce a broken copy. Use SQLite's backup instead, which is safe while the service runs:

```sh
sqlite3 detecttrace.db ".backup backup.db"
```

Or make a compacted copy:

```sh
sqlite3 detecttrace.db "VACUUM INTO 'backup.db'"
```

The container image has no `sqlite3` program. There, use Python's backup from inside the container:

```sh
docker exec detecttrace python -c "import sqlite3; sqlite3.connect('/data/detecttrace.db').backup(sqlite3.connect('/data/backup.db'))"
```

To restore, stop the service, put the backup in place of the database file, and start the service again. A backup holds the same investigation data as the database; store it as carefully.

## Privacy

DetectTrace itself never sends data anywhere: no telemetry, no update checks. The service only answers requests.

- **Tool results are dropped on arrival.** The `gen_ai.tool.call.result` attribute is removed from each span before it is stored, and it never reaches the database or the dashboard.
- **Everything else is stored.** The database holds every other span attribute your agent sends, including tool arguments and any prompt or message content your instrumentation records, plus every verdict. Treat it like the traces it came from. It is created readable and writable by its owner only (mode `0600`).
- **The dashboard** shows what the file from `check` shows: every scored case's ID, alert class, prompt version and verdicts, and the tool names and arguments of notable cases. Anyone with a read token can see it.
- **Tokens** are stored only as hashes and never logged. The access log records the method, path, status, client address and duration of each request: never headers, bodies or query strings.

The page is served with `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and a content security policy with `frame-ancestors 'none'`, so other sites can't frame it.

## Operations

- **Slow clients.** Each request must deliver its body within 30 seconds, but the web server has no other read timeout. On a network you don't trust, put a reverse proxy in front to protect the service from slow or idle connections.
- **Memory.** A trace request near the size limit can use a few hundred MiB while it is parsed. At most 4 are parsed at once, which bounds the peak. Size the container's memory for that, plus the background recompute.
- **Logs.** A failed recompute logs its full error, with the traceback; `/api/status` shows only the error type and message.
