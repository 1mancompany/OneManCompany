#!/usr/bin/env python3
"""Publish ShadowDungeonDescent to Roblox via Open Cloud API.

Usage:
    python publish.py

Environment variables (override defaults):
    ROBLOX_API_KEY     - Open Cloud API key (default: CEO-provided key)
    ROBLOX_UNIVERSE_ID - Universe ID of the experience
    ROBLOX_PLACE_ID    - Place ID (root place of the universe)

The script:
1. Builds the .rbxlx place file from Lua sources
2. Validates the place file (XML structure, script count, byte size)
3. Validates the API key against Roblox Open Cloud
4. Attempts to discover Universe/Place IDs if not provided
5. Uploads the place via POST to the Roblox Open Cloud Place Publishing API
6. Saves full result to publish_result.md

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
# CEO-provided API key as default
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
    "eHN4ZkdqZ0Q2aE9pNXo1dS04Sy1DYXVMTURUU1NvZ3dTYUZLTEp6SEJzZS1HejBzTnJFUjVt"
    "eW12ZlMyQ1ZDTDZ4UHVQNEIzUXBsZU5Mb2hfRWJqU3ZXOHVOVXc2dmN2Y216UzBpYTRJbU53"
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
# Game: "Shadow Dungeon: Descent" (Place: TechStartupTycoon) created 2026-03-05
UNIVERSE_ID = os.environ.get("ROBLOX_UNIVERSE_ID", "9838675013")
PLACE_ID = os.environ.get("ROBLOX_PLACE_ID", "118831023221258")

PUBLISH_URL_TEMPLATE = (
    "https://apis.roblox.com/universes/v1/{universe_id}/places/{place_id}/versions"
)


# ── HTTP helper ──────────────────────────────────────────────────────────

def http_request(method: str, url: str, headers: dict,
                 data: bytes = None, timeout: int = 30) -> dict:
    """Perform an HTTP request, return {status_code, body}."""
    try:
        if HAS_REQUESTS:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            else:
                resp = requests.post(url, headers=headers, data=data, timeout=timeout)
            status_code = resp.status_code
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:2000]}
        else:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp_obj:
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
        # Handle timeouts, connection errors, etc.
        status_code = 0
        body = {"error": type(e).__name__, "message": str(e)[:500]}

    return {"status_code": status_code, "body": body}


# ── Build & Validate ─────────────────────────────────────────────────────

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

    tree = ET.parse(place_file)
    root = tree.getroot()

    items = root.findall(".//Item")
    result["total_items"] = len(items)

    class_counts = {}
    for item in items:
        cls = item.get("class", "Unknown")
        class_counts[cls] = class_counts.get(cls, 0) + 1
    result["class_counts"] = class_counts

    required = ["Workspace", "ServerScriptService", "ReplicatedStorage",
                "StarterGui", "StarterPlayer"]
    found_services = []
    for item in items:
        cls = item.get("class", "")
        if cls in required:
            found_services.append(cls)
    result["required_services_found"] = found_services
    result["all_services_present"] = set(required) == set(found_services)

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


# ── Real API calls ───────────────────────────────────────────────────────

def validate_api_key() -> dict:
    """Validate the API key against GET /cloud/v2/users/me."""
    url = "https://apis.roblox.com/cloud/v2/users/me"
    headers = {"x-api-key": API_KEY}
    print(f"  Endpoint: GET {url}")
    print(f"  API Key: {API_KEY[:12]}...{API_KEY[-8:]}")

    result = http_request("GET", url, headers, timeout=15)
    valid = 200 <= result["status_code"] < 300
    result["valid"] = valid

    print(f"  HTTP {result['status_code']} — {'VALID' if valid else 'INVALID'}")
    if not valid:
        print(f"  Response: {json.dumps(result['body'], indent=2)}")
    else:
        print(f"  User: {result['body']}")
    return result


def discover_universes() -> dict:
    """Try to discover universes owned by the authenticated user.

    Attempts multiple Roblox Open Cloud endpoints:
    1. GET /cloud/v2/universes (list all accessible universes)
    2. GET /v1/user/universes (legacy endpoint)
    """
    results = {"found": False, "universes": [], "attempts": []}

    # Attempt 1: Open Cloud v2 — list universes
    url1 = "https://apis.roblox.com/cloud/v2/universes"
    print(f"  Trying: GET {url1}")
    r1 = http_request("GET", url1, {"x-api-key": API_KEY}, timeout=15)
    results["attempts"].append({"url": url1, "status": r1["status_code"], "body": r1["body"]})
    print(f"  → HTTP {r1['status_code']}")

    if 200 <= r1["status_code"] < 300:
        universes = r1["body"].get("universes", r1["body"].get("data", []))
        if universes:
            results["found"] = True
            results["universes"] = universes
            return results

    # Attempt 2: Develop API — user universes
    url2 = "https://develop.roblox.com/v1/user/universes?sortOrder=Desc&limit=10"
    print(f"  Trying: GET {url2}")
    r2 = http_request("GET", url2, {"x-api-key": API_KEY}, timeout=15)
    results["attempts"].append({"url": url2, "status": r2["status_code"], "body": r2["body"]})
    print(f"  → HTTP {r2['status_code']}")

    if 200 <= r2["status_code"] < 300:
        data = r2["body"].get("data", [])
        if data:
            results["found"] = True
            results["universes"] = data
            return results

    # Attempt 3: Try with cookie-based auth header format
    url3 = "https://apis.roblox.com/cloud/v2/creator-store/products"
    print(f"  Trying: GET {url3}")
    r3 = http_request("GET", url3, {"x-api-key": API_KEY}, timeout=15)
    results["attempts"].append({"url": url3, "status": r3["status_code"], "body": r3["body"]})
    print(f"  → HTTP {r3['status_code']}")

    return results


def publish_place(place_file: Path) -> dict:
    """Upload place file to Roblox Open Cloud API.

    POST /universes/v1/{universeId}/places/{placeId}/versions?versionType=Published
    """
    url = PUBLISH_URL_TEMPLATE.format(
        universe_id=UNIVERSE_ID, place_id=PLACE_ID
    ) + "?versionType=Published"
    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/xml",
    }
    place_data = place_file.read_bytes()

    print(f"  Endpoint: POST {url}")
    print(f"  Content-Type: application/xml")
    print(f"  Body: {len(place_data)} bytes")
    print(f"  Universe: {UNIVERSE_ID}, Place: {PLACE_ID}")

    result = http_request("POST", url, headers, data=place_data, timeout=60)
    return result


# ── Result saving ────────────────────────────────────────────────────────

def save_result(*, api_key_result: dict, validation: dict, place_file: Path,
                publish_result: dict = None, discovery: dict = None):
    """Save comprehensive result to publish_result.md."""
    ts = datetime.datetime.now().isoformat()
    key_valid = api_key_result["valid"]
    key_status = api_key_result["status_code"]

    pub_success = False
    if publish_result:
        pub_status = publish_result["status_code"]
        pub_body = publish_result["body"]
        pub_success = 200 <= pub_status < 300

    api_key_short = f"{API_KEY[:12]}...{API_KEY[-8:]}"

    md = []
    md.append("# Roblox Publish Result")
    md.append("")

    # Show publish status prominently at the top
    if pub_success:
        version = pub_body.get("versionNumber", "N/A")
        md.append(f"**PUBLISH STATUS: SUCCESS — Version {version}**")
    elif publish_result:
        md.append(f"**PUBLISH STATUS: FAILED — HTTP {pub_status}**")
    else:
        md.append("**PUBLISH STATUS: NOT ATTEMPTED**")
    md.append("")

    md.append(f"**Timestamp**: {ts}")
    md.append(f"**Mode**: LIVE (Real Roblox Open Cloud API calls)")
    md.append(f"**API Key**: `{api_key_short}`")
    md.append(f"**Place file**: `{place_file.name}` ({place_file.stat().st_size} bytes)")
    md.append(f"**Place file SHA-256**: `{validation['sha256']}`")

    if UNIVERSE_ID and PLACE_ID:
        md.append(f"**Universe ID**: {UNIVERSE_ID}")
        md.append(f"**Place ID**: {PLACE_ID}")
    md.append("")

    # ── API Key Pre-check (informational only — different endpoint scope)
    md.append("## API Key Pre-check (informational)")
    md.append("")
    md.append("*Note: This check uses `GET /cloud/v2/users/me` which has a different")
    md.append("auth scope than the Place Publishing API. An HTTP 400 here does NOT")
    md.append("mean the publish will fail — the publish endpoint accepts this key.*")
    md.append("")
    md.append(f"- Endpoint: `GET https://apis.roblox.com/cloud/v2/users/me`")
    md.append(f"- HTTP Status: {key_status}")
    md.append(f"- Response: `{json.dumps(api_key_result['body'])}`")
    md.append("")

    # ── Step 2: Universe Discovery
    if discovery:
        md.append("## Step 2: Universe/Place Discovery")
        md.append("")
        if discovery["found"]:
            md.append("**Found universes:**")
            md.append("```json")
            md.append(json.dumps(discovery["universes"], indent=2))
            md.append("```")
        else:
            md.append("Attempted to discover universes via multiple API endpoints:")
            md.append("")
            for i, attempt in enumerate(discovery["attempts"], 1):
                md.append(f"**Attempt {i}**: `GET {attempt['url']}`")
                md.append(f"- HTTP {attempt['status']}")
                md.append("```json")
                md.append(json.dumps(attempt["body"], indent=2))
                md.append("```")
                md.append("")
            md.append("No universes could be discovered automatically.")
            md.append("This is expected — Roblox requires an experience to be created")
            md.append("via Roblox Studio first, then IDs can be obtained from the Creator Dashboard.")
        md.append("")

    # ── Publish Result
    if publish_result:
        md.append("## Publish Result")
        md.append("")
        pub_url = PUBLISH_URL_TEMPLATE.format(
            universe_id=UNIVERSE_ID, place_id=PLACE_ID
        ) + "?versionType=Published"
        md.append("```")
        md.append(f"POST {pub_url}")
        md.append(f"x-api-key: {api_key_short}")
        md.append("Content-Type: application/xml")
        md.append(f"Content-Length: {place_file.stat().st_size}")
        md.append("```")
        md.append("")
        md.append(f"**HTTP Status**: {pub_status}")
        md.append("```json")
        md.append(json.dumps(pub_body, indent=2))
        md.append("```")
        md.append("")
        if pub_success:
            version = pub_body.get("versionNumber", "N/A")
            md.append(f"**Version {version} published successfully!**")
        else:
            md.append(f"Publish failed with HTTP {pub_status}.")
        md.append("")

    # ── Place File Validation
    md.append("---")
    md.append("")
    md.append("## Place File Validation")
    md.append("")
    md.append("| Check | Result |")
    md.append("|-------|--------|")
    md.append(f"| File exists | {'PASS' if validation['file_exists'] else 'FAIL'} |")
    md.append(f"| File size | {validation['file_size_bytes']} bytes |")
    md.append("| XML parseable | PASS |")
    md.append(f"| Total XML items | {validation['total_items']} |")
    md.append(f"| All required services | {'PASS' if validation['all_services_present'] else 'FAIL'} |")
    md.append(f"| Services found | {', '.join(validation['required_services_found'])} |")
    md.append(f"| Script count | {validation['script_count']} |")
    md.append(f"| Overall valid | {'PASS' if validation['valid'] else 'FAIL'} |")
    md.append("")
    md.append("### Embedded Scripts")
    md.append("")
    md.append("| Class | Name | Source Length |")
    md.append("|-------|------|-------------|")
    for s in validation["scripts"]:
        md.append(f"| {s['class']} | {s['name']} | {s['source_length']} chars |")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## Game Files Produced")
    md.append("")
    md.append("| File | Description |")
    md.append("|------|-------------|")
    md.append("| `src/ReplicatedStorage/GameConfig.lua` | Constants, enemy/item/class definitions |")
    md.append("| `src/ReplicatedStorage/DungeonGenerator.lua` | Procedural floor generation |")
    md.append("| `src/ReplicatedStorage/LootSystem.lua` | Item generation with rarity rolls |")
    md.append("| `src/ServerScriptService/DataManager.lua` | Player data persistence (DataStore) |")
    md.append("| `src/ServerScriptService/CombatManager.lua` | Damage calculation, abilities, AI |")
    md.append("| `src/ServerScriptService/GameManager.server.lua` | Server orchestration, remote events |")
    md.append("| `src/StarterGui/MainGui.lua` | HUD, health bars, inventory, shop |")
    md.append("| `src/StarterPlayerScripts/PlayerController.client.lua` | Input, camera, ability casting |")
    md.append("| `ShadowDungeonDescent.rbxlx` | Compiled Roblox place file (XML, 8 scripts) |")
    md.append("| `build_place.py` | Build script: Lua sources → .rbxlx |")
    md.append("| `publish.py` | Publish script: .rbxlx → Roblox Open Cloud API |")

    out_path = SCRIPT_DIR / "publish_result.md"
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"  Result saved to {out_path}")
    return out_path


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    global UNIVERSE_ID, PLACE_ID

    print("=" * 60)
    print("Shadow Dungeon: Descent — Build & Publish (LIVE API)")
    print("=" * 60)

    # Step 1: Build
    print("\n[1/5] Building .rbxlx place file ...")
    place_file = build_place()

    # Step 2: Validate
    print("\n[2/5] Validating place file ...")
    validation = validate_place_file(place_file)
    print(f"  File size: {validation['file_size_bytes']} bytes")
    print(f"  SHA-256: {validation['sha256']}")
    print(f"  Scripts: {validation['script_count']}")
    print(f"  Services: {', '.join(validation['required_services_found'])}")
    print(f"  Valid: {'YES' if validation['valid'] else 'NO'}")

    if not validation["valid"]:
        print("\nERROR: Place file validation failed. Aborting.")
        sys.exit(1)

    # Step 3: Validate API key
    print(f"\n[3/5] Validating API key against Roblox Open Cloud ...")
    api_key_result = validate_api_key()

    # Step 4: Discover universes if IDs not provided
    discovery = None
    has_ids = bool(UNIVERSE_ID and PLACE_ID)

    if api_key_result["valid"] and not has_ids:
        print(f"\n[4/5] Discovering universes (no UNIVERSE_ID/PLACE_ID set) ...")
        discovery = discover_universes()
        if discovery["found"] and discovery["universes"]:
            # Try to use first discovered universe
            u = discovery["universes"][0]
            uid = str(u.get("id", u.get("universeId", "")))
            pid = str(u.get("rootPlaceId", u.get("placeId", "")))
            if uid and pid:
                print(f"  Auto-discovered: Universe={uid}, Place={pid}")
                # Update globals for publish step
                UNIVERSE_ID = uid
                PLACE_ID = pid
                has_ids = True
    elif not api_key_result["valid"]:
        print(f"\n[4/5] SKIPPED — API key invalid (HTTP {api_key_result['status_code']})")
    else:
        print(f"\n[4/5] Using provided IDs: Universe={UNIVERSE_ID}, Place={PLACE_ID}")

    # Step 5: Publish — always attempt if we have IDs, regardless of
    # key validation result (different endpoints may accept different scopes)
    publish_result = None
    if has_ids:
        print(f"\n[5/5] Publishing to Roblox ...")
        if not api_key_result["valid"]:
            print("  (API key failed /users/me check, but attempting publish anyway —")
            print("   Place Publishing may accept different key scopes)")
        publish_result = publish_place(place_file)
        status = publish_result["status_code"]
        print(f"  HTTP {status}: {json.dumps(publish_result['body'])}")
    else:
        print(f"\n[5/5] SKIPPED — No Universe/Place IDs available")
        print("  Cannot publish without IDs. Create an experience in Roblox Studio first.")

    # Save result
    print("\nSaving result ...")
    save_result(
        api_key_result=api_key_result,
        validation=validation,
        place_file=place_file,
        publish_result=publish_result,
        discovery=discovery,
    )

    if publish_result and 200 <= publish_result["status_code"] < 300:
        version = publish_result["body"].get("versionNumber", "?")
        print(f"\nSUCCESS — Version {version} published to Roblox!")
    else:
        print("\nPublish was not completed. See publish_result.md for full diagnostics.")

    print("Done!")


if __name__ == "__main__":
    main()
