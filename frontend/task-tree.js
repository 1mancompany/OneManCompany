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

        this.nodeWidth = 180;
        this.nodeHeight = 80;
        this.levelSep = 120;
        this.sibSep = 40;
    }

    static STATUS_COLORS = {
        pending: '#666',
        processing: '#4a9eff',
        completed: '#ffaa00',
        accepted: '#00ff88',
        failed: '#ff4444',
        cancelled: '#888',
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

        this.g.selectAll('.tree-link')
            .data(root.links())
            .enter()
            .append('path')
            .attr('class', 'tree-link')
            .attr('d', d3.linkVertical()
                .x(d => d.x)
                .y(d => d.y));

        const nodeGroups = this.g.selectAll('.tree-node')
            .data(root.descendants())
            .enter()
            .append('g')
            .attr('class', 'tree-node')
            .attr('transform', d => `translate(${d.x}, ${d.y})`)
            .style('cursor', 'pointer')
            .on('click', (event, d) => this.selectNode(d.data));

        nodeGroups.append('rect')
            .attr('x', -this.nodeWidth / 2)
            .attr('y', -this.nodeHeight / 2)
            .attr('width', this.nodeWidth)
            .attr('height', this.nodeHeight)
            .attr('rx', 6)
            .attr('class', 'tree-node-card');

        nodeGroups.append('rect')
            .attr('x', -this.nodeWidth / 2)
            .attr('y', -this.nodeHeight / 2)
            .attr('width', 4)
            .attr('height', this.nodeHeight)
            .attr('rx', 2)
            .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666');

        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 20)
            .attr('class', 'tree-node-name')
            .text(d => {
                const info = d.data.employee_info || {};
                return info.nickname || info.name || d.data.employee_id;
            });

        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 40)
            .attr('class', 'tree-node-desc')
            .text(d => {
                const desc = d.data.description || '';
                return desc.substring(0, 25) + (desc.length > 25 ? '...' : '');
            });

        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 58)
            .attr('class', 'tree-node-status')
            .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666')
            .text(d => d.data.status);
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
        const displayName = info.nickname || info.name || node.employee_id;
        const avatarHtml = info.avatar_url
            ? `<img src="${this._escapeHtml(info.avatar_url)}" class="tree-detail-avatar" />`
            : `<div class="tree-detail-avatar">${this._escapeHtml((node.employee_id || '').slice(-2))}</div>`;

        return `
            <div class="tree-detail-header">
                ${avatarHtml}
                <div>
                    <h3>${this._escapeHtml(displayName)}</h3>
                    <div class="tree-detail-role">${this._escapeHtml(info.role || '')} · ${this._escapeHtml(node.employee_id)}</div>
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
