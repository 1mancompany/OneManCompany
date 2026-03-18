/**
 * office-tileatlas.js — Spritesheet loader and tile renderer
 *
 * All tilesets use 32×32 pixel tiles. Tile coordinates are (row, col) from
 * the top-left corner of each spritesheet.
 *
 * Sheets:
 *   'gen'        → generated/generated_tiles_32x32.png    (16×16 tiles, custom pixel art)
 *   'char01'..'char20' → Premade_Character_32x32_XX.png   (56×41 tiles each)
 *
 * Generated tileset layout (gen):
 *   r0-r1:  Wall A (warm beige) — top + baseboard
 *   r2-r3:  Wall B (cool gray)  — top + baseboard
 *   r4:     Floor — light oak wood planks
 *   r5:     Floor — dark walnut wood planks
 *   r6:     Floor — gray stone tiles
 *   r7:     Floor — checkered cream/brown
 *   r8:     Floor — red brick
 *   r9:     Floor — herringbone wood
 *   r10:    Floor — teal decorative
 *   r11:    Floor — exec carpet (gold)
 *   r12:    Desk fronts c0-4, desk tops c5-9
 *   r13:    Monitors c0-2, printer c3 | whiteboard tl(c10) tr(c11) | bookshelf tl(c12) tr(c13)
 *   r14:    Chairs c0-3, filing(c4), plants(c5-6) | whiteboard bl(c10) br(c11) | bookshelf bl(c12) br(c13)
 *   r15:    Conf table tl(c0) tr(c1) bl(c2) br(c3), conf chairs c4-5
 *
 * Character animation rows (from Spritesheet_animations_GUIDE.png):
 *   Row 0-1: 3-frame preview
 *   Row 2-3: idle (24 cols: Right 0-5, Up 6-11, Left 12-17, Down/Front 18-23)
 *   Row 4-5: walk (same direction layout)
 *   Row 6-7: sit (variant 1)
 *   Row 8-9: sit (variant 2)
 */

const TILE_SIZE = 32;

// Sheet paths relative to /assets/office/tilesets/
const SHEET_PATHS = {
  gen:      'generated/generated_tiles_32x32.png',
  office:   'Modern_Office_Revamped_v1.2/Modern_Office_32x32.png',
  room_free: 'Modern%20tiles_Free/Interiors_free/32x32/Room_Builder_free_32x32.png',
};

// Character sheet paths (char01 – char20)
for (let i = 1; i <= 20; i++) {
  const key = `char${String(i).padStart(2, '0')}`;
  const num = String(i).padStart(2, '0');
  SHEET_PATHS[key] =
    `moderninteriors-win/2_Characters/Character_Generator/0_Premade_Characters/32x32/Premade_Character_32x32_${num}.png`;
}

/**
 * Tile definitions — all environment tiles from generated sheet ('gen').
 * Format: [sheetKey, srcRow, srcCol]  or  [sheetKey, srcRow, srcCol, wTiles, hTiles]
 */
const TILE_DEFS = {
  // ── Floor tiles ──
  floor_gray:         ['gen',  6, 0],  // gray stone tiles
  floor_gray_dark:    ['gen',  6, 1],  // gray stone (variant)
  floor_brown:        ['gen',  4, 0],  // light oak wood
  floor_brown_dark:   ['gen',  5, 0],  // dark walnut wood
  floor_brick_red:    ['gen',  8, 0],  // red brick
  floor_check_yellow: ['gen',  7, 0],  // checkered cream/brown
  floor_teal:         ['gen', 10, 0],  // teal decorative
  floor_herringbone:  ['gen',  9, 0],  // herringbone wood
  floor_wood_gold:    ['gen', 11, 0],  // exec carpet (gold)
  floor_stone_gray:   ['gen',  6, 0],  // gray stone (default)
  floor_wood_warm:    ['gen',  4, 1],  // warm wood (task board bg)
  floor_stone_blue:   ['gen',  6, 2],  // stone variant (project wall bg)

  // ── Meeting room ──
  meeting_floor:  ['gen', 15, 10],   // blue-gray diamond carpet
  meeting_wall:   ['gen', 15, 11],   // dark wood partition panel

  // ── Department floor tiles (office sheet r4, c10-c13) ──
  dept_floor_0:  ['office', 4, 10],   // dark crosshatch
  dept_floor_1:  ['office', 4, 11],   // red/brown pattern
  dept_floor_2:  ['office', 4, 12],   // gray checkered
  dept_floor_3:  ['office', 4, 13],   // dark gray

  // ── Wall tiles ──
  wall_top:            ['gen', 0, 0],  // warm wall (upper)
  wall_mid:            ['gen', 1, 0],  // warm wall (baseboard)
  wall_bottom:         ['gen', 1, 0],  // baseboard
  wall_window_top:     ['gen', 2, 0],  // cool wall (upper)
  wall_window_bottom:  ['gen', 3, 0],  // cool wall (baseboard)

  // ── L-shaped desk (office sheet r47-r48, c5-c6 = 2×2 tiles) ──
  desk_l_tl:  ['office', 47, 5],  // top-left (desk surface)
  desk_l_tr:  ['office', 47, 6],  // top-right (L extension surface)
  desk_l_bl:  ['office', 48, 5],  // bottom-left (desk front)
  desk_l_br:  ['office', 48, 6],  // bottom-right (L extension front)

  // ── Computer / monitor (office sheet r8-r9, c14 = 1×2 tiles) ──
  computer_top:    ['office', 8, 14],   // monitor
  computer_bottom: ['office', 9, 14],   // keyboard area

  // ── Generated desk fronts (kept as fallback / variety) ──
  desk_wood_light:   ['gen', 12, 0],
  desk_wood_dark:    ['gen', 12, 1],
  desk_wood_orange:  ['gen', 12, 2],
  desk_wood_two:     ['gen', 12, 3],
  desk_wood_brown:   ['gen', 12, 4],
  desk_top_surface:  ['gen', 12, 5],

  // ── Monitors & equipment ──
  monitor_single:  ['gen', 13, 0],
  monitor_dual:    ['gen', 13, 2],
  printer:         ['gen', 13, 3],
  whiteboard:      ['gen', 13, 10, 2, 2],
  bookshelf:       ['gen', 13, 12, 2, 2],

  // ── Office chairs (office sheet, 1×2 tiles each) ──
  office_chair_top:     ['office',  8, 2],   // regular chair upper
  office_chair_bottom:  ['office',  9, 2],   // regular chair lower
  ceo_chair_top:        ['office', 10, 2],   // CEO chair upper
  ceo_chair_bottom:     ['office', 11, 2],   // CEO chair lower

  // ── Desk clutter / files (office sheet) ──
  desk_files:  ['office', 9, 10],

  // ── Generated chairs & furniture (kept for other uses) ──
  chair_black:     ['gen', 14, 0],
  chair_blue:      ['gen', 14, 1],
  chair_gold:      ['gen', 14, 2],
  chair_red:       ['gen', 14, 3],
  filing_cabinet:  ['gen', 14, 4],
  plant_large:     ['gen', 14, 5],
  plant_small:     ['gen', 14, 6],

  // ── Conference / meeting room ──
  conf_table_tl:     ['gen', 15, 0],
  conf_table_tr:     ['gen', 15, 1],
  conf_table_bl:     ['gen', 15, 2],
  conf_table_br:     ['gen', 15, 3],
  conf_chair_top:    ['gen', 15, 4],
  conf_chair_bottom: ['gen', 15, 5],

  // ── CEO rug ──
  ceo_rug: ['office', 14, 12],

  // ── Office border (Room Builder Free sheet) ──
  border_wall:   ['room_free', 17, 0],
  border_corner: ['room_free', 0, 12],   // dark solid corner tile
};

class TileAtlas {
  constructor(basePath = '/assets/office/tilesets') {
    this._basePath = basePath;
    this._images  = {};   // sheetKey → HTMLImageElement
    this._ready   = {};   // sheetKey → true (attempted load)
    this._loading = {};   // sheetKey → Promise
  }

  /**
   * Preload a list of sheet keys. Returns a Promise that resolves when all done.
   */
  preload(keys) {
    return Promise.allSettled(keys.map(k => this._loadSheet(k)));
  }

  _loadSheet(key) {
    if (this._ready[key]) return Promise.resolve();
    if (this._loading[key]) return this._loading[key];

    const path = SHEET_PATHS[key];
    if (!path) {
      console.warn(`[TileAtlas] Unknown sheet key: ${key}`);
      this._ready[key] = true;
      return Promise.resolve();
    }

    this._loading[key] = new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        this._images[key] = img;
        this._ready[key] = true;
        resolve();
      };
      img.onerror = () => {
        console.warn(`[TileAtlas] Failed to load: ${this._basePath}/${path}`);
        this._ready[key] = true;
        resolve();
      };
      img.src = `${this._basePath}/${path}`;
    });

    return this._loading[key];
  }

  /**
   * Draw a tile onto ctx at pixel position.
   * @param {CanvasRenderingContext2D} ctx
   * @param {string} sheetKey
   * @param {number} srcRow     — row in tileset (0-indexed)
   * @param {number} srcCol     — col in tileset (0-indexed)
   * @param {number} destX      — canvas X in pixels (world space)
   * @param {number} destY      — canvas Y in pixels (world space)
   * @param {number} [wTiles=1] — width in tiles
   * @param {number} [hTiles=1] — height in tiles
   */
  drawTile(ctx, sheetKey, srcRow, srcCol, destX, destY, wTiles = 1, hTiles = 1) {
    const img = this._images[sheetKey];
    if (!img) return;  // not loaded yet — skip silently

    ctx.drawImage(
      img,
      srcCol * TILE_SIZE,         // source X
      srcRow * TILE_SIZE,         // source Y
      TILE_SIZE * wTiles,         // source width
      TILE_SIZE * hTiles,         // source height
      Math.round(destX),          // dest X
      Math.round(destY),          // dest Y
      TILE_SIZE * wTiles,         // dest width (1:1, no scaling)
      TILE_SIZE * hTiles,         // dest height
    );
  }

  /**
   * Draw a named tile def at pixel position.
   * @param {CanvasRenderingContext2D} ctx
   * @param {string} defKey          — key in TILE_DEFS
   * @param {number} destX
   * @param {number} destY
   * @param {string} [sheetOverride] — override sheet key (used for character sheets)
   */
  drawDef(ctx, defKey, destX, destY, sheetOverride = null) {
    const def = TILE_DEFS[defKey];
    if (!def) {
      console.warn(`[TileAtlas] Unknown def: ${defKey}`);
      return;
    }
    const [sheet, row, col, w = 1, h = 1] = def;
    this.drawTile(ctx, sheetOverride || sheet, row, col, destX, destY, w, h);
  }

  /**
   * Draw a tile with rotation and/or horizontal flip.
   * @param {CanvasRenderingContext2D} ctx
   * @param {string} sheetKey
   * @param {number} srcRow
   * @param {number} srcCol
   * @param {number} destX
   * @param {number} destY
   * @param {number} [angle=0]   — rotation in radians (around tile center)
   * @param {boolean} [flipH=false] — mirror horizontally
   */
  drawTileTransformed(ctx, sheetKey, srcRow, srcCol, destX, destY, angle = 0, flipH = false) {
    const img = this._images[sheetKey];
    if (!img) return;

    const cx = Math.round(destX) + TILE_SIZE / 2;
    const cy = Math.round(destY) + TILE_SIZE / 2;
    ctx.save();
    ctx.translate(cx, cy);
    if (angle) ctx.rotate(angle);
    if (flipH) ctx.scale(-1, 1);
    ctx.drawImage(
      img,
      srcCol * TILE_SIZE, srcRow * TILE_SIZE,
      TILE_SIZE, TILE_SIZE,
      -TILE_SIZE / 2, -TILE_SIZE / 2,
      TILE_SIZE, TILE_SIZE,
    );
    ctx.restore();
  }

  /**
   * Draw a named tile def with rotation/flip.
   */
  drawDefTransformed(ctx, defKey, destX, destY, angle = 0, flipH = false) {
    const def = TILE_DEFS[defKey];
    if (!def) return;
    const [sheet, row, col] = def;
    this.drawTileTransformed(ctx, sheet, row, col, destX, destY, angle, flipH);
  }

  isReady(key) {
    return !!this._ready[key] && !!this._images[key];
  }
}

// ── Debug helper (only available when window.OMC_DEBUG is truthy) ────────────
// Usage: debugSheet('office', 0, 10)  — shows rows 0-10 of the office sheet with grid
if (window.OMC_DEBUG) window.debugSheet = function(sheetKey, startRow = 0, endRow = 5) {
  const img = tileAtlas._images[sheetKey];
  if (!img) { console.log('Not loaded:', sheetKey); return; }
  const c = document.createElement('canvas');
  c.width = img.width; c.height = (endRow - startRow + 1) * TILE_SIZE;
  document.body.appendChild(c);
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 0, startRow * TILE_SIZE, img.width, c.height, 0, 0, img.width, c.height);
  ctx.strokeStyle = 'rgba(255,0,0,0.6)';
  ctx.lineWidth = 0.5;
  for (let r = 0; r <= (endRow - startRow); r++) {
    for (let col = 0; col < img.width / TILE_SIZE; col++) {
      ctx.strokeRect(col * TILE_SIZE, r * TILE_SIZE, TILE_SIZE, TILE_SIZE);
      ctx.fillStyle = 'red';
      ctx.font = '7px sans-serif';
      ctx.fillText(`${r + startRow},${col}`, col * TILE_SIZE + 2, r * TILE_SIZE + 9);
    }
  }
  c.style.cssText = 'position:fixed;top:0;left:0;z-index:9999;background:#111;overflow:auto;max-width:100vw;max-height:100vh;';
  c.title = 'Click to remove';
  c.onclick = () => c.remove();
};  // end if (window.OMC_DEBUG)

// Singleton
const tileAtlas = new TileAtlas();
