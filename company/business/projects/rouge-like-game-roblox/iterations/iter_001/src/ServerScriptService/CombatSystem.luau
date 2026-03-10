--[[
	CombatSystem.luau — Server-authoritative combat calculations
	ServerScriptService/CombatSystem

	Handles: damage calculation, skill execution, dodge, knockback,
	talent card effects, and death/revive logic.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")

local GameConfig = require(ReplicatedStorage.Shared.GameConfig)
local SkillDatabase = require(ReplicatedStorage.Shared.SkillDatabase)

local CombatSystem = {}

-- Active player combat states: { [Player]: CombatState }
CombatSystem._playerStates = {}

-- Active enemy states: { [Model]: EnemyState }
CombatSystem._enemyStates = {}

--------------------------------------------------
-- Player combat state
--------------------------------------------------

export type CombatState = {
	classId: string,
	stats: { [string]: number },
	currentHealth: number,
	energy: number,
	comboCount: number,
	comboTimer: number,
	talents: { any },
	skillCooldowns: { [string]: number },
	dodgeCooldown: number,
	isInvincible: boolean,
	isDead: boolean,
	buffs: { any },
	shield: number,
}

function CombatSystem.initPlayerState(player: Player, classId: string): CombatState
	local classDef = SkillDatabase.Classes[classId]
	if not classDef then
		warn("[CombatSystem] Unknown class:", classId)
		return nil
	end

	local stats = {}
	for k, v in classDef.baseStats do
		stats[k] = v
	end

	local state: CombatState = {
		classId = classId,
		stats = stats,
		currentHealth = stats.maxHealth,
		energy = 0,
		comboCount = 0,
		comboTimer = 0,
		talents = {},
		skillCooldowns = {},
		dodgeCooldown = 0,
		isInvincible = false,
		isDead = false,
		buffs = {},
		shield = 0,
	}

	CombatSystem._playerStates[player] = state
	return state
end

function CombatSystem.getPlayerState(player: Player): CombatState?
	return CombatSystem._playerStates[player]
end

function CombatSystem.removePlayerState(player: Player)
	CombatSystem._playerStates[player] = nil
end

--------------------------------------------------
-- Damage calculation
--------------------------------------------------

function CombatSystem.calculateDamage(
	attackerStats: { [string]: number },
	defenderStats: { [string]: number },
	damageMultiplier: number,
	attackerTalents: { any }?
): (number, boolean) -- returns (damage, isCrit)
	local baseDamage = attackerStats.attack * damageMultiplier
	local defense = defenderStats.defense or 0

	-- Defense reduction formula: damage * (100 / (100 + defense))
	local reduction = 100 / (100 + defense)
	local damage = baseDamage * reduction

	-- Crit check
	local critRate = attackerStats.critRate or GameConfig.DEFAULT_CRIT_RATE
	local critMult = GameConfig.CRIT_MULTIPLIER
	local isCrit = false

	-- Check for crit damage talent bonus
	if attackerTalents then
		for _, talent in attackerTalents do
			if talent.effect and talent.effect.stat == "critDamage" then
				critMult += talent.effect.value
			end
		end
	end

	if math.random() < critRate then
		damage *= critMult
		isCrit = true
	end

	-- Damage reduction from talents/buffs
	local damageReduction = defenderStats.damageReduction or 0
	damage *= (1 - damageReduction)

	return math.floor(math.max(1, damage)), isCrit
end

--------------------------------------------------
-- Apply damage to enemy
--------------------------------------------------

function CombatSystem.damageEnemy(
	player: Player,
	enemyModel: Model,
	damageMultiplier: number
): (number, boolean)?
	local playerState = CombatSystem._playerStates[player]
	local enemyState = CombatSystem._enemyStates[enemyModel]
	if not playerState or not enemyState or enemyState.isDead then
		return nil
	end

	local damage, isCrit = CombatSystem.calculateDamage(
		playerState.stats,
		enemyState.stats,
		damageMultiplier,
		playerState.talents
	)

	enemyState.currentHealth -= damage

	-- Life steal
	local lifeSteal = playerState.stats.lifeSteal or 0
	if lifeSteal > 0 then
		local healAmount = math.floor(playerState.stats.maxHealth * lifeSteal)
		CombatSystem.healPlayer(player, healAmount)
	end

	-- Energy gain
	local energyGain = SkillDatabase.ENERGY_PER_HIT
	local energyBonus = playerState.stats.energyGainBonus or 0
	energyGain = math.floor(energyGain * (1 + energyBonus))
	playerState.energy = math.min(SkillDatabase.ENERGY_MAX, playerState.energy + energyGain)

	-- Combo tracking (Blade Dancer passive)
	if playerState.classId == "blade_dancer" then
		playerState.comboCount += 1
		playerState.comboTimer = SkillDatabase.Classes.blade_dancer.passive.comboResetTime

		-- Apply combo bonuses
		local passive = SkillDatabase.Classes.blade_dancer.passive
		local stacks = math.min(playerState.comboCount, passive.maxComboStacks)
		playerState.stats.attackSpeed = 1.0 + stacks * passive.comboAttackSpeedBonus
	end

	-- Check death
	if enemyState.currentHealth <= 0 then
		enemyState.isDead = true
		CombatSystem._onEnemyKilled(player, enemyModel, enemyState)
	end

	-- Fire client event for VFX
	local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
	if combatEvent then
		combatEvent:FireClient(player, "DamageDealt", {
			target = enemyModel,
			damage = damage,
			isCrit = isCrit,
			targetHealth = enemyState.currentHealth,
			targetMaxHealth = enemyState.stats.maxHealth,
		})
	end

	return damage, isCrit
end

--------------------------------------------------
-- Apply damage to player
--------------------------------------------------

function CombatSystem.damagePlayer(player: Player, rawDamage: number, source: Model?): number?
	local state = CombatSystem._playerStates[player]
	if not state or state.isDead or state.isInvincible then
		return nil
	end

	-- Shield absorb first
	if state.shield > 0 then
		if state.shield >= rawDamage then
			state.shield -= rawDamage
			return 0
		else
			rawDamage -= state.shield
			state.shield = 0
		end
	end

	-- Apply defense
	local reduction = 100 / (100 + state.stats.defense)
	local damage = math.floor(rawDamage * reduction)
	local damageReduction = state.stats.damageReduction or 0
	damage = math.floor(damage * (1 - damageReduction))
	damage = math.max(1, damage)

	state.currentHealth -= damage

	-- Reflect damage talent
	for _, talent in state.talents do
		if talent.effect and talent.effect.action == "reflect" and source then
			local reflectDmg = math.floor(damage * talent.effect.reflectPct)
			local enemyState = CombatSystem._enemyStates[source]
			if enemyState and not enemyState.isDead then
				enemyState.currentHealth -= reflectDmg
				if enemyState.currentHealth <= 0 then
					enemyState.isDead = true
					CombatSystem._onEnemyKilled(player, source, enemyState)
				end
			end
		end
	end

	-- Death check
	if state.currentHealth <= 0 then
		CombatSystem._onPlayerDeath(player, state)
	end

	-- Berserker talent check
	for _, talent in state.talents do
		if talent.effect and talent.effect.condition == "low_hp" then
			local hpPct = state.currentHealth / state.stats.maxHealth
			if hpPct <= talent.effect.threshold then
				-- Apply temporary attack bonus (tracked via buffs)
				CombatSystem._applyBuff(state, "berserker", {
					stat = "attack",
					value = talent.effect.value,
					mode = "percent",
				})
			end
		end
	end

	-- Fire client event
	local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
	if combatEvent then
		combatEvent:FireClient(player, "DamageTaken", {
			damage = damage,
			currentHealth = state.currentHealth,
			maxHealth = state.stats.maxHealth,
		})
	end

	return damage
end

--------------------------------------------------
-- Healing
--------------------------------------------------

function CombatSystem.healPlayer(player: Player, amount: number)
	local state = CombatSystem._playerStates[player]
	if not state or state.isDead then return end

	state.currentHealth = math.min(state.stats.maxHealth, state.currentHealth + amount)
end

--------------------------------------------------
-- Dodge
--------------------------------------------------

function CombatSystem.tryDodge(player: Player): boolean
	local state = CombatSystem._playerStates[player]
	if not state or state.isDead then return false end
	if state.dodgeCooldown > 0 then return false end

	state.isInvincible = true
	state.dodgeCooldown = GameConfig.DODGE_COOLDOWN

	-- Check dodge cooldown reduction from talents
	for _, talent in state.talents do
		if talent.effect and talent.effect.stat == "dodgeCooldown" then
			state.dodgeCooldown *= (1 + talent.effect.value) -- value is negative
		end
	end

	-- Schedule end of i-frames
	task.delay(GameConfig.DODGE_IFRAME_DURATION, function()
		if CombatSystem._playerStates[player] then
			state.isInvincible = false
		end
	end)

	-- Time warp talent (slow nearby enemies on dodge)
	for _, talent in state.talents do
		if talent.effect and talent.effect.action == "slow_area" then
			CombatSystem._applyAreaSlow(player, talent.effect)
		end
	end

	return true
end

--------------------------------------------------
-- Skill execution
--------------------------------------------------

function CombatSystem.useSkill(player: Player, skillId: string): boolean
	local state = CombatSystem._playerStates[player]
	if not state or state.isDead then return false end

	local classDef = SkillDatabase.Classes[state.classId]
	if not classDef then return false end

	-- Find skill definition
	local skillDef = nil
	for _, skill in classDef.skills do
		if skill.id == skillId then
			skillDef = skill
			break
		end
	end
	if not skillDef then return false end

	-- Check cooldown
	local currentCD = state.skillCooldowns[skillId] or 0
	if currentCD > 0 then return false end

	-- Set cooldown
	state.skillCooldowns[skillId] = skillDef.cooldown

	-- Soul Caster passive: grant shield after casting
	if state.classId == "soul_caster" then
		local passive = classDef.passive
		local shieldAmount = math.floor(state.stats.maxHealth * passive.shieldPercent)
		state.shield = math.max(state.shield, shieldAmount)
	end

	return true
end

-- Use ultimate skill
function CombatSystem.useUltimate(player: Player): boolean
	local state = CombatSystem._playerStates[player]
	if not state or state.isDead then return false end
	if state.energy < SkillDatabase.ENERGY_MAX then return false end

	state.energy = 0
	return true
end

--------------------------------------------------
-- Talent card management
--------------------------------------------------

function CombatSystem.addTalent(player: Player, talentData: any)
	local state = CombatSystem._playerStates[player]
	if not state then return end

	table.insert(state.talents, talentData)

	-- Apply stat-type talents immediately
	if talentData.effect and talentData.effect.type == "stat" then
		local effect = talentData.effect
		if effect.mode == "percent" then
			state.stats[effect.stat] = (state.stats[effect.stat] or 0) * (1 + effect.value)
		else
			state.stats[effect.stat] = (state.stats[effect.stat] or 0) + effect.value
		end
		-- Recalculate max health effect
		if effect.stat == "maxHealth" then
			state.currentHealth = math.min(state.currentHealth, state.stats.maxHealth)
		end
	end
end

--------------------------------------------------
-- Enemy state management
--------------------------------------------------

function CombatSystem.initEnemyState(enemyModel: Model, enemyData: any)
	CombatSystem._enemyStates[enemyModel] = {
		id = enemyData.id or "unknown",
		stats = {
			maxHealth = enemyData.health,
			attack = enemyData.attack,
			defense = enemyData.defense,
			moveSpeed = enemyData.moveSpeed,
		},
		currentHealth = enemyData.health,
		isDead = false,
		behavior = enemyData.behavior,
		xpReward = enemyData.xpReward,
		soulShards = enemyData.soulShards,
	}
end

function CombatSystem.getEnemyState(enemyModel: Model)
	return CombatSystem._enemyStates[enemyModel]
end

function CombatSystem.removeEnemyState(enemyModel: Model)
	CombatSystem._enemyStates[enemyModel] = nil
end

--------------------------------------------------
-- Update loop (call from GameManager heartbeat)
--------------------------------------------------

function CombatSystem.update(dt: number)
	for player, state in CombatSystem._playerStates do
		if not state.isDead then
			-- Cooldown ticks
			for skillId, cd in state.skillCooldowns do
				if cd > 0 then
					state.skillCooldowns[skillId] = math.max(0, cd - dt)
				end
			end

			-- Dodge cooldown
			if state.dodgeCooldown > 0 then
				state.dodgeCooldown = math.max(0, state.dodgeCooldown - dt)
			end

			-- Combo timer (Blade Dancer)
			if state.classId == "blade_dancer" and state.comboCount > 0 then
				state.comboTimer -= dt
				if state.comboTimer <= 0 then
					state.comboCount = 0
					state.stats.attackSpeed = 1.0
				end
			end

			-- Buff durations
			for i = #state.buffs, 1, -1 do
				local buff = state.buffs[i]
				if buff.duration then
					buff.duration -= dt
					if buff.duration <= 0 then
						CombatSystem._removeBuff(state, i)
					end
				end
			end
		end
	end
end

--------------------------------------------------
-- Internal helpers
--------------------------------------------------

function CombatSystem._onEnemyKilled(player: Player, enemyModel: Model, enemyState: any)
	local playerState = CombatSystem._playerStates[player]
	if not playerState then return end

	-- Energy on kill
	local energyGain = SkillDatabase.ENERGY_PER_KILL
	local energyBonus = playerState.stats.energyGainBonus or 0
	energyGain = math.floor(energyGain * (1 + energyBonus))
	playerState.energy = math.min(SkillDatabase.ENERGY_MAX, playerState.energy + energyGain)

	-- On-kill talent effects
	for _, talent in playerState.talents do
		if talent.effect and talent.effect.type == "on_kill" then
			if talent.effect.action == "chain_damage" then
				CombatSystem._chainDamage(player, enemyModel, talent.effect)
			elseif talent.effect.action == "stack_buff" then
				CombatSystem._applyStackBuff(playerState, talent.effect)
			end
		end
	end

	-- Fire kill event
	local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
	if combatEvent then
		combatEvent:FireClient(player, "EnemyKilled", {
			target = enemyModel,
			xp = enemyState.xpReward,
			soulShards = enemyState.soulShards,
		})
	end
end

function CombatSystem._onPlayerDeath(player: Player, state: CombatState)
	-- Check revive talent
	for i, talent in state.talents do
		if talent.effect and talent.effect.action == "revive" and (talent.effect.uses or 0) > 0 then
			state.currentHealth = math.floor(state.stats.maxHealth * 0.3)
			talent.effect.uses -= 1
			CombatSystem._applyBuff(state, "revive_boost", {
				stat = "attack",
				value = talent.effect.attackBonus,
				mode = "percent",
				duration = talent.effect.duration,
			})
			return
		end
	end

	state.isDead = true
	state.currentHealth = 0
end

function CombatSystem._applyBuff(state: CombatState, name: string, buffData: any)
	-- Remove existing buff of same name
	for i = #state.buffs, 1, -1 do
		if state.buffs[i].name == name then
			CombatSystem._removeBuff(state, i)
		end
	end

	local buff = {
		name = name,
		stat = buffData.stat,
		value = buffData.value,
		mode = buffData.mode,
		duration = buffData.duration,
	}
	table.insert(state.buffs, buff)

	-- Apply stat change
	if buffData.mode == "percent" then
		state.stats[buffData.stat] = (state.stats[buffData.stat] or 0) * (1 + buffData.value)
	else
		state.stats[buffData.stat] = (state.stats[buffData.stat] or 0) + buffData.value
	end
end

function CombatSystem._removeBuff(state: CombatState, index: number)
	local buff = state.buffs[index]
	if buff then
		-- Reverse stat change
		if buff.mode == "percent" then
			state.stats[buff.stat] = state.stats[buff.stat] / (1 + buff.value)
		else
			state.stats[buff.stat] = state.stats[buff.stat] - buff.value
		end
		table.remove(state.buffs, index)
	end
end

function CombatSystem._chainDamage(player: Player, source: Model, effect: any)
	local sourcePos = source:GetPivot().Position
	for enemy, enemyState in CombatSystem._enemyStates do
		if enemy ~= source and not enemyState.isDead then
			local dist = (enemy:GetPivot().Position - sourcePos).Magnitude
			if dist <= effect.radius then
				local damage = math.floor(enemyState.stats.maxHealth * effect.damagePct)
				enemyState.currentHealth -= damage
				if enemyState.currentHealth <= 0 then
					enemyState.isDead = true
					CombatSystem._onEnemyKilled(player, enemy, enemyState)
				end
			end
		end
	end
end

function CombatSystem._applyStackBuff(state: CombatState, effect: any)
	local existing = nil
	for _, buff in state.buffs do
		if buff.name == "soul_harvest_stack" then
			existing = buff
			break
		end
	end

	if existing then
		if existing.stacks < (effect.maxStacks or 15) then
			existing.stacks += 1
			state.stats[effect.stat] = (state.stats[effect.stat] or 0) * (1 + effect.value) / (1 + effect.value * (existing.stacks - 1) / existing.stacks)
		end
		existing.duration = effect.duration
	else
		local buff = {
			name = "soul_harvest_stack",
			stat = effect.stat,
			value = effect.value,
			stacks = 1,
			mode = "percent",
			duration = effect.duration,
		}
		table.insert(state.buffs, buff)
		state.stats[effect.stat] = (state.stats[effect.stat] or 0) * (1 + effect.value)
	end
end

function CombatSystem._applyAreaSlow(player: Player, effect: any)
	local character = player.Character
	if not character then return end
	local pos = character:GetPivot().Position

	for enemy, enemyState in CombatSystem._enemyStates do
		if not enemyState.isDead then
			local dist = (enemy:GetPivot().Position - pos).Magnitude
			if dist <= effect.radius then
				local origSpeed = enemyState.stats.moveSpeed
				enemyState.stats.moveSpeed *= (1 - effect.slowPct)
				task.delay(effect.duration, function()
					if CombatSystem._enemyStates[enemy] then
						enemyState.stats.moveSpeed = origSpeed
					end
				end)
			end
		end
	end
end

return CombatSystem
