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

    if api_provider == "anthropic" and api_key:
        from langchain_anthropic import ChatAnthropic
        # Determine auth method
        auth_method = ""
        if employee_id and employee_id in employee_configs:
            auth_method = employee_configs[employee_id].auth_method

        # Standard API key
        extra_headers = {}
        if auth_method == "oauth" or api_key.startswith("sk-ant-oat"):
            extra_headers["anthropic-beta"] = "oauth-2025-04-20"
        return ChatAnthropic(
            model=model,
            api_key=api_key,
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


def get_employee_skills_prompt(employee_id: str) -> str:
    """Build a skills prompt section from employees/{id}/skills/*.md files."""
    skills = load_employee_skills(employee_id)
    if not skills:
        return ""
    parts = ["\n\n## Your Skills & Knowledge:"]
    for name, content in skills.items():
        parts.append(f"\n### {name}\n{content}")
    return "\n".join(parts)


def get_employee_tools_prompt(employee_id: str) -> str:
    """Build a prompt section listing all tools this employee is authorized to use.

    Open-access tools (empty allowed_users) are available to everyone.
    Restricted tools are only shown if employee_id is in allowed_users.
    """
    from onemancompany.core.config import TOOLS_DIR

    authorized: list[dict] = []
    for t in company_state.tools.values():
        if t.allowed_users and employee_id not in t.allowed_users:
            continue  # restricted and employee not authorized
        entry: dict = {"name": t.name, "description": t.description, "folder": t.folder_name}
        # Include file contents from tool folder
        if t.folder_name and t.files:
            file_contents: dict[str, str] = {}
            tool_folder = TOOLS_DIR / t.folder_name
            for fname in t.files:
                fpath = tool_folder / fname
                if fpath.is_file():
                    try:
                        file_contents[fname] = fpath.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, ValueError):
                        file_contents[fname] = f"[binary, {fpath.stat().st_size} bytes]"
            if file_contents:
                entry["files"] = file_contents
        authorized.append(entry)

    if not authorized:
        return ""

    parts = ["\n\n## Your Authorized Tools & Equipment:"]
    for tool_info in authorized:
        parts.append(f"\n### {tool_info['name']}")
        parts.append(f"{tool_info['description']}")
        if tool_info.get("files"):
            parts.append("Files:")
            for fname, content in tool_info["files"].items():
                parts.append(f"  - {fname}:\n```\n{content}\n```")
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
                        content = output.content or ""
                        if isinstance(content, str):
                            final_content = content  # track last AI output
                            on_log("llm_output", content)
                        tool_calls = getattr(output, "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                name = tc.get("name", "?")
                                args = str(tc.get("args", {}))
                                on_log("tool_call", f"{name}({args})")
            elif kind == "on_tool_end":
                output = data.get("output", "")
                name = event.get("name", "tool")
                result_str = str(output)
                on_log("tool_result", f"{name} → {result_str}")

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

    def _get_model_name(self) -> str:
        """Return the LLM model name configured for this employee."""
        cfg = employee_configs.get(self.employee_id)
        return cfg.llm_model if cfg and cfg.llm_model else settings.default_llm_model

    def _build_prompt(self) -> str:
        """Build the full system prompt. Override in subclasses if needed."""
        return ""

    def _build_full_prompt(self) -> str:
        """Build prompt with task history injected from the agent loop."""
        prompt = self._build_prompt()
        from onemancompany.core.agent_loop import _current_loop
        loop = _current_loop.get(None)
        if loop:
            prompt += loop.get_history_context()
        return prompt

    def _set_status(self, status: str) -> None:
        """Set this agent's employee status (idle/working/in_meeting)."""
        emp = company_state.employees.get(self.employee_id)
        if emp:
            emp.status = status

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
        emp = company_state.employees.get(self.employee_id)
        if not emp or not emp.guidance_notes:
            return ""
        notes = "\n".join(f"  - {n}" for n in emp.guidance_notes)
        return (
            f"\n\n## CEO Guidance (follow these directives in all your work):\n{notes}\n"
        )

    def _get_work_principles_prompt_section(self) -> str:
        """Build a prompt section from this employee's work principles."""
        emp = company_state.employees.get(self.employee_id)
        if not emp or not emp.work_principles:
            return ""
        return (
            f"\n\n## Your Work Principles (follow strictly):\n{emp.work_principles}\n"
        )

    def _get_company_culture_prompt_section(self) -> str:
        """Build a prompt section from company culture items."""
        items = company_state.company_culture
        if not items:
            return ""
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(items))
        return (
            f"\n\n## Company Culture (values and guidelines all employees must follow):\n{rules}\n"
        )

    def _get_dynamic_context_section(self) -> str:
        """Build a dynamic context section with current datetime, team state, and workload."""
        parts = ["\n\n## Current Context"]

        # Datetime
        now = datetime.now()
        parts.append(f"- Current time: {now.strftime('%Y-%m-%d %H:%M')}")

        # Team roster summary (compact)
        team_lines = []
        for emp in company_state.employees.values():
            if emp.id == self.employee_id:
                continue
            status_tag = f"[{emp.status}]" if emp.status != "idle" else ""
            task_hint = f" — {emp.current_task_summary}" if emp.current_task_summary else ""
            team_lines.append(
                f"  - {emp.name}({emp.nickname}) ID:{emp.id} {emp.role} Lv.{emp.level}{status_tag}{task_hint}"
            )
        if team_lines:
            parts.append("- Team:\n" + "\n".join(team_lines))

        # Active projects (brief)
        if company_state.active_tasks:
            active = []
            for t in company_state.active_tasks[:5]:
                active.append(f"  - [{t.routed_to}] {t.task[:60]}")
            parts.append("- Active tasks:\n" + "\n".join(active))

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
        direction = company_state.company_direction
        if not direction:
            return ""
        return (
            f"\n\n## Company Direction (公司战略方向)\n"
            f"{direction}\n"
            f"所有工作应围绕公司方向展开，确保产出与公司战略一致。\n"
        )

    def _build_prompt_builder(self) -> PromptBuilder:
        """Build a PromptBuilder with all standard sections. Override _customize_prompt() to modify."""
        pb = PromptBuilder()
        pb.add("talent_persona", self._get_talent_persona_section(), priority=12)
        pb.add("skills", self._get_skills_prompt_section(), priority=30)
        pb.add("tools", self._get_tools_prompt_section(), priority=35)
        pb.add("direction", self._get_company_direction_section(), priority=40)
        pb.add("culture", self._get_company_culture_prompt_section(), priority=45)
        pb.add("principles", self._get_work_principles_prompt_section(), priority=50)
        pb.add("guidance", self._get_guidance_prompt_section(), priority=55)
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
        from onemancompany.agents.common_tools import BASE_TOOLS, GATED_TOOLS
        from onemancompany.core.config import load_employee_custom_tools

        self.employee_id = employee_id
        emp = company_state.employees.get(employee_id)
        self.role = emp.role if emp else "Employee"

        # Start with base tools (always available, no permission check)
        all_tools = list(BASE_TOOLS)

        # Add gated tools based on employee's tool_permissions
        tool_perms = set(emp.tool_permissions) if emp and emp.tool_permissions else set()
        self._authorized_tool_names: list[str] = []
        self._unauthorized_tool_names: list[str] = []
        for name, tool_fn in GATED_TOOLS.items():
            if name in tool_perms:
                all_tools.append(tool_fn)
                self._authorized_tool_names.append(name)
            else:
                self._unauthorized_tool_names.append(name)

        # Employee-specific custom tools (from tools/ dir)
        custom_tools = load_employee_custom_tools(employee_id)
        all_tools.extend(custom_tools)

        self._agent = create_react_agent(
            model=make_llm(employee_id),
            tools=all_tools,
        )

    def _build_prompt(self) -> str:
        emp = company_state.employees.get(self.employee_id)
        if not emp:
            return "You are a company employee."

        pb = self._build_prompt_builder()

        # 1. Role header: try employee's custom role.md, else default
        role_prompt = self._load_prompt_file("role.md")
        if role_prompt:
            header = (role_prompt
                      .replace("{name}", emp.name)
                      .replace("{nickname}", emp.nickname)
                      .replace("{role}", emp.role)
                      .replace("{department}", emp.department)
                      .replace("{level}", str(emp.level)))
        else:
            header = (
                f"You are {emp.name} (花名: {emp.nickname}), "
                f"a {emp.role} in {emp.department} (Lv.{emp.level}).\n"
                f"Follow instructions from your managers, complete tasks thoroughly, "
                f"and collaborate with colleagues when needed.\n"
            )
        pb.add("role", header, priority=10)

        # 2. Work Approach: from files or hardcoded
        work_approach = (self._load_prompt_file("work_approach.md")
                         or self._load_shared_prompt("work_approach.md")
                         or (
                             "## Work Approach\n"
                             "1. Review: FIRST use list_project_workspace to see what already exists in the project. "
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
                          "- list_project_workspace: ALWAYS call this first to see existing project files.\n"
                          "- read_file / list_directory: Read existing files to understand context before working.\n"
                          "- save_to_project: Save ALL deliverables to the project workspace.\n"
                          "- dispatch_task: Delegate sub-work to colleagues if needed.\n"
                          "- pull_meeting: ONLY for multi-person communication/discussion (2+ colleagues). "
                          "Never call a meeting with yourself alone — if you need to think, just think internally.\n"
                          "- use_tool: Access company equipment/tools registered by COO.\n"
                      ))
        pb.add("tool_usage", tool_usage, priority=20)

        # 4. Unauthorized tools section
        pb.add("unauthorized_tools", self._get_unauthorized_tools_section(), priority=36)

        return pb.build()

    def _get_unauthorized_tools_section(self) -> str:
        """Show tools the employee doesn't have permission for."""
        if not self._unauthorized_tool_names:
            return ""
        names = ", ".join(self._unauthorized_tool_names)
        return (
            f"\n\n## Restricted Tools (need COO approval)\n"
            f"The following tools exist but you don't have permission: {names}\n"
            f"If you need any of these, call request_tool_access(tool_name, reason, employee_id) to request access from COO.\n"
        )

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"{self.role} analyzing: {task[:80]}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
                HumanMessage(content=task),
            ]}
        )

        final = result["messages"][-1].content
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": self.role, "summary": final[:MAX_SUMMARY_LEN]})
        return final

