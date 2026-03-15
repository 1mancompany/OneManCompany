"""Base agent utilities shared across all LangChain agents."""

from __future__ import annotations
from datetime import datetime
from loguru import logger
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage

from onemancompany.core.config import (
    EMPLOYEES_DIR,
    MAX_SUMMARY_LEN,
    SHARED_PROMPTS_DIR,
    STATUS_IDLE,
    STATUS_WORKING,
    employee_configs,
    load_employee_skills,
    settings,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state
from onemancompany.agents.prompt_builder import PromptBuilder


def _extract_text(content) -> str:
    """Extract text from AIMessage content, handling both str and list-of-blocks formats.

    Anthropic models return content as a list of blocks like
    [{"type": "text", "text": "..."}, {"type": "tool_use", ...}].
    OpenAI-compatible models return a plain string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""


def extract_final_content(result: dict) -> str:
    """Extract the final text content from a LangGraph ainvoke result.

    Walks backwards through messages to find the last AIMessage with non-empty
    text content, since the actual last message may be a ToolMessage.

    If no AIMessage has text, synthesizes a summary from the last tool calls
    and their results.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    messages = result.get("messages", [])
    if not messages:
        return ""

    # 1. Try: last AIMessage with non-empty text
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = _extract_text(msg.content)
            if text.strip():
                return text

    # 2. Fallback: summarize from tool calls + results at the end of the chain.
    #    Walk backwards collecting ToolMessages until we hit an AIMessage (the caller).
    tool_results: list[str] = []
    tool_names: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            tool_results.append(_extract_text(msg.content))
        elif isinstance(msg, AIMessage):
            # This AIMessage had tool_calls but no text — grab the tool names
            for tc in getattr(msg, "tool_calls", []) or []:
                tool_names.append(tc.get("name", "unknown"))
            break

    if tool_names:
        parts = [f"Executed: {', '.join(tool_names)}"]
        for name, res in zip(tool_names, reversed(tool_results)):
            snippet = res[:300] if res else ""
            parts.append(f"  {name} → {snippet}")
        return "\n".join(parts)

    # 3. Last resort
    return _extract_text(messages[-1].content) or "(no output)"


def make_llm(employee_id: str = "") -> BaseChatModel:
    """Create an LLM instance, using per-agent model config from employees/{id}/profile.yaml.

    Supports multiple API providers:
    - "openrouter" (default): Uses OpenRouter API via ChatOpenAI
    - "anthropic": Uses Anthropic API directly via ChatAnthropic (requires api_key)
    """
    model = settings.default_llm_model
    temperature = 0.7
    api_provider = "openrouter"
    api_key = ""

    if employee_id and employee_id in employee_configs:
        cfg = employee_configs[employee_id]
        if cfg.llm_model:
            model = cfg.llm_model
        temperature = cfg.temperature
        api_provider = cfg.api_provider
        api_key = cfg.api_key

    if api_provider == "anthropic":
        effective_key = api_key or settings.anthropic_api_key
        if effective_key:
            from langchain_anthropic import ChatAnthropic
            # Determine auth method
            auth_method = ""
            if employee_id and employee_id in employee_configs:
                auth_method = employee_configs[employee_id].auth_method
            if not auth_method:
                auth_method = settings.anthropic_auth_method

            extra_headers = {}
            if auth_method == "oauth" or effective_key.startswith("sk-ant-oat"):
                extra_headers["anthropic-beta"] = "oauth-2025-04-20"
            return ChatAnthropic(
                model=model,
                api_key=effective_key,
                temperature=temperature,
                max_retries=3,
                default_headers=extra_headers or None,
            )

    # Self-hosted employees without an API key: fall back to default company model
    if api_provider != "openrouter":
        model = settings.default_llm_model

    # Default: OpenRouter
    return ChatOpenAI(
        model=model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=temperature,
        max_retries=3,
    )


# ---------------------------------------------------------------------------
# Overhead cost tracking — accumulates all LLM usage into company_state
# ---------------------------------------------------------------------------

def _record_overhead(
    category: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    *,
    employee_id: str = "",
    task_id: str = "",
) -> None:
    """Accumulate an LLM call's cost into company_state.overhead_costs."""
    from onemancompany.core.models import CostRecord

    record = CostRecord(
        category=category,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        employee_id=employee_id or None,
        task_id=task_id or None,
    )
    company_state.overhead_costs.add(record)


async def tracked_ainvoke(
    llm,
    messages,
    *,
    category: str = "other",
    employee_id: str = "",
    project_id: str = "",
) -> "Any":
    """Call llm.ainvoke(messages) and record token usage.

    - Always accumulates into company_state.overhead_costs (global view).
    - If project_id is set, also records into the project cost breakdown.
    - Returns the raw AIMessage result so callers need no changes.
    """
    from onemancompany.core.model_costs import get_model_cost

    result = await llm.ainvoke(messages)

    # Extract token usage from response_metadata
    meta = getattr(result, "response_metadata", {}) or {}
    usage = meta.get("usage", {}) or meta.get("token_usage", {}) or {}
    input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)

    # Determine model name
    model_name = meta.get("model_name", "") or meta.get("model", "")
    if not model_name:
        # Try to get from employee config
        cfg = employee_configs.get(employee_id)
        model_name = cfg.llm_model if cfg and cfg.llm_model else settings.default_llm_model

    # Compute cost
    if input_tokens or output_tokens:
        costs = get_model_cost(model_name)
        cost_usd = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
    else:
        cost_usd = 0.0

    # Record to project if applicable
    if project_id and (input_tokens or output_tokens):
        from onemancompany.core.project_archive import record_project_cost
        record_project_cost(project_id, employee_id, model_name, input_tokens, output_tokens, cost_usd)

    # Always record overhead
    _record_overhead(category, model_name, input_tokens, output_tokens, cost_usd)

    return result


# ---------------------------------------------------------------------------
# Standalone prompt builders — usable by any code that invokes an employee
# ---------------------------------------------------------------------------

def get_employee_talent_persona(employee_id: str) -> str:
    """Load talent persona from employees/{id}/prompts/talent_persona.md."""
    path = EMPLOYEES_DIR / employee_id / "prompts" / "talent_persona.md"
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8").strip()
    return f"\n{content}" if content else ""


def _parse_skill_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md content.

    Returns (metadata_dict, body_without_frontmatter).
    """
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("---", 3)
    if end == -1:
        return {}, raw
    import yaml
    try:
        meta = yaml.safe_load(raw[3:end]) or {}
    except Exception:
        meta = {}
    body = raw[end + 3:].lstrip("\n")
    return meta, body


def get_employee_skills_index(employee_id: str) -> dict[str, dict]:
    """Build a skill name→{name, description} index for an employee.

    Returns dict like {"ontology": {"name": "ontology", "description": "..."}}.
    """
    skills = load_employee_skills(employee_id)
    index: dict[str, dict] = {}
    for folder_name, raw_content in skills.items():
        meta, _ = _parse_skill_frontmatter(raw_content)
        index[folder_name] = {
            "name": meta.get("name", folder_name),
            "description": meta.get("description", ""),
        }
    return index


def get_employee_skills_prompt(employee_id: str) -> str:
    """Build skill prompt: autoload skills inline, others as catalog index.

    Skills with ``autoload: true`` in frontmatter are injected fully.
    Others are listed as name+description for on-demand ``load_skill`` tool.
    """
    skills = load_employee_skills(employee_id)
    if not skills:
        return ""

    autoloaded: list[str] = []
    catalog: list[str] = []

    for folder_name, raw_content in skills.items():
        meta, body = _parse_skill_frontmatter(raw_content)
        display_name = meta.get("name", folder_name)
        description = meta.get("description", "")

        if meta.get("autoload"):
            autoloaded.append(f"### {display_name}\n{body}")
        else:
            line = f"- **{display_name}**"
            if description:
                line += f": {description}"
            catalog.append(line)

    parts: list[str] = []
    if autoloaded:
        parts.append("\n\n## Active Skills")
        parts.extend(autoloaded)
    if catalog:
        parts.append("\n\n## Available Skills")
        parts.append(
            "Use the `load_skill` tool to load a skill's full instructions "
            "before applying it.\n"
        )
        parts.extend(catalog)
    return "\n".join(parts)


def get_employee_tools_prompt(employee_id: str) -> str:
    """Build a prompt section listing all tools this employee is authorized to use.

    Single source of truth: reads from tool_registry, which already handles
    permission filtering (base/gated/role/asset categories).
    Asset tools with file contents are enriched from company_state.tools metadata.
    """
    from onemancompany.core.config import TOOLS_DIR
    from onemancompany.core.tool_registry import tool_registry

    tools = tool_registry.get_tools_for(employee_id)
    if not tools:
        return ""

    parts = ["\n\n## Your Authorized Tools:"]
    for t in tools:
        meta = tool_registry.get_meta(t.name)
        description = t.description or ""

        parts.append(f"\n### {t.name}")
        parts.append(description)

        # For asset tools, include file contents from the tool folder
        if meta and meta.source == "asset":
            office_tool = company_state.tools.get(meta.name)
            if office_tool and office_tool.folder_name and office_tool.files:
                tool_folder = TOOLS_DIR / office_tool.folder_name
                for fname in office_tool.files:
                    fpath = tool_folder / fname
                    if fpath.is_file():
                        try:
                            content = fpath.read_text(encoding="utf-8")
                        except (UnicodeDecodeError, ValueError):
                            content = f"[binary, {fpath.stat().st_size} bytes]"
                        parts.append(f"  - {fname}:\n```\n{content}\n```")

    parts.append("\n### Tool Usage Rules — Internal vs External")
    parts.append(
        "- **Internal task dispatch**: Use dispatch_child() to assign work to employees. "
        "NEVER use Gmail/email for internal task routing or employee coordination.\n"
        "- **CEO escalation**: Use dispatch_child(\"00001\", description) to request CEO help. "
        "Escalate when:\n"
        "  - You need to purchase something (API keys, SaaS subscriptions, domains, etc.)\n"
        "  - You need actions outside the system (manual approval, signing contracts, legal compliance)\n"
        "  - You need external accounts or access permissions created\n"
        "  - The task exceeds your capabilities and cannot be delegated to another employee\n"
        "  - The task involves external commitments or brand representation\n"
        "  - You are blocked and no available tool or colleague can unblock you\n"
        "- **External communication**: Use Gmail ONLY for people OUTSIDE the company "
        "(clients, vendors, partners, third parties)."
    )
    return "\n".join(parts)


class BaseAgentRunner:
    """Thin wrapper around create_react_agent that publishes events."""

    role: str = "agent"
    employee_id: str = ""  # maps to company_state.employees key
    _agent = None  # subclasses set this to a LangGraph compiled graph
    _last_usage: dict = {}  # token usage from last run_streamed() call

    async def _publish(self, event_type: str, payload: dict) -> None:
        await event_bus.publish(
            CompanyEvent(type=event_type, payload=payload, agent=self.role)
        )

    async def run_streamed(self, task: str, on_log=None) -> str:
        """Run agent with streaming, calling on_log(type, content) for each LLM step.

        Uses astream_events to capture LLM input/output and tool calls in real time,
        then returns the final AI message content.
        Falls back to regular run() if _agent is not set or on_log is None.
        """
        if not self._agent or not on_log:
            return await self.run(task)

        from langchain_core.messages import HumanMessage, SystemMessage

        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"{self.role} analyzing: {task[:80]}"})

        prompt = self._build_full_prompt()
        messages_input = {
            "messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]
        }

        final_content = ""
        total_input_tokens = 0
        total_output_tokens = 0
        model_used = ""
        last_tool_calls: list[str] = []  # track tool names for fallback
        last_tool_results: list[str] = []

        async for event in self._agent.astream_events(
            messages_input, version="v2", config={"recursion_limit": 50},
        ):
            kind = event.get("event", "")
            data = event.get("data", {})
            if kind == "on_chat_model_start":
                inp = data.get("input", "")
                if isinstance(inp, list) and inp:
                    last_msg = inp[-1]
                    if hasattr(last_msg, "content"):
                        content = last_msg.content or ""
                        if isinstance(content, str):
                            on_log("llm_input", f"[{type(last_msg).__name__}] {content}")
            elif kind == "on_chat_model_end":
                output = data.get("output", None)
                if output:
                    # Extract token usage from response_metadata
                    meta = getattr(output, "response_metadata", {}) or {}
                    usage = meta.get("usage", {}) or meta.get("token_usage", {}) or {}
                    if usage:
                        total_input_tokens += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
                    if not model_used:
                        model_used = meta.get("model_name", "") or meta.get("model", "")

                    if hasattr(output, "content"):
                        text = _extract_text(output.content)
                        if text.strip():
                            final_content = text  # track last AI output
                            on_log("llm_output", text)
                        tool_calls = getattr(output, "tool_calls", None)
                        if tool_calls:
                            last_tool_calls = []
                            last_tool_results = []
                            for tc in tool_calls:
                                name = tc.get("name", "?")
                                args = str(tc.get("args", {}))
                                last_tool_calls.append(name)
                                on_log("tool_call", f"{name}({args})")
            elif kind == "on_tool_end":
                output = data.get("output", "")
                name = event.get("name", "tool")
                result_str = str(output)
                last_tool_results.append(f"{name} → {result_str[:300]}")
                on_log("tool_result", f"{name} → {result_str}")

        # If no text content from LLM, synthesize from last tool calls
        if not final_content.strip() and last_tool_calls:
            parts = [f"Executed: {', '.join(last_tool_calls)}"]
            parts.extend(last_tool_results)
            final_content = "\n".join(parts)

        # Store usage for caller to read
        self._last_usage = {
            "model": model_used or self._get_model_name(),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        }

        # Record streaming usage into overhead
        if total_input_tokens or total_output_tokens:
            from onemancompany.core.model_costs import get_model_cost
            _model = model_used or self._get_model_name()
            _costs = get_model_cost(_model)
            _cost_usd = (total_input_tokens * _costs["input"] + total_output_tokens * _costs["output"]) / 1_000_000
            _record_overhead("agent_task", _model, total_input_tokens, total_output_tokens, _cost_usd)

        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": self.role, "summary": (final_content or "")[:MAX_SUMMARY_LEN]})
        return final_content

    def _extract_and_record_usage(self, result: dict) -> dict:
        """Extract total token usage from a LangGraph ainvoke result and record it.

        Iterates over all AIMessages in the result to sum up token usage from
        multi-step tool-use loops. Updates ``_last_usage`` and records to
        company overhead costs.

        Returns the ``_last_usage`` dict.
        """
        from langchain_core.messages import AIMessage
        from onemancompany.core.model_costs import get_model_cost

        total_input = 0
        total_output = 0
        model = ""
        for msg in result.get("messages", []):
            if not isinstance(msg, AIMessage):
                continue
            meta = getattr(msg, "response_metadata", {}) or {}
            usage = meta.get("usage", {}) or meta.get("token_usage", {}) or {}
            total_input += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            total_output += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
            if not model:
                model = meta.get("model_name", "") or meta.get("model", "")

        model = model or self._get_model_name()
        self._last_usage = {
            "model": model,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
        }

        if total_input or total_output:
            costs = get_model_cost(model)
            cost_usd = (total_input * costs["input"] + total_output * costs["output"]) / 1_000_000
            _record_overhead(
                "agent_task", model, total_input, total_output, cost_usd,
                employee_id=self.employee_id,
            )

        return self._last_usage

    def _get_model_name(self) -> str:
        """Return the LLM model name configured for this employee."""
        cfg = employee_configs.get(self.employee_id)
        return cfg.llm_model if cfg and cfg.llm_model else settings.default_llm_model

    def _build_prompt(self) -> str:
        """Build the full system prompt using PromptBuilder.

        Subclasses should override _customize_prompt(pb) to add role-specific
        sections rather than overriding this method directly.
        """
        pb = self._build_prompt_builder()
        return pb.build()

    def _build_full_prompt(self) -> str:
        """Build prompt with task history injected from the agent loop."""
        prompt = self._build_prompt()
        from onemancompany.core.agent_loop import _current_vessel
        loop = _current_vessel.get(None)
        if loop:
            prompt += loop.get_history_context()
        return prompt

    def _set_status(self, status: str) -> None:
        """Set this agent's employee status (idle/working/in_meeting)."""
        # Runtime status is persisted to disk via store; no in-memory update needed.
        pass

    def _get_talent_persona_section(self) -> str:
        """Load talent persona from employees/{id}/prompts/talent_persona.md.

        This file is written during onboarding from the talent's
        system_prompt_template, capturing the talent's core identity and
        working style (e.g. "You are a senior PM with 46 frameworks...").
        """
        content = self._load_prompt_file("talent_persona.md")
        if not content:
            return ""
        return f"\n\n## Talent Persona\n{content.strip()}\n"

    def _get_skills_prompt_section(self) -> str:
        """Load skill files from employees/{id}/skills/ and build a prompt section."""
        return get_employee_skills_prompt(self.employee_id)

    def _get_tools_prompt_section(self) -> str:
        """Build a prompt section listing authorized tools for this agent."""
        return get_employee_tools_prompt(self.employee_id)

    def _get_guidance_prompt_section(self) -> str:
        """Build a prompt section from CEO guidance notes for this agent."""
        from onemancompany.core.store import load_employee_guidance
        notes_list = load_employee_guidance(self.employee_id)
        if not notes_list:
            return ""
        notes = "\n".join(f"  - {n}" for n in notes_list)
        return (
            f"\n\n## CEO Guidance (follow these directives in all your work):\n{notes}\n"
        )


    def _get_company_culture_prompt_section(self) -> str:
        """Build a prompt section from company culture items."""
        from onemancompany.core.store import load_culture
        items = load_culture()
        if not items:
            return ""
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(items))
        return (
            f"\n\n## Company Culture (values and guidelines all employees must follow):\n{rules}\n"
        )

    def _get_task_lifecycle_section(self) -> str:
        """Inject task lifecycle state documentation so agents understand the system."""
        from onemancompany.core.task_lifecycle import TASK_LIFECYCLE_DOC
        return f"\n\n{TASK_LIFECYCLE_DOC}"

    def _get_dynamic_context_section(self) -> str:
        """Build a dynamic context section with current datetime, team state, and workload."""
        parts = ["\n\n## Current Context"]

        # Datetime
        now = datetime.now()
        parts.append(f"- Current time: {now.strftime('%Y-%m-%d %H:%M')}")

        # Team roster summary (compact)
        from onemancompany.core.store import load_all_employees
        all_emps = load_all_employees()
        team_lines = []
        for eid, edata in all_emps.items():
            if eid == self.employee_id:
                continue
            runtime = edata.get("runtime", {})
            status = runtime.get("status", "idle")
            task_summary = runtime.get("current_task_summary", "")
            status_tag = f"[{status}]" if status != "idle" else ""
            task_hint = f" — {task_summary}" if task_summary else ""
            team_lines.append(
                f"  - {edata.get('name', '')}({edata.get('nickname', '')}) ID:{eid} {edata.get('role', '')} Lv.{edata.get('level', 1)}{status_tag}{task_hint}"
            )
        if team_lines:
            parts.append("- Team:\n" + "\n".join(team_lines))

        # Active projects (brief)
        from onemancompany.core.state import get_active_tasks
        active_tasks = get_active_tasks()
        if active_tasks:
            active = []
            for t in active_tasks[:5]:
                active.append(f"  - [{t.routed_to}] {t.task[:60]}")
            parts.append("- Active tasks:\n" + "\n".join(active))

        # Custom settings (target_email, polling_interval, etc.)
        from onemancompany.core.config import load_custom_settings
        custom = load_custom_settings(self.employee_id)
        if custom:
            settings_lines = [f"  - {k}: {v}" for k, v in custom.items()]
            parts.append("- Your settings:\n" + "\n".join(settings_lines))

        return "\n".join(parts)

    def _load_prompt_file(self, filename: str) -> str | None:
        """Load prompt from employee's prompts/ dir."""
        path = EMPLOYEES_DIR / self.employee_id / "prompts" / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    @staticmethod
    def _load_shared_prompt(filename: str) -> str | None:
        """Load from company/shared_prompts/."""
        path = SHARED_PROMPTS_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _get_company_direction_section(self) -> str:
        """Build a prompt section from company direction/strategy."""
        from onemancompany.core.store import load_direction
        direction = load_direction()
        if not direction:
            return ""
        return (
            f"\n\n## Company Direction\n"
            f"{direction}\n"
            f"All work should align with the company direction, ensuring output is consistent with company strategy.\n"
        )

    def _get_soul_section(self) -> str:
        """Load the employee's self-maintained SOUL.md knowledge file."""
        soul_path = EMPLOYEES_DIR / self.employee_id / "workspace" / "SOUL.md"
        if soul_path.exists():
            try:
                content = soul_path.read_text(encoding="utf-8").strip()
                if content:
                    return (
                        "## Your Personal Knowledge (SOUL.md)\n"
                        "This is your self-maintained knowledge file. You wrote this yourself "
                        "based on past experience. Use it to inform your work.\n\n"
                        f"{content}"
                    )
            except Exception as exc:
                logger.debug("Failed to read SOUL.md for {}: {}", self.employee_id, exc)
        return ""

    def _build_prompt_builder(self) -> PromptBuilder:
        """Build a PromptBuilder with all standard sections. Override _customize_prompt() to modify."""
        pb = PromptBuilder()
        pb.add("talent_persona", self._get_talent_persona_section(), priority=12)
        pb.add("soul", self._get_soul_section(), priority=15)
        pb.add("skills", self._get_skills_prompt_section(), priority=30)
        pb.add("tools", self._get_tools_prompt_section(), priority=35)
        pb.add("direction", self._get_company_direction_section(), priority=40)
        pb.add("culture", self._get_company_culture_prompt_section(), priority=45)
        pb.add("guidance", self._get_guidance_prompt_section(), priority=55)
        pb.add("task_lifecycle", self._get_task_lifecycle_section(), priority=65)
        pb.add("context", self._get_dynamic_context_section(), priority=70)
        pb.add("efficiency", self._get_efficiency_guidelines_section(), priority=80)
        self._customize_prompt(pb)
        self._load_agent_prompt_sections(pb)
        return pb

    def _customize_prompt(self, pb: PromptBuilder) -> None:
        """Override in subclasses to add/remove/modify prompt sections."""
        pass

    def _load_agent_prompt_sections(self, pb: PromptBuilder) -> None:
        """Load prompt sections from vessel/vessel.yaml or agent/manifest.yaml (fallback)."""
        from onemancompany.core.vessel_config import load_vessel_config

        emp_dir = EMPLOYEES_DIR / self.employee_id
        config = load_vessel_config(emp_dir)

        if config.context.prompt_sections:
            # Resolve files from vessel/ first, then agent/
            for ps in config.context.prompt_sections:
                if not ps.name or not ps.file:
                    continue
                content_path = None
                for search_dir in [emp_dir / "vessel", emp_dir / "agent"]:
                    candidate = search_dir / ps.file
                    if candidate.exists():
                        content_path = candidate
                        break
                if not content_path:
                    continue
                try:
                    content = content_path.read_text(encoding="utf-8")
                    pb.add(ps.name, content, priority=ps.priority)
                except Exception as _e:
                    logger.warning("Failed to load prompt section %s: %s", ps.name, _e)

    def _get_efficiency_guidelines_section(self) -> str:
        """Build efficiency guidelines to reduce wasted tokens and loops."""
        # Try loading from shared prompts file first
        content = self._load_prompt_file("efficiency.md") or self._load_shared_prompt("efficiency.md")
        if content:
            return "\n\n" + content

        return (
            "\n\n## Efficiency Rules (MUST follow)\n"
            "- Do NOT explore the filesystem unless the task explicitly requires it.\n"
            "- Do NOT re-read files you have already read in this task.\n"
            "- Do NOT create unnecessary planning steps — act directly on clear instructions.\n"
            "- Do NOT call tools repeatedly with the same arguments.\n"
            "- If a tool call fails, try a different approach instead of retrying the same call.\n"
            "- Produce output first, verify once, then finish. Do NOT loop.\n"
            "- Keep your final response concise — report what you did and the result, not your thought process.\n"
        )


class EmployeeAgent(BaseAgentRunner):
    """Generic agent runner for newly hired employees.

    Uses COMMON_TOOLS and builds a prompt from the employee's profile,
    skills, tools, work principles, and company culture.
    """

    def __init__(self, employee_id: str) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self.employee_id = employee_id
        from onemancompany.core.store import load_employee as _load_emp
        emp_data = _load_emp(employee_id) or {}
        self.role = emp_data.get("role", "Employee")

        proxied_tools = tool_registry.get_proxied_tools_for(employee_id)
        self._authorized_tool_names: list[str] = [t.name for t in proxied_tools]

        self._agent = create_react_agent(
            model=make_llm(employee_id),
            tools=proxied_tools,
        )

    def _build_prompt(self) -> str:
        from onemancompany.core.store import load_employee as _load_emp
        emp_data = _load_emp(self.employee_id)
        if not emp_data:
            return "You are a company employee."

        pb = self._build_prompt_builder()

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_role = emp_data.get("role", "Employee")
        emp_dept = emp_data.get("department", "")
        emp_level = emp_data.get("level", 1)

        # 1. Role header: try employee's custom role.md, else default
        role_prompt = self._load_prompt_file("role.md")
        if role_prompt:
            header = (role_prompt
                      .replace("{name}", emp_name)
                      .replace("{nickname}", emp_nickname)
                      .replace("{role}", emp_role)
                      .replace("{department}", emp_dept)
                      .replace("{level}", str(emp_level)))
        else:
            header = (
                f"You are {emp_name} (nickname: {emp_nickname}), "
                f"a {emp_role} in {emp_dept} (Lv.{emp_level}).\n"
                f"Follow instructions from your managers, complete tasks thoroughly, "
                f"and collaborate with colleagues when needed.\n"
            )
        pb.add("role", header, priority=10)

        # 2. Work Approach: from files or hardcoded
        work_approach = (self._load_prompt_file("work_approach.md")
                         or self._load_shared_prompt("work_approach.md")
                         or (
                             "## Work Approach\n"
                             "1. Review: FIRST use ls to see what already exists in the project workspace. "
                             "Read key files to understand what's been done — never start from scratch blindly.\n"
                             "2. Analyze: Understand the task requirements in context of existing deliverables.\n"
                             "3. Execute: Produce the deliverable — iterate on what exists, don't duplicate.\n"
                             "4. Verify: Check your output once (run code, proofread doc). Fix if needed.\n"
                             "5. Save & Report: Save output to project workspace, then report completion.\n"
                         ))
        pb.add("work_approach", work_approach, priority=15)

        # 3. Tool Usage: from files or hardcoded
        tool_usage = (self._load_prompt_file("tool_usage.md")
                      or self._load_shared_prompt("tool_usage.md")
                      or (
                          "## Tool Usage\n"
                          "- ls: ALWAYS call this first to see existing project files.\n"
                          "- read / ls: Read existing files to understand context before working.\n"
                          "- write: Save ALL deliverables to the project workspace.\n"
                          "- dispatch_child: Delegate sub-work to colleagues if needed.\n"
                          "- pull_meeting: ONLY for multi-person communication/discussion (2+ colleagues). "
                          "Never call a meeting with yourself alone — if you need to think, just think internally.\n"
                          "- use_tool: Access company equipment/tools registered by COO.\n"
                      ))
        pb.add("tool_usage", tool_usage, priority=20)

        # 4. Unauthorized tools section
        pb.add("unauthorized_tools", self._get_unauthorized_tools_section(), priority=36)

        return pb.build()

    def _get_unauthorized_tools_section(self) -> str:
        """No longer needed — all company tools are available to all employees."""
        return ""

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"{self.role} analyzing: {task[:80]}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
                HumanMessage(content=task),
            ]}
        )

        self._extract_and_record_usage(result)
        final = extract_final_content(result)
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": self.role, "summary": final[:MAX_SUMMARY_LEN]})
        return final

