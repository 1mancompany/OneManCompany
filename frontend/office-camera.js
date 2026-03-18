/**
 * office-camera.js — Pan + zoom camera for the office tilemap.
 *
 * World space:  pixel coordinates in the tilemap (0,0 = top-left tile corner)
 * Screen space: CSS pixel coordinates relative to the canvas element
 *
 * Usage:
 *   const cam = new Camera(canvas, mapW, mapH);
 *   // per-frame:
 *   cam.update();
 *   cam.applyTransform(ctx);
 *   // ... draw world-space content ...
 *   cam.resetTransform(ctx);
 *   // coordinate conversion:
 *   const world = cam.screenToWorld(e.clientX - rect.left, e.clientY - rect.top);
 */

const CAMERA_ZOOM_MAX  = 3.0;
const CAMERA_ZOOM_STEP = 0.08;
const CAMERA_LERP      = 0.12;  // fraction per frame toward target

class Camera {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {number} mapPixelW  — total map width in pixels
   * @param {number} mapPixelH  — total map height in pixels
   */
  constructor(canvas, mapPixelW, mapPixelH) {
    this.canvas    = canvas;
    this.mapPixelW = mapPixelW;
    this.mapPixelH = mapPixelH;
    this._minZoom  = 0.5;  // updated by _updateMinZoom()

    // Current state (lerped toward target)
    this.x    = 0;
    this.y    = 0;
    this.zoom = 1.0;

    // Target state
    this._tx   = 0;
    this._ty   = 0;
    this._tz   = 1.0;

    // Drag tracking
    this._dragging      = false;
    this._didDrag       = false;   // true if drag threshold was exceeded
    this._dragStartX    = 0;
    this._dragStartY    = 0;
    this._dragCamStartX = 0;
    this._dragCamStartY = 0;

    this._bindEvents();
    this._updateMinZoom();
  }

  _bindEvents() {
    const c = this.canvas;
    c.addEventListener('mousedown',  e => this._onDown(e));
    c.addEventListener('mousemove',  e => this._onMove(e));
    c.addEventListener('mouseup',    ()  => this._onUp());
    c.addEventListener('mouseleave', ()  => this._onUp());
    c.addEventListener('wheel',      e => this._onWheel(e), { passive: false });
  }

  _onDown(e) {
    this._dragging      = true;
    this._didDrag       = false;
    this._dragStartX    = e.clientX;
    this._dragStartY    = e.clientY;
    this._dragCamStartX = this._tx;
    this._dragCamStartY = this._ty;
    this.canvas.style.cursor = 'grabbing';
  }

  _onMove(e) {
    if (!this._dragging) return;
    const dx = e.clientX - this._dragStartX;
    const dy = e.clientY - this._dragStartY;
    // 4-pixel threshold before treating as a pan (prevents click being a pan)
    if (!this._didDrag && Math.hypot(dx, dy) < 4) return;
    this._didDrag = true;
    this._tx = this._dragCamStartX - dx / this._tz;
    this._ty = this._dragCamStartY - dy / this._tz;
    this._clamp(this.canvas.getBoundingClientRect());
  }

  _onUp() {
    this._dragging = false;
    this.canvas.style.cursor = 'default';
  }

  /** Minimum zoom = fit entire office (with border) in viewport. Cached on resize. */
  _updateMinZoom() {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) { this._minZoom = 0.5; return; }
    const margin = TILE_SIZE;
    const totalW = this.mapPixelW + margin * 2;
    const totalH = this.mapPixelH + margin * 2;
    this._minZoom = Math.min(rect.width / totalW, rect.height / totalH);
  }

  _onWheel(e) {
    e.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width) return;

    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;

    // World position under cursor before zoom
    const wx = this._tx + sx / this._tz;
    const wy = this._ty + sy / this._tz;

    this._tz = Math.max(this._minZoom,
               Math.min(CAMERA_ZOOM_MAX, this._tz + (e.deltaY < 0 ? CAMERA_ZOOM_STEP : -CAMERA_ZOOM_STEP)));

    // Adjust so cursor stays fixed in world space
    this._tx = wx - sx / this._tz;
    this._ty = wy - sy / this._tz;
    this._clamp(rect);
  }

  _clamp(rect) {
    if (!rect) rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const margin = TILE_SIZE;
    const vw = rect.width  / this._tz;
    const vh = rect.height / this._tz;
    const totalW = this.mapPixelW + margin * 2;
    const totalH = this.mapPixelH + margin * 2;

    if (vw >= totalW) {
      // Viewport wider than map: center horizontally
      this._tx = -margin + (totalW - vw) / 2;
    } else {
      this._tx = Math.max(-margin, Math.min(this.mapPixelW + margin - vw, this._tx));
    }

    if (vh >= totalH) {
      // Viewport taller than map: center vertically
      this._ty = -margin + (totalH - vh) / 2;
    } else {
      this._ty = Math.max(-margin, Math.min(this.mapPixelH + margin - vh, this._ty));
    }
  }

  /**
   * Returns true if the last mousedown→mouseup was a pan (drag threshold exceeded).
   * OfficeRenderer._onClick checks this to avoid firing on pan release.
   */
  wasDrag() {
    return this._didDrag;
  }

  /**
   * Smoothly move camera to center on world-pixel position (wx, wy).
   * @param {number} wx
   * @param {number} wy
   * @param {number} [targetZoom]
   */
  centerOn(wx, wy, targetZoom = null) {
    const rect = this.canvas.getBoundingClientRect();
    if (targetZoom !== null) {
      this._tz = Math.max(this._minZoom, Math.min(CAMERA_ZOOM_MAX, targetZoom));
    }
    const vw = (rect.width  || 640) / this._tz;
    const vh = (rect.height || 480) / this._tz;
    this._tx = wx - vw / 2;
    this._ty = wy - vh / 2;
    this._clamp();
  }

  /** Center on a tile grid position (col, row). Used by click-to-focus (Task 9). */
  centerOnTile(col, row, targetZoom = null) {
    this.centerOn((col + 0.5) * TILE_SIZE, (row + 0.5) * TILE_SIZE, targetZoom);
  }

  /** Call once per frame to lerp toward target. */
  update() {
    this.x    += (this._tx - this.x)    * CAMERA_LERP;
    this.y    += (this._ty - this.y)    * CAMERA_LERP;
    this.zoom += (this._tz - this.zoom) * CAMERA_LERP;
  }

  /** Apply camera transform to ctx. Call before drawing world-space content. */
  applyTransform(ctx) {
    ctx.save();
    ctx.scale(this.zoom, this.zoom);
    ctx.translate(-Math.round(this.x), -Math.round(this.y));
    ctx.imageSmoothingEnabled = false;
  }

  /** Restore ctx after drawing world-space content. */
  resetTransform(ctx) {
    ctx.restore();
  }

  /**
   * Convert screen coords (CSS px relative to canvas) to world pixels.
   * @param {number} sx
   * @param {number} sy
   * @returns {{x: number, y: number}}
   */
  screenToWorld(sx, sy) {
    return {
      x: this.x + sx / this.zoom,
      y: this.y + sy / this.zoom,
    };
  }

  /**
   * Convert world pixels to screen coords. Used for attaching UI overlays to world entities.
   */
  worldToScreen(wx, wy) {
    return {
      x: (wx - this.x) * this.zoom,
      y: (wy - this.y) * this.zoom,
    };
  }

  /**
   * Get the range of tile columns and rows currently visible (with 1-tile margin).
   * @param {number} mapCols
   * @param {number} mapRows
   * @returns {{minCol, maxCol, minRow, maxRow}}
   */
  getVisibleTiles(mapCols, mapRows) {
    const rect = this.canvas.getBoundingClientRect();
    const tl = this.screenToWorld(0, 0);
    const br = this.screenToWorld(rect.width || 640, rect.height || 480);
    return {
      minCol: Math.max(0,        Math.floor(tl.x / TILE_SIZE) - 1),
      maxCol: Math.min(mapCols - 1, Math.ceil(br.x / TILE_SIZE) + 1),
      minRow: Math.max(0,        Math.floor(tl.y / TILE_SIZE) - 1),
      maxRow: Math.min(mapRows - 1, Math.ceil(br.y / TILE_SIZE) + 1),
    };
  }

  /** Call when canvas or map dimensions change. */
  resize(mapPixelW, mapPixelH) {
    this.mapPixelW = mapPixelW;
    this.mapPixelH = mapPixelH;
    this._updateMinZoom();
    if (this._tz < this._minZoom) this._tz = this._minZoom;
    this._clamp();
  }

  /** Returns true when lerp has converged — used to skip redraws when camera is at rest. */
  isSettled() {
    return (
      Math.abs(this.x - this._tx) < 0.5 &&
      Math.abs(this.y - this._ty) < 0.5 &&
      Math.abs(this.zoom - this._tz) < 0.005
    );
  }
}
