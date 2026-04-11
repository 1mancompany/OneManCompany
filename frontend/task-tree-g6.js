// frontend/task-tree-g6.js
// Task tree visualization with G6 (AntV) — org-chart style cards, top-down layout

/* ──────────────────────────── Constants ──────────────────────────── */

const DEPT_COLORS = {
    CEO:      { badge: '#8b5cf6', bar: '#8b5cf6', text: '#fff' },
    EA:       { badge: '#3b82f6', bar: '#3b82f6', text: '#fff' },
    HR:       { badge: '#10b981', bar: '#10b981', text: '#fff' },
    COO:      { badge: '#f59e0b', bar: '#f59e0b', text: '#000' },
    CSO:      { badge: '#ef4444', bar: '#ef4444', text: '#fff' },
    Engineer: { badge: '#06b6d4', bar: '#06b6d4', text: '#000' },
    Cron:     { badge: '#6366f1', bar: '#6366f1', text: '#fff' },
    Default:  { badge: '#6b7280', bar: '#6b7280', text: '#fff' },
};

const STATUS_STYLES = {
    pending:    { color: '#f59e0b', label: '\u25cc Pending' },
    processing: { color: '#5e6ad2', label: '\u27f3 Running' },
    completed:  { color: '#10b981', label: '\u2713 Done' },
    accepted:   { color: '#10b981', label: '\u2713 Accepted' },
    finished:   { color: '#10b981', label: '\u2713 Finished' },
    failed:     { color: '#ef4444', label: '\u2717 Failed' },
    cancelled:  { color: '#62666d', label: '\u2014 Cancelled' },
    holding:    { color: '#8b5cf6', label: '\u23f8 Holding' },
    blocked:    { color: '#f97316', label: '\u2298 Blocked' },
};

const CARD_W = 240;
const CARD_MIN_H = 72;       // minimum card height (no description)
const DESC_LINE_H = 14;      // height per description line
const DESC_MAX_LINES = 6;    // cap description lines
const DESC_CHARS_PER_LINE = 34;
const CHILDREN_PAGE_SIZE = 5;

/* ─────────────────── Word-wrap helper (shared with old code) ────── */

function _wrapText(desc, maxChars, maxLines) {
    if (!desc) return [''];
    const words = desc.replace(/\n/g, ' ').split(/\s+/);
    const lines = [];
    let cur = '';
    for (const w of words) {
        const trial = cur ? cur + ' ' + w : w;
        if (trial.length <= maxChars) {
            cur = trial;
        } else {
            if (cur) lines.push(cur);
            cur = w.length > maxChars ? w.substring(0, maxChars) : w;
        }
        if (lines.length === maxLines) { cur = ''; break; }
    }
    if (cur && lines.length < maxLines) lines.push(cur);
    if (lines.length === maxLines && words.join(' ').length > lines.join(' ').length) {
        lines[maxLines - 1] = lines[maxLines - 1].substring(0, maxChars - 1) + '\u2026';
    }
    if (lines.length === 0) lines.push('');
    return lines;
}

/* ──────────────── Register custom G6 node: org-card ─────────────── */

G6.registerNode('org-card', {
    draw(cfg, group) {
        const w = CARD_W;
        const dept = cfg.dept || 'Default';
        const deptColor = DEPT_COLORS[dept] || DEPT_COLORS.Default;
        const statusStyle = STATUS_STYLES[cfg.status] || STATUS_STYLES.pending;
        const hasChildren = cfg.children && cfg.children.length > 0;
        const collapsed = cfg.collapsed;

        // --- Compute dynamic height based on description ---
        const descLines = cfg._isShowMore ? [] : _wrapText(cfg.desc || '', DESC_CHARS_PER_LINE, DESC_MAX_LINES);
        const descAreaH = descLines.length * DESC_LINE_H;
        const headerH = 48; // name row (16) + subtitle row (14) + divider gap (18)
        const h = Math.max(CARD_MIN_H, headerH + descAreaH + 10);

        // Store computed height for layout
        cfg._computedH = h;

        // --- Shadow ---
        group.addShape('rect', {
            attrs: {
                x: 2, y: 2, width: w, height: h,
                radius: 8, fill: 'rgba(0,0,0,0.3)',
                shadowColor: 'rgba(0,0,0,0.3)',
                shadowBlur: 6,
            },
            name: 'shadow',
        });

        // --- Card body ---
        const cardBody = group.addShape('rect', {
            attrs: {
                x: 0, y: 0, width: w, height: h,
                radius: 8, fill: '#191a1b',
                stroke: 'rgba(255,255,255,0.08)', lineWidth: 1,
                cursor: 'pointer',
            },
            name: 'card-body',
        });

        // --- Left status bar (department color) ---
        group.addShape('rect', {
            attrs: {
                x: 0, y: 0, width: 4, height: h,
                radius: [8, 0, 0, 8],
                fill: deptColor.bar,
            },
            name: 'status-bar',
        });

        // --- Right-side avatar ---
        const avatarX = w - 28;
        const avatarY = 20;
        const avatarR = 14;
        group.addShape('circle', {
            attrs: {
                x: avatarX, y: avatarY, r: avatarR,
                fill: deptColor.badge, opacity: 0.9,
            },
            name: 'avatar-bg',
        });
        if (cfg.avatarUrl) {
            group.addShape('image', {
                attrs: {
                    x: avatarX - avatarR, y: avatarY - avatarR,
                    width: avatarR * 2, height: avatarR * 2,
                    img: cfg.avatarUrl,
                },
                name: 'avatar-img',
            });
            group.addShape('circle', {
                attrs: {
                    x: avatarX, y: avatarY, r: avatarR,
                    fill: 'transparent', stroke: deptColor.badge, lineWidth: 2,
                },
                name: 'avatar-border',
            });
        } else {
            group.addShape('text', {
                attrs: {
                    x: avatarX, y: avatarY,
                    text: cfg.avatar || '?',
                    fontSize: 11, fontWeight: 'bold',
                    fill: deptColor.text,
                    textAlign: 'center', textBaseline: 'middle',
                },
                name: 'avatar-text',
            });
        }

        // --- "Show more" virtual node: simplified card ---
        if (cfg._isShowMore) {
            group.addShape('text', {
                attrs: {
                    x: w / 2, y: h / 2 - 6,
                    text: cfg.name || '+N more',
                    fontSize: 13, fontWeight: 'bold',
                    fill: '#8a8f98',
                    textAlign: 'center', textBaseline: 'middle',
                    cursor: 'pointer',
                },
                name: 'more-text',
            });
            group.addShape('text', {
                attrs: {
                    x: w / 2, y: h / 2 + 10,
                    text: 'Click to expand',
                    fontSize: 9, fill: '#62666d',
                    textAlign: 'center', textBaseline: 'middle',
                    cursor: 'pointer',
                },
                name: 'more-hint',
            });
            return cardBody;
        }

        // --- Row 1: Name + Status indicator (no overlap) ---
        const maxNameW = w - 80; // room for avatar + status
        group.addShape('text', {
            attrs: {
                x: 14, y: 14,
                text: _truncate(cfg.name || '', 14),
                fontSize: 12, fontWeight: 'bold',
                fill: '#f7f8f8',
                textAlign: 'left', textBaseline: 'middle',
                cursor: 'pointer',
            },
            name: 'name-text',
        });

        // Status dot + label (right-aligned, before avatar)
        const statusX = w - 72;
        group.addShape('circle', {
            attrs: {
                x: statusX, y: 14,
                r: 3, fill: statusStyle.color,
            },
            name: 'status-dot',
        });
        group.addShape('text', {
            attrs: {
                x: statusX + 6, y: 14,
                text: _truncate(statusStyle.label, 8),
                fontSize: 8, fill: statusStyle.color,
                textAlign: 'left', textBaseline: 'middle',
            },
            name: 'status-label',
        });

        // --- Row 2: Dept badge + Role (subtitle) ---
        const badgeText = dept;
        const badgeW = badgeText.length * 6 + 10;
        group.addShape('rect', {
            attrs: {
                x: 14, y: 24,
                width: badgeW, height: 14,
                radius: 6,
                fill: deptColor.badge, opacity: 0.2,
            },
            name: 'dept-badge-bg',
        });
        group.addShape('text', {
            attrs: {
                x: 14 + badgeW / 2, y: 31,
                text: badgeText,
                fontSize: 8, fontWeight: 'bold',
                fill: deptColor.badge,
                textAlign: 'center', textBaseline: 'middle',
            },
            name: 'dept-badge-text',
        });

        // Role text (right of badge)
        if (cfg.role) {
            group.addShape('text', {
                attrs: {
                    x: 14 + badgeW + 6, y: 31,
                    text: _truncate(cfg.role, 20),
                    fontSize: 9, fill: '#62666d',
                    textAlign: 'left', textBaseline: 'middle',
                },
                name: 'role-text',
            });
        }

        // --- Divider line ---
        group.addShape('line', {
            attrs: {
                x1: 10, y1: 42, x2: w - 10, y2: 42,
                stroke: 'rgba(255,255,255,0.05)', lineWidth: 1,
            },
            name: 'divider',
        });

        // --- Description area (dynamic lines) ---
        descLines.forEach((line, i) => {
            group.addShape('text', {
                attrs: {
                    x: 14, y: 54 + i * DESC_LINE_H,
                    text: line,
                    fontSize: 10, fill: '#8a8f98',
                    textAlign: 'left', textBaseline: 'middle',
                },
                name: `desc-line-${i}`,
            });
        });

        // --- Collapse/expand button (if has children) ---
        if (hasChildren) {
            const btnY = h + 6;
            group.addShape('circle', {
                attrs: {
                    x: w / 2, y: btnY, r: 8,
                    fill: 'rgba(255,255,255,0.03)', stroke: 'rgba(255,255,255,0.08)', lineWidth: 1,
                    cursor: 'pointer',
                },
                name: 'collapse-btn',
            });
            group.addShape('text', {
                attrs: {
                    x: w / 2, y: btnY,
                    text: collapsed ? '+' : '\u2212',
                    fontSize: 12, fontWeight: 'bold',
                    fill: '#8a8f98',
                    textAlign: 'center', textBaseline: 'middle',
                    cursor: 'pointer',
                },
                name: 'collapse-icon',
            });
        }

        return cardBody;
    },

    getAnchorPoints() {
        return [
            [0.5, 0],   // top center
            [0.5, 1],   // bottom center
        ];
    },
}, 'single-node');

/* ──────────────────────── Utility helpers ────────────────────────── */

function _truncate(str, max) {
    return str.length > max ? str.substring(0, max - 1) + '\u2026' : str;
}

/* ──────────────────── TaskTreeRenderer class ─────────────────────── */

class TaskTreeRenderer {
    constructor(containerId, detailId) {
        this.containerId = containerId;
        this.detailId = detailId;
        this.graph = null;
        this.treeData = null;          // raw API response {nodes, root_id}
        this.g6TreeData = null;        // transformed nested tree for G6
        this.selectedNodeId = null;
        this._currentProjectId = null;
        this._resizeObserver = null;
    }

    /* ── Public API: load ─────────────────────────────────────────── */

    async load(projectId) {
        this._currentProjectId = projectId;
        const resp = await fetch(`/api/projects/${encodeURIComponent(projectId)}/tree`);
        if (!resp.ok) return;
        this.treeData = await resp.json();
        requestAnimationFrame(() => this.render());
    }

    /* ── Public API: render ───────────────────────────────────────── */

    render() {
        if (!this.treeData || !this.treeData.nodes || !this.treeData.nodes.length) return;

        const container = document.getElementById(this.containerId);
        if (!container) return;

        let { width, height } = container.getBoundingClientRect();
        if (width < 10) width = container.parentElement?.clientWidth || 800;
        if (height < 10) height = container.parentElement?.clientHeight || 500;

        // Build nested tree data
        this.g6TreeData = this._flatNodesToTree(this.treeData.nodes, this.treeData.root_id);
        if (!this.g6TreeData) return;

        // Destroy previous graph if exists
        if (this.graph) {
            this.graph.destroy();
            this.graph = null;
        }

        // Clear container
        container.innerHTML = '';

        this.graph = new G6.TreeGraph({
            container: this.containerId,
            width: width,
            height: height,
            fitView: true,
            fitViewPadding: [40, 40, 40, 40],
            animate: true,
            animateCfg: { duration: 300, easing: 'easeCubic' },
            modes: {
                default: [
                    'drag-canvas',
                    { type: 'zoom-canvas', sensitivity: 1.5 },
                    'drag-node',
                ],
            },
            defaultNode: {
                type: 'org-card',
                size: [CARD_W, CARD_MIN_H],
            },
            defaultEdge: {
                type: 'polyline',
                style: {
                    stroke: 'rgba(255,255,255,0.05)',
                    lineWidth: 1.5,
                    radius: 10,
                    endArrow: false,
                    offset: 30,
                },
            },
            layout: {
                type: 'compactBox',
                direction: 'TB',
                getId: (d) => d.id,
                getWidth: () => CARD_W,
                getHeight: (d) => {
                    // Dynamic height: compute from description lines
                    const lines = _wrapText(d.desc || '', DESC_CHARS_PER_LINE, DESC_MAX_LINES);
                    const headerH = 48;
                    const h = Math.max(CARD_MIN_H, headerH + lines.length * DESC_LINE_H + 10);
                    return h + 16; // extra space for collapse button
                },
                getVGap: () => 40,
                getHGap: () => 30,
            },
        });

        this.graph.data(this.g6TreeData);
        this.graph.render();
        this._colorEdges();
        this._renderDependencyEdges();
        this._bindEvents();
    }

    /* ── Data transform: flat nodes → nested tree ─────────────────── */

    _flatNodesToTree(nodes, rootId) {
        const map = {};
        nodes.forEach(n => {
            const info = n.employee_info || {};
            const dept = this._inferDept(info.role, n.node_type);
            map[n.id] = {
                id: n.id,
                name: info.nickname || info.name || n.employee_id || n.id,
                avatar: this._getAvatar(info, n.node_type),
                avatarUrl: info.avatar_url || '',
                dept: dept,
                role: info.role || '',
                desc: n.description_preview || n.title || n.description || '',
                status: n.status || 'pending',
                _raw: n,
                children: [],
            };
        });

        nodes.forEach(n => {
            if (n.parent_id && map[n.parent_id]) {
                map[n.parent_id].children.push(map[n.id]);
            }
        });

        // Paginate: if a node has > PAGE_SIZE children, show first PAGE_SIZE
        // + a "show more" virtual node. Click to expand next batch.
        const PAGE_SIZE = CHILDREN_PAGE_SIZE;
        this._paginateChildren(map[rootId], PAGE_SIZE);

        return map[rootId] || null;
    }

    _paginateChildren(node, pageSize) {
        if (!node || !node.children) return;
        // Recurse first so nested nodes are paginated too
        node.children.forEach(c => this._paginateChildren(c, pageSize));

        if (node.children.length > pageSize) {
            const visible = node.children.slice(0, pageSize);
            const hidden = node.children.slice(pageSize);
            const moreNode = {
                id: `_more_${node.id}`,
                name: `+${hidden.length} more`,
                avatar: '···',
                avatarUrl: '',
                dept: 'Default',
                role: '',
                desc: `Click to show ${hidden.length} more tasks`,
                status: 'pending',
                _isShowMore: true,
                _parentId: node.id,
                _hiddenChildren: hidden,
                children: [],
            };
            visible.push(moreNode);
            node.children = visible;
        }
    }

    /* ── Infer department from role/nodeType ───────────────────────── */

    _inferDept(role, nodeType) {
        if (!role && !nodeType) return 'Default';

        // CEO-type nodes
        if (nodeType === 'ceo_prompt' || nodeType === 'ceo_followup' || nodeType === 'ceo_request') {
            return 'CEO';
        }

        const r = (role || '').toLowerCase();
        if (r.includes('ceo') || r.includes('chief executive')) return 'CEO';
        if (r.includes('ea') || r.includes('executive assistant') || r.includes('assistant')) return 'EA';
        if (r.includes('hr') || r.includes('human resource') || r.includes('\u4eba\u529b')) return 'HR';
        if (r.includes('coo') || r.includes('chief operating') || r.includes('\u8fd0\u8425')) return 'COO';
        if (r.includes('cso') || r.includes('chief security') || r.includes('\u5b89\u5168')) return 'CSO';
        if (r.includes('engineer') || r.includes('developer') || r.includes('dev') ||
            r.includes('\u5de5\u7a0b') || r.includes('\u6280\u672f') || r.includes('\u7814\u53d1')) return 'Engineer';
        if (r.includes('cron') || r.includes('scheduler') || r.includes('\u5b9a\u65f6')) return 'Cron';

        if (nodeType === 'system' || nodeType === 'cron') return 'Cron';

        return 'Default';
    }

    /* ── Avatar: emoji or initials ────────────────────────────────── */

    _getAvatar(empInfo, nodeType) {
        if (nodeType === 'ceo_prompt' || nodeType === 'ceo_followup' || nodeType === 'ceo_request') {
            return '\ud83d\udc51';  // crown emoji
        }
        const name = empInfo?.nickname || empInfo?.name || '';
        if (!name) return '?';
        // Return first 2 chars as initials
        return name.slice(0, 2);
    }

    /* ── Color edges by parent department, dashed for processing ──── */

    _colorEdges() {
        if (!this.graph) return;
        const edges = this.graph.getEdges();
        edges.forEach(edge => {
            const sourceNode = edge.getSource();
            const sourceModel = sourceNode.getModel();
            const targetNode = edge.getTarget();
            const targetModel = targetNode.getModel();
            const dept = sourceModel.dept || 'Default';
            const deptColor = DEPT_COLORS[dept] || DEPT_COLORS.Default;
            const isProcessing = targetModel.status === 'processing';

            this.graph.updateItem(edge, {
                style: {
                    stroke: isProcessing ? '#5e6ad2' : 'rgba(255,255,255,0.05)',
                    lineWidth: isProcessing ? 2 : 1.5,
                    lineDash: isProcessing ? [6, 3] : null,
                    opacity: targetModel.status === 'cancelled' ? 0.2 : 1,
                },
            });
        });
    }

    /* ── Render dependency edges (dashed orange arrows) ─────────── */

    _renderDependencyEdges() {
        if (!this.treeData || !this.graph) return;
        this.treeData.nodes.forEach(node => {
            (node.depends_on || []).forEach(depId => {
                const sourceNode = this.graph.findById(depId);
                const targetNode = this.graph.findById(node.id);
                if (sourceNode && targetNode) {
                    this.graph.addItem('edge', {
                        source: depId,
                        target: node.id,
                        type: 'line',
                        style: {
                            stroke: '#f97316',
                            lineWidth: 1.5,
                            lineDash: [6, 4],
                            opacity: 0.6,
                            endArrow: {
                                path: G6.Arrow.triangle(6, 8, 0),
                                fill: '#f97316',
                            },
                        },
                    });
                }
            });
        });
    }

    /* ── Bind graph events ────────────────────────────────────────── */

    _bindEvents() {
        if (!this.graph) return;

        // Click node → selectNode
        this.graph.on('node:click', (evt) => {
            const model = evt.item.getModel();
            // Check if clicked the collapse button
            const shapeName = evt.target?.get('name');
            if (shapeName === 'collapse-btn' || shapeName === 'collapse-icon') {
                this._toggleCollapse(evt.item);
                return;
            }
            // "Show more" pagination node — expand next batch
            if (model._isShowMore) {
                this._expandMoreChildren(model);
                return;
            }
            if (model._raw) {
                this.selectNode(model._raw);
            }
        });

        // Hover glow (subtle Linear-style)
        this.graph.on('node:mouseenter', (evt) => {
            const body = evt.item.get('group').find(s => s.get('name') === 'card-body');
            if (body) {
                body.attr('stroke', 'rgba(255,255,255,0.15)');
                body.attr('shadowColor', 'rgba(255,255,255,0.06)');
                body.attr('shadowBlur', 8);
            }
            this.graph.paint();
        });

        this.graph.on('node:mouseleave', (evt) => {
            const body = evt.item.get('group').find(s => s.get('name') === 'card-body');
            if (body) {
                body.attr('stroke', 'rgba(255,255,255,0.08)');
                body.attr('shadowColor', null);
                body.attr('shadowBlur', 0);
            }
            this.graph.paint();
        });

        // ResizeObserver for responsive — disconnect old one first to prevent leaks
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        const container = document.getElementById(this.containerId);
        if (container && typeof ResizeObserver !== 'undefined') {
            this._resizeObserver = new ResizeObserver(() => {
                if (!this.graph || this.graph.get('destroyed')) return;
                const { width, height } = container.getBoundingClientRect();
                if (width > 10 && height > 10) {
                    this.graph.changeSize(width, height);
                    this.graph.fitView(40);
                }
            });
            this._resizeObserver.observe(container);
        }
    }

    /* ── Toggle collapse/expand ────────────────────────────────────── */

    _toggleCollapse(item) {
        const model = item.getModel();
        model.collapsed = !model.collapsed;
        this.graph.layout();
        this.graph.fitView();
        // Update collapse icon
        const icon = item.get('group').find(s => s.get('name') === 'collapse-icon');
        if (icon) {
            icon.attr('text', model.collapsed ? '+' : '\u2212');
        }
        this._colorEdges();
        this.graph.paint();
    }

    /* ── Pagination: expand "show more" node ──────────────────────── */

    _expandMoreChildren(moreModel) {
        // Find the parent in the G6 tree and expand next batch
        const PAGE_SIZE = CHILDREN_PAGE_SIZE;
        const parentItem = this.graph.findById(moreModel._parentId);
        if (!parentItem) return;

        const parentModel = parentItem.getModel();
        const hidden = moreModel._hiddenChildren || [];
        if (!hidden.length) return;

        // Remove the "show more" node
        const moreItem = this.graph.findById(moreModel.id);
        if (moreItem) this.graph.removeChild(moreModel.id);

        // Add next batch
        const nextBatch = hidden.slice(0, PAGE_SIZE);
        const remaining = hidden.slice(PAGE_SIZE);

        nextBatch.forEach(child => {
            this.graph.addChild(child, parentModel.id);
        });

        // If still more hidden, add a new "show more" node
        if (remaining.length > 0) {
            const newMore = {
                id: `_more_${moreModel._parentId}_${Date.now()}`,
                name: `+${remaining.length} more`,
                avatar: '···',
                avatarUrl: '',
                dept: 'Default',
                role: '',
                desc: `Click to show ${remaining.length} more tasks`,
                status: 'pending',
                _isShowMore: true,
                _parentId: moreModel._parentId,
                _hiddenChildren: remaining,
                children: [],
            };
            this.graph.addChild(newMore, parentModel.id);
        }

        this.graph.layout();
        this.graph.fitView();
        this._colorEdges();
    }

    /* ── Public API: updateNode ────────────────────────────────────── */

    updateNode(nodeId, data) {
        if (!this.treeData) return;
        const node = this.treeData.nodes.find(n => n.id === nodeId);
        if (node) {
            Object.assign(node, data);
            if (this.graph && !this.graph.destroyed) {
                const g6Node = this.graph.findById(nodeId);
                if (g6Node) {
                    // Update the model data that the custom node draw() reads
                    const emp = data.employee_info || node.employee_info || {};
                    const role = emp.role || node.node_type || 'Task';
                    this.graph.updateItem(g6Node, {
                        status: data.status || node.status,
                        desc: data.title || data.description_preview || node.description_preview || '',
                        name: emp.name || emp.nickname || '',
                        role: role,
                        _raw: node,
                    });
                    this._colorEdges();
                    if (this.selectedNodeId === nodeId) this.selectNode(node);
                    return;
                }
            }
            // Fallback
            this.render();
            if (this.selectedNodeId === nodeId) this.selectNode(node);
        }
    }

    /* ── Public API: addNode ───────────────────────────────────────── */

    addNode(parentId, nodeData) {
        if (!this.treeData) return;
        this.treeData.nodes.push(nodeData);
        this.render();
    }

    /* ── Public API: selectNode ──────────────────────────────────── */

    selectNode(nodeData) {
        this.selectedNodeId = nodeData.id;
        const drawer = document.getElementById(this.detailId);
        const content = document.getElementById('tree-detail-content');
        if (!drawer || !content) return;
        drawer.classList.remove('hidden');

        // Highlight selected node in G6
        if (this.graph) {
            this.graph.getNodes().forEach(n => {
                const model = n.getModel();
                const body = n.get('group').find(s => s.get('name') === 'card-body');
                if (body) {
                    body.attr('lineWidth', model.id === nodeData.id ? 3 : 1.5);
                }
            });
            this.graph.paint();
        }

        content.innerHTML = this._renderNodeDetail(nodeData);

        const logToggles = content.querySelectorAll('.detail-log-toggle');
        logToggles.forEach(el => {
            el.addEventListener('click', async () => {
                const nodeId = el.dataset.nodeId;
                const logContent = document.getElementById(`node-log-${nodeId}`);
                if (!logContent) return;
                if (logContent.classList.contains('hidden')) {
                    logContent.classList.remove('hidden');
                    el.innerHTML = '&#9660; Execution Log';
                    // Use xterm.js for terminal rendering
                    if (typeof XTermLog !== 'undefined') {
                        logContent.innerHTML = '';
                        const xterm = new XTermLog(logContent, { fontSize: 11 });
                        const projectDir = nodeData.project_dir || '';
                        const qs = projectDir ? `?project_dir=${encodeURIComponent(projectDir)}&tail=200` : '?tail=200';
                        fetch(`/api/node/${nodeId}/logs${qs}`)
                            .then(r => r.json())
                            .then(data => { xterm.renderLogs(data.logs || []); })
                            .catch(() => { xterm.writeln(`${ANSI.red}Failed to load logs${ANSI.reset}`); });
                        logContent._xterm = xterm;
                    } else {
                        logContent.innerHTML = '<div style="color:#666;padding:8px">Terminal not loaded</div>';
                    }
                } else {
                    logContent.classList.add('hidden');
                    el.innerHTML = '&#9654; Execution Log';
                    if (logContent._xterm) { logContent._xterm.dispose(); logContent._xterm = null; }
                }
            });
        });
    }

    /* ── Detail drawer: node detail HTML ─────────────────────────── */

    _renderNodeDetail(node) {
        const statusColor = (STATUS_STYLES[node.status] || STATUS_STYLES.pending).color;

        const criteria = (node.acceptance_criteria || [])
            .map(c => `<li>${this._escapeHtml(c)}</li>`).join('');
        const acceptance = node.acceptance_result
            ? `<div class="detail-section">
                 <h4>Acceptance</h4>
                 <span class="${node.acceptance_result.passed ? 'status-pass' : 'status-fail'}">
                   ${node.acceptance_result.passed ? 'PASSED' : 'FAILED'}
                 </span>
                 <p>${this._escapeHtml(node.acceptance_result.notes || '')}</p>
               </div>`
            : '';

        const info = node.employee_info || {};
        const isCeo = node.node_type === 'ceo_prompt' || node.node_type === 'ceo_followup' || node.node_type === 'ceo_request';
        const displayName = isCeo ? 'CEO' : (info.nickname || info.name || node.employee_id);
        const nodeTypeLabel = node.node_type === 'ceo_prompt' ? 'Original Prompt'
            : node.node_type === 'ceo_followup' ? 'Follow-up'
            : node.node_type === 'ceo_request' ? 'CEO Request' : '';
        const avatarHtml = info.avatar_url
            ? `<img src="${this._escapeHtml(info.avatar_url)}" class="tree-detail-avatar" />`
            : `<div class="tree-detail-avatar${isCeo ? ' tree-detail-avatar-ceo' : ''}">${isCeo ? 'CEO' : this._escapeHtml((node.employee_id || '').slice(-2))}</div>`;

        return `
            <div class="tree-detail-header">
                ${avatarHtml}
                <div>
                    <h3>${this._escapeHtml(displayName)}</h3>
                    <div class="tree-detail-role">${nodeTypeLabel ? this._escapeHtml(nodeTypeLabel) : (this._escapeHtml(info.role || '') + ' \u00b7 ' + this._escapeHtml(node.employee_id))}</div>
                    <span class="tree-detail-status" style="color:${statusColor}">${this._escapeHtml(node.status)}</span>
                </div>
            </div>

            <div class="detail-section">
                <h4>Prompt</h4>
                <pre class="detail-prompt">${this._escapeHtml(node.description || '(none)')}</pre>
            </div>

            ${criteria ? `<div class="detail-section"><h4>Acceptance Criteria</h4><ul>${criteria}</ul></div>` : ''}

            <div class="detail-section">
                <h4>Result</h4>
                <pre class="detail-result">${this._escapeHtml(node.result || '(pending)')}</pre>
            </div>

            ${acceptance}

            ${this._renderDependencies(node)}

            <div class="detail-section detail-meta">
                <span>Tokens: ${node.input_tokens || 0} in / ${node.output_tokens || 0} out</span>
                <span>Cost: $${(node.cost_usd || 0).toFixed(4)}</span>
                <span>Timeout: ${node.timeout_seconds || 3600}s</span>
            </div>

            <div class="detail-section">
                <h4 class="detail-log-toggle" data-node-id="${node.id}" style="cursor:pointer;user-select:none">
                    &#9654; Execution Log
                </h4>
                <div class="detail-log-content hidden" id="node-log-${node.id}"></div>
            </div>
        `;
    }

    /* ── Detail drawer: dependencies section ─────────────────────── */

    _renderDependencies(node) {
        let html = '';

        // Prerequisites
        html += '<div class="detail-section"><h4>Prerequisites</h4>';
        if (node.depends_on && node.depends_on.length > 0) {
            html += '<ul>';
            node.depends_on.forEach(depId => {
                const depNode = this.treeData?.nodes?.find(n => n.id === depId);
                if (depNode) {
                    const sc = (STATUS_STYLES[depNode.status] || STATUS_STYLES.pending).color;
                    const desc = depNode.description ? this._escapeHtml(depNode.description.slice(0, 60)) + '...' : '';
                    html += `<li><span class="node-log-type" style="color:${sc}">\u25cf</span> ${this._escapeHtml(depNode.employee_info?.name || depId)}: ${desc} [${this._escapeHtml(depNode.status)}]</li>`;
                }
            });
            html += '</ul>';
        } else {
            html += '<p class="node-log-empty">None</p>';
        }
        html += '</div>';

        // Downstream tasks
        const allNodes = this.treeData?.nodes || [];
        const dependents = allNodes.filter(n =>
            (n.depends_on || []).includes(node.id) ||
            n.parent_id === node.id
        );
        html += '<div class="detail-section"><h4>Downstream Tasks</h4>';
        if (dependents.length > 0) {
            html += '<ul>';
            dependents.forEach(dep => {
                const sc = (STATUS_STYLES[dep.status] || STATUS_STYLES.pending).color;
                const desc = dep.description ? this._escapeHtml(dep.description.slice(0, 60)) + '...' : '';
                html += `<li><span class="node-log-type" style="color:${sc}">\u25cf</span> ${this._escapeHtml(dep.employee_info?.name || dep.id)}: ${desc} [${this._escapeHtml(dep.status)}]</li>`;
            });
            html += '</ul>';
        } else {
            html += '<p class="node-log-empty">None</p>';
        }
        html += '</div>';

        return html;
    }

    /* ── HTML escape helper ──────────────────────────────────────── */

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    /* ── Public API: destroy ──────────────────────────────────────── */

    destroy() {
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        if (this.graph) {
            this.graph.destroy();
            this.graph = null;
        }
    }
}

window.TaskTreeRenderer = TaskTreeRenderer;
