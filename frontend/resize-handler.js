/**
 * resize-handler.js — Draggable resize handles for the grid layout.
 *
 * Attaches invisible drag zones at panel edges and updates
 * CSS grid-template-columns / grid-template-rows on drag.
 *
 * Persists sizes to localStorage so they survive page reload.
 */
class GridResizer {
  constructor() {
    this._app = document.getElementById('app');
    this._dragging = null;
    this._startX = 0;
    this._startY = 0;
    this._startSize = 0;

    // Load saved sizes from localStorage (or use CSS defaults)
    this._leftWidth = parseInt(localStorage.getItem('grid-left-width')) || 240;
    this._rosterWidth = parseInt(localStorage.getItem('grid-roster-width')) || 280;
    this._topRatio = parseFloat(localStorage.getItem('grid-top-ratio')) || 0.5;

    // Edge detection threshold in px
    this._edgePx = 6;

    this._applyGrid();
    this._attachHandlers();

    document.addEventListener('mousemove', (e) => this._onMouseMove(e));
    document.addEventListener('mouseup', () => this._onMouseUp());
    window.addEventListener('resize', () => this._applyGrid());
  }

  /** Apply current sizes to the grid */
  _applyGrid() {
    this._app.style.gridTemplateColumns =
      `${this._leftWidth}px 1fr ${this._rosterWidth}px`;

    const appH = this._app.getBoundingClientRect().height - 8; // minus padding
    const topH = Math.round(appH * this._topRatio);
    const botH = appH - topH;
    this._app.style.gridTemplateRows = `${topH}px ${botH}px`;
  }

  /** Attach edge-detect listeners to relevant panels */
  _attachHandlers() {
    // --- Left column right edge ---
    for (const id of ['left-top-panel', 'left-bottom-panel']) {
      const el = document.getElementById(id);
      if (!el) continue;
      this._addEdgeListener(el, (e, rect) => rect.right - e.clientX < this._edgePx, 'col-resize', 'left-width');
    }

    // --- Roster left edge ---
    const roster = document.getElementById('roster-panel');
    if (roster) {
      this._addEdgeListener(roster, (e, rect) => e.clientX - rect.left < this._edgePx, 'col-resize', 'roster-width');
    }

    // --- Row split: bottom edge of top-row panels ---
    for (const id of ['left-top-panel', 'office-panel', 'roster-panel']) {
      const el = document.getElementById(id);
      if (!el) continue;
      this._addEdgeListener(el, (e, rect) => rect.bottom - e.clientY < this._edgePx, 'row-resize', 'row-split');
    }

    // --- Row split: top edge of bottom-row panels ---
    for (const id of ['left-bottom-panel', 'console-panel']) {
      const el = document.getElementById(id);
      if (!el) continue;
      this._addEdgeListener(el, (e, rect) => e.clientY - rect.top < this._edgePx, 'row-resize', 'row-split');
    }
  }

  /**
   * Generic helper: attach mousemove (cursor hint) + mousedown (start drag)
   * to an element's edge region.
   */
  _addEdgeListener(el, hitTest, cursorStyle, dragType) {
    // Track all edge detectors per element for cursor arbitration
    if (!el._resizeEdges) {
      el._resizeEdges = [];
      el.addEventListener('mousemove', (e) => {
        if (this._dragging) return;
        const rect = el.getBoundingClientRect();
        let matched = null;
        for (const edge of el._resizeEdges) {
          if (edge.hitTest(e, rect)) { matched = edge; break; }
        }
        el.style.cursor = matched ? matched.cursorStyle : '';
      });
    }
    el._resizeEdges.push({ hitTest, cursorStyle, dragType });

    el.addEventListener('mousedown', (e) => {
      const rect = el.getBoundingClientRect();
      if (hitTest(e, rect)) {
        this._startDrag(e, dragType);
      }
    });
  }

  _startDrag(e, type) {
    e.preventDefault();
    this._dragging = type;
    this._startX = e.clientX;
    this._startY = e.clientY;
    if (type === 'left-width') this._startSize = this._leftWidth;
    else if (type === 'roster-width') this._startSize = this._rosterWidth;
    else if (type === 'row-split') this._startSize = this._topRatio;
    document.body.style.cursor = type === 'row-split' ? 'row-resize' : 'col-resize';
    document.body.style.userSelect = 'none';
    // CSS class prevents canvas/iframes from stealing pointer events
    document.body.classList.add('resize-dragging');
  }

  _onMouseMove(e) {
    if (!this._dragging) return;

    const dx = e.clientX - this._startX;
    const dy = e.clientY - this._startY;
    const appRect = this._app.getBoundingClientRect();
    const maxColW = appRect.width * 0.4;

    if (this._dragging === 'left-width') {
      this._leftWidth = Math.max(120, Math.min(this._startSize + dx, maxColW));
    } else if (this._dragging === 'roster-width') {
      this._rosterWidth = Math.max(120, Math.min(this._startSize - dx, maxColW));
    } else if (this._dragging === 'row-split') {
      const appH = appRect.height - 8; // minus padding
      const newTop = (this._startSize * appH + dy) / appH;
      this._topRatio = Math.max(0.15, Math.min(newTop, 0.85));
    }

    this._applyGrid();
  }

  _onMouseUp() {
    if (!this._dragging) return;
    this._dragging = null;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    document.body.classList.remove('resize-dragging');

    // Persist to localStorage
    localStorage.setItem('grid-left-width', this._leftWidth);
    localStorage.setItem('grid-roster-width', this._rosterWidth);
    localStorage.setItem('grid-top-ratio', this._topRatio);
  }

  /** Reset to default sizes */
  reset() {
    this._leftWidth = 240;
    this._rosterWidth = 280;
    this._topRatio = 0.5;
    localStorage.removeItem('grid-left-width');
    localStorage.removeItem('grid-roster-width');
    localStorage.removeItem('grid-top-ratio');
    this._applyGrid();
  }
}
