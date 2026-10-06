# Running locally in your browser

`detecttrace ui` runs DetectTrace as a small app on your own computer. You upload your traces, verdicts and checklists in the browser, confirm the configuration DetectTrace proposes, and see the same dashboard `detecttrace check` writes. You need no configuration file and no Collector.

It is for one person at one computer. It listens on `127.0.0.1` only, and it sends nothing anywhere.

## Contents

- [Install](#install)
- [Starting and stopping](#starting-and-stopping)
- [How it works](#how-it-works)
- [Uploads](#uploads)
- [The configuration](#the-configuration)
- [The data folder](#the-data-folder)
- [Clearing data](#clearing-data)
- [Security](#security)
- [Limits](#limits)
- [How it differs from `check`](#how-it-differs-from-check)
- [How it differs from `serve`](#how-it-differs-from-serve)

## Install

DetectTrace isn't on PyPI yet. Once it is, install the `serve` extra, which adds the web server:

```sh
pip install "detecttrace[serve]"
detecttrace ui
```

From a clone, run `uv sync --extra serve`, then `uv run detecttrace ui`. To upload zstd-compressed traces, also install the `zstd` extra.

## Starting and stopping

```sh
detecttrace ui
```

Once it listens, it prints `DetectTrace is running at http://127.0.0.1:4321/ (press Ctrl+C to stop).` and opens that address in your browser. Until a case can be scored, it opens on the Data page, unless the address names another page.

| Option | What it does |
|---|---|
| `--data-dir PATH` | The folder that keeps the uploaded data and the configuration. Default: `~/.detecttrace`. It is created if it doesn't exist. |
| `--port PORT` | The port to listen on, on `127.0.0.1`. Default: `4321`. |
| `--no-open` | Don't open the browser. Open the printed address yourself. |

Each data folder is a separate set of data. To keep two projects apart, give each its own `--data-dir`. Run one app per data folder.

It exits with code 1 and a short explanation when it can't start:

- the port is in use (`Port 4321 is in use. Pass --port with a free port.`), or can't be opened,
- the data folder can't be created,
- the saved configuration is invalid, or names checklists that can't be used. Fix `detecttrace.yaml` in the data folder, or delete it and confirm the configuration again in the app,
- the database was written by a newer DetectTrace, is damaged, or can't be read,
- the `serve` extra isn't installed.

Any other error stops it with exit code 2. Please report it.

Ctrl+C stops it, and so does `SIGTERM`. Requests in flight get up to 5 seconds to finish. Everything you uploaded stays in the data folder, so the next start shows the same dashboard.

## How it works

The Data page has three steps.

1. **Upload.** Three cards, one each for traces, verdicts and checklists. Choose files or drop them on a card. Each card uploads its files one at a time and shows, for each file, what was stored and any problems found in it. Below the cards are the totals stored so far: spans, the trace format, verdicts and the alert classes that have a checklist.
2. **Configuration.** Once traces and verdicts are both stored, DetectTrace proposes a configuration from them, as `detecttrace init` does. Correct what is wrong, map any verdict labels it can't map by itself, and choose **Confirm**.
3. **Data notes.** Once the first results exist, the coverage lines and every input problem, with how to fix it, as on the Data page of the file from `check`.

After you confirm, the app computes the dashboard in the background. Every 30 seconds the page asks the app whether newer results exist, and reloads itself when they do. It waits while you have unsaved changes in the configuration form. Until a case can be scored, the other pages say so and link to the Data page.

New uploads after that update the dashboard by themselves, checklists included. You don't need to confirm again.

## Uploads

| Card | Files | Largest file |
|---|---|---|
| Traces | `.jsonl`, `.json`, `.jsonl.gz`, `.json.gz`, `.jsonl.zst`, `.json.zst` | 256 MiB |
| Verdicts | `.csv` | 64 MiB |
| Checklists | `.yaml`, `.yml` | 1 MiB |

The suffix is matched in any case. A larger file is refused; split it and upload the parts.

- **Traces** are OTLP JSON or JSON Lines, or a Langfuse export of observations, as for `check`. The format is detected from the content. See [Inputs](../README.md#inputs) and [trace sources](trace-sources.md).
- **Verdicts** are a CSV with `case_id`, `alert_class` and `verdict`, as for `check`. Labels are stored as written and mapped in the configuration step.
- **Checklists** are YAML files, one alert class per file, in the format of [docs/checklists.md](checklists.md). A file that isn't a valid checklist is refused with the reason. A data folder holds up to 100 checklists. You write checklists yourself, from your own playbook, as for `check`. Turning playbooks or runbooks into checklists isn't part of DetectTrace.

A file that can't be read at all, such as one that holds no trace format DetectTrace reads, is refused with a message on its card, and nothing from it is stored. A file that can be read in part is stored in part, and its problems are listed on the card, as `check` reports them.

**Tool results are never stored.** The `gen_ai.tool.call.result` attribute is removed from each span before it is stored.

### Uploading again

- **Traces.** A span already stored by an earlier upload is dropped, and the card counts it as a duplicate, without a data note, so uploading the same file twice stores nothing twice. A span repeated within one file is stored once and gets a `duplicate_span` data note, as in `check`. A span with the same trace and span ID as a stored one but different content is not stored; the first copy wins, and the conflict is listed in the data notes.
- **Verdicts.** Each row becomes its case's current verdict. A newer verdict for a case replaces the older one; the card counts verdicts added, replaced and unchanged. Rows are applied in order, so a later row for the same case in one file wins.
- **Checklists.** A checklist for an alert class that already has one replaces the earlier one, and the card says so. After you confirm, each checklist is used as soon as it is uploaded: the app reloads the checklists and recomputes the dashboard. If the checklists then can't be loaded together, the upload is refused with the reason, and the checklists stay as they were.

### One trace format per data folder

A data folder holds either OTLP traces or Langfuse traces, never both. The first trace file you upload decides which. A file in the other format is refused: `This data folder holds OTLP traces. Clear the data to switch to Langfuse.` To switch, [clear the data](#clearing-data), or start the app with another `--data-dir`.

## The configuration

The proposal is the one `detecttrace init` would make from the same spans and verdicts. It shows how many agent runs it found, then:

- **Trace attributes.** Which span attribute holds each field: **Case ID**, **Alert class**, **Agent verdict**, **Prompt version**, **Tool name** and **Tool arguments**. Under each is how many agent runs (tool calls, for the two tool fields) carry it, and how it was found, such as `detecttrace.case_id, found on 201 of 201 agent runs (detecttrace attribute)`. Type another attribute to change one; the field offers the attribute keys found on your agent runs, and the counts update as you type. The fields are explained in [docs/attributes.md](attributes.md).
- **Analyst labels** and **Agent labels.** Each verdict label DetectTrace couldn't map by itself, with a choice of `true_positive`, `false_positive` or `benign`. For the demo data, that is `Closed - Benign` and `Malicious`. A label left as "Not mapped" is reported in the data notes, as in `check`.

**Confirm** stays disabled until Case ID and Agent verdict each have an attribute and at least one analyst label maps to a verdict; the text beside it says what is missing.

Once confirmed, the step shows a summary of the saved configuration. To change it later, choose **Change configuration**, edit and confirm again. The form starts from the saved configuration, so your earlier attribute choices and label mappings stay unless you change them. Nothing needs to be uploaded again: the stored data is recomputed with the new configuration. If `detecttrace.yaml` was edited by hand and can't be read, the form says why instead of proposing a new configuration; fix the file and try again.

The configuration is saved as `detecttrace.yaml` in the data folder, in the format of a `check` configuration. It always has `checklists: "checklists"`, the app's own folder, even before you upload a checklist. It has `label_map` and `agent_label_map` when they map a label, and `mapping` with only the fields that differ from the defaults; the other fields are listed as comments. It has no `traces`, `verdicts` or `output`, because the app keeps the data itself, and no `dashboard` unless you add it by hand. You can edit it by hand while the app is stopped; it is read on the next start.

## The data folder

`~/.detecttrace` by default, or the folder given with `--data-dir`:

| Path | What it holds |
|---|---|
| `detecttrace.db` | The SQLite database: the uploaded spans without tool results, the verdicts, the replaced verdicts, the input problems and the latest results. SQLite keeps `detecttrace.db-wal` and `detecttrace.db-shm` beside it while the app runs. |
| `detecttrace.yaml` | The confirmed configuration. |
| `checklists/` | One file per alert class, named after the class, such as `impossible_travel.yaml`. |

The app creates the folder and `checklists/` readable and writable by you only (mode `0700`), and the database file the same way (mode `0600`). A folder that already exists keeps the mode it has. While a file is being uploaded, it is kept in a temporary folder inside the data folder, named `upload-` and random characters, which is removed when the upload ends. A checklist upload after you confirm keeps a copy of the checklists in a `backup-` folder the same way. If the app stops in the middle, the next start removes these folders.

The database holds every span attribute your agent recorded except tool results, including tool arguments and any prompt or message content. Treat the data folder like the traces it came from. To back it up, stop the app and copy the folder.

## Clearing data

**Clear all data**, below the upload cards, asks for confirmation, then deletes:

- every stored span, verdict, replaced verdict and input problem, the results, and the recorded trace format,
- `detecttrace.yaml`,
- the files in `checklists/` and in its subfolders, and those subfolders. A link is removed, never what it points to, so files outside the data folder are never deleted.

The data folder, the empty database and the `checklists/` folder stay. It can't be undone. Afterwards the page reloads, empty, and the next trace upload can be in either format.

## Security

The app has a single user: you, in a browser on the same computer.

- **Loopback only.** It listens on `127.0.0.1`, and there is no option to listen anywhere else, so other computers can't reach it. Other programs and other users on the same computer can; on a shared computer, keep that in mind.
- **No tokens.** There is nothing to log in with. Loopback limits who can connect, and the remaining risk, other websites open in the same browser, is handled by the next two checks.
- **Other websites can't write.** Every upload, configuration change and clear must carry the header `X-DetectTrace: 1` and an `Origin` of the app itself (`http://127.0.0.1:4321` or `http://localhost:4321`, with your port). A page on another site can't send that header without the browser asking the app first, and the app approves no such request. A write that lacks either is refused with `403`.
- **DNS rebinding is refused.** Every request, reads included, must name the app in its `Host` header: `127.0.0.1` or `localhost`, with the app's port. A site that points its own name at `127.0.0.1` still sends its own name, and is refused with `403`.
- **The page loads nothing from outside.** As with the file from `check`, the page's Content-Security-Policy lets the browser run only the page's own script and style, and lets the page send requests only to the app that served it. The page is also sent with `Content-Security-Policy: frame-ancestors 'none'`, so other sites can't frame it, and every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- **Tool results are never stored.** They are removed from each span before it reaches the database.
- **Nothing leaves your computer.** DetectTrace sends no data anywhere: no telemetry, no update checks.
- **The terminal log** shows the method, path, status, client address and duration of each request: never headers, bodies or query strings, so no uploaded file name.

## Limits

| Limit | Value |
|---|---|
| Trace file | 256 MiB |
| Trace file once decompressed | 1 GiB |
| Lines in one trace file | 2,000,000 |
| Problems found in one trace file | 100,000 |
| Verdict file | 64 MiB |
| Rows in one verdict file | 1,000,000 |
| Checklist file | 1 MiB |
| Checklists per data folder | 100 |
| Trace uploads handled at once | 4; more wait their turn |
| Verdict and checklist uploads handled at once | 2; more wait their turn |
| Time to receive one file | 30 seconds |
| File name | 128 characters, longer names are cut; characters other than letters, digits, `.`, `_` and `-` become `_` |

A file past any of these limits is refused and nothing from it is stored; split it into smaller files. `check` has no such limits on the files it reads.

The app runs on the same storage and recompute as `detecttrace serve`, so the scale and memory figures in [docs/serve.md](serve.md#limits) apply. Keep the data folder on a local disk.

## How it differs from `check`

On the same files, the app gives the same results as `check`, apart from the differences below. It reads uploads with the loaders `check` uses, and computes the dashboard with the code `serve` uses. Parity tests check this: they upload the demo data and the test fixtures through the app, except those the app refuses or can't configure (below), confirm the configuration, and compare the results with `check`'s on the same files. They allow only the differences below, and compare data notes by kind and count. As in `check`, every case counts at once, even one dated after this computer's clock: uploaded files are complete, so no case waits for spans still on their way.

The differences:

- **Sources.** The results at `/api/results.json` name `uploaded traces` and `uploaded verdicts` as their sources, not file paths, and add a `served` block. Its `settle_seconds` is `null`, because no case is held back.
- **A case's verdict can change.** A newer verdict for a case replaces the older one, which is kept in the database. `check` reports a case ID that appears twice in its CSV.
- **Uploading again.** A span already stored by an earlier upload, such as the same span in two files, is dropped and counted as a duplicate on the upload card, without a data note. `check` notes every repeated span with `duplicate_span`.
- **Refused files.** A file that can't be read at all, such as an empty trace file, is refused on its upload card. It stores nothing and leaves no data note, where `check` notes it.

The configuration form edits only attribute keys and label maps, so it can't express two things `check` can:

- `prompt_version_lookup: descendant`, for a prompt version set only on the spans below the agent run. See [docs/attributes.md](attributes.md).
- An agent verdict attribute whose values are all empty strings. The proposal counts the field as missing, so **Confirm** stays disabled; `check` takes the attribute as given.

## How it differs from `serve`

`detecttrace serve` is a long-running service for a team. The app is for one person:

- **Input.** You upload files in the browser. `serve` receives spans from an OpenTelemetry Collector over OTLP/HTTP and verdicts from a verdict API.
- **Access.** The app listens on `127.0.0.1` only and has no tokens. `serve` listens on the network and requires a token for every request but its health check, with TLS as an option.
- **Configuration.** The app proposes the configuration and you confirm it in the browser. `serve` reads a configuration file at startup.
- **Settling.** The app counts every case at once, even one dated after this computer's clock. `serve` waits `settle_seconds` after a case's agent run ends.
- **Updates.** The app's page reloads itself when newer results exist, because your own upload made them. The served page shows a "New data is available." bar instead.

For a dashboard that a team shares, fed by your agents as they run, see [docs/serve.md](serve.md).
