import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { defineConfig } from "vitest/config";

import { cspHashes } from "./build/csp-hashes.ts";
import { devSample } from "./build/dev-sample.ts";

// The page is committed to the Python package, so Python users never need Node.
export default defineConfig({
  plugins: [
    react(),
    viteSingleFile({ removeViteModuleLoader: true }),
    cspHashes({ htmlFileName: "dashboard.html", hashesFileName: "dashboard.hashes.json" }),
    devSample({
      viewPath: fileURLToPath(new URL("../tests/fixtures/demo/expected-view.json", import.meta.url)),
      resultsPath: fileURLToPath(new URL("../tests/fixtures/demo/expected.json", import.meta.url)),
    }),
  ],
  // Nothing outside src/ may reach the page; a public/ folder would be copied beside it.
  publicDir: false,
  build: {
    outDir: "../src/detecttrace/templates",
    // The output folder holds the other templates, so the build must never clear it.
    emptyOutDir: false,
    // The page has nothing to preload, and the polyfill would add a fetch call to it.
    modulePreload: { polyfill: false },
    sourcemap: false,
    rolldownOptions: {
      // Keeps the @license headers, so the page carries React's MIT notice.
      output: { comments: { legal: true } },
    },
    reportCompressedSize: false,
  },
  test: {
    environment: "jsdom",
  },
});
