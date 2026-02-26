"""Workflow Engine — parses markdown workflow documents and executes their steps.

Each workflow .md file in company_rules/ defines a sequence of stages (phases).
The engine extracts structured step definitions from the markdown and provides
an executor that runs each step dynamically, using the _chat() and _publish()
helpers for real-time frontend updates.

Markdown format expected (by convention used in company_rules/):

    # Workflow Title

    - **流程ID**: some_id
    - **责任人**: HR（知心姐）
    - **协作人**: COO（铁面侠）、项目全体成员
    - **触发条件**: ...

    ---

    ## 阶段一：Step Title

    - **负责人**: HR / COO / 每位参与员工 / ...
    - **步骤**:
      1. Do something
      2. Do something else
    - **产出**: Description of output

The engine parses these into WorkflowStep objects and executes them via
pluggable step handlers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class WorkflowStep:
    """A single stage/phase parsed from a workflow markdown document."""

    index: int  # 0-based position in the workflow
    title: str  # e.g., "阶段一：评审会准备"
    owner: str  # e.g., "HR", "COO", "每位参与员工", "COO + HR"
    instructions: list[str]  # numbered sub-steps
    output_description: str  # what this step produces
    raw_text: str  # full markdown text of this section
    collaborators: str = ""  # optional 协作人 at step level


@dataclass
class WorkflowDefinition:
    """A fully parsed workflow document."""

    name: str  # workflow title from the H1 header
    flow_id: str  # 流程ID
    owner: str  # 责任人
    collaborators: str  # 协作人
    trigger: str  # 触发条件
    steps: list[WorkflowStep] = field(default_factory=list)
    raw_text: str = ""  # full original markdown


def parse_workflow(name: str, markdown_text: str) -> WorkflowDefinition:
    """Parse a markdown workflow document into a WorkflowDefinition.

    Returns a structured representation with all steps extracted.
    """
    wf = WorkflowDefinition(
        name=name,
        flow_id="",
        owner="",
        collaborators="",
        trigger="",
        raw_text=markdown_text,
    )

    # Extract metadata from the header section (before any ## heading)
    header_match = re.search(r"^# .+?\n(.*?)(?=^## |\Z)", markdown_text, re.DOTALL | re.MULTILINE)
    if header_match:
        header_text = header_match.group(1)
        # Parse metadata fields
        flow_id_match = re.search(r"\*\*流程ID\*\*:\s*(.+)", header_text)
        if flow_id_match:
            wf.flow_id = flow_id_match.group(1).strip()

        owner_match = re.search(r"\*\*责任人\*\*:\s*(.+)", header_text)
        if owner_match:
            wf.owner = owner_match.group(1).strip()

        collab_match = re.search(r"\*\*协作人\*\*:\s*(.+)", header_text)
        if collab_match:
            wf.collaborators = collab_match.group(1).strip()

        trigger_match = re.search(r"\*\*触发条件\*\*:\s*(.+)", header_text)
        if trigger_match:
            wf.trigger = trigger_match.group(1).strip()

    # Split into ## sections (each is a step/stage)
    sections = re.split(r"^## ", markdown_text, flags=re.MULTILINE)
    step_index = 0
    for section in sections[1:]:  # skip the part before the first ##
        step = _parse_step_section(step_index, section)
        if step:
            wf.steps.append(step)
            step_index += 1

    return wf


def _parse_step_section(index: int, section_text: str) -> WorkflowStep | None:
    """Parse a single ## section into a WorkflowStep."""
    lines = section_text.strip().split("\n")
    if not lines:
        return None

    title = lines[0].strip()
    full_text = "## " + section_text

    # Extract owner (负责人)
    owner = ""
    owner_match = re.search(r"\*\*负责人\*\*:\s*(.+)", section_text)
    if owner_match:
        owner = owner_match.group(1).strip()

    # Extract collaborators at step level
    collaborators = ""
    collab_match = re.search(r"\*\*协作人\*\*:\s*(.+)", section_text)
    if collab_match:
        collaborators = collab_match.group(1).strip()

    # Extract numbered instructions from the 步骤 section
    instructions: list[str] = []
    in_steps = False
    for line in lines:
        stripped = line.strip()
        # Detect start of the 步骤 block
        if "**步骤**:" in stripped or "**步骤**：" in stripped:
            in_steps = True
            continue
        # Detect end: another **keyword**: field or a new section
        if in_steps and re.match(r"^-\s*\*\*\w+\*\*", stripped):
            in_steps = False
            continue
        if in_steps:
            # Numbered items like "1. ..." or "  - ..."
            num_match = re.match(r"^\d+\.\s+(.+)", stripped)
            if num_match:
                instructions.append(num_match.group(1).strip())
            elif stripped.startswith("- "):
                instructions.append(stripped[2:].strip())

    # Extract output description (产出)
    output_desc = ""
    output_match = re.search(r"\*\*产出\*\*:\s*(.+)", section_text)
    if output_match:
        output_desc = output_match.group(1).strip()

    return WorkflowStep(
        index=index,
        title=title,
        owner=owner,
        instructions=instructions,
        output_description=output_desc,
        raw_text=full_text,
        collaborators=collaborators,
    )


def classify_step_owner(owner_text: str) -> str:
    """Classify a step owner into a normalized category.

    Returns one of: "hr", "coo", "employees", "coo_hr", "ceo", "applicant", "senior", "unknown"
    """
    text = owner_text.lower().replace(" ", "")
    if "coo" in text and "hr" in text:
        return "coo_hr"
    if "ceo" in text:
        return "ceo"
    if "hr" in text:
        return "hr"
    if "coo" in text:
        return "coo"
    # Chinese patterns
    if "每位" in owner_text or "全体" in owner_text or "参与员工" in owner_text or "参会人员" in owner_text:
        return "employees"
    if "高级" in owner_text or "上级" in owner_text:
        return "senior"
    if "申请人" in owner_text:
        return "applicant"
    if "项目负责人" in owner_text:
        return "coo"  # project lead is under COO
    if "候选员工" in owner_text:
        return "senior"
    return "unknown"
