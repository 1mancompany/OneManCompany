"""Image generation tool via OpenRouter + Gemini image model.

Provides one LangChain @tool:
- image_generation(requirement, save_path)
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from langchain_core.tools import tool

_MODEL = "google/gemini-3-pro-image-preview"
_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_USER_AGENT = "OneManCompany-ImageGeneration/1.0"


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 60) -> tuple[dict | None, str | None]:
    """POST JSON and return (json_body, error)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
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


def _mime_to_ext(mime: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(mime.lower(), ".png")


def _decode_data_url(data_url: str) -> tuple[bytes | None, str]:
    """Decode data:image/...;base64,... into bytes."""
    if not data_url.startswith("data:image/") or "," not in data_url:
        return None, ""
    header, payload = data_url.split(",", 1)
    mime = header.split(";")[0][5:].strip() or "image/png"
    try:
        return base64.b64decode(payload), mime
    except Exception:
        return None, ""


def _decode_base64(payload: str) -> bytes | None:
    """Decode base64 payload (supports missing padding)."""
    text = payload.strip()
    if not text:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        pad = "=" * (-len(text) % 4)
        try:
            return base64.b64decode(text + pad)
        except Exception:
            try:
                return base64.urlsafe_b64decode(text + pad)
            except Exception:
                return None


def _download_image(url: str) -> tuple[bytes | None, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            mime = resp.headers.get_content_type() or "image/png"
            return data, mime
    except Exception:
        return None, ""


def _extract_data_url_from_text(text: str) -> tuple[bytes | None, str]:
    """Extract first data:image URL from free text."""
    m = re.search(r"(data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=_-]+)", text)
    if not m:
        return None, ""
    return _decode_data_url(m.group(1))


def _iter_values(obj):
    """Yield (key, value) pairs recursively for dict/list trees."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _iter_values(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_values(item)


def _extract_image_bytes(response_json: dict) -> tuple[bytes | None, str]:
    """Try to extract generated image bytes from multiple response layouts."""
    # 1) OpenAI Images style: {"data":[{"b64_json":"..."}]}
    data = response_json.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            b64_payload = item.get("b64_json") or item.get("image_base64")
            if isinstance(b64_payload, str):
                decoded = _decode_base64(b64_payload)
                if decoded:
                    return decoded, "image/png"
            url = item.get("url") or item.get("image_url")
            if isinstance(url, str):
                if url.startswith("data:image/"):
                    decoded, mime = _decode_data_url(url)
                    if decoded:
                        return decoded, mime
                elif url.startswith("http://") or url.startswith("https://"):
                    downloaded, mime = _download_image(url)
                    if downloaded:
                        return downloaded, mime

    # 2) Chat/Responses style variants — recursive scan for common fields
    base64_keys = {"b64_json", "image_base64", "base64", "b64"}
    url_keys = {"image_url", "url"}
    for key, value in _iter_values(response_json):
        if key in base64_keys and isinstance(value, str):
            decoded = _decode_base64(value)
            if decoded:
                return decoded, "image/png"

        if key in url_keys:
            url_value = ""
            if isinstance(value, str):
                url_value = value
            elif isinstance(value, dict) and isinstance(value.get("url"), str):
                url_value = value["url"]

            if url_value.startswith("data:image/"):
                decoded, mime = _decode_data_url(url_value)
                if decoded:
                    return decoded, mime
            elif url_value.startswith("http://") or url_value.startswith("https://"):
                downloaded, mime = _download_image(url_value)
                if downloaded:
                    return downloaded, mime

        if isinstance(value, str) and "data:image/" in value:
            decoded, mime = _extract_data_url_from_text(value)
            if decoded:
                return decoded, mime

    return None, ""


def _build_headers(api_key: str) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    app_name = os.environ.get("OPENROUTER_APP_NAME", "OneManCompany")
    http_referer = os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost:8000")
    if app_name:
        headers["X-Title"] = app_name
    if http_referer:
        headers["HTTP-Referer"] = http_referer
    return headers


@tool
def image_generation(requirement: str, save_path: str) -> dict:
    """Generate an image from requirement and save it to a local path.

    Args:
        requirement: Text requirement/prompt for image generation.
        save_path: Output file path (e.g. /tmp/banner.png).
    """
    requirement = (requirement or "").strip()
    save_path = (save_path or "").strip()
    if not requirement:
        return {"status": "error", "message": "requirement is empty"}
    if not save_path:
        return {"status": "error", "message": "save_path is empty"}

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return {"status": "error", "message": "OPENROUTER_API_KEY not configured"}

    base_url = os.environ.get("OPENROUTER_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    headers = _build_headers(api_key)

    attempts = [
        (
            "chat.completions.text",
            f"{base_url}/chat/completions",
            {
                "model": _MODEL,
                "messages": [{"role": "user", "content": requirement}],
            },
        ),
        (
            "chat.completions.multimodal",
            f"{base_url}/chat/completions",
            {
                "model": _MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": requirement}],
                    }
                ],
            },
        ),
        (
            "responses",
            f"{base_url}/responses",
            {
                "model": _MODEL,
                "input": requirement,
            },
        ),
    ]

    image_bytes: bytes | None = None
    image_mime = "image/png"
    errors: list[str] = []

    for attempt_name, url, payload in attempts:
        resp_json, err = _post_json(url, headers, payload)
        if err:
            errors.append(f"{attempt_name}: {err}")
            continue
        assert resp_json is not None
        image_bytes, image_mime = _extract_image_bytes(resp_json)
        if image_bytes:
            break
        snippet = json.dumps(resp_json, ensure_ascii=False)[:400]
        errors.append(f"{attempt_name}: no image found, response={snippet}")

    if not image_bytes:
        return {
            "status": "error",
            "message": "Image generation failed on all OpenRouter attempts.",
            "model": _MODEL,
            "errors": errors,
        }

    out = Path(save_path).expanduser()
    if not out.suffix:
        out = out.with_suffix(_mime_to_ext(image_mime))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image_bytes)

    return {
        "status": "ok",
        "model": _MODEL,
        "saved_to": str(out),
        "bytes": len(image_bytes),
        "mime": image_mime,
    }
