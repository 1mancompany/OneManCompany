/**
 * CeoTerminal — xterm.js-based CEO conversation terminal (right panel only).
 *
 * The project list is rendered as HTML by app.js in the left panel.
 * This class handles only the conversation view + input prompt.
 */

class CeoTerminal {
  constructor(container) {
    this._container = typeof container === 'string'
      ? document.getElementById(container) : container;
    this._term = null;
    this._fitAddon = null;
    this._currentProjectId = null;
    this._history = [];
    this._inputBuffer = '';
    this._inputHistory = [];
    this._inputHistoryIdx = -1;
    this._onSend = null;  // (projectId, text) => void
    this._init();
  }

  _init() {
    this._term = new Terminal({
      disableStdin: false,
      convertEol: true,
      fontSize: 11,
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      theme: {
        background: '#0a0a0a',
        foreground: '#b0b0b0',
        cursor: '#44aa44',
        cursorAccent: '#0a0a0a',
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
      new ResizeObserver(() => this._fit()).observe(this._container);
    }

    this._term.onKey(({key, domEvent}) => this._handleKey(key, domEvent));

    // Paste support
    this._term.onData((data) => {
      if (data.length > 1 && !data.startsWith('\x1b')) {
        this._inputBuffer += data;
        this._term.write(data);
      }
    });

    // Show welcome
    this._showWelcome();
  }

  _fit() {
    if (this._fitAddon) try { this._fitAddon.fit(); } catch(e) {}
  }

  onSend(cb) { this._onSend = cb; }

  _A() {
    return {
      reset: '\x1b[0m', bold: '\x1b[1m', dim: '\x1b[2m',
      green: '\x1b[32m', yellow: '\x1b[33m', blue: '\x1b[34m',
      cyan: '\x1b[36m', white: '\x1b[37m', gray: '\x1b[90m',
      brightGreen: '\x1b[92m', brightWhite: '\x1b[97m',
    };
  }

  _showWelcome() {
    const A = this._A();
    this._term.writeln(`${A.gray}  Select a project to start${A.reset}`);
  }

  showChat(projectId, history) {
    this._currentProjectId = projectId;
    this._history = history || [];
    this._inputBuffer = '';
    this._term.clear();
    this._term.reset();

    const A = this._A();
    const name = projectId ? projectId.split('/')[0] : 'New Task';
    const displayName = name.length > 25 ? name.substring(0, 25) + '\u2026' : name;
    this._term.writeln(`${A.cyan}${A.bold}\u2500\u2500 ${displayName} \u2500\u2500${A.reset}`);
    this._term.writeln('');

    if (this._history.length) {
      for (const msg of this._history) this._writeMsg(msg);
    } else {
      this._term.writeln(`${A.gray}  No messages yet.${A.reset}`);
    }
    this._term.writeln('');
    this._writePrompt();
  }

  appendMessage(msg) {
    // Erase prompt line, write message, re-draw prompt
    this._term.write('\r\x1b[K');
    this._writeMsg(msg);
    this._history.push(msg);
    this._writePrompt();
  }

  _writeMsg(msg) {
    const A = this._A();
    if (msg.role === 'ceo') {
      this._term.writeln(`${A.brightGreen}CEO${A.reset} ${A.gray}>${A.reset} ${A.white}${msg.text || ''}${A.reset}`);
    } else {
      const src = msg.source || 'system';
      this._term.writeln(`${A.blue}[${src}]${A.reset} ${A.gray}${msg.text || ''}${A.reset}`);
    }
  }

  _writePrompt() {
    const A = this._A();
    this._term.write(`${A.brightGreen}$${A.reset} `);
  }

  _handleKey(key, ev) {
    if (ev.key === 'Enter' || key === '\r') {
      const text = this._inputBuffer.trim();
      this._inputBuffer = '';
      this._term.writeln('');
      if (text && this._onSend) {
        this._writeMsg({ role: 'ceo', text });
        this._onSend(this._currentProjectId, text);
      }
      this._writePrompt();
      if (text) {
        this._inputHistory.push(text);
        this._inputHistoryIdx = this._inputHistory.length;
      }
      return;
    }
    if (ev.key === 'Backspace' || key === '\x7f') {
      if (this._inputBuffer.length > 0) {
        this._inputBuffer = this._inputBuffer.slice(0, -1);
        this._term.write('\b \b');
      }
      return;
    }
    if (ev.key === 'ArrowUp') {
      if (this._inputHistoryIdx > 0) {
        this._inputHistoryIdx--;
        this._replaceInput(this._inputHistory[this._inputHistoryIdx]);
      }
      return;
    }
    if (ev.key === 'ArrowDown') {
      if (this._inputHistoryIdx < this._inputHistory.length - 1) {
        this._inputHistoryIdx++;
        this._replaceInput(this._inputHistory[this._inputHistoryIdx]);
      } else {
        this._inputHistoryIdx = this._inputHistory.length;
        this._replaceInput('');
      }
      return;
    }
    if (key.length === 1 && !ev.ctrlKey && !ev.metaKey) {
      this._inputBuffer += key;
      this._term.write(key);
    }
  }

  _replaceInput(text) {
    const A = this._A();
    this._term.write(`\r\x1b[K${A.brightGreen}$${A.reset} ${text}`);
    this._inputBuffer = text;
  }
}
