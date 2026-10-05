import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";

import { computeCspHashes } from "./csp-hashes";

function sha256(text: string): string {
  return `sha256-${createHash("sha256").update(text, "utf8").digest("base64")}`;
}

const PAGE =
  '<html><head><script type="module">run()</script><style>a{}</style></head>' +
  '<body><script type="application/json" id="dt-view">{}</script></body></html>';

describe("computeCspHashes", () => {
  it("hashes the inline script's exact text", () => {
    expect(computeCspHashes(PAGE).script).toBe(sha256("run()"));
  });

  it("hashes the inline style's exact text", () => {
    expect(computeCspHashes(PAGE).style).toBe(sha256("a{}"));
  });

  it("refuses a page with a second executable script", () => {
    const page = PAGE.replace("</body>", "<script>more()</script></body>");

    expect(() => computeCspHashes(page)).toThrow("exactly one executable <script>, found 2");
  });

  it("refuses a page with no style", () => {
    const page = PAGE.replace("<style>a{}</style>", "");

    expect(() => computeCspHashes(page)).toThrow("exactly one <style>, found 0");
  });

  it("refuses an external script", () => {
    const page = PAGE.replace('<script type="module">run()', '<script type="module" src="x.js">');

    expect(() => computeCspHashes(page)).toThrow("must be inline");
  });
});
