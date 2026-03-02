"""Base agent utilities shared across all LangChain agents."""

from langchain_openai import ChatOpenAI

from onemancompany.core.config import (
    MAX_SUMMARY_LEN,
    STATUS_IDLE,
    STATUS_WORKING,
    employee_configs,
    load_employee_skills,
    settings,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state


def make_llm(employee_id: str = "") -> ChatOpenAI:
    """Create an LLM instance, using per-agent model config from employees/{id}/profile.yaml."""
    model = settings.default_llm_model
    temperature = 0.7

    if employee_id and employee_id in employee_configs:
        cfg = employee_configs[employee_id]
        if cfg.llm_model:
            model = cfg.llm_model
        temperature = cfg.temperature

    return ChatOpenAI(
        model=model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=temperature,
        max_retries=3,
    )


# ---------------------------------------------------------------------------
# Standalone prompt builders — usable by any code that invokes an employee
# ---------------------------------------------------------------------------

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

        prompt = self._build_prompt()
        messages_input = {
            "messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]
        }

        final_content = ""
        async for event in self._agent.astream_events(messages_input, version="v2"):
            kind = event.get("event", "")
            data = event.get("data", {})
            if kind == "on_chat_model_start":
                inp = data.get("input", "")
                if isinstance(inp, list) and inp:
                    last_msg = inp[-1]
                    if hasattr(last_msg, "content"):
                        content = last_msg.content or ""
                        if isinstance(content, str):
                            display = content[:300] + "..." if len(content) > 300 else content
                            on_log("llm_input", f"[{type(last_msg).__name__}] {display}")
            elif kind == "on_chat_model_end":
                output = data.get("output", None)
                if output and hasattr(output, "content"):
                    content = output.content or ""
                    if isinstance(content, str):
                        final_content = content  # track last AI output
                        display = content[:500] + "..." if len(content) > 500 else content
                        on_log("llm_output", display)
                    tool_calls = getattr(output, "tool_calls", None)
                    if tool_calls:
                        for tc in tool_calls:
                            name = tc.get("name", "?")
                            args = str(tc.get("args", {}))[:200]
                            on_log("tool_call", f"{name}({args})")
            elif kind == "on_tool_end":
                output = data.get("output", "")
                name = event.get("name", "tool")
                result_str = str(output)[:300]
                on_log("tool_result", f"{name} → {result_str}")

        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": self.role, "summary": (final_content or "")[:MAX_SUMMARY_LEN]})
        return final_content

    def _build_prompt(self) -> str:
        """Build the full system prompt. Override in subclasses if needed."""
        return ""

    def _set_status(self, status: str) -> None:
        """Set this agent's employee status (idle/working/in_meeting)."""
        emp = company_state.employees.get(self.employee_id)
        if emp:
            emp.status = status

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

