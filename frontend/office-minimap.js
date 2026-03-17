/**
 * office-minimap.js — 120×80px viewport overview overlay.
 *
 * Rendered in screen space (bottom-right corner of canvas).
 * Shows the full office map at reduced scale with a white rectangle
 * indicating the current camera viewport. Click to jump camera.
 */

const MINIMAP_W      = 120;
const MINIMAP_H      = 80;
const MINIMAP_MARGIN = 8;

class MiniMap {
  /**
   * @param {OfficeMap} officeMap
   * @param {Camera}    camera
   */
  constructor(officeMap, camera) {
    this.map    = officeMap;
    this.camera = camera;
    this._canvas = null;
    this._clickBound = this._onClick.bind(this);
  }

  /** Attach click listener to the main canvas. */
  attach(canvas) {
    this._canvas = canvas;
    canvas.addEventListener('click', this._clickBound);
  }

  detach() {
    if (this._canvas) {
      this._canvas.removeEventListener('click', this._clickBound);
      this._canvas = null;
    }
  }

  /**
   * Draw minimap onto ctx in screen space (call after camera.resetTransform).
   * @param {CanvasRenderingContext2D} ctx
   * @param {number} canvasW — CSS pixel width of canvas
   * @param {number} canvasH — CSS pixel height of canvas
   */
  draw(ctx, canvasW, canvasH) {
    const ox = canvasW - MINIMAP_W - MINIMAP_MARGIN;
    const oy = canvasH - MINIMAP_H - MINIMAP_MARGIN;

    // Background
    ctx.fillStyle = 'rgba(0,0,0,0.65)';
    ctx.fillRect(ox, oy, MINIMAP_W, MINIMAP_H);
    ctx.strokeStyle = '#555';
    ctx.lineWidth = 1;
    ctx.strokeRect(ox + 0.5, oy + 0.5, MINIMAP_W, MINIMAP_H);

    // Scale: map pixels → minimap pixels
    const mapW = this.map.cols * TILE_SIZE;
    const mapH = this.map.rows * TILE_SIZE;
    const sx   = MINIMAP_W / mapW;
    const sy   = MINIMAP_H / mapH;

    // Department zone floors
    for (const zone of this.map.zones) {
      const zx = zone.start_col * TILE_SIZE * sx;
      const zw = (zone.end_col - zone.start_col) * TILE_SIZE * sx;
      ctx.fillStyle = zone.floor1 || '#2a2a3a';
      ctx.fillRect(ox + zx, oy, zw, MINIMAP_H);
    }

    // Divider columns (dark green strip)
    ctx.fillStyle = '#1a3a1a';
    for (const divCol of this.map.dividerCols) {
      const dx = divCol * TILE_SIZE * sx;
      ctx.fillRect(ox + dx, oy, Math.max(1, TILE_SIZE * sx), MINIMAP_H);
    }

    // Regular employee dots (green)
    ctx.fillStyle = '#44ff88';
    for (const emp of this.map.employees) {
      ctx.fillRect(ox + (emp.col + 0.5) * TILE_SIZE * sx - 1,
                   oy + (emp.row + 0.5) * TILE_SIZE * sy - 1, 2, 2);
    }

    // Executive dots (gold)
    ctx.fillStyle = '#ffd700';
    for (const exec of this.map.executives) {
      ctx.fillRect(ox + (exec.col + 0.5) * TILE_SIZE * sx - 1,
                   oy + (exec.row + 0.5) * TILE_SIZE * sy - 1, 2, 2);
    }

    // CEO dot (larger gold)
    if (this.map.ceo) {
      const ceo = this.map.ceo;
      ctx.fillStyle = '#ffd700';
      ctx.fillRect(ox + (ceo.col + 0.5) * TILE_SIZE * sx - 2,
                   oy + (ceo.row + 0.5) * TILE_SIZE * sy - 2, 4, 4);
    }

    // Meeting room rectangles (blue)
    ctx.fillStyle = '#4488ff';
    for (const room of this.map.meetingRooms) {
      ctx.fillRect(
        ox + room.col * TILE_SIZE * sx,
        oy + room.row * TILE_SIZE * sy,
        Math.max(2, 2 * TILE_SIZE * sx),
        Math.max(2, 2 * TILE_SIZE * sy),
      );
    }

    // Viewport rectangle
    const cam  = this.camera;
    const rect = this._canvas ? this._canvas.getBoundingClientRect() : { width: 640, height: 480 };
    const vx   = cam.x  * sx;
    const vy   = cam.y  * sy;
    const vw   = Math.min(MINIMAP_W, (rect.width  / cam.zoom) * sx);
    const vh   = Math.min(MINIMAP_H, (rect.height / cam.zoom) * sy);

    ctx.fillStyle   = 'rgba(255,255,255,0.12)';
    ctx.fillRect(ox + vx, oy + vy, vw, vh);
    ctx.strokeStyle = 'rgba(255,255,255,0.8)';
    ctx.lineWidth   = 1;
    ctx.strokeRect(ox + vx + 0.5, oy + vy + 0.5, vw, vh);
  }

  /** Returns true if CSS pixel coords (sx, sy) are within the minimap area. */
  _inMinimap(sx, sy, canvasW, canvasH) {
    const ox = canvasW - MINIMAP_W - MINIMAP_MARGIN;
    const oy = canvasH - MINIMAP_H - MINIMAP_MARGIN;
    return sx >= ox && sx <= ox + MINIMAP_W && sy >= oy && sy <= oy + MINIMAP_H;
  }

  _onClick(e) {
    if (!this._canvas) return;
    const rect = this._canvas.getBoundingClientRect();
    const sx   = e.clientX - rect.left;
    const sy   = e.clientY - rect.top;
    if (!this._inMinimap(sx, sy, rect.width, rect.height)) return;

    // Map click position within minimap → world position
    const ox     = rect.width  - MINIMAP_W - MINIMAP_MARGIN;
    const oy     = rect.height - MINIMAP_H - MINIMAP_MARGIN;
    const relX   = (sx - ox) / MINIMAP_W;
    const relY   = (sy - oy) / MINIMAP_H;
    const worldX = relX * this.map.cols * TILE_SIZE;
    const worldY = relY * this.map.rows * TILE_SIZE;

    this.camera.centerOn(worldX, worldY);
    e.stopPropagation();  // don't fire OfficeRenderer._onClick
  }
}
