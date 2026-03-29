/**
 * CeoTerminal — xterm.js-based CEO conversation terminal
 *
 * Renders project selection (InquirerPy style) and conversation
 * history in a terminal, with a command prompt for CEO input.
 */

// ANSI escape codes
const _A = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m',
  white: '\x1b[37m',
  gray: '\x1b[90m',
  brightGreen: '\x1b[92m',
  brightYellow: '\x1b[93m',
  brightCyan: '\x1b[96m',
  brightWhite: '\x1b[97m',
  bgBlue: '\x1b[44m',
};

class CeoTerminal {
  constructor(container) {
    this._container = typeof container === 'string'
      ? document.getElementById(container) : container;
    this._term = null;
    this._fitAddon = null;

    // State
    this._mode = 'select';  // 'select' | 'chat'
    this._sessions = [];
    this._selectedIndex = 0;
    this._currentProjectId = null;
    this._history = [];
    this._inputBuffer = '';
    this._inputHistory = [];
    this._inputHistoryIndex = -1;

    // Project name map (populated externally)
    this._projectNames = {};

    // Callbacks
    this._onSend = null;           // (projectId, text) => Promise
    this._onNewTask = null;        // (text) => Promise
    this._onSelectProject = null;  // (projectId) => Promise<history>

    this._init();
  }

  _init() {
    this._term = new Terminal({
      disableStdin: false,
      convertEol: true,
      fontSize: 12,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', monospace",
      theme: {
        background: '#0a0a0a',
        foreground: '#b0b0b0',
        cursor: '#44aa44',
        cursorAccent: '#0a0a0a',
        black: '#333333',
        red: '#ff4444',
        green: '#44aa44',
        yellow: '#ffaa44',
        blue: '#44aaff',
        magenta: '#aa44ff',
        cyan: '#44aaaa',
        white: '#d4d4d4',
        brightBlack: '#666666',
        brightGreen: '#66cc66',
        brightYellow: '#ffcc66',
        brightBlue: '#66ccff',
        brightWhite: '#ffffff',
      },
      scrollback: 5000,
      cursorBlink: true,
      cursorStyle: 'bar',
    });

    if (typeof FitAddon !== 'undefined') {
      this._fitAddon = new FitAddon.FitAddon();
      this._term.loadAddon(this._fitAddon);
    }

    this._term.open(this._container);
    this._fit();

    if (typeof ResizeObserver !== 'undefined') {
      this._resizeObs = new ResizeObserver(() => this._fit());
      this._resizeObs.observe(this._container);
    }

    // Key handling
    this._term.onKey(({key, domEvent}) => {
      if (this._mode === 'select') {
        this._handleSelectKey(key, domEvent);
      } else {
        this._handleChatKey(key, domEvent);
      }
    });

    // Paste support
    this._term.onData((data) => {
      if (this._mode === 'chat' && data.length > 1 && !data.startsWith('\x1b')) {
        // Pasted text
        this._inputBuffer += data;
        this._term.write(data);
      }
    });
  }

  _fit() {
    if (this._fitAddon) {
      try { this._fitAddon.fit(); } catch (e) { /* ignore */ }
    }
  }

  // ---- Public API ---- //

  onSend(cb) { this._onSend = cb; }
  onNewTask(cb) { this._onNewTask = cb; }
  onSelectProject(cb) { this._onSelectProject = cb; }

  setProjectNames(nameMap) {
    this._projectNames = nameMap || {};
  }

  async setSessions(sessions) {
    this._sessions = sessions;
    if (this._mode === 'select') {
      this._renderSelect();
    }
  }

  async showSelect() {
    this._mode = 'select';
    this._term.clear();
    this._term.reset();
    this._renderSelect();
  }

  async showChat(projectId, history) {
    this._mode = 'chat';
    this._currentProjectId = projectId;
    this._history = history || [];
    this._inputBuffer = '';
    this._term.clear();
    this._term.reset();
    this._renderChat();
    this._renderPrompt();
  }

  appendMessage(msg) {
    if (this._mode !== 'chat') return;
    // Erase current prompt line, write message, re-render prompt
    this._term.write('\r\x1b[K');
    this._renderMessage(msg);
    this._history.push(msg);
    this._renderPrompt();
  }

  // ---- Select Mode (InquirerPy style) ---- //

  _getDisplayName(projectId) {
    const basePid = (projectId || '').split('/')[0];
    const name = this._projectNames[basePid] || basePid;
    return name.length > 30 ? name.substring(0, 30) + '\u2026' : name;
  }

  _renderSelect() {
    this._term.clear();
    this._term.reset();
    const w = _A;

    this._term.writeln(`${w.brightGreen}?${w.reset} ${w.bold}Select project:${w.reset} ${w.gray}(arrows navigate, Enter select, type to filter)${w.reset}`);
    this._term.writeln('');

    const items = [];
    for (const s of this._sessions) {
      const pid = s.project_id || '';
      const name = this._getDisplayName(pid);
      const pending = s.has_pending ? `${w.yellow}\u25cf${w.reset} ` : '  ';
      const count = s.has_pending ? ` ${w.gray}(${s.pending_count || ''} pending)${w.reset}` : '';
      items.push({ label: `${pending}${name}${count}`, projectId: pid, type: 'project' });
    }
    items.push({ label: `${w.cyan}+ New Task${w.reset}`, projectId: null, type: 'new' });

    // Clamp selected index
    if (this._selectedIndex >= items.length) this._selectedIndex = items.length - 1;
    if (this._selectedIndex < 0) this._selectedIndex = 0;

    for (let i = 0; i < items.length; i++) {
      const arrow = i === this._selectedIndex
        ? `${w.brightGreen}\u276f${w.reset} `
        : '  ';
      const highlight = i === this._selectedIndex ? w.brightWhite : '';
      const reset = i === this._selectedIndex ? w.reset : '';
      this._term.writeln(`${arrow}${highlight}${items[i].label}${reset}`);
    }

    this._selectItems = items;
  }

  _handleSelectKey(key, domEvent) {
    const items = this._selectItems || [];
    if (!items.length) return;

    if (domEvent.key === 'ArrowUp' || key === '\x1b[A') {
      this._selectedIndex = Math.max(0, this._selectedIndex - 1);
      this._renderSelect();
    } else if (domEvent.key === 'ArrowDown' || key === '\x1b[B') {
      this._selectedIndex = Math.min(items.length - 1, this._selectedIndex + 1);
      this._renderSelect();
    } else if (domEvent.key === 'Enter' || key === '\r') {
      const item = items[this._selectedIndex];
      if (item.type === 'new') {
        // Switch to chat mode with no project (new task)
        this._mode = 'chat';
        this._currentProjectId = null;
        this._history = [];
        this._inputBuffer = '';
        this._term.clear();
        this._term.reset();
        this._term.writeln(`${_A.brightGreen}?${_A.reset} ${_A.bold}New Task${_A.reset} ${_A.gray}(type task description, Enter to submit, Esc to go back)${_A.reset}`);
        this._term.writeln('');
        this._renderPrompt();
      } else if (item.projectId && this._onSelectProject) {
        this._onSelectProject(item.projectId);
      }
    }
  }

  // ---- Chat Mode ---- //

  _renderChat() {
    const w = _A;

    if (this._currentProjectId) {
      const name = this._getDisplayName(this._currentProjectId);
      this._term.writeln(`${w.bold}${w.cyan}\u2500\u2500\u2500 ${name} \u2500\u2500\u2500${w.reset} ${w.gray}(Esc: back to projects)${w.reset}`);
    } else {
      this._term.writeln(`${w.bold}${w.cyan}\u2500\u2500\u2500 New Task \u2500\u2500\u2500${w.reset} ${w.gray}(Esc: back to projects)${w.reset}`);
    }
    this._term.writeln('');

    for (const msg of this._history) {
      this._renderMessage(msg);
    }

    if (!this._history.length) {
      this._term.writeln(`${w.gray}  No messages yet.${w.reset}`);
      this._term.writeln('');
    }
  }

  _renderMessage(msg) {
    const w = _A;
    const isCeo = msg.role === 'ceo';

    if (isCeo) {
      this._term.writeln(`${w.brightGreen}CEO${w.reset} ${w.gray}>${w.reset} ${w.white}${msg.text || ''}${w.reset}`);
    } else {
      const source = msg.source || 'system';
      const sourceColor = source === 'ea_auto_reply' ? w.yellow : w.blue;
      this._term.writeln(`${sourceColor}[${source}]${w.reset} ${w.gray}${msg.text || ''}${w.reset}`);
    }
  }

  _renderPrompt() {
    const w = _A;
    this._term.write(`\r\n${w.brightGreen}$${w.reset} ${this._inputBuffer}`);
  }

  _handleChatKey(key, domEvent) {
    // Escape -> back to select
    if (domEvent.key === 'Escape' || key === '\x1b') {
      this.showSelect();
      return;
    }

    // Enter -> send
    if (domEvent.key === 'Enter' || key === '\r') {
      const text = this._inputBuffer.trim();
      if (!text) return;

      this._inputHistory.push(text);
      this._inputHistoryIndex = this._inputHistory.length;
      this._inputBuffer = '';
      this._term.writeln('');  // newline after input

      if (!this._currentProjectId && this._onNewTask) {
        this._onNewTask(text);
      } else if (this._onSend) {
        // Show CEO message immediately
        this._renderMessage({ role: 'ceo', text: text });
        this._onSend(this._currentProjectId, text);
      }

      // Don't render prompt yet — wait for response or callback to re-render
      return;
    }

    // Backspace
    if (domEvent.key === 'Backspace' || key === '\x7f') {
      if (this._inputBuffer.length > 0) {
        this._inputBuffer = this._inputBuffer.slice(0, -1);
        this._term.write('\b \b');
      }
      return;
    }

    // Arrow up/down for input history
    if (domEvent.key === 'ArrowUp' || key === '\x1b[A') {
      if (this._inputHistoryIndex > 0) {
        this._inputHistoryIndex--;
        this._replaceInput(this._inputHistory[this._inputHistoryIndex]);
      }
      return;
    }
    if (domEvent.key === 'ArrowDown' || key === '\x1b[B') {
      if (this._inputHistoryIndex < this._inputHistory.length - 1) {
        this._inputHistoryIndex++;
        this._replaceInput(this._inputHistory[this._inputHistoryIndex]);
      } else {
        this._inputHistoryIndex = this._inputHistory.length;
        this._replaceInput('');
      }
      return;
    }

    // Regular character
    if (key.length === 1 && !domEvent.ctrlKey && !domEvent.metaKey) {
      this._inputBuffer += key;
      this._term.write(key);
    }
  }

  _replaceInput(text) {
    const w = _A;
    this._term.write(`\r\x1b[K${w.brightGreen}$${w.reset} ${text}`);
    this._inputBuffer = text;
  }
}
