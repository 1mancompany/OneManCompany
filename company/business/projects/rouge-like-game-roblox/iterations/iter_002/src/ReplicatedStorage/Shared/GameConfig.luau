--[[
	GameConfig.luau — Central configuration for Soul Rift
	ReplicatedStorage/Shared/GameConfig
]]

local GameConfig = {}

-- General
GameConfig.MAX_PLAYERS_PER_RUN = 4
GameConfig.MAX_HUB_PLAYERS = 50

-- Dungeon structure (MVP: 1 floor)
GameConfig.FLOORS = {
	{
		name = "Ghost Ruins",
		theme = "ghost_ruins",
		minRooms = 8,
		maxRooms = 10,
		enemyLevelBase = 1,
		bossId = "lazarus",
	},
}

-- Room types
GameConfig.ROOM_TYPES = {
	COMBAT    = "combat",
	ELITE     = "elite",
	TREASURE  = "treasure",
	SHOP      = "shop",
	REST      = "rest",
	BOSS      = "boss",
	CHALLENGE = "challenge",
	SECRET    = "secret",
}

-- Room generation weights (MVP)
GameConfig.ROOM_WEIGHTS = {
	combat   = 50,
	treasure = 15,
	shop     = 10,
	elite    = 15,
	rest     = 10,
}

-- Combat
GameConfig.BASE_ATTACK_COOLDOWN = 0.5
GameConfig.DODGE_COOLDOWN = 1.5
GameConfig.DODGE_DISTANCE = 20
GameConfig.DODGE_IFRAME_DURATION = 0.3
GameConfig.KNOCKBACK_FORCE = 30
GameConfig.CRIT_MULTIPLIER = 2.0
GameConfig.DEFAULT_CRIT_RATE = 0.05

-- Player defaults
GameConfig.PLAYER_BASE_STATS = {
	maxHealth = 100,
	attack = 10,
	defense = 5,
	moveSpeed = 24,
	critRate = 0.05,
}

-- Loot rarity weights
GameConfig.RARITY_WEIGHTS = {
	common   = 50,
	uncommon = 30,
	rare     = 15,
	epic     = 5,
}

-- Talent card rarity weights
GameConfig.TALENT_RARITY_WEIGHTS = {
	common    = 50,
	rare      = 30,
	epic      = 15,
	legendary = 5,
}

-- Currency
GameConfig.SOUL_SHARD_BASE_DROP = 10
GameConfig.BOSS_SOUL_SHARD_MULTIPLIER = 5

-- Difficulty (MVP: normal only)
GameConfig.DIFFICULTY = {
	normal = { enemyMult = 1.0, rewardMult = 1.0 },
}

-- Timing
GameConfig.ROOM_TRANSITION_TIME = 2.0
GameConfig.TALENT_PICK_TIMEOUT = 30
GameConfig.RUN_TARGET_DURATION = 900

-- Room layout sizes (studs)
GameConfig.ROOM_SIZE = { x = 60, z = 60 }
GameConfig.CORRIDOR_LENGTH = 20
GameConfig.CORRIDOR_WIDTH = 10
GameConfig.WALL_HEIGHT = 16
GameConfig.WALL_THICKNESS = 2

return GameConfig
