"""Employee onboarding — code-driven hire flow.

Standalone functions for creating employees, setting up profiles,
copying talent assets, generating nicknames, and registering agent loops.
Called by routes.py (talent market hire) and hr_agent.py (_apply_results).
"""

from __future__ import annotations

import importlib.util
import json as _json
import re
import shutil
import subprocess
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

import yaml

from loguru import logger

from onemancompany.core.config import (
    DEFAULT_TOOL_PERMISSIONS,
    DEFAULT_TOOL_PERMISSIONS_FALLBACK,
    DEFAULT_DEPARTMENT,
    HR_ID,
    ROLE_DEPARTMENT_MAP,
    TOOLS_DIR,
    EmployeeConfig,
    ensure_employee_dir,
    settings,
)
from onemancompany.core import store as _store
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.layout import (
    compute_layout,
    get_next_desk_for_department,
    persist_all_desk_positions,
)
from onemancompany.core.state import Employee, company_state, make_title


# ---------------------------------------------------------------------------
# Nickname generation
# ---------------------------------------------------------------------------

def _get_existing_nicknames() -> set[str]:
    """Collect all nicknames in use by current and ex-employees."""
    from onemancompany.core.store import load_all_employees, load_ex_employees
    nicknames: set[str] = set()
    for edata in load_all_employees().values():
        nn = edata.get("nickname", "")
        if nn:
            nicknames.add(nn)
    for edata in load_ex_employees().values():
        nn = edata.get("nickname", "")
        if nn:
            nicknames.add(nn)
    return nicknames


async def generate_nickname(name: str, role: str, is_founding: bool = False) -> str:
    """Generate a wuxia-themed Chinese nickname (花名) for an employee.

    Founding employees (level 4) get 3-character nicknames.
    Normal employees (level 1-3) get 2-character nicknames.
    All nicknames must be unique across all current and ex-employees.
    """
    from onemancompany.agents.base import make_llm, tracked_ainvoke

    char_count = 3 if is_founding else 2
    existing = _get_existing_nicknames()
    gen_llm = make_llm(HR_ID)

    for attempt in range(5):
        avoid_clause = ""
        if existing:
            sample = list(existing)[:20]
            avoid_clause = f"- MUST NOT be any of these existing nicknames: {', '.join(sample)}\n"

        gen_prompt = (
            f"You are a wuxia novelist naming a character.\n"
            f"Give a 花名 (nickname) for: {name}, role: {role}.\n\n"
            f"Requirements:\n"
            f"- Exactly {char_count} Chinese characters\n"
            f"- Must have a wuxia/martial arts/jianghu flavor — think swordsmen, heroes, legendary figures\n"
            f"- Should sound like a person's name or title in the jianghu, not an object\n"
            f"- Creative, memorable, and fitting for their role\n"
            f"- Reference style: 独孤求败, 风清扬, 令狐冲, 段誉, 黄蓉, 小龙女, 逍遥子, 天山童姥\n"
            f"- For {char_count}-char names: 铁面侠, 暖心侠, 玲珑阁, 金算盘, 逍遥子, 追风客\n"
            f"{avoid_clause}\n"
            f"Reply with ONLY the {char_count}-character 花名, nothing else."
        )
        result = await tracked_ainvoke(gen_llm, [
            SystemMessage(content="You are a wuxia novelist. Reply with ONLY the nickname."),
            HumanMessage(content=gen_prompt),
        ], category="nickname_gen", employee_id=HR_ID)
        nickname = result.content.strip()
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', nickname)
        if len(chinese_chars) >= char_count:
            candidate = ''.join(chinese_chars[:char_count])
        elif chinese_chars:
            candidate = ''.join(chinese_chars)
        else:
            continue

        if candidate not in existing:
            return candidate

    return ""


# ---------------------------------------------------------------------------
# Tool user registration (allowed_users in company/assets/tools/*/tool.yaml)
# ---------------------------------------------------------------------------

def _update_tool_allowed_users(tool_name: str, employee_id: str, *, add: bool) -> None:
    """Add or remove *employee_id* from a central tool's ``allowed_users`` list.

    Employee-brought tools are personal — only the owning employee may use them.
    This function maintains the whitelist in ``tool.yaml``.
    """
    tool_yaml = TOOLS_DIR / tool_name / "tool.yaml"
    if not tool_yaml.exists():
        return
    with open(tool_yaml) as f:
        data = yaml.safe_load(f) or {}
    allowed: list = data.get("allowed_users", [])
    if add:
        if employee_id not in allowed:
            allowed.append(employee_id)
    else:
        if employee_id in allowed:
            allowed.remove(employee_id)
    data["allowed_users"] = allowed
    with open(tool_yaml, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def register_tool_user(tool_name: str, employee_id: str) -> None:
    """Grant *employee_id* access to a central LangChain tool."""
    _update_tool_allowed_users(tool_name, employee_id, add=True)


def unregister_tool_user(tool_name: str, employee_id: str) -> None:
    """Revoke *employee_id*'s access to a central LangChain tool."""
    _update_tool_allowed_users(tool_name, employee_id, add=False)


# ---------------------------------------------------------------------------
# Talent function installation
# ---------------------------------------------------------------------------

def _validate_tool_module(py_path) -> bool:
    """Dry-run import a .py file and check it contains at least one BaseTool instance."""
    from langchain_core.tools import BaseTool

    try:
        spec = importlib.util.spec_from_file_location(
            f"_validate_{py_path.stem}", str(py_path)
        )
        if spec is None or spec.loader is None:
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for attr_name in dir(mod):
            if isinstance(getattr(mod, attr_name), BaseTool):
                return True
        logger.warning("No BaseTool instances found in %s", py_path)
        return False
    except Exception as exc:
        logger.warning("Failed to validate tool module %s: %s", py_path, exc)
        return False


def install_talent_functions(talent_dir: Path, emp_dir, employee_id: str) -> list[str]:
    """Install talent-brought functions into the central tool registry.

    Reads ``talent_dir/functions/manifest.yaml``, validates each
    declared .py module, copies it to ``company/assets/tools/{name}/``,
    generates ``tool.yaml``, and registers the employee as a user.

    Returns a list of successfully installed function names.
    """
    fn_dir = talent_dir / "functions"
    fn_manifest_path = fn_dir / "manifest.yaml"
    if not fn_manifest_path.exists():
        return []

    with open(fn_manifest_path) as f:
        raw = yaml.safe_load(f) or {}

    declarations = raw.get("functions", [])
    if not declarations:
        return []

    installed: list[str] = []
    for decl in declarations:
        name = decl.get("name", "")
        if not name:
            continue
        description = decl.get("description", "")
        scope = decl.get("scope", "personal")

        py_src = fn_dir / f"{name}.py"
        if not py_src.exists():
            logger.warning(
                "Function %s declared in %s but %s not found — skipping",
                name, fn_manifest_path, py_src,
            )
            continue

        # Validate the module contains at least one BaseTool
        if not _validate_tool_module(py_src):
            continue

        tool_dir = TOOLS_DIR / name

        if tool_dir.exists():
            # Tool already exists (e.g. another talent brought the same one).
            # Don't overwrite, but still register this employee as a user.
            logger.info(
                "Tool %s already exists in central registry — registering user only", name,
            )
        else:
            # Create central tool directory and copy the .py file
            tool_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(py_src), str(tool_dir / f"{name}.py"))

            # Generate tool.yaml
            tool_meta: dict = {
                "id": name,
                "name": name,
                "description": description,
                "type": "langchain_module",
                "added_by": f"talent:{talent_dir.name}",
                "source_talent": talent_dir.name,
            }
            if scope == "personal":
                tool_meta["allowed_users"] = [employee_id]
            # scope == "company" → omit allowed_users entirely → unrestricted

            with open(tool_dir / "tool.yaml", "w") as f:
                yaml.dump(tool_meta, f, default_flow_style=False, allow_unicode=True)

        # Ensure the bringing employee has access
        register_tool_user(name, employee_id)
        installed.append(name)

    return installed


# ---------------------------------------------------------------------------
# Agent config installation (agent/manifest.yaml)
# ---------------------------------------------------------------------------

def install_talent_agent_config(talent_dir: Path, emp_dir, employee_id: str) -> dict | None:
    """Install talent agent config (agent/ directory) into the employee folder.

    Copies the entire agent/ directory from the talent package to the employee
    directory, then validates runner and hooks modules if declared.

    Returns the parsed manifest dict on success, or None if no agent config exists.
    """
    agent_dir = talent_dir / "agent"
    manifest_path = agent_dir / "manifest.yaml"
    if not manifest_path.exists():
        return None

    # Copy agent/ directory to employee
    dst_agent_dir = Path(emp_dir) / "agent"
    if dst_agent_dir.exists():
        shutil.rmtree(str(dst_agent_dir))
    shutil.copytree(str(agent_dir), str(dst_agent_dir))

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f) or {}

    # Validate runner module if declared
    runner_cfg = manifest.get("runner", {})
    if runner_cfg:
        mod_name = runner_cfg.get("module", "")
        cls_name = runner_cfg.get("class", "")
        if mod_name and cls_name:
            runner_py = dst_agent_dir / f"{mod_name}.py"
            if runner_py.exists():
                try:
                    from onemancompany.agents.base import BaseAgentRunner
                    spec = importlib.util.spec_from_file_location(
                        f"_validate_runner_{employee_id}", str(runner_py)
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        runner_cls = getattr(mod, cls_name, None)
                        if runner_cls is None or not (
                            isinstance(runner_cls, type) and issubclass(runner_cls, BaseAgentRunner)
                        ):
                            logger.warning(
                                "Runner class %s in %s is not a BaseAgentRunner subclass",
                                cls_name, runner_py,
                            )
                except Exception as exc:
                    logger.warning("Failed to validate runner module %s: %s", runner_py, exc)

    # Validate hooks module if declared
    hooks_cfg = manifest.get("hooks", {})
    if hooks_cfg:
        hooks_mod_name = hooks_cfg.get("module", "")
        if hooks_mod_name:
            hooks_py = dst_agent_dir / f"{hooks_mod_name}.py"
            if hooks_py.exists():
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"_validate_hooks_{employee_id}", str(hooks_py)
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        for hook_key in ("pre_task", "post_task"):
                            fn_name = hooks_cfg.get(hook_key, "")
                            if fn_name:
                                fn = getattr(mod, fn_name, None)
                                if fn is None or not callable(fn):
                                    logger.warning(
                                        "Hook function %s not found or not callable in %s",
                                        fn_name, hooks_py,
                                    )
                except Exception as exc:
                    logger.warning("Failed to validate hooks module %s: %s", hooks_py, exc)

    logger.info("Installed agent config for employee %s from talent %s", employee_id, talent_dir.name)
    return manifest


def _create_agent_runner(employee_id: str, emp_dir) -> "BaseAgentRunner":
    """Create an agent runner for an employee, using custom runner if configured.

    Search order:
      1. emp_dir/vessel/vessel.yaml runner config
      2. emp_dir/agent/manifest.yaml runner config (backward compat)
      3. Default EmployeeAgent
    """
    from pathlib import Path
    from onemancompany.core.vessel_config import load_vessel_config

    emp_path = Path(emp_dir)
    config = load_vessel_config(emp_path)

    # Try vessel config first, then legacy agent/manifest.yaml
    mod_name = config.runner.module
    cls_name = config.runner.class_name

    if mod_name and cls_name:
        # Look for runner .py in vessel/ first, then agent/
        for search_dir in [emp_path / "vessel", emp_path / "agent"]:
            runner_py = search_dir / f"{mod_name}.py"
            if runner_py.exists():
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"emp_runner_{employee_id}_{mod_name}", str(runner_py)
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        runner_cls = getattr(mod, cls_name, None)
                        if runner_cls is not None:
                            logger.info(
                                "Using custom runner %s for employee %s", cls_name, employee_id,
                            )
                            return runner_cls(employee_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to load custom runner for %s: %s — falling back to default",
                        employee_id, exc,
                    )

    from onemancompany.agents.base import EmployeeAgent
    return EmployeeAgent(employee_id)


def _load_hooks_from_config(emp_dir) -> dict[str, "Callable"]:
    """Load hook functions from an employee's vessel or agent config.

    Search order:
      1. emp_dir/vessel/vessel.yaml hooks config
      2. emp_dir/agent/manifest.yaml hooks config (backward compat)

    Returns a dict with optional "pre_task" and "post_task" callable entries.
    """
    from pathlib import Path
    from onemancompany.core.vessel_config import load_vessel_config

    emp_path = Path(emp_dir)
    config = load_vessel_config(emp_path)

    hooks_mod_name = config.hooks.module
    if not hooks_mod_name:
        return {}

    # Look for hooks .py in vessel/ first, then agent/
    hooks_py = None
    for search_dir in [emp_path / "vessel", emp_path / "agent"]:
        candidate = search_dir / f"{hooks_mod_name}.py"
        if candidate.exists():
            hooks_py = candidate
            break

    if not hooks_py:
        return {}

    result: dict[str, "Callable"] = {}
    try:
        spec = importlib.util.spec_from_file_location(
            f"emp_hooks_{emp_path.name}_{hooks_mod_name}", str(hooks_py)
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for hook_key, fn_name in [("pre_task", config.hooks.pre_task), ("post_task", config.hooks.post_task)]:
                if fn_name:
                    fn = getattr(mod, fn_name, None)
                    if fn and callable(fn):
                        result[hook_key] = fn
    except Exception as exc:
        logger.warning("Failed to load hooks from %s: %s", hooks_py, exc)

    return result


def _register_employee_hooks(employee_id: str, emp_dir) -> None:
    """Load and register hooks for an employee if agent config exists."""
    hooks = _load_hooks_from_config(emp_dir)
    if hooks:
        from onemancompany.core.agent_loop import employee_manager
        employee_manager.register_hooks(employee_id, hooks)
        logger.info("Registered hooks for employee %s: %s", employee_id, list(hooks.keys()))


# ---------------------------------------------------------------------------
# Vessel config installation
# ---------------------------------------------------------------------------

def install_talent_vessel_config(talent_dir: Path, emp_dir, employee_id: str) -> None:
    """Install vessel config (vessel.yaml) into the employee folder.

    Search order:
      1. talent_dir/vessel/vessel.yaml → direct copy
      2. talent_dir/agent/manifest.yaml → convert to vessel.yaml
      3. Neither exists → use src/onemancompany/core/default_vessel.yaml

    Also copies vessel/ subdirectories (prompt_sections/, runner .py, hooks .py).
    """
    from onemancompany.core.vessel_config import (
        _convert_legacy_manifest, _load_default_vessel_config,
        save_vessel_config,
    )

    emp_path = Path(emp_dir)
    vessel_dir = emp_path / "vessel"

    # Already installed
    if (vessel_dir / "vessel.yaml").exists():
        return

    # 1. talent has vessel/vessel.yaml
    talent_vessel = talent_dir / "vessel"
    talent_vessel_yaml = talent_vessel / "vessel.yaml"
    if talent_vessel_yaml.exists():
        vessel_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(talent_vessel_yaml), str(vessel_dir / "vessel.yaml"))

        # Copy prompt_sections/
        ps_src = talent_vessel / "prompt_sections"
        if ps_src.exists() and ps_src.is_dir():
            ps_dst = vessel_dir / "prompt_sections"
            if not ps_dst.exists():
                shutil.copytree(str(ps_src), str(ps_dst))

        # Copy runner/hooks .py files
        with open(talent_vessel_yaml) as f:
            raw = yaml.safe_load(f) or {}
        for key in ("runner", "hooks"):
            mod = (raw.get(key) or {}).get("module", "")
            if mod:
                py_src = talent_vessel / f"{mod}.py"
                if py_src.exists():
                    py_dst = vessel_dir / f"{mod}.py"
                    if not py_dst.exists():
                        shutil.copy2(str(py_src), str(py_dst))
        return

    # 2. talent has agent/manifest.yaml → convert
    talent_manifest = talent_dir / "agent" / "manifest.yaml"
    if talent_manifest.exists():
        with open(talent_manifest) as f:
            manifest = yaml.safe_load(f) or {}
        config = _convert_legacy_manifest(manifest)
        save_vessel_config(emp_path, config)

        # Copy prompt_sections from agent/
        agent_ps = talent_dir / "agent" / "prompt_sections"
        if agent_ps.exists() and agent_ps.is_dir():
            ps_dst = vessel_dir / "prompt_sections"
            if not ps_dst.exists():
                shutil.copytree(str(agent_ps), str(ps_dst))
        return

    # 3. Use default
    config = _load_default_vessel_config()
    save_vessel_config(emp_path, config)


# ---------------------------------------------------------------------------
# Talent asset copying
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Talent directory resolution
# ---------------------------------------------------------------------------

# Legacy fallback: local talent_market package (will be removed once all
# hiring flows fetch talent data via Talent Market API/MCP)
from onemancompany.core.config import TALENTS_RUNTIME_DIR as _TALENTS_CLONE_DIR, TALENTS_DIR as _BUILTIN_TALENTS_DIR


def resolve_talent_dir(talent_id: str) -> Path | None:
    """Resolve a talent_id to a filesystem path.

    Searches talents/{id}/ first, then talents/{repo}/{id}/ for
    multi-talent repos cloned as a single directory.
    """
    if not talent_id:
        return None
    # Search runtime (cloned) first, then built-in
    for base in (_TALENTS_CLONE_DIR, _BUILTIN_TALENTS_DIR):
        candidate = base / talent_id
        if candidate.exists():
            return candidate
    return None


async def clone_talent_repo(repo_url: str, talent_id: str) -> Path:
    """Clone a talent repo and flatten sub-talent directories into talents/.

    A repo may contain multiple talents as subdirectories (each with profile.yaml).
    After cloning, those subdirectories are moved up to talents/{sub_id}/ and the
    repo wrapper is removed.

    Returns the local talent directory path for the requested talent_id.
    """
    import tempfile

    _TALENTS_CLONE_DIR.mkdir(parents=True, exist_ok=True)

    # Clone into a temp dir first to inspect structure
    tmp_clone = Path(tempfile.mkdtemp(prefix="talent_clone_"))
    try:
        subprocess.run(["git", "clone", repo_url, str(tmp_clone)], check=True)

        # Check if repo itself is a single talent (has profile.yaml at root)
        if (tmp_clone / "profile.yaml").exists():
            dest = _TALENTS_CLONE_DIR / talent_id
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(str(tmp_clone), str(dest), ignore=shutil.ignore_patterns(".git"))
        else:
            # Multi-talent repo: each subdir with profile.yaml is a talent
            for sub in tmp_clone.iterdir():
                if sub.is_dir() and (sub / "profile.yaml").exists():
                    dest = _TALENTS_CLONE_DIR / sub.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(str(sub), str(dest), ignore=shutil.ignore_patterns(".git"))
    finally:
        shutil.rmtree(tmp_clone, ignore_errors=True)

    resolved = _TALENTS_CLONE_DIR / talent_id
    return resolved if resolved.exists() else _TALENTS_CLONE_DIR


# ---------------------------------------------------------------------------
# Default skills injected for every new employee
# ---------------------------------------------------------------------------

# These are package-level resources, not tied to any specific talent
_DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "default_skills"
_DEFAULT_SKILL_NAMES = ["ontology", "proactive-agent", "self-improving-agent"]


def _inject_default_skills(skills_dir: Path) -> None:
    """Copy default skills into the employee's skills folder."""
    for name in _DEFAULT_SKILL_NAMES:
        src = _DEFAULT_SKILLS_DIR / name
        dst = skills_dir / name
        if src.exists() and not dst.exists():
            shutil.copytree(str(src), str(dst))


def copy_talent_assets(talent_dir: Path, emp_dir) -> None:
    """Copy skills/ and tools/ from a talent package into an employee folder.

    For custom LangChain tools (listed in manifest.yaml ``custom_tools``),
    registers the new employee in each tool's ``allowed_users`` whitelist
    instead of copying .py files.
    """
    if not talent_dir.exists():
        return

    talent_skills = talent_dir / "skills"
    if talent_skills.exists():
        emp_skills = emp_dir / "skills"
        emp_skills.mkdir(exist_ok=True)
        for entry in talent_skills.iterdir():
            if entry.is_dir() and (entry / "SKILL.md").exists():
                # Folder-based skill: copy entire folder
                dst_dir = emp_skills / entry.name
                if not dst_dir.exists():
                    shutil.copytree(str(entry), str(dst_dir))
            elif entry.is_file() and entry.suffix == ".md":
                # Legacy plain .md: convert to folder/SKILL.md
                dst_dir = emp_skills / entry.stem
                dst_dir.mkdir(exist_ok=True)
                dst_file = dst_dir / "SKILL.md"
                if not dst_file.exists():
                    shutil.copy2(str(entry), str(dst_file))

    talent_tools = talent_dir / "tools"
    if talent_tools.exists():
        emp_tools = emp_dir / "tools"
        emp_tools.mkdir(exist_ok=True)

        # Register employee in central tool allowed_users
        manifest = talent_tools / "manifest.yaml"
        if manifest.exists():
            with open(manifest) as f:
                mdata = yaml.safe_load(f) or {}
            employee_id = emp_dir.name  # e.g. "00005"
            for tool_name in mdata.get("custom_tools", []):
                register_tool_user(tool_name, employee_id)

        for src_file in talent_tools.iterdir():
            # Skip .py files — LangChain tool modules live centrally
            # in company/assets/tools/ and are loaded at runtime.
            if src_file.suffix == ".py":
                continue
            if src_file.is_file():
                dst_file = emp_tools / src_file.name
                if not dst_file.exists():
                    shutil.copy2(str(src_file), str(dst_file))

    # Copy talent persona (system_prompt_template → prompts/talent_persona.md)
    talent_profile_path = talent_dir / "profile.yaml"
    if talent_profile_path.exists():
        with open(talent_profile_path) as f:
            talent_data = yaml.safe_load(f) or {}
        spt = talent_data.get("system_prompt_template", "")
        if spt and spt.strip():
            prompts_dir = emp_dir / "prompts"
            prompts_dir.mkdir(exist_ok=True)
            (prompts_dir / "talent_persona.md").write_text(spt.strip() + "\n", encoding="utf-8")

    # Copy CLAUDE.md for Claude CLI discovery
    talent_claude_md = talent_dir / "CLAUDE.md"
    if talent_claude_md.exists():
        dst_claude_md = emp_dir / "CLAUDE.md"
        if not dst_claude_md.exists():
            shutil.copy2(str(talent_claude_md), str(dst_claude_md))

    # Copy manifest.json (frontend UI config — OAuth buttons, settings sections)
    talent_manifest_json = talent_dir / "manifest.json"
    if talent_manifest_json.exists():
        dst_manifest_json = emp_dir / "manifest.json"
        if not dst_manifest_json.exists():
            shutil.copy2(str(talent_manifest_json), str(dst_manifest_json))

    # Copy launch.sh / heartbeat.sh for self-hosted employees
    for script_name in ("launch.sh", "heartbeat.sh"):
        talent_script = talent_dir / script_name
        if talent_script.exists():
            dst_script = emp_dir / script_name
            if not dst_script.exists():
                shutil.copy2(str(talent_script), str(dst_script))
                dst_script.chmod(dst_script.stat().st_mode | 0o755)

    # Install agent config (agent/manifest.yaml + prompts, hooks, runner)
    install_talent_agent_config(talent_dir, emp_dir, emp_dir.name)

    # Install vessel config (vessel/vessel.yaml — uses default if talent has none)
    install_talent_vessel_config(talent_dir, emp_dir, emp_dir.name)

    # Install talent-brought functions into central registry
    employee_id = emp_dir.name
    installed = install_talent_functions(talent_dir, emp_dir, employee_id)
    if installed:
        # Append to employee's tools/manifest.yaml custom_tools
        emp_tools = emp_dir / "tools"
        emp_tools.mkdir(exist_ok=True)
        emp_manifest = emp_tools / "manifest.yaml"
        if emp_manifest.exists():
            with open(emp_manifest) as f:
                emp_mdata = yaml.safe_load(f) or {}
        else:
            emp_mdata = {"builtin_tools": [], "custom_tools": []}
        existing = emp_mdata.get("custom_tools", [])
        for fn in installed:
            if fn not in existing:
                existing.append(fn)
        emp_mdata["custom_tools"] = existing
        with open(emp_manifest, "w") as f:
            yaml.dump(emp_mdata, f, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Core hire execution
# ---------------------------------------------------------------------------

async def execute_hire(
    name: str,
    nickname: str,
    role: str,
    skills: list[str],
    *,
    talent_id: str = "",
    talent_dir: Path | None = None,
    llm_model: str = "",
    temperature: float = 0.7,
    image_model: str = "",
    api_provider: str = "openrouter",
    hosting: str = "company",
    auth_method: str = "api_key",
    sprite: str = "employee_default",
    remote: bool = False,
    department: str = "",
    progress_callback=None,  # async callable(step, message)
) -> Employee:
    """Execute the full hire flow in code — no LLM involved.

    Assigns employee number, department, desk position, permissions,
    creates profile, copies talent assets, generates work principles,
    and registers the agent loop.

    Args:
        talent_dir: Path to the talent directory (cloned from Talent Market).
            If None, no talent assets are copied.
        department: Explicit department override (from COO request).
            If empty, auto-determined from ROLE_DEPARTMENT_MAP.

    Returns the newly created Employee.
    """
    from onemancompany.core.model_costs import compute_salary

    # Resolve talent_dir from talent_id if not explicitly provided
    if talent_dir is None and talent_id:
        talent_dir = resolve_talent_dir(talent_id)

    # Use explicit department if provided (from COO), otherwise auto-assign
    if not department:
        department = ROLE_DEPARTMENT_MAP.get(role, DEFAULT_DEPARTMENT)

    # Desk position
    if remote:
        desk_pos = (-1, -1)
    else:
        desk_pos = get_next_desk_for_department(company_state, department)

    emp_num = company_state.next_employee_number()

    if progress_callback:
        await progress_callback("assigning_id", f"Assigned #{emp_num}")

    # Default permissions
    default_perms = ["company_file_access", "web_search"]
    default_tool_perms = list(DEFAULT_TOOL_PERMISSIONS.get(
        department, DEFAULT_TOOL_PERMISSIONS_FALLBACK
    ))

    # Salary
    if api_provider == "openrouter":
        salary = compute_salary(llm_model) if llm_model else 0.0
    else:
        salary = 0.0

    # Auto-generate nickname if not provided
    if not nickname:
        nickname = await generate_nickname(name, role, is_founding=False)

    emp = Employee(
        id=emp_num,
        name=name,
        nickname=nickname,
        level=1,
        department=department,
        role=role,
        skills=skills,
        employee_number=emp_num,
        desk_position=desk_pos,
        sprite=sprite,
        remote=remote,
        permissions=default_perms,
        tool_permissions=default_tool_perms,
        salary_per_1m_tokens=salary,
        probation=True,
        onboarding_completed=False,
    )
    # Persist profile via store (single source of truth)
    await _store.save_employee(emp_num, {
        "name": name,
        "nickname": nickname,
        "level": 1,
        "department": department,
        "role": role,
        "skills": skills,
        "employee_number": emp_num,
        "desk_position": list(desk_pos),
        "sprite": sprite,
        "remote": remote,
        "llm_model": llm_model,
        "temperature": temperature,
        "image_model": image_model,
        "permissions": default_perms,
        "tool_permissions": default_tool_perms,
        "salary_per_1m_tokens": salary,
        "api_provider": api_provider,
        "hosting": hosting,
        "auth_method": auth_method,
        "probation": True,
        "onboarding_completed": False,
    })
    await _store.save_employee_runtime(emp_num, status="idle")

    if progress_callback:
        await progress_callback("copying_skills", "Copying skill packages...")

    emp_dir = ensure_employee_dir(emp_num)
    skills_dir = emp_dir / "skills"

    # Connection config for remote and self-hosted employees
    if remote or hosting == "self":
        connection = {
            "employee_id": emp_num,
            "company_url": f"http://{settings.host}:{settings.port}",
            "talent_id": talent_id,
        }
        (emp_dir / "connection.json").write_text(
            _json.dumps(connection, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    # Copy talent skills + tools
    if talent_dir and talent_dir.exists() and not remote:
        copy_talent_assets(talent_dir, emp_dir)

    # Copy launch.sh for self-hosted employees
    if talent_dir and hosting == "self":
        talent_launch = talent_dir / "launch.sh"
        if talent_launch.exists():
            dst_launch = emp_dir / "launch.sh"
            if not dst_launch.exists():
                shutil.copy2(str(talent_launch), str(dst_launch))
                dst_launch.chmod(dst_launch.stat().st_mode | 0o111)  # ensure executable

    # Copy heartbeat.sh for employees with custom heartbeat scripts
    if talent_dir:
        talent_hb = talent_dir / "heartbeat.sh"
        if talent_hb.exists():
            dst_hb = emp_dir / "heartbeat.sh"
            if not dst_hb.exists():
                shutil.copy2(str(talent_hb), str(dst_hb))
                dst_hb.chmod(dst_hb.stat().st_mode | 0o111)

    # Create skill stubs (folder-based)
    for skill_name in skills:
        skill_dir = skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            skill_file.write_text(
                f"---\nname: {skill_name}\ndescription: \"{name}'s {skill_name} skill.\"\n---\n\n"
                f"# {skill_name}\n\n(Auto-created by HR during hiring.)\n",
                encoding="utf-8",
            )

    # Inject default skills (ontology, proactive-agent, self-improving-agent)
    _inject_default_skills(skills_dir)

    # Create initial SOUL.md in workspace
    workspace_dir = emp_dir / "workspace"
    workspace_dir.mkdir(exist_ok=True)
    soul_path = workspace_dir / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(
            f"# {name} ({nickname}) — Personal Knowledge\n\n"
            f"**Role**: {role}\n"
            f"**Department**: {department}\n\n"
            f"## Lessons Learned\n\n"
            f"(Will be updated automatically after each task.)\n",
            encoding="utf-8",
        )

    # Generate work principles as a skill (autoloaded)
    wp_dir = skills_dir / "work-principles"
    wp_dir.mkdir(parents=True, exist_ok=True)
    wp_file = wp_dir / "SKILL.md"
    if not wp_file.exists():
        wp_file.write_text(
            f"---\nname: work-principles\nautoload: true\n"
            f"description: Personal work principles and code of conduct.\n---\n\n"
            f"# {name} ({nickname}) Work Principles\n\n"
            f"**Department**: {department}\n"
            f"**Title**: {make_title(1, role)}\n"
            f"**Level**: Lv.1\n\n"
            f"## Core Principles\n"
            f"1. Complete assigned work diligently and maintain professional standards\n"
            f"2. Actively collaborate with the team and communicate progress promptly\n"
            f"3. Continuously learn and improve professional skills\n"
            f"4. Follow company rules and guidelines\n",
            encoding="utf-8",
        )

    # Generate standalone run.py for company-hosted employees
    if hosting == "company":
        from onemancompany.core.standalone_runner import generate_run_py
        generate_run_py(emp_dir, name, emp_num)

    # Recompute layout
    compute_layout(company_state)

    if progress_callback:
        await progress_callback("registering_agent", "Registering agent...")

    await _store.append_activity(
        {"type": "employee_hired", "name": name, "nickname": nickname, "role": role}
    )
    hired_data = _store.load_employee(emp_num)
    await event_bus.publish(CompanyEvent(type="employee_hired", payload=hired_data, agent="HR"))

    if progress_callback:
        await progress_callback("completed", f"{name} ({nickname}) onboarded as #{emp_num}")

    # Register in EmployeeManager (skip remote — they use remote task queue)
    if not remote:
        from onemancompany.core.agent_loop import get_agent_loop, register_and_start_agent, register_self_hosted
        if not get_agent_loop(emp_num):
            if hosting == "self":
                register_self_hosted(emp_num)
            elif (emp_dir / "launch.sh").exists():
                # Company-hosted with launch.sh → SubprocessExecutor
                from onemancompany.core.subprocess_executor import SubprocessExecutor
                from onemancompany.core.vessel import employee_manager
                _executor = SubprocessExecutor(emp_num, script_path=str(emp_dir / "launch.sh"))
                employee_manager.register(emp_num, _executor)
            else:
                agent_runner = _create_agent_runner(emp_num, emp_dir)
                await register_and_start_agent(emp_num, agent_runner)
                _register_employee_hooks(emp_num, emp_dir)

    # Trigger onboarding routine as background task
    import asyncio
    from onemancompany.core.routine import run_onboarding_routine
    asyncio.create_task(run_onboarding_routine(emp_num))

    return emp
