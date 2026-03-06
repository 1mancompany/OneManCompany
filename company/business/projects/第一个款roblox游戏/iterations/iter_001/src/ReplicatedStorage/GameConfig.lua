-- GameConfig: shared constants for Shadow Dungeon: Descent
local GameConfig = {}

-- ── Classes ─────────────────────────────────────────────
GameConfig.CLASSES = {
	Warrior = {
		hp = 150, atk = 15, def = 12, speed = 14,
		ability = "Shield Bash",
		abilityCooldown = 5,
		abilityDamage = 25,
		abilityEffect = "stun",
		ultimate = "Berserker Rage",
		ultimateCooldown = 30,
		color = Color3.fromRGB(200, 50, 50),
	},
	Mage = {
		hp = 80, atk = 25, def = 5, speed = 12,
		ability = "Fireball",
		abilityCooldown = 4,
		abilityDamage = 40,
		abilityEffect = "burn",
		ultimate = "Arcane Storm",
		ultimateCooldown = 35,
		color = Color3.fromRGB(80, 80, 220),
	},
	Archer = {
		hp = 100, atk = 20, def = 8, speed = 18,
		ability = "Power Shot",
		abilityCooldown = 3,
		abilityDamage = 35,
		abilityEffect = "pierce",
		ultimate = "Arrow Rain",
		ultimateCooldown = 25,
		color = Color3.fromRGB(50, 180, 50),
	},
}

-- ── Enemies ─────────────────────────────────────────────
GameConfig.ENEMIES = {
	Skeleton = {
		hp = 30, atk = 5, def = 2, speed = 10,
		floorMin = 1, floorMax = 5,
		xpReward = 5, soulGemChance = 0.1,
		color = Color3.fromRGB(200, 200, 200),
		size = Vector3.new(4, 5, 4),
	},
	Zombie = {
		hp = 50, atk = 8, def = 4, speed = 7,
		floorMin = 1, floorMax = 10,
		xpReward = 8, soulGemChance = 0.15,
		special = "slow",
		color = Color3.fromRGB(80, 140, 80),
		size = Vector3.new(4, 5, 4),
	},
	DarkKnight = {
		hp = 80, atk = 12, def = 10, speed = 9,
		floorMin = 5, floorMax = 15,
		xpReward = 15, soulGemChance = 0.2,
		special = "block",
		color = Color3.fromRGB(40, 40, 60),
		size = Vector3.new(5, 6, 5),
	},
	ShadowMage = {
		hp = 60, atk = 18, def = 3, speed = 13,
		floorMin = 8, floorMax = 20,
		xpReward = 20, soulGemChance = 0.25,
		special = "teleport",
		color = Color3.fromRGB(100, 0, 150),
		size = Vector3.new(4, 5, 4),
	},
	FireElemental = {
		hp = 100, atk = 15, def = 6, speed = 11,
		floorMin = 10, floorMax = 25,
		xpReward = 25, soulGemChance = 0.3,
		special = "burn_aoe",
		color = Color3.fromRGB(255, 100, 0),
		size = Vector3.new(5, 6, 5),
	},
}

-- ── Bosses ──────────────────────────────────────────────
GameConfig.BOSSES = {
	[5]  = {
		name = "Skeleton King", hp = 300, atk = 20, def = 8, speed = 8,
		xpReward = 100, soulGemReward = 5,
		special = "summon",
		color = Color3.fromRGB(255, 255, 200),
		size = Vector3.new(8, 10, 8),
	},
	[10] = {
		name = "Shadow Dragon", hp = 500, atk = 30, def = 12, speed = 14,
		xpReward = 200, soulGemReward = 10,
		special = "breath",
		color = Color3.fromRGB(60, 0, 100),
		size = Vector3.new(10, 8, 12),
	},
	[15] = {
		name = "Void Lord", hp = 800, atk = 40, def = 15, speed = 10,
		xpReward = 350, soulGemReward = 20,
		special = "phase",
		color = Color3.fromRGB(20, 20, 20),
		size = Vector3.new(12, 14, 12),
	},
}

-- ── Loot ────────────────────────────────────────────────
GameConfig.RARITY = {
	{ name = "Common",    weight = 60, multiplier = 1.0, color = Color3.fromRGB(200, 200, 200) },
	{ name = "Uncommon",  weight = 25, multiplier = 1.3, color = Color3.fromRGB(50, 200, 50)   },
	{ name = "Rare",      weight = 10, multiplier = 1.7, color = Color3.fromRGB(50, 100, 255)  },
	{ name = "Epic",      weight = 4,  multiplier = 2.5, color = Color3.fromRGB(160, 50, 255)  },
	{ name = "Legendary", weight = 1,  multiplier = 4.0, color = Color3.fromRGB(255, 200, 0)   },
}

GameConfig.ITEM_TYPES = {
	{ name = "Sword",      slot = "weapon", baseAtk = 5,  classPref = "Warrior" },
	{ name = "Staff",      slot = "weapon", baseAtk = 8,  classPref = "Mage"    },
	{ name = "Bow",        slot = "weapon", baseAtk = 6,  classPref = "Archer"  },
	{ name = "Helmet",     slot = "helmet", baseDef = 3,  baseHp = 10 },
	{ name = "Chestplate", slot = "chest",  baseDef = 5,  baseHp = 20 },
	{ name = "Boots",      slot = "boots",  baseDef = 2,  baseHp = 5, baseSpeed = 2 },
}

GameConfig.POTIONS = {
	{ name = "Health Potion",  effect = "heal",  value = 50 },
	{ name = "Speed Potion",   effect = "speed", value = 1.5, duration = 15 },
}

-- ── Meta-Progression (Soul Gems) ────────────────────────
GameConfig.SOUL_UPGRADES = {
	{ name = "Vitality",  stat = "hp",    perLevel = 10, maxLevel = 5, baseCost = 10 },
	{ name = "Strength",  stat = "atk",   perLevel = 3,  maxLevel = 5, baseCost = 10 },
	{ name = "Armor",     stat = "def",   perLevel = 3,  maxLevel = 5, baseCost = 10 },
	{ name = "Agility",   stat = "speed", perLevel = 2,  maxLevel = 5, baseCost = 10 },
	{ name = "Lucky",     stat = "luck",  perLevel = 2,  maxLevel = 3, baseCost = 30 },
}

-- ── Dungeon ─────────────────────────────────────────────
GameConfig.ROOM_SIZE = 60           -- studs per room side
GameConfig.ROOM_HEIGHT = 20         -- wall height
GameConfig.ENEMIES_PER_FLOOR_BASE = 3
GameConfig.ENEMIES_PER_FLOOR_SCALE = 0.5  -- +0.5 per floor
GameConfig.BOSS_FLOOR_INTERVAL = 5

-- ── Soul Gems ───────────────────────────────────────────
GameConfig.SOUL_GEMS_PER_FLOOR = 1
GameConfig.SOUL_GEMS_PER_BOSS = 5

-- ── Combat ──────────────────────────────────────────────
GameConfig.ATTACK_RANGE = 8         -- studs
GameConfig.ATTACK_COOLDOWN = 0.8    -- seconds
GameConfig.ABILITY_RANGE = 15
GameConfig.STUN_DURATION = 2        -- seconds
GameConfig.BURN_DAMAGE = 3          -- per tick
GameConfig.BURN_DURATION = 5        -- seconds
GameConfig.ENEMY_AGGRO_RANGE = 25   -- studs
GameConfig.ENEMY_ATTACK_RANGE = 6

-- ── DataStore ───────────────────────────────────────────
GameConfig.DATASTORE_NAME = "ShadowDungeon_PlayerData_v1"
GameConfig.AUTO_SAVE_INTERVAL = 60

return GameConfig
