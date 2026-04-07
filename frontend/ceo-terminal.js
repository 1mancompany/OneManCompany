/**
 * CeoTerminal — DOM-based CEO conversation renderer.
 *
 * Replaces the xterm.js version. Uses DOM elements for messages and tool calls.
 * CSS preserves the terminal aesthetic (monospace, dark background).
 * Tool calls are interactive: click to expand/collapse args and results.
 */

class CeoTerminal {
  constructor(container) {
    this._container = typeof container === 'string'
      ? document.getElementById(container) : container;
    this._currentProjectId = null;
    this._history = [];
    this._pendingToolCalls = new Map(); // employee_id → { element, toolName, startTime }
    this._init();
  }

  _init() {
    this._container.classList.add('ceo-conv-scroll');
    this._showWelcome();
  }

  _fit() {
    // No-op — DOM doesn't need fitting. Kept for API compat with app.js calls.
  }

  _showWelcome() {
    this._container.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'ceo-msg--system';
    el.style.color = '#71717a';
    el.style.padding = '8px 0';
    el.textContent = '  Select a project to start';
    this._container.appendChild(el);
  }

  showChat(projectId, history) {
    this._currentProjectId = projectId;
    this._history = history || [];
    this._pendingToolCalls.clear();
    this._container.innerHTML = '';

    // Header
    const name = projectId === '_ea_chat'
      ? 'Chat with EA'
      : (projectId ? projectId.split('/')[0] : 'New Task');
    const displayName = name.length > 25 ? name.substring(0, 25) + '\u2026' : name;
    const header = document.createElement('div');
    header.className = 'ceo-conv-header';
    header.textContent = ` ${displayName}`;
    this._container.appendChild(header);
    this._addDivider();

    if (this._history.length) {
      for (const msg of this._history) {
        this._renderMsg(msg);
        this._addDivider();
      }
    } else {
      const empty = document.createElement('div');
      empty.style.color = '#71717a';
      empty.style.padding = '4px 0';
      empty.textContent = '  No messages yet.';
      this._container.appendChild(empty);
    }

    this._scrollToBottom();
  }

  appendMessage(msg) {
    // Remove "No messages yet" placeholder if present
    const placeholder = this._container.querySelector('div[style*="71717a"]');
    if (placeholder && placeholder.textContent.includes('No messages yet')) {
      placeholder.remove();
    }
    this._renderMsg(msg);
    this._addDivider();
    this._history.push(msg);
    this._scrollToBottom();
  }

  appendCeoMessage(text) {
    this.appendMessage({ role: 'ceo', text });
  }

  /**
   * Append a tool call element (running state).
   * Called when AGENT_LOG with type=tool_call arrives.
   */
  appendToolCall({ employeeId, toolName, toolArgs }) {
    const card = document.createElement('div');
    card.className = 'ceo-tool-call running';
    card.dataset.employee = employeeId;
    card.dataset.toolName = toolName;

    // Header row
    const headerEl = document.createElement('div');
    headerEl.className = 'ceo-tool-call-header';
    headerEl.innerHTML = `
      <span class="ceo-tool-icon">\u2699</span>
      <span class="ceo-tool-name">${this._esc(toolName)}</span>
      <span class="ceo-tool-status">\u23F3</span>
      <span class="ceo-tool-duration"></span>
    `;
    card.appendChild(headerEl);

    // Details (hidden by default)
    const details = document.createElement('div');
    details.className = 'ceo-tool-call-details';

    if (toolArgs && typeof toolArgs === 'object' && Object.keys(toolArgs).length > 0) {
      const argsDiv = document.createElement('div');
      argsDiv.className = 'ceo-tool-args';
      for (const [k, v] of Object.entries(toolArgs)) {
        const line = document.createElement('div');
        line.textContent = `${k}: ${JSON.stringify(v)}`;
        argsDiv.appendChild(line);
      }
      details.appendChild(argsDiv);
    }

    // Result placeholder
    const resultDiv = document.createElement('div');
    resultDiv.className = 'ceo-tool-result';
    resultDiv.style.display = 'none';
    details.appendChild(resultDiv);

    card.appendChild(details);

    // Click to expand/collapse
    headerEl.addEventListener('click', () => {
      card.classList.toggle('expanded');
    });

    this._container.appendChild(card);
    this._scrollToBottom();

    // Track pending
    this._pendingToolCalls.set(employeeId, {
      element: card,
      toolName,
      startTime: Date.now(),
    });
  }

  /**
   * Update a pending tool call to done/error state.
   * Called when AGENT_LOG with type=tool_result arrives.
   */
  updateToolCall(employeeId, { toolName, toolResult, error }) {
    const pending = this._pendingToolCalls.get(employeeId);
    let card;

    if (pending && pending.toolName === toolName) {
      card = pending.element;
      const durationMs = Date.now() - pending.startTime;
      const durationStr = durationMs < 1000
        ? `${durationMs}ms`
        : `${(durationMs / 1000).toFixed(1)}s`;
      card.querySelector('.ceo-tool-duration').textContent = durationStr;
      this._pendingToolCalls.delete(employeeId);
    } else {
      // No pending match — render as standalone done element
      card = document.createElement('div');
      card.className = 'ceo-tool-call';
      card.dataset.employee = employeeId;
      card.dataset.toolName = toolName;

      const headerEl = document.createElement('div');
      headerEl.className = 'ceo-tool-call-header';
      headerEl.innerHTML = `
        <span class="ceo-tool-icon">\u2699</span>
        <span class="ceo-tool-name">${this._esc(toolName)}</span>
        <span class="ceo-tool-status"></span>
        <span class="ceo-tool-duration"></span>
      `;
      card.appendChild(headerEl);

      const details = document.createElement('div');
      details.className = 'ceo-tool-call-details';
      const resultDiv = document.createElement('div');
      resultDiv.className = 'ceo-tool-result';
      details.appendChild(resultDiv);
      card.appendChild(details);

      headerEl.addEventListener('click', () => card.classList.toggle('expanded'));
      this._container.appendChild(card);
    }

    // Update status
    if (error) {
      card.classList.remove('running');
      card.classList.add('error');
      card.querySelector('.ceo-tool-status').textContent = '\u2717';
    } else {
      card.classList.remove('running');
      card.classList.add('done');
      card.querySelector('.ceo-tool-status').textContent = '\u2713';
    }

    // Set result text
    const resultDiv = card.querySelector('.ceo-tool-result');
    if (resultDiv && toolResult) {
      const truncated = toolResult.length > 500
        ? toolResult.substring(0, 500) + '...'
        : toolResult;
      resultDiv.textContent = truncated;
      resultDiv.style.display = '';
    }

    this._scrollToBottom();
  }

  _renderMsg(msg) {
    const el = document.createElement('div');

    if (msg.role === 'ceo') {
      el.className = 'ceo-msg ceo-msg--ceo';
      el.innerHTML = `<span class="ceo-msg-sender">CEO</span>`
        + `<span class="ceo-msg-arrow">\u203A</span>`
        + `<span class="ceo-msg-text">${this._esc(msg.text || '')}</span>`;
    } else if (msg.type === 'tool_call') {
      // Tool call from history — render as done card
      this._renderHistoryToolCall(msg);
      return;
    } else {
      const src = msg.source || 'system';
      const cls = src === 'ea_auto_reply' ? 'ceo-msg--system' : 'ceo-msg--agent';
      el.className = `ceo-msg ${cls}`;
      el.innerHTML = `<span class="ceo-msg-sender">[${this._esc(src)}]</span>`
        + `<span class="ceo-msg-text">${this._esc(msg.text || '')}</span>`;
    }

    this._container.appendChild(el);
  }

  /**
   * Render a tool call from history (already completed).
   */
  _renderHistoryToolCall(msg) {
    const card = document.createElement('div');
    card.className = 'ceo-tool-call done';
    card.dataset.employee = msg.employee_id || '';

    const headerEl = document.createElement('div');
    headerEl.className = 'ceo-tool-call-header';
    headerEl.innerHTML = `
      <span class="ceo-tool-icon">\u2699</span>
      <span class="ceo-tool-name">${this._esc(msg.tool_name || '')}</span>
      <span class="ceo-tool-status">\u2713</span>
      <span class="ceo-tool-duration"></span>
    `;
    card.appendChild(headerEl);

    const details = document.createElement('div');
    details.className = 'ceo-tool-call-details';

    if (msg.tool_args && typeof msg.tool_args === 'object') {
      const argsDiv = document.createElement('div');
      argsDiv.className = 'ceo-tool-args';
      for (const [k, v] of Object.entries(msg.tool_args)) {
        const line = document.createElement('div');
        line.textContent = `${k}: ${JSON.stringify(v)}`;
        argsDiv.appendChild(line);
      }
      details.appendChild(argsDiv);
    }

    if (msg.tool_result) {
      const resultDiv = document.createElement('div');
      resultDiv.className = 'ceo-tool-result';
      const truncated = msg.tool_result.length > 500
        ? msg.tool_result.substring(0, 500) + '...'
        : msg.tool_result;
      resultDiv.textContent = truncated;
      details.appendChild(resultDiv);
    }

    card.appendChild(details);
    headerEl.addEventListener('click', () => card.classList.toggle('expanded'));
    this._container.appendChild(card);
  }

  _addDivider() {
    const div = document.createElement('div');
    div.className = 'ceo-msg-divider';
    this._container.appendChild(div);
  }

  _scrollToBottom() {
    this._container.scrollTop = this._container.scrollHeight;
  }

  _esc(str) {
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
  }
}
