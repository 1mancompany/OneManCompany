# Soul Rift — iter_002 Publish Result

## PUBLISH STATUS: SUCCESS

- **Version**: 5
- **Universe ID**: 9830386110
- **Place ID**: 80098363530646
- **Game URL**: https://www.roblox.com/games/80098363530646
- **Scripts**: 15 Luau modules (was 14 — added ClientMain bootstrap)
- **File Size**: 141,763 bytes

## Bugs Fixed (CEO Feedback: "Nothing happens after joining")

### Root Cause 1: GameManager never ran
- `generate_rbxlx.py` created `GameManager.server` as name, but checked `name == "GameManager"` — mismatch meant it was a `ModuleScript` (never executes) instead of a `Script`
- **Fix**: Strip `.server`/`.client` suffixes from names before generating XML; use suffix to determine script class

### Root Cause 2: Client scripts never initialized
- `UIManager.client.luau`, `InputController.client.luau`, `CombatClient.client.luau` were all `LocalScript` but structured as modules (define table + return) — `init()` never called
- **Fix**: Renamed to ModuleScripts; created `ClientMain.client.luau` bootstrap LocalScript that requires and initializes all three in correct order

### Root Cause 3: No way to start a dungeon run
- Game required `StartRunEvent` from client but no UI existed to trigger it
- **Fix**: Added full welcome screen with "START DUNGEON RUN" button that fires `StartRunEvent`

### Root Cause 4: No control instructions
- Players had no idea about keyboard/touch controls
- **Fix**: Added controls overlay showing all keybinds (Q/E/R skills, F ultimate, Space dodge, T auto-attack) on welcome screen + persistent hint bar

### Additional Fix: Missing GameConfig.DEFAULT_CRIT_RATE
- `CombatSystem` referenced `GameConfig.DEFAULT_CRIT_RATE` but it wasn't defined
- **Fix**: Added `DEFAULT_CRIT_RATE = 0.05` to GameConfig

### Additional Fix: XML format compatibility
- Rewrote `generate_rbxlx.py` to use simplified XML format (matching iter_001's proven working format) — simpler referents, `<string name="Source">` instead of `<ProtectedString>`, no XML declaration
