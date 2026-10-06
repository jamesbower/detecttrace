// Hashes the one inline script and the one inline style of the built page, so the Python
// side can allow exactly them in a hash-based Content Security Policy.
import { createHash } from "node:crypto";

import type { Plugin } from "vite";

export type CspHashes = { script: string; style: string };

const SCRIPT_PATTERN = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
const STYLE_PATTERN = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
const JSON_TYPE_PATTERN = /\btype\s*=\s*["']?application\/json["']?/i;
const SRC_PATTERN = /\bsrc\s*=/i;
const EXTERNAL_LINK_PATTERN = /<link\b[^>]*\brel\s*=\s*["']?(?:stylesheet|modulepreload)\b[^>]*>/i;

export function computeCspHashes(html: string): CspHashes {
  const externalLink = EXTERNAL_LINK_PATTERN.exec(html);
  if (externalLink !== null) {
    throw new Error(`The page still links an external file: ${externalLink[0]}`);
  }
  const scripts = [...html.matchAll(SCRIPT_PATTERN)].filter(
    ([, attributes]) => !JSON_TYPE_PATTERN.test(attributes ?? ""),
  );
  const styles = [...html.matchAll(STYLE_PATTERN)];
  if (scripts.length !== 1) {
    throw new Error(`Expected exactly one executable <script>, found ${scripts.length}.`);
  }
  if (styles.length !== 1) {
    throw new Error(`Expected exactly one <style>, found ${styles.length}.`);
  }
  const [, scriptAttributes, scriptBody] = scripts[0]!;
  if (SRC_PATTERN.test(scriptAttributes ?? "")) {
    throw new Error("The <script> loads an external file; it must be inline.");
  }
  return { script: hashSha256(scriptBody ?? ""), style: hashSha256(styles[0]![1] ?? "") };
}

/** Renames the built page and writes its CSP hashes beside it. Runs after inlining. */
export function cspHashes(options: { htmlFileName: string; hashesFileName: string }): Plugin {
  return {
    name: "detecttrace-csp-hashes",
    apply: "build",
    enforce: "post",
    generateBundle(_outputOptions, bundle) {
      const page = bundle["index.html"];
      if (page === undefined || page.type !== "asset") {
        this.error("The build produced no index.html to hash.");
      }
      const html = typeof page.source === "string" ? page.source : new TextDecoder().decode(page.source);
      const hashes = computeCspHashes(html);
      delete bundle["index.html"];
      const remaining = Object.keys(bundle);
      if (remaining.length > 0) {
        this.error(`The build left files outside the page: ${remaining.join(", ")}.`);
      }
      this.emitFile({ type: "asset", fileName: options.htmlFileName, source: html });
      this.emitFile({
        type: "asset",
        fileName: options.hashesFileName,
        source: `${JSON.stringify(hashes, null, 2)}\n`,
      });
    },
  };
}

function hashSha256(text: string): string {
  return `sha256-${createHash("sha256").update(text, "utf8").digest("base64")}`;
}
