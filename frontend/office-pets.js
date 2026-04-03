/**
 * office-pets.js — PetRenderer for pixel-art pet sprites with lerp movement.
 *
 * Depends on (must be loaded first in index.html):
 *   office-tileatlas.js  → tileAtlas singleton, TILE_SIZE constant
 *   office-map.js        → WALL_ROWS
 *
 * Usage: window.PetRenderer is set at the bottom of this file.
 */

const PET_LERP_FACTOR = 0.08;

const PET_STATE_COLORS = {
  idle:     '#aaddff',
  walking:  '#aaffaa',
  sleeping: '#ccccff',
  eating:   '#ffddaa',
  playing:  '#ffaadd',
};

const FACILITY_TYPE_COLORS = {
  food_bowl: '#ddaa55',
  pet_bed:   '#8888cc',
  toy_ball:  '#dd6688',
};

const FACILITY_TYPE_ICONS = {
  food_bowl: '\u{1F356}',   // 🍖
  pet_bed:   '\u{1F6CF}',   // 🛏
  toy_ball:  '\u26BE',       // ⚾
};

class PetRenderer {
  /**
   * @param {object} tileAtlas — tile atlas singleton (for future sprite support)
   */
  constructor(tileAtlas) {
    this._tileAtlas = tileAtlas;
    this.pets = [];
    this.facilities = [];
    this.species = {};
    this._lerpState = {};   // pet_id → {x, y}
    this._animFrames = {};  // pet_id → frame counter
    this._enabled = false;
  }

  // ── Gate ──────────────────────────────────────────────────────────────────

  setEnabled(v) { this._enabled = !!v; }
  isEnabled()   { return this._enabled; }

  // ── State updates from API ───────────────────────────────────────────────

  /**
   * Receives {pets, facilities, species} from the pet system API.
   * Initializes lerp positions for new pets, cleans up removed ones.
   */
  updateState(data) {
    if (!data) return;

    this.pets       = data.pets       || [];
    this.facilities = data.facilities || [];
    this.species    = data.species    || {};

    // Track current pet IDs
    const currentIds = new Set(this.pets.map(p => p.id));

    // Initialize lerp state for new pets
    for (const pet of this.pets) {
      if (!this._lerpState[pet.id]) {
        this._lerpState[pet.id] = { x: pet.x, y: pet.y };
        this._animFrames[pet.id] = 0;
      }
    }

    // Clean up removed pets
    for (const id of Object.keys(this._lerpState)) {
      if (!currentIds.has(id)) {
        delete this._lerpState[id];
        delete this._animFrames[id];
      }
    }
  }

  // ── Per-frame tick ───────────────────────────────────────────────────────

  /**
   * Called each render frame. Lerps visual positions toward server positions
   * and increments animation frame counters.
   */
  tick(animFrame) {
    if (!this._enabled) return;

    for (const pet of this.pets) {
      const ls = this._lerpState[pet.id];
      if (!ls) continue;

      // Lerp toward server position
      ls.x += (pet.x - ls.x) * PET_LERP_FACTOR;
      ls.y += (pet.y - ls.y) * PET_LERP_FACTOR;

      // Increment animation frame
      this._animFrames[pet.id] = (this._animFrames[pet.id] || 0) + 1;
    }
  }

  // ── Entity list for Y-sort integration ───────────────────────────────────

  /**
   * Returns array of {type:'pet', pet, x, y, animFrame} for Y-sort in office.js.
   */
  getEntities() {
    if (!this._enabled) return [];

    const entities = [];
    for (const pet of this.pets) {
      const ls = this._lerpState[pet.id];
      if (!ls) continue;
      entities.push({
        type: 'pet',
        pet: pet,
        x: ls.x,
        y: ls.y,
        animFrame: this._animFrames[pet.id] || 0,
      });
    }
    return entities;
  }

  // ── Draw a single pet ────────────────────────────────────────────────────

  /**
   * Draws one pet entity on the canvas.
   * Fallback rendering: colored circle with species initial letter.
   *
   * @param {CanvasRenderingContext2D} ctx
   * @param {object} entity — from getEntities()
   * @param {number} TILE — tile size in px (32)
   * @param {number} WALL_ROWS — wall row offset (3)
   */
  drawPet(ctx, entity, TILE, WALL_ROWS) {
    const { pet, x, y, animFrame } = entity;
    const px = x * TILE;
    const py = (y + WALL_ROWS) * TILE;
    const cx = px + TILE / 2;
    const cy = py + TILE / 2;
    const radius = TILE * 0.35;

    const stateColor = PET_STATE_COLORS[pet.state] || PET_STATE_COLORS.idle;

    // ── Body: colored circle ──
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fillStyle = stateColor;
    ctx.fill();
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1;
    ctx.stroke();

    // ── Species initial letter ──
    const speciesInfo = this.species[pet.species] || {};
    const initial = (speciesInfo.name || pet.species || '?').charAt(0).toUpperCase();
    ctx.fillStyle = '#333';
    ctx.font = 'bold 10px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(initial, cx, cy);

    // ── State overlays ──
    this._drawStateOverlay(ctx, pet.state, cx, cy, radius, animFrame);

    // ── Name tag below pet ──
    const isOwned = !!pet.owner_id;
    ctx.fillStyle = isOwned ? '#44dd44' : '#ff8844';
    ctx.font = '8px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(pet.name || '???', cx, py + TILE - 2);
  }

  /**
   * Draw state-specific overlays (sleeping Z's, eating sparkle, playing heart).
   */
  _drawStateOverlay(ctx, state, cx, cy, radius, animFrame) {
    const t = animFrame * 0.06;

    if (state === 'sleeping') {
      // Floating Z's
      for (let i = 0; i < 3; i++) {
        const zOff = ((t + i * 1.2) % 3);
        const zx = cx + radius + 2 + i * 3;
        const zy = cy - radius - zOff * 6;
        const alpha = Math.max(0, 1 - zOff / 3);
        ctx.globalAlpha = alpha;
        ctx.fillStyle = '#8888cc';
        ctx.font = `${7 + i * 2}px monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('z', zx, zy);
      }
      ctx.globalAlpha = 1;
    } else if (state === 'eating') {
      // Sparkle effect
      const sparkleAlpha = (Math.sin(t * 3) + 1) * 0.4 + 0.2;
      ctx.globalAlpha = sparkleAlpha;
      ctx.fillStyle = '#ffdd44';
      ctx.font = '8px monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('\u2728', cx + radius + 4, cy - radius);  // ✨
      ctx.globalAlpha = 1;
    } else if (state === 'playing') {
      // Floating heart
      const heartY = cy - radius - 4 + Math.sin(t * 2) * 3;
      ctx.fillStyle = '#ff6688';
      ctx.font = '9px monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('\u2764', cx, heartY);  // ❤
    }
  }

  // ── Draw facilities ──────────────────────────────────────────────────────

  /**
   * Draws all pet facilities on the office floor.
   *
   * @param {CanvasRenderingContext2D} ctx
   * @param {number} TILE
   * @param {number} WALL_ROWS
   * @param {number} animFrame — global animation frame counter
   */
  drawFacilities(ctx, TILE, WALL_ROWS, animFrame) {
    if (!this._enabled) return;

    for (const fac of this.facilities) {
      const px = fac.x * TILE;
      const py = (fac.y + WALL_ROWS) * TILE;
      const size = TILE * 0.7;
      const offset = (TILE - size) / 2;

      const color = FACILITY_TYPE_COLORS[fac.type] || '#888888';

      // ── Colored square background ──
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.6;
      ctx.fillRect(px + offset, py + offset, size, size);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = '#444';
      ctx.lineWidth = 1;
      ctx.strokeRect(px + offset, py + offset, size, size);

      // ── Emoji icon ──
      const icon = FACILITY_TYPE_ICONS[fac.type] || '?';
      ctx.font = '14px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#fff';
      ctx.fillText(icon, px + TILE / 2, py + TILE / 2);
    }
  }

  // ── Hit test ─────────────────────────────────────────────────────────────

  /**
   * Returns pet object if click tile matches any pet's rounded lerp position.
   *
   * @param {number} tx — tile X coordinate
   * @param {number} ty — tile Y coordinate (in canvas-row space, i.e. already includes WALL_ROWS)
   * @param {number} WALL_ROWS
   * @returns {object|null} pet data or null
   */
  hitTest(tx, ty, WALL_ROWS) {
    if (!this._enabled) return null;

    for (const pet of this.pets) {
      const ls = this._lerpState[pet.id];
      if (!ls) continue;
      const petTX = Math.round(ls.x);
      const petTY = Math.round(ls.y) + WALL_ROWS;
      if (tx === petTX && ty === petTY) {
        return pet;
      }
    }
    return null;
  }

  // ── Tooltip ──────────────────────────────────────────────────────────────

  /**
   * Returns tooltip string: "名字 (种类) — 状态 [流浪]"
   *
   * @param {object} pet
   * @returns {string}
   */
  tooltipText(pet) {
    const speciesInfo = this.species[pet.species] || {};
    const speciesName = speciesInfo.name || pet.species || '???';
    const stateLabel  = pet.state || 'idle';
    const strayTag    = pet.owner_id ? '' : ' [流浪]';
    return `${pet.name} (${speciesName}) \u2014 ${stateLabel}${strayTag}`;
  }
}

// ── Export as global ─────────────────────────────────────────────────────────
window.PetRenderer = PetRenderer;
