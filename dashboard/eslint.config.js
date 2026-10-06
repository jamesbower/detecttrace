import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// TypeScript files skip no-undef, so Node-only globals in page code are banned by name.
const NODE_ONLY_GLOBALS = Object.keys(globals.node).filter((name) => !(name in globals.browser));

export default tseslint.config(
  { ignores: ["node_modules/"] },
  js.configs.recommended,
  tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      // Raw HTML would bypass React's escaping of the trace data the dashboard shows.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "dangerouslySetInnerHTML is banned: render text, never raw HTML.",
        },
      ],
    },
  },
  // The page runs in a browser; only the build tooling runs in Node.
  {
    files: ["src/**"],
    languageOptions: { globals: globals.browser },
    rules: {
      "no-restricted-globals": [
        "error",
        ...NODE_ONLY_GLOBALS.map((name) => ({
          name,
          message: "The page runs in a browser; Node globals are for build tooling only.",
        })),
      ],
    },
  },
  {
    files: ["build/**", "scripts/**", "vite.config.ts", "eslint.config.js"],
    languageOptions: { globals: globals.node },
  },
);
