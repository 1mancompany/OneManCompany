--[[
	SkillDatabase.luau — Skill definitions for Soul Rift
	ReplicatedStorage/Shared/SkillDatabase
	MVP: 2 classes (Blade Dancer, Soul Caster)
]]

local SkillDatabase = {}

SkillDatabase.Classes = {
	blade_dancer = {
		name = "Blade Dancer",
		displayName = "剑士",
		description = "Fast melee combos, dodge counters, and sword waves",
		baseStats = {
			maxHealth = 100,
			attack = 12,
			defense = 6,
			moveSpeed = 26,
			critRate = 0.08,
		},
		passive = {
			name = "Rising Combo",
			description = "Higher combo count increases attack speed and damage",
			comboAttackSpeedBonus = 0.02, -- per combo hit
			comboDamageBonus = 0.01,      -- per combo hit
			maxComboStacks = 30,
			comboResetTime = 3.0,
		},
		skills = {
			{
				id = "bd_slash_combo",
				name = "Swift Slash",
				description = "3-hit sword combo dealing increasing damage",
				type = "melee",
				cooldown = 0,
				damageMultiplier = { 0.8, 1.0, 1.5 }, -- per hit
				range = 8,
				comboWindow = 0.8,
			},
			{
				id = "bd_sword_wave",
				name = "Sword Wave",
				description = "Send a crescent blade projectile forward",
				type = "projectile",
				cooldown = 4.0,
				damageMultiplier = 2.0,
				range = 50,
				projectileSpeed = 60,
				width = 6,
			},
			{
				id = "bd_counter_dash",
				name = "Counter Dash",
				description = "Dash forward; if hit during dash, counter with a powerful slash",
				type = "dash_counter",
				cooldown = 6.0,
				dashDistance = 25,
				dashDuration = 0.3,
				counterDamageMultiplier = 3.0,
				counterWindow = 0.3,
			},
		},
		ultimate = {
			id = "bd_blade_storm",
			name = "Blade Storm",
			description = "Unleash a whirlwind of slashes in a wide area",
			energyCost = 100,
			damageMultiplier = 0.5, -- per tick
			tickRate = 0.15,
			duration = 2.0,
			radius = 15,
		},
	},

	soul_caster = {
		name = "Soul Caster",
		displayName = "咒术师",
		description = "Ranged soul projectiles, area spells, and shields",
		baseStats = {
			maxHealth = 80,
			attack = 14,
			defense = 4,
			moveSpeed = 22,
			critRate = 0.05,
		},
		passive = {
			name = "Soul Shield",
			description = "Gain a soul shield after casting a spell",
			shieldPercent = 0.05, -- 5% of max health
			shieldDuration = 5.0,
			internalCooldown = 2.0,
		},
		skills = {
			{
				id = "sc_soul_bolt",
				name = "Soul Bolt",
				description = "Fire a homing soul projectile",
				type = "projectile",
				cooldown = 0.8,
				damageMultiplier = 1.2,
				range = 55,
				projectileSpeed = 45,
				homingStrength = 0.3,
			},
			{
				id = "sc_soul_explosion",
				name = "Soul Explosion",
				description = "Create an explosion at target location",
				type = "area",
				cooldown = 5.0,
				damageMultiplier = 2.5,
				radius = 12,
				range = 40,
				castTime = 0.5,
			},
			{
				id = "sc_barrier",
				name = "Arcane Barrier",
				description = "Deploy a shield that absorbs damage for all nearby allies",
				type = "shield",
				cooldown = 8.0,
				shieldAmount = 40, -- flat HP
				duration = 5.0,
				radius = 15,
			},
		},
		ultimate = {
			id = "sc_soul_rain",
			name = "Soul Rain",
			description = "Rain soul projectiles over a large area",
			energyCost = 100,
			damageMultiplier = 1.0, -- per projectile
			projectileCount = 20,
			duration = 3.0,
			radius = 25,
		},
	},
}

-- Energy generation
SkillDatabase.ENERGY_PER_HIT = 5
SkillDatabase.ENERGY_PER_KILL = 15
SkillDatabase.ENERGY_MAX = 100

return SkillDatabase
