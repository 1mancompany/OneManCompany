--[[
	ItemDatabase.luau — Item/equipment definitions for Soul Rift
	ReplicatedStorage/Shared/ItemDatabase
	MVP: Weapon + Armor slots, 3 rarities (common/rare/epic)
]]

local ItemDatabase = {}

-- Stat modifiers that can roll on equipment
ItemDatabase.STAT_POOL = {
	{ stat = "attack",    min = 1,  max = 5,  weight = 20 },
	{ stat = "maxHealth", min = 5,  max = 20, weight = 20 },
	{ stat = "critRate",  min = 0.01, max = 0.05, weight = 15 },
	{ stat = "defense",   min = 1,  max = 4,  weight = 15 },
	{ stat = "moveSpeed", min = 1,  max = 3,  weight = 10 },
	{ stat = "lifeSteal", min = 0.01, max = 0.03, weight = 10 },
	{ stat = "cooldownReduction", min = 0.02, max = 0.08, weight = 10 },
}

-- Affix count by rarity
ItemDatabase.RARITY_AFFIXES = {
	common = 0,
	uncommon = 1,
	rare = 2,
	epic = 3,
}

-- Stat multiplier by rarity
ItemDatabase.RARITY_STAT_MULT = {
	common = 1.0,
	uncommon = 1.2,
	rare = 1.5,
	epic = 2.0,
}

-- Base weapons (MVP)
ItemDatabase.Weapons = {
	-- Blade Dancer weapons
	{
		id = "rusty_sword",
		name = "Rusty Sword",
		slot = "weapon",
		classRestriction = "blade_dancer",
		baseAttack = 5,
		rarity = "common",
	},
	{
		id = "steel_katana",
		name = "Steel Katana",
		slot = "weapon",
		classRestriction = "blade_dancer",
		baseAttack = 10,
		rarity = "rare",
	},
	{
		id = "phantom_blade",
		name = "Phantom Blade",
		slot = "weapon",
		classRestriction = "blade_dancer",
		baseAttack = 18,
		rarity = "epic",
		specialEffect = "phantom_slash", -- attacks have 20% chance to hit twice
	},
	-- Soul Caster weapons
	{
		id = "cracked_orb",
		name = "Cracked Soul Orb",
		slot = "weapon",
		classRestriction = "soul_caster",
		baseAttack = 4,
		rarity = "common",
	},
	{
		id = "crystal_focus",
		name = "Crystal Focus",
		slot = "weapon",
		classRestriction = "soul_caster",
		baseAttack = 9,
		rarity = "rare",
	},
	{
		id = "void_scepter",
		name = "Void Scepter",
		slot = "weapon",
		classRestriction = "soul_caster",
		baseAttack = 16,
		rarity = "epic",
		specialEffect = "soul_pierce", -- projectiles pierce 1 additional enemy
	},
}

-- Base armor (MVP, shared between classes)
ItemDatabase.Armors = {
	{
		id = "cloth_tunic",
		name = "Cloth Tunic",
		slot = "armor",
		baseDefense = 3,
		baseHealth = 10,
		rarity = "common",
	},
	{
		id = "chain_mail",
		name = "Chain Mail",
		slot = "armor",
		baseDefense = 7,
		baseHealth = 20,
		rarity = "rare",
	},
	{
		id = "spectral_plate",
		name = "Spectral Plate",
		slot = "armor",
		baseDefense = 12,
		baseHealth = 35,
		rarity = "epic",
		specialEffect = "ghost_phase", -- 10% chance to phase through attacks
	},
}

-- Shop items (in-run currency)
ItemDatabase.ShopItems = {
	{
		id = "health_potion",
		name = "Health Potion",
		type = "consumable",
		effect = "heal",
		value = 30, -- heals 30 HP
		cost = 20,
	},
	{
		id = "damage_scroll",
		name = "Damage Scroll",
		type = "consumable",
		effect = "buff_attack",
		value = 0.2, -- +20% attack for room
		duration = 60,
		cost = 30,
	},
	{
		id = "reroll_token",
		name = "Talent Reroll",
		type = "consumable",
		effect = "reroll_talents",
		cost = 25,
	},
}

return ItemDatabase
