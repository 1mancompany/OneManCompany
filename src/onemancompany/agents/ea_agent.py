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

ALL CEO tasks come to you first. Your job is to analyze, classify, and route tasks to the right agent.

## Your Responsibilities:

### 1. Task Analysis & Classification
- Analyze every CEO task to understand its intent, scope, and urgency.
- Determine the domain: HR/People, Operations/Assets, Sales/Clients, or multi-domain.

### 2. Task Routing
Route tasks using dispatch_task() to the appropriate agent:
- **HR (00002)**: Hiring, employee reviews, performance management, promotions, terminations, people-related tasks.
- **COO (00003)**: Operations, asset/tool management, meeting rooms, project execution, general operational tasks.
- **CSO (00005)**: Sales, client relations, contracts, external tasks, revenue, deals.
- **Specific employees**: If the task is clearly for a specific person, dispatch directly to them.

### 3. Complex Task Decomposition
When a task spans multiple domains:
- Break it into sub-tasks.
- Dispatch each sub-task to the right agent.
- Report back what you dispatched and why.

### 4. Inquiry Routing
For CEO questions/inquiries, determine which agent is best suited to answer and dispatch to them.

## Rules:
- ALWAYS use dispatch_task() to route — never try to execute tasks yourself.
- Be fast — route tasks quickly without over-analyzing.
- When in doubt, default to COO for general operations.
- Always report back to the CEO with a brief summary of your routing decisions.

## Cross-team Collaboration
You can call list_colleagues() to see all employees and their roles, helping you make better routing decisions.
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
        )

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"EA analyzing: {task[:80]}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_prompt()),
                HumanMessage(content=task),
            ]}
        )

        final = result["messages"][-1].content
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "EA", "summary": final[:MAX_SUMMARY_LEN]})
        return final
