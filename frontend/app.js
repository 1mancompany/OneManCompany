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
      this.logEntry('SYSTEM', '已连接到公司服务器', 'system');
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
      'state_snapshot':     () => ({ text: '状态已加载', cls: 'system', agent: 'SYSTEM' }),
      'ceo_task_submitted': (p) => ({ text: `📋 任务: ${p.task}`, cls: 'ceo', agent: 'CEO' }),
      'agent_thinking':     (p) => ({ text: `💭 ${p.message}`, cls: (msg.agent || '').toLowerCase(), agent: msg.agent }),
      'agent_done':         (p) => ({ text: `✅ ${p.role} 完成: ${p.summary}`, cls: (p.role || '').toLowerCase(), agent: p.role }),
      'employee_hired':     (p) => ({ text: `🎉 新员工入职: ${p.name} (${p.role})`, cls: 'hr', agent: 'HR' }),
      'employee_fired':     (p) => ({ text: `🚪 员工离职: ${p.name}${p.nickname ? '(' + p.nickname + ')' : ''} — ${p.reason || ''}`, cls: 'hr', agent: 'HR' }),
      'employee_rehired':   (p) => ({ text: `🔄 员工回归: ${p.name}${p.nickname ? '(' + p.nickname + ')' : ''} (${p.role})`, cls: 'hr', agent: 'CEO' }),
      'employee_reviewed':  (p) => ({ text: `📊 季度评价: ${p.id} — 绩效: ${p.score}`, cls: 'hr', agent: 'HR' }),
      'tool_added':         (p) => ({ text: `🔧 新工具: ${p.name}`, cls: 'coo', agent: 'COO' }),
      'guidance_start':     (p) => ({ text: `📖 ${p.name} 正在聆听领导教诲...`, cls: 'guidance', agent: 'CEO' }),
      'guidance_noted':     (p) => ({ text: `🎓 ${p.name}: ${p.acknowledgment}`, cls: 'guidance', agent: p.name }),
      'guidance_end':       (p) => ({ text: `📖 ${p.name} 教诲记录完毕`, cls: 'guidance', agent: 'CEO' }),
      'meeting_booked':     (p) => {
        if (p.room_id) this.meetingChats[p.room_id] = [];
        return { text: `🏢 会议室已预约: ${p.room_name || ''}`, cls: 'coo', agent: 'COO' };
      },
      'meeting_released':   (p) => {
        // Keep chat history for viewing after meeting ends
        return { text: `🏢 会议室已释放: ${p.room_name || ''}`, cls: 'coo', agent: 'COO' };
      },
      'meeting_denied':     (p) => ({ text: `🚫 会议室申请被拒: 无空闲会议室`, cls: 'coo', agent: 'COO' }),
      'routine_phase':      (p) => ({ text: `🔄 ${p.phase}: ${p.message}`, cls: 'system', agent: 'ROUTINE' }),
      'meeting_report_ready': (p) => {
        this._enqueueReviewItems(p);
        return { text: `📄 会议报告已生成，等待CEO审批`, cls: 'ceo', agent: 'SYSTEM' };
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
      'workflow_updated':    (p) => ({ text: `📋 工作流已更新: ${p.name}`, cls: 'ceo', agent: 'CEO' }),
      'candidates_ready':   (p) => {
        this.showCandidateSelection(p);
        return { text: `📋 HR 筛选完毕: ${(p.candidates || []).length} 名候选人待CEO选择`, cls: 'hr', agent: 'HR' };
      },
      'file_edit_proposed':  (p) => {
        this._enqueueFileEdit(p);
        return { text: `📝 文件编辑请求: ${p.rel_path} — ${p.reason}`, cls: 'ceo', agent: p.proposed_by || 'AGENT' };
      },
      'file_edit_applied':   (p) => ({ text: `✅ 文件已更新: ${p.rel_path}`, cls: 'ceo', agent: 'CEO' }),
      'file_edit_rejected':  (p) => ({ text: `❌ 文件编辑已拒绝: ${p.rel_path}`, cls: 'ceo', agent: 'CEO' }),
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
      panel.innerHTML = '<div class="task-empty">暂无进行中的任务</div>';
      return;
    }
    panel.innerHTML = '';
    for (const t of tasks) {
      const card = document.createElement('div');
      card.className = `task-card ${t.status}`;
      const icon = t.status === 'running' ? '🔄' : '⏳';
      const label = t.status === 'running' ? '进行中' : '排队中';
      const routeColor = t.routed_to === 'HR' ? 'var(--pixel-blue)' : 'var(--pixel-orange)';
      // Resolve current owner display name
      const ownerEmp = (this._lastEmployees || []).find(e => e.id === t.current_owner);
      const ownerLabel = ownerEmp
        ? `${ownerEmp.nickname || ownerEmp.name}`
        : (t.current_owner || t.routed_to);
      card.innerHTML = `
        <div class="task-card-status">${icon} ${label}</div>
        <div class="task-card-text">${t.task.substring(0, 60)}${t.task.length > 60 ? '...' : ''}</div>
        <div class="task-card-route" style="color:${routeColor};">${t.routed_to} · <span class="task-card-owner">当前: ${ownerLabel}</span></div>
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
        ? '<span class="roster-listening">📖 聆听中...</span>'
        : '';
      const guidanceCount = (emp.guidance_notes || []).length;
      const guidanceBadge = guidanceCount > 0
        ? `<span style="color: #aa66ff; font-size: 6px;"> [${guidanceCount}条教诲]</span>`
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
          <div class="roster-quarter">Q任务: ${qTasks}/3</div>
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

    const LEVEL_NAMES = {1: '初级', 2: '中级', 3: '高级', 4: '创始', 5: 'CEO'};

    roleSelect.innerHTML = '<option value="">全部职责</option>' +
      roles.map(r => `<option value="${r}"${r === curRole ? ' selected' : ''}>${r}</option>`).join('');
    deptSelect.innerHTML = '<option value="">全部部门</option>' +
      depts.map(d => `<option value="${d}"${d === curDept ? ' selected' : ''}>${d}</option>`).join('');
    levelSelect.innerHTML = '<option value="">全部级别</option>' +
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
      this.logEntry('CEO', '🔄 触发季度评价...', 'ceo');
      fetch('/api/hr/review', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
          setTimeout(() => { hrBtn.disabled = false; }, 5000);
        })
        .catch(err => {
          this.logEntry('SYSTEM', `错误: ${err.message}`, 'system');
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
      if (e.key === 'Enter') this.askInterviewQuestion();
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
    select.innerHTML = '<option value="">-- 选择员工 --</option>';
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
      item.className = 'guidance-note-item';
      item.textContent = note;
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
          this.logEntry('SYSTEM', `错误: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `📖 向 ${empId} 发布教诲`, 'ceo');
        }
      })
      .catch(err => {
        this.logEntry('SYSTEM', `提交失败: ${err.message}`, 'system');
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
    document.getElementById('emp-modal-title').textContent = `${roleIcon} ${emp.name || ''} 详情`;
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
    perfHtml += `<div class="perf-current-q">当前季度: ${qTasks}/3 任务</div>`;
    perfEl.innerHTML = perfHtml;

    // Work principles
    const principlesEl = document.getElementById('emp-detail-principles');
    const principles = emp.work_principles || '';
    if (principles) {
      principlesEl.textContent = principles;
      principlesEl.classList.remove('empty-hint');
    } else {
      principlesEl.innerHTML = '<span class="empty-hint">暂无工作准则</span>';
    }

    // Guidance notes
    const guidanceEl = document.getElementById('emp-detail-guidance');
    const notes = emp.guidance_notes || [];
    if (notes.length > 0) {
      guidanceEl.innerHTML = '';
      for (const note of notes) {
        const item = document.createElement('div');
        item.className = 'guidance-note-item';
        item.textContent = note;
        guidanceEl.appendChild(item);
      }
    } else {
      guidanceEl.innerHTML = '<span class="empty-hint">暂无领导教诲</span>';
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
    select.innerHTML = '<option value="">加载中...</option>';
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

      select.innerHTML = '<option value="">-- 使用默认模型 --</option>';
      for (const m of models) {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name || m.id;
        if (m.id === currentModel) opt.selected = true;
        select.appendChild(opt);
      }
      saveBtn.disabled = false;
    } catch (err) {
      select.innerHTML = '<option value="">加载失败</option>';
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
          this.logEntry('SYSTEM', `模型更新失败: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `✅ 已更新模型: ${data.model || '默认'}`, 'ceo');
        }
      })
      .catch(err => this.logEntry('SYSTEM', `更新失败: ${err.message}`, 'system'))
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

    jdEl.innerHTML = '<div style="font-size:7px;color:var(--pixel-yellow);margin-bottom:4px;">JD 岗位描述</div>' +
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

      card.innerHTML = `
        <div class="card-inner">
          <div class="card-front">
            <div class="card-avatar">${emoji}</div>
            <div class="card-name">${c.name}</div>
            <div class="card-role">${c.role} (${c.experience_years || '?'}年)</div>
            <div class="card-tags">${tags}</div>
            <div class="card-relevance">匹配度: ${relevance}</div>
          </div>
          <div class="card-back">
            <div class="card-detail-title">System Prompt</div>
            <div class="card-detail-text">${prompt}...</div>
            <div class="card-detail-title">Skills</div>
            <div class="card-detail-text">${skills}</div>
            <div class="card-detail-title">Tools</div>
            <div class="card-detail-text">${tools}</div>
            <div class="card-actions">
              <button class="pixel-btn hire" data-id="${c.id}">录用</button>
              <button class="pixel-btn interview" data-id="${c.id}">面试</button>
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
    const nickname = prompt(`为 ${candidate.name} 取一个两字中文花名:`) || '';
    if (nickname !== null) {
      fetch('/api/candidates/hire', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          batch_id: this._candidateBatchId,
          candidate_id: candidate.id,
          nickname,
        }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            this.logEntry('SYSTEM', `录用失败: ${data.error}`, 'system');
          } else {
            this.logEntry('CEO', `🎉 已录用: ${data.name}`, 'ceo');
            this.closeCandidateModal();
            this.closeInterviewModal();
          }
        })
        .catch(err => this.logEntry('SYSTEM', `错误: ${err.message}`, 'system'));
    }
  }

  // ===== Interview Chatbot (separate modal) =====
  startInterview(candidate) {
    this._interviewingCandidate = candidate;
    const modal = document.getElementById('interview-modal');
    document.getElementById('interview-modal-title').textContent =
      `💬 面试: ${candidate.name} (${candidate.role})`;
    document.getElementById('interview-chat').innerHTML =
      `<div class="a" style="color:var(--text-dim);">系统: 面试已开始，请输入问题。候选人将根据其专业背景回答。</div>`;
    document.getElementById('interview-question').value = '';
    modal.classList.remove('hidden');
    document.getElementById('interview-question').focus();
  }

  closeInterviewModal() {
    document.getElementById('interview-modal').classList.add('hidden');
  }

  askInterviewQuestion() {
    const input = document.getElementById('interview-question');
    const question = input.value.trim();
    if (!question || !this._interviewingCandidate) return;

    const chat = document.getElementById('interview-chat');
    chat.innerHTML += `<div class="q">CEO: ${question}</div>`;
    input.value = '';

    const askBtn = document.getElementById('interview-ask-btn');
    askBtn.disabled = true;

    fetch('/api/candidates/interview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        candidate: this._interviewingCandidate,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          chat.innerHTML += `<div class="a">系统: ${data.error}</div>`;
        } else {
          chat.innerHTML += `<div class="a">${this._interviewingCandidate.name}: ${data.answer}</div>`;
        }
        chat.scrollTop = chat.scrollHeight;
      })
      .catch(err => {
        chat.innerHTML += `<div class="a">系统错误: ${err.message}</div>`;
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
    listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">加载中...</div>';
    listEl.classList.remove('hidden');
    document.getElementById('project-detail').classList.add('hidden');

    fetch('/api/projects')
      .then(r => r.json())
      .then(data => {
        const projects = data.projects || [];
        if (projects.length === 0) {
          listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">暂无项目记录</div>';
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
              ${p.routed_to}${p.current_owner && p.status !== 'completed' ? ' · 当前: ' + p.current_owner : ''} | ${p.participant_count}人参与 | ${p.action_count}条记录
            </div>
          `;
          card.style.cursor = 'pointer';
          card.addEventListener('click', () => this.loadProjectDetail(p.project_id));
          listEl.appendChild(card);
        }
      })
      .catch(err => {
        listEl.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;">加载失败: ${err.message}</div>`;
      });
  }

  loadProjectDetail(projectId) {
    const listEl = document.getElementById('project-list');
    const detailEl = document.getElementById('project-detail');
    const contentEl = document.getElementById('project-detail-content');

    listEl.classList.add('hidden');
    detailEl.classList.remove('hidden');
    contentEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">加载中...</div>';

    fetch(`/api/projects/${encodeURIComponent(projectId)}`)
      .then(r => r.json())
      .then(doc => {
        if (doc.error) {
          contentEl.innerHTML = `<div style="color:var(--pixel-red);">${doc.error}</div>`;
          return;
        }
        let html = `<h4 style="color:var(--pixel-yellow);font-size:8px;margin:6px 0;">${doc.task || ''}</h4>`;
        html += `<div style="font-size:6px;color:var(--text-dim);margin-bottom:8px;">`;
        html += `状态: ${doc.status} | 路由: ${doc.routed_to} | 创建: ${(doc.created_at || '').substring(0, 19)}`;
        if (doc.completed_at) html += ` | 完成: ${doc.completed_at.substring(0, 19)}`;
        html += `</div>`;

        // Timeline
        const timeline = doc.timeline || [];
        if (timeline.length > 0) {
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:6px 0 4px;">时间线 (${timeline.length}条):</div>`;
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
          html += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 4px;">最终产出:</div>`;
          html += `<div style="font-size:6px;color:var(--pixel-white);background:var(--bg-dark);padding:6px;border:1px solid var(--border);">${doc.output}</div>`;
        }

        contentEl.innerHTML = html;
      })
      .catch(err => {
        contentEl.innerHTML = `<div style="color:var(--pixel-red);">加载失败: ${err.message}</div>`;
      });
  }

  // ===== Review Queue (逐个审核) =====

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
      title.innerHTML = '&#128196; 会议报告';
      content.innerHTML = `
        <div class="review-summary">${(item.data.summary || '').replace(/\n/g, '<br>')}</div>
      `;
    } else if (item.type === 'action_item') {
      title.innerHTML = '&#128203; 改进项';
      content.innerHTML = `
        <div class="review-action-item" style="padding:4px 6px;">
          <div style="font-size:6px;color:var(--pixel-cyan);margin-bottom:2px;">来源: ${this._escapeHtml(item.data.source)}</div>
          <div style="font-size:7px;color:var(--pixel-white);line-height:1.8;">${this._escapeHtml(item.data.description)}</div>
        </div>
      `;
    } else if (item.type === 'file_edit') {
      title.innerHTML = '&#128221; 文件编辑';
      const p = item.data;
      let html = `
        <div class="file-edit-meta">
          <div class="file-edit-info"><span class="fe-label">文件</span><span class="fe-value">${this._escapeHtml(p.rel_path || '')}</span></div>
          <div class="file-edit-info"><span class="fe-label">提出者</span><span class="fe-value">${this._escapeHtml(p.proposed_by || 'agent')}</span></div>
          <div class="file-edit-info"><span class="fe-label">原因</span><span class="fe-value">${this._escapeHtml(p.reason || '')}</span></div>
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
    html += '<div class="fe-diff-header"><span class="fe-diff-old-h">原内容</span><span class="fe-diff-new-h">新内容</span></div>';
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
      this.logEntry('CEO', '已阅会议报告', 'ceo');
      this._advanceReview();

    } else if (item.type === 'action_item') {
      this._recordActionDecision(item.data.report_id, item.data.index, true);
      this.logEntry('CEO', `✅ 批准: ${item.data.description.substring(0, 40)}`, 'ceo');
      this._advanceReview();

    } else if (item.type === 'file_edit') {
      const editId = item.data.edit_id;
      fetch(`/api/file-edits/${editId}/approve`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'error') {
            this.logEntry('SYSTEM', `审批失败: ${data.message}`, 'system');
          } else {
            this.logEntry('CEO', `✅ 文件编辑已批准: ${data.rel_path}`, 'ceo');
          }
          this._advanceReview();
        })
        .catch(err => {
          this.logEntry('SYSTEM', `审批失败: ${err.message}`, 'system');
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
      this.logEntry('CEO', '已关闭会议报告', 'ceo');
      this._advanceReview();

    } else if (item.type === 'action_item') {
      this._recordActionDecision(item.data.report_id, item.data.index, false);
      this.logEntry('CEO', `❌ 否决: ${item.data.description.substring(0, 40)}`, 'ceo');
      this._advanceReview();

    } else if (item.type === 'file_edit') {
      const editId = item.data.edit_id;
      fetch(`/api/file-edits/${editId}/reject`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          if (data.status === 'error') {
            this.logEntry('SYSTEM', `操作失败: ${data.message}`, 'system');
          } else {
            this.logEntry('CEO', `❌ 文件编辑已拒绝: ${data.rel_path}`, 'ceo');
          }
          this._advanceReview();
        })
        .catch(err => {
          this.logEntry('SYSTEM', `操作失败: ${err.message}`, 'system');
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
                this.logEntry('SYSTEM', `执行失败: ${data.error}`, 'system');
              } else {
                this.logEntry('CEO', `✅ 已批准 ${approved.length} 项改进，开始执行`, 'ceo');
              }
            })
            .catch(err => this.logEntry('SYSTEM', `执行失败: ${err.message}`, 'system'));
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
    document.getElementById('workflow-placeholder').classList.remove('hidden');
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
      .catch(err => this.logEntry('SYSTEM', `加载工作流失败: ${err.message}`, 'system'));
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
        const textarea = document.getElementById('workflow-content');
        textarea.value = data.content;
        textarea.classList.remove('hidden');
        document.getElementById('workflow-placeholder').classList.add('hidden');
        document.getElementById('workflow-save-btn').disabled = false;

        // Highlight active item
        document.querySelectorAll('.workflow-item').forEach(el => {
          el.classList.toggle('active', el.textContent === name);
        });
      })
      .catch(err => this.logEntry('SYSTEM', `加载失败: ${err.message}`, 'system'));
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
          this.logEntry('SYSTEM', `保存失败: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `📋 已更新工作流: ${this.currentWorkflowName}`, 'ceo');
        }
      })
      .catch(err => this.logEntry('SYSTEM', `保存失败: ${err.message}`, 'system'));
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
      statusText.textContent = '会议中';
    } else {
      led.className = 'status-led free';
      statusText.textContent = '空闲';
    }

    // Capacity
    document.getElementById('meeting-capacity').textContent = `${room.capacity}人`;

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
      partEl.innerHTML = '<div style="color:var(--text-dim)">暂无参会人员</div>';
    }

    // Load chat history
    const chatEl = document.getElementById('meeting-chat-messages');
    chatEl.innerHTML = '';
    const history = this.meetingChats[room.id] || [];
    if (history.length === 0) {
      chatEl.innerHTML = '<div class="chat-empty">暂无会议记录</div>';
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
      statusText.textContent = '会议中';
    } else {
      led.className = 'status-led free';
      statusText.textContent = '空闲';
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
      partEl.innerHTML = '<div style="color:var(--text-dim)">暂无参会人员</div>';
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

  // ===== Ex-Employee Wall (历史员工墙) =====
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
    listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">加载中...</div>';

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
          listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">暂无离职员工</div>';
          return;
        }
        this._renderExEmployees(list);
      })
      .catch(err => {
        listEl.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;">加载失败: ${err.message}</div>`;
      });
  }

  _renderExEmployees(exEmps) {
    const listEl = document.getElementById('ex-employee-list');
    if (exEmps.length === 0) {
      listEl.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">暂无离职员工</div>';
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
        <button class="pixel-btn small rehire-btn" data-id="${emp.id}">🔄 重新录用</button>
      `;
      card.querySelector('.rehire-btn').addEventListener('click', () => this.rehireEmployee(emp));
      listEl.appendChild(card);
    }
  }

  rehireEmployee(emp) {
    if (!confirm(`确认重新录用 ${emp.name}${emp.nickname ? '(' + emp.nickname + ')' : ''}？\n将从 Lv.1 重新开始。`)) return;

    fetch(`/api/ex-employees/${encodeURIComponent(emp.id)}/rehire`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `重新录用失败: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `🔄 已重新录用: ${data.name}`, 'ceo');
          this.loadExEmployees(); // Refresh the list
        }
      })
      .catch(err => this.logEntry('SYSTEM', `错误: ${err.message}`, 'system'));
  }

  // ===== Operations Dashboard (运营情况板) =====
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
      content.innerHTML = '<div style="color:var(--text-dim);font-size:7px;">暂无数据</div>';
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
      const d = e.department || '未分配';
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
        <div class="dash-title">人员总览</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num">${employees.length}</span><span class="dash-label">在职</span></div>
          <div class="dash-stat"><span class="dash-num">${exEmployees.length}</span><span class="dash-label">离职</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-green);">${workingCount}</span><span class="dash-label">工作中</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-gray);">${idleCount}</span><span class="dash-label">空闲</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-cyan);">${meetingCount}</span><span class="dash-label">会议中</span></div>
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">设备 & 会议室</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num">${tools.length}</span><span class="dash-label">工具</span></div>
          <div class="dash-stat"><span class="dash-num">${rooms.length}</span><span class="dash-label">会议室</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-green);">${freeRooms}</span><span class="dash-label">空闲</span></div>
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">任务状态</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num">${tasks.filter(t => t.status === 'running').length}</span><span class="dash-label">进行中</span></div>
          <div class="dash-stat"><span class="dash-num">${tasks.filter(t => t.status === 'queued').length}</span><span class="dash-label">排队中</span></div>
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">部门分布</div>
        <div class="dash-dept-list">
          ${Object.entries(depts).map(([d, c]) => `<div class="dash-dept-item"><span>${d}</span><span>${c}人</span></div>`).join('')}
        </div>
      </div>
      <div class="dash-section">
        <div class="dash-title">绩效分布</div>
        <div class="dash-stats">
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-green);">${perf375}</span><span class="dash-label">3.75 优秀</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-yellow);">${perf350}</span><span class="dash-label">3.5 合格</span></div>
          <div class="dash-stat"><span class="dash-num" style="color:var(--pixel-red);">${perf325}</span><span class="dash-label">3.25 待改进</span></div>
        </div>
      </div>
    `;
  }

  // ===== Culture Wall (公司文化墙) =====
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
      list.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:12px;">暂无文化条目，CEO可在上方添加。</div>';
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
            <button class="culture-wall-delete-btn" data-index="${idx}" title="删除">✕</button>
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
          this.logEntry('SYSTEM', `添加失败: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `文化墙新增: ${content.slice(0, 40)}`, 'ceo');
          input.value = '';
          // State will be refreshed via WebSocket push
        }
      })
      .catch(err => this.logEntry('SYSTEM', `错误: ${err.message}`, 'system'))
      .finally(() => { btn.disabled = false; });
  }

  removeCultureItem(index) {
    fetch(`/api/culture-wall/${index}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          this.logEntry('SYSTEM', `删除失败: ${data.error}`, 'system');
        } else {
          this.logEntry('CEO', `文化墙移除: ${data.removed?.content?.slice(0, 40) || ''}`, 'ceo');
          // State will be refreshed via WebSocket push
        }
      })
      .catch(err => this.logEntry('SYSTEM', `错误: ${err.message}`, 'system'));
  }

  _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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
        this.logEntry('CEO', `任务已分配给 ${data.routed_to}`, 'ceo');
      })
      .catch(err => {
        this.logEntry('SYSTEM', `提交失败: ${err.message}`, 'system');
      })
      .finally(() => {
        setTimeout(() => { submitBtn.disabled = false; }, 2000);
      });

    input.value = '';
  }
}

// Boot
window.app = new AppController();
