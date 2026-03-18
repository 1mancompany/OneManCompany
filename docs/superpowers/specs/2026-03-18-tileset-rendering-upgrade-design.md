# Office Tileset Rendering Upgrade — Design Spec

## Summary

Replace all Canvas 2D primitive drawing (fillRect-based walls, furniture, characters, decorations) with LimeZu tileset-based rendering. The current office renderer uses ~99% hand-drawn primitives with tilesets loaded but barely used. This upgrade switches to full tileset rendering for a game-quality pixel art office.

## Goals

1. Game-quality pixel art visuals using LimeZu 32x32 tilesets for all rendering layers
2. Character sprites from 20 Premade Character spritesheets (idle + sit states)
3. Configurable character appearance per employee via `avatar_sprite` field
4. Preserve all existing interactivity (click, tooltip, camera, minimap, WebSocket sync)
5. Net code reduction (~400 lines deleted, ~150 added)

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | Full replacement (walls, floors, furniture, characters, decorations, interactive, meetings) | Mixed primitive + tileset looks inconsistent |
| Character animation | Static idle + sit only (no walk) | Walk needs pathfinding; employees are mostly seated |
| Character assignment | `avatar_sprite` field in profile.yaml, hash fallback | Configurable but zero-config by default |
| Interactive elements | Keep interaction logic, replace visual background with tiles | Minimal risk, visual consistency |
| Wall windows | Tileset walls + Canvas dynamic sky/stars | Best of both: consistent material + game-feel animation |
| Migration strategy | Layer-by-layer replacement | office.js already has per-layer draw methods; each step is testable |

## Architecture

No new files created. No new classes. Changes are scoped to:
- `frontend/office-tileatlas.js` — expand TILE_DEFS
- `frontend/office.js` — replace draw methods internals
- Backend — add `avatar_sprite` field support

### Migration Order

```
1. Walls (drawWalls)        — tileset wall tiles + Canvas window animation
2. Floors (drawFloor)       — remove fallback color blocks, tile-only
3. Furniture (drawDesk)     — desk/chair/monitor tiles replace fillRect
4. Characters (drawCharacter) — Premade Character sprites replace procedural generation
5. Decorations (drawDecorations, drawPlants) — tiles replace PNG sprites + primitives
6. Interactive (drawBulletinBoard, drawProjectWall) — tile background, keep data rendering
7. Meeting rooms (drawMeetingRoom) — conference table/chair tiles
```

## Tile Inventory

### Available Tilesets

| Sheet Key | File | Content |
|-----------|------|---------|
| `room` | Room_Builder_Office_32x32.png | Walls (rows 0-3), floors (rows 5-13) |
| `interiors_room` | Room_Builder_32x32.png | Wall variants with windows, doors, colors |
| `office` | Modern_Office_32x32.png | Furniture, chairs, plants, monitors, conference |
| `interiors` | Interiors_32x32.png | Generic furniture, rugs, curtains |
| `char01`–`char20` | Premade_Character_32x32_XX.png | 20 character spritesheets |

### New TILE_DEFS to Add

```javascript
// Walls (from room or interiors_room sheet — exact coords need visual calibration)
wall_top:           [sheet, row, col],
wall_mid:           [sheet, row, col],
wall_bottom:        [sheet, row, col],
wall_window_top:    [sheet, row, col],
wall_window_bottom: [sheet, row, col],

// Furniture variants (from office sheet)
desk_cubicle_tl:    ['office', row, col],
desk_cubicle_tr:    ['office', row, col],
desk_cubicle_bl:    ['office', row, col],
desk_cubicle_br:    ['office', row, col],
chair_orange:       ['office', row, col],
chair_gold:         ['office', row, col],
monitor_desktop:    ['office', row, col],
keyboard:           ['office', row, col],
filing_cabinet:     ['office', row, col],

// Character poses (template — sheet key is dynamic per employee)
// Row 0, cols 0-2: idle front (3 frames, 1×2 tiles each)
// Row 4, col 0: sit variant 1 (1×2 tiles)
// Row 6, col 0: sit variant 2 (1×2 tiles)
```

Note: Exact row/col coordinates require visual calibration against spritesheet images (same process as the original Task 8 calibration).

## Layer Replacement Details

### Layer 1: Walls (drawWalls)

**Current**: 3-row color gradient + hand-drawn windows + star animation

**New**:
- Fill top 3 rows with wall tiles (wall_top/wall_mid/wall_bottom)
- Every 4 columns: window tile variant
- Window interior: Canvas dynamic sky gradient + twinkling stars (preserved)
- Implementation: draw wall tiles first, then overlay dynamic sky in window regions using Canvas

### Layer 2: Floors (drawFloor)

**Current**: Fallback color checkerboard + tileset overlay

**New**:
- Remove `FLOOR_FALLBACK` object and fallback color logic
- Tile-only rendering (brief black flash acceptable during async load)
- Existing floor TILE_DEFS and zone logic unchanged
- Plant divider columns unchanged

### Layer 3: Furniture (drawDesk)

**Current**: fillRect desk surface + chair + monitor + keyboard

**New**:
- Workstation = 2×2 tile group: chair(TL) + empty(TR) + desk_left(BL) + desk_right(BR)
- Monitor: `drawDef` overlaid on desk top
- Keyboard: `drawDef` overlaid on desk surface
- CEO/executive workstations: gold/orange chair variants for visual distinction
- Delete `PALETTE.desk`, `PALETTE.chair`, `PALETTE.deskDark`, `PALETTE.deskLight`

### Layer 4: Characters (drawCharacter)

**Current**: Procedurally generated pixel humans (hash-based skin/hair/shirt colors, animated limbs/eyes)

**New**:
- Character sprite = 1 tile wide × 2 tiles tall (32×64px)
- Working/has task → sit sprite (row 4, col 0)
- Idle → idle front sprite (row 0, col 0-2, animated 3-frame loop)
- Sprite bottom aligns with chair position; upper body visible above desk
- Status overlays drawn on top of sprite (unchanged): work bubbles, zzz, listening glow, CEO crown

**Frame selection logic**:
```javascript
_getCharFrame(employee) {
  const spriteNum = employee.avatar_sprite || ((hash(employee.id) % 20) + 1);
  const sheetKey = `char${String(spriteNum).padStart(2, '0')}`;

  if (employee.current_task || employee.status === 'working') {
    return { sheet: sheetKey, row: 4, col: 0, w: 1, h: 2 };
  }
  const frame = Math.floor(this.animFrame * 0.02) % 3;
  return { sheet: sheetKey, row: 0, col: frame, w: 1, h: 2 };
}
```

**Preloading**: On `updateState()`, scan employee list, preload only sheets for current employees. Fallback while loading: solid-color silhouette.

### Layer 5: Decorations (drawDecorations + drawPlants)

**Current**: 6 PNG sprites with fallback primitives + hand-drawn plants

**New**:
- All decorations via TILE_DEFS: bookshelf(2×2), whiteboard(2×2), printer(1×1)
- Plants: already using plant_large/plant_small tiles
- Delete: `_decoImages`, `_preloadDecorations()`, fallback primitive drawing
- Coffee machine steam: preserve as Canvas overlay (white pixel animation on top of tile)

### Layer 6: Interactive Elements (drawBulletinBoard + drawProjectWall)

**Current**: Hand-drawn wooden bulletin board + dark green project wall

**New**:
- Bulletin board: whiteboard tile as background, Canvas sticky notes/pins on top
- Project wall: bookshelf or dark tile as background, Canvas project cards on top
- All click handling, hover glow, data display logic unchanged

### Layer 7: Meeting Rooms (drawMeetingRoom)

**Current**: fillRect table + chairs + participant mini-heads

**New**:
- Table: conf_table_tl/tr/bl/br (2×2) — already defined in TILE_DEFS
- Chairs: conf_chair_top/bottom — already defined
- Participants: employee character sprite idle frame, scaled down
- Status LED + booked animation: preserved as Canvas overlay

## Backend Changes

### avatar_sprite Field

- Location: `employees/{id}/profile.yaml`
- Field: `avatar_sprite: 5` (integer 1-20)
- Default: not set → frontend uses `(hash(employee_id) % 20) + 1`
- Backend passes through to `/api/state` response in employee data
- Onboarding: assign random value (1-20) when creating new employee

### No Other Backend Changes

- `layout.py`: unchanged (floor_style, divider_cols already present)
- `config.py`: unchanged (DEPT_FLOOR_STYLES already present)
- API response format: additive only (new field in employee object)

## Code Impact

### Deleted (~400 lines)

- `PALETTE` furniture/people colors (desk, chair, skin, hair, shirt, etc.)
- `FLOOR_FALLBACK` object
- `drawWalls()` gradient + hand-drawn window rendering
- `drawDesk()` fillRect desk/chair/monitor/keyboard
- `drawCharacter()` procedural generation (skin/hair/shirt/eyes/mouth/arms)
- `drawPlants()` hand-drawn plant segments
- `drawDecorations()` PNG preload + fallback primitives
- `drawMeetingRoom()` fillRect table/chairs
- `_decoImages`, `_preloadDecorations()`

### Preserved

- Camera system (office-camera.js) — untouched
- OfficeMap data layer (office-map.js) — untouched
- MiniMap (office-minimap.js) — untouched
- TileAtlas class (office-tileatlas.js) — only TILE_DEFS expanded
- Click/hover/tooltip interactions (_onClick, _onMouseMove)
- Animation system (animFrame counter, window sky, status overlays)
- Particle system (hire notification)
- WebSocket updateState() flow
- Department labels + divider lines

### Added (~150 lines)

- ~15 new TILE_DEFS entries (walls, furniture variants)
- `_getCharFrame()` method (~15 lines)
- Character sheet preloading in updateState() (~10 lines)
- Tile drawing calls in each draw method (~10-20 lines per method)
- Backend avatar_sprite field handling (~5 lines)

### Unchanged Files

- `office-camera.js`
- `office-map.js`
- `office-minimap.js`
- `frontend/style.css`
- `frontend/app.js`
- `frontend/index.html`

## Testing

### Visual Verification (Primary)

No JS test framework available. Each layer replacement verified by:
- Tile grid alignment (no sub-pixel offset)
- Correct tile coordinates (not wrong tile)
- Crisp pixels at zoom 1x/2x/3x
- Click interactions still functional

### Character Sprite Calibration

- Sit sprite bottom aligns with chair
- Idle sprite centered in workstation
- Spot-check 3-4 different characters out of 20

### Backend Tests

- `avatar_sprite` field: profile.yaml read/write, default fallback
- Existing layout tests unaffected

### Regression Checklist

After each layer replacement:
1. Click employee → detail panel opens
2. Click bulletin board → content displays
3. Click project wall → content displays
4. Click meeting room → meeting info shows
5. Drag pan + scroll zoom works
6. Minimap click-to-jump works
7. New hire particle effect fires
8. Window star animation plays
9. Department labels and dividers render
