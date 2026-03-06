# Shadow Dungeon: Descent — Game Design Document

## 1. Market Analysis (March 2026)

### Roblox Top Genres
| Genre | Examples | Daily Active Users |
|-------|---------|-------------------|
| Simulator/Tycoon | Grow a Garden, Brookhaven | 500K–2M |
| RPG/Dungeon | Dungeon Quest, Deepwoken, World//Zero | 100K–500K |
| Horror/Procedural | Doors, Pressure | 200K–800K |
| Anime Fighters | Blox Fruits, Anime Defenders | 300K–1M |

### Why Roguelike?
- **"Doors"** proved that procedural room-based gameplay drives massive engagement (2B+ visits)
- **"Dungeon Quest"** showed loot-driven dungeon crawlers retain players long-term
- **Roguelike renaissance** in mainstream gaming (Hades 2, Balatro) creates cross-platform demand
- **High replay value** from procedural generation keeps players coming back
- **Low asset requirement** — gameplay-driven, not art-driven

### Competitive Gap
Most Roblox dungeon games are either pure MMORPGs (heavy grind) or horror walks (no combat depth).
A fast-paced roguelike with class-based combat + meta-progression fills an underserved niche.

## 2. Game Concept: "Shadow Dungeon: Descent"

**Tagline**: Choose your class. Clear the floor. Don't die.

Players descend through procedurally generated dungeon floors, fighting enemies, collecting loot,
and facing bosses every 5 floors. Death sends you back to floor 1, but you keep **Soul Gems**
earned during the run, which unlock permanent upgrades.

### Core Loop
1. **Select Class** → Warrior / Mage / Archer (each with unique abilities)
2. **Enter Dungeon** → Procedurally generated rooms per floor
3. **Fight Enemies** → Real-time combat with abilities on cooldown
4. **Collect Loot** → Weapons, armor, potions drop from enemies
5. **Clear Floor** → Defeat all enemies to unlock stairs to next floor
6. **Boss Fight** → Every 5th floor has a boss with unique mechanics
7. **Die or Ascend** → Death resets run; Soul Gems persist for permanent upgrades
8. **Meta-Progression** → Spend Soul Gems on permanent stat boosts between runs

## 3. Classes

| Class | HP | ATK | DEF | Speed | Primary Ability | Ultimate |
|-------|-----|-----|-----|-------|----------------|----------|
| Warrior | 150 | 15 | 12 | 14 | Shield Bash (stun) | Berserker Rage (2x ATK, 10s) |
| Mage | 80 | 25 | 5 | 12 | Fireball (AoE) | Arcane Storm (massive AoE) |
| Archer | 100 | 20 | 8 | 18 | Power Shot (pierce) | Arrow Rain (AoE) |

## 4. Enemies

| Enemy | HP | ATK | Floor Range | Special |
|-------|-----|-----|-------------|---------|
| Skeleton | 30 | 5 | 1–5 | None |
| Zombie | 50 | 8 | 1–10 | Slow on hit |
| Dark Knight | 80 | 12 | 5–15 | Block (50% damage reduction) |
| Shadow Mage | 60 | 18 | 8–20 | Teleport, ranged attack |
| Fire Elemental | 100 | 15 | 10–25 | AoE burn |
| **Boss: Skeleton King** | 300 | 20 | 5 | Summon skeletons |
| **Boss: Shadow Dragon** | 500 | 30 | 10 | Breath attack, flight |
| **Boss: Void Lord** | 800 | 40 | 15 | Phase shift, AoE |

## 5. Loot System

### Rarity Tiers
| Rarity | Color | Drop Rate | Stat Multiplier |
|--------|-------|-----------|-----------------|
| Common | White | 60% | 1.0x |
| Uncommon | Green | 25% | 1.3x |
| Rare | Blue | 10% | 1.7x |
| Epic | Purple | 4% | 2.5x |
| Legendary | Gold | 1% | 4.0x |

### Item Types
- **Weapons**: Sword, Staff, Bow (class-specific, boost ATK)
- **Armor**: Helmet, Chestplate, Boots (boost DEF/HP)
- **Potions**: Health Potion (heal 50 HP), Speed Potion (1.5x speed, 15s)

## 6. Meta-Progression (Soul Gems)

| Upgrade | Cost | Effect |
|---------|------|--------|
| Vitality I–V | 10–50 | +10 HP per level |
| Strength I–V | 10–50 | +3 ATK per level |
| Armor I–V | 10–50 | +3 DEF per level |
| Agility I–V | 10–50 | +2 Speed per level |
| Lucky I–III | 30–90 | +2% better loot per level |

Soul Gems earned: 1 per floor cleared + 5 per boss killed.

## 7. Technical Architecture

```
src/
├── ReplicatedStorage/
│   ├── GameConfig.lua        -- Constants, enemy/item/class definitions
│   ├── DungeonGenerator.lua  -- Procedural floor generation
│   └── LootSystem.lua        -- Item generation with rarity rolls
├── ServerScriptService/
│   ├── GameManager.server.lua -- Server orchestration, remote events
│   ├── CombatManager.lua      -- Damage calculation, abilities, AI
│   └── DataManager.lua        -- Player save data (DataStore)
├── StarterGui/
│   └── MainGui.lua            -- HUD, health bars, inventory, shop
└── StarterPlayerScripts/
    └── PlayerController.client.lua -- Input, camera, ability casting
```

## 8. Implementation Priority
- **Phase 1**: Core combat + procedural floors + 3 classes (this iteration)
- **Phase 2**: Full loot system + meta-progression shop
- **Phase 3**: Multiplayer co-op, leaderboards, gamepasses
