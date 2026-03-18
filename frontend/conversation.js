/* ===== ChatPanel — Reusable conversation component ===== */

class ChatPanel {
    constructor(containerEl) {
        this._container = containerEl;
        this._messagesEl = null;
        this._inputEl = null;
        this._sendBtn = null;
        this._closeBtn = null;
        this._typingEl = null;
        this._fileInput = null;
        this._onSendCb = null;
        this._onCloseCb = null;
        this._convId = null;
        this._convType = null;
        this._render();
    }

    _render() {
        this._container.innerHTML = `
            <div class="chat-panel">
                <div class="chat-panel-header">
                    <span class="chat-panel-type"></span>
                    <span class="chat-panel-employee"></span>
                    <button class="chat-panel-close-btn">End</button>
                </div>
                <div class="chat-panel-messages"></div>
                <div class="chat-panel-typing hidden">typing...</div>
                <div class="chat-panel-input-row">
                    <textarea class="chat-panel-input" rows="2" placeholder="Type a message..."></textarea>
                    <div class="chat-panel-actions">
                        <label class="chat-panel-upload-label">
                            <input type="file" class="chat-panel-file" multiple hidden />
                            +
                        </label>
                        <button class="chat-panel-send-btn">Send</button>
                    </div>
                </div>
            </div>
        `;
        this._messagesEl = this._container.querySelector('.chat-panel-messages');
        this._inputEl = this._container.querySelector('.chat-panel-input');
        this._sendBtn = this._container.querySelector('.chat-panel-send-btn');
        this._closeBtn = this._container.querySelector('.chat-panel-close-btn');
        this._typingEl = this._container.querySelector('.chat-panel-typing');
        this._fileInput = this._container.querySelector('.chat-panel-file');

        this._sendBtn.addEventListener('click', () => this._handleSend());
        this._closeBtn.addEventListener('click', () => {
            if (this._onCloseCb) this._onCloseCb(this._convId);
        });
        this._inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this._handleSend();
            }
        });
    }

    async _handleSend() {
        const text = this._inputEl.value.trim();
        if (!text || !this._onSendCb) return;
        this._inputEl.value = '';

        let attachments = [];
        if (this._fileInput.files.length > 0) {
            const formData = new FormData();
            for (const file of this._fileInput.files) {
                formData.append('files', file);
            }
            try {
                const resp = await fetch(`/api/conversation/${this._convId}/upload`, {
                    method: 'POST', body: formData,
                });
                const data = await resp.json();
                attachments = data.attachments || [];
            } catch (err) {
                console.error('Upload failed:', err);
            }
            this._fileInput.value = '';
        }

        this._onSendCb(this._convId, text, attachments);
    }

    setConversation(convId, convType, employeeName) {
        this._convId = convId;
        this._convType = convType;
        this._container.querySelector('.chat-panel-type').textContent =
            convType === 'oneonone' ? '1-on-1' : 'Inbox';
        this._container.querySelector('.chat-panel-employee').textContent = employeeName;
    }

    renderMessages(messages) {
        this._messagesEl.innerHTML = '';
        for (const msg of messages) {
            this._appendMessageEl(msg);
        }
        this._messagesEl.scrollTop = this._messagesEl.scrollHeight;
    }

    appendMessage(msg) {
        this._appendMessageEl(msg);
        this._messagesEl.scrollTop = this._messagesEl.scrollHeight;
        this.showTyping(false);
    }

    _appendMessageEl(msg) {
        const div = document.createElement('div');
        const isCeo = msg.sender === 'ceo';
        div.className = `chat-msg ${isCeo ? 'chat-msg-ceo' : 'chat-msg-agent'}`;
        div.innerHTML = `
            <div class="chat-msg-role">${this._escapeHtml(msg.role)}</div>
            <div class="chat-msg-text">${this._escapeHtml(msg.text)}</div>
            ${msg.attachments && msg.attachments.length
                ? `<div class="chat-msg-attachments">${msg.attachments.map(a =>
                    `<span class="chat-msg-attachment">${this._escapeHtml(a.split('/').pop())}</span>`
                  ).join('')}</div>`
                : ''}
        `;
        this._messagesEl.appendChild(div);
    }

    setInputEnabled(enabled) {
        this._inputEl.disabled = !enabled;
        this._sendBtn.disabled = !enabled;
        this._closeBtn.style.display = enabled ? '' : 'none';
    }

    showTyping(show) {
        this._typingEl.classList.toggle('hidden', !show);
    }

    getConvId() { return this._convId; }
    getConvType() { return this._convType; }
    onSend(cb) { this._onSendCb = cb; }
    onClose(cb) { this._onCloseCb = cb; }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}
