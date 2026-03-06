# Roblox Game Design Document

## 1. Market Analysis
Current popular Roblox games often revolve around:
- **Simulator / Tycoon**: Addictive progression loops, easy to monetize.
- **Obby (Obstacle Course)**: Easy to create, high player retention if challenging enough.
- **Roleplay (RP)**: High social interaction, requires significant initial content.
- **Anime Fighters/Gacha**: High monetization potential, requires good combat and visual effects.

**Selected Genre**: Simulator / Tycoon
**Reason**: Tycoon games have a proven track record on Roblox for steady engagement and monetization. They are relatively straightforward to implement compared to complex RPGs or fast-paced shooters.

## 2. Game Concept: "Tech Startup Tycoon"
Players start in a garage and build their way up to a massive tech campus.

### Core Loop
1. Generate "Code" (clicker mechanic).
2. Sell "Code" for "Cash".
3. Use "Cash" to buy upgrades (better computers, hire NPCs to code automatically, decorations).
4. Rebirth (prestige) to gain permanent multipliers.

## 3. Implementation Plan
- **Phase 1: Basic Mechanics**: Clicker, currency system, simple upgrade path.
- **Phase 2: Visuals & Environment**: Basic tycoon layout, droppers/upgraders in visual form.
- **Phase 3: Monetization**: Gamepasses (Auto-clicker, 2x Cash, VIP area).
- **Phase 4: Polish & Publish**: UI, sounds, testing, release to Roblox.

## 4. Current Status

### Development — COMPLETE
All game scripts implemented and compiled into `TechStartupTycoon.rbxlx` (23 KB):
- `GameConfig.lua` — Constants, 7 upgrade tiers, rebirth multipliers
- `DataManager.lua` — Player data persistence via Roblox DataStore
- `GameManager.server.lua` — Server-side logic (click, sell, upgrades, rebirth, auto-code)
- `MainGui.lua` — Full UI (HUD, shop panels, rebirth dialog)
- `ClickHandler.client.lua` — Client-side input handling

### Build Pipeline — COMPLETE
- `build_place.py` compiles Lua sources → `.rbxlx` XML place file
- `publish.py` handles Roblox Open Cloud API publishing (with credential validation)

### Publishing — COMPLETE
- **Roblox Account**: fredyuzx (User ID: 10617330902)
- **Target**: Universe `9838675013`, Place `118831023221258`
- **Game URL**: https://www.roblox.com/games/118831023221258/TechStartupTycoon
- Real API call (2026-03-06): `POST /universes/v1/9838675013/places/118831023221258/versions` → **HTTP 200**
- **Published Version**: 18
- Place file: 20,329 bytes, 5 scripts, all validations PASS
