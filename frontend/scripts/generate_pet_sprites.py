#!/usr/bin/env python3
"""
Generate ~246 pixel-art sprite layer PNGs for the pet system.

Each species (cat/dog/hamster) × 6 poses × multiple part variants.
All images are 32×32 RGBA with transparency.

Layer types:
  - body.png:       Grayscale body fill (tinted by frontend)
  - pattern_*.png:  Darker markings on transparent
  - ears_*.png:     Ear shapes on transparent
  - tail_*.png:     Tail shapes on transparent (cat/dog only)
  - cheeks_*.png:   Cheek pouches on transparent (hamster only)
  - eyes.png:       Grayscale eyes (tinted by frontend)
  - lineart.png:    Black outline on transparent
  - collar.png:     Grayscale collar (dog only, tinted by frontend)

Usage:
    python scripts/generate_pet_sprites.py
"""

from __future__ import annotations

import os
from pathlib import Path
from PIL import Image

# ── Output root ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUT_ROOT = PROJECT_ROOT / "frontend" / "sprites" / "pets"

W, H = 32, 32

# ── Color constants ──────────────────────────────────────────────────────────
TRANSPARENT = (0, 0, 0, 0)
BLACK = (20, 20, 22, 255)         # lineart
BODY_LIGHT = (220, 220, 220, 255) # body fill (grayscale, tinted by FE)
BODY_BELLY = (240, 240, 240, 255) # lighter belly area
BODY_SHADOW = (180, 180, 180, 255)# shadow areas
EYE_FILL = (210, 210, 210, 255)   # eye base (tinted)
EYE_SHINE = (255, 255, 255, 255)  # highlight stays white
PATTERN_DARK = (90, 90, 90, 200)  # pattern markings
PATTERN_MED = (120, 120, 120, 180)
EAR_INNER = (190, 170, 170, 255)  # inner ear pink-gray
COLLAR_GRAY = (200, 200, 200, 255)
COLLAR_TAG = (230, 230, 230, 255)
CHEEK_FILL = (210, 200, 195, 255)


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE DEFINITIONS — 32×32 pixel maps per species per pose
# Characters: .=transparent  B=body  b=belly  s=shadow  L=lineart(outline)
#             E=eye  H=eye_shine  P=pupil  N=nose  W=whisker  I=inner_ear
#             T=tail  C=collar  K=collar_tag  Q=cheek  M=mouth
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_template(lines: list[str]) -> list[list[str]]:
    """Pad/truncate template lines to exactly 32×32."""
    grid = []
    for line in lines[:H]:
        row = list(line[:W])
        row += ['.'] * (W - len(row))
        grid.append(row)
    while len(grid) < H:
        grid.append(['.'] * W)
    return grid


# ── CAT TEMPLATES ────────────────────────────────────────────────────────────

CAT_SIT = _parse_template([
    # 0         1111111111222222222233
    # 0123456789012345678901234567890 1
    '................................',  # 0
    '................................',  # 1
    '...L..........................',   # 2
    '..LIL..............LIL.........',  # 3  pointy ear tips
    '..LIIL.............LIIL........',  # 4  ears
    '.LIIIBLLLLLLLLLLLLBIILL........',  # 5  ears + head top
    '.LBBBBBBBBBBBBBBBBBBBL.........',  # 6  head
    '.LBBBBBBBBBBBBBBBBBBBL.........',  # 7
    '.LBBEPHBBBBBBBBHPEBBL.........',   # 8  eyes (almond)
    '.LBBBPBBBBBBBBBPBBBL..........',   # 9
    'W.LBBBBBBNNBBBBBBBL..W.........',  # 10 whiskers + nose
    '.W.LBBBBBMBBBBBBBL.W...........',  # 11 whiskers + mouth
    '....LBBBBBBBBBBBL..............',  # 12 narrow chin
    '.....LBBBBBBBBL................',  # 13 thin neck
    '.....LBBBBBBBBL................',  # 14
    '....LBBBBBBBBBL................',  # 15 body starts
    '...LBBBBbbbbBBBBL..............',  # 16 body + belly
    '...LBBBBbbbbBBBBL..............',  # 17
    '...LBBBBbbbbBBBBL..............',  # 18
    '....LBBBBBBBBBL................',  # 19 body tapers
    '.....LBBBBBBBL.................',  # 20
    '....LsL....LsLBBL..............',  # 21 thin legs + tail starts
    '....LsL....LsL.LBBL...........',  # 22          tail
    '....LbL....LbL..LBBL..........',  # 23 paws     tail curves
    '....LLL....LLL...LBBL.........',  # 24          tail
    '..................LLLL..........',  # 25          tail tip
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

CAT_WALK_A = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '...L............................',  # 2
    '..LIL..............LIL.........',  # 3
    '..LIIL.............LIIL........',  # 4
    '.LIIIBLLLLLLLLLLLLBIILL........',  # 5
    '.LBBBBBBBBBBBBBBBBBBBL.........',  # 6
    '.LBBBBBBBBBBBBBBBBBBBL.........',  # 7
    '.LBBEPHBBBBBBBBHPEBBL.........',   # 8
    '.LBBBPBBBBBBBBBPBBBL..........',   # 9
    'W.LBBBBBBNNBBBBBBBL..W.........',  # 10
    '.W.LBBBBBMBBBBBBBL.W...........',  # 11
    '....LBBBBBBBBBBBL..............',  # 12
    '.....LBBBBBBBBL................',  # 13
    '....LBBBBBBBBBBL...............',  # 14
    '...LBBBBBBBBBBBBLl.............', # 15 body leans forward
    '...LBBBBbbbbBBBBBL.............',  # 16
    '...LBBBBbbbbBBBBL..............',  # 17
    '....LBBBBbbBBBBL...............',  # 18
    '.....LBBBBBBBBl................',  # 19
    '....LsL...LsL..................',  # 20 legs: left forward, right back
    '...LsL.....LsL.................',  # 21
    '...LbL......LbL................',  # 22
    '...LLL.......LLL...............',  # 23
    '................................',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

CAT_WALK_B = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '...L............................',  # 2
    '..LIL..............LIL.........',  # 3
    '..LIIL.............LIIL........',  # 4
    '.LIIIBLLLLLLLLLLLLBIILL........',  # 5
    '.LBBBBBBBBBBBBBBBBBBBL.........',  # 6
    '.LBBBBBBBBBBBBBBBBBBBL.........',  # 7
    '.LBBEPHBBBBBBBBHPEBBL.........',   # 8
    '.LBBBPBBBBBBBBBPBBBL..........',   # 9
    'W.LBBBBBBNNBBBBBBBL..W.........',  # 10
    '.W.LBBBBBMBBBBBBBL.W...........',  # 11
    '....LBBBBBBBBBBBL..............',  # 12
    '.....LBBBBBBBBL................',  # 13
    '....LBBBBBBBBBBL...............',  # 14
    '...LBBBBBBBBBBBBl..............',  # 15
    '...LBBBBbbbbBBBBL..............',  # 16
    '...LBBBBbbbbBBBBL..............',  # 17
    '....LBBBBbbBBBBL...............',  # 18
    '.....LBBBBBBBBl................',  # 19
    '.......LsL.LsL.................',  # 20 legs: right forward, left back
    '......LsL...LsL................',  # 21
    '.....LbL.....LbL...............',  # 22
    '.....LLL......LLL..............',  # 23
    '................................',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

CAT_SLEEP = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '................................',  # 3
    '................................',  # 4
    '................................',  # 5
    '................................',  # 6
    '................................',  # 7
    '................................',  # 8
    '................................',  # 9
    '................................',  # 10
    '...L............................',  # 11
    '..LIL.........LIL..............',  # 12 ears
    '..LIILLLLLLLLLLIIL..............',  # 13
    '.LBBBBBBBBBBBBBBBL..............',  # 14 head (eyes closed = lines)
    '.LBBBLLLBBBBLLLBBBL.............',  # 15 closed eyes (—)
    '.LBBBBBBNNBBBBBBBL..............',  # 16 nose
    '..LBBBBBBBBBBBBBL...............',  # 17 chin
    '...LLBBBBBBBBBBBBLL.............',  # 18 curled body starts
    '.....LBBBBBBBBBBBBbL...........',  # 19
    '....LBBBBbbbbBBBBbbBL..........',  # 20 belly
    '....LBBBBbbbbBBBBBBBBL.........',  # 21
    '....LBBBBbbbbBBBBBBBBBL........',  # 22
    '.....LBBBBBBBBBBBBBBBl.........',  # 23 tail wraps around
    '......LLLLLLLLLLLLLL...........',   # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

CAT_EAT = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '................................',  # 3
    '................................',  # 4
    '...L............................',  # 5
    '..LIL..............LIL.........',  # 6
    '..LIIL.............LIIL........',  # 7
    '.LIIIBLLLLLLLLLLLLBIILL........',  # 8
    '.LBBBBBBBBBBBBBBBBBBBL.........',  # 9
    '.LBBEPHBBBBBBBBHPEBBL.........',   # 10
    '.LBBBPBBBBBBBBBPBBBL..........',   # 11
    'W.LBBBBBBNNBBBBBBBL..W.........',  # 12
    '.W.LBBBBBMBBBBBBBL.W...........',  # 13
    '....LBBBBBBBBBBBL..............',  # 14  head down
    '....LBBBBBBBBBBL...............',  # 15  neck bends
    '...LBBBBBBBBBBBL...............',  # 16  body
    '...LBBBBbbbbBBBBL..............',  # 17
    '...LBBBBbbbbBBBBL..............',  # 18
    '...LBBBBbbbbBBBBL..............',  # 19
    '....LBBBBBBBBBL................',  # 20
    '....LsL....LsLBBL..............',  # 21 legs + tail
    '....LsL....LsL.LBBL...........',  # 22
    '....LbL....LbL..LBBL..........',  # 23
    '....LLL....LLL...LLLL.........',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

CAT_PLAY = _parse_template([
    '................................',  # 0
    '..L.............................',  # 1
    '.LIL.............LIL...........',  # 2
    '.LIIL............LIIL..........',  # 3
    'LIIIBLLLLLLLLLLLLBIILL..........',  # 4
    'LBBBBBBBBBBBBBBBBBBBL..........',  # 5
    'LBBBBBBBBBBBBBBBBBBBL..........',  # 6
    'LBBEPHBBBBBBBBHPEBBL...........',  # 7
    'LBBBPBBBBBBBBBPBBBL............',  # 8
    '.LBBBBBBNNBBBBBBBL.............',  # 9  whiskers
    '..LBBBBBMBBBBBBBL..............',  # 10
    '...LBBBBBBBBBBBL...............',  # 11
    '....LBBBBBBBBL.................',  # 12
    '...LBBBBBBBBBBBL...............',  # 13
    '..LBBBBBbbbbBBBBBL.............',  # 14 pouncing body
    '..LBBBBBbbbbBBBBBBL............',  # 15
    '..LBBBBBbbbbBBBBBL.............',  # 16
    '...LBBBBBBBBBBBBl..............',  # 17
    '..LsL.......LsL................',  # 18 front paws up
    '.LsL.........LsL...............',  # 19
    '.LbL..........LsL..............',  # 20
    '.LLL...........LLL.............',  # 21
    '................................',  # 22
    '................................',  # 23
    '................................',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])


# ── DOG TEMPLATES ────────────────────────────────────────────────────────────

DOG_SIT = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '.......LLLLLLLLLL..............',   # 2 dome head top
    '......LBBBBBBBBBL..............',   # 3
    '.....LBBBBBBBBBBBL.............',   # 4
    '....LBBBBBBBBBBBBBL............',   # 5
    'LL.LBBBBBBBBBBBBBBL.LL.........',   # 6 ears from sides
    'IL.LBBEPHBBBHPEBBL.LI.........',   # 7 eyes with whites + inner ear
    'IL.LBBBPBBBBBPBBBL.LI.........',   # 8
    'IL.LBBBBBBBBBBBBBl.LI.........',   # 9
    'LL..LBBBBBBBBBBBBL..LL.........',  # 10
    '.....LBBBBNNBBBBl..............',  # 11 wide muzzle + nose
    '.....LBBBBMMBBBBBL.............',  # 12 mouth
    '......LBBBBBBBBBL..............',  # 13 jaw
    '......LCCCCCCCCL...............',  # 14 collar
    '.....LCCCKCCCCCCL..............',  # 15 collar + tag
    '....LBBBBBBBBBBBBL.............',  # 16 thick neck
    '...LBBBBBBBBBBBBBBBL...........',  # 17 wide chest
    '..LBBBBBbbbbbbBBBBBL..........',  # 18 stocky body + belly
    '..LBBBBBbbbbbbBBBBBL..........',  # 19
    '..LBBBBBbbbbbbBBBBBL..........',  # 20
    '...LBBBBBBBBBBBBBBL............',  # 21
    '...LssBL.....LBssL............',  # 22 thick legs
    '...LssBL.....LBssL............',  # 23
    '...LbbBL.....LBbbL............',  # 24 paws
    '...LLLLL.....LLLLL.............',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

DOG_WALK_A = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '.......LLLLLLLLLL..............',   # 2
    '......LBBBBBBBBBL..............',   # 3
    '.....LBBBBBBBBBBBL.............',   # 4
    '....LBBBBBBBBBBBBBL............',   # 5
    'LL.LBBBBBBBBBBBBBBL.LL.........',   # 6
    'IL.LBBEPHBBBHPEBBL.LI.........',   # 7
    'IL.LBBBPBBBBBPBBBL.LI.........',   # 8
    'IL.LBBBBBBBBBBBBBl.LI.........',   # 9
    'LL..LBBBBBBBBBBBBL..LL.........',  # 10
    '.....LBBBBNNBBBBl..............',  # 11
    '.....LBBBBMMBBBBBL.............',  # 12
    '......LBBBBBBBBBL..............',  # 13
    '......LCCCCCCCCL...............',  # 14
    '.....LCCCKCCCCCCL..............',  # 15
    '....LBBBBBBBBBBBBBL............',  # 16
    '...LBBBBBbbbbbbBBBBL..........',  # 17
    '..LBBBBBBbbbbbbBBBBBL.........',  # 18
    '..LBBBBBBbbbbbbBBBBBL.........',  # 19
    '...LBBBBBBBBBBBBBBBl..........',  # 20
    '..LssBL......LBssL............',  # 21 left leg forward
    '.LssBL........LBssL...........',  # 22
    '.LbbBL.........LBbbL..........',  # 23
    '.LLLLL..........LLLLL..........',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

DOG_WALK_B = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '.......LLLLLLLLLL..............',   # 2
    '......LBBBBBBBBBL..............',   # 3
    '.....LBBBBBBBBBBBL.............',   # 4
    '....LBBBBBBBBBBBBBL............',   # 5
    'LL.LBBBBBBBBBBBBBBL.LL.........',   # 6
    'IL.LBBEPHBBBHPEBBL.LI.........',   # 7
    'IL.LBBBPBBBBBPBBBL.LI.........',   # 8
    'IL.LBBBBBBBBBBBBBl.LI.........',   # 9
    'LL..LBBBBBBBBBBBBL..LL.........',  # 10
    '.....LBBBBNNBBBBl..............',  # 11
    '.....LBBBBMMBBBBBL.............',  # 12
    '......LBBBBBBBBBL..............',  # 13
    '......LCCCCCCCCL...............',  # 14
    '.....LCCCKCCCCCCL..............',  # 15
    '....LBBBBBBBBBBBBBL............',  # 16
    '...LBBBBBbbbbbbBBBBL..........',  # 17
    '..LBBBBBBbbbbbbBBBBBL.........',  # 18
    '..LBBBBBBbbbbbbBBBBBL.........',  # 19
    '...LBBBBBBBBBBBBBBBl..........',  # 20
    '.....LBssL..LssBL.............', # 21 right leg forward
    '......LBssL.LssBL.............',  # 22
    '.......LBbbLLbbBL..............',  # 23
    '.......LLLLL.LLLLL.............',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

DOG_SLEEP = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '................................',  # 3
    '................................',  # 4
    '................................',  # 5
    '................................',  # 6
    '................................',  # 7
    '................................',  # 8
    '................................',  # 9
    '......LLLLLLLLLL...............',   # 10 dome head
    '.....LBBBBBBBBBL...............',   # 11
    'LL..LBBBBBBBBBBBL..............',   # 12
    'IL.LBBBLLLBBLLLBBBL............',   # 13 closed eyes + inner ear
    'IL.LBBBBBBNNBBBBBL.............',   # 14
    'LL..LBBBBBMBBBBBL..............',   # 15
    '.....LCCCCCCCCL................',   # 16 collar
    '....LLBBBBBBBBBBLL.............',   # 17
    '...LBBBBBBBBBBBBBBbL..........',  # 18 body
    '..LBBBBBbbbbbbBBBBbbBL.........',  # 19
    '..LBBBBBbbbbbbBBBBBBBBL........',  # 20
    '..LBBBBBbbbbbbBBBBBBBBBL.......',  # 21
    '...LBBBBBBBBBBBBBBBBBBl........',  # 22
    '....LLLLLLLLLLLLLLLLL..........',  # 23
    '................................',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

DOG_EAT = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '................................',  # 3
    '................................',  # 4
    '.......LLLLLLLLLL..............',   # 5
    '......LBBBBBBBBBL..............',   # 6
    '.....LBBBBBBBBBBBL.............',   # 7
    'LL.LBBBBBBBBBBBBBBL.LL.........',   # 8
    'IL.LBBEPHBBBHPEBBL.LI.........',   # 9
    'IL.LBBBPBBBBBPBBBL.LI.........',   # 10
    'LL..LBBBBBBBBBBBBL..LL.........',  # 11
    '.....LBBBBNNBBBBl..............',  # 12
    '.....LBBBBMMBBBBBL.............',  # 13 head low
    '......LBBBBBBBBBL..............',  # 14
    '......LCCCCCCCCL...............',  # 15 collar
    '....LBBBBBBBBBBBBBL............',  # 16
    '...LBBBBBbbbbbbBBBBL..........',  # 17 body
    '..LBBBBBBbbbbbbBBBBBL.........',  # 18
    '..LBBBBBBbbbbbbBBBBBL.........',  # 19
    '...LBBBBBBBBBBBBBBBl..........',  # 20
    '...LssBL.....LBssL............',  # 21
    '...LssBL.....LBssL............',  # 22
    '...LbbBL.....LBbbL............',  # 23
    '...LLLLL.....LLLLL.............',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

DOG_PLAY = _parse_template([
    '................................',  # 0
    '......LLLLLLLLLL...............',   # 1
    '.....LBBBBBBBBBL...............',   # 2
    '....LBBBBBBBBBBBL..............',   # 3
    'LL.LBBBBBBBBBBBBBBL.LL.........',   # 4
    'IL.LBBEPHBBBHPEBBL.LI.........',   # 5
    'IL.LBBBPBBBBBPBBBL.LI.........',   # 6
    'LL..LBBBBBBBBBBBBL..LL.........',   # 7
    '.....LBBBBNNBBBBl..............',   # 8
    '.....LBBBBMMBBBBBL.............',   # 9 happy mouth
    '......LBBBBBBBBBL..............',  # 10
    '......LCCCCCCCCL...............',  # 11 collar
    '.....LBBBBBBBBBBBL.............',  # 12
    '....LBBBBBBBBBBBBBL............',  # 13 jumping body
    '...LBBBBBbbbbbbBBBBBL.........',  # 14
    '..LBBBBBBbbbbbbBBBBBBL........',  # 15
    '..LBBBBBBbbbbbbBBBBBBL........',  # 16
    '...LBBBBBBBBBBBBBBBBL..........',  # 17
    '..LsL................LsL.......',  # 18 legs splayed (jumping)
    '.LsL..................LsL......',  # 19
    '.LbL..................LbL......',  # 20
    '.LLL..................LLL......',  # 21
    '................................',  # 22
    '................................',  # 23
    '................................',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])


# ── HAMSTER TEMPLATES ────────────────────────────────────────────────────────

HAM_SIT = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '........LBL....LBL.............',  # 3 tiny ears
    '........LIBL..LBIL.............',  # 4 inner ear
    '.......LLBBLLBBLL...............',  # 5
    '......LBBBBBBBBBL..............',   # 6 round head
    '.....LBBBBBBBBBBBL.............',   # 7
    '.....LBBEPHBBHPEBBL............',  # 8 beady eyes
    '.....LBBBPBBBBPBBBL............',  # 9
    '....LBBBBBNNBBBBBBL............',  # 10 tiny nose
    'QQQLBBBBBBMMBBBBBBBLQQQ........',  # 11 cheeks bulge!
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 12 max cheek width
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 13
    '..LBBBBBBBBBBBBBBBBBL..........',  # 14
    '..LBBBBBbbbbbbBBBBBBL..........',  # 15 round belly
    '..LBBBBBbbbbbbBBBBBBL..........',  # 16
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 17 widest body
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 18
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 19
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 20
    '..LBBBBBBBBBBBBBBBBBL..........',  # 21
    '..LBBBBBBBBBBBBBBBBBL..........',  # 22
    '...LBBBBBBBBBBBBBBBL...........',  # 23
    '....LsL........LsL............',  # 24 tiny legs barely visible
    '....LbL........LbL............',  # 25
    '....LLL........LLL.............',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

HAM_WALK_A = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '........LBL....LBL.............',  # 3
    '........LIBL..LBIL.............',  # 4
    '.......LLBBLLBBLL...............',  # 5
    '......LBBBBBBBBBL..............',   # 6
    '.....LBBBBBBBBBBBL.............',   # 7
    '.....LBBEPHBBHPEBBL............',  # 8
    '.....LBBBPBBBBPBBBL............',  # 9
    '....LBBBBBNNBBBBBBL............',  # 10
    'QQQLBBBBBBMMBBBBBBBLQQQ........',  # 11
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 12
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 13
    '..LBBBBBBBBBBBBBBBBBl..........',  # 14
    '..LBBBBBbbbbbbBBBBBBL..........',  # 15
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 16
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 17
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 18
    '..LBBBBBBBBBBBBBBBBBL..........',  # 19
    '..LBBBBBBBBBBBBBBBBBL..........',  # 20
    '...LBBBBBBBBBBBBBBBL...........',  # 21
    '...LsL..........LsL...........',  # 22 left forward
    '..LbL............LbL...........',  # 23
    '..LLL.............LLL..........',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

HAM_WALK_B = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '........LBL....LBL.............',  # 3
    '........LIBL..LBIL.............',  # 4
    '.......LLBBLLBBLL...............',  # 5
    '......LBBBBBBBBBL..............',   # 6
    '.....LBBBBBBBBBBBL.............',   # 7
    '.....LBBEPHBBHPEBBL............',  # 8
    '.....LBBBPBBBBPBBBL............',  # 9
    '....LBBBBBNNBBBBBBL............',  # 10
    'QQQLBBBBBBMMBBBBBBBLQQQ........',  # 11
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 12
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 13
    '..LBBBBBBBBBBBBBBBBBl..........',  # 14
    '..LBBBBBbbbbbbBBBBBBL..........',  # 15
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 16
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 17
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 18
    '..LBBBBBBBBBBBBBBBBBL..........',  # 19
    '..LBBBBBBBBBBBBBBBBBL..........',  # 20
    '...LBBBBBBBBBBBBBBBL...........',  # 21
    '.....LsL......LsL.............',  # 22 right forward
    '......LbL....LbL...............',  # 23
    '.......LLL...LLL...............',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

HAM_SLEEP = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '................................',  # 3
    '................................',  # 4
    '................................',  # 5
    '................................',  # 6
    '................................',  # 7
    '................................',  # 8
    '................................',  # 9
    '........LBL....LBL.............',  # 10 ears
    '........LIBL..LBIL.............',  # 11
    '.......LLBBLLBBLL...............',  # 12
    '......LBBBBBBBBBL..............',   # 13
    '.....LBBBBBBBBBBBL.............',   # 14
    '.....LBBBLLLBBLLLBBL...........',  # 15 closed eyes
    '....LBBBBBNNBBBBBBL............',  # 16
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 17 cheeks
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 18
    '..LBBBBBBbbbbbbBBBBBBL.........',  # 19
    '.LBBBBBBBbbbbbbBBBBBBBL........',  # 20
    '.LBBBBBBBbbbbbbBBBBBBBL........',  # 21
    '.LBBBBBBBbbbbbbBBBBBBBL........',  # 22
    '..LBBBBBBBBBBBBBBBBBBL.........',  # 23
    '...LLLLLLLLLLLLLLLLLL..........',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

HAM_EAT = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '................................',  # 2
    '................................',  # 3
    '................................',  # 4
    '........LBL....LBL.............',  # 5 ears
    '........LIBL..LBIL.............',  # 6
    '.......LLBBLLBBLL...............',  # 7
    '......LBBBBBBBBBL..............',   # 8
    '.....LBBBBBBBBBBBL.............',   # 9
    '.....LBBEPHBBHPEBBL............',  # 10 eyes
    '.....LBBBPBBBBPBBBL............',  # 11
    '....LBBBBBNNBBBBBBL............',  # 12
    'QQQLBBBBBBMMBBBBBBBLQQQ........',  # 13 cheeks (stuffed when eating)
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 14
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 15
    '..LBBBBBBBBBBBBBBBBBL..........',  # 16
    '..LBBBBBbbbbbbBBBBBBL..........',  # 17
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 18
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 19
    '.LBBBBBBbbbbbbBBBBBBBL.........',  # 20
    '..LBBBBBBBBBBBBBBBBBL..........',  # 21
    '...LBBBBBBBBBBBBBBBL...........',  # 22
    '....LsL........LsL............',  # 23
    '....LbL........LbL............',  # 24
    '....LLL........LLL.............',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])

HAM_PLAY = _parse_template([
    '................................',  # 0
    '................................',  # 1
    '........LBL....LBL.............',  # 2 ears
    '........LIBL..LBIL.............',  # 3
    '.......LLBBLLBBLL...............',  # 4
    '......LBBBBBBBBBL..............',   # 5
    '.....LBBBBBBBBBBBL.............',   # 6
    '.....LBBEPHBBHPEBBL............',  # 7
    '.....LBBBPBBBBPBBBL............',  # 8
    '....LBBBBBNNBBBBBBL............',  # 9
    'QQQLBBBBBBMMBBBBBBBLQQQ........',  # 10 cheeks
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 11
    'QQQLBBBBBBBBBBBBBBBLQQQ........',  # 12
    '..LBBBBBBBBBBBBBBBBBl..........',  # 13
    '..LBBBBBbbbbbbBBBBBBL..........',  # 14 on hind legs
    '..LBBBBBbbbbbbBBBBBBL..........',  # 15
    '..LBBBBBbbbbbbBBBBBBL..........',  # 16
    '...LBBBBBBBBBBBBBBBL...........',  # 17
    '...LBBBBBBBBBBBBBBBL...........',  # 18
    '....LBBBBBBBBBBBBBL............',  # 19
    '....LsL........LsL............',  # 20
    '....LsL........LsL............',  # 21 taller legs (standing up)
    '....LbL........LbL............',  # 22
    '....LLL........LLL.............',  # 23
    '................................',  # 24
    '................................',  # 25
    '................................',  # 26
    '................................',  # 27
    '................................',  # 28
    '................................',  # 29
    '................................',  # 30
    '................................',  # 31
])


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

TEMPLATES = {
    'cat': {
        'sit': CAT_SIT, 'walk_a': CAT_WALK_A, 'walk_b': CAT_WALK_B,
        'sleep': CAT_SLEEP, 'eat': CAT_EAT, 'play': CAT_PLAY,
    },
    'dog': {
        'sit': DOG_SIT, 'walk_a': DOG_WALK_A, 'walk_b': DOG_WALK_B,
        'sleep': DOG_SLEEP, 'eat': DOG_EAT, 'play': DOG_PLAY,
    },
    'hamster': {
        'sit': HAM_SIT, 'walk_a': HAM_WALK_A, 'walk_b': HAM_WALK_B,
        'sleep': HAM_SLEEP, 'eat': HAM_EAT, 'play': HAM_PLAY,
    },
}

POSES = ['sit', 'walk_a', 'walk_b', 'sleep', 'eat', 'play']


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER EXTRACTION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_image() -> Image.Image:
    return Image.new('RGBA', (W, H), TRANSPARENT)


def _char_to_body_pixel(ch: str) -> tuple | None:
    """Return body-layer color for given template char, or None if not body."""
    if ch == 'B':
        return BODY_LIGHT
    if ch == 'b':
        return BODY_BELLY
    if ch == 's':
        return BODY_SHADOW
    return None


def extract_body(grid: list[list[str]]) -> Image.Image:
    """Body silhouette in grayscale — B/b/s chars."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            c = _char_to_body_pixel(ch)
            if c:
                img.putpixel((x, y), c)
    return img


def extract_lineart(grid: list[list[str]]) -> Image.Image:
    """Outline pixels — L chars."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'L':
                img.putpixel((x, y), BLACK)
    return img


def extract_eyes(grid: list[list[str]]) -> Image.Image:
    """Eye layer — E=fill, H=shine, P=pupil."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'E':
                img.putpixel((x, y), EYE_FILL)
            elif ch == 'H':
                img.putpixel((x, y), EYE_SHINE)
            elif ch == 'P':
                img.putpixel((x, y), (40, 40, 42, 255))  # pupil dark
    return img


def extract_collar(grid: list[list[str]]) -> Image.Image:
    """Collar layer (dog only) — C=collar, K=tag."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'C':
                img.putpixel((x, y), COLLAR_GRAY)
            elif ch == 'K':
                img.putpixel((x, y), COLLAR_TAG)
    return img


def extract_cheeks(grid: list[list[str]], stuffed: bool = False) -> Image.Image:
    """Cheek layer (hamster only) — Q chars. Stuffed = slightly larger."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'Q':
                img.putpixel((x, y), CHEEK_FILL)
    if stuffed:
        # Expand cheek pixels by 1px outward on left/right edges
        pixels = img.load()
        extra = []
        for y in range(H):
            for x in range(W):
                if pixels[x, y][3] > 0:
                    for dx in [-1, 1]:
                        nx = x + dx
                        if 0 <= nx < W and pixels[nx, y][3] == 0:
                            extra.append((nx, y))
        for (ex, ey) in extra:
            img.putpixel((ex, ey), CHEEK_FILL)
    return img


def extract_inner_ears(grid: list[list[str]]) -> Image.Image:
    """Inner ear detail — I chars."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I':
                img.putpixel((x, y), EAR_INNER)
    return img


def _get_body_mask(grid: list[list[str]]) -> set[tuple[int, int]]:
    """Get set of (x, y) coordinates that are body pixels (B, b, s)."""
    mask = set()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch in ('B', 'b', 's', 'E', 'H', 'P', 'N', 'M', 'I', 'C', 'K', 'Q', 'W'):
                mask.add((x, y))
    return mask


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _body_bounds(grid: list[list[str]]) -> tuple[int, int, int, int]:
    """Return (min_x, min_y, max_x, max_y) of body area."""
    mask = _get_body_mask(grid)
    if not mask:
        return (0, 0, W, H)
    xs = [p[0] for p in mask]
    ys = [p[1] for p in mask]
    return (min(xs), min(ys), max(xs), max(ys))


def _head_bounds(grid: list[list[str]]) -> tuple[int, int, int, int]:
    """Approximate head region — top 40% of body."""
    bx0, by0, bx1, by1 = _body_bounds(grid)
    head_h = int((by1 - by0) * 0.4)
    return (bx0, by0, bx1, by0 + head_h)


def _body_lower_bounds(grid: list[list[str]]) -> tuple[int, int, int, int]:
    """Lower 60% of body — for saddle/back patterns."""
    bx0, by0, bx1, by1 = _body_bounds(grid)
    head_h = int((by1 - by0) * 0.4)
    return (bx0, by0 + head_h, bx1, by1)


def gen_pattern_tabby(grid: list[list[str]]) -> Image.Image:
    """Horizontal stripes across body."""
    img = _make_image()
    mask = _get_body_mask(grid)
    bx0, by0, bx1, by1 = _body_bounds(grid)
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask and (y - by0) % 4 in (0, 1):
                img.putpixel((x, y), PATTERN_DARK)
    return img


def gen_pattern_spotted(grid: list[list[str]], species: str = 'cat') -> Image.Image:
    """Random-ish spots/dots on body."""
    img = _make_image()
    mask = _get_body_mask(grid)
    bx0, by0, bx1, by1 = _body_bounds(grid)
    # Deterministic spots based on position
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask:
                # Hash-based deterministic "random"
                h = ((x * 7 + y * 13 + 37) * 31) % 100
                if h < 15:  # ~15% coverage
                    img.putpixel((x, y), PATTERN_DARK)
                    # Add adjacent pixels for larger spots
                    for dx, dy in [(1, 0), (0, 1)]:
                        nx, ny = x + dx, y + dy
                        if (nx, ny) in mask:
                            img.putpixel((nx, ny), PATTERN_DARK)
    return img


def gen_pattern_bicolor(grid: list[list[str]]) -> Image.Image:
    """Half the body in a different shade — left/right split."""
    img = _make_image()
    mask = _get_body_mask(grid)
    bx0, by0, bx1, by1 = _body_bounds(grid)
    mid_x = (bx0 + bx1) // 2
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask and x >= mid_x:
                img.putpixel((x, y), PATTERN_MED)
    return img


def gen_pattern_calico(grid: list[list[str]]) -> Image.Image:
    """Irregular patches — orange-ish + dark areas."""
    img = _make_image()
    mask = _get_body_mask(grid)
    orange_patch = (160, 120, 80, 180)
    dark_patch = (70, 65, 60, 180)
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask:
                h = ((x * 11 + y * 23 + 7) * 17) % 100
                if h < 20:
                    img.putpixel((x, y), orange_patch)
                elif h < 35:
                    img.putpixel((x, y), dark_patch)
    return img


def gen_pattern_pointed(grid: list[list[str]]) -> Image.Image:
    """Dark extremities — ears, face mask, paws, tail tip (Siamese)."""
    img = _make_image()
    mask = _get_body_mask(grid)
    bx0, by0, bx1, by1 = _body_bounds(grid)
    body_h = by1 - by0
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask:
                rel_y = (y - by0) / max(body_h, 1)
                # Dark on top 25% (ears/face) and bottom 15% (paws)
                if rel_y < 0.25 or rel_y > 0.85:
                    img.putpixel((x, y), PATTERN_DARK)
    # Also darken inner ear pixels
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I':
                img.putpixel((x, y), PATTERN_DARK)
    return img


def gen_pattern_masked(grid: list[list[str]]) -> Image.Image:
    """Dark face/muzzle area (dog)."""
    img = _make_image()
    mask = _get_body_mask(grid)
    hx0, hy0, hx1, hy1 = _head_bounds(grid)
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask and hy0 <= y <= hy1:
                img.putpixel((x, y), PATTERN_DARK)
    return img


def gen_pattern_saddle(grid: list[list[str]]) -> Image.Image:
    """Dark back/saddle area (dog) — upper body, not belly."""
    img = _make_image()
    mask = _get_body_mask(grid)
    bx0, by0, bx1, by1 = _body_lower_bounds(grid)
    mid_x = (bx0 + bx1) // 2
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask and by0 <= y <= by1:
                # Only the back (not belly = 'b')
                if grid[y][x] != 'b':
                    img.putpixel((x, y), PATTERN_DARK)
    return img


def gen_pattern_merle(grid: list[list[str]]) -> Image.Image:
    """Irregular mottled patches (dog)."""
    img = _make_image()
    mask = _get_body_mask(grid)
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask:
                h = ((x * 13 + y * 7 + 41) * 23) % 100
                if h < 25:
                    img.putpixel((x, y), PATTERN_MED)
                elif h < 35:
                    img.putpixel((x, y), PATTERN_DARK)
    return img


def gen_pattern_striped(grid: list[list[str]]) -> Image.Image:
    """Dorsal stripe down the back (hamster)."""
    img = _make_image()
    mask = _get_body_mask(grid)
    bx0, by0, bx1, by1 = _body_bounds(grid)
    mid_x = (bx0 + bx1) // 2
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask and abs(x - mid_x) <= 1:
                img.putpixel((x, y), PATTERN_DARK)
    return img


def gen_pattern_panda(grid: list[list[str]]) -> Image.Image:
    """Dark eye patches and ear patches (hamster)."""
    img = _make_image()
    # Darken around eyes and ears
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch in ('E', 'P', 'H', 'I'):
                # Eye/ear region — paint surrounding body pixels dark
                for dx in range(-1, 2):
                    for dy in range(-1, 2):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < W and 0 <= ny < H:
                            gch = grid[ny][nx]
                            if gch in ('B', 'b', 's', 'E', 'P', 'I'):
                                img.putpixel((nx, ny), PATTERN_DARK)
    return img


def gen_pattern_patched(grid: list[list[str]]) -> Image.Image:
    """Irregular darker patches (hamster)."""
    img = _make_image()
    mask = _get_body_mask(grid)
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if (x, y) in mask:
                h = ((x * 5 + y * 19 + 13) * 29) % 100
                if h < 20:
                    img.putpixel((x, y), PATTERN_MED)
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# EAR VARIANT GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _find_ear_pixels(grid: list[list[str]]) -> list[tuple[int, int, str]]:
    """Find all ear-related pixels (I = inner ear, and adjacent L/B)."""
    ear_pixels = []
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I':
                ear_pixels.append((x, y, ch))
    return ear_pixels


def _get_ear_region(grid: list[list[str]]) -> tuple[int, int, int, int]:
    """Get bounding box of ear region (top of head)."""
    bx0, by0, bx1, by1 = _body_bounds(grid)
    # Ears are in the top 3 rows of body area
    return (bx0, max(0, by0 - 2), bx1, by0 + 4)


def gen_ears_cat_pointy(grid: list[list[str]]) -> Image.Image:
    """Default cat pointy ears — tall triangles. Use inner ear pixels as-is."""
    return extract_inner_ears(grid)


def gen_ears_cat_round(grid: list[list[str]]) -> Image.Image:
    """Shorter, rounded cat ears — shift inner ear pixels down 1px."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I' and y + 1 < H:
                img.putpixel((x, y + 1), EAR_INNER)
    return img


def gen_ears_cat_fold(grid: list[list[str]]) -> Image.Image:
    """Scottish fold ears — inner ear pixels shifted down and inward."""
    img = _make_image()
    bx0, by0, bx1, by1 = _body_bounds(grid)
    mid_x = (bx0 + bx1) // 2
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I':
                # Fold: shift down 2, toward center
                ny = y + 2
                nx = x + (1 if x < mid_x else -1)
                if 0 <= nx < W and 0 <= ny < H:
                    img.putpixel((nx, ny), EAR_INNER)
    return img


def gen_ears_dog_floppy(grid: list[list[str]]) -> Image.Image:
    """Floppy ears hanging from sides of head."""
    img = _make_image()
    # Find side-ear pixels (the ones on template edges, marked as B inside LL blocks)
    # For dog templates, the ears are the LL..LB..BL..LL pattern on sides
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I':
                img.putpixel((x, y), EAR_INNER)
    return img


def gen_ears_dog_pointy(grid: list[list[str]]) -> Image.Image:
    """Pointy erect dog ears — draw triangles above head."""
    img = _make_image()
    bx0, by0, bx1, by1 = _body_bounds(grid)
    # Draw small pointy ears above the head line
    left_x = bx0 + 3
    right_x = bx1 - 3
    for dy in range(3):
        for dx in range(-dy, dy + 1):
            for ex in [left_x, right_x]:
                px, py = ex + dx, by0 - 3 + dy
                if 0 <= px < W and 0 <= py < H:
                    if dx == -dy or dx == dy or dy == 2:
                        pass  # outline handled by lineart
                    else:
                        img.putpixel((px, py), EAR_INNER)
    return img


def gen_ears_dog_half(grid: list[list[str]]) -> Image.Image:
    """Half-folded dog ears."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I':
                # Shift inner ear slightly
                img.putpixel((x, y), EAR_INNER)
    # Add a fold mark pixel
    bx0, by0, bx1, by1 = _body_bounds(grid)
    return img


def gen_ears_ham_round(grid: list[list[str]]) -> Image.Image:
    """Small round hamster ears."""
    return extract_inner_ears(grid)


def gen_ears_ham_pointed(grid: list[list[str]]) -> Image.Image:
    """Slightly pointed hamster ears — extend 1px up."""
    img = extract_inner_ears(grid)
    # Find topmost ear pixels and add 1px above
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'I' and y > 0:
                # Check if this is top of ear
                above = grid[y - 1][x] if y > 0 else '.'
                if above != 'I':
                    img.putpixel((x, y - 1), EAR_INNER)
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# TAIL VARIANT GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _find_tail_pixels(grid: list[list[str]]) -> list[tuple[int, int]]:
    """Find pixels marked as T in template (not used in current templates;
    tail is drawn programmatically based on body bounds)."""
    # In our templates, the tail for cat is part of the B pixels in lower-right
    # We'll generate tails programmatically relative to body
    return []


def gen_tail_cat_long(grid: list[list[str]]) -> Image.Image:
    """Long straight cat tail extending right."""
    img = _make_image()
    mask = _get_body_mask(grid)
    bx0, by0, bx1, by1 = _body_bounds(grid)
    # Tail starts from right side of body, lower area
    tx = bx1 - 2
    ty = by1 - 5
    for i in range(7):
        px, py = tx + i, ty
        if 0 <= px < W and 0 <= py < H:
            img.putpixel((px, py), BODY_LIGHT)
        px2, py2 = tx + i, ty + 1
        if 0 <= px2 < W and 0 <= py2 < H:
            img.putpixel((px2, py2), BODY_LIGHT)
    return img


def gen_tail_cat_curled(grid: list[list[str]]) -> Image.Image:
    """Curled up cat tail."""
    img = _make_image()
    bx0, by0, bx1, by1 = _body_bounds(grid)
    tx = bx1 - 2
    ty = by1 - 5
    # Curve upward
    points = [(0, 0), (1, 0), (2, 0), (3, -1), (4, -2), (4, -3), (3, -4)]
    for dx, dy in points:
        px, py = tx + dx, ty + dy
        if 0 <= px < W and 0 <= py < H:
            img.putpixel((px, py), BODY_LIGHT)
        px2, py2 = tx + dx, ty + dy + 1
        if 0 <= px2 < W and 0 <= py2 < H:
            img.putpixel((px2, py2), BODY_LIGHT)
    return img


def gen_tail_cat_fluffy(grid: list[list[str]]) -> Image.Image:
    """Thick/puffy cat tail."""
    img = _make_image()
    bx0, by0, bx1, by1 = _body_bounds(grid)
    tx = bx1 - 2
    ty = by1 - 6
    for i in range(6):
        for j in range(-1, 2):  # 3px thick
            px, py = tx + i, ty + j
            if 0 <= px < W and 0 <= py < H:
                img.putpixel((px, py), BODY_LIGHT)
    # Fluffy tip — extra pixels
    for j in range(-2, 3):
        px, py = tx + 5, ty + j
        if 0 <= px < W and 0 <= py < H:
            img.putpixel((px, py), BODY_LIGHT)
    return img


def gen_tail_dog_up(grid: list[list[str]]) -> Image.Image:
    """Dog tail pointing up (happy)."""
    img = _make_image()
    bx0, by0, bx1, by1 = _body_bounds(grid)
    tx = bx1 - 1
    ty = by1 - 8
    for i in range(5):
        px, py = tx, ty - i
        if 0 <= px < W and 0 <= py < H:
            img.putpixel((px, py), BODY_LIGHT)
        px2 = tx + 1
        if 0 <= px2 < W:
            img.putpixel((px2, py), BODY_LIGHT)
    return img


def gen_tail_dog_down(grid: list[list[str]]) -> Image.Image:
    """Dog tail pointing down."""
    img = _make_image()
    bx0, by0, bx1, by1 = _body_bounds(grid)
    tx = bx1 - 1
    ty = by1 - 4
    for i in range(4):
        px, py = tx, ty + i
        if 0 <= px < W and 0 <= py < H:
            img.putpixel((px, py), BODY_LIGHT)
        px2 = tx + 1
        if 0 <= px2 < W:
            img.putpixel((px2, py), BODY_LIGHT)
    return img


def gen_tail_dog_curled(grid: list[list[str]]) -> Image.Image:
    """Dog tail curled over back."""
    img = _make_image()
    bx0, by0, bx1, by1 = _body_bounds(grid)
    tx = bx1 - 1
    ty = by1 - 6
    points = [(0, 0), (0, -1), (0, -2), (-1, -3), (-2, -3), (-3, -2)]
    for dx, dy in points:
        px, py = tx + dx, ty + dy
        if 0 <= px < W and 0 <= py < H:
            img.putpixel((px, py), BODY_LIGHT)
        px2, py2 = tx + dx + 1, ty + dy
        if 0 <= px2 < W and 0 <= py2 < H:
            img.putpixel((px2, py2), BODY_LIGHT)
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# WHISKER / NOSE EXTRACTION (drawn as part of lineart)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_face_details(grid: list[list[str]]) -> Image.Image:
    """Nose (N), mouth (M), whisker (W) pixels — added to lineart."""
    img = _make_image()
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == 'N':
                img.putpixel((x, y), (60, 40, 40, 255))   # dark nose
            elif ch == 'M':
                img.putpixel((x, y), (60, 40, 40, 200))   # mouth line
            elif ch == 'W':
                img.putpixel((x, y), (100, 100, 100, 180)) # whisker
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE LINEART (outline + face details)
# ═══════════════════════════════════════════════════════════════════════════════

def make_lineart(grid: list[list[str]]) -> Image.Image:
    """Combine outline (L) with nose/mouth/whisker details."""
    outline = extract_lineart(grid)
    face = extract_face_details(grid)
    outline.paste(face, mask=face)
    return outline


# ═══════════════════════════════════════════════════════════════════════════════
# FILE GENERATION DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

def generate_species_pose(species: str, pose: str, grid: list[list[str]], out_dir: Path):
    """Generate all layer PNGs for a given species+pose."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Common layers ──
    extract_body(grid).save(out_dir / 'body.png')
    make_lineart(grid).save(out_dir / 'lineart.png')
    extract_eyes(grid).save(out_dir / 'eyes.png')

    # ── Species-specific layers ──
    if species == 'cat':
        gen_pattern_tabby(grid).save(out_dir / 'pattern_tabby.png')
        gen_pattern_spotted(grid).save(out_dir / 'pattern_spotted.png')
        gen_pattern_bicolor(grid).save(out_dir / 'pattern_bicolor.png')
        gen_pattern_calico(grid).save(out_dir / 'pattern_calico.png')
        gen_pattern_pointed(grid).save(out_dir / 'pattern_pointed.png')

        gen_ears_cat_pointy(grid).save(out_dir / 'ears_pointy.png')
        gen_ears_cat_round(grid).save(out_dir / 'ears_round.png')
        gen_ears_cat_fold(grid).save(out_dir / 'ears_fold.png')

        gen_tail_cat_long(grid).save(out_dir / 'tail_long.png')
        gen_tail_cat_curled(grid).save(out_dir / 'tail_curled.png')
        gen_tail_cat_fluffy(grid).save(out_dir / 'tail_fluffy.png')

    elif species == 'dog':
        gen_pattern_spotted(grid, species='dog').save(out_dir / 'pattern_spotted.png')
        gen_pattern_masked(grid).save(out_dir / 'pattern_masked.png')
        gen_pattern_saddle(grid).save(out_dir / 'pattern_saddle.png')
        gen_pattern_merle(grid).save(out_dir / 'pattern_merle.png')

        gen_ears_dog_floppy(grid).save(out_dir / 'ears_floppy.png')
        gen_ears_dog_pointy(grid).save(out_dir / 'ears_pointy.png')
        gen_ears_dog_half(grid).save(out_dir / 'ears_half.png')

        gen_tail_dog_up(grid).save(out_dir / 'tail_up.png')
        gen_tail_dog_down(grid).save(out_dir / 'tail_down.png')
        gen_tail_dog_curled(grid).save(out_dir / 'tail_curled.png')

        extract_collar(grid).save(out_dir / 'collar.png')

    elif species == 'hamster':
        gen_pattern_striped(grid).save(out_dir / 'pattern_striped.png')
        gen_pattern_panda(grid).save(out_dir / 'pattern_panda.png')
        gen_pattern_patched(grid).save(out_dir / 'pattern_patched.png')

        extract_cheeks(grid, stuffed=False).save(out_dir / 'cheeks_normal.png')
        extract_cheeks(grid, stuffed=True).save(out_dir / 'cheeks_stuffed.png')

        gen_ears_ham_round(grid).save(out_dir / 'ears_round.png')
        gen_ears_ham_pointed(grid).save(out_dir / 'ears_pointed.png')


def count_expected_files() -> int:
    """Count how many PNGs we expect to generate."""
    # cat: body, lineart, eyes, 5 patterns, 3 ears, 3 tails = 14 per pose × 6 poses = 84
    # dog: body, lineart, eyes, 4 patterns, 3 ears, 3 tails, collar = 15 per pose × 6 = 90
    # hamster: body, lineart, eyes, 3 patterns, 2 cheeks, 2 ears = 13 per pose × 6 = 78
    # Total: 84 + 90 + 78 = 252
    cat_per_pose = 3 + 5 + 3 + 3  # common(3) + patterns(5) + ears(3) + tails(3) = 14
    dog_per_pose = 3 + 4 + 3 + 3 + 1  # common(3) + patterns(4) + ears(3) + tails(3) + collar(1) = 14... wait
    ham_per_pose = 3 + 3 + 2 + 2  # common(3) + patterns(3) + cheeks(2) + ears(2) = 10

    # Recount:
    # cat: body(1) + lineart(1) + eyes(1) + pattern×5 + ears×3 + tail×3 = 14
    # dog: body(1) + lineart(1) + eyes(1) + pattern×4 + ears×3 + tail×3 + collar(1) = 14
    # ham: body(1) + lineart(1) + eyes(1) + pattern×3 + cheeks×2 + ears×2 = 10
    return (14 + 14 + 10) * 6  # 228


def main():
    print(f"Generating pet sprite layers to {OUT_ROOT}")
    total = 0

    for species in ('cat', 'dog', 'hamster'):
        for pose in POSES:
            grid = TEMPLATES[species][pose]
            out_dir = OUT_ROOT / species / pose
            generate_species_pose(species, pose, grid, out_dir)
            n_files = len(list(out_dir.glob('*.png')))
            total += n_files
            print(f"  {species}/{pose}: {n_files} PNGs")

    print(f"\nTotal: {total} PNG files generated")
    print(f"Expected: {count_expected_files()}")


if __name__ == '__main__':
    main()
