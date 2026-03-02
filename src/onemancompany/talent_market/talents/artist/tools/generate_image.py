"""Image generation tool — calls the employee's configured image_model via OpenRouter.

Exports a LangChain @tool function for generating images from text prompts.
The actual model used is read from the employee's profile.yaml `image_model` field
at runtime (e.g. google/gemini-3-pro-image-preview).

OpenRouter image generation requires ``"modalities": ["image", "text"]`` in the
request and returns base64-encoded images in ``choices[].message.images[]``.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path

import httpx
from langchain_core.tools import tool


def _save_images_from_response(data: dict, output_dir: Path) -> list[str]:
    """Extract base64 images from OpenRouter response and save to disk.

    Returns list of saved file paths.
    """
    saved: list[str] = []
    message = data.get("choices", [{}])[0].get("message", {})
    images = message.get("images", [])

    for img in images:
        url_str = img.get("image_url", {}).get("url", "")
        if not url_str.startswith("data:image/"):
            continue
        # Parse data URI: data:image/png;base64,<data>
        try:
            header, b64data = url_str.split(",", 1)
            # Extract extension from header (e.g. "data:image/png;base64")
            ext = header.split("/")[1].split(";")[0]
            raw = base64.b64decode(b64data)
            fname = f"generated_{uuid.uuid4().hex[:8]}.{ext}"
            out = output_dir / fname
            out.write_bytes(raw)
            saved.append(str(out))
        except Exception:
            continue

    return saved


@tool
def generate_image(
    prompt: str, style: str = "illustration", size: str = "1024x1024"
) -> dict:
    """Generate an image using your configured image generation model.

    This tool calls the image model specified in your employee profile to
    produce visual content.  Use it for illustrations, concept art, UI assets,
    posters, and any other visual work.

    Args:
        prompt: Detailed text description of the image to generate.
        style: Visual style hint (illustration, concept_art, pixel_art, poster, photo_realistic).
        size: Output image dimensions (default: 1024x1024).

    Returns:
        A dict with status, saved file paths, model info, and text content.
    """
    # --- resolve employee context at runtime ---
    from onemancompany.core.agent_loop import _current_loop, _current_task_id
    from onemancompany.core.config import employee_configs, settings, PROJECTS_DIR

    loop = _current_loop.get(None)
    if not loop:
        return {"status": "error", "message": "No agent context — cannot determine image model."}

    employee_id = loop.agent.employee_id
    cfg = employee_configs.get(employee_id)
    if not cfg or not cfg.image_model:
        return {
            "status": "error",
            "message": f"No image_model configured for employee {employee_id}. "
            "Ask HR to update your profile.",
        }

    model = cfg.image_model

    # Determine output directory (project workspace if available, else temp)
    task_id = _current_task_id.get(None)
    output_dir = Path("/tmp") / "generated_images"
    if task_id:
        task = loop.board.get_task(task_id)
        if task:
            # Use project_dir (resolved workspace path) — dispatch_task() may
            # clear project_id but preserves original_project_dir
            pdir = task.project_dir or task.original_project_dir
            if pdir and Path(pdir).exists():
                output_dir = Path(pdir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build the prompt for the image model
    full_prompt = (
        f"Generate a {style} style image ({size}). "
        f"Description: {prompt}"
    )

    # Call OpenRouter with image modalities
    try:
        resp = httpx.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": full_prompt}],
                "modalities": ["image", "text"],
            },
            timeout=120,
        )
    except Exception as e:
        return {"status": "error", "message": f"HTTP request failed: {e}"}

    if resp.status_code != 200:
        return {"status": "error", "message": f"API error {resp.status_code}: {resp.text[:300]}"}

    data = resp.json()

    # Extract text content
    text_content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

    # Extract and save images
    images = _save_images_from_response(data, output_dir)

    return {
        "status": "success",
        "model": model,
        "prompt": prompt,
        "style": style,
        "size": size,
        "images": images,
        "image_count": len(images),
        "output_dir": str(output_dir),
        "text": text_content[:500] if text_content else "",
        "usage": data.get("usage"),
    }
