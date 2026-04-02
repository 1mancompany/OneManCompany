#!/usr/bin/env python3
"""
Debug Trace Visualizer — 将 OneManCompany 的 debug_trace.jsonl 解析并生成交互式 HTML 可视化页面。

Usage:
    python trace_visualizer.py <path_to_debug_trace.jsonl> [-o output.html] [--open]

Examples:
    python trace_visualizer.py debug_trace.jsonl
    python trace_visualizer.py debug_trace.jsonl -o my_trace.html --open
    python trace_visualizer.py path/to/iter_001/debug_trace.jsonl --open
"""

import argparse
import html as html_mod
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ─── JSONL Parsing ───────────────────────────────────────────────────────────

def parse_jsonl(path: str) -> list[dict]:
    """Parse the debug_trace.jsonl file, extracting tool calls from messages."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, 1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError as e:
                print(f"WARNING: Skipping line {line_num}, invalid JSON: {e}", file=sys.stderr)
                continue

            tool_calls = extract_tool_calls(obj.get("messages", []))

            model = obj.get("model", "")
            if "/" in model:
                model = model.rsplit("/", 1)[-1]

            usage = {}
            # usage can live in top-level or inside messages
            if "usage" in obj and isinstance(obj["usage"], dict):
                usage = obj["usage"]
            else:
                # try to find usage in the last message
                for msg in reversed(obj.get("messages", [])):
                    if isinstance(msg, dict) and "usage_metadata" in msg:
                        um = msg["usage_metadata"]
                        usage = {
                            "input_tokens": um.get("prompt_token_count", um.get("input_tokens", 0)),
                            "output_tokens": um.get("candidates_token_count", um.get("output_tokens", 0)),
                        }
                        break

            entries.append({
                "line": line_num,
                "ts": obj.get("ts", ""),
                "employee": obj.get("employee_id", "unknown"),
                "node_id": obj.get("node_id", ""),
                "model": model[:50],
                "tokens_in": usage.get("input_tokens", 0),
                "tokens_out": usage.get("output_tokens", 0),
                "tools": tool_calls,
            })
    return entries


def extract_tool_calls(messages: list) -> list[dict]:
    """Walk through the messages array and pair tool_calls with their results."""
    if not isinstance(messages, list):
        return []

    # Collect all tool call invocations and their results
    pending_calls: dict[str, dict] = {}  # tool_call_id -> {name, args}
    results: dict[str, dict] = {}        # tool_call_id -> result_summary
    ordered_ids: list[str] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")

        # --- Assistant messages may contain tool_calls ---
        if role == "assistant":
            # OpenAI / LangChain format: tool_calls array
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                fn = tc.get("function", {})
                name = fn.get("name", tc.get("name", "unknown"))
                args_raw = fn.get("arguments", tc.get("args", "{}"))
                args = _parse_args(args_raw)
                if tc_id and tc_id not in pending_calls:
                    ordered_ids.append(tc_id)
                pending_calls[tc_id] = {"name": name, "args": args}

            # Some formats embed tool_calls in content parts
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "tool_use":
                        tc_id = part.get("id", "")
                        name = part.get("name", "unknown")
                        args = part.get("input", {})
                        if not isinstance(args, dict):
                            args = _parse_args(args)
                        if tc_id and tc_id not in pending_calls:
                            ordered_ids.append(tc_id)
                        pending_calls[tc_id] = {"name": name, "args": args}

        # --- Tool result messages ---
        elif role in ("tool", "function"):
            tc_id = msg.get("tool_call_id", msg.get("id", ""))
            content = msg.get("content", "")
            results[tc_id] = _parse_result(content)

    # Merge into ordered list
    tool_calls = []
    for tc_id in ordered_ids:
        call = pending_calls.get(tc_id, {})
        result = results.get(tc_id, {})
        tool_calls.append({
            "name": call.get("name", "unknown"),
            "args": _truncate_dict(call.get("args", {})),
            "result": _truncate_dict(result),
        })

    return tool_calls


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return {"raw": raw}
    return {}


def _parse_result(content) -> dict:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        # Return a trimmed version
        return {"result": content[:300]}
    if isinstance(content, list):
        # Some results are arrays of content blocks
        texts = []
        for part in content:
            if isinstance(part, dict):
                texts.append(part.get("text", str(part))[:200])
            else:
                texts.append(str(part)[:200])
        return {"result": " | ".join(texts)[:300]}
    return {}


def _truncate_dict(d: dict, max_val_len: int = 150) -> dict:
    out = {}
    for k, v in d.items():
        val = str(v)
        if len(val) > max_val_len:
            val = val[:max_val_len] + "..."
        out[k] = val
    return out


# ─── Employee Role Detection ────────────────────────────────────────────────

def detect_roles(entries: list[dict]) -> dict[str, str]:
    """Heuristically detect employee roles from their tool usage patterns."""
    tool_usage: dict[str, list[str]] = {}
    for entry in entries:
        emp = entry["employee"]
        if emp not in tool_usage:
            tool_usage[emp] = []
        for tc in entry["tools"]:
            tool_usage[emp].append(tc["name"])

    roles = {}
    for emp, tools in tool_usage.items():
        tool_set = set(tools)
        management_tools = {"dispatch_child", "accept_child", "reject_child", "unblock_child",
                            "resume_held_task", "list_colleagues", "pull_meeting"}
        research_tools = {"web_search", "WebSearch"}
        coding_tools = {"bash", "Bash", "write", "Write", "edit", "Edit"}
        project_tools = {"set_project_name", "set_project_budget"}

        mgmt_count = sum(1 for t in tools if t in management_tools)
        research_count = sum(1 for t in tools if t in research_tools)
        code_count = sum(1 for t in tools if t in coding_tools)
        project_count = sum(1 for t in tools if t in project_tools)

        total = len(tools)
        if total == 0:
            roles[emp] = "Unknown"
            continue

        list_coll_count = tools.count("list_colleagues")

        # COO pattern: heavy management + frequent list_colleagues (coordination loops)
        if mgmt_count / total > 0.35 and list_coll_count >= 5:
            roles[emp] = "COO / Manager"
        # Project Lead: dispatches/accepts tasks, sets project metadata, but less coordination overhead
        elif project_count > 0 or ("dispatch_child" in tool_set and "accept_child" in tool_set):
            roles[emp] = "Project Lead"
        # Manager: mostly management tools but fewer coordination loops
        elif mgmt_count / total > 0.4:
            roles[emp] = "Manager"
        elif research_count >= 5:
            roles[emp] = "Deep Researcher"
        elif research_count > 0 or code_count > 0:
            roles[emp] = "Researcher / Writer"
        else:
            roles[emp] = "Worker"

    return roles


# ─── Purpose Annotation via LLM-free heuristics ─────────────────────────────

def annotate_purposes(entries: list[dict], roles: dict[str, str]) -> dict[int, str]:
    """Generate a purpose description for each step based on tool patterns."""
    purposes = {}
    for entry in entries:
        emp = entry["employee"]
        role = roles.get(emp, "Worker")
        tools = [tc["name"] for tc in entry["tools"]]
        tool_set = set(tools)

        parts = []

        # Detect patterns
        dispatch_count = tools.count("dispatch_child")
        accept_count = tools.count("accept_child")
        search_count = sum(1 for t in tools if t in ("web_search", "WebSearch"))
        write_count = sum(1 for t in tools if t in ("write", "Write"))
        read_count = sum(1 for t in tools if t in ("read", "Read", "read_node_detail"))
        bash_count = sum(1 for t in tools if t in ("bash", "Bash"))
        list_colleagues_count = tools.count("list_colleagues")

        if "set_project_name" in tool_set or "set_project_budget" in tool_set:
            parts.append("项目设置/更新")
        if "pull_meeting" in tool_set:
            parts.append("召开会议对齐")
        if "view_meeting_minutes" in tool_set:
            parts.append("查看会议纪要")
        if dispatch_count > 0:
            parts.append(f"分派 {dispatch_count} 个子任务")
        if accept_count > 0:
            parts.append(f"验收 {accept_count} 个子任务")
        if "reject_child" in tool_set:
            parts.append("驳回子任务")
        if "unblock_child" in tool_set:
            parts.append("解除任务阻塞")
        if "resume_held_task" in tool_set:
            parts.append("恢复挂起任务")
        if search_count > 0:
            parts.append(f"Web 搜索 ×{search_count}")
        if write_count > 0:
            parts.append(f"写入 {write_count} 个文件")
        if "edit" in tool_set or "Edit" in tool_set:
            parts.append("编辑文件")
        if bash_count > 0:
            parts.append(f"执行 {bash_count} 个命令")
        if read_count > 0 and not parts:
            parts.append(f"读取 {read_count} 个文件/节点")
        if list_colleagues_count >= 3:
            parts.append(f"检查团队状态 ×{list_colleagues_count}")
        if "load_skill" in tool_set:
            parts.append("加载技能")
        if "TodoWrite" in tool_set:
            parts.append("更新待办跟踪")

        purpose = f"[{role}] " + "，".join(parts) if parts else f"[{role}] 执行操作"
        purposes[entry["line"]] = purpose

    return purposes


# ─── Color Assignment ────────────────────────────────────────────────────────

PALETTE = [
    {"bg": "124,58,237",  "fg": "#a78bfa", "name": "purple"},   # 0
    {"bg": "56,189,248",  "fg": "#38bdf8", "name": "blue"},     # 1
    {"bg": "52,211,153",  "fg": "#34d399", "name": "green"},    # 2
    {"bg": "251,191,36",  "fg": "#fbbf24", "name": "amber"},    # 3
    {"bg": "244,114,182", "fg": "#f472b6", "name": "pink"},     # 4
    {"bg": "248,113,113", "fg": "#f87171", "name": "red"},      # 5
    {"bg": "34,211,238",  "fg": "#22d3ee", "name": "cyan"},     # 6
    {"bg": "163,230,53",  "fg": "#a3e635", "name": "lime"},     # 7
]


def assign_colors(employees: list[str]) -> dict[str, dict]:
    """Assign a color from the palette to each employee."""
    colors = {}
    for i, emp in enumerate(employees):
        colors[emp] = PALETTE[i % len(PALETTE)]
    return colors


# ─── HTML Generation ─────────────────────────────────────────────────────────

def generate_html(entries: list[dict], roles: dict[str, str],
                  purposes: dict[int, str], title: str) -> str:
    """Generate the full self-contained HTML visualization."""

    employees = sorted(set(e["employee"] for e in entries))
    colors = assign_colors(employees)

    # Build dynamic CSS for employee colors
    emp_css = ""
    for emp, col in colors.items():
        emp_css += f"""
.emp-{emp} {{ background: rgba({col['bg']},0.2); color: {col['fg']}; border-color: {col['fg']}; }}
.dot-{emp} {{ border-color: {col['fg']}; }}
.order-{emp} {{ background: rgba({col['bg']},0.25); color: {col['fg']}; }}"""

    # Build filter buttons
    filter_btns = '<button class="filter-btn active" data-filter="all">All</button>\n'
    for emp in employees:
        role = roles.get(emp, "Worker")
        fg = colors[emp]["fg"]
        filter_btns += f'    <button class="filter-btn" data-filter="{emp}"><span style="color:{fg}">●</span> {emp} {html_mod.escape(role)}</button>\n'

    # Stats
    total_tools = sum(len(e["tools"]) for e in entries)
    unique_tools = len(set(tc["name"] for e in entries for tc in e["tools"]))
    total_in = sum(e["tokens_in"] for e in entries)
    total_out = sum(e["tokens_out"] for e in entries)
    if len(entries) >= 2 and entries[0]["ts"] and entries[-1]["ts"]:
        try:
            t0 = datetime.fromisoformat(entries[0]["ts"])
            t1 = datetime.fromisoformat(entries[-1]["ts"])
            duration_min = int((t1 - t0).total_seconds() / 60)
        except Exception:
            duration_min = "?"
    else:
        duration_min = "?"

    # Serialize data for JS
    js_data = json.dumps(entries, ensure_ascii=False)
    js_roles = json.dumps(roles, ensure_ascii=False)
    js_purposes = json.dumps(purposes, ensure_ascii=False)
    js_colors = json.dumps({emp: col["fg"] for emp, col in colors.items()}, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_mod.escape(title)}</title>
<style>
:root {{
  --bg: #0f172a; --surface: #1e293b; --surface2: #334155; --border: #475569;
  --text: #e2e8f0; --text2: #94a3b8; --text3: #64748b;
  --accent: #38bdf8; --green: #34d399; --amber: #fbbf24; --red: #f87171; --purple: #a78bfa; --pink: #f472b6;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'SF Mono', 'Fira Code', monospace; background: var(--bg); color: var(--text); overflow-x: hidden; }}

.header {{
  position: sticky; top: 0; z-index: 100; background: rgba(15,23,42,0.95); backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border); padding: 16px 24px;
}}
.header h1 {{ font-size: 18px; font-weight: 700; margin-bottom: 4px; }}
.header p {{ font-size: 12px; color: var(--text3); }}
.header .stats {{ display: flex; gap: 24px; margin-top: 10px; flex-wrap: wrap; }}
.stat {{ display: flex; flex-direction: column; }}
.stat-val {{ font-size: 20px; font-weight: 700; color: var(--accent); }}
.stat-label {{ font-size: 11px; color: var(--text3); text-transform: uppercase; letter-spacing: 0.5px; }}

.controls {{
  position: sticky; top: 110px; z-index: 99; background: rgba(15,23,42,0.9); backdrop-filter: blur(8px);
  padding: 10px 24px; border-bottom: 1px solid var(--surface2); display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
}}
.controls label {{ font-size: 12px; color: var(--text3); margin-right: 4px; }}
.filter-btn {{
  background: var(--surface); border: 1px solid var(--surface2); color: var(--text2); padding: 4px 12px;
  border-radius: 6px; cursor: pointer; font-size: 12px; transition: all 0.15s;
}}
.filter-btn:hover, .filter-btn.active {{ background: var(--surface2); color: var(--text); border-color: var(--accent); }}
.filter-btn.active {{ box-shadow: 0 0 0 1px var(--accent); }}

.main {{ padding: 24px; max-width: 1400px; margin: 0 auto; }}

.timeline {{ position: relative; }}
.timeline::before {{
  content: ''; position: absolute; left: 140px; top: 0; bottom: 0;
  width: 2px; background: linear-gradient(to bottom, var(--surface2), var(--border), var(--surface2));
}}

.step {{
  position: relative; margin-bottom: 8px; padding-left: 170px;
  opacity: 0; animation: fadeSlideIn 0.3s ease forwards;
}}
@keyframes fadeSlideIn {{ to {{ opacity: 1; }} }}

.step-meta {{
  position: absolute; left: 0; top: 0; width: 120px; text-align: right;
  font-size: 11px; color: var(--text3); padding-top: 12px;
}}
.step-meta .ts {{ color: var(--text2); font-weight: 600; }}
.step-dot {{
  position: absolute; left: 134px; top: 14px; width: 14px; height: 14px;
  border-radius: 50%; border: 2px solid; z-index: 2; background: var(--bg);
}}

.step-card {{
  background: var(--surface); border: 1px solid var(--surface2); border-radius: 10px;
  overflow: hidden; transition: all 0.2s;
}}
.step-card:hover {{ border-color: var(--border); }}

.step-header {{
  display: flex; align-items: center; gap: 10px; padding: 10px 16px; cursor: pointer;
  user-select: none;
}}
.step-header:hover {{ background: rgba(255,255,255,0.02); }}
.emp-badge {{
  padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 700;
  white-space: nowrap; min-width: 90px; text-align: center;
}}
.step-title {{ font-size: 13px; font-weight: 600; flex: 1; }}
.step-info {{ font-size: 11px; color: var(--text3); white-space: nowrap; display: flex; gap: 12px; }}
.chevron {{ color: var(--text3); transition: transform 0.2s; font-size: 14px; }}
.chevron.open {{ transform: rotate(90deg); }}

.step-body {{ display: none; border-top: 1px solid var(--surface2); }}
.step-body.open {{ display: block; }}

.tool-list {{ padding: 12px 16px; display: flex; flex-direction: column; gap: 6px; }}

.tool-item {{
  background: var(--bg); border: 1px solid var(--surface2); border-radius: 8px;
  overflow: hidden; transition: border-color 0.15s;
}}
.tool-item:hover {{ border-color: var(--border); }}
.tool-item-header {{
  display: flex; align-items: center; gap: 8px; padding: 8px 12px; cursor: pointer;
}}
.tool-item-header:hover {{ background: rgba(255,255,255,0.02); }}
.tool-order {{
  width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700; flex-shrink: 0;
}}
.tool-name {{ font-size: 13px; font-weight: 600; color: var(--accent); }}
.tool-status {{ font-size: 10px; margin-left: auto; padding: 2px 8px; border-radius: 3px; }}
.tool-status.ok {{ background: rgba(52,211,153,0.15); color: var(--green); }}
.tool-status.error {{ background: rgba(248,113,113,0.15); color: var(--red); }}
.tool-chevron {{ color: var(--text3); font-size: 12px; transition: transform 0.15s; }}
.tool-chevron.open {{ transform: rotate(90deg); }}

.tool-detail {{ display: none; border-top: 1px solid var(--surface2); padding: 10px 12px; }}
.tool-detail.open {{ display: block; }}

.detail-section {{ margin-bottom: 8px; }}
.detail-section:last-child {{ margin-bottom: 0; }}
.detail-label {{ font-size: 10px; color: var(--text3); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; font-weight: 600; }}
.detail-content {{
  font-size: 12px; color: var(--text2); background: rgba(0,0,0,0.3); border-radius: 6px;
  padding: 8px 10px; word-break: break-all; white-space: pre-wrap; line-height: 1.5;
  max-height: 200px; overflow-y: auto;
}}
.detail-content .key {{ color: var(--purple); }}
.detail-content .val {{ color: var(--text2); }}

.step-purpose {{
  padding: 8px 16px 12px; font-size: 12px; color: var(--amber); line-height: 1.5;
  border-top: 1px solid var(--surface2); background: rgba(217,119,6,0.05);
}}
.step-purpose::before {{ content: '\\1F4A1  '; }}

{emp_css}

::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: var(--surface2); border-radius: 3px; }}

@media (max-width: 768px) {{
  .timeline::before {{ left: 20px; }}
  .step {{ padding-left: 46px; }}
  .step-meta {{ display: none; }}
  .step-dot {{ left: 14px; }}
}}
</style>
</head>
<body>

<div class="header">
  <h1>{html_mod.escape(title)}</h1>
  <p>Tool Call Chain Visualization · Generated by trace_visualizer.py</p>
  <div class="stats">
    <div class="stat"><span class="stat-val">{len(entries)}</span><span class="stat-label">Steps</span></div>
    <div class="stat"><span class="stat-val">{total_tools}</span><span class="stat-label">Tool Calls</span></div>
    <div class="stat"><span class="stat-val">{unique_tools}</span><span class="stat-label">Unique Tools</span></div>
    <div class="stat"><span class="stat-val">{len(employees)}</span><span class="stat-label">Employees</span></div>
    <div class="stat"><span class="stat-val">{duration_min}m</span><span class="stat-label">Duration</span></div>
    <div class="stat"><span class="stat-val">{total_in // 1000}K</span><span class="stat-label">Tokens In</span></div>
    <div class="stat"><span class="stat-val">{total_out // 1000}K</span><span class="stat-label">Tokens Out</span></div>
  </div>
</div>

<div class="controls">
  <label>Filter by Employee:</label>
  {filter_btns}
  <span style="color:var(--surface2);margin:0 8px">|</span>
  <button class="filter-btn" id="expandAllBtn">Expand All</button>
  <button class="filter-btn" id="collapseAllBtn">Collapse All</button>
</div>

<div class="main">
  <div class="timeline" id="timeline"></div>
</div>

<script>
const TRACE_DATA = {js_data};
const EMPLOYEE_ROLES = {js_roles};
const STEP_PURPOSES = {js_purposes};
const EMP_COLORS = {js_colors};

function renderTimeline(filter) {{
  const timeline = document.getElementById('timeline');
  timeline.innerHTML = '';

  TRACE_DATA.forEach((entry, idx) => {{
    if (filter !== 'all' && entry.employee !== filter) return;

    const ts = entry.ts ? entry.ts.substring(11, 19) : '';
    const emp = entry.employee;
    const role = EMPLOYEE_ROLES[emp] || 'Unknown';
    const purpose = STEP_PURPOSES[String(entry.line)] || '';

    const step = document.createElement('div');
    step.className = 'step';
    step.style.animationDelay = `${{idx * 30}}ms`;

    step.innerHTML = `
      <div class="step-meta">
        <div class="ts">${{ts}}</div>
        <div>Line ${{entry.line}}</div>
        <div>${{(entry.tokens_in/1000).toFixed(0)}}K / ${{(entry.tokens_out/1000).toFixed(0)}}K tok</div>
      </div>
      <div class="step-dot dot-${{emp}}"></div>
      <div class="step-card">
        <div class="step-header" onclick="toggleStep(this)">
          <span class="emp-badge emp-${{emp}}">${{emp}} ${{role}}</span>
          <span class="step-title">${{entry.tools.length}} tool calls</span>
          <span class="step-info"><span>${{entry.model}}</span></span>
          <span class="chevron">▶</span>
        </div>
        ${{purpose ? `<div class="step-purpose">${{purpose}}</div>` : ''}}
        <div class="step-body">
          <div class="tool-list">
            ${{entry.tools.map((tool, ti) => renderTool(tool, ti, emp)).join('')}}
          </div>
        </div>
      </div>
    `;
    timeline.appendChild(step);
  }});
}}

function renderTool(tool, index, emp) {{
  const status = (tool.result && tool.result.status) || '';
  const isError = status === 'error' || (tool.result && tool.result.message && String(tool.result.message).includes('error'));
  const statusClass = isError ? 'error' : 'ok';
  const statusText = isError ? 'ERROR' : (status || 'OK');

  const argsHtml = Object.keys(tool.args).length > 0
    ? Object.entries(tool.args).map(([k, v]) => `<span class="key">${{escHtml(k)}}</span>: <span class="val">${{escHtml(v)}}</span>`).join('\\n')
    : '<span class="val">(no arguments)</span>';

  const resultHtml = Object.keys(tool.result).length > 0
    ? Object.entries(tool.result).map(([k, v]) => `<span class="key">${{escHtml(k)}}</span>: <span class="val">${{escHtml(v)}}</span>`).join('\\n')
    : '<span class="val">(no result data)</span>';

  return `
    <div class="tool-item">
      <div class="tool-item-header" onclick="toggleTool(this)">
        <span class="tool-order order-${{emp}}">${{index + 1}}</span>
        <span class="tool-name">${{escHtml(tool.name)}}</span>
        <span class="tool-status ${{statusClass}}">${{statusText}}</span>
        <span class="tool-chevron">▶</span>
      </div>
      <div class="tool-detail">
        <div class="detail-section">
          <div class="detail-label">Input Arguments</div>
          <div class="detail-content">${{argsHtml}}</div>
        </div>
        <div class="detail-section">
          <div class="detail-label">Output / Result</div>
          <div class="detail-content">${{resultHtml}}</div>
        </div>
      </div>
    </div>
  `;
}}

function escHtml(s) {{
  const div = document.createElement('div');
  div.textContent = String(s);
  return div.innerHTML;
}}

function toggleStep(header) {{
  const body = header.parentElement.querySelector('.step-body');
  const chevron = header.querySelector('.chevron');
  body.classList.toggle('open');
  chevron.classList.toggle('open');
}}

function toggleTool(header) {{
  const detail = header.parentElement.querySelector('.tool-detail');
  const chevron = header.querySelector('.tool-chevron');
  detail.classList.toggle('open');
  chevron.classList.toggle('open');
}}

document.querySelectorAll('.filter-btn[data-filter]').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.filter-btn[data-filter]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTimeline(btn.dataset.filter);
  }});
}});

document.getElementById('expandAllBtn').addEventListener('click', () => {{
  document.querySelectorAll('.step-body').forEach(b => b.classList.add('open'));
  document.querySelectorAll('.chevron').forEach(c => c.classList.add('open'));
}});
document.getElementById('collapseAllBtn').addEventListener('click', () => {{
  document.querySelectorAll('.step-body, .tool-detail').forEach(b => b.classList.remove('open'));
  document.querySelectorAll('.chevron, .tool-chevron').forEach(c => c.classList.remove('open'));
}});

renderTimeline('all');
</script>
</body>
</html>"""


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Visualize OneManCompany debug_trace.jsonl as interactive HTML timeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Path to debug_trace.jsonl file")
    parser.add_argument("-o", "--output", help="Output HTML file path (default: <input_stem>_trace.html)")
    parser.add_argument("--open", action="store_true", help="Open the HTML file in default browser after generation")
    parser.add_argument("--title", help="Custom title for the visualization")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_name(input_path.stem + "_viz.html")

    title = args.title or f"Tool Call Chain — {input_path.stem}"

    print(f"Parsing {input_path} ...")
    entries = parse_jsonl(str(input_path))

    if not entries:
        print("ERROR: No valid entries found in file.", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(entries)} steps, {sum(len(e['tools']) for e in entries)} tool calls")

    roles = detect_roles(entries)
    print(f"  Detected roles: {roles}")

    purposes = annotate_purposes(entries, roles)

    html = generate_html(entries, roles, purposes, title)

    output_path.write_text(html, encoding="utf-8")
    print(f"  Written to {output_path} ({len(html):,} bytes)")

    if args.open:
        if sys.platform == "darwin":
            subprocess.run(["open", str(output_path)])
        elif sys.platform == "win32":
            os.startfile(str(output_path))
        else:
            subprocess.run(["xdg-open", str(output_path)])
        print("  Opened in browser.")

    print("Done.")


if __name__ == "__main__":
    main()
