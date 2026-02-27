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
    this.connect();
    this.bindUI();
    this.bindCollapsibles();
  }

  // ===== WebSocket =====
  connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws`;

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.logEntry('SYSTEM', 'Connected to company server', 'system');
      this.reconnectDelay = 1000;
      const statusEl = document.getElementById('connection-status');
      statusEl.textContent = '● ONLINE';
      statusEl.classList.add('online');
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
      this.updateGuidanceDropdown(msg.state.employees);
      // Refresh guidance notes display if an employee is selected
      const sel = document.getElementById('guidance-target');
      if (sel.value) this.showGuidanceNotes(sel.value);
      // Refresh meeting modal if open
      if (this.viewingRoomId) {
        const room = rooms.find(r => r.id === this.viewingRoomId);
        if (room) this._refreshMeetingModalStatus(room);
      }
      // Refresh culture wall if modal is open
      if (!document.getElementById('culture-wall-modal').classList.contains('hidden')) {
        this._renderCultureWall();
      }
    }

    // Log the event
    const formatters = {
      'state_snapshot':     () => ({ text: 'State loaded', cls: 'system', agent: 'SYSTEM' }),
      'ceo_task_submitted': (p) => ({ text: `📋 Task: ${p.task}`, cls: 'ceo', agent: 'CEO' }),
      'agent_thinking':     (p) => ({ text: `💭 ${p.message}`, cls: (msg.agent || '').toLowerCase(), agent: msg.agent }),
      'agent_done':         (p) => ({ text: `✅ ${p.role} done: ${p.summary}`, cls: (p.role || '').toLowerCase(), agent: p.role }),
      'employee_hired':     (p) => ({ text: `🎉 New hire: ${p.name} (${p.role})`, cls: 'hr', agent: 'HR' }),
      'employee_fired':     (p) => ({ text: `🚪 Departure: ${p.name}${p.nickname ? '(' + p.nickname + ')' : ''} — ${p.reason || ''}`, cls: 'hr', agent: 'HR' }),
      'employee_rehired':   (p) => ({ text: `🔄 Rehired: ${p.name}${p.nickname ? '(' + p.nickname + ')' : ''} (${p.role})`, cls: 'hr', agent: 'CEO' }),
      'employee_reviewed':  (p) => ({ text: `📊 Quarterly review: ${p.id} — Score: ${p.score}`, cls: 'hr', agent: 'HR' }),
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
        this._enqueueReviewItems(p);
        return { text: `📄 Meeting report ready for CEO review`, cls: 'ceo', agent: 'SYSTEM' };
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
    };

    const formatter = formatters[msg.type];
    if (formatter) {
      const { text, cls, agent } = formatter(msg.payload || {});
      this.logEntry(agent || 'SYSTEM', text, cls);
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
      panel.appendChild(card);
    }
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
          <div class="roster-name">${roleIcon} ${emp.name} ${nn}${guidanceBadge}</div>
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

    input.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        this.submitTask();
      }
    });

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

    // Roster filter bindings
    ['roster-filter-role', 'roster-filter-dept', 'roster-filter-level'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => this._onRosterFilterChange());
    });

    // Guidance modal bindings
    const guidanceToolbarBtn = document.getElementById('guidance-toolbar-btn');
    const guidanceCloseBtn = document.getElementById('guidance-close-btn');
    const guidanceModal = document.getElementById('guidance-modal');
    const guidanceTarget = document.getElementById('guidance-target');
    const guidanceInput = document.getElementById('guidance-input');
    const guidanceBtn = document.getElementById('guidance-btn');

    guidanceToolbarBtn.addEventListener('click', () => {
      guidanceModal.classList.remove('hidden');
    });

    guidanceCloseBtn.addEventListener('click', () => {
      guidanceModal.classList.add('hidden');
    });

    guidanceModal.addEventListener('click', (e) => {
      if (e.target === guidanceModal) {
        guidanceModal.classList.add('hidden');
      }
    });

    guidanceTarget.addEventListener('change', () => {
      const empId = guidanceTarget.value;
      guidanceBtn.disabled = !empId;
      this.showGuidanceNotes(empId);
    });

    guidanceBtn.addEventListener('click', () => this.submitGuidance());

    guidanceInput.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        this.submitGuidance();
      }
    });

    // Review queue panel bindings
    document.getElementById('review-approve-btn').addEventListener('click', () => this._reviewApprove());
    document.getElementById('review-reject-btn').addEventListener('click', () => this._reviewReject());

    // Meeting room modal bindings
    document.getElementById('meeting-close-btn').addEventListener('click', () => this.closeMeetingRoom());
    document.getElementById('meeting-modal').addEventListener('click', (e) => {
      if (e.target.id === 'meeting-modal') this.closeMeetingRoom();
    });

    // Employee detail modal bindings
    document.getElementById('employee-close-btn').addEventListener('click', () => this.closeEmployeeDetail());
    document.getElementById('employee-modal').addEventListener('click', (e) => {
      if (e.target.id === 'employee-modal') this.closeEmployeeDetail();
    });
    document.getElementById('emp-model-save-btn').addEventListener('click', () => this.saveEmployeeModel());

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

    // Culture wall modal bindings
    document.getElementById('culture-wall-toolbar-btn').addEventListener('click', () => this.openCultureWall());
    document.getElementById('culture-wall-close-btn').addEventListener('click', () => this.closeCultureWall());
    document.getElementById('culture-wall-modal').addEventListener('click', (e) => {
      if (e.target.id === 'culture-wall-modal') this.closeCultureWall();
    });
    document.getElementById('culture-wall-add-btn').addEventListener('click', () => this.addCultureItem());
    document.getElementById('culture-wall-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.addCultureItem(); }
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
    document.getElementById('project-back-btn').addEventListener('click', () => {
      document.getElementById('project-detail').classList.add('hidden');
      document.getElementById('project-list').classList.remove('hidden');
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
  }

  // ===== Guidance =====
  updateGuidanceDropdown(employees) {
    const select = document.getElementById('guidance-target');
    const currentVal = select.value;
    // Keep first placeholder option
    select.innerHTML = '<option value="">-- Select Employee --</option>';
    for (const emp of employees) {
      const opt = document.createElement('option');
      opt.value = emp.id;
      const icon = emp.role === 'HR' ? '💼' : emp.role === 'COO' ? '⚙️' : '🤖';
      opt.textContent = `${icon} ${emp.name} (${emp.role})`;
      if (emp.is_listening) opt.textContent += ' 📖';
      select.appendChild(opt);
    }
    // Restore selection
    if (currentVal) select.value = currentVal;
  }

  showGuidanceNotes(empId) {
    const display = document.getElementById('guidance-notes-display');
    const list = document.getElementById('guidance-notes-list');
    if (!empId) {
      display.classList.add('hidden');
      return;
    }
    // Find employee in current state
    const emp = (window.officeRenderer?.state?.employees || []).find(e => e.id === empId);
    if (!emp || !(emp.guidance_notes || []).length) {
      display.classList.add('hidden');
      return;
    }
    display.classList.remove('hidden');
    list.innerHTML = '';
    for (const note of emp.guidance_notes) {
      const item = document.createElement('div');
      item.className = 'guidance-note-item md-rendered';
      item.innerHTML = this._renderMarkdown(note);
      list.appendChild(item);
    }
  }

  submitGuidance() {
    const targetSelect = document.getElementById('guidance-target');
    const guidanceInput = document.getElementById('guidance-input');
    const guidanceBtn = document.getElementById('guidance-btn');

    const empId = targetSelect.value;
    const guidance = guidanceInput.value.trim();
    if (!empId || !guidance) return;

    guidanceBtn.disabled = true;

    fetch('/api/ceo/guidance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ employee_id: empId, guidance }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Error: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `📖 1-on-1 note sent to ${empId}`, 'ceo');
        }
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Submit failed: ${err.message}`, 'system');
      })
      .finally(() => {
        setTimeout(() => { guidanceBtn.disabled = false; }, 3000);
      });

    guidanceInput.value = '';
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

    // Load model dropdown
    this._loadModelDropdown(emp.id);

    modal.classList.remove('hidden');
  }

  closeEmployeeDetail() {
    this.viewingEmployeeId = null;
    document.getElementById('employee-modal').classList.add('hidden');
  }

  async _loadModelDropdown(empId) {
    const select = document.getElementById('emp-detail-model');
    const saveBtn = document.getElementById('emp-model-save-btn');
    select.innerHTML = '<option value="">Loading...</option>';
    saveBtn.disabled = true;

    try {
      // Fetch employee's current model and model list in parallel
      const [empResp, modelsResp] = await Promise.all([
        fetch(`/api/employee/${empId}`).then(r => r.json()),
        this.cachedModels
          ? Promise.resolve({ models: this.cachedModels })
          : fetch('/api/models').then(r => r.json()),
      ]);

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
      card.innerHTML = `
        <div class="card-inner">
          <div class="card-front">
            <div class="card-avatar">${emoji}</div>
            <div class="card-name">${c.name}</div>
            <div class="card-role">${c.role} (${c.experience_years || '?'}yr)</div>
            <div class="card-model" title="${llmModel}">🤖 ${llmModel.split('/').pop()}</div>
            <div class="card-tags">${tags}</div>
            <div class="card-relevance">Match: ${relevance}</div>
          </div>
          <div class="card-back">
            <div class="card-detail-title">System Prompt</div>
            <div class="card-detail-text">${prompt}...</div>
            <div class="card-detail-title">Skills</div>
            <div class="card-detail-text">${skills}</div>
            <div class="card-detail-title">Tools</div>
            <div class="card-detail-text">${tools}</div>
            <div class="card-detail-title">LLM Model</div>
            <div class="card-detail-text">${c.llm_model || 'default'}</div>
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
    // Nickname is auto-generated by the employee themselves
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
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'));
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
          card.innerHTML = `
            <div class="project-card-header">
              <span>${statusIcon} ${p.task.substring(0, 40)}${p.task.length > 40 ? '...' : ''}</span>
              <span class="project-card-date">${date}</span>
            </div>
            <div class="project-card-meta">
              ${p.routed_to}${p.current_owner && p.status !== 'completed' ? ' · Current: ' + p.current_owner : ''} | ${p.participant_count} participants | ${p.action_count} entries
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
  }

  // ===== Culture Wall =====
  openCultureWall() {
    document.getElementById('culture-wall-modal').classList.remove('hidden');
    this._renderCultureWall();
  }

  closeCultureWall() {
    document.getElementById('culture-wall-modal').classList.add('hidden');
  }

  _renderCultureWall() {
    const list = document.getElementById('culture-wall-list');
    const items = window.officeRenderer?.state?.culture_wall || [];
    if (!items.length) {
      list.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:12px;">No culture entries yet. CEO can add above.</div>';
      return;
    }
    list.innerHTML = items.map((item, idx) => {
      const date = item.created_at ? new Date(item.created_at).toLocaleDateString('zh-CN') : '';
      return `
        <div class="culture-wall-card">
          <div class="culture-wall-card-num">${idx + 1}</div>
          <div class="culture-wall-card-content">${this._escapeHtml(item.content)}</div>
          <div class="culture-wall-card-meta">
            <span class="culture-wall-card-date">${date}</span>
            <button class="culture-wall-delete-btn" data-index="${idx}" title="Delete">✕</button>
          </div>
        </div>`;
    }).join('');
    // Bind delete buttons
    list.querySelectorAll('.culture-wall-delete-btn').forEach(btn => {
      btn.addEventListener('click', () => this.removeCultureItem(parseInt(btn.dataset.index)));
    });
  }

  addCultureItem() {
    const input = document.getElementById('culture-wall-input');
    const content = input.value.trim();
    if (!content) return;

    const btn = document.getElementById('culture-wall-add-btn');
    btn.disabled = true;

    fetch('/api/culture-wall', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Add failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `Culture wall added: ${content.slice(0, 40)}`, 'ceo');
          input.value = '';
          // State will be refreshed via WebSocket push
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'))
      .finally(() => { btn.disabled = false; });
  }

  removeCultureItem(index) {
    fetch(`/api/culture-wall/${index}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `Delete failed: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `Culture wall removed: ${data.removed?.content?.slice(0, 40) || ''}`, 'ceo');
          // State will be refreshed via WebSocket push
        }
      })
      .catch(err => this.logEntry('SYSTEM', `Error: ${err.message}`, 'system'));
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

  submitTask() {
    const input = document.getElementById('task-input');
    const task = input.value.trim();
    if (!task) return;

    const submitBtn = document.getElementById('submit-btn');
    submitBtn.disabled = true;

    fetch('/api/ceo/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task }),
    })
      .then(r => r.json())
      .then(data => {
        this.logEntry('CEO', `Task assigned to ${data.routed_to}`, 'ceo');
      })
      .catch(err => {
        this.logEntry('SYSTEM', `Submit failed: ${err.message}`, 'system');
      })
      .finally(() => {
        setTimeout(() => { submitBtn.disabled = false; }, 2000);
      });

    input.value = '';
  }
}

// Boot
window.app = new AppController();
