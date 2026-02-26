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
            f"\n\n## 你的工作准则 (Work Principles — 严格遵守):\n{emp.work_principles}\n"
        )

    def _get_culture_wall_prompt_section(self) -> str:
        """Build a prompt section from company culture wall items."""
        items = company_state.culture_wall
        if not items:
            return ""
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(items))
        return (
            f"\n\n## 公司文化墙 (Company Culture — 全员必须遵守的价值观和行为准则):\n{rules}\n"
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

        current_principles = emp.work_principles or "（暂无工作准则）"
        update_prompt = (
            f"你是 {emp.name}（{emp.nickname}，{emp.role}，部门: {emp.department}）。\n"
            f"CEO刚对你下达了新的指导:\n\n\"{guidance}\"\n\n"
            f"你当前的工作准则如下:\n{current_principles}\n\n"
            f"请根据CEO的新指导，更新你的工作准则。要求:\n"
            f"1. 保留原有仍然有效的准则\n"
            f"2. 将新指导的核心要求融入准则中\n"
            f"3. 如果新指导与旧准则冲突，以新指导为准\n"
            f"4. 保持 Markdown 格式，结构清晰\n"
            f"5. 准则应简洁可执行，不要空话\n\n"
            f"直接输出更新后的完整工作准则（Markdown格式），不要添加额外解释。"
        )
        principles_resp = await llm.ainvoke(update_prompt)
        new_principles = principles_resp.content

        # Persist updated work principles
        emp.work_principles = new_principles
        save_work_principles(self.employee_id, new_principles)

        # Acknowledge the guidance
        ack_prompt = (
            f"你是 {emp.name}（{emp.role}）。CEO刚对你下达了指导:\n\n"
            f'"{guidance}"\n\n'
            f"你已将其融入工作准则。用1-2句中文简要回应CEO，表示你已理解并更新了工作准则。"
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
