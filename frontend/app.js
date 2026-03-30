/**
 * app.js — WebSocket client, CEO console, and activity log controller
 */

const ROLE_EMOJI = {
  Engineer: '💻', Designer: '🎨', Analyst: '📊',
  DevOps: '🔧', QA: '🧪', Marketing: '📢',
  'Game Engineer': '🎮', 'Game Designer': '🎯',
  'Project Manager': '📋', Manager: '📋',
  HR: '💼', COO: '⚙️',
};

class AppController {
  constructor() {
    this.ws = null;
    this.reconnectDelay = 1000;
    this.viewingRoomId = null;
    this.viewingEmployeeId = null;
    // Meeting agenda cache per room (room_id → agenda data)
    this._meetingAgendaCache = {};
    // Dashboard cost auto-refresh timer
    this._dashboardCostTimer = null;
    // Input history (up/down arrow)
    this._inputHistory = JSON.parse(localStorage.getItem('ceo_input_history') || '[]');
    this._historyIndex = -1;
    this._historyDraft = '';
    // Task attachment files
    this._taskPendingFiles = [];
    // Cooldown: prevent accidental double-submit (key → timestamp)
    this._actionCooldowns = {};
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
      const t0 = performance.now();
      // Single API call replaces 6 parallel fetches
      const data = await fetch('/api/bootstrap').then(r => r.json());
      const { employees, tasks, rooms, tools, activity_log, version, office_layout } = data;
      console.debug(`[bootstrap] /api/bootstrap took ${(performance.now() - t0).toFixed(0)}ms`);

      this.updateRoster(employees);
      this.updateOneononeDropdown(employees);
      this.updateProjectsPanel();
      if (window.officeRenderer) {
        window.officeRenderer.updateState({
          employees, meeting_rooms: rooms, tools, office_layout,
        });
      }
      // Show version
      if (version) {
        document.getElementById('app-version').textContent = `v${version}`;
      }
      // Update counters
      document.getElementById('employee-count').textContent = `👥 ${employees.length}`;
      document.getElementById('tool-count').textContent = `🔧 ${tools.length}`;
      const freeRooms = rooms.filter(r => !r.is_booked).length;
      document.getElementById('room-count').textContent = `🏢 ${freeRooms}/${rooms.length}`;
      // Restore meeting agenda cache from room data (survives page refresh)
      for (const room of rooms) {
        if (room.agenda && room.agenda.items && room.agenda.items.length > 0) {
          this._meetingAgendaCache[room.id] = room.agenda;
        }
      }
      // Refresh meeting modal if open
      if (this.viewingRoomId) {
        const room = rooms.find(r => r.id === this.viewingRoomId);
        if (room) this._refreshMeetingModalStatus(room);
      }
      // Render historical activity log entries
      if (activity_log && activity_log.length > 0) {
        this._renderHistoricalActivityLog(activity_log);
      }
      // Restore pending candidate shortlist modal if HR submitted candidates
      this._restorePendingCandidates();
      // Restore onboarding progress modal if there's an active onboarding
      this._restoreOnboardingProgress();

      // Lazy-load full task tree summaries in background (non-blocking)
      this._lazyLoadTaskTrees();
    } catch (e) {
      console.error('Bootstrap failed:', e);
    }
  }

  async _lazyLoadTaskTrees() {
    // After initial render, fetch task queue to overlay progress on project cards
    try {
      this.updateProjectsPanel();
    } catch (e) {
      console.debug('[bootstrap] lazy tree load failed:', e);
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
    try {
      const stateData = await fetch('/api/state').then(r => r.json());
      if (window.officeRenderer && stateData.office_layout) {
        window.officeRenderer.updateState({ office_layout: stateData.office_layout });
      }
    } catch (e) {
      console.warn('Failed to fetch office layout:', e);
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
      if (c.includes('employees'))       this._fetchAndRenderRoster();
      if (c.includes('rooms'))          this._fetchAndRenderRooms();
      if (c.includes('tools'))          this._fetchAndRenderTools();
      if (c.includes('office_layout'))  this._fetchAndRenderOfficeLayout();
      if (c.includes('culture') && !document.getElementById('company-culture-modal').classList.contains('hidden')) {
        this._renderCompanyCulture();
      }
      if (c.includes('task_queue'))    this.updateProjectsPanel();
      if (c.includes('overhead') && !document.getElementById('dashboard-modal').classList.contains('hidden')) {
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

    // CEO session real-time updates (project-level conversations)
    if (msg.type === 'ceo_session_message') {
      const p = msg.payload || {};
      this._refreshCeoProjectList();
      // If viewing this project in terminal, append message
      if (this._currentCeoProject === p.project_id && this._ceoTerm) {
        this._ceoTerm.appendMessage({
          role: 'system',
          text: p.message,
          source: p.source_employee || 'system',
        });
      }
      // Also refresh project list panel
      this.updateProjectsPanel();
      return;
    }
    // Unified conversation events
    if (msg.type === 'conversation_message') {
      const p = msg.payload || msg;
      // xterm terminal path: 1-on-1 or EA chat
      if (this._currentConvId === p.conv_id && this._ceoTerm && (this._currentConvType === 'oneonone' || this._currentConvType === 'ea_chat')) {
        if (p.sender !== 'ceo' && p.text != null) {
          const source = this._currentConvType === 'ea_chat'
            ? '玲珑阁 (EA)'
            : this._resolveEmployeeNickname(p.employee_id || this._currentConvEmployeeId || '');
          this._ceoTerm.appendMessage({
            role: 'system',
            text: p.text,
            source,
          });
        }
      } else if (this._chatPanel && p.conv_id === this._chatPanel.getConvId() && p.text != null) {
        // ChatPanel path (only for non-terminal conversations)
        this._chatPanel.appendMessage(p);
      }
      return;
    }
    if (msg.type === 'conversation_phase') {
      const p = msg.payload || msg;
      if (p.phase === 'closed' && this._chatPanel && p.conv_id === this._chatPanel.getConvId()) {
        this._chatPanel.setInputEnabled(false);
      }
      // Refresh 1-on-1 list when a conversation closes
      if (p.phase === 'closed') {
        this._refreshOneononeList();
        // If we were viewing this closed conversation, show a notice
        if (this._currentConvId === p.conv_id && this._ceoTerm) {
          this._ceoTerm.appendMessage({
            role: 'system',
            text: '1-on-1 session ended.',
            source: 'system',
          });
          this._currentConvId = null;
          this._currentConvType = null;
          this._currentConvEmployeeId = null;
        }
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
      'talent_profile_error': (p) => {
        const fields = (p.missing_fields || []).join(', ');
        const lines = [];
        lines.push(`${ANSI.red}${ANSI.bold}Talent Profile Error${ANSI.reset}`);
        lines.push('');
        lines.push(`${ANSI.cyan}Talent:${ANSI.reset}  ${p.talent_id || 'unknown'}`);
        if (fields) lines.push(`${ANSI.cyan}Missing:${ANSI.reset} ${ANSI.yellow}${fields}${ANSI.reset}`);
        if (p.talent_link) lines.push(`${ANSI.cyan}Repo:${ANSI.reset}    ${p.talent_link}`);
        lines.push('');
        lines.push(`${ANSI.dim}Please contact the talent uploader to fix this issue.${ANSI.reset}`);
        if (p.talent_link) lines.push(`${ANSI.dim}You can file an issue on the talent repo.${ANSI.reset}`);
        this._showXtermAlert('Talent Profile Error', lines);
        return { text: `Talent profile error: ${p.talent_id}`, cls: 'hr', agent: 'HR' };
      },
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
        return { text: `💬 [${p.speaker}] ${p.message || ''}`, cls: 'system', agent: 'MEETING' };
      },
      'meeting_agenda_update': (p) => {
        // Cache agenda state so it survives modal close/reopen
        if (p.room_id) this._meetingAgendaCache[p.room_id] = p;
        if (this.viewingRoomId === p.room_id) {
          this._renderMeetingAgenda(p);
        }
        return null; // no activity log entry
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
      'open_popup':          (p) => {
        this.openPopup(p);
        return { text: `📢 ${p.title || 'Notification'}`, cls: 'system', agent: p.agent || 'SYSTEM' };
      },
      'request_credentials': (p) => {
        this.openPopup({ ...p, type: 'credentials' });
        return { text: `🔑 ${p.title || 'Credentials required'}`, cls: 'system', agent: p.agent || 'SYSTEM' };
      },
      'agent_task_update':   (p) => {
        // In-place update: use WS payload directly instead of REST re-fetch
        if (this.viewingEmployeeId && p.employee_id === this.viewingEmployeeId && p.task) {
          this._updateTaskBoardCard(p.task);
        }
        return { text: `📋 ${p.employee_id} task: ${p.task?.status || 'updated'}`, cls: 'system', agent: 'AGENT' };
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
        this._showProjectReportModal(p);
        return { text: `📊 Project Report: ${p.subject}`, cls: 'ceo', agent: 'SYSTEM' };
      },
      'background_task_update': (p) => {
        const bgModal = document.getElementById('bg-tasks-modal');
        if (bgModal && !bgModal.classList.contains('hidden')) {
          // In-place update: use WS payload to update list item
          this._updateBgTaskListItem(p);
          // Refresh detail view if viewing this specific task
          if (this._bgTaskSelected && this._bgTaskSelected === p.id) {
            this._updateBgTaskDetailStatus(p);
          }
        }
        return { text: `BG Task ${p.id || '?'}: ${p.status}`, cls: 'system', agent: 'SYSTEM' };
      },
      'cron_status_change': (p) => {
        // Layer 2: refresh cron list if viewing this employee.
        // Uses REST fetch (not in-place DOM update) because the cron list
        // is small and the payload lacks the full cron config needed to render.
        if (this.viewingEmployeeId && p.employee_id === this.viewingEmployeeId) {
          this._fetchCronList(this.viewingEmployeeId);
        }
        return null;
      },
      'review_reminder': (p) => {
        const nodes = p.overdue_nodes || [];
        if (!nodes.length) return null;
        const summaries = nodes.map(n => {
          const mins = Math.round(n.waiting_seconds / 60);
          return `${n.employee_id}: ${n.description || ''} (${mins}m)`;
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
        // Real-time append: use WS payload directly instead of re-polling REST
        if (this.viewingEmployeeId && p.employee_id === this.viewingEmployeeId && p.log) {
          // Skip if REST fetch is in-flight (renderLogs will do a full refresh)
          if (this._logFetchInFlight) return null;
          if (this._empXterm) {
            this._empXterm.appendLog(p.log);
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

  // ===== Panel Divider Drag (removed — grid gap replaces dividers) =====
  _initPanelDividers() {
    // Draggable resize handles for all grid panel borders
    this._gridResizer = new GridResizer();
  }

  // ===== Cancel Task (used by project card overlay) =====
  async _cancelTask(projectId) {
    if (!confirm('Are you sure you want to cancel this task?')) return;
    try {
      const resp = await fetch(`/api/task/${projectId}/abort`, { method: 'POST' });
      const data = await resp.json();
      if (data.status === 'ok') {
        this.updateProjectsPanel();
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

  // ===== Activity Log (xterm.js terminal) =====
  _activityLogAnsi = {
    ceo: ANSI.brightCyan, hr: ANSI.yellow, coo: ANSI.green, ea: ANSI.yellow,
    system: ANSI.gray, meeting: ANSI.green, guidance: ANSI.dim,
    routine: ANSI.dim, agent: ANSI.cyan,
  }

  _activityTypeMap = {
    'pull_meeting': (e) => ({ agent: 'MEETING', text: `${e.topic || 'Meeting'} (${e.rounds || 0} rounds)` }),
    'knowledge_deposited': (e) => ({ agent: 'SYSTEM', text: `Knowledge: ${e.name || ''}` }),
    'employee_hired': (e) => ({ agent: 'HR', text: `New hire: ${e.name || ''} (${e.role || ''})` }),
    'employee_fired': (e) => ({ agent: 'HR', text: `Departure: ${e.name || ''}` }),
    'tool_added': (e) => ({ agent: 'COO', text: `New tool: ${e.name || ''}` }),
    'ceo_task': (e) => ({ agent: 'CEO', text: `Task: ${e.task || ''}` }),
    'meeting_booked': (e) => ({ agent: 'COO', text: `Room booked: ${e.room_name || ''}` }),
    'meeting_released': (e) => ({ agent: 'COO', text: `Room released: ${e.room_name || ''}` }),
    'promotion': (e) => ({ agent: 'HR', text: `Promoted: ${e.name || ''}` }),
  }

  _ensureActivityXterm() {
    if (this._activityXterm) return;
    const el = document.getElementById('activity-log');
    if (!el || typeof XTermLog === 'undefined') return;
    el.innerHTML = '';
    this._activityXterm = new XTermLog(el, { fontSize: 10 });
  }

  _renderHistoricalActivityLog(entries) {
    this._ensureActivityXterm();
    if (!this._activityXterm) return;
    const fallback = (e) => ({ agent: (e.type || 'SYS').toUpperCase(), text: `${e.name || e.topic || e.task || e.type || ''}` });
    for (const e of entries) {
      const { agent, text } = (this._activityTypeMap[e.type] || fallback)(e);
      const ts = e.timestamp ? e.timestamp.substring(11, 19) : '        ';
      const color = this._activityLogAnsi[agent.toLowerCase()] || ANSI.gray;
      this._activityXterm.writeln(`${ANSI.gray}${ts}${ANSI.reset} ${color}${agent}${ANSI.reset} ${text}`);
    }
  }

  logEntry(agent, message, cssClass = 'system') {
    this._ensureActivityXterm();
    if (!this._activityXterm) return;
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit' });
    const color = this._activityLogAnsi[cssClass] || this._activityLogAnsi[agent.toLowerCase()] || ANSI.gray;
    this._activityXterm.writeln(`${ANSI.gray}${time}${ANSI.reset} ${color}${agent}${ANSI.reset} ${message}`);
    this._activityXterm.scrollToBottom();
  }

  // ===== UI Bindings =====
  bindUI() {
    const hrBtn = document.getElementById('hr-review-btn');

    // Initialize CEO Terminal (xterm.js-based)
    this._initCeoTerminal();

    hrBtn?.addEventListener('click', () => {
      hrBtn.disabled = true;
      this.logEntry('CEO', '🔄 Triggering quarterly review...', 'ceo');
      fetch('/api/hr/review', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            this.logEntry('SYSTEM', `Review failed: ${data.error}`, 'system');
          } else {
            this.logEntry('HR', '📋 Quarterly review task assigned to HR', 'hr');
          }
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
        .catch(err => console.error('[apply-code-update] failed:', err));
    });
    document.getElementById('code-update-dismiss-btn').addEventListener('click', () => {
      document.getElementById('code-update-banner').classList.add('hidden');
    });

    // Roster filter bindings
    ['roster-filter-role', 'roster-filter-dept', 'roster-filter-level'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => this._onRosterFilterChange());
    });

    // EA auto-reply toggle in CEO Inbox header
    const eaToggleEl = document.getElementById('ea-autoreply-toggle');
    const eaCheckbox = document.getElementById('ea-autoreply-checkbox');
    if (eaToggleEl) {
      // Prevent collapsible header click from toggling when clicking the EA toggle
      eaToggleEl.addEventListener('click', (e) => e.stopPropagation());
    }
    if (eaCheckbox) {
      eaCheckbox.addEventListener('change', () => {
        if (this._currentConvNodeId) {
          this._toggleEaAutoReply(this._currentConvNodeId, eaCheckbox.checked);
        }
      });
    }

    // 1-on-1 meeting modal bindings
    const oneononeModal = document.getElementById('oneonone-modal');
    // Meeting button removed from toolbar — meetings now via /1on1, /allhands, /discuss
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
    // CEO chat in meeting room
    document.getElementById('meeting-ceo-send-btn').addEventListener('click', () => this._sendMeetingRoomMessage());
    document.getElementById('meeting-ceo-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._sendMeetingRoomMessage();
      }
    });

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
    document.getElementById('reload-toolbar-btn')?.addEventListener('click', () => this.adminReload());

    // Operations dashboard modal bindings
    document.getElementById('dashboard-toolbar-btn').addEventListener('click', () => this.openDashboard());
    document.getElementById('dashboard-close-btn').addEventListener('click', () => this.closeDashboard());
    document.getElementById('dashboard-modal').addEventListener('click', (e) => {
      if (e.target.id === 'dashboard-modal') this.closeDashboard();
    });

    // Background Tasks modal bindings
    document.getElementById('bg-tasks-toolbar-btn').addEventListener('click', () => this.openBackgroundTasks());
    document.getElementById('bg-tasks-close-btn').addEventListener('click', () => this.closeBackgroundTasks());
    document.getElementById('bg-tasks-modal').addEventListener('click', (e) => {
      if (e.target.id === 'bg-tasks-modal') this.closeBackgroundTasks();
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

    // CEO task file upload (element may not exist if terminal mode)
    document.getElementById('task-file-input')?.addEventListener('change', (e) => {
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

    // 1-on-1 → use unified conversation module in right panel
    const select = document.getElementById('oneonone-target');
    const empId = select.value;
    if (!empId) return;

    this.closeOneononeModal();
    await this._startOneononeConversation(empId);
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

    // 1-on-1 button
    const oneononeBtn = document.getElementById('emp-oneonone-btn');
    if (oneononeBtn) {
      oneononeBtn.onclick = () => {
        this.closeEmployeeDetail();
        this._startOneononeConversation(emp.id);
      };
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
    this._taskBoardFilter = '';
    this._fetchTaskBoard(emp.id);
    this._fetchExecutionLogs(emp.id);
    this._fetchProgressLog(emp.id);
    this._fetchCronList(emp.id);
    this._fetchEmployeeProjects(emp.id);

    modal.classList.remove('hidden');
  }

  closeEmployeeDetail() {
    this.viewingEmployeeId = null;
    if (this._empXterm) { this._empXterm.dispose(); this._empXterm = null; }
    if (this._empProgressXterm) { this._empProgressXterm.dispose(); this._empProgressXterm = null; }
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

  async _fetchTaskBoard(empId, status) {
    try {
      const filter = status || this._taskBoardFilter || '';
      const qs = filter ? `?status=${filter}` : '';
      const resp = await fetch(`/api/employee/${empId}/taskboard${qs}`);
      const data = await resp.json();
      this._renderTaskBoard(data.tasks || [], data.counts || {});
    } catch (err) {
      console.error('Task board fetch error:', err);
    }
  }

  async _fetchExecutionLogs(empId) {
    this._logFetchInFlight = true;
    try {
      const resp = await fetch(`/api/employee/${empId}/logs?tail=100`);
      const data = await resp.json();
      const el = document.getElementById('emp-detail-logs');
      if (data.logs && data.logs.length > 0) {
        if (!this._empXterm) {
          el.innerHTML = '';
          this._empXterm = new XTermLog(el, { fontSize: 11 });
        }
        this._empXterm.renderLogs(data.logs);
      } else {
        el.innerHTML = '<span class="empty-hint">No logs</span>';
      }
    } catch (err) {
      console.error('Execution logs fetch error:', err);
    } finally {
      this._logFetchInFlight = false;
    }
  }

  async _fetchProgressLog(empId) {
    try {
      const resp = await fetch(`/api/employee/${empId}/progress-log?limit=30`);
      const data = await resp.json();
      const entries = data.entries || [];
      const el = document.getElementById('emp-detail-progress');
      if (!el) return;

      if (entries.length > 0 && typeof XTermLog !== 'undefined') {
        if (!this._empProgressXterm) {
          el.innerHTML = '';
          this._empProgressXterm = new XTermLog(el, { fontSize: 10 });
        }
        this._empProgressXterm.clear();
        this._empProgressXterm.writeln(`${ANSI.dim}── Work History (completed task summaries) ──${ANSI.reset}`);
        for (const e of entries) {
          const ts = e.timestamp ? e.timestamp.substring(5, 16).replace('T', ' ') : '';
          const content = e.content || '';
          if (content.startsWith('Completed:')) {
            this._empProgressXterm.writeln(`${ANSI.gray}${ts}${ANSI.reset} ${ANSI.green}${content}${ANSI.reset}`);
          } else {
            this._empProgressXterm.writeln(`${ANSI.gray}${ts}${ANSI.reset} ${content}`);
          }
        }
      } else {
        el.innerHTML = '<span class="empty-hint">No work history</span>';
      }
    } catch (err) {
      console.error('Progress log fetch error:', err);
    }
  }

  _renderTaskBoard(tasks, counts) {
    const el = document.getElementById('emp-detail-taskboard');
    const empId = this.viewingEmployeeId;
    const cur = this._taskBoardFilter || '';

    let tabs = '';
    if (counts && counts.total > 0) {
      const btn = (label, val, cnt) => {
        const active = cur === val ? 'font-weight:bold;border-bottom:2px solid #4af' : '';
        return `<button onclick="app._taskBoardFilter='${val}';app._fetchTaskBoard('${empId}','${val}')" style="background:transparent;color:#ccc;border:none;cursor:pointer;padding:2px 6px;font-size:10px;${active}">${label}(${cnt})</button>`;
      };
      tabs = `<div style="display:flex;gap:2px;margin-bottom:4px;border-bottom:1px solid #333;padding-bottom:2px">
        ${btn('All','',counts.total)}${btn('Active','active',counts.active)}${btn('Done','completed',counts.completed)}${counts.failed ? btn('Failed','failed',counts.failed) : ''}
      </div>`;
    }

    if (!tasks || tasks.length === 0) {
      el.innerHTML = tabs + '<span class="empty-hint">No tasks</span>';
      return;
    }

    let html = tabs;
    for (const task of tasks) {
      const statusCls = task.status.replace('_', '-');
      html += `<div class="emp-taskboard-item ${statusCls}" data-task-id="${task.id}">`;
      html += `<div class="emp-taskboard-status" style="display:flex;justify-content:space-between;align-items:center;">`;
      html += `<span>${task.status}</span>`;
      if (task.status === 'pending' || task.status === 'processing') {
        html += `<button class="emp-task-cancel-btn" onclick="window._abortAgentTask('${empId}','${task.id}')">CANCEL</button>`;
      }
      html += `</div>`;
      html += `<div class="emp-taskboard-desc">${this._escHtml(task.description_preview || task.description || '')}</div>`;
      if (task.result) {
        html += `<div class="emp-taskboard-result">${this._escHtml(task.result)}</div>`;
      }
      if (task.cost_usd > 0) {
        html += `<div class="emp-taskboard-cost">$${task.cost_usd.toFixed(4)}</div>`;
      }
      html += '</div>';
    }
    el.innerHTML = html;
  }

  _updateTaskBoardCard(task) {
    // In-place update of a single task card in the taskboard.
    // If the task matches the current filter, re-render the full board
    // with the updated task merged in. This avoids a REST round-trip.
    const el = document.getElementById('emp-detail-taskboard');
    if (!el) return;
    // Find existing card for this task and update it, or do a full re-fetch
    // if it's a new task (not yet in the board).
    const existing = el.querySelector(`.emp-taskboard-item[data-task-id="${task.id}"]`);
    if (existing) {
      // Update status + result in-place
      const statusEl = existing.querySelector('.emp-taskboard-status span');
      if (statusEl) statusEl.textContent = task.status;
      existing.className = `emp-taskboard-item ${(task.status || '').replace('_', '-')}`;
      const descEl = existing.querySelector('.emp-taskboard-desc');
      if (descEl) descEl.textContent = task.description_preview || task.description || '';
      const resultEl = existing.querySelector('.emp-taskboard-result');
      if (task.result) {
        if (resultEl) {
          resultEl.textContent = task.result;
        } else {
          const div = document.createElement('div');
          div.className = 'emp-taskboard-result';
          div.textContent = task.result;
          existing.appendChild(div);
        }
      }
    } else {
      // New task not in DOM — full refresh needed (one-time)
      this._fetchTaskBoard(this.viewingEmployeeId);
    }
  }

  // _renderExecutionLogs removed — all rendering via XTermLog


  // ===== Trace Viewer =====

  openTraceViewer(projectId, projectName) {
    const modal = document.getElementById('trace-modal');
    const titleEl = document.getElementById('trace-modal-title');
    const metaEl = document.getElementById('trace-modal-meta');
    const feedPanel = document.getElementById('trace-feed-panel');

    titleEl.textContent = `\u2588\u2588 ${(projectName || projectId).toUpperCase()} `;
    metaEl.textContent = 'loading...';
    feedPanel.innerHTML = '';

    // Dispose previous xterm instance
    if (this._traceXterm) { this._traceXterm.dispose(); this._traceXterm = null; }

    modal.classList.remove('hidden');

    const xterm = new XTermLog(feedPanel, { fontSize: 11 });
    this._traceXterm = xterm;
    xterm.writeln(`${ANSI.gray}Loading trace...${ANSI.reset}`);

    fetch(`/api/projects/${projectId}/tree`)
      .then(r => r.json())
      .then(async data => {
        const nodes = {};
        for (const n of data.nodes) nodes[n.id] = n;
        await traceLoadAllNodeLogs(nodes);
        xterm.clear();
        xterm.renderTraceFeed(nodes, data.root_id);
        metaEl.textContent = `${Object.keys(nodes).length} nodes`;
      })
      .catch(e => {
        xterm.writeln(`${ANSI.red}Error: ${e.message}${ANSI.reset}`);
      });
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
        projects.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
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
            this._showRetroPopup(employeeId, pid);
          });
        });
      })
      .catch(err => {
        console.error('[loadProjectList] failed:', err);
        container.innerHTML = '<span class="empty-hint">Failed to load</span>';
      });
  }

  _openProjectFromId(projectId) {
    this._loadIterationDetail(projectId, projectId);
    const detailEl = document.getElementById('project-detail');
    if (detailEl) detailEl.classList.remove('hidden');
  }

  async _showRetroPopup(employeeId, projectId) {
    try {
      const resp = await fetch(`/api/employees/${employeeId}/projects/${projectId}/retrospective`);
      const data = await resp.json();

      let html = '';

      if (data.self_evaluation) {
        html += `<div class="retro-section"><h4>Self Evaluation</h4><p>${this._escHtml(data.self_evaluation)}</p></div>`;
      }
      if (data.feedback) {
        html += `<div class="retro-section"><h4>Feedback</h4><p>${this._escHtml(data.feedback)}</p></div>`;
      }
      if (data.senior_reviews && data.senior_reviews.length > 0) {
        html += '<div class="retro-section"><h4>Senior Reviews</h4>';
        for (const sr of data.senior_reviews) {
          html += `<p><strong>${this._escHtml(sr.reviewer)}:</strong> ${this._escHtml(sr.review)}</p>`;
        }
        html += '</div>';
      }
      if (data.hr_improvements && data.hr_improvements.length > 0) {
        html += '<div class="retro-section"><h4>Improvements</h4>';
        for (const item of data.hr_improvements) {
          html += `<p>${this._escHtml(item)}</p>`;
        }
        html += '</div>';
      }

      if (!html) {
        html = '<p class="empty-hint">No retrospective data for this project yet.</p>';
      }

      this.openPopup({
        title: 'Project Retrospective',
        type: 'info',
      });

      const body = document.getElementById('generic-popup-body');
      body.innerHTML = html;
    } catch (err) {
      console.error('[showRetroPopup] failed:', err);
      this.openPopup({ title: 'Error', message: 'Failed to load retrospective data.' });
    }
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
    div.textContent = String(str ?? '');
    return div.innerHTML;
  }

  _sortProjectsNewestFirst(projects) {
    return projects.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  }

  // ===== CEO Terminal (two-column: project list + xterm conversation) ===== //

  _EA_CHAT = '_ea_chat';  // Reserved sentinel — not a real project ID
  _currentCeoProject = null;  // Currently selected project_id (session-level, e.g. "proj/iter_001")

  async _initCeoTerminal() {
    const messagesContainer = document.getElementById('ceo-conv-messages');
    if (!messagesContainer) return;

    this._ceoTerm = new CeoTerminal(messagesContainer);

    // Wire Chat button — switches to EA default chat
    document.getElementById('ceo-chat-btn')?.addEventListener('click', () => {
      this._openEaChat();
    });

    // Wire project list toggle
    const toggle = document.getElementById('ceo-list-toggle');
    const projectList = document.getElementById('ceo-project-list');
    toggle?.addEventListener('click', () => {
      const collapsed = projectList.classList.toggle('collapsed');
      toggle.textContent = collapsed ? '▶' : '◀';
      setTimeout(() => this._ceoTerm?._fit(), 200);
    });

    // Wire 1-on-1 section collapsible toggle
    document.getElementById('ceo-oneonone-toggle')?.addEventListener('click', () => {
      const items = document.getElementById('ceo-oneonone-items');
      const arrow = document.querySelector('#ceo-oneonone-toggle .ceo-section-arrow');
      items?.classList.toggle('collapsed');
      if (arrow) arrow.textContent = items.classList.contains('collapsed') ? '\u25B6' : '\u25BC';
    });

    // Wire HTML input
    const input = document.getElementById('ceo-conv-input');
    this._inputHistory = JSON.parse(localStorage.getItem('ceo-input-history') || '[]');
    this._inputHistoryIdx = this._inputHistory.length;
    const doSend = async () => {
      const text = (input?.value || '').trim();
      if (!text) return;

      // Execute slash command if input matches one exactly
      if (text.startsWith('/')) {
        const cmdText = text.split(' ')[0].toLowerCase();
        const match = this._slashCommands.find(c => c.cmd === cmdText);
        if (match) {
          input.value = '';
          const slashMenu = document.getElementById('ceo-slash-menu');
          slashMenu?.classList.add('hidden');
          match.action();
          return;
        }
      }

      // Save to input history
      if (!this._inputHistory.length || this._inputHistory[this._inputHistory.length - 1] !== text) {
        this._inputHistory.push(text);
        if (this._inputHistory.length > 100) this._inputHistory.shift();
        localStorage.setItem('ceo-input-history', JSON.stringify(this._inputHistory));
      }
      this._inputHistoryIdx = this._inputHistory.length;
      input.value = '';

      // Show CEO message immediately in terminal
      this._ceoTerm?.appendCeoMessage(text);

      // /iter mode: create new iteration on pending project
      if (this._pendingIterProject) {
        const pid = this._pendingIterProject;
        this._pendingIterProject = null;
        if (input) input.placeholder = '$ Type message, / for commands (Enter to send)';
        try {
          const formData = new FormData();
          formData.append('task', text);
          formData.append('project_id', pid.split('/')[0]);
          formData.append('mode', 'standard');
          await fetch('/api/ceo/task', { method: 'POST', body: formData });
          await this._refreshCeoProjectList();
          this._ceoTerm?.appendMessage({ role: 'system', text: 'New iteration created.', source: 'system' });
        } catch (e) { console.error('Failed to create iteration:', e); }
        input?.focus();
        return;
      }

      // 1-on-1 conversation mode: send via conversation API
      if (this._currentConvType === 'oneonone' && this._currentConvId) {
        try {
          await fetch(`/api/conversation/${this._currentConvId}/message`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text }),
          });
        } catch (e) { console.error('Failed to send 1-on-1 message:', e); }
        input?.focus();
        return;
      }

      if (this._currentCeoProject === this._EA_CHAT && this._eaChatConvId) {
        // EA Chat: send as conversation message — EA decides whether to create project
        try {
          await fetch(`/api/conversation/${this._eaChatConvId}/message`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text }),
          });
          // EA response arrives via WebSocket conversation_message event
        } catch (e) { console.error('Failed to send EA chat message:', e); }
      } else if (!this._currentCeoProject) {
        // No project selected and no EA chat — fallback to task creation
        const mode = this._pendingSimpleMode ? 'simple' : 'standard';
        this._pendingSimpleMode = false;
        try {
          const formData = new FormData();
          formData.append('task', text);
          formData.append('mode', mode);
          await fetch('/api/ceo/task', { method: 'POST', body: formData });
          await this._refreshCeoProjectList();
        } catch (e) { console.error('Failed to submit task:', e); }
      } else {
        try {
          await fetch(`/api/ceo/sessions/${encodeURIComponent(this._currentCeoProject)}/message`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text}),
          });
          await this._refreshCeoProjectList();
        } catch (e) { console.error('Failed to send:', e); }
      }

      input?.focus();
    };

    input?.addEventListener('keydown', (e) => {
      const slashMenu = document.getElementById('ceo-slash-menu');
      const menuVisible = slashMenu && !slashMenu.classList.contains('hidden');

      // Tab: autocomplete selected slash command into input (fallback to first item)
      if (e.key === 'Tab' && menuVisible) {
        const activeItem = slashMenu.querySelector('.slash-item.active')
                        || slashMenu.querySelector('.slash-item');
        if (activeItem) {
          e.preventDefault();
          const cmd = activeItem.querySelector('.slash-cmd')?.textContent || '';
          if (cmd) {
            input.value = cmd + ' ';
            slashMenu.classList.add('hidden');
          }
        }
        return;
      }

      // Enter: execute selected slash command or send message
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (menuVisible) {
          const activeItem = slashMenu.querySelector('.slash-item.active');
          if (activeItem) {
            activeItem.click();
            return;
          }
        }
        doSend();
        return;
      }

      // Arrow keys: navigate slash menu or input history
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (menuVisible) {
          e.preventDefault();
          this._navigateSlashMenu(e.key === 'ArrowDown' ? 1 : -1);
        } else if (this._inputHistory?.length) {
          e.preventDefault();
          if (e.key === 'ArrowUp' && this._inputHistoryIdx > 0) {
            this._inputHistoryIdx--;
            input.value = this._inputHistory[this._inputHistoryIdx];
          } else if (e.key === 'ArrowDown') {
            if (this._inputHistoryIdx < this._inputHistory.length - 1) {
              this._inputHistoryIdx++;
              input.value = this._inputHistory[this._inputHistoryIdx];
            } else {
              this._inputHistoryIdx = this._inputHistory.length;
              input.value = '';
            }
          }
        }
        return;
      }

      // Escape: close slash menu
      if (e.key === 'Escape') {
        slashMenu?.classList.add('hidden');
      }
    });
    input?.addEventListener('input', () => this._handleSlashInput(input));

    // File upload
    const fileInput = document.getElementById('ceo-file-input');
    fileInput?.addEventListener('change', () => {
      if (!fileInput.files?.length) return;
      const names = Array.from(fileInput.files).map(f => f.name).join(', ');
      this._ceoTerm?.appendMessage({
        role: 'system', text: `Attached: ${names}`, source: 'upload',
      });
      // Store files for next send
      this._pendingFiles = Array.from(fileInput.files);
      fileInput.value = '';
    });

    await this._refreshCeoProjectList();

    // Default: open EA chat
    this._openEaChat();
  }

  _eaChatConvId = null;  // Persistent conversation ID for EA chat

  async _openEaChat() {
    // Clear pending modes that could interfere
    this._pendingIterProject = null;
    this._pendingSimpleMode = false;
    this._currentCeoProject = this._EA_CHAT;
    this._currentConvType = 'ea_chat';
    this._currentConvId = this._eaChatConvId;
    this._currentConvEmployeeId = '00004';  // EA

    // Update active states in sidebar
    document.querySelectorAll('.ceo-proj-item').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.ceo-oneonone-item').forEach(el => el.classList.remove('active'));
    document.getElementById('ceo-chat-btn')?.classList.add('active');

    // Ensure EA chat conversation exists
    if (!this._eaChatConvId) {
      await this._ensureEaChatConversation();
    }

    // Load conversation history
    let messages = [];
    if (this._eaChatConvId) {
      try {
        const resp = await fetch(`/api/conversation/${this._eaChatConvId}/messages`);
        const data = await resp.json();
        messages = (data.messages || []).map(m => ({
          role: m.sender === 'ceo' ? 'ceo' : 'system',
          text: m.text || '',
          source: m.sender === 'ceo' ? undefined : '玲珑阁 (EA)',
        }));
      } catch (e) { console.error('EA chat history load error:', e); }
    }
    this._ceoTerm?.showChat(this._EA_CHAT, messages);
  }

  async _ensureEaChatConversation() {
    // Check localStorage for existing EA chat conv_id
    this._eaChatConvId = localStorage.getItem('ea-chat-conv-id') || null;
    if (this._eaChatConvId) {
      // Verify it still exists
      try {
        const resp = await fetch(`/api/conversation/${this._eaChatConvId}/messages`);
        if (resp.ok) {
          this._currentConvId = this._eaChatConvId;
          return;
        }
      } catch (e) { console.error('EA chat error:', e); }
      // Stale — clear and recreate
      this._eaChatConvId = null;
      localStorage.removeItem('ea-chat-conv-id');
    }

    // Create a new persistent EA chat conversation
    try {
      const resp = await fetch('/api/conversation/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ type: 'ea_chat', employee_id: '00004', tools_enabled: true }),
      });
      const conv = await resp.json();
      this._eaChatConvId = conv.id;
      this._currentConvId = conv.id;
      localStorage.setItem('ea-chat-conv-id', conv.id);
    } catch (e) {
      console.error('Failed to create EA chat conversation:', e);
    }
  }

  // --- Slash command menu --- //

  get _slashCommands() {
    const projName = this._currentCeoProject ? this._currentCeoProject.split('/')[0] : null;
    return [
      { cmd: '/new', desc: 'Create a new project', action: () => {
        this._currentCeoProject = null; this._currentConvId = null; this._currentConvType = null;
        this._refreshCeoProjectList(); this._ceoTerm?.showChat(null, []);
      }},
      { cmd: '/iter', desc: projName ? `New iteration on "${projName}"` : 'Select a project first', action: () => {
        if (!this._currentCeoProject) {
          this._ceoTerm?.appendMessage({ role: 'system', text: 'Select a project first, then use /iter', source: 'system' });
          return;
        }
        // Switch to new-iter mode: next Enter submits as iteration
        this._pendingIterProject = this._currentCeoProject;
        this._ceoTerm?.appendMessage({ role: 'system', text: `Type the iteration goal for "${projName}". Press Enter to create.`, source: 'system' });
        const input = document.getElementById('ceo-conv-input');
        if (input) input.placeholder = `$ New iteration for ${projName}...`;
      }},
      { cmd: '/end', desc: this._currentConvType === 'oneonone' ? 'End current 1-on-1 (triggers reflection)' : 'No active 1-on-1', action: () => {
        if (this._currentConvType !== 'oneonone' || !this._currentConvId) {
          this._ceoTerm?.appendMessage({ role: 'system', text: 'No active 1-on-1 to end.', source: 'system' });
          return;
        }
        this._endOneononeFromTerminal();
      }},
      { cmd: '/simple', desc: 'Simple task (no retrospective)', action: () => {
        this._pendingSimpleMode = true;
        this._currentCeoProject = null; this._currentConvId = null; this._currentConvType = null;
        this._refreshCeoProjectList();
        this._ceoTerm?.showChat(null, []);
        this._ceoTerm?.appendMessage({ role: 'system', text: 'Simple mode: type task and press Enter. EA will dispatch directly, no retrospective.', source: 'system' });
        const input = document.getElementById('ceo-conv-input');
        if (input) input.placeholder = '$ Simple task (Enter to submit)...';
      }},
      { cmd: '/review', desc: 'Trigger quarterly performance review', action: () => {
        this._ceoTerm?.appendMessage({ role: 'system', text: 'Triggering quarterly review...', source: 'system' });
        this.logEntry('CEO', '🔄 Triggering quarterly review...', 'ceo');
        fetch('/api/hr/review', { method: 'POST' })
          .then(r => r.json())
          .then(data => {
            if (data.error) {
              this._ceoTerm?.appendMessage({ role: 'system', text: `Review failed: ${data.error}`, source: 'system' });
              this.logEntry('SYSTEM', `Review failed: ${data.error}`, 'system');
            } else {
              this._ceoTerm?.appendMessage({ role: 'system', text: '📋 Quarterly review task assigned to HR', source: 'system' });
              this.logEntry('HR', '📋 Quarterly review task assigned to HR', 'hr');
            }
          })
          .catch(e => {
            this._ceoTerm?.appendMessage({ role: 'system', text: `Review error: ${e.message}`, source: 'system' });
          });
      }},
      { cmd: '/1on1', desc: 'Start 1-on-1 meeting with an employee', action: () => {
        const modal = document.getElementById('oneonone-modal');
        if (modal) {
          document.getElementById('meeting-type-select').value = 'oneonone';
          document.getElementById('meeting-type-select').dispatchEvent(new Event('change'));
          modal.classList.remove('hidden');
        }
      }},
      { cmd: '/allhands', desc: 'Start All-Hands meeting (CEO address)', action: () => {
        this._ceoTerm?.appendMessage({ role: 'system', text: 'Starting All-Hands meeting...', source: 'system' });
        document.getElementById('oneonone-modal')?.classList.remove('hidden');
        this._startGroupMeeting('all_hands');
      }},
      { cmd: '/discuss', desc: 'Start discussion meeting (open floor)', action: () => {
        this._ceoTerm?.appendMessage({ role: 'system', text: 'Starting discussion meeting...', source: 'system' });
        document.getElementById('oneonone-modal')?.classList.remove('hidden');
        this._startGroupMeeting('discussion');
      }},
      { cmd: '/attach', desc: 'Attach file or image', action: () => document.getElementById('ceo-file-input')?.click() },
    ];
  }

  _handleSlashInput(input) {
    const text = input.value;
    const menu = document.getElementById('ceo-slash-menu');
    if (!menu) return;

    if (text.startsWith('/')) {
      const query = text.toLowerCase();
      const matches = this._slashCommands.filter(c => c.cmd.startsWith(query));
      if (matches.length) {
        menu.innerHTML = matches.map((c, i) =>
          `<div class="slash-item${i === 0 ? ' active' : ''}" data-idx="${i}">` +
          `<span class="slash-cmd">${c.cmd}</span><span class="slash-desc">${c.desc}</span></div>`
        ).join('');
        menu.classList.remove('hidden');
        menu.querySelectorAll('.slash-item').forEach((el, i) => {
          el.addEventListener('click', () => {
            menu.classList.add('hidden');
            input.value = '';
            matches[i].action();
          });
        });
        return;
      }
    }
    menu.classList.add('hidden');
  }

  _navigateSlashMenu(dir) {
    const menu = document.getElementById('ceo-slash-menu');
    if (!menu) return;
    const items = menu.querySelectorAll('.slash-item');
    if (!items.length) return;
    let idx = Array.from(items).findIndex(el => el.classList.contains('active'));
    items[idx]?.classList.remove('active');
    idx = Math.max(0, Math.min(items.length - 1, idx + dir));
    items[idx]?.classList.add('active');
  }

  async _refreshCeoProjectList() {
    const listEl = document.getElementById('ceo-projects-section');
    if (!listEl) return;

    let sessions = [];
    try {
      const resp = await fetch('/api/ceo/sessions');
      sessions = (await resp.json()).sessions || [];
    } catch (e) {}

    // Fetch project names for display
    let projectNames = {};
    try {
      const namesResp = await fetch('/api/projects/named');
      const namesData = await namesResp.json();
      for (const p of namesData.projects || namesData || []) {
        projectNames[p.project_id || p.id] = p.name || p.project_name || '';
      }
    } catch (e) { /* ignore */ }

    listEl.innerHTML = '';

    // Update Chat button active state
    const chatBtn = document.getElementById('ceo-chat-btn');
    chatBtn?.classList.toggle('active', this._currentCeoProject === this._EA_CHAT || !this._currentCeoProject);

    for (const s of sessions) {
      // Skip EA chat session from project list (it has its own Chat button)
      if (s.project_id === this._EA_CHAT) continue;
      const item = document.createElement('div');
      const hasPending = s.has_pending;
      item.className = 'ceo-proj-item' + (this._currentCeoProject === s.project_id ? ' active' : '') + (hasPending ? ' has-pending' : '');
      const basePid = (s.project_id || '').split('/')[0];
      const name = projectNames[basePid] || basePid;
      const display = name.length > 14 ? name.substring(0, 14) + '\u2026' : name;
      const badge = hasPending ? '<span class="ceo-proj-pending">●</span>' : '';
      item.innerHTML = badge + this._escHtml(display);
      item.addEventListener('click', () => {
        chatBtn?.classList.remove('active');
        this._selectCeoProject(s.project_id);
      });
      listEl.appendChild(item);
    }

    // Render actions pinned at bottom
    // Also refresh 1-on-1 list
    this._refreshOneononeList();
  }

  async _refreshOneononeList() {
    const container = document.getElementById('ceo-oneonone-items');
    if (!container) return;

    let convs = [];
    try {
      const resp = await fetch('/api/conversations?type=oneonone');
      convs = (await resp.json()).conversations || [];
    } catch (e) { /* ignore */ }

    // Filter to active only
    convs = convs.filter(c => c.phase === 'active');

    container.innerHTML = '';
    if (!convs.length) {
      const empty = document.createElement('div');
      empty.className = 'ceo-section-header';
      empty.style.fontStyle = 'italic';
      empty.textContent = 'No active sessions';
      container.appendChild(empty);
      return;
    }

    for (const conv of convs) {
      const item = document.createElement('div');
      const empName = this._resolveEmployeeNickname(conv.employee_id);
      item.className = 'ceo-oneonone-item' + (this._currentConvId === conv.id ? ' active' : '');
      item.textContent = empName;
      item.title = `1-on-1 with ${empName}`;
      item.addEventListener('click', () => this._openOneononeInTerminal(conv));
      container.appendChild(item);
    }
  }

  async _openOneononeInTerminal(conv) {
    // Clear project selection
    this._currentCeoProject = null;
    this._currentConvId = conv.id;
    this._currentConvType = 'oneonone';
    this._currentConvEmployeeId = conv.employee_id;

    // Update active states in lists
    document.querySelectorAll('.ceo-proj-item').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.ceo-oneonone-item').forEach(el => el.classList.remove('active'));
    // Find and activate this one
    const items = document.querySelectorAll('.ceo-oneonone-item');
    items.forEach(el => {
      if (el.textContent === this._resolveEmployeeName(conv.employee_id)) {
        el.classList.add('active');
      }
    });

    // Load conversation messages
    let messages = [];
    try {
      const resp = await fetch(`/api/conversation/${conv.id}/messages`);
      const data = await resp.json();
      messages = data.messages || [];
    } catch (e) {
      console.error('Failed to load conversation messages:', e);
    }

    // Convert to terminal format
    const empNick = this._resolveEmployeeNickname(conv.employee_id);
    const history = messages.map(m => ({
      role: m.sender === 'ceo' ? 'ceo' : 'system',
      text: m.text || '',
      source: m.sender === 'ceo' ? undefined : empNick,
    }));

    this._ceoTerm?.showChat(`1on1:${empNick}`, history);
  }

  async _endOneononeFromTerminal() {
    const convId = this._currentConvId;
    if (!convId) return;

    this._ceoTerm?.appendMessage({ role: 'system', text: 'Ending 1-on-1... employee is reflecting on the conversation...', source: 'system' });
    this.logEntry('SYSTEM', 'Ending 1-on-1... employee is reflecting on the conversation...', 'system');

    try {
      const resp = await fetch(`/api/conversation/${convId}/close?wait_hooks=true`, {
        method: 'POST',
      }).then(r => r.json()).catch(() => ({}));

      if (resp.hook_result) {
        const hr = resp.hook_result;
        const empName = this._resolveEmployeeNickname(resp.employee_id || this._currentConvEmployeeId || '');
        if (hr.principles_updated) {
          this._ceoTerm?.appendMessage({ role: 'system', text: `${empName} updated their work principles based on the meeting.`, source: 'system' });
          this.logEntry('SYSTEM', `${empName} updated their work principles based on the meeting.`, 'system');
        }
        if (hr.note_saved) {
          this._ceoTerm?.appendMessage({ role: 'system', text: `1-on-1 note saved to ${empName}'s guidance record.`, source: 'system' });
          this.logEntry('SYSTEM', `1-on-1 note saved to ${empName}'s guidance record.`, 'system');
        }
        if (!hr.principles_updated && !hr.note_saved) {
          this._ceoTerm?.appendMessage({ role: 'system', text: `1-on-1 ended (no reflection generated).`, source: 'system' });
        }
      } else {
        this._ceoTerm?.appendMessage({ role: 'system', text: '1-on-1 ended.', source: 'system' });
      }
    } catch (e) {
      this._ceoTerm?.appendMessage({ role: 'system', text: `Failed to end 1-on-1: ${e.message}`, source: 'system' });
    }

    // Clear 1-on-1 state
    this._currentConvId = null;
    this._currentConvType = null;
    this._currentConvEmployeeId = null;
    this._refreshCeoProjectList();
  }

  async _selectCeoProject(projectId) {
    this._currentCeoProject = projectId;
    this._currentConvId = null;
    this._currentConvType = null;
    this._currentConvEmployeeId = null;
    this._refreshCeoProjectList();

    if (!projectId) {
      this._ceoTerm?.showChat(null, []);
      return;
    }

    try {
      const resp = await fetch(`/api/ceo/sessions/${encodeURIComponent(projectId)}`);
      if (!resp.ok) {
        this._ceoTerm?.showChat(projectId, []);
        return;
      }
      const data = await resp.json();
      this._ceoTerm?.showChat(projectId, data.history || []);
    } catch (e) {
      this._ceoTerm?.showChat(projectId, []);
    }
  }

  async _loadModelOrApiKeySection(empId) {
    const container = document.getElementById('emp-settings-container');
    container.innerHTML = '<div style="color:var(--text-dim);font-size:6px;padding:4px;">Loading settings...</div>';

    try {
      const empResp = await fetch(`/api/employee/${empId}?_t=${Date.now()}`).then(r => r.json());
      const manifest = empResp.manifest;

      if (empResp.hosting === 'self') {
        // Claude Session — show login status instead of model picker
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
        // Deduplicate sections by id (first occurrence wins)
        const seenIds = new Set();
        const dedupSections = [];
        for (const s of manifest.settings.sections) {
          if (s.id && seenIds.has(s.id)) continue;
          if (s.id) seenIds.add(s.id);
          dedupSections.push(s);
        }
        for (const section of dedupSections) {
          const sectionEl = document.createElement('div');
          sectionEl.className = 'emp-settings-section';
          if (section.title) {
            sectionEl.innerHTML = `<div class="emp-settings-title">${this._escHtml(section.title)}</div>`;
          }
          for (const field of section.fields) {
            const fieldEl = this._renderManifestField(field, empResp, empId);
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

  _renderManifestField(field, empData, empId) {
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
      if (field.placeholder) ta.placeholder = field.placeholder;
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
    } else if (field.type === 'action_button') {
      const btn = document.createElement('button');
      btn.className = 'pixel-btn small';
      btn.textContent = field.label || 'Run';
      btn.dataset.fieldKey = field.key;
      btn.dataset.fieldType = 'action_button';
      btn.dataset.action = field.action || '';
      btn.dataset.cvField = field.cv_field || '';
      btn.addEventListener('click', (e) => {
        const sectionEl = e.target.closest('.emp-settings-section');
        this._handleManifestAction(field, empId, sectionEl);
      });
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
      if (type === 'action_button') continue;
      if (key.startsWith('_')) continue;  // internal/ephemeral fields (e.g. _cv_json)
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
      // Save hosting (agent family) via hosting endpoint — hot-swap, no restart
      if ('hosting' in payload) {
        const resp = await fetch(`/api/employee/${empId}/hosting`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hosting: payload.hosting }),
        }).then(r => r.json());
        if (resp.status === 'updated') {
          this.logEntry('SYSTEM', `Agent family switched to "${payload.hosting}". Active immediately.`, 'system');
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

  /** Registry of manifest action handlers keyed by action name. */
  _manifestActions = {
    hire_from_cv: async (field, empId, sectionEl) => {
      const scope = sectionEl || document.getElementById('emp-settings-container');
      const cvEl = scope.querySelector(`[data-field-key="${field.cv_field}"]`);
      if (!cvEl || !cvEl.value.trim()) {
        this.logEntry('SYSTEM', 'Please paste a CV JSON before clicking Hire.', 'system');
        return;
      }
      let cv;
      try {
        cv = JSON.parse(cvEl.value.trim());
      } catch {
        this.logEntry('SYSTEM', 'Invalid JSON in CV field.', 'system');
        return;
      }
      const btn = scope.querySelector(`[data-field-key="${field.key}"]`);
      btn.disabled = true;
      btn.textContent = 'Hiring...';
      try {
        const resp = await fetch('/api/candidates/hire-from-cv', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cv }),
        }).then(r => r.json());
        if (resp.error) {
          this.logEntry('SYSTEM', `Hire failed: ${this._escHtml(resp.error)}`, 'system');
        } else {
          this.logEntry('CEO', `Onboarding started for ${this._escHtml(resp.name)} (${this._escHtml(resp.role)})`, 'ceo');
          cvEl.value = '';
        }
      } catch (err) {
        this.logEntry('SYSTEM', `Hire request failed: ${this._escHtml(err.message)}`, 'system');
      } finally {
        btn.disabled = false;
        btn.textContent = field.label || 'Hire';
      }
    },
  };

  async _handleManifestAction(field, empId, sectionEl) {
    const handler = this._manifestActions[field.action];
    if (!handler) {
      console.error(`[manifest] Unknown action: ${field.action}`);
      return;
    }
    await handler(field, empId, sectionEl);
  }

  _renderSelfHostedSection(empId, empData, container) {
    const sessions = empData.sessions || [];
    const hasActive = sessions.some(s => s.status === 'running');
    const statusColor = hasActive ? 'var(--pixel-green)' : 'var(--pixel-yellow)';
    const statusText = hasActive ? 'Active' : (sessions.length > 0 ? 'Idle' : 'No sessions');
    const currentModel = empData.llm_model || 'opus';
    const claudeModels = [
      { id: 'opus', label: 'Claude Opus' },
      { id: 'sonnet', label: 'Claude Sonnet' },
    ];

    const section = document.createElement('div');
    section.className = 'emp-detail-section-content';
    section.style.cssText = 'display:flex;flex-direction:column;gap:3px;';
    section.innerHTML = `
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:55px;">Agent Family</span>
        <span style="font-size:6px;color:var(--pixel-cyan);">Claude Session</span>
      </div>
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:55px;">Model</span>
        <select id="emp-self-hosted-model" class="emp-model-select" style="flex:1;">
          ${claudeModels.map(m => `<option value="${m.id}" ${m.id === currentModel ? 'selected' : ''}>${m.label}</option>`).join('')}
        </select>
      </div>
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="font-size:6px;color:var(--pixel-yellow);min-width:55px;">Status</span>
        <span style="font-size:6px;color:${statusColor};">${statusText}</span>
      </div>
      ${sessions.length > 0 ? `<div style="font-size:5px;color:var(--text-dim);margin-top:2px;">${sessions.length} session(s)</div>` : ''}
    `;
    container.appendChild(section);

    const modelSelect = section.querySelector('#emp-self-hosted-model');
    modelSelect.addEventListener('change', async () => {
      const newModel = modelSelect.value;
      modelSelect.disabled = true;
      try {
        await fetch(`/api/employee/${empId}/model`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: newModel }),
        });
        this.logEntry('CEO', `Updated self-hosted employee #${empId} model to ${newModel}`, 'ceo');
      } catch (e) {
        console.error('Failed to save model:', e);
      } finally {
        modelSelect.disabled = false;
      }
    });
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

    // Wire save button to saveEmployeeModel
    document.getElementById('emp-model-save-btn').addEventListener('click', () => this.saveEmployeeModel());
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

  async _restorePendingCandidates() {
    try {
      const resp = await fetch('/api/candidates/pending');
      const data = await resp.json();
      const batches = data.batches || {};
      for (const [batchId, batch] of Object.entries(batches)) {
        const candidates = batch.candidates || [];
        if (candidates.length > 0) {
          this.showCandidateSelection({
            batch_id: batchId,
            candidates,
            roles: batch.roles || [],
          });
          break;  // Show one batch at a time
        }
      }
    } catch (e) {
      console.debug('[bootstrap] No pending candidates:', e);
    }
  }

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

    for (const roleGroup of this._candidateRoles) {
      const section = document.createElement('div');
      section.className = 'role-group';

      const roleEmoji = ROLE_EMOJI[roleGroup.role] || '🤖';
      const candidateCount = (roleGroup.candidates || []).length;

      const esc = this._escapeHtml;
      section.innerHTML = `
        <div class="role-group-header">
          <span class="role-group-icon">${roleEmoji}</span>
          <span class="role-group-title">${esc(roleGroup.role)}</span>
          <span class="role-group-count">${candidateCount}</span>
          ${roleGroup.description ? `<span class="role-group-desc">${esc(roleGroup.description)}</span>` : ''}
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
        const scorePct = Math.min(Math.round(score * 100), 100);
        const scoreColor = scorePct >= 80 ? 'var(--pixel-green)' : scorePct >= 50 ? 'var(--pixel-yellow)' : 'var(--pixel-red)';
        const reasoning = c.reasoning || '';

        const esc = this._escapeHtml;
        const llmModel = c.llm_model || 'default';
        const costPer1m = esc(c.cost_per_1m_tokens ? `$${Number(c.cost_per_1m_tokens).toFixed(2)}/1M` : (c.salary_per_1m_tokens ? `$${Number(c.salary_per_1m_tokens).toFixed(2)}/1M` : 'N/A'));
        const hiringFee = esc(c.hiring_fee != null ? `$${Number(c.hiring_fee).toFixed(2)}` : 'Free');
        const hosting = c.hosting || 'company';
        const familyLabels = { company: '🧠 LangChain', self: '🤖 Claude', openclaw: '🦞 OpenClaw' };
        const hostingLabel = esc(familyLabels[hosting] || hosting);
        const authLabel = esc(c.auth_method === 'oauth' ? 'OAuth' : 'API Key');

        card.innerHTML = `
          <div class="card-inner">
            <div class="card-front">
              <div class="card-select-indicator"></div>
              <div class="card-avatar">${emoji}</div>
              <div class="card-name">${esc(c.name)}</div>
              <div class="card-role">${esc(c.role)}</div>
              <div class="card-model" title="${esc(llmModel)}">🤖 ${esc(llmModel.split('/').pop())}</div>
              <div class="card-tags">${esc(tags)}</div>
              <div class="card-score-bar">
                <div class="score-fill" style="width:${scorePct}%;background:${scoreColor};"></div>
                <span class="score-label">${scorePct}%</span>
              </div>
              ${reasoning ? `<div class="card-reasoning" title="${esc(reasoning)}">${esc(reasoning.substring(0, 40))}${reasoning.length > 40 ? '...' : ''}</div>` : ''}
              <div class="card-cost">${costPer1m} | ${hiringFee}</div>
              <div class="card-hosting">${hostingLabel}</div>
            </div>
            <div class="card-back">
              <div class="card-detail-title">Skills</div>
              <div class="card-detail-text">${esc(skills) || 'N/A'}</div>
              <div class="card-detail-title">Tools</div>
              <div class="card-detail-text">${esc(tools) || 'N/A'}</div>
              <div class="card-detail-title">LLM</div>
              <div class="card-detail-text">${esc(llmModel)} (${esc(c.api_provider || 'openrouter')})</div>
              <div class="card-detail-title">Cost</div>
              <div class="card-detail-text">${costPer1m} | Fee: ${hiringFee}</div>
              <div class="card-detail-title">Agent Family</div>
              <div class="card-detail-text">${hostingLabel} | Auth: ${authLabel}</div>
            </div>
          </div>
        `;

        // Click card to toggle selection; detail button opens detail panel
        card.addEventListener('click', (e) => {
          if (e.target.closest('.pixel-btn')) return;
          this._toggleCandidateSelection(cid, c, roleGroup.role, card);
        });

        // "Details" button below the card
        const detailBtn = document.createElement('button');
        detailBtn.className = 'pixel-btn card-detail-btn';
        detailBtn.textContent = '📋 Details';
        detailBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          this._showCandidateDetail(cid, c, roleGroup.role, card);
        });

        // Wrap card + detail button in a container
        const cardWrapper = document.createElement('div');
        cardWrapper.className = 'candidate-card-wrapper';
        cardWrapper.appendChild(card);
        cardWrapper.appendChild(detailBtn);
        cardsContainer.appendChild(cardWrapper);
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
    const esc = (s) => this._escapeHtml(s || '');
    const emoji = ROLE_EMOJI[c.role] || '🤖';
    const skills = (c.skill_set || c.skills || []).map(s => {
      if (typeof s === 'object') return `<span class="detail-skill">${esc(s.name)} <em>${esc(s.proficiency)}</em></span>`;
      return `<span class="detail-skill">${esc(s)}</span>`;
    }).join('');
    const tools = (c.tool_set || []).map(t => {
      if (typeof t === 'object') return `<span class="detail-tool">${esc(t.name)}</span>`;
      return `<span class="detail-tool">${esc(t)}</span>`;
    }).join('');
    const tags = (c.personality_tags || []).map(t => `<span class="detail-tag">${esc(t)}</span>`).join('');
    const score = c.score || c.jd_relevance || 0;
    const scorePct = Math.min(Math.round(score * 100), 100);
    const scoreColor = scorePct >= 80 ? 'var(--pixel-green)' : scorePct >= 50 ? 'var(--pixel-yellow)' : 'var(--pixel-red)';
    const llmModel = c.llm_model || 'default';
    const costPer1m = esc(c.cost_per_1m_tokens ? `$${Number(c.cost_per_1m_tokens).toFixed(2)}/1M` : (c.salary_per_1m_tokens ? `$${Number(c.salary_per_1m_tokens).toFixed(2)}/1M` : 'N/A'));
    const hiringFee = esc(c.hiring_fee != null ? `$${Number(c.hiring_fee).toFixed(2)}` : 'Free');
    const hosting = c.hosting || 'company';
    const familyLabels = { company: '🧠 LangChain', self: '🤖 Claude', openclaw: '🦞 OpenClaw' };
    const hostingLabel = esc(familyLabels[hosting] || hosting);
    const authLabel = esc(c.auth_method === 'oauth' ? 'OAuth' : 'API Key');
    const reasoning = c.reasoning || '';

    content.innerHTML = `
      <div class="detail-header">
        <div class="detail-avatar">${emoji}</div>
        <div class="detail-name-block">
          <div class="detail-name">${esc(c.name)}</div>
          <div class="detail-role">${esc(c.role)}</div>
        </div>
        <div class="detail-score" style="border-color:${scoreColor}">
          <span style="color:${scoreColor}">${scorePct}%</span>
          <small>match</small>
        </div>
      </div>
      ${reasoning ? `<div class="detail-section"><div class="detail-label">Match Reasoning</div><div class="detail-text">${esc(reasoning)}</div></div>` : ''}
      ${tags ? `<div class="detail-section"><div class="detail-label">Personality</div><div class="detail-tags-list">${tags}</div></div>` : ''}
      <div class="detail-section"><div class="detail-label">Skills</div><div class="detail-skills-list">${skills || '<em>N/A</em>'}</div></div>
      ${tools ? `<div class="detail-section"><div class="detail-label">Tools</div><div class="detail-tools-list">${tools}</div></div>` : ''}
      <div class="detail-section detail-grid">
        <div><div class="detail-label">LLM Model</div><div class="detail-text">🤖 ${esc(llmModel)}</div></div>
        <div><div class="detail-label">Provider</div><div class="detail-text">${esc(c.api_provider || 'openrouter')}</div></div>
        <div><div class="detail-label">Cost</div><div class="detail-text">${costPer1m}</div></div>
        <div><div class="detail-label">Hiring Fee</div><div class="detail-text">${hiringFee}</div></div>
        <div><div class="detail-label">Agent Family</div><div class="detail-text">${hostingLabel}</div></div>
        <div><div class="detail-label">Auth</div><div class="detail-text">${authLabel}</div></div>
      </div>
      ${c.description_md ? `<div class="detail-section"><div class="detail-label">Description</div><div class="detail-description md-rendered">${this._renderMarkdown(c.description_md)}</div></div>` : ''}
    `;

    // Wire up panel buttons
    const interviewBtn = document.getElementById('detail-interview-btn');
    const selectBtn = document.getElementById('detail-select-btn');
    const closeBtn = document.getElementById('detail-panel-close');

    const isSelected = this._selectedCandidates.has(candidateId);
    selectBtn.textContent = isSelected ? '✗ Deselect' : '✔ Select';
    selectBtn.className = isSelected ? 'pixel-btn danger' : 'pixel-btn secondary';

    // Replace buttons to remove stale listeners from prior calls
    interviewBtn.replaceWith(interviewBtn.cloneNode(true));
    selectBtn.replaceWith(selectBtn.cloneNode(true));
    closeBtn.replaceWith(closeBtn.cloneNode(true));
    const newInterviewBtn = document.getElementById('detail-interview-btn');
    const newSelectBtn = document.getElementById('detail-select-btn');
    const newCloseBtn = document.getElementById('detail-panel-close');
    newSelectBtn.textContent = isSelected ? '✗ Deselect' : '✔ Select';
    newSelectBtn.className = isSelected ? 'pixel-btn danger' : 'pixel-btn secondary';
    // Only remote (self-hosted) candidates support interview
    const isRemote = (c.hosting === 'self');
    if (!isRemote) {
      newInterviewBtn.disabled = true;
      newInterviewBtn.title = 'For security reasons, only remote (self-hosted) employees support interview';
      newInterviewBtn.textContent = '🔒 Interview';
    } else {
      newInterviewBtn.disabled = false;
      newInterviewBtn.title = '';
      newInterviewBtn.textContent = '💬 Interview';
    }
    newInterviewBtn.addEventListener('click', () => {
      if (!isRemote) return;
      this.startInterview(c);
    });
    newSelectBtn.addEventListener('click', () => {
      this._toggleCandidateSelection(candidateId, c, role, cardEl);
      const nowSelected = this._selectedCandidates.has(candidateId);
      newSelectBtn.textContent = nowSelected ? '✗ Deselect' : '✔ Select';
      newSelectBtn.className = nowSelected ? 'pixel-btn danger' : 'pixel-btn secondary';
    });
    newCloseBtn.addEventListener('click', () => {
      panel.classList.add('hidden');
      cardEl.classList.remove('detail-active');
    });

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

    // Reset detail panel state so it doesn't flash stale content on reopen
    const detailPanel = document.getElementById('candidate-detail-panel');
    if (detailPanel) detailPanel.classList.add('hidden');
    document.querySelectorAll('.candidate-card.detail-active').forEach(el => el.classList.remove('detail-active'));
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
  }

  loadProjectList() {
    const listEl = document.getElementById('project-list');
    listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">Loading...</div>';
    listEl.classList.remove('hidden');
    document.getElementById('project-detail').classList.add('hidden');

    fetch('/api/projects/named')
      .then(r => r.json())
      .then(data => {
        const projects = this._sortProjectsNewestFirst(data.projects || []);
        if (projects.length === 0) {
          listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">No project records</div>';
          return;
        }
        listEl.innerHTML = '';
        projects.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
        for (const p of projects) {
          const card = document.createElement('div');
          card.className = 'project-card';
          const statusIcon = p.status === 'completed' ? '\u2705' : (p.status === 'archived' ? '\uD83D\uDCE6' : '\uD83D\uDD04');
          const date = p.created_at ? p.created_at.substring(0, 10) : '';
          const costStr = p.cost_usd ? ` · $${p.cost_usd.toFixed(3)}` : '';
          const name = p.name || p.task || p.project_id;
          card.innerHTML = `
            <div class="project-card-header">
              <span>${statusIcon} ${this._escHtml(name.substring(0, 50))}${name.length > 50 ? '...' : ''}</span>
              <span class="project-card-date">${date}</span>
            </div>
            <div class="project-card-meta">
              ${p.iteration_count || 0} iteration(s) | ${p.file_count || 0} files${costStr}${p.current_owner ? ' · ' + this._escHtml(p.current_owner) : ''}
            </div>
          `;
          card.style.cursor = 'pointer';
          card.addEventListener('click', () => {
            // Show iteration split view (same as sidebar)
            this._showProjectInModal(p.project_id);
          });
          listEl.appendChild(card);
        }
      })
      .catch(err => {
        listEl.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;">Load failed: ${this._escHtml(err.message)}</div>`;
      });
  }

  _showProjectInModal(projectId) {
    // Reuse _openProjectDetail logic but stay inside the already-open modal
    const listEl = document.getElementById('project-list');
    const detailEl = document.getElementById('project-detail');
    const contentEl = document.getElementById('project-detail-content');
    listEl.classList.add('hidden');
    detailEl.classList.remove('hidden');
    contentEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">Loading...</div>';

    fetch(`/api/projects/named/${encodeURIComponent(projectId)}`)
      .then(r => r.json())
      .then(proj => {
        if (proj.error) {
          contentEl.innerHTML = `<div style="color:var(--pixel-red);">${this._escHtml(proj.error)}</div>`;
          return;
        }
        this._renderProjectDetail(projectId, proj, contentEl);
      })
      .catch(err => {
        contentEl.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;">Load failed: ${this._escHtml(err.message)}</div>`;
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
              html += `<div style="color:var(--pixel-white);margin-top:1px;">${this._escHtml(entry.detail)}</div>`;
            }
            html += `</div>`;
          }
        }

        // Cost & Budget
        const cost = doc.cost || {};
        if (cost.actual_cost_usd > 0 || cost.budget_estimate_usd > 0) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 4px;">Cost & Budget: <span style="font-size:5px;color:var(--text-dim);">(estimated)</span></div>`;
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

        // Documents
        const files = doc.files || [];
        if (files.length > 0) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 4px;">Documents (${files.length}):</div>`;
          for (const f of files) {
            const url = `/api/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(f)}`;
            const safeUrl = this._escHtml(url);
            const safeFile = this._escHtml(f);
            const encodedUrl = encodeURIComponent(url);
            const encodedFile = encodeURIComponent(f);
            html += `<div style="font-size:6px;margin:2px 0;">`;
            html += `<a href="${safeUrl}" class="project-doc-link" data-url="${encodedUrl}" data-file="${encodedFile}" style="color:var(--pixel-green);text-decoration:underline;cursor:pointer;">${safeFile}</a>`;
            html += `</div>`;
          }
        }

        contentEl.innerHTML = html;
        contentEl.querySelectorAll('.project-doc-link').forEach((link) => {
          link.addEventListener('click', (e) => {
            e.preventDefault();
            const rawUrl = decodeURIComponent(link.dataset.url || '');
            const rawFile = decodeURIComponent(link.dataset.file || '');
            window._ceoViewFile(rawUrl, rawFile);
          });
        });
      })
      .catch(err => {
        contentEl.innerHTML = `<div style="color:var(--pixel-red);">Load failed: ${this._escHtml(err.message)}</div>`;
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
        }).catch(err => console.error('[credentials submit] failed:', err));
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
            fetch(btn.callback_url, { method: 'POST' }).catch(err => console.error('[button callback] failed:', err));
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

    // Remove stale agenda from previous meeting
    const oldAgenda = document.getElementById('meeting-agenda-list');
    if (oldAgenda) oldAgenda.parentElement.remove();

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

    // Show CEO input if room is booked (meeting in progress)
    const ceoInputArea = document.getElementById('meeting-ceo-input-area');
    if (room.is_booked) {
      ceoInputArea.classList.remove('hidden');
    } else {
      ceoInputArea.classList.add('hidden');
    }

    // Restore cached agenda for this room (survives modal close/reopen)
    const cachedAgenda = this._meetingAgendaCache[room.id];
    if (cachedAgenda && cachedAgenda.items && cachedAgenda.items.length > 0) {
      this._renderMeetingAgenda(cachedAgenda);
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
      .catch(err => {
        console.error('[loadChat] failed:', err);
        chatEl.innerHTML = '<div class="chat-empty">Failed to load chat</div>';
      });
  }

  // ===== Meeting Minutes =====
  openMeetingMinutes(room) {
    const modal = document.getElementById('meeting-modal');
    modal.classList.remove('hidden');
    document.getElementById('meeting-modal-title').textContent = `Meeting Minutes: ${room.name}`;

    // Reuse meeting modal body area for minutes list
    const chatEl = document.getElementById('meeting-chat-messages');
    chatEl.innerHTML = '<div class="chat-empty">Loading minutes...</div>';

    // Hide CEO input and info panel clutter
    const ceoInputArea = document.getElementById('meeting-ceo-input-area');
    if (ceoInputArea) ceoInputArea.classList.add('hidden');

    fetch(`/api/rooms/${encodeURIComponent(room.id)}/minutes`)
      .then(r => r.json())
      .then(minutes => {
        if (!minutes || minutes.length === 0) {
          chatEl.innerHTML = '<div class="chat-empty">No archived meetings</div>';
          return;
        }
        chatEl.innerHTML = '';
        const list = document.createElement('div');
        list.className = 'meeting-minutes-list';
        for (const m of minutes) {
          const card = document.createElement('div');
          card.className = 'meeting-minute-card';
          const dateStr = m.minute_id ? m.minute_id.split('_').slice(-2).join(' ') : '';
          card.innerHTML = `
            <div class="meeting-minute-topic">${this._escHtml(m.topic || 'Untitled')}</div>
            <div class="meeting-minute-meta">${m.message_count || 0} messages · ${m.participants?.length || 0} participants · ${dateStr}</div>
          `;
          card.style.cursor = 'pointer';
          card.addEventListener('click', () => this._showMeetingMinuteDetail(m.minute_id));
          list.appendChild(card);
        }
        chatEl.appendChild(list);
      })
      .catch(err => {
        console.error('[meetingMinutes] failed:', err);
        chatEl.innerHTML = '<div class="chat-empty">Failed to load minutes</div>';
      });
  }

  _showMeetingMinuteDetail(minuteId) {
    const chatEl = document.getElementById('meeting-chat-messages');
    chatEl.innerHTML = '<div class="chat-empty">Loading...</div>';

    fetch(`/api/meeting-minutes/${encodeURIComponent(minuteId)}`)
      .then(r => r.json())
      .then(data => {
        if (!data || data.error) {
          chatEl.innerHTML = '<div class="chat-empty">Not found</div>';
          return;
        }
        chatEl.innerHTML = '';
        // Back button
        const backBtn = document.createElement('button');
        backBtn.textContent = 'Back to list';
        backBtn.className = 'btn-small';
        backBtn.style.marginBottom = '8px';
        backBtn.addEventListener('click', () => {
          // Re-open the minutes list for this room
          this.openMeetingMinutes({ id: data.room_id, name: data.room_name || data.room_id });
        });
        chatEl.appendChild(backBtn);

        // Summary
        if (data.summary) {
          const summaryEl = document.createElement('div');
          summaryEl.className = 'meeting-minute-detail';
          summaryEl.innerHTML = `<h4>Summary</h4><pre>${this._escHtml(data.summary)}</pre>`;
          chatEl.appendChild(summaryEl);
        }

        // Chat messages
        const messages = data.messages || [];
        for (const msg of messages) {
          this._appendChatMessage(msg);
        }
      })
      .catch(err => {
        console.error('[minuteDetail] failed:', err);
        chatEl.innerHTML = '<div class="chat-empty">Failed to load</div>';
      });
  }

  _refreshMeetingModalStatus(room) {
    const led = document.getElementById('meeting-modal-status-led');
    const statusText = document.getElementById('meeting-modal-status-text');
    const ceoInputArea = document.getElementById('meeting-ceo-input-area');
    if (room.is_booked) {
      led.className = 'status-led booked';
      statusText.textContent = 'In Meeting';
      ceoInputArea.classList.remove('hidden');
    } else {
      led.className = 'status-led free';
      statusText.textContent = 'Available';
      ceoInputArea.classList.add('hidden');
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
    document.getElementById('meeting-ceo-input-area').classList.add('hidden');
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

  _renderMeetingAgenda(data) {
    let agendaEl = document.getElementById('meeting-agenda-list');
    if (!agendaEl) {
      // Create agenda container in the info panel
      const infoPanel = document.getElementById('meeting-info-panel');
      if (!infoPanel) return;
      const block = document.createElement('div');
      block.className = 'meeting-info-block';
      block.innerHTML = '<div class="meeting-info-label">Agenda</div><div id="meeting-agenda-list" class="meeting-agenda-list"></div>';
      infoPanel.appendChild(block);
      agendaEl = document.getElementById('meeting-agenda-list');
    }
    const items = data.items || [];
    const current = data.current_index;
    const completed = new Set(data.completed || []);
    agendaEl.innerHTML = items.map((item, i) => {
      const done = completed.has(i);
      const active = i === current;
      const cls = done ? 'agenda-done' : active ? 'agenda-active' : 'agenda-pending';
      const icon = done ? '✅' : active ? '▶' : '⬜';
      return `<div class="agenda-item ${cls}">${icon} ${this._escHtml(item)}</div>`;
    }).join('');
  }

  async _sendMeetingRoomMessage() {
    const input = document.getElementById('meeting-ceo-input');
    const text = input.value.trim();
    if (!text || !this.viewingRoomId) return;
    input.value = '';
    try {
      await fetch(`/api/rooms/${encodeURIComponent(this.viewingRoomId)}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
    } catch (e) {
      console.error('Failed to send meeting room message:', e);
    }
    // Message appears via WebSocket meeting_chat event
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
        listEl.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;">Load failed: ${this._escHtml(err.message)}</div>`;
      });
  }

  _renderExEmployees(exEmps) {
    const listEl = document.getElementById('ex-employee-list');
    if (exEmps.length === 0) {
      listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">No ex-employees</div>';
      return;
    }
    listEl.innerHTML = '';
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
          <div class="dash-title">\u{1F4B0} Cost Overview <span style="font-size:5px;color:var(--text-dim);">(estimated, subject to actual billing)</span></div>
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
        const catLabels = {oneonone:'1-on-1', meeting:'Meeting', routine:'Routine', interview:'Interview', agent_task:'Agent Task', history_compress:'History Compress', completion_check:'Completion Check', nickname_gen:'Nickname Gen', remote_worker:'Remote Worker'};
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
    }).catch(err => console.error('[loadCostPanel] failed:', err));
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
      .catch(err => {
        console.error('[loadCulture] failed:', err);
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
      .catch(err => { console.error('[addCultureItem] failed:', err); input.value = ''; });
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
    if (!this._checkCooldown('enrichDirection')) return;
    btn.disabled = true;
    btn.textContent = '⏳ Sending...';

    const task = `The CEO has drafted a company direction statement. Please polish and expand it into a complete corporate positioning description, preserving the core message while adding strategic vision, target market, core competencies, and other dimensions. Once polished, dispatch to COO to save via deposit_company_knowledge(category="direction").\n\nDraft content:\n${draft}`;

    const formData = new FormData();
    formData.append('task', task);
    fetch('/api/ceo/task', {
      method: 'POST',
      body: formData,
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
    if (!bar) return;
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

  /** Show a simple alert modal. htmlContent must be pre-sanitized (use _escapeHtml). */
  _showAlertModal(title, textContent) {
    // Legacy shim — renders plain text (HTML tags shown literally in xterm)
    this._showXtermAlert(title, [textContent]);
  }

  _showXtermAlert(title, lines) {
    let overlay = document.getElementById('alert-modal-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'alert-modal-overlay';
      overlay.className = 'modal-overlay';
      overlay.innerHTML = `
        <div class="modal-content alert-modal-content">
          <div class="modal-header">
            <h3 class="pixel-title" id="alert-modal-title"></h3>
            <button class="modal-close" id="alert-modal-close">\u2715</button>
          </div>
          <div id="alert-modal-body" class="alert-modal-body" style="background:#0a0a0a;padding:0;"></div>
          <div class="alert-modal-footer">
            <button class="pixel-btn" id="alert-modal-ok">OK</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      const close = () => {
        overlay.classList.add('hidden');
        if (this._alertXterm) { this._alertXterm.dispose(); this._alertXterm = null; }
      };
      overlay.querySelector('#alert-modal-close').addEventListener('click', close);
      overlay.querySelector('#alert-modal-ok').addEventListener('click', close);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    }
    overlay.querySelector('#alert-modal-title').textContent = title;
    const body = overlay.querySelector('#alert-modal-body');
    body.innerHTML = '';
    if (this._alertXterm) { this._alertXterm.dispose(); this._alertXterm = null; }
    this._alertXterm = new XTermLog(body, { fontSize: 12 });
    for (const line of lines) {
      this._alertXterm.writeln(line);
    }
    overlay.classList.remove('hidden');
  }

  // ----------- Unified Conversation Methods -----------

  async _openConversation(convId) {
    const chatContainer = document.getElementById('right-panel-chat');

    // Hide CEO Console + CEO Inbox sections only, keep Activity Log visible
    for (const target of ['ceo-body']) {
      const body = document.getElementById(target);
      if (body) body.style.display = 'none';
      // Hide the corresponding collapsible header
      const header = body?.previousElementSibling;
      if (header?.classList.contains('collapsible-header')) header.style.display = 'none';
    }
    chatContainer.classList.remove('hidden');

    if (!this._chatPanel) {
      this._chatPanel = new ChatPanel(chatContainer);
    }
    // Always re-wire callbacks (may switch between 1-on-1 and inbox)
    this._chatPanel.onSend((id, text, attachments) => this._sendConversationMessage(id, text, attachments));
    this._chatPanel.onClear((id) => this._clearConversationHistory(id));
    this._chatPanel.onClose((id) => this._closeConversation(id));

    // Fetch conversation + messages
    let convResp, msgsResp;
    try {
      [convResp, msgsResp] = await Promise.all([
        fetch(`/api/conversation/${convId}`).then(r => r.json()),
        fetch(`/api/conversation/${convId}/messages`).then(r => r.json()),
      ]);
    } catch (err) {
      console.error('Failed to load conversation:', err);
      chatContainer.classList.add('hidden');
      this._restoreConsoleSections();
      return;
    }

    const empName = this._resolveEmployeeName(convResp.employee_id);
    this._chatPanel.setConversation(convId, convResp.type, empName);
    this._chatPanel.renderMessages(msgsResp.messages);
    this._chatPanel.setInputEnabled(convResp.phase === 'active');
  }

  _resolveEmployeeName(employeeId) {
    // Try to find from office renderer state, then company state snapshot
    const sources = [
      window.officeRenderer?.state?.employees,
      window.app?._lastSnapshot?.employees,
    ];
    for (const employees of sources) {
      if (!Array.isArray(employees)) continue;
      const emp = employees.find(e => e.id === employeeId);
      if (emp) return emp.name || emp.nickname || employeeId;
    }
    return employeeId;
  }

  _resolveEmployeeNickname(employeeId) {
    // Returns "花名 (编号)" format for compact display
    const sources = [
      window.officeRenderer?.state?.employees,
      window.app?._lastSnapshot?.employees,
    ];
    for (const employees of sources) {
      if (!employees) continue;
      const list = Array.isArray(employees) ? employees : Object.values(employees);
      const emp = list.find(e => e.id === employeeId || e.employee_id === employeeId);
      if (emp) {
        const nick = emp.nickname || emp.name || employeeId;
        return nick === employeeId ? employeeId : `${nick} (${employeeId})`;
      }
    }
    return employeeId;
  }

  async _startOneononeConversation(employeeId) {
    const resp = await fetch('/api/conversation/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        type: 'oneonone', employee_id: employeeId, tools_enabled: true,
      }),
    });
    const conv = await resp.json();
    // Open 1-on-1 in the xterm terminal instead of ChatPanel
    await this._openOneononeInTerminal(conv);
    await this._refreshOneononeList();
  }

  async _sendConversationMessage(convId, text, attachments) {
    if (!this._chatPanel) return;
    this._chatPanel.showTyping(true);
    try {
      const resp = await fetch(`/api/conversation/${convId}/message`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text, attachments }),
      });
      if (!resp.ok) {
        const errText = await resp.text().catch(() => '');
        throw new Error(`Server error (${resp.status})${errText ? `: ${errText}` : ''}`);
      }
    } catch (err) {
      this._chatPanel.showTyping(false);
      alert(`Failed to send message: ${err.message}`);
    }
    // Reply arrives via WebSocket conversation_message event
  }

  async _clearConversationHistory(convId) {
    if (!this._chatPanel || !convId) return;
    if (this._chatPanel.getConvType() !== 'oneonone') return;

    const confirmed = confirm('Clear all 1-on-1 history for this employee? This cannot be undone.');
    if (!confirmed) return;

    try {
      const resp = await fetch(`/api/conversation/${convId}/clear`, { method: 'POST' });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || data.error || `Server error (${resp.status})`);
      }

      const msgsResp = await fetch(`/api/conversation/${convId}/messages`).then(r => r.json());
      this._chatPanel.renderMessages(msgsResp.messages || []);
      this._chatPanel.showTyping(false);

      const empName = this._resolveEmployeeName(data.employee_id || '');
      this.logEntry('SYSTEM', `🧹 Cleared 1-on-1 history for ${empName}.`, 'system');
    } catch (err) {
      alert(`Failed to clear history: ${err.message}`);
    }
  }

  async _closeConversation(convId) {
    if (!this._chatPanel) return;
    const convType = this._chatPanel.getConvType();
    const waitHooks = convType === 'oneonone';

    // Show reflection status for 1-on-1
    if (waitHooks) {
      this._chatPanel.setInputEnabled(false);
      this.logEntry('SYSTEM', 'Ending 1-on-1... employee is reflecting on the conversation...', 'system');
    }

    const resp = await fetch(`/api/conversation/${convId}/close?wait_hooks=${waitHooks}`, {
      method: 'POST',
    }).then(r => r.json()).catch(() => ({}));

    // Log 1-on-1 reflection results
    if (waitHooks && resp.hook_result) {
      const hr = resp.hook_result;
      const empName = this._resolveEmployeeName(resp.employee_id || '');
      if (hr.principles_updated) {
        this.logEntry('SYSTEM', `${empName} updated their work principles based on the meeting.`, 'system');
      }
      if (hr.note_saved) {
        this.logEntry('SYSTEM', `1-on-1 note saved to ${empName}'s guidance record.`, 'system');
      }
      if (!hr.principles_updated && !hr.note_saved) {
        this.logEntry('SYSTEM', `1-on-1 with ${empName} ended (no reflection generated).`, 'system');
      }
    }

    // Restore CEO Console + CEO Inbox sections
    const chatContainer = document.getElementById('right-panel-chat');
    chatContainer.classList.add('hidden');
    this._restoreConsoleSections();
    this._chatPanel = null;
  }

  _restoreConsoleSections() {
    for (const target of ['ceo-body']) {
      const body = document.getElementById(target);
      if (body) body.style.display = '';
      const header = body?.previousElementSibling;
      if (header?.classList.contains('collapsible-header')) header.style.display = '';
    }
    // Hide EA auto-reply toggle when returning to inbox list
    const eaToggle = document.getElementById('ea-autoreply-toggle');
    if (eaToggle) eaToggle.classList.add('hidden');
  }


  _showProjectReportModal(data) {
    const empName = data.employee_name || data.employee_id || '';
    const autoConfirmSec = data.auto_confirm_seconds || 120;
    const projectId = data.project_id || '';
    const overlay = document.createElement('div');
    overlay.className = 'project-report-overlay';
    overlay.innerHTML = `
      <div class="project-report-dialog">
        <div class="project-report-header">📊 ${this._escHtml(data.subject || 'Project Report')}</div>
        <div class="project-report-body">${this._renderMarkdown(data.report || '')}</div>
        <div class="project-report-meta">
          <span>Employee: ${this._escHtml(empName)}</span>
          <span>${data.timestamp ? new Date(data.timestamp).toLocaleString() : ''}</span>
        </div>
        <div class="project-report-actions">
          <button class="pixel-btn project-report-confirm">✓ Confirm</button>
          <span class="project-report-countdown" style="color:var(--text-dim);font-size:5px;margin-left:6px;">Auto-confirm in ${autoConfirmSec}s</span>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const confirmBtn = overlay.querySelector('.project-report-confirm');
    const countdownEl = overlay.querySelector('.project-report-countdown');
    confirmBtn.focus();

    let remaining = autoConfirmSec;
    const countdownInterval = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(countdownInterval);
        confirmAndDismiss();
        return;
      }
      countdownEl.textContent = `Auto-confirm in ${remaining}s`;
    }, 1000);

    const dismiss = () => {
      clearInterval(countdownInterval);
      overlay.remove();
      document.removeEventListener('keydown', onKey);
    };
    const confirmAndDismiss = () => {
      if (projectId) {
        fetch(`/api/ceo/report/${encodeURIComponent(projectId)}/confirm`, { method: 'POST' })
          .then(r => r.json())
          .then(d => { if (d.status === 'ok') console.log('[ceo_report] confirmed', projectId); })
          .catch(err => console.error('[ceo_report] confirm failed:', err));
      }
      dismiss();
    };
    const onKey = (e) => { if (e.key === 'Escape') dismiss(); };
    confirmBtn.addEventListener('click', confirmAndDismiss);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) dismiss(); });
    document.addEventListener('keydown', onKey);
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

  /** Returns true if the action is allowed (not in cooldown). Sets a 5s cooldown on first call. */
  _checkCooldown(actionKey, cooldownMs = 5000) {
    const now = Date.now();
    const last = this._actionCooldowns[actionKey] || 0;
    if (now - last < cooldownMs) {
      console.debug(`[cooldown] ${actionKey} blocked, ${cooldownMs - (now - last)}ms remaining`);
      return false;
    }
    this._actionCooldowns[actionKey] = now;
    return true;
  }

  async submitTask(mode = 'standard', taskText = null) {
    const task = taskText || '';
    if (!task) return;
    if (!this._checkCooldown('submitTask')) return;

    // Use currently selected project if any
    const projectId = this._currentCeoProject || '';

    // Build multipart FormData — task + files in one request
    const formData = new FormData();
    formData.append('task', task);
    if (projectId) formData.append('project_id', projectId);
    if (mode !== 'standard') formData.append('mode', mode);
    for (const f of this._taskPendingFiles) {
      formData.append('files', f.file);
    }
    this._taskPendingFiles = [];

    fetch('/api/ceo/task', {
      method: 'POST',
      body: formData,
    })
      .then(r => r.json())
      .then(data => {
        this.logEntry('CEO', `Task assigned to ${data.routed_to}`, 'ceo');
        // Refresh terminal sessions — new project should appear
        this._refreshCeoProjectList();
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Submit failed: ${err.message}`, 'system');
      });
  }

  // ===== Projects Panel =====
  updateProjectsPanel() {
    const panel = document.getElementById('projects-panel-list');
    if (!panel) return;
    fetch('/api/projects/named')
      .then(r => r.json())
      .then(data => {
        const projects = this._sortProjectsNewestFirst(data.projects || []);
        if (projects.length === 0) {
          panel.innerHTML = '<div class="task-empty">No projects</div>';
          return;
        }
        panel.innerHTML = '';
        projects.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
        for (const p of projects) {
          const card = document.createElement('div');
          // Determine sidebar border color class by iteration status:
          // white=running, yellow=pending/holding, green=completed
          const iterStatus = p.latest_iter_status || '';
          let statusClass = 'running';  // white (default for active)
          if (iterStatus === 'completed') {
            statusClass = 'completed';  // green
          } else if (iterStatus === 'pending_confirmation' || iterStatus === 'pending' || iterStatus === 'holding') {
            statusClass = 'pending';  // yellow
          } else if (p.status === 'archived') {
            statusClass = 'completed';  // archived → green
          }
          card.className = `project-panel-card status-${statusClass}`;
          card.dataset.projectId = p.project_id;
          const displayName = p.name || p.task || p.project_id;
          const meta = p.iteration_count != null
            ? `${p.iteration_count} iteration${p.iteration_count !== 1 ? 's' : ''} · ${p.status}`
            : p.status;
          card.innerHTML = `
            <div class="project-panel-name">${this._escHtml(displayName)}</div>
            <div class="project-panel-meta">${meta}</div>
          `;
          card.style.cursor = 'pointer';
          card.addEventListener('click', () => {
            this._openProjectDetail(p.project_id);
            this._selectCeoProject(p.project_id);
          });
          panel.appendChild(card);
        }
        // Fetch CEO session data and overlay pending indicators
        this._overlaySessionPendingBadges(panel);
        // Fetch task queue and overlay progress info on matching project cards
        this._overlayTaskProgress(panel);
      })
      .catch(err => console.error('[updateProjectsPanel] failed:', err));
  }

  async _overlaySessionPendingBadges(container) {
    try {
      const sessResp = await fetch('/api/ceo/sessions');
      const sessData = await sessResp.json();
      const pendingMap = {};
      for (const s of sessData.sessions || []) {
        // project_id format: "shortid_name_date/iter_001" — extract base
        const base = s.project_id.split('/')[0];
        if (s.has_pending) pendingMap[base] = (pendingMap[base] || 0) + (s.pending_count || 1);
      }
      // Add pending badge to matching project cards
      for (const card of container.querySelectorAll('.project-panel-card')) {
        const pid = card.dataset.projectId;
        if (pendingMap[pid]) {
          const badge = document.createElement('span');
          badge.className = 'ceo-pending-badge';
          badge.textContent = `\u25CF ${pendingMap[pid]}`;
          card.querySelector('.project-panel-name')?.prepend(badge);
        }
      }
    } catch (e) { /* session endpoint may not be available yet */ }
  }

  async _overlayTaskProgress(container) {
    try {
      const taskResp = await fetch('/api/task-queue');
      const tasks = await taskResp.json();

      for (const t of tasks) {
        if (!t.project_id) continue;
        const basePid = t.project_id.split('/')[0];
        const card = container.querySelector(`.project-panel-card[data-project-id="${basePid}"]`);
        if (!card) continue;

        const isTerminal = ['completed', 'finished', 'failed', 'cancelled'].includes(t.status);

        let progressHtml = '';

        if (!isTerminal && t.tree) {
          const childCount = t.tree.total - 1;
          if (childCount > 0) {
            const done = t.tree.terminal;
            const pct = Math.round((done / childCount) * 100);
            progressHtml += `<div class="proj-progress"><div class="proj-progress-track"><div class="proj-progress-bar" style="width:${pct}%"></div></div><span>${done}/${childCount}</span></div>`;
          }
          // Current executor
          if (t.tree.active_nodes?.length > 0) {
            const node = t.tree.active_nodes[0];
            const emp = (window.officeRenderer?.state?.employees || []).find(e => e.id === node.employee_id);
            const name = emp ? (emp.nickname || emp.name) : node.employee_id;
            progressHtml += `<div class="proj-executor">${this._escHtml(name)}</div>`;
          }
        }

        // Cancel button for active tasks
        if (!isTerminal && t.project_id) {
          progressHtml += `<button class="proj-cancel-btn" data-pid="${this._escHtml(t.project_id)}" title="Cancel">&#10005;</button>`;
        }

        // Trace button
        if (t.project_id) {
          progressHtml += `<button class="proj-trace-btn" data-pid="${this._escHtml(t.project_id)}" data-task="${this._escHtml(t.task.substring(0, 40))}" title="Trace">T</button>`;
        }

        if (progressHtml) {
          const overlay = document.createElement('div');
          overlay.className = 'proj-card-progress';
          overlay.innerHTML = progressHtml;
          card.appendChild(overlay);

          // Wire cancel
          const cancelBtn = overlay.querySelector('.proj-cancel-btn');
          if (cancelBtn) {
            cancelBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              this._cancelTask(cancelBtn.dataset.pid);
            });
          }

          // Wire trace
          const traceBtn = overlay.querySelector('.proj-trace-btn');
          if (traceBtn) {
            traceBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              this.openTraceViewer(traceBtn.dataset.pid, traceBtn.dataset.task);
            });
          }
        }
      }
    } catch (e) {
      console.debug('[_overlayTaskProgress] failed:', e);
    }
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
        this._renderProjectDetail(projectId, proj, contentEl);
      })
      .catch(err => {
        console.error('[_openProjectDetail] failed:', err);
      });
  }

  _renderProjectDetail(projectId, proj, contentEl) {
    const totalCost = proj.total_cost_usd || 0;
    let headerHtml = `<div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;">
      <span class="project-name-editable" data-project-id="${this._escHtml(projectId)}" title="Click to rename" style="color:var(--pixel-cyan);font-size:8px;cursor:pointer;border-bottom:1px dashed var(--text-dim);">${this._escHtml(proj.name || projectId)}</span>
      <span style="color:var(--text-dim);font-size:6px;">${proj.status}</span>
      ${totalCost > 0 ? `<span style="color:var(--pixel-yellow);font-size:6px;">$${totalCost.toFixed(4)}</span>` : ''}
      <button onclick="app.openTraceViewer('${this._escHtml(projectId)}','${this._escHtml(proj.name || projectId)}')" style="margin-left:auto;background:#1a1a1a;color:#4af;border:1px solid #333;padding:1px 8px;font-size:6px;cursor:pointer;font-family:monospace">TRACE</button>
      <button onclick="app._deleteProject('${this._escHtml(projectId)}')" style="background:#1a1a1a;color:var(--pixel-red);border:1px solid #500;padding:1px 8px;font-size:6px;cursor:pointer;font-family:monospace" title="Delete project and all data">DELETE</button>
    </div>`;

    // Build split layout: iteration list (left) + detail (right)
    let iterListHtml = '';
    const iters = proj.iteration_details || [];
    if (iters.length === 0) {
      iterListHtml = '<div style="color:var(--text-dim);font-size:6px;">No iterations yet</div>';
    }
    for (const it of iters) {
      const statusColor = it.status === 'completed' ? 'var(--pixel-green)' : it.status === 'pending_confirmation' ? 'var(--pixel-yellow)' : 'var(--pixel-white)';
      const statusIcon = it.status === 'completed' ? '\u2705' : it.status === 'pending_confirmation' ? '\u23F3' : '\uD83D\uDD04';
      const iterCost = it.cost_usd ? ` · $${it.cost_usd.toFixed(4)}` : '';
      iterListHtml += `<div class="project-iter-card" data-iter-id="${it.iteration_id}" data-project-id="${projectId}">
        <div style="color:${statusColor};">${statusIcon} ${it.iteration_id}${iterCost}</div>
        <div style="color:var(--pixel-white);margin-top:2px;">${this._escHtml(it.task || '')}</div>
        <div style="color:var(--text-dim);margin-top:1px;">${it.created_at ? it.created_at.substring(0, 16) : ''}</div>
      </div>`;
    }
    if (proj.status === 'active') {
      iterListHtml += `<div style="margin-top:8px;"><button class="pixel-btn secondary archive-project-btn" style="font-size:6px;padding:4px 8px;">Archive</button></div>`;
    }

    contentEl.innerHTML = `${headerHtml}
      <div class="project-detail-split">
        <div class="project-iter-list">${iterListHtml}</div>
        <div class="project-iter-detail" id="project-iter-detail">
          <div style="color:var(--text-dim);font-size:6px;padding:12px;">Select an iteration to view details</div>
        </div>
      </div>`;

    // Bind archive button
    const archiveBtn = contentEl.querySelector('.archive-project-btn');
    if (archiveBtn) {
      archiveBtn.addEventListener('click', () => this._archiveProject(projectId));
    }

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
            }).catch(err => console.error('[rename project] failed:', err));
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
  }

  _loadIterationDetail(projectId, iterationId) {
    const panel = document.getElementById('project-iter-detail');
    if (!panel) return;
    panel.innerHTML = '<div style="color:var(--text-dim);font-size:6px;">Loading...</div>';

    // Use qualified iteration ID (projectId/iterationId) for unambiguous lookup
    const qualifiedId = (projectId && iterationId && projectId !== iterationId)
      ? `${projectId}/${iterationId}` : iterationId;
    // Encode each path segment separately so '/' is preserved for FastAPI path params
    const qualifiedPath = (projectId && iterationId && projectId !== iterationId)
      ? `${encodeURIComponent(projectId)}/${encodeURIComponent(iterationId)}`
      : encodeURIComponent(iterationId);

    // Fetch project doc + task tree in parallel
    Promise.all([
      fetch(`/api/projects/${qualifiedPath}`).then(r => r.json()),
      fetch(`/api/projects/${qualifiedPath}/tree`).then(r => r.ok ? r.json() : null).catch(() => null),
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
            const arNotes = ar.notes ? ` — ${this._escHtml(ar.notes)}` : '';
            detailHtml += `<div style="font-size:6px;color:${ar.accepted ? 'var(--pixel-green)' : 'var(--pixel-red)'};margin:4px 0;">${arIcon} Acceptance Result: ${arLabel}${arNotes}</div>`;
          }
          const ear = doc.ea_review_result;
          if (ear) {
            const earIcon = ear.approved ? '\u2705' : '\u274C';
            const earLabel = ear.approved ? 'Approved' : 'Rejected';
            const earNotes = ear.notes ? ` — ${this._escHtml(ear.notes)}` : '';
            detailHtml += `<div style="font-size:6px;color:${ear.approved ? 'var(--pixel-green)' : 'var(--pixel-red)'};margin:2px 0;">EA Review: ${earIcon} ${earLabel}${earNotes}</div>`;
          }
        }

        if (doc.status !== 'completed' && doc.status !== 'pending_confirmation') {
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
        const fileBaseUrl = `/api/projects/${qualifiedPath}/files/`;
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

        // CEO Report (stored when project completion report is submitted)
        if (doc.ceo_report) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">📊 CEO Report</div>`;
          detailHtml += `<div class="task-result-report md-rendered" style="border-left:2px solid var(--pixel-cyan);padding-left:6px;">${this._renderMarkdown(doc.ceo_report)}</div>`;
        }

        if (taskResult) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Task Report</div>`;
          detailHtml += `<div class="task-result-report md-rendered">${this._renderMarkdown(taskResult)}</div>`;
        } else if (doc.output) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Output</div>`;
          detailHtml += `<div style="font-size:5px;color:var(--pixel-white);background:var(--bg-dark);padding:4px;border:1px solid var(--border);max-height:80px;overflow-y:auto;">${this._escHtml(doc.output)}</div>`;
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
              detailHtml += `<div style="color:var(--pixel-white);margin-top:1px;">${this._escHtml(entry.detail)}</div>`;
            }
            detailHtml += `</div>`;
          }
          detailHtml += `</div>`;
        } else {
          detailHtml += `<div style="font-size:5px;color:var(--text-dim);">No log entries</div>`;
        }

        const cost = doc.cost || {};
        detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Cost & Budget <span style="font-size:5px;color:var(--text-dim);">(estimated)</span></div>`;
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
              this._treeRenderer.load(qualifiedId);
              this._currentTreeProjectId = qualifiedId;
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
              .catch(err => { console.error('[stopTasks] failed:', err); stopBtn.disabled = false; stopBtn.textContent = '■ Stop All Tasks'; });
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
        panel.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">Load failed: ${this._escHtml(err.message)}</div>`;
      });
  }

  _continueIteration(projectId, iterationId) {
    if (!this._checkCooldown('continueIteration')) return;
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
    if (!this._checkCooldown('submitFollowup')) return;
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
    const textExts = ['txt', 'py', 'js', 'css', 'yaml', 'yml', 'json', 'csv',
                      'tsv', 'xml', 'sh', 'toml', 'cfg', 'ini', 'log', 'rst', 'tex', 'sql',
                      'r', 'rb', 'go', 'java', 'c', 'cpp', 'h', 'hpp', 'rs', 'swift', 'kt',
                      'ts', 'tsx', 'jsx'];
    const fileExt = (ext || '').toLowerCase();

    if (fileExt === 'html' || fileExt === 'htm') {
      window.open(url, '_blank', 'noopener');
      return;
    }

    if (fileExt === 'md' || fileExt === 'markdown') {
      fetch(url)
        .then(r => r.text())
        .then(text => {
          this._showFileViewer(
            filename,
            `<div style="max-height:65vh;overflow-y:auto;margin:0;padding:8px;background:var(--bg-dark);border:1px solid var(--border);"><div class="md-rendered">${this._renderMarkdown(text)}</div></div>`,
          );
        })
        .catch(err => { console.error('[viewMarkdown] failed, opening in new tab:', err); window.open(url, '_blank', 'noopener'); });
      return;
    }

    if (imageExts.includes(fileExt)) {
      // Open image in a simple overlay
      this._showFileViewer(filename, `<img src="${url}" style="max-width:100%;max-height:70vh;" />`);
    } else if (textExts.includes(fileExt)) {
      // Fetch text content and display
      fetch(url)
        .then(r => r.text())
        .then(text => {
          this._showFileViewer(filename, `<pre style="font-size:6px;color:var(--pixel-white);white-space:pre-wrap;word-break:break-all;max-height:65vh;overflow-y:auto;margin:0;padding:6px;background:var(--bg-dark);border:1px solid var(--border);">${this._escHtml(text)}</pre>`);
        })
        .catch(err => { console.error('[viewFile] failed, opening in new tab:', err); window.open(url, '_blank', 'noopener'); });
    } else if (fileExt === 'pdf') {
      window.open(url, '_blank', 'noopener');
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
      .catch(err => console.error('[archiveProject] failed:', err));
  }

  _deleteProject(projectId) {
    if (!confirm(`Delete project "${projectId}" and ALL its data? This cannot be undone.`)) return;
    fetch(`/api/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' })
      .then(r => {
        if (!r.ok) throw new Error(`Server error: ${r.status}`);
        return r.json();
      })
      .then(data => {
        if (data.status === 'deleted') {
          this.logEntry('CEO', `Project "${projectId}" deleted`, 'ceo');
          this.updateProjectsPanel();
          this.loadActiveProjects();
          document.getElementById('project-modal').classList.add('hidden');
        }
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Failed to delete project: ${err.message}`, 'system');
      });
  }

  loadActiveProjects() {
    // Replaced by CEO Terminal — refresh sessions instead
    this._refreshCeoProjectList();
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

  // ===== Background Tasks =====

  openBackgroundTasks() {
    document.getElementById('bg-tasks-modal').classList.remove('hidden');
    this._bgTaskSelected = null;
    this._bgTaskXterm = null;
    this._fetchBackgroundTasks();
  }

  closeBackgroundTasks() {
    document.getElementById('bg-tasks-modal').classList.add('hidden');
    this._bgTaskSelected = null;
    if (this._bgTaskXterm) { this._bgTaskXterm.dispose(); this._bgTaskXterm = null; }
  }

  async _fetchBackgroundTasks() {
    try {
      const resp = await fetch('/api/background-tasks');
      const data = await resp.json();
      this._renderBgTaskList(data.tasks);
      document.getElementById('bg-tasks-slots').textContent =
        `${data.running_count}/${data.max_concurrent} SLOTS`;
      // If selected task is in the list, refresh detail (re-fetch with output)
      if (this._bgTaskSelected) {
        const current = data.tasks.find(t => t.id === this._bgTaskSelected);
        if (current) this._fetchBgTaskDetail(this._bgTaskSelected);
      }
    } catch (e) {
      console.error('[bg-tasks] fetch error:', e);
    }
  }

  _renderBgTaskList(tasks) {
    // Sort: running first, then by started_at descending
    tasks.sort((a, b) => {
      if (a.status === 'running' && b.status !== 'running') return -1;
      if (a.status !== 'running' && b.status === 'running') return 1;
      return (b.started_at || '').localeCompare(a.started_at || '');
    });
    const el = document.getElementById('bg-tasks-list');
    if (!tasks.length) {
      el.innerHTML = '<div style="color:#555;font-size:10px;padding:12px;font-family:var(--font-mono);">No background tasks</div>';
      return;
    }
    const statusIcon = { running: '\u2588', completed: '\u2591', failed: '\u2573', stopped: '\u2592' };
    const statusColor = { running: '#44aa44', completed: '#666', failed: '#ff4444', stopped: '#aa4444' };
    let html = '';
    for (const t of tasks) {
      const selected = t.id === this._bgTaskSelected ? ' selected' : '';
      const dur = this._bgTaskDuration(t);
      const cmd = this._escHtml(t.command.length > 30 ? t.command.substring(0, 30) + '...' : t.command);
      html += `<div class="bg-task-item status-${t.status}${selected}" data-id="${t.id}">`;
      html += `<div class="bg-task-item-status" style="color:${statusColor[t.status] || '#666'}">${statusIcon[t.status] || '\u2591'} ${t.status.toUpperCase()} ${dur}</div>`;
      html += `<div class="bg-task-item-cmd">${cmd}</div>`;
      if (t.port) html += `<div class="bg-task-item-port">\u25B6 :${t.port}</div>`;
      html += '</div>';
    }
    el.innerHTML = html;
    el.querySelectorAll('.bg-task-item').forEach(item => {
      item.addEventListener('click', () => {
        this._bgTaskSelected = item.dataset.id;
        this._fetchBgTaskDetail(item.dataset.id);
        el.querySelectorAll('.bg-task-item').forEach(i => i.classList.remove('selected'));
        item.classList.add('selected');
      });
    });
  }

  async _fetchBgTaskDetail(taskId) {
    this._bgTaskSelected = taskId;
    try {
      const resp = await fetch(`/api/background-tasks/${taskId}?tail=200`);
      if (!resp.ok) return;
      const data = await resp.json();
      this._renderBgTaskDetail(data.task, data.output_tail);
      // No polling — WS background_task_update event triggers refresh
    } catch (e) {
      console.error('[bg-tasks] detail fetch error:', e);
    }
  }

  _renderBgTaskDetail(task, outputTail) {
    const el = document.getElementById('bg-tasks-detail');
    const statusColor = { running: '#44aa44', completed: '#666', failed: '#ff4444', stopped: '#aa4444' };

    let metaHtml = `<span style="color:${statusColor[task.status] || '#666'}">\u2588 ${task.status.toUpperCase()}</span>`;
    if (task.port) {
      const addr = task.address || `http://localhost:${task.port}`;
      metaHtml += `<span style="color:#aa44ff">\u25B6 <a href="${addr}" target="_blank" style="color:#aa44ff;">${addr}</a></span>`;
    }
    if (task.pid) metaHtml += `<span style="color:#555">PID ${task.pid}</span>`;
    if (task.started_by) metaHtml += `<span style="color:#555">by ${this._escHtml(task.started_by)}</span>`;
    const dur = this._bgTaskDuration(task);
    if (dur) metaHtml += `<span style="color:#555">${dur}</span>`;

    el.innerHTML = `
      <div class="bg-tasks-detail-header">
        <div class="bg-tasks-detail-cmd">${this._escHtml(task.command)}</div>
        <div class="bg-tasks-detail-desc">${this._escHtml(task.description)}</div>
        <div class="bg-tasks-detail-meta">${metaHtml}</div>
      </div>
      <div class="bg-tasks-detail-output" id="bg-tasks-output"></div>
      ${task.status === 'running' ? '<div class="bg-tasks-detail-actions"><button class="bg-tasks-stop-btn" id="bg-tasks-stop-btn">\u25A0 STOP</button></div>' : ''}
    `;

    const outputEl = document.getElementById('bg-tasks-output');
    if (this._bgTaskXterm) { this._bgTaskXterm.dispose(); }
    this._bgTaskXterm = new XTermLog(outputEl, { fontSize: 11 });
    if (outputTail) {
      for (const line of outputTail.split('\n')) {
        this._bgTaskXterm.writeln(line);
      }
    } else {
      this._bgTaskXterm.writeln(`${ANSI.gray}No output yet${ANSI.reset}`);
    }

    const stopBtn = document.getElementById('bg-tasks-stop-btn');
    if (stopBtn) {
      stopBtn.addEventListener('click', async () => {
        if (!confirm('Stop this background task?')) return;
        stopBtn.disabled = true;
        stopBtn.textContent = 'STOPPING...';
        try {
          await fetch(`/api/background-tasks/${task.id}/stop`, { method: 'POST' });
          // WS background_task_update event will update UI in-place
        } catch (e) {
          console.error('[bg-tasks] stop error:', e);
        }
      });
    }
  }

  _bgTaskDuration(task) {
    if (!task.started_at) return '';
    const start = new Date(task.started_at);
    const end = task.ended_at ? new Date(task.ended_at) : new Date();
    const s = Math.floor((end - start) / 1000);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
    return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
  }

  _updateBgTaskListItem(taskData) {
    // In-place update of a bg task list item from WS payload
    const el = document.getElementById('bg-tasks-list');
    if (!el) return;
    const item = el.querySelector(`.bg-task-item[data-id="${taskData.id}"]`);
    if (item) {
      const statusIcon = { running: '\u2588', completed: '\u2591', failed: '\u2573', stopped: '\u2592' };
      const statusColor = { running: '#44aa44', completed: '#666', failed: '#ff4444', stopped: '#aa4444' };
      const statusEl = item.querySelector('.bg-task-item-status');
      if (statusEl) {
        statusEl.style.color = statusColor[taskData.status] || '#666';
        statusEl.textContent = `${statusIcon[taskData.status] || '\u2591'} ${taskData.status.toUpperCase()} ${this._bgTaskDuration(taskData)}`;
      }
      item.className = `bg-task-item status-${taskData.status}${taskData.id === this._bgTaskSelected ? ' selected' : ''}`;
      // Update slots counter
      const slotsEl = document.getElementById('bg-tasks-slots');
      if (slotsEl && taskData.status !== 'running') {
        // Recount running items from DOM
        const runningCount = el.querySelectorAll('.bg-task-item.status-running').length;
        slotsEl.textContent = `${runningCount}/3 SLOTS`;
      }
    } else {
      // New task not in DOM — full refresh needed (one-time)
      this._fetchBackgroundTasks();
    }
  }

  _updateBgTaskDetailStatus(taskData) {
    // Update the detail panel status from WS payload without full REST re-fetch
    const metaEl = document.querySelector('.bg-tasks-detail-meta');
    if (!metaEl) return;
    const statusColor = { running: '#44aa44', completed: '#666', failed: '#ff4444', stopped: '#aa4444' };
    // Update the first span (status)
    const statusSpan = metaEl.querySelector('span');
    if (statusSpan) {
      statusSpan.style.color = statusColor[taskData.status] || '#666';
      statusSpan.textContent = `\u2588 ${taskData.status.toUpperCase()}`;
    }
    // If task finished, remove stop button and show final status
    if (taskData.status !== 'running') {
      const stopBtn = document.getElementById('bg-tasks-stop-btn');
      if (stopBtn) stopBtn.parentElement.remove();
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
      // WS agent_task_update event will update the task card in-place
    }
  } catch (err) {
    console.error('Cancel failed:', err);
  }
};

// Global file viewer for CEO report workspace files
window._ceoViewFile = function(url, filename) {
  try {
    const ext = (String(filename || '').split('.').pop() || '').toLowerCase();
    if (window.app && typeof window.app._openProjectFile === 'function') {
      window.app._openProjectFile(filename, url, ext);
    } else {
      window.open(url, '_blank', 'noopener');
    }
  } catch (err) {
    console.error('Failed to view file:', err);
  }
};

// Boot
window.app = new AppController();
