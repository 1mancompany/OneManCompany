"""Vessel DNA — vessel.yaml 配置定义与加载。

VesselConfig 定义了一个员工躯壳(vessel)的完整 DNA：
- runner: 神经系统配置（如何连接执行后端）
- hooks: 生命周期钩子
- context: 上下文注入配置
- limits: 执行限制
- capabilities: 能力声明

加载优先级:
  1. emp_dir/vessel/vessel.yaml
  2. emp_dir/agent/manifest.yaml (向后兼容，自动转换)
  3. src/onemancompany/core/default_vessel.yaml (默认 DNA)
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RunnerConfig:
    """神经系统配置 — 定义如何连接执行后端。"""
    module: str = ""
    class_name: str = ""


@dataclass
class HooksConfig:
    """生命周期钩子 — 任务前后的回调。"""
    module: str = ""
    pre_task: str = ""
    post_task: str = ""


@dataclass
class PromptSection:
    """Prompt 注入片段。"""
    name: str
    file: str = ""
    priority: int = 50


@dataclass
class ContextConfig:
    """上下文注入配置。"""
    prompt_sections: list[PromptSection] = field(default_factory=list)
    inject_progress_log: bool = True
    inject_task_history: bool = True


@dataclass
class LimitsConfig:
    """执行限制。"""
    max_retries: int = 3
    retry_delays: list[int] = field(default_factory=lambda: [5, 15, 30])
    max_subtask_iterations: int = 3
    max_subtask_depth: int = 2
    task_timeout_seconds: int = 600


@dataclass
class CapabilitiesConfig:
    """能力声明 — vessel 支持的平台能力。"""
    file_upload: bool = False
    websocket: bool = False
    sandbox: bool = True
    image_generation: bool = False


@dataclass
class VesselConfig:
    """vessel.yaml 完整结构 — 员工躯壳的 DNA。"""
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    capabilities: CapabilitiesConfig = field(default_factory=CapabilitiesConfig)


# ---------------------------------------------------------------------------
# Default config path
# ---------------------------------------------------------------------------

_DEFAULT_VESSEL_YAML = Path(__file__).parent / "default_vessel.yaml"


# ---------------------------------------------------------------------------
# Loading & saving
# ---------------------------------------------------------------------------

def _parse_vessel_dict(raw: dict) -> VesselConfig:
    """Parse a raw dict (from YAML) into a VesselConfig."""
    runner_raw = raw.get("runner", {}) or {}
    hooks_raw = raw.get("hooks", {}) or {}
    context_raw = raw.get("context", {}) or {}
    limits_raw = raw.get("limits", {}) or {}
    caps_raw = raw.get("capabilities", {}) or {}

    prompt_sections = []
    for ps in (context_raw.get("prompt_sections") or []):
        prompt_sections.append(PromptSection(
            name=ps.get("name", ""),
            file=ps.get("file", ""),
            priority=ps.get("priority", 50),
        ))

    return VesselConfig(
        runner=RunnerConfig(
            module=runner_raw.get("module", ""),
            class_name=runner_raw.get("class_name", "") or runner_raw.get("class", ""),
        ),
        hooks=HooksConfig(
            module=hooks_raw.get("module", ""),
            pre_task=hooks_raw.get("pre_task", ""),
            post_task=hooks_raw.get("post_task", ""),
        ),
        context=ContextConfig(
            prompt_sections=prompt_sections,
            inject_progress_log=context_raw.get("inject_progress_log", True),
            inject_task_history=context_raw.get("inject_task_history", True),
        ),
        limits=LimitsConfig(
            max_retries=limits_raw.get("max_retries", 3),
            retry_delays=limits_raw.get("retry_delays", [5, 15, 30]),
            max_subtask_iterations=limits_raw.get("max_subtask_iterations", 3),
            max_subtask_depth=limits_raw.get("max_subtask_depth", 2),
            task_timeout_seconds=limits_raw.get("task_timeout_seconds", 600),
        ),
        capabilities=CapabilitiesConfig(
            file_upload=caps_raw.get("file_upload", False),
            websocket=caps_raw.get("websocket", False),
            sandbox=caps_raw.get("sandbox", True),
            image_generation=caps_raw.get("image_generation", False),
        ),
    )


def _load_default_vessel_config() -> VesselConfig:
    """Load the default vessel config from src/onemancompany/core/default_vessel.yaml."""
    if _DEFAULT_VESSEL_YAML.exists():
        with open(_DEFAULT_VESSEL_YAML) as f:
            raw = yaml.safe_load(f) or {}
        return _parse_vessel_dict(raw)
    return VesselConfig()


def load_vessel_config(emp_dir: Path) -> VesselConfig:
    """Load VesselConfig for an employee directory.

    Search order:
      1. emp_dir/vessel/vessel.yaml
      2. emp_dir/agent/manifest.yaml (legacy, auto-converted)
      3. default_vessel.yaml (built-in default)
    """
    # 1. vessel/vessel.yaml
    vessel_yaml = emp_dir / "vessel" / "vessel.yaml"
    if vessel_yaml.exists():
        try:
            with open(vessel_yaml) as f:
                raw = yaml.safe_load(f) or {}
            return _parse_vessel_dict(raw)
        except yaml.YAMLError:
            return _load_default_vessel_config()

    # 2. agent/manifest.yaml (legacy fallback)
    manifest_yaml = emp_dir / "agent" / "manifest.yaml"
    if manifest_yaml.exists():
        try:
            with open(manifest_yaml) as f:
                manifest = yaml.safe_load(f) or {}
            return _convert_legacy_manifest(manifest)
        except yaml.YAMLError:
            return _load_default_vessel_config()

    # 3. Default
    return _load_default_vessel_config()


def save_vessel_config(emp_dir: Path, config: VesselConfig) -> None:
    """Write VesselConfig to emp_dir/vessel/vessel.yaml."""
    vessel_dir = emp_dir / "vessel"
    vessel_dir.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "runner": {
            "module": config.runner.module,
            "class_name": config.runner.class_name,
        },
        "hooks": {
            "module": config.hooks.module,
            "pre_task": config.hooks.pre_task,
            "post_task": config.hooks.post_task,
        },
        "context": {
            "prompt_sections": [
                {"name": ps.name, "file": ps.file, "priority": ps.priority}
                for ps in config.context.prompt_sections
            ],
            "inject_progress_log": config.context.inject_progress_log,
            "inject_task_history": config.context.inject_task_history,
        },
        "limits": {
            "max_retries": config.limits.max_retries,
            "retry_delays": config.limits.retry_delays,
            "max_subtask_iterations": config.limits.max_subtask_iterations,
            "max_subtask_depth": config.limits.max_subtask_depth,
            "task_timeout_seconds": config.limits.task_timeout_seconds,
        },
        "capabilities": {
            "file_upload": config.capabilities.file_upload,
            "websocket": config.capabilities.websocket,
            "sandbox": config.capabilities.sandbox,
            "image_generation": config.capabilities.image_generation,
        },
    }

    with open(vessel_dir / "vessel.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _convert_legacy_manifest(manifest: dict) -> VesselConfig:
    """Convert an agent/manifest.yaml dict to VesselConfig."""
    runner_cfg = manifest.get("runner", {}) or {}
    hooks_cfg = manifest.get("hooks", {}) or {}
    prompt_sections_raw = manifest.get("prompt_sections", []) or []

    prompt_sections = [
        PromptSection(
            name=ps.get("name", ""),
            file=ps.get("file", ""),
            priority=ps.get("priority", 50),
        )
        for ps in prompt_sections_raw
    ]

    return VesselConfig(
        runner=RunnerConfig(
            module=runner_cfg.get("module", ""),
            class_name=runner_cfg.get("class", "") or runner_cfg.get("class_name", ""),
        ),
        hooks=HooksConfig(
            module=hooks_cfg.get("module", ""),
            pre_task=hooks_cfg.get("pre_task", ""),
            post_task=hooks_cfg.get("post_task", ""),
        ),
        context=ContextConfig(
            prompt_sections=prompt_sections,
        ),
    )


def migrate_agent_to_vessel(emp_dir: Path) -> bool:
    """Migrate agent/ directory to vessel/ for an employee.

    - Converts agent/manifest.yaml → vessel/vessel.yaml
    - Copies prompt_sections/, runner .py, hooks .py to vessel/
    - Keeps agent/ directory intact for backward compatibility

    Returns True if migration was performed, False if already migrated or no agent/ exists.
    """
    vessel_dir = emp_dir / "vessel"
    agent_dir = emp_dir / "agent"

    # Already migrated
    if (vessel_dir / "vessel.yaml").exists():
        return False

    # No agent config to migrate
    manifest_path = agent_dir / "manifest.yaml"
    if not manifest_path.exists():
        return False

    # Convert manifest
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f) or {}

    config = _convert_legacy_manifest(manifest)
    save_vessel_config(emp_dir, config)

    # Copy prompt_sections/
    agent_ps = agent_dir / "prompt_sections"
    if agent_ps.exists() and agent_ps.is_dir():
        vessel_ps = vessel_dir / "prompt_sections"
        if not vessel_ps.exists():
            shutil.copytree(str(agent_ps), str(vessel_ps))

    # Copy runner .py if declared
    runner_mod = manifest.get("runner", {}).get("module", "")
    if runner_mod:
        runner_py = agent_dir / f"{runner_mod}.py"
        if runner_py.exists():
            dst = vessel_dir / f"{runner_mod}.py"
            if not dst.exists():
                shutil.copy2(str(runner_py), str(dst))

    # Copy hooks .py if declared
    hooks_mod = manifest.get("hooks", {}).get("module", "")
    if hooks_mod:
        hooks_py = agent_dir / f"{hooks_mod}.py"
        if hooks_py.exists():
            dst = vessel_dir / f"{hooks_mod}.py"
            if not dst.exists():
                shutil.copy2(str(hooks_py), str(dst))

    return True
