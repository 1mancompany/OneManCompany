-- GameManager: main server script for Shadow Dungeon: Descent
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))
local DataManager = require(script.Parent:WaitForChild("DataManager"))
local CombatManager = require(script.Parent:WaitForChild("CombatManager"))
local DungeonGenerator = require(ReplicatedStorage:WaitForChild("DungeonGenerator"))
local LootSystem = require(ReplicatedStorage:WaitForChild("LootSystem"))

-- ── Remote Events / Functions ───────────────────────────
local function createRemote(className, name)
	local remote = Instance.new(className)
	remote.Name = name
	remote.Parent = ReplicatedStorage
	return remote
end

-- Client → Server events
local SelectClassEvent = createRemote("RemoteFunction", "SelectClassEvent")
local AttackEvent = createRemote("RemoteEvent", "AttackEvent")
local UseAbilityEvent = createRemote("RemoteEvent", "UseAbilityEvent")
local UseUltimateEvent = createRemote("RemoteEvent", "UseUltimateEvent")
local UsePotionEvent = createRemote("RemoteEvent", "UsePotionEvent")
local EquipItemEvent = createRemote("RemoteFunction", "EquipItemEvent")
local BuySoulUpgradeEvent = createRemote("RemoteFunction", "BuySoulUpgradeEvent")
local StartRunEvent = createRemote("RemoteEvent", "StartRunEvent")

-- Server → Client events
local DataUpdateEvent = createRemote("RemoteEvent", "DataUpdateEvent")
local FloorUpdateEvent = createRemote("RemoteEvent", "FloorUpdateEvent")
local CombatFeedbackEvent = createRemote("RemoteEvent", "CombatFeedbackEvent")
local LootDropEvent = createRemote("RemoteEvent", "LootDropEvent")
local GameOverEvent = createRemote("RemoteEvent", "GameOverEvent")
local EnemyUpdateEvent = createRemote("RemoteEvent", "EnemyUpdateEvent")

-- ── Per-Player State ────────────────────────────────────
local playerStates = {}  -- [userId] = { enemies, floor, abilityCooldown, ... }

local function getPlayerState(player)
	return playerStates[player.UserId]
end

local function syncData(player)
	local data = DataManager.getData(player)
	if data then
		DataUpdateEvent:FireClient(player, data)
	end
end

-- ── Dungeon Management ──────────────────────────────────
local dungeonFolder = Instance.new("Folder")
dungeonFolder.Name = "Dungeons"
dungeonFolder.Parent = workspace

local function spawnEnemiesForFloor(player, state, floorNum, origin, isBoss)
	state.enemies = {}

	if isBoss then
		local bossData = GameConfig.BOSSES[floorNum]
		if bossData then
			local bossState = {
				id = "boss_" .. floorNum,
				name = bossData.name,
				data = bossData,
				hp = bossData.hp,
				maxHp = bossData.hp,
				atk = bossData.atk,
				def = bossData.def,
				speed = bossData.speed,
				position = origin + Vector3.new(0, 3, 0),
				isBoss = true,
				attackCooldown = 0,
				specialCooldown = 10,
			}
			table.insert(state.enemies, bossState)
		end
	else
		local count = DungeonGenerator.getEnemyCount(floorNum)
		local available = DungeonGenerator.getEnemiesForFloor(floorNum)
		if #available == 0 then return end

		local positions = DungeonGenerator.getEnemySpawnPositions(
			origin, count, floorNum * 99 + player.UserId)

		local rng = Random.new(floorNum * 777 + player.UserId)
		for i = 1, count do
			local pick = available[rng:NextInteger(1, #available)]
			local enemyState = {
				id = "enemy_" .. floorNum .. "_" .. i,
				name = pick.name,
				data = pick.data,
				hp = pick.data.hp,
				maxHp = pick.data.hp,
				atk = pick.data.atk,
				def = pick.data.def,
				speed = pick.data.speed,
				position = positions[i] or (origin + Vector3.new(i * 5, 3, 0)),
				isBoss = false,
				attackCooldown = 0,
			}
			table.insert(state.enemies, enemyState)
		end
	end

	-- Send initial enemy data to client
	local enemyInfo = {}
	for _, e in ipairs(state.enemies) do
		table.insert(enemyInfo, {
			id = e.id,
			name = e.name,
			hp = e.hp,
			maxHp = e.maxHp,
			position = e.position,
			isBoss = e.isBoss,
			color = e.data.color,
			size = e.data.size,
		})
	end
	EnemyUpdateEvent:FireClient(player, { type = "spawn", enemies = enemyInfo })
end

local function advanceFloor(player)
	local data = DataManager.getData(player)
	local state = getPlayerState(player)
	if not data or not state then return end

	-- Clean up previous floor
	if state.floorFolder then
		DungeonGenerator.destroyFloor(state.floorFolder)
	end

	data.currentFloor = data.currentFloor + 1
	local floorNum = data.currentFloor

	-- Award soul gems for clearing floor
	if floorNum > 1 then
		data.soulGems = data.soulGems + GameConfig.SOUL_GEMS_PER_FLOOR
		data.totalSoulGems = data.totalSoulGems + GameConfig.SOUL_GEMS_PER_FLOOR
	end

	-- Build new floor
	local origin = Vector3.new(0, 0, 0)
	local floorFolder, isBoss = DungeonGenerator.buildFloor(floorNum, origin, dungeonFolder)
	state.floorFolder = floorFolder
	state.floorCleared = false

	-- Spawn enemies
	spawnEnemiesForFloor(player, state, floorNum, origin, isBoss)

	-- Teleport player to spawn point
	local character = player.Character
	if character then
		local hrp = character:FindFirstChild("HumanoidRootPart")
		if hrp then
			hrp.CFrame = CFrame.new(origin + Vector3.new(0, 5, -GameConfig.ROOM_SIZE / 2 + 10))
		end
	end

	FloorUpdateEvent:FireClient(player, {
		floor = floorNum,
		isBoss = isBoss,
		enemyCount = #state.enemies,
	})
	syncData(player)
end

local function handlePlayerDeath(player)
	local data = DataManager.getData(player)
	local state = getPlayerState(player)
	if not data or not state then return end

	DataManager.endRun(player)

	if state.floorFolder then
		DungeonGenerator.destroyFloor(state.floorFolder)
		state.floorFolder = nil
	end
	state.enemies = {}

	GameOverEvent:FireClient(player, {
		floor = data.bestFloor,
		soulGems = data.soulGems,
		totalRuns = data.totalRuns,
	})
	syncData(player)
end

-- ── Remote Event Handlers ───────────────────────────────

-- Class selection + start run
SelectClassEvent.OnServerInvoke = function(player, className)
	if not GameConfig.CLASSES[className] then
		return false, "Invalid class"
	end

	local data = DataManager.initRunStats(player, className)
	if not data then return false, "Failed to init" end

	playerStates[player.UserId] = {
		enemies = {},
		floorFolder = nil,
		abilityCooldown = 0,
		ultimateCooldown = 0,
		attackCooldown = 0,
		floorCleared = false,
		atkBuff = 1,
		atkBuffTimer = 0,
	}

	syncData(player)
	return true, "Class selected: " .. className
end

-- Start run (after class selection)
StartRunEvent.OnServerEvent:Connect(function(player)
	local data = DataManager.getData(player)
	if not data or not data.runActive then return end
	advanceFloor(player)
end)

-- Basic attack
AttackEvent.OnServerEvent:Connect(function(player, targetId)
	local data = DataManager.getData(player)
	local state = getPlayerState(player)
	if not data or not state or data.hp <= 0 then return end

	-- Cooldown check
	if state.attackCooldown > 0 then return end
	state.attackCooldown = GameConfig.ATTACK_COOLDOWN

	-- Find target enemy
	local target = nil
	for _, e in ipairs(state.enemies) do
		if e.id == targetId and e.hp > 0 then
			target = e
			break
		end
	end
	if not target then return end

	-- Apply ATK buff
	local effectiveData = { atk = data.atk * state.atkBuff, def = data.def }
	local damage, killed = CombatManager.playerAttackEnemy(effectiveData, target)

	CombatFeedbackEvent:FireClient(player, {
		type = "player_attack",
		damage = damage,
		targetId = targetId,
		killed = killed,
	})

	if killed then
		data.totalKills = data.totalKills + 1

		-- Loot drops
		local luckBonus = 0
		local upgrades = data.soulUpgrades or {}
		if upgrades["Lucky"] then
			luckBonus = upgrades["Lucky"] * 2
		end

		local drops
		if target.isBoss then
			drops = LootSystem.rollBossDrops(target.data, data.currentFloor, luckBonus)
			data.soulGems = data.soulGems + (target.data.soulGemReward or 5)
			data.totalSoulGems = data.totalSoulGems + (target.data.soulGemReward or 5)
		else
			drops = LootSystem.rollDrops(target.data, data.currentFloor, luckBonus)
		end

		-- Add drops to inventory
		for _, item in ipairs(drops) do
			if item.slot == "soul_gem" then
				data.soulGems = data.soulGems + item.value
				data.totalSoulGems = data.totalSoulGems + item.value
			elseif item.slot == "potion" then
				data.potions = data.potions + 1
			else
				table.insert(data.inventory, item)
			end
		end

		if #drops > 0 then
			LootDropEvent:FireClient(player, drops)
		end

		-- Check if floor is cleared
		local allDead = true
		for _, e in ipairs(state.enemies) do
			if e.hp > 0 then
				allDead = false
				break
			end
		end
		if allDead then
			state.floorCleared = true
			FloorUpdateEvent:FireClient(player, {
				type = "floor_cleared",
				floor = data.currentFloor,
			})
		end
	end

	-- Update enemy states for client
	local enemyUpdates = {}
	for _, e in ipairs(state.enemies) do
		table.insert(enemyUpdates, {
			id = e.id, hp = e.hp, maxHp = e.maxHp,
			position = e.position, stunned = e.stunned, burning = e.burning,
		})
	end
	EnemyUpdateEvent:FireClient(player, { type = "update", enemies = enemyUpdates })
	syncData(player)
end)

-- Use ability
UseAbilityEvent.OnServerEvent:Connect(function(player)
	local data = DataManager.getData(player)
	local state = getPlayerState(player)
	if not data or not state or data.hp <= 0 then return end

	local classData = GameConfig.CLASSES[data.selectedClass]
	if not classData then return end

	if state.abilityCooldown > 0 then return end
	state.abilityCooldown = classData.abilityCooldown

	-- Get enemies in ability range
	local character = player.Character
	if not character then return end
	local hrp = character:FindFirstChild("HumanoidRootPart")
	if not hrp then return end
	local playerPos = hrp.Position

	local targets = {}
	for _, e in ipairs(state.enemies) do
		if e.hp > 0 then
			local dist = (e.position - playerPos).Magnitude
			if dist <= GameConfig.ABILITY_RANGE then
				table.insert(targets, e)
			end
		end
	end

	local effectiveData = { atk = data.atk * state.atkBuff, def = data.def }
	local results = CombatManager.executeAbility(effectiveData, data.selectedClass, targets)

	for _, r in ipairs(results) do
		if r.killed then
			data.totalKills = data.totalKills + 1
		end
	end

	CombatFeedbackEvent:FireClient(player, {
		type = "ability",
		abilityName = classData.ability,
		results = results,
	})

	-- Check floor cleared
	local allDead = true
	for _, e in ipairs(state.enemies) do
		if e.hp > 0 then allDead = false; break end
	end
	if allDead then
		state.floorCleared = true
		FloorUpdateEvent:FireClient(player, { type = "floor_cleared", floor = data.currentFloor })
	end

	syncData(player)
end)

-- Use ultimate
UseUltimateEvent.OnServerEvent:Connect(function(player)
	local data = DataManager.getData(player)
	local state = getPlayerState(player)
	if not data or not state or data.hp <= 0 then return end

	local classData = GameConfig.CLASSES[data.selectedClass]
	if not classData then return end

	if state.ultimateCooldown > 0 then return end
	state.ultimateCooldown = classData.ultimateCooldown

	local character = player.Character
	if not character then return end
	local hrp = character:FindFirstChild("HumanoidRootPart")
	if not hrp then return end

	-- All enemies as targets for AoE ultimates
	local targets = {}
	for _, e in ipairs(state.enemies) do
		if e.hp > 0 then
			table.insert(targets, e)
		end
	end

	local effectiveData = { atk = data.atk * state.atkBuff, def = data.def }
	local results = CombatManager.executeUltimate(effectiveData, data.selectedClass, targets)

	-- Handle buff results (Warrior)
	for _, r in ipairs(results) do
		if r.effect == "buff" and r.stat == "atk" then
			state.atkBuff = r.multiplier
			state.atkBuffTimer = r.duration
		end
		if r.killed then
			data.totalKills = data.totalKills + 1
		end
	end

	CombatFeedbackEvent:FireClient(player, {
		type = "ultimate",
		ultimateName = classData.ultimate,
		results = results,
	})

	syncData(player)
end)

-- Use potion
UsePotionEvent.OnServerEvent:Connect(function(player)
	local data = DataManager.getData(player)
	if not data or data.potions <= 0 then return end

	data.potions = data.potions - 1
	local healAmount = 50
	data.hp = math.min(data.hp + healAmount, data.maxHp)

	CombatFeedbackEvent:FireClient(player, {
		type = "potion",
		healAmount = healAmount,
		newHp = data.hp,
	})
	syncData(player)
end)

-- Equip item from inventory
EquipItemEvent.OnServerInvoke = function(player, inventoryIndex)
	local data = DataManager.getData(player)
	if not data then return false, "No data" end
	if not data.inventory[inventoryIndex] then return false, "Invalid item" end

	local item = data.inventory[inventoryIndex]
	local slot = item.slot
	if not slot or slot == "potion" or slot == "soul_gem" then
		return false, "Cannot equip this item"
	end

	-- Swap with currently equipped
	local old = data.equipment[slot]
	data.equipment[slot] = item
	table.remove(data.inventory, inventoryIndex)
	if old then
		table.insert(data.inventory, old)
	end

	DataManager.recalcStats(player)
	syncData(player)
	return true, "Equipped " .. item.name
end

-- Buy soul upgrade
BuySoulUpgradeEvent.OnServerInvoke = function(player, upgradeName)
	local data = DataManager.getData(player)
	if not data then return false, "No data" end

	-- Find upgrade definition
	local upDef = nil
	for _, u in ipairs(GameConfig.SOUL_UPGRADES) do
		if u.name == upgradeName then
			upDef = u
			break
		end
	end
	if not upDef then return false, "Invalid upgrade" end

	local currentLevel = (data.soulUpgrades[upgradeName] or 0)
	if currentLevel >= upDef.maxLevel then
		return false, "Already at max level"
	end

	local cost = upDef.baseCost * (currentLevel + 1)
	if data.soulGems < cost then
		return false, "Not enough Soul Gems (need " .. cost .. ")"
	end

	data.soulGems = data.soulGems - cost
	data.soulUpgrades[upgradeName] = currentLevel + 1

	syncData(player)
	return true, upgradeName .. " upgraded to level " .. (currentLevel + 1)
end

-- ── Game Loop (enemy AI + cooldowns) ────────────────────
RunService.Heartbeat:Connect(function(dt)
	for _, player in ipairs(Players:GetPlayers()) do
		local state = getPlayerState(player)
		local data = DataManager.getData(player)
		if not state or not data or not data.runActive or data.hp <= 0 then
			continue
		end

		-- Update cooldowns
		if state.attackCooldown > 0 then
			state.attackCooldown = state.attackCooldown - dt
		end
		if state.abilityCooldown > 0 then
			state.abilityCooldown = state.abilityCooldown - dt
		end
		if state.ultimateCooldown > 0 then
			state.ultimateCooldown = state.ultimateCooldown - dt
		end

		-- ATK buff timer
		if state.atkBuffTimer > 0 then
			state.atkBuffTimer = state.atkBuffTimer - dt
			if state.atkBuffTimer <= 0 then
				state.atkBuff = 1
			end
		end

		-- Floor cleared: wait for player to walk to exit
		if state.floorCleared then
			local character = player.Character
			if character then
				local hrp = character:FindFirstChild("HumanoidRootPart")
				if hrp then
					local exitPos = Vector3.new(0, 3, GameConfig.ROOM_SIZE / 2)
					local dist = (hrp.Position - exitPos).Magnitude
					if dist < 8 then
						advanceFloor(player)
					end
				end
			end
			continue
		end

		-- Enemy AI
		local character = player.Character
		if not character then continue end
		local hrp = character:FindFirstChild("HumanoidRootPart")
		if not hrp then continue end
		local playerPos = hrp.Position

		for _, enemy in ipairs(state.enemies) do
			if enemy.hp > 0 then
				local action = CombatManager.enemyAITick(
					enemy, enemy.data, playerPos, dt)

				if action.action == "attack" then
					local damage = CombatManager.enemyAttackPlayer(enemy.data, data)
					CombatFeedbackEvent:FireClient(player, {
						type = "enemy_attack",
						damage = damage,
						enemyName = enemy.name,
						playerHp = data.hp,
					})

					if data.hp <= 0 then
						handlePlayerDeath(player)
						break
					end
				end

				-- Boss special attacks
				if enemy.isBoss and enemy.hp > 0 then
					enemy.specialCooldown = (enemy.specialCooldown or 0) - dt
					if enemy.specialCooldown <= 0 then
						enemy.specialCooldown = 12
						local special = CombatManager.bossSpecialAttack(
							enemy, enemy.data, playerPos)
						if special then
							CombatFeedbackEvent:FireClient(player, {
								type = "boss_special",
								special = special,
								bossName = enemy.name,
							})

							if special.type == "summon" then
								-- Add summoned enemies
								for j, pos in ipairs(special.positions) do
									local summonData = GameConfig.ENEMIES[special.enemy]
									if summonData then
										table.insert(state.enemies, {
											id = "summon_" .. os.clock() .. "_" .. j,
											name = special.enemy,
											data = summonData,
											hp = summonData.hp,
											maxHp = summonData.hp,
											atk = summonData.atk,
											def = summonData.def,
											speed = summonData.speed,
											position = pos,
											isBoss = false,
											attackCooldown = 0,
										})
									end
								end
							elseif special.type == "breath" or special.type == "phase" then
								local dist = (playerPos - enemy.position).Magnitude
								if dist <= (special.range or special.aoeRange or 15) then
									local damage = math.floor(special.damage or special.aoeDamage or 0)
									data.hp = data.hp - damage
									CombatFeedbackEvent:FireClient(player, {
										type = "boss_damage",
										damage = damage,
										playerHp = data.hp,
									})
									if data.hp <= 0 then
										handlePlayerDeath(player)
									end
								end
							end
						end
					end
				end
			end
		end
	end
end)

-- ── Auto-Save ───────────────────────────────────────────
local saveTimer = 0
RunService.Heartbeat:Connect(function(dt)
	saveTimer = saveTimer + dt
	if saveTimer < GameConfig.AUTO_SAVE_INTERVAL then return end
	saveTimer = 0
	for _, player in ipairs(Players:GetPlayers()) do
		DataManager.saveData(player)
	end
end)

-- ── Player Join/Leave ───────────────────────────────────
Players.PlayerAdded:Connect(function(player)
	DataManager.loadData(player)
	syncData(player)

	-- Respawn handler
	player.CharacterAdded:Connect(function(character)
		local data = DataManager.getData(player)
		if data and data.runActive and data.hp > 0 then
			-- Restore run state if player respawns mid-run
			local hrp = character:WaitForChild("HumanoidRootPart")
			hrp.CFrame = CFrame.new(0, 5, -GameConfig.ROOM_SIZE / 2 + 10)
		end
	end)
end)

Players.PlayerRemoving:Connect(function(player)
	DataManager.saveData(player)
	DataManager.clearCache(player)

	-- Clean up dungeon
	local state = playerStates[player.UserId]
	if state and state.floorFolder then
		DungeonGenerator.destroyFloor(state.floorFolder)
	end
	playerStates[player.UserId] = nil
end)

game:BindToClose(function()
	for _, player in ipairs(Players:GetPlayers()) do
		DataManager.saveData(player)
	end
end)

print("[ShadowDungeon] GameManager loaded successfully")
