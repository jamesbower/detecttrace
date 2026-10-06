// `npm run dev` only: fills the page's empty data blocks with the demo fixtures, as the Python
// side does for a real dashboard. `apply: "serve"` keeps it out of the production build.
import { readFileSync } from "node:fs";

import type { Plugin } from "vite";

export type DevSampleOptions = { viewPath: string; resultsPath: string };

export function devSample(options: DevSampleOptions): Plugin {
  return {
    name: "detecttrace-dev-sample",
    apply: "serve",
    transformIndexHtml(html) {
      return fillDataBlocks(html, {
        "dt-view": readFileSync(options.viewPath, "utf8"),
        "dt-results": readFileSync(options.resultsPath, "utf8"),
      });
    },
  };
}

/** Puts each JSON text into the empty `<script>` block with its id. */
export function fillDataBlocks(html: string, blocks: Readonly<Record<string, string>>): string {
  let filled = html;
  for (const [id, json] of Object.entries(blocks)) {
    const empty = `<script type="application/json" id="${id}"></script>`;
    if (!filled.includes(empty)) {
      throw new Error(`The page has no empty "${id}" data block to fill.`);
    }
    // Escaped so a "</script>" inside a string cannot end the block early.
    const safe = json.trim().replaceAll("<", "\\u003c");
    filled = filled.replace(empty, () => `<script type="application/json" id="${id}">${safe}</script>`);
  }
  return filled;
}
