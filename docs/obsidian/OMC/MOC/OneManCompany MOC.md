# OneManCompany (OMC) - Map of Content

> **From Skills to Talent: Organising Heterogeneous Agents as a Real-World Company**
> Zhengxu Yu, Yu Fu, Zhiyuan He, Yuxuan Huang, Lee Ka Yiu, Meng Fang, Weilin Luo, Jun Wang

## Core Concepts
- [[AI Organisation]] — the central thesis: organisation-level abstraction for multi-agent systems
- [[Talent-Container Architecture]] — separation of agent identity from runtime
- [[Organisational Interfaces]] — six typed interfaces (Execution, Task, Event, Storage, Context, Lifecycle)
- [[Talent Market]] — community-driven agent marketplace with three sourcing channels

## Execution & Coordination
- [[E2R Tree Search]] — Explore-Execute-Review loop for strategy search
- [[DAG Task Execution]] — AND-tree with dependency edges, FSM lifecycle
- [[Task Lifecycle FSM]] — 9-state finite state machine with formal guarantees
- [[Seven Invariants]] — DAG, mutual exclusion, idempotency, review termination, etc.

## Self-Evolution
- [[Individual Evolution]] — CEO 1-on-1, post-task reflection, working principles
- [[Organisation Evolution]] — project retrospectives, SOPs, HR lifecycle
- [[HR Performance Pipeline]] — periodic reviews, PIP, automated offboarding

## Experiments & Results
- [[PRDBench Results]] — 84.67% success rate, +15.48pp over SOTA
- [[Case Studies]] — content generation, game dev, audiobook, research survey

## Design Philosophy (Canvas)
- ![[Canvas/Abstraction Ladder]] — Skill → Talent → Organisation: why "organisation" is the right abstraction level
- ![[Canvas/Human-AI Organisation Parallel]] — structural isomorphism between human companies and AI agent systems
- ![[Canvas/Evolution Flywheel]] — three-level learning loop: Individual → Organisation → Market
- ![[Canvas/Six Interfaces as OS Kernel]] — six organisational interfaces mapped to OS kernel subsystems

## Implementation (Repo)
- [[Architecture Walkthrough]] — end-to-end request lifecycle: CEO input → task tree → scheduling → LLM → frontend
- ![[Canvas/Architecture Canvas]] — visual architecture diagram (Canvas)
- [[Repo Architecture]] — FastAPI + WebSocket backend, Canvas 2D + G6 frontend
- [[Employee Data Model]] — profile.yaml, guidance.yaml, work_principles.md
- [[Executor Backends]] — LangChainExecutor, ClaudeSessionExecutor, ScriptExecutor, CeoExecutor
- [[MCP Tool Bridge]] — MCP server for self-hosted employees
- [[Workflow Engine]] — markdown-driven workflow definitions
- [[Snapshot and Hot Reload]] — registry-based providers, graceful restart
- [[Unified CEO Comms]] — ConversationService replacing CeoBroker, pending queue + auto-reply
- [[Skill Hooks]] — CC-style lifecycle hooks for company-hosted agents
- [[Task Tree Visualization]] — G6-based interactive task tree with org-card nodes
- [[Product Management System]] — Product, Issue, Version, KR: first-principles design for Agile product management

## Related
- [[Skills vs Talents]] — capability-level vs organisation-level abstractions
- [[OS Kernel Analogy]] — mapping organisational interfaces to OS subsystems
- [[Related Work Comparison]] — MetaGPT, AutoGen, CrewAI, Paperclip, AIOS, etc.
