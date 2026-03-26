/**
 * trace-viewer.js — Brutalist trace viewer for task execution
 *
 * Two-layer view:
 * 1. TraceTreeView: task tree hierarchy with box-drawing characters
 * 2. NodeTraceView: per-node execution log (JSONL from disk)
 *
 * Style: monospace, terminal aesthetic, no border-radius, high contrast
 */

// TraceTreeView removed — replaced by TraceFeedView (ink-style terminal)


// ─────────────────────────────────────────────────────────
// Shared log processing utilities
// ─────────────────────────────────────────────────────────

function traceGroupSteps(logs) {
  const steps = [];
  const seen = new Set();
  let i = 0;
  while (i < logs.length) {
    const log = logs[i];
    const key = `${log.timestamp}-${log.type}-${(log.content || '').substring(0, 50)}`;
    if (seen.has(key)) { i++; continue; }
    seen.add(key);
    if (log.type === 'tool_call') {
      const toolName = traceExtractToolName(log.content);
      let result = null;
      for (let j = i + 1; j < Math.min(i + 4, logs.length); j++) {
        if (logs[j].type === 'tool_result' && logs[j].content && logs[j].content.includes(toolName)) {
          result = logs[j];
          if (j + 1 < logs.length && logs[j + 1].type === 'tool_result'
              && logs[j + 1].content && logs[j + 1].content.includes("content='")) {
            seen.add(`${logs[j + 1].timestamp}-${logs[j + 1].type}-${(logs[j + 1].content || '').substring(0, 50)}`);
          }
          break;
        }
      }
      steps.push({ type: 'tool', timestamp: log.timestamp, toolName, input: log.content, result });
    } else if (log.type === 'tool_result') {
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

function traceExtractToolName(content) {
  if (!content) return '';
  const match = content.match(/^(\w+)\(/);
  return match ? match[1] : content.substring(0, 20);
}

function traceExtractToolInput(content) {
  if (!content) return '';
  const match = content.match(/^\w+\((\{.*\})\)$/s);
  if (match) {
    try {
      const obj = JSON.parse(match[1].replace(/'/g, '"'));
      return Object.entries(obj).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ');
    } catch { /* fall through */ }
  }
  const m2 = content.match(/^\w+\((.*)\)$/s);
  return m2 ? m2[1] : content;
}

function traceExtractToolResult(content, toolName) {
  if (!content) return '';
  const prefix = `${toolName} \u2192 `;
  if (content.startsWith(prefix)) return content.substring(prefix.length);
  const prefix2 = `${toolName} → `;
  if (content.startsWith(prefix2)) return content.substring(prefix2.length);
  return content;
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
    const steps = traceGroupSteps(this._logs);
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

  // _groupSteps, _extractToolName moved to module-level functions

  _renderToolStep(step) {
    const ts = this._fmtTime(step.timestamp);
    const input = traceExtractToolInput(step.input);
    const resultText = step.result ? traceExtractToolResult(step.result.content, step.toolName) : '';
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

  // _extractToolInput, _extractToolResult moved to module-level functions

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

// ─────────────────────────────────────────────────────────
// TraceFeedView — Reader Feed mode (Laminar-inspired)
//
// Flattens the entire task tree into a single linear narrative.
// Each node becomes a section header, followed by its execution
// log entries inline. Everything scrolls in one column.
// ─────────────────────────────────────────────────────────

class TraceFeedView {
  constructor(container) {
    this._el = typeof container === 'string' ? document.getElementById(container) : container;
    this._nodes = {};
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
      await this._loadAllLogs();
      this.render();
    } catch (e) {
      this._el.textContent = `Error: ${e.message}`;
    }
  }

  async _loadAllLogs() {
    const promises = Object.values(this._nodes).map(async (node) => {
      if (!node.project_dir) return;
      try {
        const resp = await fetch(`/api/node/${node.id}/logs?project_dir=${encodeURIComponent(node.project_dir)}&tail=200`);
        const data = await resp.json();
        node._logs = data.logs || [];
      } catch (e) {
        console.warn(`[TraceFeed] Failed to load logs for ${node.id}:`, e);
        node._logs = [];
      }
    });
    await Promise.all(promises);
  }

  render() {
    if (!this._rootId) {
      this._el.textContent = 'No trace data';
      return;
    }
    // Build pure text lines with minimal color spans, render into <pre>
    const lines = [];
    this._renderNode(this._rootId, '', lines);
    this._el.innerHTML = `<pre class="term">${lines.join('\n')}</pre>`;
    this._el.scrollTop = 0;
  }

  _renderNode(nodeId, prefix, lines) {
    const node = this._nodes[nodeId];
    if (!node) return;

    const emp = node.employee_info || {};
    const name = emp.nickname || emp.name || node.employee_id || '';
    const status = node.status || '';
    const dur = this._dur(node.created_at, node.completed_at);
    const cost = node.cost_usd > 0 ? ` $${node.cost_usd.toFixed(4)}` : '';
    const type = this._type(node.node_type);
    const sIcon = this._sIcon(status);
    const sColor = this._sColor(status);

    // ── Node header ──
    lines.push(`${this._e(prefix)}<span style="color:${sColor}">${sIcon}</span> <b>${this._e(name)}</b>${type ? ` <span style="color:#555">${type}</span>` : ''} <span style="color:#555">${dur}${cost}</span>`);

    // Description
    const desc = (node.description_preview || '').substring(0, 90).replace(/\n/g, ' ');
    if (desc) {
      lines.push(`${this._e(prefix)}<span style="color:#444">${this._e(desc)}</span>`);
    }

    // Execution log entries
    const logs = node._logs || [];
    if (logs.length > 0) {
      const steps = traceGroupSteps(logs);
      for (const step of steps) {
        const ts = step.timestamp ? step.timestamp.substring(11, 19) : '        ';
        if (step.type === 'tool') {
          const toolName = step.toolName || '';
          const input = traceExtractToolInput(step.input).substring(0, 70);
          const result = step.result ? traceExtractToolResult(step.result.content, toolName).substring(0, 80) : '';
          const ok = step.result && !(step.result.content || '').includes('error');
          const icon = step.result ? (ok ? '\u2713' : '\u2717') : '\u2026';
          let line = `${this._e(prefix)}<span style="color:#444">${ts}</span> <span style="color:#4af">tool</span> <span style="color:#4af">${this._e(toolName)}</span> ${input ? this._e(input) : ''}`;
          if (result) line += `\n${this._e(prefix)}         <span style="color:#585">\u2192 ${this._e(result)}</span> ${icon}`;
          lines.push(line);
        } else if (step.type === 'llm_output') {
          const summary = (step.content || '').split('\n')[0].substring(0, 90);
          lines.push(`${this._e(prefix)}<span style="color:#444">${ts}</span> <span style="color:#fa4">llm</span>  <span style="color:#997">${this._e(summary)}</span>`);
        } else if (step.type === 'start') {
          const content = (step.content || '').substring(0, 90).replace(/\n/g, ' ');
          lines.push(`${this._e(prefix)}<span style="color:#444">${ts}</span> <span style="color:#666">start</span> ${this._e(content)}`);
        } else if (step.type === 'result' || step.type === 'end') {
          const content = (step.content || '').substring(0, 90).replace(/\n/g, ' ');
          lines.push(`${this._e(prefix)}<span style="color:#444">${ts}</span> <span style="color:#4a4">${step.type}</span> ${this._e(content)}`);
        }
      }
    }

    // Result
    if (node.result && ['completed', 'accepted', 'finished'].includes(status)) {
      const r = node.result.substring(0, 120).replace(/\n/g, ' ');
      lines.push(`${this._e(prefix)}<span style="color:#4a4">\u2192 ${this._e(r)}</span>`);
    }

    lines.push('');  // blank line between nodes

    // Children
    const children = (node.children_ids || []).filter(id => this._nodes[id]);
    for (const childId of children) {
      this._renderNode(childId, prefix + '\u2502 ', lines);
    }
  }

  _sIcon(s) {
    return { pending:'\u2591', processing:'\u2593', holding:'\u2592', completed:'\u2588', accepted:'\u2588', finished:'\u2588', failed:'\u2573', blocked:'\u2592', cancelled:'\u2573' }[s] || '\u2591';
  }

  _sColor(s) {
    return { pending:'#666', processing:'#ff4', holding:'#fa4', completed:'#4a4', accepted:'#4a4', finished:'#2a2', failed:'#f44', blocked:'#a44', cancelled:'#844' }[s] || '#666';
  }

  _type(t) {
    return { ceo_prompt:'CEO', review:'REVIEW', ceo_request:'CEO_REQ', watchdog_nudge:'WD', system:'SYS' }[t] || '';
  }

  _dur(start, end) {
    if (!start) return '';
    const s = Math.floor(((end ? new Date(end) : new Date()) - new Date(start)) / 1000);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s/60)}m${s%60}s`;
    return `${Math.floor(s/3600)}h${Math.floor((s%3600)/60)}m`;
  }

  _e(s) {
    return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
}
