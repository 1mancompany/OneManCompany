/**
 * office.js — Pixel art office canvas renderer
 * Draws employees, desks, tools, and the office environment
 * using pure Canvas 2D API rectangles (no sprite sheets needed).
 */

const TILE = 32;
const COLS = 20;
let ROWS = 15;  // dynamic, updated from office_layout.canvas_rows

const PALETTE = {
  // Floor
  floor1: '#2a2a3e',
  floor2: '#262636',
  // Walls
  wallTop: '#14142a',
  wallMid: '#1c1c35',
  wallBot: '#222244',
  // Furniture
  desk: '#8b6914',
  deskDark: '#6b4f0e',
  deskLight: '#a07818',
  chair: '#3a3060',
  chairDark: '#2a2048',
  // Tech
  screenOn: '#00ccff',
  screenGlow: 'rgba(0, 204, 255, 0.15)',
  screenOff: '#333355',
  led1: '#00ff88',
  led2: '#ff4444',
  led3: '#ffdd00',
  // People
  skin: ['#f0c080', '#e8b070', '#c89060', '#a07050', '#805840'],
  hair: ['#2a2a2a', '#4a2a00', '#cc8800', '#cc4444', '#8844aa', '#224488'],
  shirt: ['#4488ff', '#ff4466', '#44cc44', '#cc44cc', '#ff8844', '#44cccc'],
  // Special
  ceoGold: '#ffd700',
  hrBlue: '#4488ff',
  cooOrange: '#ff8844',
  eaGreen: '#44cc88',
  csoPurple: '#aa44ff',
  // Meeting Room
  meetingTable: '#5c4420',
  meetingTableLight: '#7a5c2e',
  meetingChair: '#445566',
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
  // Environment
  plant: '#22aa44',
  plantPot: '#884422',
  windowFrame: '#4444aa',
  windowGlass: '#1a1a55',
  windowSky: '#2244aa',
};

class OfficeRenderer {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    // Disable image smoothing for crisp pixel art
    this.ctx.imageSmoothingEnabled = false;
    this.state = { employees: [], tools: [], meeting_rooms: [], ceo_tasks: [], activity_log: [] };
    this.animFrame = 0;
    this.hoverTile = null;
    this.particles = [];

    // Handle high-DPI displays
    this._setupHiDPI();

    // Mouse tracking for tooltips
    this.canvas.addEventListener('mousemove', (e) => this._onMouseMove(e));
    this.canvas.addEventListener('mouseleave', () => {
      this.hoverTile = null;
      document.getElementById('tooltip').classList.add('hidden');
    });

    // Click handler for interactive elements (bulletin board, meeting rooms, employees)
    this.canvas.addEventListener('click', (e) => this._onClick(e));

    this._resizeCanvas();
    window.addEventListener('resize', () => this._resizeCanvas());
    // Watch for parent container size changes (e.g. panel divider drag)
    new ResizeObserver(() => this._resizeCanvas()).observe(this.canvas.parentElement);
    this.loop();
  }

  _setupHiDPI() {
    const dpr = window.devicePixelRatio || 1;
    // Scale the internal canvas resolution up by devicePixelRatio
    const logicalW = 640;
    const logicalH = 480;
    this.canvas.width = logicalW * dpr;
    this.canvas.height = logicalH * dpr;
    this.canvas.style.width = logicalW + 'px';
    this.canvas.style.height = logicalH + 'px';
    this.ctx.scale(dpr, dpr);
    this.ctx.imageSmoothingEnabled = false;
    this.dpr = dpr;
    // Store logical dimensions for clearing
    this.logicalWidth = logicalW;
    this.logicalHeight = logicalH;
  }

  _resizeCanvas() {
    const parent = this.canvas.parentElement;
    const w = parent.clientWidth;
    const h = parent.clientHeight - 45; // subtract header
    if (w <= 0 || h <= 0) return;

    // Fill entire container — stretch to fit
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';

    // Update internal resolution for hi-DPI
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(COLS * TILE * dpr);
    this.canvas.height = Math.round(ROWS * TILE * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.imageSmoothingEnabled = false;
    this.dpr = dpr;
    this.scale = w / (COLS * TILE);
  }

  _onMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    // Map from CSS pixels to logical canvas pixels (COLS*TILE x ROWS*TILE)
    const scaleX = (COLS * TILE) / rect.width;
    const scaleY = (ROWS * TILE) / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;
    const tx = Math.floor(mx / TILE);
    const ty = Math.floor(my / TILE);
    this.hoverTile = { x: tx, y: ty, screenX: e.clientX, screenY: e.clientY };
  }

  _onClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = (COLS * TILE) / rect.width;
    const scaleY = (ROWS * TILE) / rect.height;
    const mx = (e.clientX - rect.left) * scaleX;
    const my = (e.clientY - rect.top) * scaleY;
    const tx = Math.floor(mx / TILE);
    const ty = Math.floor(my / TILE);

    // Bulletin board is at tiles (5,0)-(7,1) on the wall
    if (tx >= 5 && tx <= 7 && ty >= 0 && ty <= 1) {
      if (window.app && window.app.openWorkflowPanel) {
        window.app.openWorkflowPanel();
      }
      return;
    }

    // Project wall at tiles (12,0)-(14,1)
    if (tx >= 12 && tx <= 14 && ty >= 0 && ty <= 1) {
      if (window.app && window.app.openProjectWall) {
        window.app.openProjectWall();
      }
      return;
    }

    // Check meeting rooms — each room occupies 2x2 tiles offset by +3 rows
    for (const room of (this.state.meeting_rooms || [])) {
      const [rx, ry] = room.position || [0, 0];
      // Room drawn at (rx, ry+3) spanning 2 tiles wide, 2 tiles tall
      if (tx >= rx && tx <= rx + 1 && ty >= ry + 3 && ty <= ry + 5) {
        if (window.app && window.app.openMeetingRoom) {
          window.app.openMeetingRoom(room);
        }
        return;
      }
    }

    // Check employees — detect click on employee sprite at desk position
    for (const emp of this.state.employees) {
      const [ex, ey] = emp.desk_position || [0, 0];
      // Employee character is drawn at (ex, ey+3), sprite occupies roughly 1 tile wide, ~2 tiles tall
      if (tx === ex && (ty === ey + 2 || ty === ey + 3 || ty === ey + 4)) {
        if (window.app && window.app.openEmployeeDetail) {
          window.app.openEmployeeDetail(emp);
        }
        return;
      }
    }
  }

  updateState(newState) {
    const oldEmpCount = this.state.employees.length;
    this.state = newState;

    // Dynamically expand canvas rows based on layout
    const newRows = (newState.office_layout || {}).canvas_rows || 15;
    if (newRows !== ROWS) {
      ROWS = newRows;
      this._resizeCanvas();
    }

    // Spawn particles on new hire
    if (newState.employees.length > oldEmpCount) {
      const latest = newState.employees[newState.employees.length - 1];
      const [gx, gy] = latest.desk_position || [0, 0];
      this._spawnParticles(gx * TILE + 16, (gy + 3) * TILE, PALETTE.led1, 12);
    }

    // Preload tool icons
    this._preloadToolIcons();
  }

  _preloadToolIcons() {
    if (!this._toolIcons) this._toolIcons = {};
    for (const tool of (this.state.tools || [])) {
      if (tool.has_icon && !this._toolIcons[tool.id]) {
        const img = new Image();
        img.src = `/api/tools/${encodeURIComponent(tool.id)}/icon`;
        img.onload = () => {
          this._toolIcons[tool.id] = img;
        };
        this._toolIcons[tool.id] = null; // mark as loading
      }
    }
  }

  // ===== Particle system =====
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
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.1; // gravity
      p.life--;
      if (p.life <= 0) this.particles.splice(i, 1);
    }
  }

  _drawParticles() {
    const ctx = this.ctx;
    for (const p of this.particles) {
      ctx.globalAlpha = Math.min(1, p.life / 15);
      ctx.fillStyle = p.color;
      ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size, p.size);
    }
    ctx.globalAlpha = 1;
  }

  // ===== Drawing primitives =====
  _rect(x, y, w, h, color) {
    this.ctx.fillStyle = color;
    this.ctx.fillRect(x, y, w, h);
  }

  // ===== Environment =====
  drawFloor() {
    const ctx = this.ctx;
    const layout = this.state.office_layout || {};
    const zones = layout.zones || [];
    const execRow = layout.executive_row != null ? layout.executive_row : -1;
    const execColors = layout.exec_floor_colors || null;
    const deptStartRow = layout.dept_start_row != null ? layout.dept_start_row : 1;
    const deptEndRow = layout.dept_end_row != null ? layout.dept_end_row : 7;

    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        // Canvas row to grid-Y: rows 0-2 are wall/decorations, grid-Y 0 starts at canvas row 3
        const gy = r - 3;
        let f1 = PALETTE.floor1;
        let f2 = PALETTE.floor2;

        // Executive row
        if (gy === execRow && execColors) {
          f1 = execColors[0];
          f2 = execColors[1];
        }
        // Department zone area
        else if (gy >= deptStartRow && gy <= deptEndRow) {
          for (const zone of zones) {
            if (c >= zone.start_col && c < zone.end_col) {
              f1 = zone.floor1;
              f2 = zone.floor2;
              break;
            }
          }
        }

        ctx.fillStyle = (r + c) % 2 === 0 ? f1 : f2;
        ctx.fillRect(c * TILE, r * TILE, TILE, TILE);
      }
    }
  }

  drawWalls() {
    const ctx = this.ctx;
    // Top wall
    this._rect(0, 0, COLS * TILE, TILE, PALETTE.wallTop);
    this._rect(0, TILE * 0.8, COLS * TILE, TILE * 0.2, PALETTE.wallBot);

    // Windows on wall
    for (let i = 1; i < COLS - 1; i += 4) {
      this._drawWindow(i * TILE + 8, 4);
    }
  }

  _drawWindow(x, y) {
    const ctx = this.ctx;
    // Frame
    this._rect(x - 2, y - 2, TILE - 12, TILE - 8, PALETTE.windowFrame);
    // Glass
    this._rect(x, y, TILE - 16, TILE - 12, PALETTE.windowGlass);
    // Sky reflection
    const shimmer = Math.sin(this.animFrame * 0.03) * 0.3 + 0.5;
    ctx.globalAlpha = shimmer * 0.3;
    this._rect(x + 2, y + 2, 6, TILE - 16, PALETTE.windowSky);
    ctx.globalAlpha = 1;
  }

  drawPlants() {
    // Decorative plants at fixed positions
    const plantPositions = [[0, 1], [19, 1], [10, 1]];
    for (const [gx, gy] of plantPositions) {
      this._drawPlant(gx * TILE + 8, gy * TILE);
    }
  }

  _drawPlant(x, y) {
    // Pot
    this._rect(x + 4, y + 16, 16, 12, PALETTE.plantPot);
    this._rect(x + 2, y + 14, 20, 4, PALETTE.plantPot);
    // Leaves
    const sway = Math.sin(this.animFrame * 0.02) * 2;
    this._rect(x + 8 + sway, y + 2, 8, 14, PALETTE.plant);
    this._rect(x + 4 + sway, y + 6, 6, 10, '#1d9940');
    this._rect(x + 14 + sway, y + 4, 6, 12, '#28bb4c');
  }

  // ===== Department Labels & Zone Dividers =====
  drawDepartmentLabels() {
    const ctx = this.ctx;
    const layout = this.state.office_layout || {};
    const zones = layout.zones || [];
    const execRow = layout.executive_row != null ? layout.executive_row : 0;
    const deptStartRow = layout.dept_start_row != null ? layout.dept_start_row : 1;
    const deptEndRow = layout.dept_end_row != null ? layout.dept_end_row : 7;

    if (zones.length === 0) return;

    // Large watermark-style department names rendered ON the zone area
    const zoneMidCanvasY = ((deptStartRow + deptEndRow) / 2 + 3) * TILE + TILE / 2;

    for (let i = 0; i < zones.length; i++) {
      const zone = zones[i];
      const zoneWidthPx = (zone.end_col - zone.start_col) * TILE;
      const centerX = ((zone.start_col + zone.end_col) / 2) * TILE;

      // Fit font size to zone width (large but capped)
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

      // Dashed vertical divider between zones (not before the first)
      if (i > 0) {
        const divX = zone.start_col * TILE;
        ctx.strokeStyle = zone.label_color || '#555';
        ctx.globalAlpha = 0.25;
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(divX, (deptStartRow + 3) * TILE);
        ctx.lineTo(divX, (deptEndRow + 4) * TILE);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      }
    }

    // "Executive" watermark on exec row
    ctx.save();
    ctx.globalAlpha = 0.1;
    ctx.fillStyle = '#c0b060';
    ctx.font = 'bold 18px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Executive', 10 * TILE, (execRow + 3) * TILE + TILE / 2);
    ctx.restore();
  }

  // ===== Bulletin Board (Company Rules) =====
  drawBulletinBoard() {
    const ctx = this.ctx;
    const bx = 5 * TILE;
    const by = 2;

    // Board background (cork)
    this._rect(bx, by, TILE * 3, TILE - 4, PALETTE.boardBg);
    // Frame
    this._rect(bx, by, TILE * 3, 2, PALETTE.boardFrame);
    this._rect(bx, by + TILE - 6, TILE * 3, 2, PALETTE.boardFrame);
    this._rect(bx, by, 2, TILE - 4, PALETTE.boardFrame);
    this._rect(bx + TILE * 3 - 2, by, 2, TILE - 4, PALETTE.boardFrame);

    // Paper notes pinned to board
    const papers = [
      { x: bx + 6, y: by + 5, w: 18, h: 14, color: PALETTE.boardPaper },
      { x: bx + 28, y: by + 4, w: 16, h: 12, color: PALETTE.boardPaperAlt },
      { x: bx + 48, y: by + 6, w: 20, h: 13, color: PALETTE.boardPaper },
      { x: bx + 14, y: by + 16, w: 14, h: 8, color: PALETTE.boardPaperAlt },
      { x: bx + 38, y: by + 15, w: 18, h: 10, color: PALETTE.boardPaper },
    ];

    for (const p of papers) {
      this._rect(p.x, p.y, p.w, p.h, p.color);
      // Pin at top center
      this._rect(p.x + Math.floor(p.w / 2) - 1, p.y - 1, 3, 3, PALETTE.boardPin);
      // Text lines (tiny)
      for (let i = 0; i < 3 && i * 3 + 3 < p.h; i++) {
        this._rect(p.x + 2, p.y + 3 + i * 3, p.w - 4, 1, '#aaa89a');
      }
    }

    // Hover glow effect
    if (this.hoverTile && this.hoverTile.x >= 5 && this.hoverTile.x <= 7 && this.hoverTile.y <= 1) {
      const pulse = Math.sin(this.animFrame * 0.1) * 0.15 + 0.25;
      ctx.globalAlpha = pulse;
      this._rect(bx - 2, by - 2, TILE * 3 + 4, TILE, PALETTE.ceoGold);
      ctx.globalAlpha = 1;
    }

    // Label below
    ctx.fillStyle = PALETTE.boardPaper;
    ctx.font = '7px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Rules', bx + TILE * 1.5, by + TILE + 4);
    ctx.textAlign = 'left';
  }

  // ===== Project Wall =====
  drawProjectWall() {
    const ctx = this.ctx;
    const bx = 12 * TILE;
    const by = 2;

    // Board background (green-tinted)
    this._rect(bx, by, TILE * 3, TILE - 4, PALETTE.projectBg);
    // Frame
    this._rect(bx, by, TILE * 3, 2, PALETTE.projectFrame);
    this._rect(bx, by + TILE - 6, TILE * 3, 2, PALETTE.projectFrame);
    this._rect(bx, by, 2, TILE - 4, PALETTE.projectFrame);
    this._rect(bx + TILE * 3 - 2, by, 2, TILE - 4, PALETTE.projectFrame);

    // Project cards pinned to board
    const cards = [
      { x: bx + 6, y: by + 5, w: 18, h: 14, color: PALETTE.projectCard },
      { x: bx + 28, y: by + 4, w: 16, h: 12, color: PALETTE.projectCardAlt },
      { x: bx + 48, y: by + 6, w: 20, h: 13, color: PALETTE.projectCard },
      { x: bx + 14, y: by + 16, w: 14, h: 8, color: PALETTE.projectCardAlt },
    ];

    for (const c of cards) {
      this._rect(c.x, c.y, c.w, c.h, c.color);
      // Yellow pin at top center
      this._rect(c.x + Math.floor(c.w / 2) - 1, c.y - 1, 3, 3, PALETTE.projectPin);
      // Text lines (tiny)
      for (let i = 0; i < 3 && i * 3 + 3 < c.h; i++) {
        this._rect(c.x + 2, c.y + 3 + i * 3, c.w - 4, 1, '#7a9a7a');
      }
    }

    // Hover glow effect
    if (this.hoverTile && this.hoverTile.x >= 12 && this.hoverTile.x <= 14 && this.hoverTile.y <= 1) {
      const pulse = Math.sin(this.animFrame * 0.1) * 0.15 + 0.25;
      ctx.globalAlpha = pulse;
      this._rect(bx - 2, by - 2, TILE * 3 + 4, TILE, PALETTE.led1);
      ctx.globalAlpha = 1;
    }

    // Label below
    ctx.fillStyle = PALETTE.projectCard;
    ctx.font = '7px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Projects', bx + TILE * 1.5, by + TILE + 4);
    ctx.textAlign = 'left';
  }

  // ===== Desk & Chair =====
  drawDesk(gx, gy, hasMonitor = true) {
    const px = gx * TILE;
    const py = gy * TILE;

    // Chair (behind desk)
    this._rect(px + 8, py - 4, 16, 8, PALETTE.chair);
    this._rect(px + 12, py + 4, 8, 6, PALETTE.chairDark);

    // Desk surface
    this._rect(px, py + 12, TILE, 14, PALETTE.desk);
    this._rect(px, py + 24, TILE, 4, PALETTE.deskDark);
    // Desk highlight
    this._rect(px + 2, py + 12, TILE - 4, 2, PALETTE.deskLight);

    // Desk legs
    this._rect(px + 2, py + 26, 4, 6, PALETTE.deskDark);
    this._rect(px + TILE - 6, py + 26, 4, 6, PALETTE.deskDark);

    if (hasMonitor) {
      // Monitor
      const screenColor = PALETTE.screenOn;
      this._rect(px + 6, py, 20, 14, '#222');
      this._rect(px + 8, py + 2, 16, 10, screenColor);
      // Screen glow
      this.ctx.fillStyle = PALETTE.screenGlow;
      this.ctx.fillRect(px + 4, py - 2, 24, 18);
      // Stand
      this._rect(px + 14, py + 12, 4, 4, '#333');
    }
  }

  // ===== Character drawing =====
  drawCharacter(gx, gy, data, isCEO = false) {
    const ctx = this.ctx;
    const px = gx * TILE + 4;
    const py = gy * TILE - TILE + 4;

    const ROLE_COLORS = {
      'HR': PALETTE.hrBlue,
      'COO': PALETTE.cooOrange,
      'EA': PALETTE.eaGreen,
      'CSO': PALETTE.csoPurple,
    };

    // Determine colors based on ID for variety
    const hash = this._hashStr(data.id || 'default');
    const skinIdx = hash % PALETTE.skin.length;
    const hairIdx = (hash >> 2) % PALETTE.hair.length;
    const shirtIdx = (hash >> 4) % PALETTE.shirt.length;

    let shirtColor = PALETTE.shirt[shirtIdx];
    let labelColor = PALETTE.led1;

    const roleColor = ROLE_COLORS[data.role];
    if (isCEO) {
      shirtColor = PALETTE.ceoGold;
      labelColor = PALETTE.ceoGold;
    } else if (roleColor) {
      shirtColor = roleColor;
      labelColor = roleColor;
    }

    // Body bounce animation
    const bounce = Math.sin(this.animFrame * 0.05 + hash) * 1;

    const bx = px;
    const by = py + bounce;

    // Shadow
    ctx.globalAlpha = 0.3;
    this._rect(bx + 2, gy * TILE + 28, 20, 4, '#000');
    ctx.globalAlpha = 1;

    // Body
    this._rect(bx + 4, by + 16, 16, 14, shirtColor);

    // Head
    this._rect(bx + 6, by + 6, 12, 12, PALETTE.skin[skinIdx]);

    // Hair
    this._rect(bx + 5, by + 4, 14, 5, PALETTE.hair[hairIdx]);

    // Eyes
    const blinkPhase = (this.animFrame + hash * 7) % 120;
    if (blinkPhase > 3) {
      this._rect(bx + 8, by + 10, 3, 3, '#111');
      this._rect(bx + 14, by + 10, 3, 3, '#111');
      // Eye highlights
      this._rect(bx + 9, by + 10, 1, 1, '#fff');
      this._rect(bx + 15, by + 10, 1, 1, '#fff');
    } else {
      // Blink
      this._rect(bx + 8, by + 11, 3, 1, '#111');
      this._rect(bx + 14, by + 11, 3, 1, '#111');
    }

    // CEO crown
    if (isCEO) {
      this._rect(bx + 6, by + 1, 12, 4, PALETTE.ceoGold);
      this._rect(bx + 6, by - 1, 3, 3, PALETTE.ceoGold);
      this._rect(bx + 11, by - 2, 3, 3, PALETTE.ceoGold);
      this._rect(bx + 15, by - 1, 3, 3, PALETTE.ceoGold);
      // Jewels
      this._rect(bx + 7, by + 0, 1, 1, '#ff4444');
      this._rect(bx + 12, by - 1, 1, 1, '#4488ff');
      this._rect(bx + 16, by + 0, 1, 1, '#44ff44');
    }

    // Listening mode glow + speech bubble
    if (data.is_listening) {
      // Pulsing purple glow around character
      const glowAlpha = Math.sin(this.animFrame * 0.1) * 0.3 + 0.4;
      ctx.globalAlpha = glowAlpha;
      this._rect(bx - 2, by - 2, 28, 38, '#aa66ff');
      ctx.globalAlpha = 1;

      // Speech bubble with book icon above head
      const bubbleX = bx + 2;
      const bubbleY = by - 14;
      this._rect(bubbleX, bubbleY, 20, 12, '#fff');
      this._rect(bubbleX + 2, bubbleY + 2, 16, 8, '#fff');
      // Bubble tail
      this._rect(bubbleX + 8, bubbleY + 12, 4, 3, '#fff');
      // Book icon (pixel art)
      this._rect(bubbleX + 5, bubbleY + 2, 10, 8, '#aa66ff');
      this._rect(bubbleX + 9, bubbleY + 2, 2, 8, '#fff');

      // Guidance count badge
      const noteCount = (data.guidance_notes || []).length;
      if (noteCount > 0) {
        this._rect(bx + 20, by - 2, 10, 10, '#aa66ff');
        ctx.fillStyle = '#fff';
        ctx.font = '7px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(String(noteCount), bx + 25, by + 6);
        ctx.textAlign = 'left';
      }
    } else if ((data.guidance_notes || []).length > 0) {
      // Small badge showing guidance count even when not listening
      const noteCount = data.guidance_notes.length;
      this._rect(bx + 20, by, 10, 10, '#6633aa');
      ctx.fillStyle = '#fff';
      ctx.font = '7px monospace';
      ctx.textAlign = 'center';
      ctx.fillText(String(noteCount), bx + 25, by + 8);
      ctx.textAlign = 'left';
    }

    // Status icon above head (only for non-CEO, non-listening)
    if (!isCEO && !data.is_listening) {
      const status = data.status || 'idle';
      const iconX = bx + 2;
      const iconY = by - 10;

      if (status === 'working') {
        // Working: small bubble with animated "..." dots
        this._rect(iconX, iconY - 2, 20, 10, '#fff');
        this._rect(iconX + 8, iconY + 8, 4, 3, '#fff'); // tail
        const dotPhase = Math.floor(this.animFrame * 0.08) % 4;
        const dotColor = '#4488ff';
        if (dotPhase >= 1) this._rect(iconX + 3, iconY + 2, 3, 3, dotColor);
        if (dotPhase >= 2) this._rect(iconX + 8, iconY + 2, 3, 3, dotColor);
        if (dotPhase >= 3) this._rect(iconX + 13, iconY + 2, 3, 3, dotColor);
      } else if (status === 'idle') {
        // Idle: floating "z z z" with gentle drift upward
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

    // Offline / needs-setup indicator (higher priority than status icons)
    if (!isCEO) {
      if (data.needs_setup) {
        // Yellow key icon — pulsing to remind setup needed
        const keyX = bx + 18, keyY = by - 12;
        const alpha = 0.6 + Math.sin(this.animFrame * 0.08) * 0.3;
        ctx.globalAlpha = alpha;
        this._rect(keyX, keyY, 12, 10, '#ffaa00');
        // Pixel-art key shape
        ctx.fillStyle = '#fff';
        this._rect(keyX + 2, keyY + 2, 4, 4, '#fff');  // key head
        this._rect(keyX + 6, keyY + 4, 4, 2, '#fff');   // key shaft
        this._rect(keyX + 8, keyY + 5, 2, 3, '#fff');   // key tooth
        ctx.globalAlpha = 1;
      } else if (data.api_online === false) {
        // Red disconnect icon — flashing
        const offX = bx + 18, offY = by - 12;
        const alpha = 0.5 + Math.sin(this.animFrame * 0.1) * 0.4;
        ctx.globalAlpha = alpha;
        this._rect(offX, offY, 12, 10, '#ff3344');
        // White X disconnect symbol
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(offX + 3, offY + 2);
        ctx.lineTo(offX + 9, offY + 8);
        ctx.moveTo(offX + 9, offY + 2);
        ctx.lineTo(offX + 3, offY + 8);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }

    // Name tag — show nickname if available, with level
    ctx.fillStyle = labelColor;
    ctx.font = '8px monospace';
    ctx.textAlign = 'center';
    const displayName = data.nickname || (data.name || data.role || '').substring(0, 8);
    const lvlTag = data.level ? ` L${data.level}` : '';
    ctx.fillText(displayName + lvlTag, px + 12, gy * TILE + 36);
    ctx.textAlign = 'left';
  }

  _hashStr(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  // ===== Tool/Equipment =====
  drawToolEquipment(gx, gy, toolData) {
    const px = gx * TILE;
    const py = gy * TILE;

    // If tool has no icon, skip rendering entirely
    if (!toolData.has_icon) return;

    const icon = this._toolIcons && this._toolIcons[toolData.id];
    if (icon) {
      // Draw icon scaled to fit within TILE x TILE, centered
      const maxW = TILE;
      const maxH = TILE;
      const scale = Math.min(maxW / icon.width, maxH / icon.height);
      const w = Math.round(icon.width * scale);
      const h = Math.round(icon.height * scale);
      const ox = px + Math.round((TILE - w) / 2);
      const oy = py + Math.round((TILE - h) / 2);
      this.ctx.drawImage(icon, ox, oy, w, h);
    } else {
      // Icon still loading — draw placeholder
      this._rect(px + 8, py + 8, 16, 16, '#334455');
    }

    // Label below
    this.ctx.fillStyle = PALETTE.led1;
    this.ctx.font = '7px monospace';
    this.ctx.textAlign = 'center';
    const label = (toolData.name || 'TOOL').substring(0, 8).toUpperCase();
    this.ctx.fillText(label, px + 16, py + 36);
    this.ctx.textAlign = 'left';
  }

  // ===== Meeting Room =====
  drawMeetingRoom(gx, gy, roomData) {
    const ctx = this.ctx;
    const px = gx * TILE;
    const py = gy * TILE;

    // Room floor (slightly different color)
    this._rect(px - 4, py - 4, TILE * 2 + 8, TILE * 2 + 8, '#1e1e38');
    // Room border
    this._rect(px - 4, py - 4, TILE * 2 + 8, 2, '#3a3a66');
    this._rect(px - 4, py - 4, 2, TILE * 2 + 8, '#3a3a66');
    this._rect(px + TILE * 2 + 2, py - 4, 2, TILE * 2 + 8, '#3a3a66');
    this._rect(px - 4, py + TILE * 2 + 2, TILE * 2 + 8, 2, '#3a3a66');

    // Conference table (center)
    this._rect(px + 8, py + 12, TILE + 16, 20, PALETTE.meetingTable);
    this._rect(px + 10, py + 12, TILE + 12, 2, PALETTE.meetingTableLight);

    // Chairs around table
    const chairPositions = [
      [px + 4, py + 8],   [px + 22, py + 8],   [px + 40, py + 8],
      [px + 4, py + 34],  [px + 22, py + 34],  [px + 40, py + 34],
    ];
    const numChairs = Math.min(roomData.capacity || 6, chairPositions.length);
    for (let i = 0; i < numChairs; i++) {
      const [cx, cy] = chairPositions[i];
      this._rect(cx, cy, 10, 8, PALETTE.meetingChair);
    }

    // Status indicator (booked = red glow, free = green LED)
    const statusColor = roomData.is_booked ? PALETTE.meetingBooked : PALETTE.meetingFree;
    const glowAlpha = roomData.is_booked
      ? Math.sin(this.animFrame * 0.08) * 0.3 + 0.5
      : 0.8;
    ctx.globalAlpha = glowAlpha;
    this._rect(px + TILE - 2, py - 2, 6, 6, statusColor);
    ctx.globalAlpha = 1;

    // Participants (small colored dots on chairs if booked)
    if (roomData.is_booked && roomData.participants) {
      for (let i = 0; i < Math.min(roomData.participants.length, numChairs); i++) {
        const [cx, cy] = chairPositions[i];
        const pHash = this._hashStr(roomData.participants[i] || '');
        const pColor = PALETTE.shirt[pHash % PALETTE.shirt.length];
        this._rect(cx + 2, cy - 4, 6, 6, pColor);
        this._rect(cx + 3, cy - 8, 4, 5, PALETTE.skin[pHash % PALETTE.skin.length]);
      }
    }

    // Label
    ctx.fillStyle = statusColor;
    ctx.font = '7px monospace';
    ctx.textAlign = 'center';
    const label = (roomData.name || 'Meeting Room').substring(0, 8);
    ctx.fillText(label, px + TILE, py + TILE * 2 + 12);
    if (roomData.is_booked) {
      ctx.fillStyle = PALETTE.meetingBooked;
      ctx.fillText('In Use', px + TILE, py + TILE * 2 + 20);
    }
    ctx.textAlign = 'left';
  }

  // ===== Tooltip =====
  _updateTooltip() {
    if (!this.hoverTile) return;
    const { x, y, screenX, screenY } = this.hoverTile;

    let tooltipText = null;

    // Check bulletin board (tiles 5-7, row 0-1)
    if (x >= 5 && x <= 7 && y <= 1) {
      tooltipText = '📋 Company Rules\nClick to view and edit workflows';
    }

    if (x >= 12 && x <= 14 && y <= 1) {
      tooltipText = '📋 Project Wall\nClick to view project history';
    }

    // Check CEO (executive row, center)
    const ceoCanvasRow = ((this.state.office_layout || {}).executive_row || 0) + 3;
    if (x === 9 && (y === ceoCanvasRow - 1 || y === ceoCanvasRow || y === ceoCanvasRow + 1)) {
      tooltipText = 'CEO (You)\nRole: Chief Executive\nInput tasks below';
    }

    // Check employees
    const LEVEL_NAMES = {1: 'Junior', 2: 'Mid', 3: 'Senior', 4: 'Founding', 5: 'CEO'};
    for (const emp of this.state.employees) {
      const [ex, ey] = emp.desk_position || [0, 0];
      if (x === ex && (y === ey + 2 || y === ey + 3 || y === ey + 4)) {
        const nn = emp.nickname ? ` (${emp.nickname})` : '';
        const lvl = LEVEL_NAMES[emp.level] || `Lv.${emp.level}`;
        const title = emp.title || `${lvl}${emp.role}`;
        const hist = emp.performance_history || [];
        const latestScore = hist.length > 0 ? hist[hist.length - 1].score : '-';
        tooltipText = `${emp.name}${nn}\n${title}\nSkills: ${(emp.skills || []).join(', ')}\nPerformance: ${latestScore}`;
        if (emp.needs_setup) {
          tooltipText += '\n🔑 Needs API setup';
        } else if (emp.api_online === false) {
          tooltipText += '\n🔴 API offline';
        }
        if (emp.is_listening) {
          tooltipText += '\n📖 In 1-on-1 meeting...';
        }
        tooltipText += '\n\n(Click for details)';
        break;
      }
    }

    // Check tools (hit area: tool tile + label tile below, only tools with icons)
    for (const tool of this.state.tools) {
      if (!tool.has_icon) continue;
      const [tx, ty] = tool.desk_position || [0, 0];
      const canvasY = ty + 3;
      if (x === tx && y >= canvasY && y <= canvasY + 1) {
        tooltipText = `🔧 ${tool.name}`;
        if (tool.description) tooltipText += `\n${tool.description}`;
        break;
      }
    }

    // Check meeting rooms
    for (const room of (this.state.meeting_rooms || [])) {
      const [rx, ry] = room.position || [0, 0];
      if (x >= rx && x <= rx + 1 && y >= ry + 3 && y <= ry + 5) {
        const status = room.is_booked ? '🔴 In Use' : '🟢 Available';
        tooltipText = `🏢 ${room.name}\n${room.description}\nCapacity: ${room.capacity}\nStatus: ${status}`;
        if (room.is_booked && room.participants && room.participants.length > 0) {
          tooltipText += `\nParticipants: ${room.participants.join(', ')}`;
        }
        break;
      }
    }

    const tooltip = document.getElementById('tooltip');
    if (tooltipText) {
      tooltip.textContent = tooltipText;
      tooltip.style.left = (screenX - this.canvas.parentElement.getBoundingClientRect().left + 12) + 'px';
      tooltip.style.top = (screenY - this.canvas.parentElement.getBoundingClientRect().top - 8) + 'px';
      tooltip.classList.remove('hidden');
    } else {
      tooltip.classList.add('hidden');
    }
  }

  // ===== Main render =====
  render() {
    const ctx = this.ctx;
    // Clear using logical dimensions (the context is already scaled by dpr)
    ctx.clearRect(0, 0, COLS * TILE, ROWS * TILE);

    // Environment
    this.drawFloor();
    this.drawWalls();
    this.drawBulletinBoard();
    this.drawProjectWall();
    this.drawPlants();
    this.drawDepartmentLabels();

    // Build set of employees currently in a meeting room
    const inMeeting = {};  // emp_id -> room position
    for (const room of (this.state.meeting_rooms || [])) {
      if (room.is_booked && room.participants) {
        const [rx, ry] = room.position || [0, 0];
        for (let i = 0; i < room.participants.length; i++) {
          inMeeting[room.participants[i]] = {
            x: rx + (i % 3),
            y: ry + 3 + Math.floor(i / 3),
          };
        }
      }
    }

    // CEO desk (executive row center)
    const execRowCanvas = ((this.state.office_layout || {}).executive_row || 0) + 3;
    this.drawDesk(9, execRowCanvas, true);
    if (!inMeeting['ceo']) {
      this.drawCharacter(9, execRowCanvas, { id: 'ceo_boss', name: 'CEO', role: 'CEO' }, true);
    }

    // AI Employees — draw desk always, avatar at desk OR meeting room
    for (const emp of this.state.employees) {
      if (emp.remote) continue;  // remote employees are not rendered in the office
      const [gx, gy] = emp.desk_position || [0, 0];
      this.drawDesk(gx, gy + 3, true);

      if (inMeeting[emp.id]) {
        // Draw small avatar at meeting room position
        const pos = inMeeting[emp.id];
        this.drawCharacter(pos.x, pos.y, emp);
      } else {
        // Draw at desk
        this.drawCharacter(gx, gy + 3, emp);
      }
    }

    // Tools/Equipment (only render tools with icons)
    for (const tool of this.state.tools) {
      if (!tool.has_icon) continue;
      const [gx, gy] = tool.desk_position || [0, 0];
      this.drawToolEquipment(gx, gy + 3, tool);
    }

    // Meeting Rooms
    for (const room of (this.state.meeting_rooms || [])) {
      const [gx, gy] = room.position || [0, 0];
      this.drawMeetingRoom(gx, gy + 3, room);
    }

    // Draw CEO avatar in meeting room if applicable
    if (inMeeting['ceo']) {
      const pos = inMeeting['ceo'];
      this.drawCharacter(pos.x, pos.y, { id: 'ceo_boss', name: 'CEO', role: 'CEO' }, true);
    }

    // Particles
    this._updateParticles();
    this._drawParticles();

    // Tooltip
    this._updateTooltip();

    // Scanline effect (subtle)
    ctx.globalAlpha = 0.03;
    for (let y = 0; y < ROWS * TILE; y += 2) {
      this._rect(0, y, COLS * TILE, 1, '#000');
    }
    ctx.globalAlpha = 1;
  }

  loop() {
    this.animFrame++;
    this.render();
    requestAnimationFrame(() => this.loop());
  }
}

// Draw CEO avatar in the small canvas
function drawCEOAvatar() {
  const c = document.getElementById('ceo-avatar');
  if (!c) return;
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;

  // Background
  ctx.fillStyle = '#1a1a33';
  ctx.fillRect(0, 0, 48, 48);

  // Body
  ctx.fillStyle = '#ffd700';
  ctx.fillRect(14, 28, 20, 16);

  // Head
  ctx.fillStyle = '#f0c080';
  ctx.fillRect(16, 14, 16, 14);

  // Hair
  ctx.fillStyle = '#2a2a2a';
  ctx.fillRect(15, 10, 18, 6);

  // Crown
  ctx.fillStyle = '#ffd700';
  ctx.fillRect(15, 6, 18, 5);
  ctx.fillRect(15, 3, 4, 4);
  ctx.fillRect(21, 2, 4, 4);
  ctx.fillRect(28, 3, 4, 4);

  // Eyes
  ctx.fillStyle = '#111';
  ctx.fillRect(20, 20, 3, 3);
  ctx.fillRect(26, 20, 3, 3);
  ctx.fillRect(21, 20, 1, 1);
  ctx.fillRect(27, 20, 1, 1);

  // Smile
  ctx.fillRect(22, 25, 5, 1);
}

// Initialize
window.officeRenderer = new OfficeRenderer('office-canvas');
drawCEOAvatar();
