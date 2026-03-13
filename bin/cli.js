#!/usr/bin/env node

/**
 * OneManCompany CLI — npx @carbonkite/onemancompany
 *
 * Zero-prerequisites launcher. Automatically installs UV and Python if needed.
 * Works on Windows, macOS, and Linux.
 */

const { execSync, spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Config ──────────────────────────────────────────────────────────────────
const REPO_URL = "https://github.com/1mancompany/OneManCompany.git";
const DIR_NAME = "OneManCompany";
const PYTHON_VERSION = "3.12";

// ── Colors ──────────────────────────────────────────────────────────────────
const cyan = (s) => `\x1b[1;36m${s}\x1b[0m`;
const yellow = (s) => `\x1b[1;33m${s}\x1b[0m`;
const red = (s) => `\x1b[1;31m${s}\x1b[0m`;
const green = (s) => `\x1b[1;32m${s}\x1b[0m`;
const dim = (s) => `\x1b[2m${s}\x1b[0m`;

const info = (msg) => console.log(cyan(`▸ ${msg}`));
const warn = (msg) => console.log(yellow(`⚠ ${msg}`));
const fail = (msg) => {
  console.error(red(`✖ ${msg}`));
  process.exit(1);
};

const isWindows = os.platform() === "win32";

// ── Helpers ─────────────────────────────────────────────────────────────────
function commandExists(cmd) {
  try {
    if (isWindows) {
      execSync(`where ${cmd}`, { stdio: "ignore" });
    } else {
      execSync(`command -v ${cmd}`, { stdio: "ignore" });
    }
    return true;
  } catch {
    return false;
  }
}

function run(cmd, opts = {}) {
  return execSync(cmd, { stdio: "inherit", ...opts });
}

function runShell(cmd, opts = {}) {
  return execSync(cmd, { stdio: "inherit", shell: true, ...opts });
}

// ── UV installer ────────────────────────────────────────────────────────────
function ensureUV() {
  if (commandExists("uv")) {
    const ver = execSync("uv --version").toString().trim();
    info(`Found ${ver}`);
    return;
  }

  info("Installing UV (fast Python package manager)...");

  try {
    if (isWindows) {
      runShell("powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"");
    } else {
      runShell("curl -LsSf https://astral.sh/uv/install.sh | sh");
    }
  } catch (e) {
    fail(
      "Failed to install UV automatically.\n" +
      "Please install it manually: https://docs.astral.sh/uv/getting-started/installation/\n" +
      `Error: ${e.message}`
    );
  }

  // Add UV to PATH for the current session
  const home = os.homedir();
  const uvBinPaths = isWindows
    ? [path.join(home, ".cargo", "bin")]
    : [path.join(home, ".local", "bin"), path.join(home, ".cargo", "bin")];

  for (const p of uvBinPaths) {
    if (fs.existsSync(path.join(p, isWindows ? "uv.exe" : "uv"))) {
      process.env.PATH = `${p}${path.delimiter}${process.env.PATH}`;
      break;
    }
  }

  if (!commandExists("uv")) {
    fail(
      "UV was installed but not found in PATH.\n" +
      "Please restart your terminal and try again, or add UV to your PATH manually."
    );
  }

  info(`Installed ${execSync("uv --version").toString().trim()}`);
}

// ── Python via UV ───────────────────────────────────────────────────────────
function ensurePython() {
  // Check if UV-managed Python exists
  try {
    const ver = execSync(`uv python find ${PYTHON_VERSION} 2>&1`).toString().trim();
    if (ver) {
      info(`Found Python at ${ver}`);
      return;
    }
  } catch {}

  info(`Installing Python ${PYTHON_VERSION} via UV...`);
  try {
    runShell(`uv python install ${PYTHON_VERSION}`);
    info(`Python ${PYTHON_VERSION} installed`);
  } catch (e) {
    fail(`Failed to install Python ${PYTHON_VERSION}: ${e.message}`);
  }
}

// ── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);

  // Help
  if (args.includes("--help") || args.includes("-h")) {
    console.log(`
${cyan("Memento-OneManCompany")} — The AI Operating System for One-Person Companies

${green("Usage:")}
  npx @carbonkite/onemancompany              Start (auto-installs everything)
  npx @carbonkite/onemancompany init         Re-run setup wizard
  npx @carbonkite/onemancompany --port 8080  Custom port
  npx @carbonkite/onemancompany --dir ./my   Custom install directory

${green("Options:")}
  --dir <path>    Install directory (default: ./OneManCompany)
  --port <port>   Server port (default: 8000)
  --help, -h      Show this help

${green("What gets installed automatically:")}
  1. UV        — Fast Python package manager  ${dim("(https://astral.sh/uv)")}
  2. Python    — ${PYTHON_VERSION}+ via UV               ${dim("(managed, no system changes)")}
  3. Project   — Cloned from GitHub            ${dim("(into current directory)")}
`);
    return;
  }

  console.log();
  console.log(cyan("╔═══════════════════════════════════════════════╗"));
  console.log(cyan("║   Memento-OneManCompany — AI Company OS       ║"));
  console.log(cyan("╚═══════════════════════════════════════════════╝"));
  console.log();

  // ── Check git ─────────────────────────────────────────────────────────
  if (!commandExists("git")) {
    fail(
      "Git is required but not found.\n" +
      (isWindows
        ? "Install from https://git-scm.com/download/win"
        : os.platform() === "darwin"
          ? "Run: xcode-select --install"
          : "Run: sudo apt install git  (or your distro's equivalent)")
    );
  }

  // ── Install UV + Python ───────────────────────────────────────────────
  ensureUV();
  ensurePython();

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

  // ── Setup venv + deps via UV ──────────────────────────────────────────
  const venvDir = path.join(installDir, ".venv");
  if (!fs.existsSync(venvDir)) {
    info("Creating virtual environment...");
    runShell(`uv venv --python ${PYTHON_VERSION}`, { cwd: installDir });
  }

  info("Installing dependencies...");
  runShell("uv pip install -e .", { cwd: installDir });

  // ── Launch (directly via Python, all platforms) ────────────────────────
  const pythonBin = isWindows
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");

  if (!fs.existsSync(pythonBin)) {
    fail(`Python not found at ${pythonBin}. Try deleting .venv and running again.`);
  }

  const initComplete = fs.existsSync(path.join(installDir, ".onemancompany", ".env"))
    && fs.existsSync(path.join(installDir, ".onemancompany", "company", "human_resource", "employees"));

  // Run setup wizard if needed
  if (passthrough[0] === "init" || !initComplete) {
    info("Running setup wizard...\n");
    const initResult = spawnSync(pythonBin, ["-m", "onemancompany.onboard"], {
      cwd: installDir,
      stdio: "inherit",
    });
    if (initResult.status !== 0) fail("Setup wizard failed");
    if (passthrough[0] === "init") passthrough.shift();
  }

  // Start server
  info("Starting OneManCompany...\n");
  const child = spawn(pythonBin, ["-m", "onemancompany.main", ...passthrough], {
    cwd: installDir,
    stdio: "inherit",
  });
  child.on("close", (code) => process.exit(code ?? 0));
  child.on("error", (err) => fail(`Failed to start: ${err.message}`));
}

main();
