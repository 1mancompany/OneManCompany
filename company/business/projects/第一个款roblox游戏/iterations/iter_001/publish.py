#!/usr/bin/env python3
"""Publish TechStartupTycoon to Roblox via Open Cloud API.

Usage:
    export ROBLOX_API_KEY="your-api-key"
    export ROBLOX_UNIVERSE_ID="your-universe-id"
    export ROBLOX_PLACE_ID="your-place-id"
    python publish.py

Environment variables (ALL REQUIRED):
    ROBLOX_API_KEY     - Open Cloud API key
                         Create at: https://create.roblox.com/dashboard/credentials
                         Permissions: universe-places → Write
    ROBLOX_UNIVERSE_ID - Universe ID of the experience
                         Find at: Creator Dashboard → hover experience → ⋯ → Copy Universe ID
    ROBLOX_PLACE_ID    - Place ID (root place of the universe)
                         Find at: Creator Dashboard → experience → place config URL

The script:
1. Builds the .rbxlx place file from Lua sources
2. Validates the place file (XML structure, script count, byte size)
3. Uploads it via POST to the Roblox Open Cloud Place Publishing API
4. Reports the version number on success

API Reference:
    https://create.roblox.com/docs/cloud/open-cloud/usage-place-publishing
"""
import os
import sys
import json
import hashlib
import datetime
import xml.etree.ElementTree as ET
from pathlib import Path

# Try to use requests; fall back to urllib
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False

SCRIPT_DIR = Path(__file__).parent

# ── Config ──────────────────────────────────────────────────────────────
# API key provided by project owner. Override via env var if needed.
API_KEY = os.environ.get("ROBLOX_API_KEY", "OOd4ZsDDE0mPLhQmsK8/3yN+tvW0+ktIvd+Oi2bN3XHGUVpDZXlKaGJHY2lPaUpTVXpJMU5pSXNJbXRwWkNJNkluTnBa")
# Universe/Place IDs must be obtained from Creator Dashboard after creating
# an experience in Roblox Studio. Override via env vars.
UNIVERSE_ID = os.environ.get("ROBLOX_UNIVERSE_ID", "")
PLACE_ID = os.environ.get("ROBLOX_PLACE_ID", "")

PUBLISH_URL_TEMPLATE = (
    "https://apis.roblox.com/universes/v1/{universe_id}/places/{place_id}/versions"
)


def build_place() -> Path:
    """Build the .rbxlx file."""
    from build_place import build_place as _build
    return _build()


def validate_place_file(place_file: Path) -> dict:
    """Validate the .rbxlx file structure and return diagnostics."""
    data = place_file.read_bytes()
    result = {
        "file_exists": place_file.exists(),
        "file_size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }

    # Parse XML and check structure
    tree = ET.parse(place_file)
    root = tree.getroot()

    items = root.findall(".//Item")
    result["total_items"] = len(items)

    class_counts = {}
    for item in items:
        cls = item.get("class", "Unknown")
        class_counts[cls] = class_counts.get(cls, 0) + 1
    result["class_counts"] = class_counts

    # Check for required services
    required = ["Workspace", "ServerScriptService", "ReplicatedStorage", "StarterGui", "StarterPlayer"]
    found_services = []
    for item in items:
        cls = item.get("class", "")
        if cls in required:
            found_services.append(cls)
    result["required_services_found"] = found_services
    result["all_services_present"] = set(required) == set(found_services)

    # Count scripts
    script_classes = {"Script", "LocalScript", "ModuleScript"}
    scripts = [it for it in items if it.get("class") in script_classes]
    result["script_count"] = len(scripts)
    result["scripts"] = []
    for s in scripts:
        props = s.find("Properties")
        name = ""
        source_len = 0
        if props is not None:
            name_el = props.find("string[@name='Name']")
            if name_el is not None:
                name = name_el.text or ""
            src_el = props.find("ProtectedString[@name='Source']")
            if src_el is not None and src_el.text:
                source_len = len(src_el.text)
        result["scripts"].append({
            "class": s.get("class"),
            "name": name,
            "source_length": source_len,
        })

    result["valid"] = (
        result["all_services_present"]
        and result["script_count"] >= 4
        and result["file_size_bytes"] > 1000
    )
    return result


def publish_place(place_file: Path) -> dict:
    """Upload place file to Roblox Open Cloud API.

    API endpoint:
        POST https://apis.roblox.com/universes/v1/{universeId}/places/{placeId}/versions
             ?versionType=Published
    Headers:
        x-api-key: <API_KEY>
        Content-Type: application/xml
    Body:
        Raw .rbxlx file bytes
    Response (200):
        {"versionNumber": <int>}
    """
    url = PUBLISH_URL_TEMPLATE.format(
        universe_id=UNIVERSE_ID, place_id=PLACE_ID
    )
    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/xml",
    }

    place_data = place_file.read_bytes()
    print(f"  Uploading {len(place_data)} bytes to Roblox Open Cloud ...")
    print(f"  URL: {url}?versionType=Published")
    print(f"  Universe: {UNIVERSE_ID}, Place: {PLACE_ID}")

    if HAS_REQUESTS:
        resp = requests.post(
            url, params={"versionType": "Published"},
            headers=headers, data=place_data, timeout=60,
        )
        status_code = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:2000]}
    else:
        full_url = url + "?versionType=Published"
        req = urllib.request.Request(
            full_url, data=place_data, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp_obj:
                status_code = resp_obj.status
                raw = resp_obj.read().decode()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {"raw": raw[:2000]}
        except urllib.error.HTTPError as e:
            status_code = e.code
            raw = e.read().decode()
            try:
                body = json.loads(raw)
            except Exception:
                body = {"raw": raw[:2000]}

    return {"status_code": status_code, "body": body}



def validate_api_key() -> dict:
    """Validate the API key by calling a real Roblox Open Cloud endpoint.

    Uses GET /cloud/v2/users/me as a lightweight auth check.
    Returns {"valid": bool, "status_code": int, "body": dict}.
    """
    url = "https://apis.roblox.com/cloud/v2/users/me"
    headers = {"x-api-key": API_KEY}

    print(f"  Testing API key against {url} ...")

    if HAS_REQUESTS:
        resp = requests.get(url, headers=headers, timeout=15)
        status_code = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
    else:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp_obj:
                status_code = resp_obj.status
                raw = resp_obj.read().decode()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {"raw": raw[:500]}
        except urllib.error.HTTPError as e:
            status_code = e.code
            raw = e.read().decode()
            try:
                body = json.loads(raw)
            except Exception:
                body = {"raw": raw[:500]}

    valid = 200 <= status_code < 300
    print(f"  API key validation: HTTP {status_code} — {'VALID' if valid else 'INVALID'}")
    if not valid:
        print(f"  Response: {json.dumps(body)}")
    return {"valid": valid, "status_code": status_code, "body": body}


def save_full_result(api_key_result: dict, validation: dict, place_file: Path,
                     publish_result: dict = None):
    """Save comprehensive result to publish_result.md."""
    ts = datetime.datetime.now().isoformat()
    key_valid = api_key_result["valid"]

    if publish_result:
        status = publish_result["status_code"]
        body = publish_result["body"]
        success = 200 <= status < 300
    else:
        status = api_key_result["status_code"]
        body = api_key_result["body"]
        success = False

    md_lines = [
        "# Roblox Publish Result",
        "",
        f"**Timestamp**: {ts}",
        f"**Mode**: LIVE (Roblox Open Cloud API — real calls, no mock)",
        f"**API Key**: `{API_KEY[:4]}...` (provided by project owner)",
        f"**API Key Valid**: {'YES' if key_valid else 'NO — Roblox returned HTTP ' + str(api_key_result['status_code'])}",
        f"**Place file**: `{place_file.name}` ({place_file.stat().st_size} bytes)",
        f"**Place file SHA-256**: `{validation['sha256']}`",
    ]

    if UNIVERSE_ID and PLACE_ID:
        md_lines.extend([
            f"**Target Universe**: {UNIVERSE_ID}",
            f"**Target Place**: {PLACE_ID}",
        ])

    if publish_result:
        md_lines.extend([
            f"**Publish HTTP Status**: {status}",
            f"**Publish Success**: {'YES' if success else 'NO'}",
        ])

    md_lines.append("")

    # API key validation section
    md_lines.extend([
        "## Step 1: API Key Validation",
        "",
        f"Called `GET https://apis.roblox.com/cloud/v2/users/me` with the provided API key.",
        "",
        f"- **HTTP Status**: {api_key_result['status_code']}",
        f"- **Result**: {'Key is valid' if key_valid else 'Key rejected by Roblox'}",
        "- **Response**:",
        "```json",
        json.dumps(api_key_result["body"], indent=2),
        "```",
    ])

    if not key_valid:
        md_lines.extend([
            "",
            "The API key `OOd4ZsDDE0mP` was rejected by the Roblox Open Cloud API with",
            f"HTTP {api_key_result['status_code']}. This means the key is either expired,",
            "revoked, or was never a valid Roblox Open Cloud API key.",
            "",
            "### How to Fix",
            "",
            "1. Log in to https://create.roblox.com/dashboard/credentials",
            "2. Create a **new** API key with **universe-places → Write** permission",
            "3. Copy the key immediately (Roblox won't show it again)",
            "4. Set the environment variable: `export ROBLOX_API_KEY=\"your-new-key\"`",
            "5. Re-run: `python publish.py`",
        ])

    if not UNIVERSE_ID or not PLACE_ID:
        md_lines.extend([
            "",
            "## Step 2: Universe & Place IDs (MISSING)",
            "",
            "The Roblox Open Cloud API **cannot create new experiences programmatically**.",
            "An experience must first be created via Roblox Studio or the Creator Dashboard.",
            "",
            "### How to Get the IDs",
            "",
            "1. Open **Roblox Studio** and create a new experience (or use an existing one)",
            "2. Publish it once from Studio to create the Universe/Place on Roblox servers",
            "3. Go to https://create.roblox.com/dashboard/creations",
            "4. Hover over the experience → **⋯** → **Copy Universe ID**",
            "5. Open the experience → place config → note the **Place ID** from the URL",
            "6. Set environment variables:",
            "   ```bash",
            '   export ROBLOX_UNIVERSE_ID="your-universe-id"',
            '   export ROBLOX_PLACE_ID="your-place-id"',
            "   ```",
        ])

    if publish_result:
        md_lines.extend([
            "",
            "## Step 3: Publish Attempt",
            "",
            f"- **URL**: `{PUBLISH_URL_TEMPLATE.format(universe_id=UNIVERSE_ID, place_id=PLACE_ID)}?versionType=Published`",
            f"- **HTTP Status**: {status}",
            f"- **Success**: {'YES' if success else 'NO'}",
            "- **Response**:",
            "```json",
            json.dumps(body, indent=2),
            "```",
        ])
        if success:
            version = body.get("versionNumber", "N/A")
            md_lines.extend([
                "",
                f"**Version {version} published successfully!**",
            ])

    # Place file validation section
    md_lines.extend([
        "",
        "---",
        "",
        "## Place File Validation (ALL PASS)",
        "",
        "| Check | Result |",
        "|-------|--------|",
        f"| File exists | {'PASS' if validation['file_exists'] else 'FAIL'} |",
        f"| File size | {validation['file_size_bytes']} bytes |",
        f"| XML parseable | PASS |",
        f"| Total XML items | {validation['total_items']} |",
        f"| All required services present | {'PASS' if validation['all_services_present'] else 'FAIL'} |",
        f"| Services found | {', '.join(validation['required_services_found'])} |",
        f"| Script count | {validation['script_count']} |",
        f"| Overall valid | {'PASS' if validation['valid'] else 'FAIL'} |",
        "",
        "### Embedded Scripts",
        "",
        "| Class | Name | Source Length |",
        "|-------|------|-------------|",
    ])
    for s in validation["scripts"]:
        md_lines.append(f"| {s['class']} | {s['name']} | {s['source_length']} chars |")

    md_lines.extend([
        "",
        "---",
        "",
        "## Game Files Produced",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `default.project.json` | Rojo project configuration |",
        "| `src/ReplicatedStorage/GameConfig.lua` | Game constants and upgrade definitions |",
        "| `src/ServerScriptService/DataManager.lua` | Player data persistence (DataStore) |",
        "| `src/ServerScriptService/GameManager.server.lua` | Main server logic (click, sell, upgrades, rebirth, auto-code) |",
        "| `src/StarterGui/MainGui.lua` | UI creation (LocalScript) |",
        "| `src/StarterPlayerScripts/ClickHandler.client.lua` | Client-side input handling |",
        "| `TechStartupTycoon.rbxlx` | Compiled Roblox place file (XML) |",
        "| `build_place.py` | Build script: Lua sources → .rbxlx |",
        "| `publish.py` | Publish script: .rbxlx → Roblox Open Cloud API |",
    ])

    out_path = SCRIPT_DIR / "publish_result.md"
    out_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"  Result saved to {out_path}")
    return out_path


def main():
    print("=" * 60)
    print("Tech Startup Tycoon — Build & Publish (REAL API)")
    print("=" * 60)

    # Step 1: Build
    print("\n[1/5] Building .rbxlx place file ...")
    place_file = build_place()

    # Step 2: Validate
    print("\n[2/5] Validating place file ...")
    validation = validate_place_file(place_file)
    print(f"  File size: {validation['file_size_bytes']} bytes")
    print(f"  SHA-256: {validation['sha256']}")
    print(f"  XML items: {validation['total_items']}")
    print(f"  Scripts: {validation['script_count']}")
    print(f"  Services: {', '.join(validation['required_services_found'])}")
    print(f"  Valid: {'YES' if validation['valid'] else 'NO'}")

    if not validation["valid"]:
        print("\nERROR: Place file validation failed. Aborting.")
        sys.exit(1)

    # Step 3: Validate API key against real Roblox endpoint
    print(f"\n[3/5] Validating API key against Roblox Open Cloud ...")
    print(f"  API key: {API_KEY[:4]}...")
    api_key_result = validate_api_key()

    # Step 4: Attempt publish if we have all credentials
    publish_result = None
    has_ids = bool(UNIVERSE_ID and PLACE_ID)

    if api_key_result["valid"] and has_ids:
        print(f"\n[4/5] Publishing to Universe={UNIVERSE_ID}, Place={PLACE_ID} ...")
        publish_result = publish_place(place_file)
    elif not api_key_result["valid"]:
        print(f"\n[4/5] SKIPPED — API key rejected by Roblox (HTTP {api_key_result['status_code']})")
        print("  Cannot proceed with publish. See publish_result.md for fix instructions.")
    else:
        print(f"\n[4/5] SKIPPED — Missing ROBLOX_UNIVERSE_ID and/or ROBLOX_PLACE_ID")
        print("  The Roblox API cannot create new experiences. You must:")
        print("  1. Create an experience in Roblox Studio")
        print("  2. Publish it once from Studio")
        print("  3. Set ROBLOX_UNIVERSE_ID and ROBLOX_PLACE_ID env vars")

    # Step 5: Save result
    print(f"\n[5/5] Saving result ...")
    save_full_result(api_key_result, validation, place_file, publish_result)

    if publish_result and 200 <= publish_result["status_code"] < 300:
        version = publish_result["body"].get("versionNumber", "?")
        print(f"\nSUCCESS — Version {version} published to Roblox!")
    else:
        print("\nPublish was not completed. See publish_result.md for details and next steps.")
        sys.exit(1)

    print("Done!")
    return publish_result


if __name__ == "__main__":
    main()
