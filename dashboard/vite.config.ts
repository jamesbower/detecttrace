import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { defineConfig } from "vitest/config";

import { cspHashes } from "./build/csp-hashes.ts";

// The page is committed to the Python package, so Python users never need Node.
export default defineConfig({
  plugins: [
    react(),
    viteSingleFile({ removeViteModuleLoader: true }),
    cspHashes({ htmlFileName: "dashboard.html", hashesFileName: "dashboard.hashes.json" }),
  ],
  build: {
    outDir: "../src/detecttrace/templates",
    // The output folder holds the other templates, so the build must never clear it.
    emptyOutDir: false,
    // The page has nothing to preload, and the polyfill would add a fetch call to it.
    modulePreload: { polyfill: false },
    sourcemap: false,
    reportCompressedSize: false,
  },
  test: {
    environment: "jsdom",
  },
});
