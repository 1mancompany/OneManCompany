"""Base agent utilities shared across all LangChain agents."""

from langchain_openai import ChatOpenAI

from onemancompany.core.config import (
    employee_configs,
    load_employee_skills,
    load_work_principles,
    save_employee_guidance,
    save_work_principles,
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
    )


class BaseAgentRunner:
    """Thin wrapper around create_react_agent that publishes events."""

    role: str = "agent"
    employee_id: str = ""  # maps to company_state.employees key

    async def _publish(self, event_type: str, payload: dict) -> None:
        await event_bus.publish(
            CompanyEvent(type=event_type, payload=payload, agent=self.role)
        )

    def _set_status(self, status: str) -> None:
        """Set this agent's employee status (idle/working/in_meeting)."""
        emp = company_state.employees.get(self.employee_id)
        if emp:
            emp.status = status

    def _get_skills_prompt_section(self) -> str:
        """Load skill files from employees/{id}/skills/ and build a prompt section."""
        skills = load_employee_skills(self.employee_id)
        if not skills:
            return ""
        parts = ["\n\n## Your Skills & Knowledge:"]
        for name, content in skills.items():
            parts.append(f"\n### {name}\n{content}")
        return "\n".join(parts)

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

    def _get_culture_wall_prompt_section(self) -> str:
        """Build a prompt section from company culture wall items."""
        items = company_state.culture_wall
        if not items:
            return ""
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(items))
        return (
            f"\n\n## Company Culture Wall (values and guidelines all employees must follow):\n{rules}\n"
        )

    async def receive_guidance(self, guidance: str) -> str:
        """CEO provides guidance to this agent. Record it, update work principles, and acknowledge."""
        emp = company_state.employees.get(self.employee_id)
        if not emp:
            return "Employee not found."

        emp.is_listening = True
        await self._publish(
            "guidance_start",
            {"employee_id": self.employee_id, "name": emp.name},
        )

        emp.guidance_notes.append(guidance)
        company_state.activity_log.append(
            {"type": "guidance", "employee": emp.name, "note": guidance}
        )

        # Persist to employees/{id}/guidance.yaml
        save_employee_guidance(self.employee_id, emp.guidance_notes)

        # Use LLM to update work principles based on new guidance
        llm = make_llm(self.employee_id)

        current_principles = emp.work_principles or "(No work principles yet)"
        update_prompt = (
            f"You are {emp.name} ({emp.nickname}, {emp.role}, Department: {emp.department}).\n"
            f"The CEO just gave you new guidance:\n\n\"{guidance}\"\n\n"
            f"Your current work principles are:\n{current_principles}\n\n"
            f"Update your work principles based on the CEO's new guidance. Requirements:\n"
            f"1. Keep existing principles that are still valid\n"
            f"2. Incorporate the core requirements from the new guidance\n"
            f"3. If new guidance conflicts with old principles, the new guidance takes precedence\n"
            f"4. Maintain Markdown format with clear structure\n"
            f"5. Principles should be concise and actionable\n\n"
            f"Output the updated complete work principles (Markdown format) directly, with no additional explanation."
        )
        principles_resp = await llm.ainvoke(update_prompt)
        new_principles = principles_resp.content

        # Persist updated work principles
        emp.work_principles = new_principles
        save_work_principles(self.employee_id, new_principles)

        # Acknowledge the guidance
        ack_prompt = (
            f"You are {emp.name} ({emp.role}). The CEO just gave you guidance:\n\n"
            f'"{guidance}"\n\n'
            f"You have incorporated it into your work principles. Briefly respond to the CEO in 1-2 sentences, "
            f"confirming you understood and updated your work principles."
        )
        response = await llm.ainvoke(ack_prompt)
        ack_text = response.content

        await self._publish(
            "guidance_noted",
            {
                "employee_id": self.employee_id,
                "name": emp.name,
                "guidance": guidance,
                "acknowledgment": ack_text,
                "principles_updated": True,
            },
        )

        emp.is_listening = False
        await self._publish(
            "guidance_end",
            {"employee_id": self.employee_id, "name": emp.name},
        )

        return ack_text
