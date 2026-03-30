/**
 * CeoTerminal — xterm.js-based CEO conversation terminal (READ-ONLY display).
 *
 * The project list is rendered as HTML by app.js in the left panel.
 * Input is handled by a separate HTML textarea wired in app.js.
 * This class handles only the conversation display with dividers.
 */

class CeoTerminal {
  constructor(container) {
    this._container = typeof container === 'string'
      ? document.getElementById(container) : container;
    this._term = null;
    this._fitAddon = null;
    this._currentProjectId = null;
    this._history = [];
    this._init();
  }

  _init() {
    this._term = new Terminal({
      disableStdin: true,      // READ-ONLY
      convertEol: true,
      fontSize: 11,
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      theme: {
        background: '#0a0a0a',
        foreground: '#d4d4d4',
        cursor: '#0a0a0a',      // hidden cursor
        cursorAccent: '#0a0a0a',
      },
      scrollback: 5000,
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

    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => this._fit()).observe(this._container);
    }

    this._showWelcome();
  }

  _fit() {
    if (this._fitAddon) try { this._fitAddon.fit(); } catch(e) {}
  }

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

  _divider() {
    const A = this._A();
    const cols = this._term.cols || 40;
    this._term.writeln(`${A.dim}${'─'.repeat(Math.max(cols - 2, 10))}${A.reset}`);
  }

  showChat(projectId, history) {
    this._currentProjectId = projectId;
    this._history = history || [];
    this._term.clear();
    this._term.reset();

    const A = this._A();
    const name = projectId ? projectId.split('/')[0] : 'New Task';
    const displayName = name.length > 25 ? name.substring(0, 25) + '\u2026' : name;
    this._term.writeln(`${A.cyan}${A.bold} ${displayName}${A.reset}`);
    this._divider();

    if (this._history.length) {
      for (const msg of this._history) {
        this._writeMsg(msg);
        this._divider();
      }
    } else {
      this._term.writeln(`${A.gray}  No messages yet.${A.reset}`);
    }

    this._term.scrollToBottom();
  }

  appendMessage(msg) {
    this._writeMsg(msg);
    this._divider();
    this._history.push(msg);
    this._term.scrollToBottom();
  }

  // Append CEO's own message (called immediately when CEO sends)
  appendCeoMessage(text) {
    this._writeMsg({ role: 'ceo', text });
    this._divider();
    this._history.push({ role: 'ceo', text });
    this._term.scrollToBottom();
  }

  _writeMsg(msg) {
    const A = this._A();
    const lines = (msg.text || '').split('\n');
    if (msg.role === 'ceo') {
      this._term.writeln(`  ${A.brightGreen}CEO${A.reset} ${A.dim}›${A.reset} ${A.brightWhite}${lines[0]}${A.reset}`);
      for (let i = 1; i < lines.length; i++) {
        this._term.writeln(`  ${A.brightWhite}${lines[i]}${A.reset}`);
      }
    } else {
      const src = msg.source || 'system';
      const srcColor = src === 'ea_auto_reply' ? A.yellow : A.cyan;
      this._term.writeln(`  ${srcColor}[${src}]${A.reset} ${A.white}${lines[0]}${A.reset}`);
      for (let i = 1; i < lines.length; i++) {
        this._term.writeln(`  ${A.white}${lines[i]}${A.reset}`);
      }
    }
  }
}
