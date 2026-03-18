# Tileset Rendering Upgrade Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all Canvas 2D primitive drawing in office.js with LimeZu tileset-based rendering for game-quality pixel art.

**Architecture:** Replace each draw method's internals in `office.js` one layer at a time (walls → floors → furniture → characters → decorations → interactive → meetings). Expand `TILE_DEFS` in `office-tileatlas.js` with new tile coordinates. Add `avatar_sprite` field to employee profiles for character sprite assignment. No new files created — only modify existing ones.

**Tech Stack:** Vanilla JS Canvas 2D, LimeZu 32x32 tilesets, Python (profile.yaml field)

**Known constraints:**
- No JS test framework — visual verification only for frontend changes
- Tile sprite coordinates require visual calibration against spritesheet images using `window.debugSheet()` (set `window.OMC_DEBUG = true` first)
- Character sprites are 1 tile wide × 2 tiles tall (32×64px)
- Coordinate system: `OfficeMap` stores positions in canvas-row space (gy + WALL_ROWS). Do not add WALL_ROWS again in draw methods.

---

## File Map

| File | Change | Responsibility |
|------|--------|----------------|
| `frontend/office-tileatlas.js` | Modify | Add ~15 new TILE_DEFS for walls, furniture, characters |
| `frontend/office.js` | Modify | Replace draw method internals (walls, floor, desk, character, decorations, plants, meetings, bulletin/project) |
| `src/onemancompany/agents/onboarding.py` | Modify | Assign `avatar_sprite` to new employees |

---

## Chunk 1: TILE_DEFS Expansion + Wall Rendering

### Task 0: Create feature branch

- [ ] **Step 1: Create and switch to feature branch**

```bash
git checkout -b feat/tileset-rendering-upgrade
```

---

### Task 1: Add wall and furniture TILE_DEFS to office-tileatlas.js

**Files:**
- Modify: `frontend/office-tileatlas.js:47-82`

**Prerequisite:** Before writing code, visually calibrate tile coordinates. Open browser, set `window.OMC_DEBUG = true`, then run `debugSheet('room', 0, 4)` to see wall tile rows in Room_Builder_Office_32x32.png. Also run `debugSheet('office', 0, 20)` to verify furniture positions. Note the correct (row, col) for each tile needed.

- [ ] **Step 1: Add new TILE_DEFS entries**

Add these entries after the existing `floor_wood_gold` definition in `office-tileatlas.js`, inside the `TILE_DEFS` object:

```javascript
  // ── Wall tiles (from Room_Builder_Office_32x32.png) ──
  // Rows 0-3 are wall sections: top edge, mid body, baseboard
  // Exact row/col must be calibrated with debugSheet('room', 0, 4)
  wall_top:            ['room',  0,  0],   // top edge of wall
  wall_mid:            ['room',  1,  0],   // middle wall body
  wall_bottom:         ['room',  2,  0],   // baseboard / lower wall
  wall_window_top:     ['room',  0,  3],   // wall with window (upper half)
  wall_window_bottom:  ['room',  1,  3],   // wall with window (lower half)

  // ── Additional furniture (from Modern_Office_32x32.png) ──
  // Calibrate with debugSheet('office', 0, 20)
  chair_orange:   ['office',  8,  4],
  chair_gold:     ['office',  8,  6],
  monitor_screen: ['office',  0,  4],   // standalone monitor
  keyboard_tile:  ['office',  1,  4],   // keyboard on desk
  filing_cabinet: ['office', 12,  0],
  sofa_l:         ['office', 10,  0],
  sofa_r:         ['office', 10,  2],
```

- [ ] **Step 2: Preload character sheets on demand**

In `office-tileatlas.js`, the `SHEET_PATHS` already registers `char01` through `char20`. No changes needed here — preloading will be triggered from `office.js` in Task 4.

- [ ] **Step 3: Verify no syntax errors**

Open the app in browser, check browser console for JavaScript errors. The new TILE_DEFS are just data — they won't affect rendering until draw methods use them.

- [ ] **Step 4: Commit**

```bash
git add frontend/office-tileatlas.js
git commit -m "feat: expand TILE_DEFS with wall and furniture tile coordinates"
```

---

### Task 2: Replace drawWalls with tileset rendering

**Files:**
- Modify: `frontend/office.js:399-455` (drawWalls + _drawWindow methods)

The current `drawWalls()` draws 3 colored bands + hand-drawn windows. Replace with tileset wall tiles, keeping the dynamic window sky/star animation.

- [ ] **Step 1: Rewrite drawWalls()**

Replace the entire `drawWalls()` method (lines 401-423) with:

```javascript
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
```

- [ ] **Step 2: Rename _drawWindow to _drawWindowAnimation and simplify**

Replace the entire `_drawWindow(x, y)` method (lines 425-455) with a version that only draws the dynamic sky/star animation overlay, not the window frame (that's now in the tile):

```javascript
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
```

- [ ] **Step 3: Visual verification**

Start the server, open browser:
1. Wall tiles render in top 3 rows
2. Windows show dynamic sky gradient + twinkling stars
3. Bulletin board area (cols 5-7) and project wall (cols 12-14) have solid walls (no windows)
4. Pan/zoom works normally in wall area

If wall tile coordinates are wrong, use `debugSheet('room', 0, 4)` to find the correct row/col and update TILE_DEFS.

- [ ] **Step 4: Commit**

```bash
git add frontend/office.js
git commit -m "feat: replace hand-drawn walls with tileset wall tiles"
```

---

### Task 3: Simplify drawFloor to tile-only rendering

**Files:**
- Modify: `frontend/office.js:346-397` (drawFloor method)
- Modify: `frontend/office.js:72-80` (FLOOR_FALLBACK constant)

- [ ] **Step 1: Remove FLOOR_FALLBACK and simplify drawFloor**

Delete the `FLOOR_FALLBACK` constant (lines 72-80).

Replace `drawFloor()` (lines 346-397) with:

```javascript
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
```

- [ ] **Step 2: Clean up PALETTE — remove floor colors**

Delete these lines from PALETTE (lines 16-18):

```javascript
  // Floor (warm-tinted dark tiles, not grey)
  floor1: '#2c2840',
  floor2: '#262238',
```

- [ ] **Step 3: Visual verification**

Open browser:
1. Floor tiles render from tileset (different textures per department zone)
2. Plant divider columns still appear between zones
3. Screen glow still appears under employee desks
4. No fallback colored checkerboard visible

- [ ] **Step 4: Commit**

```bash
git add frontend/office.js
git commit -m "feat: simplify floor rendering to tile-only, remove fallback colors"
```

---

## Chunk 2: Furniture + Character Rendering

### Task 4: Replace drawDesk with tileset furniture

**Files:**
- Modify: `frontend/office.js:777-826` (drawDesk method)

- [ ] **Step 1: Rewrite drawDesk()**

Replace the entire `drawDesk(gx, gy, hasMonitor = true)` method with:

```javascript
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
```

- [ ] **Step 2: Delete unused PALETTE furniture colors**

Remove these from PALETTE (lines 23-28):

```javascript
  // Furniture (rich warm wood tones)
  desk: '#9a7420',
  deskDark: '#6d5210',
  deskLight: '#b8901e',
  chair: '#3c3468',
  chairDark: '#2c2450',
```

Note: Check if `PALETTE.desk` or `PALETTE.chair` are referenced anywhere else in the file before deleting. If the fallback in the new drawDesk uses them, keep them or use inline hex values.

- [ ] **Step 3: Update _drawEntities for executive chair variants**

In `_drawEntities()` (line 1266), the CEO desk currently uses `this.drawDesk(9, execRowCanvas, true)`. The `chairDef` parameter was already added in Step 1. Change the CEO desk call:
```javascript
    this.drawDesk(9, execRowCanvas, true, 'chair_gold');
```

- [ ] **Step 4: Visual verification**

Open browser:
1. Desks show tileset chair + desk surface + monitor tiles
2. CEO has gold chair
3. Monitor has animated screen glow + scanlines
4. Clicking employees still works (click detection unchanged)

If tile positions look wrong, use `debugSheet('office', 0, 10)` to recalibrate.

- [ ] **Step 5: Commit**

```bash
git add frontend/office.js
git commit -m "feat: replace hand-drawn desks with tileset furniture tiles"
```

---

### Task 5: Replace drawCharacter with sprite rendering

**Files:**
- Modify: `frontend/office.js:830-1115` (drawCharacter method only; keep _hashStr at 1117-1123)
- Modify: `frontend/office.js:82-137` (constructor — add character preloading)
- Modify: `frontend/office.js:162-196` (updateState — preload character sheets)

This is the largest single change. The current `drawCharacter` is ~290 lines of procedural pixel art. Replace with ~80 lines of sprite rendering.

- [ ] **Step 1: Add character sheet preloading in updateState()**

In `updateState()` (after line 195 `this._preloadAvatars();`), add:

```javascript
    // Preload character spritesheets for current employees
    this._preloadCharacterSheets();
```

Add the new method after `_preloadToolIcons()`:

```javascript
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
```

- [ ] **Step 2: Add _getCharFrame helper method**

Add after `_preloadCharacterSheets`:

```javascript
  /**
   * Get the sprite frame for an employee based on their state.
   * Returns { sheet, row, col, w, h } for tileAtlas.drawTile().
   * Character sprites are 1 tile wide × 2 tiles tall.
   */
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
```

- [ ] **Step 3: Rewrite drawCharacter()**

Replace the entire `drawCharacter(gx, gy, data, isCEO = false)` method (lines 830-1115) with:

```javascript
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
```

- [ ] **Step 4: Keep PALETTE people colors (still needed)**

`PALETTE.skin`, `PALETTE.hair`, and `PALETTE.shirt` are still used by `drawMeetingRoom` (participant mini-head fallback) and `drawCEOAvatar`. Do NOT delete them. Add a comment:

```javascript
  // People — still used by meeting room participant fallback and drawCEOAvatar
  skin: ['#f5cc8e', '#eab878', '#d09868', '#b07858', '#8c6048'],
  hair: ['#1a1a24', '#5c3010', '#dd9922', '#cc4444', '#7744aa', '#2255aa', '#884444', '#446644'],
  shirt: ['#4488ff', '#ff4466', '#44cc44', '#cc44cc', '#ff8844', '#44cccc', '#8866dd', '#dd8844'],
```

- [ ] **Step 5: Visual verification**

Open browser:
1. Employees show LimeZu character sprites (not procedural pixel people)
2. Sitting employees show sit pose, idle employees show idle front
3. CEO has crown above sprite
4. Status overlays (working dots, idle zzz, listening glow) appear correctly
5. Name tags display correctly below characters
6. Setup key / offline X badges work
7. Click on employee still opens detail panel
8. Scroll/zoom — character sprites stay crisp (imageSmoothingEnabled = false)

- [ ] **Step 6: Commit**

```bash
git add frontend/office.js
git commit -m "feat: replace procedural characters with Premade Character sprites"
```

---

### Task 6: Add avatar_sprite to employee onboarding

**Files:**
- Modify: `src/onemancompany/agents/onboarding.py`

- [ ] **Step 1: Find the employee profile creation in onboarding**

Search for where `profile.yaml` is written during new employee onboarding. The `avatar_sprite` field should be randomly assigned (1-20) when creating a new employee profile.

```bash
.venv/bin/python -c "import random; print(random.randint(1, 20))"
```

- [ ] **Step 2: Add avatar_sprite assignment**

In `onboarding.py` line 836, the `save_employee()` call has a dict literal with all profile fields. Add `avatar_sprite` to that dict. Note: add a trailing comma after `False` on the `"onboarding_completed"` line first:

```python
import random  # add to top-level imports if not already present

# In the save_employee dict (line 836-858), change the last entry and add:
        "onboarding_completed": False,   # ← add trailing comma
        "avatar_sprite": random.randint(1, 20),
```

- [ ] **Step 3: Verify compilation**

```bash
.venv/bin/python -c "from onemancompany.agents.onboarding import run_onboarding; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/agents/onboarding.py
git commit -m "feat: assign random avatar_sprite (1-20) to new employees"
```

---

## Chunk 3: Decorations, Interactive Elements, Meeting Rooms

### Task 7: Replace drawDecorations and drawPlants with tiles

**Files:**
- Modify: `frontend/office.js:457-575` (drawPlants, drawDecorations, _drawDecoFallback)

- [ ] **Step 1: Replace drawPlants()**

Replace the entire `drawPlants()` + `_drawPlant()` methods (lines 459-488) with:

```javascript
  drawPlants() {
    const plantPositions = [[0, 1], [19, 1], [10, 1]];
    for (const [gx, gy] of plantPositions) {
      // Plant is 1×2 tiles (leafy top + pot bottom)
      tileAtlas.drawDef(this.ctx, 'plant_large', gx * TILE, gy * TILE);
    }
  }
```

Delete `_drawPlant()` method entirely.

- [ ] **Step 2: Replace drawDecorations()**

Replace `drawDecorations()` and `_drawDecoFallback()` (lines 492-575) with:

```javascript
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
```

Note: The exact decoration placements and which tiles to use may need adjustment during visual calibration. The key point is replacing PNG preloading + fallback primitives with `tileAtlas.drawDef()` calls.

- [ ] **Step 3: Remove _decoImages and _preloadDecorations**

In the constructor (line 93), delete:
```javascript
    this._decoImages  = {};
```

Delete `_preloadDecorations()` method (lines 212-220).

In the constructor (line 110), delete:
```javascript
    this._preloadDecorations();
```

- [ ] **Step 4: Visual verification**

Open browser:
1. Plants render as tile sprites (not hand-drawn)
2. Bookshelf, whiteboard, printer show as tiles
3. Coffee machine has steam animation
4. Server rack has blinking LEDs
5. Wall clock still renders

- [ ] **Step 5: Commit**

```bash
git add frontend/office.js
git commit -m "feat: replace decoration sprites and primitives with tileset tiles"
```

---

### Task 8: Update drawBulletinBoard and drawProjectWall backgrounds

**Files:**
- Modify: `frontend/office.js:634-755` (drawBulletinBoard + drawProjectWall)

Keep all the data rendering (papers, cards, pins) and click interaction. Only replace the background fill with a tile.

- [ ] **Step 1: Update drawBulletinBoard background**

In `drawBulletinBoard()`, replace ONLY the background fill and texture lines (lines 643-652). The whiteboard tile is 2×2 (64×64px) but the board is 3 tiles wide × ~28px tall, so a single tile won't cover it. Instead, tile the background with the wood floor tile for a cork-board look:

```javascript
    // Fallback FIRST (tile draws on top, silent no-op if not loaded)
    if (!tileAtlas.isReady('room')) {
      this._rect(bx, by, bw, bh, PALETTE.boardBg);
    }

    // Tile background — use floor wood tile for cork-board texture
    for (let tx = 0; tx < 3; tx++) {
      tileAtlas.drawDef(this.ctx, 'floor_wood_warm', bx + tx * TILE, by - 2);
    }
```

Keep everything else (frame, papers, pins, hover glow, label) unchanged.

- [ ] **Step 2: Update drawProjectWall background**

In `drawProjectWall()`, replace ONLY the background fill and grid lines (lines 708-714). Same issue as bulletin board — bookshelf tile is 2×2 and won't cover 3-tile-wide area. Use a dark floor tile:

```javascript
    // Fallback FIRST (tile draws on top, silent no-op if not loaded)
    if (!tileAtlas.isReady('room')) {
      this._rect(bx, by, bw, bh, PALETTE.projectBg);
    }

    // Tile background — use dark stone floor tile for project wall surface
    for (let tx = 0; tx < 3; tx++) {
      tileAtlas.drawDef(this.ctx, 'floor_stone_blue', bx + tx * TILE, by - 2);
    }
```

Keep everything else (frame, cards, hover glow, label) unchanged.

- [ ] **Step 3: Visual verification**

1. Bulletin board has tileset background with papers/pins on top
2. Project wall has tileset background with cards on top
3. Hover glow still works on both
4. Clicking still opens the respective panels

- [ ] **Step 4: Commit**

```bash
git add frontend/office.js
git commit -m "feat: use tileset backgrounds for bulletin board and project wall"
```

---

### Task 9: Replace drawMeetingRoom with tileset tiles

**Files:**
- Modify: `frontend/office.js:1156-1245` (drawMeetingRoom)

- [ ] **Step 1: Rewrite drawMeetingRoom()**

Replace the entire method with:

```javascript
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
```

- [ ] **Step 2: Visual verification**

1. Meeting rooms show tileset conference table + chairs
2. Participant mini-sprites (scaled character sprites) appear when booked
3. Status LED and label work correctly
4. Click on meeting room still opens info

- [ ] **Step 3: Commit**

```bash
git add frontend/office.js
git commit -m "feat: replace meeting room primitives with tileset conference tiles"
```

---

## Chunk 4: Cleanup + Final Verification

### Task 10: Final cleanup and regression testing

**Files:**
- Modify: `frontend/office.js` — remove dead code

- [ ] **Step 1: Remove dead PALETTE entries**

Review PALETTE and remove any colors no longer referenced anywhere in office.js:
- `floor1`, `floor2` (already removed in Task 3)
- `desk`, `deskDark`, `deskLight`, `chair`, `chairDark` (if removed in Task 4)
- `plant`, `plantPot` (replaced by tiles in Task 7)
- `windowFrame`, `windowGlass`, `windowSky` (if no longer used after Task 2)

Search for each before deleting: use browser console `Ctrl+F` in the file.

- [ ] **Step 2: Keep _lighten and _darken (still used)**

`_lighten` and `_darken` ARE still used by bulletin board frame rendering (line 655, 676), project wall frame rendering (line 718), and other Canvas overlay code. Do NOT delete them.

- [ ] **Step 3: Update constructor comment**

Change line 106 from:
```javascript
    // Preload tileset sheets (character sheets excluded until Task 8 spritesheet rendering)
```
to:
```javascript
    // Preload tileset sheets (character sheets loaded on-demand per employee)
```

- [ ] **Step 4: Full regression test**

Open browser and verify ALL interactions:
1. Pan (drag) and zoom (scroll wheel) work
2. Minimap shows correct overview + click-to-jump works
3. Click employee → detail panel
4. Click bulletin board → rules panel
5. Click project wall → project history
6. Click meeting room → meeting info
7. New employee hire → particle effect
8. Window star animation plays
9. Department labels render between zones
10. CEO has gold chair + crown + special name tag
11. Working employees show sit sprite + work bubble
12. Idle employees show idle sprite + zzz
13. Listening employees show purple glow
14. All text (names, labels) renders clearly at various zoom levels

- [ ] **Step 5: Commit**

```bash
git add frontend/office.js
git commit -m "chore: remove dead palette entries and update comments after tileset migration"
```

- [ ] **Step 6: Push branch and create PR**

```bash
git push -u origin feat/tileset-rendering-upgrade
```

Create PR with title: "feat: full tileset rendering upgrade for game-quality office"
