#!/usr/bin/env python3
"""
Ember Veil — Fox Avatar in HBO Profile Style (v3, final polish)
Rounder face, larger expressive eyes, stronger HBO aesthetic.
"""

import math
from PIL import Image, ImageDraw, ImageFilter
import numpy as np

W, H = 800, 800

# ── Palette ────────────────────────────────────────────────────────
BG_DEEP = (12, 15, 25)
BG_MID = (20, 25, 40)
AMBER = (215, 148, 58)
AMBER_LIGHT = (232, 175, 85)
COPPER = (180, 108, 42)
BURNT = (140, 75, 30)
DARK_AMBER = (88, 48, 20)
GOLD_BRIGHT = (245, 190, 95)
EYE_GOLD = (225, 188, 65)
EYE_INNER = (200, 160, 45)
PUPIL = (18, 14, 8)
NOSE_DARK = (35, 25, 18)
WHITE_CREAM = (238, 228, 212)
CREAM_MID = (218, 205, 182)


def lerp(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def spoly(img, pts, color, blur=2.5):
    mask = Image.new('L', (W * 2, H * 2), 0)
    ImageDraw.Draw(mask).polygon([(int(x * 2), int(y * 2)) for x, y in pts], fill=255)
    mask = mask.resize((W, H), Image.LANCZOS)
    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    layer = Image.new('RGBA', (W, H), color + (255,))
    img.paste(Image.composite(layer, img, mask))


def sellip(img, cx, cy, rx, ry, color, blur=2):
    mask = Image.new('L', (W * 2, H * 2), 0)
    ImageDraw.Draw(mask).ellipse([
        int((cx - rx) * 2), int((cy - ry) * 2),
        int((cx + rx) * 2), int((cy + ry) * 2)
    ], fill=255)
    mask = mask.resize((W, H), Image.LANCZOS)
    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    layer = Image.new('RGBA', (W, H), color + (255,))
    img.paste(Image.composite(layer, img, mask))


def radial_glow(img, cx, cy, r, color, strength=0.5):
    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    gpx = glow.load()
    r2 = r * r
    ylo, yhi = max(0, int(cy - r)), min(H, int(cy + r) + 1)
    xlo, xhi = max(0, int(cx - r)), min(W, int(cx + r) + 1)
    for y in range(ylo, yhi):
        for x in range(xlo, xhi):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 < r2:
                t = 1.0 - math.sqrt(d2) / r
                a = int(t * t * t * strength * 255)
                gpx[x, y] = color + (a,)
    return Image.alpha_composite(img, glow)


def background():
    img = Image.new('RGBA', (W, H), BG_DEEP + (255,))
    px = img.load()
    cx, cy = W // 2, H // 2 - 30
    md = math.sqrt(cx ** 2 + (cy + 30) ** 2)
    for y in range(H):
        for x in range(W):
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2) / md
            c = lerp(BG_MID, BG_DEEP, d * 0.65 + 0.35)
            n = (hash((x * 7919 + y * 104729) % 999983) % 5) - 2
            c = tuple(max(0, min(255, v + n)) for v in c)
            px[x, y] = c + (255,)
    return img


def fox(img):
    cx, cy = 400, 405

    # ── EARS (behind head) ─────────────────────────────────────────
    for s in [-1, 1]:
        # Outer ear
        spoly(img, [
            (cx + s * 48, cy - 130),
            (cx + s * 108, cy - 290),
            (cx + s * 158, cy - 65),
        ], BURNT, 2)
        # Inner ear
        spoly(img, [
            (cx + s * 60, cy - 138),
            (cx + s * 105, cy - 260),
            (cx + s * 145, cy - 85),
        ], DARK_AMBER, 3)

    # ── HEAD (rounder, wider polygon) ──────────────────────────────
    # More points for roundness
    head_pts = []
    n_pts = 24
    # Superellipse-ish shape: wider at cheeks, tapered chin
    for i in range(n_pts):
        angle = 2 * math.pi * i / n_pts - math.pi / 2  # start from top
        # Base ellipse
        base_rx, base_ry = 160, 155
        x = math.cos(angle) * base_rx
        y = math.sin(angle) * base_ry

        # Make top flatter, bottom pointier
        if y > 40:  # chin area
            squeeze = 1.0 - (y - 40) / 160 * 0.35
            x *= squeeze

        # Puff out the cheeks slightly
        if -30 < y < 40:
            puff = 1.0 + 0.06 * math.cos(math.pi * (y + 30) / 70)
            x *= puff

        head_pts.append((cx + x, cy + y))

    spoly(img, head_pts, AMBER, 3)

    # ── FOREHEAD BRIGHT ZONE ───────────────────────────────────────
    spoly(img, [
        (cx, cy - 148),
        (cx + 60, cy - 105),
        (cx + 40, cy - 40),
        (cx, cy - 15),
        (cx - 40, cy - 40),
        (cx - 60, cy - 105),
    ], AMBER_LIGHT, 7)

    # Center bright strip
    spoly(img, [
        (cx, cy - 145),
        (cx + 25, cy - 105),
        (cx + 15, cy - 45),
        (cx, cy - 25),
        (cx - 15, cy - 45),
        (cx - 25, cy - 105),
    ], GOLD_BRIGHT, 8)

    # ── SIDE SHADOW PLANES ─────────────────────────────────────────
    for s in [-1, 1]:
        spoly(img, [
            (cx + s * 75, cy - 120),
            (cx + s * 160, cy - 20),
            (cx + s * 162, cy + 40),
            (cx + s * 130, cy + 90),
            (cx + s * 55, cy + 15),
        ], COPPER, 5)

        # Deeper edge shadow
        spoly(img, [
            (cx + s * 120, cy - 50),
            (cx + s * 162, cy + 10),
            (cx + s * 150, cy + 70),
            (cx + s * 115, cy + 10),
        ], BURNT, 6)

    # ── MUZZLE (white/cream area) ──────────────────────────────────
    spoly(img, [
        (cx - 70, cy + 15),
        (cx - 25, cy - 8),
        (cx, cy - 15),
        (cx + 25, cy - 8),
        (cx + 70, cy + 15),
        (cx + 62, cy + 80),
        (cx + 38, cy + 120),
        (cx, cy + 135),
        (cx - 38, cy + 120),
        (cx - 62, cy + 80),
    ], WHITE_CREAM, 4)

    # Muzzle center shadow
    spoly(img, [
        (cx - 28, cy + 50),
        (cx, cy + 38),
        (cx + 28, cy + 50),
        (cx + 20, cy + 95),
        (cx, cy + 110),
        (cx - 20, cy + 95),
    ], CREAM_MID, 6)

    # ── NOSE ───────────────────────────────────────────────────────
    # Rounded triangular nose
    spoly(img, [
        (cx - 18, cy + 50),
        (cx, cy + 40),
        (cx + 18, cy + 50),
        (cx + 14, cy + 65),
        (cx, cy + 72),
        (cx - 14, cy + 65),
    ], NOSE_DARK, 2)
    # Nose shine
    spoly(img, [
        (cx - 10, cy + 45),
        (cx + 10, cy + 45),
        (cx + 7, cy + 53),
        (cx - 7, cy + 53),
    ], (58, 45, 35), 2)

    # Mouth line
    spoly(img, [
        (cx - 1, cy + 72),
        (cx + 1, cy + 72),
        (cx + 1, cy + 88),
        (cx - 1, cy + 88),
    ], (55, 40, 32), 2)

    # ── EYES (larger, more expressive) ─────────────────────────────
    ey = cy - 25
    for s in [-1, 1]:
        ex = cx + s * 58

        # Dark socket
        sellip(img, ex, ey, 32, 22, (30, 22, 14), blur=5)

        # Eye white/gold (almond)
        eye_pts = [
            (ex - 27, ey + 2),
            (ex - 14, ey - 16),
            (ex + 4, ey - 18),
            (ex + 22, ey - 10),
            (ex + 27, ey),
            (ex + 18, ey + 15),
            (ex, ey + 17),
            (ex - 18, ey + 12),
        ]
        spoly(img, eye_pts, EYE_GOLD, 2)

        # Inner iris
        sellip(img, ex + s * 1, ey, 16, 13, EYE_INNER, blur=2)

        # Pupil (vertical slit)
        sellip(img, ex + s * 1, ey, 4.5, 13, PUPIL, blur=1)

        # Primary catch light (top-left-ish)
        sellip(img, ex - s * 8, ey - 6, 6, 4.5, (255, 250, 230), blur=2)

        # Secondary catch light
        sellip(img, ex + s * 5, ey + 5, 3, 2.5, (250, 240, 210), blur=1.5)

    # ── BROW MARKS (subtle darker fur above eyes) ──────────────────
    for s in [-1, 1]:
        ex = cx + s * 58
        spoly(img, [
            (ex - s * 5, ey - 25),
            (ex + s * 30, ey - 30),
            (ex + s * 32, ey - 22),
            (ex - s * 3, ey - 18),
        ], COPPER, 5)

    # ── CHEEK MARKS ────────────────────────────────────────────────
    for s in [-1, 1]:
        ex = cx + s * 58
        spoly(img, [
            (ex + s * 18, ey + 18),
            (ex + s * 55, cy + 30),
            (ex + s * 52, cy + 38),
            (ex + s * 15, ey + 24),
        ], DARK_AMBER, 5)

    # ── WHISKER DOTS ───────────────────────────────────────────────
    for s in [-1, 1]:
        for i, (dx, dy) in enumerate([(42, 0), (55, 6), (48, 14)]):
            sellip(img, cx + s * dx, cy + 75 + dy, 2.5, 2.5, NOSE_DARK, blur=2)


def atmosphere(img):
    """Multi-pass HBO atmospheric treatment."""
    # Pass 1: strong bloom
    bloom = img.filter(ImageFilter.GaussianBlur(20))
    img = Image.blend(img, bloom, 0.2)

    # Pass 2: warm glow from center
    img = radial_glow(img, W // 2, H // 2 - 25, 280, (55, 38, 15), strength=0.4)

    # Pass 3: subtle amber halo around fox
    img = radial_glow(img, W // 2, H // 2 - 25, 200, (90, 55, 20), strength=0.12)

    # Pass 4: vignette (heavy, HBO-dark)
    vig = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    vpx = vig.load()
    cxv, cyv = W // 2, H // 2
    mr = math.sqrt(cxv ** 2 + cyv ** 2)
    for y in range(H):
        for x in range(W):
            d = math.sqrt((x - cxv) ** 2 + (y - cyv) ** 2) / mr
            a = int(max(0, min(230, (d - 0.3) * 420)))
            vpx[x, y] = (6, 8, 16, a)
    img = Image.alpha_composite(img.convert('RGBA'), vig)

    # Pass 5: light secondary bloom for dreamy quality
    bloom2 = img.filter(ImageFilter.GaussianBlur(6))
    img = Image.blend(img, bloom2, 0.08)

    # Pass 6: film grain
    px = img.load()
    for y in range(H):
        for x in range(W):
            g = (hash((x * 7919 + y * 104729 + 31) % 999983) % 7) - 3
            r, gv, b, a = px[x, y]
            px[x, y] = (
                max(0, min(255, r + g)),
                max(0, min(255, gv + g)),
                max(0, min(255, b + g)),
                a
            )
    return img


def main():
    print("Fox avatar v3...")
    img = background()
    print("  BG")
    fox(img)
    print("  Fox")
    img = atmosphere(img)
    print("  Atmosphere")

    out = "/Users/yuzhengxu/projects/OneManCompany/docs/report/fox-avatar.png"
    img.save(out, "PNG")
    print(f"  Done: {out}")


if __name__ == "__main__":
    main()
