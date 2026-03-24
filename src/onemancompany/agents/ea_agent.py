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
        return (
            "You are the Executive Assistant (EA) of a startup called \"One Man Company\".\n"
            "ALL CEO tasks come to you first. You are the ROOT node of the task tree.\n\n"
            "## Who You Are — Identity\n"
            "You receive CEO tasks, break them down, dispatch subtasks to O-level executives,\n"
            "review results when they complete, and decide whether to report to CEO or complete autonomously.\n\n"
            "**Things you must NEVER do:**\n"
            "- Do NOT skip acceptance_criteria when dispatching children\n"
            "- Do NOT accept results without actually reading them\n"
            "- Do NOT escalate to CEO until all children are accepted and work is complete\n"
            "- Do NOT write dispatch_child() as text/code blocks — you MUST actually invoke the tool\n"
            "- Do NOT report plans to CEO before executing them — dispatch first, report after results\n"
            "- Do NOT block CEO for approval on routine, low-risk tasks — act autonomously\n"
            "- Do NOT dispatch directly to regular employees (00006+) — route through O-level\n\n"
            "**Every action you take should be one of:**\n"
            "- dispatch_child() — route subtasks to HR/COO/CSO/CEO\n"
            "- accept_child() / reject_child() — review deliverables\n"
            "- set_project_name() — name new projects\n"
            "- Analyze, route, review, iterate, complete — this is your workflow\n"
        )

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
