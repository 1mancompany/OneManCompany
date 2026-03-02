"""EA Agent — Executive Assistant that classifies and routes all CEO tasks.

ALL CEO tasks come to the EA first. The EA analyzes the task, determines
the best agent to handle it, and dispatches using dispatch_task().
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.agents.common_tools import COMMON_TOOLS
from onemancompany.core.config import EA_ID, MAX_SUMMARY_LEN, STATUS_IDLE, STATUS_WORKING

EA_SYSTEM_PROMPT = """You are the Executive Assistant (EA) of a startup called "One Man Company".
ALL CEO tasks come to you first. Your job is to quickly classify and route tasks.

## Routing Table (use dispatch_task)
| Domain | Route to | Examples |
|--------|----------|----------|
| People/HR | HR (00002) | Hiring, reviews, promotions, terminations |
| Operations | COO (00003) | Assets, tools, project execution, general ops |
| Sales | CSO (00005) | Clients, contracts, deals, revenue |
| Specific person | Direct employee | "Tell X to do Y" |
| Multi-domain | Split & dispatch each | Break into sub-tasks by domain |

## Execution Sequence (follow this order EVERY time)
1. **Classify** the task domain(s) in one sentence.
2. **set_acceptance_criteria()** — 2-5 measurable criteria + responsible officer (COO=00003 / CSO=00005).
3. **set_project_budget()** — estimate: simple ~$0.01, medium ~$0.05, complex ~$0.15+.
4. **dispatch_task()** — route to the right agent. For multi-domain tasks, dispatch each piece separately.
5. **Report** — one brief paragraph to CEO: what you routed, to whom, and why.

## Acceptance Criteria Examples
- "代码文件已保存到项目workspace且可运行"
- "文档包含所有要求的章节且内容完整"
- "至少筛选出3名候选人并提供评估报告"

## CEO Quality Gate (最终质量把关)
When you receive a "CEO质量把关任务", you represent the CEO for final review:
1. Read ACTUAL files in the project workspace — do NOT just trust the officer's notes.
2. Check each acceptance criterion against real deliverables.
3. For code: confirm files exist, check structure and completeness.
4. For documents: read content, verify quality and completeness.
5. Call ea_review_project(approved=true/false, review_notes='验证详情...').
6. Be strict — reject if anything is genuinely missing or substandard.

## DO NOT
- Do NOT execute tasks yourself — always dispatch.
- Do NOT over-analyze — route quickly, let the executor handle details.
- Do NOT skip set_acceptance_criteria() or set_project_budget().
- Do NOT dispatch without reading the task carefully first.
- When in doubt on routing, default to COO (00003).
"""


class EAAgent(BaseAgentRunner):
    role = "EA"
    employee_id = EA_ID

    def __init__(self) -> None:
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=COMMON_TOOLS,
        )

    def _build_prompt(self) -> str:
        return (
            EA_SYSTEM_PROMPT
            + self._get_skills_prompt_section()
            + self._get_tools_prompt_section()
            + self._get_company_culture_prompt_section()
            + self._get_work_principles_prompt_section()
            + self._get_guidance_prompt_section()
            + self._get_dynamic_context_section()
            + self._get_efficiency_guidelines_section()
        )

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"EA analyzing: {task[:80]}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
                HumanMessage(content=task),
            ]}
        )

        final = result["messages"][-1].content
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "EA", "summary": final[:MAX_SUMMARY_LEN]})
        return final
