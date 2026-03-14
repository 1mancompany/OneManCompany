#!/usr/bin/env node

/**
 * OneManCompany CLI — npx @1mancompany/onemancompany
 *
 * Zero-prerequisites launcher. Automatically installs UV and Python if needed.
 * Works on Windows, macOS, and Linux.
 */

const { execSync, spawn, spawnSync } = require("child_process");
const readline = require("readline");
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
const PID_FILE = ".onemancompany.pid";

// ── Helpers ─────────────────────────────────────────────────────────────────

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim().toLowerCase());
    });
  });
}

function findInstallDir() {
  const dirFromArgs = (() => {
    const args = process.argv.slice(2);
    for (let i = 0; i < args.length; i++) {
      if (args[i] === "--dir" && args[i + 1]) return path.resolve(args[i + 1]);
    }
    return null;
  })();
  return dirFromArgs || path.resolve(process.cwd(), DIR_NAME);
}

function writePidFile(installDir, pid) {
  fs.writeFileSync(path.join(installDir, PID_FILE), String(pid));
}

function readPidFile(installDir) {
  const pidPath = path.join(installDir, PID_FILE);
  if (!fs.existsSync(pidPath)) return null;
  const pid = parseInt(fs.readFileSync(pidPath, "utf-8").trim(), 10);
  return isNaN(pid) ? null : pid;
}

function removePidFile(installDir) {
  const pidPath = path.join(installDir, PID_FILE);
  if (fs.existsSync(pidPath)) fs.unlinkSync(pidPath);
}

function isProcessRunning(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function stopService(installDir) {
  const pid = readPidFile(installDir);
  if (pid && isProcessRunning(pid)) {
    info(`Stopping OneManCompany service (PID ${pid})...`);
    try {
      process.kill(pid, "SIGTERM");
      // Wait up to 5s for graceful shutdown
      for (let i = 0; i < 50; i++) {
        if (!isProcessRunning(pid)) break;
        spawnSync("sleep", ["0.1"]);
      }
      if (isProcessRunning(pid)) {
        process.kill(pid, "SIGKILL");
      }
      info("Service stopped");
    } catch {
      warn("Could not stop service — it may have already exited");
    }
  }
  removePidFile(installDir);
}
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
async function main() {
  const args = process.argv.slice(2);

  // Help
  if (args.includes("--help") || args.includes("-h")) {
    console.log(`
${cyan("OneManCompany")} — The AI Operating System for One-Person Companies

${green("Usage:")}
  npx @1mancompany/onemancompany              Start (runs in background)
  npx @1mancompany/onemancompany --debug      Start with logs (Ctrl+C to stop)
  npx @1mancompany/onemancompany stop         Stop background service
  npx @1mancompany/onemancompany init         Re-run setup process
  npx @1mancompany/onemancompany uninstall    Stop service and remove installation
  npx @1mancompany/onemancompany --port 8080  Custom port
  npx @1mancompany/onemancompany --dir ./my   Custom install directory

${green("Options:")}
  --dir <path>    Install directory (default: ./OneManCompany)
  --port <port>   Server port (default: 8000)
  --debug         Run in foreground with logs (default: background)
  --help, -h      Show this help

${green("What gets installed automatically:")}
  1. UV        — Fast Python package manager  ${dim("(https://astral.sh/uv)")}
  2. Python    — ${PYTHON_VERSION}+ via UV               ${dim("(managed, no system changes)")}
  3. Project   — Cloned from GitHub            ${dim("(into current directory)")}
`);
    return;
  }

  // ── Uninstall ─────────────────────────────────────────────────────────
  if (args[0] === "uninstall") {
    const installDir = findInstallDir();
    if (!fs.existsSync(installDir)) {
      warn(`No installation found at ${installDir}`);
      return;
    }

    const answer = await ask(
      yellow("⚠") + `  This will stop the service and delete ${installDir}\n` +
      "  Are you sure? [y/N] "
    );
    if (answer !== "y" && answer !== "yes") {
      console.log("  Aborted.");
      return;
    }

    stopService(installDir);

    info(`Removing ${installDir}...`);
    fs.rmSync(installDir, { recursive: true, force: true });
    info("OneManCompany has been uninstalled.");
    console.log(dim(`  To reinstall: npx @1mancompany/onemancompany`));
    return;
  }

  // ── Stop ────────────────────────────────────────────────────────────
  if (args[0] === "stop") {
    const installDir = findInstallDir();
    const pid = readPidFile(installDir);
    if (pid && isProcessRunning(pid)) {
      stopService(installDir);
      console.log(green("  ✓ OneManCompany stopped."));
    } else {
      warn("No running OneManCompany service found.");
      removePidFile(installDir);
    }
    return;
  }

  console.log();
  console.log(cyan("╔═══════════════════════════════════════════════╗"));
  console.log(cyan("║   OneManCompany — AI Company OS       ║"));
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

  // ── Check if already running ─────────────────────────────────────────
  const existingPid = readPidFile(installDir);
  if (existingPid && isProcessRunning(existingPid)) {
    warn("OneManCompany is already running.");
    const answer = await ask(
      "  Stop the service and re-setup? [y/N] "
    );
    if (answer === "y" || answer === "yes") {
      stopService(installDir);
      info("Re-running setup process...\n");
      const pythonBinCheck = isWindows
        ? path.join(installDir, ".venv", "Scripts", "python.exe")
        : path.join(installDir, ".venv", "bin", "python");
      if (fs.existsSync(pythonBinCheck)) {
        const initResult = spawnSync(pythonBinCheck, ["-m", "onemancompany.onboard"], {
          cwd: installDir,
          stdio: "inherit",
        });
        if (initResult.status !== 0) fail("Setup wizard failed");
      }
    } else {
      console.log("  Continuing with existing service.");
      return;
    }
  }

  // ── Setup venv + deps via UV ──────────────────────────────────────────
  const venvDir = path.join(installDir, ".venv");
  if (!fs.existsSync(venvDir)) {
    info("Creating virtual environment...");
    runShell(`uv venv --python ${PYTHON_VERSION}`, { cwd: installDir });
  }

  info("Installing dependencies...");
  runShell(`uv pip install -e . -p "${path.join(venvDir, isWindows ? "Scripts/python.exe" : "bin/python")}"`, { cwd: installDir });

  // ── Launch (directly via Python, all platforms) ────────────────────────
  const pythonBin = isWindows
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");

  if (!fs.existsSync(pythonBin)) {
    fail(`Python not found at ${pythonBin}. Try deleting .venv and running again.`);
  }

  const initComplete = fs.existsSync(path.join(installDir, ".onemancompany", ".env"))
    && fs.existsSync(path.join(installDir, ".onemancompany", "company", "human_resource", "employees"));

  // Run setup process if needed
  if (passthrough[0] === "init" || !initComplete) {
    info("Running setup process...\n");
    const initResult = spawnSync(pythonBin, ["-m", "onemancompany.onboard"], {
      cwd: installDir,
      stdio: "inherit",
    });
    if (initResult.status !== 0) fail("Setup wizard failed");
    if (passthrough[0] === "init") passthrough.shift();
  }

  // Start server
  const debugMode = passthrough.includes("--debug");
  const launchArgs = passthrough.filter((a) => a !== "--debug");

  if (debugMode) {
    // ── Foreground mode: show logs, Ctrl+C to kill ──────────────────
    info("Starting OneManCompany in debug mode (Ctrl+C to stop)...\n");
    const child = spawn(pythonBin, ["-m", "onemancompany.main", ...launchArgs], {
      cwd: installDir,
      stdio: "inherit",
    });

    writePidFile(installDir, child.pid);

    const cleanup = () => { removePidFile(installDir); };
    child.on("close", (code) => { cleanup(); process.exit(code ?? 0); });
    child.on("error", (err) => { cleanup(); fail(`Failed to start: ${err.message}`); });
    process.on("SIGINT", () => { child.kill("SIGTERM"); });
    process.on("SIGTERM", () => { child.kill("SIGTERM"); });
  } else {
    // ── Background mode: detach and exit CLI ────────────────────────
    info("Starting OneManCompany in background...");
    const logFile = path.join(installDir, ".onemancompany", "server.log");
    // Ensure log directory exists
    const logDir = path.dirname(logFile);
    if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });

    const out = fs.openSync(logFile, "a");
    const err = fs.openSync(logFile, "a");

    const child = spawn(pythonBin, ["-m", "onemancompany.main", ...launchArgs], {
      cwd: installDir,
      stdio: ["ignore", out, err],
      detached: true,
    });

    writePidFile(installDir, child.pid);
    child.unref();

    // Wait a moment to check it didn't crash immediately
    await new Promise((r) => setTimeout(r, 1500));
    if (isProcessRunning(child.pid)) {
      console.log();
      console.log(green("  ✓ OneManCompany is running!"));
      console.log();
      console.log(`  ${cyan("→")} Open ${cyan("http://localhost:8000")} in your browser`);
      console.log(`  ${dim("  Logs:")} ${logFile}`);
      console.log(`  ${dim("  Stop:")} npx @1mancompany/onemancompany stop`);
      console.log(`  ${dim("  Debug:")} npx @1mancompany/onemancompany --debug`);
      console.log();
    } else {
      removePidFile(installDir);
      fail("Server exited unexpectedly. Run with --debug to see logs.");
    }
  }
}

main().catch((err) => {
  console.error(red(`✖ ${err.message}`));
  process.exit(1);
});
