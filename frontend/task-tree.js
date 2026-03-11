// frontend/task-tree.js
// Task tree visualization with D3.js — top-down family tree layout

class TaskTreeRenderer {
    constructor(containerId, detailId) {
        this.containerId = containerId;
        this.detailId = detailId;
        this.svg = null;
        this.g = null;
        this.zoom = null;
        this.treeData = null;
        this.selectedNodeId = null;

        this.nodeWidth = 220;
        this.nodeHeight = 90;
        this.levelSep = 100;
        this.sibSep = 30;
    }

    static STATUS_COLORS = {
        pending: '#666',
        processing: '#4a9eff',
        completed: '#ffaa00',
        accepted: '#00ff88',
        failed: '#ff4444',
        cancelled: '#888',
        blocked: '#ff4444',
    };

    static STATUS_LABELS = {
        pending: 'PENDING',
        processing: 'RUNNING',
        completed: 'DONE',
        accepted: 'ACCEPTED',
        failed: 'FAILED',
        cancelled: 'CANCELLED',
        holding: 'HOLDING',
        blocked: 'BLOCKED',
    };

    async load(projectId) {
        const resp = await fetch(`/api/projects/${encodeURIComponent(projectId)}/tree`);
        if (!resp.ok) return;
        this.treeData = await resp.json();
        // Defer render to next frame so container has layout dimensions
        requestAnimationFrame(() => this.render());
    }

    render() {
        if (!this.treeData || !this.treeData.nodes.length) return;

        const container = document.getElementById(this.containerId);
        if (!container) return;
        const svgEl = container.querySelector('svg');
        let { width, height } = container.getBoundingClientRect();
        // Fallback if container hasn't laid out yet
        if (width < 10) width = container.parentElement?.clientWidth || 800;
        if (height < 10) height = container.parentElement?.clientHeight || 500;

        d3.select(svgEl).selectAll('*').remove();

        this.svg = d3.select(svgEl)
            .attr('width', '100%')
            .attr('height', '100%')
            .attr('viewBox', `0 0 ${width} ${height}`);

        this.zoom = d3.zoom()
            .scaleExtent([0.3, 3])
            .on('zoom', (event) => {
                this.g.attr('transform', event.transform);
            });
        this.svg.call(this.zoom);

        // Arrow marker for dependency lines
        let defs = this.svg.select('defs');
        if (defs.empty()) defs = this.svg.append('defs');
        defs.selectAll('#dep-arrow').remove();
        defs.append('marker')
            .attr('id', 'dep-arrow')
            .attr('viewBox', '0 0 10 10')
            .attr('refX', 10).attr('refY', 5)
            .attr('markerWidth', 6).attr('markerHeight', 6)
            .attr('orient', 'auto')
            .append('path')
            .attr('d', 'M 0 0 L 10 5 L 0 10 z')
            .attr('fill', '#888');

        this.g = this.svg.append('g')
            .attr('transform', `translate(${width / 2}, 40)`);

        const nodesMap = {};
        this.treeData.nodes.forEach(n => { nodesMap[n.id] = { ...n, children: [] }; });
        this.treeData.nodes.forEach(n => {
            if (n.parent_id && nodesMap[n.parent_id]) {
                nodesMap[n.parent_id].children.push(nodesMap[n.id]);
            }
        });

        const rootData = nodesMap[this.treeData.root_id];
        if (!rootData) return;

        const root = d3.hierarchy(rootData);
        const treeLayout = d3.tree()
            .nodeSize([this.nodeWidth + this.sibSep, this.nodeHeight + this.levelSep]);
        treeLayout(root);

        // Connection lines — colored by child status, dashed for inactive branch
        this.g.selectAll('.tree-link')
            .data(root.links())
            .enter()
            .append('path')
            .attr('class', d => {
                const status = d.target.data.status;
                const active = d.target.data.branch_active !== false;
                return `tree-link tree-link-${status}${active ? '' : ' tree-link-inactive'}`;
            })
            .attr('d', d3.linkVertical()
                .x(d => d.x)
                .y(d => d.y))
            .attr('stroke-width', d => d.target.data.branch_active !== false ? 2.5 : 1);

        // --- Dependency arrows (dashed) ---
        const nodesData = root.descendants();
        const depLinks = [];
        nodesData.forEach(n => {
            (n.data.depends_on || []).forEach(depId => {
                const depNode = nodesData.find(d => d.data.id === depId);
                if (depNode) {
                    depLinks.push({ source: depNode, target: n, status: depNode.data.status });
                }
            });
        });

        this.g.selectAll('.dep-link')
            .data(depLinks)
            .enter()
            .append('line')
            .attr('class', 'dep-link')
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y)
            .attr('stroke', d => {
                if (d.status === 'accepted') return '#00ff88';
                if (d.status === 'failed' || d.status === 'cancelled') return '#ff4444';
                return '#666';
            })
            .attr('stroke-width', 1.5)
            .attr('stroke-dasharray', '6,3')
            .attr('marker-end', 'url(#dep-arrow)');

        const nodeGroups = this.g.selectAll('.tree-node')
            .data(root.descendants())
            .enter()
            .append('g')
            .attr('class', 'tree-node')
            .attr('transform', d => `translate(${d.x}, ${d.y})`)
            .style('cursor', 'pointer')
            .on('click', (event, d) => this.selectNode(d.data));

        // Card background
        nodeGroups.append('rect')
            .attr('x', -this.nodeWidth / 2)
            .attr('y', -this.nodeHeight / 2)
            .attr('width', this.nodeWidth)
            .attr('height', this.nodeHeight)
            .attr('rx', 8)
            .attr('class', d => {
                const active = d.data.branch_active !== false;
                const isCeo = d.data.node_type === 'ceo_prompt' || d.data.node_type === 'ceo_followup';
                return `tree-node-card${active ? '' : ' tree-node-inactive'}${isCeo ? ' tree-node-ceo' : ''}`;
            });

        // Status color bar (left edge) — gold for CEO nodes
        nodeGroups.append('rect')
            .attr('x', -this.nodeWidth / 2)
            .attr('y', -this.nodeHeight / 2)
            .attr('width', 4)
            .attr('height', this.nodeHeight)
            .attr('rx', 2)
            .attr('fill', d => {
                const isCeo = d.data.node_type === 'ceo_prompt' || d.data.node_type === 'ceo_followup';
                return isCeo ? '#ffd700' : (TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666');
            });

        // Avatar circle with initials fallback
        nodeGroups.each(function(d) {
            const g = d3.select(this);
            const info = d.data.employee_info || {};
            const cx = -(220 / 2) + 24;
            const cy = -8;

            if (info.avatar_url) {
                const clipId = `clip-${d.data.id}`;
                g.append('clipPath').attr('id', clipId)
                    .append('circle').attr('cx', cx).attr('cy', cy).attr('r', 14);
                g.append('image')
                    .attr('href', info.avatar_url)
                    .attr('x', cx - 14).attr('y', cy - 14)
                    .attr('width', 28).attr('height', 28)
                    .attr('clip-path', `url(#${clipId})`);
            } else {
                g.append('circle')
                    .attr('cx', cx).attr('cy', cy).attr('r', 14)
                    .attr('class', 'tree-avatar-fallback');
                g.append('text')
                    .attr('x', cx).attr('y', cy + 4)
                    .attr('text-anchor', 'middle')
                    .attr('class', 'tree-avatar-text')
                    .text((info.nickname || info.name || d.data.employee_id || '').slice(0, 2));
            }
        });

        // Name + role
        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 46)
            .attr('y', -this.nodeHeight / 2 + 22)
            .attr('class', 'tree-node-name')
            .text(d => {
                const info = d.data.employee_info || {};
                return info.nickname || info.name || d.data.employee_id;
            });

        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 46)
            .attr('y', -this.nodeHeight / 2 + 36)
            .attr('class', 'tree-node-role')
            .text(d => {
                const info = d.data.employee_info || {};
                return info.role || '';
            });

        // Description
        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 56)
            .attr('class', 'tree-node-desc')
            .text(d => {
                const desc = d.data.description || '';
                return desc.substring(0, 30) + (desc.length > 30 ? '...' : '');
            });

        // Status pill (rounded rect + text)
        const pills = nodeGroups.append('g')
            .attr('transform', d => `translate(${this.nodeWidth / 2 - 60}, ${this.nodeHeight / 2 - 18})`);

        pills.append('rect')
            .attr('width', 52)
            .attr('height', 14)
            .attr('rx', 7)
            .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666')
            .attr('opacity', 0.2);

        pills.append('text')
            .attr('x', 26)
            .attr('y', 10)
            .attr('text-anchor', 'middle')
            .attr('class', 'tree-node-pill-text')
            .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666')
            .text(d => TaskTreeRenderer.STATUS_LABELS[d.data.status] || d.data.status);

        // Branch label for inactive nodes
        nodeGroups.filter(d => d.data.branch_active === false)
            .append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 72)
            .attr('class', 'tree-branch-label')
            .text(d => `Branch ${d.data.branch}`);

        // Dependency status labels
        nodeGroups.filter(d => d.data.dependency_status === 'waiting')
            .append('text')
            .attr('class', 'tree-dep-label')
            .attr('y', this.nodeHeight / 2 + 14)
            .attr('text-anchor', 'middle')
            .attr('fill', '#aaa')
            .attr('font-size', '9px')
            .text(d => {
                const depIds = d.data.depends_on || [];
                const names = depIds.map(id => {
                    const dn = nodesData.find(n => n.data.id === id);
                    return dn ? (dn.data.employee_info?.name || 'Unknown') : '?';
                });
                return 'Waiting: ' + names.join(', ');
            });

        nodeGroups.filter(d => d.data.dependency_status === 'blocked')
            .append('text')
            .attr('class', 'tree-dep-label blocked')
            .attr('y', this.nodeHeight / 2 + 14)
            .attr('text-anchor', 'middle')
            .attr('fill', '#ff4444')
            .attr('font-size', '9px')
            .text('Blocked');

        // Animate nodes in
        nodeGroups
            .attr('opacity', 0)
            .transition()
            .duration(300)
            .delay((d, i) => i * 50)
            .attr('opacity', 1);
    }

    selectNode(nodeData) {
        this.selectedNodeId = nodeData.id;
        const drawer = document.getElementById(this.detailId);
        const content = document.getElementById('tree-detail-content');
        drawer.classList.remove('hidden');

        this.g.selectAll('.tree-node-card')
            .classed('selected', d => d.data.id === nodeData.id);

        content.innerHTML = this._renderNodeDetail(nodeData);
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    _renderDependencies(node) {
        let html = '';

        // 前置任务
        html += '<div class="detail-section"><h4>前置任务</h4>';
        if (node.depends_on && node.depends_on.length > 0) {
            html += '<ul>';
            node.depends_on.forEach(depId => {
                const depNode = this.treeData?.nodes?.find(n => n.id === depId);
                if (depNode) {
                    const statusColor = TaskTreeRenderer.STATUS_COLORS[depNode.status] || '#666';
                    const desc = depNode.description ? this._escapeHtml(depNode.description.slice(0, 60)) + '...' : '';
                    html += `<li><span style="color:${statusColor}">\u25cf</span> ${this._escapeHtml(depNode.employee_info?.name || depId)}: ${desc} [${this._escapeHtml(depNode.status)}]</li>`;
                }
            });
            html += '</ul>';
        } else {
            html += '<p style="color:#888;margin:4px 0">无</p>';
        }
        html += '</div>';

        // 后续任务
        const allNodes = this.treeData?.nodes || [];
        const dependents = allNodes.filter(n => (n.depends_on || []).includes(node.id));
        html += '<div class="detail-section"><h4>后续任务</h4>';
        if (dependents.length > 0) {
            html += '<ul>';
            dependents.forEach(dep => {
                const statusColor = TaskTreeRenderer.STATUS_COLORS[dep.status] || '#666';
                const desc = dep.description ? this._escapeHtml(dep.description.slice(0, 60)) + '...' : '';
                html += `<li><span style="color:${statusColor}">\u25cf</span> ${this._escapeHtml(dep.employee_info?.name || dep.id)}: ${desc} [${this._escapeHtml(dep.status)}]</li>`;
            });
            html += '</ul>';
        } else {
            html += '<p style="color:#888;margin:4px 0">无</p>';
        }
        html += '</div>';

        return html;
    }

    _renderNodeDetail(node) {
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
        const isCeo = node.node_type === 'ceo_prompt' || node.node_type === 'ceo_followup';
        const displayName = isCeo ? 'CEO' : (info.nickname || info.name || node.employee_id);
        const nodeTypeLabel = node.node_type === 'ceo_prompt' ? 'Original Prompt'
            : node.node_type === 'ceo_followup' ? 'Follow-up' : '';
        const avatarHtml = info.avatar_url
            ? `<img src="${this._escapeHtml(info.avatar_url)}" class="tree-detail-avatar" />`
            : `<div class="tree-detail-avatar${isCeo ? ' tree-detail-avatar-ceo' : ''}">${isCeo ? 'CEO' : this._escapeHtml((node.employee_id || '').slice(-2))}</div>`;

        return `
            <div class="tree-detail-header">
                ${avatarHtml}
                <div>
                    <h3>${this._escapeHtml(displayName)}</h3>
                    <div class="tree-detail-role">${nodeTypeLabel ? this._escapeHtml(nodeTypeLabel) : (this._escapeHtml(info.role || '') + ' · ' + this._escapeHtml(node.employee_id))}</div>
                    <span class="tree-detail-status" style="color:${TaskTreeRenderer.STATUS_COLORS[node.status] || '#666'}">${this._escapeHtml(node.status)}</span>
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
        `;
    }

    updateNode(nodeId, data) {
        if (!this.treeData) return;
        const node = this.treeData.nodes.find(n => n.id === nodeId);
        if (node) {
            Object.assign(node, data);
            this.render();
            if (this.selectedNodeId === nodeId) {
                this.selectNode(node);
            }
        }
    }

    addNode(parentId, nodeData) {
        if (!this.treeData) return;
        this.treeData.nodes.push(nodeData);
        this.render();
    }
}

window.TaskTreeRenderer = TaskTreeRenderer;
