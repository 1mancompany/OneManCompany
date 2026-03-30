"""Workflow Engine — parses markdown workflow documents and executes their steps.

Each workflow .md file in business/workflows/ defines a sequence of stages (phases).
The engine extracts structured step definitions from the markdown and provides
an executor that runs each step dynamically, using the _chat() and _publish()
helpers for real-time frontend updates.

Markdown format expected (by convention used in business/workflows/):

    # Workflow Title

    - **Flow ID**: some_id
    - **Owner**: HR
    - **Collaborators**: COO, all project members
    - **Trigger**: ...

    ---

    ## Phase 1: Step Title

    - **Responsible**: HR / COO / Each participating employee / ...
    - **Steps**:
      1. Do something
      2. Do something else
    - **Output**: Description of output

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
    title: str  # e.g., "Phase 1: Review Preparation"
    owner: str  # e.g., "HR", "COO", "Each participating employee", "COO + HR"
    instructions: list[str]  # numbered sub-steps
    output_description: str  # what this step produces
    raw_text: str  # full markdown text of this section
    collaborators: str = ""  # optional collaborators at step level
    goal: str = ""  # what this step must achieve
    depends_on: list[int] = field(default_factory=list)  # indices of prerequisite phases


@dataclass
class WorkflowDefinition:
    """A fully parsed workflow document."""

    name: str  # workflow title from the H1 header
    flow_id: str  # Flow ID
    owner: str  # Owner
    collaborators: str  # Collaborators
    trigger: str  # Trigger
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
        flow_id_match = re.search(r"\*\*Flow ID\*\*:\s*(.+)", header_text)
        if flow_id_match:
            wf.flow_id = flow_id_match.group(1).strip()

        owner_match = re.search(r"\*\*Owner\*\*:\s*(.+)", header_text)
        if owner_match:
            wf.owner = owner_match.group(1).strip()

        collab_match = re.search(r"\*\*Collaborators\*\*:\s*(.+)", header_text)
        if collab_match:
            wf.collaborators = collab_match.group(1).strip()

        trigger_match = re.search(r"\*\*Trigger\*\*:\s*(.+)", header_text)
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

    _resolve_depends_on(wf.steps)
    return wf


def _parse_step_section(index: int, section_text: str) -> WorkflowStep | None:
    """Parse a single ## section into a WorkflowStep."""
    lines = section_text.strip().split("\n")
    if not lines:  # pragma: no cover – str.split always returns non-empty list
        return None

    title = lines[0].strip()
    full_text = "## " + section_text

    # Extract owner (Responsible)
    owner = ""
    owner_match = re.search(r"\*\*Responsible\*\*:\s*(.+)", section_text)
    if owner_match:
        owner = owner_match.group(1).strip()

    # Extract collaborators at step level
    collaborators = ""
    collab_match = re.search(r"\*\*Collaborators\*\*:\s*(.+)", section_text)
    if collab_match:
        collaborators = collab_match.group(1).strip()

    # Extract goal
    goal = ""
    goal_match = re.search(r"\*\*Goal\*\*:\s*(.+)", section_text)
    if goal_match:
        goal = goal_match.group(1).strip()

    # Extract raw depends_on phase number strings (resolved later)
    raw_depends_on: list[str] = []
    dep_match = re.search(r"\*\*Depends on\*\*:\s*(.+)", section_text)
    if dep_match:
        dep_raw = dep_match.group(1).strip()
        for part in dep_raw.split(","):
            phase_num_match = re.search(r"Phase\s+([\d.]+)", part.strip(), re.IGNORECASE)
            if phase_num_match:
                raw_depends_on.append(phase_num_match.group(1))

    # Extract numbered instructions from the Steps section
    instructions: list[str] = []
    in_steps = False
    for line in lines:
        stripped = line.strip()
        # Detect start of the Steps block
        if "**Steps**:" in stripped or "**Steps**:" in stripped:
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

    # Extract output description (Output)
    output_desc = ""
    output_match = re.search(r"\*\*Output\*\*:\s*(.+)", section_text)
    if output_match:
        output_desc = output_match.group(1).strip()

    step = WorkflowStep(
        index=index,
        title=title,
        owner=owner,
        instructions=instructions,
        output_description=output_desc,
        raw_text=full_text,
        collaborators=collaborators,
        goal=goal,
    )
    step._raw_depends_on = raw_depends_on  # type: ignore[attr-defined]
    return step


def _resolve_depends_on(steps: list[WorkflowStep]) -> None:
    """Resolve raw phase number strings into 0-based step indices in-place.

    Builds a map from phase number (e.g. "1", "1.5") to step index by
    scanning each step's title for a ``Phase N:`` prefix, then fills in
    ``step.depends_on`` from the temporary ``_raw_depends_on`` attribute.
    """
    phase_num_to_index: dict[str, int] = {}
    for step in steps:
        phase_match = re.match(r"Phase\s+([\d.]+)", step.title, re.IGNORECASE)
        if phase_match:
            phase_num_to_index[phase_match.group(1)] = step.index

    for step in steps:
        raw: list[str] = getattr(step, "_raw_depends_on", [])
        step.depends_on = [phase_num_to_index[num] for num in raw if num in phase_num_to_index]
        # Clean up temporary attribute set by _parse_step_section
        if hasattr(step, "_raw_depends_on"):
            del step._raw_depends_on  # type: ignore[attr-defined]


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
    # English patterns
    if "each" in text or "all" in text or "participating" in text or "attendees" in text:
        return "employees"
    if "senior" in text or "supervisor" in text:
        return "senior"
    if "applicant" in text or "requester" in text:
        return "applicant"
    if "projectlead" in text:
        return "coo"  # project lead is under COO
    if "candidate" in text:
        return "senior"
    return "unknown"
