/**
 * pet-sprite-composer.js — Loads PNG sprite layers, tints with palette colors,
 * composites them, and caches the result.
 *
 * Must be loaded BEFORE office-pets.js in index.html.
 */

const SPRITE_SIZE = 32;
const SPRITE_BASE_PATH = '/sprites/pets';

const STATE_TO_POSE = {
  idle: 'sit',
  walking: 'walk_a',
  sleeping: 'sleep',
  eating: 'eat',
  playing: 'play',
};

class PetSpriteComposer {
  constructor() {
    this._palettes = null;
    this._parts = null;
    this._imageCache = {};    // path → Image | false (loaded or failed)
    this._spriteCache = {};   // `${petId}_${pose}` → canvas
    this._walkFrame = {};     // petId → 0|1
    this._walkTimer = {};     // petId → frame counter
    this._ready = false;
    this._pending = {};       // cacheKey → true (in-flight compositions)
  }

  async init() {
    try {
      const [palettes, parts] = await Promise.all([
        fetch(`${SPRITE_BASE_PATH}/palettes.json`).then(r => r.json()),
        fetch(`${SPRITE_BASE_PATH}/parts.json`).then(r => r.json()),
      ]);
      this._palettes = palettes;
      this._parts = parts;
      this._ready = true;
    } catch (e) {
      console.debug('[sprites] Failed to load config:', e);
    }
  }

  isReady() { return this._ready; }

  getSprite(pet, animFrame) {
    if (!this._ready || !pet.appearance) return null;

    let pose = STATE_TO_POSE[pet.state] || 'sit';

    if (pet.state === 'walking') {
      if (!this._walkTimer[pet.id]) this._walkTimer[pet.id] = 0;
      this._walkTimer[pet.id]++;
      if (!this._walkFrame[pet.id]) this._walkFrame[pet.id] = 0;
      if (this._walkTimer[pet.id] % 15 === 0) {
        this._walkFrame[pet.id] = 1 - this._walkFrame[pet.id];
      }
      pose = this._walkFrame[pet.id] === 0 ? 'walk_a' : 'walk_b';
    }

    const cacheKey = `${pet.id}_${pose}`;
    if (this._spriteCache[cacheKey]) return this._spriteCache[cacheKey];

    // Start async composition if not already in flight
    if (!this._pending[cacheKey]) {
      this._pending[cacheKey] = true;
      this._composeAsync(pet, pose, cacheKey).catch(() => {}).finally(() => {
        delete this._pending[cacheKey];
      });
    }
    return null; // will be available next frame
  }

  async _composeAsync(pet, pose, cacheKey) {
    const app = pet.appearance;
    const species = pet.species;
    const sp = this._palettes[species];
    if (!sp) return;

    const canvas = document.createElement('canvas');
    canvas.width = SPRITE_SIZE;
    canvas.height = SPRITE_SIZE;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;

    const base = `${SPRITE_BASE_PATH}/${species}/${pose}`;

    // 1. Body (grayscale → tint)
    const bodyImg = await this._loadImage(`${base}/body.png`);
    if (bodyImg) {
      const bodyColor = sp.body?.[app.body_color] || [200, 200, 200];
      this._drawTinted(ctx, bodyImg, bodyColor);
    }

    // 2. Pattern (skip 'solid')
    if (app.pattern && app.pattern !== 'solid') {
      const patImg = await this._loadImage(`${base}/pattern_${app.pattern}.png`);
      if (patImg) { ctx.drawImage(patImg, 0, 0); }
    }

    // 3. Ears
    if (app.ears) {
      const earImg = await this._loadImage(`${base}/ears_${app.ears}.png`);
      if (earImg) { ctx.drawImage(earImg, 0, 0); }
    }

    // 4. Tail
    if (app.tail) {
      const tailImg = await this._loadImage(`${base}/tail_${app.tail}.png`);
      if (tailImg) { ctx.drawImage(tailImg, 0, 0); }
    }

    // 5. Cheeks (hamster)
    if (app.cheeks) {
      const chkImg = await this._loadImage(`${base}/cheeks_${app.cheeks}.png`);
      if (chkImg) { ctx.drawImage(chkImg, 0, 0); }
    }

    // 6. Eyes (grayscale → tint)
    const eyeImg = await this._loadImage(`${base}/eyes.png`);
    if (eyeImg) {
      const eyeColor = sp.eyes?.[app.eye_color] || [100, 100, 100];
      this._drawTinted(ctx, eyeImg, eyeColor);
    }

    // 7. Lineart (on top)
    const lineImg = await this._loadImage(`${base}/lineart.png`);
    if (lineImg) { ctx.drawImage(lineImg, 0, 0); }

    // 8. Collar (dog, grayscale → tint)
    if (app.collar_color) {
      const colImg = await this._loadImage(`${base}/collar.png`);
      if (colImg) {
        const colColor = sp.collar?.[app.collar_color] || [200, 50, 50];
        this._drawTinted(ctx, colImg, colColor);
      }
    }

    this._spriteCache[cacheKey] = canvas;
  }

  _drawTinted(ctx, img, [r, g, b]) {
    const tmp = document.createElement('canvas');
    tmp.width = SPRITE_SIZE;
    tmp.height = SPRITE_SIZE;
    const t = tmp.getContext('2d');
    t.imageSmoothingEnabled = false;
    t.drawImage(img, 0, 0);
    t.globalCompositeOperation = 'multiply';
    t.fillStyle = `rgb(${r},${g},${b})`;
    t.fillRect(0, 0, SPRITE_SIZE, SPRITE_SIZE);
    t.globalCompositeOperation = 'destination-in';
    t.drawImage(img, 0, 0);
    ctx.drawImage(tmp, 0, 0);
  }

  async _loadImage(path) {
    if (this._imageCache[path] === false) return null;
    if (this._imageCache[path]) return this._imageCache[path];
    return new Promise(resolve => {
      const img = new Image();
      img.onload = () => { this._imageCache[path] = img; resolve(img); };
      img.onerror = () => { this._imageCache[path] = false; resolve(null); };
      img.src = path;
    });
  }

  clearPet(petId) {
    for (const key of Object.keys(this._spriteCache)) {
      if (key.startsWith(petId + '_')) delete this._spriteCache[key];
    }
    delete this._walkFrame[petId];
    delete this._walkTimer[petId];
  }
}

window.PetSpriteComposer = PetSpriteComposer;
