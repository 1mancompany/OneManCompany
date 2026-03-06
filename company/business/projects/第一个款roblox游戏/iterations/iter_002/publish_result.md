# Roblox Publish Result

**Timestamp**: 2026-03-06T13:15:59.909823
**Mode**: LIVE (Roblox Open Cloud API)
**Place file**: `TechStartupTycoon.rbxlx` (20329 bytes)
**Place file SHA-256**: `cc921c0d8b62ad839ca51f7bce111ef6cee24a023dd6c5d83070ec5339a53144`
**Target Universe**: 9838675013
**Target Place**: 118831023221258
**HTTP Status**: 200
**Success**: YES

## Published Successfully

- **Version Number**: 18
- **Game URL**: https://www.roblox.com/games/118831023221258/TechStartupTycoon
- **Roblox Account**: fredyuzx (User ID: 10617330902)
- **API Response**:
```json
{
  "versionNumber": 18
}
```

---

## Place File Validation

| Check | Result |
|-------|--------|
| File exists | PASS |
| File size | 20329 bytes |
| XML parseable | PASS |
| Total XML items | 12 |
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