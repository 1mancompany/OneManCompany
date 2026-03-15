"""Background removal tool for generated images.

Provides one LangChain @tool:
- remove_image_background(input_path, output_path, tolerance=28)
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from langchain_core.tools import tool


def _dominant_corner_color(px, width: int, height: int) -> tuple[int, int, int]:
    """Estimate background color from the most similar corner cluster."""
    corners = [
        px[0, 0][:3],
        px[max(width - 1, 0), 0][:3],
        px[0, max(height - 1, 0)][:3],
        px[max(width - 1, 0), max(height - 1, 0)][:3],
    ]
    # 16-level quantization keeps similar corner colors in one bucket.
    buckets: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}
    for c in corners:
        key = (c[0] // 16, c[1] // 16, c[2] // 16)
        buckets.setdefault(key, []).append(c)
    dominant = max(buckets.values(), key=len)
    r = sum(c[0] for c in dominant) // len(dominant)
    g = sum(c[1] for c in dominant) // len(dominant)
    b = sum(c[2] for c in dominant) // len(dominant)
    return r, g, b


def _is_close_to_bg(rgb: tuple[int, int, int], bg: tuple[int, int, int], tolerance: int) -> bool:
    """Fast color similarity check (Manhattan distance)."""
    return (
        abs(rgb[0] - bg[0]) +
        abs(rgb[1] - bg[1]) +
        abs(rgb[2] - bg[2])
    ) <= tolerance * 3


@tool
def remove_image_background(input_path: str, output_path: str, tolerance: int = 28) -> dict:
    """Remove background from an image and save as transparent PNG. USE THIS after image_generation when transparency is needed.

    Best for: logos, stickers, product images, overlays — any image that needs a transparent background.

    Args:
        input_path: Source image path (output from image_generation).
        output_path: Output path (will be saved as .png with transparency).
        tolerance: Color tolerance for background matching (0-255, default 28). Increase for noisy backgrounds.
    """
    input_path = (input_path or "").strip()
    output_path = (output_path or "").strip()
    if not input_path:
        return {"status": "error", "message": "input_path is empty"}
    if not output_path:
        return {"status": "error", "message": "output_path is empty"}

    tolerance = max(0, min(int(tolerance), 255))

    src = Path(input_path).expanduser()
    if not src.exists() or not src.is_file():
        return {"status": "error", "message": f"input file not found: {src}"}

    try:
        from PIL import Image
    except ImportError:
        return {"status": "error", "message": "Pillow is required (pip install pillow)"}

    try:
        img = Image.open(src).convert("RGBA")
    except Exception as e:
        return {"status": "error", "message": f"failed to read image: {e}"}

    width, height = img.size
    if width == 0 or height == 0:
        return {"status": "error", "message": "invalid image size"}

    px = img.load()
    bg_color = _dominant_corner_color(px, width, height)
    visited = bytearray(width * height)
    q: deque[tuple[int, int]] = deque()

    def idx(x: int, y: int) -> int:
        return y * width + x

    def maybe_enqueue(x: int, y: int) -> None:
        if x < 0 or y < 0 or x >= width or y >= height:
            return
        p = px[x, y]
        rgb = p[:3]
        if p[3] == 0 or _is_close_to_bg(rgb, bg_color, tolerance):
            q.append((x, y))

    # Seed flood fill from image borders only, so subject interior isn't removed.
    for x in range(width):
        maybe_enqueue(x, 0)
        if height > 1:
            maybe_enqueue(x, height - 1)
    for y in range(1, height - 1):
        maybe_enqueue(0, y)
        if width > 1:
            maybe_enqueue(width - 1, y)

    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        i = idx(x, y)
        if visited[i]:
            continue

        p = px[x, y]
        rgb = p[:3]
        if p[3] != 0 and not _is_close_to_bg(rgb, bg_color, tolerance):
            continue

        visited[i] = 1
        q.append((x + 1, y))
        q.append((x - 1, y))
        q.append((x, y + 1))
        q.append((x, y - 1))

    removed_pixels = 0
    for y in range(height):
        for x in range(width):
            i = idx(x, y)
            if not visited[i]:
                continue
            r, g, b, a = px[x, y]
            if a != 0:
                px[x, y] = (r, g, b, 0)
                removed_pixels += 1

    out = Path(output_path).expanduser()
    if out.suffix.lower() != ".png":
        out = out.with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        img.save(out, format="PNG")
    except Exception as e:
        return {"status": "error", "message": f"failed to save output: {e}"}

    total_pixels = width * height
    return {
        "status": "ok",
        "input_path": str(src),
        "output_path": str(out),
        "size": {"width": width, "height": height},
        "bg_color_rgb": bg_color,
        "tolerance": tolerance,
        "removed_pixels": removed_pixels,
        "removed_ratio": round(removed_pixels / max(total_pixels, 1), 4),
    }
