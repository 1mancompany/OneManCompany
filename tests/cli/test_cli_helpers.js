#!/usr/bin/env node
/* Smoke tests for bin/cli.js helpers — runnable via `node tests/cli/test_cli_helpers.js`.
 *
 * Regression guard for the @dev-stays-stale bug: installing via `npx @dev`
 * used to leave the local checkout at whatever version it was last copied at,
 * and the banner printed the npm CLI version instead of the actually-installed
 * app version. Both behaviors are tested below.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execSync } = require("child_process");

const cliPath = path.resolve(__dirname, "..", "..", "bin", "cli.js");
const cliSrc = fs.readFileSync(cliPath, "utf-8");

let failures = 0;
function assert(cond, msg) {
  if (cond) {
    console.log(`  ok  ${msg}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${msg}`);
  }
}

// ── readAppVersion ──────────────────────────────────────────────────────────
// Load the helper out of cli.js by evaluating just the function body in a
// sandbox-free way (it depends on fs/path which are real). We grab the source
// between `function readAppVersion` and the next blank-line-followed-by-token.
function loadReadAppVersion() {
  const m = cliSrc.match(/function readAppVersion\([\s\S]*?\n\}\n/);
  if (!m) throw new Error("readAppVersion not found in cli.js");
  // eslint-disable-next-line no-new-func
  return new Function("fs", "path", `${m[0]}\nreturn readAppVersion;`)(fs, path);
}

const readAppVersion = loadReadAppVersion();

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "omc-cli-test-"));

// Case 1: valid pyproject.toml
fs.writeFileSync(
  path.join(tmp, "pyproject.toml"),
  `[project]\nname = "x"\nversion = "1.2.3"\ndescription = "x"\n`,
);
assert(readAppVersion(tmp) === "1.2.3", 'readAppVersion returns "1.2.3" for valid pyproject');

// Case 2: missing pyproject
const tmp2 = fs.mkdtempSync(path.join(os.tmpdir(), "omc-cli-test-empty-"));
assert(readAppVersion(tmp2) === null, "readAppVersion returns null when pyproject is missing");

// Case 3: pyproject without a version line
fs.writeFileSync(path.join(tmp2, "pyproject.toml"), `[project]\nname = "x"\n`);
assert(readAppVersion(tmp2) === null, "readAppVersion returns null when version line is absent");

// ── Banner / update behavior is documented inline ──────────────────────────
// The CLI must:
//   1. Default-refresh source on every run (so `@dev` stays in sync)
//   2. Use the *installed* app version (from installDir/pyproject.toml) for
//      the banner, never the npm CLI's cliVersion
// Both are enforced by the source-level invariants below — testing them
// end-to-end requires running the real CLI (which installs UV/Python), so
// instead we assert the source contains the expected idioms.

assert(
  /Updating installation to v\$\{cliVersion\}/.test(cliSrc),
  "CLI logs an update line referencing cliVersion on existing installs",
);
assert(
  /const wantNoUpdate = passthrough\.includes\("--no-update"\)/.test(cliSrc),
  "CLI honors --no-update opt-out",
);
assert(
  /v\$\{appVersion\} in (background|debug mode)/.test(cliSrc),
  "Startup messages use the installed appVersion, not cliVersion",
);
assert(
  !/v\$\{cliVersion\} in (background|debug mode)/.test(cliSrc),
  "Startup messages do NOT use cliVersion (would mask the actual installed version)",
);
assert(
  /const verTag = `v\$\{appVersion\}`/.test(cliSrc),
  "Banner uses appVersion read from installDir/pyproject.toml",
);

if (failures) {
  console.log(`\n${failures} failed`);
  process.exit(1);
}
console.log("\nall tests passed");
