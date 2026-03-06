-- DataManager: player data persistence via Roblox DataStore
local DataStoreService = game:GetService("DataStoreService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))

local DataManager = {}

local dataStore = DataStoreService:GetDataStore(GameConfig.DATASTORE_NAME)
local playerCache = {}

-- Default data for a new player
local function defaultData()
	return {
		-- Meta-progression (persists across runs)
		soulGems = 0,
		totalSoulGems = 0,
		bestFloor = 0,
		totalRuns = 0,
		totalKills = 0,
		selectedClass = "Warrior",

		-- Soul gem upgrades (permanent)
		soulUpgrades = {}, -- { [upgradeName] = level }

		-- Current run state (reset on death)
		currentFloor = 0,
		runActive = false,

		-- Equipped items (reset on death)
		equipment = {}, -- { weapon = item, helmet = item, chest = item, boots = item }

		-- Inventory (reset on death)
		inventory = {}, -- list of items
		potions = 0,

		-- Current run combat stats (calculated from class + gear + upgrades)
		hp = 0,
		maxHp = 0,
		atk = 0,
		def = 0,
		speed = 0,
	}
end

function DataManager.loadData(player)
	local key = "player_" .. player.UserId
	local success, data = pcall(function()
		return dataStore:GetAsync(key)
	end)

	if success and data then
		-- Merge with defaults to handle schema evolution
		local defaults = defaultData()
		for k, v in pairs(defaults) do
			if data[k] == nil then
				data[k] = v
			end
		end
		playerCache[player.UserId] = data
	else
		playerCache[player.UserId] = defaultData()
	end

	return playerCache[player.UserId]
end

function DataManager.saveData(player)
	local data = playerCache[player.UserId]
	if not data then return end

	local key = "player_" .. player.UserId
	local success, err = pcall(function()
		dataStore:SetAsync(key, data)
	end)

	if not success then
		warn("[DataManager] Failed to save for " .. player.Name .. ": " .. tostring(err))
	end
end

function DataManager.getData(player)
	return playerCache[player.UserId]
end

function DataManager.clearCache(player)
	playerCache[player.UserId] = nil
end

-- Initialize run stats based on class + soul upgrades
function DataManager.initRunStats(player, className)
	local data = playerCache[player.UserId]
	if not data then return end

	local classData = GameConfig.CLASSES[className]
	if not classData then return end

	data.selectedClass = className
	data.runActive = true
	data.currentFloor = 0
	data.equipment = {}
	data.inventory = {}
	data.potions = 2 -- Start with 2 health potions

	-- Base stats from class
	data.maxHp = classData.hp
	data.atk = classData.atk
	data.def = classData.def
	data.speed = classData.speed

	-- Apply soul gem upgrades
	local upgrades = data.soulUpgrades or {}
	for _, upDef in ipairs(GameConfig.SOUL_UPGRADES) do
		local level = upgrades[upDef.name] or 0
		if level > 0 then
			if upDef.stat == "hp" then
				data.maxHp = data.maxHp + upDef.perLevel * level
			elseif upDef.stat == "atk" then
				data.atk = data.atk + upDef.perLevel * level
			elseif upDef.stat == "def" then
				data.def = data.def + upDef.perLevel * level
			elseif upDef.stat == "speed" then
				data.speed = data.speed + upDef.perLevel * level
			end
		end
	end

	data.hp = data.maxHp
	return data
end

-- End run (death or quit)
function DataManager.endRun(player)
	local data = playerCache[player.UserId]
	if not data then return end

	data.totalRuns = data.totalRuns + 1
	if data.currentFloor > data.bestFloor then
		data.bestFloor = data.currentFloor
	end

	data.runActive = false
	data.currentFloor = 0
	data.equipment = {}
	data.inventory = {}
	data.potions = 0
	data.hp = 0
end

-- Apply equipment stats
function DataManager.recalcStats(player)
	local data = playerCache[player.UserId]
	if not data then return end

	local classData = GameConfig.CLASSES[data.selectedClass]
	if not classData then return end

	-- Reset to base
	data.maxHp = classData.hp
	data.atk = classData.atk
	data.def = classData.def
	data.speed = classData.speed

	-- Soul upgrades
	local upgrades = data.soulUpgrades or {}
	for _, upDef in ipairs(GameConfig.SOUL_UPGRADES) do
		local level = upgrades[upDef.name] or 0
		if level > 0 then
			if upDef.stat == "hp" then
				data.maxHp = data.maxHp + upDef.perLevel * level
			elseif upDef.stat == "atk" then
				data.atk = data.atk + upDef.perLevel * level
			elseif upDef.stat == "def" then
				data.def = data.def + upDef.perLevel * level
			elseif upDef.stat == "speed" then
				data.speed = data.speed + upDef.perLevel * level
			end
		end
	end

	-- Equipment bonuses
	for _, item in pairs(data.equipment) do
		if item.atk then data.atk = data.atk + item.atk end
		if item.def then data.def = data.def + item.def end
		if item.hp then data.maxHp = data.maxHp + item.hp end
		if item.speed then data.speed = data.speed + item.speed end
	end

	-- Clamp HP
	if data.hp > data.maxHp then
		data.hp = data.maxHp
	end
end

return DataManager
