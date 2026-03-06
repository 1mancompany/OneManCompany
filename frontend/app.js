/**
 * app.js — WebSocket client, CEO console, and activity log controller
 */

class AppController {
  constructor() {
    this.ws = null;
    this.reconnectDelay = 1000;
    // Meeting chat: room_id -> [{speaker, role, message, time}]
    this.meetingChats = {};
    this.viewingRoomId = null;
    this.viewingEmployeeId = null;
    this.cachedModels = null;  // cached OpenRouter model list
    // Review queue — items shown one by one with counter
    this.reviewQueue = [];      // [{type, data, decision?}]
    this._reviewIndex = 0;      // current item index in reviewQueue
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

  handleMessage(msg) {
    // Update office canvas state
    if (msg.state && window.officeRenderer) {
      window.officeRenderer.updateState(msg.state);
    }

    // Cache state for tool list and other UI
    if (msg.state) {
      this.state = msg.state;
    }

    // Update counters
    if (msg.state) {
      document.getElementById('employee-count').textContent =
        `👥 ${msg.state.employees.length}`;
      document.getElementById('tool-count').textContent =
        `🔧 ${msg.state.tools.length}`;
      // Meeting room count
      const rooms = msg.state.meeting_rooms || [];
      const freeRooms = rooms.filter(r => !r.is_booked).length;
      document.getElementById('room-count').textContent =
        `🏢 ${freeRooms}/${rooms.length}`;
      this.updateRoster(msg.state.employees);
      this.updateTaskPanel(msg.state.active_tasks || []);
      this.updateOneononeDropdown(msg.state.employees);
      this.updateProjectsPanel();
      // Refresh meeting modal if open
      if (this.viewingRoomId) {
        const room = rooms.find(r => r.id === this.viewingRoomId);
        if (room) this._refreshMeetingModalStatus(room);
      }
      // Refresh company culture if modal is open
      if (!document.getElementById('company-culture-modal').classList.contains('hidden')) {
        this._renderCompanyCulture();
      }
      // Refresh deferred resolutions panel
      this._refreshDeferredPanel();
      // Auto-refresh dashboard costs if modal is open (debounce 2s)
      if (!document.getElementById('dashboard-modal').classList.contains('hidden')) {
        clearTimeout(this._dashboardCostTimer);
        this._dashboardCostTimer = setTimeout(() => this._renderDashboard(), 2000);
      }
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
        if (p.room_id) this.meetingChats[p.room_id] = [];
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
        if (!this.meetingChats[roomId]) this.meetingChats[roomId] = [];
        const chatEntry = {
          speaker: p.speaker,
          role: p.role,
          message: p.message,
          time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
        };
        this.meetingChats[roomId].push(chatEntry);
        // If this room is currently being viewed, append the message live
        if (this.viewingRoomId === roomId) {
          this._appendChatMessage(chatEntry);
        }
        return { text: `💬 [${p.speaker}] ${(p.message || '').substring(0, 50)}`, cls: 'system', agent: 'MEETING' };
      },
      'workflow_updated':    (p) => ({ text: `📋 Workflow updated: ${p.name}`, cls: 'ceo', agent: 'CEO' }),
      'candidates_ready':   (p) => {
        this.showCandidateSelection(p);
        return { text: `📋 HR screening done: ${(p.candidates || []).length} candidates for CEO selection`, cls: 'hr', agent: 'HR' };
      },
      'file_edit_proposed':  (p) => {
        this._enqueueFileEdit(p);
        return { text: `📝 File edit request: ${p.rel_path} — ${p.reason}`, cls: 'ceo', agent: p.proposed_by || 'AGENT' };
      },
      'file_edit_applied':   (p) => ({ text: `✅ File updated: ${p.rel_path}`, cls: 'ceo', agent: 'CEO' }),
      'file_edit_rejected':  (p) => ({ text: `❌ File edit rejected: ${p.rel_path}`, cls: 'ceo', agent: 'CEO' }),
      'hiring_request_ready': (p) => {
        this.showHiringRequestModal(p);
        return { text: `📋 COO requests hiring: ${p.role} — ${p.reason}`, cls: 'coo', agent: 'COO' };
      },
      'hiring_request_decided': (p) => {
        return { text: `${p.approved ? '✅' : '❌'} Hiring request ${p.approved ? 'approved' : 'rejected'}: ${p.role}`, cls: 'ceo', agent: 'CEO' };
      },
      'resolution_ready':    (p) => {
        this._showResolutionModal(p);
        return { text: `📋 Resolution ready: ${(p.edits || []).length} file edit(s) for review`, cls: 'ceo', agent: 'SYSTEM' };
      },
      'resolution_decided':  (p) => {
        this._refreshDeferredPanel();
        return { text: `✅ Resolution decided`, cls: 'ceo', agent: 'CEO' };
      },
      'inquiry_started':     (p) => {
        this._startInquiryMode(p, msg.state);
        return { text: `🔍 Inquiry started with ${p.agent_role} in meeting room`, cls: 'ceo', agent: 'CEO' };
      },
      'inquiry_ended':       (p) => {
        this._endInquiryMode();
        return { text: `🔍 Inquiry ended`, cls: 'ceo', agent: 'CEO' };
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
      'ceo_report': (p) => {
        this._showCeoReport(p);
        const icon = p.action_required ? '🚨' : '📊';
        return { text: `${icon} CEO Report: ${p.subject}`, cls: 'ceo', agent: 'SYSTEM' };
      },
      'code_update_available': (p) => {
        this._showCodeUpdateBanner(p.count, p.changed_files);
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
    const panel = document.getElementById('task-panel-list');
    if (!tasks || tasks.length === 0) {
      panel.innerHTML = '<div class="task-empty">No active tasks</div>';
      return;
    }
    panel.innerHTML = '';
    for (const t of tasks) {
      const card = document.createElement('div');
      card.className = `task-card ${t.status}`;
      const icon = t.status === 'running' ? '🔄' : '⏳';
      const label = t.status === 'running' ? 'Running' : 'Queued';
      const routeColor = t.routed_to === 'HR' ? 'var(--pixel-blue)' : 'var(--pixel-orange)';
      // Resolve current owner display name
      const ownerEmp = (this._lastEmployees || []).find(e => e.id === t.current_owner);
      const ownerLabel = ownerEmp
        ? `${ownerEmp.nickname || ownerEmp.name}`
        : (t.current_owner || t.routed_to);
      card.innerHTML = `
        <div class="task-card-status">${icon} ${label}</div>
        <div class="task-card-text">${t.task.substring(0, 60)}${t.task.length > 60 ? '...' : ''}</div>
        <div class="task-card-route" style="color:${routeColor};">${t.routed_to} · <span class="task-card-owner">Current: ${ownerLabel}</span></div>
      `;
      if (t.project_id && !t.project_id.startsWith('_auto_')) {
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => this.openTaskLog(t.project_id));
      }
      panel.appendChild(card);
    }
  }

  openTaskLog(projectId) {
    const modal = document.getElementById('project-modal');
    const listEl = document.getElementById('project-list');
    const detailEl = document.getElementById('project-detail');
    const contentEl = document.getElementById('project-detail-content');

    // Open project modal directly in detail view (skip the list)
    modal.classList.remove('hidden');
    listEl.classList.add('hidden');
    detailEl.classList.remove('hidden');
    contentEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">Loading task log...</div>';

    fetch(`/api/projects/${encodeURIComponent(projectId)}`)
      .then(r => r.json())
      .then(doc => {
        if (doc.error) {
          contentEl.innerHTML = `<div style="color:var(--pixel-red);">${doc.error}</div>`;
          return;
        }
        let html = `<h4 style="color:var(--pixel-yellow);font-size:8px;margin:6px 0;">${doc.task || ''}</h4>`;
        html += `<div style="font-size:6px;color:var(--text-dim);margin-bottom:8px;display:flex;align-items:center;gap:8px;">`;
        html += `<span>Status: <span style="color:var(--pixel-green);">${doc.status}</span> | Routed to: ${doc.routed_to} | Started: ${(doc.created_at || '').substring(11, 19)}</span>`;
        if (doc.completed_at) html += `<span>| Completed: ${doc.completed_at.substring(11, 19)}</span>`;
        if (doc.status !== 'completed') {
          html += `<button class="pixel-btn small" style="background:var(--pixel-red);color:#fff;box-shadow:2px 2px 0 #991122;font-size:5px;padding:2px 6px;margin-left:auto;" onclick="window._abortTask('${doc.project_id}')">ABORT</button>`;
        }
        html += `</div>`;

        // Timeline (live task log)
        const timeline = doc.timeline || [];
        if (timeline.length > 0) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:6px 0 4px;">Task Log (${timeline.length} entries):</div>`;
          for (const entry of timeline) {
            const time = (entry.time || '').substring(11, 19);
            const ownerColor = entry.employee_id === 'hr' ? 'var(--pixel-blue)' :
                               entry.employee_id === 'coo' ? 'var(--pixel-orange)' : 'var(--pixel-green)';
            html += `<div style="font-size:6px;line-height:1.8;border-left:2px solid var(--border);padding-left:6px;margin:2px 0;">`;
            html += `<span style="color:var(--text-dim);">[${time}]</span> `;
            html += `<span style="color:${ownerColor};">${entry.employee_id}</span> `;
            html += `<span style="color:var(--pixel-yellow);">${entry.action}</span>`;
            if (entry.detail) {
              html += `<div style="color:var(--pixel-white);margin-top:1px;">${entry.detail.substring(0, 300)}${entry.detail.length > 300 ? '...' : ''}</div>`;
            }
            html += `</div>`;
          }
        } else {
          html += `<div style="font-size:6px;color:var(--text-dim);">No log entries yet — task is still initializing...</div>`;
        }

        // Workspace files
        const files = doc.files || [];
        if (files.length > 0) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 4px;">Workspace Files (${files.length}):</div>`;
          for (const f of files) {
            html += `<div style="font-size:6px;color:var(--pixel-white);padding:1px 0;">📄 ${f}</div>`;
          }
        }

        contentEl.innerHTML = html;
      })
      .catch(err => {
        contentEl.innerHTML = `<div style="color:var(--pixel-red);">Load failed: ${err.message}</div>`;
      });
  }

  // ===== Roster =====
  updateRoster(employees) {
    const roster = document.getElementById('roster-list');
    roster.innerHTML = '';

    // Store latest employees for re-filtering
    this._lastEmployees = employees;

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
      card.innerHTML = `
        <div class="roster-info">
          <div class="roster-name">${roleIcon} ${emp.name} ${nn}${guidanceBadge}${remoteBadge}${probationBadge}${pipBadge}</div>
          <div class="roster-role"><span class="roster-empnum">${empNum}</span> ${title} — ${(emp.skills || []).slice(0, 3).join(', ')}</div>
          <div class="roster-quarter">Q Tasks: ${qTasks}/3</div>
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
    if (this._lastEmployees) {
      this.updateRoster(this._lastEmployees);
    }
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

    // Project selector toggle
    const projectSelect = document.getElementById('project-select');
    const newProjectName = document.getElementById('new-project-name');
    if (projectSelect && newProjectName) {
      projectSelect.addEventListener('change', () => {
        if (projectSelect.value === '__new__') {
          newProjectName.classList.remove('hidden');
          newProjectName.focus();
        } else {
          newProjectName.classList.add('hidden');
          newProjectName.value = '';
        }
      });
    }
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
      fetch('/api/admin/apply-code-update', { method: 'POST' }).catch(() => {});
    });
    document.getElementById('code-update-dismiss-btn').addEventListener('click', () => {
      document.getElementById('code-update-banner').classList.add('hidden');
    });

    // Roster filter bindings
    ['roster-filter-role', 'roster-filter-dept', 'roster-filter-level'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => this._onRosterFilterChange());
    });

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
    document.getElementById('oneonone-target').addEventListener('change', () => {
      document.getElementById('oneonone-start-btn').disabled = !document.getElementById('oneonone-target').value;
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

    // Review queue panel bindings
    document.getElementById('review-approve-btn').addEventListener('click', () => this._reviewApprove());
    document.getElementById('review-reject-btn').addEventListener('click', () => this._reviewReject());

    // Resolution review modal bindings
    document.getElementById('resolution-close-btn').addEventListener('click', () => this._closeResolutionModal());
    document.getElementById('resolution-cancel-btn').addEventListener('click', () => this._closeResolutionModal());
    document.getElementById('resolution-submit-btn').addEventListener('click', () => this._submitResolutionDecisions());
    document.getElementById('resolution-modal').addEventListener('click', (e) => {
      if (e.target.id === 'resolution-modal') this._closeResolutionModal();
    });

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

    // Settings panel: fetch API settings on first expand
    this._settingsLoaded = false;
    const settingsHeader = document.querySelector('[data-target="settings-body"]');
    if (settingsHeader) {
      settingsHeader.addEventListener('click', () => {
        if (!this._settingsLoaded) {
          this._settingsLoaded = true;
          this._renderApiSettings();
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

  startOneonone() {
    const select = document.getElementById('oneonone-target');
    const empId = select.value;
    if (!empId) return;

    const emp = (this._lastEmployees || []).find(e => e.id === empId);
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
      .then(r => r.json())
      .then(data => {
        typing.classList.add('hidden');
        if (data.error) {
          this._addOneononeSystemMsg(`Error: ${data.error}`);
        } else {
          // Record history
          this._oneononeHistory.push({ role: 'ceo', content: message });
          this._oneononeHistory.push({ role: 'employee', content: data.response });

          const emp = (this._lastEmployees || []).find(e => e.id === this._oneononeEmployeeId);
          const name = emp ? emp.name : 'Employee';
          this._addOneononeBubble(name, data.response, 'incoming');
        }
      })
      .catch(err => {
        typing.classList.add('hidden');
        this._addOneononeSystemMsg(`Error: ${err.message}`);
      })
      .finally(() => { sendBtn.disabled = false; });
  }

  endOneononeMeeting() {
    if (!this._oneononeEmployeeId) return;

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
      .then(r => r.json())
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

    // Load model dropdown / API key section based on provider
    this._loadModelOrApiKeySection(emp.id);

    // Fetch and render agent task board + logs
    this._fetchTaskBoard(emp.id);
    this._fetchExecutionLogs(emp.id);

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
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          alert(`Cannot dismiss: ${data.error}`);
        } else {
          this.closeEmployeeDetail();
          this.addLog(`Dismissed ${data.name} (${data.nickname}) — ${data.reason}`);
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

    // Group: top-level tasks first, sub-tasks nested under parents
    const topLevel = tasks.filter(t => !t.parent_id);
    const subTasks = tasks.filter(t => t.parent_id);
    const subByParent = {};
    for (const st of subTasks) {
      if (!subByParent[st.parent_id]) subByParent[st.parent_id] = [];
      subByParent[st.parent_id].push(st);
    }

    const empId = this.viewingEmployeeId;
    let html = '';
    for (const task of topLevel) {
      const statusCls = task.status.replace('_', '-');
      html += `<div class="emp-taskboard-item ${statusCls}">`;
      html += `<div class="emp-taskboard-status" style="display:flex;justify-content:space-between;align-items:center;">`;
      html += `<span>${task.status}</span>`;
      if (task.status === 'pending' || task.status === 'in_progress') {
        html += `<button class="emp-task-cancel-btn" onclick="window._abortAgentTask('${empId}','${task.id}')">CANCEL</button>`;
      }
      html += `</div>`;
      html += `<div class="emp-taskboard-desc">${this._escHtml(task.description.substring(0, 120))}</div>`;
      if (task.result) {
        html += `<div class="emp-taskboard-result">${this._escHtml(task.result.substring(0, 100))}</div>`;
      }
      if (task.total_tokens > 0) {
        const costStr = task.estimated_cost_usd ? `$${task.estimated_cost_usd.toFixed(4)}` : '';
        html += `<div class="emp-taskboard-cost">${task.total_tokens} tokens ${costStr}</div>`;
      }
      html += '</div>';

      // Render sub-tasks indented
      const subs = subByParent[task.id] || [];
      for (const sub of subs) {
        const subCls = sub.status.replace('_', '-');
        html += `<div class="emp-taskboard-item sub-task ${subCls}">`;
        html += `<div class="emp-taskboard-status" style="display:flex;justify-content:space-between;align-items:center;">`;
        html += `<span>${sub.status}</span>`;
        if (sub.status === 'pending' || sub.status === 'in_progress') {
          html += `<button class="emp-task-cancel-btn" onclick="window._abortAgentTask('${empId}','${sub.id}')">CANCEL</button>`;
        }
        html += `</div>`;
        html += `<div class="emp-taskboard-desc">${this._escHtml(sub.description.substring(0, 100))}</div>`;
        html += '</div>';
      }
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
      }
    }, 3000);
  }

  _stopTaskBoardPolling() {
    if (this._taskBoardPollTimer) {
      clearInterval(this._taskBoardPollTimer);
      this._taskBoardPollTimer = null;
    }
  }

  // ===== Code Update Banner =====
  _showCeoReport(payload) {
    const { subject, report, action_required } = payload;
    const icon = action_required ? '🚨' : '📊';
    const urgency = action_required ? ' (需要CEO操作)' : '';

    // Build a modal overlay for the report
    let overlay = document.getElementById('ceo-report-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'ceo-report-overlay';
      overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
      document.body.appendChild(overlay);
    }

    const reportHtml = this._escHtml(report).replace(/\n/g, '<br>');
    overlay.innerHTML = `
      <div style="background:var(--bg-panel,#1a1a2e);border:2px solid ${action_required ? '#ff6b6b' : 'var(--accent,#4fc3f7)'};border-radius:4px;padding:12px;max-width:500px;max-height:70vh;overflow-y:auto;font-family:var(--font-mono,monospace);font-size:7px;color:var(--text,#e0e0e0);">
        <div style="font-size:9px;font-weight:bold;margin-bottom:8px;color:${action_required ? '#ff6b6b' : 'var(--accent,#4fc3f7)'};">
          ${icon} ${this._escHtml(subject)}${urgency}
        </div>
        <div style="line-height:1.5;margin-bottom:10px;white-space:pre-wrap;">${reportHtml}</div>
        <div style="text-align:right;">
          <button class="pixel-btn small" id="ceo-report-dismiss">知道了</button>
        </div>
      </div>
    `;
    overlay.classList.remove('hidden');
    overlay.style.display = 'flex';
    document.getElementById('ceo-report-dismiss').onclick = () => {
      overlay.style.display = 'none';
    };
  }

  _showCodeUpdateBanner(count, files) {
    const banner = document.getElementById('code-update-banner');
    const textEl = document.getElementById('code-update-text');
    const shortFiles = (files || []).map(f => f.split('/').slice(-2).join('/'));
    textEl.textContent = `🔄 ${count} file(s) changed: ${shortFiles.slice(0, 3).join(', ')}${count > 3 ? '...' : ''}`;
    banner.classList.remove('hidden');
  }

  _escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  async _loadModelOrApiKeySection(empId) {
    const container = document.getElementById('emp-settings-container');
    container.innerHTML = '<div style="color:var(--text-dim);font-size:6px;padding:4px;">Loading settings...</div>';

    try {
      const empResp = await fetch(`/api/employee/${empId}`).then(r => r.json());
      const manifest = empResp.manifest;

      if (manifest && manifest.settings && manifest.settings.sections) {
        container.innerHTML = '';
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
      const isSet = field.key === 'api_key' ? empData.api_key_set : !!currentValue;
      input.placeholder = isSet
        ? `Set (${empData.api_key_preview || '****'})`
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
      select.innerHTML = options.map(o =>
        `<option value="${o}"${o === currentValue ? ' selected' : ''}>${o}</option>`
      ).join('');
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
      const modelsResp = this.cachedModels
        ? { models: this.cachedModels }
        : await fetch('/api/models').then(r => r.json());
      const models = modelsResp.models || [];
      if (!this.cachedModels && models.length > 0) {
        this.cachedModels = models;
      }
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
      this.logEntry('CEO', `Settings saved for employee #${empId}`, 'ceo');
      // Refresh to show updated status
      this._loadModelOrApiKeySection(empId);
    } catch (err) {
      this.logEntry('SYSTEM', `Save failed: ${err.message}`, 'system');
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save';
    }
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
          <option value="openrouter"${currentProvider === 'openrouter' ? ' selected' : ''}>OpenRouter</option>
          <option value="anthropic"${currentProvider === 'anthropic' ? ' selected' : ''}>Anthropic</option>
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

    // Provider change handler
    document.getElementById('emp-detail-provider').addEventListener('change', (e) => {
      const provider = e.target.value;
      const saveBtn = document.getElementById('emp-model-save-btn');
      saveBtn.disabled = true;
      saveBtn.textContent = 'Switching...';
      fetch(`/api/employee/${empId}/provider`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_provider: provider }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            this.logEntry('SYSTEM', `Provider switch failed: ${data.error}`, 'system');
          } else {
            this.logEntry('CEO', `Switched to ${provider}`, 'ceo');
            // Reload settings to reflect new provider
            this._loadModelOrApiKeySection(empId);
          }
        })
        .catch(err => this.logEntry('SYSTEM', `Switch failed: ${err.message}`, 'system'))
        .finally(() => { saveBtn.disabled = false; saveBtn.textContent = 'Save'; });
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
      const modelsResp = this.cachedModels
        ? { models: this.cachedModels }
        : await fetch('/api/models').then(r => r.json());

      const currentModel = empResp.llm_model || '';
      const models = modelsResp.models || [];
      if (!this.cachedModels && models.length > 0) {
        this.cachedModels = models;
      }

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
    this._interviewingCandidate = null;

    const modal = document.getElementById('candidate-modal');
    const jdEl = document.getElementById('candidate-jd');
    const cardsEl = document.getElementById('candidate-cards');

    jdEl.innerHTML = '<div style="font-size:7px;color:var(--pixel-yellow);margin-bottom:4px;">JD — Job Description</div>' +
      (payload.jd || '').replace(/\n/g, '<br>');
    cardsEl.innerHTML = '';

    const ROLE_EMOJI = {
      Engineer: '💻', Designer: '🎨', Analyst: '📊',
      DevOps: '🔧', QA: '🧪', Marketing: '📢',
    };

    for (const c of this._candidateList) {
      const card = document.createElement('div');
      card.className = 'candidate-card';
      const emoji = ROLE_EMOJI[c.role] || '🤖';
      const tags = (c.personality_tags || []).join(' / ');
      const skills = (c.skill_set || []).map(s => typeof s === 'object' ? s.name : s).join(', ');
      const tools = (c.tool_set || []).map(t => typeof t === 'object' ? t.name : t).join(', ');
      const prompt = (c.system_prompt || '').substring(0, 80);
      const relevance = c.jd_relevance ? `${(c.jd_relevance * 100).toFixed(0)}%` : '-';

      const llmModel = c.llm_model || 'default';
      const costPer1m = c.cost_per_1m_tokens ? `$${c.cost_per_1m_tokens.toFixed(2)}/1M` : 'N/A';
      const hiringFee = c.hiring_fee ? `$${c.hiring_fee.toFixed(2)}` : 'Free';
      const hosting = c.hosting || 'company';
      const hostingLabel = hosting === 'self' ? '🏠 Self-hosted' : '🏢 Company';
      const authLabel = c.auth_method === 'oauth' ? 'OAuth' : 'API Key';
      card.innerHTML = `
        <div class="card-inner">
          <div class="card-front">
            <div class="card-avatar">${emoji}</div>
            <div class="card-name">${c.name}</div>
            <div class="card-role">${c.role} (${c.experience_years || '?'}yr)</div>
            <div class="card-model" title="${llmModel}">🤖 ${llmModel.split('/').pop()}</div>
            <div class="card-tags">${tags}</div>
            <div class="card-relevance">Match: ${relevance}</div>
            <div class="card-cost">Cost: ${costPer1m} | Fee: ${hiringFee}</div>
            <div class="card-hosting">${hostingLabel}${c.remote ? ' | Remote' : ''}</div>
          </div>
          <div class="card-back">
            <div class="card-detail-title">Skills</div>
            <div class="card-detail-text">${skills}</div>
            <div class="card-detail-title">Tools</div>
            <div class="card-detail-text">${tools}</div>
            <div class="card-detail-title">LLM</div>
            <div class="card-detail-text">${c.llm_model || 'default'} (${c.api_provider || 'openrouter'})</div>
            <div class="card-detail-title">Cost</div>
            <div class="card-detail-text">${costPer1m} | Hiring: ${hiringFee}</div>
            <div class="card-detail-title">Hosting</div>
            <div class="card-detail-text">${hostingLabel} | Auth: ${authLabel}</div>
            <div class="card-actions">
              <button class="pixel-btn hire" data-id="${c.id}">Hire</button>
              <button class="pixel-btn interview" data-id="${c.id}">Interview</button>
            </div>
          </div>
        </div>
      `;

      // Click card to flip
      card.addEventListener('click', (e) => {
        if (e.target.closest('.pixel-btn')) return; // don't flip on button click
        card.classList.toggle('flipped');
      });

      // Hire button
      card.querySelector('.pixel-btn.hire').addEventListener('click', () => this.hireCandidate(c));

      // Interview button — opens separate chatbot modal
      card.querySelector('.pixel-btn.interview').addEventListener('click', () => this.startInterview(c));

      cardsEl.appendChild(card);
    }

    modal.classList.remove('hidden');
  }

  closeCandidateModal() {
    document.getElementById('candidate-modal').classList.add('hidden');
    this._interviewingCandidate = null;
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
          const nn = data.nickname ? ` (${data.nickname})` : '';
          this.logEntry('CEO', `🎉 Hired: ${data.name}${nn}`, 'ceo');
          if (data.state) {
            this.handleMessage({ type: 'state_snapshot', state: data.state, payload: {} });
          }
          this.closeCandidateModal();
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

  // ===== Review Queue =====

  /**
   * Enqueue meeting report action items — each becomes a separate review entry.
   */
  _enqueueReviewItems(payload) {
    const reportId = payload.report_id || '';
    const summary = payload.summary || '';
    const actions = payload.action_items || [];

    // Store report id on the queue batch so we can send bulk approval later
    const batchId = `report_${Date.now()}`;

    // First item: the report summary (approve = acknowledge, reject = dismiss)
    if (summary) {
      this.reviewQueue.push({
        type: 'report_summary',
        data: { report_id: reportId, summary, batchId },
      });
    }

    // Each action item as an individual review entry
    actions.forEach((item, idx) => {
      this.reviewQueue.push({
        type: 'action_item',
        data: {
          report_id: reportId,
          index: idx,
          source: item.source || '',
          description: item.description || (typeof item === 'string' ? item : JSON.stringify(item)),
          batchId,
        },
        decision: null,  // null = pending, true = approved, false = rejected
      });
    });

    this._showReviewQueue();
  }

  /**
   * Enqueue a file edit proposal.
   */
  _enqueueFileEdit(payload) {
    this.reviewQueue.push({
      type: 'file_edit',
      data: payload,
    });
    this._showReviewQueue();
  }

  /**
   * Show the review queue panel, rendering the current item.
   */
  _showReviewQueue() {
    if (this.reviewQueue.length === 0) {
      document.getElementById('review-queue-section').classList.add('hidden');
      return;
    }
    // Clamp index
    if (this._reviewIndex >= this.reviewQueue.length) {
      this._reviewIndex = 0;
    }
    document.getElementById('review-queue-section').classList.remove('hidden');
    this._renderCurrentReview();
  }

  /**
   * Render the current review item with counter.
   */
  _renderCurrentReview() {
    const section = document.getElementById('review-queue-section');
    const content = document.getElementById('review-queue-content');
    const counter = document.getElementById('review-queue-counter');
    const title = document.getElementById('review-queue-title');

    if (this.reviewQueue.length === 0) {
      section.classList.add('hidden');
      return;
    }

    const item = this.reviewQueue[this._reviewIndex];
    counter.textContent = `${this._reviewIndex + 1}/${this.reviewQueue.length}`;

    if (item.type === 'report_summary') {
      title.innerHTML = '&#128196; Meeting Report';
      content.innerHTML = `
        <div class="review-summary md-rendered">${this._renderMarkdown(item.data.summary || '')}</div>
      `;
    } else if (item.type === 'action_item') {
      title.innerHTML = '&#128203; Improvement Items';
      content.innerHTML = `
        <div class="review-action-item" style="padding:4px 6px;">
          <div style="font-size:6px;color:var(--pixel-cyan);margin-bottom:2px;">Source: ${this._escapeHtml(item.data.source)}</div>
          <div class="md-rendered" style="font-size:7px;">${this._renderMarkdown(item.data.description)}</div>
        </div>
      `;
    } else if (item.type === 'file_edit') {
      title.innerHTML = '&#128221; File Edit';
      const p = item.data;
      let html = `
        <div class="file-edit-meta">
          <div class="file-edit-info"><span class="fe-label">File</span><span class="fe-value">${this._escapeHtml(p.rel_path || '')}</span></div>
          <div class="file-edit-info"><span class="fe-label">Proposed by</span><span class="fe-value">${this._escapeHtml(p.proposed_by || 'agent')}</span></div>
          <div class="file-edit-info"><span class="fe-label">Reason</span><span class="fe-value">${this._escapeHtml(p.reason || '')}</span></div>
        </div>
      `;
      html += this._buildDiffView(p.old_content || '', p.new_content || '');
      content.innerHTML = html;
    }
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

  /**
   * Handle approve button click for the current review item.
   */
  _reviewApprove() {
    const item = this.reviewQueue[this._reviewIndex];
    if (!item) return;

    if (item.type === 'report_summary') {
      // Just acknowledge the summary, move to next
      this.logEntry('CEO', 'Meeting report reviewed', 'ceo');
      this._advanceReview();

    } else if (item.type === 'action_item') {
      this._recordActionDecision(item.data.report_id, item.data.index, true);
      this.logEntry('CEO', `✅ Approved: ${item.data.description.substring(0, 40)}`, 'ceo');
      this._advanceReview();

    } else if (item.type === 'file_edit') {
      const editId = item.data.edit_id;
      fetch(`/api/file-edits/${editId}/approve`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'error') {
            this.logEntry('SYSTEM', `Approval failed: ${data.message}`, 'system');
          } else {
            this.logEntry('CEO', `✅ File edit approved: ${data.rel_path}`, 'ceo');
          }
          this._advanceReview();
        })
        .catch(err => {
          this.logEntry('SYSTEM', `Approval failed: ${err.message}`, 'system');
          this._advanceReview();
        });
    }
  }

  /**
   * Handle reject button click for the current review item.
   */
  _reviewReject() {
    const item = this.reviewQueue[this._reviewIndex];
    if (!item) return;

    if (item.type === 'report_summary') {
      this.logEntry('CEO', 'Meeting report dismissed', 'ceo');
      this._advanceReview();

    } else if (item.type === 'action_item') {
      this._recordActionDecision(item.data.report_id, item.data.index, false);
      this.logEntry('CEO', `❌ Rejected: ${item.data.description.substring(0, 40)}`, 'ceo');
      this._advanceReview();

    } else if (item.type === 'file_edit') {
      const editId = item.data.edit_id;
      fetch(`/api/file-edits/${editId}/reject`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'error') {
            this.logEntry('SYSTEM', `Action failed: ${data.message}`, 'system');
          } else {
            this.logEntry('CEO', `❌ File edit rejected: ${data.rel_path}`, 'ceo');
          }
          this._advanceReview();
        })
        .catch(err => {
          this.logEntry('SYSTEM', `Action failed: ${err.message}`, 'system');
          this._advanceReview();
        });
    }
  }

  /**
   * Advance to the next review item. When all items in a batch are reviewed,
   * send the batch approval to the backend.
   */
  _advanceReview() {
    // Remove current item from queue
    this.reviewQueue.splice(this._reviewIndex, 1);

    // Check if we just finished an action_item batch — collect decisions
    this._flushActionBatch();

    if (this.reviewQueue.length === 0) {
      document.getElementById('review-queue-section').classList.add('hidden');
      this._reviewIndex = 0;
      return;
    }

    // Clamp index (stay at same position since we removed current)
    if (this._reviewIndex >= this.reviewQueue.length) {
      this._reviewIndex = 0;
    }
    this._renderCurrentReview();
  }

  /**
   * Record an action item decision for later batch submission.
   */
  _recordActionDecision(reportId, index, approved) {
    if (!this._reviewDecisions) this._reviewDecisions = {};
    if (!this._reviewDecisions[reportId]) this._reviewDecisions[reportId] = [];
    this._reviewDecisions[reportId].push({ index, approved });
  }

  /**
   * When no more action_items remain for a report, send bulk approval.
   */
  _flushActionBatch() {
    if (!this._reviewDecisions) this._reviewDecisions = {};

    // Check if any report batches have no remaining items in the queue
    const pendingReportIds = new Set();
    for (const item of this.reviewQueue) {
      if (item.type === 'action_item' && item.data.report_id) {
        pendingReportIds.add(item.data.report_id);
      }
    }

    // For each tracked report, if no more items pending, send approval
    for (const [reportId, decisions] of Object.entries(this._reviewDecisions)) {
      if (!pendingReportIds.has(reportId) && decisions.length > 0) {
        const approved = decisions
          .filter(d => d.approved)
          .map(d => d.index);
        if (approved.length > 0) {
          fetch('/api/routine/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: reportId, approved_indices: approved }),
          })
            .then(r => r.json())
            .then(data => {
              if (data.error) {
                this.logEntry('SYSTEM', `Execution failed: ${data.error}`, 'system');
              } else {
                this.logEntry('CEO', `✅ Approved ${approved.length} improvements, executing`, 'ceo');
              }
            })
            .catch(err => this.logEntry('SYSTEM', `Execution failed: ${err.message}`, 'system'));
        }
        delete this._reviewDecisions[reportId];
      }
    }
  }

  // ===== Resolution Review Modal =====

  _showResolutionModal(resolution) {
    this._currentResolution = resolution;
    const modal = document.getElementById('resolution-modal');
    const taskInfo = document.getElementById('resolution-task-info');
    const editsEl = document.getElementById('resolution-edits');

    taskInfo.innerHTML = `
      <div class="res-task-label">Task: <span class="res-task-text">${this._escapeHtml(resolution.task || '')}</span></div>
      <div class="res-meta">Project: ${this._escapeHtml(resolution.project_id || '')} &middot; ${(resolution.edits || []).length} edit(s)</div>
    `;

    let html = '';
    for (const edit of (resolution.edits || [])) {
      html += `<div class="res-edit-card" data-edit-id="${this._escapeHtml(edit.edit_id)}">`;
      html += `<div class="res-edit-header">`;
      html += `<span class="res-edit-file">${this._escapeHtml(edit.rel_path || '')}</span>`;
      html += `<span class="res-edit-by">by ${this._escapeHtml(edit.proposed_by || 'agent')}</span>`;
      html += `</div>`;
      html += `<div class="res-edit-reason">${this._escapeHtml(edit.reason || '')}</div>`;
      html += this._buildDiffView(edit.old_content || '', edit.new_content || '');
      html += `<div class="res-edit-actions">`;
      html += `<label class="res-radio"><input type="radio" name="decision_${edit.edit_id}" value="approve" checked> Approve</label>`;
      html += `<label class="res-radio"><input type="radio" name="decision_${edit.edit_id}" value="reject"> Reject</label>`;
      html += `<label class="res-radio"><input type="radio" name="decision_${edit.edit_id}" value="defer"> Defer</label>`;
      html += `</div>`;
      html += `</div>`;
    }
    editsEl.innerHTML = html;
    modal.classList.remove('hidden');
  }

  _closeResolutionModal() {
    document.getElementById('resolution-modal').classList.add('hidden');
    this._currentResolution = null;
  }

  _submitResolutionDecisions() {
    const resolution = this._currentResolution;
    if (!resolution) return;

    const decisions = {};
    for (const edit of (resolution.edits || [])) {
      const selected = document.querySelector(`input[name="decision_${edit.edit_id}"]:checked`);
      if (selected) {
        decisions[edit.edit_id] = selected.value;
      }
    }

    const btn = document.getElementById('resolution-submit-btn');
    btn.disabled = true;

    fetch(`/api/resolutions/${encodeURIComponent(resolution.resolution_id)}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decisions }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'error') {
          this.logEntry('SYSTEM', `Resolution error: ${data.message}`, 'system');
        } else {
          const results = data.results || [];
          const approved = results.filter(r => r.decision === 'approve').length;
          const rejected = results.filter(r => r.decision === 'reject').length;
          const deferred = results.filter(r => r.decision === 'defer').length;
          this.logEntry('CEO', `Resolution decided: ${approved} approved, ${rejected} rejected, ${deferred} deferred`, 'ceo');
          this._refreshDeferredPanel();
        }
        this._closeResolutionModal();
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Resolution submit failed: ${err.message}`, 'system');
      })
      .finally(() => {
        btn.disabled = false;
      });
  }

  // ===== Deferred Resolutions Panel =====

  _refreshDeferredPanel() {
    fetch('/api/resolutions/deferred')
      .then(r => r.json())
      .then(data => {
        const list = document.getElementById('deferred-list');
        const edits = data.edits || [];
        if (edits.length === 0) {
          list.innerHTML = '<div class="deferred-empty">No deferred edits</div>';
          return;
        }
        let html = '';
        for (const edit of edits) {
          const expiredBadge = edit.expired
            ? '<span class="deferred-expired">EXPIRED</span>'
            : '<span class="deferred-active">ACTIVE</span>';
          html += `<div class="deferred-card${edit.expired ? ' expired' : ''}">`;
          html += `<div class="deferred-card-header">`;
          html += `<span class="deferred-file">${this._escapeHtml(edit.rel_path || '')}</span>`;
          html += expiredBadge;
          html += `</div>`;
          html += `<div class="deferred-reason">${this._escapeHtml(edit.reason || '')}</div>`;
          if (!edit.expired) {
            html += `<button class="pixel-btn small deferred-exec-btn" data-res-id="${this._escapeHtml(edit.resolution_id)}" data-edit-id="${this._escapeHtml(edit.edit_id)}">Execute</button>`;
          }
          html += `</div>`;
        }
        list.innerHTML = html;

        // Bind execute buttons
        list.querySelectorAll('.deferred-exec-btn').forEach(btn => {
          btn.addEventListener('click', () => {
            const resId = btn.dataset.resId;
            const editId = btn.dataset.editId;
            btn.disabled = true;
            fetch(`/api/resolutions/deferred/${encodeURIComponent(resId)}/${encodeURIComponent(editId)}/execute`, {
              method: 'POST',
            })
              .then(r => r.json())
              .then(result => {
                if (result.status === 'error') {
                  this.logEntry('SYSTEM', `Deferred exec failed: ${result.message}`, 'system');
                } else {
                  this.logEntry('CEO', `Deferred edit executed: ${result.rel_path || editId}`, 'ceo');
                }
                this._refreshDeferredPanel();
              })
              .catch(err => {
                this.logEntry('SYSTEM', `Deferred exec failed: ${err.message}`, 'system');
                btn.disabled = false;
              });
          });
        });
      })
      .catch(() => {});
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

    // Load chat history
    const chatEl = document.getElementById('meeting-chat-messages');
    chatEl.innerHTML = '';
    const history = this.meetingChats[room.id] || [];
    if (history.length === 0) {
      chatEl.innerHTML = '<div class="chat-empty">No meeting logs</div>';
    } else {
      for (const msg of history) {
        this._appendChatMessage(msg);
      }
    }
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
  _startInquiryMode(payload, state) {
    this._inquirySessionId = payload.session_id;
    this._inquiryRoomId = payload.room_id;

    // Find the room from state
    const rooms = (state && state.meeting_rooms) || [];
    const room = rooms.find(r => r.id === payload.room_id);
    if (room) {
      this.openMeetingRoom(room);
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
          if (data.state) {
            this.handleMessage({ type: 'state_snapshot', state: data.state, payload: {} });
          }
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
      const resp = await fetch('/api/settings/api');
      const data = await resp.json();
      const or = data.openrouter || {};
      const ant = data.anthropic || {};

      // Fetch models for dropdown (cached)
      let modelOptions = '';
      if (!this.cachedModels) {
        try {
          const mResp = await fetch('/api/models');
          const mData = await mResp.json();
          this.cachedModels = mData.models || [];
        } catch { this.cachedModels = []; }
      }
      for (const m of this.cachedModels) {
        const sel = m.id === or.default_model ? ' selected' : '';
        modelOptions += `<option value="${m.id}"${sel}>${m.name || m.id}</option>`;
      }
      if (!modelOptions) {
        modelOptions = `<option value="${or.default_model || ''}" selected>${or.default_model || '(none)'}</option>`;
      }

      container.innerHTML = `
        <div class="api-provider-card">
          <div class="api-card-header api-card-toggle" data-target="api-or-body">
            <span class="api-status-dot ${or.api_key_set ? 'online' : 'offline'}"></span>
            <span class="api-card-title">OpenRouter</span>
            <span class="api-card-arrow">&#9660;</span>
          </div>
          <div id="api-or-body" class="api-card-body collapsed">
            <label class="api-field-label">API Key</label>
            <input type="password" id="api-or-key" class="api-key-input" placeholder="${or.api_key_set ? or.api_key_preview : 'sk-or-...'}" />
            <label class="api-field-label">Base URL</label>
            <input type="text" id="api-or-url" class="api-key-input" value="${this._escHtml(or.base_url || '')}" />
            <label class="api-field-label">Default Model</label>
            <select id="api-or-model" class="api-key-input">${modelOptions}</select>
            <div class="api-card-actions">
              <button class="pixel-btn small api-test-btn" onclick="app._testApiConnection('openrouter')">Test</button>
              <button class="pixel-btn small" onclick="app._saveApiSettings('openrouter')">Save</button>
              <span id="api-or-result" class="api-test-result"></span>
            </div>
          </div>
        </div>
        <div class="api-provider-card">
          <div class="api-card-header api-card-toggle" data-target="api-ant-body">
            <span class="api-status-dot ${ant.api_key_set ? 'online' : 'offline'}"></span>
            <span class="api-card-title">Anthropic</span>
            <span class="api-card-arrow">&#9660;</span>
          </div>
          <div id="api-ant-body" class="api-card-body collapsed">
            <div class="api-card-actions">
              <button class="pixel-btn small" onclick="app._startCompanyOAuth()">OAuth Login</button>
              <span class="api-field-label" style="margin-left:4px;">${ant.api_key_set ? '&#9989; Connected' : '&#10060; Not connected'}</span>
            </div>
          </div>
        </div>
      `;
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
    const body = { provider };
    if (provider === 'openrouter') {
      const key = document.getElementById('api-or-key').value.trim();
      const url = document.getElementById('api-or-url').value.trim();
      const model = document.getElementById('api-or-model').value;
      if (key) body.api_key = key;
      if (url) body.base_url = url;
      if (model) body.default_model = model;
    } else {
      const key = document.getElementById('api-ant-key').value.trim();
      if (key) body.api_key = key;
    }
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

  async _testApiConnection(provider) {
    const resultEl = document.getElementById(provider === 'openrouter' ? 'api-or-result' : 'api-ant-result');
    resultEl.textContent = '...';
    resultEl.className = 'api-test-result';
    try {
      const resp = await fetch('/api/settings/api/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      });
      const data = await resp.json();
      if (data.ok) {
        resultEl.textContent = 'OK';
        resultEl.classList.add('success');
      } else {
        resultEl.textContent = 'FAIL';
        resultEl.classList.add('fail');
      }
    } catch (e) {
      resultEl.textContent = 'ERR';
      resultEl.classList.add('fail');
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
    const items = window.officeRenderer?.state?.company_culture || [];
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

    const task = `CEO写了一段公司方向的草稿，请帮忙润色和丰富成一个完整的企业定位描述，保留核心意思，补充战略愿景、目标市场、核心竞争力等维度。润色完成后，使用 save_company_direction 工具保存。\n\n草稿内容:\n${draft}`;

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

  openToolList() {
    const modal = document.getElementById('tool-list-modal');
    const body = document.getElementById('tool-list-body');
    const tools = (this.state && this.state.tools) || [];

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
    modal.classList.remove('hidden');
  }

  async openToolDetail(toolId) {
    const res = await fetch(`/api/tools/${encodeURIComponent(toolId)}/definition`);
    if (!res.ok) return;
    const data = await res.json();

    const body = document.getElementById('tool-list-body');
    body.innerHTML = `
      <button class="btn-back" onclick="window.app.openToolList()">&larr; Back</button>
      <div class="tool-detail">
        <h3>${this._escapeHtml(data.name)}</h3>
        <p>${this._escapeHtml(data.description || '')}</p>
        <div class="tool-detail-section-title">Definition (tool.yaml)</div>
        <pre class="tool-yaml-content">${this._escapeHtml(data.yaml_content)}</pre>
        ${data.files.length > 0 ? `
          <div class="tool-detail-section-title">Files</div>
          <ul class="tool-file-list">${data.files.map(f => `<li>${this._escapeHtml(f)}</li>`).join('')}</ul>
        ` : ''}
      </div>
    `;
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
    let projectName = '';
    if (projectId === '__new__') {
      const nameInput = document.getElementById('new-project-name');
      projectName = nameInput ? nameInput.value.trim() : '';
      if (!projectName) { alert('Enter project name'); return; }
    }

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
    if (projectId && projectId !== '__new__') reqBody.project_id = projectId;
    if (projectName) reqBody.project_name = projectName;

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
    const newNameInput = document.getElementById('new-project-name');
    if (newNameInput) { newNameInput.value = ''; newNameInput.classList.add('hidden'); }
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
          const isActive = p.status === 'active';
          card.className = `project-panel-card ${isActive ? 'active' : 'archived'}`;
          card.innerHTML = `
            <div class="project-panel-name">${this._escHtml(p.name)}</div>
            <div class="project-panel-meta">${p.iteration_count} iteration${p.iteration_count !== 1 ? 's' : ''} · ${p.status}</div>
          `;
          card.style.cursor = 'pointer';
          card.addEventListener('click', () => this._openProjectDetail(p.project_id));
          panel.appendChild(card);
        }
      })
      .catch(() => {});
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
          <span style="color:var(--pixel-cyan);font-size:8px;">${this._escHtml(proj.name || projectId)}</span>
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

    fetch(`/api/projects/${encodeURIComponent(iterationId)}`)
      .then(r => r.json())
      .then(doc => {
        if (doc.error) {
          panel.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">${doc.error}</div>`;
          return;
        }

        // Tab bar — "详情" fixed + dynamic plugin tabs
        const plugins = window.pluginLoader.getPlugins();
        let tabBarHtml = `<div class="project-tabs"><button class="project-tab active" data-tab="detail">详情</button>`;
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
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:6px 0 3px;">验收标准 (${criteria.length})</div>`;
          const ar = doc.acceptance_result;
          for (let i = 0; i < criteria.length; i++) {
            const icon = ar ? (ar.accepted ? '\u2705' : '\u274C') : '\u2B1C';
            detailHtml += `<div style="font-size:5px;color:var(--pixel-white);padding:1px 0;">${icon} ${i + 1}. ${this._escHtml(criteria[i])}</div>`;
          }
          if (ar) {
            const arIcon = ar.accepted ? '\u2705' : '\u274C';
            const arLabel = ar.accepted ? '通过' : '未通过';
            const arNotes = ar.notes ? ` — ${this._escHtml(ar.notes.substring(0, 200))}${ar.notes.length > 200 ? '...' : ''}` : '';
            detailHtml += `<div style="font-size:6px;color:${ar.accepted ? 'var(--pixel-green)' : 'var(--pixel-red)'};margin:4px 0;">${arIcon} 验收结果: ${arLabel}${arNotes}</div>`;
          }
          const ear = doc.ea_review_result;
          if (ear) {
            const earIcon = ear.approved ? '\u2705' : '\u274C';
            const earLabel = ear.approved ? '通过' : '驳回';
            const earNotes = ear.notes ? ` — ${this._escHtml(ear.notes.substring(0, 200))}${ear.notes.length > 200 ? '...' : ''}` : '';
            detailHtml += `<div style="font-size:6px;color:${ear.approved ? 'var(--pixel-green)' : 'var(--pixel-red)'};margin:2px 0;">EA审核: ${earIcon} ${earLabel}${earNotes}</div>`;
          }
        }

        if (doc.status !== 'completed') {
          detailHtml += `<div style="margin:8px 0;"><button class="pixel-btn" id="continue-iter-btn" style="font-size:6px;padding:4px 10px;">\u25B6 继续当前轮次</button></div>`;
        }

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

        if (doc.output) {
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

        // Build full panel HTML with tabs — detail + dynamic plugin containers
        let fullHtml = tabBarHtml + `<div class="project-tab-content" data-tab="detail">${detailHtml}</div>`;
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
            if (tabName.startsWith('plugin-')) {
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

        // Bind continue button
        const continueBtn = document.getElementById('continue-iter-btn');
        if (continueBtn) {
          continueBtn.addEventListener('click', () => {
            this._continueIteration(projectId, iterationId);
          });
        }
      })
      .catch(err => {
        panel.innerHTML = `<div style="color:var(--pixel-red);font-size:6px;">Load failed: ${err.message}</div>`;
      });
  }

  _continueIteration(projectId, iterationId) {
    const btn = document.getElementById('continue-iter-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 提交中...'; }

    fetch('/api/projects/continue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, iteration_id: iterationId }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('CEO', `继续失败: ${data.error}`, 'error');
          if (btn) { btn.disabled = false; btn.textContent = '▶ 继续当前轮次'; }
        } else {
          this.logEntry('CEO', `已继续轮次 ${iterationId}，任务已推送给 ${data.routed_to}`, 'ceo');
          const modal = document.getElementById('project-modal');
          if (modal) modal.classList.add('hidden');
        }
      })
      .catch(err => {
        this.logEntry('CEO', `继续失败: ${err.message}`, 'error');
        if (btn) { btn.disabled = false; btn.textContent = '▶ 继续当前轮次'; }
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

// Boot
window.app = new AppController();
