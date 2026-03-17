/**
 * app.js — WebSocket client, CEO console, and activity log controller
 */

class AppController {
  constructor() {
    this.ws = null;
    this.reconnectDelay = 1000;
    this.viewingRoomId = null;
    this.viewingEmployeeId = null;
    // Inquiry session state
    this._inquirySessionId = null;
    this._inquiryRoomId = null;
    // Dashboard cost auto-refresh timer
    this._dashboardCostTimer = null;
    // Input history (up/down arrow)
    this._inputHistory = JSON.parse(localStorage.getItem('ceo_input_history') || '[]');
    this._historyIndex = -1;
    this._historyDraft = '';
    // Task attachment files
    this._taskPendingFiles = [];
    // Board view: track which project's plugin tab is being viewed
    this._viewingBoardProjectId = null;
    // Initialize plugin system before connecting
    window.pluginLoader.init().then(() => {
      this.connect();
      this.bindUI();
    });
    this.bindCollapsibles();
    this._initPanelDividers();
  }

  // ===== WebSocket =====
  connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws`;

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      const statusEl = document.getElementById('connection-status');
      statusEl.textContent = '● ONLINE';
      statusEl.classList.add('online');
      // Hide reconnecting overlay
      document.getElementById('reconnecting-overlay').classList.add('hidden');
      // Clear restart banner after successful reconnect (server restarted)
      const banner = document.getElementById('code-update-banner');
      if (banner) {
        banner.classList.add('hidden');
        const applyBtn = document.getElementById('code-update-apply-btn');
        if (applyBtn) { applyBtn.textContent = 'Apply'; applyBtn.disabled = false; }
      }
      this.bootstrap();
    };

    this.ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        this.handleMessage(msg);
      } catch (e) {
        console.error('WS parse error:', e);
      }
    };

    this.ws.onclose = () => {
      const statusEl = document.getElementById('connection-status');
      statusEl.textContent = '● OFFLINE';
      statusEl.classList.remove('online');
      // Show reconnecting overlay
      document.getElementById('reconnecting-overlay').classList.remove('hidden');
      setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 10000);
    };

    this.ws.onerror = () => {};
  }

  async bootstrap() {
    try {
      const [employees, tasks, rooms, tools, activityLog, stateData] = await Promise.all([
        fetch('/api/employees').then(r => r.json()),
        fetch('/api/task-queue').then(r => r.json()),
        fetch('/api/rooms').then(r => r.json()),
        fetch('/api/tools').then(r => r.json()),
        fetch('/api/activity-log').then(r => r.json()),
        fetch('/api/state').then(r => r.json()),
      ]);
      this.updateRoster(employees);
      this.updateTaskPanel(tasks);
      this.updateOneononeDropdown(employees);
      this.updateProjectsPanel();
      if (window.officeRenderer) {
        window.officeRenderer.updateState({
          employees, meeting_rooms: rooms, tools,
          office_layout: stateData.office_layout,
        });
      }
      // Update counters
      document.getElementById('employee-count').textContent = `👥 ${employees.length}`;
      document.getElementById('tool-count').textContent = `🔧 ${tools.length}`;
      const freeRooms = rooms.filter(r => !r.is_booked).length;
      document.getElementById('room-count').textContent = `🏢 ${freeRooms}/${rooms.length}`;
      // Refresh meeting modal if open
      if (this.viewingRoomId) {
        const room = rooms.find(r => r.id === this.viewingRoomId);
        if (room) this._refreshMeetingModalStatus(room);
      }
      this._refreshCeoInbox();
      // Restore onboarding progress modal if there's an active onboarding
      this._restoreOnboardingProgress();
    } catch (e) {
      console.error('Bootstrap failed:', e);
    }
  }

  async _restoreOnboardingProgress() {
    try {
      const data = await fetch('/api/onboarding/status').then(r => r.json());
      const batches = data.batches || {};
      if (Object.keys(batches).length === 0) return;

      // Restore modal for each active batch
      for (const [batchId, batch] of Object.entries(batches)) {
        const items = batch.items || {};
        if (Object.keys(items).length === 0) continue;

        // Build selections array to pass to _showOnboardingProgress
        const selections = Object.entries(items).map(([cid, info]) => ({
          candidate_id: cid, role: info.role || '', name: info.name || cid,
        }));

        // Show the modal with all candidates
        this._onboardingBatchId = batchId;
        this._showOnboardingProgress(selections);

        // Replay each candidate's current step
        for (const [cid, info] of Object.entries(items)) {
          this._handleOnboardingProgress({
            candidate_id: cid,
            step: info.step,
            message: info.message || '',
            name: info.name,
          });
        }
      }
    } catch (e) {
      console.warn('Failed to restore onboarding progress:', e);
    }
  }

  async _fetchAndRenderOfficeLayout() {
    const stateData = await fetch('/api/state').then(r => r.json());
    if (window.officeRenderer && stateData.office_layout) {
      window.officeRenderer.updateState({ office_layout: stateData.office_layout });
    }
  }

  async _fetchAndRenderRoster() {
    const employees = await fetch('/api/employees').then(r => r.json());
    this.updateRoster(employees);
    this.updateOneononeDropdown(employees);
    if (window.officeRenderer) {
      window.officeRenderer.updateState({ employees });
    }
    document.getElementById('employee-count').textContent = `👥 ${employees.length}`;
  }

  async _fetchAndRenderTaskPanel() {
    const tasks = await fetch('/api/task-queue').then(r => r.json());
    this.updateTaskPanel(tasks);
  }

  async _fetchAndRenderRooms() {
    const rooms = await fetch('/api/rooms').then(r => r.json());
    if (window.officeRenderer) {
      window.officeRenderer.updateState({ meeting_rooms: rooms });
    }
    const freeRooms = rooms.filter(r => !r.is_booked).length;
    document.getElementById('room-count').textContent = `🏢 ${freeRooms}/${rooms.length}`;
    // Refresh meeting modal if open
    if (this.viewingRoomId) {
      const room = rooms.find(r => r.id === this.viewingRoomId);
      if (room) this._refreshMeetingModalStatus(room);
    }
  }

  async _fetchAndRenderTools() {
    const tools = await fetch('/api/tools').then(r => r.json());
    if (window.officeRenderer) {
      window.officeRenderer.updateState({ tools });
    }
    document.getElementById('tool-count').textContent = `🔧 ${tools.length}`;
  }

  handleMessage(msg) {
    // Handle tick-based state_changed
    if (msg.type === 'state_changed') {
      const c = msg.changed || [];
      if (c.includes('employees'))      this._fetchAndRenderRoster();
      if (c.includes('task_queue'))   this._fetchAndRenderTaskPanel();
      if (c.includes('rooms'))        this._fetchAndRenderRooms();
      if (c.includes('tools'))        this._fetchAndRenderTools();
      if (c.includes('office_layout')) this._fetchAndRenderOfficeLayout();
      // Refresh company culture if modal is open
      if (!document.getElementById('company-culture-modal').classList.contains('hidden')) {
        this._renderCompanyCulture();
      }
      // Refresh projects panel
      this.updateProjectsPanel();
      // Auto-refresh dashboard costs if modal is open (debounce 2s)
      if (!document.getElementById('dashboard-modal').classList.contains('hidden')) {
        clearTimeout(this._dashboardCostTimer);
        this._dashboardCostTimer = setTimeout(() => this._renderDashboard(), 2000);
      }
      return;
    }

    // Handle connected message — bootstrap from REST API
    if (msg.type === 'connected') {
      this.bootstrap();
      return;
    }

    // CEO inbox real-time updates
    if (msg.type === 'ceo_inbox_updated') {
      this._refreshCeoInbox();
      return;
    }
    if (msg.type === 'ceo_conversation') {
      if (this._currentConvNodeId === (msg.payload && msg.payload.node_id)) {
        this._appendConvMessage({
          sender: msg.payload.sender,
          text: msg.payload.text,
          timestamp: msg.payload.timestamp,
        });
      }
      return;
    }

    // Log the event
    const formatters = {
      'state_snapshot':     () => {
        const now = new Date().toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const el = document.getElementById('last-sync-time');
        if (el) el.textContent = `Sync ${now}`;
        return null;
      },
      'ceo_task_submitted': (p) => ({ text: `📋 Task: ${p.task}`, cls: 'ceo', agent: 'CEO' }),
      'agent_thinking':     (p) => ({ text: `💭 ${p.message}`, cls: (msg.agent || '').toLowerCase(), agent: msg.agent }),
      'agent_done':         (p) => ({ text: `✅ ${p.role} done: ${p.summary}`, cls: (p.role || '').toLowerCase(), agent: p.role }),
      'employee_hired':     (p) => ({ text: `🎉 New hire: ${p.name} (${p.role})`, cls: 'hr', agent: 'HR' }),
      'employee_fired':     (p) => ({ text: `🚪 Departure: ${p.name}${p.nickname ? '(' + p.nickname + ')' : ''} — ${p.reason || ''}`, cls: 'hr', agent: 'HR' }),
      'employee_rehired':   (p) => ({ text: `🔄 Rehired: ${p.name}${p.nickname ? '(' + p.nickname + ')' : ''} (${p.role})`, cls: 'hr', agent: 'CEO' }),
      'employee_reviewed':  (p) => ({ text: `📊 Quarterly review: ${p.id} — Score: ${p.score}`, cls: 'hr', agent: 'HR' }),
      'okr_updated':        (p) => ({ text: `🎯 OKRs updated for #${p.employee_id}`, cls: 'hr', agent: 'HR' }),
      'onboarding_started': (p) => ({ text: `📋 Onboarding started: ${p.name}`, cls: 'hr', agent: 'HR' }),
      'onboarding_completed': (p) => ({ text: `✅ Onboarding completed: ${p.name}`, cls: 'hr', agent: 'HR' }),
      'probation_review':   (p) => ({ text: `📋 Probation review: #${p.id} — ${p.passed ? 'Passed' : 'Failed'}`, cls: 'hr', agent: 'HR' }),
      'pip_started':        (p) => ({ text: `⚠️ PIP started for #${p.id}`, cls: 'hr', agent: 'HR' }),
      'pip_resolved':       (p) => ({ text: `✅ PIP resolved for #${p.id}`, cls: 'hr', agent: 'HR' }),
      'exit_interview_started': (p) => ({ text: `🚪 Exit interview: ${p.name}`, cls: 'hr', agent: 'HR' }),
      'exit_interview_completed': (p) => ({ text: `📄 Exit interview done: ${p.name}`, cls: 'hr', agent: 'HR' }),
      'tool_added':         (p) => ({ text: `🔧 New tool: ${p.name}`, cls: 'coo', agent: 'COO' }),
      'guidance_start':     (p) => ({ text: `📖 ${p.name} is in a 1-on-1 meeting...`, cls: 'guidance', agent: 'CEO' }),
      'guidance_noted':     (p) => ({ text: `🎓 ${p.name}: ${p.acknowledgment}`, cls: 'guidance', agent: p.name }),
      'guidance_end':       (p) => ({ text: `📖 ${p.name}'s 1-on-1 meeting concluded`, cls: 'guidance', agent: 'CEO' }),
      'meeting_booked':     (p) => {
        return { text: `🏢 Room booked: ${p.room_name || ''}`, cls: 'coo', agent: 'COO' };
      },
      'meeting_released':   (p) => {
        // Keep chat history for viewing after meeting ends
        return { text: `🏢 Room released: ${p.room_name || ''}`, cls: 'coo', agent: 'COO' };
      },
      'meeting_denied':     (p) => ({ text: `🚫 Room request denied: no rooms available`, cls: 'coo', agent: 'COO' }),
      'routine_phase':      (p) => ({ text: `🔄 ${p.phase}: ${p.message}`, cls: 'system', agent: 'ROUTINE' }),
      'meeting_report_ready': (p) => {
        // Legacy event — no longer enqueues for CEO review (EA handles approval)
        return { text: `📄 Meeting report ready (EA reviewed)`, cls: 'system', agent: 'EA' };
      },
      'meeting_report_complete': (p) => {
        return { text: `📄 Meeting report complete (EA approved)`, cls: 'system', agent: 'EA' };
      },
      'recurring_action_items': (p) => {
        const items = (p.items || []).map(i => `  - ${i}`).join('\n');
        return { text: `⚠️ ${p.message || 'Recurring issues'}:\n${items}`, cls: 'ceo', agent: 'EA' };
      },
      'meeting_chat':       (p) => {
        const roomId = p.room_id || '';
        // If this room is currently being viewed, append the message live
        if (this.viewingRoomId === roomId) {
          const chatEntry = {
            speaker: p.speaker,
            role: p.role,
            message: p.message,
            time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
          };
          this._appendChatMessage(chatEntry);
        }
        return { text: `💬 [${p.speaker}] ${(p.message || '').substring(0, 50)}`, cls: 'system', agent: 'MEETING' };
      },
      'workflow_updated':    (p) => ({ text: `📋 Workflow updated: ${p.name}`, cls: 'ceo', agent: 'CEO' }),
      'candidates_ready':   (p) => {
        this.showCandidateSelection(p);
        const totalCandidates = (p.roles || []).reduce((sum, r) => sum + (r.candidates || []).length, 0) || (p.candidates || []).length;
        return { text: `📋 HR screening done: ${totalCandidates} candidates in ${(p.roles || []).length || 1} role(s)`, cls: 'hr', agent: 'HR' };
      },
      'onboarding_progress': (p) => {
        this._handleOnboardingProgress(p);
        return null; // no log entry, modal handles it
      },
      'file_edit_proposed':  (p) => {
        return { text: `📝 File edit request: ${p.rel_path} — ${p.reason}`, cls: 'ceo', agent: p.proposed_by || 'AGENT' };
      },
      'file_edit_applied':   (p) => ({ text: `✅ File updated: ${p.rel_path}`, cls: 'ceo', agent: 'CEO' }),
      'file_edit_rejected':  (p) => ({ text: `❌ File edit rejected: ${p.rel_path}`, cls: 'ceo', agent: 'CEO' }),
      'hiring_request_ready': (p) => {
        return { text: `📋 COO auto-approved hiring: ${p.role} — ${p.reason} (hire_id: ${p.hire_id})`, cls: 'coo', agent: 'COO' };
      },
      'hiring_request_decided': (p) => {
        return { text: `${p.approved ? '✅' : '❌'} Hiring ${p.approved ? 'confirmed' : 'rejected'}: ${p.role}`, cls: 'ceo', agent: 'CEO' };
      },
      'inquiry_started':     (p) => {
        this._startInquiryMode(p);
        return { text: `🔍 Inquiry started with ${p.agent_role} in meeting room`, cls: 'ceo', agent: 'CEO' };
      },
      'inquiry_ended':       (p) => {
        this._endInquiryMode();
        return { text: `🔍 Inquiry ended`, cls: 'ceo', agent: 'CEO' };
      },
      'open_popup':          (p) => {
        this.openPopup(p);
        return { text: `📢 ${p.title || 'Notification'}`, cls: 'system', agent: p.agent || 'SYSTEM' };
      },
      'request_credentials': (p) => {
        this.openPopup({ ...p, type: 'credentials' });
        return { text: `🔑 ${p.title || 'Credentials required'}`, cls: 'system', agent: p.agent || 'SYSTEM' };
      },
      'agent_task_update':   (p) => {
        // Refresh task board if viewing this employee
        if (this.viewingEmployeeId && p.employee_id === this.viewingEmployeeId) {
          this._fetchTaskBoard(this.viewingEmployeeId);
        }
        return { text: `📋 ${p.employee_id} task: ${p.status || 'updated'}`, cls: 'system', agent: 'AGENT' };
      },
      'dispatch_status_change': (p) => {
        // Refresh the active plugin tab if viewing that project
        if (this._viewingBoardProjectId && p.project_id) {
          const activeTab = document.querySelector('.project-tab.active');
          if (activeTab && activeTab.dataset.tab && activeTab.dataset.tab.startsWith('plugin-')) {
            const pluginId = activeTab.dataset.tab.replace('plugin-', '');
            const container = document.querySelector(`.project-tab-content[data-tab="${activeTab.dataset.tab}"]`);
            if (container) {
              window.pluginLoader.render(pluginId, this._viewingBoardProjectId, container, {escHtml: this._escHtml, projectId: this._viewingBoardProjectId});
            }
          }
        }
        return null;
      },
      'tree_update': (p) => {
        if (this._treeRenderer && this._currentTreeProjectId === p.project_id) {
          if (p.event_type === 'node_added') {
            this._treeRenderer.addNode(p.node_id, p.data);
          } else {
            this._treeRenderer.updateNode(p.node_id, p.data);
          }
        }
        return null;
      },
      'ceo_report': (p) => {
        const icon = p.action_required ? '🚨' : '📊';
        return { text: `${icon} CEO Report: ${p.subject}`, cls: 'ceo', agent: 'SYSTEM' };
      },
      'review_reminder': (p) => {
        const nodes = p.overdue_nodes || [];
        if (!nodes.length) return null;
        const summaries = nodes.map(n => {
          const mins = Math.round(n.waiting_seconds / 60);
          return `${n.employee_id}: ${(n.description || '').substring(0, 60)} (${mins}m)`;
        });
        return { text: `⏰ ${nodes.length} task(s) awaiting review:\n${summaries.join('\n')}`, cls: 'ceo', agent: 'SYSTEM' };
      },
      'code_update_available': (p) => {
        this._showCodeUpdateBanner(p.count, p.changed_files);
        return null;
      },
      'frontend_update_available': (p) => {
        console.log('[hot-reload] Frontend files changed, reloading...', p.changed_files);
        setTimeout(() => location.reload(), 300);
        return null;
      },
      'backend_restart_scheduled': (p) => {
        if (p.immediate) {
          this._showRestartBanner('Restarting server...');
        } else {
          this._showRestartBanner('Code changed — restart after tasks complete');
        }
        return null;
      },
      'agent_log':           (p) => {
        // Append log entry live if viewing this employee
        if (this.viewingEmployeeId && p.employee_id === this.viewingEmployeeId) {
          const container = document.getElementById('emp-detail-logs');
          if (container && !container.querySelector('.empty-hint')) {
            const ts = (p.timestamp || '').substring(11, 19);
            const cls = p.log_type || 'info';
            const raw = p.content || '';
            const truncated = raw.length > 200;
            let logHtml = `<div class="emp-log-entry ${cls}"><span class="log-time">${ts}</span> <span class="log-content">${this._escHtml(truncated ? raw.substring(0, 200) : raw)}</span>`;
            if (truncated) {
              logHtml += `<span class="log-full" style="display:none">${this._escHtml(raw)}</span>`;
              logHtml += `<span class="log-expand" onclick="this.parentElement.querySelector('.log-content').style.display='none';this.parentElement.querySelector('.log-full').style.display='inline';this.style.display='none';this.nextElementSibling.style.display='inline'">...more</span>`;
              logHtml += `<span class="log-collapse" style="display:none" onclick="this.parentElement.querySelector('.log-content').style.display='inline';this.parentElement.querySelector('.log-full').style.display='none';this.style.display='none';this.previousElementSibling.style.display='inline'">less</span>`;
            }
            logHtml += '</div>';
            container.insertAdjacentHTML('beforeend', logHtml);
            container.scrollTop = container.scrollHeight;
          }
        }
        return null;  // don't spam the activity log
      },
    };

    const formatter = formatters[msg.type];
    if (formatter) {
      const result = formatter(msg.payload || {});
      if (result) {
        const { text, cls, agent } = result;
        this.logEntry(agent || 'SYSTEM', text, cls);
      }
    }
  }

  // ===== Collapsible panels =====
  bindCollapsibles() {
    document.querySelectorAll('.collapsible-header').forEach(header => {
      header.addEventListener('click', () => {
        const targetId = header.getAttribute('data-target');
        const body = document.getElementById(targetId);
        if (!body) return;

        const isCollapsed = body.classList.contains('collapsed');
        if (isCollapsed) {
          body.classList.remove('collapsed');
          header.classList.remove('collapsed');
        } else {
          body.classList.add('collapsed');
          header.classList.add('collapsed');
        }
      });
    });
  }

  // ===== Panel Divider Drag =====
  _initPanelDividers() {
    const app = document.getElementById('app');
    const dividerL = document.getElementById('divider-left');
    const dividerR = document.getElementById('divider-right');

    // Restore saved widths
    const savedLeft = localStorage.getItem('panel_left_w');
    const savedRight = localStorage.getItem('panel_right_w');
    if (savedLeft || savedRight) {
      const lw = parseInt(savedLeft) || 240;
      const rw = parseInt(savedRight) || 340;
      app.style.gridTemplateColumns = `${lw}px 6px 1fr 6px ${rw}px`;
    }

    const startDrag = (divider, side) => {
      return (eDown) => {
        eDown.preventDefault();
        divider.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        const startX = eDown.clientX;
        const cols = getComputedStyle(app).gridTemplateColumns.split(/\s+/);
        const leftW = parseFloat(cols[0]);
        const rightW = parseFloat(cols[4]);

        const onMove = (eMove) => {
          const dx = eMove.clientX - startX;
          if (side === 'left') {
            const newW = Math.max(120, Math.min(leftW + dx, window.innerWidth * 0.4));
            app.style.gridTemplateColumns = `${newW}px 6px 1fr 6px ${rightW}px`;
          } else {
            const newW = Math.max(200, Math.min(rightW - dx, window.innerWidth * 0.5));
            app.style.gridTemplateColumns = `${leftW}px 6px 1fr 6px ${newW}px`;
          }
        };

        const onUp = () => {
          divider.classList.remove('dragging');
          document.body.style.cursor = '';
          document.body.style.userSelect = '';
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          // Save widths
          const finalCols = getComputedStyle(app).gridTemplateColumns.split(/\s+/);
          localStorage.setItem('panel_left_w', parseInt(finalCols[0]));
          localStorage.setItem('panel_right_w', parseInt(finalCols[4]));
          // Resize canvas
          if (window.officeRenderer) window.officeRenderer._resizeCanvas();
        };

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      };
    };

    dividerL.addEventListener('mousedown', startDrag(dividerL, 'left'));
    dividerR.addEventListener('mousedown', startDrag(dividerR, 'right'));
  }

  // ===== Task Panel =====
  updateTaskPanel(tasks) {
    if (tasks) {
      this._renderTaskPanel(tasks);
      return;
    }
    fetch('/api/task-queue')
      .then(r => r.json())
      .then(t => this._renderTaskPanel(t))
      .catch(() => {});
  }

  _renderTaskPanel(tasks) {
    const panel = document.getElementById('task-panel-list');
    if (!tasks || tasks.length === 0) {
      panel.innerHTML = '<div class="task-empty">No active tasks</div>';
      return;
    }
    panel.innerHTML = '';
    for (const t of tasks) {
      const card = document.createElement('div');
      const isTerminal = ['completed', 'finished', 'failed', 'cancelled'].includes(t.status);
      card.className = `task-card ${t.status}`;

      // Status icon
      const statusMap = {
        pending: ['⏳', 'Pending'],
        processing: ['🔄', 'Processing'],
        completed: ['✅', 'Completed'],
        finished: ['🏁', 'Finished'],
        failed: ['❌', 'Failed'],
        cancelled: ['🚫', 'Cancelled'],
        holding: ['⏸️', 'Holding'],
      };
      const [icon, label] = statusMap[t.status] || ['⏳', t.status];

      // Owner — resolve from tree root node for better display
      let ownerLabel = '';
      if (!isTerminal) {
        const ownerEmp = (window.officeRenderer?.state?.employees || []).find(e => e.id === t.current_owner);
        ownerLabel = ownerEmp ? (ownerEmp.nickname || ownerEmp.name) : '';
        if (t.current_owner === 'pending' || !ownerLabel) {
          ownerLabel = t.routed_to || '';
        }
      } else if (t.tree) {
        const rootNode = t.tree.active_nodes?.[0];
        if (rootNode) {
          const rootEmp = (window.officeRenderer?.state?.employees || []).find(e => e.id === rootNode.employee_id);
          ownerLabel = rootEmp ? (rootEmp.nickname || rootEmp.name) : rootNode.employee_id;
        }
      }

      // Task description
      const taskText = this._escHtml(t.task.substring(0, 80)) + (t.task.length > 80 ? '...' : '');

      // Result from tree root node for completed tasks
      let resultHtml = '';
      if (isTerminal && t.tree) {
        const rootNode = t.tree.root_result;
        if (rootNode) {
          const firstLine = rootNode.split('\n').find(l => l.trim()) || '';
          const summary = firstLine.substring(0, 120);
          resultHtml = `<div class="task-card-result">${this._escHtml(summary)}${firstLine.length > 120 ? '...' : ''}</div>`;
        }
      }

      // Tree progress — only for active multi-node trees
      let treeHtml = '';
      if (!isTerminal && t.tree) {
        const tr = t.tree;
        const childCount = tr.total - 1;
        if (childCount > 0) {
          const done = tr.terminal;
          const pct = Math.round((done / childCount) * 100);
          treeHtml = `<div class="task-card-tree">
            <div class="task-tree-progress"><div class="task-tree-bar" style="width:${pct}%"></div></div>
            <span class="task-tree-label">${done}/${childCount} subtasks</span>
          </div>`;
        }
        if (tr.active_nodes && tr.active_nodes.length > 0) {
          const nodesHtml = tr.active_nodes.slice(0, 3).map(n => {
            const nEmp = (window.officeRenderer?.state?.employees || []).find(e => e.id === n.employee_id);
            const nName = nEmp ? (nEmp.nickname || nEmp.name) : n.employee_id;
            const nColor = n.status === 'processing' ? 'var(--pixel-green)' : 'var(--text-dim)';
            return `<div class="task-tree-node" style="border-left-color:${nColor};"><span class="task-tree-node-name">${this._escHtml(nName)}</span> <span class="task-tree-node-desc">${this._escHtml(n.description)}</span></div>`;
          }).join('');
          const extra = tr.active_nodes.length > 3 ? `<div style="color:var(--text-dim);font-size:5px;">+${tr.active_nodes.length - 3} more</div>` : '';
          treeHtml += `<div class="task-tree-nodes">${nodesHtml}${extra}</div>`;
        }
      }

      // Completed time
      let timeHtml = '';
      if (isTerminal && t.completed_at) {
        const completedTime = t.completed_at.substring(11, 19);
        timeHtml = `<span class="task-card-time">${completedTime}</span>`;
      }

      // Cancel button for active tasks
      const cancelBtn = !isTerminal && t.project_id
        ? `<button class="task-cancel-btn" data-project-id="${this._escHtml(t.project_id)}" title="取消任务">✕</button>`
        : '';

      card.innerHTML = `
        <div class="task-card-header">
          <span class="task-card-status">${icon} ${label}</span>
          <span class="task-card-meta">${ownerLabel ? this._escHtml(ownerLabel) : ''}${timeHtml ? (ownerLabel ? ' · ' : '') + timeHtml : ''}${cancelBtn}</span>
        </div>
        <div class="task-card-text">${taskText}</div>
        ${resultHtml}
        ${treeHtml}
      `;

      // Bind cancel button
      const btn = card.querySelector('.task-cancel-btn');
      if (btn) {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          this._cancelTask(btn.dataset.projectId);
        });
      }

      if (t.project_id && !t.project_id.startsWith('_auto_')) {
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => this._openTaskInBoard(t.project_id));
      }
      panel.appendChild(card);
    }
  }

  async _cancelTask(projectId) {
    if (!confirm('Are you sure you want to cancel this task?')) return;
    try {
      const resp = await fetch(`/api/task/${projectId}/abort`, { method: 'POST' });
      const data = await resp.json();
      if (data.status === 'ok') {
        this.updateTaskPanel();
      }
    } catch (e) {
      console.error('Cancel task failed:', e);
    }
  }

  // ===== Roster =====
  updateRoster(employees) {
    const roster = document.getElementById('roster-list');
    roster.innerHTML = '';

    // Read current filter values
    const filterRole = document.getElementById('roster-filter-role')?.value || '';
    const filterDept = document.getElementById('roster-filter-dept')?.value || '';
    const filterLevel = document.getElementById('roster-filter-level')?.value || '';

    // Populate filter dropdowns with unique values (only once per update)
    this._populateRosterFilters(employees);

    // CEO card (always first, not subject to filters)
    const ceoCard = document.createElement('div');
    ceoCard.className = 'roster-card';
    ceoCard.innerHTML = `
      <img class="roster-avatar" src="/api/employees/00001/avatar"
           onerror="this.style.display='none'" />
      <div class="roster-info">
        <div class="roster-name" style="color: #ffd700;">👑 CEO (You)</div>
        <div class="roster-role"><span class="roster-empnum">#00001</span> Chief Executive Officer</div>
      </div>
    `;
    roster.appendChild(ceoCard);

    // Sort by employee_number (ascending)
    const sorted = [...employees].sort((a, b) => {
      const na = a.employee_number || '99999';
      const nb = b.employee_number || '99999';
      return na.localeCompare(nb);
    });

    // Apply filters
    const filtered = sorted.filter(emp => {
      if (filterRole && emp.role !== filterRole) return false;
      if (filterDept && emp.department !== filterDept) return false;
      if (filterLevel && String(emp.level) !== filterLevel) return false;
      return true;
    });

    for (const emp of filtered) {
      const card = document.createElement('div');
      card.className = 'roster-card';
      const roleIcon = emp.role === 'HR' ? '💼' : emp.role === 'COO' ? '⚙️' : '🤖';
      const listeningBadge = emp.is_listening
        ? '<span class="roster-listening">📖 In meeting...</span>'
        : '';
      const remoteBadge = emp.remote
        ? '<span class="roster-remote">🌐 Remote</span>'
        : '';
      const probationBadge = emp.probation
        ? '<span class="roster-badge probation">PROBATION</span>'
        : '';
      const pipBadge = emp.pip
        ? '<span class="roster-badge pip">PIP</span>'
        : '';
      const guidanceCount = (emp.guidance_notes || []).length;
      const guidanceBadge = guidanceCount > 0
        ? `<span style="color: #aa66ff; font-size: 6px;"> [${guidanceCount} notes]</span>`
        : '';
      const nn = emp.nickname ? `(${emp.nickname})` : '';
      const empNum = emp.employee_number ? `#${emp.employee_number}` : '';
      const title = emp.title || emp.role;
      // Latest quarter score
      const hist = emp.performance_history || [];
      const latestScore = hist.length > 0 ? hist[hist.length - 1].score : '-';
      const scoreClass = latestScore === 3.75 ? ' high' : latestScore === 3.25 ? ' low' : '';
      const qTasks = emp.current_quarter_tasks || 0;
      const levelPrefix = emp.level ? `L${emp.level} ` : '';
      card.innerHTML = `
        <img class="roster-avatar" src="/api/employees/${emp.id}/avatar"
             onerror="this.style.display='none'" />
        <div class="roster-info">
          <div class="roster-name">${roleIcon} ${levelPrefix}${emp.name} ${nn}${guidanceBadge}${remoteBadge}${probationBadge}${pipBadge}</div>
          <div class="roster-role"><span class="roster-empnum">${empNum}</span> ${title}</div>
          <div class="roster-quarter">${(emp.skills || []).slice(0, 3).join(', ') || `Q Tasks: ${qTasks}/3`}</div>
          ${listeningBadge}
        </div>
        <div class="roster-score${scoreClass}">${latestScore}</div>
      `;
      // Click on roster card also opens employee detail
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => this.openEmployeeDetail(emp));
      roster.appendChild(card);
    }
  }

  _populateRosterFilters(employees) {
    const roleSelect = document.getElementById('roster-filter-role');
    const deptSelect = document.getElementById('roster-filter-dept');
    const levelSelect = document.getElementById('roster-filter-level');
    if (!roleSelect || !deptSelect || !levelSelect) return;

    const curRole = roleSelect.value;
    const curDept = deptSelect.value;
    const curLevel = levelSelect.value;

    const roles = [...new Set(employees.map(e => e.role).filter(Boolean))].sort();
    const depts = [...new Set(employees.map(e => e.department).filter(Boolean))].sort();
    const levels = [...new Set(employees.map(e => e.level))].sort((a, b) => a - b);

    const LEVEL_NAMES = {1: 'Junior', 2: 'Mid', 3: 'Senior', 4: 'Founding', 5: 'CEO'};

    roleSelect.innerHTML = '<option value="">All Roles</option>' +
      roles.map(r => `<option value="${r}"${r === curRole ? ' selected' : ''}>${r}</option>`).join('');
    deptSelect.innerHTML = '<option value="">All Departments</option>' +
      depts.map(d => `<option value="${d}"${d === curDept ? ' selected' : ''}>${d}</option>`).join('');
    levelSelect.innerHTML = '<option value="">All Levels</option>' +
      levels.map(l => `<option value="${l}"${String(l) === curLevel ? ' selected' : ''}>${LEVEL_NAMES[l] || 'Lv.' + l}</option>`).join('');
  }

  _onRosterFilterChange() {
    this._fetchAndRenderRoster();
  }

  // ===== Activity Log =====
  logEntry(agent, message, cssClass = 'system') {
    const log = document.getElementById('log-entries');
    const entry = document.createElement('div');
    entry.className = `log-entry ${cssClass}`;
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    entry.innerHTML = `<span class="timestamp">[${time}]</span> <span class="agent">${agent}:</span> ${message}`;
    log.prepend(entry);
    // Keep log manageable
    while (log.children.length > 80) log.removeChild(log.lastChild);
  }

  // ===== UI Bindings =====
  bindUI() {
    const submitBtn = document.getElementById('submit-btn');
    const hrBtn = document.getElementById('hr-review-btn');
    const input = document.getElementById('task-input');

    submitBtn.addEventListener('click', () => this.submitTask());

    // Paste image into task input
    input.addEventListener('paste', (e) => {
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      const files = [];
      for (const item of items) {
        if (item.kind === 'file') {
          files.push(item.getAsFile());
        }
      }
      if (files.length) {
        e.preventDefault();
        this._handleTaskFileSelect(files);
      }
    });

    input.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        this.submitTask();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (this._inputHistory.length === 0) return;
        if (this._historyIndex === -1) this._historyDraft = input.value;
        if (this._historyIndex < this._inputHistory.length - 1) {
          this._historyIndex++;
          input.value = this._inputHistory[this._inputHistory.length - 1 - this._historyIndex];
        }
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (this._historyIndex <= 0) {
          this._historyIndex = -1;
          input.value = this._historyDraft;
        } else {
          this._historyIndex--;
          input.value = this._inputHistory[this._inputHistory.length - 1 - this._historyIndex];
        }
      }
    });

    // Load active projects into selector on startup
    this.loadActiveProjects();

    hrBtn.addEventListener('click', () => {
      hrBtn.disabled = true;
      this.logEntry('CEO', '🔄 Triggering quarterly review...', 'ceo');
      fetch('/api/hr/review', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
          setTimeout(() => { hrBtn.disabled = false; }, 5000);
        })
        .catch(err => {
          this.logEntry('SYSTEM', `Error: ${err.message}`, 'system');
          hrBtn.disabled = false;
        });
    });

    // Code update banner bindings
    document.getElementById('code-update-apply-btn').addEventListener('click', () => {
      const btn = document.getElementById('code-update-apply-btn');
      btn.disabled = true;
      btn.textContent = 'Applying...';
      fetch('/api/admin/apply-code-update', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'deferred') {
            btn.textContent = 'Waiting for tasks...';
            // Will auto-restart when tasks complete; reconnect logic handles the rest
          }
        })
        .catch(() => {});
    });
    document.getElementById('code-update-dismiss-btn').addEventListener('click', () => {
      document.getElementById('code-update-banner').classList.add('hidden');
    });

    // Roster filter bindings
    ['roster-filter-role', 'roster-filter-dept', 'roster-filter-level'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => this._onRosterFilterChange());
    });

    // CEO conversation dialog: Enter key to send
    const convInput = document.getElementById('ceo-conv-input');
    if (convInput) {
      convInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          app._sendCeoMessage();
        }
      });
    }

    // 1-on-1 meeting modal bindings
    const oneononeModal = document.getElementById('oneonone-modal');
    document.getElementById('guidance-toolbar-btn').addEventListener('click', () => {
      oneononeModal.classList.remove('hidden');
    });
    document.getElementById('oneonone-close-btn').addEventListener('click', () => this.closeOneononeModal());
    document.getElementById('oneonone-close-btn2').addEventListener('click', () => this.closeOneononeModal());
    oneononeModal.addEventListener('click', (e) => {
      if (e.target === oneononeModal) this.closeOneononeModal();
    });
    // Meeting type selector — show/hide employee dropdown
    document.getElementById('meeting-type-select').addEventListener('change', () => {
      const type = document.getElementById('meeting-type-select').value;
      const empRow = document.getElementById('oneonone-employee-select-row');
      const startBtn = document.getElementById('oneonone-start-btn');
      if (type === 'oneonone') {
        empRow.style.display = '';
        startBtn.disabled = !document.getElementById('oneonone-target').value;
      } else {
        empRow.style.display = 'none';
        startBtn.disabled = false;
      }
    });
    document.getElementById('oneonone-target').addEventListener('change', () => {
      const type = document.getElementById('meeting-type-select').value;
      if (type === 'oneonone') {
        document.getElementById('oneonone-start-btn').disabled = !document.getElementById('oneonone-target').value;
      }
    });
    document.getElementById('oneonone-start-btn').addEventListener('click', () => this.startOneonone());
    document.getElementById('oneonone-send-btn').addEventListener('click', () => this.sendOneononeMessage());
    this._oneononeInputHistory = [];   // CEO messages sent this session
    this._oneononeHistoryIdx = -1;     // -1 = not browsing history
    this._oneononeSavedDraft = '';      // save current draft when browsing
    document.getElementById('oneonone-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendOneononeMessage();
      } else if (e.key === 'ArrowUp' && !e.shiftKey) {
        const ta = e.target;
        // Only intercept if cursor is at the start (no multiline navigation needed)
        if (ta.selectionStart === 0 && this._oneononeInputHistory.length > 0) {
          e.preventDefault();
          if (this._oneononeHistoryIdx === -1) {
            this._oneononeSavedDraft = ta.value;
            this._oneononeHistoryIdx = this._oneononeInputHistory.length - 1;
          } else if (this._oneononeHistoryIdx > 0) {
            this._oneononeHistoryIdx--;
          }
          ta.value = this._oneononeInputHistory[this._oneononeHistoryIdx];
        }
      } else if (e.key === 'ArrowDown' && !e.shiftKey) {
        const ta = e.target;
        if (this._oneononeHistoryIdx !== -1) {
          e.preventDefault();
          if (this._oneononeHistoryIdx < this._oneononeInputHistory.length - 1) {
            this._oneononeHistoryIdx++;
            ta.value = this._oneononeInputHistory[this._oneononeHistoryIdx];
          } else {
            // Back to draft
            this._oneononeHistoryIdx = -1;
            ta.value = this._oneononeSavedDraft;
          }
        }
      }
    });
    document.getElementById('oneonone-end-btn').addEventListener('click', () => this.endOneononeMeeting());

    // Meeting room modal bindings
    document.getElementById('meeting-close-btn').addEventListener('click', () => this.closeMeetingRoom());
    document.getElementById('meeting-modal').addEventListener('click', (e) => {
      if (e.target.id === 'meeting-modal') this.closeMeetingRoom();
    });

    // Inquiry chat bindings (inside meeting modal)
    document.getElementById('meeting-inquiry-send-btn').addEventListener('click', () => this._sendInquiryMessage());
    document.getElementById('meeting-inquiry-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._sendInquiryMessage();
      }
    });
    document.getElementById('meeting-inquiry-end-btn').addEventListener('click', () => this._endInquirySession());

    // Employee detail modal bindings
    document.getElementById('emp-avatar-upload-input').addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file || !this.viewingEmployeeId) return;
      const reader = new FileReader();
      reader.onload = async () => {
        const resp = await fetch(`/api/employees/${this.viewingEmployeeId}/avatar`, {
          method: 'POST',
          body: new Uint8Array(reader.result),
          headers: { 'Content-Type': 'application/octet-stream' },
        });
        if (resp.ok) {
          const avatarImg = document.getElementById('emp-detail-avatar');
          avatarImg.src = `/api/employees/${this.viewingEmployeeId}/avatar?t=${Date.now()}`;
          avatarImg.style.display = '';
        }
      };
      reader.readAsArrayBuffer(file);
      e.target.value = '';
    });
    document.getElementById('employee-close-btn').addEventListener('click', () => this.closeEmployeeDetail());
    document.getElementById('employee-modal').addEventListener('click', (e) => {
      if (e.target.id === 'employee-modal') this.closeEmployeeDetail();
    });
    // Listen for OAuth popup completion — callback page sends postMessage('oauth_done')
    window.addEventListener('message', (e) => {
      if (e.data === 'oauth_done' && this.viewingEmployeeId) {
        this._loadModelOrApiKeySection(this.viewingEmployeeId);
        this.logEntry('SYSTEM', 'OAuth login completed! Employee is now authenticated.', 'system');
      }
    });

    // Reload data button
    document.getElementById('reload-toolbar-btn').addEventListener('click', () => this.adminReload());

    // Operations dashboard modal bindings
    document.getElementById('dashboard-toolbar-btn').addEventListener('click', () => this.openDashboard());
    document.getElementById('dashboard-close-btn').addEventListener('click', () => this.closeDashboard());
    document.getElementById('dashboard-modal').addEventListener('click', (e) => {
      if (e.target.id === 'dashboard-modal') this.closeDashboard();
    });

    // Candidate selection modal bindings
    document.getElementById('candidate-close-btn').addEventListener('click', () => this.closeCandidateModal());
    document.getElementById('candidate-modal').addEventListener('click', (e) => {
      if (e.target.id === 'candidate-modal') this.closeCandidateModal();
    });
    document.getElementById('candidate-batch-hire-btn').addEventListener('click', () => this.batchHireCandidates());
    document.getElementById('onboarding-done-btn').addEventListener('click', () => {
      const toast = document.getElementById('onboarding-progress-modal');
      toast.classList.add('hidden');
      this._onboardingItems = null;
      document.getElementById('onboarding-progress-list').innerHTML = '';
      document.getElementById('onboarding-done-btn').classList.add('hidden');
      // Tell backend to clear completed batches so they don't re-appear on refresh
      if (this._onboardingBatchId) {
        fetch('/api/onboarding/dismiss', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ batch_id: this._onboardingBatchId }),
        }).catch(err => console.warn('Failed to dismiss onboarding batch:', err));
      }
      this._onboardingBatchId = null;
    });
    document.getElementById('onboarding-toggle-btn').addEventListener('click', () => {
      const toast = document.getElementById('onboarding-progress-modal');
      toast.classList.toggle('collapsed');
      const btn = document.getElementById('onboarding-toggle-btn');
      btn.textContent = toast.classList.contains('collapsed') ? '\u25B6' : '\u25BC';
    });

    // Talent pool modal bindings
    document.getElementById('talent-pool-close-btn').addEventListener('click', () => this.closeTalentPool());
    document.getElementById('talent-pool-modal').addEventListener('click', (e) => {
      if (e.target.id === 'talent-pool-modal') this.closeTalentPool();
    });

    // Interview chatbot modal bindings
    document.getElementById('interview-close-btn').addEventListener('click', () => this.closeInterviewModal());
    document.getElementById('interview-modal').addEventListener('click', (e) => {
      if (e.target.id === 'interview-modal') this.closeInterviewModal();
    });
    document.getElementById('interview-back-btn').addEventListener('click', () => this.closeInterviewModal());
    document.getElementById('interview-ask-btn').addEventListener('click', () => this.askInterviewQuestion());
    document.getElementById('interview-question').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.askInterviewQuestion();
      }
    });
    document.getElementById('interview-hire-btn').addEventListener('click', () => {
      if (this._interviewingCandidate) {
        this.hireCandidateFromInterview();
      }
    });

    // Company culture modal bindings
    document.getElementById('company-culture-toolbar-btn').addEventListener('click', () => this.openCompanyCulture());
    document.getElementById('company-culture-close-btn').addEventListener('click', () => this.closeCompanyCulture());
    document.getElementById('company-culture-modal').addEventListener('click', (e) => {
      if (e.target.id === 'company-culture-modal') this.closeCompanyCulture();
    });
    document.getElementById('company-culture-add-btn').addEventListener('click', () => this.addCultureItem());
    document.getElementById('company-culture-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.addCultureItem(); }
    });

    // Company direction modal bindings
    document.getElementById('company-direction-toolbar-btn').addEventListener('click', () => this.openCompanyDirection());
    document.getElementById('company-direction-close-btn').addEventListener('click', () => this.closeCompanyDirection());
    document.getElementById('company-direction-modal').addEventListener('click', (e) => {
      if (e.target.id === 'company-direction-modal') this.closeCompanyDirection();
    });
    document.getElementById('company-direction-save-btn').addEventListener('click', () => this.saveCompanyDirection());

    // 1-on-1 file upload
    document.getElementById('oneonone-file-input').addEventListener('change', (e) => {
      this._handleOneononeFileSelect(e.target.files);
      e.target.value = '';
    });

    // CEO task file upload
    document.getElementById('task-file-input').addEventListener('change', (e) => {
      this._handleTaskFileSelect(e.target.files);
      e.target.value = '';
    });

    // Abort all tasks (panic button)
    document.getElementById('abort-all-toolbar-btn')?.addEventListener('click', async () => {
        if (!confirm('Are you sure you want to stop all tasks for all employees?\nThis will cancel ALL running tasks for ALL employees.')) return;
        try {
            const resp = await fetch('/api/abort-all', { method: 'POST' });
            const data = await resp.json();
            if (data.status === 'ok') {
                console.log('Abort all result:', data);
            } else {
                alert(data.detail || data.message || 'Failed to abort all tasks');
            }
        } catch (e) {
            console.error('Abort all failed:', e);
            alert('Failed to abort all tasks');
        }
    });

    // Ex-employee wall modal bindings
    document.getElementById('ex-employee-toolbar-btn').addEventListener('click', () => this.openExEmployeeWall());
    document.getElementById('ex-employee-close-btn').addEventListener('click', () => this.closeExEmployeeWall());
    document.getElementById('ex-employee-modal').addEventListener('click', (e) => {
      if (e.target.id === 'ex-employee-modal') this.closeExEmployeeWall();
    });

    // Project wall modal bindings
    document.getElementById('project-close-btn').addEventListener('click', () => this.closeProjectWall());
    document.getElementById('project-modal').addEventListener('click', (e) => {
      if (e.target.id === 'project-modal') this.closeProjectWall();
    });
    // Workflow modal bindings
    document.getElementById('workflow-close-btn').addEventListener('click', () => this.closeWorkflowPanel());
    document.getElementById('workflow-cancel-btn').addEventListener('click', () => this.closeWorkflowPanel());
    document.getElementById('workflow-edit-btn').addEventListener('click', () => this.toggleWorkflowEdit());
    document.getElementById('workflow-save-btn').addEventListener('click', () => this.saveWorkflow());
    // Close modal on overlay click
    document.getElementById('workflow-modal').addEventListener('click', (e) => {
      if (e.target.id === 'workflow-modal') this.closeWorkflowPanel();
    });

    // Generic popup modal bindings
    document.getElementById('generic-popup-close-btn').addEventListener('click', () => this.closePopup());
    document.getElementById('generic-popup-modal').addEventListener('click', (e) => {
      if (e.target.id === 'generic-popup-modal') this.closePopup();
    });

    // Settings floating panel: toggle via toolbar button
    this._settingsLoaded = false;
    const settingsBtn = document.getElementById('settings-toolbar-btn');
    const settingsPanel = document.getElementById('settings-floating-panel');
    if (settingsBtn && settingsPanel) {
      settingsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        settingsPanel.classList.toggle('hidden');
        if (!settingsPanel.classList.contains('hidden')) {
          // Position below the button using fixed positioning
          const rect = settingsBtn.getBoundingClientRect();
          settingsPanel.style.top = (rect.bottom + 4) + 'px';
          settingsPanel.style.right = (window.innerWidth - rect.right) + 'px';
          if (!this._settingsLoaded) {
            this._settingsLoaded = true;
            this._renderApiSettings();
          }
          this._renderSystemCrons();  // Always refresh cron status
        }
      });
      document.addEventListener('click', (e) => {
        if (!settingsPanel.contains(e.target) && e.target !== settingsBtn) {
          settingsPanel.classList.add('hidden');
        }
      });
    }

    // Settings sub-section toggle
    document.querySelectorAll('.settings-section-header').forEach(hdr => {
      hdr.addEventListener('click', () => {
        const targetId = hdr.getAttribute('data-target');
        const body = document.getElementById(targetId);
        if (body) {
          hdr.classList.toggle('collapsed');
          body.classList.toggle('collapsed');
        }
      });
    });

    // Listen for OAuth popup completion (company-level)
    window.addEventListener('message', (e) => {
      if (e.data === 'oauth_done' && this._settingsLoaded) {
        setTimeout(() => this._renderApiSettings(), 500);
      }
    });
  }

  // ===== 1-on-1 Meeting (Conversational Chat) =====
  updateOneononeDropdown(employees) {
    const select = document.getElementById('oneonone-target');
    const currentVal = select.value;
    select.innerHTML = '<option value="">-- Select Employee --</option>';
    for (const emp of employees) {
      const opt = document.createElement('option');
      opt.value = emp.id;
      const icon = emp.role === 'HR' ? '💼' : emp.role === 'COO' ? '⚙️' : '🤖';
      opt.textContent = `${icon} ${emp.name} (${emp.role})`;
      if (emp.is_listening) opt.textContent += ' 📖';
      select.appendChild(opt);
    }
    if (currentVal) select.value = currentVal;
  }

  async startOneonone() {
    const meetingType = document.getElementById('meeting-type-select').value;
    this._meetingType = meetingType;

    if (meetingType === 'all_hands' || meetingType === 'discussion') {
      return this._startGroupMeeting(meetingType);
    }

    // Original 1-on-1 flow
    const select = document.getElementById('oneonone-target');
    const empId = select.value;
    if (!empId) return;

    const emp = await fetch(`/api/employee/${empId}`).then(r => r.json()).catch(() => null);
    if (!emp) return;

    this._oneononeEmployeeId = empId;
    this._oneononeHistory = [];  // [{role: 'ceo'|'employee', content}]
    this._oneononeInputHistory = [];
    this._oneononeHistoryIdx = -1;
    this._oneononePendingFiles = [];

    // Switch to chat phase
    document.getElementById('oneonone-setup').classList.add('hidden');
    document.getElementById('oneonone-chat-phase').classList.remove('hidden');
    const nn = emp.nickname ? ` (${emp.nickname})` : '';
    document.getElementById('oneonone-chat-title').textContent =
      `🎓 1-on-1: ${emp.name}${nn}`;

    // Clear chat
    const chat = document.getElementById('oneonone-chat');
    chat.innerHTML = '';
    this._addOneononeSystemMsg(`1-on-1 meeting with ${emp.name}${nn} started. Chat naturally — when done, click "End Meeting".`);

    // Load previous 1-on-1 history from disk
    const prevHistory = await fetch(`/api/employee/${empId}/oneonone`).then(r => r.json()).catch(() => []);
    if (Array.isArray(prevHistory) && prevHistory.length > 0) {
      const empName = emp.name || 'Employee';
      for (const entry of prevHistory) {
        if (entry.role === 'ceo') {
          this._addOneononeBubble('CEO', entry.content, 'outgoing');
        } else if (entry.role === 'employee') {
          this._addOneononeBubble(empName, entry.content, 'incoming');
        }
      }
      this._oneononeHistory = prevHistory;
      this._addOneononeSystemMsg('── Previous conversation loaded ──');
    }

    // Reset input
    const textarea = document.getElementById('oneonone-input');
    textarea.value = '';
    textarea.style.height = 'auto';
    textarea.oninput = () => {
      textarea.style.height = 'auto';
      textarea.style.height = Math.min(textarea.scrollHeight, 80) + 'px';
    };
    textarea.focus();
  }

  async _startGroupMeeting(meetingType) {
    const startBtn = document.getElementById('oneonone-start-btn');
    startBtn.disabled = true;

    const res = await fetch('/api/meeting/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: meetingType }),
    }).then(r => r.json()).catch(e => ({ error: e.message }));

    startBtn.disabled = false;

    if (res.error) {
      alert(res.error);
      return;
    }

    this._oneononeEmployeeId = '__group_meeting__';
    this._oneononeHistory = [];
    this._oneononeInputHistory = [];
    this._oneononeHistoryIdx = -1;
    this._oneononePendingFiles = [];

    // Switch to chat phase
    document.getElementById('oneonone-setup').classList.add('hidden');
    document.getElementById('oneonone-chat-phase').classList.remove('hidden');

    const typeLabel = meetingType === 'all_hands' ? 'All-Hands' : 'Discussion';
    document.getElementById('oneonone-chat-title').textContent = `🎓 ${typeLabel} Meeting`;

    const chat = document.getElementById('oneonone-chat');
    chat.innerHTML = '';
    const participantNames = res.participants.map(p => p.nickname || p.name).join(', ');
    this._addOneononeSystemMsg(`${typeLabel} meeting started in ${res.room_name}. Participants: ${participantNames}`);

    if (meetingType === 'all_hands') {
      this._addOneononeSystemMsg('All-Hands mode: Send your address. Employees will absorb silently.');
    } else {
      this._addOneononeSystemMsg('Discussion mode: Send a message to start discussion. Employees will compete to respond.');
    }

    const textarea = document.getElementById('oneonone-input');
    textarea.value = '';
    textarea.style.height = 'auto';
    textarea.focus();
  }

  _addOneononeSystemMsg(text) {
    const chat = document.getElementById('oneonone-chat');
    const div = document.createElement('div');
    div.className = 'chat-msg-system';
    div.textContent = text;
    chat.appendChild(div);
    this._scrollOneononeToBottom();
  }

  _addOneononeBubble(sender, text, type) {
    const chat = document.getElementById('oneonone-chat');
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${type}`;
    const avatar = type === 'outgoing' ? '👔' : '🤖';
    bubble.innerHTML = `
      <div class="bubble-avatar">${avatar}</div>
      <div class="bubble-content">
        <div class="bubble-sender">${this._escapeHtml(sender)}</div>
        <div class="bubble-text">${this._escapeHtml(text)}</div>
      </div>
    `;
    chat.appendChild(bubble);
    this._scrollOneononeToBottom();
  }

  _scrollOneononeToBottom() {
    const container = document.querySelector('#oneonone-chat-phase .chat-container');
    if (container) container.scrollTop = container.scrollHeight;
  }

  async sendOneononeMessage() {
    const textarea = document.getElementById('oneonone-input');
    const message = textarea.value.trim();
    const hasFiles = this._oneononePendingFiles && this._oneononePendingFiles.length > 0;
    if ((!message && !hasFiles) || !this._oneononeEmployeeId) return;

    // Group meeting — use meeting/chat endpoint
    if (this._oneononeEmployeeId === '__group_meeting__') {
      return this._sendGroupMeetingMessage(message);
    }

    // Upload files first if any
    let attachments = [];
    const filePreviewData = hasFiles ? [...this._oneononePendingFiles] : [];
    if (hasFiles) {
      attachments = await this._uploadOneononeFiles();
    }

    // Show CEO bubble with image previews
    const displayText = message || '(attachment)';
    if (filePreviewData.length > 0) {
      // Build bubble with images
      const chat = document.getElementById('oneonone-chat');
      const bubble = document.createElement('div');
      bubble.className = 'chat-bubble outgoing';
      let attachHtml = '';
      for (const f of filePreviewData) {
        if (f.type === 'image') {
          attachHtml += `<img class="bubble-image" src="${f.dataUrl}" alt="attachment" style="max-height:80px;margin-top:4px;" />`;
        } else {
          attachHtml += `<div class="bubble-file" style="font-size:6px;color:var(--pixel-cyan);">📎 ${f.name}</div>`;
        }
      }
      bubble.innerHTML = `
        <div class="bubble-avatar">👔</div>
        <div class="bubble-content">
          <div class="bubble-sender">CEO</div>
          <div class="bubble-text">${this._escapeHtml(displayText)}</div>
          ${attachHtml}
        </div>
      `;
      chat.appendChild(bubble);
      this._scrollOneononeToBottom();
    } else {
      this._addOneononeBubble('CEO', displayText, 'outgoing');
    }

    this._oneononeInputHistory.push(message);
    this._oneononeHistoryIdx = -1;
    this._oneononeSavedDraft = '';
    textarea.value = '';
    textarea.style.height = 'auto';

    // Show typing
    const typing = document.getElementById('oneonone-typing');
    typing.classList.remove('hidden');
    this._scrollOneononeToBottom();
    const sendBtn = document.getElementById('oneonone-send-btn');
    sendBtn.disabled = true;

    fetch('/api/oneonone/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        employee_id: this._oneononeEmployeeId,
        message,
        history: this._oneononeHistory,
        attachments,
      }),
    })
      .then(r => {
        if (!r.ok) return r.text().then(t => ({ error: `Server error (${r.status}): ${t}` }));
        return r.json();
      })
      .then(data => {
        typing.classList.add('hidden');
        if (data.error) {
          this._addOneononeSystemMsg(`Error: ${data.error}`);
        } else {
          // Record history
          this._oneononeHistory.push({ role: 'ceo', content: message });
          this._oneononeHistory.push({ role: 'employee', content: data.response });

          const empMatch = (window.officeRenderer?.state?.employees || []).find(e => e.id === this._oneononeEmployeeId);
          const empName = empMatch ? empMatch.name : 'Employee';
          this._addOneononeBubble(empName, data.response, 'incoming');
        }
      })
      .catch(err => {
        typing.classList.add('hidden');
        this._addOneononeSystemMsg(`Error: ${err.message}`);
      })
      .finally(() => { sendBtn.disabled = false; });
  }

  async _sendGroupMeetingMessage(message) {
    const textarea = document.getElementById('oneonone-input');
    this._addOneononeBubble('CEO', message, 'outgoing');
    this._oneononeInputHistory.push(message);
    this._oneononeHistoryIdx = -1;
    textarea.value = '';
    textarea.style.height = 'auto';

    const typing = document.getElementById('oneonone-typing');
    typing.classList.remove('hidden');
    this._scrollOneononeToBottom();
    const sendBtn = document.getElementById('oneonone-send-btn');
    sendBtn.disabled = true;

    try {
      const res = await fetch('/api/meeting/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      }).then(r => r.json());

      typing.classList.add('hidden');

      if (res.error) {
        this._addOneononeSystemMsg(`Error: ${res.error}`);
      } else if (res.responses) {
        for (const r of res.responses) {
          const display = r.nickname || r.name || 'Employee';
          this._addOneononeBubble(display, r.message, 'incoming');
        }
        if (res.responses.length === 0 && this._meetingType === 'discussion') {
          this._addOneononeSystemMsg('No one wants to speak. Send another message or end the meeting.');
        }
      }
    } catch (err) {
      typing.classList.add('hidden');
      this._addOneononeSystemMsg(`Error: ${err.message}`);
    } finally {
      sendBtn.disabled = false;
    }
  }

  endOneononeMeeting() {
    if (!this._oneononeEmployeeId) return;

    // Group meeting — use meeting/end endpoint
    if (this._oneononeEmployeeId === '__group_meeting__') {
      return this._endGroupMeeting();
    }

    const endBtn = document.getElementById('oneonone-end-btn');
    endBtn.disabled = true;
    this._addOneononeSystemMsg('Ending meeting... reflecting on conversation...');

    fetch('/api/oneonone/end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        employee_id: this._oneononeEmployeeId,
        history: this._oneononeHistory,
      }),
    })
      .then(r => {
        if (!r.ok) return r.text().then(t => ({ error: `Server error (${r.status}): ${t}` }));
        return r.json();
      })
      .then(data => {
        if (data.error) {
          this._addOneononeSystemMsg(`Error: ${data.error}`);
        } else {
          if (data.principles_updated) {
            this._addOneononeSystemMsg('Meeting concluded. Work principles have been updated based on the conversation.');
            this.logEntry('CEO', `🎓 1-on-1 ended — principles updated`, 'guidance');
          } else {
            this._addOneononeSystemMsg('Meeting concluded. No principle updates needed.');
            this.logEntry('CEO', `🎓 1-on-1 ended — casual chat`, 'guidance');
          }
        }
      })
      .catch(err => {
        this._addOneononeSystemMsg(`Error: ${err.message}`);
      })
      .finally(() => {
        endBtn.disabled = false;
        this._oneononeEmployeeId = null;
        this._oneononeHistory = [];
      });
  }

  async _endGroupMeeting() {
    const endBtn = document.getElementById('oneonone-end-btn');
    endBtn.disabled = true;
    const sendBtn = document.getElementById('oneonone-send-btn');
    sendBtn.disabled = true;
    this._addOneononeSystemMsg('Ending meeting... EA is summarizing action points...');

    try {
      const data = await fetch('/api/meeting/end', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }).then(r => r.json());

      if (data.error) {
        this._addOneononeSystemMsg(`Error: ${data.error}`);
      } else {
        const ap = data.action_points || [];
        if (ap.length > 0) {
          this._addOneononeSystemMsg(`Meeting concluded. ${ap.length} action point(s):`);
          for (const point of ap) {
            this._addOneononeSystemMsg(`  • ${point}`);
          }
          if (data.project_id) {
            this._addOneononeSystemMsg(`Project created: ${data.project_id}`);
          }
          this.logEntry('CEO', `🎓 Meeting ended — ${ap.length} action points → project created`, 'guidance');
        } else {
          this._addOneononeSystemMsg('Meeting concluded. No action points — informational only.');
          this.logEntry('CEO', `🎓 Meeting ended — informational`, 'guidance');
        }
      }
    } catch (err) {
      this._addOneononeSystemMsg(`Error: ${err.message}`);
    } finally {
      endBtn.disabled = false;
      sendBtn.disabled = false;
      this._oneononeEmployeeId = null;
      this._oneononeHistory = [];
      this._meetingType = null;
    }
  }

  closeOneononeModal() {
    // If in a meeting, end it first
    if (this._oneononeEmployeeId && this._oneononeHistory && this._oneononeHistory.length > 0) {
      this.endOneononeMeeting();
    } else if (this._oneononeEmployeeId) {
      // Reset meeting state without calling end (no history = nothing to reflect on)
      this._oneononeEmployeeId = null;
      this._oneononeHistory = [];
    }
    document.getElementById('oneonone-modal').classList.add('hidden');
    // Reset to setup phase for next time
    document.getElementById('oneonone-setup').classList.remove('hidden');
    document.getElementById('oneonone-chat-phase').classList.add('hidden');
  }

  // ===== Employee Detail Modal =====
  openEmployeeDetail(emp) {
    const modal = document.getElementById('employee-modal');

    this.viewingEmployeeId = emp.id;

    // Avatar
    const avatarImg = document.getElementById('emp-detail-avatar');
    avatarImg.src = `/api/employees/${emp.id}/avatar?t=${Date.now()}`;
    avatarImg.onerror = function() { this.style.display = 'none'; };
    avatarImg.onload = function() { this.style.display = ''; };

    // Populate data
    const roleIcon = emp.role === 'HR' ? '💼' : emp.role === 'COO' ? '⚙️' : '🤖';
    document.getElementById('emp-modal-title').textContent = `${roleIcon} ${emp.name || ''} Details`;
    document.getElementById('emp-detail-number').textContent = emp.employee_number || '-';
    document.getElementById('emp-detail-name').textContent = emp.name || '-';
    document.getElementById('emp-detail-nickname').textContent = emp.nickname || '-';
    document.getElementById('emp-detail-department').textContent = emp.department || '-';
    document.getElementById('emp-detail-role').textContent = emp.title || emp.role || '-';
    document.getElementById('emp-detail-level').textContent = `Lv.${emp.level}`;
    document.getElementById('emp-detail-skills').textContent =
      (emp.skills || []).join(', ') || '-';

    // Permissions — render as tags
    const permsEl = document.getElementById('emp-detail-permissions');
    const perms = emp.permissions || [];
    if (perms.length) {
      permsEl.innerHTML = perms.map(p => `<span class="perm-tag perm-${p}">${p}</span>`).join(' ');
    } else {
      permsEl.textContent = '-';
    }

    // Salary
    const salaryEl = document.getElementById('emp-detail-salary');
    salaryEl.textContent = emp.salary_per_1m_tokens ? `$${emp.salary_per_1m_tokens}/1M tokens` : '-';

    // Performance history — quarter cards
    const perfEl = document.getElementById('emp-detail-perf-wrapper');
    const hist = emp.performance_history || [];
    const qTasks = emp.current_quarter_tasks || 0;
    let perfHtml = '<div class="perf-quarters">';
    // Show up to 3 past quarters
    for (let i = 0; i < 3; i++) {
      if (i < hist.length) {
        const q = hist[i];
        const cls = q.score === 3.75 ? 'high' : q.score === 3.25 ? 'low' : 'mid';
        perfHtml += `<div class="perf-quarter-card ${cls}"><div class="pq-label">Q${i + 1}</div><div class="pq-score">${q.score}</div></div>`;
      } else {
        perfHtml += `<div class="perf-quarter-card empty"><div class="pq-label">Q${i + 1}</div><div class="pq-score">-</div></div>`;
      }
    }
    perfHtml += '</div>';
    perfHtml += `<div class="perf-current-q">Current quarter: ${qTasks}/3 tasks</div>`;
    perfEl.innerHTML = perfHtml;

    // HR badges (probation / PIP)
    const hrBadgesEl = document.getElementById('emp-detail-hr-badges');
    let badgesHtml = '';
    if (emp.probation) badgesHtml += '<span class="roster-badge probation">PROBATION</span>';
    if (emp.pip) badgesHtml += '<span class="roster-badge pip">PIP</span>';
    if (!emp.onboarding_completed) badgesHtml += '<span class="roster-badge onboarding">ONBOARDING</span>';
    hrBadgesEl.innerHTML = badgesHtml;

    // OKRs
    const okrSection = document.getElementById('emp-detail-okr-section');
    const okrEl = document.getElementById('emp-detail-okrs');
    const okrs = emp.okrs || [];
    if (okrs.length > 0) {
      okrSection.classList.remove('hidden');
      let okrHtml = '';
      for (const okr of okrs) {
        const progress = okr.progress || 0;
        okrHtml += `<div class="okr-item">
          <div class="okr-objective">${okr.objective || okr.title || '-'}</div>
          <div class="okr-progress-bar"><div class="okr-progress-fill" style="width:${progress}%"></div></div>
          <div class="okr-progress-text">${progress}%</div>
        </div>`;
      }
      okrEl.innerHTML = okrHtml;
    } else {
      okrSection.classList.add('hidden');
    }

    // Work principles (rendered as Markdown)
    const principlesEl = document.getElementById('emp-detail-principles');
    const principles = emp.work_principles || '';
    if (principles) {
      principlesEl.innerHTML = `<div class="md-rendered">${this._renderMarkdown(principles)}</div>`;
      principlesEl.classList.remove('empty-hint');
    } else {
      principlesEl.innerHTML = '<span class="empty-hint">No work principles yet</span>';
    }

    // Guidance notes (rendered as Markdown)
    const guidanceEl = document.getElementById('emp-detail-guidance');
    const notes = emp.guidance_notes || [];
    if (notes.length > 0) {
      guidanceEl.innerHTML = '';
      for (const note of notes) {
        const item = document.createElement('div');
        item.className = 'guidance-note-item md-rendered';
        item.innerHTML = this._renderMarkdown(note);
        guidanceEl.appendChild(item);
      }
    } else {
      guidanceEl.innerHTML = '<span class="empty-hint">No 1-on-1 notes yet</span>';
    }

    // Fire button — hidden for founding employees (Lv.4+)
    const fireBtn = document.getElementById('emp-fire-btn');
    if (emp.level >= 4) {
      fireBtn.style.display = 'none';
    } else {
      fireBtn.style.display = '';
      fireBtn.onclick = () => this._confirmFireEmployee(emp);
    }

    // Talent Pool button — only for HR (00001)
    let talentPoolBtn = document.getElementById('emp-talent-pool-btn');
    if (!talentPoolBtn) {
      talentPoolBtn = document.createElement('button');
      talentPoolBtn.id = 'emp-talent-pool-btn';
      talentPoolBtn.className = 'pixel-btn emp-fire-btn';
      talentPoolBtn.textContent = '📋 Talent Pool';
      talentPoolBtn.style.marginRight = '8px';
      const fireBtn2 = document.getElementById('emp-fire-btn');
      fireBtn2.parentNode.insertBefore(talentPoolBtn, fireBtn2);
    }
    if (emp.id === '00001') {
      talentPoolBtn.style.display = '';
      talentPoolBtn.onclick = () => this.openTalentPool();
    } else {
      talentPoolBtn.style.display = 'none';
    }

    // Load model dropdown / API key section based on provider
    this._loadModelOrApiKeySection(emp.id);

    // Fetch and render agent task board + logs + crons
    this._fetchTaskBoard(emp.id);
    this._fetchExecutionLogs(emp.id);
    this._fetchCronList(emp.id);
    this._fetchEmployeeProjects(emp.id);

    // Start auto-refresh for task board while modal is open
    this._startTaskBoardPolling(emp.id);

    modal.classList.remove('hidden');
  }

  closeEmployeeDetail() {
    this.viewingEmployeeId = null;
    this._stopTaskBoardPolling();
    document.getElementById('employee-modal').classList.add('hidden');
  }

  _confirmFireEmployee(emp) {
    const reason = prompt(
      `Dismiss ${emp.name} (${emp.nickname})?\n\nEnter reason (or Cancel to abort):`,
      'CEO decision'
    );
    if (reason === null) return; // user cancelled

    if (!confirm(`Are you sure you want to dismiss ${emp.name}?\nReason: ${reason}\n\nThis cannot be undone.`)) {
      return;
    }

    fetch(`/api/employee/${emp.id}/fire`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason || 'CEO decision' }),
    })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => {
        if (data.error) {
          alert(`Cannot dismiss: ${data.error}`);
        } else {
          this.closeEmployeeDetail();
          this.addLog(`Dismissed ${data.name} (${data.nickname}) — ${data.reason}`);
          this.fetchState();
        }
      })
      .catch(err => {
        console.error('Fire employee error:', err);
        alert('Failed to dismiss employee. See console for details.');
      });
  }

  async _fetchTaskBoard(empId) {
    try {
      const resp = await fetch(`/api/employee/${empId}/taskboard`);
      const data = await resp.json();
      this._renderTaskBoard(data.tasks || []);
    } catch (err) {
      console.error('Task board fetch error:', err);
    }
  }

  async _fetchExecutionLogs(empId) {
    try {
      const resp = await fetch(`/api/employee/${empId}/logs`);
      const data = await resp.json();
      this._renderExecutionLogs(data.logs || []);
    } catch (err) {
      console.error('Execution logs fetch error:', err);
    }
  }

  _renderTaskBoard(tasks) {
    const el = document.getElementById('emp-detail-taskboard');
    if (!tasks || tasks.length === 0) {
      el.innerHTML = '<span class="empty-hint">No tasks</span>';
      return;
    }

    const empId = this.viewingEmployeeId;
    let html = '';
    for (const task of tasks) {
      const statusCls = task.status.replace('_', '-');
      html += `<div class="emp-taskboard-item ${statusCls}">`;
      html += `<div class="emp-taskboard-status" style="display:flex;justify-content:space-between;align-items:center;">`;
      html += `<span>${task.status}</span>`;
      if (task.status === 'pending' || task.status === 'processing') {
        html += `<button class="emp-task-cancel-btn" onclick="window._abortAgentTask('${empId}','${task.id}')">CANCEL</button>`;
      }
      html += `</div>`;
      html += `<div class="emp-taskboard-desc">${this._escHtml((task.description_preview || task.description || '').substring(0, 120))}</div>`;
      if (task.result) {
        html += `<div class="emp-taskboard-result">${this._escHtml(task.result.substring(0, 100))}</div>`;
      }
      if (task.cost_usd > 0) {
        html += `<div class="emp-taskboard-cost">$${task.cost_usd.toFixed(4)}</div>`;
      }
      html += '</div>';
    }
    el.innerHTML = html;
  }

  _renderExecutionLogs(logs) {
    const el = document.getElementById('emp-detail-logs');
    if (!logs || logs.length === 0) {
      el.innerHTML = '<span class="empty-hint">No logs</span>';
      return;
    }

    // Preserve which log entries are expanded before re-render
    const expandedSet = new Set();
    el.querySelectorAll('.emp-log-entry').forEach((entry, i) => {
      const full = entry.querySelector('.log-full');
      if (full && full.style.display !== 'none') expandedSet.add(i);
    });
    const prevScrollTop = el.scrollTop;
    const wasAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20;

    let html = '';
    for (let i = 0; i < logs.length; i++) {
      const log = logs[i];
      const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) : '';
      const typeCls = log.type || '';
      const raw = log.content || '';
      const truncated = raw.length > 200;
      const isExpanded = expandedSet.has(i);
      html += `<div class="emp-log-entry ${typeCls}">`;
      html += `<span class="log-ts">${ts}</span>`;
      html += `<span class="log-content" style="display:${truncated && isExpanded ? 'none' : 'inline'}">${this._escHtml(truncated ? raw.substring(0, 200) : raw)}</span>`;
      if (truncated) {
        html += `<span class="log-full" style="display:${isExpanded ? 'inline' : 'none'}">${this._escHtml(raw)}</span>`;
        html += `<span class="log-expand" style="display:${isExpanded ? 'none' : 'inline'}" onclick="this.parentElement.querySelector('.log-content').style.display='none';this.parentElement.querySelector('.log-full').style.display='inline';this.style.display='none';this.nextElementSibling.style.display='inline'">...more</span>`;
        html += `<span class="log-collapse" style="display:${isExpanded ? 'inline' : 'none'}" onclick="this.parentElement.querySelector('.log-content').style.display='inline';this.parentElement.querySelector('.log-full').style.display='none';this.style.display='none';this.previousElementSibling.style.display='inline'">less</span>`;
      }
      html += '</div>';
    }
    el.innerHTML = html;
    // Only auto-scroll if user was already at the bottom
    if (wasAtBottom) {
      el.scrollTop = el.scrollHeight;
    } else {
      el.scrollTop = prevScrollTop;
    }
  }

  _startTaskBoardPolling(empId) {
    this._stopTaskBoardPolling();
    this._taskBoardPollTimer = setInterval(() => {
      if (this.viewingEmployeeId === empId) {
        this._fetchTaskBoard(empId);
        this._fetchExecutionLogs(empId);
        this._fetchCronList(empId);
      }
    }, 3000);
  }

  _stopTaskBoardPolling() {
    if (this._taskBoardPollTimer) {
      clearInterval(this._taskBoardPollTimer);
      this._taskBoardPollTimer = null;
    }
  }

  // ===== Cron Management =====

  async _fetchCronList(empId) {
    try {
      const resp = await fetch(`/api/automations/${empId}`);
      const data = await resp.json();
      const crons = data.crons || [];
      const section = document.getElementById('emp-detail-cron-section');
      const container = document.getElementById('emp-detail-crons');
      if (!section || !container) return;

      section.style.display = '';
      if (crons.length === 0) {
        container.innerHTML = '<span class="empty-hint">No scheduled jobs</span>';
        const stopAllBtn = document.getElementById('emp-cron-stop-all-btn');
        if (stopAllBtn) stopAllBtn.style.display = 'none';
        return;
      }
      container.innerHTML = '';

      // Show/hide "Stop All" button
      const stopAllBtn = document.getElementById('emp-cron-stop-all-btn');
      if (stopAllBtn) {
        if (crons.length >= 2) {
          stopAllBtn.style.display = '';
          stopAllBtn.onclick = () => this._stopAllCrons(empId);
        } else {
          stopAllBtn.style.display = 'none';
        }
      }

      for (const cron of crons) {
        const item = document.createElement('div');
        item.className = 'emp-cron-item';

        const statusDot = cron.running
          ? '<span class="cron-status-dot running"></span>'
          : '<span class="cron-status-dot stopped"></span>';

        const info = document.createElement('div');
        info.className = 'emp-cron-info';
        const taskCount = (cron.dispatched_task_ids || []).length;
        const taskCountHtml = taskCount > 0
          ? `<span class="cron-task-count">${taskCount} tasks</span>`
          : '';
        info.innerHTML = `
          ${statusDot}
          <span class="cron-name">${this._escapeHtml(cron.name)}</span>
          <span class="cron-interval">${this._escapeHtml(cron.interval)}</span>
          ${taskCountHtml}
        `;

        const desc = document.createElement('div');
        desc.className = 'emp-cron-desc';
        desc.textContent = cron.task_description || '';

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'emp-cron-cancel-btn';
        cancelBtn.textContent = 'STOP';
        cancelBtn.onclick = () => this._cancelCron(empId, cron.name);

        item.appendChild(info);
        item.appendChild(desc);
        item.appendChild(cancelBtn);
        container.appendChild(item);
      }
    } catch (err) {
      console.error('Failed to fetch cron list:', err);
    }
  }

  async _cancelCron(empId, cronName) {
    if (!confirm(`Stop scheduled job "${cronName}"? Its pending tasks will also be cancelled.`)) return;
    try {
      const resp = await fetch(`/api/automations/${empId}/cron/${encodeURIComponent(cronName)}/stop`, {
        method: 'POST',
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        this._fetchCronList(empId);
      } else {
        alert(data.detail || data.message || 'Failed to stop cron');
      }
    } catch (err) {
      console.error('Failed to cancel cron:', err);
      alert('Failed to stop cron job');
    }
  }

  async _stopAllCrons(empId) {
    if (!confirm('Stop ALL scheduled jobs for this employee?')) return;
    try {
      const resp = await fetch(`/api/automations/${empId}/crons/stop-all`, {
        method: 'POST',
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        this._fetchCronList(empId);
      } else {
        alert(data.detail || data.message || 'Failed to stop all crons');
      }
    } catch (err) {
      console.error('Failed to stop all crons:', err);
      alert('Failed to stop all cron jobs');
    }
  }

  // ===== Employee Project History =====

  _fetchEmployeeProjects(employeeId) {
    const container = document.getElementById('emp-detail-projects');
    if (!container) return;
    container.innerHTML = '<span class="empty-hint">Loading...</span>';

    fetch(`/api/employees/${employeeId}/projects`)
      .then(r => r.json())
      .then(projects => {
        if (!projects || projects.length === 0) {
          container.innerHTML = '<span class="empty-hint">No project history</span>';
          return;
        }
        let html = '';
        for (const p of projects) {
          const statusCls = p.status === 'completed' ? 'pixel-green' : 'pixel-yellow';
          html += `<div class="emp-project-item" data-project-id="${this._escHtml(p.project_id)}">`;
          html += `<div class="emp-project-task">${this._escHtml(p.task || p.project_id)}</div>`;
          html += `<div class="emp-project-meta">`;
          html += `<span class="emp-project-role">${this._escHtml(p.role_in_project)}</span>`;
          html += `<span style="color:var(--${statusCls});">${this._escHtml(p.status)}</span>`;
          html += `</div></div>`;
        }
        container.innerHTML = html;

        container.querySelectorAll('.emp-project-item').forEach(el => {
          el.addEventListener('click', () => {
            const pid = el.dataset.projectId;
            this.closeEmployeeDetail();
            this._openProjectFromId(pid);
          });
        });
      })
      .catch(() => {
        container.innerHTML = '<span class="empty-hint">Failed to load</span>';
      });
  }

  _openProjectFromId(projectId) {
    this._loadIterationDetail(projectId, projectId);
    const detailEl = document.getElementById('project-detail');
    if (detailEl) detailEl.classList.remove('hidden');
  }

  // ===== Code Update Banner =====

  async _loadWorkspaceFiles(containerId, listUrl, title, fileBaseUrl, downloadUrl, isProject = false) {
    const container = document.getElementById(containerId);
    if (!container) return;

    try {
      const resp = await fetch(listUrl);
      const data = await resp.json();
      const files = isProject ? (data.files || []) : (data.files || []);
      if (!files.length) {
        container.innerHTML = '';
        return;
      }

      let html = `<div style="border:1px solid #333;border-radius:3px;padding:6px;">`;
      html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">`;
      html += `<span style="font-size:7px;font-weight:bold;color:var(--accent,#4fc3f7);">📁 ${this._escHtml(title)} (${files.length})</span>`;
      html += `<a href="${downloadUrl}" style="font-size:6px;color:#4fc3f7;text-decoration:underline;cursor:pointer;">⬇ ZIP</a>`;
      html += `</div>`;

      for (const f of files) {
        const fname = f.name || f;
        const fpath = f.path || f;
        const isDir = f.is_dir || false;
        const size = f.size != null ? this._formatFileSize(f.size) : '';

        if (isDir) {
          html += `<div style="padding:2px 4px;color:#888;">📂 ${this._escHtml(fname)}/</div>`;
        } else {
          const viewUrl = `${fileBaseUrl}/${encodeURIComponent(fpath)}`;
          html += `<div style="padding:2px 4px;display:flex;justify-content:space-between;align-items:center;">`;
          html += `<span style="cursor:pointer;color:#e0e0e0;text-decoration:underline;" onclick="window._ceoViewFile('${this._escHtml(viewUrl)}','${this._escHtml(fname)}')">${this._escHtml(fname)}</span>`;
          html += `<span style="color:#666;font-size:6px;">${size}</span>`;
          html += `</div>`;
        }
      }
      html += `</div>`;
      container.innerHTML = html;
    } catch (err) {
      console.error('Failed to load workspace files:', err);
    }
  }

  _formatFileSize(bytes) {
    if (bytes < 1024) return bytes + 'B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB';
    return (bytes / (1024 * 1024)).toFixed(1) + 'MB';
  }

  _showCodeUpdateBanner(count, files) {
    const banner = document.getElementById('code-update-banner');
    const textEl = document.getElementById('code-update-text');
    const shortFiles = (files || []).map(f => f.split('/').slice(-2).join('/'));
    textEl.textContent = `🔄 ${count} backend file(s) changed: ${shortFiles.slice(0, 3).join(', ')}${count > 3 ? '...' : ''}`;
    banner.classList.remove('hidden');
  }

  _showRestartBanner(message) {
    const banner = document.getElementById('code-update-banner');
    const textEl = document.getElementById('code-update-text');
    const applyBtn = document.getElementById('code-update-apply-btn');
    textEl.textContent = `⏳ ${message}`;
    applyBtn.textContent = 'Waiting...';
    applyBtn.disabled = true;
    banner.classList.remove('hidden');
  }

  _escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ===== CEO Inbox =====
  async _refreshCeoInbox() {
    try {
      const resp = await fetch('/api/ceo/inbox');
      const data = await resp.json();
      this._renderCeoInbox(data.items || []);
    } catch (e) {
      console.error('Failed to refresh CEO inbox:', e);
    }
  }

  _renderCeoInbox(items) {
    const list = document.getElementById('ceo-inbox-list');
    const badge = document.getElementById('ceo-inbox-badge');
    if (!list) return;

    if (items.length === 0) {
      list.innerHTML = '<div class="inbox-empty">No pending requests</div>';
      if (badge) { badge.textContent = '0'; badge.classList.add('hidden'); }
      return;
    }

    if (badge) {
      badge.textContent = items.length;
      badge.classList.remove('hidden');
      badge.classList.toggle('inbox-badge-active', items.length > 0);
    }

    list.innerHTML = items.map(item => `
      <div class="inbox-item" data-node-id="${item.node_id}" onclick="app._openCeoConversation('${item.node_id}')">
        <span class="inbox-status">${item.status === 'processing' ? '🔄' : '⏸'}</span>
        <div class="inbox-item-content">
          <div class="inbox-item-from">${this._escHtml(item.from_nickname || item.from_employee_id)}</div>
          <div class="inbox-item-desc">${this._escHtml((item.description || '').substring(0, 60))}${(item.description || '').length > 60 ? '...' : ''}</div>
        </div>
      </div>
    `).join('');
  }

  // ===== CEO Conversation Dialog =====
  async _openCeoConversation(nodeId) {
    try {
      const resp = await fetch(`/api/ceo/inbox/${nodeId}/open`, { method: 'POST' });
      const data = await resp.json();
      this._currentConvNodeId = nodeId;

      const overlay = document.getElementById('ceo-conv-overlay');
      const title = document.getElementById('ceo-conv-title');
      const desc = document.getElementById('ceo-conv-desc');

      const nickname = data.employee_nickname || data.employee_id || 'Employee';
      title.textContent = `📥 Task Request from ${nickname}`;
      if (data.description) {
        desc.textContent = data.description;
        desc.classList.remove('hidden');
      }
      this._renderConvMessages(data.messages || []);
      overlay.classList.remove('hidden');
      document.getElementById('ceo-conv-input').focus();
    } catch (e) {
      console.error('Failed to open conversation:', e);
    }
  }

  _closeCeoConversation() {
    document.getElementById('ceo-conv-overlay').classList.add('hidden');
  }

  _renderConvMessages(messages) {
    const container = document.getElementById('ceo-conv-messages');
    container.innerHTML = messages.map(m => {
      const isCeo = m.sender === 'ceo';
      const cls = isCeo ? 'conv-msg-ceo' : 'conv-msg-employee';
      let attachHtml = '';
      if (m.attachments && m.attachments.length) {
        attachHtml = m.attachments.map(a => `<div class="conv-attachment">📎 ${this._escHtml(a.filename)}</div>`).join('');
      }
      return `<div class="conv-msg ${cls}">
        <div class="conv-msg-sender">${isCeo ? 'CEO' : this._escHtml(m.sender)}</div>
        <div class="conv-msg-text">${this._escHtml(m.text)}</div>
        ${attachHtml}
        <div class="conv-msg-time">${new Date(m.timestamp).toLocaleTimeString()}</div>
      </div>`;
    }).join('');
    container.scrollTop = container.scrollHeight;
  }

  _appendConvMessage(msg) {
    const container = document.getElementById('ceo-conv-messages');
    const isCeo = msg.sender === 'ceo';
    const cls = isCeo ? 'conv-msg-ceo' : 'conv-msg-employee';
    let attachHtml = '';
    if (msg.attachments && msg.attachments.length) {
      attachHtml = msg.attachments.map(a => `<div class="conv-attachment">📎 ${this._escHtml(a.filename)}</div>`).join('');
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="conv-msg ${cls}">
        <div class="conv-msg-sender">${isCeo ? 'CEO' : this._escHtml(msg.sender)}</div>
        <div class="conv-msg-text">${this._escHtml(msg.text)}</div>
        ${attachHtml}
        <div class="conv-msg-time">${new Date(msg.timestamp).toLocaleTimeString()}</div>
      </div>
    `);
    container.scrollTop = container.scrollHeight;
  }

  async _sendCeoMessage() {
    const input = document.getElementById('ceo-conv-input');
    const text = input.value.trim();
    if (!text || !this._currentConvNodeId) return;
    input.value = '';
    this._appendConvMessage({ sender: 'ceo', text, timestamp: new Date().toISOString() });
    try {
      await fetch(`/api/ceo/inbox/${this._currentConvNodeId}/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
    } catch (e) {
      console.error('Failed to send message:', e);
    }
  }

  async _completeCeoConversation() {
    if (!this._currentConvNodeId) return;
    if (!confirm('Confirm completing this conversation?')) return;
    try {
      await fetch(`/api/ceo/inbox/${this._currentConvNodeId}/complete`, { method: 'POST' });
      this._closeCeoConversation();
      this._currentConvNodeId = null;
      this._refreshCeoInbox();
    } catch (e) {
      console.error('Failed to complete conversation:', e);
    }
  }

  async _uploadCeoAttachment() {
    const fileInput = document.getElementById('ceo-conv-file');
    if (!fileInput.files.length || !this._currentConvNodeId) return;
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    try {
      const resp = await fetch(`/api/ceo/inbox/${this._currentConvNodeId}/upload`, {
        method: 'POST', body: formData,
      });
      const data = await resp.json();
      this._appendConvMessage(data.message);
    } catch (e) {
      console.error('Failed to upload:', e);
    }
    fileInput.value = '';
  }

  async _loadModelOrApiKeySection(empId) {
    const container = document.getElementById('emp-settings-container');
    container.innerHTML = '<div style="color:var(--text-dim);font-size:6px;padding:4px;">Loading settings...</div>';

    try {
      const empResp = await fetch(`/api/employee/${empId}?_t=${Date.now()}`).then(r => r.json());
      const manifest = empResp.manifest;

      if (empResp.hosting === 'self') {
        // Self-hosted (Claude Code) — show login status instead of model picker
        container.innerHTML = '';
        this._renderSelfHostedSection(empId, empResp, container);
      } else if (manifest && manifest.settings && manifest.settings.sections) {
        container.innerHTML = '';
        // Founding employee notice
        if (empResp.level >= 4) {
          const notice = document.createElement('div');
          notice.style.cssText = 'font-size:5px;color:var(--pixel-yellow);padding:2px 4px;margin-bottom:3px;opacity:0.7;';
          notice.textContent = '⚠ Settings changes will trigger a server reload. Use when no tasks are running.';
          container.appendChild(notice);
        }
        for (const section of manifest.settings.sections) {
          const sectionEl = document.createElement('div');
          sectionEl.className = 'emp-settings-section';
          if (section.title) {
            sectionEl.innerHTML = `<div class="emp-settings-title">${this._escHtml(section.title)}</div>`;
          }
          for (const field of section.fields) {
            const fieldEl = this._renderManifestField(field, empResp);
            sectionEl.appendChild(fieldEl);
          }
          container.appendChild(sectionEl);
        }
        // Add a save button
        const saveRow = document.createElement('div');
        saveRow.style.cssText = 'display:flex;gap:4px;margin-top:4px;';
        saveRow.innerHTML = '<button class="pixel-btn small" id="emp-manifest-save-btn">Save</button>';
        container.appendChild(saveRow);
        document.getElementById('emp-manifest-save-btn').addEventListener('click', () => this._saveManifestSettings(empId));
      } else {
        // No manifest — fallback to simple model dropdown
        container.innerHTML = '';
        this._renderFallbackModelSection(empId, empResp, container);
      }
    } catch (err) {
      console.error('Failed to load employee settings:', err);
      container.innerHTML = '<div style="color:var(--pixel-red);font-size:6px;">Load failed</div>';
    }
  }

  _renderManifestField(field, empData) {
    const row = document.createElement('div');
    row.className = 'emp-settings-field';
    row.style.cssText = 'display:flex;align-items:center;gap:4px;width:100%;margin:2px 0;';

    const label = document.createElement('span');
    label.style.cssText = 'font-size:6px;color:var(--pixel-yellow);white-space:nowrap;min-width:60px;';
    label.textContent = field.label || field.key;
    row.appendChild(label);

    // Get current value from empData
    let currentValue = empData[field.key] ?? field.default ?? '';

    if (field.type === 'secret') {
      const input = document.createElement('input');
      input.type = 'password';
      input.className = 'emp-model-select';
      input.style.cssText = 'flex:1;';
      input.dataset.fieldKey = field.key;
      input.dataset.fieldType = 'secret';
      const isSet = field.key === 'api_key' ? empData.api_key_set : !!empData[`${field.key}_set`];
      const preview = field.key === 'api_key' ? empData.api_key_preview : empData[`${field.key}_preview`];
      input.placeholder = isSet
        ? `Set (${preview || '****'})`
        : 'Not set...';
      input.value = '';
      row.appendChild(input);
      // Status indicator
      const status = document.createElement('span');
      status.style.cssText = `font-size:6px;color:${isSet ? 'var(--pixel-green)' : 'var(--pixel-red,#f44)'};white-space:nowrap;`;
      status.textContent = isSet ? 'Set' : 'None';
      row.appendChild(status);
    } else if (field.type === 'number') {
      const input = document.createElement('input');
      input.type = 'number';
      input.className = 'emp-model-select';
      input.style.cssText = 'flex:1;';
      input.dataset.fieldKey = field.key;
      input.dataset.fieldType = 'number';
      input.value = currentValue;
      if (field.min !== undefined) input.min = field.min;
      if (field.max !== undefined) input.max = field.max;
      if (field.step !== undefined) input.step = field.step;
      row.appendChild(input);
    } else if (field.type === 'select' && field.options_from === 'api:models') {
      const select = document.createElement('select');
      select.className = 'emp-model-select';
      select.style.cssText = 'flex:1;';
      select.dataset.fieldKey = field.key;
      select.dataset.fieldType = 'select';
      select.innerHTML = '<option value="">Loading...</option>';
      row.appendChild(select);
      // Async load models
      this._populateModelSelect(select, currentValue);
    } else if (field.type === 'select') {
      const select = document.createElement('select');
      select.className = 'emp-model-select';
      select.style.cssText = 'flex:1;';
      select.dataset.fieldKey = field.key;
      select.dataset.fieldType = 'select';
      const options = field.options || [];
      select.innerHTML = options.map(o => {
        const val = typeof o === 'object' ? o.value : o;
        const lbl = typeof o === 'object' ? (o.label || o.value) : o;
        return `<option value="${val}"${val === currentValue ? ' selected' : ''}>${lbl}</option>`;
      }).join('');
      row.appendChild(select);
    } else if (field.type === 'toggle') {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.fieldKey = field.key;
      cb.dataset.fieldType = 'toggle';
      cb.checked = !!currentValue;
      row.appendChild(cb);
    } else if (field.type === 'textarea') {
      const ta = document.createElement('textarea');
      ta.className = 'emp-model-select';
      ta.style.cssText = 'flex:1;min-height:40px;resize:vertical;';
      ta.dataset.fieldKey = field.key;
      ta.dataset.fieldType = 'textarea';
      ta.value = currentValue;
      row.appendChild(ta);
    } else if (field.type === 'readonly') {
      const span = document.createElement('span');
      span.style.cssText = 'font-size:6px;color:var(--pixel-green);flex:1;';
      span.dataset.fieldKey = field.key;
      span.dataset.fieldType = 'readonly';
      if (field.value_from === 'api:sessions' && empData.sessions) {
        const sessions = empData.sessions || [];
        span.textContent = sessions.length > 0
          ? `${sessions.length} session(s): ${sessions.map(s => s.project_id).join(', ')}`
          : 'On-demand (no active sessions)';
      } else {
        span.textContent = currentValue || '-';
      }
      row.appendChild(span);
    } else if (field.type === 'oauth_button') {
      const btn = document.createElement('button');
      btn.id = 'emp-oauth-login-btn';
      btn.className = 'pixel-btn small';
      btn.textContent = empData.oauth_logged_in ? 'Re-login' : 'Login';
      btn.dataset.fieldKey = field.key;
      btn.dataset.fieldType = 'oauth_button';
      btn.addEventListener('click', () => this.startOAuthLogin());
      row.appendChild(btn);
    } else {
      // Default: text input
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'emp-model-select';
      input.style.cssText = 'flex:1;';
      input.dataset.fieldKey = field.key;
      input.dataset.fieldType = 'text';
      input.value = currentValue;
      row.appendChild(input);
    }

    return row;
  }

  async _populateModelSelect(select, currentModel) {
    try {
      const modelsResp = await fetch('/api/models').then(r => r.json());
      const models = modelsResp.models || [];
      select.innerHTML = '<option value="">-- Use default --</option>';
      for (const m of models) {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name || m.id;
        if (m.id === currentModel) opt.selected = true;
        select.appendChild(opt);
      }
    } catch (err) {
      select.innerHTML = '<option value="">Load failed</option>';
    }
  }

  async _saveManifestSettings(empId) {
    const container = document.getElementById('emp-settings-container');
    const fields = container.querySelectorAll('[data-field-key]');
    const payload = {};

    for (const el of fields) {
      const key = el.dataset.fieldKey;
      const type = el.dataset.fieldType;
      if (type === 'readonly') continue;
      if (type === 'secret') {
        if (el.value) payload[key] = el.value; // only send if changed
      } else if (type === 'toggle') {
        payload[key] = el.checked;
      } else if (type === 'number') {
        payload[key] = parseFloat(el.value) || 0;
      } else {
        payload[key] = el.value;
      }
    }

    // Map to existing API endpoints
    const saveBtn = document.getElementById('emp-manifest-save-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';

    try {
      // Save hosting mode via hosting endpoint
      if ('hosting' in payload) {
        const resp = await fetch(`/api/employee/${empId}/hosting`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hosting: payload.hosting }),
        }).then(r => r.json());
        if (resp.restart_required) {
          this.logEntry('SYSTEM', `Hosting changed to "${payload.hosting}". Restart required.`, 'system');
          this._showRestartBanner(`Hosting mode changed for #${empId}. Restart to apply.`);
        }
      }
      // Save model + temperature via model endpoint
      if ('llm_model' in payload) {
        await fetch(`/api/employee/${empId}/model`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: payload.llm_model, temperature: payload.temperature }),
        });
      }
      // Save API key via api-key endpoint
      if ('api_key' in payload) {
        await fetch(`/api/employee/${empId}/api-key`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: payload.api_key, model: payload.llm_model }),
        });
      }
      // Save custom settings (target_email, polling_interval, etc.) via generic endpoint
      const reserved = new Set(['hosting', 'llm_model', 'temperature', 'api_key', 'api_provider']);
      const customPayload = {};
      for (const [k, v] of Object.entries(payload)) {
        if (!reserved.has(k)) customPayload[k] = v;
      }
      if (Object.keys(customPayload).length > 0) {
        await fetch(`/api/employee/${empId}/settings`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(customPayload),
        });
      }
      this.logEntry('CEO', `Settings saved for employee #${empId}`, 'ceo');
      // Refresh to show updated settings (await to ensure re-render completes before finally block)
      await this._loadModelOrApiKeySection(empId);
    } catch (err) {
      this.logEntry('SYSTEM', `Save failed: ${err.message}`, 'system');
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save';
    }
  }

  _renderSelfHostedSection(empId, empData, container) {
    const sessions = empData.sessions || [];
    const hasActive = sessions.some(s => s.status === 'running');
    const statusColor = hasActive ? 'var(--pixel-green)' : 'var(--pixel-yellow)';
    const statusText = hasActive ? 'Active' : (sessions.length > 0 ? 'Idle' : 'No sessions');

    const section = document.createElement('div');
    section.className = 'emp-detail-section-content';
    section.style.cssText = 'display:flex;flex-direction:column;gap:3px;';
    section.innerHTML = `
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:55px;">Hosting</span>
        <span style="font-size:6px;color:var(--pixel-cyan);">Self-hosted (Claude Code)</span>
      </div>
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:55px;">Auth</span>
        <span style="font-size:6px;color:var(--text-main);">${this._escHtml(empData.auth_method || 'cli')}</span>
      </div>
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:55px;">Status</span>
        <span style="font-size:6px;color:${statusColor};">${statusText}</span>
      </div>
      ${sessions.length > 0 ? `<div style="font-size:5px;color:var(--text-dim);margin-top:2px;">${sessions.length} session(s)</div>` : ''}
    `;
    container.appendChild(section);
  }

  _renderFallbackModelSection(empId, empData, container) {
    const currentProvider = empData.api_provider || 'openrouter';
    const section = document.createElement('div');
    section.className = 'emp-detail-section-content emp-model-section';
    section.style.cssText = 'display:flex;flex-direction:column;gap:3px;';
    section.innerHTML = `
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:45px;">Provider</span>
        <select id="emp-detail-provider" class="emp-model-select" style="flex:1;">
          <option value="">Loading...</option>
        </select>
      </div>
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:45px;">Model</span>
        <select id="emp-detail-model" class="emp-model-select" style="flex:1;"><option value="">Loading...</option></select>
      </div>
      <div style="display:flex;gap:4px;justify-content:flex-end;">
        <button id="emp-model-save-btn" class="pixel-btn small" disabled>Save</button>
      </div>
    `;
    container.appendChild(section);

    // Populate provider dropdown from API
    fetch('/api/auth/providers')
      .then(r => r.json())
      .then(groups => {
        const providerSelect = document.getElementById('emp-detail-provider');
        if (!providerSelect) return;
        providerSelect.innerHTML = groups
          .map(g => `<option value="${g.group_id}"${g.group_id === currentProvider ? ' selected' : ''}>${g.label}</option>`)
          .join('');
      });

    // Provider change handler
    document.getElementById('emp-detail-provider').addEventListener('change', async (e) => {
      const provider = e.target.value;
      const saveBtn = document.getElementById('emp-model-save-btn');
      saveBtn.disabled = true;
      saveBtn.textContent = 'Switching...';

      try {
        // For now, just apply the provider change (API key can be set separately)
        const resp = await fetch('/api/auth/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scope: 'employee',
            employee_id: empId,
            choice: `${provider}-api-key`,
            api_key: '',
          }),
        });
        const data = await resp.json();
        if (data.error) {
          this.logEntry('SYSTEM', `Provider switch failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `Switched to ${provider}`, 'ceo');
          this._loadModelOrApiKeySection(empId);
        }
      } catch (err) {
        this.logEntry('SYSTEM', `Switch failed: ${err.message}`, 'system');
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save';
      }
    });

    this._loadModelDropdown(empId, empData);
  }

  async _loadModelDropdown(empId, empData) {
    const select = document.getElementById('emp-detail-model');
    const saveBtn = document.getElementById('emp-model-save-btn');
    if (!select || !saveBtn) return;
    select.innerHTML = '<option value="">Loading...</option>';
    saveBtn.disabled = true;

    try {
      const empResp = empData || await fetch(`/api/employee/${empId}`).then(r => r.json());
      const modelsResp = await fetch('/api/models').then(r => r.json());

      const currentModel = empResp.llm_model || '';
      const models = modelsResp.models || [];

      select.innerHTML = '<option value="">-- Use default model --</option>';
      for (const m of models) {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name || m.id;
        if (m.id === currentModel) opt.selected = true;
        select.appendChild(opt);
      }
      saveBtn.disabled = false;
    } catch (err) {
      select.innerHTML = '<option value="">Load failed</option>';
      console.error('Model list error:', err);
    }
  }

  saveEmployeeApiKey() {
    const empId = this.viewingEmployeeId;
    if (!empId) return;

    const keyInput = document.getElementById('emp-detail-api-key');
    const modelInput = document.getElementById('emp-api-model-input');
    const saveBtn = document.getElementById('emp-api-key-save-btn');
    saveBtn.disabled = true;

    const payload = {};
    if (keyInput.value) payload.api_key = keyInput.value;
    if (modelInput.value) payload.model = modelInput.value;

    fetch(`/api/employee/${empId}/api-key`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `API key update failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `API key updated for ${data.api_provider}`, 'ceo');
          // Refresh the status display
          const keyStatus = document.getElementById('emp-api-key-status');
          keyStatus.textContent = data.api_key_set ? 'Authenticated' : 'No key';
          keyStatus.style.color = data.api_key_set ? 'var(--pixel-green)' : 'var(--pixel-red, #f44)';
          keyInput.value = '';
          keyInput.placeholder = data.api_key_set ? 'API Key set (update to change)' : 'Enter API Key...';
          // Self-hosted sessions are on-demand, no auto-launch needed
        }
      })
      .catch(err => this.logEntry('SYSTEM', `API key update failed: ${err.message}`, 'system'))
      .finally(() => { saveBtn.disabled = false; });
  }

  // ===== Self-Hosted Session Info =====

  async _refreshSessionStatus(empId, empData) {
    const statusEl = document.getElementById('emp-session-status');
    const keyStatus = document.getElementById('emp-api-key-status');
    try {
      // Use sessions from pre-fetched employee data, or fetch them
      let sessions;
      if (empData && empData.sessions) {
        sessions = empData.sessions;
      } else {
        const res = await fetch(`/api/employee/${empId}/sessions`).then(r => r.json());
        sessions = res.sessions || [];
      }
      if (sessions.length > 0) {
        const labels = sessions.map(s => s.project_id).join(', ');
        statusEl.textContent = `Sessions (${sessions.length}): ${labels}`;
        statusEl.style.color = 'var(--pixel-green)';
        keyStatus.textContent = `${sessions.length} session(s)`;
        keyStatus.style.color = 'var(--pixel-green)';
      } else {
        statusEl.textContent = 'On-demand (no active sessions)';
        statusEl.style.color = 'var(--pixel-blue, #4af)';
        keyStatus.textContent = 'Ready';
        keyStatus.style.color = 'var(--pixel-green)';
      }
    } catch {
      statusEl.textContent = 'Status unknown';
      statusEl.style.color = '#aaa';
    }
  }

  startOAuthLogin() {
    const empId = this.viewingEmployeeId;
    if (!empId) return;

    const btn = document.getElementById('emp-oauth-login-btn');
    btn.disabled = true;
    btn.textContent = '...';

    fetch(`/api/employee/${empId}/oauth/start`, { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `OAuth start failed: ${data.error}`, 'system');
          return;
        }
        // Callback redirects back to localhost — fully automatic
        const w = 600, h = 700;
        const left = (screen.width - w) / 2, top = (screen.height - h) / 2;
        const popup = window.open(data.auth_url, 'oauth_login',
          `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`);
        if (!popup || popup.closed) {
          this.logEntry('SYSTEM',
            `<a href="${data.auth_url}" target="_blank" style="color:var(--pixel-green);text-decoration:underline;">Click here to open login page</a>`,
            'system');
        } else {
          this.logEntry('SYSTEM', 'Authorizing... login will complete automatically.', 'system');
        }
      })
      .catch(err => this.logEntry('SYSTEM', `OAuth error: ${err.message}`, 'system'))
      .finally(() => { btn.disabled = false; btn.textContent = 'Login'; });
  }

  async _tryAutoReadClipboard() {
    const empId = this._oauthEmpId;
    if (!this._oauthState || !empId) return;
    try {
      const text = await navigator.clipboard.readText();
      if (text && text.trim().length > 10) {
        let code = text.trim();
        if (code.includes('#')) code = code.split('#')[0];
        if (code.includes('code=')) {
          try {
            const url = new URL(code.replace('#', '?'));
            code = url.searchParams.get('code') || code;
          } catch { /* use as-is */ }
        }
        this.logEntry('SYSTEM', 'Auto-detected code from clipboard, logging in...', 'system');
        if (this._oauthPasteHandler) {
          document.removeEventListener('paste', this._oauthPasteHandler);
          this._oauthPasteHandler = null;
        }
        this._exchangeOAuthCode(empId, code);
        return;
      }
    } catch { /* clipboard not available */ }
    // Fallback: show manual input
    document.getElementById('emp-oauth-code-row').style.display = 'flex';
    this.logEntry('SYSTEM',
      'Paste the code (Ctrl+V) anywhere on this page, or type it above and click Submit.',
      'system');
  }

  _exchangeOAuthCode(empId, code) {
    fetch(`/api/employee/${empId}/oauth/exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, state: this._oauthState }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `OAuth failed: ${data.error}`, 'system');
          // Show manual input as fallback
          document.getElementById('emp-oauth-code-row').style.display = 'flex';
        } else {
          this.logEntry('SYSTEM', `Login successful! ${data.launch || ''}`, 'system');
          document.getElementById('emp-oauth-code-row').style.display = 'none';
          this._oauthState = null;
          this._loadModelOrApiKeySection(empId);
        }
      })
      .catch(err => this.logEntry('SYSTEM', `OAuth error: ${err.message}`, 'system'));
  }

  submitOAuthCode() {
    const empId = this.viewingEmployeeId;
    if (!empId || !this._oauthState) return;

    const input = document.getElementById('emp-oauth-code-input');
    let code = input.value.trim();
    if (!code) return;

    // Handle "code#state" format from Anthropic callback page
    if (code.includes('#')) code = code.split('#')[0];
    // Handle URL format
    if (code.includes('code=')) {
      try {
        const url = new URL(code.replace('#', '?'));
        code = url.searchParams.get('code') || code;
      } catch { /* use as-is */ }
    }

    this._exchangeOAuthCode(empId, code);
  }

  saveEmployeeModel() {
    const empId = this.viewingEmployeeId;
    if (!empId) return;

    const select = document.getElementById('emp-detail-model');
    const model = select.value;
    const saveBtn = document.getElementById('emp-model-save-btn');
    saveBtn.disabled = true;

    fetch(`/api/employee/${empId}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Model update failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `✅ Model updated: ${data.model || 'default'}`, 'ceo');
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Update failed: ${err.message}`, 'system'))
      .finally(() => { saveBtn.disabled = false; });
  }

  // ===== Hiring Request (COO → CEO) =====
  showHiringRequestModal(payload) {
    const modal = document.getElementById('hiring-request-modal');
    const bodyEl = document.getElementById('hiring-request-body');

    const skills = (payload.desired_skills || []).join(', ') || 'N/A';
    bodyEl.innerHTML = `
      <div style="margin-bottom:6px;">
        <span style="color:var(--pixel-yellow);font-size:7px;">ROLE</span><br>
        <span style="font-size:8px;">${payload.role}</span>
      </div>
      <div style="margin-bottom:6px;">
        <span style="color:var(--pixel-yellow);font-size:7px;">REASON</span><br>
        <span style="font-size:7px;">${payload.reason}</span>
      </div>
      <div style="margin-bottom:6px;">
        <span style="color:var(--pixel-yellow);font-size:7px;">DESIRED SKILLS</span><br>
        <span style="font-size:7px;">${skills}</span>
      </div>
    `;

    const approveBtn = document.getElementById('hiring-request-approve');
    const rejectBtn = document.getElementById('hiring-request-reject');
    const closeBtn = document.getElementById('hiring-request-close-btn');

    const cleanup = () => { modal.classList.add('hidden'); };

    const decide = (approved) => {
      fetch(`/api/hiring-requests/${payload.request_id}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved }),
      })
        .then(r => r.json())
        .then(data => {
          this.logEntry('CEO', `Hiring request ${approved ? 'approved' : 'rejected'}: ${payload.role}`, 'ceo');
        })
        .catch(err => this.logEntry('SYSTEM', `Decision failed: ${err.message}`, 'system'));
      cleanup();
    };

    approveBtn.onclick = () => decide(true);
    rejectBtn.onclick = () => decide(false);
    closeBtn.onclick = cleanup;
    modal.onclick = (e) => { if (e.target === modal) cleanup(); };

    modal.classList.remove('hidden');
  }

  // ===== Candidate Selection (Boss Online) =====
  showCandidateSelection(payload) {
    this._candidateBatchId = payload.batch_id;
    this._candidateList = payload.candidates || [];
    this._candidateRoles = payload.roles || [];
    this._selectedCandidates = new Map(); // candidateId -> {candidate, role}
    this._interviewingCandidate = null;

    // If no roles structure, wrap flat candidates into a single role group
    if (!this._candidateRoles.length && this._candidateList.length) {
      this._candidateRoles = [{ role: 'Candidates', description: '', candidates: this._candidateList }];
    }

    // Build flat lookup of all candidates
    this._allCandidatesMap = new Map();
    for (const role of this._candidateRoles) {
      for (const c of (role.candidates || [])) {
        const cid = c.talent_id || c.id;
        if (cid) this._allCandidatesMap.set(cid, c);
      }
    }

    const modal = document.getElementById('candidate-modal');
    const jdEl = document.getElementById('candidate-jd');
    const rolesEl = document.getElementById('candidate-roles');

    // JD sidebar
    jdEl.innerHTML = '<div style="font-size:7px;color:var(--pixel-yellow);margin-bottom:4px;">JD — Job Description</div>' +
      (payload.jd || '').replace(/\n/g, '<br>');

    // Render role groups
    rolesEl.innerHTML = '';

    const ROLE_EMOJI = {
      Engineer: '💻', Designer: '🎨', Analyst: '📊',
      DevOps: '🔧', QA: '🧪', Marketing: '📢',
      'Game Engineer': '🎮', 'Game Designer': '🎯',
      'Project Manager': '📋', Manager: '📋',
    };

    for (const roleGroup of this._candidateRoles) {
      const section = document.createElement('div');
      section.className = 'role-group';

      const roleEmoji = ROLE_EMOJI[roleGroup.role] || '🤖';
      const candidateCount = (roleGroup.candidates || []).length;

      section.innerHTML = `
        <div class="role-group-header">
          <span class="role-group-icon">${roleEmoji}</span>
          <span class="role-group-title">${roleGroup.role}</span>
          <span class="role-group-count">${candidateCount}</span>
          ${roleGroup.description ? `<span class="role-group-desc">${roleGroup.description}</span>` : ''}
        </div>
        <div class="role-group-cards"></div>
      `;

      const cardsContainer = section.querySelector('.role-group-cards');

      for (const c of (roleGroup.candidates || [])) {
        const cid = c.talent_id || c.id;
        const card = document.createElement('div');
        card.className = 'candidate-card';
        card.dataset.candidateId = cid;
        card.dataset.role = roleGroup.role;

        const emoji = ROLE_EMOJI[c.role] || '🤖';
        const tags = (c.personality_tags || []).join(' / ');
        const skills = (c.skill_set || c.skills || []).map(s => typeof s === 'object' ? s.name : s).join(', ');
        const tools = (c.tool_set || []).map(t => typeof t === 'object' ? t.name : t).join(', ');

        // Score display — handle both old (jd_relevance) and new (score) formats
        const score = c.score || c.jd_relevance || 0;
        const scorePct = Math.round(score * 100);
        const scoreColor = scorePct >= 80 ? 'var(--pixel-green)' : scorePct >= 50 ? 'var(--pixel-yellow)' : 'var(--pixel-red)';
        const reasoning = c.reasoning || '';

        const llmModel = c.llm_model || 'default';
        const costPer1m = c.cost_per_1m_tokens ? `$${c.cost_per_1m_tokens.toFixed(2)}/1M` : (c.salary_per_1m_tokens ? `$${c.salary_per_1m_tokens.toFixed(2)}/1M` : 'N/A');
        const hiringFee = c.hiring_fee != null ? `$${Number(c.hiring_fee).toFixed(2)}` : 'Free';
        const hosting = c.hosting || 'company';
        const hostingLabel = hosting === 'self' ? '🏠 Self' : '🏢 Co.';
        const authLabel = c.auth_method === 'oauth' ? 'OAuth' : 'API Key';

        card.innerHTML = `
          <div class="card-inner">
            <div class="card-front">
              <div class="card-select-indicator"></div>
              <div class="card-avatar">${emoji}</div>
              <div class="card-name">${c.name}</div>
              <div class="card-role">${c.role}</div>
              <div class="card-model" title="${llmModel}">🤖 ${llmModel.split('/').pop()}</div>
              <div class="card-tags">${tags}</div>
              <div class="card-score-bar">
                <div class="score-fill" style="width:${scorePct}%;background:${scoreColor};"></div>
                <span class="score-label">${scorePct}%</span>
              </div>
              ${reasoning ? `<div class="card-reasoning" title="${reasoning.replace(/"/g, '&quot;')}">${reasoning.substring(0, 40)}${reasoning.length > 40 ? '...' : ''}</div>` : ''}
              <div class="card-cost">${costPer1m} | ${hiringFee}</div>
              <div class="card-hosting">${hostingLabel}</div>
            </div>
            <div class="card-back">
              <div class="card-detail-title">Skills</div>
              <div class="card-detail-text">${skills || 'N/A'}</div>
              <div class="card-detail-title">Tools</div>
              <div class="card-detail-text">${tools || 'N/A'}</div>
              <div class="card-detail-title">LLM</div>
              <div class="card-detail-text">${llmModel} (${c.api_provider || 'openrouter'})</div>
              <div class="card-detail-title">Cost</div>
              <div class="card-detail-text">${costPer1m} | Fee: ${hiringFee}</div>
              <div class="card-detail-title">Hosting</div>
              <div class="card-detail-text">${hostingLabel} | Auth: ${authLabel}</div>
              <div class="card-actions">
                <button class="pixel-btn interview" data-id="${cid}">Interview</button>
              </div>
            </div>
          </div>
        `;

        // Click card to show detail panel (or toggle selection with Ctrl/Cmd)
        card.addEventListener('click', (e) => {
          if (e.target.closest('.pixel-btn')) return;
          if (e.ctrlKey || e.metaKey || e.shiftKey) {
            this._toggleCandidateSelection(cid, c, roleGroup.role, card);
            return;
          }
          this._showCandidateDetail(cid, c, roleGroup.role, card);
        });

        cardsContainer.appendChild(card);
      }

      rolesEl.appendChild(section);
    }

    // Show batch bar
    this._updateBatchBar();
    modal.classList.remove('hidden');
  }

  _toggleCandidateSelection(candidateId, candidate, role, cardEl) {
    if (this._selectedCandidates.has(candidateId)) {
      this._selectedCandidates.delete(candidateId);
      cardEl.classList.remove('selected');
    } else {
      this._selectedCandidates.set(candidateId, { candidate, role });
      cardEl.classList.add('selected');
    }
    this._updateBatchBar();
  }

  _showCandidateDetail(candidateId, candidate, role, cardEl) {
    const panel = document.getElementById('candidate-detail-panel');
    const content = document.getElementById('detail-panel-content');

    // Highlight active card
    document.querySelectorAll('.candidate-card.detail-active').forEach(c => c.classList.remove('detail-active'));
    cardEl.classList.add('detail-active');

    const c = candidate;
    const emoji = ROLE_EMOJI[c.role] || '🤖';
    const skills = (c.skill_set || c.skills || []).map(s => {
      if (typeof s === 'object') return `<span class="detail-skill">${s.name} <em>${s.proficiency || ''}</em></span>`;
      return `<span class="detail-skill">${s}</span>`;
    }).join('');
    const tools = (c.tool_set || []).map(t => {
      if (typeof t === 'object') return `<span class="detail-tool">${t.name}</span>`;
      return `<span class="detail-tool">${t}</span>`;
    }).join('');
    const tags = (c.personality_tags || []).map(t => `<span class="detail-tag">${t}</span>`).join('');
    const score = c.score || c.jd_relevance || 0;
    const scorePct = Math.round(score * 100);
    const scoreColor = scorePct >= 80 ? 'var(--pixel-green)' : scorePct >= 50 ? 'var(--pixel-yellow)' : 'var(--pixel-red)';
    const llmModel = c.llm_model || 'default';
    const costPer1m = c.cost_per_1m_tokens ? `$${c.cost_per_1m_tokens.toFixed(2)}/1M` : (c.salary_per_1m_tokens ? `$${c.salary_per_1m_tokens.toFixed(2)}/1M` : 'N/A');
    const hiringFee = c.hiring_fee != null ? `$${Number(c.hiring_fee).toFixed(2)}` : 'Free';
    const hosting = c.hosting || 'company';
    const hostingLabel = hosting === 'self' ? '🏠 Self-hosted' : '🏢 Company-hosted';
    const authLabel = c.auth_method === 'oauth' ? 'OAuth' : 'API Key';
    const reasoning = c.reasoning || '';

    content.innerHTML = `
      <div class="detail-header">
        <div class="detail-avatar">${emoji}</div>
        <div class="detail-name-block">
          <div class="detail-name">${c.name}</div>
          <div class="detail-role">${c.role}</div>
        </div>
        <div class="detail-score" style="border-color:${scoreColor}">
          <span style="color:${scoreColor}">${scorePct}%</span>
          <small>match</small>
        </div>
      </div>
      ${reasoning ? `<div class="detail-section"><div class="detail-label">Match Reasoning</div><div class="detail-text">${reasoning}</div></div>` : ''}
      ${tags ? `<div class="detail-section"><div class="detail-label">Personality</div><div class="detail-tags-list">${tags}</div></div>` : ''}
      <div class="detail-section"><div class="detail-label">Skills</div><div class="detail-skills-list">${skills || '<em>N/A</em>'}</div></div>
      ${tools ? `<div class="detail-section"><div class="detail-label">Tools</div><div class="detail-tools-list">${tools}</div></div>` : ''}
      <div class="detail-section detail-grid">
        <div><div class="detail-label">LLM Model</div><div class="detail-text">🤖 ${llmModel}</div></div>
        <div><div class="detail-label">Provider</div><div class="detail-text">${c.api_provider || 'openrouter'}</div></div>
        <div><div class="detail-label">Cost</div><div class="detail-text">${costPer1m}</div></div>
        <div><div class="detail-label">Hiring Fee</div><div class="detail-text">${hiringFee}</div></div>
        <div><div class="detail-label">Hosting</div><div class="detail-text">${hostingLabel}</div></div>
        <div><div class="detail-label">Auth</div><div class="detail-text">${authLabel}</div></div>
      </div>
    `;

    // Wire up panel buttons
    const interviewBtn = document.getElementById('detail-interview-btn');
    const selectBtn = document.getElementById('detail-select-btn');
    const closeBtn = document.getElementById('detail-panel-close');

    const isSelected = this._selectedCandidates.has(candidateId);
    selectBtn.textContent = isSelected ? '✗ Deselect' : '✔ Select';
    selectBtn.className = isSelected ? 'pixel-btn danger' : 'pixel-btn secondary';

    interviewBtn.onclick = () => this.startInterview(c);
    selectBtn.onclick = () => {
      this._toggleCandidateSelection(candidateId, c, role, cardEl);
      const nowSelected = this._selectedCandidates.has(candidateId);
      selectBtn.textContent = nowSelected ? '✗ Deselect' : '✔ Select';
      selectBtn.className = nowSelected ? 'pixel-btn danger' : 'pixel-btn secondary';
    };
    closeBtn.onclick = () => {
      panel.classList.add('hidden');
      cardEl.classList.remove('detail-active');
    };

    panel.classList.remove('hidden');
  }

  _updateBatchBar() {
    const count = this._selectedCandidates.size;
    const bar = document.getElementById('candidate-batch-bar');
    const countEl = document.getElementById('candidate-batch-count');
    const btn = document.getElementById('candidate-batch-hire-btn');

    if (count > 0) {
      bar.classList.remove('hidden');
      countEl.textContent = `${count} selected`;
      btn.textContent = `RECRUIT PARTY (${count})`;
      btn.disabled = false;
    } else {
      bar.classList.remove('hidden'); // always show bar for context
      countEl.textContent = '0 selected — click cards to select';
      btn.textContent = 'RECRUIT PARTY (0)';
      btn.disabled = true;
    }
  }

  batchHireCandidates() {
    const selections = [];
    for (const [candidateId, { candidate, role }] of this._selectedCandidates) {
      selections.push({ candidate_id: candidateId, role });
    }

    if (!selections.length) return;

    // Disable button
    const btn = document.getElementById('candidate-batch-hire-btn');
    btn.disabled = true;
    btn.textContent = 'RECRUITING...';

    this.logEntry('CEO', `Batch hiring ${selections.length} candidate(s)...`, 'ceo');

    // Save batch_id — closeCandidateModal won't clear it when _batchHired=true,
    // but keep a local copy as defensive measure
    const batchId = this._candidateBatchId;

    // Mark as hired so closeCandidateModal won't dismiss or clear batch_id
    this._batchHired = true;

    // Show onboarding progress modal
    this._onboardingBatchId = batchId;
    this._showOnboardingProgress(selections);

    // Close candidate modal (UI only — no dismiss, no batch_id cleanup)
    this.closeCandidateModal();
    this._batchHired = false;
    this._candidateBatchId = null;  // batch consumed, clean up

    fetch('/api/candidates/batch-hire', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        batch_id: batchId,
        selections,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Batch hire failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `⏳ Onboarding ${data.count || selections.length} candidate(s) in background...`, 'ceo');
        }
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Batch hire error: ${err.message}`, 'system');
      });
  }

  _showOnboardingProgress(selections) {
    const modal = document.getElementById('onboarding-progress-modal');
    const list = document.getElementById('onboarding-progress-list');

    list.innerHTML = '';
    document.getElementById('onboarding-done-btn').classList.add('hidden');
    modal.classList.remove('collapsed');
    this._onboardingItems = new Map();

    for (const sel of selections) {
      const candidate = this._allCandidatesMap ? this._allCandidatesMap.get(sel.candidate_id) : null;
      const name = candidate ? candidate.name : (sel.name || sel.candidate_id);
      const role = sel.role;

      const item = document.createElement('div');
      item.className = 'onboarding-item';
      item.innerHTML = `
        <div class="onboarding-item-header">
          <span class="onboarding-item-name">${name}</span>
          <span class="onboarding-item-role">${role}</span>
        </div>
        <div class="onboarding-steps">
          <div class="onboarding-step waiting" data-step="assigning_id">
            <span class="step-dot"></span>
            <span class="step-label">Assign ID</span>
          </div>
          <div class="onboarding-step waiting" data-step="copying_skills">
            <span class="step-dot"></span>
            <span class="step-label">Copy Skills</span>
          </div>
          <div class="onboarding-step waiting" data-step="registering_agent">
            <span class="step-dot"></span>
            <span class="step-label">Register Agent</span>
          </div>
          <div class="onboarding-step waiting" data-step="completed">
            <span class="step-dot"></span>
            <span class="step-label">Ready</span>
          </div>
        </div>
        <div class="onboarding-item-message"></div>
      `;

      list.appendChild(item);
      this._onboardingItems.set(sel.candidate_id, item);
    }

    modal.classList.remove('hidden');
  }

  _handleOnboardingProgress(payload) {
    const { candidate_id, step, message, name } = payload;

    // Ensure modal is visible
    const modal = document.getElementById('onboarding-progress-modal');
    if (modal.classList.contains('hidden')) {
      modal.classList.remove('hidden');
    }

    let item = this._onboardingItems ? this._onboardingItems.get(candidate_id) : null;

    // Create item dynamically if not found (e.g., single hire or modal wasn't pre-populated)
    if (!item) {
      if (!this._onboardingItems) this._onboardingItems = new Map();
      const list = document.getElementById('onboarding-progress-list');
      item = document.createElement('div');
      item.className = 'onboarding-item';
      item.innerHTML = `
        <div class="onboarding-item-header">
          <span class="onboarding-item-name">${name || candidate_id}</span>
          <span class="onboarding-item-role"></span>
        </div>
        <div class="onboarding-steps">
          <div class="onboarding-step waiting" data-step="assigning_id">
            <span class="step-dot"></span>
            <span class="step-label">Assign ID</span>
          </div>
          <div class="onboarding-step waiting" data-step="copying_skills">
            <span class="step-dot"></span>
            <span class="step-label">Copy Skills</span>
          </div>
          <div class="onboarding-step waiting" data-step="registering_agent">
            <span class="step-dot"></span>
            <span class="step-label">Register Agent</span>
          </div>
          <div class="onboarding-step waiting" data-step="completed">
            <span class="step-dot"></span>
            <span class="step-label">Ready</span>
          </div>
        </div>
        <div class="onboarding-item-message"></div>
      `;
      list.appendChild(item);
      this._onboardingItems.set(candidate_id, item);
    }

    // Update steps
    const steps = ['assigning_id', 'copying_skills', 'registering_agent', 'completed'];
    const stepIndex = steps.indexOf(step);

    const stepEls = item.querySelectorAll('.onboarding-step');
    stepEls.forEach((el, i) => {
      el.classList.remove('waiting', 'active', 'done', 'failed');
      if (step === 'failed') {
        if (i <= Math.max(stepIndex, 0)) el.classList.add('failed');
        else el.classList.add('waiting');
      } else if (i < stepIndex) {
        el.classList.add('done');
      } else if (i === stepIndex) {
        el.classList.add(step === 'completed' ? 'done' : 'active');
      } else {
        el.classList.add('waiting');
      }
    });

    // Update message
    const msgEl = item.querySelector('.onboarding-item-message');
    if (msgEl) msgEl.textContent = message || '';

    // Mark item status
    if (step === 'completed') {
      item.classList.add('completed');
    } else if (step === 'failed') {
      item.classList.add('failed');
    }

    // Check if all items are done — show close button
    if (this._onboardingItems) {
      const allDone = Array.from(this._onboardingItems.values()).every(
        el => el.classList.contains('completed') || el.classList.contains('failed')
      );
      if (allDone) {
        const closeBtn = document.getElementById('onboarding-done-btn');
        if (closeBtn) closeBtn.classList.remove('hidden');
      }
    }
  }

  closeCandidateModal() {
    const modal = document.getElementById('candidate-modal');
    const wasVisible = !modal.classList.contains('hidden');
    modal.classList.add('hidden');

    // If modal was visible and no candidates were hired, dismiss the shortlist
    if (wasVisible && this._candidateBatchId && (!this._batchHired)) {
      fetch('/api/candidates/dismiss', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_id: this._candidateBatchId }),
      }).catch(err => console.warn('Failed to dismiss shortlist:', err));
      this.logEntry('CEO', '🚫 Shortlist dismissed — this recruitment round is cancelled.', 'ceo');
      // Only clear batch_id on dismiss — hired flows manage their own cleanup
      this._candidateBatchId = null;
    }

    this._interviewingCandidate = null;
    this._selectedCandidates = new Map();
  }

  hireCandidate(candidate) {
    // Show loading state
    this.logEntry('CEO', `Processing hire for ${candidate.name}...`, 'ceo');
    // Disable all hire buttons to prevent double-click
    document.querySelectorAll('.pixel-btn.hire').forEach(b => { b.disabled = true; b.textContent = 'Hiring...'; });

    fetch('/api/candidates/hire', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        batch_id: this._candidateBatchId,
        candidate_id: candidate.id,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Hire failed: ${data.error}`, 'system');
          document.querySelectorAll('.pixel-btn.hire').forEach(b => { b.disabled = false; b.textContent = 'Hire'; });
        } else {
          this.logEntry('CEO', `⏳ Onboarding ${data.name || candidate.name} in background...`, 'ceo');
          this._batchHired = true;
          this.closeCandidateModal();
          this._batchHired = false;
          this._candidateBatchId = null;  // batch consumed
          this.closeInterviewModal();
        }
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Error: ${err.message}`, 'system');
        document.querySelectorAll('.pixel-btn.hire').forEach(b => { b.disabled = false; b.textContent = 'Hire'; });
      });
  }

  // ===== Interview Chatbot (separate modal) =====
  startInterview(candidate) {
    this._interviewingCandidate = candidate;
    this._pendingFiles = [];  // files to attach to next message

    const modal = document.getElementById('interview-modal');
    document.getElementById('interview-modal-title').textContent =
      `💬 Interview: ${candidate.name} (${candidate.role})`;

    // Show model badge
    const badge = document.getElementById('interview-model-badge');
    badge.textContent = candidate.llm_model || 'default';

    // Clear chat
    const chat = document.getElementById('interview-chat');
    chat.innerHTML = '';
    this._addChatSystemMsg(`Interview with ${candidate.name} started. Ask questions and the candidate will respond based on their expertise.`);

    // Reset input
    const textarea = document.getElementById('interview-question');
    textarea.value = '';
    textarea.style.height = 'auto';

    // Clear previews
    this._pendingFiles = [];
    this._updatePreviewBar();

    // Setup file input
    const fileInput = document.getElementById('interview-file-input');
    fileInput.value = '';
    fileInput.onchange = () => this._handleFileSelect(fileInput.files);

    // Setup drag-and-drop on chat container
    const container = modal.querySelector('.chat-container');
    container.ondragover = (e) => { e.preventDefault(); container.style.borderColor = 'var(--pixel-cyan)'; };
    container.ondragleave = () => { container.style.borderColor = ''; };
    container.ondrop = (e) => {
      e.preventDefault();
      container.style.borderColor = '';
      if (e.dataTransfer.files.length) this._handleFileSelect(e.dataTransfer.files);
    };

    // Auto-resize textarea
    textarea.oninput = () => {
      textarea.style.height = 'auto';
      textarea.style.height = Math.min(textarea.scrollHeight, 80) + 'px';
    };

    modal.classList.remove('hidden');
    textarea.focus();
  }

  _addChatSystemMsg(text) {
    const chat = document.getElementById('interview-chat');
    const div = document.createElement('div');
    div.className = 'chat-msg-system';
    div.textContent = text;
    chat.appendChild(div);
    this._scrollChatToBottom();
  }

  _addChatBubble(sender, text, type, attachments = []) {
    const chat = document.getElementById('interview-chat');
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${type}`;

    const avatar = type === 'outgoing' ? '👔' : '🤖';

    let attachHtml = '';
    for (const att of attachments) {
      if (att.type === 'image') {
        attachHtml += `<img class="bubble-image" src="${att.dataUrl}" alt="attachment" onclick="window.open(this.src)" />`;
      } else if (att.type === 'video') {
        attachHtml += `<video class="bubble-image" src="${att.dataUrl}" controls style="max-height:120px;"></video>`;
      } else {
        attachHtml += `<div class="bubble-file">${att.name}</div>`;
      }
    }

    bubble.innerHTML = `
      <div class="bubble-avatar">${avatar}</div>
      <div class="bubble-content">
        <div class="bubble-sender">${sender}</div>
        <div class="bubble-text">${this._escapeHtml(text)}</div>
        ${attachHtml}
      </div>
    `;
    chat.appendChild(bubble);
    this._scrollChatToBottom();
  }

  _scrollChatToBottom() {
    const container = document.querySelector('.chat-container');
    if (container) container.scrollTop = container.scrollHeight;
  }

  _showTypingIndicator() {
    const typing = document.getElementById('interview-typing');
    if (typing) typing.classList.remove('hidden');
    this._scrollChatToBottom();
  }

  _hideTypingIndicator() {
    const typing = document.getElementById('interview-typing');
    if (typing) typing.classList.add('hidden');
  }

  _handleFileSelect(files) {
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = (e) => {
        let type = 'file';
        if (file.type.startsWith('image/')) type = 'image';
        else if (file.type.startsWith('video/')) type = 'video';

        this._pendingFiles.push({
          name: file.name,
          type,
          dataUrl: e.target.result,
          // Extract base64 from data URL for API
          base64: e.target.result.split(',')[1] || '',
          mimeType: file.type,
        });
        this._updatePreviewBar();
      };
      reader.readAsDataURL(file);
    }
  }

  _updatePreviewBar() {
    const bar = document.getElementById('interview-preview-bar');
    if (!this._pendingFiles.length) {
      bar.classList.add('hidden');
      bar.innerHTML = '';
      return;
    }
    bar.classList.remove('hidden');
    bar.innerHTML = '';
    this._pendingFiles.forEach((f, idx) => {
      const item = document.createElement('div');
      item.className = 'chat-preview-item';
      if (f.type === 'image') {
        item.innerHTML = `<img class="chat-preview-thumb" src="${f.dataUrl}" alt="${f.name}" />`;
      } else if (f.type === 'video') {
        item.innerHTML = `<div class="chat-preview-file">🎬<br>${f.name.substring(0, 8)}</div>`;
      } else {
        item.innerHTML = `<div class="chat-preview-file">📄<br>${f.name.substring(0, 8)}</div>`;
      }
      const removeBtn = document.createElement('button');
      removeBtn.className = 'chat-preview-remove';
      removeBtn.textContent = '×';
      removeBtn.onclick = () => {
        this._pendingFiles.splice(idx, 1);
        this._updatePreviewBar();
      };
      item.appendChild(removeBtn);
      bar.appendChild(item);
    });
  }

  closeInterviewModal() {
    document.getElementById('interview-modal').classList.add('hidden');
  }

  askInterviewQuestion() {
    const textarea = document.getElementById('interview-question');
    const question = textarea.value.trim();
    if ((!question && !this._pendingFiles.length) || !this._interviewingCandidate) return;

    // Gather attachments
    const attachments = [...this._pendingFiles];
    const imageB64s = attachments.filter(f => f.type === 'image').map(f => f.base64);

    // Show CEO message with attachments
    this._addChatBubble('CEO', question || '(attachment)', 'outgoing', attachments);

    // Clear input and previews
    textarea.value = '';
    textarea.style.height = 'auto';
    this._pendingFiles = [];
    this._updatePreviewBar();

    // Show typing indicator
    this._showTypingIndicator();
    const askBtn = document.getElementById('interview-ask-btn');
    askBtn.disabled = true;

    fetch('/api/candidates/interview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: question || 'Please look at the attached content.',
        candidate: this._interviewingCandidate,
        images: imageB64s,
      }),
    })
      .then(r => r.json())
      .then(data => {
        this._hideTypingIndicator();
        if (data.error) {
          this._addChatSystemMsg(`Error: ${data.error}`);
        } else {
          this._addChatBubble(
            this._interviewingCandidate.name,
            data.answer,
            'incoming'
          );
        }
      })
      .catch(err => {
        this._hideTypingIndicator();
        this._addChatSystemMsg(`Error: ${err.message}`);
      })
      .finally(() => { askBtn.disabled = false; });
  }

  hireCandidateFromInterview() {
    if (this._interviewingCandidate) {
      this.hireCandidate(this._interviewingCandidate);
    }
  }

  // ===== Project Wall =====
  openProjectWall() {
    const modal = document.getElementById('project-modal');
    modal.classList.remove('hidden');
    this.loadProjectList();
  }

  closeProjectWall() {
    document.getElementById('project-modal').classList.add('hidden');
    if (this._treeRenderer) this._treeRenderer.stopAutoRefresh();
  }

  loadProjectList() {
    const listEl = document.getElementById('project-list');
    listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">Loading...</div>';
    listEl.classList.remove('hidden');
    document.getElementById('project-detail').classList.add('hidden');

    fetch('/api/projects')
      .then(r => r.json())
      .then(data => {
        const projects = data.projects || [];
        if (projects.length === 0) {
          listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">No project records</div>';
          return;
        }
        listEl.innerHTML = '';
        for (const p of projects) {
          const card = document.createElement('div');
          card.className = 'project-card';
          const statusIcon = p.status === 'completed' ? '\u2705' : '\uD83D\uDD04';
          const date = p.created_at ? p.created_at.substring(0, 10) : '';
          const costStr = p.cost_usd ? ` · $${p.cost_usd.toFixed(3)}` : '';
          card.innerHTML = `
            <div class="project-card-header">
              <span>${statusIcon} ${p.task.substring(0, 40)}${p.task.length > 40 ? '...' : ''}</span>
              <span class="project-card-date">${date}</span>
            </div>
            <div class="project-card-meta">
              ${p.routed_to}${p.current_owner && p.status !== 'completed' ? ' · Current: ' + p.current_owner : ''} | ${p.participant_count} participants | ${p.action_count} entries${costStr}
            </div>
          `;
          card.style.cursor = 'pointer';
          card.addEventListener('click', () => this.loadProjectDetail(p.project_id));
          listEl.appendChild(card);
        }
      })
      .catch(err => {
        listEl.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;">Load failed: ${err.message}</div>`;
      });
  }

  loadProjectDetail(projectId) {
    const listEl = document.getElementById('project-list');
    const detailEl = document.getElementById('project-detail');
    const contentEl = document.getElementById('project-detail-content');

    listEl.classList.add('hidden');
    detailEl.classList.remove('hidden');
    contentEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">Loading...</div>';

    fetch(`/api/projects/${encodeURIComponent(projectId)}`)
      .then(r => r.json())
      .then(doc => {
        if (doc.error) {
          contentEl.innerHTML = `<div style="color:var(--pixel-red);">${doc.error}</div>`;
          return;
        }
        let html = `<h4 style="color:var(--pixel-yellow);font-size:8px;margin:6px 0;">${doc.task || ''}</h4>`;
        html += `<div style="font-size:6px;color:var(--text-dim);margin-bottom:8px;">`;
        html += `Status: ${doc.status} | Routed to: ${doc.routed_to} | Created: ${(doc.created_at || '').substring(0, 19)}`;
        if (doc.completed_at) html += ` | Completed: ${doc.completed_at.substring(0, 19)}`;
        html += `</div>`;

        // Timeline
        const timeline = doc.timeline || [];
        if (timeline.length > 0) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:6px 0 4px;">Timeline (${timeline.length} entries):</div>`;
          for (const entry of timeline) {
            const time = (entry.time || '').substring(11, 19);
            html += `<div style="font-size:6px;line-height:1.8;border-left:2px solid var(--border);padding-left:6px;margin:2px 0;">`;
            html += `<span style="color:var(--text-dim);">[${time}]</span> `;
            html += `<span style="color:var(--pixel-green);">${entry.employee_id}</span> `;
            html += `<span style="color:var(--pixel-yellow);">${entry.action}</span>`;
            if (entry.detail) {
              html += `<div style="color:var(--pixel-white);margin-top:1px;">${entry.detail.substring(0, 200)}${entry.detail.length > 200 ? '...' : ''}</div>`;
            }
            html += `</div>`;
          }
        }

        // Cost & Budget
        const cost = doc.cost || {};
        if (cost.actual_cost_usd > 0 || cost.budget_estimate_usd > 0) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 4px;">Cost & Budget:</div>`;
          const actual = cost.actual_cost_usd || 0;
          const budget = cost.budget_estimate_usd || 0;
          const tokens = cost.token_usage || {};
          let budgetLine = '';
          if (budget > 0) {
            const pct = ((actual / budget) * 100).toFixed(1);
            const pctColor = pct > 100 ? 'var(--pixel-red)' : 'var(--pixel-green)';
            budgetLine = ` / Budget: $${budget.toFixed(3)} (<span style="color:${pctColor};">${pct}%</span>)`;
          }
          html += `<div style="font-size:6px;color:var(--pixel-white);margin:2px 0;">Actual: $${actual.toFixed(4)}${budgetLine}</div>`;
          html += `<div style="font-size:6px;color:var(--text-dim);margin:2px 0;">Tokens: ${(tokens.input||0).toLocaleString()} in / ${(tokens.output||0).toLocaleString()} out</div>`;
          // Breakdown table
          const breakdown = cost.breakdown || [];
          if (breakdown.length > 0) {
            html += `<table style="font-size:5px;width:100%;border-collapse:collapse;margin-top:4px;">`;
            html += `<tr style="color:var(--text-dim);"><th style="text-align:left;">Employee</th><th>Model</th><th>Tokens</th><th>Cost</th></tr>`;
            for (const b of breakdown) {
              html += `<tr><td>${b.employee_id}</td><td>${(b.model||'').split('/').pop()}</td><td>${(b.total_tokens||0).toLocaleString()}</td><td>$${(b.cost_usd||0).toFixed(4)}</td></tr>`;
            }
            html += `</table>`;
          }
        }

        // Output
        if (doc.output) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 4px;">Final Output:</div>`;
          html += `<div style="font-size:6px;color:var(--pixel-white);background:var(--bg-dark);padding:6px;border:1px solid var(--border);">${doc.output}</div>`;
        }

        contentEl.innerHTML = html;
      })
      .catch(err => {
        contentEl.innerHTML = `<div style="color:var(--pixel-red);">Load failed: ${err.message}</div>`;
      });
  }


  /**
   * Build a side-by-side diff view HTML string.
   */
  _buildDiffView(oldContent, newContent) {
    const oldLines = oldContent.split('\n');
    const newLines = newContent.split('\n');
    const truncAt = 60;

    let html = '<div class="file-edit-diff">';
    html += '<div class="fe-diff-header"><span class="fe-diff-old-h">Original</span><span class="fe-diff-new-h">New</span></div>';
    html += '<div class="fe-diff-body">';
    html += '<div class="fe-diff-col fe-diff-old">';
    for (let i = 0; i < Math.min(oldLines.length, truncAt); i++) {
      const cls = (i < newLines.length && oldLines[i] !== newLines[i]) ? ' fe-changed' : '';
      html += `<div class="fe-diff-line${cls}">${this._escapeHtml(oldLines[i])}</div>`;
    }
    if (oldLines.length > truncAt) html += `<div class="fe-diff-line fe-truncated">... (${oldLines.length - truncAt} more lines)</div>`;
    html += '</div>';
    html += '<div class="fe-diff-col fe-diff-new">';
    for (let i = 0; i < Math.min(newLines.length, truncAt); i++) {
      const cls = (i >= oldLines.length) ? ' fe-added'
        : (oldLines[i] !== newLines[i]) ? ' fe-changed' : '';
      html += `<div class="fe-diff-line${cls}">${this._escapeHtml(newLines[i])}</div>`;
    }
    if (newLines.length > truncAt) html += `<div class="fe-diff-line fe-truncated">... (${newLines.length - truncAt} more lines)</div>`;
    html += '</div></div></div>';
    return html;
  }


  // ===== Workflow Viewer/Editor =====
  openWorkflowPanel() {
    const modal = document.getElementById('workflow-modal');
    modal.classList.remove('hidden');
    this.loadWorkflowList();
  }

  closeWorkflowPanel() {
    document.getElementById('workflow-modal').classList.add('hidden');
    this.currentWorkflowName = null;
    document.getElementById('workflow-content').classList.add('hidden');
    document.getElementById('workflow-rendered').classList.add('hidden');
    document.getElementById('workflow-placeholder').classList.remove('hidden');
    document.getElementById('workflow-edit-btn').disabled = true;
    document.getElementById('workflow-save-btn').classList.add('hidden');
    document.getElementById('workflow-save-btn').disabled = true;
  }

  // ===== Generic Popup System =====
  // Usage from backend: publish event "open_popup" with payload:
  //   { type: "info"|"confirm"|"url"|"oauth", title, message, url, buttons, agent }
  openPopup(opts = {}) {
    const modal = document.getElementById('generic-popup-modal');
    const title = document.getElementById('generic-popup-title');
    const body = document.getElementById('generic-popup-body');
    const footer = document.getElementById('generic-popup-footer');

    title.textContent = opts.title || 'Notification';
    body.innerHTML = '';
    footer.innerHTML = '';
    footer.style.display = 'none';

    const type = opts.type || 'info';

    // Message text
    if (opts.message) {
      const msg = document.createElement('div');
      msg.className = 'popup-message';
      msg.textContent = opts.message;
      body.appendChild(msg);
    }

    // URL display + open button
    if (opts.url) {
      const urlBox = document.createElement('div');
      urlBox.className = 'popup-url-box';
      urlBox.textContent = opts.url;
      urlBox.title = 'Click to open';
      urlBox.onclick = () => window.open(opts.url, '_blank');
      body.appendChild(urlBox);
    }

    // Type-specific behavior
    if (type === 'oauth' && opts.url) {
      const actions = document.createElement('div');
      actions.className = 'popup-actions';
      const openBtn = document.createElement('button');
      openBtn.className = 'pixel-btn';
      openBtn.textContent = 'Authorize';
      openBtn.onclick = () => {
        const w = 600, h = 700;
        const left = (screen.width - w) / 2, top = (screen.height - h) / 2;
        const popup = window.open(opts.url, 'oauth_popup',
          `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`);
        if (!popup || popup.closed) {
          window.open(opts.url, '_blank');
        }
      };
      actions.appendChild(openBtn);
      body.appendChild(actions);
    }

    if (type === 'confirm') {
      footer.style.display = 'flex';
      const confirmBtn = document.createElement('button');
      confirmBtn.className = 'pixel-btn';
      confirmBtn.textContent = opts.confirm_label || 'Confirm';
      confirmBtn.onclick = () => {
        if (opts.callback_url) {
          fetch(opts.callback_url, { method: 'POST' })
            .then(() => this.closePopup())
            .catch(() => this.closePopup());
        } else {
          this.closePopup();
        }
      };
      const cancelBtn = document.createElement('button');
      cancelBtn.className = 'pixel-btn secondary';
      cancelBtn.textContent = 'Cancel';
      cancelBtn.onclick = () => this.closePopup();
      footer.appendChild(cancelBtn);
      footer.appendChild(confirmBtn);
    }

    // Credentials input form
    if (type === 'credentials' && opts.fields) {
      const form = document.createElement('div');
      form.style.cssText = 'display:flex;flex-direction:column;gap:6px;margin-top:8px;';
      const inputs = {};
      for (const f of opts.fields) {
        const label = document.createElement('label');
        label.style.cssText = 'font-size:7px;color:var(--text-dim);';
        label.textContent = f.label || f.name;
        const input = document.createElement('input');
        input.type = f.secret ? 'password' : 'text';
        input.placeholder = f.placeholder || '';
        input.value = f.default || '';
        input.style.cssText = 'width:100%;background:var(--bg-dark);color:var(--pixel-white);border:2px solid var(--border);font-family:"Press Start 2P",monospace;font-size:7px;padding:6px;';
        inputs[f.name] = input;
        form.appendChild(label);
        form.appendChild(input);
      }
      body.appendChild(form);

      footer.style.display = 'flex';
      const submitBtn = document.createElement('button');
      submitBtn.className = 'pixel-btn';
      submitBtn.textContent = opts.submit_label || 'Submit';
      submitBtn.onclick = () => {
        const values = {};
        for (const [k, inp] of Object.entries(inputs)) values[k] = inp.value;
        const url = opts.callback_url || `/api/credentials/${opts.service_name || 'default'}`;
        fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(values),
        }).catch(() => {});
        this.closePopup();
      };
      const cancelBtn = document.createElement('button');
      cancelBtn.className = 'pixel-btn secondary';
      cancelBtn.textContent = 'Cancel';
      cancelBtn.onclick = () => this.closePopup();
      footer.appendChild(cancelBtn);
      footer.appendChild(submitBtn);
    }

    // Custom buttons
    if (opts.buttons && Array.isArray(opts.buttons)) {
      footer.style.display = 'flex';
      for (const btn of opts.buttons) {
        const el = document.createElement('button');
        el.className = btn.primary ? 'pixel-btn' : 'pixel-btn secondary';
        el.textContent = btn.label || 'OK';
        if (btn.url) {
          el.onclick = () => window.open(btn.url, '_blank');
        } else if (btn.close) {
          el.onclick = () => this.closePopup();
        } else if (btn.callback_url) {
          el.onclick = () => {
            fetch(btn.callback_url, { method: 'POST' }).catch(() => {});
            this.closePopup();
          };
        }
        footer.appendChild(el);
      }
    }

    modal.classList.remove('hidden');
  }

  closePopup() {
    document.getElementById('generic-popup-modal').classList.add('hidden');
  }

  loadWorkflowList() {
    fetch('/api/workflows')
      .then(r => r.json())
      .then(data => {
        const list = document.getElementById('workflow-list');
        list.innerHTML = '';
        for (const wf of (data.workflows || [])) {
          const item = document.createElement('div');
          item.className = 'workflow-item';
          item.textContent = wf.name;
          item.addEventListener('click', () => this.loadWorkflow(wf.name));
          list.appendChild(item);
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Failed to load workflows: ${err.message}`, 'system'));
  }

  loadWorkflow(name) {
    document.getElementById('workflow-placeholder').classList.add('hidden');
    fetch(`/api/workflows/${encodeURIComponent(name)}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', data.error, 'system');
          return;
        }
        this.currentWorkflowName = name;
        this._currentWorkflowRaw = data.content;
        // Show rendered markdown view (default)
        const rendered = document.getElementById('workflow-rendered');
        rendered.innerHTML = '<div class="md-rendered">' + this._renderMarkdown(data.content) + '</div>';
        rendered.classList.remove('hidden');
        document.getElementById('workflow-content').classList.add('hidden');
        document.getElementById('workflow-placeholder').classList.add('hidden');
        document.getElementById('workflow-edit-btn').disabled = false;
        document.getElementById('workflow-save-btn').classList.add('hidden');

        // Highlight active item
        document.querySelectorAll('.workflow-item').forEach(el => {
          el.classList.toggle('active', el.textContent === name);
        });
      })
      .catch(err => this.logEntry('SYSTEM', `Load failed: ${err.message}`, 'system'));
  }

  toggleWorkflowEdit() {
    const textarea = document.getElementById('workflow-content');
    const rendered = document.getElementById('workflow-rendered');
    const editBtn = document.getElementById('workflow-edit-btn');
    const saveBtn = document.getElementById('workflow-save-btn');

    if (textarea.classList.contains('hidden')) {
      // Switch to edit mode
      textarea.value = this._currentWorkflowRaw;
      textarea.classList.remove('hidden');
      rendered.classList.add('hidden');
      editBtn.textContent = '👁 Preview';
      saveBtn.classList.remove('hidden');
      saveBtn.disabled = false;
    } else {
      // Switch back to rendered view
      this._currentWorkflowRaw = textarea.value;
      rendered.innerHTML = '<div class="md-rendered">' + this._renderMarkdown(textarea.value) + '</div>';
      textarea.classList.add('hidden');
      rendered.classList.remove('hidden');
      editBtn.textContent = '✎ Edit';
      saveBtn.classList.add('hidden');
    }
  }

  saveWorkflow() {
    if (!this.currentWorkflowName) return;
    const content = document.getElementById('workflow-content').value;
    fetch(`/api/workflows/${encodeURIComponent(this.currentWorkflowName)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Save failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `📋 Workflow updated: ${this.currentWorkflowName}`, 'ceo');
          // Switch back to rendered view after save
          this._currentWorkflowRaw = content;
          const rendered = document.getElementById('workflow-rendered');
          rendered.innerHTML = '<div class="md-rendered">' + this._renderMarkdown(content) + '</div>';
          document.getElementById('workflow-content').classList.add('hidden');
          rendered.classList.remove('hidden');
          document.getElementById('workflow-edit-btn').textContent = '✎ Edit';
          document.getElementById('workflow-save-btn').classList.add('hidden');
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Save failed: ${err.message}`, 'system'));
  }

  // ===== Meeting Room Zoom =====
  openMeetingRoom(room) {
    this.viewingRoomId = room.id;
    const modal = document.getElementById('meeting-modal');
    modal.classList.remove('hidden');

    // Title
    document.getElementById('meeting-modal-title').textContent = `🏢 ${room.name}`;

    // Status
    const led = document.getElementById('meeting-modal-status-led');
    const statusText = document.getElementById('meeting-modal-status-text');
    if (room.is_booked) {
      led.className = 'status-led booked';
      statusText.textContent = 'In Meeting';
    } else {
      led.className = 'status-led free';
      statusText.textContent = 'Available';
    }

    // Capacity
    document.getElementById('meeting-capacity').textContent = `${room.capacity} people`;

    // Participants
    const partEl = document.getElementById('meeting-participants');
    if (room.is_booked && room.participants && room.participants.length > 0) {
      const ROLE_COLORS = { hr: '#4488ff', coo: '#ff8844', ceo: '#ffd700' };
      partEl.innerHTML = room.participants.map(pid => {
        const color = ROLE_COLORS[pid] || '#00ff88';
        const emp = (window.officeRenderer?.state?.employees || []).find(e => e.id === pid);
        const label = emp ? `${emp.nickname || emp.name} (${emp.role})` : pid;
        return `<div class="meeting-participant">
          <span class="meeting-participant-dot" style="background:${color}"></span>
          <span>${label}</span>
        </div>`;
      }).join('');
    } else {
      partEl.innerHTML = '<div style="color:var(--text-dim)">No participants</div>';
    }

    // Load chat history from API
    const chatEl = document.getElementById('meeting-chat-messages');
    chatEl.innerHTML = '<div class="chat-empty">Loading...</div>';
    fetch(`/api/rooms/${encodeURIComponent(room.id)}/chat`)
      .then(r => r.json())
      .then(messages => {
        chatEl.innerHTML = '';
        if (!messages || messages.length === 0) {
          chatEl.innerHTML = '<div class="chat-empty">No meeting logs</div>';
        } else {
          for (const msg of messages) {
            this._appendChatMessage(msg);
          }
        }
      })
      .catch(() => {
        chatEl.innerHTML = '<div class="chat-empty">Failed to load chat</div>';
      });
  }

  _refreshMeetingModalStatus(room) {
    const led = document.getElementById('meeting-modal-status-led');
    const statusText = document.getElementById('meeting-modal-status-text');
    if (room.is_booked) {
      led.className = 'status-led booked';
      statusText.textContent = 'In Meeting';
    } else {
      led.className = 'status-led free';
      statusText.textContent = 'Available';
    }
    // Update participants
    const partEl = document.getElementById('meeting-participants');
    if (room.is_booked && room.participants && room.participants.length > 0) {
      const ROLE_COLORS = { hr: '#4488ff', coo: '#ff8844', ceo: '#ffd700' };
      partEl.innerHTML = room.participants.map(pid => {
        const color = ROLE_COLORS[pid] || '#00ff88';
        const emp = (window.officeRenderer?.state?.employees || []).find(e => e.id === pid);
        const label = emp ? `${emp.nickname || emp.name} (${emp.role})` : pid;
        return `<div class="meeting-participant">
          <span class="meeting-participant-dot" style="background:${color}"></span>
          <span>${label}</span>
        </div>`;
      }).join('');
    } else {
      partEl.innerHTML = '<div style="color:var(--text-dim)">No participants</div>';
    }
  }

  closeMeetingRoom() {
    // If inquiry is active in this room, end it
    if (this._inquirySessionId && this._inquiryRoomId === this.viewingRoomId) {
      this._endInquirySession();
    }
    // Hide inquiry UI elements
    document.getElementById('meeting-inquiry-input-area').classList.add('hidden');
    document.getElementById('meeting-inquiry-typing').classList.add('hidden');
    document.getElementById('meeting-inquiry-actions').classList.add('hidden');
    this.viewingRoomId = null;
    document.getElementById('meeting-modal').classList.add('hidden');
  }

  _appendChatMessage(entry) {
    const chatEl = document.getElementById('meeting-chat-messages');
    // Remove empty placeholder if present
    const empty = chatEl.querySelector('.chat-empty');
    if (empty) empty.remove();

    const roleClass = {
      'HR': 'role-hr', 'COO': 'role-coo', 'CEO': 'role-ceo',
    }[entry.role] || 'role-employee';

    const div = document.createElement('div');
    div.className = `chat-msg ${roleClass}`;
    div.innerHTML = `<span class="chat-time">[${entry.time}]</span> <span class="chat-speaker">${entry.speaker}:</span> ${entry.message}`;
    chatEl.appendChild(div);
    // Auto-scroll to bottom
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  // ===== Inquiry Session =====
  async _startInquiryMode(payload) {
    this._inquirySessionId = payload.session_id;
    this._inquiryRoomId = payload.room_id;

    // Fetch the room from API
    try {
      const rooms = await fetch('/api/rooms').then(r => r.json());
      const room = rooms.find(r => r.id === payload.room_id);
      if (room) {
        this.openMeetingRoom(room);
      }
    } catch (e) {
      console.error('Failed to fetch rooms for inquiry:', e);
    }

    // Show inquiry input area and actions
    document.getElementById('meeting-inquiry-input-area').classList.remove('hidden');
    document.getElementById('meeting-inquiry-actions').classList.remove('hidden');
    document.getElementById('meeting-inquiry-input').focus();
  }

  _sendInquiryMessage() {
    const input = document.getElementById('meeting-inquiry-input');
    const message = input.value.trim();
    if (!message || !this._inquirySessionId) return;

    input.value = '';
    const sendBtn = document.getElementById('meeting-inquiry-send-btn');
    sendBtn.disabled = true;

    // Show typing indicator
    document.getElementById('meeting-inquiry-typing').classList.remove('hidden');

    fetch('/api/inquiry/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: this._inquirySessionId, message }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Inquiry error: ${data.error}`, 'system');
        }
        // Chat messages arrive via WebSocket meeting_chat events
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Inquiry failed: ${err.message}`, 'system');
      })
      .finally(() => {
        sendBtn.disabled = false;
        document.getElementById('meeting-inquiry-typing').classList.add('hidden');
        input.focus();
      });
  }

  _endInquirySession() {
    if (!this._inquirySessionId) return;

    const endBtn = document.getElementById('meeting-inquiry-end-btn');
    endBtn.disabled = true;

    fetch('/api/inquiry/end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: this._inquirySessionId }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `End inquiry error: ${data.error}`, 'system');
        }
      })
      .catch(err => {
        this.logEntry('SYSTEM', `End inquiry failed: ${err.message}`, 'system');
      })
      .finally(() => {
        endBtn.disabled = false;
      });
  }

  _endInquiryMode() {
    this._inquirySessionId = null;
    this._inquiryRoomId = null;
    document.getElementById('meeting-inquiry-input-area').classList.add('hidden');
    document.getElementById('meeting-inquiry-typing').classList.add('hidden');
    document.getElementById('meeting-inquiry-actions').classList.add('hidden');
  }

  // ===== Ex-Employee Wall =====
  openExEmployeeWall() {
    const modal = document.getElementById('ex-employee-modal');
    modal.classList.remove('hidden');
    this.loadExEmployees();
  }

  closeExEmployeeWall() {
    document.getElementById('ex-employee-modal').classList.add('hidden');
  }

  loadExEmployees() {
    const listEl = document.getElementById('ex-employee-list');
    listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">Loading...</div>';

    // Use state data if available, otherwise fetch
    const exEmps = window.officeRenderer?.state?.ex_employees || [];
    if (exEmps.length > 0) {
      this._renderExEmployees(exEmps);
      return;
    }

    fetch('/api/ex-employees')
      .then(r => r.json())
      .then(data => {
        const list = data.ex_employees || [];
        if (list.length === 0) {
          listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">No ex-employees</div>';
          return;
        }
        this._renderExEmployees(list);
      })
      .catch(err => {
        listEl.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;">Load failed: ${err.message}</div>`;
      });
  }

  _renderExEmployees(exEmps) {
    const listEl = document.getElementById('ex-employee-list');
    if (exEmps.length === 0) {
      listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">No ex-employees</div>';
      return;
    }
    listEl.innerHTML = '';
    const ROLE_EMOJI = {
      Engineer: '💻', Designer: '🎨', Analyst: '📊',
      DevOps: '🔧', QA: '🧪', Marketing: '📢', HR: '💼', COO: '⚙️',
    };
    for (const emp of exEmps) {
      const card = document.createElement('div');
      card.className = 'ex-employee-card';
      const emoji = ROLE_EMOJI[emp.role] || '🤖';
      const nn = emp.nickname ? `(${emp.nickname})` : '';
      const skills = (emp.skills || []).slice(0, 3).join(', ');
      card.innerHTML = `
        <div class="ex-emp-info">
          <div class="ex-emp-name">${emoji} ${emp.name} ${nn}</div>
          <div class="ex-emp-role">${emp.title || emp.role} — ${emp.department || ''}</div>
          <div class="ex-emp-skills">${skills}</div>
        </div>
        <button class="pixel-btn small rehire-btn" data-id="${emp.id}">🔄 Rehire</button>
      `;
      card.querySelector('.rehire-btn').addEventListener('click', () => this.rehireEmployee(emp));
      listEl.appendChild(card);
    }
  }

  rehireEmployee(emp) {
    if (!confirm(`Confirm rehire ${emp.name}${emp.nickname ? '(' + emp.nickname + ')' : ''}? Will restart from Lv.1.`)) return;

    fetch(`/api/ex-employees/${encodeURIComponent(emp.id)}/rehire`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Rehire failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `🔄 Rehired: ${data.name}`, 'ceo');
          this.bootstrap();
          this.loadExEmployees(); // Refresh the list
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'));
  }

  // ===== Global API Settings =====
  async _renderApiSettings() {
    const container = document.getElementById('api-settings-content');
    container.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:6px;">Loading...</div>';
    try {
      const [settingsResp, groupsResp] = await Promise.all([
        fetch('/api/settings/api'),
        fetch('/api/auth/providers'),
      ]);
      const settings = await settingsResp.json();
      const groups = await groupsResp.json();
      const tm = settings.talent_market || {};

      let html = '';
      // Dynamic LLM provider cards
      for (const group of groups) {
        const providerId = group.group_id;
        const bodyId = `api-${providerId}-body`;
        // Check if this provider has a key set from settings
        const providerSettings = settings[providerId] || {};
        const isConfigured = providerSettings.api_key_set || false;

        // Anthropic: show Setup Token (OAuth) as primary, API Key as fallback
        const hasSetupToken = group.choices && group.choices.some(c => c.auth_method === 'setup_token' && c.available);
        const oauthSection = hasSetupToken ? `
              <div style="margin-bottom:6px;">
                <label class="api-field-label">Setup Token (Recommended)</label>
                <div class="api-card-actions">
                  <button class="pixel-btn small" onclick="app._startCompanyOAuth()">Authorize with Anthropic</button>
                  <span id="api-${providerId}-oauth-result" class="api-test-result"></span>
                </div>
              </div>
              <div style="border-top:1px solid var(--border);padding-top:4px;margin-top:4px;">
                <label class="api-field-label" style="font-size:5.5px;color:var(--text-dim);">Or use API Key directly</label>
              </div>` : '';

        html += `
          <div class="api-provider-card">
            <div class="api-card-header api-card-toggle" data-target="${bodyId}">
              <span class="api-status-dot ${isConfigured ? 'online' : 'offline'}"></span>
              <span class="api-card-title">${group.label}</span>
              <span class="api-card-hint" style="font-size:5.5px;color:var(--text-dim);margin-left:4px;">${group.hint}</span>
              <span class="api-card-arrow">&#9660;</span>
            </div>
            <div id="${bodyId}" class="api-card-body collapsed">
              ${oauthSection}
              <label class="api-field-label">API Key</label>
              <input type="password" id="api-${providerId}-key" class="api-key-input" placeholder="${isConfigured ? '••••••••' : 'Enter API key...'}" />
              <div class="api-card-actions">
                <button class="pixel-btn small api-test-btn" onclick="app._testProviderKey('${providerId}')">Test</button>
                <button class="pixel-btn small" onclick="app._saveProviderKey('${providerId}')">Save</button>
                <span id="api-${providerId}-result" class="api-test-result"></span>
              </div>
            </div>
          </div>
        `;
      }

      // Talent Market card (unchanged)
      html += `
        <div class="api-provider-card">
          <div class="api-card-header api-card-toggle" data-target="api-tm-body">
            <span class="api-status-dot ${tm.connected ? 'online' : (tm.mode === 'local' ? 'online' : 'offline')}"></span>
            <span class="api-card-title">Talent Market</span>
            <span class="api-card-status">${tm.connected ? '☁️ Cloud' : (tm.local_talent_count > 0 ? '💾 Local (' + tm.local_talent_count + ')' : '⚠️ Not Connected')}</span>
            <span class="api-card-arrow">&#9660;</span>
          </div>
          <div id="api-tm-body" class="api-card-body collapsed">
            <div class="tm-status-info" style="font-size:6.5px;margin-bottom:4px;color:var(--text-dim);">
              ${tm.connected
                ? '✅ Connected to Cloud Talent Market'
                : tm.api_key_set
                  ? '❌ Cloud connection failed, using Local Talent Market'
                  : 'API Key not configured, using Local Talent Market (' + (tm.local_talent_count || 0) + ' talents)'}
            </div>
            <label class="api-field-label">API Key (configure to use cloud service)</label>
            <input type="password" id="api-tm-key" class="api-key-input" placeholder="${tm.api_key_set ? tm.api_key_preview : '(none)'}" />
            <div class="api-card-actions">
              <button class="pixel-btn small" onclick="app._saveApiSettings('talent_market')">Save</button>
              <span id="api-tm-result" class="api-test-result"></span>
            </div>
          </div>
        </div>
      `;

      container.innerHTML = html;
      // Bind toggle for provider cards
      container.querySelectorAll('.api-card-toggle').forEach(hdr => {
        hdr.addEventListener('click', () => {
          const body = document.getElementById(hdr.dataset.target);
          if (body) {
            hdr.classList.toggle('collapsed');
            body.classList.toggle('collapsed');
          }
        });
      });
    } catch (e) {
      container.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;padding:6px;">Error: ${e.message}</div>`;
    }
  }

  async _saveApiSettings(provider) {
    if (provider === 'talent_market') {
      const body = { provider, mode: 'remote' };
      const key = document.getElementById('api-tm-key').value.trim();
      if (key) body.api_key = key;
      try {
        const resp = await fetch('/api/settings/api', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.status === 'updated') {
          this._settingsLoaded = false;
          this._renderApiSettings();
        }
      } catch (e) {
        console.error('Save API settings error:', e);
      }
    }
  }

  async _saveProviderKey(providerId) {
    const keyInput = document.getElementById(`api-${providerId}-key`);
    const resultEl = document.getElementById(`api-${providerId}-result`);
    const apiKey = keyInput ? keyInput.value.trim() : '';
    if (!apiKey) { if (resultEl) { resultEl.textContent = 'No key'; resultEl.className = 'api-test-result fail'; } return; }

    try {
      const resp = await fetch('/api/auth/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: 'company',
          choice: `${providerId}-api-key`,
          api_key: apiKey,
        }),
      });
      const data = await resp.json();
      if (data.status === 'applied') {
        if (resultEl) { resultEl.textContent = 'Saved'; resultEl.className = 'api-test-result success'; }
        this._settingsLoaded = false;
        this._renderApiSettings();
      } else {
        if (resultEl) { resultEl.textContent = data.error || 'Error'; resultEl.className = 'api-test-result fail'; }
      }
    } catch (e) {
      if (resultEl) { resultEl.textContent = 'Error'; resultEl.className = 'api-test-result fail'; }
    }
  }

  async _testProviderKey(providerId) {
    const keyInput = document.getElementById(`api-${providerId}-key`);
    const resultEl = document.getElementById(`api-${providerId}-result`);
    if (resultEl) { resultEl.textContent = '...'; resultEl.className = 'api-test-result'; }

    // Get the API key from input or use existing
    const apiKey = keyInput ? keyInput.value.trim() : '';
    if (!apiKey) {
      if (resultEl) { resultEl.textContent = 'No key'; resultEl.className = 'api-test-result fail'; }
      return;
    }

    try {
      const resp = await fetch('/api/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: providerId,
          api_key: apiKey,
          model: 'test',  // minimal model name for probe
        }),
      });
      const data = await resp.json();
      if (data.ok) {
        if (resultEl) { resultEl.textContent = 'OK'; resultEl.className = 'api-test-result success'; }
      } else {
        if (resultEl) { resultEl.textContent = 'FAIL'; resultEl.className = 'api-test-result fail'; }
      }
    } catch (e) {
      if (resultEl) { resultEl.textContent = 'ERR'; resultEl.className = 'api-test-result fail'; }
    }
  }

  // ===== System Crons Settings =====
  async _renderSystemCrons() {
    const container = document.getElementById('system-crons-content');
    if (!container) return;
    container.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:6px;">Loading...</div>';
    try {
      const resp = await fetch('/api/system/crons');
      const crons = await resp.json();

      if (!crons.length) {
        container.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:6px;">No system crons registered.</div>';
        return;
      }

      let html = '<table class="pixel-table" style="width:100%;font-size:6.5px;"><thead><tr>';
      html += '<th>Name</th><th>Interval</th><th>Description</th><th>Runs</th><th>Status</th><th></th>';
      html += '</tr></thead><tbody>';

      for (const c of crons) {
        const statusDot = c.running
          ? '<span class="api-status-dot online"></span>'
          : '<span class="api-status-dot offline"></span>';
        const btnLabel = c.running ? 'Stop' : 'Start';
        const btnAction = c.running ? 'stop' : 'start';
        html += '<tr>' +
          '<td>' + this._escHtml(c.name) + '</td>' +
          '<td><input type="text" class="cron-interval-input" id="cron-interval-' + c.name + '"' +
          ' value="' + this._escHtml(c.interval) + '" style="width:36px;font-size:6px;text-align:center;" /></td>' +
          '<td>' + this._escHtml(c.description) + '</td>' +
          '<td>' + (c.run_count != null ? c.run_count : '-') + '</td>' +
          '<td>' + statusDot + '</td>' +
          '<td>' +
            '<button class="pixel-btn small" onclick="app._toggleSystemCron(\'' + c.name + '\', \'' + btnAction + '\')">' + btnLabel + '</button> ' +
            '<button class="pixel-btn small" onclick="app._updateCronInterval(\'' + c.name + '\')">Set</button>' +
          '</td>' +
        '</tr>';
      }
      html += '</tbody></table>';
      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = '<div style="color:var(--pixel-red);font-size:7px;padding:6px;">Error: ' + e.message + '</div>';
    }
  }

  async _toggleSystemCron(name, action) {
    try {
      await fetch('/api/system/crons/' + name + '/' + action, { method: 'POST' });
      this._renderSystemCrons();
    } catch (e) {
      console.error('Toggle system cron failed:', e);
    }
  }

  async _updateCronInterval(name) {
    const input = document.getElementById('cron-interval-' + name);
    if (!input) return;
    const interval = input.value.trim();
    if (!interval) return;
    try {
      const resp = await fetch('/api/system/crons/' + name, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interval: interval }),
      });
      const result = await resp.json();
      if (result.status === 'error') {
        alert(result.message);
      } else {
        this._renderSystemCrons();
      }
    } catch (e) {
      console.error('Update cron interval failed:', e);
    }
  }

  async _startCompanyOAuth() {
    try {
      const resp = await fetch('/api/settings/api/oauth/start', { method: 'POST' });
      const data = await resp.json();
      if (data.auth_url) {
        window.open(data.auth_url, 'anthropic_oauth', 'width=600,height=700');
      }
    } catch (e) {
      console.error('Company OAuth start error:', e);
    }
  }

  // ===== Operations Dashboard =====
  openDashboard() {
    const modal = document.getElementById('dashboard-modal');
    modal.classList.remove('hidden');
    this._renderDashboard();
  }

  closeDashboard() {
    document.getElementById('dashboard-modal').classList.add('hidden');
  }

  _renderDashboard() {
    const content = document.getElementById('dashboard-content');
    const state = window.officeRenderer?.state;
    if (!state) {
      content.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">No data</div>';
      return;
    }

    const employees = state.employees || [];
    const exEmployees = state.ex_employees || [];
    const tools = state.tools || [];
    const rooms = state.meeting_rooms || [];
    const tasks = state.active_tasks || [];
    const freeRooms = rooms.filter(r => !r.is_booked).length;

    // Calculate stats
    const workingCount = employees.filter(e => e.status === 'working').length;
    const idleCount = employees.filter(e => e.status === 'idle').length;
    const meetingCount = employees.filter(e => e.status === 'in_meeting').length;

    // Department breakdown
    const depts = {};
    for (const e of employees) {
      const d = e.department || 'Unassigned';
      depts[d] = (depts[d] || 0) + 1;
    }

    // Performance distribution
    let perf375 = 0, perf350 = 0, perf325 = 0;
    for (const e of employees) {
      const hist = e.performance_history || [];
      if (hist.length > 0) {
        const latest = hist[hist.length - 1].score;
        if (latest === 3.75) perf375++;
        else if (latest === 3.5) perf350++;
        else perf325++;
      }
    }

    content.innerHTML = `
      <div class="dash-section">
        <div class="dash-title">Staff Overview</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num">${employees.length}</span><span class="dash-label">Active</span></div>
          <div class="dash-stat"><span class="dash-num">${exEmployees.length}</span><span class="dash-label">Departed</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-green);">${workingCount}</span><span class="dash-label">Working</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-gray);">${idleCount}</span><span class="dash-label">Idle</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-cyan);">${meetingCount}</span><span class="dash-label">In Meeting</span></div>
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">Equipment & Meeting Rooms</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num">${tools.length}</span><span class="dash-label">Tools</span></div>
          <div class="dash-stat"><span class="dash-num">${rooms.length}</span><span class="dash-label">Rooms</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-green);">${freeRooms}</span><span class="dash-label">Available</span></div>
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">Task Status</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num">${tasks.filter(t => t.status === 'running').length}</span><span class="dash-label">Running</span></div>
          <div class="dash-stat"><span class="dash-num">${tasks.filter(t => t.status === 'queued').length}</span><span class="dash-label">Queued</span></div>
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">Dept Distribution</div>
        <div class="dash-dept-list">
          ${Object.entries(depts).map(([d, c]) => `<div class="dash-dept-item"><span>${d}</span><span>${c}</span></div>`).join('')}
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">Performance Distribution</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-green);">${perf375}</span><span class="dash-label">3.75 Excellent</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-yellow);">${perf350}</span><span class="dash-label">3.5 Qualified</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-red);">${perf325}</span><span class="dash-label">3.25 Needs Improvement</span></div>
        </div>
      </div>
    `;

    // Fetch cost data asynchronously and append
    fetch('/api/dashboard/costs').then(r => r.json()).then(data => {
      let costHtml = '';
      // Section 1: Grand total (project + overhead combined)
      const t = data.total || {};
      const grandTotal = data.grand_total_usd || 0;
      const oh = data.overhead || {};
      const projectCost = t.cost_usd || 0;
      const overheadCost = oh.total_cost_usd || 0;
      costHtml += `
        <div class="dash-section">
          <div class="dash-title">\u{1F4B0} Cost Overview</div>
          <div class="dash-stats">
            <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-yellow);">$${grandTotal.toFixed(3)}</span><span class="dash-label">Grand Total</span></div>
            <div class="dash-stat"><span class="dash-num">$${projectCost.toFixed(3)}</span><span class="dash-label">Projects</span></div>
            <div class="dash-stat"><span class="dash-num">$${overheadCost.toFixed(3)}</span><span class="dash-label">Overhead</span></div>
          </div>
          <div class="dash-stats" style="margin-top:4px;">
            <div class="dash-stat"><span class="dash-num">${((t.total_tokens || 0) / 1000).toFixed(1)}k</span><span class="dash-label">Project Tokens</span></div>
            <div class="dash-stat"><span class="dash-num">${(((oh.total_input_tokens || 0) + (oh.total_output_tokens || 0)) / 1000).toFixed(1)}k</span><span class="dash-label">Overhead Tokens</span></div>
          </div>
        </div>`;

      // Section 2: Overhead by category
      const cats = oh.by_category || {};
      if (Object.keys(cats).length) {
        const catLabels = {qa:'CEO Q&A', inquiry:'Inquiry', oneonone:'1-on-1', meeting:'Meeting', routine:'Routine', interview:'Interview', agent_task:'Agent Task', history_compress:'History Compress', completion_check:'Completion Check', nickname_gen:'Nickname Gen', remote_worker:'Remote Worker'};
        costHtml += `
          <div class="dash-section">
            <div class="dash-title">\u{1F4B0} Overhead by Category</div>
            <table class="dash-cost-table">
              <tr><th>Category</th><th>USD</th><th>In Tokens</th><th>Out Tokens</th></tr>
              ${Object.entries(cats).sort((a,b) => b[1].cost_usd - a[1].cost_usd).map(([c, v]) =>
                `<tr><td>${catLabels[c] || c}</td><td>$${v.cost_usd.toFixed(3)}</td><td>${(v.input_tokens/1000).toFixed(1)}k</td><td>${(v.output_tokens/1000).toFixed(1)}k</td></tr>`
              ).join('')}
            </table>
          </div>`;
      }

      // Section 3: Per-department costs
      const deptCosts = data.by_department || {};
      if (Object.keys(deptCosts).length) {
        costHtml += `
          <div class="dash-section">
            <div class="dash-title">\u{1F4B0} Cost by Department</div>
            <table class="dash-cost-table">
              <tr><th>Department</th><th>USD</th><th>Tokens</th></tr>
              ${Object.entries(deptCosts).map(([d, v]) =>
                `<tr><td>${d}</td><td>$${v.cost_usd.toFixed(3)}</td><td>${(v.total_tokens/1000).toFixed(1)}k</td></tr>`
              ).join('')}
            </table>
          </div>`;
      }

      // Section 4: Recent 10 projects costs
      const projects = data.recent_projects || [];
      if (projects.length) {
        costHtml += `
          <div class="dash-section">
            <div class="dash-title">\u{1F4B0} Recent Projects Cost</div>
            <table class="dash-cost-table">
              <tr><th>Project</th><th>USD</th><th>Tokens</th><th>Status</th></tr>
              ${projects.map(p =>
                `<tr><td title="${p.project_id}">${p.task || p.project_id}</td><td>$${(p.cost_usd||0).toFixed(3)}</td><td>${((p.total_tokens||0)/1000).toFixed(1)}k</td><td>${p.status}</td></tr>`
              ).join('')}
            </table>
          </div>`;
      }

      content.insertAdjacentHTML('beforeend', costHtml);
    }).catch(() => {});
  }

  // ===== Company Culture =====
  openCompanyCulture() {
    document.getElementById('company-culture-modal').classList.remove('hidden');
    this._renderCompanyCulture();
  }

  closeCompanyCulture() {
    document.getElementById('company-culture-modal').classList.add('hidden');
  }

  _renderCompanyCulture() {
    const list = document.getElementById('company-culture-list');
    list.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:12px;">Loading...</div>';
    fetch('/api/company-culture')
      .then(r => r.json())
      .then(data => {
        const items = data.items || data || [];
        if (!items.length) {
          list.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:12px;">No culture entries yet. CEO can add above.</div>';
          return;
        }
        list.innerHTML = items.map((item, idx) => {
          const date = item.created_at ? new Date(item.created_at).toLocaleDateString('zh-CN') : '';
          return `
            <div class="company-culture-card">
              <div class="company-culture-card-num">${idx + 1}</div>
              <div class="company-culture-card-content">${this._escapeHtml(item.content)}</div>
              <div class="company-culture-card-meta">
                <span class="company-culture-card-date">${date}</span>
                <button class="company-culture-delete-btn" data-index="${idx}" title="Delete">✕</button>
              </div>
            </div>`;
        }).join('');
        // Bind delete buttons
        list.querySelectorAll('.company-culture-delete-btn').forEach(btn => {
          btn.addEventListener('click', () => this.removeCultureItem(parseInt(btn.dataset.index)));
        });
      })
      .catch(() => {
        list.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:12px;">Failed to load culture.</div>';
      });
  }

  addCultureItem() {
    const input = document.getElementById('company-culture-input');
    const content = input.value.trim();
    if (!content) return;

    const btn = document.getElementById('company-culture-add-btn');
    btn.disabled = true;

    fetch('/api/company-culture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Add failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `Company culture added: ${content.slice(0, 40)}`, 'ceo');
          input.value = '';
          // State will be refreshed via WebSocket push
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'))
      .finally(() => { btn.disabled = false; });
  }

  removeCultureItem(index) {
    fetch(`/api/company-culture/${index}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Delete failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `Company culture removed: ${data.removed?.content?.slice(0, 40) || ''}`, 'ceo');
          // State will be refreshed via WebSocket push
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'));
  }

  // ===== Company Direction =====
  openCompanyDirection() {
    const modal = document.getElementById('company-direction-modal');
    const input = document.getElementById('company-direction-input');
    modal.classList.remove('hidden');
    // Load current direction
    fetch('/api/company/direction')
      .then(r => r.json())
      .then(data => {
        input.value = data.direction || '';
        this._renderCurrentDirection(data.direction || '');
      })
      .catch(() => { input.value = ''; });
  }

  closeCompanyDirection() {
    document.getElementById('company-direction-modal').classList.add('hidden');
  }

  saveCompanyDirection() {
    const input = document.getElementById('company-direction-input');
    const direction = input.value.trim();
    const btn = document.getElementById('company-direction-save-btn');
    btn.disabled = true;

    fetch('/api/company/direction', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Save failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `Company direction updated`, 'ceo');
          this._renderCurrentDirection(direction);
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'))
      .finally(() => { btn.disabled = false; });
  }

  enrichCompanyDirection() {
    const input = document.getElementById('company-direction-input');
    const draft = input.value.trim();
    const btn = document.getElementById('company-direction-enrich-btn');
    if (!draft) {
      this.logEntry('SYSTEM', 'Please write a draft direction first.', 'system');
      return;
    }
    btn.disabled = true;
    btn.textContent = '⏳ Sending...';

    const task = `The CEO has drafted a company direction statement. Please polish and expand it into a complete corporate positioning description, preserving the core message while adding strategic vision, target market, core competencies, and other dimensions. Once polished, use the save_company_direction tool to save.\n\nDraft content:\n${draft}`;

    fetch('/api/ceo/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Enrich failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `Direction polish task sent to EA`, 'ceo');
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'))
      .finally(() => {
        btn.disabled = false;
        btn.innerHTML = '&#10024; Polish / Enrich';
      });
  }

  _renderCurrentDirection(text) {
    const el = document.getElementById('company-direction-current');
    if (!text) {
      el.style.display = 'none';
      return;
    }
    el.style.display = 'block';
    el.textContent = text;
  }

  // ===== CEO Task File Upload =====
  _handleTaskFileSelect(files) {
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = (e) => {
        let type = 'file';
        if (file.type.startsWith('image/')) type = 'image';
        else if (file.type.startsWith('video/')) type = 'video';
        this._taskPendingFiles.push({
          name: file.name,
          type,
          dataUrl: e.target.result,
          file: file,
        });
        this._updateTaskPreviewBar();
      };
      reader.readAsDataURL(file);
    }
  }

  _updateTaskPreviewBar() {
    const bar = document.getElementById('task-preview-bar');
    if (!this._taskPendingFiles.length) {
      bar.classList.add('hidden');
      bar.innerHTML = '';
      return;
    }
    bar.classList.remove('hidden');
    bar.innerHTML = '';
    this._taskPendingFiles.forEach((f, idx) => {
      const item = document.createElement('div');
      item.className = 'chat-preview-item';
      if (f.type === 'image') {
        item.innerHTML = `<img class="chat-preview-thumb" src="${f.dataUrl}" alt="${f.name}" />`;
      } else if (f.type === 'video') {
        item.innerHTML = `<div class="chat-preview-file">🎬<br>${f.name.substring(0, 8)}</div>`;
      } else {
        item.innerHTML = `<div class="chat-preview-file">📄<br>${f.name.substring(0, 8)}</div>`;
      }
      const removeBtn = document.createElement('button');
      removeBtn.className = 'chat-preview-remove';
      removeBtn.textContent = '×';
      removeBtn.onclick = () => {
        this._taskPendingFiles.splice(idx, 1);
        this._updateTaskPreviewBar();
      };
      item.appendChild(removeBtn);
      bar.appendChild(item);
    });
  }

  async _uploadTaskFiles() {
    if (!this._taskPendingFiles.length) return [];
    const uploaded = [];
    for (const f of this._taskPendingFiles) {
      const formData = new FormData();
      formData.append('file', f.file);
      try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        uploaded.push({
          path: data.path,
          filename: data.filename,
          type: f.type,
          content_type: data.content_type || '',
        });
      } catch (err) {
        console.error('Task file upload failed:', err);
      }
    }
    this._taskPendingFiles = [];
    this._updateTaskPreviewBar();
    return uploaded;
  }

  // ===== 1-on-1 File Upload =====
  _handleOneononeFileSelect(files) {
    if (!this._oneononePendingFiles) this._oneononePendingFiles = [];
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = (e) => {
        let type = 'file';
        if (file.type.startsWith('image/')) type = 'image';
        else if (file.type.startsWith('video/')) type = 'video';
        this._oneononePendingFiles.push({
          name: file.name,
          type,
          dataUrl: e.target.result,
          file: file,
        });
        this._updateOneononePreviewBar();
      };
      reader.readAsDataURL(file);
    }
  }

  _updateOneononePreviewBar() {
    const bar = document.getElementById('oneonone-preview-bar');
    if (!this._oneononePendingFiles || !this._oneononePendingFiles.length) {
      bar.classList.add('hidden');
      bar.innerHTML = '';
      return;
    }
    bar.classList.remove('hidden');
    bar.innerHTML = '';
    this._oneononePendingFiles.forEach((f, idx) => {
      const item = document.createElement('div');
      item.className = 'chat-preview-item';
      if (f.type === 'image') {
        item.innerHTML = `<img class="chat-preview-thumb" src="${f.dataUrl}" alt="${f.name}" />`;
      } else {
        item.innerHTML = `<div class="chat-preview-file">📄<br>${f.name.substring(0, 8)}</div>`;
      }
      const removeBtn = document.createElement('button');
      removeBtn.className = 'chat-preview-remove';
      removeBtn.textContent = '×';
      removeBtn.onclick = () => {
        this._oneononePendingFiles.splice(idx, 1);
        this._updateOneononePreviewBar();
      };
      item.appendChild(removeBtn);
      bar.appendChild(item);
    });
  }

  async _uploadOneononeFiles() {
    if (!this._oneononePendingFiles || !this._oneononePendingFiles.length) return [];
    const uploaded = [];
    for (const f of this._oneononePendingFiles) {
      const formData = new FormData();
      formData.append('file', f.file);
      try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        uploaded.push({
          path: data.path,
          filename: data.filename,
          type: f.type,
          content_type: data.content_type || '',
        });
      } catch (err) {
        console.error('Upload failed:', err);
      }
    }
    this._oneononePendingFiles = [];
    this._updateOneononePreviewBar();
    return uploaded;
  }

  // ===== Tool Detail — Dynamic Section Renderer Framework =====
  //
  // Each tool's definition returns a `sections` array from the backend.
  // Sections are typed objects: { type: "oauth"|"env_vars"|"info"|"files"|..., ...data }
  // The frontend renderer registry maps type → render function.
  // To add a new section type: add one entry to _toolSectionRenderers.

  /** Section renderer registry — type → (toolId, section, escHtml) → HTML string */
  _toolSectionRenderers = {
    /** OAuth login/credentials section */
    oauth: (toolId, s, esc) => {
      const title = s.title || 'OAuth';
      const credsFormId = `tool-oauth-creds-${toolId.replace(/\W/g, '')}`;
      // Help text for obtaining credentials
      const redirectHint = s.redirect_uri ? `<div style="margin-bottom:4px;font-size:6px;color:#ccc;">Redirect URI: <code style="user-select:all;color:#f0c040;">${esc(s.redirect_uri)}</code></div>` : '';
      const helpHtml = s.credentials_help_text ? `
        <div style="margin-bottom:4px;font-size:6px;color:#aaa;">
          ${esc(s.credentials_help_text)}${s.credentials_help_url ? ` <a href="${esc(s.credentials_help_url)}" target="_blank" rel="noopener" style="color:#6af;">Get credentials &rarr;</a>` : ''}
        </div>${redirectHint}` : redirectHint;
      // Credentials form (shared across states — collapsible when already configured)
      const credsForm = `
        ${helpHtml}
        <div id="${credsFormId}" class="tool-oauth-creds-form" ${s.has_credentials ? 'style="display:none;"' : ''}>
          <div style="margin-bottom:4px;color:#888;font-size:6px;">
            <code>${esc(s.client_id_env)}</code> / <code>${esc(s.client_secret_env)}</code>
          </div>
          <input type="text" id="tool-oauth-client-id" placeholder="Client ID" class="tool-oauth-input" />
          <input type="password" id="tool-oauth-client-secret" placeholder="Client Secret" class="tool-oauth-input" />
          <button class="pixel-btn small" onclick="window.app._toolAction('credentials','${esc(toolId)}')">Save</button>
        </div>`;

      if (!s.has_credentials) {
        return `
          <div class="tool-section">
            <div class="tool-section-title">${esc(title)}</div>
            <div class="tool-section-body">
              <div class="tool-oauth-status disconnected">Not configured — credentials required</div>
              ${credsForm}
            </div>
          </div>`;
      }
      const preview = s.client_id_preview ? `<span style="color:#666;font-size:6px;margin-left:6px;">Client ID: ${esc(s.client_id_preview)}</span>` : '';
      const editBtn = `<span class="tool-oauth-edit" onclick="document.getElementById('${credsFormId}').style.display=document.getElementById('${credsFormId}').style.display==='none'?'block':'none'">Edit</span>`;
      if (!s.is_authorized) {
        return `
          <div class="tool-section">
            <div class="tool-section-title">${esc(title)}</div>
            <div class="tool-section-body">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
                <div class="tool-oauth-status disconnected" style="margin:0;">Not connected${preview}</div>
                ${editBtn}
              </div>
              <button class="pixel-btn" onclick="window.app._toolAction('login','${esc(toolId)}')">Login with ${esc(s.service_name)}</button>
              ${credsForm}
            </div>
          </div>`;
      }
      return `
        <div class="tool-section">
          <div class="tool-section-title">${esc(title)}</div>
          <div class="tool-section-body">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
              <div class="tool-oauth-status connected" style="margin:0;">Connected${preview}</div>
              <div>${editBtn} <button class="pixel-btn small" onclick="window.app._toolAction('logout','${esc(toolId)}')">Disconnect</button></div>
            </div>
            ${credsForm}
          </div>
        </div>`;
    },

    /** Environment variable configuration */
    env_vars: (toolId, s, esc) => {
      const title = s.title || 'Environment Variables';
      const helpHtml = s.credentials_help_text ? `
        <div style="margin-bottom:4px;font-size:6px;color:#aaa;">
          ${esc(s.credentials_help_text)}${s.credentials_help_url ? ` <a href="${esc(s.credentials_help_url)}" target="_blank" rel="noopener" style="color:#6af;">Get API key &rarr;</a>` : ''}
        </div>` : '';
      const vars = s.vars || [];
      const inputs = vars.map((v, i) => {
        const inputType = v.secret ? 'password' : 'text';
        const statusDot = v.is_set ? '<span style="color:#4caf50;" title="Set">&#9679;</span>' : '<span style="color:#ff9800;" title="Not set">&#9675;</span>';
        // For secret fields, show placeholder hint; for non-secret, show actual value
        const displayVal = v.secret ? '' : (v.value || '');
        const placeholder = v.secret && v.is_set ? '(configured — enter new value to update)' : (v.placeholder || v.name);
        return `<div style="margin-bottom:3px;">
          <label style="font-size:6px;color:#888;">${statusDot} ${esc(v.label || v.name)}</label>
          <input type="${inputType}" id="tool-env-${i}" placeholder="${esc(placeholder)}"
                 value="${esc(displayVal)}" class="tool-oauth-input" data-env-name="${esc(v.name)}" />
        </div>`;
      }).join('');
      return `
        <div class="tool-section">
          <div class="tool-section-title">${esc(title)}</div>
          <div class="tool-section-body">
            ${helpHtml}
            ${inputs}
            <button class="pixel-btn small" onclick="window.app._toolAction('save_env','${esc(toolId)}')">Save</button>
          </div>
        </div>`;
    },

    /** Read-only info / status display */
    info: (toolId, s, esc) => {
      const title = s.title || 'Info';
      const items = (s.items || []).map(item =>
        `<div class="tool-info-row"><span class="tool-info-label">${esc(item.label)}:</span> <span>${esc(item.value)}</span></div>`
      ).join('');
      return `
        <div class="tool-section">
          <div class="tool-section-title">${esc(title)}</div>
          <div class="tool-section-body">${items || '<span class="empty-hint">No info</span>'}</div>
        </div>`;
    },

    /** Allowed users / access control */
    access: (toolId, s, esc) => {
      const title = s.title || 'Access Control';
      const users = s.allowed_users || [];
      const mode = users.length === 0 && s.open_access ? 'Open to all employees' : `${users.length} employee(s)`;
      const list = users.map(u => `<span class="perm-tag">${esc(u.name || u.id)}</span>`).join(' ');
      return `
        <div class="tool-section">
          <div class="tool-section-title">${esc(title)}</div>
          <div class="tool-section-body">
            <div style="font-size:7px;margin-bottom:4px;">${esc(mode)}</div>
            ${list}
          </div>
        </div>`;
    },

    /** Email templates management */
    templates: (toolId, s, esc) => {
      const templates = s.templates || [];
      if (!templates.length) {
        return `
          <div class="tool-section">
            <div class="tool-section-title">${esc(s.title || 'Templates')}</div>
            <div class="tool-section-body">
              <span class="empty-hint">No templates</span>
              <button class="pixel-btn small" style="margin-top:4px;" onclick="window.app._templateNew('${esc(toolId)}','${esc(s.templates_dir || 'templates')}')">+ New Template</button>
            </div>
          </div>`;
      }
      const items = templates.map(t => `
        <div class="tool-template-item" style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;border-bottom:1px solid #333;">
          <div>
            <span style="font-size:7px;color:#e0e0e0;">${esc(t.name)}</span>
            <span style="font-size:6px;color:#888;margin-left:4px;">${esc(t.description || '')}</span>
          </div>
          <div>
            <button class="pixel-btn small" onclick="window.app._templateOpen('${esc(toolId)}','${esc(t.filename)}')">Edit</button>
            <button class="pixel-btn small" style="color:#f44;" onclick="window.app._templateDelete('${esc(toolId)}','${esc(t.filename)}')">Del</button>
          </div>
        </div>
      `).join('');
      return `
        <div class="tool-section">
          <div class="tool-section-title">${esc(s.title || 'Templates')}</div>
          <div class="tool-section-body">
            ${items}
            <button class="pixel-btn small" style="margin-top:4px;" onclick="window.app._templateNew('${esc(toolId)}','${esc(s.templates_dir || 'templates')}')">+ New Template</button>
          </div>
        </div>`;
    },

    /** File listing */
    files: (toolId, s, esc) => {
      const files = s.files || [];
      if (!files.length) return '';
      return `
        <div class="tool-section">
          <div class="tool-section-title">${s.title || 'Files'}</div>
          <div class="tool-section-body">
            <ul class="tool-file-list">${files.map(f => `<li>${esc(f)}</li>`).join('')}</ul>
          </div>
        </div>`;
    },

    /** Raw YAML definition */
    definition: (toolId, s, esc) => {
      return `
        <div class="tool-section">
          <div class="tool-section-title">${s.title || 'Definition'}</div>
          <div class="tool-section-body">
            <pre class="tool-yaml-content">${esc(s.content || '')}</pre>
          </div>
        </div>`;
    },
  };

  async openToolList() {
    const modal = document.getElementById('tool-list-modal');
    const body = document.getElementById('tool-list-body');
    body.innerHTML = '<span class="empty-hint">Loading...</span>';
    modal.classList.remove('hidden');

    try {
      const tools = await fetch('/api/tools').then(r => r.json());
      if (tools.length === 0) {
        body.innerHTML = '<span class="empty-hint">No tools registered</span>';
      } else {
        body.innerHTML = tools.map(t => `
          <div class="tool-list-item" onclick="window.app.openToolDetail('${this._escapeHtml(t.id)}')">
            ${t.has_icon ? `<img src="/api/tools/${encodeURIComponent(t.id)}/icon" class="tool-list-icon" />` : '<span class="tool-list-no-icon">&#128295;</span>'}
            <div class="tool-list-info">
              <div class="tool-list-name">${this._escapeHtml(t.name)}</div>
              <div class="tool-list-desc">${this._escapeHtml(t.description || '')}</div>
            </div>
          </div>
        `).join('');
      }
    } catch (e) {
      body.innerHTML = '<span class="empty-hint">Failed to load tools</span>';
    }
  }

  async openToolDetail(toolId) {
    const res = await fetch(`/api/tools/${encodeURIComponent(toolId)}/definition`);
    if (!res.ok) return;
    const data = await res.json();
    const body = document.getElementById('tool-list-body');
    const esc = (t) => this._escapeHtml(t);

    // Render all sections dynamically
    const sections = (data.sections || []);
    const sectionsHtml = sections.map(s => {
      const renderer = this._toolSectionRenderers[s.type];
      if (!renderer) return `<div class="tool-section"><div class="tool-section-title">${esc(s.type)}</div><div class="tool-section-body"><span class="empty-hint">Unknown section type: ${esc(s.type)}</span></div></div>`;
      return renderer(toolId, s, esc);
    }).join('');

    body.innerHTML = `
      <button class="btn-back" onclick="window.app.openToolList()">&larr; Back</button>
      <div class="tool-detail">
        <div class="tool-detail-header">
          ${data.has_icon ? `<img src="/api/tools/${encodeURIComponent(toolId)}/icon" class="tool-detail-icon" />` : ''}
          <div>
            <h3>${esc(data.name)}</h3>
            <p>${esc(data.description || '')}</p>
          </div>
        </div>
        ${sectionsHtml}
      </div>
    `;
  }

  /** Unified tool action dispatcher — called from section renderers */
  async _toolAction(action, toolId) {
    const esc = encodeURIComponent(toolId);
    switch (action) {
      case 'login': {
        const res = await fetch(`/api/tools/${esc}/oauth/login`, { method: 'POST' });
        const data = await res.json();
        if (data.auth_url) {
          window.open(data.auth_url, '_blank', 'width=600,height=700');
          setTimeout(() => this.openToolDetail(toolId), 5000);
        } else {
          alert(data.message || 'OAuth login failed');
        }
        break;
      }
      case 'logout': {
        if (!confirm('Disconnect OAuth for this tool?')) return;
        await fetch(`/api/tools/${esc}/oauth/logout`, { method: 'POST' });
        this.openToolDetail(toolId);
        break;
      }
      case 'credentials': {
        const clientId = document.getElementById('tool-oauth-client-id')?.value || '';
        const clientSecret = document.getElementById('tool-oauth-client-secret')?.value || '';
        if (!clientId || !clientSecret) { alert('Both Client ID and Client Secret required'); return; }
        const res = await fetch(`/api/tools/${esc}/oauth/credentials`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
        });
        const data = await res.json();
        if (data.status === 'ok') this.openToolDetail(toolId);
        else alert(data.message || 'Failed');
        break;
      }
      case 'save_env': {
        const inputs = document.querySelectorAll('[data-env-name]');
        const vars = {};
        inputs.forEach(el => { vars[el.dataset.envName] = el.value; });
        const res = await fetch(`/api/tools/${esc}/env`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(vars),
        });
        const data = await res.json();
        if (data.status === 'ok') this.openToolDetail(toolId);
        else alert(data.message || 'Failed');
        break;
      }
    }
  }

  // --- Template management ---

  async _templateOpen(toolId, filename) {
    const esc = encodeURIComponent;
    const res = await fetch(`/api/tools/${esc(toolId)}/templates/${esc(filename)}`);
    if (!res.ok) { alert('Failed to load template'); return; }
    const data = await res.json();
    const body = document.getElementById('tool-list-body');
    const escH = (t) => this._escapeHtml(t);
    body.innerHTML = `
      <button class="btn-back" onclick="window.app.openToolDetail('${escH(toolId)}')">&larr; Back</button>
      <div style="padding:4px;">
        <h3 style="font-size:8px;margin:4px 0;">${escH(filename)}</h3>
        <textarea id="template-editor" style="width:100%;min-height:200px;background:#1a1a2e;color:#e0e0e0;border:1px solid #444;font-family:monospace;font-size:7px;padding:4px;resize:vertical;">${escH(data.content || '')}</textarea>
        <div style="margin-top:4px;display:flex;gap:4px;">
          <button class="pixel-btn" onclick="window.app._templateSave('${escH(toolId)}','${escH(filename)}')">Save</button>
          <button class="pixel-btn small" onclick="window.app.openToolDetail('${escH(toolId)}')">Cancel</button>
        </div>
      </div>`;
  }

  async _templateSave(toolId, filename) {
    const content = document.getElementById('template-editor')?.value || '';
    if (!content.trim()) { alert('Template cannot be empty'); return; }
    const esc = encodeURIComponent;
    const res = await fetch(`/api/tools/${esc(toolId)}/templates/${esc(filename)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const data = await res.json();
    if (data.status === 'ok') this.openToolDetail(toolId);
    else alert(data.message || 'Save failed');
  }

  async _templateDelete(toolId, filename) {
    if (!confirm(`Delete template "${filename}"?`)) return;
    const esc = encodeURIComponent;
    const res = await fetch(`/api/tools/${esc(toolId)}/templates/${esc(filename)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.status === 'ok') this.openToolDetail(toolId);
    else alert(data.message || 'Delete failed');
  }

  _templateNew(toolId, templatesDir) {
    const filename = prompt('Template filename (e.g. my_template.md):');
    if (!filename) return;
    // Open editor with empty content
    const body = document.getElementById('tool-list-body');
    const esc = (t) => this._escapeHtml(t);
    const defaultContent = `---\nname: ${filename.replace(/\.\w+$/, '')}\ndescription: \nvariables: []\n---\n\nSubject: \n\n`;
    body.innerHTML = `
      <button class="btn-back" onclick="window.app.openToolDetail('${esc(toolId)}')">&larr; Back</button>
      <div style="padding:4px;">
        <h3 style="font-size:8px;margin:4px 0;">New: ${esc(filename)}</h3>
        <textarea id="template-editor" style="width:100%;min-height:200px;background:#1a1a2e;color:#e0e0e0;border:1px solid #444;font-family:monospace;font-size:7px;padding:4px;resize:vertical;">${esc(defaultContent)}</textarea>
        <div style="margin-top:4px;display:flex;gap:4px;">
          <button class="pixel-btn" onclick="window.app._templateSave('${esc(toolId)}','${esc(filename)}')">Create</button>
          <button class="pixel-btn small" onclick="window.app.openToolDetail('${esc(toolId)}')">Cancel</button>
        </div>
      </div>`;
  }

  _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Lightweight Markdown → HTML renderer.
   * Handles: headers, bold, italic, inline code, code blocks, lists, links, newlines.
   */
  _renderMarkdown(md) {
    if (!md) return '';
    let html = this._escapeHtml(md);
    // Code blocks (```...```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="md-code-block"><code>$2</code></pre>');
    // Headers (# to ####)
    html = html.replace(/^####\s+(.+)$/gm, '<div class="md-h4">$1</div>');
    html = html.replace(/^###\s+(.+)$/gm, '<div class="md-h3">$1</div>');
    html = html.replace(/^##\s+(.+)$/gm, '<div class="md-h2">$1</div>');
    html = html.replace(/^#\s+(.+)$/gm, '<div class="md-h1">$1</div>');
    // Bold (**text**)
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic (*text*)
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Inline code (`code`)
    html = html.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');
    // Unordered lists (- item)
    html = html.replace(/^(\s*)[-*]\s+(.+)$/gm, '$1<div class="md-li">$2</div>');
    // Ordered lists (1. item)
    html = html.replace(/^\s*\d+\.\s+(.+)$/gm, '<div class="md-li md-oli">$1</div>');
    // Horizontal rule (--- or ***)
    html = html.replace(/^[-*]{3,}$/gm, '<hr class="md-hr">');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    // Clean up double <br> after block elements
    html = html.replace(/<\/div><br>/g, '</div>');
    html = html.replace(/<\/pre><br>/g, '</pre>');
    html = html.replace(/<hr class="md-hr"><br>/g, '<hr class="md-hr">');
    return html;
  }

  async submitTask() {
    const input = document.getElementById('task-input');
    const task = input.value.trim();
    if (!task) return;

    // Read project selector
    const projectSelect = document.getElementById('project-select');
    const projectId = projectSelect ? projectSelect.value : '';

    // Save to input history
    if (this._inputHistory[this._inputHistory.length - 1] !== task) {
      this._inputHistory.push(task);
      if (this._inputHistory.length > 20) this._inputHistory.shift();
      localStorage.setItem('ceo_input_history', JSON.stringify(this._inputHistory));
    }
    this._historyIndex = -1;
    this._historyDraft = '';

    const submitBtn = document.getElementById('submit-btn');
    submitBtn.disabled = true;

    // Upload attached files if any
    let attachments = [];
    if (this._taskPendingFiles.length) {
      attachments = await this._uploadTaskFiles();
    }

    // Q&A mode: lightweight ask, no project creation
    if (projectId === '__qa__') {
      this.logEntry('CEO', task, 'ceo');
      fetch('/api/ceo/qa', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: task, attachments }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            this.logEntry('SYSTEM', `Q&A error: ${data.error}`, 'system');
          } else {
            this.logEntry('AI', data.answer, 'agent');
          }
        })
        .catch(err => {
          this.logEntry('SYSTEM', `Q&A failed: ${err.message}`, 'system');
        })
        .finally(() => {
          setTimeout(() => { submitBtn.disabled = false; }, 2000);
        });
      input.value = '';
      return;
    }

    const reqBody = { task, attachments };
    if (projectId) reqBody.project_id = projectId;

    fetch('/api/ceo/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody),
    })
      .then(r => r.json())
      .then(data => {
        if (data.task_type === 'inquiry') {
          this.logEntry('CEO', `Inquiry started with ${data.agent_role}`, 'ceo');
        } else {
          this.logEntry('CEO', `Task assigned to ${data.routed_to}`, 'ceo');
        }
        // Refresh project selector after submit
        this.loadActiveProjects();
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Submit failed: ${err.message}`, 'system');
      })
      .finally(() => {
        setTimeout(() => { submitBtn.disabled = false; }, 2000);
      });

    input.value = '';
    // Reset project selector
    if (projectSelect) projectSelect.value = '';
  }

  // ===== Projects Panel =====
  updateProjectsPanel() {
    const panel = document.getElementById('projects-panel-list');
    if (!panel) return;
    fetch('/api/projects/named')
      .then(r => r.json())
      .then(data => {
        const projects = data.projects || [];
        if (projects.length === 0) {
          panel.innerHTML = '<div class="task-empty">No projects</div>';
          return;
        }
        panel.innerHTML = '';
        for (const p of projects) {
          const card = document.createElement('div');
          const isActive = p.status === 'active' || p.status === 'in_progress';
          card.className = `project-panel-card ${isActive ? 'active' : 'archived'}`;
          const displayName = p.name || p.task || p.project_id;
          const meta = p.iteration_count != null
            ? `${p.iteration_count} iteration${p.iteration_count !== 1 ? 's' : ''} · ${p.status}`
            : p.status;
          card.innerHTML = `
            <div class="project-panel-name">${this._escHtml(displayName)}</div>
            <div class="project-panel-meta">${meta}</div>
          `;
          card.style.cursor = 'pointer';
          card.addEventListener('click', () => this._openProjectDetail(p.project_id));
          panel.appendChild(card);
        }
      })
      .catch(() => {});
  }

  _openTaskInBoard(projectId, nodeId) {
    // Open project modal and load iteration detail directly (with task tree tab)
    const modal = document.getElementById('project-modal');
    const listEl = document.getElementById('project-list');
    const detailEl = document.getElementById('project-detail');
    const contentEl = document.getElementById('project-detail-content');
    modal.classList.remove('hidden');
    listEl.classList.add('hidden');
    detailEl.classList.remove('hidden');
    // Render directly into contentEl — no split wrapper needed
    contentEl.innerHTML = `<div id="project-iter-detail" style="width:100%;height:100%;overflow-y:auto;">
      <div style="color:var(--text-dim);font-size:6px;">Loading...</div>
    </div>`;
    this._loadIterationDetail(projectId, projectId, nodeId);
  }

  _openProjectDetail(projectId) {
    fetch(`/api/projects/named/${encodeURIComponent(projectId)}`)
      .then(r => r.json())
      .then(proj => {
        if (proj.error) return;
        const modal = document.getElementById('project-modal');
        const listEl = document.getElementById('project-list');
        const detailEl = document.getElementById('project-detail');
        const contentEl = document.getElementById('project-detail-content');
        modal.classList.remove('hidden');
        listEl.classList.add('hidden');
        detailEl.classList.remove('hidden');

        const totalCost = proj.total_cost_usd || 0;
        let headerHtml = `<div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;">
          <span class="project-name-editable" data-project-id="${this._escHtml(projectId)}" title="Click to rename" style="color:var(--pixel-cyan);font-size:8px;cursor:pointer;border-bottom:1px dashed var(--text-dim);">${this._escHtml(proj.name || projectId)}</span>
          <span style="color:var(--text-dim);font-size:6px;">${proj.status}</span>
          ${totalCost > 0 ? `<span style="color:var(--pixel-yellow);font-size:6px;">$${totalCost.toFixed(4)}</span>` : ''}
        </div>`;

        // Build split layout: iteration list (left) + detail (right)
        let iterListHtml = '';
        const iters = proj.iteration_details || [];
        if (iters.length === 0) {
          iterListHtml = '<div style="color:var(--text-dim);font-size:6px;">No iterations yet</div>';
        }
        for (const it of iters) {
          const statusColor = it.status === 'completed' ? 'var(--pixel-green)' : 'var(--pixel-yellow)';
          const iterCost = it.cost_usd ? ` · $${it.cost_usd.toFixed(4)}` : '';
          iterListHtml += `<div class="project-iter-card" data-iter-id="${it.iteration_id}" data-project-id="${projectId}">
            <div style="color:${statusColor};">${it.status === 'completed' ? '\u2705' : '\uD83D\uDD04'} ${it.iteration_id}${iterCost}</div>
            <div style="color:var(--pixel-white);margin-top:2px;">${this._escHtml((it.task || '').substring(0, 60))}</div>
            <div style="color:var(--text-dim);margin-top:1px;">${it.created_at ? it.created_at.substring(0, 16) : ''}</div>
          </div>`;
        }
        if (proj.status === 'active') {
          iterListHtml += `<div style="margin-top:8px;"><button class="pixel-btn secondary" style="font-size:6px;padding:4px 8px;" onclick="window.app._archiveProject('${projectId}')">Archive</button></div>`;
        }

        contentEl.innerHTML = `${headerHtml}
          <div class="project-detail-split">
            <div class="project-iter-list">${iterListHtml}</div>
            <div class="project-iter-detail" id="project-iter-detail">
              <div style="color:var(--text-dim);font-size:6px;padding:12px;">Select an iteration to view details</div>
            </div>
          </div>`;

        // Bind click on iteration cards
        contentEl.querySelectorAll('.project-iter-card').forEach(card => {
          card.addEventListener('click', () => {
            contentEl.querySelectorAll('.project-iter-card').forEach(c => c.classList.remove('active'));
            card.classList.add('active');
            this._loadIterationDetail(card.dataset.projectId, card.dataset.iterId);
          });
        });

        // Bind click-to-edit on project name
        const nameEl = contentEl.querySelector('.project-name-editable');
        if (nameEl) {
          nameEl.addEventListener('click', () => {
            const pid = nameEl.dataset.projectId;
            const current = nameEl.textContent;
            const input = document.createElement('input');
            input.type = 'text';
            input.value = current;
            input.style.cssText = 'font-size:8px;color:var(--pixel-cyan);background:var(--bg-dark);border:1px solid var(--pixel-cyan);padding:1px 4px;width:160px;';
            const save = () => {
              const newName = input.value.trim();
              if (newName && newName !== current) {
                fetch(`/api/projects/${encodeURIComponent(pid)}/name`, {
                  method: 'PATCH',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ name: newName }),
                }).then(r => r.json()).then(d => {
                  if (d.status === 'ok') {
                    nameEl.textContent = newName;
                    this.loadActiveProjects();
                  }
                }).catch(() => {});
              }
              input.replaceWith(nameEl);
            };
            input.addEventListener('blur', save);
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } if (e.key === 'Escape') input.replaceWith(nameEl); });
            nameEl.replaceWith(input);
            input.focus();
            input.select();
          });
        }

        // Auto-select first iteration
        if (iters.length > 0) {
          const firstCard = contentEl.querySelector('.project-iter-card');
          if (firstCard) firstCard.click();
        }
      })
      .catch(() => {});
  }

  _loadIterationDetail(projectId, iterationId) {
    const panel = document.getElementById('project-iter-detail');
    if (!panel) return;
    panel.innerHTML = '<div style="color:var(--text-dim);font-size:6px;">Loading...</div>';

    // Fetch project doc + task tree in parallel
    Promise.all([
      fetch(`/api/projects/${encodeURIComponent(iterationId)}`).then(r => r.json()),
      fetch(`/api/projects/${encodeURIComponent(iterationId)}/tree`).then(r => r.ok ? r.json() : null).catch(() => null),
    ]).then(([doc, treeData]) => {
        if (doc.error) {
          panel.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">${doc.error}</div>`;
          return;
        }

        // Extract task result from tree root node
        let taskResult = '';
        if (treeData && treeData.root_id && treeData.nodes) {
          const rootNode = treeData.nodes.find(n => n.id === treeData.root_id);
          if (rootNode && rootNode.result) {
            taskResult = rootNode.result;
          }
        }

        // Tab bar — "Detail" + "Task Tree" fixed + dynamic plugin tabs
        const plugins = window.pluginLoader.getPlugins();
        let tabBarHtml = `<div class="project-tabs"><button class="project-tab active" data-tab="detail">Detail</button>`;
        tabBarHtml += `<button class="project-tab" data-tab="task-tree">\uD83C\uDF33 Task Tree</button>`;
        for (const p of plugins) {
          tabBarHtml += `<button class="project-tab" data-tab="plugin-${p.id}">${p.icon ? p.icon + ' ' : ''}${p.name}</button>`;
        }
        tabBarHtml += `</div>`;

        // Detail tab content
        let detailHtml = '';
        detailHtml += `<div style="color:var(--pixel-yellow);font-size:7px;margin-bottom:6px;">${this._escHtml(doc.task || '')}</div>`;
        detailHtml += `<div style="font-size:5px;color:var(--text-dim);margin-bottom:8px;">Status: ${doc.status} | Owner: ${doc.current_owner || '-'}</div>`;

        // Acceptance criteria
        const criteria = doc.acceptance_criteria || [];
        if (criteria.length > 0) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:6px 0 3px;">Acceptance Criteria (${criteria.length})</div>`;
          const ar = doc.acceptance_result;
          for (let i = 0; i < criteria.length; i++) {
            const icon = ar ? (ar.accepted ? '\u2705' : '\u274C') : '\u2B1C';
            detailHtml += `<div style="font-size:5px;color:var(--pixel-white);padding:1px 0;">${icon} ${i + 1}. ${this._escHtml(criteria[i])}</div>`;
          }
          if (ar) {
            const arIcon = ar.accepted ? '\u2705' : '\u274C';
            const arLabel = ar.accepted ? 'Passed' : 'Failed';
            const arNotes = ar.notes ? ` — ${this._escHtml(ar.notes.substring(0, 200))}${ar.notes.length > 200 ? '...' : ''}` : '';
            detailHtml += `<div style="font-size:6px;color:${ar.accepted ? 'var(--pixel-green)' : 'var(--pixel-red)'};margin:4px 0;">${arIcon} Acceptance Result: ${arLabel}${arNotes}</div>`;
          }
          const ear = doc.ea_review_result;
          if (ear) {
            const earIcon = ear.approved ? '\u2705' : '\u274C';
            const earLabel = ear.approved ? 'Approved' : 'Rejected';
            const earNotes = ear.notes ? ` — ${this._escHtml(ear.notes.substring(0, 200))}${ear.notes.length > 200 ? '...' : ''}` : '';
            detailHtml += `<div style="font-size:6px;color:${ear.approved ? 'var(--pixel-green)' : 'var(--pixel-red)'};margin:2px 0;">EA Review: ${earIcon} ${earLabel}${earNotes}</div>`;
          }
        }

        if (doc.status !== 'completed') {
          detailHtml += `<div style="margin:8px 0;display:flex;gap:6px;">`;
          detailHtml += `<button class="pixel-btn" id="continue-iter-btn" style="font-size:6px;padding:4px 10px;">\u25B6 Continue Current Iteration</button>`;
          detailHtml += `<button class="pixel-btn" id="stop-iter-btn" style="font-size:6px;padding:4px 10px;background:var(--pixel-red);color:#000;">■ Stop All Tasks</button>`;
          detailHtml += `</div>`;
        }

        // Follow-up button (always available)
        detailHtml += `<div class="task-followup-section">
          <button class="pixel-btn" id="followup-btn" style="font-size:6px;padding:4px 10px;">+ Follow-up Task</button>
          <div id="followup-input-area" class="hidden" style="margin-top:6px;">
            <textarea id="followup-instructions" class="followup-textarea" placeholder="Enter follow-up instructions..." rows="3"></textarea>
            <div style="margin-top:4px;display:flex;gap:4px;">
              <button class="pixel-btn" id="followup-submit" style="font-size:6px;padding:3px 8px;">Send</button>
              <button class="pixel-btn secondary" id="followup-cancel" style="font-size:6px;padding:3px 8px;">Cancel</button>
            </div>
          </div>
        </div>`;

        const files = doc.files || [];
        const fileBaseUrl = `/api/projects/${encodeURIComponent(iterationId)}/files/`;
        detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:6px 0 3px;">Documents (${files.length})</div>`;
        if (files.length > 0) {
          for (const f of files) {
            const ext = f.split('.').pop().toLowerCase();
            const icon = {png:'\uD83D\uDDBC',jpg:'\uD83D\uDDBC',jpeg:'\uD83D\uDDBC',gif:'\uD83D\uDDBC',svg:'\uD83D\uDDBC',pdf:'\uD83D\uDCC3'}[ext] || '\uD83D\uDCC4';
            detailHtml += `<div class="project-file-item" data-file="${this._escHtml(f)}" data-url="${fileBaseUrl}${encodeURIComponent(f)}" data-ext="${ext}" style="font-size:6px;color:var(--pixel-green);padding:3px 2px;border-bottom:1px solid var(--border);cursor:pointer;">${icon} ${this._escHtml(f)}</div>`;
          }
        } else {
          detailHtml += `<div style="font-size:5px;color:var(--text-dim);">No output documents yet</div>`;
        }

        if (taskResult) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Task Report</div>`;
          detailHtml += `<div class="task-result-report md-rendered">${this._renderMarkdown(taskResult)}</div>`;
        } else if (doc.output) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Output</div>`;
          detailHtml += `<div style="font-size:5px;color:var(--pixel-white);background:var(--bg-dark);padding:4px;border:1px solid var(--border);max-height:80px;overflow-y:auto;">${this._escHtml(doc.output).substring(0, 500)}</div>`;
        }

        const timeline = doc.timeline || [];
        detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Log (${timeline.length})</div>`;
        if (timeline.length > 0) {
          detailHtml += `<div style="max-height:120px;overflow-y:auto;">`;
          for (const entry of timeline) {
            const time = (entry.time || '').substring(11, 19);
            detailHtml += `<div style="font-size:5px;line-height:1.6;border-left:2px solid var(--border);padding-left:4px;margin:1px 0;">`;
            detailHtml += `<span style="color:var(--text-dim);">[${time}]</span> `;
            detailHtml += `<span style="color:var(--pixel-green);">${entry.employee_id}</span> `;
            detailHtml += `<span style="color:var(--pixel-yellow);">${entry.action}</span>`;
            if (entry.detail) {
              detailHtml += `<div style="color:var(--pixel-white);margin-top:1px;">${this._escHtml(entry.detail.substring(0, 150))}${entry.detail.length > 150 ? '...' : ''}</div>`;
            }
            detailHtml += `</div>`;
          }
          detailHtml += `</div>`;
        } else {
          detailHtml += `<div style="font-size:5px;color:var(--text-dim);">No log entries</div>`;
        }

        const cost = doc.cost || {};
        detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Cost & Budget</div>`;
        const actual = cost.actual_cost_usd || 0;
        const budget = cost.budget_estimate_usd || 0;
        const tokens = cost.token_usage || {};
        if (actual > 0 || budget > 0) {
          let budgetLine = '';
          if (budget > 0) {
            const pct = ((actual / budget) * 100).toFixed(1);
            const pctColor = pct > 100 ? 'var(--pixel-red)' : 'var(--pixel-green)';
            budgetLine = ` / Budget: $${budget.toFixed(3)} (<span style="color:${pctColor};">${pct}%</span>)`;
          }
          detailHtml += `<div style="font-size:6px;color:var(--pixel-white);margin:2px 0;">Actual: $${actual.toFixed(4)}${budgetLine}</div>`;
          detailHtml += `<div style="font-size:5px;color:var(--text-dim);margin:2px 0;">Tokens: ${(tokens.input||0).toLocaleString()} in / ${(tokens.output||0).toLocaleString()} out</div>`;
          const breakdown = cost.breakdown || [];
          if (breakdown.length > 0) {
            detailHtml += `<table style="font-size:5px;width:100%;border-collapse:collapse;margin-top:3px;">`;
            detailHtml += `<tr style="color:var(--text-dim);"><th style="text-align:left;">Employee</th><th>Model</th><th>Tokens</th><th>Cost</th></tr>`;
            for (const b of breakdown) {
              detailHtml += `<tr><td>${b.employee_id}</td><td>${(b.model||'').split('/').pop()}</td><td>${(b.total_tokens||0).toLocaleString()}</td><td>$${(b.cost_usd||0).toFixed(4)}</td></tr>`;
            }
            detailHtml += `</table>`;
          }
        } else {
          detailHtml += `<div style="font-size:5px;color:var(--text-dim);">No cost data</div>`;
        }

        // Team section
        const team = doc.team || [];
        if (team.length > 0) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Team (${team.length})</div>`;
          detailHtml += `<div class="project-team-list">`;
          for (const m of team) {
            const empId = m.employee_id || '';
            const role = m.role || '';
            detailHtml += `<div class="project-team-member" data-emp-id="${this._escHtml(empId)}">`;
            detailHtml += `<img src="/api/employees/${empId}/avatar" class="project-team-avatar" onerror="this.style.display='none'" />`;
            detailHtml += `<div class="project-team-info">`;
            detailHtml += `<span class="project-team-name">${this._escHtml(empId)}</span>`;
            detailHtml += `<span class="project-team-role">${this._escHtml(role)}</span>`;
            detailHtml += `</div></div>`;
          }
          detailHtml += `</div>`;
        }

        // Build full panel HTML with tabs — detail + task tree + dynamic plugin containers
        let fullHtml = tabBarHtml + `<div class="project-tab-content" data-tab="detail">${detailHtml}</div>`;
        fullHtml += `<div class="project-tab-content" data-tab="task-tree" style="display:none;">
          <div class="project-tree-layout">
            <div id="board-tree-container" class="project-tree-canvas">
              <svg id="board-tree-svg"></svg>
            </div>
            <div id="board-tree-detail" class="project-tree-drawer hidden">
              <div id="tree-detail-content"></div>
            </div>
          </div>
        </div>`;
        for (const p of plugins) {
          fullHtml += `<div class="project-tab-content" data-tab="plugin-${p.id}" style="display:none;"><div style="color:var(--text-dim);font-size:6px;">Loading...</div></div>`;
        }
        panel.innerHTML = fullHtml;

        // Bind tab switching
        panel.querySelectorAll('.project-tab').forEach(tab => {
          tab.addEventListener('click', () => {
            panel.querySelectorAll('.project-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const tabName = tab.dataset.tab;
            panel.querySelectorAll('.project-tab-content').forEach(c => {
              c.style.display = c.dataset.tab === tabName ? '' : 'none';
            });
            if (tabName === 'task-tree') {
              // Lazy-load task tree
              if (!this._treeRenderer) {
                this._treeRenderer = new TaskTreeRenderer('board-tree-container', 'board-tree-detail');
              }
              this._treeRenderer.load(iterationId);
              this._currentTreeProjectId = iterationId;
            } else if (tabName.startsWith('plugin-')) {
              const pluginId = tabName.replace('plugin-', '');
              this._viewingBoardProjectId = projectId;
              const container = panel.querySelector(`.project-tab-content[data-tab="${tabName}"]`);
              if (container) {
                window.pluginLoader.render(pluginId, projectId, container, {escHtml: this._escHtml, projectId});
              }
            } else {
              this._viewingBoardProjectId = null;
            }
          });
        });

        // Bind file click handlers
        panel.querySelectorAll('.project-file-item').forEach(item => {
          item.addEventListener('click', () => {
            this._openProjectFile(item.dataset.file, item.dataset.url, item.dataset.ext);
          });
        });

        // Bind team member click → open employee detail
        panel.querySelectorAll('.project-team-member').forEach(el => {
          el.addEventListener('click', () => {
            const empId = el.dataset.empId;
            const emp = this.employees.find(e => e.id === empId);
            if (emp) this.openEmployeeDetail(emp);
          });
        });

        // Bind continue button
        const continueBtn = document.getElementById('continue-iter-btn');
        if (continueBtn) {
          continueBtn.addEventListener('click', () => {
            this._continueIteration(projectId, iterationId);
          });
        }

        // Bind stop button
        const stopBtn = document.getElementById('stop-iter-btn');
        if (stopBtn) {
          stopBtn.addEventListener('click', () => {
            if (!confirm('Are you sure you want to stop all running tasks in this iteration?')) return;
            stopBtn.disabled = true;
            stopBtn.textContent = '⏳ Stopping...';
            fetch(`/api/task/${encodeURIComponent(iterationId)}/abort`, { method: 'POST' })
              .then(r => r.json())
              .then(data => {
                stopBtn.textContent = `■ Stopped (${data.cancelled || 0})`;
                this._loadIterationDetail(projectId, iterationId);
              })
              .catch(() => { stopBtn.disabled = false; stopBtn.textContent = '■ Stop All Tasks'; });
          });
        }

        // Bind follow-up button
        const followupBtn = document.getElementById('followup-btn');
        const followupArea = document.getElementById('followup-input-area');
        if (followupBtn && followupArea) {
          followupBtn.addEventListener('click', () => {
            followupBtn.classList.add('hidden');
            followupArea.classList.remove('hidden');
            document.getElementById('followup-instructions')?.focus();
          });
          document.getElementById('followup-cancel')?.addEventListener('click', () => {
            followupArea.classList.add('hidden');
            followupBtn.classList.remove('hidden');
          });
          document.getElementById('followup-submit')?.addEventListener('click', () => {
            const textarea = document.getElementById('followup-instructions');
            const text = textarea?.value?.trim();
            if (!text) return;
            this._submitFollowup(iterationId, text);
          });
        }
      })
      .catch(err => {
        panel.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">Load failed: ${err.message}</div>`;
      });
  }

  _continueIteration(projectId, iterationId) {
    const btn = document.getElementById('continue-iter-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Submitting...'; }

    fetch('/api/projects/continue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, iteration_id: iterationId }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('CEO', `Continue failed: ${data.error}`, 'error');
          if (btn) { btn.disabled = false; btn.textContent = '▶ Continue Current Iteration'; }
        } else {
          this.logEntry('CEO', `Continued iteration ${iterationId}, tasks routed to ${data.routed_to}`, 'ceo');
          const modal = document.getElementById('project-modal');
          if (modal) modal.classList.add('hidden');
        }
      })
      .catch(err => {
        this.logEntry('CEO', `Continue failed: ${err.message}`, 'error');
        if (btn) { btn.disabled = false; btn.textContent = '▶ Continue Current Iteration'; }
      });
  }

  _submitFollowup(projectId, instructions) {
    const submitBtn = document.getElementById('followup-submit');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = '⏳ Submitting...'; }

    fetch(`/api/task/${encodeURIComponent(projectId)}/followup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instructions }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('CEO', `Follow-up task failed: ${data.error}`, 'error');
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Send'; }
        } else {
          this.logEntry('CEO', `Follow-up instructions added, tasks routed to EA`, 'ceo');
          const modal = document.getElementById('project-modal');
          if (modal) modal.classList.add('hidden');
        }
      })
      .catch(err => {
        this.logEntry('CEO', `Follow-up task failed: ${err.message}`, 'error');
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Send'; }
      });
  }

  _openProjectFile(filename, url, ext) {
    const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'svg'];
    const textExts = ['txt', 'md', 'py', 'js', 'html', 'css', 'yaml', 'yml', 'json', 'csv',
                      'tsv', 'xml', 'sh', 'toml', 'cfg', 'ini', 'log', 'rst', 'tex', 'sql',
                      'r', 'rb', 'go', 'java', 'c', 'cpp', 'h', 'hpp', 'rs', 'swift', 'kt',
                      'ts', 'tsx', 'jsx'];

    if (imageExts.includes(ext)) {
      // Open image in a simple overlay
      this._showFileViewer(filename, `<img src="${url}" style="max-width:100%;max-height:70vh;" />`);
    } else if (textExts.includes(ext)) {
      // Fetch text content and display
      fetch(url)
        .then(r => r.text())
        .then(text => {
          this._showFileViewer(filename, `<pre style="font-size:6px;color:var(--pixel-white);white-space:pre-wrap;word-break:break-all;max-height:65vh;overflow-y:auto;margin:0;padding:6px;background:var(--bg-dark);border:1px solid var(--border);">${this._escHtml(text)}</pre>`);
        })
        .catch(() => { window.open(url, '_blank'); });
    } else if (ext === 'pdf') {
      window.open(url, '_blank');
    } else {
      // Download other files
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
    }
  }

  _showFileViewer(filename, contentHtml) {
    // Reuse or create a file viewer overlay
    let viewer = document.getElementById('file-viewer-overlay');
    if (!viewer) {
      viewer = document.createElement('div');
      viewer.id = 'file-viewer-overlay';
      viewer.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;';
      viewer.addEventListener('click', (e) => {
        if (e.target === viewer) viewer.style.display = 'none';
      });
      document.body.appendChild(viewer);
    }
    viewer.style.display = 'flex';
    viewer.innerHTML = `
      <div style="max-width:750px;width:100%;background:var(--bg-panel);border:1px solid var(--pixel-cyan);padding:8px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <span style="color:var(--pixel-cyan);font-size:7px;">${this._escHtml(filename)}</span>
          <button id="file-viewer-close-btn" style="background:none;border:1px solid var(--border);color:var(--pixel-white);cursor:pointer;font-size:7px;padding:2px 6px;">\u2715</button>
        </div>
        ${contentHtml}
      </div>`;
    document.getElementById('file-viewer-close-btn').addEventListener('click', () => {
      viewer.style.display = 'none';
    });
  }

  _archiveProject(projectId) {
    fetch(`/api/projects/${encodeURIComponent(projectId)}/archive`, { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'archived') {
          this.logEntry('CEO', `Project "${projectId}" archived`, 'ceo');
          this.updateProjectsPanel();
          this.loadActiveProjects();
          document.getElementById('project-modal').classList.add('hidden');
        }
      })
      .catch(() => {});
  }

  loadActiveProjects() {
    const select = document.getElementById('project-select');
    if (!select) return;
    fetch('/api/projects/named')
      .then(r => r.json())
      .then(data => {
        const projects = (data.projects || []).filter(p => p.status === 'active');
        // Preserve first two static options
        const currentVal = select.value;
        while (select.options.length > 2) select.remove(2);
        for (const p of projects) {
          const opt = document.createElement('option');
          opt.value = p.project_id;
          opt.textContent = `📁 ${p.name} (${p.iteration_count})`;
          select.appendChild(opt);
        }
        // Restore selection if still valid
        if (currentVal && [...select.options].some(o => o.value === currentVal)) {
          select.value = currentVal;
        }
      })
      .catch(() => {});
  }

  // ===== Admin Reload =====
  adminReload() {
    const btn = document.getElementById('reload-toolbar-btn');
    btn.disabled = true;
    btn.title = 'Reloading...';
    fetch('/api/admin/reload', { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        const updated = (data.employees_updated || []).length;
        const added = (data.employees_added || []).length;
        this.logEntry('SYSTEM', `Reloaded: ${updated} updated, ${added} added`, 'system');
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Reload failed: ${err.message}`, 'system');
      })
      .finally(() => {
        btn.disabled = false;
        btn.title = 'Reload Data';
      });
  }

  async openTalentPool() {
    try {
      const resp = await fetch('/api/talent-pool');
      const data = await resp.json();
      this._renderTalentPool(data);
      document.getElementById('talent-pool-modal').classList.remove('hidden');
    } catch (e) {
      console.error('Failed to load talent pool:', e);
    }
  }

  closeTalentPool() {
    document.getElementById('talent-pool-modal').classList.add('hidden');
  }

  _renderTalentPool(data) {
    const badge = document.getElementById('talent-pool-source-badge');
    badge.textContent = data.source === 'api' ? 'API' : 'Local';
    badge.className = 'talent-pool-badge ' + (data.source === 'api' ? 'api' : 'local');

    const list = document.getElementById('talent-pool-list');
    list.innerHTML = '';

    if (!data.talents || data.talents.length === 0) {
      list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px;">No talents available</div>';
      return;
    }

    for (const t of data.talents) {
      const card = document.createElement('div');
      card.className = 'talent-pool-card';
      card.innerHTML = `
        <div class="talent-name">${t.name || t.talent_id}</div>
        <div class="talent-role">${t.role || ''}</div>
        <div class="talent-skills">
          ${(t.skills || []).map(s => `<span class="skill-tag">${s}</span>`).join('')}
        </div>
        <div class="talent-status">${t.status === 'purchased' ? '✓ Purchased' : 'Local'}</div>
      `;
      list.appendChild(card);
    }
  }
}

// Global abort handler for task detail view
window._abortTask = async function(projectId) {
  if (!confirm('Abort this task? All related sub-tasks will be cancelled.')) return;
  try {
    const res = await fetch(`/api/task/${encodeURIComponent(projectId)}/abort`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      window.app.logEntry('CEO', `Task aborted (${data.cancelled} tasks cancelled)`, 'ceo');
      // Close the project modal
      document.getElementById('project-modal').classList.add('hidden');
    }
  } catch (err) {
    console.error('Abort failed:', err);
  }
};

// Global abort handler for individual agent task
window._abortAgentTask = async function(employeeId, taskId) {
  if (!confirm('Cancel this task?')) return;
  try {
    const res = await fetch(`/api/employee/${encodeURIComponent(employeeId)}/task/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      window.app.logEntry('CEO', `Task cancelled for ${employeeId}`, 'ceo');
      // Refresh the task board
      window.app._fetchTaskBoard(employeeId);
    }
  } catch (err) {
    console.error('Cancel failed:', err);
  }
};

// Global file viewer for CEO report workspace files
window._ceoViewFile = async function(url, filename) {
  try {
    const resp = await fetch(url);
    const contentType = resp.headers.get('content-type') || '';

    let overlay = document.getElementById('ceo-file-viewer');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'ceo-file-viewer';
      overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:10001;display:flex;align-items:center;justify-content:center;';
      document.body.appendChild(overlay);
    }

    let bodyHtml;
    if (contentType.startsWith('image/')) {
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      bodyHtml = `<img src="${objUrl}" style="max-width:100%;max-height:60vh;" />`;
    } else {
      const text = await resp.text();
      const escaped = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      bodyHtml = `<pre style="white-space:pre-wrap;word-break:break-all;max-height:60vh;overflow-y:auto;margin:0;font-size:6.5px;line-height:1.4;">${escaped}</pre>`;
    }

    overlay.innerHTML = `
      <div style="background:var(--bg-panel,#1a1a2e);border:2px solid var(--accent,#4fc3f7);border-radius:4px;padding:12px;max-width:700px;width:90%;font-family:var(--font-mono,monospace);color:var(--text,#e0e0e0);">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <span style="font-size:8px;font-weight:bold;color:var(--accent,#4fc3f7);">📄 ${filename.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</span>
          <div>
            <a href="${url}" download="${filename}" style="font-size:6px;color:#4fc3f7;margin-right:8px;text-decoration:underline;">⬇ Download</a>
            <button class="pixel-btn small" id="ceo-file-viewer-close">Close</button>
          </div>
        </div>
        ${bodyHtml}
      </div>
    `;
    overlay.style.display = 'flex';
    document.getElementById('ceo-file-viewer-close').onclick = () => {
      overlay.style.display = 'none';
    };
  } catch (err) {
    console.error('Failed to view file:', err);
  }
};

// Boot
window.app = new AppController();
