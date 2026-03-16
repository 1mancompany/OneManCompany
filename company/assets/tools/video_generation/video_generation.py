"""Video generation tool via fal.ai + Bytedance Seedance 1.5 Pro.

Three-step workflow:
1. video_submit(prompt) → returns status_url + response_url
2. video_check_status(status_url) → poll via cron task every 30s until COMPLETED
3. video_download(response_url, save_path) → save video to disk
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from langchain_core.tools import tool

_MODEL_ID = "fal-ai/bytedance/seedance/v1.5/pro/text-to-video"
_QUEUE_BASE = "https://queue.fal.run"
_USER_AGENT = "OneManCompany-VideoGeneration/1.0"


# ── HTTP helpers ──────────────────────────────────────────


def _get_api_key() -> str | None:
    key = os.environ.get("FAL_KEY", "") or os.environ.get("FAL_API_KEY", "")
    return key.strip() or None


def _build_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }


def _request_json(
    method: str,
    url: str,
    headers: dict,
    payload: dict | None = None,
    timeout: int = 120,
) -> tuple[dict | None, str | None]:
    """Make an HTTP request and return (json_body, error)."""
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw), None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return None, f"HTTP {e.code}: {body_text[:800]}"
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON response: {e}"
    except Exception as e:
        return None, str(e)


def _download_file(url: str, timeout: int = 300) -> tuple[bytes | None, str]:
    """Download a file from URL, return (bytes, error_or_content_type)."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ct = resp.headers.get_content_type() or "video/mp4"
            return data, ct
    except Exception as e:
        return None, str(e)


# ── Tool 1: Submit ────────────────────────────────────────


@tool
def video_submit(
    prompt: str,
    duration: str = "5",
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    generate_audio: bool = True,
) -> dict:
    """Submit a text-to-video generation request. USE THIS for any video creation task.

    This is Step 1 of 3 for video generation:
    1. Call video_submit(prompt) → get status_url and response_url
    2. Create a cron task to call video_check_status(status_url) every 30 seconds
    3. When status is COMPLETED, call video_download(response_url, save_path)

    Args:
        prompt: Detailed description of the video to generate. Be specific about scenes, motion, style.
        duration: Video length: "auto", or "4" through "15" seconds (default: "5").
        aspect_ratio: "auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16" (default: "16:9").
        resolution: "480p" or "720p" (default: "720p").
        generate_audio: Whether to generate audio (default: True, doubles cost).
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"status": "error", "message": "prompt is empty"}

    api_key = _get_api_key()
    if not api_key:
        return {"status": "error", "message": "FAL_KEY or FAL_API_KEY not configured"}

    headers = _build_headers(api_key)
    payload = {
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "generate_audio": generate_audio,
    }

    url = f"{_QUEUE_BASE}/{_MODEL_ID}"
    resp, err = _request_json("POST", url, headers, payload, timeout=30)
    if err:
        return {"status": "error", "message": f"Failed to submit: {err}"}

    assert resp is not None
    request_id = resp.get("request_id")
    if not request_id:
        return {"status": "error", "message": f"No request_id: {json.dumps(resp)[:400]}"}

    return {
        "status": "ok",
        "message": (
            f"Video generation submitted! request_id: {request_id}. "
            "Generation typically takes 1-5 minutes. "
            "Create a cron task to call video_check_status every 30-60 seconds "
            "with the status_url below. Once COMPLETED, call video_download "
            "with the response_url below and a save_path."
        ),
        "request_id": request_id,
        "status_url": resp.get("status_url"),
        "response_url": resp.get("response_url"),
        "cancel_url": resp.get("cancel_url"),
        "queue_position": resp.get("queue_position"),
    }


# ── Tool 2: Check Status ─────────────────────────────────


@tool
def video_check_status(status_url: str) -> dict:
    """Check if a submitted video generation is done (Step 2 of 3).

    Call this via cron task every 30 seconds after video_submit.
    When generation_status is COMPLETED → call video_download.
    When FAILED → stop the cron task and report the error.

    Args:
        status_url: The status_url returned by video_submit.
    """
    status_url = (status_url or "").strip()
    if not status_url:
        return {"status": "error", "message": "status_url is empty"}

    api_key = _get_api_key()
    if not api_key:
        return {"status": "error", "message": "FAL_KEY or FAL_API_KEY not configured"}

    headers = _build_headers(api_key)
    resp, err = _request_json("GET", status_url, headers, timeout=30)
    if err:
        return {"status": "error", "message": f"Failed to check status: {err}"}

    assert resp is not None
    gen_status = resp.get("status", "UNKNOWN")

    result = {
        "status": "ok",
        "generation_status": gen_status,
        "request_id": resp.get("request_id"),
    }

    if gen_status == "IN_QUEUE":
        result["queue_position"] = resp.get("queue_position")
        result["message"] = "Still in queue. Check again in 30-60 seconds."
    elif gen_status == "IN_PROGRESS":
        result["message"] = "Video is being generated. Check again in 30-60 seconds."
    elif gen_status == "COMPLETED":
        result["message"] = (
            "Video generation is complete! "
            "Call video_download with the response_url and a save_path to download it. "
            "You can cancel the cron task now."
        )
        result["inference_time"] = (resp.get("metrics") or {}).get("inference_time")
    elif gen_status in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
        result["message"] = f"Generation failed: {resp.get('error', 'unknown error')}. You can cancel the cron task now."
    else:
        result["message"] = f"Unknown status: {gen_status}. Check again in 30 seconds."

    return result


# ── Tool 3: Download ──────────────────────────────────────


@tool
def video_download(response_url: str, save_path: str) -> dict:
    """Download a completed video to disk (Step 3 of 3).

    Call this after video_check_status returns COMPLETED. Stop the cron task after downloading.

    Args:
        response_url: The response_url returned by video_submit.
        save_path: Output file path (e.g. /tmp/my_video.mp4).
    """
    response_url = (response_url or "").strip()
    save_path = (save_path or "").strip()
    if not response_url:
        return {"status": "error", "message": "response_url is empty"}
    if not save_path:
        return {"status": "error", "message": "save_path is empty"}

    api_key = _get_api_key()
    if not api_key:
        return {"status": "error", "message": "FAL_KEY or FAL_API_KEY not configured"}

    headers = _build_headers(api_key)

    # Fetch the result
    resp, err = _request_json("GET", response_url, headers, timeout=60)
    if err:
        return {"status": "error", "message": f"Failed to fetch result: {err}"}

    assert resp is not None

    # Extract video URL — fal.ai format: {"video": {"url": "...", ...}}
    video_info = resp.get("video", {})
    video_url = video_info.get("url") if isinstance(video_info, dict) else None
    if not video_url:
        snippet = json.dumps(resp, ensure_ascii=False)[:400]
        return {
            "status": "error",
            "message": f"No video URL in result: {snippet}",
        }

    # Download
    video_bytes, content_type = _download_file(video_url)
    if not video_bytes:
        return {
            "status": "error",
            "message": f"Failed to download video: {content_type}",
            "video_url": video_url,
        }

    # Save to disk
    out = Path(save_path).expanduser()
    if not out.suffix:
        out = out.with_suffix(".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(video_bytes)

    return {
        "status": "ok",
        "model": _MODEL_ID,
        "saved_to": str(out),
        "bytes": len(video_bytes),
        "content_type": video_info.get("content_type", content_type),
        "video_url": video_url,
        "seed": resp.get("seed"),
    }
