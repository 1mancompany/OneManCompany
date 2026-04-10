"""Base agent utilities shared across all LangChain agents."""

from __future__ import annotations
from datetime import datetime
from loguru import logger
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage

import onemancompany.core.config as _cfg

from onemancompany.core.config import (
    AGENT_DIR_NAME,
    EMPLOYEES_DIR,
    BLOCK_KEY_TEXT,
    BLOCK_KEY_TYPE,
    BLOCK_TYPE_TEXT,
    CHAT_CLASS_ANTHROPIC,
    CHAT_CLASS_OPENAI,
    FOUNDING_IDS,
    MAX_SUMMARY_LEN,
    PF_DEPARTMENT,
    PF_LEVEL,
    PF_NAME,
    PF_NICKNAME,
    PF_ROLE,
    PF_RUNTIME,
    PF_STATUS,
    PF_CURRENT_TASK_SUMMARY,
    PROMPTS_DIR_NAME,
    PROVIDER_REGISTRY,
    SHARED_PROMPTS_DIR,
    SOUL_FILENAME,
    STATUS_IDLE,
    STATUS_WORKING,
    TALENT_PERSONA_FILENAME,
    VESSEL_DIR_NAME,
    WORKSPACE_DIR_NAME,
    employee_configs,
    get_provider,
    load_employee_skills,
    read_text_utf,
)
from onemancompany.core.models import AuthMethod
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state
from onemancompany.agents.prompt_builder import PromptBuilder

EFFICIENCY_PROMPT_FILENAME = "efficiency.md"
WORK_APPROACH_PROMPT_FILENAME = "work_approach.md"
TOOL_USAGE_PROMPT_FILENAME = "tool_usage.md"
ROLE_PROMPT_FILENAME = "role.md"
_LG_MESSAGES_KEY = "messages"  # LangGraph result dict key
_TC_NAME_KEY = "name"  # tool_call dict key
_TC_ATTR = "tool_calls"  # AIMessage attribute name
_UNKNOWN_TOOL = "unknown"
_NO_OUTPUT = "(no output)"


def _extract_text(content) -> str:
    """Extract text from AIMessage content, handling both str and list-of-blocks formats.

    Anthropic models return content as a list of blocks like
    [{"type": "text", "text": "..."}, {"type": "tool_use", ...}].
    OpenAI-compatible models return a plain string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get(BLOCK_KEY_TYPE) == BLOCK_TYPE_TEXT:
                parts.append(block.get(BLOCK_KEY_TEXT, ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""


def extract_final_content(result: dict) -> str:
    """Extract the final text content from a LangGraph ainvoke result.

    Walks backwards through messages to find the last AIMessage with non-empty
    text content, since the actual last message may be a ToolMessage.

    If no AIMessage has text, synthesizes a summary from the last tool calls
    and their results.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    messages = result.get(_LG_MESSAGES_KEY, [])
    if not messages:
        return ""

    # 1. Try: last AIMessage with non-empty text
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = _extract_text(msg.content)
            if text.strip():
                return text

    # 2. Fallback: summarize from tool calls + results at the end of the chain.
    #    Walk backwards collecting ToolMessages until we hit an AIMessage (the caller).
    tool_results: list[str] = []
    tool_names: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            tool_results.append(_extract_text(msg.content))
        elif isinstance(msg, AIMessage):
            # This AIMessage had tool_calls but no text — grab the tool names
            for tc in getattr(msg, _TC_ATTR, []) or []:
                tool_names.append(tc.get(_TC_NAME_KEY, _UNKNOWN_TOOL))
            break

    if tool_names:
        parts = [f"Executed: {', '.join(tool_names)}"]
        for name, res in zip(tool_names, reversed(tool_results)):
            snippet = res[:300] if res else ""
            parts.append(f"  {name} → {snippet}")
        return "\n".join(parts)

    # 3. Last resort — collect ALL tool calls from the conversation
    all_tool_calls = []
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, _TC_ATTR, []) or []:
                all_tool_calls.append(tc.get(_TC_NAME_KEY, _UNKNOWN_TOOL))
    if all_tool_calls:
        return f"Executed tools: {', '.join(all_tool_calls)}"
    return _extract_text(messages[-1].content) or _NO_OUTPUT


def _resolve_provider_key(provider_name: str, employee_api_key: str) -> str:
    """Resolve API key: employee-level → company-level (from Settings via PROVIDER_REGISTRY)."""
    if employee_api_key:
        return employee_api_key
    prov = get_provider(provider_name)
    if prov and prov.env_key:
        return getattr(_cfg.settings, prov.env_key, "")
    return ""


def make_llm(employee_id: str = "", temperature: float | None = None) -> BaseChatModel:
    """Create an LLM instance, using per-agent model config from employees/{id}/profile.yaml.

    Supports all providers in PROVIDER_REGISTRY (openrouter, openai, anthropic,
    kimi, deepseek, qwen, zhipu, groq, together, etc.).

    Args:
        employee_id: Use this employee's LLM config. Empty = company default.
        temperature: Override temperature. None = use employee/default value.
    """
    settings = _cfg.settings  # always read the latest (may be reloaded at runtime)
    model = settings.default_llm_model
    effective_temp = 0.7
    api_provider = settings.default_api_provider or "openrouter"
    api_key = ""

    if employee_id and employee_id in employee_configs:
        cfg = employee_configs[employee_id]
        if cfg.llm_model:
            model = cfg.llm_model
        effective_temp = cfg.temperature
        api_provider = cfg.api_provider
        api_key = cfg.api_key

    if temperature is not None:
        effective_temp = temperature

    prov = get_provider(api_provider)

    # --- Anthropic (non-OpenAI-compatible) ---
    if prov and prov.chat_class == CHAT_CLASS_ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        auth_method = ""
        if employee_id and employee_id in employee_configs:
            auth_method = employee_configs[employee_id].auth_method
        if not auth_method:
            auth_method = settings.anthropic_auth_method

        # Use OAuth token if auth_method is oauth, otherwise use API key
        if auth_method == AuthMethod.OAUTH:
            effective_key = api_key or settings.anthropic_oauth_token or settings.anthropic_api_key
        else:
            effective_key = _resolve_provider_key(api_provider, api_key)

        if effective_key:
            extra_headers = {}
            if auth_method == AuthMethod.OAUTH or effective_key.startswith("sk-ant-oat"):
                extra_headers["anthropic-beta"] = "oauth-2025-04-20"
            return ChatAnthropic(
                model=model,
                api_key=effective_key,
                temperature=effective_temp,
                max_retries=3,
                timeout=300.0,
                default_headers=extra_headers or None,
            )

    # --- OpenAI-compatible providers (openrouter, openai, kimi, deepseek, etc.) ---
    if prov and prov.chat_class == CHAT_CLASS_OPENAI:
        effective_key = _resolve_provider_key(api_provider, api_key)
        if effective_key:
            base_url = prov.base_url
            # Allow custom base_url override: provider-specific or global
            if api_provider == "openrouter":
                base_url = settings.openrouter_base_url
            elif api_provider == "custom" or (settings.default_api_base_url and api_provider == settings.default_api_provider):
                base_url = settings.default_api_base_url
            return ChatOpenAI(
                model=model,
                api_key=effective_key,
                base_url=base_url,
                temperature=effective_temp,
                max_retries=3,
                request_timeout=300.0,
                stream_usage=True,
            )

    # --- Fallback: unknown provider or no key → fall back to openrouter with default model ---
    if api_provider != "openrouter":
        logger.debug("Provider '{}' has no key, falling back to openrouter default", api_provider)
        model = settings.default_llm_model

    fallback_key = settings.openrouter_api_key
    if not fallback_key:
        logger.warning("make_llm: no API key for provider '{}' and no OpenRouter fallback key; LLM calls will fail", api_provider)

    return ChatOpenAI(
        model=model,
        api_key=fallback_key,
        base_url=settings.openrouter_base_url,
        temperature=effective_temp,
        max_retries=3,
        request_timeout=300.0,
        stream_usage=True,
    )


# ---------------------------------------------------------------------------
# Overhead cost tracking — accumulates all LLM usage into company_state
# ---------------------------------------------------------------------------

def _record_overhead(
    category: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    *,
    employee_id: str = "",
    task_id: str = "",
) -> None:
    """Accumulate an LLM call's cost into company_state.overhead_costs."""
    from onemancompany.core.models import CostRecord

    record = CostRecord(
        category=category,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        employee_id=employee_id or None,
        task_id=task_id or None,
    )
    company_state.overhead_costs.add(record)


async def tracked_ainvoke(
    llm,
    messages,
    *,
    category: str = "other",
    employee_id: str = "",
    project_id: str = "",
) -> "Any":
    """Call llm.ainvoke(messages) and record token usage.

    - Always accumulates into company_state.overhead_costs (global view).
    - If project_id is set, also records into the project cost breakdown.
    - Returns the raw AIMessage result so callers need no changes.
    """
    from onemancompany.core.model_costs import get_model_cost

    result = await llm.ainvoke(messages)

    # Extract token usage from response_metadata, fallback to usage_metadata
    meta = getattr(result, "response_metadata", {}) or {}
    usage = meta.get("usage", {}) or meta.get("token_usage", {}) or {}
    input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
    if not input_tokens and not output_tokens:
        usage_meta = getattr(result, "usage_metadata", None)
        if usage_meta and isinstance(usage_meta, dict):
            input_tokens = usage_meta.get("input_tokens", 0)
            output_tokens = usage_meta.get("output_tokens", 0)

    # Determine model name
    model_name = meta.get("model_name", "") or meta.get("model", "")
    if not model_name:
        # Try to get from employee config
        cfg = employee_configs.get(employee_id)
        model_name = cfg.llm_model if cfg and cfg.llm_model else _cfg.settings.default_llm_model

    # Compute cost: prefer provider-reported cost, fallback to catalog price
    provider_cost = usage.get("cost") if usage else None
    if provider_cost is not None and provider_cost:
        cost_usd = float(provider_cost)
    elif input_tokens or output_tokens:
        costs = get_model_cost(model_name)
        cost_usd = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
    else:
        cost_usd = 0.0

    # Record to project if applicable
    if project_id and (input_tokens or output_tokens):
        from onemancompany.core.project_archive import record_project_cost
        record_project_cost(project_id, employee_id, model_name, input_tokens, output_tokens, cost_usd)

    # Always record overhead
    _record_overhead(category, model_name, input_tokens, output_tokens, cost_usd)

    # Write to project-level LLM trace JSONL (legacy per-event trace)
    if project_id:
        from datetime import datetime, timezone
        from onemancompany.core.claude_session import write_llm_trace
        prompt_text = messages if isinstance(messages, str) else str(messages)
        response_text = getattr(result, "content", "") or ""
        write_llm_trace(project_id, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "employee_id": employee_id,
            "source": "langchain",
            "role": "user", "type": "prompt",
            "content": prompt_text,
            "category": category,
        })
        write_llm_trace(project_id, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "employee_id": employee_id,
            "source": "langchain",
            "role": "assistant", "type": "text",
            "content": response_text if isinstance(response_text, str) else str(response_text),
            "model": model_name,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        })

        # Write Debug trace (full conversation record for fine-tuning)
        try:
            from onemancompany.core.llm_trace import write_debug_trace_async
            from onemancompany.core.project_archive import get_project_dir
            _proj_dir = get_project_dir(project_id)
            if _proj_dir:
                # Resolve node_id from contextvar if available
                _node_id = ""
                try:
                    from onemancompany.core.agent_loop import _current_task_id
                    _node_id = _current_task_id.get("")
                except Exception as _e:
                    logger.debug("[debug_trace] failed to resolve node_id: {}", _e)
                # Build structured message list from input
                _debug_msgs = messages if isinstance(messages, list) else [messages]
                _debug_msgs_out = list(_debug_msgs) + [result]
                write_debug_trace_async(
                    _proj_dir,
                    employee_id=employee_id,
                    node_id=_node_id,
                    source="tracked_ainvoke",
                    messages=_debug_msgs_out,
                    model=model_name,
                    usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                )
        except Exception as e:
            logger.debug("[debug_trace] tracked_ainvoke write failed: {}", e)

    return result


# ---------------------------------------------------------------------------
# Standalone prompt builders — usable by any code that invokes an employee
# ---------------------------------------------------------------------------

def get_employee_talent_persona(employee_id: str) -> str:
    """Load talent persona from employees/{id}/prompts/talent_persona.md."""
    path = EMPLOYEES_DIR / employee_id / PROMPTS_DIR_NAME / TALENT_PERSONA_FILENAME
    if not path.exists():
        return ""
    content = read_text_utf(path).strip()
    return f"\n{content}" if content else ""


def _parse_skill_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md content.

    Returns (metadata_dict, body_without_frontmatter).
    """
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("---", 3)
    if end == -1:
        return {}, raw
    import yaml
    try:
        meta = yaml.safe_load(raw[3:end]) or {}
    except Exception:
        meta = {}
    body = raw[end + 3:].lstrip("\n")
    return meta, body


def get_employee_skills_index(employee_id: str) -> dict[str, dict]:
    """Build a skill name→{name, description} index for an employee.

    Returns dict like {"ontology": {"name": "ontology", "description": "..."}}.
    """
    skills = load_employee_skills(employee_id)
    index: dict[str, dict] = {}
    for folder_name, raw_content in skills.items():
        meta, _ = _parse_skill_frontmatter(raw_content)
        index[folder_name] = {
            "name": meta.get("name", folder_name),
            "description": meta.get("description", ""),
        }
    return index


def get_employee_skills_prompt(employee_id: str) -> str:
    """Build skill prompt: autoload skills inline, others as catalog index.

    Skills with ``autoload: true`` in frontmatter are injected fully.
    Others are listed as name+description for on-demand ``load_skill`` tool.
    """
    skills = load_employee_skills(employee_id)
    if not skills:
        return ""

    autoloaded: list[str] = []
    catalog: list[str] = []

    for folder_name, raw_content in skills.items():
        meta, body = _parse_skill_frontmatter(raw_content)
        display_name = meta.get("name", folder_name)
        description = meta.get("description", "")

        if meta.get("autoload"):
            autoloaded.append(f"### {display_name}\n{body}")
        else:
            line = f"- **{display_name}**"
            if description:
                line += f": {description}"
            catalog.append(line)

    parts: list[str] = []
    if autoloaded:
        parts.append("\n\n## Active Skills")
        parts.extend(autoloaded)
    if catalog:
        parts.append("\n\n## Available Skills")
        parts.append(
            "Use the `load_skill` tool to load a skill's full instructions "
            "before applying it.\n"
        )
        parts.extend(catalog)
    return "\n".join(parts)


def get_employee_tools_prompt(employee_id: str) -> str:
    """Build a prompt section listing all tools this employee is authorized to use.

    Single source of truth: reads from tool_registry, which already handles
    permission filtering (base/gated/role/asset categories).
    Asset tools with file contents are enriched from company_state.tools metadata.
    """
    from onemancompany.core.config import TOOLS_DIR
    from onemancompany.core.tool_registry import tool_registry

    tools = tool_registry.get_tools_for(employee_id)
    if not tools:
        return ""

    parts = ["\n\n## Your Authorized Tools:"]
    for t in tools:
        meta = tool_registry.get_meta(t.name)
        description = t.description or ""

        parts.append(f"\n### {t.name}")
        parts.append(description)

        # For asset tools, include file contents from the tool folder
        if meta and meta.source == "asset":
            office_tool = company_state.tools.get(meta.name)
            if office_tool and office_tool.folder_name and office_tool.files:
                tool_folder = TOOLS_DIR / office_tool.folder_name
                for fname in office_tool.files:
                    fpath = tool_folder / fname
                    if fpath.is_file():
                        try:
                            content = read_text_utf(fpath)
                        except (UnicodeDecodeError, ValueError):
                            content = f"[binary, {fpath.stat().st_size} bytes]"
                        parts.append(f"  - {fname}:\n```\n{content}\n```")

    # List company equipment (template tools) accessible via use_tool()
    # These are NOT langchain tools — employees call use_tool("tool_name") to access them.
    equipment_tools = [
        t for t in company_state.tools.values()
        if not t.allowed_users or employee_id in t.allowed_users
    ]
    if equipment_tools:
        parts.append("\n## Company Equipment (use via `use_tool(tool_name)`):")
        for t in equipment_tools:
            parts.append(f"- **{t.name}** ({t.id}): {t.description[:100] if t.description else 'No description'}")

    parts.append(
        "\n### Tool Selection Guide — Use the Right Tool\n\n"
        "| Task | Tool | NOT this |\n"
        "|------|------|----------|\n"
        "| Set company direction/culture/workflow | deposit_company_knowledge() | write on yaml files directly |\n"
        "| Read/modify any file | read() then edit() or write() | bash cat/sed/echo |\n"
        "| Search for files | glob_files() | bash find |\n"
        "| Search file contents | grep_search() | bash grep/rg |\n"
        "| Run shell commands | bash() | Only when no dedicated tool exists |\n"
        "| Assign work to a colleague | dispatch_child() | Email/Gmail |\n"
        "| Learn about colleagues | list_colleagues() | Reading profile files directly |\n"
        "| Discuss with colleagues | pull_meeting() | dispatch_child with chat-like messages |\n"
        "| View task details | read_node_detail() | Reading task_tree.yaml directly |\n"
        "| Current news/events/prices/data | web_search() | Using training data (likely outdated) |\n\n"
        "IMPORTANT:\n"
        "- Always prefer dedicated tools over generic file operations.\n"
        "- After modifying a file, verify the change: read() the file to confirm.\n"
        "- If a tool returns an error, read the message — it tells you what to do next.\n"
        "- **CRITICAL: If the task requires current/recent information (news, events, prices, "
        "market data, today's date-specific content), you MUST call web_search() BEFORE answering. "
        "Your training data is outdated — do NOT rely on it for anything time-sensitive.**"
    )

    parts.append("\n### Tool Usage Rules — Internal vs External")
    parts.append(
        "- **Internal task dispatch**: Use dispatch_child() to assign work to employees. "
        "NEVER use Gmail/email for internal task routing or employee coordination.\n"
        "- **CEO escalation**: Use dispatch_child(\"00001\", description) to request CEO help. "
        "Escalate when:\n"
        "  - You need to purchase something (API keys, SaaS subscriptions, domains, etc.)\n"
        "  - You need actions outside the system (manual approval, signing contracts, legal compliance)\n"
        "  - You need external accounts or access permissions created\n"
        "  - The task exceeds your capabilities and cannot be delegated to another employee\n"
        "  - The task involves external commitments or brand representation\n"
        "  - You are blocked and no available tool or colleague can unblock you\n"
        "- **External communication**: Use Gmail ONLY for people OUTSIDE the company "
        "(clients, vendors, partners, third parties).\n"
        "- **Task independence**: Every task you receive is a NEW, independent assignment. "
        "NEVER refuse a task because a similar one was done before. Past projects in your "
        "work history are COMPLETED — they do not block new work on the same topic."
    )
    return "\n".join(parts)


class BaseAgentRunner:
    """Thin wrapper around create_react_agent that publishes events."""

    role: str = "agent"
    employee_id: str = ""  # maps to company_state.employees key
    _agent = None  # subclasses set this to a LangGraph compiled graph
    _agent_tools = None  # cached tools list for agent rebuild
    _last_usage: dict = {}  # token usage from last run_streamed() call

    def _refresh_agent(self) -> None:
        """Rebuild the LangGraph agent with a fresh LLM instance.

        Called before each task execution so that model changes in
        profile.yaml take effect without server restart.
        """
        if self._agent_tools is None:
            return  # subclass didn't store tools — skip refresh
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=self._agent_tools,
        )

    async def _publish(self, event_type: str, payload: dict) -> None:
        await event_bus.publish(
            CompanyEvent(type=event_type, payload=payload, agent=self.role)
        )

    async def run_streamed(self, task: str, on_log=None) -> str:
        """Run agent with streaming, calling on_log(type, content) for each LLM step.

        Uses astream_events to capture LLM input/output and tool calls in real time,
        then returns the final AI message content.
        Falls back to regular run() if _agent is not set or on_log is None.
        """
        if not on_log:
            return await self.run(task)  # run() calls _refresh_agent()

        self._refresh_agent()
        from langchain_core.messages import HumanMessage, SystemMessage

        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"{self.role} analyzing: {task}"})

        prompt = self._build_full_prompt()
        messages_input = {
            "messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]
        }

        final_content = ""
        total_input_tokens = 0
        total_output_tokens = 0
        provider_cost: float | None = None  # provider-reported cost (e.g. OpenRouter)
        model_used = ""
        last_tool_calls: list[str] = []  # track tool names for fallback
        last_tool_results: list[str] = []
        # Debug trace: accumulate full message objects from streaming events
        debug_messages: list = []

        async for event in self._agent.astream_events(
            messages_input, version="v2", config={"recursion_limit": 50},
        ):
            kind = event.get("event", "")
            data = event.get("data", {})
            if kind == "on_chat_model_start":
                inp = data.get("input", "")
                if isinstance(inp, list) and inp:
                    # Capture all input messages for SFT on first LLM call
                    if not debug_messages:
                        debug_messages.extend(inp)
                    last_msg = inp[-1]
                    if hasattr(last_msg, "content"):
                        content = last_msg.content or ""
                        if isinstance(content, str):
                            on_log("llm_input", f"[{type(last_msg).__name__}] {content}")
            elif kind == "on_chat_model_end":
                output = data.get("output", None)
                if output:
                    # Capture AI message for Debug trace
                    debug_messages.append(output)
                    # Extract token usage — try response_metadata first, then usage_metadata
                    meta = getattr(output, "response_metadata", {}) or {}
                    usage = meta.get("usage", {}) or meta.get("token_usage", {}) or {}
                    if usage:
                        total_input_tokens += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
                        # Provider-reported cost (e.g. OpenRouter includes "cost" in token_usage)
                        if "cost" in usage and usage["cost"]:
                            provider_cost = (provider_cost or 0.0) + float(usage["cost"])
                    else:
                        # Streaming mode: usage lives in usage_metadata (requires stream_usage=True)
                        usage_meta = getattr(output, "usage_metadata", None)
                        if usage_meta and isinstance(usage_meta, dict):
                            total_input_tokens += usage_meta.get("input_tokens", 0)
                            total_output_tokens += usage_meta.get("output_tokens", 0)
                        else:
                            logger.debug("[COST] on_chat_model_end: no usage data for employee={}, meta_keys={}", self.employee_id, list(meta.keys()))
                    if not model_used:
                        model_used = meta.get("model_name", "") or meta.get("model", "")

                    if hasattr(output, "content"):
                        text = _extract_text(output.content)
                        if text.strip():
                            final_content = text  # track last AI output
                            on_log("llm_output", text)
                        tool_calls = getattr(output, "tool_calls", None)
                        if tool_calls:
                            last_tool_calls = []
                            last_tool_results = []
                            for tc in tool_calls:
                                name = tc.get("name", "?")
                                args_dict = tc.get("args", {})
                                args = str(args_dict)
                                last_tool_calls.append(name)
                                on_log("tool_call", {
                                    "tool_name": name,
                                    "tool_args": args_dict,
                                    "content": f"{name}({args})",
                                })
            elif kind == "on_tool_end":
                output = data.get("output", "")
                name = event.get("name", "tool")
                result_str = str(output)
                last_tool_results.append(f"{name} → {result_str}")
                on_log("tool_result", {
                    "tool_name": name,
                    "tool_result": result_str,
                    "content": f"{name} → {result_str}",
                })
                # Capture ToolMessage for Debug trace
                raw_output = data.get("output")
                if raw_output and hasattr(raw_output, "content"):
                    debug_messages.append(raw_output)

        # If no text content from LLM, synthesize from last tool calls
        if not final_content.strip() and last_tool_calls:
            parts = [f"Executed: {', '.join(last_tool_calls)}"]
            parts.extend(last_tool_results)
            final_content = "\n".join(parts)

        # Compute cost: prefer provider-reported cost, fallback to catalog price
        _model = model_used or self._get_model_name()
        if provider_cost is not None:
            _cost_usd = provider_cost
        elif total_input_tokens or total_output_tokens:
            from onemancompany.core.model_costs import get_model_cost
            _costs = get_model_cost(_model)
            _cost_usd = (total_input_tokens * _costs["input"] + total_output_tokens * _costs["output"]) / 1_000_000
        else:
            _cost_usd = 0.0

        # Store usage for caller to read
        self._last_usage = {
            "model": _model,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "cost_usd": _cost_usd,
        }

        # Record streaming usage into overhead
        if total_input_tokens or total_output_tokens or _cost_usd > 0:
            _record_overhead("agent_task", _model, total_input_tokens, total_output_tokens, _cost_usd)

        # Write Debug trace from accumulated streaming messages
        if debug_messages:
            # Prepend system+user prompt if missing from streaming events
            initial_msgs = messages_input.get("messages", [])
            if initial_msgs and debug_messages and not any(
                getattr(m, "type", None) == "system" or (isinstance(m, dict) and m.get("role") == "system")
                for m in debug_messages
            ):
                debug_messages = list(initial_msgs) + debug_messages
            self._write_debug_trace(
                {_LG_MESSAGES_KEY: debug_messages},
                self._last_usage,
            )

        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": self.role, "summary": (final_content or "")[:MAX_SUMMARY_LEN]})
        return final_content

    def _extract_and_record_usage(self, result: dict) -> dict:
        """Extract total token usage from a LangGraph ainvoke result and record it.

        Iterates over all AIMessages in the result to sum up token usage from
        multi-step tool-use loops. Updates ``_last_usage`` and records to
        company overhead costs.

        Returns the ``_last_usage`` dict.
        """
        from langchain_core.messages import AIMessage
        from onemancompany.core.model_costs import get_model_cost

        total_input = 0
        total_output = 0
        provider_cost: float | None = None
        model = ""
        for msg in result.get(_LG_MESSAGES_KEY, []):
            if not isinstance(msg, AIMessage):
                continue
            meta = getattr(msg, "response_metadata", {}) or {}
            usage = meta.get("usage", {}) or meta.get("token_usage", {}) or {}
            msg_input = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            msg_output = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
            # Provider-reported cost (e.g. OpenRouter)
            if "cost" in usage and usage["cost"]:
                provider_cost = (provider_cost or 0.0) + float(usage["cost"])
            # Fallback: streaming mode puts usage in usage_metadata
            if not msg_input and not msg_output:
                usage_meta = getattr(msg, "usage_metadata", None)
                if usage_meta and isinstance(usage_meta, dict):
                    msg_input = usage_meta.get("input_tokens", 0)
                    msg_output = usage_meta.get("output_tokens", 0)
            total_input += msg_input
            total_output += msg_output
            if not model:
                model = meta.get("model_name", "") or meta.get("model", "")

        model = model or self._get_model_name()

        # Cost: prefer provider-reported, fallback to catalog price
        if provider_cost is not None:
            cost_usd = provider_cost
        elif total_input or total_output:
            costs = get_model_cost(model)
            cost_usd = (total_input * costs["input"] + total_output * costs["output"]) / 1_000_000
        else:
            cost_usd = 0.0

        self._last_usage = {
            "model": model,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "cost_usd": cost_usd,
        }

        if total_input or total_output or cost_usd > 0:
            _record_overhead(
                "agent_task", model, total_input, total_output, cost_usd,
                employee_id=self.employee_id,
            )

        return self._last_usage

    # Class-level cache: serialized tool schemas per employee_id
    _debug_tool_cache: dict[str, list] = {}

    def _write_debug_trace(self, result: dict, usage_info: dict) -> None:
        """Write a complete SFT training record from a LangGraph ainvoke result."""
        try:
            from pathlib import Path as _Path

            from onemancompany.core.agent_loop import _current_vessel
            from onemancompany.core.llm_trace import write_debug_trace_async
            from onemancompany.core.tool_registry import tool_registry

            vessel = _current_vessel.get(None)
            if not vessel:
                return
            # Get project_dir from current running task entry
            entry = vessel.manager._current_entries.get(self.employee_id)
            if not entry:
                return
            from onemancompany.core.task_tree import get_tree
            tree = get_tree(entry.tree_path)
            node = tree.get_node(entry.node_id) if tree else None
            project_dir = (node.project_dir if node else "") or str(
                _Path(entry.tree_path).parent
            )
            if not project_dir:
                return

            messages = result.get(_LG_MESSAGES_KEY, [])

            # Cache tool schemas — they don't change within a session
            if self.employee_id not in self._debug_tool_cache:
                from onemancompany.core.llm_trace import _serialize_tool_schema
                tools = tool_registry.get_proxied_tools_for(self.employee_id)
                serialized = []
                for t in tools:
                    try:
                        serialized.append(_serialize_tool_schema(t))
                    except Exception:
                        logger.debug("[debug_trace] failed to serialize tool {}", getattr(t, "name", "?"))
                self._debug_tool_cache[self.employee_id] = serialized

            write_debug_trace_async(
                project_dir,
                employee_id=self.employee_id,
                node_id=entry.node_id,
                source="langchain",
                messages=messages,
                tools=self._debug_tool_cache[self.employee_id],
                model=usage_info.get("model", ""),
                usage={
                    "input_tokens": usage_info.get("input_tokens", 0),
                    "output_tokens": usage_info.get("output_tokens", 0),
                },
            )
        except Exception as e:
            logger.debug("[debug_trace] failed to write Debug trace record for {}: {}", self.employee_id, e)

    def _get_model_name(self) -> str:
        """Return the LLM model name configured for this employee."""
        cfg = employee_configs.get(self.employee_id)
        return cfg.llm_model if cfg and cfg.llm_model else _cfg.settings.default_llm_model

    def _build_prompt(self) -> str:
        """Build the full system prompt using PromptBuilder.

        Subclasses should override _customize_prompt(pb) to add role-specific
        sections rather than overriding this method directly.
        """
        pb = self._build_prompt_builder()
        return pb.build()

    def _build_full_prompt(self) -> str:
        """Build prompt with task history injected from the agent loop."""
        prompt = self._build_prompt()
        from onemancompany.core.agent_loop import _current_vessel
        loop = _current_vessel.get(None)
        if loop:
            prompt += loop.get_history_context()
        return prompt

    def _set_status(self, status: str) -> None:
        """Set this agent's employee status (idle/working/in_meeting)."""
        # Runtime status is persisted to disk via store; no in-memory update needed.
        pass

    # ----- Standardized role identity (Who You Are / NEVER / Actions) -----

    def _get_role_identity_section(self) -> str:
        """Build standardized identity sections: Who You Are, NEVER Do, Core Actions.

        Founding agents override this to provide domain-specific identity.
        Regular employees get archetype-based identity (manager/executor)
        from the shared ``build_role_identity()`` function.

        For LangChain employees, identity is in the system prompt (here).
        For Claude CLI employees, identity is in the task prompt (vessel.py).
        Both call the same ``build_role_identity()`` — single source of truth.
        """
        from onemancompany.core.vessel import build_role_identity
        return build_role_identity(self.employee_id)

    def _get_skills_prompt_section(self) -> str:
        """Load skill files from employees/{id}/skills/ and build a prompt section."""
        return get_employee_skills_prompt(self.employee_id)

    def _get_tools_prompt_section(self) -> str:
        """Build a prompt section listing authorized tools for this agent."""
        return get_employee_tools_prompt(self.employee_id)

    def _get_task_lifecycle_section(self) -> str:
        """Inject brief task lifecycle reference — full doc available via load_skill."""
        return (
            "\n\n## Task Lifecycle\n"
            "Tasks follow: pending → processing → completed → accepted → finished.\n"
            "→ load_skill(\"task_lifecycle\") for the full state machine, transitions, and task tree model."
        )

    def _get_filesystem_section(self) -> str:
        """Inform employees that all company data is stored on the filesystem."""
        from onemancompany.core.config import DATA_ROOT
        data_root_abs = str(DATA_ROOT.resolve())
        return (
            "\n\n## File Storage\n"
            "All company data — projects, documents, reports, employee files — "
            "is stored on the filesystem. There is NO database. "
            "When you need to read or write company data, use file operations.\n"
            f"- Company data root: {data_root_abs}\n"
        )

    def _get_dynamic_context_section(self) -> str:
        """Build a dynamic context section with current datetime, team state, and workload."""
        parts = ["\n\n## Current Context"]

        # Datetime
        now = datetime.now()
        parts.append(f"- Current time: {now.strftime('%Y-%m-%d %H:%M')}")

        # Team roster summary (compact)
        from onemancompany.core.store import load_all_employees
        all_emps = load_all_employees()
        team_lines = []
        for eid, edata in all_emps.items():
            if eid == self.employee_id:
                continue
            runtime = edata.get(PF_RUNTIME, {})
            status = runtime.get(PF_STATUS, STATUS_IDLE)
            task_summary = runtime.get(PF_CURRENT_TASK_SUMMARY, "")
            status_tag = f"[{status}]" if status != STATUS_IDLE else ""
            task_hint = f" — {task_summary}" if task_summary else ""
            team_lines.append(
                f"  - {edata.get(PF_NAME, '')}({edata.get(PF_NICKNAME, '')}) ID:{eid} {edata.get(PF_ROLE, '')} Lv.{edata.get(PF_LEVEL, 1)}{status_tag}{task_hint}"
            )
        if team_lines:
            parts.append("- Team:\n" + "\n".join(team_lines))

        # Active projects (brief)
        from onemancompany.core.state import get_active_tasks
        active_tasks = get_active_tasks()
        if active_tasks:
            active = []
            for t in active_tasks[:5]:
                active.append(f"  - [{t.routed_to}] {t.task[:60]}")
            parts.append("- Active tasks:\n" + "\n".join(active))

        # Custom settings (target_email, polling_interval, etc.)
        from onemancompany.core.config import load_custom_settings
        custom = load_custom_settings(self.employee_id)
        if custom:
            settings_lines = [f"  - {k}: {v}" for k, v in custom.items()]
            parts.append("- Your settings:\n" + "\n".join(settings_lines))

        return "\n".join(parts)

    def _load_prompt_file(self, filename: str) -> str | None:
        """Load prompt from employee's prompts/ dir."""
        path = EMPLOYEES_DIR / self.employee_id / PROMPTS_DIR_NAME / filename
        if path.exists():
            return read_text_utf(path)
        return None

    @staticmethod
    def _load_shared_prompt(filename: str) -> str | None:
        """Load from company/shared_prompts/."""
        path = SHARED_PROMPTS_DIR / filename
        if path.exists():
            return read_text_utf(path)
        return None

    def _get_company_direction_section(self) -> str:
        """Build a prompt section from company direction/strategy."""
        from onemancompany.core.store import load_direction
        direction = load_direction()
        if not direction:
            return ""
        return (
            f"\n\n## Company Direction\n"
            f"{direction}\n"
            f"All work should align with the company direction, ensuring output is consistent with company strategy.\n"
        )

    def _get_soul_section(self) -> str:
        """Load the employee's self-maintained SOUL.md knowledge file."""
        soul_path = EMPLOYEES_DIR / self.employee_id / WORKSPACE_DIR_NAME / SOUL_FILENAME
        if soul_path.exists():
            try:
                content = read_text_utf(soul_path).strip()
                if content:
                    return (
                        "## Your Personal Knowledge (SOUL.md)\n"
                        "This is your self-maintained knowledge file. You wrote this yourself "
                        "based on past experience. Use it to inform your work.\n\n"
                        f"{content}"
                    )
            except Exception as exc:
                logger.debug("Failed to read SOUL.md for {}: {}", self.employee_id, exc)
        return ""

    def _build_prompt_builder(self) -> PromptBuilder:
        """Build a PromptBuilder with all standard sections. Override _customize_prompt() to modify."""
        pb = PromptBuilder()
        # --- Founding team custom sections (overridden via _get_role_identity_section) ---
        pb.add("role_identity", self._get_role_identity_section(), priority=8)
        # --- Agent-level operational sections ---
        pb.add("soul", self._get_soul_section(), priority=15)
        pb.add("skills", self._get_skills_prompt_section(), priority=30)
        pb.add("tools", self._get_tools_prompt_section(), priority=35)
        pb.add("direction", self._get_company_direction_section(), priority=40)
        # NOTE: role identity (non-founding), culture, guidance, work principles,
        # talent persona, and CLAUDE.md are ALL injected via
        # _build_company_context_block() in every task prompt (vessel.py).
        # They are NOT in the system prompt to avoid duplication.
        pb.add("task_lifecycle", self._get_task_lifecycle_section(), priority=65)
        pb.add("filesystem", self._get_filesystem_section(), priority=66)
        pb.add("context", self._get_dynamic_context_section(), priority=70)
        pb.add("efficiency", self._get_efficiency_guidelines_section(), priority=80)
        self._customize_prompt(pb)
        self._load_agent_prompt_sections(pb)
        return pb

    def _customize_prompt(self, pb: PromptBuilder) -> None:
        """Override in subclasses to add/remove/modify prompt sections."""
        pass

    def _load_agent_prompt_sections(self, pb: PromptBuilder) -> None:
        """Load prompt sections from vessel/vessel.yaml or agent/manifest.yaml (fallback)."""
        from onemancompany.core.vessel_config import load_vessel_config

        emp_dir = EMPLOYEES_DIR / self.employee_id
        config = load_vessel_config(emp_dir)

        if config.context.prompt_sections:
            # Resolve files from vessel/ first, then agent/
            for ps in config.context.prompt_sections:
                if not ps.name or not ps.file:
                    continue
                content_path = None
                for search_dir in [emp_dir / VESSEL_DIR_NAME, emp_dir / AGENT_DIR_NAME]:
                    candidate = search_dir / ps.file
                    if candidate.exists():
                        content_path = candidate
                        break
                if not content_path:
                    continue
                try:
                    content = read_text_utf(content_path)
                    pb.add(ps.name, content, priority=ps.priority)
                except Exception as _e:
                    logger.warning("Failed to load prompt section %s: %s", ps.name, _e)

    def _get_efficiency_guidelines_section(self) -> str:
        """Build efficiency guidelines to reduce wasted tokens and loops."""
        # Try loading from shared prompts file first
        content = self._load_prompt_file(EFFICIENCY_PROMPT_FILENAME) or self._load_shared_prompt(EFFICIENCY_PROMPT_FILENAME)
        if content:
            return "\n\n" + content

        return (
            "\n\n## Efficiency Rules (MUST follow)\n"
            "- Do NOT explore the filesystem unless the task explicitly requires it.\n"
            "- Do NOT re-read files you have already read in this task.\n"
            "- Do NOT create unnecessary planning steps — act directly on clear instructions.\n"
            "- Do NOT call tools repeatedly with the same arguments.\n"
            "- If a tool call fails, try a different approach instead of retrying the same call.\n"
            "- Produce output first, verify once, then finish. Do NOT loop.\n"
            "- Keep your final response concise — report what you did and the result, not your thought process.\n"
        )


class EmployeeAgent(BaseAgentRunner):
    """Generic agent runner for newly hired employees.

    Uses COMMON_TOOLS and builds a prompt from the employee's profile,
    skills, tools, work principles, and company culture.
    """

    def __init__(self, employee_id: str) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self.employee_id = employee_id
        from onemancompany.core.store import load_employee as _load_emp
        emp_data = _load_emp(employee_id) or {}
        self.role = emp_data.get(PF_ROLE, "Employee")

        proxied_tools = tool_registry.get_proxied_tools_for(employee_id)
        self._authorized_tool_names: list[str] = [t.name for t in proxied_tools]
        self._agent_tools = proxied_tools

        self._agent = create_react_agent(
            model=make_llm(employee_id),
            tools=proxied_tools,
        )

    def _build_prompt(self) -> str:
        from onemancompany.core.store import load_employee as _load_emp
        emp_data = _load_emp(self.employee_id)
        if not emp_data:
            return "You are a company employee."

        pb = self._build_prompt_builder()

        emp_name = emp_data.get(PF_NAME, "")
        emp_nickname = emp_data.get(PF_NICKNAME, "")
        emp_role = emp_data.get(PF_ROLE, "Employee")
        emp_dept = emp_data.get(PF_DEPARTMENT, "")
        emp_level = emp_data.get(PF_LEVEL, 1)

        # 1. Role header: try employee's custom role.md, else default
        role_prompt = self._load_prompt_file(ROLE_PROMPT_FILENAME)
        if role_prompt:
            header = (role_prompt
                      .replace("{name}", emp_name)
                      .replace("{nickname}", emp_nickname)
                      .replace("{role}", emp_role)
                      .replace("{department}", emp_dept)
                      .replace("{level}", str(emp_level)))
        else:
            header = (
                f"You are {emp_name} (nickname: {emp_nickname}), "
                f"a {emp_role} in {emp_dept} (Lv.{emp_level}).\n"
                f"Follow instructions from your managers, complete tasks thoroughly, "
                f"and collaborate with colleagues when needed.\n"
            )
        pb.add("role", header, priority=10)

        # 2. Work Approach: from files or hardcoded
        work_approach = (self._load_prompt_file(WORK_APPROACH_PROMPT_FILENAME)
                         or self._load_shared_prompt(WORK_APPROACH_PROMPT_FILENAME)
                         or (
                             "## Work Approach\n"
                             "1. Review: FIRST use ls to see what already exists in the project workspace. "
                             "Read key files to understand what's been done — never start from scratch blindly.\n"
                             "2. Analyze: Understand the task requirements in context of existing deliverables.\n"
                             "3. Execute: Produce the deliverable — iterate on what exists, don't duplicate.\n"
                             "4. Verify: Check your output once (run code, proofread doc). Fix if needed.\n"
                             "5. Save & Report: Save output to project workspace, then report completion.\n"
                         ))
        pb.add("work_approach", work_approach, priority=15)

        # 3. Tool Usage: from files or hardcoded
        tool_usage = (self._load_prompt_file(TOOL_USAGE_PROMPT_FILENAME)
                      or self._load_shared_prompt(TOOL_USAGE_PROMPT_FILENAME)
                      or (
                          "## Tool Usage\n"
                          "- ls: ALWAYS call this first to see existing project files.\n"
                          "- read / ls: Read existing files to understand context before working.\n"
                          "- write: Save ALL deliverables to the project workspace.\n"
                          "- dispatch_child: Delegate sub-work to colleagues if needed.\n"
                          "- pull_meeting: ONLY for multi-person communication/discussion (2+ colleagues). "
                          "Never call a meeting with yourself alone — if you need to think, just think internally.\n"
                          "- use_tool: Access company equipment/tools registered by COO.\n"
                      ))
        pb.add("tool_usage", tool_usage, priority=20)

        # 3.5. Company directory map — so agents know where files live
        from onemancompany.core.config import COMPANY_DIR, WORKFLOWS_DIR, PROJECTS_DIR
        dir_map = (
            "## Company Directory Map\n"
            f"- Company root: {COMPANY_DIR}\n"
            f"- Workflows / SOPs: {WORKFLOWS_DIR}\n"
            f"- Projects: {PROJECTS_DIR}\n"
            "- Use `ls()` with these absolute paths or relative paths under company root (e.g. `ls('business/workflows')`).\n"
            "- Use `read()` with absolute paths to read any file.\n"
            "- Project deliverables go in the project workspace path given in your task description.\n"
        )
        pb.add("directory_map", dir_map, priority=21)

        # 4. Unauthorized tools section
        pb.add("unauthorized_tools", self._get_unauthorized_tools_section(), priority=36)

        return pb.build()

    def _get_unauthorized_tools_section(self) -> str:
        """No longer needed — all company tools are available to all employees."""
        return ""

    async def run(self, task: str) -> str:
        self._refresh_agent()
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"{self.role} analyzing: {task}"})

        initial_msgs = [
            SystemMessage(content=self._build_full_prompt()),
            HumanMessage(content=task),
        ]
        result = await self._agent.ainvoke({"messages": initial_msgs})

        usage_info = self._extract_and_record_usage(result)
        final = extract_final_content(result)

        # Write Debug trace — full conversation for fine-tuning
        # Ensure system+user messages are included (LangGraph may strip system from result)
        result_msgs = result.get(_LG_MESSAGES_KEY, [])
        if result_msgs and not any(
            getattr(m, "type", None) == "system" or (isinstance(m, dict) and m.get("role") == "system")
            for m in result_msgs
        ):
            result[_LG_MESSAGES_KEY] = list(initial_msgs) + result_msgs
        self._write_debug_trace(result, usage_info)

        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": self.role, "summary": final[:MAX_SUMMARY_LEN]})
        return final

