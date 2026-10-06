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

A page registers itself with `registerPage` from `src/registry.ts`: a path, a title, an icon (an SVG path drawn in a 24 by 24 box), an order in the sidebar, and a component that gets the view and the results. The built-in pages register in `src/pages/index.ts`. Each page's route is a hash, such as `#/cases`, and its filters are query parameters after it, so a link or a reload keeps them.

A panel adds content to an existing page without editing it. `registerPanel(slot, component)` puts a component in one of these slots: `overview-after-kpis`, `version-row-detail` (a version table row, with its class and version), `trend-footer`, `case-detail` (an opened case, with its ID) and `notes-after`. Panels render in registration order, each inside its own error boundary.
