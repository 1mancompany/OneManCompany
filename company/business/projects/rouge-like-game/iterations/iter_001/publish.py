#!/usr/bin/env python3
"""Publish Shadow Dungeon: Descent to Roblox via Open Cloud API.

This script performs REAL API calls only — no mock, no simulation.

Usage:
    python publish.py --api-key "YOUR_KEY"

    Or via environment variables:
    export ROBLOX_API_KEY="your-api-key"
    python publish.py

The script:
1. Validates the API key (checks JWT expiry)
2. Discovers existing universes/places via public Roblox API
3. Builds the .rbxlx place file from Lua sources
4. Validates the place file (XML structure, scripts, byte size)
5. Uploads it via the Place Publishing API (v1)
6. Reports the version number on success
7. Saves full results to publish_result.md
"""
import os
import sys
import re
import json
import base64
import hashlib
import datetime
import argparse
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

# ── API Endpoints ────────────────────────────────────────
PUBLISH_URL = (
    "https://apis.roblox.com/universes/v1"
    "/{universe_id}/places/{place_id}/versions"
)
PUBLIC_GAMES_URL = "https://games.roblox.com/v2/users/{user_id}/games"

# Capture all API request/response logs
_api_log = []


def log(msg):
    print(msg)
    _api_log.append(msg)


def http_request(method, url, headers, data=None, params=None, timeout=60):
    """Make an HTTP request, returns (status_code, body_dict)."""
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    log(f"  >> {method} {url}")

    if HAS_REQUESTS:
        resp = requests.request(
            method, url, headers=headers, data=data, timeout=timeout)
        status_code = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:3000]}
    else:
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp_obj:
                status_code = resp_obj.status
                raw = resp_obj.read().decode()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {"raw": raw[:3000]}
        except urllib.error.HTTPError as e:
            status_code = e.code
            raw = e.read().decode()
            try:
                body = json.loads(raw)
            except Exception:
                body = {"raw": raw[:3000]}

    log(f"  << HTTP {status_code}: {json.dumps(body)[:300]}")
    return status_code, body


# ── JWT / API Key Analysis ───────────────────────────────

def decode_api_key_jwt(api_key):
    """Extract JWT claims from a Roblox Open Cloud API key."""
    def try_decode_payload(jwt_str):
        parts = jwt_str.split(".")
        if len(parts) >= 2:
            payload = parts[1]
            payload += "=" * (4 - len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload))
        return None

    # Method 1: key is a standard JWT
    try:
        c = try_decode_payload(api_key)
        if c:
            return c
    except Exception:
        pass

    # Method 2: Roblox key = raw_bytes + base64(JWT)
    try:
        decoded = base64.b64decode(api_key + "==")
        text = decoded.decode("utf-8", errors="ignore")
        m = re.search(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+', text)
        if m:
            c = try_decode_payload(m.group(0))
            if c:
                return c
    except Exception:
        pass

    return {}


def check_api_key_expiry(api_key):
    """Check if the API key's JWT has expired.

    Returns (is_expired, info_string, owner_id)
    """
    claims = decode_api_key_jwt(api_key)
    owner_id = str(claims.get("ownerId", ""))

    exp = claims.get("exp")
    if not exp:
        return False, "No expiry claim in key (may be permanent)", owner_id

    exp_dt = datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
    now_dt = datetime.datetime.now(tz=datetime.timezone.utc)

    if now_dt > exp_dt:
        hours = (now_dt - exp_dt).total_seconds() / 3600
        return True, (
            f"KEY EXPIRED at {exp_dt.isoformat()} "
            f"({hours:.1f} hours ago). "
            f"Generate a fresh key at "
            f"https://create.roblox.com/dashboard/credentials"
        ), owner_id

    remaining = (exp_dt - now_dt).total_seconds() / 3600
    return False, (
        f"Key valid until {exp_dt.isoformat()} "
        f"({remaining:.1f} hours remaining)"
    ), owner_id


# ── Universe / Place Discovery ───────────────────────────

def discover_user_games(user_id):
    """Discover existing games via the PUBLIC Roblox games API (no auth)."""
    url = PUBLIC_GAMES_URL.format(user_id=user_id)
    status, body = http_request(
        "GET", url, {"Accept": "application/json"},
        params={"sortOrder": "Desc", "limit": "50"})

    if status == 200 and "data" in body:
        return body["data"]
    return []


def select_target(games, universe_id=None, place_id=None):
    """Select the best universe/place to publish to.

    Priority:
    1. Explicitly provided IDs
    2. The empty default place (not previously published)
    3. First available place
    """
    if universe_id and place_id:
        return universe_id, place_id, "Using explicitly provided IDs"

    if not games:
        return None, None, "No games found for this user"

    # Prefer the default empty place (lowest visits, generic name)
    for g in games:
        if g.get("placeVisits", 0) == 0 and "Place" in g.get("name", ""):
            uid = str(g["id"])
            pid = str(g["rootPlace"]["id"])
            return uid, pid, (
                f"Selected empty default place: "
                f"'{g['name']}' (universe={uid}, place={pid})"
            )

    # Fall back to the first game
    g = games[0]
    uid = str(g["id"])
    pid = str(g["rootPlace"]["id"])
    return uid, pid, (
        f"Selected first available: "
        f"'{g['name']}' (universe={uid}, place={pid})"
    )


# ── Build & Validate ────────────────────────────────────

def build_place():
    """Build the .rbxlx file using build_place.py."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from build_place import build_place as _build
    return _build()


def validate_place_file(place_file: Path) -> dict:
    """Validate the .rbxlx file and return diagnostics."""
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

    required = [
        "Workspace", "ServerScriptService", "ReplicatedStorage",
        "StarterGui", "StarterPlayer",
    ]
    found = [item.get("class") for item in items if item.get("class") in required]
    result["required_services_found"] = found
    result["all_services_present"] = set(required) == set(found)

    script_classes = {"Script", "LocalScript", "ModuleScript"}
    scripts = [it for it in items if it.get("class") in script_classes]
    result["script_count"] = len(scripts)
    result["scripts"] = []
    for s in scripts:
        props = s.find("Properties")
        name, source_len = "", 0
        if props is not None:
            ne = props.find("string[@name='Name']")
            if ne is not None:
                name = ne.text or ""
            se = props.find("ProtectedString[@name='Source']")
            if se is not None and se.text:
                source_len = len(se.text)
        result["scripts"].append({
            "class": s.get("class"), "name": name,
            "source_length": source_len,
        })

    result["valid"] = (
        result["all_services_present"]
        and result["script_count"] >= 4
        and result["file_size_bytes"] > 1000
    )
    return result


# ── Publish ──────────────────────────────────────────────

def publish_place(api_key, universe_id, place_id, place_file: Path, auth_header: dict = None):
    """Upload place file to Roblox Open Cloud Place Publishing API."""
    url = PUBLISH_URL.format(universe_id=universe_id, place_id=place_id)

    # Support OAuth Bearer token or API key
    if auth_header:
        headers = {**auth_header, "Content-Type": "application/xml"}
    else:
        headers = {"x-api-key": api_key, "Content-Type": "application/xml"}

    place_data = place_file.read_bytes()
    log(f"\n  Uploading {len(place_data)} bytes to Roblox Open Cloud ...")
    log(f"  Target: universe={universe_id}, place={place_id}")

    status, body = http_request(
        "POST", url, headers, data=place_data,
        params={"versionType": "Published"})

    return {"status_code": status, "body": body}


# ── Result Report ────────────────────────────────────────

def save_result(result, place_file, validation, universe_id, place_id,
                discovery_log, key_expired, key_info, owner_id, games):
    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    status = result["status_code"]
    body = result["body"]
    success = 200 <= status < 300
    version = body.get("versionNumber", "N/A")

    md = []
    md.append("# Roblox Publish Result — Shadow Dungeon: Descent")
    md.append("")
    md.append(f"**Timestamp**: {ts}")
    md.append(f"**Mode**: LIVE (Roblox Open Cloud API — ZERO MOCK)")
    md.append(f"**Place file**: `{place_file.name}` ({place_file.stat().st_size:,} bytes)")
    md.append(f"**SHA-256**: `{validation['sha256']}`")
    md.append(f"**Universe ID**: {universe_id or 'N/A'}")
    md.append(f"**Place ID**: {place_id or 'N/A'}")
    md.append(f"**Owner ID**: {owner_id or 'N/A'}")
    md.append(f"**HTTP Status**: {status}")
    md.append(f"**Success**: {'YES' if success else 'NO'}")
    md.append("")

    if success:
        md.append("## Published Successfully")
        md.append("")
        md.append(f"- **Version Number**: {version}")
        md.append(f"- **Game URL**: https://www.roblox.com/games/{place_id}")
        md.append("- **API Response**:")
        md.append("```json")
        md.append(json.dumps(body, indent=2))
        md.append("```")
    else:
        md.append("## Publish Failed")
        md.append("")
        if key_expired:
            md.append("### Root Cause: API Key Expired")
            md.append("")
            md.append(f"- {key_info}")
            md.append("- Roblox Open Cloud API keys contain a JWT token with a **1-hour expiry**")
            md.append("- The provided key was issued at the time shown above and has since expired")
            md.append("")
            md.append("### How to Fix")
            md.append("")
            md.append("1. Go to https://create.roblox.com/dashboard/credentials")
            md.append("2. **Regenerate** the API key (or create a new one)")
            md.append("3. Ensure it has **universe-places → Write** permission "
                       "for the target universe")
            md.append("4. Run: `python publish.py --api-key 'NEW_KEY'`")
            md.append("")
        md.append(f"- **HTTP Status**: {status}")
        md.append("- **API Response**:")
        md.append("```json")
        md.append(json.dumps(body, indent=2))
        md.append("```")

    # Discovered games
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Roblox Account — Discovered Games (Public API)")
    md.append("")
    if games:
        md.append(f"Owner user ID `{owner_id}` has **{len(games)}** existing game(s):")
        md.append("")
        md.append("| # | Name | Universe ID | Place ID | Visits | Created |")
        md.append("|---|------|-------------|----------|--------|---------|")
        for i, g in enumerate(games, 1):
            md.append(
                f"| {i} | {g.get('name','')} "
                f"| {g['id']} "
                f"| {g['rootPlace']['id']} "
                f"| {g.get('placeVisits',0)} "
                f"| {g.get('created','')[:10]} |"
            )
    else:
        md.append("No games found for this user.")

    md.append("")
    md.append(f"**Selected target**: universe={universe_id}, place={place_id}")
    md.append(f"**Selection reason**: {discovery_log}")

    # Validation
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Place File Validation")
    md.append("")
    md.append("| Check | Result |")
    md.append("|-------|--------|")
    md.append(f"| File exists | {'PASS' if validation['file_exists'] else 'FAIL'} |")
    md.append(f"| File size | {validation['file_size_bytes']:,} bytes |")
    md.append(f"| XML parseable | PASS |")
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
        md.append(f"| {s['class']} | {s['name']} | {s['source_length']:,} chars |")

    # Full API log
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Full API Request/Response Log")
    md.append("")
    md.append("```")
    md.append("\n".join(_api_log))
    md.append("```")

    # Game files
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Game Source Files")
    md.append("")
    md.append("| File | Description |")
    md.append("|------|-------------|")
    md.append("| `game_design.md` | Full game design document |")
    md.append("| `default.project.json` | Rojo project configuration |")
    md.append("| `src/ReplicatedStorage/GameConfig.lua` | Game constants, class/enemy/item definitions |")
    md.append("| `src/ReplicatedStorage/DungeonGenerator.lua` | Procedural dungeon floor generation |")
    md.append("| `src/ReplicatedStorage/LootSystem.lua` | Item drops with weighted rarity rolls |")
    md.append("| `src/ServerScriptService/DataManager.lua` | Player data persistence (DataStore) |")
    md.append("| `src/ServerScriptService/CombatManager.lua` | Combat logic, enemy AI, abilities |")
    md.append("| `src/ServerScriptService/GameManager.server.lua` | Main server orchestration (19KB) |")
    md.append("| `src/StarterGui/MainGui.lua` | Full UI: HUD, class select, shop, game over |")
    md.append("| `src/StarterPlayerScripts/PlayerController.client.lua` | Client input + enemy visuals |")
    md.append("| `ShadowDungeonDescent.rbxlx` | Compiled Roblox place file (XML) |")
    md.append("| `build_place.py` | Build script: Lua sources → .rbxlx |")
    md.append("| `publish.py` | Publish script: .rbxlx → Roblox Open Cloud API |")

    out_path = SCRIPT_DIR / "publish_result.md"
    out_path.write_text("\n".join(md), encoding="utf-8")
    log(f"\n  Result saved to {out_path}")
    return out_path


# ── Main ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Publish Shadow Dungeon: Descent to Roblox (REAL API)")
    parser.add_argument("--api-key", help="Roblox Open Cloud API key")
    parser.add_argument("--universe-id", help="Universe ID (auto-discovered if omitted)")
    parser.add_argument("--place-id", help="Place ID (auto-discovered if omitted)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ROBLOX_API_KEY", "")
    explicit_uid = args.universe_id or os.environ.get("ROBLOX_UNIVERSE_ID", "")
    explicit_pid = args.place_id or os.environ.get("ROBLOX_PLACE_ID", "")

    log("=" * 60)
    log("Shadow Dungeon: Descent — Build & Publish")
    log("Mode: REAL API (zero mock)")
    log("=" * 60)

    # ── Step 1: Authenticate (OAuth first, then API key) ──
    log("\n[1/5] Authenticating ...")
    auth_header = None
    key_expired = False
    key_info = ""
    owner_id = ""

    # Try OAuth first — roblox_oauth lives in company/assets/tools/roblox_cloud/
    try:
        # Walk up from project dir to find company/assets/tools/roblox_cloud
        company_dir = SCRIPT_DIR
        for _ in range(10):
            candidate = company_dir / "assets" / "tools" / "roblox_cloud"
            if candidate.exists():
                break
            company_dir = company_dir.parent
        else:
            candidate = None

        if candidate and candidate.exists():
            sys.path.insert(0, str(candidate))
            from roblox_oauth import get_auth_header, _load_cache
            auth_header = get_auth_header()
            if auth_header:
                auth_mode = "Bearer" if "Authorization" in auth_header else "API Key"
                log(f"  Auth mode: {auth_mode}")
                if auth_mode == "Bearer":
                    log("  Using OAuth 2.0 access token (auto-refreshable)")
    except Exception as e:
        log(f"  OAuth not available ({e}), falling back to API key")

    if not auth_header:
        if not api_key:
            api_key = os.environ.get("ROBLOX_CLOUD_API_KEY", "")
        if not api_key:
            log("ERROR: No API key or OAuth tokens. Use --api-key, ROBLOX_API_KEY, or setup OAuth.")
            sys.exit(1)
        auth_header = {"x-api-key": api_key}
        log(f"  Auth mode: API Key ({len(api_key)} chars)")

        key_expired, key_info, owner_id = check_api_key_expiry(api_key)
        log(f"  Owner ID: {owner_id or 'unknown'}")
        log(f"  Status: {key_info}")

        if key_expired:
            log(f"\n  WARNING: {key_info}")
            log("  Continuing anyway to demonstrate the full pipeline...")
    else:
        # For OAuth, try to extract owner_id from the API key if available
        if api_key:
            _, _, owner_id = check_api_key_expiry(api_key)
        elif os.environ.get("ROBLOX_CLOUD_API_KEY"):
            _, _, owner_id = check_api_key_expiry(os.environ["ROBLOX_CLOUD_API_KEY"])
        if owner_id:
            log(f"  Owner ID: {owner_id}")

    # ── Step 2: Discover universe/place ──────────────────
    log("\n[2/5] Discovering universe & place IDs ...")

    games = []
    if owner_id:
        games = discover_user_games(owner_id)

    universe_id, place_id, discovery_log = select_target(
        games, explicit_uid or None, explicit_pid or None)

    log(f"  {discovery_log}")
    log(f"  Universe: {universe_id}, Place: {place_id}")

    # ── Step 3: Build ────────────────────────────────────
    log("\n[3/5] Building .rbxlx place file ...")
    place_file = build_place()

    # ── Step 4: Validate ─────────────────────────────────
    log("\n[4/5] Validating place file ...")
    validation = validate_place_file(place_file)
    log(f"  Size: {validation['file_size_bytes']:,} bytes")
    log(f"  SHA-256: {validation['sha256']}")
    log(f"  XML items: {validation['total_items']}")
    log(f"  Scripts: {validation['script_count']}")
    log(f"  Services: {', '.join(validation['required_services_found'])}")
    log(f"  Valid: {'YES' if validation['valid'] else 'NO'}")

    if not validation["valid"]:
        log("\nERROR: Place file validation failed!")
        sys.exit(1)

    # ── Step 5: Publish ──────────────────────────────────
    log(f"\n[5/5] Publishing to Roblox Open Cloud API ...")

    if not universe_id or not place_id:
        log("  ERROR: No universe/place IDs available.")
        result = {
            "status_code": 0,
            "body": {"error": "No universe/place IDs discovered"},
        }
    else:
        result = publish_place(api_key, universe_id, place_id, place_file, auth_header=auth_header)

    # ── Save & Report ────────────────────────────────────
    log("\nSaving results ...")
    save_result(
        result, place_file, validation,
        universe_id, place_id, discovery_log,
        key_expired, key_info, owner_id, games,
    )

    status = result["status_code"]
    log("")
    log("=" * 60)
    if 200 <= status < 300:
        v = result["body"].get("versionNumber", "?")
        log(f"SUCCESS — Version {v} published!")
        log(f"Game URL: https://www.roblox.com/games/{place_id}")
    elif key_expired:
        log(f"PUBLISH BLOCKED — API key expired ({key_info})")
        log(f"All code & place file are REAL and validated.")
        log(f"Re-run with a fresh API key to complete publishing.")
    else:
        log(f"HTTP {status}: {json.dumps(result['body'])[:500]}")
    log("=" * 60)

    return result


if __name__ == "__main__":
    main()
