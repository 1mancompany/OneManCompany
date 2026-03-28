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
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_ROOT = Path(__file__).parent.parent.parent

from onemancompany.core.config import (
    COMPANY_TEMPLATE_DIR, CONFIG_YAML_FILENAME,
    DATA_DIR_NAME, DOT_ENV_FILENAME, EMPLOYEES_DIR,
    ENCODING_UTF8,
    ENV_KEY_ANTHROPIC, ENV_KEY_ANTHROPIC_AUTH, ENV_KEY_DEFAULT_MODEL,
    ENV_KEY_DEFAULT_PROVIDER, ENV_KEY_HOST, ENV_KEY_OPENROUTER,
    ENV_KEY_PORT, ENV_KEY_SANDBOX_ENABLED, ENV_KEY_SKILLSMP,
    ENV_KEY_TALENT_MARKET,
    ENV_OMC_EMPLOYEE_ID, ENV_OMC_PROJECT_DIR, ENV_OMC_PROJECT_ID,
    ENV_OMC_SERVER_URL, ENV_OMC_TASK_ID, HR_DIR, MCP_CONFIG_FILENAME,
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, TOOLS_DIR,
    WORKSPACE_DIR_NAME,
)
from onemancompany.core.models import AuthMethod
DATA_ROOT = Path.cwd() / DATA_DIR_NAME

# OpenRouter API response field names
OR_FIELD_ID = "id"
OR_FIELD_NAME = "name"
OR_FIELD_PRICING = "pricing"
OR_FIELD_PROMPT = "prompt"
OR_FIELD_COMPLETION = "completion"
OR_FIELD_CONTEXT_LENGTH = "context_length"
OR_FIELD_DATA = "data"

# Price display
PRICE_FREE = "free"
PRICE_NA = "N/A"

# Internal model dict keys
MODEL_KEY_ID = "id"
MODEL_KEY_NAME = "name"
MODEL_KEY_PROMPT_PRICE = "prompt_price"
MODEL_KEY_COMPLETION_PRICE = "completion_price"
MODEL_KEY_CONTEXT = "context"

# Table column headers
COL_NUM = "#"
COL_MODEL_ID = "Model ID"
COL_NAME = "Name"
COL_PROMPT = "Prompt"
COL_COMPLETION = "Completion"
COL_CONTEXT = "Context"
COL_PROVIDER = "Provider"
COL_AUTH_METHODS = "Auth Methods"

LOGO = r"""
 ╔═══════════════════════════════════════════════════╗
 ║                                                   ║
 ║   ██████╗ ███╗   ███╗ ██████╗                     ║
 ║  ██╔═══██╗████╗ ████║██╔════╝                     ║
 ║  ██║   ██║██╔████╔██║██║                          ║
 ║  ██║   ██║██║╚██╔╝██║██║                          ║
 ║  ╚██████╔╝██║ ╚═╝ ██║╚██████╗                    ║
 ║   ╚═════╝ ╚═╝     ╚═╝ ╚═════╝                    ║
 ║                                                   ║
 ║       O N E   M A N   C O M P A N Y              ║
 ║       ═══════════════════════════                  ║
 ║       [ NEURAL BOOTSTRAP SEQUENCE ]               ║
 ║                                                   ║
 ╚═══════════════════════════════════════════════════╝
"""

TOTAL_STEPS = 6

HOSTING_LABELS = {"company": "LangChain", "self": "Claude Code", "openclaw": "OpenClaw"}

# InquirerPy theme — cyberpunk neon
from InquirerPy.utils import InquirerPyStyle as _IStyle
INQ_STYLE = _IStyle({"questionmark": "#ff44cc", "pointer": "#00e5ff", "highlighted": "#00e5ff",
                      "input": "#39ff14", "answer": "#39ff14", "checkbox": "#39ff14"})

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PAGE_SIZE = 15

# Default model per provider (non-OpenRouter)
PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
    "deepseek": "deepseek-chat",
    "kimi": "moonshot-v1-8k",
    "qwen": "qwen-plus",
    "zhipu": "glm-4",
    "groq": "llama-3.3-70b-versatile",
    "together": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    "google": "gemini-2.0-flash",
    "minimax": "MiniMax-Text-01",
}


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------

def _print_step(console: Console, num: int, codename: str, subtitle: str) -> None:
    """Print a cyberpunk-styled step header with lightning decorations."""
    console.print()
    console.print(f"[bright_magenta]  ⚡┌─ STEP {num:02d}/{TOTAL_STEPS:02d} ─────────────────────────────────────⚡┐[/bright_magenta]")
    console.print(f"[bright_magenta]  ░ │[/bright_magenta] [bold bright_cyan]{codename}[/bold bright_cyan] [dim]// {subtitle}[/dim]")
    console.print(f"[bright_magenta]  ⚡└──────────────────────────────────────────────────⚡┘[/bright_magenta]")

def _step_welcome(console: Console) -> None:
    console.print(Panel(
        Text(LOGO, style="bold bright_cyan"),
        border_style="bright_magenta",
        padding=(0, 1),
    ))
    console.print()
    console.print("  [bold bright_green]⚡ INITIATING NEURAL BOOTSTRAP ⚡[/bold bright_green]")
    console.print("  [dim bright_cyan]░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░[/dim bright_cyan]")
    console.print()
    console.print("  [bright_white]Your AI company is about to come online.[/bright_white]")
    console.print("  [bright_white]In 60 seconds, a full executive team will be deployed.[/bright_white]")
    console.print()


def _format_price(price_str: str | None) -> str:
    """Format per-token price string to $/M tokens."""
    if not price_str:
        return PRICE_FREE
    try:
        per_token = float(price_str)
        per_million = per_token * 1_000_000
        if per_million == 0:
            return PRICE_FREE
        if per_million < 0.01:
            return f"${per_million:.4f}/M"
        return f"${per_million:.2f}/M"
    except (ValueError, TypeError):
        return PRICE_NA


def _fetch_openrouter_models(console: Console) -> list[dict]:
    """Fetch model list from OpenRouter API. Returns list of model dicts."""
    with console.status("  Fetching models from OpenRouter..."):
        try:
            resp = httpx.get(OPENROUTER_MODELS_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json().get(OR_FIELD_DATA, [])
        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] Failed to fetch models: {e}")
            return []

    models = []
    for m in data:
        model_id = m.get(OR_FIELD_ID, "")
        pricing = m.get(OR_FIELD_PRICING, {}) or {}
        models.append({
            MODEL_KEY_ID: model_id,
            MODEL_KEY_NAME: m.get(OR_FIELD_NAME, model_id),
            MODEL_KEY_PROMPT_PRICE: _format_price(pricing.get(OR_FIELD_PROMPT)),
            MODEL_KEY_COMPLETION_PRICE: _format_price(pricing.get(OR_FIELD_COMPLETION)),
            MODEL_KEY_CONTEXT: m.get(OR_FIELD_CONTEXT_LENGTH, 0),
        })

    models.sort(key=lambda m: m[MODEL_KEY_ID])
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
    table.add_column(COL_NUM, style="cyan", width=5)
    table.add_column(COL_MODEL_ID, min_width=35)
    table.add_column(COL_NAME, min_width=20)
    table.add_column(COL_PROMPT, justify="right", width=12)
    table.add_column(COL_COMPLETION, justify="right", width=12)
    table.add_column(COL_CONTEXT, justify="right", width=10)

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(models))
    for i in range(start, end):
        m = models[i]
        num = str(offset + i + 1)
        ctx = f"{m[MODEL_KEY_CONTEXT] // 1000}k" if m[MODEL_KEY_CONTEXT] else "—"
        table.add_row(num, m[MODEL_KEY_ID], m[MODEL_KEY_NAME], m[MODEL_KEY_PROMPT_PRICE], m[MODEL_KEY_COMPLETION_PRICE], ctx)

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
    """Interactive model selector with fuzzy search."""
    from InquirerPy import inquirer as _inq

    if not all_models:
        console.print("  [yellow]Could not load model list.[/yellow]")
        return _inq.text(
            message="Enter model ID (e.g. anthropic/claude-sonnet-4):",
            style=INQ_STYLE,
        ).execute().strip()

    # Build choices with pricing info
    choices = []
    for m in all_models:
        prompt_price = _format_price(m.get(MODEL_KEY_PROMPT_PRICE))
        comp_price = _format_price(m.get(MODEL_KEY_COMPLETION_PRICE))
        label = f"{m[MODEL_KEY_ID]}  [{prompt_price} / {comp_price}]"
        choices.append({"name": label, "value": m[MODEL_KEY_ID]})

    model = _inq.fuzzy(
        message="Select model (type to filter):",
        choices=choices,
        max_height="15",
        style=_IStyle({**INQ_STYLE.dict, "fuzzy_match": "#ff44cc"}),
    ).execute()

    console.print(f"  [bright_green]▸[/bright_green] Selected: [bold bright_cyan]{model}[/bold bright_cyan]")
    return model


def _step_llm(console: Console) -> tuple[str, str, str]:
    """Select provider, enter API key, choose model. Returns (provider, api_key, model)."""
    from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS
    from onemancompany.core.config import PROVIDER_REGISTRY

    _print_step(console, 1, "NEURAL CORE", "LLM Configuration")
    console.print(
        "\n  [dim]Select the neural substrate for your employees.[/dim]\n"
        "  [dim]Each agent's model can be reconfigured later via the web UI.[/dim]\n"
    )

    # 1. Select provider
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column(COL_NUM, style="cyan", width=4)
    table.add_column(COL_PROVIDER, min_width=20)
    table.add_column(COL_AUTH_METHODS, min_width=20)

    available_groups = [
        g for g in AUTH_CHOICE_GROUPS
        if any(c.available and c.auth_method == AuthMethod.API_KEY for c in g.choices)
    ]
    for i, group in enumerate(available_groups, 1):
        table.add_row(str(i), group.label, group.hint)

    console.print("  Select your LLM provider:\n")
    console.print(table)

    # Find OpenRouter's actual position in the filtered list
    or_num = next(
        (i for i, g in enumerate(available_groups, 1) if g.group_id == PROVIDER_OPENROUTER),
        None,
    )
    from InquirerPy import inquirer as _inq

    console.print()
    provider_choices = [
        {"name": f"{g.label}  ({g.hint})", "value": g.group_id}
        for g in available_groups
    ]
    or_default = PROVIDER_OPENROUTER if any(g.group_id == PROVIDER_OPENROUTER for g in available_groups) else available_groups[0].group_id

    provider = _inq.select(
        message="Select LLM provider:",
        choices=provider_choices,
        default=or_default,
        style=INQ_STYLE,
    ).execute()

    selected_group = next(g for g in available_groups if g.group_id == provider)
    console.print(f"\n  [bright_green]▸[/bright_green] Selected: [bold bright_cyan]{selected_group.label}[/bold bright_cyan]\n")

    # 2. Enter API key
    console.print(
        f"  [dim]Paste your {selected_group.label} API key below.[/dim]\n"
        f"  [dim]Input is hidden for security.[/dim]"
    )
    while True:
        api_key = _inq.secret(
            message=f"{selected_group.label} API Key:",
            style=INQ_STYLE,
        ).execute()
        if api_key.strip():
            break
        console.print("  [red]API key is required — your employees can't think without it.[/red]")

    # 3. Select model
    console.print()
    if provider == PROVIDER_OPENROUTER:
        all_models = _fetch_openrouter_models(console)
        if all_models:
            console.print(f"  [green]✔[/green] Found {len(all_models)} models")
        console.print(
            "  [dim]This only sets the [bold]default[/bold] model, used for company-level features.\n"
            "  Each employee can use a different LLM — configurable on the web UI.\n\n"
            "  How to pick a model:\n"
            "    • Type a [bold]number[/bold] and press [bold]Enter[/bold] to select\n"
            "    • Type a [bold]keyword[/bold] (e.g. \"claude\") to search\n"
            "    • Type [bold]n[/bold]/[bold]p[/bold] to go to next/previous page\n"
            "    • Type [bold]c[/bold] to enter a custom model ID[/dim]\n"
        )
        model = _select_model_interactive(console, all_models)
    else:
        # For non-OpenRouter providers, ask for model ID directly
        default_model = PROVIDER_DEFAULT_MODELS.get(provider, "")
        model = _inq.text(
            message=f"Model ID:",
            default=default_model,
            style=INQ_STYLE,
        ).execute().strip()

    return provider, api_key.strip(), model


def _step_server(console: Console) -> tuple[str, int]:
    console.print()
    _print_step(console, 2, "NETWORK NODE", "Server Configuration")
    console.print(
        "\n  [dim]Deploy your company node on the local network.[/dim]\n"
        "  [dim]After genesis, open the URL to enter your office.[/dim]\n"
    )
    console.print(
        f"  Default: [bold]http://0.0.0.0:8000[/bold]\n"
        f"  [dim]0.0.0.0 means accessible from any device on your network.\n"
        f"  Use 127.0.0.1 for local-only access.[/dim]\n"
    )

    from InquirerPy import inquirer as _inq
    console.print()
    use_defaults = _inq.confirm(
        message="Use default host/port (0.0.0.0:8000)?",
        default=True,
        style=INQ_STYLE,
    ).execute()
    if use_defaults:
        console.print("  [green]✔[/green] Using [bold]0.0.0.0:8000[/bold]\n")
        return "0.0.0.0", 8000

    host = _inq.text(
        message="Host:",
        default="0.0.0.0",
        style=INQ_STYLE,
    ).execute()
    port_str = _inq.text(
        message="Port:",
        default="8000",
        style=INQ_STYLE,
    ).execute()
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    return host, port


def _step_agent_family(console: Console) -> dict[str, str]:
    """Ask which agent families to enable and assign to each founding employee.

    Returns:
        Dict mapping employee_id → hosting value (company/self/openclaw).
    """
    from onemancompany.core.config import HR_ID, COO_ID, EA_ID, CSO_ID

    _print_step(console, 5, "VESSEL DEPLOY", "Agent Family Assignment")
    console.print(
        "\n  [dim]Each vessel carries an AI consciousness.[/dim]\n"
        "  [dim]Choose the neural architecture for your founding team.[/dim]\n"
    )

    # Show options with cyberpunk styling
    console.print("  [bright_cyan]╔══════╦═════════════════╦══════════════════════════════════════════╗[/bright_cyan]")
    console.print("  [bright_cyan]║[/bright_cyan] [bold] # [/bold] [bright_cyan]║[/bright_cyan] [bold]  Vessel Type  [/bold] [bright_cyan]║[/bright_cyan] [bold]  Neural Substrate                     [/bold] [bright_cyan]║[/bright_cyan]")
    console.print("  [bright_cyan]╠══════╬═════════════════╬══════════════════════════════════════════╣[/bright_cyan]")
    console.print("  [bright_cyan]║[/bright_cyan] [bright_green] 1 [/bright_green]  [bright_cyan]║[/bright_cyan] [bright_green]  LangChain    [/bright_green] [bright_cyan]║[/bright_cyan]  Built-in Python agent [dim](default)[/dim]      [bright_cyan]║[/bright_cyan]")
    console.print("  [bright_cyan]║[/bright_cyan] [bright_yellow] 2 [/bright_yellow]  [bright_cyan]║[/bright_cyan] [bright_yellow]  Claude Code  [/bright_yellow] [bright_cyan]║[/bright_cyan]  Claude CLI via MCP bridge              [bright_cyan]║[/bright_cyan]")
    console.print("  [bright_cyan]║[/bright_cyan] [bright_red] 3 [/bright_red]  [bright_cyan]║[/bright_cyan] [bright_red]  OpenClaw     [/bright_red] [bright_cyan]║[/bright_cyan]  OpenClaw gateway subprocess  [dim]🦞[/dim]       [bright_cyan]║[/bright_cyan]")
    console.print("  [bright_cyan]╚══════╩═════════════════╩══════════════════════════════════════════╝[/bright_cyan]")

    # Multi-select which families to enable (space to toggle, enter to confirm)
    from InquirerPy import inquirer as _inq

    console.print()
    console.print("  [dim]Use ↑↓ to navigate, Space to select, Enter to confirm[/dim]\n")

    family_choices = [
        {"name": "LangChain    — Built-in Python agent (default)", "value": "company", "enabled": True},
        {"name": "Claude Code  — Claude CLI via MCP bridge", "value": "self"},
        {"name": "OpenClaw     — OpenClaw gateway subprocess 🦞", "value": "openclaw"},
    ]
    selected = _inq.checkbox(
        message="Select agent families:",
        choices=family_choices,
        style=_IStyle({**INQ_STYLE.dict, "instruction": "#888"}),
        instruction="(Space=toggle, Enter=confirm)",
        validate=lambda result: len(result) > 0,
        invalid_message="Select at least one agent family.",
    ).execute()

    families_enabled = set(selected)
    family_labels = HOSTING_LABELS
    console.print(f"\n  [bright_green]▸ ENABLED:[/bright_green] [bold bright_cyan]{', '.join(family_labels[f] for f in sorted(families_enabled))}[/bold bright_cyan]\n")

    # If only one family enabled, assign all founders to it
    if len(families_enabled) == 1:
        only_family = next(iter(families_enabled))
        founders = {HR_ID: only_family, COO_ID: only_family, EA_ID: only_family, CSO_ID: only_family}
        console.print(f"\n  [bright_green]⚡ ALL VESSELS LOCKED TO[/bright_green] [bold bright_cyan]{family_labels[only_family]}[/bold bright_cyan] [bright_green]⚡[/bright_green]\n")
        return founders

    # Multiple families — ask per founder with list selector
    console.print()
    console.print("  [bright_magenta]⚡━━━ VESSEL ASSIGNMENT PROTOCOL ━━━⚡[/bright_magenta]")
    console.print("  [dim]Designate the neural architecture for each executive:[/dim]\n")

    founder_display = [
        (EA_ID, "Pat EA       // Executive Assistant"),
        (HR_ID, "Sam HR       // Human Resources"),
        (COO_ID, "Alex COO     // Chief Operating Officer"),
        (CSO_ID, "Morgan CSO   // Chief Sales Officer"),
    ]
    family_options = [{"name": family_labels[f], "value": f} for f in ["company", "self", "openclaw"] if f in families_enabled]

    founders: dict[str, str] = {}
    for emp_id, display_name in founder_display:
        hosting = _inq.select(
            message=f"{display_name}:",
            choices=family_options,
            default=family_options[0]["value"],
            style=INQ_STYLE,
        ).execute()
        founders[emp_id] = hosting
        console.print(f"    [bright_green]⚡ VESSEL LOCKED → {family_labels[hosting]}[/bright_green]")

    console.print()
    return founders


def _step_sandbox(console: Console) -> bool:
    """Ask whether to install sandbox tools (Docker-based code execution)."""
    console.print()
    _print_step(console, 3, "SANDBOX MESH", "Isolated Execution")
    console.print(
        "\n  [dim]Sandbox gives your AI employees a safe place to run code.\n"
        "  Without it, code execution happens directly on your machine.\n"
        "  With it, each task runs in an isolated Docker container.[/dim]\n"
    )
    console.print(
        "  [bold]Requirements:[/bold]\n"
        "    • [cyan]Docker[/cyan] — must be installed and running\n"
        "    • Python packages will be installed automatically\n"
        "  [dim]This is optional. You can always enable it later.[/dim]\n"
    )
    from InquirerPy import inquirer as _inq
    install = _inq.confirm(
        message="Install sandbox tools?",
        default=False,
        style=INQ_STYLE,
    ).execute()
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
    _print_step(console, 4, "UPLINK ARRAY", "External Integrations")
    console.print(
        "\n  [dim]These are all optional.\n"
        "  Paste a key and press [bold]Enter[/bold] to save it,\n"
        "  or just press [bold]Enter[/bold] to skip.\n"
        "  You can always add them later in [bold].onemancompany/.env[/bold][/dim]\n"
    )

    from InquirerPy import inquirer as _inq

    extras: dict[str, str] = {}

    # Anthropic API Key
    console.print(
        "  [bold]Anthropic API Key[/bold]\n"
        "  [dim]Needed for Claude Code execution mode. Skip to configure later.[/dim]"
    )
    key = _inq.secret(
        message="Anthropic API Key (Enter to skip):",
        style=INQ_STYLE,
        default="",
    ).execute()
    if key.strip():
        extras[ENV_KEY_ANTHROPIC] = key.strip()
        console.print("  [bright_green]▸[/bright_green] Saved")
    console.print()

    # SkillMarket API Key
    console.print(
        "  [bold]SkillMarket API Key[/bold]\n"
        "  [dim]Enables community skills for employees.[/dim]"
    )
    key = _inq.secret(
        message="SkillMarket API Key (Enter to skip):",
        style=INQ_STYLE,
        default="",
    ).execute()
    if key.strip():
        extras[ENV_KEY_SKILLSMP] = key.strip()
        console.print("  [bright_green]▸[/bright_green] Saved")
    console.print()

    # Talent Market API Key
    console.print(
        "  [bold bright_yellow]★ Recommended[/bold bright_yellow]  [bold]Talent Market API Key[/bold]\n"
        "  [dim]Lets HR hire AI employees from the marketplace.\n"
        "  Without this, only 4 founding executives available.\n"
        "  Register at[/dim] [link=https://one-man-company.com]one-man-company.com[/link]"
    )
    key = _inq.secret(
        message="Talent Market API Key (Enter to skip):",
        style=INQ_STYLE,
        default="",
    ).execute()
    if key.strip():
        extras[ENV_KEY_TALENT_MARKET] = key.strip()
        console.print("  [bright_green]▸[/bright_green] Saved")

    return extras


def _step_execute(
    console: Console,
    provider: str,
    api_key: str,
    model: str,
    host: str,
    port: int,
    extras: dict[str, str],
    sandbox_enabled: bool = False,
    founder_families: dict[str, str] | None = None,
) -> None:
    console.print()
    _print_step(console, 6, "GENESIS", "Company Initialization")
    console.print(
        "\n  [dim]Deploying company infrastructure and founding team...[/dim]\n"
    )

    # 1. Copy company/ template
    src_company = SOURCE_ROOT / COMPANY_TEMPLATE_DIR
    dst_company = DATA_ROOT / COMPANY_TEMPLATE_DIR
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
    from onemancompany.core.config import PROVIDER_REGISTRY
    prov_cfg = PROVIDER_REGISTRY.get(provider)
    env_key_name = prov_cfg.env_key.upper() if prov_cfg else f"{provider.upper()}_API_KEY"

    env_lines = [
        "# Generated by onemancompany-init",
        f"{ENV_KEY_DEFAULT_PROVIDER}={provider}",
        f"{env_key_name}={api_key}",
        f"{ENV_KEY_DEFAULT_MODEL}={model}",
        f"{ENV_KEY_HOST}={host}",
        f"{ENV_KEY_PORT}={port}",
    ]
    # Also write base_url for OpenRouter (needed by existing code)
    if provider == PROVIDER_OPENROUTER:
        env_lines.append("OPENROUTER_BASE_URL=https://openrouter.ai/api/v1")
    if ENV_KEY_ANTHROPIC in extras:
        env_lines.append(f"{ENV_KEY_ANTHROPIC}={extras[ENV_KEY_ANTHROPIC]}")
        env_lines.append(f"{ENV_KEY_ANTHROPIC_AUTH}={AuthMethod.API_KEY}")
    if ENV_KEY_SKILLSMP in extras:
        env_lines.append(f"{ENV_KEY_SKILLSMP}={extras[ENV_KEY_SKILLSMP]}")

    env_path = DATA_ROOT / DOT_ENV_FILENAME
    env_path.write_text("\n".join(env_lines) + "\n", encoding=ENCODING_UTF8)
    console.print("  [green]\u2714[/green] .env written")

    # 3. Copy config.yaml and inject Talent Market API key if provided
    src_config = SOURCE_ROOT / CONFIG_YAML_FILENAME
    dst_config = DATA_ROOT / CONFIG_YAML_FILENAME
    if src_config.exists() and not dst_config.exists():
        shutil.copy2(str(src_config), str(dst_config))
        console.print("  [green]\u2714[/green] config.yaml copied")
    # Patch config.yaml with user choices
    if dst_config.exists():
        import yaml
        cfg = yaml.safe_load(dst_config.read_text(encoding=ENCODING_UTF8)) or {}
        # Sandbox toggle
        cfg.setdefault("tools", {}).setdefault("sandbox", {})["enabled"] = sandbox_enabled
        # Talent Market API key
        tm_key = extras.get(ENV_KEY_TALENT_MARKET, "")
        if tm_key:
            cfg.setdefault("talent_market", {})["api_key"] = tm_key
        dst_config.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding=ENCODING_UTF8)
        if sandbox_enabled:
            console.print("  [green]\u2714[/green] Sandbox tools enabled")
        if tm_key:
            console.print("  [green]\u2714[/green] Talent Market API key saved")

    # 4. Sync founding employees' llm_model to user-selected default
    import yaml as _yaml
    from onemancompany.core.config import FOUNDING_IDS, EMPLOYEES_DIR
    _synced = 0
    for _fid in FOUNDING_IDS:
        _profile = EMPLOYEES_DIR / _fid / "profile.yaml"
        if _profile.exists():
            _pdata = _yaml.safe_load(_profile.read_text(encoding=ENCODING_UTF8)) or {}
            if _pdata.get("llm_model") != model:
                _pdata["llm_model"] = model
                _profile.write_text(_yaml.dump(_pdata, default_flow_style=False, allow_unicode=True), encoding=ENCODING_UTF8)
                _synced += 1
    if _synced:
        console.print(f"  [green]\u2714[/green] Founding employees model set to {model}")

    # 5. Assign random default avatars to founding employees
    _assign_default_avatars(console)

    # 6. Generate MCP configs for founding employees
    with console.status("  Generating MCP configs..."):
        _generate_mcp_configs(extras.get(ENV_KEY_SKILLSMP, ""))
    console.print("  [green]\u2714[/green] MCP configs generated for founding employees")

    # 7. Apply agent family (hosting) assignments to founding employees
    if founder_families:
        _apply_founder_families(console, founder_families)


def _apply_founder_families(console: Console, founder_families: dict[str, str]) -> None:
    """Set hosting mode in profile.yaml and install openclaw launch.sh if needed."""
    import subprocess
    import yaml as _yaml

    family_labels = HOSTING_LABELS
    needs_openclaw = any(v == "openclaw" for v in founder_families.values())

    # Install openclaw CLI if any founder uses it
    if needs_openclaw:
        with console.status("  [bright_cyan]Installing OpenClaw CLI...[/bright_cyan]"):
            try:
                result = subprocess.run(
                    ["npm", "install", "-g", "openclaw@latest"],
                    capture_output=True, timeout=120,
                )
                if result.returncode == 0:
                    console.print("  [bright_green]\u2714[/bright_green] OpenClaw CLI installed")
                else:
                    err = result.stderr.decode(errors="replace")[:200] if result.stderr else "unknown error"
                    console.print(f"  [yellow]\u26a0[/yellow] OpenClaw CLI install failed: {err}")
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                console.print(f"  [yellow]\u26a0[/yellow] OpenClaw CLI install skipped: {e}")

    # Apply per-founder hosting
    changed = 0
    for emp_id, hosting in founder_families.items():
        profile_path = EMPLOYEES_DIR / emp_id / "profile.yaml"
        if not profile_path.exists():
            continue
        data = _yaml.safe_load(profile_path.read_text(encoding=ENCODING_UTF8)) or {}
        if data.get("hosting") != hosting:
            data["hosting"] = hosting
            profile_path.write_text(
                _yaml.dump(data, default_flow_style=False, allow_unicode=True),
                encoding=ENCODING_UTF8,
            )
            changed += 1

    if changed:
        from onemancompany.core.config import HR_ID, COO_ID, EA_ID, CSO_ID
        _id_names = {EA_ID: "EA", HR_ID: "HR", COO_ID: "COO", CSO_ID: "CSO"}
        summary = ", ".join(
            f"{_id_names.get(eid, eid)}→{family_labels[h]}"
            for eid, h in sorted(founder_families.items())
        )
        console.print(f"  [bright_green]\u2714[/bright_green] Vessels deployed: {summary}")


def _assign_default_avatars(console: Console) -> None:
    """Assign random avatars from avatars/ to founding employees that lack one."""
    import random

    avatars_dir = HR_DIR / "avatars"
    if not avatars_dir.exists():
        return

    avatars = sorted(p for p in avatars_dir.iterdir() if p.suffix in (".png", ".jpg", ".jpeg"))
    if not avatars:
        return

    from onemancompany.core.config import FOUNDING_IDS
    exec_ids = sorted(FOUNDING_IDS)
    pool = list(avatars)
    random.shuffle(pool)

    assigned = 0
    for i, emp_id in enumerate(exec_ids):
        emp_dir = EMPLOYEES_DIR / emp_id
        if not emp_dir.exists():
            continue
        # Skip if already has a custom avatar
        if any((emp_dir / f"avatar.{ext}").exists() for ext in ("png", "jpg", "jpeg")):
            continue
        pick = pool[i % len(pool)]
        shutil.copy2(str(pick), str(emp_dir / f"avatar{pick.suffix}"))
        assigned += 1

    if assigned:
        console.print(f"  [green]\u2714[/green] Default avatars assigned to {assigned} founding employees")
    else:
        console.print("  [green]\u2714[/green] Founding employees already have avatars")


def _generate_mcp_configs(skillsmp_key: str) -> None:
    """Generate mcp_config.json for founding employees."""
    import sys

    python_path = sys.executable
    from onemancompany.core.config import EXEC_IDS
    exec_ids = sorted(EXEC_IDS)

    for emp_id in exec_ids:
        emp_dir = EMPLOYEES_DIR / emp_id
        if not emp_dir.exists():
            continue

        servers: dict = {
            "onemancompany": {
                "command": python_path,
                "args": ["-m", "onemancompany.tools.mcp.server"],
                "env": {
                    ENV_OMC_EMPLOYEE_ID: emp_id,
                    ENV_OMC_TASK_ID: "",
                    ENV_OMC_PROJECT_ID: "",
                    ENV_OMC_PROJECT_DIR: "",
                    ENV_OMC_SERVER_URL: "http://localhost:8000",
                },
            },
        }

        gmail_mcp = TOOLS_DIR / "gmail" / "mcp_server.py"
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
                    "--workdir", str(emp_dir / WORKSPACE_DIR_NAME),
                ],
                "env": {
                    ENV_KEY_SKILLSMP: skillsmp_key,
                },
            }

        config_path = emp_dir / MCP_CONFIG_FILENAME
        config_path.write_text(
            json.dumps({"mcpServers": servers}, indent=2),
            encoding=ENCODING_UTF8,
        )


def _step_done(console: Console, host: str, port: int) -> None:
    console.print()
    url = f"http://{'localhost' if host == '0.0.0.0' else host}:{port}"
    console.print(Panel(
        "[bold bright_green]"
        "  ╔═══════════════════════════════════════════╗\n"
        "  ║                                           ║\n"
        "  ║   ██████╗ ███████╗███╗   ██╗███████╗     ║\n"
        "  ║  ██╔════╝ ██╔════╝████╗  ██║██╔════╝     ║\n"
        "  ║  ██║  ███╗█████╗  ██╔██╗ ██║█████╗       ║\n"
        "  ║  ██║   ██║██╔══╝  ██║╚██╗██║██╔══╝       ║\n"
        "  ║  ╚██████╔╝███████╗██║ ╚████║███████╗     ║\n"
        "  ║   ╚═════╝ ╚══════╝╚═╝  ╚═══╝╚══════╝     ║\n"
        "  ║                                           ║\n"
        "  ║   G E N E S I S   C O M P L E T E        ║\n"
        "  ╚═══════════════════════════════════════════╝\n"
        "[/bold bright_green]\n"
        "  [bright_cyan]Your company is online. Neural cores activated.[/bright_cyan]\n\n"
        "  [bold]FOUNDING TEAM DEPLOYED:[/bold]\n"
        "  [bright_green]  ▸[/bright_green] Pat EA       [dim]// Executive Assistant — task routing & quality gate[/dim]\n"
        "  [bright_green]  ▸[/bright_green] Sam HR       [dim]// Human Resources — hiring & performance[/dim]\n"
        "  [bright_green]  ▸[/bright_green] Alex COO     [dim]// Chief Operating Officer — operations & dispatch[/dim]\n"
        "  [bright_green]  ▸[/bright_green] Morgan CSO   [dim]// Chief Sales Officer — sales & client relations[/dim]\n\n"
        "  [bold]NEXT PROTOCOL:[/bold]\n"
        f"  [bright_cyan]  01[/bright_cyan] Server boots automatically after this sequence\n"
        f"  [bright_cyan]  02[/bright_cyan] Access your office → [link={url}][bold]{url}[/bold][/link]\n"
        f"  [bright_cyan]  03[/bright_cyan] Issue your first directive to the team\n\n"
        "  [dim]> Example: \"Build me a puzzle game for mobile\"[/dim]\n\n"
        "  [dim]Need reinforcements? Configure Talent Market and tell HR to recruit.[/dim]",
        border_style="bright_magenta",
        padding=(1, 2),
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
        from InquirerPy import inquirer as _inq
        if not _inq.confirm(
            message="Reconfigure?",
            default=False,
            style=INQ_STYLE,
        ).execute():
            console.print("\n  Aborted. Existing configuration unchanged.")
            return

    provider, api_key, model = _step_llm(console)
    host, port = _step_server(console)
    sandbox_enabled = _step_sandbox(console)
    extras = _step_optional(console)
    founder_families = _step_agent_family(console)
    _step_execute(console, provider, api_key, model, host, port, extras,
                  sandbox_enabled=sandbox_enabled, founder_families=founder_families)
    _step_done(console, host, port)


def run_auto(*, skip_confirm: bool = False) -> None:
    """Non-interactive init that reads config from .env file."""
    import os

    console = Console()
    console.rule("[bold]OneManCompany Auto Init[/bold]")

    # Find .env — check CWD first, then project root
    env_path = Path.cwd() / DOT_ENV_FILENAME
    if not env_path.exists():
        env_path = SOURCE_ROOT / DOT_ENV_FILENAME
    if not env_path.exists():
        console.print("[red]  ✗ No .env file found. Run onemancompany-init interactively first.[/red]")
        raise SystemExit(1)

    # Parse .env
    env = {}
    for line in env_path.read_text(encoding=ENCODING_UTF8).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

    # Determine provider from env keys
    if env.get(ENV_KEY_OPENROUTER):
        provider = PROVIDER_OPENROUTER
        api_key = env[ENV_KEY_OPENROUTER]
    elif env.get(ENV_KEY_ANTHROPIC):
        provider = PROVIDER_ANTHROPIC
        api_key = env[ENV_KEY_ANTHROPIC]
    else:
        # Try to detect from DEFAULT_API_PROVIDER
        provider = env.get(ENV_KEY_DEFAULT_PROVIDER, PROVIDER_OPENROUTER)
        api_key = env.get(f"{provider.upper()}_API_KEY", "")

    model = env.get(ENV_KEY_DEFAULT_MODEL, "anthropic/claude-sonnet-4")
    host = env.get(ENV_KEY_HOST, "0.0.0.0")
    port = int(env.get(ENV_KEY_PORT, "8000"))

    extras: dict[str, str] = {}
    if env.get(ENV_KEY_ANTHROPIC):
        extras[ENV_KEY_ANTHROPIC] = env[ENV_KEY_ANTHROPIC]
    if env.get(ENV_KEY_SKILLSMP):
        extras[ENV_KEY_SKILLSMP] = env[ENV_KEY_SKILLSMP]
    if env.get(ENV_KEY_TALENT_MARKET):
        extras[ENV_KEY_TALENT_MARKET] = env[ENV_KEY_TALENT_MARKET]

    sandbox_enabled = env.get(ENV_KEY_SANDBOX_ENABLED, "").lower() in ("1", "true", "yes")

    masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "****"
    console.print(f"  Provider: [cyan]{provider}[/cyan]")
    console.print(f"  API Key:  [cyan]{masked}[/cyan]")
    console.print(f"  Model:    [cyan]{model}[/cyan]")
    console.print(f"  Server:   [cyan]{host}:{port}[/cyan]")
    console.print()

    if not skip_confirm:
        from InquirerPy import inquirer as _inq
        if not _inq.confirm(
            message="Proceed with auto-init?",
            default=False,
            style=INQ_STYLE,
        ).execute():
            console.print("\n  Aborted.")
            return

    _step_execute(console, provider, api_key, model, host, port, extras, sandbox_enabled=sandbox_enabled)
    _step_done(console, host, port)


def main() -> None:
    """CLI entry point for onemancompany-init."""
    import sys

    try:
        if "--auto" in sys.argv:
            run_auto(skip_confirm=("-y" in sys.argv or "--yes" in sys.argv))
        else:
            run_wizard()
    except KeyboardInterrupt:
        console = Console()
        console.print("\n\n  [yellow]Cancelled.[/yellow]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
