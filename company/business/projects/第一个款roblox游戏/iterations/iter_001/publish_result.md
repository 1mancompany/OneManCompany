# Roblox Publish Result

**Timestamp**: 2026-03-05T17:34:19.129199
**Mode**: LIVE (Roblox Open Cloud API — real calls, no mock)
**API Key**: `OOd4...` (provided by project owner)
**API Key Valid**: NO — Roblox returned HTTP 401
**Place file**: `TechStartupTycoon.rbxlx` (23455 bytes)
**Place file SHA-256**: `3dea64179153687fd399e4dc6a5f2485578c4771e06f92c4f88b97c544cad274`

## Step 1: API Key Validation

Called `GET https://apis.roblox.com/cloud/v2/users/me` with the provided API key.

- **HTTP Status**: 401
- **Result**: Key rejected by Roblox
- **Response**:
```json
{
  "errors": [
    {
      "code": 0,
      "message": "Invalid API Key"
    }
  ]
}
```

The API key `OOd4ZsDDE0mP` was rejected by the Roblox Open Cloud API with
HTTP 401. This means the key is either expired,
revoked, or was never a valid Roblox Open Cloud API key.

### How to Fix

1. Log in to https://create.roblox.com/dashboard/credentials
2. Create a **new** API key with **universe-places → Write** permission
3. Copy the key immediately (Roblox won't show it again)
4. Set the environment variable: `export ROBLOX_API_KEY="your-new-key"`
5. Re-run: `python publish.py`

## Step 2: Universe & Place IDs (MISSING)

The Roblox Open Cloud API **cannot create new experiences programmatically**.
An experience must first be created via Roblox Studio or the Creator Dashboard.

### How to Get the IDs

1. Open **Roblox Studio** and create a new experience (or use an existing one)
2. Publish it once from Studio to create the Universe/Place on Roblox servers
3. Go to https://create.roblox.com/dashboard/creations
4. Hover over the experience → **⋯** → **Copy Universe ID**
5. Open the experience → place config → note the **Place ID** from the URL
6. Set environment variables:
   ```bash
   export ROBLOX_UNIVERSE_ID="your-universe-id"
   export ROBLOX_PLACE_ID="your-place-id"
   ```

---

## Place File Validation (ALL PASS)

| Check | Result |
|-------|--------|
| File exists | PASS |
| File size | 23455 bytes |
| XML parseable | PASS |
| Total XML items | 14 |
| All required services present | PASS |
| Services found | Workspace, ReplicatedStorage, ServerScriptService, StarterGui, StarterPlayer |
| Script count | 5 |
| Overall valid | PASS |

### Embedded Scripts

| Class | Name | Source Length |
|-------|------|-------------|
| ModuleScript | GameConfig | 1500 chars |
| ModuleScript | DataManager | 1873 chars |
| Script | GameManager | 4765 chars |
| LocalScript | MainGui | 6332 chars |
| LocalScript | ClickHandler | 3815 chars |

---

## Game Files Produced

| File | Description |
|------|-------------|
| `default.project.json` | Rojo project configuration |
| `src/ReplicatedStorage/GameConfig.lua` | Game constants and upgrade definitions |
| `src/ServerScriptService/DataManager.lua` | Player data persistence (DataStore) |
| `src/ServerScriptService/GameManager.server.lua` | Main server logic (click, sell, upgrades, rebirth, auto-code) |
| `src/StarterGui/MainGui.lua` | UI creation (LocalScript) |
| `src/StarterPlayerScripts/ClickHandler.client.lua` | Client-side input handling |
| `TechStartupTycoon.rbxlx` | Compiled Roblox place file (XML) |
| `build_place.py` | Build script: Lua sources → .rbxlx |
| `publish.py` | Publish script: .rbxlx → Roblox Open Cloud API |