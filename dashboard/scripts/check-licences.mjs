// Fails when a production dependency of the dashboard carries a licence outside the allow-list.
// The built page inlines these packages, so their licences ship with every dashboard.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export const ALLOWED_LICENCES = new Set([
  "MIT",
  "ISC",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "Apache-2.0",
  "0BSD",
]);

/** True when an SPDX expression permits use under the allow-list. */
export function isAllowedLicence(expression) {
  if (typeof expression !== "string" || expression.trim() === "") {
    return false;
  }
  const bare = expression.trim().replace(/^\((.*)\)$/, "$1");
  if (bare.includes("(")) {
    // Nested expressions are rare enough to review by hand.
    return false;
  }
  if (/\sOR\s/.test(bare)) {
    return bare.split(/\s+OR\s+/).some((part) => ALLOWED_LICENCES.has(part));
  }
  return bare.split(/\s+AND\s+/).every((part) => ALLOWED_LICENCES.has(part));
}

/** Each package in an `npm ls --json --long` tree whose licence is not allowed, sorted. */
export function findDisallowed(tree, readLicence) {
  const disallowed = new Map();
  const visit = (dependencies) => {
    for (const node of Object.values(dependencies ?? {})) {
      const id = `${node.name}@${node.version}`;
      if (!disallowed.has(id)) {
        const licence = readLicence(node.path);
        if (!isAllowedLicence(licence)) {
          disallowed.set(id, `${id}: ${licence ?? "no licence"}`);
        }
      }
      visit(node.dependencies);
    }
  };
  visit(tree.dependencies);
  return [...disallowed.values()].sort();
}

function readPackageLicence(packagePath) {
  const manifest = JSON.parse(readFileSync(join(packagePath, "package.json"), "utf8"));
  const licence = manifest.license;
  return typeof licence === "object" && licence !== null ? licence.type : licence;
}

function main() {
  const output = execFileSync("npm", ["ls", "--omit=dev", "--all", "--json", "--long"], {
    encoding: "utf8",
  });
  const problems = findDisallowed(JSON.parse(output), readPackageLicence);
  if (problems.length > 0) {
    console.error(`Production dependencies with a licence outside the allow-list:\n${problems.join("\n")}`);
    process.exit(1);
  }
  console.log("Every production dependency has an allowed licence.");
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
