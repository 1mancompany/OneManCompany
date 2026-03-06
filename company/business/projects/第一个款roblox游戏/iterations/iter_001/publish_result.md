# Roblox Publish Result

**PUBLISH STATUS: SUCCESS — Version 17**

**Timestamp**: 2026-03-06T13:04:38.766962
**Mode**: LIVE (Real Roblox Open Cloud API calls)
**API Key**: `OOd4ZsDDE0mP...MlJPdw==`
**Place file**: `ShadowDungeonDescent.rbxlx` (74426 bytes)
**Place file SHA-256**: `9b8df936e8e61ee942f397bc792cb19470b8eaf578d5af74e41711e0e416f3c1`
**Universe ID**: 9838675013
**Place ID**: 118831023221258

## API Key Pre-check (informational)

*Note: This check uses `GET /cloud/v2/users/me` which has a different
auth scope than the Place Publishing API. An HTTP 400 here does NOT
mean the publish will fail — the publish endpoint accepts this key.*

- Endpoint: `GET https://apis.roblox.com/cloud/v2/users/me`
- HTTP Status: 400
- Response: `{"code": "INVALID_ARGUMENT", "message": "Invalid User ID in the request."}`

## Publish Result

```
POST https://apis.roblox.com/universes/v1/9838675013/places/118831023221258/versions?versionType=Published
x-api-key: OOd4ZsDDE0mP...MlJPdw==
Content-Type: application/xml
Content-Length: 74426
```

**HTTP Status**: 200
```json
{
  "versionNumber": 17
}
```

**Version 17 published successfully!**

---

## Place File Validation

| Check | Result |
|-------|--------|
| File exists | PASS |
| File size | 74426 bytes |
| XML parseable | PASS |
| Total XML items | 15 |
| All required services | PASS |
| Services found | Workspace, ReplicatedStorage, ServerScriptService, StarterGui, StarterPlayer |
| Script count | 8 |
| Overall valid | PASS |

### Embedded Scripts

| Class | Name | Source Length |
|-------|------|-------------|
| ModuleScript | GameConfig | 5553 chars |
| ModuleScript | DungeonGenerator | 6967 chars |
| ModuleScript | LootSystem | 3756 chars |
| ModuleScript | DataManager | 4935 chars |
| ModuleScript | CombatManager | 7611 chars |
| Script | GameManager | 19056 chars |
| LocalScript | MainGui | 14653 chars |
| LocalScript | PlayerController | 6648 chars |

---

## Game Files Produced

| File | Description |
|------|-------------|
| `src/ReplicatedStorage/GameConfig.lua` | Constants, enemy/item/class definitions |
| `src/ReplicatedStorage/DungeonGenerator.lua` | Procedural floor generation |
| `src/ReplicatedStorage/LootSystem.lua` | Item generation with rarity rolls |
| `src/ServerScriptService/DataManager.lua` | Player data persistence (DataStore) |
| `src/ServerScriptService/CombatManager.lua` | Damage calculation, abilities, AI |
| `src/ServerScriptService/GameManager.server.lua` | Server orchestration, remote events |
| `src/StarterGui/MainGui.lua` | HUD, health bars, inventory, shop |
| `src/StarterPlayerScripts/PlayerController.client.lua` | Input, camera, ability casting |
| `ShadowDungeonDescent.rbxlx` | Compiled Roblox place file (XML, 8 scripts) |
| `build_place.py` | Build script: Lua sources → .rbxlx |
| `publish.py` | Publish script: .rbxlx → Roblox Open Cloud API |