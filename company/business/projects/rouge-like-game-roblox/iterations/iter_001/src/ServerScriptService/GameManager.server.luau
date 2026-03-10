--[[
	GameManager.luau — Main game orchestration
	ServerScriptService/GameManager

	Controls the full game loop:
	1. Player joins → load data → hub
	2. Player starts run → generate dungeon → spawn in room 1
	3. Room loop: spawn enemies → combat → clear → talent pick → next room
	4. Boss fight → run complete → results → return to hub
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local GameConfig = require(ReplicatedStorage.Shared.GameConfig)
local SkillDatabase = require(ReplicatedStorage.Shared.SkillDatabase)
local EnemyDatabase = require(ReplicatedStorage.Shared.EnemyDatabase)

local DungeonGenerator = require(script.Parent.DungeonGenerator)
local CombatSystem = require(script.Parent.CombatSystem)
local EnemyAI = require(script.Parent.EnemyAI)
local LootSystem = require(script.Parent.LootSystem)
local PlayerDataManager = require(script.Parent.PlayerDataManager)

local GameManager = {}

-- Run state per active run
GameManager._activeRuns = {} :: { [Player]: RunState }

export type RunState = {
	floorIndex: number,
	roomSequence: { any },
	currentRoomIndex: number,
	currentRoomModel: Model?,
	dungeonModels: { Model },
	enemiesAlive: { Model },
	totalKills: number,
	startTime: number,
	soulShardsEarned: number,
	xpEarned: number,
	isInCombat: boolean,
	seed: number,
}

--------------------------------------------------
-- Initialization
--------------------------------------------------

function GameManager.init()
	-- Create RemoteEvents
	GameManager._createRemoteEvents()

	-- Setup player data auto-save
	PlayerDataManager.setupAutoSave(120)

	-- Player join / leave
	Players.PlayerAdded:Connect(function(player)
		GameManager._onPlayerJoined(player)
	end)

	Players.PlayerRemoving:Connect(function(player)
		GameManager._onPlayerLeaving(player)
	end)

	-- Handle already-connected players
	for _, player in Players:GetPlayers() do
		task.spawn(function()
			GameManager._onPlayerJoined(player)
		end)
	end

	-- Server heartbeat
	RunService.Heartbeat:Connect(function(dt)
		GameManager._update(dt)
	end)

	print("[GameManager] Soul Rift initialized")
end

--------------------------------------------------
-- Remote events setup
--------------------------------------------------

function GameManager._createRemoteEvents()
	local events = {
		"CombatEvent",      -- Server → Client: combat VFX
		"SkillEvent",       -- Client → Server: use skill
		"DodgeEvent",       -- Client → Server: dodge
		"UltimateEvent",    -- Client → Server: use ultimate
		"TalentSelectEvent",-- Client → Server: pick talent card
		"StartRunEvent",    -- Client → Server: start dungeon run
		"ReturnToHubEvent", -- Client → Server: return to hub
		"EnemyAttackEvent", -- Server internal: enemy damage
	}

	for _, name in events do
		if not ReplicatedStorage:FindFirstChild(name) then
			local event = Instance.new("RemoteEvent")
			event.Name = name
			event.Parent = ReplicatedStorage
		end
	end

	-- Connect handlers
	ReplicatedStorage.SkillEvent.OnServerEvent:Connect(function(player, skillIndex, target)
		GameManager._onSkillUsed(player, skillIndex, target)
	end)

	ReplicatedStorage.DodgeEvent.OnServerEvent:Connect(function(player)
		CombatSystem.tryDodge(player)
	end)

	ReplicatedStorage.UltimateEvent.OnServerEvent:Connect(function(player)
		GameManager._onUltimateUsed(player)
	end)

	ReplicatedStorage.TalentSelectEvent.OnServerEvent:Connect(function(player, talentId)
		GameManager._onTalentSelected(player, talentId)
	end)

	ReplicatedStorage.StartRunEvent.OnServerEvent:Connect(function(player)
		GameManager.startRun(player)
	end)

	ReplicatedStorage.ReturnToHubEvent.OnServerEvent:Connect(function(player)
		GameManager.endRun(player, false)
	end)
end

--------------------------------------------------
-- Player join/leave
--------------------------------------------------

function GameManager._onPlayerJoined(player: Player)
	-- Load persistent data
	local data = PlayerDataManager.loadPlayer(player)
	print("[GameManager] Player joined:", player.Name, "Level:", data.level)

	-- Spawn in hub
	player.CharacterAdded:Connect(function(character)
		-- Setup character for combat
		task.wait(0.5) -- wait for character to load
		local rootPart = character:FindFirstChild("HumanoidRootPart")
		if rootPart then
			rootPart.Position = Vector3.new(0, 10, 100) -- Hub spawn point
		end
	end)
end

function GameManager._onPlayerLeaving(player: Player)
	-- End any active run
	if GameManager._activeRuns[player] then
		GameManager._cleanupRun(player)
	end
	CombatSystem.removePlayerState(player)
end

--------------------------------------------------
-- Run lifecycle
--------------------------------------------------

function GameManager.startRun(player: Player)
	if GameManager._activeRuns[player] then
		warn("[GameManager] Player already in a run:", player.Name)
		return
	end

	local data = PlayerDataManager.getData(player)
	if not data then return end

	local classId = data.selectedClass
	local seed = math.random(1, 999999)

	-- Initialize combat state
	CombatSystem.initPlayerState(player, classId)

	-- Apply equipped items to stats
	local state = CombatSystem.getPlayerState(player)
	if state and data.equippedWeapon then
		for stat, value in data.equippedWeapon.stats or {} do
			state.stats[stat] = (state.stats[stat] or 0) + value
		end
	end
	if state and data.equippedArmor then
		for stat, value in data.equippedArmor.stats or {} do
			state.stats[stat] = (state.stats[stat] or 0) + value
		end
	end

	-- Generate dungeon
	local floorIndex = 1
	local roomSequence = DungeonGenerator.generateFloorSequence(floorIndex, seed)

	-- Build dungeon geometry
	local dungeonModels = DungeonGenerator.buildFloor(roomSequence)

	-- Create run state
	local runState: RunState = {
		floorIndex = floorIndex,
		roomSequence = roomSequence,
		currentRoomIndex = 0,
		currentRoomModel = nil,
		dungeonModels = dungeonModels,
		enemiesAlive = {},
		totalKills = 0,
		startTime = tick(),
		soulShardsEarned = 0,
		xpEarned = 0,
		isInCombat = false,
		seed = seed,
	}

	GameManager._activeRuns[player] = runState

	-- Start first room
	GameManager._enterRoom(player, 1)

	print("[GameManager] Run started:", player.Name, "Class:", classId, "Seed:", seed)
end

function GameManager.endRun(player: Player, cleared: boolean)
	local runState = GameManager._activeRuns[player]
	if not runState then return end

	local duration = tick() - runState.startTime

	-- Save progress
	local data = PlayerDataManager.getData(player)
	if data then
		PlayerDataManager.addXP(player, runState.xpEarned)
		PlayerDataManager.addSoulShards(player, runState.soulShardsEarned)
		PlayerDataManager.addKills(player, runState.totalKills)
		PlayerDataManager.recordRunComplete(player, cleared, runState.floorIndex, duration)
		PlayerDataManager.savePlayer(player)
	end

	-- Send results to client
	local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
	if combatEvent then
		combatEvent:FireClient(player, "RunComplete", {
			cleared = cleared,
			roomsCleared = runState.currentRoomIndex,
			enemiesKilled = runState.totalKills,
			xpEarned = runState.xpEarned,
			soulShards = runState.soulShardsEarned,
			duration = duration,
		})
	end

	-- Cleanup
	GameManager._cleanupRun(player)

	-- Teleport back to hub
	local character = player.Character
	if character and character:FindFirstChild("HumanoidRootPart") then
		character.HumanoidRootPart.Position = Vector3.new(0, 10, 100)
	end

	print("[GameManager] Run ended:", player.Name, "Cleared:", cleared, "Duration:", math.floor(duration))
end

--------------------------------------------------
-- Room progression
--------------------------------------------------

function GameManager._enterRoom(player: Player, roomIndex: number)
	local runState = GameManager._activeRuns[player]
	if not runState then return end

	if roomIndex > #runState.roomSequence then
		-- All rooms cleared — run complete!
		GameManager.endRun(player, true)
		return
	end

	runState.currentRoomIndex = roomIndex
	local roomData = runState.roomSequence[roomIndex]

	-- Notify client of room transition
	local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
	if combatEvent then
		combatEvent:FireClient(player, "RoomTransition", {
			roomType = roomData.type,
			roomIndex = roomIndex,
		})
	end

	-- Teleport player to room
	local roomSpacing = GameConfig.ROOM_SIZE.z + GameConfig.CORRIDOR_LENGTH
	local roomPos = Vector3.new(0, 5, -(roomIndex - 1) * roomSpacing)

	task.delay(GameConfig.ROOM_TRANSITION_TIME, function()
		local character = player.Character
		if character and character:FindFirstChild("HumanoidRootPart") then
			character.HumanoidRootPart.CFrame = CFrame.new(roomPos)
		end

		-- Handle room by type
		GameManager._processRoom(player, roomData)
	end)
end

function GameManager._processRoom(player: Player, roomData: any)
	local runState = GameManager._activeRuns[player]
	if not runState then return end

	local roomType = roomData.type

	if roomType == GameConfig.ROOM_TYPES.COMBAT or roomType == GameConfig.ROOM_TYPES.ELITE then
		-- Spawn enemies
		local enemyIds = DungeonGenerator.getEnemySpawns(roomData)
		runState.isInCombat = true
		runState.enemiesAlive = {}

		local roomCenter = Vector3.new(0, 0, -(roomData.index - 1) * (GameConfig.ROOM_SIZE.z + GameConfig.CORRIDOR_LENGTH))
		local roomHalfSize = GameConfig.ROOM_SIZE.x / 2 - 5

		for _, enemyId in enemyIds do
			local spawnPos = roomCenter + Vector3.new(
				math.random(-roomHalfSize, roomHalfSize),
				0,
				math.random(-roomHalfSize, roomHalfSize)
			)
			local enemyModel = EnemyAI.spawnEnemy(enemyId, spawnPos)
			if enemyModel then
				CombatSystem.initEnemyState(enemyModel, EnemyDatabase.Enemies[enemyId])
				table.insert(runState.enemiesAlive, enemyModel)
			end
		end

	elseif roomType == GameConfig.ROOM_TYPES.BOSS then
		-- Spawn boss
		runState.isInCombat = true
		runState.enemiesAlive = {}

		local bossId = roomData.bossId
		local bossDef = EnemyDatabase.Bosses[bossId]
		if bossDef then
			local roomCenter = Vector3.new(0, 0, -(roomData.index - 1) * (GameConfig.ROOM_SIZE.z + GameConfig.CORRIDOR_LENGTH))
			local bossModel = EnemyAI.spawnEnemy("shadow_knight", roomCenter) -- placeholder model
			if bossModel then
				bossModel.Name = bossId
				-- Init boss with boss stats
				CombatSystem.initEnemyState(bossModel, {
					id = bossId,
					health = bossDef.health,
					attack = bossDef.attack,
					defense = bossDef.defense,
					moveSpeed = bossDef.moveSpeed,
					behavior = bossDef.phases[1].behavior,
					xpReward = bossDef.xpReward,
					soulShards = bossDef.soulShards,
				})
				table.insert(runState.enemiesAlive, bossModel)
			end
		end

	elseif roomType == GameConfig.ROOM_TYPES.TREASURE then
		local data = PlayerDataManager.getData(player)
		if data then
			local loot = LootSystem.generateTreasureRoomLoot(data.selectedClass, runState.floorIndex)
			runState.soulShardsEarned += loot.soulShards
			if loot.equipment then
				PlayerDataManager.addToInventory(player, loot.equipment)
			end
		end
		-- Auto-advance after brief delay
		task.delay(3, function()
			GameManager._onRoomCleared(player)
		end)

	elseif roomType == GameConfig.ROOM_TYPES.SHOP then
		local data = PlayerDataManager.getData(player)
		if data then
			local inventory = LootSystem.generateShopInventory(data.selectedClass, runState.floorIndex)
			local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
			if combatEvent then
				combatEvent:FireClient(player, "ShopOpen", { inventory = inventory })
			end
		end
		-- Player exits shop manually or auto-advance after timeout
		task.delay(30, function()
			if GameManager._activeRuns[player] and runState.currentRoomIndex == runState.roomSequence[runState.currentRoomIndex] then
				GameManager._onRoomCleared(player)
			end
		end)

	elseif roomType == GameConfig.ROOM_TYPES.REST then
		-- Heal player
		local state = CombatSystem.getPlayerState(player)
		if state then
			local healAmount = math.floor(state.stats.maxHealth * 0.3)
			CombatSystem.healPlayer(player, healAmount)
		end
		-- Check second_wind talent
		if state then
			for _, talent in state.talents do
				if talent.effect and talent.effect.event == "room_enter" and talent.effect.action == "heal" then
					local extraHeal = math.floor(state.stats.maxHealth * talent.effect.value)
					CombatSystem.healPlayer(player, extraHeal)
				end
			end
		end
		task.delay(5, function()
			GameManager._onRoomCleared(player)
		end)
	end
end

--------------------------------------------------
-- Room cleared logic
--------------------------------------------------

function GameManager._onRoomCleared(player: Player)
	local runState = GameManager._activeRuns[player]
	if not runState then return end

	runState.isInCombat = false

	-- Notify client
	local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
	if combatEvent then
		combatEvent:FireClient(player, "RoomCleared")
	end

	-- Offer talent cards after combat rooms
	local roomData = runState.roomSequence[runState.currentRoomIndex]
	if roomData and (roomData.type == GameConfig.ROOM_TYPES.COMBAT or roomData.type == GameConfig.ROOM_TYPES.ELITE) then
		local state = CombatSystem.getPlayerState(player)
		local data = PlayerDataManager.getData(player)
		if state and data then
			local ownedIds = {}
			for _, t in state.talents do
				table.insert(ownedIds, t.id)
			end
			local cards = LootSystem.rollTalentCards(data.selectedClass, ownedIds)
			if #cards > 0 and combatEvent then
				combatEvent:FireClient(player, "TalentCards", { cards = cards })
				-- Wait for talent selection before advancing
				return
			end
		end
	end

	-- Advance to next room
	GameManager._enterRoom(player, runState.currentRoomIndex + 1)
end

--------------------------------------------------
-- Input handlers
--------------------------------------------------

function GameManager._onSkillUsed(player: Player, skillIndex: number, target: Model?)
	local state = CombatSystem.getPlayerState(player)
	if not state or state.isDead then return end

	local classDef = SkillDatabase.Classes[state.classId]
	if not classDef or not classDef.skills[skillIndex] then return end

	local skill = classDef.skills[skillIndex]
	if not CombatSystem.useSkill(player, skill.id) then return end

	-- Apply damage to target or nearest enemy
	local runState = GameManager._activeRuns[player]
	if not runState then return end

	if target and CombatSystem.getEnemyState(target) then
		CombatSystem.damageEnemy(player, target, skill.damageMultiplier or 1.0)
	else
		-- Find nearest enemy in range
		local character = player.Character
		if not character or not character:FindFirstChild("HumanoidRootPart") then return end
		local pos = character.HumanoidRootPart.Position

		local range = skill.range or 10
		for _, enemy in runState.enemiesAlive do
			if enemy.Parent and enemy:FindFirstChild("HumanoidRootPart") then
				local dist = (enemy.HumanoidRootPart.Position - pos).Magnitude
				if dist <= range then
					local dmgMult = skill.damageMultiplier
					if type(dmgMult) == "table" then
						-- Combo: use appropriate hit multiplier
						local comboHit = (state.comboCount % #dmgMult) + 1
						dmgMult = dmgMult[comboHit]
					end
					CombatSystem.damageEnemy(player, enemy, dmgMult or 1.0)
					break
				end
			end
		end
	end

	-- Check if all enemies dead
	GameManager._checkRoomComplete(player)
end

function GameManager._onUltimateUsed(player: Player)
	if not CombatSystem.useUltimate(player) then return end

	local state = CombatSystem.getPlayerState(player)
	if not state then return end

	local classDef = SkillDatabase.Classes[state.classId]
	if not classDef then return end

	local ult = classDef.ultimate
	local runState = GameManager._activeRuns[player]
	if not runState then return end

	-- Area damage to all enemies in radius
	local character = player.Character
	if not character or not character:FindFirstChild("HumanoidRootPart") then return end
	local pos = character.HumanoidRootPart.Position

	for _, enemy in runState.enemiesAlive do
		if enemy.Parent and enemy:FindFirstChild("HumanoidRootPart") then
			local dist = (enemy.HumanoidRootPart.Position - pos).Magnitude
			if dist <= (ult.radius or 15) then
				CombatSystem.damageEnemy(player, enemy, ult.damageMultiplier or 2.0)
			end
		end
	end

	GameManager._checkRoomComplete(player)
end

function GameManager._onTalentSelected(player: Player, talentId: string)
	local state = CombatSystem.getPlayerState(player)
	if not state then return end

	-- Find talent in database
	local data = PlayerDataManager.getData(player)
	if not data then return end

	local pool = require(ReplicatedStorage.Shared.TalentCardDatabase).getPoolForClass(data.selectedClass)
	for _, card in pool do
		if card.id == talentId then
			CombatSystem.addTalent(player, card)
			break
		end
	end

	-- Continue to next room
	local runState = GameManager._activeRuns[player]
	if runState then
		GameManager._enterRoom(player, runState.currentRoomIndex + 1)
	end
end

--------------------------------------------------
-- Combat checks
--------------------------------------------------

function GameManager._checkRoomComplete(player: Player)
	local runState = GameManager._activeRuns[player]
	if not runState or not runState.isInCombat then return end

	-- Remove dead enemies
	for i = #runState.enemiesAlive, 1, -1 do
		local enemy = runState.enemiesAlive[i]
		local enemyState = CombatSystem.getEnemyState(enemy)
		if not enemyState or enemyState.isDead then
			-- Generate drops
			local data = PlayerDataManager.getData(player)
			if data and enemyState then
				local drops = LootSystem.generateEnemyDrops(enemyState.id, data.selectedClass)
				runState.soulShardsEarned += drops.soulShards
				runState.xpEarned += drops.xp
				if drops.equipment then
					PlayerDataManager.addToInventory(player, drops.equipment)
				end
			end

			runState.totalKills += 1
			CombatSystem.removeEnemyState(enemy)
			EnemyAI.removeEnemy(enemy)
			table.remove(runState.enemiesAlive, i)
		end
	end

	-- Check if room is clear
	if #runState.enemiesAlive == 0 then
		GameManager._onRoomCleared(player)
	end
end

--------------------------------------------------
-- Update loop
--------------------------------------------------

function GameManager._update(dt: number)
	-- Update combat system (cooldowns, buffs)
	CombatSystem.update(dt)

	-- Update enemy AI
	EnemyAI.update(dt)

	-- Check for dead players
	for player, runState in GameManager._activeRuns do
		local state = CombatSystem.getPlayerState(player)
		if state and state.isDead then
			local combatEvent = ReplicatedStorage:FindFirstChild("CombatEvent")
			if combatEvent then
				combatEvent:FireClient(player, "PlayerDied")
			end
			task.delay(3, function()
				GameManager.endRun(player, false)
			end)
		end
	end
end

--------------------------------------------------
-- Cleanup
--------------------------------------------------

function GameManager._cleanupRun(player: Player)
	local runState = GameManager._activeRuns[player]
	if not runState then return end

	-- Destroy dungeon
	for _, model in runState.dungeonModels do
		if model.Parent then
			model:Destroy()
		end
	end

	-- Clean up enemies
	for _, enemy in runState.enemiesAlive do
		CombatSystem.removeEnemyState(enemy)
		EnemyAI.removeEnemy(enemy)
	end

	CombatSystem.removePlayerState(player)
	GameManager._activeRuns[player] = nil
end

-- Boot
GameManager.init()

return GameManager
