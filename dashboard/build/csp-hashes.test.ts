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

  it("hashes a script body's surrounding whitespace too", () => {
    const page = PAGE.replace("run()", "\n  run()\n");

    // The digest of "\n  run()\n", computed outside Node with openssl.
    expect(computeCspHashes(page).script).toBe("sha256-n70kDLiI7xto2L8E2mYfhBOZvIqtOhzAvITUbPNMSPE=");
  });

  it("refuses a page with no executable script", () => {
    const page = PAGE.replace('<script type="module">run()</script>', "");

    expect(() => computeCspHashes(page)).toThrow("Expected exactly one executable <script>, found 0.");
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

  it("refuses a page that still links a stylesheet", () => {
    const page = PAGE.replace("</head>", '<link rel="stylesheet" href="a.css"></head>');

    expect(() => computeCspHashes(page)).toThrow("still links an external file");
  });

  it("refuses a page that still preloads a module", () => {
    const page = PAGE.replace("</head>", '<link rel="modulepreload" href="a.js"></head>');

    expect(() => computeCspHashes(page)).toThrow("still links an external file");
  });
});
