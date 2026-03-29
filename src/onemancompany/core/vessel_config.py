"""Vessel DNA — vessel.yaml configuration definition and loading.

VesselConfig defines the complete DNA of an employee vessel:
- runner: Neural system configuration (how to connect to the execution backend)
- hooks: Lifecycle hooks
- context: Context injection configuration
- limits: Execution limits
- capabilities: Capability declarations

Loading priority:
  1. emp_dir/vessel/vessel.yaml
  2. src/onemancompany/core/default_vessel.yaml (default DNA)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from onemancompany.core.config import VESSEL_DIR_NAME, VESSEL_YAML_FILENAME, open_utf

# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RunnerConfig:
    """Neural system configuration — defines how to connect to the execution backend."""
    module: str = ""
    class_name: str = ""


@dataclass
class HooksConfig:
    """Lifecycle hooks — pre/post-task callbacks."""
    module: str = ""
    pre_task: str = ""
    post_task: str = ""


@dataclass
class PromptSection:
    """Prompt injection fragment."""
    name: str
    file: str = ""
    priority: int = 50


@dataclass
class ContextConfig:
    """Context injection configuration."""
    prompt_sections: list[PromptSection] = field(default_factory=list)
    inject_progress_log: bool = True
    inject_task_history: bool = True


@dataclass
class LimitsConfig:
    """Execution limits."""
    max_retries: int = 3
    retry_delays: list[int] = field(default_factory=lambda: [5, 15, 30])
    max_subtask_iterations: int = 3
    max_subtask_depth: int = 2
    task_timeout_seconds: int = 600


@dataclass
class CapabilitiesConfig:
    """Capability declarations — platform capabilities supported by the vessel."""
    file_upload: bool = False
    websocket: bool = False
    sandbox: bool = False
    image_generation: bool = False


@dataclass
class VesselConfig:
    """vessel.yaml full structure — the DNA of an employee vessel."""
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
            sandbox=caps_raw.get("sandbox", False),
            image_generation=caps_raw.get("image_generation", False),
        ),
    )


def _load_default_vessel_config() -> VesselConfig:
    """Load the default vessel config from src/onemancompany/core/default_vessel.yaml."""
    if _DEFAULT_VESSEL_YAML.exists():
        with open_utf(_DEFAULT_VESSEL_YAML) as f:
            raw = yaml.safe_load(f) or {}
        return _parse_vessel_dict(raw)
    return VesselConfig()


def load_vessel_config(emp_dir: Path) -> VesselConfig:
    """Load VesselConfig for an employee directory.

    Search order:
      1. emp_dir/vessel/vessel.yaml
      2. default_vessel.yaml (built-in default)
    """
    # 1. vessel/vessel.yaml
    vessel_yaml = emp_dir / VESSEL_DIR_NAME / VESSEL_YAML_FILENAME
    if vessel_yaml.exists():
        try:
            with open_utf(vessel_yaml) as f:
                raw = yaml.safe_load(f) or {}
            return _parse_vessel_dict(raw)
        except yaml.YAMLError:
            return _load_default_vessel_config()

    # 2. Default
    return _load_default_vessel_config()


def save_vessel_config(emp_dir: Path, config: VesselConfig) -> None:
    """Write VesselConfig to emp_dir/vessel/vessel.yaml."""
    vessel_dir = emp_dir / VESSEL_DIR_NAME
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

    with open_utf(vessel_dir / VESSEL_YAML_FILENAME, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


