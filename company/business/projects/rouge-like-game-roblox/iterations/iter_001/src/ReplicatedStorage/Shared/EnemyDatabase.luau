--[[
	EnemyDatabase.luau — Enemy definitions for Soul Rift
	ReplicatedStorage/Shared/EnemyDatabase
	MVP: Ghost Ruins floor enemies + Boss 1
]]

local EnemyDatabase = {}

EnemyDatabase.Enemies = {
	-- Floor 1: Ghost Ruins
	wandering_ghost = {
		name = "Wandering Ghost",
		health = 30,
		attack = 8,
		defense = 2,
		moveSpeed = 10,
		attackRange = 5,
		detectionRange = 30,
		behavior = "chase_explode", -- slowly chases, explodes near player
		attackCooldown = 2.0,
		xpReward = 10,
		soulShards = 5,
		difficulty = 1,
	},
	bone_archer = {
		name = "Bone Archer",
		health = 20,
		attack = 12,
		defense = 1,
		moveSpeed = 8,
		attackRange = 40,
		detectionRange = 45,
		behavior = "ranged_kite", -- keeps distance, shoots projectiles
		attackCooldown = 1.5,
		projectileSpeed = 50,
		xpReward = 12,
		soulShards = 6,
		difficulty = 1,
	},
	shadow_knight = {
		name = "Shadow Knight",
		health = 60,
		attack = 15,
		defense = 8,
		moveSpeed = 16,
		attackRange = 8,
		detectionRange = 35,
		behavior = "charge_spin", -- charges at player, spin attack
		attackCooldown = 2.5,
		chargeSpeed = 40,
		xpReward = 20,
		soulShards = 10,
		difficulty = 2,
	},
	rune_guardian = {
		name = "Rune Guardian",
		health = 80,
		attack = 10,
		defense = 15,
		moveSpeed = 6,
		attackRange = 10,
		detectionRange = 25,
		behavior = "shield_reflect", -- has shield, reflects projectiles
		attackCooldown = 3.0,
		shieldHealth = 30,
		xpReward = 25,
		soulShards = 15,
		difficulty = 3,
	},
}

EnemyDatabase.Bosses = {
	lazarus = {
		name = "Undead Lord Lazarus",
		health = 500,
		attack = 20,
		defense = 10,
		moveSpeed = 14,
		phases = {
			{
				name = "Sword & Bones",
				hpThreshold = 1.0, -- starts at 100%
				behavior = "boss_melee_summon",
				attackCooldown = 1.5,
				summonCooldown = 8.0,
				summonId = "wandering_ghost",
				summonCount = 3,
			},
			{
				name = "Soul Form",
				hpThreshold = 0.6, -- triggers at 60% HP
				behavior = "boss_ranged_tracking",
				attackCooldown = 1.0,
				projectileSpeed = 30,
				trackingStrength = 0.5,
			},
			{
				name = "Dual Form",
				hpThreshold = 0.3, -- triggers at 30% HP
				behavior = "boss_dual_mode",
				attackCooldown = 0.8,
				summonCooldown = 6.0,
				summonId = "shadow_knight",
				summonCount = 2,
			},
		},
		xpReward = 200,
		soulShards = 100,
		lootTable = {
			{ itemPool = "ghost_ruins_epic", chance = 0.5 },
			{ itemPool = "ghost_ruins_rare", chance = 1.0 },
		},
	},
}

-- Spawn tables: how many enemies per combat room by difficulty
EnemyDatabase.SpawnTables = {
	ghost_ruins = {
		easy   = { minCount = 3, maxCount = 5, pool = { "wandering_ghost", "bone_archer" } },
		medium = { minCount = 4, maxCount = 6, pool = { "wandering_ghost", "bone_archer", "shadow_knight" } },
		hard   = { minCount = 5, maxCount = 7, pool = { "bone_archer", "shadow_knight", "rune_guardian" } },
	},
}

return EnemyDatabase
