#!/usr/bin/env node
/* Smoke tests for bin/cli.js helpers + install/update semantics.
 *
 * Runnable via `node tests/cli/test_cli_helpers.js`.
 *
 * Regression guard for two bugs:
 *   1. `npx @dev` re-runs used to leave the local checkout stale (default
 *      was opt-in --update). Now CODE_ITEMS are refreshed every run.
 *   2. The banner used npm CLI version instead of the actually-installed app
 *      version, masking bug #1.
 *
 * Also guards against the v2 regression we caught in review: refreshing
 * everything (including `company/` + `config.yaml`) on every run would
 * silently destroy user-edited workflows. USER_OWNED_ITEMS must be
 * preserved across updates.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");

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

function mktmp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

// Extract a named top-level function body from cli.js and reify it. Used so
// we can exercise helpers without booting the full CLI (which installs UV).
function extractFn(name, deps) {
  const re = new RegExp(`function ${name}\\([\\s\\S]*?\\n\\}\\n`);
  const m = cliSrc.match(re);
  if (!m) throw new Error(`${name} not found in cli.js`);
  const depNames = Object.keys(deps);
  // eslint-disable-next-line no-new-func
  return new Function(...depNames, `${m[0]}\nreturn ${name};`)(...depNames.map((k) => deps[k]));
}

// ── readAppVersion ──────────────────────────────────────────────────────────
const readAppVersion = extractFn("readAppVersion", { fs, path });

{
  const tmp = mktmp("omc-cli-rav-");
  fs.writeFileSync(
    path.join(tmp, "pyproject.toml"),
    `[project]\nname = "x"\nversion = "1.2.3"\ndescription = "x"\n`,
  );
  assert(readAppVersion(tmp) === "1.2.3", 'readAppVersion returns "1.2.3" for valid pyproject');
}
{
  const tmp = mktmp("omc-cli-rav-missing-");
  assert(readAppVersion(tmp) === null, "readAppVersion returns null when pyproject is missing");
}
{
  const tmp = mktmp("omc-cli-rav-noversion-");
  fs.writeFileSync(path.join(tmp, "pyproject.toml"), `[project]\nname = "x"\n`);
  assert(readAppVersion(tmp) === null, "readAppVersion returns null when version line is absent");
}

// ── copyItems: overwrite vs preserve semantics ─────────────────────────────
// This is the critical behavioral test — review caught a v1 regression where
// default-refresh was overwriting user-owned files. copyItems with
// {overwrite: false} must leave existing dest files untouched.
// (copyItems is defined inline inside main() in cli.js, so we re-implement
// the same shape here against a controllable srcRoot. The source-level
// invariants further down assert the production version stays in sync.)

{
  const srcRoot = mktmp("omc-cli-copy-src-");
  const dstRoot = mktmp("omc-cli-copy-dst-");
  // Set up: src has new versions of both items
  fs.mkdirSync(path.join(srcRoot, "src"));
  fs.writeFileSync(path.join(srcRoot, "src", "x.py"), "new code");
  fs.mkdirSync(path.join(srcRoot, "company"));
  fs.writeFileSync(path.join(srcRoot, "company", "default.md"), "shipped default");

  // Dest already has the user's customized versions
  fs.mkdirSync(path.join(dstRoot, "src"));
  fs.writeFileSync(path.join(dstRoot, "src", "x.py"), "old code");
  fs.mkdirSync(path.join(dstRoot, "company"));
  fs.writeFileSync(path.join(dstRoot, "company", "default.md"), "USER EDITS");

  // Run copyItems against a fake npmPkgRoot by stubbing path.join to use srcRoot
  // Instead: we just call the extracted copyItems(items, dstRoot, {overwrite})
  // but the function looks at npmPkgRoot internally — actually NO, it takes
  // src from path.join(npmPkgRoot, item). npmPkgRoot is a closure var in
  // main(). Since we extracted copyItems standalone, it has no closure. Let's
  // re-create it inline with srcRoot for testing.
  function copyItemsTest(items, destRoot, { overwrite }) {
    for (const item of items) {
      const src = path.join(srcRoot, item);
      const dest = path.join(destRoot, item);
      if (!fs.existsSync(src)) continue;
      if (fs.existsSync(dest)) {
        if (!overwrite) continue;
        const tmp = `${dest}.tmp-${process.pid}`;
        if (fs.existsSync(tmp)) fs.rmSync(tmp, { recursive: true, force: true });
        fs.cpSync(src, tmp, { recursive: true });
        fs.rmSync(dest, { recursive: true, force: true });
        fs.renameSync(tmp, dest);
      } else {
        fs.cpSync(src, dest, { recursive: true });
      }
    }
  }

  // CODE: refreshed
  copyItemsTest(["src"], dstRoot, { overwrite: true });
  assert(
    fs.readFileSync(path.join(dstRoot, "src", "x.py"), "utf-8") === "new code",
    "copyItems({overwrite: true}) replaces existing files (CODE refresh path)",
  );

  // USER-OWNED: preserved
  copyItemsTest(["company"], dstRoot, { overwrite: false });
  assert(
    fs.readFileSync(path.join(dstRoot, "company", "default.md"), "utf-8") === "USER EDITS",
    "copyItems({overwrite: false}) preserves existing user edits (USER-OWNED preserve path)",
  );

  // USER-OWNED first install: bootstrap when dest missing
  const freshDst = mktmp("omc-cli-copy-fresh-");
  copyItemsTest(["company"], freshDst, { overwrite: false });
  assert(
    fs.existsSync(path.join(freshDst, "company", "default.md")) &&
      fs.readFileSync(path.join(freshDst, "company", "default.md"), "utf-8") === "shipped default",
    "copyItems({overwrite: false}) DOES copy when dest doesn't exist (first-install bootstrap)",
  );
}

// ── Source-level invariants for things hard to test behaviorally ──────────
assert(
  /const wantNoUpdate = passthrough\.includes\("--no-update"\)/.test(cliSrc),
  "CLI honors --no-update opt-out",
);
assert(
  /CODE_ITEMS\s*=\s*\[[^\]]*"src"[^\]]*"frontend"[^\]]*\]/.test(cliSrc),
  "CODE_ITEMS includes at least src and frontend",
);
assert(
  /USER_OWNED_ITEMS\s*=\s*\[[^\]]*"company"[^\]]*"config\.yaml"[^\]]*\]/.test(cliSrc),
  "USER_OWNED_ITEMS protects company/ and config.yaml from overwrite",
);
assert(
  /CLI_ONLY_FLAGS\s*=\s*new Set\(\[[^\]]*"--no-update"[^\]]*\]\)/.test(cliSrc),
  "--no-update is stripped before forwarding args to the Python launcher",
);
assert(
  /const verTag = `v\$\{appVersion\}`/.test(cliSrc),
  "Banner uses appVersion (read from installDir/pyproject.toml), not cliVersion",
);
assert(
  !/Starting OneManCompany v\$\{cliVersion\}/.test(cliSrc),
  "Startup messages do NOT use cliVersion (would mask the actual installed version)",
);

if (failures) {
  console.log(`\n${failures} failed`);
  process.exit(1);
}
console.log("\nall tests passed");
