"""EA Agent — Executive Assistant that classifies and routes all CEO tasks.

ALL CEO tasks come to the EA first. The EA analyzes the task, determines
the best agent to handle it, and dispatches using dispatch_child().
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, extract_final_content, make_llm
from onemancompany.core.config import CEO_ID, COO_ID, CSO_ID, EA_ID, HR_ID, MAX_SUMMARY_LEN, STATUS_IDLE, STATUS_WORKING


# EA operational prompt is now in employees/00004/role_guide.md (loaded by _get_role_identity_section)


class EAAgent(BaseAgentRunner):
    role = "EA"
    employee_id = EA_ID

    def __init__(self) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self._agent_tools = tool_registry.get_proxied_tools_for(self.employee_id)
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=self._agent_tools,
        )

    def _get_role_identity_section(self) -> str:
        from onemancompany.core.config import EMPLOYEES_DIR, read_text_utf
        guide_path = EMPLOYEES_DIR / self.employee_id / "role_guide.md"
        if guide_path.exists():
            return read_text_utf(guide_path)
        return ""

    def _customize_prompt(self, pb) -> None:
        pass  # All EA prompt content is in role_guide.md

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"EA analyzing: {task}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
                HumanMessage(content=task),
            ]}
        )

        self._extract_and_record_usage(result)
        final = extract_final_content(result)
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "EA", "summary": final[:MAX_SUMMARY_LEN]})
        return final
