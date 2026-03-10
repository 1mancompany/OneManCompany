#!/usr/bin/env python3
"""
Publish Soul Rift .rbxlx to Roblox via Open Cloud API.
Reads API key from .env, discovers universe/place, then publishes.
"""
import os
import json
import base64
import urllib.request
import urllib.error
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RBXLX_PATH = os.path.join(SCRIPT_DIR, "SoulRift.rbxlx")
ENV_PATH = os.path.join(SCRIPT_DIR, "../../../../../../.env")

CLOUD_V2 = "https://apis.roblox.com/cloud/v2"
PUBLISH_V1 = "https://apis.roblox.com/universes/v1"

def load_api_key():
    """Load ROBLOX_CLOUD_API_KEY from .env file."""
    env_path = os.path.normpath(ENV_PATH)
    if not os.path.exists(env_path):
        print(f"ERROR: .env not found at {env_path}")
        sys.exit(1)

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("ROBLOX_CLOUD_API_KEY="):
                key = line.split("=", 1)[1].strip()
                print(f"API Key loaded ({len(key)} chars)")
                return key

    print("ERROR: ROBLOX_CLOUD_API_KEY not found in .env")
    sys.exit(1)

def api_request(method, url, api_key, body=None, content_type=None):
    """Make an API request."""
    headers = {"x-api-key": api_key}
    if content_type:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        print(f"  HTTP {e.code}: {body_text[:500]}")
        try:
            return e.code, json.loads(body_text)
        except:
            return e.code, body_text

def try_decode_api_key(api_key):
    """Try to extract owner/universe info from API key."""
    # The key appears to be base64-encoded JWT
    try:
        # Try decoding as base64
        decoded = base64.b64decode(api_key + "==").decode('utf-8', errors='ignore')
        print(f"Decoded key preview: {decoded[:200]}...")

        # Look for JWT payload
        parts = decoded.split(".")
        if len(parts) >= 2:
            # Try to decode JWT payload
            payload = parts[1]
            # Add padding
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            try:
                payload_data = json.loads(base64.b64decode(payload).decode())
                print(f"JWT payload: {json.dumps(payload_data, indent=2)}")
                owner_id = payload_data.get("ownerId") or payload_data.get("sub")
                if owner_id:
                    print(f"Owner ID: {owner_id}")
                    return owner_id
            except:
                pass
    except:
        pass
    return None

def discover_universe(api_key):
    """Try to discover the universe/place via API."""
    # Try to get user info or list universes
    # Method 1: Try the v2 API to list the user's universes
    print("\nAttempting to discover universe...")

    # The Open Cloud API doesn't have a "list my universes" endpoint directly.
    # We need universe_id. Let's check if we can get it from env.

    # Check for ROBLOX_UNIVERSE_ID in env
    uid = os.environ.get("ROBLOX_UNIVERSE_ID")
    pid = os.environ.get("ROBLOX_PLACE_ID")

    if uid and pid:
        print(f"Found in env: Universe={uid}, Place={pid}")
        return int(uid), int(pid)

    # Check .env file for these
    env_path = os.path.normpath(ENV_PATH)
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("ROBLOX_UNIVERSE_ID="):
                uid = line.split("=", 1)[1].strip()
            elif line.startswith("ROBLOX_PLACE_ID="):
                pid = line.split("=", 1)[1].strip()

    if uid and pid:
        print(f"Found in .env: Universe={uid}, Place={pid}")
        return int(uid), int(pid)

    # Try to decode from API key
    owner_id = try_decode_api_key(api_key)

    # If we have owner_id, try to get their experiences
    if owner_id:
        print(f"\nTrying to list experiences for owner {owner_id}...")
        # Try v2 group/user universes endpoint
        for endpoint in [
            f"https://apis.roblox.com/cloud/v2/users/{owner_id}/universes",
            f"https://apis.roblox.com/cloud/v2/groups/{owner_id}/universes",
        ]:
            status, body = api_request("GET", endpoint, api_key)
            print(f"  {endpoint} -> {status}")
            if status == 200 and isinstance(body, dict):
                universes = body.get("universes", [])
                if universes:
                    u = universes[0]
                    uid = u.get("id", "").split("/")[-1]
                    print(f"  Found universe: {uid}")
                    # Get first place
                    places_url = f"{CLOUD_V2}/universes/{uid}/places"
                    ps, pb = api_request("GET", places_url, api_key)
                    if ps == 200 and isinstance(pb, dict):
                        places = pb.get("places", [])
                        if places:
                            pid = places[0].get("id", "").split("/")[-1]
                            return int(uid), int(pid)

    return None, None

def publish_place(api_key, universe_id, place_id, rbxlx_path):
    """Publish .rbxlx file to Roblox."""
    print(f"\nPublishing to Universe={universe_id}, Place={place_id}...")

    with open(rbxlx_path, 'rb') as f:
        content = f.read()

    print(f"File size: {len(content):,} bytes")

    url = (
        f"{PUBLISH_V1}/{universe_id}/places/{place_id}/versions"
        "?versionType=Published"
    )

    status, body = api_request(
        "POST", url, api_key,
        body=content,
        content_type="application/xml"
    )

    if status == 200:
        version = body.get("versionNumber", "unknown") if isinstance(body, dict) else "unknown"
        print(f"\n✅ PUBLISH SUCCESS!")
        print(f"  Version: {version}")
        print(f"  Game URL: https://www.roblox.com/games/{place_id}")
        return True, version
    else:
        print(f"\n❌ PUBLISH FAILED (HTTP {status})")
        print(f"  Response: {body}")
        return False, None

def main():
    print("=" * 60)
    print("Soul Rift — Roblox Publisher")
    print("=" * 60)

    # Check .rbxlx exists
    if not os.path.exists(RBXLX_PATH):
        print(f"ERROR: {RBXLX_PATH} not found. Run generate_rbxlx.py first.")
        sys.exit(1)

    print(f"Place file: {RBXLX_PATH} ({os.path.getsize(RBXLX_PATH):,} bytes)")

    # Load API key
    api_key = load_api_key()

    # Discover universe/place
    universe_id, place_id = discover_universe(api_key)

    if not universe_id or not place_id:
        print("\n⚠️  Could not auto-discover Universe/Place IDs.")
        print("Please set ROBLOX_UNIVERSE_ID and ROBLOX_PLACE_ID in .env")
        print("Or add roblox_universe_id to employee 00007 profile.yaml")

        # Save what we have as a result
        result = {
            "status": "NEEDS_CONFIG",
            "message": "Universe ID and Place ID required",
            "scripts_ready": 14,
            "rbxlx_generated": True,
            "rbxlx_size": os.path.getsize(RBXLX_PATH),
        }
        with open(os.path.join(SCRIPT_DIR, "publish_result.md"), 'w') as f:
            f.write("# Soul Rift Publish Result\n\n")
            f.write("## Status: NEEDS CONFIGURATION\n\n")
            f.write("All 14 Luau modules are complete and .rbxlx is generated.\n\n")
            f.write("### To publish, configure:\n")
            f.write("1. Add `ROBLOX_UNIVERSE_ID=<your_universe_id>` to `.env`\n")
            f.write("2. Add `ROBLOX_PLACE_ID=<your_place_id>` to `.env`\n")
            f.write("3. Re-run `python3 publish.py`\n\n")
            f.write("### How to get these IDs:\n")
            f.write("1. Go to https://create.roblox.com\n")
            f.write("2. Create a new Experience or use existing\n")
            f.write("3. Universe ID is in the URL: create.roblox.com/dashboard/creations/experiences/<UNIVERSE_ID>\n")
            f.write("4. Place ID is the start place ID shown in the experience settings\n")
        print("\nSaved publish_result.md with instructions.")
        return

    # Publish
    success, version = publish_place(api_key, universe_id, place_id, RBXLX_PATH)

    # Save result
    with open(os.path.join(SCRIPT_DIR, "publish_result.md"), 'w') as f:
        f.write("# Soul Rift Publish Result\n\n")
        if success:
            f.write(f"## Status: ✅ SUCCESS\n\n")
            f.write(f"- **Version**: {version}\n")
            f.write(f"- **Universe ID**: {universe_id}\n")
            f.write(f"- **Place ID**: {place_id}\n")
            f.write(f"- **Game URL**: https://www.roblox.com/games/{place_id}\n")
            f.write(f"- **Scripts**: 14 Luau modules\n")
            f.write(f"- **File Size**: {os.path.getsize(RBXLX_PATH):,} bytes\n")
        else:
            f.write(f"## Status: ❌ FAILED\n\n")
            f.write(f"- **Universe ID**: {universe_id}\n")
            f.write(f"- **Place ID**: {place_id}\n")
            f.write("- Check API key permissions and try again\n")

    print(f"\nSaved publish_result.md")

if __name__ == "__main__":
    main()
