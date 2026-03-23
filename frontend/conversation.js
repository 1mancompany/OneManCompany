/* ===== ChatPanel — Reusable conversation component ===== */

class ChatPanel {
    constructor(containerEl) {
        this._container = containerEl;
        this._messagesEl = null;
        this._inputEl = null;
        this._sendBtn = null;
        this._clearBtn = null;
        this._closeBtn = null;
        this._typingEl = null;
        this._fileInput = null;
        this._onSendCb = null;
        this._onClearCb = null;
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
                    <label class="chat-panel-ea-toggle hidden" title="EA auto-reply if CEO doesn't respond in 60s">
                        <input type="checkbox" class="chat-panel-ea-checkbox" />
                        <span class="chat-panel-ea-label">EA</span>
                    </label>
                    <button class="chat-panel-clear-btn">Clear</button>
                    <button class="chat-panel-close-btn">End</button>
                </div>
                <div class="chat-panel-messages"></div>
                <div class="chat-panel-typing hidden" aria-label="Agent thinking">
                    <span class="chat-panel-typing-dot">.</span>
                    <span class="chat-panel-typing-dot">.</span>
                    <span class="chat-panel-typing-dot">.</span>
                </div>
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
        this._clearBtn = this._container.querySelector('.chat-panel-clear-btn');
        this._closeBtn = this._container.querySelector('.chat-panel-close-btn');
        this._typingEl = this._container.querySelector('.chat-panel-typing');
        this._fileInput = this._container.querySelector('.chat-panel-file');
        this._eaToggle = this._container.querySelector('.chat-panel-ea-toggle');
        this._eaCheckbox = this._container.querySelector('.chat-panel-ea-checkbox');
        this._onEaToggleCb = null;

        this._sendBtn.addEventListener('click', () => this._handleSend());
        this._eaCheckbox.addEventListener('change', () => {
            if (this._onEaToggleCb) this._onEaToggleCb(this._convId, this._eaCheckbox.checked);
        });
        this._clearBtn.addEventListener('click', () => {
            if (this._onClearCb) this._onClearCb(this._convId);
        });
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
        this._clearBtn.style.display = convType === 'oneonone' ? '' : 'none';
        this._closeBtn.textContent = convType === 'ceo_inbox' ? 'Complete' : 'End';
        // Show EA auto-reply toggle only for CEO inbox conversations
        this._eaToggle.classList.toggle('hidden', convType !== 'ceo_inbox');
        this._eaCheckbox.checked = false;
    }

    onEaToggle(cb) { this._onEaToggleCb = cb; }

    renderMessages(messages) {
        this._messagesEl.innerHTML = '';
        for (const msg of messages) {
            this._appendMessageEl(msg);
        }
        this._messagesEl.scrollTop = this._messagesEl.scrollHeight;
        this.showTyping(false);
    }

    appendMessage(msg) {
        this._appendMessageEl(msg);
        this._messagesEl.scrollTop = this._messagesEl.scrollHeight;
        // Keep showing "..." after CEO's own echo message; stop only when agent/system replies.
        if ((msg?.sender || '').toLowerCase() !== 'ceo') {
            this.showTyping(false);
        }
    }

    _appendMessageEl(msg) {
        const div = document.createElement('div');
        const isCeo = msg.sender === 'ceo';
        const attachmentHtml = (msg.attachments || [])
            .map(a => this._renderAttachment(a))
            .join('');
        div.className = `chat-msg ${isCeo ? 'chat-msg-ceo' : 'chat-msg-agent'}`;
        div.innerHTML = `
            <div class="chat-msg-role">${this._escapeHtml(msg.role)}</div>
            <div class="chat-msg-text">${this._escapeHtml(msg.text)}</div>
            ${attachmentHtml
                ? `<div class="chat-msg-attachments">${attachmentHtml}</div>`
                : ''}
        `;
        this._messagesEl.appendChild(div);
    }

    _renderAttachment(attachment) {
        const raw = typeof attachment === 'string'
            ? attachment
            : (attachment?.url || attachment?.path || attachment?.file || '');
        const url = this._toAttachmentUrl(raw);
        const filename = this._attachmentName(raw);

        if (url && this._isImageUrl(url)) {
            const safeUrl = this._escapeHtml(url);
            const safeName = this._escapeHtml(filename);
            return `<a class="chat-msg-image-link" href="${safeUrl}" target="_blank" rel="noopener"><img class="chat-msg-image" src="${safeUrl}" alt="${safeName}" /></a>`;
        }
        if (url) {
            const safeUrl = this._escapeHtml(url);
            const safeName = this._escapeHtml(filename);
            return `<a class="chat-msg-attachment chat-msg-attachment-link" href="${safeUrl}" target="_blank" rel="noopener">${safeName}</a>`;
        }
        return `<span class="chat-msg-attachment">${this._escapeHtml(filename)}</span>`;
    }

    _attachmentName(pathOrUrl) {
        if (!pathOrUrl || typeof pathOrUrl !== 'string') return 'attachment';
        const clean = pathOrUrl.split('?')[0].split('#')[0];
        const idx = clean.lastIndexOf('/');
        if (idx >= 0 && idx < clean.length - 1) return clean.slice(idx + 1);
        return clean || 'attachment';
    }

    _toAttachmentUrl(pathOrUrl) {
        if (!pathOrUrl || typeof pathOrUrl !== 'string') return '';
        const raw = pathOrUrl.trim();
        if (!raw) return '';

        if (raw.startsWith('/api/') || raw.startsWith('http://') || raw.startsWith('https://')) {
            return raw;
        }
        if (raw.startsWith('data:image/')) {
            return raw;
        }

        // Convert absolute employee workspace path to API URL.
        const m = raw.match(/\/\.onemancompany\/company\/human_resource\/employees\/([^/]+)\/workspace\/(.+)$/);
        if (m) {
            const employeeId = encodeURIComponent(m[1]);
            const relPath = m[2].split('/').map(seg => encodeURIComponent(seg)).join('/');
            return `/api/employee/${employeeId}/workspace/files/${relPath}`;
        }
        return '';
    }

    _isImageUrl(url) {
        if (!url) return false;
        if (url.startsWith('data:image/')) return true;
        return /\.(png|jpe?g|gif|webp|svg)([?#].*)?$/i.test(url);
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
    onClear(cb) { this._onClearCb = cb; }
    onClose(cb) { this._onCloseCb = cb; }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}
