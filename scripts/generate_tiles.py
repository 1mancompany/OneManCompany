#!/usr/bin/env python3
"""
Generate high-quality 32x32 pixel art office tiles matching LimeZu aesthetic.

Output: frontend/assets/office/tilesets/generated/generated_tiles_32x32.png
Layout: 16 columns × 16 rows (512×512 px)

Row map:
  r0-r1: Wall set A (warm beige/cream) — top + baseboard
  r2-r3: Wall set B (cool gray/blue) — top + baseboard
  r4:    Floor - wood planks (light oak)
  r5:    Floor - wood planks (dark walnut)
  r6:    Floor - gray stone tiles
  r7:    Floor - checkered (cream/brown)
  r8:    Floor - brick (red)
  r9:    Floor - herringbone wood
  r10:   Floor - teal decorative
  r11:   Floor - carpet (exec gold)
  r12:   Desk fronts (5 color variants) + desk tops
  r13:   Monitors, printer, whiteboard pieces
  r14:   Chairs (4 variants), filing cabinet, plant
  r15:   Conference table pieces, bookshelf pieces
"""

from PIL import Image, ImageDraw
import random

TILE = 32
COLS = 16
ROWS = 16
W = COLS * TILE
H = ROWS * TILE

img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

# ── Helpers ──────────────────────────────────────────────────────────────────

def px(x, y, color):
    """Set a single pixel (clipped to image bounds)."""
    if 0 <= x < W and 0 <= y < H:
        img.putpixel((x, y), color)

def fill_rect(x, y, w, h, color):
    """Fill a rectangle."""
    for dy in range(h):
        for dx in range(w):
            px(x + dx, y + dy, color)

def tile_origin(row, col):
    """Get pixel origin for a tile cell."""
    return col * TILE, row * TILE

def shift_color(base, amount):
    """Lighten/darken a color."""
    return tuple(max(0, min(255, c + amount)) for c in base[:3]) + ((base[3],) if len(base) > 3 else (255,))

def vary_color(base, variance=5):
    """Add slight random variation to a color for texture."""
    return tuple(max(0, min(255, c + random.randint(-variance, variance))) for c in base[:3]) + (255,)

def dither_fill(x0, y0, w, h, c1, c2, pattern="checker"):
    """Fill area with dithered pattern of two colors."""
    for dy in range(h):
        for dx in range(w):
            if pattern == "checker":
                use_c1 = (dx + dy) % 2 == 0
            elif pattern == "horizontal":
                use_c1 = dy % 2 == 0
            else:
                use_c1 = dx % 2 == 0
            px(x0 + dx, y0 + dy, c1 if use_c1 else c2)


# ── Wall Tiles ───────────────────────────────────────────────────────────────
# LimeZu style: horizontal colored stripes with subtle variation, baseboard row below

def draw_wall_pair(row, col, wall_color, stripe_color, baseboard_color, trim_color):
    """Draw a 2-row wall tile (top: wall face, bottom: baseboard)."""
    ox, oy = tile_origin(row, col)
    bx, by = tile_origin(row + 1, col)

    # Top wall tile: main wall color with horizontal decorative stripes
    # Fill base
    for dy in range(TILE):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(wall_color, 3))

    # Decorative horizontal stripe band (like LimeZu warm walls)
    stripe_y1, stripe_y2 = 8, 12
    for dy in range(stripe_y1, stripe_y2):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(stripe_color, 4))
    # Thin accent lines above and below stripe
    for dx in range(TILE):
        px(ox + dx, oy + stripe_y1 - 1, shift_color(stripe_color, -20))
        px(ox + dx, oy + stripe_y2, shift_color(stripe_color, -20))

    # Second decorative stripe near top
    for dy in range(2, 5):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(shift_color(wall_color, 10), 3))
    for dx in range(TILE):
        px(ox + dx, oy + 1, shift_color(wall_color, -15))
        px(ox + dx, oy + 5, shift_color(wall_color, -15))

    # Bottom wall/baseboard tile
    # Upper portion: continuation of wall
    for dy in range(TILE):
        for dx in range(TILE):
            px(bx + dx, by + dy, vary_color(wall_color, 3))

    # Baseboard: darker strip at bottom
    for dy in range(TILE - 6, TILE):
        for dx in range(TILE):
            px(bx + dx, by + dy, vary_color(baseboard_color, 3))
    # Baseboard top edge (trim)
    for dx in range(TILE):
        px(bx + dx, by + TILE - 7, trim_color)
        px(bx + dx, by + TILE - 1, shift_color(baseboard_color, -20))

    # Mid-wall subtle horizontal line
    for dx in range(TILE):
        px(bx + dx, by + 10, shift_color(wall_color, -10))


# Wall Set A: Warm beige/cream (rows 0-1)
for c in range(8):  # 8 tile variants for seamless tiling
    random.seed(100 + c)
    draw_wall_pair(0, c,
        wall_color=(222, 200, 170, 255),      # warm beige
        stripe_color=(210, 180, 140, 255),     # darker warm
        baseboard_color=(160, 130, 100, 255),  # brown baseboard
        trim_color=(180, 150, 120, 255))       # trim

# Wall Set B: Cool gray/blue (rows 2-3)
for c in range(8):
    random.seed(200 + c)
    draw_wall_pair(2, c,
        wall_color=(190, 195, 210, 255),       # cool gray-blue
        stripe_color=(170, 175, 195, 255),     # darker blue-gray
        baseboard_color=(130, 135, 150, 255),  # dark gray baseboard
        trim_color=(155, 160, 175, 255))       # trim


# ── Floor Tiles ──────────────────────────────────────────────────────────────

def draw_wood_floor(row, col, plank_colors, grain_color, gap_color, horizontal=True):
    """Draw wood plank floor tile with grain detail."""
    ox, oy = tile_origin(row, col)

    if horizontal:
        # Horizontal planks, ~8px tall each, 4 planks per tile
        plank_h = 8
        for p in range(4):
            pc = plank_colors[p % len(plank_colors)]
            py = oy + p * plank_h
            # Fill plank
            for dy in range(plank_h):
                for dx in range(TILE):
                    px(ox + dx, py + dy, vary_color(pc, 4))
            # Wood grain lines (subtle horizontal streaks)
            random.seed(row * 1000 + col * 100 + p * 10)
            for g in range(3):
                gy = py + random.randint(1, plank_h - 2)
                gx_start = random.randint(0, 8)
                gx_len = random.randint(8, 24)
                for dx in range(gx_start, min(gx_start + gx_len, TILE)):
                    px(ox + dx, gy, vary_color(grain_color, 3))
            # Plank gap (dark line at bottom of each plank)
            if p < 3:
                for dx in range(TILE):
                    px(ox + dx, py + plank_h - 1, gap_color)
            # Occasional knot
            if random.random() > 0.6:
                kx = random.randint(4, TILE - 5)
                ky = py + random.randint(2, plank_h - 3)
                px(ox + kx, ky, shift_color(pc, -25))
                px(ox + kx + 1, ky, shift_color(pc, -20))
                px(ox + kx, ky + 1, shift_color(pc, -20))
    else:
        # Vertical planks
        plank_w = 8
        for p in range(4):
            pc = plank_colors[p % len(plank_colors)]
            ppx = ox + p * plank_w
            for dy in range(TILE):
                for dx in range(plank_w):
                    px(ppx + dx, oy + dy, vary_color(pc, 4))
            # Vertical grain
            random.seed(row * 1000 + col * 100 + p * 10 + 1)
            for g in range(3):
                gx = ppx + random.randint(1, plank_w - 2)
                gy_start = random.randint(0, 8)
                gy_len = random.randint(8, 24)
                for dy in range(gy_start, min(gy_start + gy_len, TILE)):
                    px(gx, oy + dy, vary_color(grain_color, 3))
            # Gap
            if p < 3:
                for dy in range(TILE):
                    px(ppx + plank_w - 1, oy + dy, gap_color)


# Row 4: Light oak wood planks
for c in range(8):
    random.seed(400 + c)
    draw_wood_floor(4, c,
        plank_colors=[(200, 170, 120, 255), (195, 165, 115, 255),
                      (205, 175, 125, 255), (190, 160, 110, 255)],
        grain_color=(180, 150, 100, 255),
        gap_color=(150, 125, 85, 255))

# Row 5: Dark walnut wood planks
for c in range(8):
    random.seed(500 + c)
    draw_wood_floor(5, c,
        plank_colors=[(130, 90, 60, 255), (125, 85, 55, 255),
                      (135, 95, 65, 255), (120, 80, 50, 255)],
        grain_color=(110, 75, 45, 255),
        gap_color=(90, 60, 35, 255))


def draw_stone_floor(row, col):
    """Gray stone/tile floor with mortar lines."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col)

    base = (175, 180, 185, 255)
    mortar = (145, 148, 152, 255)

    # Fill base with subtle texture
    for dy in range(TILE):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(base, 5))

    # Stone tile grid (8×8 sub-tiles with mortar)
    stone_size = 8
    for sy in range(4):
        for sx in range(4):
            # Mortar lines
            for dx in range(stone_size):
                px(ox + sx * stone_size + dx, oy + sy * stone_size, mortar)
            for dy in range(stone_size):
                px(ox + sx * stone_size, oy + sy * stone_size + dy, mortar)

            # Slight color variation per stone
            stone_tint = random.randint(-8, 8)
            sc = shift_color(base, stone_tint)
            for dy in range(1, stone_size):
                for dx in range(1, stone_size):
                    px(ox + sx * stone_size + dx, oy + sy * stone_size + dy, vary_color(sc, 3))

            # Corner shadow (bottom-right of each stone)
            shadow = shift_color(sc, -12)
            for dx in range(1, stone_size):
                px(ox + sx * stone_size + dx, oy + (sy + 1) * stone_size - 1, shadow)
            for dy in range(1, stone_size):
                px(ox + (sx + 1) * stone_size - 1, oy + sy * stone_size + dy, shadow)
            # Highlight (top-left)
            highlight = shift_color(sc, 10)
            for dx in range(2, stone_size - 1):
                px(ox + sx * stone_size + dx, oy + sy * stone_size + 1, highlight)
            for dy in range(2, stone_size - 1):
                px(ox + sx * stone_size + 1, oy + sy * stone_size + dy, highlight)


# Row 6: Gray stone
for c in range(8):
    draw_stone_floor(6, c)


def draw_checkered_floor(row, col, c1, c2):
    """Checkered floor tile."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col)

    check_size = 8  # 4×4 checks per tile
    for cy in range(4):
        for cx in range(4):
            is_c1 = (cx + cy) % 2 == 0
            base = c1 if is_c1 else c2
            for dy in range(check_size):
                for dx in range(check_size):
                    px(ox + cx * check_size + dx, oy + cy * check_size + dy, vary_color(base, 3))
            # Subtle inner shadow/highlight for depth
            highlight = shift_color(base, 8)
            shadow = shift_color(base, -8)
            sx = ox + cx * check_size
            sy_start = oy + cy * check_size
            for dx in range(1, check_size - 1):
                px(sx + dx, sy_start + 1, highlight)
            for dy in range(1, check_size - 1):
                px(sx + 1, sy_start + dy, highlight)
            for dx in range(1, check_size - 1):
                px(sx + dx, sy_start + check_size - 1, shadow)
            for dy in range(1, check_size - 1):
                px(sx + check_size - 1, sy_start + dy, shadow)


# Row 7: Checkered cream/brown
for c in range(8):
    draw_checkered_floor(7, c,
        (230, 215, 185, 255),  # cream
        (185, 155, 115, 255))  # brown


def draw_brick_floor(row, col):
    """Red brick floor with mortar lines."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col)

    mortar = (200, 195, 185, 255)
    brick_colors = [
        (180, 80, 65, 255), (175, 75, 60, 255),
        (170, 85, 70, 255), (185, 78, 62, 255),
    ]

    # Fill mortar base
    for dy in range(TILE):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(mortar, 3))

    # Bricks: standard running bond pattern
    brick_w = 14
    brick_h = 6
    mortar_w = 2

    for by in range(6):  # rows of bricks
        y_start = by * (brick_h + mortar_w)
        if y_start >= TILE:
            break
        offset = (brick_w // 2 + mortar_w // 2) if by % 2 else 0

        bx = -offset
        while bx < TILE:
            bc = random.choice(brick_colors)
            for dy in range(min(brick_h, TILE - y_start)):
                for dx in range(min(brick_w, TILE)):
                    xx = bx + dx
                    yy = y_start + dy
                    if 0 <= xx < TILE and 0 <= yy < TILE:
                        px(ox + xx, oy + yy, vary_color(bc, 6))

            # Brick highlight (top edge)
            highlight = shift_color(bc, 15)
            for dx in range(min(brick_w, TILE)):
                xx = bx + dx
                if 0 <= xx < TILE and y_start < TILE:
                    px(ox + xx, oy + y_start, highlight)

            # Brick shadow (bottom edge)
            shadow = shift_color(bc, -15)
            bot = y_start + brick_h - 1
            if bot < TILE:
                for dx in range(min(brick_w, TILE)):
                    xx = bx + dx
                    if 0 <= xx < TILE:
                        px(ox + xx, oy + bot, shadow)

            bx += brick_w + mortar_w


# Row 8: Red brick
for c in range(8):
    draw_brick_floor(8, c)


def draw_herringbone_floor(row, col):
    """Herringbone wood pattern floor."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col)

    colors = [
        (185, 130, 80, 255), (175, 120, 70, 255),
        (190, 135, 85, 255), (180, 125, 75, 255),
    ]
    outline = (140, 95, 55, 255)

    # Background
    for dy in range(TILE):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color((170, 120, 72, 255), 3))

    # Herringbone: alternating diagonal blocks
    block_w, block_h = 4, 8
    for by in range(-2, 6):
        for bx in range(-2, 10):
            is_right = (bx + by) % 2 == 0
            bc = random.choice(colors)
            cx = bx * block_w
            cy = by * block_h + (0 if is_right else block_w)

            if is_right:
                # Right-leaning block
                for dy in range(block_h):
                    for dx in range(block_w):
                        xx = cx + dx + dy
                        yy = cy + dy - dx
                        if 0 <= xx < TILE and 0 <= yy < TILE:
                            px(ox + xx, oy + yy, vary_color(bc, 4))
            else:
                # Left-leaning block
                for dy in range(block_h):
                    for dx in range(block_w):
                        xx = cx + dx - dy + block_h
                        yy = cy + dy + dx - block_w
                        if 0 <= xx < TILE and 0 <= yy < TILE:
                            px(ox + xx, oy + yy, vary_color(bc, 4))

    # Overlay a grid pattern to create herringbone illusion
    # V-shaped pattern
    for dy in range(TILE):
        for dx in range(TILE):
            # Create V pattern
            cell_x = dx % 8
            cell_y = dy % 8
            if cell_x == 0 or cell_y == 0:
                existing = img.getpixel((ox + dx, oy + dy))
                px(ox + dx, oy + dy, shift_color(existing, -15))


# Row 9: Herringbone
for c in range(8):
    draw_herringbone_floor(9, c)


def draw_teal_floor(row, col):
    """Teal decorative tile floor with circular pattern like LimeZu."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col)

    base = (140, 195, 190, 255)
    pattern = (120, 175, 170, 255)
    highlight = (160, 215, 210, 255)

    # Base fill
    for dy in range(TILE):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(base, 3))

    # 4×4 grid of circular motifs (8px each)
    for cy in range(4):
        for cx in range(4):
            center_x = cx * 8 + 4
            center_y = cy * 8 + 4
            # Draw circle-ish pattern
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    dist = abs(dx) + abs(dy)  # Manhattan distance for pixel art
                    xx, yy = center_x + dx, center_y + dy
                    if 0 <= xx < TILE and 0 <= yy < TILE:
                        if dist == 3:
                            px(ox + xx, oy + yy, pattern)
                        elif dist == 4:
                            px(ox + xx, oy + yy, shift_color(pattern, -10))
                        elif dist <= 2:
                            px(ox + xx, oy + yy, highlight if dist <= 1 else base)


# Row 10: Teal
for c in range(8):
    draw_teal_floor(10, c)


def draw_carpet_floor(row, col):
    """Executive carpet floor - subtle woven texture."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col)

    base = (165, 140, 90, 255)  # gold/tan
    dark = (155, 130, 80, 255)
    light = (175, 150, 100, 255)

    for dy in range(TILE):
        for dx in range(TILE):
            # Woven texture: alternating tiny patterns
            if (dx + dy) % 4 == 0:
                px(ox + dx, oy + dy, vary_color(dark, 3))
            elif (dx + dy) % 4 == 2:
                px(ox + dx, oy + dy, vary_color(light, 3))
            else:
                px(ox + dx, oy + dy, vary_color(base, 4))


# Row 11: Carpet
for c in range(8):
    draw_carpet_floor(11, c)


# ── Desk Tiles (Row 12) ─────────────────────────────────────────────────────

def draw_desk_front(row, col, body_color, edge_color, shadow_color):
    """Draw desk front panel (viewed from front, top-down perspective)."""
    ox, oy = tile_origin(row, col)

    # Full desk front panel
    # Top edge (desk surface seen from front)
    for dx in range(TILE):
        px(ox + dx, oy, shift_color(body_color, 20))
        px(ox + dx, oy + 1, shift_color(body_color, 15))

    # Main body with wood grain
    random.seed(row * 1000 + col * 100 + 50)
    for dy in range(2, TILE - 2):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(body_color, 4))

    # Wood grain horizontal lines
    for g in range(4):
        gy = 4 + g * 7
        if gy < TILE - 2:
            grain = shift_color(body_color, -12)
            gx_start = random.randint(0, 4)
            for dx in range(gx_start, TILE - random.randint(0, 4)):
                px(ox + dx, oy + gy, vary_color(grain, 3))

    # Left edge
    for dy in range(TILE):
        px(ox, oy + dy, edge_color)
    # Right edge
    for dy in range(TILE):
        px(ox + TILE - 1, oy + dy, edge_color)

    # Bottom edge shadow
    for dx in range(TILE):
        px(ox + dx, oy + TILE - 2, shadow_color)
        px(ox + dx, oy + TILE - 1, shift_color(shadow_color, -10))

    # Panel detail: inset rectangle
    inset = shift_color(body_color, -8)
    for dx in range(4, TILE - 4):
        px(ox + dx, oy + 6, inset)
        px(ox + dx, oy + TILE - 6, inset)
    for dy in range(6, TILE - 5):
        px(ox + 4, oy + dy, inset)
        px(ox + TILE - 5, oy + dy, inset)


# 5 desk color variants
desk_colors = [
    ((200, 170, 120), (170, 140, 95), (150, 120, 80)),   # light pine
    ((160, 110, 70), (130, 85, 50), (110, 70, 40)),      # dark cherry
    ((195, 150, 90), (165, 125, 65), (145, 105, 55)),    # warm orange
    ((180, 145, 100), (150, 118, 75), (130, 100, 65)),   # two-tone
    ((155, 120, 80), (125, 95, 58), (108, 78, 48)),      # brown
]

for i, (body, edge, shadow) in enumerate(desk_colors):
    draw_desk_front(12, i, body + (255,), edge + (255,), shadow + (255,))


def draw_desk_top(row, col, color):
    """Desk top surface (thin strip viewed from above)."""
    ox, oy = tile_origin(row, col)

    # Top surface with slight 3D bevel
    highlight = shift_color(color, 15)
    shadow = shift_color(color, -15)

    # Front edge highlight
    for dx in range(TILE):
        px(ox + dx, oy, highlight)

    # Main surface
    for dy in range(1, TILE - 1):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(color, 3))

    # Wood grain
    random.seed(12000 + col)
    for g in range(3):
        gy = 3 + g * 9
        if gy < TILE - 1:
            gc = shift_color(color, -10)
            for dx in range(2, TILE - 2):
                if random.random() > 0.2:
                    px(ox + dx, oy + gy, gc)

    # Back edge shadow
    for dx in range(TILE):
        px(ox + dx, oy + TILE - 1, shadow)

    # Left/right edges
    for dy in range(TILE):
        px(ox, oy + dy, shift_color(color, -10))
        px(ox + TILE - 1, oy + dy, shadow)


for i in range(5):
    draw_desk_top(12, 5 + i, desk_colors[i][0] + (255,))


# ── Monitors, Printer, Whiteboard (Row 13) ──────────────────────────────────

def draw_monitor(row, col, screen_color=(80, 140, 200, 255)):
    """Draw a monitor viewed from above/front (top-down office perspective)."""
    ox, oy = tile_origin(row, col)

    bezel = (50, 52, 58, 255)
    screen = screen_color
    stand = (65, 68, 75, 255)
    base_c = (55, 58, 64, 255)

    # Monitor stand/base at bottom
    for dx in range(10, 22):
        px(ox + dx, oy + TILE - 3, base_c)
        px(ox + dx, oy + TILE - 2, base_c)
    for dx in range(14, 18):
        px(ox + dx, oy + TILE - 4, stand)
        px(ox + dx, oy + TILE - 5, stand)

    # Monitor body (bezel)
    for dy in range(2, TILE - 6):
        for dx in range(2, 30):
            px(ox + dx, oy + dy, bezel)

    # Screen area
    for dy in range(4, TILE - 8):
        for dx in range(4, 28):
            px(ox + dx, oy + dy, vary_color(screen, 5))

    # Screen reflection (top-left bright spot)
    for dy in range(4, 8):
        for dx in range(4, 12):
            existing = img.getpixel((ox + dx, oy + dy))
            px(ox + dx, oy + dy, shift_color(existing, 20))

    # Screen highlight edge
    for dx in range(4, 28):
        px(ox + dx, oy + 4, shift_color(screen, 15))

    # Power LED
    px(ox + 15, oy + TILE - 7, (80, 220, 80, 255))
    px(ox + 16, oy + TILE - 7, (60, 180, 60, 255))


draw_monitor(13, 0, (80, 140, 200, 255))  # Blue screen
draw_monitor(13, 1, (70, 160, 120, 255))  # Green screen (code)


def draw_dual_monitor(row, col):
    """Draw dual monitor setup (2 tiles wide but we fit in 1 tile)."""
    ox, oy = tile_origin(row, col)

    bezel = (50, 52, 58, 255)
    screen_l = (80, 140, 200, 255)
    screen_r = (70, 160, 120, 255)
    stand = (55, 58, 64, 255)

    # Shared stand
    for dx in range(12, 20):
        px(ox + dx, oy + TILE - 3, stand)
        px(ox + dx, oy + TILE - 2, stand)
    for dx in range(14, 18):
        px(ox + dx, oy + TILE - 4, stand)

    # Left monitor
    for dy in range(2, TILE - 5):
        for dx in range(1, 15):
            px(ox + dx, oy + dy, bezel)
    for dy in range(4, TILE - 7):
        for dx in range(3, 13):
            px(ox + dx, oy + dy, vary_color(screen_l, 5))

    # Right monitor
    for dy in range(2, TILE - 5):
        for dx in range(17, 31):
            px(ox + dx, oy + dy, bezel)
    for dy in range(4, TILE - 7):
        for dx in range(19, 29):
            px(ox + dx, oy + dy, vary_color(screen_r, 5))

    # LEDs
    px(ox + 7, oy + TILE - 6, (80, 220, 80, 255))
    px(ox + 23, oy + TILE - 6, (80, 220, 80, 255))


draw_dual_monitor(13, 2)


def draw_printer(row, col):
    """Office printer."""
    ox, oy = tile_origin(row, col)

    body = (195, 195, 200, 255)
    dark = (120, 122, 128, 255)
    paper = (240, 238, 232, 255)

    # Main body
    for dy in range(8, TILE - 2):
        for dx in range(3, 29):
            px(ox + dx, oy + dy, vary_color(body, 3))

    # Top surface (slightly lighter)
    for dy in range(8, 12):
        for dx in range(3, 29):
            px(ox + dx, oy + dy, shift_color(body, 8))

    # Paper tray slot
    for dx in range(6, 26):
        px(ox + dx, oy + 12, dark)
    # Paper sticking out
    for dy in range(5, 12):
        for dx in range(8, 24):
            px(ox + dx, oy + dy, paper)
    for dx in range(8, 24):
        px(ox + dx, oy + 5, shift_color(paper, -10))

    # Control panel
    for dx in range(20, 27):
        px(ox + dx, oy + 9, (60, 60, 65, 255))
        px(ox + dx, oy + 10, (60, 60, 65, 255))
    # Buttons
    px(ox + 21, oy + 9, (80, 200, 80, 255))
    px(ox + 23, oy + 9, (200, 80, 80, 255))

    # Shadow at bottom
    for dx in range(3, 29):
        px(ox + dx, oy + TILE - 2, shift_color(body, -20))
        px(ox + dx, oy + TILE - 3, shift_color(body, -10))

    # Edges
    for dy in range(8, TILE - 2):
        px(ox + 3, oy + dy, shift_color(body, -15))
        px(ox + 28, oy + dy, shift_color(body, -15))


draw_printer(13, 3)


def draw_whiteboard_piece(row, col, piece="tl"):
    """Whiteboard (2×2 tile), draw one quadrant."""
    ox, oy = tile_origin(row, col)

    frame = (160, 162, 168, 255)
    board = (240, 240, 245, 255)
    marker_red = (200, 60, 50, 255)
    marker_blue = (50, 80, 180, 255)

    if piece == "tl":
        # Frame top + left
        for dx in range(TILE):
            px(ox + dx, oy, frame)
            px(ox + dx, oy + 1, frame)
        for dy in range(TILE):
            px(ox, oy + dy, frame)
            px(ox + 1, oy + dy, frame)
        # Board surface
        for dy in range(2, TILE):
            for dx in range(2, TILE):
                px(ox + dx, oy + dy, vary_color(board, 2))
        # Some "writing" marks
        for dx in range(5, 25):
            px(ox + dx, oy + 8, marker_blue)
        for dx in range(5, 20):
            px(ox + dx, oy + 14, marker_red)
        for dx in range(5, 28):
            px(ox + dx, oy + 20, marker_blue)
    elif piece == "tr":
        # Frame top + right
        for dx in range(TILE):
            px(ox + dx, oy, frame)
            px(ox + dx, oy + 1, frame)
        for dy in range(TILE):
            px(ox + TILE - 1, oy + dy, frame)
            px(ox + TILE - 2, oy + dy, frame)
        # Board
        for dy in range(2, TILE):
            for dx in range(0, TILE - 2):
                px(ox + dx, oy + dy, vary_color(board, 2))
        # More writing
        for dx in range(0, 18):
            px(ox + dx, oy + 8, marker_blue)
        for dx in range(0, 22):
            px(ox + dx, oy + 14, marker_red)
    elif piece == "bl":
        for dy in range(TILE):
            px(ox, oy + dy, frame)
            px(ox + 1, oy + dy, frame)
        for dx in range(TILE):
            px(ox + dx, oy + TILE - 1, frame)
            px(ox + dx, oy + TILE - 2, frame)
        # Board
        for dy in range(0, TILE - 2):
            for dx in range(2, TILE):
                px(ox + dx, oy + dy, vary_color(board, 2))
        # Marker tray at bottom
        for dx in range(6, 26):
            px(ox + dx, oy + TILE - 4, (180, 182, 188, 255))
        # Markers
        for dx in range(8, 12):
            px(ox + dx, oy + TILE - 5, marker_red)
        for dx in range(14, 18):
            px(ox + dx, oy + TILE - 5, marker_blue)
        for dx in range(20, 24):
            px(ox + dx, oy + TILE - 5, (50, 160, 50, 255))
    elif piece == "br":
        for dy in range(TILE):
            px(ox + TILE - 1, oy + dy, frame)
            px(ox + TILE - 2, oy + dy, frame)
        for dx in range(TILE):
            px(ox + dx, oy + TILE - 1, frame)
            px(ox + dx, oy + TILE - 2, frame)
        for dy in range(0, TILE - 2):
            for dx in range(0, TILE - 2):
                px(ox + dx, oy + dy, vary_color(board, 2))


# Whiteboard 2×2 at (r13,c10)-(r14,c11) so drawDef can use [sheet, 13, 10, 2, 2]
draw_whiteboard_piece(13, 10, "tl")
draw_whiteboard_piece(13, 11, "tr")
draw_whiteboard_piece(14, 10, "bl")
draw_whiteboard_piece(14, 11, "br")


# ── Chairs, Filing Cabinet, Plant (Row 14) ──────────────────────────────────

def draw_office_chair(row, col, seat_color, back_color=None):
    """Office chair viewed from above (top-down)."""
    ox, oy = tile_origin(row, col)
    if back_color is None:
        back_color = shift_color(seat_color, -20)

    wheel_color = (55, 55, 60, 255)
    base_color = (70, 72, 78, 255)

    # Chair base (5-star pattern simplified to cross)
    for dx in range(6, 26):
        px(ox + dx, oy + TILE - 3, base_color)
    for dy in range(8, TILE - 3):
        px(ox + 15, oy + dy, base_color)
        px(ox + 16, oy + dy, base_color)

    # Wheels (5 points)
    wheel_positions = [(6, TILE-3), (25, TILE-3), (4, TILE-8), (27, TILE-8), (15, TILE-2)]
    for wx, wy in wheel_positions:
        if 0 <= wx < TILE and 0 <= wy < TILE:
            px(ox + wx, oy + wy, wheel_color)
            if wx + 1 < TILE:
                px(ox + wx + 1, oy + wy, wheel_color)

    # Chair back (top portion - curved)
    for dy in range(2, 12):
        for dx in range(6, 26):
            # Curved shape
            center_dist = abs(dx - 16)
            if center_dist < 10 - (dy - 2) * 0.3:
                px(ox + dx, oy + dy, vary_color(back_color, 4))

    # Chair back outline
    for dy in range(3, 11):
        left = max(6, int(7 + (dy - 3) * 0.3))
        right = min(25, int(25 - (dy - 3) * 0.3))
        px(ox + left, oy + dy, shift_color(back_color, -25))
        px(ox + right, oy + dy, shift_color(back_color, -25))
    for dx in range(8, 24):
        px(ox + dx, oy + 2, shift_color(back_color, -25))

    # Seat (lower rounded square)
    for dy in range(13, 24):
        for dx in range(7, 25):
            px(ox + dx, oy + dy, vary_color(seat_color, 4))
    # Seat edges
    for dy in range(13, 24):
        px(ox + 7, oy + dy, shift_color(seat_color, -15))
        px(ox + 24, oy + dy, shift_color(seat_color, -15))
    for dx in range(7, 25):
        px(ox + dx, oy + 13, shift_color(seat_color, 10))
        px(ox + dx, oy + 23, shift_color(seat_color, -15))

    # Armrests
    arm_color = shift_color(seat_color, -30)
    for dy in range(10, 22):
        px(ox + 5, oy + dy, arm_color)
        px(ox + 6, oy + dy, arm_color)
        px(ox + 25, oy + dy, arm_color)
        px(ox + 26, oy + dy, arm_color)


draw_office_chair(14, 0, (55, 58, 68, 255))     # Black
draw_office_chair(14, 1, (60, 80, 140, 255))     # Blue
draw_office_chair(14, 2, (140, 90, 50, 255))     # Brown/gold
draw_office_chair(14, 3, (130, 55, 55, 255))     # Red


def draw_filing_cabinet(row, col):
    """Filing cabinet viewed from front."""
    ox, oy = tile_origin(row, col)

    body = (100, 105, 115, 255)
    face = (120, 125, 135, 255)
    handle = (170, 172, 178, 255)
    shadow = (75, 78, 85, 255)

    # Body
    for dy in range(2, TILE - 1):
        for dx in range(4, 28):
            px(ox + dx, oy + dy, vary_color(body, 3))

    # Top surface
    for dx in range(4, 28):
        px(ox + dx, oy + 2, shift_color(body, 15))
        px(ox + dx, oy + 3, shift_color(body, 10))

    # Three drawer faces
    drawer_starts = [5, 14, 23]
    for ds in drawer_starts:
        for dy in range(ds, min(ds + 8, TILE - 2)):
            for dx in range(6, 26):
                px(ox + dx, oy + dy, vary_color(face, 3))
        # Handle
        for dx in range(13, 19):
            px(ox + dx, oy + ds + 2, handle)
            px(ox + dx, oy + ds + 3, handle)
        # Drawer line (gap between drawers)
        if ds + 8 < TILE - 1:
            for dx in range(5, 27):
                px(ox + dx, oy + ds + 8, shadow)

    # Edges
    for dy in range(2, TILE - 1):
        px(ox + 4, oy + dy, shift_color(body, -15))
        px(ox + 27, oy + dy, shadow)
    for dx in range(4, 28):
        px(ox + dx, oy + TILE - 1, shadow)


draw_filing_cabinet(14, 4)


def draw_potted_plant(row, col):
    """Potted plant with bushy leaves."""
    ox, oy = tile_origin(row, col)

    pot = (160, 95, 60, 255)
    pot_shadow = (130, 70, 40, 255)
    leaf_colors = [
        (60, 140, 55, 255), (50, 130, 45, 255),
        (70, 155, 65, 255), (45, 120, 40, 255),
    ]

    # Pot
    for dy in range(20, TILE - 1):
        width = 5 + (TILE - 1 - dy)  # Tapered pot
        center = TILE // 2
        for dx in range(center - width // 2, center + width // 2):
            if 0 <= dx < TILE:
                px(ox + dx, oy + dy, vary_color(pot, 4))
    # Pot rim
    for dx in range(9, 23):
        px(ox + dx, oy + 20, shift_color(pot, 15))
        px(ox + dx, oy + 21, pot)
    # Pot shadow
    for dx in range(11, 21):
        px(ox + dx, oy + TILE - 2, pot_shadow)

    # Leaves (bushy, overlapping circles)
    random.seed(14000 + col)
    leaf_centers = [
        (16, 10), (10, 12), (22, 12), (13, 8), (19, 8),
        (16, 6), (12, 14), (20, 14), (8, 10), (24, 10),
    ]
    for lx, ly in leaf_centers:
        lc = random.choice(leaf_colors)
        r = random.randint(3, 5)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    xx, yy = lx + dx, ly + dy
                    if 0 <= xx < TILE and 0 <= yy < 20:
                        px(ox + xx, oy + yy, vary_color(lc, 5))

    # Leaf highlights
    for lx, ly in leaf_centers[:5]:
        px(ox + lx - 1, oy + ly - 1, shift_color(leaf_colors[0], 25))


draw_potted_plant(14, 5)

# Smaller plant
def draw_small_plant(row, col):
    """Smaller desk plant."""
    ox, oy = tile_origin(row, col)

    pot = (140, 140, 145, 255)
    leaf_colors = [(60, 145, 55, 255), (55, 135, 50, 255), (70, 155, 65, 255)]

    # Small pot
    for dy in range(22, TILE - 1):
        w = 4 + (TILE - 1 - dy) // 2
        cx = TILE // 2
        for dx in range(cx - w, cx + w):
            if 0 <= dx < TILE:
                px(ox + dx, oy + dy, vary_color(pot, 3))
    for dx in range(11, 21):
        px(ox + dx, oy + 22, shift_color(pot, 10))

    # Leaves
    random.seed(14100 + col)
    leaf_centers = [(16, 14), (12, 16), (20, 16), (14, 12), (18, 12)]
    for lx, ly in leaf_centers:
        lc = random.choice(leaf_colors)
        r = 3
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    xx, yy = lx + dx, ly + dy
                    if 0 <= xx < TILE and 0 <= yy < 22:
                        px(ox + xx, oy + yy, vary_color(lc, 5))


draw_small_plant(14, 6)


# ── Conference Table & Bookshelf (Row 15) ────────────────────────────────────

def draw_conf_table_piece(row, col, piece):
    """Conference table piece (2×2 table)."""
    ox, oy = tile_origin(row, col)

    table = (160, 130, 90, 255)
    edge = (130, 100, 65, 255)
    highlight = (175, 145, 105, 255)

    if piece == "tl":
        for dy in range(TILE):
            for dx in range(TILE):
                px(ox + dx, oy + dy, vary_color(table, 3))
        # Top edge
        for dx in range(TILE):
            px(ox + dx, oy, highlight)
        # Left edge
        for dy in range(TILE):
            px(ox, oy + dy, edge)
        # Corner roundness
        px(ox, oy, edge)
        px(ox + 1, oy, edge)
        px(ox, oy + 1, edge)
    elif piece == "tr":
        for dy in range(TILE):
            for dx in range(TILE):
                px(ox + dx, oy + dy, vary_color(table, 3))
        for dx in range(TILE):
            px(ox + dx, oy, highlight)
        for dy in range(TILE):
            px(ox + TILE - 1, oy + dy, edge)
    elif piece == "bl":
        for dy in range(TILE):
            for dx in range(TILE):
                px(ox + dx, oy + dy, vary_color(table, 3))
        for dx in range(TILE):
            px(ox + dx, oy + TILE - 1, shift_color(edge, -10))
        for dy in range(TILE):
            px(ox, oy + dy, edge)
    elif piece == "br":
        for dy in range(TILE):
            for dx in range(TILE):
                px(ox + dx, oy + dy, vary_color(table, 3))
        for dx in range(TILE):
            px(ox + dx, oy + TILE - 1, shift_color(edge, -10))
        for dy in range(TILE):
            px(ox + TILE - 1, oy + dy, edge)

    # Wood grain
    random.seed(15000 + col * 10 + hash(piece))
    for g in range(3):
        gy = random.randint(3, TILE - 4)
        gc = shift_color(table, -8)
        for dx in range(2, TILE - 2):
            if random.random() > 0.15:
                px(ox + dx, oy + gy, gc)


draw_conf_table_piece(15, 0, "tl")
draw_conf_table_piece(15, 1, "tr")
draw_conf_table_piece(15, 2, "bl")
draw_conf_table_piece(15, 3, "br")


def draw_conf_chair(row, col, facing="top"):
    """Conference chair (simpler than office chair)."""
    ox, oy = tile_origin(row, col)

    seat = (75, 80, 95, 255)
    back = (60, 63, 75, 255)

    if facing == "top":
        # Back at top, seat below
        for dy in range(4, 10):
            for dx in range(6, 26):
                px(ox + dx, oy + dy, vary_color(back, 3))
        for dy in range(11, 24):
            for dx in range(8, 24):
                px(ox + dx, oy + dy, vary_color(seat, 4))
        # Legs
        for dy in range(25, TILE - 1):
            px(ox + 9, oy + dy, (55, 55, 60, 255))
            px(ox + 22, oy + dy, (55, 55, 60, 255))
    else:
        # Seat at top, back at bottom
        for dy in range(8, 21):
            for dx in range(8, 24):
                px(ox + dx, oy + dy, vary_color(seat, 4))
        for dy in range(22, 28):
            for dx in range(6, 26):
                px(ox + dx, oy + dy, vary_color(back, 3))
        # Legs
        for dy in range(2, 7):
            px(ox + 9, oy + dy, (55, 55, 60, 255))
            px(ox + 22, oy + dy, (55, 55, 60, 255))


draw_conf_chair(15, 4, "top")
draw_conf_chair(15, 5, "bottom")


def draw_bookshelf_piece(row, col, piece):
    """Bookshelf (2×2 tile)."""
    ox, oy = tile_origin(row, col)

    frame = (120, 85, 55, 255)
    shelf = (140, 100, 65, 255)
    book_colors = [
        (180, 60, 50, 255), (50, 80, 160, 255), (50, 140, 60, 255),
        (180, 140, 50, 255), (140, 60, 140, 255), (200, 120, 60, 255),
        (80, 80, 85, 255), (160, 160, 165, 255),
    ]

    # Frame
    if piece in ("tl", "bl"):
        for dy in range(TILE):
            px(ox, oy + dy, frame)
            px(ox + 1, oy + dy, frame)
    if piece in ("tr", "br"):
        for dy in range(TILE):
            px(ox + TILE - 1, oy + dy, frame)
            px(ox + TILE - 2, oy + dy, frame)
    if piece in ("tl", "tr"):
        for dx in range(TILE):
            px(ox + dx, oy, frame)
            px(ox + dx, oy + 1, frame)
    if piece in ("bl", "br"):
        for dx in range(TILE):
            px(ox + dx, oy + TILE - 1, frame)
            px(ox + dx, oy + TILE - 2, frame)

    # Background (dark wood)
    margin_l = 2 if piece in ("tl", "bl") else 0
    margin_r = TILE - 2 if piece in ("tr", "br") else TILE
    margin_t = 2 if piece in ("tl", "tr") else 0
    margin_b = TILE - 2 if piece in ("bl", "br") else TILE

    bg = (85, 60, 38, 255)
    for dy in range(margin_t, margin_b):
        for dx in range(margin_l, margin_r):
            px(ox + dx, oy + dy, vary_color(bg, 3))

    # Shelf dividers (horizontal)
    shelf_ys = [15] if piece in ("tl", "tr") else [15]
    for sy in shelf_ys:
        for dx in range(margin_l, margin_r):
            px(ox + dx, oy + sy, shelf)
            px(ox + dx, oy + sy + 1, shift_color(shelf, -10))

    # Books
    random.seed(15500 + col * 10 + hash(piece))
    # Top shelf books
    bx = margin_l + 1
    for _ in range(8):
        bc = random.choice(book_colors)
        bw = random.randint(2, 4)
        bh = random.randint(10, 13)
        for dy in range(max(margin_t + 1, shelf_ys[0] - bh), shelf_ys[0]):
            for dx in range(bx, min(bx + bw, margin_r)):
                px(ox + dx, oy + dy, vary_color(bc, 5))
        # Book spine highlight
        if bx < margin_r:
            for dy in range(max(margin_t + 1, shelf_ys[0] - bh), shelf_ys[0]):
                px(ox + bx, oy + dy, shift_color(bc, 15))
        bx += bw + 1
        if bx >= margin_r - 2:
            break

    # Bottom shelf books
    bx = margin_l + 1
    for _ in range(8):
        bc = random.choice(book_colors)
        bw = random.randint(2, 4)
        bh = random.randint(10, 13)
        bottom = margin_b - 1
        for dy in range(max(shelf_ys[0] + 2, bottom - bh), bottom):
            for dx in range(bx, min(bx + bw, margin_r)):
                px(ox + dx, oy + dy, vary_color(bc, 5))
        if bx < margin_r:
            for dy in range(max(shelf_ys[0] + 2, bottom - bh), bottom):
                px(ox + bx, oy + dy, shift_color(bc, 15))
        bx += bw + 1
        if bx >= margin_r - 2:
            break


# Bookshelf 2×2 at (r13,c12)-(r14,c13) so drawDef can use [sheet, 13, 12, 2, 2]
draw_bookshelf_piece(13, 12, "tl")
draw_bookshelf_piece(13, 13, "tr")
draw_bookshelf_piece(14, 12, "bl")
draw_bookshelf_piece(14, 13, "br")


# ── Meeting Room Tiles (r15, c10=floor, c11=wall) ────────────────────────────

def draw_meeting_floor(row, col):
    """Meeting room floor — deep blue-gray carpet with subtle diamond pattern."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col + 9999)

    base = (58, 62, 82, 255)
    light = (66, 70, 92, 255)
    dark = (48, 52, 70, 255)

    for dy in range(TILE):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(base, 3))

    # Diamond / argyle pattern
    for dy in range(TILE):
        for dx in range(TILE):
            # Diamond grid every 8px
            cx = (dx % 16) - 8
            cy = (dy % 16) - 8
            diamond = abs(cx) + abs(cy)
            if diamond == 7 or diamond == 8:
                px(ox + dx, oy + dy, vary_color(dark, 2))
            elif diamond <= 3:
                px(ox + dx, oy + dy, vary_color(light, 2))

    # Subtle woven texture overlay
    for dy in range(TILE):
        for dx in range(TILE):
            if (dx + dy) % 6 == 0:
                existing = img.getpixel((ox + dx, oy + dy))
                px(ox + dx, oy + dy, shift_color(existing, -4))


draw_meeting_floor(15, 10)


def draw_meeting_wall(row, col):
    """Meeting room wall/partition — dark wood panel with trim."""
    ox, oy = tile_origin(row, col)
    random.seed(row * 1000 + col + 8888)

    panel = (55, 45, 35, 255)
    trim = (75, 60, 45, 255)
    groove = (40, 32, 24, 255)
    highlight = (68, 55, 42, 255)

    # Main panel fill
    for dy in range(TILE):
        for dx in range(TILE):
            px(ox + dx, oy + dy, vary_color(panel, 3))

    # Wood grain (horizontal)
    for g in range(5):
        gy = 3 + g * 6
        if gy < TILE:
            gc = shift_color(panel, -8)
            for dx in range(TILE):
                if random.random() > 0.15:
                    px(ox + dx, oy + gy, vary_color(gc, 2))

    # Panel groove lines (vertical dividers)
    for gx in [0, TILE - 1]:
        for dy in range(TILE):
            px(ox + gx, oy + dy, groove)

    # Top trim
    for dx in range(TILE):
        px(ox + dx, oy, trim)
        px(ox + dx, oy + 1, highlight)

    # Bottom trim
    for dx in range(TILE):
        px(ox + dx, oy + TILE - 1, groove)
        px(ox + dx, oy + TILE - 2, trim)

    # Center inset panel detail
    for dy in range(4, TILE - 4):
        px(ox + 4, oy + dy, groove)
        px(ox + TILE - 5, oy + dy, groove)
    for dx in range(4, TILE - 4):
        px(ox + dx, oy + 4, groove)
        px(ox + dx, oy + TILE - 5, groove)
    # Inset highlight
    for dx in range(5, TILE - 5):
        px(ox + dx, oy + 5, highlight)
    for dy in range(5, TILE - 5):
        px(ox + 5, oy + dy, highlight)


draw_meeting_wall(15, 11)


# ── Save ─────────────────────────────────────────────────────────────────────
import os
outdir = os.path.join(os.path.dirname(__file__), "..", "frontend", "assets", "office", "tilesets", "generated")
os.makedirs(outdir, exist_ok=True)
outpath = os.path.join(outdir, "generated_tiles_32x32.png")
img.save(outpath)
print(f"Saved {W}×{H} tileset to {outpath}")
print(f"Rows: {ROWS}, Cols: {COLS}")
