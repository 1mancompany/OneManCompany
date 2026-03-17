# Office Tileset Visualization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hand-drawn Canvas 2D office with LimeZu tileset-based rendering, pan+zoom camera, department floor tiles, and plant-column dividers.

**Architecture:** The existing `OfficeRenderer` class is refactored into 4 cooperating modules: `TileAtlas` (spritesheet loader), `Camera` (pan/zoom/viewport), `OfficeMap` (tilemap data), and a rewritten `OfficeRenderer` (orchestrator). The backend `layout.py` adds two new fields (`floor_style`, `divider_cols`) to zone data — additive only, no breaking API changes. All existing click/tooltip/WebSocket logic is preserved.

**Tech Stack:** Vanilla JS Canvas 2D, LimeZu 32x32 tilesets, Python pytest (backend tests)

**Known constraints:**
- JS modules have no automated test framework (no bundler/Jest) — JS correctness verified via browser console and visual inspection
- Tile sprite coordinates in `TILE_DEFS` require human visual verification against tileset images (Task 8); an agent without vision tools should flag Task 8 for human review
- Coordinate system: `OfficeMap` stores all entity positions in **canvas-row space** (i.e., `gy + WALL_ROWS`). All click detection in `_onClick` uses these pre-offset values — do not add 3 again

---

## File Map

| File | Change | Responsibility |
|------|--------|----------------|
| `frontend/office.js` | Full rewrite | OfficeRenderer orchestrator |
| `frontend/office-tileatlas.js` | Create | TileAtlas singleton — spritesheet loading & tile blitting |
| `frontend/office-camera.js` | Create | Camera — pan/zoom/culling/coordinate transforms |
| `frontend/office-map.js` | Create | OfficeMap — tilemap data layer built from API |
| `frontend/office-minimap.js` | Create | MiniMap — 120×80 overlay renderer |
| `frontend/index.html` | Modify | Add `<script>` tags for new files |
| `src/onemancompany/core/config.py` | Modify | Add `DEPT_FLOOR_STYLES` constant |
| `src/onemancompany/core/layout.py` | Modify | Add `floor_style` + `divider_cols` to zone data |
| `tests/unit/core/test_layout_tileset.py` | Create | Tests for new layout fields |

---

## Chunk 1: Backend — Zone Floor Style + Divider Columns

### Task 1: Add DEPT_FLOOR_STYLES to config.py

**Files:**
- Modify: `src/onemancompany/core/config.py` (after DEPT_COLORS)

- [ ] **Step 1: Add the constant after DEPT_COLORS in config.py**

Find the line `DEPT_COLORS = {` and add below it:

```python
# Floor tile style key per department — used by frontend TileAtlas to pick tile variant
# Keys correspond to sections in the Room_Builder_Office_32x32.png tileset
DEPT_FLOOR_STYLES: dict[str, str] = {
    "Engineering": "stone_blue",
    "Design": "wood_warm",
    "Analytics": "tile_green",
    "Marketing": "carpet_red",
    "General": "stone_gray",
    # Executive area uses gold-accent wood (handled separately in layout)
}

# Width in grid columns of the plant divider between department zones
DEPT_DIVIDER_WIDTH: int = 1
```

- [ ] **Step 2: Verify import compiles**

```bash
.venv/bin/python -c "from onemancompany.core.config import DEPT_FLOOR_STYLES, DEPT_DIVIDER_WIDTH; print(DEPT_FLOOR_STYLES)"
```

Expected: `{'Engineering': 'stone_blue', ...}`

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/core/config.py
git commit -m "feat: add DEPT_FLOOR_STYLES and DEPT_DIVIDER_WIDTH constants"
```

---

### Task 2: Extend layout.py zone data with floor_style + divider_cols

**Files:**
- Modify: `src/onemancompany/core/layout.py`
- Modify: `src/onemancompany/core/config.py` (import)
- Create: `tests/unit/core/test_layout_tileset.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/core/test_layout_tileset.py`:

```python
"""Tests for tileset-specific layout fields added to zone data.

NOTE on mocking: load_all_employees and mark_dirty are lazy-imported inside
function bodies in layout.py, so they must be patched at their source module
(onemancompany.core.store), not at the importing layout module.
_persist_positions is a module-level function in layout.py, so it patches there.
"""
import pytest
from unittest.mock import patch, MagicMock


def _make_fake_employees(dept_map: dict[str, int]) -> dict:
    """Build fake employee dict: {emp_id: {department, level, ...}}."""
    employees = {}
    n = 6  # start after founding IDs (00001-00005)
    for dept, count in dept_map.items():
        for _ in range(count):
            eid = f"{n:05d}"
            employees[eid] = {
                "department": dept,
                "level": 1,
                "employee_number": eid,
                "desk_position": [0, 0],
                "remote": False,
            }
            n += 1
    return employees


# Patch at the source module for lazy imports; _persist_positions is local to layout
@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_zones_include_floor_style(mock_persist, mock_dirty, mock_load):
    """Each zone dict must contain a 'floor_style' string."""
    from onemancompany.core.layout import compute_layout
    from onemancompany.core.config import DEPT_FLOOR_STYLES

    mock_load.return_value = _make_fake_employees({"Engineering": 2, "Design": 2})

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    for zone in layout["zones"]:
        assert "floor_style" in zone, f"zone {zone['department']} missing floor_style"
        dept = zone["department"]
        expected = DEPT_FLOOR_STYLES.get(dept, "stone_gray")
        assert zone["floor_style"] == expected


@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_layout_includes_divider_cols(mock_persist, mock_dirty, mock_load):
    """Layout dict must contain 'divider_cols' listing columns between zones."""
    from onemancompany.core.layout import compute_layout

    mock_load.return_value = _make_fake_employees({"Engineering": 2, "Design": 2})

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    assert "divider_cols" in layout
    dividers = layout["divider_cols"]
    assert isinstance(dividers, list)
    # With 2 departments there is 1 divider between them
    assert len(dividers) == 1


@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_divider_cols_at_zone_boundaries(mock_persist, mock_dirty, mock_load):
    """Divider column should sit at end_col of left zone."""
    from onemancompany.core.layout import compute_layout

    mock_load.return_value = _make_fake_employees({"Engineering": 3, "Design": 3, "Marketing": 3})

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    zones = layout["zones"]
    dividers = layout["divider_cols"]

    # Each divider is at the end_col of the zone to its left
    for i, div_col in enumerate(dividers):
        left_zone = zones[i]
        assert div_col == left_zone["end_col"], (
            f"Divider {i} at col {div_col} should be at {left_zone['department']}.end_col={left_zone['end_col']}"
        )


@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_no_dividers_with_single_department(mock_persist, mock_dirty, mock_load):
    """Single department → no dividers."""
    from onemancompany.core.layout import compute_layout

    mock_load.return_value = _make_fake_employees({"Engineering": 3})

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    assert layout["divider_cols"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/core/test_layout_tileset.py -v 2>&1 | head -30
```

Expected: 4 failures (KeyError or AssertionError — `floor_style` and `divider_cols` not yet in layout)

- [ ] **Step 3: Update DeptZone dataclass to carry floor_style**

In `src/onemancompany/core/layout.py`, update the `DeptZone` dataclass:

```python
@dataclass
class DeptZone:
    department: str
    start_col: int
    end_col: int  # exclusive
    floor1: str = ""
    floor2: str = ""
    label_color: str = ""
    label_en: str = ""
    floor_style: str = "stone_gray"   # NEW — tileset style key

    def to_dict(self) -> dict:
        return {
            "department": self.department,
            "start_col": self.start_col,
            "end_col": self.end_col,
            "floor1": self.floor1,
            "floor2": self.floor2,
            "label_color": self.label_color,
            "label_en": self.label_en,
            "floor_style": self.floor_style,   # NEW
        }
```

- [ ] **Step 4: Update _compute_zones to set floor_style**

In `_compute_zones()`, update the import at the top of the function (or top of file):

```python
from onemancompany.core.config import (
    ...
    DEPT_FLOOR_STYLES,   # add this
    DEPT_DIVIDER_WIDTH,  # add this
)
```

In `_compute_zones()`, update the DeptZone construction:

```python
zones.append(DeptZone(
    department=d,
    start_col=col,
    end_col=col + w,
    floor1=colors[0],
    floor2=colors[1],
    label_color=colors[2],
    label_en=d,
    floor_style=DEPT_FLOOR_STYLES.get(d, "stone_gray"),   # NEW
))
```

- [ ] **Step 5: Add divider_cols computation in compute_layout()**

In `compute_layout()`, after building the layout dict, add:

```python
# Divider columns sit at the end_col boundary of each zone (exclusive end).
# The zone to the right starts at end_col+DEPT_DIVIDER_WIDTH, but since
# _compute_zones already allocates 20 total columns, we record divider positions
# as the end_col of the left zone — the frontend renders plants there and treats
# that column as impassable for desk placement.
# Note: employees are already assigned desk positions INSIDE zone.start_col to
# zone.end_col-1 by _assign_desks_in_zone (1-col padding from zone edge), so
# the boundary column at end_col is naturally free of desks.
divider_cols = [zones[i].end_col for i in range(len(zones) - 1)]
layout["divider_cols"] = divider_cols
```

This goes right before `compute_asset_layout(company_state, layout)`.

> **Note on overlap:** `_assign_desks_in_zone` places desks starting at `zone.start_col + 1` (1-col padding from edge), so the column at `zone.end_col` is the padding column of the right zone — it will not contain a desk. This naturally prevents desk/plant overlap without zone width adjustment.

- [ ] **Step 6: Add config imports to layout.py top-level imports**

In the existing import block in `layout.py`:

```python
from onemancompany.core.config import (
    ...
    DEPT_FLOOR_STYLES,
    DEPT_DIVIDER_WIDTH,
)
```

- [ ] **Step 7: Run tests — should pass**

```bash
.venv/bin/python -m pytest tests/unit/core/test_layout_tileset.py -v
```

Expected: 4 PASSED

- [ ] **Step 8: Run existing layout tests to ensure no regression**

```bash
.venv/bin/python -m pytest tests/ -v -k "layout" 2>&1 | tail -20
```

Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add src/onemancompany/core/config.py src/onemancompany/core/layout.py tests/unit/core/test_layout_tileset.py
git commit -m "feat: add floor_style and divider_cols to office layout API response"
```

---

## Chunk 2: TileAtlas — Spritesheet Loading

### Task 3: Create TileAtlas class

**Files:**
- Create: `frontend/office-tileatlas.js`

The TileAtlas loads PNG spritesheets and provides `drawTile(ctx, sheet, srcRow, srcCol, destX, destY, w=1, h=1)`. Tile coordinates are expressed in 32px grid units.

- [ ] **Step 1: Create `frontend/office-tileatlas.js`**

```javascript
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
 *   Row 0-1: idle (face down)
 *   Row 2-3: walk (face down)
 *   Row 4-5: sit (variant 1)
 *   Row 6-7: sit (variant 2)
 */

const TILE_SIZE = 32;

// Sheet paths relative to /assets/office/tilesets/
const SHEET_PATHS = {
  office:     'Modern_Office_Revamped_v1.2/Modern_Office_32x32.png',
  room:       'Modern_Office_Revamped_v1.2/1_Room_Builder_Office/Room_Builder_Office_32x32.png',
  interiors:  'moderninteriors-win/1_Interiors/32x32/Interiors_32x32.png',
  interiors_room: 'moderninteriors-win/1_Interiors/32x32/Room_Builder_32x32.png',
};

// Character sheet paths (01 – 20)
for (let i = 1; i <= 20; i++) {
  const key = `char${String(i).padStart(2, '0')}`;
  const num = String(i).padStart(2, '0');
  SHEET_PATHS[key] =
    `moderninteriors-win/2_Characters/Character_Generator/0_Premade_Characters/32x32/Premade_Character_32x32_${num}.png`;
}

/**
 * Tile definitions for semantic access.
 * Format: [sheetKey, srcRow, srcCol, widthInTiles, heightInTiles]
 *
 * IMPORTANT: Coordinates below must be verified visually against the actual
 * spritesheet images. The values here are best-effort — adjust if needed.
 */
const TILE_DEFS = {
  // ── Floor tiles (from room builder sheet) ──
  floor_stone_gray:   ['room', 2, 0],   // gray stone tile
  floor_stone_blue:   ['room', 2, 2],   // blue-tinted stone
  floor_wood_warm:    ['room', 4, 0],   // warm wood planks
  floor_tile_green:   ['room', 6, 0],   // green tiling
  floor_carpet_red:   ['room', 8, 0],   // red carpet
  floor_wood_gold:    ['room', 4, 4],   // gold-tinted wood (exec area)

  // ── Potted plants (from interiors sheet) ──
  // Row 0, col 8 area in Interiors has plants — adjust coords after visual check
  plant_large:        ['interiors', 0, 8,  1, 2],  // tall potted plant (1×2 tiles)
  plant_small:        ['interiors', 2, 8,  1, 1],  // small potted plant (1×1 tile)
  plant_round:        ['interiors', 2, 10, 1, 1],  // round bush pot

  // ── Office furniture (from office sheet) ──
  // Desk variations — coordinates approximate, verify visually
  desk_top_l:         ['office', 0, 0],    // top-left desk tile
  desk_top_r:         ['office', 0, 1],    // top-right desk tile
  desk_front_l:       ['office', 1, 0],    // front-left (with chair)
  desk_front_r:       ['office', 1, 1],    // front-right
  monitor_single:     ['office', 0, 4],    // single monitor on desk
  monitor_dual:       ['office', 0, 5],    // dual monitors
  chair_black:        ['office', 2, 0],    // office chair (black)
  chair_blue:         ['office', 2, 2],    // office chair (blue)
  whiteboard:         ['office', 3, 8, 2, 2], // whiteboard (2×2)
  bookshelf:          ['office', 6, 0, 2, 2], // bookshelf (2×2)
  printer:            ['office', 4, 6, 1, 1], // printer

  // ── Meeting room furniture ──
  conf_table_tl:      ['office', 10, 0],   // conference table top-left
  conf_table_tr:      ['office', 10, 1],   // conference table top-right
  conf_table_bl:      ['office', 11, 0],   // conference table bottom-left
  conf_table_br:      ['office', 11, 1],   // conference table bottom-right
  conf_chair_top:     ['office', 9, 0],    // chair facing down (top of table)
  conf_chair_bottom:  ['office', 12, 0],   // chair facing up (bottom of table)

  // ── Wall elements ──
  wall_base:          ['room', 0, 0, 1, 2],  // wall section (1×2 tiles tall)
  window_2x2:         ['room', 0, 4, 2, 2],  // window (2×2)

  // ── Character idle front (facing camera) ──
  // Each char sheet: idle is rows 0-1, col 0 = frame 1
  char_idle_frame0:   [null, 0, 0],  // sheet set at render time
  char_sit_frame0:    [null, 4, 0],  // sitting at desk
};

class TileAtlas {
  constructor(basePath = '/assets/office/tilesets') {
    this._basePath = basePath;
    this._images = {};      // sheetKey → HTMLImageElement
    this._ready = {};       // sheetKey → true/false
    this._loading = new Set();
  }

  /**
   * Preload a list of sheet keys. Returns a Promise that resolves when all loaded.
   */
  preload(keys) {
    const promises = keys.map(key => this._loadSheet(key));
    return Promise.allSettled(promises);
  }

  _loadSheet(key) {
    if (this._ready[key]) return Promise.resolve();
    if (this._loading.has(key)) {
      // Already in flight — wait for it
      return new Promise(resolve => {
        const check = () => {
          if (this._ready[key]) resolve();
          else setTimeout(check, 50);
        };
        check();
      });
    }

    this._loading.add(key);
    const path = SHEET_PATHS[key];
    if (!path) {
      console.warn(`[TileAtlas] Unknown sheet key: ${key}`);
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        this._images[key] = img;
        this._ready[key] = true;
        this._loading.delete(key);
        resolve();
      };
      img.onerror = () => {
        console.warn(`[TileAtlas] Failed to load: ${path}`);
        this._ready[key] = true;  // mark as attempted so we don't retry
        this._loading.delete(key);
        resolve();
      };
      img.src = `${this._basePath}/${path}`;
    });
  }

  /**
   * Draw a tile onto ctx.
   * @param {CanvasRenderingContext2D} ctx
   * @param {string} sheetKey  — key in SHEET_PATHS
   * @param {number} srcRow    — row in tileset (0-indexed)
   * @param {number} srcCol    — col in tileset (0-indexed)
   * @param {number} destX     — canvas X in pixels
   * @param {number} destY     — canvas Y in pixels
   * @param {number} [wTiles=1] — width in tiles
   * @param {number} [hTiles=1] — height in tiles
   */
  drawTile(ctx, sheetKey, srcRow, srcCol, destX, destY, wTiles = 1, hTiles = 1) {
    const img = this._images[sheetKey];
    if (!img) return;  // not loaded yet — skip silently

    ctx.drawImage(
      img,
      srcCol * TILE_SIZE,           // source X
      srcRow * TILE_SIZE,           // source Y
      TILE_SIZE * wTiles,           // source width
      TILE_SIZE * hTiles,           // source height
      Math.round(destX),            // dest X
      Math.round(destY),            // dest Y
      TILE_SIZE * wTiles,           // dest width (no scaling, 1:1)
      TILE_SIZE * hTiles,           // dest height
    );
  }

  /**
   * Draw a named tile def at pixel position.
   * @param {CanvasRenderingContext2D} ctx
   * @param {string} defKey  — key in TILE_DEFS
   * @param {number} destX
   * @param {number} destY
   * @param {string} [sheetOverride]  — override the sheet (used for char sheets)
   */
  drawDef(ctx, defKey, destX, destY, sheetOverride = null) {
    const def = TILE_DEFS[defKey];
    if (!def) { console.warn(`[TileAtlas] Unknown def: ${defKey}`); return; }
    const [sheet, row, col, w = 1, h = 1] = def;
    this.drawTile(ctx, sheetOverride || sheet, row, col, destX, destY, w, h);
  }

  isReady(key) {
    return !!this._ready[key];
  }
}

// Singleton
const tileAtlas = new TileAtlas();
```

- [ ] **Step 2: Add script tag to index.html**

In `frontend/index.html`, find the line that loads `office.js` and add before it:

```html
<script src="office-tileatlas.js"></script>
```

- [ ] **Step 3: Verify no console errors on load**

Start the server: `.venv/bin/python -m onemancompany`
Open the browser, check console — no JS syntax errors from `office-tileatlas.js`.

- [ ] **Step 4: Commit**

```bash
git add frontend/office-tileatlas.js frontend/index.html
git commit -m "feat: add TileAtlas spritesheet loader"
```

---

## Chunk 3: Camera System

### Task 4: Create Camera class

**Files:**
- Create: `frontend/office-camera.js`

The Camera class manages the viewport transform. It handles pan (drag), zoom (wheel), bounds clamping, and coordinate conversion. The OfficeRenderer applies `Camera.applyTransform(ctx)` before drawing tiles.

- [ ] **Step 1: Create `frontend/office-camera.js`**

```javascript
/**
 * office-camera.js — Pan + zoom camera for the office tilemap.
 *
 * World space: pixel coordinates in the tilemap (0,0 = top-left tile)
 * Screen space: CSS pixel coordinates on the canvas element
 *
 * The camera tracks a viewport into world space. The renderer calls
 * applyTransform(ctx) to set up the canvas transform, then draws tiles
 * in world coordinates. After drawing, call resetTransform(ctx).
 */

const CAMERA_ZOOM_MIN = 0.5;
const CAMERA_ZOOM_MAX = 3.0;
const CAMERA_ZOOM_STEP = 0.15;
const CAMERA_LERP_SPEED = 0.12;  // fraction per frame (0.12 = smooth but responsive)

class Camera {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {number} mapPixelW  — total map width in pixels (COLS * TILE)
   * @param {number} mapPixelH  — total map height in pixels (ROWS * TILE)
   */
  constructor(canvas, mapPixelW, mapPixelH) {
    this.canvas = canvas;
    this.mapPixelW = mapPixelW;
    this.mapPixelH = mapPixelH;

    // Current viewport state (in world pixels, top-left corner)
    this.x = 0;
    this.y = 0;
    this.zoom = 1.0;

    // Target state (camera lerps toward these)
    this._targetX = 0;
    this._targetY = 0;
    this._targetZoom = 1.0;

    // Drag state
    this._dragging = false;
    this._dragStartX = 0;
    this._dragStartY = 0;
    this._dragCamStartX = 0;
    this._dragCamStartY = 0;

    this._bindEvents();
  }

  _bindEvents() {
    const c = this.canvas;
    c.addEventListener('mousedown',  e => this._onMouseDown(e));
    c.addEventListener('mousemove',  e => this._onMouseMoveCamera(e));
    c.addEventListener('mouseup',    e => this._onMouseUp(e));
    c.addEventListener('mouseleave', e => this._onMouseUp(e));
    c.addEventListener('wheel',      e => this._onWheel(e), { passive: false });
    c.addEventListener('dblclick',   e => this._onDblClick(e));
  }

  _onMouseDown(e) {
    this._dragging = true;
    this._didDrag = false;  // track whether actual movement happened
    this._dragStartX = e.clientX;
    this._dragStartY = e.clientY;
    this._dragCamStartX = this._targetX;
    this._dragCamStartY = this._targetY;
    this.canvas.style.cursor = 'grabbing';
  }

  _onMouseMoveCamera(e) {
    if (!this._dragging) return;
    const dx = e.clientX - this._dragStartX;
    const dy = e.clientY - this._dragStartY;
    // Only start panning after 4px threshold — prevents treating a click as a pan
    if (!this._didDrag && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
    this._didDrag = true;
    this._targetX = this._dragCamStartX - dx / this._targetZoom;
    this._targetY = this._dragCamStartY - dy / this._targetZoom;
    this._clampTarget();
  }

  _onMouseUp(e) {
    this._dragging = false;
    this.canvas.style.cursor = 'default';
  }

  /**
   * Returns true if the last mousedown→mouseup sequence was a pan (not a click).
   * OfficeRenderer._onClick calls this to skip processing during pan release.
   */
  wasDrag() {
    return this._didDrag;
  }

  _onWheel(e) {
    e.preventDefault();

    const rect = this.canvas.getBoundingClientRect();
    // Cursor position in screen space (CSS pixels relative to canvas)
    const mouseScreenX = e.clientX - rect.left;
    const mouseScreenY = e.clientY - rect.top;

    // Cursor in world space before zoom change
    const mouseWorldX = this._targetX + mouseScreenX / this._targetZoom;
    const mouseWorldY = this._targetY + mouseScreenY / this._targetZoom;

    // Apply zoom
    const delta = e.deltaY > 0 ? -CAMERA_ZOOM_STEP : CAMERA_ZOOM_STEP;
    this._targetZoom = Math.max(CAMERA_ZOOM_MIN,
                        Math.min(CAMERA_ZOOM_MAX, this._targetZoom + delta));

    // Adjust position so the point under cursor stays fixed
    this._targetX = mouseWorldX - mouseScreenX / this._targetZoom;
    this._targetY = mouseWorldY - mouseScreenY / this._targetZoom;
    this._clampTarget();
  }

  _onDblClick(e) {
    // Double-click passes through to OfficeRenderer for interactivity.
    // Camera doesn't act here — the renderer calls centerOn() if needed.
  }

  _clampTarget() {
    const rect = this.canvas.getBoundingClientRect();
    // getBoundingClientRect returns 0 before first layout paint — skip clamp in that case
    if (!rect.width || !rect.height) return;
    const viewW = rect.width / this._targetZoom;
    const viewH = rect.height / this._targetZoom;

    this._targetX = Math.max(0, Math.min(Math.max(0, this.mapPixelW - viewW), this._targetX));
    this._targetY = Math.max(0, Math.min(Math.max(0, this.mapPixelH - viewH), this._targetY));
  }

  /**
   * Smoothly move camera to center on a world-pixel position.
   * @param {number} worldX
   * @param {number} worldY
   * @param {number} [targetZoom]  — optional zoom level to set
   */
  centerOn(worldX, worldY, targetZoom = null) {
    const rect = this.canvas.getBoundingClientRect();
    if (targetZoom !== null) this._targetZoom = Math.max(CAMERA_ZOOM_MIN, Math.min(CAMERA_ZOOM_MAX, targetZoom));
    this._targetX = worldX - (rect.width / 2) / this._targetZoom;
    this._targetY = worldY - (rect.height / 2) / this._targetZoom;
    this._clampTarget();
  }

  /**
   * Center camera on a tile grid position (gx, gy).
   */
  centerOnTile(gx, gy, targetZoom = null) {
    this.centerOn((gx + 0.5) * TILE_SIZE, (gy + 0.5) * TILE_SIZE, targetZoom);
  }

  /**
   * Update camera state (lerp toward target). Call once per frame.
   */
  update() {
    this.x    += (this._targetX    - this.x)    * CAMERA_LERP_SPEED;
    this.y    += (this._targetY    - this.y)    * CAMERA_LERP_SPEED;
    this.zoom += (this._targetZoom - this.zoom) * CAMERA_LERP_SPEED;
  }

  /**
   * Apply camera transform to ctx. Call before drawing world-space content.
   * Saves ctx state.
   */
  applyTransform(ctx) {
    ctx.save();
    ctx.scale(this.zoom, this.zoom);
    ctx.translate(-this.x, -this.y);
    ctx.imageSmoothingEnabled = false;
  }

  /**
   * Restore ctx state after drawing world-space content.
   */
  resetTransform(ctx) {
    ctx.restore();
  }

  /**
   * Convert screen-space coordinates (CSS pixels relative to canvas) to world pixels.
   */
  screenToWorld(sx, sy) {
    return {
      x: this.x + sx / this.zoom,
      y: this.y + sy / this.zoom,
    };
  }

  /**
   * Convert world pixels to screen-space (CSS pixels relative to canvas).
   */
  worldToScreen(wx, wy) {
    return {
      x: (wx - this.x) * this.zoom,
      y: (wy - this.y) * this.zoom,
    };
  }

  /**
   * Returns the range of tile columns and rows currently visible.
   * Add 1-tile margin for smooth scroll.
   */
  getVisibleTiles(mapCols, mapRows) {
    const rect = this.canvas.getBoundingClientRect();
    const tl = this.screenToWorld(0, 0);
    const br = this.screenToWorld(rect.width, rect.height);

    return {
      minCol: Math.max(0, Math.floor(tl.x / TILE_SIZE) - 1),
      maxCol: Math.min(mapCols - 1, Math.ceil(br.x / TILE_SIZE) + 1),
      minRow: Math.max(0, Math.floor(tl.y / TILE_SIZE) - 1),
      maxRow: Math.min(mapRows - 1, Math.ceil(br.y / TILE_SIZE) + 1),
    };
  }

  /**
   * Resize: called when canvas dimensions change.
   */
  resize(mapPixelW, mapPixelH) {
    this.mapPixelW = mapPixelW;
    this.mapPixelH = mapPixelH;
    this._clampTarget();
  }

  /**
   * Returns true if the camera is close enough to its target to be considered settled.
   */
  isSettled() {
    return (
      Math.abs(this.x - this._targetX) < 0.5 &&
      Math.abs(this.y - this._targetY) < 0.5 &&
      Math.abs(this.zoom - this._targetZoom) < 0.005
    );
  }
}
```

- [ ] **Step 2: Add script tag to index.html (after tileatlas, before office.js)**

```html
<script src="office-camera.js"></script>
```

- [ ] **Step 3: Quick sanity check — add temporary test to browser console**

After loading the page, paste in console:
```javascript
const cam = new Camera(document.getElementById('office-canvas'), 640, 480);
const w = cam.screenToWorld(320, 240);
console.assert(w.x === 320 && w.y === 240, 'screenToWorld at zoom=1 should return same coords');
cam._targetZoom = 2.0; cam.zoom = 2.0;
const w2 = cam.screenToWorld(320, 240);
console.assert(w2.x === 160 && w2.y === 120, 'screenToWorld at zoom=2 should halve coords');
console.log('Camera tests passed');
```

Expected: `Camera tests passed` in console.

- [ ] **Step 4: Commit**

```bash
git add frontend/office-camera.js frontend/index.html
git commit -m "feat: add Camera class with pan/zoom/bounds/coordinate transforms"
```

---

## Chunk 4: OfficeMap Data Layer

### Task 5: Create OfficeMap class

**Files:**
- Create: `frontend/office-map.js`

OfficeMap builds the tilemap grid from the layout API response and employee/room data.

- [ ] **Step 1: Create `frontend/office-map.js`**

```javascript
/**
 * office-map.js — Tilemap data layer.
 *
 * Converts the backend layout API response + employee list into a 2D grid
 * of tile definitions for the renderer to draw.
 *
 * Grid coordinate system:
 *   (col, row) — col = horizontal (0 = leftmost), row = vertical (0 = top)
 *   Matches backend gx/gy but row=0 is the wall row at the canvas top.
 *
 * Wall offset: backend gy=0 corresponds to canvas row 3 (first 3 rows are wall).
 *   canvas_row = backend_gy + WALL_ROWS
 */

const WALL_ROWS = 3;   // rows reserved for wall area at top of canvas
const MAP_COLS  = 20;  // fixed grid width (matches backend COLS=20)

class OfficeMap {
  constructor() {
    this.cols = MAP_COLS;
    this.rows = 15;  // default, updated from layout API

    // Flat arrays indexed [row * cols + col]
    this._floor    = [];  // floor tile def key per cell
    this._overlay  = [];  // decoration/furniture tile def key (or null)
    this._isDivider = []; // true for plant-divider columns

    // Zone metadata
    this.zones = [];          // from layout API
    this.dividerCols = new Set();  // set of divider column indices

    // Entity positions (grid coords in canvas space)
    this.employees     = [];  // [{id, col, row, charIdx, ...emp data}]
    this.meetingRooms  = [];  // [{id, col, row, ...room data}]
    this.tools         = [];  // [{id, col, row, ...tool data}]
    this.ceo           = null; // {col, row}
    this.executives    = [];  // [{id, col, row, role}]
  }

  /**
   * Rebuild the tilemap from fresh API data.
   * @param {object} layoutData  — response from /api/layout (office_layout)
   * @param {Array}  employees   — response from /api/employees
   * @param {Array}  rooms       — response from /api/rooms
   * @param {Array}  tools       — response from /api/tools (those with positions)
   */
  rebuild(layoutData, employees, rooms, tools) {
    const canvasRows = layoutData.canvas_rows || 15;
    this.rows = canvasRows;
    this.cols = MAP_COLS;
    this.zones = layoutData.zones || [];
    this.dividerCols = new Set(layoutData.divider_cols || []);

    // Initialize flat arrays
    const size = this.rows * this.cols;
    this._floor   = new Array(size).fill('floor_stone_gray');
    this._overlay = new Array(size).fill(null);
    this._isDivider = new Array(size).fill(false);

    // Paint department floor tiles
    this._buildFloors(layoutData);

    // Paint plant dividers
    this._buildDividers();

    // Build entity lists
    this._buildEntities(employees, rooms, tools, layoutData);
  }

  _buildFloors(layoutData) {
    const execRow = (layoutData.executive_row ?? 0) + WALL_ROWS;
    const execH   = layoutData.exec_row_height ?? 2;

    // Executive floor (rows execRow..execRow+execH-1)
    for (let row = execRow; row < execRow + execH; row++) {
      for (let col = 0; col < this.cols; col++) {
        this._setFloor(col, row, 'floor_wood_gold');
      }
    }

    // Department zones
    for (const zone of (layoutData.zones || [])) {
      const style = zone.floor_style || 'stone_gray';
      const floorKey = `floor_${style}`;
      const startRow = (layoutData.dept_start_row ?? 4) + WALL_ROWS;
      const endRow   = (layoutData.dept_end_row ?? 10) + WALL_ROWS;

      for (let row = startRow; row <= endRow + 3; row++) {  // +3 for overflow rows
        for (let col = zone.start_col; col < zone.end_col; col++) {
          this._setFloor(col, row, floorKey);
        }
      }
    }
  }

  _buildDividers() {
    for (const divCol of this.dividerCols) {
      for (let row = WALL_ROWS; row < this.rows; row++) {
        this._isDivider[row * this.cols + divCol] = true;
        // Overlay: alternate large/small plant
        this._overlay[row * this.cols + divCol] = (row % 2 === 0) ? 'plant_large' : 'plant_small';
      }
    }
  }

  _buildEntities(employees, rooms, tools, layoutData) {
    this.employees = [];
    this.meetingRooms = [];
    this.tools = [];
    this.ceo = null;
    this.executives = [];

    const execRow = (layoutData.executive_row ?? 0) + WALL_ROWS;

    // Map known exec IDs to roles
    const EXEC_ROLES = { '00001': 'CEO', '00002': 'HR', '00003': 'COO', '00004': 'EA', '00005': 'CSO' };

    for (const emp of (employees || [])) {
      const [gx, gy] = emp.desk_position || [0, 0];
      const row = gy + WALL_ROWS;
      const charIdx = this._charIdx(emp.id);

      const entry = { ...emp, col: gx, row, charIdx };

      if (emp.id === '00001') {
        this.ceo = entry;
      } else if (EXEC_ROLES[emp.id]) {
        this.executives.push({ ...entry, role: EXEC_ROLES[emp.id] });
      } else {
        this.employees.push(entry);
      }
    }

    for (const room of (rooms || [])) {
      const [gx, gy] = room.position || [0, 0];
      this.meetingRooms.push({ ...room, col: gx, row: gy + WALL_ROWS });
    }

    for (const tool of (tools || [])) {
      if (tool.desk_position && tool.has_icon) {
        const [gx, gy] = tool.desk_position;
        this.tools.push({ ...tool, col: gx, row: gy + WALL_ROWS });
      }
    }
  }

  /** Deterministic character sheet index (1-20) from employee ID string. */
  _charIdx(empId) {
    let h = 0;
    for (let i = 0; i < empId.length; i++) {
      h = (h * 31 + empId.charCodeAt(i)) >>> 0;
    }
    return (h % 20) + 1;  // 1..20
  }

  _setFloor(col, row, key) {
    if (col < 0 || col >= this.cols || row < 0 || row >= this.rows) return;
    this._floor[row * this.cols + col] = key;
  }

  getFloor(col, row)     { return this._floor[row * this.cols + col] || 'floor_stone_gray'; }
  getOverlay(col, row)   { return this._overlay[row * this.cols + col]; }
  isDivider(col, row)    { return this._isDivider[row * this.cols + col]; }
}
```

- [ ] **Step 2: Add script tag to index.html**

```html
<script src="office-map.js"></script>
```

- [ ] **Step 3: Verify syntax**

```bash
node -e "$(cat frontend/office-map.js); console.log('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add frontend/office-map.js frontend/index.html
git commit -m "feat: add OfficeMap tilemap data layer"
```

---

## Chunk 5: MiniMap Overlay

### Task 6: Create MiniMap class

**Files:**
- Create: `frontend/office-minimap.js`

- [ ] **Step 1: Create `frontend/office-minimap.js`**

```javascript
/**
 * office-minimap.js — 120×80px viewport overview overlay.
 *
 * Renders in the bottom-right corner of the main canvas.
 * Shows the full map at reduced scale with a rectangle indicating
 * the current camera viewport. Click to jump.
 */

const MINIMAP_W = 120;
const MINIMAP_H = 80;
const MINIMAP_MARGIN = 8;
const MINIMAP_BG = 'rgba(0, 0, 0, 0.65)';
const MINIMAP_BORDER = '#444';
const MINIMAP_VIEWPORT_COLOR = 'rgba(255, 255, 255, 0.35)';
const MINIMAP_VIEWPORT_BORDER = 'rgba(255, 255, 255, 0.8)';
const MINIMAP_EMPLOYEE_COLOR = '#44ff88';
const MINIMAP_EXEC_COLOR = '#ffd700';
const MINIMAP_ROOM_COLOR = '#4488ff';

class MiniMap {
  /**
   * @param {OfficeMap} officeMap
   * @param {Camera} camera
   */
  constructor(officeMap, camera) {
    this.map = officeMap;
    this.camera = camera;
    this._clickBound = this._onClick.bind(this);
  }

  /**
   * Attach click listener to the main canvas for minimap interaction.
   */
  attach(canvas) {
    this._canvas = canvas;
    canvas.addEventListener('click', this._clickBound);
  }

  detach() {
    if (this._canvas) this._canvas.removeEventListener('click', this._clickBound);
  }

  /**
   * Draw the minimap onto the provided rendering context.
   * Call this AFTER resetTransform (in screen space).
   */
  draw(ctx, canvasWidth, canvasHeight) {
    const mx = canvasWidth - MINIMAP_W - MINIMAP_MARGIN;
    const my = canvasHeight - MINIMAP_H - MINIMAP_MARGIN;

    // Background
    ctx.fillStyle = MINIMAP_BG;
    ctx.fillRect(mx, my, MINIMAP_W, MINIMAP_H);
    ctx.strokeStyle = MINIMAP_BORDER;
    ctx.lineWidth = 1;
    ctx.strokeRect(mx + 0.5, my + 0.5, MINIMAP_W, MINIMAP_H);

    // Scale factors: map → minimap
    const mapW = this.map.cols * TILE_SIZE;
    const mapH = this.map.rows * TILE_SIZE;
    const sx = MINIMAP_W / mapW;
    const sy = MINIMAP_H / mapH;

    // Department zone floors
    for (const zone of this.map.zones) {
      const x = zone.start_col * TILE_SIZE * sx;
      const w = (zone.end_col - zone.start_col) * TILE_SIZE * sx;
      ctx.fillStyle = zone.floor1 || '#333';
      ctx.fillRect(mx + x, my, w, MINIMAP_H);
    }

    // Divider lines
    ctx.fillStyle = '#2a4a2a';
    for (const divCol of this.map.dividerCols) {
      const x = divCol * TILE_SIZE * sx;
      ctx.fillRect(mx + x, my, Math.max(1, TILE_SIZE * sx), MINIMAP_H);
    }

    // Employee dots
    for (const emp of this.map.employees) {
      const ex = (emp.col + 0.5) * TILE_SIZE * sx;
      const ey = (emp.row + 0.5) * TILE_SIZE * sy;
      ctx.fillStyle = MINIMAP_EMPLOYEE_COLOR;
      ctx.fillRect(mx + ex - 1, my + ey - 1, 2, 2);
    }

    // Executive dots (gold)
    ctx.fillStyle = MINIMAP_EXEC_COLOR;
    for (const exec of this.map.executives) {
      const ex = (exec.col + 0.5) * TILE_SIZE * sx;
      const ey = (exec.row + 0.5) * TILE_SIZE * sy;
      ctx.fillRect(mx + ex - 1, my + ey - 1, 2, 2);
    }
    if (this.map.ceo) {
      const ex = (this.map.ceo.col + 0.5) * TILE_SIZE * sx;
      const ey = (this.map.ceo.row + 0.5) * TILE_SIZE * sy;
      ctx.fillRect(mx + ex - 1, my + ey - 1, 3, 3);
    }

    // Meeting room dots
    ctx.fillStyle = MINIMAP_ROOM_COLOR;
    for (const room of this.map.meetingRooms) {
      const rx = room.col * TILE_SIZE * sx;
      const ry = room.row * TILE_SIZE * sy;
      ctx.fillRect(mx + rx, my + ry, Math.max(2, 2 * TILE_SIZE * sx), Math.max(2, 2 * TILE_SIZE * sy));
    }

    // Viewport rectangle
    const cam = this.camera;
    const vx  = cam.x * sx;
    const vy  = cam.y * sy;
    const rect = this._canvas?.getBoundingClientRect();
    const vw  = rect ? (rect.width  / cam.zoom) * sx : MINIMAP_W * 0.3;
    const vh  = rect ? (rect.height / cam.zoom) * sy : MINIMAP_H * 0.3;

    ctx.fillStyle = MINIMAP_VIEWPORT_COLOR;
    ctx.fillRect(mx + vx, my + vy, Math.min(vw, MINIMAP_W), Math.min(vh, MINIMAP_H));
    ctx.strokeStyle = MINIMAP_VIEWPORT_BORDER;
    ctx.lineWidth = 1;
    ctx.strokeRect(mx + vx + 0.5, my + vy + 0.5, Math.min(vw, MINIMAP_W), Math.min(vh, MINIMAP_H));
  }

  /** Returns true if screen coords (sx, sy) are within the minimap area. */
  _inMinimap(sx, sy, canvasW, canvasH) {
    const mx = canvasW - MINIMAP_W - MINIMAP_MARGIN;
    const my = canvasH - MINIMAP_H - MINIMAP_MARGIN;
    return sx >= mx && sx <= mx + MINIMAP_W && sy >= my && sy <= my + MINIMAP_H;
  }

  _onClick(e) {
    const rect = this._canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    if (!this._inMinimap(sx, sy, rect.width, rect.height)) return;

    // Map click position within minimap to world position
    const mx = rect.width  - MINIMAP_W - MINIMAP_MARGIN;
    const my = rect.height - MINIMAP_H - MINIMAP_MARGIN;
    const relX = (sx - mx) / MINIMAP_W;
    const relY = (sy - my) / MINIMAP_H;

    const worldX = relX * this.map.cols * TILE_SIZE;
    const worldY = relY * this.map.rows * TILE_SIZE;
    this.camera.centerOn(worldX, worldY);

    e.stopPropagation();  // prevent OfficeRenderer click handler from firing
  }
}
```

- [ ] **Step 2: Add script tag to index.html**

```html
<script src="office-minimap.js"></script>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/office-minimap.js frontend/index.html
git commit -m "feat: add MiniMap overlay with viewport indicator and click-to-jump"
```

---

## Chunk 6: OfficeRenderer Rewrite

### Task 7: Rewrite office.js

**Files:**
- Modify: `frontend/office.js` — complete rewrite preserving public API

This is the main integration step. The rewritten `office.js` wires together TileAtlas, Camera, OfficeMap, and MiniMap. It preserves the `OfficeRenderer` class name and `updateState()` interface so `app.js` doesn't change.

**Key behaviors to preserve:**
- `window.officeRenderer = new OfficeRenderer('office-canvas')` at file end
- `officeRenderer.updateState({employees, meeting_rooms, tools, office_layout})` — same signature
- Click handlers: bulletin board (tiles 5-7, row 0-1), project wall (tiles 12-14, row 0-1), meeting rooms, employees
- Hover tooltips via `_updateTooltip()`
- Particle effects on new hires
- Animation frame counter for sprite animation

- [ ] **Step 1: Identify all interactive element positions to preserve**

From current `office.js` `_onClick`:
```
Bulletin board: tx 5-7, ty 0-1 (canvas tiles)
Project wall:   tx 12-14, ty 0-1 (canvas tiles)
Meeting rooms:  tx >= rx, tx <= rx+1, ty >= ry+3, ty <= ry+5
Employees:      tx === ex, ty in {ey+2, ey+3, ey+4}
Tools:          tx === tool.col, ty in {tool.row, tool.row+1}
```

These use the current fixed tile coordinates. With Camera, we need `Camera.screenToWorld()` first, then divide by TILE_SIZE to get tile coords.

- [ ] **Step 2: Write the new office.js**

```javascript
/**
 * office.js — Tileset-based office canvas renderer (rewrite)
 *
 * Uses LimeZu Modern Office + Modern Interiors 32×32 tilesets.
 * Requires: office-tileatlas.js, office-camera.js, office-map.js, office-minimap.js
 *
 * Public API (unchanged from previous version):
 *   new OfficeRenderer('office-canvas')
 *   officeRenderer.updateState({employees, meeting_rooms, tools, office_layout})
 */

// Re-export tile size constant (other modules use TILE_SIZE from tileatlas)
const TILE = TILE_SIZE;   // = 32, from office-tileatlas.js
const COLS = 20;
let ROWS = 18;  // updated dynamically from layout API

// Sheets to preload at startup
const PRELOAD_SHEETS = [
  'office', 'room', 'interiors',
  ...Array.from({length: 20}, (_, i) => `char${String(i+1).padStart(2,'0')}`),
];

// Role colors (for name tags and badges)
const ROLE_COLORS = {
  'CEO': '#ffd700',
  'HR':  '#5599ff',
  'COO': '#ff9944',
  'EA':  '#44ddaa',
  'CSO': '#bb55ff',
};

// Floor tile style → actual TILE_DEFS key mapping
const FLOOR_TILE_KEY = {
  stone_gray:  'floor_stone_gray',
  stone_blue:  'floor_stone_blue',
  wood_warm:   'floor_wood_warm',
  tile_green:  'floor_tile_green',
  carpet_red:  'floor_carpet_red',
  wood_gold:   'floor_wood_gold',
};

class OfficeRenderer {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.ctx.imageSmoothingEnabled = false;

    // State (same fields as before)
    this.state = {
      employees: [], tools: [], meeting_rooms: [], office_layout: {},
      ceo_tasks: [], activity_log: [],
    };

    // Subsystems
    this.officeMap = new OfficeMap();
    this.camera    = new Camera(this.canvas, COLS * TILE, ROWS * TILE);
    this.miniMap   = new MiniMap(this.officeMap, this.camera);
    this.miniMap.attach(this.canvas);

    // Preload all tilesets
    tileAtlas.preload(PRELOAD_SHEETS).then(() => {
      console.log('[OfficeRenderer] All tilesets loaded');
    });

    // Animation
    this.animFrame = 0;
    this.particles = [];

    // Hover/tooltip state
    this.hoverTile = null;

    // Avatar image cache (for custom employee photos)
    this._avatarImages = {};

    // Event listeners
    this.canvas.addEventListener('mousemove', e => this._onMouseMove(e));
    this.canvas.addEventListener('mouseleave', () => {
      this.hoverTile = null;
      const tt = document.getElementById('tooltip');
      if (tt) tt.classList.add('hidden');
    });
    this.canvas.addEventListener('click', e => this._onClick(e));

    this._resizeCanvas();
    window.addEventListener('resize', () => this._resizeCanvas());
    new ResizeObserver(() => this._resizeCanvas()).observe(this.canvas.parentElement);

    this.loop();
  }

  _resizeCanvas() {
    const parent = this.canvas.parentElement;
    const w = parent.clientWidth;
    const h = parent.clientHeight - 45;
    if (w <= 0 || h <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    this.canvas.style.width  = w + 'px';
    this.canvas.style.height = h + 'px';
    this.canvas.width  = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.imageSmoothingEnabled = false;
    this.dpr = dpr;

    this.camera.resize(this.officeMap.cols * TILE, this.officeMap.rows * TILE);
  }

  /**
   * Update state from API data.
   * Same signature as before — called by app.js.
   */
  updateState(newState) {
    const prevIds = new Set((this.state.employees || []).map(e => e.id));

    Object.assign(this.state, newState);

    // Detect new hires for particle effects
    const newEmps = (newState.employees || []).filter(e => !prevIds.has(e.id));

    // Update ROWS if layout changed
    const layout = this.state.office_layout || {};
    if (layout.canvas_rows && layout.canvas_rows !== ROWS) {
      ROWS = layout.canvas_rows;
    }

    // Rebuild tilemap
    this.officeMap.rebuild(
      layout,
      this.state.employees,
      this.state.meeting_rooms,
      this.state.tools,
    );

    this.camera.resize(this.officeMap.cols * TILE, this.officeMap.rows * TILE);

    // Spawn hire particles
    for (const emp of newEmps) {
      this._spawnHireParticles(emp);
    }

    // Preload custom avatars
    for (const emp of (newState.employees || [])) {
      if (!this._avatarImages[emp.id]) {
        const img = new Image();
        img.src = `/api/employees/${emp.id}/avatar`;
        img.onload  = () => { this._avatarImages[emp.id] = img; };
        img.onerror = () => { this._avatarImages[emp.id] = null; };
      }
    }
  }

  // ─── Coordinate helpers ─────────────────────────────────────────────────────

  /** Convert mouse event to screen coords (CSS pixels relative to canvas). */
  _screenCoords(e) {
    const rect = this.canvas.getBoundingClientRect();
    return { sx: e.clientX - rect.left, sy: e.clientY - rect.top };
  }

  /** Convert screen coords to tile grid (col, row). */
  _screenToTile(sx, sy) {
    const w = this.camera.screenToWorld(sx, sy);
    return { tx: Math.floor(w.x / TILE), ty: Math.floor(w.y / TILE) };
  }

  // ─── Event handlers ─────────────────────────────────────────────────────────

  _onMouseMove(e) {
    const { sx, sy } = this._screenCoords(e);
    const { tx, ty } = this._screenToTile(sx, sy);
    this.hoverTile = { x: tx, y: ty, screenX: e.clientX, screenY: e.clientY };
    this._updateTooltip();
  }

  _onClick(e) {
    // Skip if the camera was being panned (drag threshold exceeded)
    if (this.camera.wasDrag()) return;

    const { sx, sy } = this._screenCoords(e);

    // Minimap handles its own clicks (stops propagation) — so by the time
    // we're here, it wasn't a minimap click.

    const { tx, ty } = this._screenToTile(sx, sy);

    // ── Bulletin board (canvas tiles 5-7, row 0-1) ──
    if (tx >= 5 && tx <= 7 && ty >= 0 && ty <= 1) {
      window.app?.openWorkflowPanel?.();
      return;
    }

    // ── Project wall (canvas tiles 12-14, row 0-1) ──
    if (tx >= 12 && tx <= 14 && ty >= 0 && ty <= 1) {
      window.app?.openProjectWall?.();
      return;
    }

    // ── Meeting rooms ──
    for (const room of this.officeMap.meetingRooms) {
      if (tx >= room.col && tx <= room.col + 1 &&
          ty >= room.row && ty <= room.row + 2) {
        window.app?.openMeetingRoom?.(room);
        return;
      }
    }

    // ── Employees ──
    for (const emp of [...this.officeMap.employees, ...this.officeMap.executives, this.officeMap.ceo].filter(Boolean)) {
      if (tx === emp.col && (ty === emp.row - 1 || ty === emp.row || ty === emp.row + 1)) {
        window.app?.openEmployeeDetail?.(emp);
        return;
      }
    }

    // ── Tools ──
    for (const tool of this.officeMap.tools) {
      if (tx === tool.col && (ty === tool.row || ty === tool.row + 1)) {
        window.app?.openToolDetail?.(tool.id);  // app.js expects a tool ID string, not object
        return;
      }
    }
  }

  // ─── Tooltip ────────────────────────────────────────────────────────────────

  _updateTooltip() {
    const tooltip = document.getElementById('tooltip');
    if (!tooltip || !this.hoverTile) return;

    const { x: tx, y: ty, screenX, screenY } = this.hoverTile;
    let text = null;

    // Check employees
    for (const emp of [...this.officeMap.employees, ...this.officeMap.executives, this.officeMap.ceo].filter(Boolean)) {
      if (tx === emp.col && Math.abs(ty - emp.row) <= 1) {
        text = `${emp.nickname || emp.name} · ${emp.role || ''} L${emp.level || ''}`;
        break;
      }
    }

    // Check rooms
    if (!text) {
      for (const room of this.officeMap.meetingRooms) {
        if (tx >= room.col && tx <= room.col + 1 && ty >= room.row && ty <= room.row + 2) {
          text = `${room.name} — ${room.booked ? '🔴 Booked' : '🟢 Free'}`;
          break;
        }
      }
    }

    // Check tools
    if (!text) {
      for (const tool of this.officeMap.tools) {
        if (tx === tool.col && Math.abs(ty - tool.row) <= 1) {
          text = tool.name;
          break;
        }
      }
    }

    if (text) {
      tooltip.textContent = text;
      tooltip.style.left = (screenX + 12) + 'px';
      tooltip.style.top  = (screenY - 8) + 'px';
      tooltip.classList.remove('hidden');
    } else {
      tooltip.classList.add('hidden');
    }
  }

  // ─── Particles ──────────────────────────────────────────────────────────────

  _spawnHireParticles(emp) {
    const [gx, gy] = emp.desk_position || [0, 0];
    const wx = (gx + 0.5) * TILE;
    const wy = (gy + WALL_ROWS + 0.5) * TILE;
    for (let i = 0; i < 20; i++) {
      this.particles.push({
        x: wx, y: wy,
        vx: (Math.random() - 0.5) * 3,
        vy: (Math.random() - 0.5) * 3 - 1,
        life: 1.0,
        color: `hsl(${Math.random() * 360}, 100%, 70%)`,
      });
    }
  }

  _updateParticles() {
    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.1;
      p.life -= 0.025;
      if (p.life <= 0) this.particles.splice(i, 1);
    }
  }

  _drawParticles(ctx) {
    for (const p of this.particles) {
      ctx.globalAlpha = p.life;
      ctx.fillStyle = p.color;
      ctx.fillRect(Math.round(p.x), Math.round(p.y), 3, 3);
    }
    ctx.globalAlpha = 1;
  }

  // ─── Rendering ──────────────────────────────────────────────────────────────

  render() {
    const ctx = this.ctx;
    const rect = this.canvas.getBoundingClientRect();
    const W = rect.width, H = rect.height;

    // Clear
    ctx.clearRect(0, 0, W, H);

    // Update camera
    this.camera.update();

    // ── World space (camera transform applied) ──────────────────────
    this.camera.applyTransform(ctx);

    const vis = this.camera.getVisibleTiles(this.officeMap.cols, this.officeMap.rows);

    // 1. Floor tiles
    this._drawFloor(ctx, vis);

    // 2. Wall area (rows 0-2)
    this._drawWalls(ctx, vis);

    // 3. Plant dividers
    this._drawDividers(ctx, vis);

    // 4. Static decorations (bulletin board, project wall, windows, plants)
    this._drawWallDecorations(ctx);

    // 5. Furniture + characters (Y-sorted)
    this._drawEntities(ctx);

    // 6. Particles
    this._drawParticles(ctx);

    this.camera.resetTransform(ctx);
    // ── Screen space ────────────────────────────────────────────────

    // 7. MiniMap
    this.miniMap.draw(ctx, W, H);
  }

  _drawFloor(ctx, vis) {
    for (let row = Math.max(WALL_ROWS, vis.minRow); row <= vis.maxRow; row++) {
      for (let col = vis.minCol; col <= vis.maxCol; col++) {
        if (this.officeMap.isDivider(col, row)) continue;  // handled by _drawDividers

        const floorKey = this.officeMap.getFloor(col, row);
        const px = col * TILE;
        const py = row * TILE;

        if (!tileAtlas.isReady('room')) {
          // Fallback: simple colored rectangle
          ctx.fillStyle = this._fallbackFloorColor(floorKey);
          ctx.fillRect(px, py, TILE, TILE);
        } else {
          tileAtlas.drawDef(ctx, floorKey, px, py);
        }
      }
    }
  }

  _fallbackFloorColor(key) {
    const map = {
      floor_stone_gray: '#3a3a4e',
      floor_stone_blue: '#3a4a5e',
      floor_wood_warm:  '#5e3a1a',
      floor_tile_green: '#1a3e2a',
      floor_carpet_red: '#3e1a1a',
      floor_wood_gold:  '#2a2a1a',
    };
    return map[key] || '#333';
  }

  _drawWalls(ctx, vis) {
    const wallRows = Math.min(WALL_ROWS, vis.maxRow + 1);
    for (let row = vis.minRow; row < wallRows; row++) {
      for (let col = vis.minCol; col <= vis.maxCol; col++) {
        const px = col * TILE, py = row * TILE;
        // Wall tile from room builder, or fallback
        if (tileAtlas.isReady('room')) {
          tileAtlas.drawTile(ctx, 'room', 0, col % 4, px, py);
        } else {
          ctx.fillStyle = row === 0 ? '#161428' : row === 1 ? '#1e1a34' : '#242040';
          ctx.fillRect(px, py, TILE, TILE);
        }
      }
    }

    // Bulletin board (drawn at tiles 5-7, rows 0-1)
    // Project wall (drawn at tiles 12-14, rows 0-1)
    // These are handled in _drawWallDecorations
  }

  _drawDividers(ctx, vis) {
    for (const divCol of this.officeMap.dividerCols) {
      if (divCol < vis.minCol || divCol > vis.maxCol) continue;
      const px = divCol * TILE;
      for (let row = Math.max(WALL_ROWS, vis.minRow); row <= vis.maxRow; row++) {
        const py = row * TILE;
        // Dark base
        ctx.fillStyle = '#1a2a1a';
        ctx.fillRect(px, py, TILE, TILE);

        // Plant sprite from tileset, alternating large/small
        const defKey = (row % 2 === 0) ? 'plant_large' : 'plant_small';
        if (tileAtlas.isReady('interiors')) {
          tileAtlas.drawDef(ctx, defKey, px, py);
        } else {
          // Pixel-art fallback plant
          this._drawFallbackPlant(ctx, px, py);
        }
      }
    }
  }

  _drawFallbackPlant(ctx, px, py) {
    ctx.fillStyle = '#8B4513';
    ctx.fillRect(px + 10, py + 20, 12, 10);
    ctx.fillStyle = '#228B22';
    ctx.fillRect(px + 6, py + 4, 20, 18);
    ctx.fillStyle = '#2d9b2d';
    ctx.fillRect(px + 10, py + 1, 12, 8);
  }

  _drawWallDecorations(ctx) {
    // Bulletin board at tiles (5, 0)-(7, 1)
    ctx.fillStyle = '#6b4226';
    ctx.fillRect(5 * TILE, 0, 3 * TILE, 2 * TILE);
    ctx.fillStyle = '#4a2e18';
    ctx.strokeStyle = '#4a2e18';
    ctx.strokeRect(5 * TILE, 0, 3 * TILE, 2 * TILE);
    ctx.fillStyle = '#f0e8d0';
    ctx.font = `${TILE * 0.5}px monospace`;
    ctx.fillText('📋', 5 * TILE + 4, TILE);

    // Project wall at tiles (12, 0)-(14, 1)
    ctx.fillStyle = '#1a3a2a';
    ctx.fillRect(12 * TILE, 0, 3 * TILE, 2 * TILE);
    ctx.fillStyle = '#0d2a1a';
    ctx.strokeStyle = '#0d2a1a';
    ctx.strokeRect(12 * TILE, 0, 3 * TILE, 2 * TILE);
    ctx.fillStyle = '#d4e8d0';
    ctx.fillText('📋', 12 * TILE + 4, TILE);

    // Windows at tiles 7-8 and 10-11
    for (const wx of [7, 10]) {
      ctx.fillStyle = '#87CEEB';
      ctx.fillRect(wx * TILE, 0, 2 * TILE, 2 * TILE);
      ctx.fillStyle = '#5a9aba';
      ctx.fillRect(wx * TILE + 2, 2, 2 * TILE - 4, 2 * TILE - 4);
    }
  }

  _drawEntities(ctx) {
    // Collect all renderable entities with Y position for sorting
    const entities = [];

    // CEO
    if (this.officeMap.ceo) {
      entities.push({ type: 'employee', data: this.officeMap.ceo, y: this.officeMap.ceo.row });
    }

    // Executives + employees
    for (const emp of [...this.officeMap.executives, ...this.officeMap.employees]) {
      entities.push({ type: 'employee', data: emp, y: emp.row });
    }

    // Meeting rooms
    for (const room of this.officeMap.meetingRooms) {
      entities.push({ type: 'room', data: room, y: room.row });
    }

    // Tools
    for (const tool of this.officeMap.tools) {
      entities.push({ type: 'tool', data: tool, y: tool.row });
    }

    // Sort by Y (painter's algorithm — back to front)
    entities.sort((a, b) => a.y - b.y);

    for (const entity of entities) {
      if (entity.type === 'employee') {
        this._drawDesk(ctx, entity.data);
        this._drawCharacter(ctx, entity.data);
      } else if (entity.type === 'room') {
        this._drawMeetingRoom(ctx, entity.data);
      } else if (entity.type === 'tool') {
        this._drawTool(ctx, entity.data);
      }
    }
  }

  _drawDesk(ctx, emp) {
    const px = emp.col * TILE;
    const py = emp.row * TILE;

    if (tileAtlas.isReady('office')) {
      // Chair behind desk
      tileAtlas.drawDef(ctx, 'chair_black', px, py - TILE);
      // Desk (top row)
      tileAtlas.drawDef(ctx, 'desk_top_l', px, py);
      tileAtlas.drawDef(ctx, 'desk_top_r', px + TILE, py);
      // Monitor
      tileAtlas.drawDef(ctx, 'monitor_single', px, py - TILE);
    } else {
      // Fallback hand-drawn desk
      ctx.fillStyle = '#9a7420';
      ctx.fillRect(px + 2, py, TILE - 4, TILE * 0.4);
      ctx.fillStyle = '#6d5210';
      ctx.fillRect(px + 2, py + TILE * 0.4, TILE - 4, 2);
      // Monitor
      ctx.fillStyle = '#333355';
      ctx.fillRect(px + 6, py - 12, 20, 12);
      ctx.fillStyle = '#22ddff';
      ctx.fillRect(px + 8, py - 10, 16, 8);
    }
  }

  _drawCharacter(ctx, emp) {
    const isCEO = emp.id === '00001';
    const isExec = ['00002','00003','00004','00005'].includes(emp.id);
    const charIdx = emp.charIdx || 1;
    const sheetKey = `char${String(charIdx).padStart(2, '0')}`;

    // Character sprite origin: 1 tile above the desk (character occupies ~1 tile height)
    // emp.row is in canvas-row space (already includes WALL_ROWS offset)
    const px = emp.col * TILE;
    const charPy = (emp.row - 1) * TILE;  // top of character sprite in world pixels

    if (tileAtlas.isReady(sheetKey)) {
      // Use idle front facing frame (row 0, col 0 = frame 0)
      // Animate: alternate frame 0 and frame 1 every 30 ticks
      const frame = Math.floor(this.animFrame / 30) % 2;
      tileAtlas.drawTile(ctx, sheetKey, 0, frame, px, charPy);
    } else {
      // Fallback: simple colored rectangle person
      ctx.fillStyle = isCEO ? '#ffd700' : (isExec ? '#5599ff' : '#4488ff');
      ctx.fillRect(px + 6, charPy + 4, 20, 28);
      ctx.fillStyle = '#f5cc8e';
      ctx.fillRect(px + 8, charPy, 16, 16);
    }

    // Name tag (below character, at desk row)
    this._drawNameTag(ctx, emp, px, charPy);

    // Status badge (above character head)
    this._drawStatusBadge(ctx, emp, px, charPy);

    // CEO crown (at top of character head)
    if (isCEO) this._drawCrown(ctx, px, charPy);
  }

  _drawNameTag(ctx, emp, px, py) {
    const name = (emp.nickname || emp.name || '').slice(0, 6);
    const role = emp.role || '';
    const roleColor = ROLE_COLORS[role] || '#aaaaaa';

    ctx.fillStyle = 'rgba(20,20,30,0.85)';
    ctx.fillRect(px - 2, py + TILE - 8, TILE + 4, 10);
    ctx.fillStyle = roleColor;
    ctx.font = '8px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(name, px + TILE / 2, py + TILE);
    ctx.textAlign = 'left';
  }

  _drawStatusBadge(ctx, emp, px, py) {
    const status = emp.status;
    if (status === 'working') {
      // Animated dots
      const dots = '.'.repeat((Math.floor(this.animFrame / 15) % 3) + 1);
      ctx.fillStyle = '#44ff88';
      ctx.font = '9px monospace';
      ctx.fillText(dots, px + TILE - 6, py - TILE + 4);
    } else if (status === 'idle' || !status) {
      // zzz
      if (Math.floor(this.animFrame / 45) % 2 === 0) {
        ctx.fillStyle = '#aaaaff';
        ctx.font = '8px monospace';
        ctx.fillText('z', px + TILE - 4, py - TILE + 2);
      }
    }
  }

  _drawCrown(ctx, px, py) {
    // Simple pixel crown above CEO head
    const glint = Math.floor(this.animFrame / 20) % 2;
    ctx.fillStyle = '#ffd700';
    ctx.fillRect(px + 6,  py - 6, 3, 4);  // left peak
    ctx.fillRect(px + 14, py - 6, 3, 4);  // right peak
    ctx.fillRect(px + 10, py - 8, 3, 6);  // center peak
    ctx.fillRect(px + 5,  py - 2, 22, 3); // crown base
    if (glint) {
      ctx.fillStyle = '#ffffaa';
      ctx.fillRect(px + 11, py - 7, 1, 2);
    }
  }

  _drawMeetingRoom(ctx, room) {
    const px = room.col * TILE;
    const py = room.row * TILE;

    if (tileAtlas.isReady('office')) {
      // Conference table (2×2) starting at py+TILE
      tileAtlas.drawDef(ctx, 'conf_table_tl', px,        py + TILE);
      tileAtlas.drawDef(ctx, 'conf_table_tr', px + TILE, py + TILE);
      tileAtlas.drawDef(ctx, 'conf_table_bl', px,        py + 2*TILE);
      tileAtlas.drawDef(ctx, 'conf_table_br', px + TILE, py + 2*TILE);
    } else {
      // Fallback
      ctx.fillStyle = room.booked ? '#440020' : '#003020';
      ctx.fillRect(px, py, 2 * TILE, 3 * TILE);
      ctx.fillStyle = '#5c4420';
      ctx.fillRect(px + 4, py + TILE, 2 * TILE - 8, 2 * TILE - 8);
    }

    // Room name label
    ctx.fillStyle = room.booked ? '#ff4455' : '#00ff88';
    ctx.font = '8px monospace';
    ctx.fillText((room.name || 'Room').slice(0, 8), px + 2, py + TILE - 2);
  }

  _drawTool(ctx, tool) {
    const px = tool.col * TILE;
    const py = tool.row * TILE;
    ctx.fillStyle = '#223322';
    ctx.fillRect(px + 2, py + 2, TILE - 4, TILE - 4);
    ctx.fillStyle = '#44ff88';
    ctx.font = '8px monospace';
    ctx.fillText((tool.name || '').slice(0, 4).toUpperCase(), px + 4, py + TILE / 2 + 4);
  }

  // ─── Main loop ──────────────────────────────────────────────────────────────

  loop() {
    this.animFrame++;
    this._updateParticles();
    this.render();
    requestAnimationFrame(() => this.loop());
  }
}

// Instantiate globally (app.js uses window.officeRenderer)
window.officeRenderer = new OfficeRenderer('office-canvas');
```

- [ ] **Step 3: Update index.html script order**

Ensure scripts load in this order:

```html
<script src="office-tileatlas.js"></script>
<script src="office-camera.js"></script>
<script src="office-map.js"></script>
<script src="office-minimap.js"></script>
<script src="office.js"></script>
```

- [ ] **Step 4: Restart server and verify no console errors**

```bash
pkill -f "python -m onemancompany" 2>/dev/null; .venv/bin/python -m onemancompany &
sleep 2; echo "Server started"
```

Open http://localhost:8000, check browser console for errors.

- [ ] **Step 5: Verify tile coordinates are correct**

Open the tileset images and cross-check:
- `room` sheet: verify floor tile row/col indices at which styles appear
- `office` sheet: verify desk, monitor, chair tile positions
- Update `TILE_DEFS` in `office-tileatlas.js` with correct coordinates

To inspect: read the PNG and count 32px grid squares from top-left.

- [ ] **Step 6: Commit**

```bash
git add frontend/office.js frontend/index.html
git commit -m "feat: rewrite office.js with tileset-based rendering, camera, and mini-map"
```

---

## Chunk 7: Tile Coordinate Calibration

### Task 8: Verify and fix tile coordinates

This task is done in-browser by visual inspection. The tile coordinates in `TILE_DEFS` are best-effort estimates that must be validated against the actual tilesheet images.

**Files:**
- Modify: `frontend/office-tileatlas.js`

- [ ] **Step 1: Open tileset images for reference**

Read the key tilesheet images in your editor to see the actual grid:
- `frontend/assets/office/tilesets/Modern_Office_Revamped_v1.2/Modern_Office_32x32.png`
- `frontend/assets/office/tilesets/Modern_Office_Revamped_v1.2/1_Room_Builder_Office/Room_Builder_Office_32x32.png`
- `frontend/assets/office/tilesets/moderninteriors-win/1_Interiors/32x32/Interiors_32x32.png`

- [ ] **Step 2: Write a debug helper to enumerate tiles**

Temporarily add to browser console to visualize tilesheet grid:

```javascript
function debugSheet(sheetKey, startRow, endRow) {
  const img = tileAtlas._images[sheetKey];
  if (!img) { console.log('Not loaded:', sheetKey); return; }
  const c = document.createElement('canvas');
  c.width = img.width; c.height = img.height;
  document.body.appendChild(c);
  const ctx = c.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 0, 0);
  ctx.strokeStyle = 'red';
  ctx.lineWidth = 0.5;
  for (let r = startRow; r <= endRow; r++) {
    for (let col = 0; col < img.width/32; col++) {
      ctx.strokeRect(col*32, r*32, 32, 32);
      ctx.fillStyle='red'; ctx.font='8px sans-serif';
      ctx.fillText(`${r},${col}`, col*32+2, r*32+10);
    }
  }
  c.style.cssText='position:fixed;top:0;left:0;z-index:9999;background:#fff;max-width:100vw;overflow:auto;';
}
debugSheet('office', 0, 10);
```

- [ ] **Step 3: Update TILE_DEFS with correct coordinates**

After verifying visually, update the `TILE_DEFS` in `office-tileatlas.js` with the correct `[sheet, row, col]` values.

- [ ] **Step 4: Test with several employees and departments**

Add at least 3 departments with 5+ employees each. Verify:
- Different floor tile textures per department
- Plant columns visible between departments
- Characters render (not blank or fallback)
- Desks render correctly
- Pan/zoom works
- Mini-map shows correct positions

- [ ] **Step 5: Commit**

```bash
git add frontend/office-tileatlas.js
git commit -m "fix: correct tile coordinates for floor, desk, plant, and character sprites"
```

---

## Chunk 8: Polish and Integration Testing

### Task 9: End-to-end integration verification

- [ ] **Step 1: Run backend tests**

```bash
.venv/bin/python -m pytest tests/unit/core/test_layout_tileset.py tests/unit/core/ -v 2>&1 | tail -20
```

Expected: all pass, no regressions.

- [ ] **Step 2: Verify click interactions still work**

Test in browser:
- Click bulletin board (tiles 5-7 row 0-1) → workflow panel opens
- Click project wall (tiles 12-14 row 0-1) → project wall opens
- Click an employee → employee detail panel opens
- Click a meeting room → room detail opens
- Mini-map click → camera jumps to that area
- Double-check all at different zoom levels (1x, 2x, 0.5x)

- [ ] **Step 3: Test auto-expansion with many employees**

```bash
# Use the existing layout test with 30 employees
.venv/bin/python -c "
from onemancompany.core.layout import _assign_desks_in_zone, _compute_zones
dept_groups = {'Engineering': [{'id': f'{i:05d}', 'level': 1, 'employee_number': f'{i:05d}', 'desk_position': (0,0)} for i in range(6, 36)]}
zones = _compute_zones(dept_groups)
from onemancompany.core.layout import _assign_desks_in_zone
max_row = _assign_desks_in_zone(zones[0], dept_groups['Engineering'])
print(f'Max row: {max_row}')
assert max_row > 10, 'Should exceed default DEPT_END_ROW'
print('Auto-expansion test: PASSED')
"
```

- [ ] **Step 4: Commit any remaining polish**

```bash
git add -u
git commit -m "fix: integration polish for tileset office renderer"
```

- [ ] **Step 5: Push branch and update PR**

```bash
git push origin fix/dept-color-auto-refresh
gh pr edit --body "$(cat <<'EOF'
## Office Visualization Redesign + Layout Fixes

### What Changed

#### Frontend — Tileset-Based Office Renderer
- **Replaced** hand-drawn `fillRect()` rendering with LimeZu 32×32 pixel-art tilesets
- **Added** `TileAtlas` class: loads Modern Office + Modern Interiors spritesheets, blits tiles by grid coordinate
- **Added** `Camera` class: mouse drag to pan, scroll wheel to zoom (0.5x–3x), smooth lerp transitions, bounds clamping
- **Added** `OfficeMap` class: tilemap data layer built from layout API, translates backend zone data to renderable grid
- **Added** `MiniMap` overlay: 120×80px viewport overview in bottom-right corner, click to jump
- **Department floors**: Each department renders a distinct floor tile material (stone, wood, carpet, tile) from the tileset
- **Plant dividers**: Potted plant columns separate adjacent departments (alternating large/small sprites)
- **Characters**: 20 premade LimeZu character sprites assigned deterministically by employee ID

#### Backend — Layout API Extension (additive, no breaking changes)
- `layout.py`: Zone data now includes `floor_style` (string key for frontend tile selection)
- `layout.py`: Layout response now includes `divider_cols` (list of grid-X columns for plant dividers)
- `config.py`: Added `DEPT_FLOOR_STYLES` and `DEPT_DIVIDER_WIDTH` constants

#### Other Fixes (from previous session)
- Layout auto-expansion: office canvas grows vertically when employees exceed 3 desk rows
- Avatar system: default avatars assigned deterministically on hire, oval clip path in renderer
- Department colors updated from WebSocket without requiring server restart

### API Compatibility
All existing REST endpoints and WebSocket messages are unchanged. The layout API response is extended additively — existing fields are preserved.

### Assets
Paid tilesets (LimeZu Modern Office v1.2 + Modern Interiors) stored in `frontend/assets/office/tilesets/`. These are commercial assets licensed for use in this project.
EOF
)"
```

---

## Summary

| Task | Files | Outcome |
|------|-------|---------|
| 1 | `config.py` | `DEPT_FLOOR_STYLES` constant |
| 2 | `layout.py` + tests | Zone `floor_style` + `divider_cols` in API |
| 3 | `office-tileatlas.js` | Spritesheet loader singleton |
| 4 | `office-camera.js` | Pan/zoom/culling camera |
| 5 | `office-map.js` | Tilemap data from API |
| 6 | `office-minimap.js` | Minimap overlay |
| 7 | `office.js` | OfficeRenderer rewrite |
| 8 | `office-tileatlas.js` | Tile coordinate calibration |
| 9 | All | Integration verification |
