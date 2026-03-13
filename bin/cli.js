#!/usr/bin/env node

/**
 * OneManCompany CLI — npx onemancompany
 *
 * Downloads (or updates) the repo, sets up Python env, and starts the server.
 */

const { execSync, spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Config ──────────────────────────────────────────────────────────────────
const REPO_URL = "https://github.com/CarbonKite/OneManCompany.git";
const DIR_NAME = "OneManCompany";
const DEFAULT_PORT = 8000;

// ── Colors ──────────────────────────────────────────────────────────────────
const cyan = (s) => `\x1b[1;36m${s}\x1b[0m`;
const yellow = (s) => `\x1b[1;33m${s}\x1b[0m`;
const red = (s) => `\x1b[1;31m${s}\x1b[0m`;
const green = (s) => `\x1b[1;32m${s}\x1b[0m`;

const info = (msg) => console.log(cyan(`▸ ${msg}`));
const warn = (msg) => console.log(yellow(`⚠ ${msg}`));
const fail = (msg) => {
  console.error(red(`✖ ${msg}`));
  process.exit(1);
};

// ── Helpers ─────────────────────────────────────────────────────────────────
function commandExists(cmd) {
  try {
    execSync(`command -v ${cmd}`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function run(cmd, opts = {}) {
  return execSync(cmd, { stdio: "inherit", ...opts });
}

function getPython() {
  for (const cmd of ["python3", "python"]) {
    if (commandExists(cmd)) {
      try {
        const ver = execSync(`${cmd} --version 2>&1`).toString().trim();
        const match = ver.match(/(\d+)\.(\d+)/);
        if (match && (parseInt(match[1]) > 3 || (parseInt(match[1]) === 3 && parseInt(match[2]) >= 12))) {
          return cmd;
        }
      } catch {}
    }
  }
  return null;
}

// ── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);

  // Help
  if (args.includes("--help") || args.includes("-h")) {
    console.log(`
${cyan("OneManCompany")} — The AI Operating System for One-Person Companies

${green("Usage:")}
  npx onemancompany              Start (clone + setup if first time)
  npx onemancompany init         Re-run setup wizard
  npx onemancompany --port 8080  Custom port
  npx onemancompany --dir ./my   Custom install directory

${green("Options:")}
  --dir <path>    Install directory (default: ./OneManCompany)
  --port <port>   Server port (default: 8000)
  --help, -h      Show this help
`);
    return;
  }

  console.log();
  console.log(cyan("╔══════════════════════════════════════════╗"));
  console.log(cyan("║     OneManCompany — AI Company OS        ║"));
  console.log(cyan("╚══════════════════════════════════════════╝"));
  console.log();

  // ── Check prerequisites ───────────────────────────────────────────────
  if (!commandExists("git")) {
    fail("git is required but not found. Please install git first.");
  }

  const python = getPython();
  if (!python) {
    fail("Python 3.12+ is required but not found.\nInstall from https://www.python.org/downloads/");
  }
  info(`Found ${execSync(`${python} --version`).toString().trim()}`);

  // ── Parse args ────────────────────────────────────────────────────────
  let installDir = path.resolve(process.cwd(), DIR_NAME);
  const passthrough = [];

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--dir" && args[i + 1]) {
      installDir = path.resolve(args[++i]);
    } else {
      passthrough.push(args[i]);
    }
  }

  // ── Clone or update ───────────────────────────────────────────────────
  if (fs.existsSync(path.join(installDir, ".git"))) {
    info(`Updating existing installation at ${installDir}`);
    try {
      run("git pull --ff-only", { cwd: installDir });
    } catch {
      warn("git pull failed — continuing with current version");
    }
  } else if (fs.existsSync(installDir)) {
    info(`Directory ${installDir} exists (not a git repo) — using as-is`);
  } else {
    info(`Cloning OneManCompany into ${installDir}...`);
    run(`git clone --depth 1 ${REPO_URL} "${installDir}"`);
  }

  // ── Hand off to start.sh ──────────────────────────────────────────────
  const startScript = path.join(installDir, "start.sh");
  if (!fs.existsSync(startScript)) {
    fail(`start.sh not found at ${startScript}`);
  }

  info("Launching OneManCompany...\n");

  const child = spawn("bash", [startScript, ...passthrough], {
    cwd: installDir,
    stdio: "inherit",
    env: { ...process.env, PYTHON: python },
  });

  child.on("close", (code) => process.exit(code ?? 0));
  child.on("error", (err) => fail(`Failed to start: ${err.message}`));
}

main();
