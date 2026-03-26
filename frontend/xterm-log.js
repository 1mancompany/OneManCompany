/**
 * xterm-log.js — xterm.js wrapper for log/trace display
 *
 * Provides XTermLog class that wraps xterm.js Terminal in read-only mode
 * and renders our log data as ANSI-colored terminal output.
 */

// ANSI color codes
const ANSI = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  // Foreground
  black: '\x1b[30m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
  white: '\x1b[37m',
  gray: '\x1b[90m',
  brightRed: '\x1b[91m',
  brightGreen: '\x1b[92m',
  brightYellow: '\x1b[93m',
  brightBlue: '\x1b[94m',
  brightMagenta: '\x1b[95m',
  brightCyan: '\x1b[96m',
  brightWhite: '\x1b[97m',
};


class XTermLog {
  constructor(container, opts = {}) {
    this._container = typeof container === 'string' ? document.getElementById(container) : container;
    this._term = null;
    this._fitAddon = null;
    this._fontSize = opts.fontSize || 12;
    this._init();
  }

  _init() {
    this._term = new Terminal({
      disableStdin: true,
      convertEol: true,
      fontSize: this._fontSize,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', monospace",
      theme: {
        background: '#0a0a0a',
        foreground: '#b0b0b0',
        cursor: '#0a0a0a',  // hide cursor
        black: '#333333',
        red: '#ff4444',
        green: '#44aa44',
        yellow: '#ffaa44',
        blue: '#44aaff',
        magenta: '#aa44ff',
        cyan: '#44aaaa',
        white: '#d4d4d4',
        brightBlack: '#666666',
        brightRed: '#ff6666',
        brightGreen: '#66cc66',
        brightYellow: '#ffcc66',
        brightBlue: '#66ccff',
        brightMagenta: '#cc66ff',
        brightCyan: '#66cccc',
        brightWhite: '#ffffff',
      },
      scrollback: 10000,
      cursorBlink: false,
      cursorStyle: 'bar',
      cursorWidth: 0,
    });

    if (typeof FitAddon !== 'undefined') {
      this._fitAddon = new FitAddon.FitAddon();
      this._term.loadAddon(this._fitAddon);
    }

    this._term.open(this._container);
    this._fit();

    // Refit on container resize
    if (typeof ResizeObserver !== 'undefined') {
      this._resizeObs = new ResizeObserver(() => this._fit());
      this._resizeObs.observe(this._container);
    }
  }

  _fit() {
    if (this._fitAddon) {
      try { this._fitAddon.fit(); } catch (e) { console.warn('[XTermLog] fit failed:', e); }
    }
  }

  clear() {
    this._term.clear();
    this._term.reset();
  }

  writeln(text) {
    this._term.writeln(text);
  }

  write(text) {
    this._term.write(text);
  }

  scrollToBottom() {
    this._term.scrollToBottom();
  }

  dispose() {
    if (this._resizeObs) this._resizeObs.disconnect();
    if (this._term) this._term.dispose();
  }

  // ─────────────────────────────────────────────────────────
  // High-level renderers for our data formats
  // ─────────────────────────────────────────────────────────

  /**
   * Render execution logs (from /api/node/{id}/logs or /api/employee/{id}/logs)
   */
  renderLogs(logs) {
    this.clear();
    if (!logs || !logs.length) {
      this.writeln(`${ANSI.gray}No execution logs${ANSI.reset}`);
      return;
    }
    const steps = traceGroupSteps(logs);
    for (const step of steps) {
      this._renderStep(step);
    }
  }

  /**
   * Render trace feed (full project tree with inline logs)
   */
  renderTraceFeed(nodes, rootId) {
    this.clear();
    if (!rootId || !nodes[rootId]) {
      this.writeln(`${ANSI.gray}No trace data${ANSI.reset}`);
      return;
    }
    this._renderFeedNode(nodes, rootId, '');
  }

  // ─────────────────────────────────────────────────────────
  // Step renderers (for execution logs)
  // ─────────────────────────────────────────────────────────

  _renderStep(step) {
    const ts = step.timestamp ? `${ANSI.gray}${step.timestamp.substring(11, 19)}${ANSI.reset}` : '';

    if (step.type === 'tool') {
      const name = step.toolName || '';
      const input = traceExtractToolInput(step.input).substring(0, 100);
      const result = step.result ? traceExtractToolResult(step.result.content, name).substring(0, 120) : '';
      const ok = step.result && !(step.result.content || '').includes('error');
      const icon = step.result ? (ok ? `${ANSI.green}\u2713${ANSI.reset}` : `${ANSI.red}\u2717${ANSI.reset}`) : `${ANSI.gray}\u2026${ANSI.reset}`;

      this.writeln(`${ts} ${ANSI.cyan}tool${ANSI.reset} ${ANSI.brightCyan}${name}${ANSI.reset} ${input}`);
      if (result) {
        this.writeln(`         ${ANSI.green}\u2192 ${result}${ANSI.reset} ${icon}`);
      }
    } else if (step.type === 'llm_output') {
      const summary = (step.content || '').split('\n')[0].substring(0, 120);
      this.writeln(`${ts} ${ANSI.yellow}llm${ANSI.reset}  ${ANSI.dim}${summary}${ANSI.reset}`);
    } else if (step.type === 'start') {
      const content = (step.content || '').substring(0, 120).replace(/\n/g, ' ');
      this.writeln(`${ts} ${ANSI.white}start${ANSI.reset} ${content}`);
    } else if (step.type === 'result' || step.type === 'end') {
      const content = (step.content || '').substring(0, 120).replace(/\n/g, ' ');
      this.writeln(`${ts} ${ANSI.green}${step.type}${ANSI.reset}  ${content}`);
    } else if (step.type === 'holding' || step.type === 'auto_holding') {
      const content = (step.content || '').substring(0, 100);
      this.writeln(`${ts} ${ANSI.yellow}hold${ANSI.reset}  ${content}`);
    } else if (step.type === 'error') {
      const content = (step.content || '').substring(0, 150);
      this.writeln(`${ts} ${ANSI.red}error${ANSI.reset} ${content}`);
    }
  }

  // ─────────────────────────────────────────────────────────
  // Feed renderer (tree + inline logs)
  // ─────────────────────────────────────────────────────────

  _renderFeedNode(nodes, nodeId, prefix) {
    const node = nodes[nodeId];
    if (!node) return;

    const emp = node.employee_info || {};
    const name = emp.nickname || emp.name || node.employee_id || '';
    const status = node.status || '';
    const dur = this._dur(node.created_at, node.completed_at);
    const cost = node.cost_usd > 0 ? ` $${node.cost_usd.toFixed(4)}` : '';
    const type = this._typeLabel(node.node_type);
    const sColor = this._statusAnsi(status);
    const sIcon = this._statusIcon(status);

    // Node header
    this.writeln(`${ANSI.gray}${prefix}${ANSI.reset}${sColor}${sIcon}${ANSI.reset} ${ANSI.bold}${ANSI.white}${name}${ANSI.reset}${type ? ` ${ANSI.gray}${type}${ANSI.reset}` : ''} ${ANSI.gray}${dur}${cost}${ANSI.reset}`);

    // Description
    const desc = (node.description_preview || '').substring(0, 100).replace(/\n/g, ' ');
    if (desc) {
      this.writeln(`${ANSI.gray}${prefix}${ANSI.reset}  ${ANSI.dim}${desc}${ANSI.reset}`);
    }

    // Inline execution logs
    const logs = node._logs || [];
    if (logs.length > 0) {
      const steps = traceGroupSteps(logs);
      for (const step of steps) {
        const ts = step.timestamp ? step.timestamp.substring(11, 19) : '';
        if (step.type === 'tool') {
          const toolName = step.toolName || '';
          const input = traceExtractToolInput(step.input).substring(0, 70);
          const result = step.result ? traceExtractToolResult(step.result.content, toolName).substring(0, 80) : '';
          const ok = step.result && !(step.result.content || '').includes('error');
          const icon = step.result ? (ok ? `${ANSI.green}\u2713${ANSI.reset}` : `${ANSI.red}\u2717${ANSI.reset}`) : '';
          this.writeln(`${ANSI.gray}${prefix}  ${ts}${ANSI.reset} ${ANSI.cyan}tool${ANSI.reset} ${ANSI.brightCyan}${toolName}${ANSI.reset} ${input}`);
          if (result) this.writeln(`${ANSI.gray}${prefix}           ${ANSI.green}\u2192 ${result}${ANSI.reset} ${icon}`);
        } else if (step.type === 'llm_output') {
          const summary = (step.content || '').split('\n')[0].substring(0, 90);
          this.writeln(`${ANSI.gray}${prefix}  ${ts}${ANSI.reset} ${ANSI.yellow}llm${ANSI.reset}  ${ANSI.dim}${summary}${ANSI.reset}`);
        } else if (step.type === 'start') {
          const content = (step.content || '').substring(0, 90).replace(/\n/g, ' ');
          this.writeln(`${ANSI.gray}${prefix}  ${ts} start${ANSI.reset} ${content}`);
        } else if (step.type === 'result' || step.type === 'end') {
          const content = (step.content || '').substring(0, 90).replace(/\n/g, ' ');
          this.writeln(`${ANSI.gray}${prefix}  ${ts}${ANSI.reset} ${ANSI.green}${step.type}${ANSI.reset}  ${content}`);
        }
      }
    }

    // Result
    if (node.result && ['completed', 'accepted', 'finished'].includes(status)) {
      const r = node.result.substring(0, 120).replace(/\n/g, ' ');
      this.writeln(`${ANSI.gray}${prefix}${ANSI.reset}  ${ANSI.green}\u2192 ${r}${ANSI.reset}`);
    }

    this.writeln('');  // blank line

    // Children
    const children = (node.children_ids || []).filter(id => nodes[id]);
    for (const childId of children) {
      this._renderFeedNode(nodes, childId, prefix + '\u2502 ');
    }
  }

  _statusAnsi(s) {
    return { pending: ANSI.gray, processing: ANSI.brightYellow, holding: ANSI.yellow,
      completed: ANSI.green, accepted: ANSI.green, finished: ANSI.brightGreen,
      failed: ANSI.red, blocked: ANSI.red, cancelled: ANSI.dim }[s] || ANSI.gray;
  }

  _statusIcon(s) {
    return { pending: '\u2591', processing: '\u2593', holding: '\u2592',
      completed: '\u2588', accepted: '\u2588', finished: '\u2588',
      failed: '\u2573', blocked: '\u2592', cancelled: '\u2573' }[s] || '\u2591';
  }

  _typeLabel(t) {
    return { ceo_prompt: 'CEO', review: 'REVIEW', ceo_request: 'CEO_REQ',
      watchdog_nudge: 'WD', system: 'SYS' }[t] || '';
  }

  _dur(start, end) {
    if (!start) return '';
    const s = Math.floor(((end ? new Date(end) : new Date()) - new Date(start)) / 1000);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
    return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
  }
}
