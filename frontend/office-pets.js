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

// ── Sprite constants ────────────────────────────────────────────────────────
const _SPRITE_SIZE = 24;

// ── PRNG (mulberry32) — deterministic sprite generation from pet ID seed ────
function _petPrng(s) {
  return () => {
    s |= 0;
    s = s + 0x6D2B79F5 | 0;
    let t = Math.imul(s ^ s >>> 15, 1 | s);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

function _petHashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return h;
}

// ── Templates: 24×24 bitmap string arrays ───────────────────────────────────
// 0=transparent 1=outline 2=body 3=shadow 4=belly 5=eye_color 6=nose 7=inner_ear/cheek
// 8=whisker 9=pupil A=eye_white/highlight B=detail C=tongue(dog) D=collar

const CAT_T = [
  '000100000000000000100000', // 0  ear tips
  '001210000000000001210000', // 1
  '001221000000000012210000', // 2  ears taper
  '012272100000000127221000', // 3  inner ear
  '012222211111112222210000', // 4  head top wide
  '012222222222222222210000', // 5  head
  '012225A922222295A2210000', // 6  almond eyes
  '012222592222229522210000', // 7
  '801222222266222222108000', // 8  whiskers + nose
  '080122222B22B222210800B0', // 9  whiskers + mouth line
  '000012222222222210000000', // 10 narrow chin
  '000001222222222100000000', // 11 thin neck
  '000001222222222100000000', // 12
  '000012222222222210000000', // 13 body starts
  '000122224444422221000000', // 14 slim body + belly
  '000122224444422221000000', // 15
  '000122224444422221000000', // 16
  '000012222222222210000000', // 17 body tapers
  '000001222222222100000000', // 18
  '000013100001312200000000', // 19 thin legs + tail starts
  '000013100001300220000000', // 20           tail
  '000014100001300022000000', // 21 paws      tail curves
  '000000000000000002200000', // 22            tail tip
  '000000000000000000200000', // 23
];

const DOG_FLOP = [
  '000000011111100000000000', // 0  round dome top
  '000001222222210000000000', // 1
  '000012222222221000000000', // 2  round head
  '033012222222222103300000', // 3  floppy ears start from sides
  '033012222222222103300000', // 4  ears hang down
  '033122A5922295A2213300B0', // 5  round eyes with whites
  '033122259222952221330000', // 6
  '033012244444422103300000', // 7  wide snout/muzzle
  '033001244466442100330000', // 8  big square nose
  '003001224C4C422100300000', // 9  mouth + optional tongue
  '000001222442221000000000', // 10 wide jaw
  '00000122DDDD2210000000B0', // 11 collar
  '000012222222222100000000', // 12 thick neck
  '000122222222222210000000', // 13 wide chest
  '001222224444422222100000', // 14 stocky body
  '012222224444422222210000', // 15 widest
  '012222224444422222210000', // 16
  '012222224444422222210000', // 17
  '001222222222222222100000', // 18
  '000132210000012231000000', // 19 thick legs
  '000132210000012231000000', // 20
  '000132210000012231000000', // 21
  '000133310000013331000000', // 22 big paws
  '000000000000000000000000', // 23
];

const DOG_POINT = [
  '000120000000000021000000', // 0  pointy ears up top
  '001220000000000022100000', // 1  ears
  '012272011111101272100000', // 2  ears + head top
  '012222222222222222100000', // 3
  '012222222222222222100000', // 4
  '01222A5922229952A2100000', // 5  eyes
  '012222592222952221000000', // 6
  '001224444444444221000000', // 7  wide muzzle
  '001222444466444221000000', // 8  big nose
  '000012244C4C442100000000', // 9  mouth
  '000012222442222100000000', // 10 jaw
  '00001222DDDD2221000000B0', // 11 collar
  '000012222222222100000000', // 12 neck
  '000122222222222210000000', // 13 wide chest
  '001222224444422222100000', // 14 body
  '012222224444422222210000', // 15
  '012222224444422222210000', // 16
  '012222224444422222210000', // 17
  '001222222222222222100000', // 18
  '000132210000012231000000', // 19 legs
  '000132210000012231000000', // 20
  '000132210000012231000000', // 21
  '000133310000013331000000', // 22 paws
  '000000000000000000000000', // 23
];

const HAM_T = [
  '000000000000000000000000', // 0
  '000001200000002100000000', // 1  tiny round ears
  '000012700000007210000000', // 2  inner ear
  '000012211111112210000000', // 3  head
  '000122222222222221000000', // 4
  '001222225A925A222210000B', // 5  big round eyes
  '001222225992592222100000', // 6
  '077222222266222222770000', // 7  cheeks start + nose
  '772222222B22B222227700B0', // 8  big cheeks
  '772222222222222222770000', // 9  max cheek width
  '772222222222222222770000', // 10
  '077222244444442222700000', // 11 round belly
  '012222244444442222210000', // 12
  '122222244444442222221000', // 13 widest body
  '122222244444442222221000', // 14
  '122222244444442222221000', // 15
  '122222244444442222221000', // 16
  '122222244444442222221000', // 17
  '012222222222222222210000', // 18
  '012222222222222222210000', // 19
  '001222222222222222100000', // 20
  '000122222222222221000000', // 21
  '000013310000013310000000', // 22 tiny legs
  '000014410000014410000000', // 23 tiny paws
];

// ── Palettes ────────────────────────────────────────────────────────────────
const CAT_P = [
  {name:'Orange Tabby', 2:[235,165,65],3:[190,120,35],4:[255,228,178],5:[70,205,92],6:[218,142,142],7:[222,162,158],9:[22,22,24],A:[240,240,242],B:[160,120,110]},
  {name:'Black',        2:[58,55,62],  3:[40,38,44],  4:[82,78,88],   5:[185,225,52],6:[182,132,132],7:[122,82,88],  9:[15,15,18],A:[240,240,242],B:[100,80,80]},
  {name:'White',        2:[244,242,238],3:[218,212,205],4:[254,252,248],5:[82,172,232],6:[222,158,158],7:[228,182,178],9:[22,22,24],A:[240,240,242],B:[180,160,155]},
  {name:'Gray Tabby',   2:[150,150,160],3:[110,110,120],4:[198,195,202],5:[78,192,212],6:[202,142,142],7:[198,158,152],9:[22,22,24],A:[240,240,242],B:[130,115,110]},
  {name:'Siamese',      2:[218,202,180],3:[102,78,58],  4:[238,228,210],5:[78,158,228],6:[198,132,132],7:[192,148,138],9:[22,22,24],A:[240,240,242],B:[140,110,100]},
  {name:'Calico',       2:[248,240,228],3:[212,128,58], 4:[254,250,242],5:[72,202,92], 6:[218,142,142],7:[222,168,158],9:[22,22,24],A:[240,240,242],B:[160,130,120]},
  {name:'Tuxedo',       2:[52,50,58],  3:[38,36,42],   4:[244,242,238],5:[72,202,92], 6:[202,142,142],7:[142,102,102],9:[18,18,20],A:[240,240,242],B:[110,90,85]},
  {name:'Ginger',       2:[220,148,60],3:[180,108,35],  4:[250,210,140],5:[228,192,52],6:[218,148,148],7:[218,162,152],9:[22,22,24],A:[240,240,242],B:[160,120,100]},
];

const DOG_P = [
  {name:'Golden',   2:[220,180,90], 3:[184,144,58],4:[250,228,170],5:[58,42,22], 6:[32,32,35],7:[222,172,158],9:[18,18,20],A:[242,242,244],B:[140,110,90],C:[228,115,115],D:[222,62,58]},
  {name:'Brown',    2:[160,110,65], 3:[120,78,44], 4:[208,178,132],5:[68,50,30], 6:[32,32,35],7:[188,138,118],9:[18,18,20],A:[242,242,244],B:[110,80,60],C:[228,115,115],D:[58,122,222]},
  {name:'Black Lab',2:[60,58,55],   3:[40,38,36],  4:[90,86,80],   5:[62,48,28], 6:[32,32,35],7:[102,78,72],  9:[18,18,20],A:[242,242,244],B:[70,55,50],C:[228,115,115],D:[222,182,52]},
  {name:'White',    2:[242,240,234],3:[218,212,202],4:[254,252,248],5:[52,78,118],6:[32,32,35],7:[228,188,178],9:[18,18,20],A:[242,242,244],B:[180,160,150],C:[228,115,115],D:[58,178,82]},
  {name:'Husky',    2:[180,184,194],3:[100,104,114],4:[240,240,244],5:[62,88,132],6:[32,32,35],7:[198,158,150],9:[18,18,20],A:[242,242,244],B:[130,115,110],C:[228,115,115],D:[222,62,58]},
  {name:'Corgi',    2:[230,180,94], 3:[190,140,60],4:[254,248,230],5:[55,40,20], 6:[32,32,35],7:[220,170,150],9:[18,18,20],A:[242,242,244],B:[150,120,95],C:[228,115,115],D:[58,122,222]},
  {name:'Chocolate',2:[130,80,42],  3:[94,54,26],  4:[180,138,90], 5:[50,35,18], 6:[32,32,35],7:[160,110,90], 9:[18,18,20],A:[242,242,244],B:[100,70,50],C:[228,115,115],D:[222,62,58]},
  {name:'Dalmatian',2:[244,242,238],3:[58,58,62],  4:[254,252,248],5:[52,68,98], 6:[32,32,35],7:[228,188,178],9:[18,18,20],A:[242,242,244],B:[180,160,150],C:[228,115,115],D:[182,82,202]},
];

const HAM_P = [
  {name:'Golden',  2:[230,190,110],3:[190,150,70], 4:[255,244,218],5:[24,24,26],6:[228,165,165],7:[255,218,182],9:[18,18,20],A:[252,252,254],B:[170,140,120]},
  {name:'White',   2:[246,244,242],3:[220,216,210],4:[255,254,252],5:[24,24,26],6:[230,170,170],7:[255,230,220],9:[18,18,20],A:[252,252,254],B:[190,175,168]},
  {name:'Gray',    2:[170,170,178],3:[130,130,138],4:[218,218,222],5:[24,24,26],6:[224,160,160],7:[230,214,210],9:[18,18,20],A:[252,252,254],B:[150,135,130]},
  {name:'Cinnamon',2:[200,140,78], 3:[160,104,50],4:[240,218,180],5:[24,24,26],6:[228,165,165],7:[250,208,168],9:[18,18,20],A:[252,252,254],B:[160,125,100]},
  {name:'Cream',   2:[248,234,200],3:[220,204,168],4:[255,250,238],5:[24,24,26],6:[230,170,170],7:[255,234,214],9:[18,18,20],A:[252,252,254],B:[185,168,150]},
  {name:'Panda',   2:[242,242,242],3:[60,60,64],   4:[252,252,252],5:[24,24,26],6:[228,165,165],7:[255,230,220],9:[18,18,20],A:[252,252,254],B:[180,170,165]},
];

// ── Sprite drawing helpers (module-level, shared) ───────────────────────────

function _drawTemplate(ctx, tmpl, palette) {
  const ol = [30, 26, 34], wh = [190, 190, 195];
  const cm = { 1: ol, 8: wh, ...palette };
  for (let y = 0; y < _SPRITE_SIZE; y++) {
    const row = tmpl[y];
    for (let x = 0; x < _SPRITE_SIZE; x++) {
      const ch = row[x];
      if (ch === '0') continue;
      const idx = parseInt(ch, 16);
      const c = cm[idx];
      if (!c) continue;
      ctx.fillStyle = `rgb(${c[0]},${c[1]},${c[2]})`;
      ctx.fillRect(x, y, 1, 1);
    }
  }
}

function _tabbyOverlay(ctx, tmpl, dark) {
  for (let y = 0; y < _SPRITE_SIZE; y++) {
    for (let x = 0; x < _SPRITE_SIZE; x++) {
      if (tmpl[y][x] === '2' && ((y % 3 === 0 && x % 2 === 0) || (y % 3 === 1 && (x + 1) % 4 === 0))) {
        ctx.fillStyle = `rgb(${dark[0]},${dark[1]},${dark[2]})`;
        ctx.fillRect(x, y, 1, 1);
      }
    }
  }
}

function _spotsOverlay(ctx, tmpl, dark, seed) {
  const r = _petPrng(seed);
  for (let y = 0; y < _SPRITE_SIZE; y++) {
    for (let x = 0; x < _SPRITE_SIZE; x++) {
      if (tmpl[y][x] === '2' && r() < 0.1) {
        ctx.fillStyle = `rgb(${dark[0]},${dark[1]},${dark[2]})`;
        ctx.fillRect(x, y, 1, 1);
      }
    }
  }
}

function _calicoOverlay(ctx, tmpl, seed) {
  const r = _petPrng(seed);
  const or = [218, 132, 58], bk = [58, 52, 55];
  for (let y = 0; y < _SPRITE_SIZE; y++) {
    for (let x = 0; x < _SPRITE_SIZE; x++) {
      if (tmpl[y][x] === '2') {
        const v = r();
        if (v < 0.18) {
          ctx.fillStyle = `rgb(${or[0]},${or[1]},${or[2]})`;
          ctx.fillRect(x, y, 1, 1);
        } else if (v < 0.28) {
          ctx.fillStyle = `rgb(${bk[0]},${bk[1]},${bk[2]})`;
          ctx.fillRect(x, y, 1, 1);
        }
      }
    }
  }
}

function _generateCatSprite(ctx, seed) {
  const r = _petPrng(seed);
  const p = CAT_P[Math.floor(r() * CAT_P.length)];
  const tb = p.name.includes('Tabby') || r() > 0.65;
  const cal = p.name === 'Calico';
  _drawTemplate(ctx, CAT_T, p);
  if (tb && !cal) _tabbyOverlay(ctx, CAT_T, p[3]);
  if (cal) _calicoOverlay(ctx, CAT_T, seed + 99);
}

function _generateDogSprite(ctx, seed) {
  const r = _petPrng(seed);
  const p = DOG_P[Math.floor(r() * DOG_P.length)];
  const pt = r() > 0.5;
  const t = pt ? DOG_POINT : DOG_FLOP;
  _drawTemplate(ctx, t, p);
  if (p.name === 'Dalmatian') _spotsOverlay(ctx, t, p[3], seed + 77);

  // Tongue: hide if random says so — overdraw C pixels with belly color
  if (r() <= 0.55) {
    for (let y = 0; y < _SPRITE_SIZE; y++) {
      for (let x = 0; x < _SPRITE_SIZE; x++) {
        if (t[y][x] === 'C') {
          ctx.fillStyle = `rgb(${p[4][0]},${p[4][1]},${p[4][2]})`;
          ctx.fillRect(x, y, 1, 1);
        }
      }
    }
  }

  // Short tail
  const tc = p[2];
  ctx.fillStyle = `rgb(${tc[0]},${tc[1]},${tc[2]})`;
  if (r() > 0.5) {
    // tail up (happy)
    ctx.fillRect(19, 15, 1, 1);
    ctx.fillRect(20, 14, 1, 1);
    ctx.fillRect(20, 13, 1, 1);
    ctx.fillRect(21, 12, 1, 1);
  } else {
    // tail down
    ctx.fillRect(19, 19, 1, 1);
    ctx.fillRect(20, 20, 1, 1);
    ctx.fillRect(20, 21, 1, 1);
  }
}

function _generateHamsterSprite(ctx, seed) {
  const r = _petPrng(seed);
  const p = HAM_P[Math.floor(r() * HAM_P.length)];
  _drawTemplate(ctx, HAM_T, p);

  // Panda patches
  if (p.name === 'Panda') {
    const dk = p[3];
    ctx.fillStyle = `rgb(${dk[0]},${dk[1]},${dk[2]})`;
    // Eye patches
    [5, 6].forEach(y => [7, 8, 15, 16].forEach(x => ctx.fillRect(x, y, 1, 1)));
    // Ear patches
    ctx.fillRect(5, 1, 1, 2);
    ctx.fillRect(17, 1, 1, 2);
  }

  // Dorsal stripe
  if (r() > 0.5) {
    const dk = p[3];
    ctx.fillStyle = `rgb(${dk[0]},${dk[1]},${dk[2]})`;
    for (let y = 4; y <= 11; y++) ctx.fillRect(11, y, 1, 1);
    ctx.fillRect(12, 5, 1, 1);
    ctx.fillRect(12, 7, 1, 1);
  }

  // Stuffed cheeks (extend + teeth)
  if (r() > 0.4) {
    const ck = p[7];
    ctx.fillStyle = `rgb(${ck[0]},${ck[1]},${ck[2]})`;
    for (let dy = 0; dy < 3; dy++) {
      ctx.fillRect(0, 8 + dy, 1, 1);
      ctx.fillRect(23, 8 + dy, 1, 1);
    }
    ctx.fillStyle = 'rgb(252,250,244)';
    ctx.fillRect(10, 8, 1, 1);
    ctx.fillRect(13, 8, 1, 1);
  }
}

// ── Species → sprite generator routing ──────────────────────────────────────
const _SPRITE_GENERATORS = {
  cat:     _generateCatSprite,
  dog:     _generateDogSprite,
  hamster: _generateHamsterSprite,
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
    this.consumables = {};  // consumable type definitions
    this._lerpState = {};   // pet_id → {x, y}
    this._animFrames = {};  // pet_id → frame counter
    this._spriteCache = new Map();  // pet_id → offscreen canvas
    this._enabled = false;

    // Layered sprite composer (PNG layers + palette tinting)
    this._composer = null;
    if (window.PetSpriteComposer) {
      this._composer = new PetSpriteComposer();
      this._composer.init().catch(e => console.debug('[pets] SpriteComposer init failed:', e));
    }
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

    this.pets         = data.pets         || [];
    this.facilities   = data.facilities   || [];
    this.species      = data.species      || {};
    this.consumables = data.consumables  || {};
    this.tokens       = data.tokens       ?? null;

    // Track current pet IDs
    const currentIds = new Set(this.pets.map(p => p.id));

    // Initialize lerp state for new pets
    for (const pet of this.pets) {
      if (!this._lerpState[pet.id]) {
        this._lerpState[pet.id] = { x: pet.position[0], y: pet.position[1] };
        this._animFrames[pet.id] = 0;
      }
    }

    // Clean up removed pets
    for (const id of Object.keys(this._lerpState)) {
      if (!currentIds.has(id)) {
        delete this._lerpState[id];
        delete this._animFrames[id];
        this._spriteCache.delete(id);
        if (this._composer) this._composer.clearPet(id);
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
      ls.x += (pet.position[0] - ls.x) * PET_LERP_FACTOR;
      ls.y += (pet.position[1] - ls.y) * PET_LERP_FACTOR;

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

  // ── Sprite cache ──────────────────────────────────────────────────────────

  /**
   * Returns a cached 24×24 offscreen canvas with the pet's procedural sprite.
   * Generates on first access; subsequent calls return the cached version.
   */
  _getSprite(pet) {
    if (this._spriteCache.has(pet.id)) return this._spriteCache.get(pet.id);

    const generator = _SPRITE_GENERATORS[pet.species];
    if (!generator) return null;  // unknown species → fallback

    const offscreen = document.createElement('canvas');
    offscreen.width = _SPRITE_SIZE;
    offscreen.height = _SPRITE_SIZE;
    const offCtx = offscreen.getContext('2d');
    offCtx.imageSmoothingEnabled = false;

    const seed = _petHashStr(pet.id);
    generator(offCtx, seed);

    this._spriteCache.set(pet.id, offscreen);
    return offscreen;
  }

  // ── Draw a single pet ────────────────────────────────────────────────────

  /**
   * Draws one pet entity on the canvas.
   * Uses procedural pixel art sprite for known species, colored circle fallback otherwise.
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

    // ── Try layered sprite composition first ──
    if (this._composer?.isReady() && pet.appearance) {
      const composed = this._composer.getSprite(pet, animFrame);
      if (composed) {
        const savedSmoothing = ctx.imageSmoothingEnabled;
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(composed, px, py, TILE, TILE);
        ctx.imageSmoothingEnabled = savedSmoothing;

        // Draw overlays and return early (skip procedural fallback)
        this._drawStateOverlay(ctx, pet.state, cx, cy, radius, animFrame);

        const isOwned = !!pet.owner;
        ctx.fillStyle = isOwned ? '#44dd44' : '#ff8844';
        ctx.font = '8px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(pet.name || '???', cx, py + TILE - 2);

        if (pet.current_speech) {
          this._drawSpeechBubble(ctx, pet.current_speech, cx, cy - radius - 4, animFrame);
        }
        return;
      }
    }

    // ── Fallback: procedural sprite or colored circle ──
    const sprite = this._getSprite(pet);
    if (sprite) {
      // Center the 24×24 sprite within the 32×32 tile, draw with pixelated scaling
      const savedSmoothing = ctx.imageSmoothingEnabled;
      ctx.imageSmoothingEnabled = false;
      const drawSize = TILE;  // scale 24→32 to fill the tile
      const drawX = px + (TILE - drawSize) / 2;
      const drawY = py + (TILE - drawSize) / 2;
      ctx.drawImage(sprite, 0, 0, _SPRITE_SIZE, _SPRITE_SIZE, drawX, drawY, drawSize, drawSize);
      ctx.imageSmoothingEnabled = savedSmoothing;
    } else {
      // Fallback: colored circle with species initial
      const stateColor = PET_STATE_COLORS[pet.state] || PET_STATE_COLORS.idle;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = stateColor;
      ctx.fill();
      ctx.strokeStyle = '#333';
      ctx.lineWidth = 1;
      ctx.stroke();

      const speciesInfo = this.species[pet.species] || {};
      const initial = (speciesInfo.name || pet.species || '?').charAt(0).toUpperCase();
      ctx.fillStyle = '#333';
      ctx.font = 'bold 10px monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(initial, cx, cy);
    }

    // ── State overlays ──
    this._drawStateOverlay(ctx, pet.state, cx, cy, radius, animFrame);

    // ── Name tag below pet ──
    const isOwned = !!pet.owner;
    ctx.fillStyle = isOwned ? '#44dd44' : '#ff8844';
    ctx.font = '8px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(pet.name || '???', cx, py + TILE - 2);

    // ── Speech bubble ──
    if (pet.current_speech) {
      this._drawSpeechBubble(ctx, pet.current_speech, cx, cy - radius - 4, animFrame);
    }
  }

  /**
   * Draw a Simlish speech bubble above a pet.
   */
  _drawSpeechBubble(ctx, text, cx, bottomY, animFrame) {
    ctx.save();

    // Fade-in/out based on animation frame
    const fadeAlpha = Math.min(1.0, Math.sin((animFrame % 120) * Math.PI / 120) * 1.5 + 0.5);
    ctx.globalAlpha = Math.max(0.3, fadeAlpha);

    ctx.font = '7px monospace';
    const metrics = ctx.measureText(text);
    const padX = 4;
    const padY = 3;
    const bubbleW = metrics.width + padX * 2;
    const bubbleH = 12 + padY * 2;
    const bubbleX = cx - bubbleW / 2;
    const bubbleY = bottomY - bubbleH - 6;

    // Rounded rectangle background
    const r = 4;
    ctx.fillStyle = 'rgba(255, 255, 240, 0.92)';
    ctx.beginPath();
    ctx.moveTo(bubbleX + r, bubbleY);
    ctx.lineTo(bubbleX + bubbleW - r, bubbleY);
    ctx.quadraticCurveTo(bubbleX + bubbleW, bubbleY, bubbleX + bubbleW, bubbleY + r);
    ctx.lineTo(bubbleX + bubbleW, bubbleY + bubbleH - r);
    ctx.quadraticCurveTo(bubbleX + bubbleW, bubbleY + bubbleH, bubbleX + bubbleW - r, bubbleY + bubbleH);
    ctx.lineTo(bubbleX + r, bubbleY + bubbleH);
    ctx.quadraticCurveTo(bubbleX, bubbleY + bubbleH, bubbleX, bubbleY + bubbleH - r);
    ctx.lineTo(bubbleX, bubbleY + r);
    ctx.quadraticCurveTo(bubbleX, bubbleY, bubbleX + r, bubbleY);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = '#999';
    ctx.lineWidth = 0.5;
    ctx.stroke();

    // Triangle pointer
    ctx.fillStyle = 'rgba(255, 255, 240, 0.92)';
    ctx.beginPath();
    ctx.moveTo(cx - 3, bubbleY + bubbleH);
    ctx.lineTo(cx, bubbleY + bubbleH + 4);
    ctx.lineTo(cx + 3, bubbleY + bubbleH);
    ctx.closePath();
    ctx.fill();

    // Text
    ctx.fillStyle = '#333';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, cx, bubbleY + bubbleH / 2);

    ctx.restore();
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
      const px = fac.position[0] * TILE;
      const py = (fac.position[1] + WALL_ROWS) * TILE;
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
   * Returns tooltip string: "Name (Species) — Status [Stray]"
   *
   * @param {object} pet
   * @returns {string}
   */
  tooltipText(pet) {
    const speciesInfo = this.species[pet.species] || {};
    const speciesName = speciesInfo.name || pet.species || '???';
    const stateLabel  = pet.state || 'idle';
    const strayTag    = pet.owner ? '' : ' [Stray]';
    return `${pet.name} (${speciesName}) \u2014 ${stateLabel}${strayTag}`;
  }
}

// ── Export as global ─────────────────────────────────────────────────────────
window.PetRenderer = PetRenderer;
