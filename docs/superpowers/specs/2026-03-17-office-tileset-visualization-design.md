# Office Tileset Visualization Redesign

## Summary

Replace the hand-drawn Canvas 2D office rendering with a tileset-based system using LimeZu's Modern Office and Modern Interiors asset packs. Add camera controls (pan, zoom, mini-map) and use distinct floor tile patterns + potted plant columns to separate departments.

## Goals

1. Professional pixel-art visuals using commercial tilesets (LimeZu 32x32)
2. Pan + zoom camera with mini-map for navigating large offices
3. Department zones differentiated by floor tile material, divided by plant columns
4. Auto-expanding map as employee count grows
5. Preserve all existing interactivity (click employee, tooltip, meeting rooms, boards)

## Architecture

### Rendering Pipeline

```
TileAtlas (singleton)
  ├── Loads spritesheet PNGs at startup
  ├── Extracts 32x32 tiles by (row, col) index
  └── Caches as ImageBitmap for fast blitting
        ↓
OfficeMap (data layer)
  ├── 2D grid: each cell = { floor, furniture, decoration, character }
  ├── Built from backend layout API response
  ├── Zones define floor tile type per department
  ├── Plant divider columns between zones
  └── Auto-expands rows when employees overflow
        ↓
Camera (viewport)
  ├── Pan: mouse drag / touch drag
  ├── Zoom: scroll wheel / pinch (1x–3x, 0.25 steps)
  ├── Culling: only visible tiles + 1-tile margin rendered
  ├── Smooth lerp transitions
  └── Double-click → center + zoom on target
        ↓
Canvas 2D (imageSmoothingEnabled = false, hi-DPI)
```

### TileAtlas

Singleton that loads spritesheet PNGs and provides `drawTile(ctx, sheetName, row, col, destX, destY)`.

Source sheets (all 32x32 grid):
- `Room_Builder_Office_32x32.png` — walls, floors (multiple materials)
- `Modern_Office_32x32.png` — office furniture (desks, computers, chairs, whiteboards)
- `Interiors_32x32.png` — generic furniture, plants, rugs, windows, curtains
- `Room_Builder_32x32.png` — additional wall/floor variants from Modern Interiors
- `Premade_Character_32x32_XX.png` — 20 character spritesheets

Tile coordinates are defined as constants in a `TILE_DEFS` object mapping semantic names to `[sheet, row, col, width, height]` tuples (width/height in tiles, default 1x1).

### OfficeMap

Data layer that converts backend layout API response into a renderable tilemap.

```javascript
class OfficeMap {
  constructor(cols, rows) // dynamic sizing
  setZones(zones)         // from layout API — sets floor tiles per zone
  placeEmployee(empId, gx, gy, characterIdx)
  placeFurniture(gx, gy, tileDef)
  placePlantDivider(col)  // full-height plant column
  getCell(gx, gy) → { floor, furniture, decoration, character }
}
```

**Floor tile mapping per department** (configurable in `config.py`):
- Engineering → blue-gray stone tiles
- Design → warm wood planks
- Analytics → green-tinted tile
- Marketing → warm carpet/brick
- General → neutral gray tile
- Executive area → gold-accent wood

**Plant dividers**: 1-tile-wide column of alternating large/small potted plants between adjacent zones. Added to zone data in `layout.py` by inserting `divider_cols` array.

### Camera

```javascript
class Camera {
  constructor(canvas, mapWidth, mapHeight)

  // State
  x, y          // top-left corner in world pixels
  zoom          // 1.0 – 3.0
  targetX, targetY, targetZoom  // for smooth lerp

  // Input
  onMouseDown(e)   // start drag
  onMouseMove(e)   // drag pan
  onMouseUp(e)     // end drag
  onWheel(e)       // zoom (centered on cursor)
  onDblClick(e)    // center on clicked tile

  // Transform
  worldToScreen(wx, wy) → [sx, sy]
  screenToWorld(sx, sy) → [wx, wy]
  getVisibleBounds() → { minCol, maxCol, minRow, maxRow }

  // Update
  update(dt)       // lerp toward target
  centerOn(gx, gy, zoom?)  // smooth pan to grid position
}
```

**Mini-map**: 120×80px canvas overlay in bottom-right corner. Renders full map at ~4x reduction. Draws viewport rectangle. Click to jump.

### Character System

20 premade character spritesheets (`Premade_Character_32x32_01..20.png`). Each assigned to employees by `hash(empId) % 20 + 1`.

Spritesheet layout (per LimeZu standard):
- Row 0: idle front (3 frames)
- Rows 1-4: walk cycle (down, left, right, up × 4 frames each)
- Additional rows: sitting, actions

For office display, primarily use:
- **Idle front** when at desk
- **Sitting** pose when working (if available in sheet)

Status overlays (speech bubbles, zzz, working dots) drawn as separate sprites above character position, same as current system.

### Furniture Placement

Each desk unit is a 2×2 tile group:
```
[chair ] [      ]
[desk-L] [desk-R]  ← desk with monitor
```

Tile indices extracted from `Modern_Office_32x32.png`. Multiple desk variants available (cubicle walls, open desk, L-shaped).

Meeting rooms use `Conference_Hall_32x32.png` tiles: conference table (2×2), chairs around it, glass partition walls.

## Backend Changes

### layout.py

Add to zone data:
- `floor_style`: string key for tile pattern (e.g., "stone", "wood", "carpet")
- `divider_cols`: list of grid-X columns that should render as plant dividers

```python
DEPT_FLOOR_STYLES = {
    "Engineering": "stone_blue",
    "Design": "wood_warm",
    "Analytics": "tile_green",
    "Marketing": "carpet_red",
    "General": "stone_gray",
}
```

In `_compute_zones()`, insert 1-col gap between zones for plant dividers. Adjust `TOTAL_COLS` or zone widths to accommodate.

### config.py

Add `DEPT_FLOOR_STYLES` mapping and `DEPT_DIVIDER_WIDTH = 1`.

## Frontend Changes

### office.js — Major Rewrite

Replace current monolithic `OfficeRenderer` with modular components:

1. **TileAtlas** class — spritesheet loading and tile extraction
2. **Camera** class — pan/zoom/bounds/culling
3. **OfficeMap** class — tilemap data from API
4. **OfficeRenderer** class — orchestrates rendering loop
5. **MiniMap** class — overlay mini-map

Keep existing:
- Click handlers (employee detail, boards, rooms)
- Tooltip system
- WebSocket state sync via `updateState()`
- Animation frame counter for sprite animations

### Render Order (per frame)

1. Get visible tile bounds from Camera
2. Draw floor tiles (by zone, only visible)
3. Draw plant dividers (only visible columns)
4. Draw wall tiles (top rows)
5. Draw furniture (desks, decorations) — sorted by Y for overlap
6. Draw characters — sorted by Y for overlap
7. Draw status overlays (bubbles, badges)
8. Draw mini-map
9. Draw tooltip (screen-space)

### Coordinate Mapping

All click/hover events go through `Camera.screenToWorld()` before tile lookup. This replaces the current direct `(e.clientX - rect.left) * scaleX` approach.

## Migration Path

1. Current `office.js` is 1654 lines. The rewrite replaces it entirely.
2. Backend layout API response format is extended (additive, not breaking).
3. Existing employee click, tooltip, and WebSocket sync behavior is preserved.
4. Assets served via existing FastAPI `StaticFiles` mount at `/`.

## Testing

- Verify tileset loading and correct tile extraction coordinates
- Camera bounds: cannot pan beyond map edges
- Zoom: verify pixel-perfect rendering at all zoom levels
- Click detection: verify screenToWorld mapping at various pan/zoom states
- Auto-expansion: add 30+ employees, verify map grows and camera can reach new rows
- Mini-map: verify viewport indicator tracks camera correctly
- Department dividers: verify plant columns appear between all adjacent zones
