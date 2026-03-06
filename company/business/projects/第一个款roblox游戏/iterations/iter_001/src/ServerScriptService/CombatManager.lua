-- CombatManager: server-side combat logic, enemy AI, damage calculation
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))

local CombatManager = {}

local rng = Random.new()

-- ── Damage Calculation ──────────────────────────────────
function CombatManager.calculateDamage(attackerAtk, defenderDef)
	-- Base damage = ATK - DEF/2, minimum 1
	local baseDmg = math.max(1, attackerAtk - math.floor(defenderDef / 2))
	-- Add ±15% variance
	local variance = rng:NextNumber(0.85, 1.15)
	return math.floor(baseDmg * variance)
end

-- Player attacks enemy
function CombatManager.playerAttackEnemy(playerData, enemyState)
	if not enemyState or enemyState.hp <= 0 then return 0, false end

	-- Check if enemy is blocking (DarkKnight special)
	local def = enemyState.def
	if enemyState.blocking then
		def = def * 2
	end

	local damage = CombatManager.calculateDamage(playerData.atk, def)
	enemyState.hp = enemyState.hp - damage

	local killed = enemyState.hp <= 0
	return damage, killed
end

-- Enemy attacks player (returns damage dealt)
function CombatManager.enemyAttackPlayer(enemyData, playerData)
	if not playerData or playerData.hp <= 0 then return 0 end

	local damage = CombatManager.calculateDamage(enemyData.atk, playerData.def)

	-- Apply special effects
	if enemyData.special == "slow" then
		-- Slow effect handled by caller
	end

	playerData.hp = playerData.hp - damage
	return damage
end

-- ── Ability Execution ───────────────────────────────────
function CombatManager.executeAbility(playerData, className, targetEnemies)
	local classData = GameConfig.CLASSES[className]
	if not classData then return {} end

	local results = {}

	if classData.abilityEffect == "stun" then
		-- Shield Bash: single target, high damage + stun
		if #targetEnemies > 0 then
			local target = targetEnemies[1]
			local damage = CombatManager.calculateDamage(
				playerData.atk + classData.abilityDamage, target.def)
			target.hp = target.hp - damage
			target.stunned = true
			target.stunTimer = GameConfig.STUN_DURATION
			table.insert(results, {
				enemy = target,
				damage = damage,
				effect = "stun",
				killed = target.hp <= 0,
			})
		end

	elseif classData.abilityEffect == "burn" then
		-- Fireball: AoE damage + burn DOT
		for _, enemy in ipairs(targetEnemies) do
			local damage = CombatManager.calculateDamage(
				playerData.atk + classData.abilityDamage, enemy.def)
			enemy.hp = enemy.hp - damage
			enemy.burning = true
			enemy.burnTimer = GameConfig.BURN_DURATION
			table.insert(results, {
				enemy = enemy,
				damage = damage,
				effect = "burn",
				killed = enemy.hp <= 0,
			})
		end

	elseif classData.abilityEffect == "pierce" then
		-- Power Shot: single target, ignores defense
		if #targetEnemies > 0 then
			local target = targetEnemies[1]
			local damage = playerData.atk + classData.abilityDamage
			damage = math.floor(damage * rng:NextNumber(0.9, 1.1))
			target.hp = target.hp - damage
			table.insert(results, {
				enemy = target,
				damage = damage,
				effect = "pierce",
				killed = target.hp <= 0,
			})
		end
	end

	return results
end

-- ── Ultimate Execution ──────────────────────────────────
function CombatManager.executeUltimate(playerData, className, targetEnemies)
	local classData = GameConfig.CLASSES[className]
	if not classData then return {} end

	local results = {}

	if className == "Warrior" then
		-- Berserker Rage: buff self (2x ATK for 10s, handled by caller)
		table.insert(results, {
			effect = "buff",
			stat = "atk",
			multiplier = 2,
			duration = 10,
		})

	elseif className == "Mage" then
		-- Arcane Storm: massive AoE
		for _, enemy in ipairs(targetEnemies) do
			local damage = CombatManager.calculateDamage(
				playerData.atk * 3, enemy.def)
			enemy.hp = enemy.hp - damage
			table.insert(results, {
				enemy = enemy,
				damage = damage,
				effect = "arcane_storm",
				killed = enemy.hp <= 0,
			})
		end

	elseif className == "Archer" then
		-- Arrow Rain: AoE with moderate damage
		for _, enemy in ipairs(targetEnemies) do
			local damage = CombatManager.calculateDamage(
				playerData.atk * 2, enemy.def)
			enemy.hp = enemy.hp - damage
			table.insert(results, {
				enemy = enemy,
				damage = damage,
				effect = "arrow_rain",
				killed = enemy.hp <= 0,
			})
		end
	end

	return results
end

-- ── Enemy AI Tick ───────────────────────────────────────
-- Returns an action for the enemy to take
function CombatManager.enemyAITick(enemyState, enemyData, playerPosition, dt)
	if not enemyState or enemyState.hp <= 0 then
		return { action = "dead" }
	end

	-- Handle stun
	if enemyState.stunned then
		enemyState.stunTimer = (enemyState.stunTimer or 0) - dt
		if enemyState.stunTimer <= 0 then
			enemyState.stunned = false
		end
		return { action = "stunned" }
	end

	-- Handle burn DOT
	if enemyState.burning then
		enemyState.burnTimer = (enemyState.burnTimer or 0) - dt
		if enemyState.burnTimer <= 0 then
			enemyState.burning = false
		else
			-- Apply burn damage every second
			enemyState.burnAccum = (enemyState.burnAccum or 0) + dt
			if enemyState.burnAccum >= 1 then
				enemyState.burnAccum = 0
				enemyState.hp = enemyState.hp - GameConfig.BURN_DAMAGE
				if enemyState.hp <= 0 then
					return { action = "died_from_burn" }
				end
			end
		end
	end

	-- Calculate distance to player
	local distance = (playerPosition - enemyState.position).Magnitude

	-- Aggro check
	if distance > GameConfig.ENEMY_AGGRO_RANGE then
		return { action = "idle" }
	end

	-- Attack if in range
	if distance <= GameConfig.ENEMY_ATTACK_RANGE then
		enemyState.attackCooldown = (enemyState.attackCooldown or 0) - dt
		if enemyState.attackCooldown <= 0 then
			enemyState.attackCooldown = 1.2  -- Attack every 1.2 seconds

			-- Special behaviors
			if enemyData.special == "block" and rng:NextNumber() < 0.3 then
				enemyState.blocking = true
				return { action = "block" }
			end
			if enemyData.special == "teleport" and rng:NextNumber() < 0.2 then
				-- Teleport to random position near player
				local offset = Vector3.new(
					rng:NextNumber(-10, 10), 0, rng:NextNumber(-10, 10))
				enemyState.position = playerPosition + offset
				return { action = "teleport", position = enemyState.position }
			end

			enemyState.blocking = false
			return { action = "attack" }
		end
		return { action = "idle_near" }
	end

	-- Move toward player
	local direction = (playerPosition - enemyState.position).Unit
	local moveSpeed = (enemyData.speed or 10) * dt
	enemyState.position = enemyState.position + direction * moveSpeed
	return { action = "move", position = enemyState.position }
end

-- ── Boss Special Attacks ────────────────────────────────
function CombatManager.bossSpecialAttack(bossState, bossData, playerPosition)
	if not bossData.special then return nil end

	if bossData.special == "summon" then
		-- Skeleton King: summon 2 skeletons
		return {
			type = "summon",
			enemy = "Skeleton",
			count = 2,
			positions = {
				bossState.position + Vector3.new(8, 0, 0),
				bossState.position + Vector3.new(-8, 0, 0),
			},
		}
	elseif bossData.special == "breath" then
		-- Shadow Dragon: cone attack in player direction
		return {
			type = "breath",
			damage = bossData.atk * 2,
			direction = (playerPosition - bossState.position).Unit,
			range = 20,
		}
	elseif bossData.special == "phase" then
		-- Void Lord: become invulnerable briefly + AoE
		bossState.phasing = true
		return {
			type = "phase",
			duration = 3,
			aoeDamage = bossData.atk * 1.5,
			aoeRange = 15,
		}
	end
	return nil
end

return CombatManager
