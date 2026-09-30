# Checklists

A checklist lists the tool calls your incident response playbook requires for one alert class. DetectTrace checks each case's successful tool calls against it. The share of items satisfied is the case's evidence completeness (see [Metrics](metrics.md#evidence-completeness)).

Write checklists from your own playbook. The examples on this page are generic.

## Format

One YAML file per alert class:

```yaml
alert_class: impossible_travel
items:
  - id: signin_history
    tool: get_signin_logs
    args:
      range: { min_duration: 24h }
  - id: mfa_check
    tool: check_mfa_status
  - id: location_history
    tool: get_user_locations
    args:
      lookback_days: { min: 30 }
```

| Key | Required | Meaning |
|---|---|---|
| `alert_class` | Yes | The class, as the verdict file writes it. Matching ignores case and extra spaces. |
| `items` | Yes, at least one | The required tool calls, in the order the dashboard lists them |
| `items[].id` | Yes | A name for the step, unique within the file |
| `items[].tool` | Yes | The tool name, matched exactly (including case) against the tool name in the traces |
| `items[].args` | No | Argument rules, keyed by argument path. Without `args`, any successful call to the tool satisfies the item. |

Point `checklists` in `detecttrace.yaml` at one file or at a folder. In a folder, every `*.yaml` and `*.yml` file counts (any case), in subfolders too. Hidden files and folders are skipped, and symbolic links to folders are not followed. An alert class with no checklist gets verdict metrics only.

## Argument rules

Each path under `args` holds one or more rules. Every rule under every path must pass for a call to pass.

| Rule | Passes when the argument | Example |
|---|---|---|
| `equals` | equals the value | `mode: { equals: full }` |
| `in` | equals one of the values | `scope: { in: [tenant, user] }` |
| `exists` | is present (`true`), or absent (`false`) | `include_history: { exists: true }` |
| `matches` | is text containing a match for the regular expression | `query: { matches: "(?i)signinlogs" }` |
| `min` | is a number at least this large | `lookback_days: { min: 30 }` |
| `max` | is a number at most this large | `result_limit: { max: 1000 }` |
| `min_duration` | is a duration at least this long | `range: { min_duration: 24h }` |
| `kql_min_ago` | is a KQL query with an `ago(...)` lookback at least this long | `query: { kql_min_ago: 7d }` |

Every rule in one checklist:

```yaml
alert_class: example_class
items:
  - id: every_rule
    tool: search_logs
    args:
      mode: { equals: full }
      scope: { in: [tenant, user] }
      include_history: { exists: true }
      debug: { exists: false }
      query: { matches: "(?i)signinlogs", kql_min_ago: 7d }
      lookback_days: { min: 30, max: 90 }
      range: { min_duration: P1D }
```

### `equals` and `in`

Values compare as JSON: `24` equals `24.0`, but `true` never equals `1`, and the text `"24"` never equals the number `24`. Lists and objects compare deeply. `equals: null` passes only when the argument is JSON `null`, not when it is missing. `in` needs at least one value.

### `exists`

`exists: true` passes when the path holds any value, `null` included. `exists: false` passes when it doesn't, including for a call with no arguments at all.

### `matches`

A Python regular expression, searched anywhere in the text, so `signinlogs` matches `SigninLogs | where ...` only with `(?i)` for case-insensitive matching. Anchor it with `^` and `$` to match the whole text. The argument must be text; a number or list never matches. An invalid expression is an error when the checklist loads.

### `min` and `max`

Both bounds are inclusive. The argument must be a JSON number: the text `"30"` and `true` never pass. In the checklist, the bound must be a finite number, and `min` can't be greater than `max`.

### `min_duration`

The argument is text holding a duration, read in one of the forms below. It passes when the duration is at least the rule's.

To compare two timestamps instead, give `start` and `end`: paths inside the argument to two ISO 8601 timestamps. The rule passes when they are at least the duration apart, in either order. Both timestamps must have a time zone, or both must have none.

```yaml
alert_class: example_class
items:
  - id: grant_window
    tool: get_oauth_grants
    args:
      window: { min_duration: 24h, start: start, end: end }
  - id: search_window
    tool: search_logs
    args:
      $: { min_duration: 7d, start: from, end: to }
```

The first item reads `{"window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}}`. The second reads `from` and `to` at the top level of the arguments.

### `kql_min_ago`

The argument is a KQL query. DetectTrace finds every `ago(...)` in it and reads its timespan (`d`, `h`, `m`, `s` or `ms`, as in `ago(7d)` or `ago(1.5h)`). The rule passes when any one lookback is at least the rule's duration. So a query with `ago(1h)` and `ago(30d)` passes `kql_min_ago: 7d`. A lookback that can't be read, such as `ago(time(1d))`, is reported.

## Durations

`min_duration` and `kql_min_ago` take these forms, and `min_duration` reads the same forms in arguments:

| Form | Examples | Units |
|---|---|---|
| Number and unit | `24h`, `7d`, `30m`, `90s`, `1.5h` | `s`, `m`, `h`, `d` (lowercase) |
| ISO 8601 | `P1D`, `PT24H`, `P1W`, `P1DT12H`, `PT0.5H` | Weeks, days, hours, minutes, seconds |

A leading minus is ignored, so `-24h` reads as 24 hours. Years and months (`P1M`) are not accepted, since they have no fixed length. Weeks may be mixed with days (`P1W2D`), and any unit may have a fraction.

## Argument paths

| Path | Reads |
|---|---|
| `lookback_days` | The top-level key `lookback_days` |
| `filter.user` | Key `user` inside the object `filter` |
| `filters[0].field` | Key `field` in the first element of the list `filters` |
| `$` | The whole arguments object |

A path that leads nowhere is missing: it fails every rule except `exists: false`. Write `query`, not `$.query`.

## Types

Rules never convert types. A rule that meets a value it can never pass is reported once per checklist item, with a fix. For example:

- `min` or `max` on text such as `"30"`: use `min_duration` or `equals`.
- `equals: 24` on the text `"24"`: quote the expected value in the checklist (`equals: "24"`).
- `equals: "24"` on the number `24`: unquote it.
- `min_duration` on a number: use `min`.

## Outcomes

Each case gets one outcome per item:

| Outcome | When |
|---|---|
| Satisfied | At least one successful call to the tool passes every rule. |
| Failed | No successful call passes, and at least one call to the tool errored. |
| Missed: not called | The case has no call to the tool. |
| Missed: wrong arguments | Every call succeeded, and none passed the rules. The case detail names the first failing rule of the first such call, as `<path>: <rule>`, or "arguments could not be read". |

A failed call is never checked for arguments. So a retry counts: a call that errors and then succeeds satisfies the item when the retry's arguments pass. Every successful call is checked, so call order doesn't change the outcome.

When one path has several rules, the first failing rule is named in this order: `equals`, `in`, `exists`, `matches`, `min_duration`, `kql_min_ago`, `min`, `max`. Paths are checked in checklist order.

Failed and missed items both count as skipped in [skipped steps by version](metrics.md#skipped-steps-by-version).

## Inactive `.yaml.example` files

`detecttrace init` writes an example checklist as `checklists/<class>.yaml.example`. It lists every tool the agent called for that class, most called first. It has no effect until you edit it down to what your playbook requires and rename it to `.yaml`: a checklist copied from what the agent happened to do would show a completeness of about 100%.

`check` notes each `.yaml.example` file: "checklist '...' is inactive. Rename it to .yaml to measure evidence completeness." A folder holding only `.yaml.example` files runs without checklists.

## Validation

A checklist that can't be used stops `check` with exit code 1, naming the file and each problem. For example:

```text
impossible_travel.yaml: invalid checklist
  items.0.colour: Extra inputs are not permitted
  items.1.args.lookback_days.min: must be a number
```

| Problem | Message |
|---|---|
| An unknown key, anywhere | `Extra inputs are not permitted` |
| Two items with one ID | `duplicate item id '<id>'` |
| A path with no rule | `no rule given; use equals, in, exists, matches, min_duration, kql_min_ago, min or max` |
| A rule set to `null` (except `equals`) | `'<rule>' must not be null` |
| `start` without `end`, or the reverse | `'start' and 'end' must be given together` |
| `start` and `end` without `min_duration` | `'start' and 'end' are only used with 'min_duration'` |
| A duration that can't be read | `'<text>' is not a duration; use a form like 24h, 7d or PT24H` |
| `$.query` as a path | `path '$.query' is not valid; use 'query', not '$.query'` |
| Two files for one alert class (after ignoring case and extra spaces) | `alert class '<class>' already has a checklist in <file>. Keep one file per alert class.` |
| A `checklists` folder with no `*.yaml`, `*.yml` or `.yaml.example` file | `No checklist files (*.yaml, *.yml) found under <folder>.` |

Checklist files are read as YAML 1.2 with JSON types only, so `no` is text, not `false`, and `2026-09-01` is text, not a date. Duplicate keys, aliases (`*name`) and tags such as `!!timestamp` are errors. A file larger than 1 MiB is refused.

Checklist items are also checked against the traces, as warnings:

- an item whose tool no case calls ("checklist item requires the tool '...', which no case calls");
- an item with argument rules whose tool is called, but never with arguments (check `mapping.tool_arguments`, see [Trace attributes](attributes.md#the-mapping-section));
- a checklist for an alert class that no case has.
