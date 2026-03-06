# Roblox Publish Result — Shadow Dungeon: Descent

**Timestamp**: 2026-03-06T12:05:38.827452+00:00
**Mode**: LIVE (Roblox Open Cloud API — ZERO MOCK)
**Place file**: `ShadowDungeonDescent.rbxlx` (80,620 bytes)
**SHA-256**: `3eabc50292becc99443bf57e458320bcc1d383906d938a1d0419648264a3c031`
**Universe ID**: 9830386110
**Place ID**: 80098363530646
**Owner ID**: 10617330902
**HTTP Status**: 401
**Success**: NO

## Publish Failed

### Root Cause: API Key Expired

- KEY EXPIRED at 2026-03-05T18:16:51+00:00 (17.8 hours ago). Generate a fresh key at https://create.roblox.com/dashboard/credentials
- Roblox Open Cloud API keys contain a JWT token with a **1-hour expiry**
- The provided key was issued at the time shown above and has since expired

### How to Fix

1. Go to https://create.roblox.com/dashboard/credentials
2. **Regenerate** the API key (or create a new one)
3. Ensure it has **universe-places → Write** permission for the target universe
4. Run: `python publish.py --api-key 'NEW_KEY'`

- **HTTP Status**: 401
- **API Response**:
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

---

## Roblox Account — Discovered Games (Public API)

Owner user ID `10617330902` has **2** existing game(s):

| # | Name | Universe ID | Place ID | Visits | Created |
|---|------|-------------|----------|--------|---------|
| 1 | TechStartupTycoon | 9838675013 | 118831023221258 | 0 | 2026-03-05 |
| 2 | fredyuzx's Place | 9830386110 | 80098363530646 | 0 | 2026-03-03 |

**Selected target**: universe=9830386110, place=80098363530646
**Selection reason**: Selected empty default place: 'fredyuzx's Place' (universe=9830386110, place=80098363530646)

---

## Place File Validation

| Check | Result |
|-------|--------|
| File exists | PASS |
| File size | 80,620 bytes |
| XML parseable | PASS |
| Total XML items | 17 |
| All required services | PASS |
| Services found | Workspace, ReplicatedStorage, ServerScriptService, StarterGui, StarterPlayer |
| Script count | 8 |
| Overall valid | PASS |

### Embedded Scripts

| Class | Name | Source Length |
|-------|------|-------------|
| ModuleScript | GameConfig | 5,553 chars |
| ModuleScript | DungeonGenerator | 6,967 chars |
| ModuleScript | LootSystem | 3,756 chars |
| ModuleScript | DataManager | 4,935 chars |
| ModuleScript | CombatManager | 7,611 chars |
| Script | GameManager | 19,056 chars |
| LocalScript | MainGui | 14,653 chars |
| LocalScript | PlayerController | 6,648 chars |

---

## Full API Request/Response Log

```
============================================================
Shadow Dungeon: Descent — Build & Publish
Mode: REAL API (zero mock)
============================================================

[1/5] Validating API key ...
  Key length: 968 chars
  Owner ID: 10617330902
  Status: KEY EXPIRED at 2026-03-05T18:16:51+00:00 (17.8 hours ago). Generate a fresh key at https://create.roblox.com/dashboard/credentials

  WARNING: KEY EXPIRED at 2026-03-05T18:16:51+00:00 (17.8 hours ago). Generate a fresh key at https://create.roblox.com/dashboard/credentials
  Continuing anyway to demonstrate the full pipeline...

[2/5] Discovering universe & place IDs ...
  >> GET https://games.roblox.com/v2/users/10617330902/games?sortOrder=Desc&limit=50
  << HTTP 200: {"previousPageCursor": null, "nextPageCursor": null, "data": [{"id": 9838675013, "name": "TechStartupTycoon", "description": "From Garage to Global Tech Empire\n\nEver dreamed of building the next big tech company? In **Tech Startup Tycoon**, you start with nothing but a keyboard and a dream. Write 
  Selected empty default place: 'fredyuzx's Place' (universe=9830386110, place=80098363530646)
  Universe: 9830386110, Place: 80098363530646

[3/5] Building .rbxlx place file ...

[4/5] Validating place file ...
  Size: 80,620 bytes
  SHA-256: 3eabc50292becc99443bf57e458320bcc1d383906d938a1d0419648264a3c031
  XML items: 17
  Scripts: 8
  Services: Workspace, ReplicatedStorage, ServerScriptService, StarterGui, StarterPlayer
  Valid: YES

[5/5] Publishing to Roblox Open Cloud API ...

  Uploading 80620 bytes to Roblox Open Cloud ...
  Target: universe=9830386110, place=80098363530646
  >> POST https://apis.roblox.com/universes/v1/9830386110/places/80098363530646/versions?versionType=Published
  << HTTP 401: {"errors": [{"code": 0, "message": "Invalid API Key"}]}

Saving results ...
```

---

## Game Source Files

| File | Description |
|------|-------------|
| `game_design.md` | Full game design document |
| `default.project.json` | Rojo project configuration |
| `src/ReplicatedStorage/GameConfig.lua` | Game constants, class/enemy/item definitions |
| `src/ReplicatedStorage/DungeonGenerator.lua` | Procedural dungeon floor generation |
| `src/ReplicatedStorage/LootSystem.lua` | Item drops with weighted rarity rolls |
| `src/ServerScriptService/DataManager.lua` | Player data persistence (DataStore) |
| `src/ServerScriptService/CombatManager.lua` | Combat logic, enemy AI, abilities |
| `src/ServerScriptService/GameManager.server.lua` | Main server orchestration (19KB) |
| `src/StarterGui/MainGui.lua` | Full UI: HUD, class select, shop, game over |
| `src/StarterPlayerScripts/PlayerController.client.lua` | Client input + enemy visuals |
| `ShadowDungeonDescent.rbxlx` | Compiled Roblox place file (XML) |
| `build_place.py` | Build script: Lua sources → .rbxlx |
| `publish.py` | Publish script: .rbxlx → Roblox Open Cloud API |