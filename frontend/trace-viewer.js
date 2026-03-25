/**
 * trace-viewer.js — Brutalist trace viewer for task execution
 *
 * Two-layer view:
 * 1. TraceTreeView: task tree hierarchy with box-drawing characters
 * 2. NodeTraceView: per-node execution log (JSONL from disk)
 *
 * Style: monospace, terminal aesthetic, no border-radius, high contrast
 */

// ─────────────────────────────────────────────────────────
// TraceTreeView — renders task tree as brutalist text tree
// ─────────────────────────────────────────────────────────

class TraceTreeView {
  constructor(container, opts = {}) {
    this._el = typeof container === 'string' ? document.getElementById(container) : container;
    this._onNodeSelect = opts.onNodeSelect || (() => {});
    this._selectedNodeId = null;
    this._nodes = {};  // id → node data
    this._rootId = '';
    this._projectId = '';
  }

  async load(projectId) {
    this._projectId = projectId;
    try {
      const resp = await fetch(`/api/projects/${projectId}/tree`);
      if (!resp.ok) { this._el.textContent = 'Tree not found'; return; }
      const data = await resp.json();
      this._rootId = data.root_id;
      this._nodes = {};
      for (const n of data.nodes) this._nodes[n.id] = n;
      this.render();
    } catch (e) {
      this._el.textContent = `Error: ${e.message}`;
    }
  }

  render() {
    if (!this._rootId || !this._nodes[this._rootId]) {
      this._el.textContent = 'No tree data';
      return;
    }
    const lines = [];
    this._renderNode(this._rootId, '', true, lines);
    this._el.innerHTML = lines.join('\n');
  }

  _renderNode(nodeId, prefix, isLast, lines) {
    const node = this._nodes[nodeId];
    if (!node) return;

    const connector = prefix === '' ? '' : (isLast ? ' \u2514 ' : ' \u251C ');
    const childPrefix = prefix === '' ? '' : prefix + (isLast ? '   ' : ' \u2502 ');

    // Status block
    const statusCls = this._statusClass(node.status);
    const statusIcon = this._statusIcon(node.status);

    // Employee name
    const emp = node.employee_info || {};
    const name = emp.nickname || emp.name || node.employee_id || '';

    // Node type label
    const typeLabel = this._typeLabel(node.node_type);

    // Duration
    const duration = this._duration(node.created_at, node.completed_at);

    // Cost
    const cost = node.cost_usd > 0 ? ` $${node.cost_usd.toFixed(4)}` : '';

    // Description preview
    const desc = (node.description_preview || '').substring(0, 60).replace(/\n/g, ' ');

    const selected = node.id === this._selectedNodeId ? ' trace-node-selected' : '';

    const line = `<span class="trace-node${selected}" data-node-id="${node.id}" onclick="window._traceSelectNode('${node.id}')">`
      + `<span class="trace-prefix">${this._esc(prefix + connector)}</span>`
      + `<span class="trace-status ${statusCls}">${statusIcon}</span> `
      + `<span class="trace-name">${this._esc(name)}</span>`
      + (typeLabel ? ` <span class="trace-type">${typeLabel}</span>` : '')
      + ` <span class="trace-bar">${this._bar(node.status)}</span>`
      + ` <span class="trace-duration">${duration}</span>`
      + `<span class="trace-cost">${cost}</span>`
      + (desc ? `\n<span class="trace-prefix">${this._esc(childPrefix)}</span>  <span class="trace-desc">${this._esc(desc)}</span>` : '')
      + `</span>`;

    lines.push(line);

    // Render children
    const children = (node.children_ids || []).filter(id => this._nodes[id]);
    for (let i = 0; i < children.length; i++) {
      this._renderNode(children[i], childPrefix, i === children.length - 1, lines);
    }
  }

  selectNode(nodeId) {
    this._selectedNodeId = nodeId;
    this.render();
    this._onNodeSelect(nodeId, this._nodes[nodeId]);
  }

  _statusClass(status) {
    const map = {
      pending: 'st-pending', processing: 'st-processing', holding: 'st-holding',
      completed: 'st-completed', accepted: 'st-accepted', finished: 'st-finished',
      failed: 'st-failed', blocked: 'st-blocked', cancelled: 'st-cancelled',
    };
    return map[status] || 'st-unknown';
  }

  _statusIcon(status) {
    const map = {
      pending: '\u2591\u2591', processing: '\u2593\u2593', holding: '\u2592\u2592',
      completed: '\u2588\u2588', accepted: '\u2588\u2588', finished: '\u2588\u2588',
      failed: '\u2573\u2573', blocked: '\u2592\u2592', cancelled: '\u2573\u2573',
    };
    return map[status] || '\u2591\u2591';
  }

  _typeLabel(nodeType) {
    const map = {
      ceo_prompt: 'CEO', task: '', review: 'REVIEW',
      ceo_request: 'CEO_REQ', watchdog_nudge: 'WATCHDOG',
      ceo_followup: 'FOLLOWUP', system: 'SYS', adhoc: 'ADHOC',
    };
    return map[nodeType] || '';
  }

  _bar(status) {
    const active = ['pending', 'processing', 'holding'];
    const done = ['completed', 'accepted', 'finished'];
    if (active.includes(status)) return '\u2501'.repeat(20);
    if (done.includes(status)) return '\u2501'.repeat(20);
    return '\u2501'.repeat(10);
  }

  _duration(start, end) {
    if (!start) return '';
    const s = new Date(start);
    const e = end ? new Date(end) : new Date();
    const secs = Math.floor((e - s) / 1000);
    if (secs < 60) return `${secs}s`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m${secs % 60}s`;
    return `${Math.floor(secs / 3600)}h${Math.floor((secs % 3600) / 60)}m`;
  }

  _esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
}


// ─────────────────────────────────────────────────────────
// NodeTraceView — renders per-node execution log
// ─────────────────────────────────────────────────────────

class NodeTraceView {
  constructor(container) {
    this._el = typeof container === 'string' ? document.getElementById(container) : container;
    this._nodeId = '';
    this._logs = [];
  }

  async load(nodeId, projectDir) {
    this._nodeId = nodeId;
    try {
      const qs = projectDir ? `?project_dir=${encodeURIComponent(projectDir)}&tail=500` : '?tail=500';
      const resp = await fetch(`/api/node/${nodeId}/logs${qs}`);
      const data = await resp.json();
      this._logs = data.logs || [];
      this.render();
    } catch (e) {
      this._el.textContent = `Error: ${e.message}`;
    }
  }

  render() {
    if (!this._logs.length) {
      this._el.innerHTML = '<span class="trace-empty">No execution logs</span>';
      return;
    }

    // Group tool_call + tool_result pairs
    const steps = this._groupSteps(this._logs);
    const lines = [];

    for (const step of steps) {
      if (step.type === 'tool') {
        lines.push(this._renderToolStep(step));
      } else if (step.type === 'llm_output') {
        lines.push(this._renderLlmStep(step));
      } else {
        lines.push(this._renderGenericStep(step));
      }
    }

    this._el.innerHTML = lines.join('');
    // Auto-scroll to bottom
    this._el.scrollTop = this._el.scrollHeight;
  }

  _groupSteps(logs) {
    const steps = [];
    const seen = new Set();
    let i = 0;

    while (i < logs.length) {
      const log = logs[i];
      const key = `${log.timestamp}-${log.type}-${(log.content || '').substring(0, 50)}`;

      // Deduplicate (raw + parsed tool_result pairs)
      if (seen.has(key)) { i++; continue; }
      seen.add(key);

      if (log.type === 'tool_call') {
        // Look ahead for matching tool_result
        const toolName = this._extractToolName(log.content);
        let result = null;
        for (let j = i + 1; j < Math.min(i + 4, logs.length); j++) {
          if (logs[j].type === 'tool_result' && logs[j].content && logs[j].content.includes(toolName)) {
            result = logs[j];
            // Skip duplicate tool_result (raw format with content='...' name='...')
            if (j + 1 < logs.length && logs[j + 1].type === 'tool_result'
                && logs[j + 1].content && logs[j + 1].content.includes("content='")) {
              seen.add(`${logs[j + 1].timestamp}-${logs[j + 1].type}-${(logs[j + 1].content || '').substring(0, 50)}`);
            }
            break;
          }
        }
        steps.push({ type: 'tool', timestamp: log.timestamp, toolName, input: log.content, result });
      } else if (log.type === 'tool_result') {
        // Orphaned tool_result (no matching call) — skip duplicates
        if (log.content && log.content.includes("content='")) { i++; continue; }
        steps.push({ type: 'tool_result_orphan', timestamp: log.timestamp, content: log.content });
      } else if (log.type === 'llm_output') {
        steps.push({ type: 'llm_output', timestamp: log.timestamp, content: log.content });
      } else {
        steps.push({ type: log.type, timestamp: log.timestamp, content: log.content });
      }
      i++;
    }
    return steps;
  }

  _extractToolName(content) {
    if (!content) return '';
    const match = content.match(/^(\w+)\(/);
    return match ? match[1] : content.substring(0, 20);
  }

  _renderToolStep(step) {
    const ts = this._fmtTime(step.timestamp);
    const input = this._extractToolInput(step.input);
    const resultText = step.result ? this._extractToolResult(step.result.content, step.toolName) : '';
    const resultOk = step.result && !resultText.includes('error');
    const resultIcon = step.result ? (resultOk ? '\u2713' : '\u2717') : '\u2026';

    const inputTruncated = input.length > 120;
    const resultTruncated = resultText.length > 200;

    return `<div class="trace-step trace-tool">`
      + `<span class="trace-ts">${ts}</span>`
      + `<span class="trace-pipe">\u2503</span>`
      + ` <span class="trace-label trace-label-tool">TOOL</span>`
      + ` <span class="trace-tool-name">\u258C ${this._esc(step.toolName)}</span>`
      + ` <span class="trace-tool-icon">${resultIcon}</span>`
      + `\n<span class="trace-ts-pad"></span><span class="trace-pipe">\u2503</span>`
      + ` <span class="trace-tool-io">\u256D\u2500</span> <span class="trace-tool-input">${this._esc(inputTruncated ? input.substring(0, 120) : input)}</span>`
      + (inputTruncated ? `<span class="trace-expand" onclick="this.nextElementSibling.style.display='inline';this.style.display='none'">\u2026more</span><span class="trace-expanded" style="display:none">${this._esc(input.substring(120))}</span>` : '')
      + `\n<span class="trace-ts-pad"></span><span class="trace-pipe">\u2503</span>`
      + ` <span class="trace-tool-io">\u2570\u2500</span> <span class="trace-tool-result">\u2192 ${this._esc(resultTruncated ? resultText.substring(0, 200) : resultText)}</span>`
      + (resultTruncated ? `<span class="trace-expand" onclick="this.nextElementSibling.style.display='inline';this.style.display='none'">\u2026more</span><span class="trace-expanded" style="display:none">${this._esc(resultText.substring(200))}</span>` : '')
      + `</div>`;
  }

  _renderLlmStep(step) {
    const ts = this._fmtTime(step.timestamp);
    const content = step.content || '';
    const lines = content.split('\n');
    const summary = lines.slice(0, 2).join(' ').substring(0, 120);
    const isTruncated = content.length > 120;

    return `<div class="trace-step trace-llm">`
      + `<span class="trace-ts">${ts}</span>`
      + `<span class="trace-pipe">\u2503</span>`
      + ` <span class="trace-label trace-label-llm">LLM</span>`
      + ` <span class="trace-llm-bar">${'\u258C'.repeat(Math.min(20, Math.ceil(content.length / 100)))}</span>`
      + `\n<span class="trace-ts-pad"></span><span class="trace-pipe">\u2503</span>`
      + ` <span class="trace-llm-content">${this._esc(summary)}</span>`
      + (isTruncated ? `\n<span class="trace-ts-pad"></span><span class="trace-pipe">\u2503</span> <span class="trace-expand" onclick="this.nextElementSibling.style.display='block';this.style.display='none'">\u00B7\u00B7\u00B7(click to expand)</span><span class="trace-expanded trace-llm-full" style="display:none">${this._esc(content)}</span>` : '')
      + `</div>`;
  }

  _renderGenericStep(step) {
    const ts = this._fmtTime(step.timestamp);
    const typeCls = `trace-label-${step.type || 'info'}`;
    const label = (step.type || 'INFO').toUpperCase();
    const content = (step.content || '').substring(0, 300);
    const isFill = ['start', 'result', 'end'].includes(step.type);

    return `<div class="trace-step trace-${step.type || 'info'}">`
      + `<span class="trace-ts">${ts}</span>`
      + `<span class="trace-pipe">\u2503</span>`
      + ` <span class="trace-label ${typeCls}">${label}</span>`
      + (isFill ? ` <span class="trace-fill">${'\u2588'.repeat(20)}</span>` : '')
      + `\n<span class="trace-ts-pad"></span><span class="trace-pipe">\u2503</span>`
      + ` <span class="trace-generic-content">${this._esc(content)}</span>`
      + `</div>`;
  }

  _extractToolInput(content) {
    if (!content) return '';
    // "bash({'command': 'ls -R src/'})" → "command: ls -R src/"
    const match = content.match(/^\w+\((\{.*\})\)$/s);
    if (match) {
      try {
        const obj = JSON.parse(match[1].replace(/'/g, '"'));
        return Object.entries(obj).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ');
      } catch { /* fall through */ }
    }
    // "tool_name({...})" → strip tool name
    const m2 = content.match(/^\w+\((.*)\)$/s);
    return m2 ? m2[1] : content;
  }

  _extractToolResult(content, toolName) {
    if (!content) return '';
    // "tool_name → result" format
    const prefix = `${toolName} \u2192 `;
    if (content.startsWith(prefix)) return content.substring(prefix.length);
    const prefix2 = `${toolName} → `;
    if (content.startsWith(prefix2)) return content.substring(prefix2.length);
    return content;
  }

  _fmtTime(ts) {
    if (!ts) return '        ';
    return ts.substring(11, 19);  // HH:MM:SS
  }

  _esc(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
}


// ─────────────────────────────────────────────────────────
// Global handler for tree node selection
// ─────────────────────────────────────────────────────────

window._traceSelectNode = function(nodeId) {
  if (window._traceTreeView) {
    window._traceTreeView.selectNode(nodeId);
  }
};
