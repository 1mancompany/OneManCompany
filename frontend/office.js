/**
 * office.js — Tileset-based pixel art office renderer with pan/zoom camera.
 *
 * Depends on (must be loaded first in index.html):
 *   office-tileatlas.js  → tileAtlas singleton, TILE_SIZE constant
 *   office-camera.js     → Camera class
 *   office-map.js        → OfficeMap class, WALL_ROWS, MAP_COLS
 *   office-minimap.js    → MiniMap class
 */

const TILE = TILE_SIZE;   // 32 — alias kept for all existing drawing code
const COLS = MAP_COLS;    // 20
let ROWS = 18;            // updated from office_layout.canvas_rows

const PALETTE = {
  // Walls (deep indigo with subtle warmth)
  wallTop: '#161428',
  wallMid: '#1e1a34',
  wallBot: '#242040',
  // Tech (vivid cyan glow)
  screenOn: '#22ddff',
  led1: '#33ffaa',
  // People — still used by meeting room participant fallback
  skin: ['#f5cc8e', '#eab878', '#d09868', '#b07858', '#8c6048'],
  hair: ['#1a1a24', '#5c3010', '#dd9922', '#cc4444', '#7744aa', '#2255aa', '#884444', '#446644'],
  shirt: ['#4488ff', '#ff4466', '#44cc44', '#cc44cc', '#ff8844', '#44cccc', '#8866dd', '#dd8844'],
  // Special (slightly brighter, more saturated)
  ceoGold: '#ffd700',
  hrBlue: '#5599ff',
  cooOrange: '#ff9944',
  eaGreen: '#44ddaa',
  csoPurple: '#bb55ff',
  // Meeting Room
  meetingBooked: '#ff4455',
  meetingFree: '#00ff88',
  // Bulletin Board
  boardBg: '#6b4226',
  boardFrame: '#4a2e18',
  boardPin: '#ff4444',
  boardPaper: '#f0e8d0',
  boardPaperAlt: '#e8dcc0',
  // Project Wall
  projectBg: '#1a3a2a',
  projectFrame: '#0d2a1a',
  projectCard: '#d4e8d0',
  projectCardAlt: '#c0dcc0',
  projectPin: '#ffdd00',
};


class OfficeRenderer {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.ctx.imageSmoothingEnabled = false;

    this.state = { employees: [], tools: [], meeting_rooms: [], ceo_tasks: [], activity_log: [] };
    this.animFrame = 0;
    this.hoverTile = null;    // {x, y, screenX, screenY} in tile coords
    this.particles = [];
    this._avatarImages = {};
    this._toolIcons   = {};
    this.dpr = window.devicePixelRatio || 1;

    // ── New: tilemap, camera, minimap ──
    this.officeMap = new OfficeMap();
    this.camera    = new Camera(
      this.canvas,
      MAP_COLS * TILE,
      this.officeMap.rows * TILE,
    );
    this.minimap = new MiniMap(this.officeMap, this.camera);

    // Preload tileset sheets (character sheets loaded on-demand per employee)
    tileAtlas.preload(['room', 'office', 'interiors', 'interiors_room']);

    // Mouse / click events
    this.canvas.addEventListener('mousemove', e => this._onMouseMove(e));
    this.canvas.addEventListener('mouseleave', () => {
      this.hoverTile = null;
      const el = document.getElementById('tooltip');
      if (el) el.classList.add('hidden');
    });
    // Minimap click listener must be registered BEFORE the main click handler
    // so its stopPropagation() prevents _onClick from also firing on minimap clicks.
    this.minimap.attach(this.canvas);
    this.canvas.addEventListener('click', e => this._onClick(e));

    // Responsive sizing
    this._resizeCanvas();
    window.addEventListener('resize', () => this._resizeCanvas());
    new ResizeObserver(() => this._resizeCanvas()).observe(this.canvas.parentElement);

    // Center camera on exec area initially
    this.camera.centerOn(
      (MAP_COLS / 2) * TILE,
      WALL_ROWS * TILE,
      1.0,
    );

    this.loop();
  }

  // ── Canvas sizing ──────────────────────────────────────────────────────────

  _resizeCanvas() {
    const parent = this.canvas.parentElement;
    const cssW = parent.clientWidth;
    const cssH = parent.clientHeight - 45; // 45px = office panel header height
    if (cssW <= 0 || cssH <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    this.dpr = dpr;

    this.canvas.style.width  = cssW + 'px';
    this.canvas.style.height = cssH + 'px';
    this.canvas.width  = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);

    if (this.camera) {
      this.camera.resize(MAP_COLS * TILE, ROWS * TILE);
    }
  }

  // ── State update ───────────────────────────────────────────────────────────

  updateState(newState) {
    const oldEmpCount = (this.state.employees || []).length;
    this.state = { ...this.state, ...newState };

    if (this.state.office_layout) {
      const layout = this.state.office_layout;
      this.officeMap.rebuild(
        layout,
        this.state.employees    || [],
        this.state.meeting_rooms || [],
        this.state.tools         || [],
      );
      const newRows = this.officeMap.rows;
      if (newRows !== ROWS) {
        ROWS = newRows;
        this.camera.resize(MAP_COLS * TILE, ROWS * TILE);
      }
    }

    // Spawn particles on new hire
    const empList = this.state.employees || [];
    if (empList.length > oldEmpCount) {
      const latest = empList[empList.length - 1];
      const [gx, gy] = latest.desk_position || [0, 0];
      this._spawnParticles(
        gx * TILE + 16,
        (gy + WALL_ROWS) * TILE,
        PALETTE.led1,
        12,
      );
    }

    this._preloadToolIcons();
    this._preloadAvatars();
    // Preload character spritesheets for current employees
    this._preloadCharacterSheets();
  }

  // ── Preloaders ─────────────────────────────────────────────────────────────

  _preloadAvatars() {
    for (const emp of (this.state.employees || [])) {
      if (emp.id && !(emp.id in this._avatarImages)) {
        const img = new Image();
        img.src = `/api/employees/${emp.id}/avatar`;
        img.onload  = () => { this._avatarImages[emp.id] = img; };
        img.onerror = () => { this._avatarImages[emp.id] = null; };
        this._avatarImages[emp.id] = undefined; // loading sentinel
      }
    }
  }

  _preloadToolIcons() {
    for (const tool of (this.state.tools || [])) {
      if (tool.has_icon && !this._toolIcons[tool.id]) {
        const img = new Image();
        img.src = `/api/tools/${encodeURIComponent(tool.id)}/icon`;
        img.onload = () => { this._toolIcons[tool.id] = img; };
        this._toolIcons[tool.id] = null; // mark as loading
      }
    }
  }

  _preloadCharacterSheets() {
    const needed = new Set();
    for (const emp of (this.state.employees || [])) {
      const spriteNum = emp.avatar_sprite || ((this._hashStr(emp.id || 'default') % 20) + 1);
      needed.add(`char${String(spriteNum).padStart(2, '0')}`);
    }
    const toLoad = [...needed].filter(k => !tileAtlas.isReady(k));
    if (toLoad.length > 0) {
      tileAtlas.preload(toLoad);
    }
  }

  _getCharFrame(data) {
    const hash = this._hashStr(data.id || 'default');
    const spriteNum = data.avatar_sprite || ((hash % 20) + 1);
    const sheetKey = `char${String(spriteNum).padStart(2, '0')}`;

    if (data.current_task || data.status === 'working') {
      // Sit pose: row 4 (upper half) + row 5 (lower half)
      return { sheet: sheetKey, row: 4, col: 0, w: 1, h: 2 };
    }
    // Idle front: row 0, 3-frame animation
    const frame = Math.floor(this.animFrame * 0.02) % 3;
    return { sheet: sheetKey, row: 0, col: frame, w: 1, h: 2 };
  }

  // ── Click / hover ──────────────────────────────────────────────────────────

  _onMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const world = this.camera.screenToWorld(sx, sy);
    this.hoverTile = {
      x: Math.floor(world.x / TILE),
      y: Math.floor(world.y / TILE),
      screenX: e.clientX,
      screenY: e.clientY,
    };
  }

  _onClick(e) {
    // Ignore if this mousedown→mouseup was a pan drag
    if (this.camera.wasDrag()) return;

    const rect = this.canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const world = this.camera.screenToWorld(sx, sy);
    const tx = Math.floor(world.x / TILE);
    const ty = Math.floor(world.y / TILE);

    // Bulletin board tiles (5-7, rows 0-1)
    if (tx >= 5 && tx <= 7 && ty >= 0 && ty <= 1) {
      if (window.app?.openWorkflowPanel) window.app.openWorkflowPanel();
      return;
    }

    // Project wall tiles (12-14, rows 0-1)
    if (tx >= 12 && tx <= 14 && ty >= 0 && ty <= 1) {
      if (window.app?.openProjectWall) window.app.openProjectWall();
      return;
    }

    // Meeting rooms — 2×2 tile footprint offset by WALL_ROWS
    for (const room of (this.state.meeting_rooms || [])) {
      const [rx, ry] = room.position || [0, 0];
      if (tx >= rx && tx <= rx + 1 && ty >= ry + WALL_ROWS && ty <= ry + WALL_ROWS + 2) {
        if (window.app?.openMeetingRoom) window.app.openMeetingRoom(room);
        return;
      }
    }

    // Employees — sprite spans ~2 tiles above desk row
    for (const emp of (this.state.employees || [])) {
      const [ex, ey] = emp.desk_position || [0, 0];
      const canvasRow = ey + WALL_ROWS;
      if (tx === ex && (ty === canvasRow - 1 || ty === canvasRow || ty === canvasRow + 1)) {
        if (window.app?.openEmployeeDetail) window.app.openEmployeeDetail(emp);
        return;
      }
    }

    // Tools
    for (const tool of (this.state.tools || [])) {
      if (!tool.has_icon) continue;
      const [gx, gy] = tool.desk_position || [0, 0];
      const canvasRow = gy + WALL_ROWS;
      if (tx === gx && ty >= canvasRow && ty <= canvasRow + 1) {
        if (window.app?.openToolDetail) window.app.openToolDetail(tool.id);
        return;
      }
    }
  }

  // ── Particle system ────────────────────────────────────────────────────────

  _spawnParticles(x, y, color, count) {
    for (let i = 0; i < count; i++) {
      this.particles.push({
        x, y,
        vx: (Math.random() - 0.5) * 4,
        vy: -Math.random() * 3 - 1,
        life: 30 + Math.random() * 20,
        color,
        size: 2 + Math.random() * 3,
      });
    }
  }

  _updateParticles() {
    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.x  += p.vx;
      p.y  += p.vy;
      p.vy += 0.1;
      p.life--;
      if (p.life <= 0) this.particles.splice(i, 1);
    }
  }

  _drawParticles() {
    for (const p of this.particles) {
      this.ctx.globalAlpha = Math.min(1, p.life / 15);
      this.ctx.fillStyle = p.color;
      this.ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size, p.size);
    }
    this.ctx.globalAlpha = 1;
  }

  // ── Drawing primitives ─────────────────────────────────────────────────────

  _rect(x, y, w, h, color) {
    this.ctx.fillStyle = color;
    this.ctx.fillRect(x, y, w, h);
  }

  // ── Floor (tileset-only) ─────────────────────────────

  drawFloor() {
    const ctx  = this.ctx;
    const vis  = this.camera.getVisibleTiles(COLS, ROWS);

    for (let row = vis.minRow; row <= vis.maxRow; row++) {
      for (let col = vis.minCol; col <= vis.maxCol; col++) {
        const x = col * TILE;
        const y = row * TILE;
        const floorKey = this.officeMap.getFloor(col, row);

        // Draw tileset floor tile (silent no-op if not loaded)
        tileAtlas.drawDef(ctx, floorKey, x, y);

        // Divider plant overlay (fallback green strip if tiles not loaded)
        if (this.officeMap.isDivider(col, row)) {
          if (!tileAtlas.isReady('office')) {
            this._rect(x + 12, y, 8, TILE, '#2a5a30');
          }
          const overlay = this.officeMap.getOverlay(col, row);
          if (overlay) tileAtlas.drawDef(ctx, overlay, x, y);
        }
      }
    }

    // Ambient floor glow under screen areas
    ctx.globalAlpha = 0.04;
    for (const emp of (this.state.employees || [])) {
      if (emp.remote) continue;
      const [ex, ey] = emp.desk_position || [0, 0];
      ctx.fillStyle = PALETTE.screenOn;
      ctx.fillRect(ex * TILE - 8, (ey + WALL_ROWS) * TILE + 8, TILE + 16, TILE);
    }
    ctx.globalAlpha = 1;
  }

  // ── Walls ──────────────────────────────────────────────────────────────────

  drawWalls() {
    const ctx = this.ctx;
    const vis = this.camera.getVisibleTiles(COLS, ROWS);

    // Only draw wall tiles in rows 0-2 (WALL_ROWS = 3)
    for (let col = Math.max(0, vis.minCol); col <= Math.min(COLS - 1, vis.maxCol); col++) {
      const x = col * TILE;

      // Determine if this column has a window
      // Windows at every 4 columns, except where bulletin board (cols 5-7)
      // and project wall (cols 12-14) are
      const hasWindow = (col % 4 === 0) && !(col >= 4 && col <= 8) && !(col >= 11 && col <= 15);

      // Fallback: draw colored bands FIRST (tile draws on top, silent no-op if not loaded)
      if (!tileAtlas.isReady('room')) {
        this._rect(x, 0, TILE, 8, PALETTE.wallTop);
        this._rect(x, 8, TILE, 16, PALETTE.wallMid);
        this._rect(x, 24, TILE, 8, PALETTE.wallBot);
      }

      // Row 0: top wall
      tileAtlas.drawDef(ctx, hasWindow ? 'wall_window_top' : 'wall_top', x, 0);
      // Row 1: mid wall (window bottom or solid)
      tileAtlas.drawDef(ctx, hasWindow ? 'wall_window_bottom' : 'wall_mid', x, TILE);
      // Row 2: baseboard
      tileAtlas.drawDef(ctx, 'wall_bottom', x, TILE * 2);

      // Dynamic sky + stars in window panes (Canvas overlay on top of tile)
      if (hasWindow) {
        this._drawWindowAnimation(x + 8, 4);
      }
    }

    // Baseboard shadow line
    this._rect(0, 30, COLS * TILE, 2, '#2a2650');
  }

  _drawWindowAnimation(x, y) {
    const ctx = this.ctx;
    const glassW = (TILE - 18) / 2;

    // Dynamic sky color
    const timeOfDay = (Math.sin(this.animFrame * 0.005) + 1) / 2;
    const skyTop = `rgb(${30 + timeOfDay * 20}, ${50 + timeOfDay * 30}, ${130 + timeOfDay * 40})`;
    ctx.globalAlpha = 0.4;
    this._rect(x + 1, y + 1, glassW - 2, 6, skyTop);
    this._rect(x + glassW + 3, y + 1, glassW - 2, 6, skyTop);
    ctx.globalAlpha = 1;

    // Twinkling stars
    const starPhase = (this.animFrame + x * 7) % 200;
    if (starPhase < 80) {
      ctx.globalAlpha = starPhase < 40 ? starPhase / 40 : (80 - starPhase) / 40;
      this._rect(x + 3, y + 2, 1, 1, '#fff');
      this._rect(x + glassW + 5, y + 4, 1, 1, '#fff');
      ctx.globalAlpha = 1;
    }
  }

  // ── Plants ─────────────────────────────────────────────────────────────────

  drawPlants() {
    const plantPositions = [[0, 1], [19, 1], [10, 1]];
    for (const [gx, gy] of plantPositions) {
      // Plant is 1×2 tiles (leafy top + pot bottom)
      tileAtlas.drawDef(this.ctx, 'plant_large', gx * TILE, gy * TILE);
    }
  }

  // ── Decorations ────────────────────────────────────────────────────────────

  drawDecorations() {
    const ctx = this.ctx;

    // Tile-based decorations (positions match original placements)
    // Original: water_cooler(2,1), bookshelf(8,1), server_rack(16,1), coffee_machine(17,1)
    tileAtlas.drawDef(ctx, 'plant_small', 2 * TILE, 1 * TILE);   // water cooler area
    tileAtlas.drawDef(ctx, 'bookshelf', 8 * TILE, 1 * TILE);     // bookshelf (2×2)
    tileAtlas.drawDef(ctx, 'filing_cabinet', 16 * TILE, 1 * TILE); // server rack area
    tileAtlas.drawDef(ctx, 'printer', 17 * TILE, 1 * TILE);      // coffee machine area

    // Wall clock (small, in wall area — keep as primitive, no good tile match)
    const clockX = 9 * TILE + 8;
    const clockY = 2;
    this._rect(clockX, clockY, 16, 16, '#333355');
    this._rect(clockX + 1, clockY + 1, 14, 14, '#ddd');
    this._rect(clockX + 7, clockY + 3, 2, 6, '#222');
    this._rect(clockX + 7, clockY + 7, 5, 2, '#222');
    this._rect(clockX + 7, clockY + 7, 2, 2, '#ff4444');

    // Coffee machine steam animation (Canvas overlay on tile)
    const steamPhase = Math.sin(this.animFrame * 0.06);
    ctx.globalAlpha = 0.3;
    this._rect(17 * TILE + 14 + steamPhase, 1 * TILE + 2, 2, 4, '#fff');
    this._rect(17 * TILE + 17 - steamPhase, 1 * TILE, 2, 5, '#fff');
    ctx.globalAlpha = 1;

    // Server rack blinking LEDs (Canvas overlay on tile)
    for (let sy = 1 * TILE + 4; sy < 1 * TILE + 28; sy += 6) {
      const ledOn = ((this.animFrame + sy) % 60) < 40;
      this._rect(16 * TILE + 11, sy + 2, 2, 1, ledOn ? '#44ff88' : '#334433');
      this._rect(16 * TILE + 14, sy + 2, 2, 1, '#ffaa00');
    }
  }

  // ── Department Labels ──────────────────────────────────────────────────────

  drawDepartmentLabels() {
    const ctx = this.ctx;
    const layout = this.state.office_layout || {};
    const zones = layout.zones || [];
    const execRow = layout.executive_row != null ? layout.executive_row : 0;
    const deptStartRow = layout.dept_start_row != null ? layout.dept_start_row : 1;
    const deptEndRow = layout.dept_end_row != null ? layout.dept_end_row : 7;

    if (zones.length === 0) return;

    const zoneMidCanvasY = ((deptStartRow + deptEndRow) / 2 + WALL_ROWS) * TILE + TILE / 2;

    for (let i = 0; i < zones.length; i++) {
      const zone = zones[i];
      const zoneWidthPx = (zone.end_col - zone.start_col) * TILE;
      const centerX = ((zone.start_col + zone.end_col) / 2) * TILE;
      const label = zone.label_en || zone.department;
      const fontSize = Math.min(Math.floor(zoneWidthPx / label.length * 1.6), 32);

      ctx.save();
      ctx.globalAlpha = 0.12;
      ctx.fillStyle = zone.label_color || '#888';
      ctx.font = `bold ${fontSize}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, centerX, zoneMidCanvasY);
      ctx.restore();

      if (i > 0) {
        const divX = zone.start_col * TILE;
        ctx.strokeStyle = zone.label_color || '#555';
        ctx.globalAlpha = 0.25;
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(divX, (deptStartRow + WALL_ROWS) * TILE);
        ctx.lineTo(divX, (deptEndRow + WALL_ROWS + 1) * TILE);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      }
    }

    const execRowH = layout.exec_row_height || 2;
    const execMidCanvasY = (execRow + execRowH / 2 + WALL_ROWS) * TILE;
    ctx.save();
    ctx.globalAlpha = 0.1;
    ctx.fillStyle = '#c0b060';
    ctx.font = 'bold 22px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Executive', 10 * TILE, execMidCanvasY);
    ctx.restore();
  }

  // ── Bulletin Board ─────────────────────────────────────────────────────────

  drawBulletinBoard() {
    const ctx = this.ctx;
    const bx = 5 * TILE;
    const by = 2;
    const bw = TILE * 3;
    const bh = TILE - 4;

    // Fallback FIRST (tile draws on top, silent no-op if not loaded)
    if (!tileAtlas.isReady('room')) {
      this._rect(bx, by, bw, bh, PALETTE.boardBg);
    }

    // Tile background — use floor wood tile for cork-board texture
    for (let tx = 0; tx < 3; tx++) {
      tileAtlas.drawDef(this.ctx, 'floor_wood_warm', bx + tx * TILE, by - 2);
    }

    const fc = PALETTE.boardFrame;
    const fl = this._lighten(fc, 20);
    this._rect(bx - 1, by - 1, bw + 2, 3, fc);
    this._rect(bx - 1, by - 1, bw + 2, 1, fl);
    this._rect(bx - 1, by + bh - 2, bw + 2, 3, fc);
    this._rect(bx - 1, by, 3, bh, fc);
    this._rect(bx - 1, by, 1, bh, fl);
    this._rect(bx + bw - 2, by, 3, bh, fc);

    const papers = [
      { x: bx + 6,  y: by + 5,  w: 18, h: 14, color: PALETTE.boardPaper,    tilt:  1 },
      { x: bx + 28, y: by + 4,  w: 16, h: 12, color: PALETTE.boardPaperAlt, tilt: -1 },
      { x: bx + 48, y: by + 6,  w: 20, h: 13, color: PALETTE.boardPaper,    tilt:  0 },
      { x: bx + 14, y: by + 16, w: 14, h: 8,  color: PALETTE.boardPaperAlt, tilt:  1 },
      { x: bx + 38, y: by + 15, w: 18, h: 10, color: PALETTE.boardPaper,    tilt: -1 },
    ];

    for (const p of papers) {
      ctx.globalAlpha = 0.15;
      this._rect(p.x + 1, p.y + 1, p.w, p.h, '#000');
      ctx.globalAlpha = 1;
      this._rect(p.x, p.y, p.w, p.h, p.color);
      this._rect(p.x + p.w - 3, p.y + p.h - 3, 3, 3, this._darken(p.color, 20));
      const pinX = p.x + Math.floor(p.w / 2) - 1;
      this._rect(pinX, p.y - 1, 3, 3, PALETTE.boardPin);
      this._rect(pinX, p.y - 1, 1, 1, '#ff8888');
      for (let i = 0; i < 3 && i * 3 + 3 < p.h; i++) {
        this._rect(p.x + 2, p.y + 3 + i * 3, p.w - 4, 1, '#aaa89a');
      }
    }

    if (this.hoverTile && this.hoverTile.x >= 5 && this.hoverTile.x <= 7 && this.hoverTile.y <= 1) {
      const pulse = Math.sin(this.animFrame * 0.1) * 0.15 + 0.25;
      ctx.globalAlpha = pulse;
      this._rect(bx - 2, by - 2, bw + 4, TILE, PALETTE.ceoGold);
      ctx.globalAlpha = 1;
    }

    ctx.fillStyle = PALETTE.boardPaper;
    ctx.font = '7px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('\u{1F4CB} Rules', bx + TILE * 1.5, by + TILE + 4);
    ctx.textAlign = 'left';
  }

  // ── Project Wall ───────────────────────────────────────────────────────────

  drawProjectWall() {
    const ctx = this.ctx;
    const bx = 12 * TILE;
    const by = 2;
    const bw = TILE * 3;
    const bh = TILE - 4;

    // Fallback FIRST (tile draws on top, silent no-op if not loaded)
    if (!tileAtlas.isReady('room')) {
      this._rect(bx, by, bw, bh, PALETTE.projectBg);
    }

    // Tile background — use dark stone floor tile for project wall surface
    for (let tx = 0; tx < 3; tx++) {
      tileAtlas.drawDef(this.ctx, 'floor_stone_blue', bx + tx * TILE, by - 2);
    }

    const fc = PALETTE.projectFrame;
    this._rect(bx - 1, by - 1, bw + 2, 3, fc);
    this._rect(bx - 1, by - 1, bw + 2, 1, this._lighten(fc, 20));
    this._rect(bx - 1, by + bh - 2, bw + 2, 3, fc);
    this._rect(bx - 1, by, 3, bh, fc);
    this._rect(bx + bw - 2, by, 3, bh, fc);

    const cards = [
      { x: bx + 4,          y: by + 8,  w: 22, h: 10, color: PALETTE.projectCard },
      { x: bx + 4,          y: by + 19, w: 22, h: 6,  color: PALETTE.projectCardAlt },
      { x: bx + TILE + 2,   y: by + 8,  w: 22, h: 12, color: '#e8d0d0' },
      { x: bx + TILE * 2 + 2, y: by + 8,  w: 22, h: 8,  color: '#d0d0e8' },
      { x: bx + TILE * 2 + 2, y: by + 18, w: 22, h: 6,  color: PALETTE.projectCardAlt },
    ];

    for (const c of cards) {
      ctx.globalAlpha = 0.15;
      this._rect(c.x + 1, c.y + 1, c.w, c.h, '#000');
      ctx.globalAlpha = 1;
      this._rect(c.x, c.y, c.w, c.h, c.color);
      const tagColors = ['#44aa66', '#aa6644', '#4466aa', '#aa44aa'];
      this._rect(c.x, c.y, 2, c.h, tagColors[(c.x + c.y) % tagColors.length]);
      for (let i = 0; i < 2 && i * 3 + 2 < c.h; i++) {
        this._rect(c.x + 4, c.y + 2 + i * 3, c.w - 6, 1, '#7a9a7a');
      }
    }

    if (this.hoverTile && this.hoverTile.x >= 12 && this.hoverTile.x <= 14 && this.hoverTile.y <= 1) {
      const pulse = Math.sin(this.animFrame * 0.1) * 0.15 + 0.25;
      ctx.globalAlpha = pulse;
      this._rect(bx - 2, by - 2, bw + 4, TILE, PALETTE.led1);
      ctx.globalAlpha = 1;
    }

    ctx.fillStyle = PALETTE.projectCard;
    ctx.font = '7px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('\u{1F4CA} Projects', bx + TILE * 1.5, by + TILE + 4);
    ctx.textAlign = 'left';
  }

  // ── Color helpers ──────────────────────────────────────────────────────────

  _lighten(hex, amt) {
    const n = parseInt(hex.replace('#', ''), 16);
    const r = Math.min(255, (n >> 16) + amt);
    const g = Math.min(255, ((n >> 8) & 0xff) + amt);
    const b = Math.min(255, (n & 0xff) + amt);
    return `#${(r << 16 | g << 8 | b).toString(16).padStart(6, '0')}`;
  }

  _darken(hex, amt) {
    const n = parseInt(hex.replace('#', ''), 16);
    const r = Math.max(0, (n >> 16) - amt);
    const g = Math.max(0, ((n >> 8) & 0xff) - amt);
    const b = Math.max(0, (n & 0xff) - amt);
    return `#${(r << 16 | g << 8 | b).toString(16).padStart(6, '0')}`;
  }

  // ── Desk (tileset furniture) ──────────────────────────────────

  drawDesk(gx, gy, hasMonitor = true, chairDef = 'chair_black') {
    const px = gx * TILE;
    const py = gy * TILE;
    const ctx = this.ctx;

    // Fallback FIRST (tile draws on top, silent no-op if not loaded)
    if (!tileAtlas.isReady('office')) {
      this._rect(px + 10, py - 2, 12, 8, '#3c3468');   // chair
      this._rect(px, py + 12, TILE, 12, '#9a7420');     // desk
    }

    // Chair tile (above desk, offset so it doesn't overlap monitor)
    tileAtlas.drawDef(ctx, chairDef, px, py);

    // Desk surface tile
    tileAtlas.drawDef(ctx, 'desk_front_l', px, py + TILE);

    // Monitor on desk (drawn above desk surface, behind character)
    if (hasMonitor) {
      tileAtlas.drawDef(ctx, 'monitor_single', px, py - 4);

      // Animated screen glow (Canvas overlay on top of tile)
      ctx.globalAlpha = 0.15;
      ctx.fillStyle = PALETTE.screenOn;
      ctx.fillRect(px + 6, py - 2, 20, 12);
      ctx.globalAlpha = 1;

      // Scanlines on monitor
      ctx.globalAlpha = 0.08;
      for (let sy = py - 2; sy < py + 10; sy += 2) {
        ctx.fillRect(px + 6, sy, 20, 1);
      }
      ctx.globalAlpha = 1;
    }
  }

  // ── Character (sprite-based with status overlays) ──────────────────────────

  drawCharacter(gx, gy, data, isCEO = false) {
    const ctx = this.ctx;
    const px = gx * TILE;
    // Character sprite is 2 tiles tall; bottom aligns with desk row
    const py = gy * TILE - TILE;

    const hash = this._hashStr(data.id || 'default');

    // ── Draw character sprite ──
    const frame = this._getCharFrame(data);
    if (tileAtlas.isReady(frame.sheet)) {
      tileAtlas.drawTile(ctx, frame.sheet, frame.row, frame.col, px, py, frame.w, frame.h);
    } else {
      // Fallback silhouette while loading
      ctx.globalAlpha = 0.3;
      this._rect(px + 8, py + 4, 16, 28, '#888');
      ctx.globalAlpha = 1;
    }

    // ── Avatar image overlay (if available, draw on top of sprite head area) ──
    const avatarImg = this._avatarImages?.[data.id];
    if (avatarImg) {
      const hx = px + 4, hy = py + 2;
      ctx.save();
      ctx.beginPath();
      ctx.arc(hx + 12, hy + 10, 9, 0, Math.PI * 2);
      ctx.clip();
      ctx.drawImage(avatarImg, hx + 3, hy + 1, 18, 18);
      ctx.restore();
    }

    // ── CEO crown (drawn above sprite) ──
    if (isCEO) {
      const cy = py - 2;
      this._rect(px + 9, cy + 2, 14, 2, PALETTE.ceoGold);
      this._rect(px + 9, cy + 2, 14, 1, '#ffe44d');
      this._rect(px + 9, cy, 3, 3, PALETTE.ceoGold);
      this._rect(px + 14, cy - 2, 4, 5, PALETTE.ceoGold);
      this._rect(px + 20, cy, 3, 3, PALETTE.ceoGold);
      const twinkle = Math.floor(this.animFrame * 0.05) % 3;
      this._rect(px + 10, cy + 1, 2, 2, twinkle === 0 ? '#ff6666' : '#ff4444');
      this._rect(px + 15, cy - 1, 2, 2, twinkle === 1 ? '#66ddff' : '#44bbdd');
      this._rect(px + 20, cy + 1, 2, 2, twinkle === 2 ? '#66ff66' : '#44dd44');
    }

    // ── Status overlays (listening, working, idle) ──
    const ROLE_COLORS = {
      HR: PALETTE.hrBlue, COO: PALETTE.cooOrange,
      EA: PALETTE.eaGreen, CSO: PALETTE.csoPurple,
    };
    let labelColor = isCEO ? PALETTE.ceoGold : (ROLE_COLORS[data.role] || PALETTE.led1);

    if (data.is_listening) {
      const glowAlpha = Math.sin(this.animFrame * 0.1) * 0.2 + 0.3;
      ctx.globalAlpha = glowAlpha;
      ctx.strokeStyle = '#cc88ff';
      ctx.lineWidth = 1;
      ctx.strokeRect(px + 2, py + 1, TILE - 4, TILE * 2 - 2);
      ctx.globalAlpha = 1;

      // Listening bubble
      const bubbleX = px + 2, bubbleY = py - 12;
      this._rect(bubbleX + 1, bubbleY, 18, 10, '#fff');
      this._rect(bubbleX, bubbleY + 1, 20, 8, '#fff');
      this._rect(bubbleX + 8, bubbleY + 10, 4, 2, '#fff');
      this._rect(bubbleX + 9, bubbleY + 12, 2, 1, '#fff');
      this._rect(bubbleX + 5, bubbleY + 2, 10, 6, '#9955dd');
      this._rect(bubbleX + 9, bubbleY + 2, 2, 6, '#fff');

      const noteCount = (data.guidance_notes || []).length;
      if (noteCount > 0) {
        this._rect(px + 24, py - 2, 8, 8, '#aa66ff');
        ctx.fillStyle = '#fff';
        ctx.font = '7px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(String(noteCount), px + 28, py + 5);
        ctx.textAlign = 'left';
      }
    } else if ((data.guidance_notes || []).length > 0) {
      const noteCount = data.guidance_notes.length;
      this._rect(px + 24, py + 2, 8, 8, '#6633aa');
      ctx.fillStyle = '#fff';
      ctx.font = '7px monospace';
      ctx.textAlign = 'center';
      ctx.fillText(String(noteCount), px + 28, py + 9);
      ctx.textAlign = 'left';
    }

    if (!isCEO && !data.is_listening) {
      const status = data.status || 'idle';
      const iconX = px + 2, iconY = py - 10;

      if (status === 'working') {
        this._rect(iconX + 1, iconY - 2, 18, 8, '#fff');
        this._rect(iconX, iconY - 1, 20, 6, '#fff');
        this._rect(iconX + 8, iconY + 6, 4, 2, '#fff');
        this._rect(iconX + 9, iconY + 8, 2, 1, '#fff');
        const dotPhase = Math.floor(this.animFrame * 0.08) % 4;
        if (dotPhase >= 1) this._rect(iconX + 4, iconY + 1, 3, 3, '#4488ff');
        if (dotPhase >= 2) this._rect(iconX + 9, iconY + 1, 3, 3, '#55aaff');
        if (dotPhase >= 3) this._rect(iconX + 14, iconY + 1, 3, 3, '#4488ff');
      } else if (status === 'idle') {
        const drift = (this.animFrame * 0.03 + hash) % 1;
        const zAlpha = 0.4 + Math.sin(this.animFrame * 0.04 + hash) * 0.3;
        ctx.globalAlpha = zAlpha;
        ctx.fillStyle = '#8888aa';
        ctx.font = '8px monospace';
        ctx.fillText('z', iconX + 14, iconY + 2 - drift * 4);
        ctx.font = '7px monospace';
        ctx.fillText('z', iconX + 19, iconY - 2 - drift * 4);
        ctx.font = '6px monospace';
        ctx.fillText('z', iconX + 23, iconY - 5 - drift * 4);
        ctx.globalAlpha = 1;
      }
    }

    // Setup/offline badges
    if (!isCEO) {
      if (data.needs_setup) {
        const keyX = px + 22, keyY = py - 10;
        const alpha = 0.6 + Math.sin(this.animFrame * 0.08) * 0.3;
        ctx.globalAlpha = alpha;
        this._rect(keyX, keyY, 10, 8, '#ffaa00');
        this._rect(keyX + 2, keyY + 2, 3, 3, '#fff');
        this._rect(keyX + 5, keyY + 3, 3, 1, '#fff');
        ctx.globalAlpha = 1;
      } else if (data.api_online === false) {
        const offX = px + 22, offY = py - 10;
        const alpha = 0.5 + Math.sin(this.animFrame * 0.1) * 0.4;
        ctx.globalAlpha = alpha;
        this._rect(offX, offY, 10, 8, '#ff3344');
        this._rect(offX + 2, offY + 1, 2, 2, '#fff');
        this._rect(offX + 6, offY + 1, 2, 2, '#fff');
        this._rect(offX + 4, offY + 3, 2, 2, '#fff');
        this._rect(offX + 2, offY + 5, 2, 2, '#fff');
        this._rect(offX + 6, offY + 5, 2, 2, '#fff');
        ctx.globalAlpha = 1;
      }
    }

    // Name tag
    ctx.font = '8px monospace';
    const displayName = data.nickname || (data.name || data.role || '').substring(0, 8);
    const lvlTag = data.level ? ` L${data.level}` : '';
    const nameText = displayName + lvlTag;
    const nameW = ctx.measureText(nameText).width;
    const tagW = nameW + 6;
    const tagX = px + TILE / 2 - tagW / 2;
    const tagY = gy * TILE + 32;
    this._rect(tagX, tagY, tagW, 9, '#0d0d1a');
    this._rect(tagX, tagY, tagW, 1, '#2a2a44');
    this._rect(tagX, tagY + 8, tagW, 1, '#2a2a44');
    this._rect(tagX, tagY, 1, 9, '#2a2a44');
    this._rect(tagX + tagW - 1, tagY, 1, 9, '#2a2a44');
    ctx.fillStyle = labelColor;
    ctx.textAlign = 'center';
    ctx.fillText(nameText, px + TILE / 2, tagY + 8);
    ctx.textAlign = 'left';
  }

  _hashStr(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  // ── Tool Equipment ─────────────────────────────────────────────────────────

  drawToolEquipment(gx, gy, toolData) {
    const px = gx * TILE;
    const py = gy * TILE;

    if (!toolData.has_icon) return;

    const icon = this._toolIcons && this._toolIcons[toolData.id];
    if (icon) {
      const maxW = TILE, maxH = TILE;
      const scale = Math.min(maxW / icon.width, maxH / icon.height);
      const w = Math.round(icon.width * scale);
      const h = Math.round(icon.height * scale);
      const ox = px + Math.round((TILE - w) / 2);
      const oy = py + Math.round((TILE - h) / 2);
      this.ctx.drawImage(icon, ox, oy, w, h);
    } else {
      this._rect(px + 8, py + 8, 16, 16, '#334455');
    }

    this.ctx.fillStyle = PALETTE.led1;
    this.ctx.font = '7px monospace';
    this.ctx.textAlign = 'center';
    const label = (toolData.name || 'TOOL').substring(0, 8).toUpperCase();
    this.ctx.fillText(label, px + 16, py + 36);
    this.ctx.textAlign = 'left';
  }

  // ── Meeting Room ───────────────────────────────────────────────────────────

  drawMeetingRoom(gx, gy, roomData) {
    const ctx = this.ctx;
    const px = gx * TILE;
    const py = gy * TILE;

    // Room floor (2×2 area)
    this._rect(px - 4, py - 4, TILE * 2 + 8, TILE * 2 + 8, '#1c1c36');

    // Conference table (2×2 tile group, centered)
    tileAtlas.drawDef(ctx, 'conf_table_tl', px, py);
    tileAtlas.drawDef(ctx, 'conf_table_tr', px + TILE, py);
    tileAtlas.drawDef(ctx, 'conf_table_bl', px, py + TILE);
    tileAtlas.drawDef(ctx, 'conf_table_br', px + TILE, py + TILE);

    // Chairs around table (3 top, 3 bottom — matches original 6-chair capacity)
    for (let cx = 0; cx < 3; cx++) {
      tileAtlas.drawDef(ctx, 'conf_chair_top', px - TILE / 2 + cx * TILE, py - TILE);
      tileAtlas.drawDef(ctx, 'conf_chair_bottom', px - TILE / 2 + cx * TILE, py + TILE * 2);
    }

    // Wall border
    const wc = '#3a3a66', wl = '#4a4a88';
    this._rect(px - 4, py - 4, TILE * 2 + 8, 3, wc);
    this._rect(px - 4, py - 4, TILE * 2 + 8, 1, wl);
    this._rect(px - 4, py + TILE * 2 + 1, TILE * 2 + 8, 3, wc);
    this._rect(px - 4, py - 4, 3, TILE * 2 + 8, wc);
    this._rect(px + TILE * 2 + 1, py - 4, 3, TILE * 2 + 8, wc);

    // Status LED
    const statusColor = roomData.is_booked ? PALETTE.meetingBooked : PALETTE.meetingFree;
    const glowAlpha = roomData.is_booked
      ? Math.sin(this.animFrame * 0.08) * 0.3 + 0.5
      : 0.8;
    ctx.globalAlpha = glowAlpha;
    this._rect(px + TILE - 1, py - 3, 4, 4, statusColor);
    ctx.globalAlpha = 1;

    // Participant mini-heads (keep original style — colored shirt/skin/hair)
    // Using full character sprites at this scale would be unreadable
    if (roomData.is_booked && roomData.participants) {
      const chairPositions = [
        [px + 4, py + 8],  [px + 22, py + 8],  [px + 40, py + 8],
        [px + 4, py + 34], [px + 22, py + 34], [px + 40, py + 34],
      ];
      const numChairs = Math.min(roomData.capacity || 6, chairPositions.length);
      for (let i = 0; i < Math.min(roomData.participants.length, numChairs); i++) {
        const [cx, cy] = chairPositions[i];
        const pHash  = this._hashStr(roomData.participants[i] || '');
        const pColor = PALETTE.shirt[pHash % PALETTE.shirt.length];
        const pSkin  = PALETTE.skin[pHash % PALETTE.skin.length];
        this._rect(cx + 2, cy - 3, 6, 5, pColor);
        this._rect(cx + 3, cy - 7, 4, 4, pSkin);
        this._rect(cx + 3, cy - 8, 4, 2, PALETTE.hair[pHash % PALETTE.hair.length]);
      }
    }

    // Room label
    const label = (roomData.name || 'Meeting').substring(0, 10);
    ctx.font = '7px monospace';
    const lw = ctx.measureText(label).width + 4;
    const lx = px + TILE - lw / 2;
    const ly = py + TILE * 2 + 8;
    this._rect(lx, ly, lw, 9, '#0d0d1a');
    this._rect(lx, ly, lw, 1, '#2a2a44');
    ctx.fillStyle = statusColor;
    ctx.textAlign = 'center';
    ctx.fillText(label, px + TILE, ly + 8);
    if (roomData.is_booked) {
      ctx.fillStyle = PALETTE.meetingBooked;
      ctx.font = '6px monospace';
      ctx.fillText('IN USE', px + TILE, ly + 16);
    }
    ctx.textAlign = 'left';
  }

  // ── Entity drawing (Y-sorted) ──────────────────────────────────────────────

  _drawEntities() {
    // Build inMeeting map: empId → {x, y} in canvas-row space
    const inMeeting = {};
    for (const room of (this.state.meeting_rooms || [])) {
      if (room.is_booked && room.participants) {
        const [rx, ry] = room.position || [0, 0];
        for (let i = 0; i < room.participants.length; i++) {
          inMeeting[room.participants[i]] = {
            x: rx + (i % 3),
            y: ry + WALL_ROWS + Math.floor(i / 3),
          };
        }
      }
    }

    // CEO
    const execRowCanvas = ((this.state.office_layout || {}).executive_row || 0) + WALL_ROWS;
    this.drawDesk(9, execRowCanvas, true, 'chair_gold');
    if (!inMeeting['ceo']) {
      this.drawCharacter(9, execRowCanvas, { id: 'ceo_boss', name: 'CEO', role: 'CEO' }, true);
    }

    // AI employees
    for (const emp of (this.state.employees || [])) {
      if (emp.remote) continue;
      const [gx, gy] = emp.desk_position || [0, 0];
      this.drawDesk(gx, gy + WALL_ROWS, true);
      if (inMeeting[emp.id]) {
        const pos = inMeeting[emp.id];
        this.drawCharacter(pos.x, pos.y, emp);
      } else {
        this.drawCharacter(gx, gy + WALL_ROWS, emp);
      }
    }

    // Tools
    for (const tool of (this.state.tools || [])) {
      if (!tool.has_icon) continue;
      const [gx, gy] = tool.desk_position || [0, 0];
      this.drawToolEquipment(gx, gy + WALL_ROWS, tool);
    }

    // Meeting rooms
    for (const room of (this.state.meeting_rooms || [])) {
      const [gx, gy] = room.position || [0, 0];
      this.drawMeetingRoom(gx, gy + WALL_ROWS, room);
    }

    // CEO in meeting room
    if (inMeeting['ceo']) {
      const pos = inMeeting['ceo'];
      this.drawCharacter(pos.x, pos.y, { id: 'ceo_boss', name: 'CEO', role: 'CEO' }, true);
    }
  }

  // ── Tooltip ────────────────────────────────────────────────────────────────

  _updateTooltip() {
    if (!this.hoverTile) return;
    const { x, y, screenX, screenY } = this.hoverTile;

    let tooltipText = null;

    if (x >= 5 && x <= 7 && y <= 1) {
      tooltipText = '📋 Company Rules\nClick to view and edit workflows';
    }
    if (x >= 12 && x <= 14 && y <= 1) {
      tooltipText = '📋 Project Wall\nClick to view project history';
    }

    const ceoCanvasRow = ((this.state.office_layout || {}).executive_row || 0) + WALL_ROWS;
    if (x === 9 && (y === ceoCanvasRow - 1 || y === ceoCanvasRow || y === ceoCanvasRow + 1)) {
      tooltipText = 'CEO (You)\nRole: Chief Executive\nInput tasks below';
    }

    const LEVEL_NAMES = { 1: 'Junior', 2: 'Mid', 3: 'Senior', 4: 'Founding', 5: 'CEO' };
    for (const emp of (this.state.employees || [])) {
      const [ex, ey] = emp.desk_position || [0, 0];
      const canvasRow = ey + WALL_ROWS;
      if (x === ex && (y === canvasRow - 1 || y === canvasRow || y === canvasRow + 1)) {
        const nn  = emp.nickname ? ` (${emp.nickname})` : '';
        const lvl = LEVEL_NAMES[emp.level] || `Lv.${emp.level}`;
        const title = emp.title || `${lvl}${emp.role}`;
        const hist = emp.performance_history || [];
        const latestScore = hist.length > 0 ? hist[hist.length - 1].score : '-';
        tooltipText = `${emp.name}${nn}\n${title}\nSkills: ${(emp.skills || []).join(', ')}\nPerformance: ${latestScore}`;
        if (emp.needs_setup) tooltipText += '\n🔑 Needs API setup';
        else if (emp.api_online === false) tooltipText += '\n🔴 API offline';
        if (emp.is_listening) tooltipText += '\n📖 In 1-on-1 meeting...';
        tooltipText += '\n\n(Click for details)';
        break;
      }
    }

    for (const tool of (this.state.tools || [])) {
      if (!tool.has_icon) continue;
      const [tx, ty] = tool.desk_position || [0, 0];
      const canvasRow = ty + WALL_ROWS;
      if (x === tx && y >= canvasRow && y <= canvasRow + 1) {
        tooltipText = `🔧 ${tool.name}`;
        if (tool.description) tooltipText += `\n${tool.description}`;
        break;
      }
    }

    for (const room of (this.state.meeting_rooms || [])) {
      const [rx, ry] = room.position || [0, 0];
      if (x >= rx && x <= rx + 1 && y >= ry + WALL_ROWS && y <= ry + WALL_ROWS + 2) {
        const status = room.is_booked ? '🔴 In Use' : '🟢 Available';
        tooltipText = `🏢 ${room.name}\n${room.description}\nCapacity: ${room.capacity}\nStatus: ${status}`;
        if (room.is_booked && room.participants?.length > 0) {
          tooltipText += `\nParticipants: ${room.participants.join(', ')}`;
        }
        break;
      }
    }

    const tooltip = document.getElementById('tooltip');
    if (!tooltip) return;
    if (tooltipText) {
      tooltip.textContent = tooltipText;
      const canvasRect = this.canvas.parentElement.getBoundingClientRect();
      tooltip.style.left = (screenX - canvasRect.left + 12) + 'px';
      tooltip.style.top  = (screenY - canvasRect.top  - 8) + 'px';
      tooltip.classList.remove('hidden');
    } else {
      tooltip.classList.add('hidden');
    }
  }

  // ── Main render loop ───────────────────────────────────────────────────────

  render() {
    const dpr  = this.dpr || 1;
    const ctx  = this.ctx;
    const cssW = this.canvas.width  / dpr;
    const cssH = this.canvas.height / dpr;

    // Clear buffer
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Base DPR scaling (maps CSS pixels → physical pixels)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;

    // === World space (pan + zoom) ===
    this.camera.applyTransform(ctx);

    this.drawFloor();
    this.drawWalls();
    this.drawBulletinBoard();
    this.drawProjectWall();
    this.drawPlants();
    this.drawDecorations();
    this.drawDepartmentLabels();

    this._drawEntities();

    this._updateParticles();
    this._drawParticles();

    this.camera.resetTransform(ctx);

    // === Screen space ===
    // Subtle scanline (screen space — covers full viewport at any zoom/pan)
    ctx.globalAlpha = 0.02;
    ctx.fillStyle = '#000';
    for (let sy = 0; sy < cssH; sy += 2) {
      ctx.fillRect(0, sy, cssW, 1);
    }
    ctx.globalAlpha = 1;

    this.minimap.draw(ctx, cssW, cssH);
    this._updateTooltip();
  }

  loop() {
    this.camera.update();
    this.animFrame++;
    this.render();
    requestAnimationFrame(() => this.loop());
  }
}

// ── CEO avatar (48×48 px, drawn in sidebar) ───────────────────────────────────

function drawCEOAvatar() {
  const c = document.getElementById('ceo-avatar');
  if (!c) return;
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const P = 2;

  ctx.fillStyle = '#14142a';
  ctx.fillRect(0, 0, 48, 48);
  ctx.fillStyle = '#1c1c35';
  ctx.fillRect(0, 44, 48, 4);

  const r = (x, y, w, h, color) => { ctx.fillStyle = color; ctx.fillRect(x, y, w, h); };

  r(16, 30, 16, 16, '#ffd700');
  r(16, 30, 3, 14, '#ffe44d');
  r(29, 30, 3, 14, '#ccaa00');
  r(21, 30, 6, P, '#f0c080');
  r(22, 32, 4, P, '#f0c080');

  r(12, 31, 4, 10, '#ffd700');
  r(12, 31, 2, 10, '#ffe44d');
  r(12, 40, 4, 3, '#f0c080');
  r(32, 31, 4, 10, '#ffd700');
  r(34, 31, 2, 10, '#ccaa00');
  r(32, 40, 4, 3, '#f0c080');

  r(17, 10, 14, P, '#f0c080');
  r(15, 12, 18, 16, '#f0c080');
  r(17, 28, 14, P, '#f0c080');
  ctx.globalAlpha = 0.15;
  r(15, 14, 4, 12, '#fff');
  r(29, 14, 4, 12, '#000');
  ctx.globalAlpha = 1;
  ctx.globalAlpha = 0.25;
  r(15, 22, 4, 3, '#ff7777');
  r(29, 22, 4, 3, '#ff7777');
  ctx.globalAlpha = 1;

  r(16, 6, 16, P * 2, '#2a2a2a');
  r(15, 10, 18, 4, '#2a2a2a');
  r(13, 12, 4, 10, '#2a2a2a');
  r(31, 12, 4, 10, '#2a2a2a');
  ctx.globalAlpha = 0.2;
  r(19, 8, 6, P, '#fff');
  ctx.globalAlpha = 1;

  r(15, 5, 18, 3, '#ffd700');
  r(16, 5, 16, 1, '#ffe44d');
  r(15, 2, 4, 4, '#ffd700');
  r(22, 0, 4, 6, '#ffd700');
  r(29, 2, 4, 4, '#ffd700');
  r(16, 2, 2, P, '#fff8aa');
  r(23, 0, 2, P, '#fff8aa');
  r(30, 2, 2, P, '#fff8aa');
  r(16, 3, P, P, '#ff4444');
  r(23, 1, P, P, '#44ddff');
  r(30, 3, P, P, '#44ff44');

  r(18, 16, 5, 5, '#fff');
  r(27, 16, 5, 5, '#fff');
  r(19, 17, 4, 4, '#334466');
  r(28, 17, 4, 4, '#334466');
  r(20, 18, 3, 3, '#111');
  r(29, 18, 3, 3, '#111');
  r(19, 16, P, P, '#fff');
  r(28, 16, P, P, '#fff');

  r(21, 25, 6, P, '#c08060');
  r(22, 27, 4, P, '#c08060');
}

// Initialize
window.officeRenderer = new OfficeRenderer('office-canvas');
drawCEOAvatar();
