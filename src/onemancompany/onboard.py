"""TUI onboarding wizard for OneManCompany.

Run via `onemancompany-init` to bootstrap the .onemancompany/ data directory.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_ROOT = Path(__file__).parent.parent.parent
DATA_ROOT = Path.cwd() / ".onemancompany"

LOGO = r"""
   ___  __  __  ____
  / _ \|  \/  |/ ___|
 | | | | |\/| | |
 | |_| | |  | | |___
  \___/|_|  |_|\____|

  One Man Company
"""

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PAGE_SIZE = 15


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------

def _step_welcome(console: Console) -> None:
    console.print(Panel(
        Text(LOGO, style="bold cyan", justify="center"),
        title="Welcome",
        border_style="cyan",
    ))
    console.print(
        "This wizard will set up your [bold].onemancompany/[/bold] workspace.\n"
        "It takes about 30 seconds.\n"
    )


def _format_price(price_str: str | None) -> str:
    """Format per-token price string to $/M tokens."""
    if not price_str:
        return "free"
    try:
        per_token = float(price_str)
        per_million = per_token * 1_000_000
        if per_million == 0:
            return "free"
        if per_million < 0.01:
            return f"${per_million:.4f}/M"
        return f"${per_million:.2f}/M"
    except (ValueError, TypeError):
        return "N/A"


def _fetch_openrouter_models(console: Console) -> list[dict]:
    """Fetch model list from OpenRouter API. Returns list of model dicts."""
    with console.status("  Fetching models from OpenRouter..."):
        try:
            resp = httpx.get(OPENROUTER_MODELS_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] Failed to fetch models: {e}")
            return []

    models = []
    for m in data:
        model_id = m.get("id", "")
        pricing = m.get("pricing", {}) or {}
        models.append({
            "id": model_id,
            "name": m.get("name", model_id),
            "prompt_price": _format_price(pricing.get("prompt")),
            "completion_price": _format_price(pricing.get("completion")),
            "context": m.get("context_length", 0),
        })

    models.sort(key=lambda m: m["id"])
    return models


def _print_model_page(
    console: Console,
    models: list[dict],
    page: int,
    total_pages: int,
    offset: int = 0,
    search_term: str = "",
) -> None:
    """Print a page of models as a Rich table."""
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="cyan", width=5)
    table.add_column("Model ID", min_width=35)
    table.add_column("Name", min_width=20)
    table.add_column("Prompt", justify="right", width=12)
    table.add_column("Completion", justify="right", width=12)
    table.add_column("Context", justify="right", width=10)

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(models))
    for i in range(start, end):
        m = models[i]
        num = str(offset + i + 1)
        ctx = f"{m['context'] // 1000}k" if m["context"] else "—"
        table.add_row(num, m["id"], m["name"], m["prompt_price"], m["completion_price"], ctx)

    title = f"  Models (page {page + 1}/{total_pages})"
    if search_term:
        title += f"  —  filter: [yellow]{search_term}[/yellow]"
    console.print(title)
    console.print(table)
    console.print(
        "  [dim]Enter [cyan]number[/cyan] to select  |  "
        "type to [cyan]search[/cyan]  |  "
        "[cyan]n[/cyan]ext  [cyan]p[/cyan]rev  "
        "[cyan]a[/cyan]ll (reset filter)  "
        "[cyan]c[/cyan]ustom model ID[/dim]\n"
    )


def _select_model_interactive(console: Console, all_models: list[dict]) -> str:
    """Interactive model selector with search and pagination."""
    if not all_models:
        # Fallback if API unavailable
        console.print("  [yellow]Could not load model list.[/yellow]")
        return Prompt.ask("  Enter model ID (e.g. anthropic/claude-sonnet-4)", console=console).strip()

    filtered = all_models
    search_term = ""
    page = 0

    while True:
        total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages - 1)
        _print_model_page(console, filtered, page, total_pages, search_term=search_term)

        choice = Prompt.ask("  >", default="", console=console).strip()

        if not choice:
            continue

        # Navigation commands
        if choice.lower() == "n":
            if page < total_pages - 1:
                page += 1
            else:
                console.print("  [dim]Already on last page.[/dim]")
            continue
        if choice.lower() == "p":
            if page > 0:
                page -= 1
            else:
                console.print("  [dim]Already on first page.[/dim]")
            continue
        if choice.lower() == "a":
            filtered = all_models
            search_term = ""
            page = 0
            continue
        if choice.lower() == "c":
            return Prompt.ask("  Enter model ID", console=console).strip()

        # Number selection
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(filtered):
                selected = filtered[idx]
                console.print(f"  [green]✔[/green] Selected: [bold]{selected['id']}[/bold]")
                return selected["id"]
            console.print(f"  [red]Invalid number. Range: 1-{len(filtered)}[/red]")
            continue

        # Treat as search term
        search_term = choice.lower()
        filtered = [
            m for m in all_models
            if search_term in m["id"].lower() or search_term in m["name"].lower()
        ]
        page = 0
        if not filtered:
            console.print(f"  [yellow]No models matching '{choice}'. Showing all.[/yellow]")
            filtered = all_models
            search_term = ""


def _step_llm(console: Console) -> tuple[str, str]:
    console.rule("[bold]Step 1[/bold]  LLM Configuration")
    console.print()

    api_key = Prompt.ask(
        "  OpenRouter API Key",
        password=True,
        console=console,
    )
    while not api_key.strip():
        console.print("  [red]API key is required.[/red]")
        api_key = Prompt.ask("  OpenRouter API Key", password=True, console=console)

    console.print()
    all_models = _fetch_openrouter_models(console)
    if all_models:
        console.print(f"  [green]✔[/green] Found {len(all_models)} models")
    console.print(
        "  [dim]This only sets the [bold]default[/bold] model, used for company-level features\n"
        "  (e.g. polishing company direction, generating nicknames).\n"
        "  Each employee can use a different LLM — configurable on the web UI.[/dim]\n"
    )
    model = _select_model_interactive(console, all_models)

    return api_key.strip(), model


def _step_server(console: Console) -> tuple[str, int]:
    console.print()
    console.rule("[bold]Step 2[/bold]  Server Configuration")
    console.print()

    host = Prompt.ask("  Host", default="0.0.0.0", console=console)
    port_str = Prompt.ask("  Port", default="8000", console=console)
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    return host, port


def _step_sandbox(console: Console) -> bool:
    """Ask whether to install sandbox tools (Docker-based code execution)."""
    console.print()
    console.rule("[bold]Step 3[/bold]  Sandbox Tools")
    console.print(
        "\n  Sandbox provides isolated Docker containers for AI employees to\n"
        "  execute code, run commands, and manage files safely.\n"
    )
    console.print(
        "  [bold]Dependencies required:[/bold]\n"
        "    • [cyan]Docker[/cyan] — must be installed and running\n"
        "    • [cyan]opensandbox[/cyan] + [cyan]opensandbox-code-interpreter[/cyan] — Python packages\n"
        "      Install via: [dim]uv pip install 'onemancompany[sandbox]'[/dim]\n"
    )
    install = Confirm.ask("  Install sandbox tools?", default=False, console=console)
    if install:
        console.print()
        _install_sandbox_deps(console)
    return install


def _install_sandbox_deps(console: Console) -> None:
    """Attempt to install sandbox optional dependencies via uv/pip."""
    import subprocess
    import sys

    # Try uv first, fall back to pip
    venv_python = sys.executable
    cmds = [
        [venv_python, "-m", "uv", "pip", "install", "onemancompany[sandbox]"],
        [venv_python, "-m", "pip", "install", "onemancompany[sandbox]"],
    ]
    for cmd in cmds:
        try:
            with console.status("  Installing sandbox dependencies..."):
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=120,
                )
            if result.returncode == 0:
                console.print("  [green]✔[/green] Sandbox dependencies installed")
                return
        except FileNotFoundError:
            console.print(f"  [dim]{cmd[2]} not available, trying fallback...[/dim]")
        except subprocess.TimeoutExpired:
            console.print("  [yellow]⚠[/yellow] Installation timed out")

    console.print(
        "  [yellow]⚠[/yellow] Auto-install failed. Install manually:\n"
        "    [dim]uv pip install 'onemancompany[sandbox]'[/dim]"
    )


def _step_optional(console: Console) -> dict[str, str]:
    console.print()
    console.rule("[bold]Step 4[/bold]  Optional Configuration")
    console.print("  [dim]Press Enter to skip any key you don't have.[/dim]\n")

    extras: dict[str, str] = {}

    key = Prompt.ask("  Anthropic API Key", default="", password=True, console=console)
    if key.strip():
        extras["ANTHROPIC_API_KEY"] = key.strip()

    key = Prompt.ask("  FastSkills MCP Key", default="", password=True, console=console)
    if key.strip():
        extras["SKILLSMP_API_KEY"] = key.strip()

    console.print(
        "  [bold yellow]★ Recommended[/bold yellow] Talent Market — hire community-verified AI employees\n"
        "    Register at [link=https://carbonkites.com]https://carbonkites.com[/link] to get your API key"
    )
    key = Prompt.ask("  Talent Market API Key", default="", password=True, console=console)
    if key.strip():
        extras["TALENT_MARKET_API_KEY"] = key.strip()

    return extras


def _step_execute(
    console: Console,
    api_key: str,
    model: str,
    host: str,
    port: int,
    extras: dict[str, str],
    sandbox_enabled: bool = False,
) -> None:
    console.print()
    console.rule("[bold]Step 5[/bold]  Initializing")
    console.print()

    # 1. Copy company/ template
    src_company = SOURCE_ROOT / "company"
    dst_company = DATA_ROOT / "company"
    if src_company.exists() and not dst_company.exists():
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with console.status("  Copying company template..."):
            shutil.copytree(str(src_company), str(dst_company), symlinks=True)
        console.print("  [green]\u2714[/green] Company template copied")
    elif src_company.exists() and dst_company.exists():
        # Merge missing files/dirs from template into existing directory
        with console.status("  Checking company template completeness..."):
            patched = False
            for src_path in src_company.rglob("*"):
                rel = src_path.relative_to(src_company)
                dst_path = dst_company / rel
                if src_path.is_dir():
                    dst_path.mkdir(parents=True, exist_ok=True)
                elif not dst_path.exists():
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_path), str(dst_path))
                    patched = True
        if patched:
            console.print("  [green]\u2714[/green] Missing template files restored")
        else:
            console.print("  [green]\u2714[/green] Company directory complete")
    else:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        console.print("  [yellow]\u26a0[/yellow] No source company/ template found")

    # 2. Write .env
    env_lines = [
        "# Generated by onemancompany-init",
        f"OPENROUTER_API_KEY={api_key}",
        "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1",
        f"DEFAULT_LLM_MODEL={model}",
        f"HOST={host}",
        f"PORT={port}",
    ]
    if "ANTHROPIC_API_KEY" in extras:
        env_lines.append(f"ANTHROPIC_API_KEY={extras['ANTHROPIC_API_KEY']}")
        env_lines.append("ANTHROPIC_AUTH_METHOD=api_key")
    if "SKILLSMP_API_KEY" in extras:
        env_lines.append(f"SKILLSMP_API_KEY={extras['SKILLSMP_API_KEY']}")

    env_path = DATA_ROOT / ".env"
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    console.print("  [green]\u2714[/green] .env written")

    # 3. Copy config.yaml and inject Talent Market API key if provided
    src_config = SOURCE_ROOT / "config.yaml"
    dst_config = DATA_ROOT / "config.yaml"
    if src_config.exists() and not dst_config.exists():
        shutil.copy2(str(src_config), str(dst_config))
        console.print("  [green]\u2714[/green] config.yaml copied")
    # Patch config.yaml with user choices
    if dst_config.exists():
        import yaml
        cfg = yaml.safe_load(dst_config.read_text(encoding="utf-8")) or {}
        # Sandbox toggle
        cfg.setdefault("tools", {}).setdefault("sandbox", {})["enabled"] = sandbox_enabled
        # Talent Market API key
        tm_key = extras.get("TALENT_MARKET_API_KEY", "")
        if tm_key:
            cfg.setdefault("talent_market", {})["api_key"] = tm_key
        dst_config.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        if sandbox_enabled:
            console.print("  [green]\u2714[/green] Sandbox tools enabled")
        if tm_key:
            console.print("  [green]\u2714[/green] Talent Market API key saved")

    # 4. Generate MCP configs for founding employees
    with console.status("  Generating MCP configs..."):
        _generate_mcp_configs(extras.get("SKILLSMP_API_KEY", ""))
    console.print("  [green]\u2714[/green] MCP configs generated for founding employees")


def _generate_mcp_configs(skillsmp_key: str) -> None:
    """Generate mcp_config.json for founding employees."""
    import sys

    employees_dir = DATA_ROOT / "company" / "human_resource" / "employees"
    tools_dir = DATA_ROOT / "company" / "assets" / "tools"
    python_path = sys.executable
    exec_ids = ["00002", "00003", "00004", "00005"]

    for emp_id in exec_ids:
        emp_dir = employees_dir / emp_id
        if not emp_dir.exists():
            continue

        servers: dict = {
            "onemancompany": {
                "command": python_path,
                "args": ["-m", "onemancompany.tools.mcp.server"],
                "env": {
                    "OMC_EMPLOYEE_ID": emp_id,
                    "OMC_TASK_ID": "",
                    "OMC_PROJECT_ID": "",
                    "OMC_PROJECT_DIR": "",
                    "OMC_SERVER_URL": "http://localhost:8000",
                },
            },
        }

        gmail_mcp = tools_dir / "gmail" / "mcp_server.py"
        if gmail_mcp.exists():
            servers["gmail"] = {
                "command": python_path,
                "args": [str(gmail_mcp)],
            }

        if skillsmp_key:
            servers["fastskills"] = {
                "command": "uvx",
                "args": [
                    "fastskills",
                    "--skills-dir", str(emp_dir / "skills"),
                    "--workdir", str(emp_dir / "workspace"),
                ],
                "env": {
                    "SKILLSMP_API_KEY": skillsmp_key,
                },
            }

        config_path = emp_dir / "mcp_config.json"
        config_path.write_text(
            json.dumps({"mcpServers": servers}, indent=2),
            encoding="utf-8",
        )


def _step_done(console: Console, host: str, port: int) -> None:
    console.print()
    console.rule("[bold green]Done![/bold green]")
    console.print()
    console.print(Panel(
        f"  Workspace created at [bold].onemancompany/[/bold]\n\n"
        f"  Start the server:\n"
        f"    [cyan]onemancompany[/cyan]\n\n"
        f"  Then open [link=http://{host}:{port}]http://{host}:{port}[/link] in your browser.",
        title="Next Steps",
        border_style="green",
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_wizard() -> None:
    """Run the onboarding wizard."""
    console = Console()

    _step_welcome(console)

    # Check existing installation
    if DATA_ROOT.exists():
        console.print(
            f"[yellow]\u26a0[/yellow]  [bold].onemancompany/[/bold] already exists at\n"
            f"   {DATA_ROOT}\n"
        )
        if not Confirm.ask("  Reconfigure?", default=False, console=console):
            console.print("\n  Aborted. Existing configuration unchanged.")
            return

    api_key, model = _step_llm(console)
    host, port = _step_server(console)
    sandbox_enabled = _step_sandbox(console)
    extras = _step_optional(console)
    _step_execute(console, api_key, model, host, port, extras, sandbox_enabled=sandbox_enabled)
    _step_done(console, host, port)


def main() -> None:
    """CLI entry point for onemancompany-init."""
    try:
        run_wizard()
    except KeyboardInterrupt:
        console = Console()
        console.print("\n\n  [yellow]Cancelled.[/yellow]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
