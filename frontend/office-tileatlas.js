/**
 * office-tileatlas.js — Spritesheet loader and tile renderer
 *
 * All tilesets use 32×32 pixel tiles. Tile coordinates are (row, col) from
 * the top-left corner of each spritesheet.
 *
 * Sheets available:
 *   'office'     → Modern_Office_32x32.png                (16×53 tiles)
 *   'room'       → Room_Builder_Office_32x32.png          (16×14 tiles)
 *   'interiors'  → Interiors_32x32.png                    (16×1064 tiles)
 *   'char01'..'char20' → Premade_Character_32x32_XX.png  (56×41 tiles each)
 *
 * Character animation rows (from Spritesheet_animations_GUIDE.png):
 *   Row 0-1: idle (face down, 2-frame loop)
 *   Row 2-3: walk (face down)
 *   Row 4-5: sit (variant 1)
 *   Row 6-7: sit (variant 2)
 *
 * TILE_DEFS coordinates are approximate and must be visually calibrated in Task 8.
 */

const TILE_SIZE = 32;

// Sheet paths relative to /assets/office/tilesets/
const SHEET_PATHS = {
  office:         'Modern_Office_Revamped_v1.2/Modern_Office_32x32.png',
  room:           'Modern_Office_Revamped_v1.2/1_Room_Builder_Office/Room_Builder_Office_32x32.png',
  interiors:      'moderninteriors-win/1_Interiors/32x32/Interiors_32x32.png',
  interiors_room: 'moderninteriors-win/1_Interiors/32x32/Room_Builder_32x32.png',
};

// Character sheet paths (char01 – char20)
for (let i = 1; i <= 20; i++) {
  const key = `char${String(i).padStart(2, '0')}`;
  const num = String(i).padStart(2, '0');
  SHEET_PATHS[key] =
    `moderninteriors-win/2_Characters/Character_Generator/0_Premade_Characters/32x32/Premade_Character_32x32_${num}.png`;
}

/**
 * Tile definitions for semantic access.
 * Format: [sheetKey, srcRow, srcCol]  or  [sheetKey, srcRow, srcCol, widthTiles, heightTiles]
 *
 * ⚠️  COORDINATES ARE APPROXIMATE — must be verified visually in Task 8
 *     Use debugSheet() in browser console to inspect tile positions.
 */
const TILE_DEFS = {
  // ── Floor tiles (from Room_Builder_Office_32x32.png) ──
  // Row 0-1: wall top areas; rows 2+ are floor variants
  floor_stone_gray:  ['room',  2,  0],
  floor_stone_blue:  ['room',  2,  4],
  floor_wood_warm:   ['room',  4,  0],
  floor_tile_green:  ['room',  6,  0],
  floor_carpet_red:  ['room',  8,  0],
  floor_wood_gold:   ['room',  4,  4],

  // ── Potted plants (from Interiors_32x32.png) ──
  // Plant sprites are in the upper portion of the massive Interiors sheet
  // These rows/cols are approximate — verify in Task 8
  plant_large:  ['interiors',  0,  8, 1, 2],  // tall potted plant (1×2 tiles)
  plant_small:  ['interiors',  2,  8, 1, 1],  // small potted plant (1×1 tile)
  plant_round:  ['interiors',  2, 10, 1, 1],  // round bush pot

  // ── Office furniture (from Modern_Office_32x32.png) ──
  // Desk unit: occupies 2 tiles wide, 2 tall
  desk_top_l:      ['office',  0,  0],
  desk_top_r:      ['office',  0,  1],
  desk_front_l:    ['office',  1,  0],
  desk_front_r:    ['office',  1,  1],
  monitor_single:  ['office',  0,  4],
  monitor_dual:    ['office',  0,  5],
  chair_black:     ['office',  2,  0],
  chair_blue:      ['office',  2,  2],
  whiteboard:      ['office',  3,  8, 2, 2],
  bookshelf:       ['office',  6,  0, 2, 2],
  printer:         ['office',  4,  6],

  // ── Conference / meeting room furniture ──
  conf_table_tl:     ['office', 10,  0],
  conf_table_tr:     ['office', 10,  1],
  conf_table_bl:     ['office', 11,  0],
  conf_table_br:     ['office', 11,  1],
  conf_chair_top:    ['office',  9,  0],
  conf_chair_bottom: ['office', 12,  0],
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

  isReady(key) {
    return !!this._ready[key] && !!this._images[key];
  }
}

// ── Debug helper (call from browser console) ────────────────────────────────
// Usage: debugSheet('office', 0, 10)  — shows rows 0-10 of the office sheet with grid
window.debugSheet = function(sheetKey, startRow = 0, endRow = 5) {
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
};

// Singleton
const tileAtlas = new TileAtlas();
