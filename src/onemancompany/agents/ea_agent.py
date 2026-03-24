"""EA Agent — Executive Assistant that classifies and routes all CEO tasks.

ALL CEO tasks come to the EA first. The EA analyzes the task, determines
the best agent to handle it, and dispatches using dispatch_child().
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, extract_final_content, make_llm
from onemancompany.core.config import CEO_ID, COO_ID, CSO_ID, EA_ID, HR_ID, MAX_SUMMARY_LEN, STATUS_IDLE, STATUS_WORKING

EA_SYSTEM_PROMPT = f"""## EA Dispatch Authority
Your SOPs & Workflows list contains the full EA Dispatch Authority SOP (ea_dispatch_authority_sop).
**Before handling any CEO task, read() the SOP to ensure you follow the correct dispatch and review procedure.**

Key rules (read SOP for details):
- **Default: act autonomously** on routine/low-risk tasks. Only escalate to CEO for financial, personnel, irreversible, or ambiguous decisions.
- **Only dispatch to O-level**: HR({HR_ID}), COO({COO_ID}), CSO({CSO_ID}), or CEO({CEO_ID}). Never dispatch directly to regular employees.
- **Iterate phases**: After accepting one phase, proactively dispatch the NEXT phase. Never mark complete when follow-up work remains.
- **Project naming**: For new tasks, call set_project_name(name) with a concise 2-6 word name.
"""


class EAAgent(BaseAgentRunner):
    role = "EA"
    employee_id = EA_ID

    def __init__(self) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=tool_registry.get_proxied_tools_for(self.employee_id),
        )

    def _get_role_identity_section(self) -> str:
        from onemancompany.core.config import EMPLOYEES_DIR, ENCODING_UTF8
        guide_path = EMPLOYEES_DIR / self.employee_id / "role_guide.md"
        if guide_path.exists():
            return guide_path.read_text(encoding=ENCODING_UTF8)
        return ""

    def _customize_prompt(self, pb) -> None:
        pb.add("role", EA_SYSTEM_PROMPT, priority=10)

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"EA analyzing: {task[:80]}"})

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
