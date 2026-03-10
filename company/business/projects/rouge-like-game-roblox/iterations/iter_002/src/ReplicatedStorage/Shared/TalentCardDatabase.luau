--[[
	TalentCardDatabase.luau — Talent card (in-run buff) definitions
	ReplicatedStorage/Shared/TalentCardDatabase
	MVP: 20 universal + 5 per class = 30 total
]]

local TalentCardDatabase = {}

-- Universal talents (available to all classes)
TalentCardDatabase.Universal = {
	-- Common (10)
	{ id = "t_life_steal",     name = "Life Steal",      rarity = "common", description = "Attacks restore 1% max HP",        effect = { type = "stat", stat = "lifeSteal", value = 0.01 } },
	{ id = "t_swift_feet",     name = "Swift Feet",      rarity = "common", description = "Move speed +10%",                  effect = { type = "stat", stat = "moveSpeed", value = 0.10, mode = "percent" } },
	{ id = "t_iron_skin",      name = "Iron Skin",       rarity = "common", description = "Defense +15%",                     effect = { type = "stat", stat = "defense", value = 0.15, mode = "percent" } },
	{ id = "t_sharp_edge",     name = "Sharp Edge",      rarity = "common", description = "Attack +10%",                      effect = { type = "stat", stat = "attack", value = 0.10, mode = "percent" } },
	{ id = "t_vitality",       name = "Vitality",        rarity = "common", description = "Max HP +15%",                      effect = { type = "stat", stat = "maxHealth", value = 0.15, mode = "percent" } },
	{ id = "t_quick_hands",    name = "Quick Hands",     rarity = "common", description = "Attack speed +8%",                 effect = { type = "stat", stat = "attackSpeed", value = 0.08, mode = "percent" } },
	{ id = "t_lucky_strike",   name = "Lucky Strike",    rarity = "common", description = "Crit rate +3%",                    effect = { type = "stat", stat = "critRate", value = 0.03 } },
	{ id = "t_scavenger",      name = "Scavenger",       rarity = "common", description = "Soul shard drops +20%",            effect = { type = "stat", stat = "soulShardBonus", value = 0.20 } },
	{ id = "t_thick_armor",    name = "Thick Armor",     rarity = "common", description = "Reduce damage taken by 5%",        effect = { type = "stat", stat = "damageReduction", value = 0.05 } },
	{ id = "t_energy_flow",    name = "Energy Flow",     rarity = "common", description = "Ultimate energy gain +15%",        effect = { type = "stat", stat = "energyGainBonus", value = 0.15 } },

	-- Rare (6)
	{ id = "t_chain_lightning", name = "Chain Lightning",  rarity = "rare", description = "Kills deal 50% damage to nearby enemies", effect = { type = "on_kill", action = "chain_damage", radius = 15, damagePct = 0.50 } },
	{ id = "t_berserker",       name = "Berserker",        rarity = "rare", description = "Below 30% HP: +40% attack",                effect = { type = "conditional", condition = "low_hp", threshold = 0.3, stat = "attack", value = 0.40, mode = "percent" } },
	{ id = "t_dodge_master",    name = "Dodge Master",     rarity = "rare", description = "Dodge cooldown -30%",                      effect = { type = "stat", stat = "dodgeCooldown", value = -0.30, mode = "percent" } },
	{ id = "t_gold_rush",       name = "Gold Rush",        rarity = "rare", description = "Enemies drop +50% more gold",              effect = { type = "stat", stat = "goldBonus", value = 0.50 } },
	{ id = "t_second_wind",     name = "Second Wind",      rarity = "rare", description = "Heal 20% HP when entering a new room",     effect = { type = "on_event", event = "room_enter", action = "heal", value = 0.20 } },
	{ id = "t_crit_damage",     name = "Brutal Crits",     rarity = "rare", description = "Critical damage +50%",                     effect = { type = "stat", stat = "critDamage", value = 0.50 } },

	-- Epic (3)
	{ id = "t_time_warp",      name = "Time Warp",       rarity = "epic", description = "Dodge slows nearby enemies 50% for 2s",   effect = { type = "on_dodge", action = "slow_area", radius = 15, slowPct = 0.50, duration = 2.0 } },
	{ id = "t_soul_harvest",   name = "Soul Harvest",    rarity = "epic", description = "Kills grant a stacking +2% attack (max 30%)", effect = { type = "on_kill", action = "stack_buff", stat = "attack", value = 0.02, maxStacks = 15, duration = 10.0 } },
	{ id = "t_mirror_shield",  name = "Mirror Shield",   rarity = "epic", description = "Reflect 30% damage taken back to attacker",   effect = { type = "on_hit_taken", action = "reflect", reflectPct = 0.30 } },

	-- Legendary (1)
	{ id = "t_undying_soul",   name = "Undying Soul",    rarity = "legendary", description = "Revive on death with +100% attack for 60s (once per run)", effect = { type = "on_death", action = "revive", attackBonus = 1.00, duration = 60.0, uses = 1 } },
}

-- Blade Dancer exclusive talents (5)
TalentCardDatabase.BladeDancer = {
	{ id = "t_bd_wind_blade",   name = "Wind Blade",      rarity = "common",    description = "Sword Wave range +50%",                   effect = { type = "skill_mod", skillId = "bd_sword_wave", stat = "range", value = 0.50, mode = "percent" } },
	{ id = "t_bd_quick_slash",  name = "Quick Slash",     rarity = "common",    description = "Combo attack speed +15%",                 effect = { type = "skill_mod", skillId = "bd_slash_combo", stat = "speed", value = 0.15, mode = "percent" } },
	{ id = "t_bd_shadow_clone", name = "Shadow Clone",    rarity = "rare",      description = "Combo finisher summons a clone that attacks once", effect = { type = "on_combo_end", action = "summon_clone", damageMultiplier = 0.5 } },
	{ id = "t_bd_blade_field",  name = "Blade Field",     rarity = "epic",      description = "Ultimate becomes a persistent blade field",        effect = { type = "skill_mod", skillId = "bd_blade_storm", stat = "mode", value = "persistent_field" } },
	{ id = "t_bd_sword_intent", name = "Sword Intent",    rarity = "legendary", description = "Combo never resets; +5% attack per 10 combo",      effect = { type = "passive_mod", passive = "rising_combo", comboResetTime = 9999, bonusPer10 = 0.05 } },
}

-- Soul Caster exclusive talents (5)
TalentCardDatabase.SoulCaster = {
	{ id = "t_sc_piercing",     name = "Piercing Bolt",   rarity = "common",    description = "Soul Bolt pierces 1 additional enemy",          effect = { type = "skill_mod", skillId = "sc_soul_bolt", stat = "pierce", value = 1 } },
	{ id = "t_sc_bigger_boom",  name = "Bigger Boom",     rarity = "common",    description = "Soul Explosion radius +40%",                    effect = { type = "skill_mod", skillId = "sc_soul_explosion", stat = "radius", value = 0.40, mode = "percent" } },
	{ id = "t_sc_mana_surge",   name = "Mana Surge",      rarity = "rare",      description = "After casting 3 spells, next spell costs no cooldown", effect = { type = "on_cast_count", threshold = 3, action = "reset_cooldown" } },
	{ id = "t_sc_soul_link",    name = "Soul Link",       rarity = "epic",      description = "Barrier also heals allies inside for 5 HP/s",         effect = { type = "skill_mod", skillId = "sc_barrier", stat = "healPerSec", value = 5 } },
	{ id = "t_sc_soul_rain_ex", name = "Soul Tempest",    rarity = "legendary", description = "Ultimate projectiles home on enemies and explode",    effect = { type = "skill_mod", skillId = "sc_soul_rain", stat = "mode", value = "homing_explode" } },
}

-- Helper: get talent pool for a given class
function TalentCardDatabase.getPoolForClass(classId: string): { any }
	local pool = {}
	for _, card in TalentCardDatabase.Universal do
		table.insert(pool, card)
	end
	if classId == "blade_dancer" then
		for _, card in TalentCardDatabase.BladeDancer do
			table.insert(pool, card)
		end
	elseif classId == "soul_caster" then
		for _, card in TalentCardDatabase.SoulCaster do
			table.insert(pool, card)
		end
	end
	return pool
end

return TalentCardDatabase
