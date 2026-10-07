# Dashboard

The DetectTrace dashboard is a React app. Vite builds it into one self-contained HTML file, `src/detecttrace/templates/dashboard.html`, plus `dashboard.hashes.json` beside it. Python fills that file with the view and the results as JSON, and with a Content-Security-Policy built from the hashes. Users of DetectTrace never need Node; only contributors to this folder do.

## Requirements

Node 22.22.2 exactly, with npm: the version CI builds with, pinned in `.nvmrc` and in `engines` in `package.json`.

## Licences

The built page inlines React, React DOM and scheduler, all MIT. Their licence texts are in `THIRD_PARTY_NOTICES` at the repository root, which ships in the wheel and the sdist beside `LICENSE`. When a production dependency is added or updated, add or update its licence text there; `node scripts/check-licences.mjs` checks every production dependency's licence against the allow-list.

## Commands

Run these in `dashboard/`:

```sh
npm ci              # install the exact locked packages
npm run lint        # ESLint
npm run typecheck   # TypeScript, for the app and the build config
npm test            # Vitest
npm run build       # write the page and its hashes to src/detecttrace/templates/
npm run dev         # a dev server that shows the demo data
```

## Rebuild and commit the page

Any change under `dashboard/` needs `npm run build`, and the rebuilt `src/detecttrace/templates/dashboard.html` and `src/detecttrace/templates/dashboard.hashes.json` committed with it. CI rebuilds the page and fails if the result differs from the committed files.

## Display text comes from Python

`src/detecttrace/dashboard_view.py` builds the view: every number, interval, label and sentence that depends on the data, already formatted. React lays it out and doesn't compute or format a number. To change what a value says, change the view model in Python. The page's fixed wording, such as each page's title and description, lives in the components.

`src/view.d.ts` holds the view's TypeScript types. It is generated from the Python dataclasses; don't edit it. After you change the view, run this from the repository root:

```sh
uv run python scripts/generate_view_types.py
```

## Adding pages and panels

A page registers itself with `registerPage` from `src/registry.ts`: a path, a title, an icon (an SVG path drawn in a 24 by 24 box), an order in the sidebar, and a component that gets the view and the results. The built-in pages register in `src/pages/index.ts`. Each page's route and its filters, as query parameters, are in the page's address, so a link or a reload keeps them. A page from `detecttrace serve` or `detecttrace ui` routes on the path, such as `/cases?class=class-1`: both servers answer a `GET` for every path of one segment of lowercase letters and hyphens (`[a-z][a-z-]*`), except `/api`, with the page, so a new page needs no change on the Python side. `registerPage` refuses any other path, so a page's path is always one the servers answer. The file from `check` routes on the hash, such as `#/cases?class=class-1`, because a file opened from disk has no server to ask for `/cases`. Link to a page with `PageLink` from `src/components/PageLink.tsx`, which writes the right address for either and opens a page in place on a plain click.

A page that needs no results, such as Help, can also show on a waiting page, before there is anything to score. Give it `resultlessModes`, the view modes in which a waiting page shows it: Help registers with `["offline", "served", "ui"]`, and Data with `["ui"]`, as only the ui app's reader supplies the data there. Its component then gets `results: Results | null` (`ResultlessPageProps`), and the type checker refuses one that expects results. A waiting page lists only the pages resultless in its mode in the sidebar, except in the ui app, which lists every page, and shows the waiting state in place of any other page. Without `resultlessModes`, a page needs results.

A panel adds content to an existing page without editing it. `registerPanel(slot, component)` puts a component in one of these slots: `overview-after-kpis`, `version-row-detail` (a version table row, with its class and version), `trend-footer`, `case-detail` (an opened case, with its ID) and `notes-after`. Panels render in registration order, each inside its own error boundary.

## Adding a help section

A section of the Help page registers itself with `registerHelpSection({ id, title, order, body, modes? })` from `src/registry.ts`. The `id` is the section heading's id and its address, such as `#/help#verdict-agreement` or `/help#verdict-agreement`; it must match `^[a-z][a-z-]*$`, and `registerHelpSection` refuses any other id, the shell's own `main-content`, or an id already registered. The page lists the sections by `order`, then by `id`. `modes` limits a section to those view modes, such as `["ui"]` for one that only `detecttrace ui` shows; without it, the section shows in every mode. The built-in sections register in `src/help/sections.tsx`. `tests/test_help_text.py` checks the wording in `src/help/`: each term's short explanation against its section in `docs/metrics.md`, and every file for words that judge rather than describe.
