"""
Generate OG image for 1mancompany/claude-code repository.
Terminal Cartography aesthetic — dark field, monospace glyphs, systematic marks.
Refined pass: bolder title, larger nodes, stronger visual hierarchy.
"""

import math
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

random.seed(42)

W, H = 1200, 630
FONT_DIR = "/Users/yuzhengxu/.claude/skills/canvas-design/canvas-fonts"

# === Color palette ===
BG = (12, 12, 16)
ACCENT = (235, 170, 55)       # warm amber
ACCENT_BRIGHT = (255, 195, 75)
GRID_LINE = (28, 28, 36)
GLYPH_DIM = (45, 45, 55)
GLYPH_MED = (75, 75, 90)
LABEL_CLR = (130, 130, 148)
WHITE_SOFT = (215, 215, 225)
CYAN = (75, 195, 210)

img = Image.new("RGBA", (W, H), BG)
draw = ImageDraw.Draw(img)

# === Fonts ===
font_mono_xs = ImageFont.truetype(f"{FONT_DIR}/RedHatMono-Regular.ttf", 8)
font_mono_sm = ImageFont.truetype(f"{FONT_DIR}/DMMono-Regular.ttf", 10)
font_label = ImageFont.truetype(f"{FONT_DIR}/IBMPlexMono-Regular.ttf", 11)
font_label_bold = ImageFont.truetype(f"{FONT_DIR}/IBMPlexMono-Bold.ttf", 11)
font_title = ImageFont.truetype(f"{FONT_DIR}/GeistMono-Bold.ttf", 52)
font_subtitle = ImageFont.truetype(f"{FONT_DIR}/GeistMono-Regular.ttf", 16)
font_stat_key = ImageFont.truetype(f"{FONT_DIR}/IBMPlexMono-Bold.ttf", 11)
font_stat_val = ImageFont.truetype(f"{FONT_DIR}/IBMPlexMono-Regular.ttf", 11)
font_tiny = ImageFont.truetype(f"{FONT_DIR}/DMMono-Regular.ttf", 8)
font_node_label = ImageFont.truetype(f"{FONT_DIR}/GeistMono-Regular.ttf", 11)
font_node_label_hi = ImageFont.truetype(f"{FONT_DIR}/GeistMono-Bold.ttf", 12)

# === Layer 1: Grid ===
for x in range(0, W, 40):
    a = 18 + int(6 * math.sin(x * 0.015))
    draw.line([(x, 0), (x, H)], fill=(*GRID_LINE[:3], a), width=1)
for y in range(0, H, 40):
    a = 18 + int(6 * math.cos(y * 0.02))
    draw.line([(0, y), (W, y)], fill=(*GRID_LINE[:3], a), width=1)

# === Layer 2: Code texture ===
code_frags = [
    "async", "await", "import", "export", "const", "function", "return",
    "class", "interface", "type", "enum", "module", "yield", "Promise",
    "=>", "===", "!==", "&&", "||", "??", "?.", "...",
    "tool", "agent", "spawn", "bash", "grep", "glob", "read", "edit",
    "{}", "[]", "()", "<>", "//",
    "0x", "ff", "00", "1a", "3f", "7e",
]

# Left region — dense
for _ in range(220):
    x = random.randint(20, 350)
    y = random.randint(20, H - 20)
    draw.text((x, y), random.choice(code_frags),
              fill=(*GLYPH_DIM[:3], random.randint(22, 50)), font=font_mono_xs)

# Background texture everywhere (very faint)
for _ in range(90):
    x = random.randint(380, W - 50)
    y = random.randint(20, H - 20)
    draw.text((x, y), random.choice(code_frags),
              fill=(*GLYPH_DIM[:3], random.randint(12, 28)), font=font_mono_xs)

# === Layer 3: Architecture graph ===
nodes = [
    # Left cluster — tool architecture
    (150, 130, "CLI", 8, True),
    (270, 80, "AgentTool", 7, True),
    (340, 170, "Bash", 5, False),
    (290, 250, "FileOps", 5, False),
    (170, 290, "Permissions", 5, False),
    (110, 210, "MCP", 7, True),
    (230, 165, "Executor", 7, True),
    (75, 150, "Config", 4, False),
    (190, 380, "Skills", 5, False),
    (310, 340, "LSP", 4, False),
    (90, 360, "OAuth", 4, False),
    (250, 410, "Hooks", 4, False),
    (360, 280, "WebFetch", 4, False),
    (130, 450, "Teams", 4, False),
    (50, 300, "State", 5, False),
    (320, 420, "Telemetry", 4, False),
    # Right cluster — source tree
    (780, 130, "src/", 7, True),
    (870, 85, "commands/", 5, False),
    (940, 155, "tools/", 6, True),
    (870, 215, "agents/", 5, False),
    (780, 260, "core/", 5, False),
    (960, 230, "ui/", 4, False),
    (820, 330, "services/", 5, False),
    (910, 300, "bridge/", 4, False),
    (750, 380, "utils/", 4, False),
    (850, 390, "types/", 4, False),
    (970, 350, "plugins/", 4, False),
]

edges = [
    (0, 6), (0, 7), (6, 1), (6, 2), (6, 3), (6, 5),
    (5, 4), (4, 14), (6, 8), (8, 11), (3, 9), (3, 12),
    (4, 10), (8, 13), (14, 10), (9, 15),
    (16, 17), (16, 18), (16, 19), (16, 20), (18, 21),
    (20, 22), (22, 23), (20, 24), (22, 25), (21, 26),
    (1, 19), (5, 22), (2, 18),
]

# Edges
for i, j in edges:
    x1, y1 = nodes[i][0], nodes[i][1]
    x2, y2 = nodes[j][0], nodes[j][1]
    is_cross = (i < 16 and j >= 16) or (j < 16 and i >= 16)
    if is_cross:
        steps = 40
        for s in range(0, steps, 2):
            t1, t2 = s / steps, min((s + 1) / steps, 1.0)
            draw.line([
                (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1),
                (x1 + (x2 - x1) * t2, y1 + (y2 - y1) * t2)
            ], fill=(*CYAN[:3], 45), width=1)
    else:
        draw.line([(x1, y1), (x2, y2)], fill=(*ACCENT[:3], 40), width=1)

# Nodes
for x, y, label, size, hi in nodes:
    if hi:
        for r in range(size + 12, size, -1):
            a = int(20 * (1 - (r - size) / 12))
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(*ACCENT[:3], a))
        draw.ellipse([x - size, y - size, x + size, y + size], fill=ACCENT_BRIGHT)
    else:
        draw.ellipse([x - size, y - size, x + size, y + size], fill=(*GLYPH_MED[:3], 200))
    draw.text(
        (x + size + 6, y - 7),
        label,
        fill=WHITE_SOFT if hi else LABEL_CLR,
        font=font_node_label_hi if hi else font_node_label
    )

# === Layer 4: Cartographic ruler ===
y_ruler = 38
draw.line([(440, y_ruler), (1050, y_ruler)], fill=(*ACCENT[:3], 45), width=1)
for tick in range(440, 1051, 50):
    draw.line([(tick, y_ruler - 4), (tick, y_ruler + 4)], fill=(*ACCENT[:3], 65), width=1)
# Ruler labels
draw.text((450, y_ruler + 8), "1,900 files", fill=(*ACCENT[:3], 100), font=font_tiny)
draw.text((660, y_ruler + 8), "512K+ lines", fill=(*ACCENT[:3], 100), font=font_tiny)
draw.text((880, y_ruler + 8), "~40 tools", fill=(*ACCENT[:3], 100), font=font_tiny)

# Right edge ruler
x_re = W - 30
draw.line([(x_re, 90), (x_re, 500)], fill=(*ACCENT[:3], 35), width=1)
for ty in range(90, 501, 41):
    draw.line([(x_re - 3, ty), (x_re + 3, ty)], fill=(*ACCENT[:3], 55), width=1)

# === Layer 5: Title — center ===
title_x, title_y = 430, 200

# Draw "claude" in white
draw.text((title_x, title_y), "claude", fill=WHITE_SOFT, font=font_title)
bbox = draw.textbbox((title_x, title_y), "claude", font=font_title)
# Draw "-code" in amber
draw.text((bbox[2], title_y), "-code", fill=ACCENT_BRIGHT, font=font_title)

# Thin accent line under title
title_end_bbox = draw.textbbox((bbox[2], title_y), "-code", font=font_title)
line_y = title_y + 62
draw.line([(title_x, line_y), (title_end_bbox[2], line_y)], fill=(*ACCENT[:3], 80), width=1)

# Subtitle
draw.text(
    (title_x + 2, line_y + 10),
    "source architecture · mapped",
    fill=(*LABEL_CLR[:3], 170), font=font_subtitle
)

# Repo URL
draw.text(
    (title_x + 2, line_y + 34),
    "github.com/1mancompany/claude-code",
    fill=(*ACCENT[:3], 130), font=font_label
)

# === Layer 6: Stats ===
stats_x = 430
stats_y = 400
stats = [
    ("RUNTIME", "Bun + TypeScript"),
    ("TOOLS", "Bash · Read · Edit · Grep · Glob · Agent"),
    ("ARCH", "React/Ink TUI · MCP · LSP · OAuth"),
    ("AGENTS", "Multi-agent coordinator · sub-spawning"),
]
for i, (k, v) in enumerate(stats):
    yp = stats_y + i * 24
    draw.text((stats_x, yp), k, fill=(*ACCENT[:3], 160), font=font_stat_key)
    draw.text((stats_x + 75, yp), v, fill=(*LABEL_CLR[:3], 140), font=font_stat_val)

# === Layer 7: Corner annotations ===
draw.text((15, 8), "N 37°46'30\"", fill=(*GLYPH_DIM[:3], 55), font=font_tiny)
draw.text((15, 20), "W 122°25'10\"", fill=(*GLYPH_DIM[:3], 55), font=font_tiny)
draw.text((W - 85, H - 20), "REV 2026.03", fill=(*GLYPH_DIM[:3], 55), font=font_tiny)

# === Layer 8: Hash borders ===
h1 = "".join(random.choices("0123456789abcdef", k=180))
draw.text((20, H - 14), h1, fill=(*GLYPH_DIM[:3], 28), font=font_mono_xs)
h2 = "".join(random.choices("0123456789abcdef", k=180))
draw.text((20, 2), h2, fill=(*GLYPH_DIM[:3], 22), font=font_mono_xs)

# === Layer 9: Scan lines ===
scan = Image.new("RGBA", (W, H), (0, 0, 0, 0))
sd = ImageDraw.Draw(scan)
for y in range(0, H, 3):
    sd.line([(0, y), (W, y)], fill=(0, 0, 0, 6), width=1)
img = Image.alpha_composite(img, scan)

# === Layer 10: Vignette ===
vig = Image.new("RGBA", (W, H), (0, 0, 0, 0))
vd = ImageDraw.Draw(vig)
cx, cy = W // 2, H // 2
md = math.sqrt(cx**2 + cy**2)
for r in range(0, int(md), 2):
    a = int(40 * (r / md) ** 2.2)
    if a > 0:
        vd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 0, 0, min(a, 45)))
img = Image.alpha_composite(img, vig)

# === Save ===
final = img.convert("RGB")
out = "/Users/yuzhengxu/projects/OneManCompany/docs/og-claude-code.png"
final.save(out, "PNG")
print(f"Saved: {out} ({final.size})")
