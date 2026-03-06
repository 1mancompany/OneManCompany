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
# CEO-provided API key as default (corrected key matching api_key.txt)
DEFAULT_API_KEY = (
    "OOd4ZsDDE0mPLhQmsK8/3yN+tvW0+ktIvd+Oi2bN3XHGUVpDZXlKaGJHY2lPaUpTVXpJMU5p"
    "SXNJbXRwWkNJNkluTnBaeTB5TURJeExUQTNMVEV6VkRFNE9qVXhPalE1V2lJc0luUjVjQ0k2"
    "SWtwWFZDSjkuZXlKaGRXUWlPaUpTYjJKc2IzaEpiblJsY201aGJDSXNJbWx6Y3lJNklrTnNi"
    "M1ZrUVhWMGFHVnVkR2xqWVhScGIyNVRaWEoyYVdObElpd2lZbUZ6WlVGd2FVdGxlU0k2SWs5"
    "UFpEUmFjMFJFUlRCdFVFeG9VVzF6U3pndk0zbE9LM1IyVnpBcmEzUkpkbVFyVDJreVlrNHpX"
    "RWhIVlZad1JDSXNJbTkzYm1WeVNXUWlPaUl4TURZeE56TXpNRGt3TWlJc0ltVjRjQ0k2TVRj"
    "M01qY3pORFl4TVN3aWFXRjBJam94TnpjeU56TXhNREV4TENKdVltWWlPakUzTnpJM016RXdN"
    "VEY5LmVTeG84aFBKTGx0VEs0eEdxSlRfTkZ6S1BsR2JMNmRKS1BKN0tEdEg3eVl0S1kyRGlF"
    "Nm1fdGs4OVM2cDR5cDNDQmQ1SkxJN29XY0gzWm5FYnhITktIdkMwTnRtRXQ3clp0dXVFNWFS"
    "eHN4ZkdqZ0Q2aE9pNXo1dS04Sy1DYXVMTURSU1NvZ3dTYUZLTEp6SEJzZS1HejBzTnJFUjVt"
    "eW12ZlMyQ1ZDTDZ4UHVQNEIzUXBsZU5Nb2hfRWJqU3ZXOHVOVXc2dmN2Y216UzBpYTRJbU53"
    "bWxjMVpDUW5CZ3NETHRtRERURDRBUkRnUmhiOWVIenB1LU1nUU1RYkUxSWxWMjFtU0hrQ0N3"
    "MWFxT09fbUQ5TU9WdnpJbnpGT1JyQ3Vqd04xdEVlSDV0c2RPczA5ZGlQOTdYQjFyZ1VMV1Vf"
    "Y2pmUTdRaF9fTGJPTDEzU3E5MlJPdw=="
)
# Key priority: env ROBLOX_API_KEY > env ROBLOX_CLOUD_API_KEY > api_key.txt > DEFAULT_API_KEY
def _load_api_key():
    key = os.environ.get("ROBLOX_API_KEY") or os.environ.get("ROBLOX_CLOUD_API_KEY")
    if key:
        return key
    key_file = SCRIPT_DIR / "api_key.txt"
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        if key:
            return key
    return DEFAULT_API_KEY

API_KEY = _load_api_key()
# Discovered via Roblox public API: user 10617330902 (fredyuzx)
UNIVERSE_ID = os.environ.get("ROBLOX_UNIVERSE_ID", "9838675013")
PLACE_ID = os.environ.get("ROBLOX_PLACE_ID", "118831023221258")

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

    try:
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
    except Exception as e:
        status_code = 0
        body = {"error": type(e).__name__, "message": str(e)[:500]}

    return {"status_code": status_code, "body": body}


def save_result(result: dict, place_file: Path, validation: dict):
    """Save publish result to publish_result.md."""
    ts = datetime.datetime.now().isoformat()
    status = result["status_code"]
    body = result["body"]
    version_number = body.get("versionNumber", "N/A")
    success = 200 <= status < 300

    md_lines = [
        "# Roblox Publish Result",
        "",
        f"**Timestamp**: {ts}",
        f"**Mode**: LIVE (Roblox Open Cloud API)",
        f"**Place file**: `{place_file.name}` ({place_file.stat().st_size} bytes)",
        f"**Place file SHA-256**: `{validation['sha256']}`",
        f"**Target Universe**: {UNIVERSE_ID}",
        f"**Target Place**: {PLACE_ID}",
        f"**HTTP Status**: {status}",
        f"**Success**: {'YES' if success else 'NO'}",
        "",
    ]

    if success:
        md_lines.extend([
            "## Published Successfully",
            "",
            f"- **Version Number**: {version_number}",
            f"- **API Response**:",
            "```json",
            json.dumps(body, indent=2),
            "```",
        ])
    else:
        md_lines.extend([
            "## Publish Failed",
            "",
            f"- **Error Response**:",
            "```json",
            json.dumps(body, indent=2),
            "```",
            "",
            "### Troubleshooting",
            "",
            "1. Verify `ROBLOX_API_KEY` is valid and has **universe-places → Write** permission",
            "2. Verify `ROBLOX_UNIVERSE_ID` and `ROBLOX_PLACE_ID` are correct",
            "3. The API key must have Write scope for the target universe",
            "4. Create/manage API keys at: https://create.roblox.com/dashboard/credentials",
            "5. Check API reference: https://create.roblox.com/docs/cloud/open-cloud/usage-place-publishing",
        ])

    # Place file validation section
    md_lines.extend([
        "",
        "---",
        "",
        "## Place File Validation",
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

    # Game files section
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


def check_credentials():
    """Check that all required credentials are present. Exit with guidance if not."""
    missing = []
    if not API_KEY:
        missing.append("ROBLOX_API_KEY")
    if not UNIVERSE_ID:
        missing.append("ROBLOX_UNIVERSE_ID")
    if not PLACE_ID:
        missing.append("ROBLOX_PLACE_ID")

    if not missing:
        print(f"  API Key: {API_KEY[:12]}...{API_KEY[-8:]}")
        print(f"  Universe: {UNIVERSE_ID}")
        print(f"  Place: {PLACE_ID}")
        return

    print("ERROR: Missing required credentials:")
    for var in missing:
        print(f"  - {var}")
    sys.exit(1)


def main():
    print("=" * 60)
    print("Tech Startup Tycoon — Build & Publish")
    print("=" * 60)

    # Step 1: Check credentials
    print("\n[1/4] Checking Roblox Open Cloud credentials ...")
    check_credentials()
    print("  All credentials present.")

    # Step 2: Build
    print("\n[2/4] Building .rbxlx place file ...")
    place_file = build_place()

    # Step 3: Validate
    print("\n[3/4] Validating place file ...")
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

    # Step 4: Publish via real API
    print(f"\n[4/4] Publishing to Roblox Open Cloud API ...")
    result = publish_place(place_file)

    # Save result
    print("\nSaving result ...")
    save_result(result, place_file, validation)

    status = result["status_code"]
    if 200 <= status < 300:
        version = result["body"].get("versionNumber", "?")
        print(f"\nSUCCESS — Version {version} published to Roblox!")
    else:
        print(f"\nFAILED — HTTP {status}")
        print(f"Response: {json.dumps(result['body'], indent=2)}")
        sys.exit(1)

    print("Done!")
    return result


if __name__ == "__main__":
    main()
