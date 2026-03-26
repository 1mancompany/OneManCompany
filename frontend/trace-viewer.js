/**
 * trace-viewer.js — Shared log processing utilities
 *
 * Used by xterm-log.js (XTermLog) for step grouping, tool input/result extraction.
 * All rendering is done by XTermLog via xterm.js.
 */

// ─────────────────────────────────────────────────────────
// Data loading
// ─────────────────────────────────────────────────────────

async function traceLoadAllNodeLogs(nodes) {
  const promises = Object.values(nodes).map(async (node) => {
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

// ─────────────────────────────────────────────────────────
// Log step grouping (tool_call + tool_result pairing, dedup)
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

// ─────────────────────────────────────────────────────────
// Tool content extraction
// ─────────────────────────────────────────────────────────

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
