--[[
	PlayerDataManager.luau — Player data persistence
	ServerScriptService/PlayerDataManager

	Stores: permanent progression, equipment inventory, unlocks.
	Uses Roblox DataStoreService for persistence.
]]

local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local PlayerDataManager = {}

-- In-memory cache
PlayerDataManager._cache = {} :: { [number]: PlayerData }

local DATA_STORE_NAME = "SoulRift_PlayerData_v1"
local dataStore = DataStoreService:GetDataStore(DATA_STORE_NAME)

export type PlayerData = {
	-- Profile
	level: number,
	xp: number,
	xpToNextLevel: number,
	soulShards: number,
	totalRuns: number,
	totalClears: number,

	-- Unlocks
	unlockedClasses: { string },
	selectedClass: string,

	-- Equipment inventory
	inventory: { any },
	equippedWeapon: any?,
	equippedArmor: any?,

	-- Stats
	totalKills: number,
	highestFloor: number,
	fastestClear: number?, -- seconds

	-- Settings
	autoAttack: boolean,
}

local DEFAULT_DATA: PlayerData = {
	level = 1,
	xp = 0,
	xpToNextLevel = 100,
	soulShards = 0,
	totalRuns = 0,
	totalClears = 0,
	unlockedClasses = { "blade_dancer", "soul_caster" },
	selectedClass = "blade_dancer",
	inventory = {},
	equippedWeapon = nil,
	equippedArmor = nil,
	totalKills = 0,
	highestFloor = 0,
	fastestClear = nil,
	autoAttack = true,
}

--------------------------------------------------
-- Load / Save
--------------------------------------------------

function PlayerDataManager.loadPlayer(player: Player): PlayerData
	local userId = player.UserId
	local success, data = pcall(function()
		return dataStore:GetAsync("player_" .. userId)
	end)

	if success and data then
		-- Merge with defaults for any missing fields
		for key, default in DEFAULT_DATA do
			if data[key] == nil then
				data[key] = default
			end
		end
		PlayerDataManager._cache[userId] = data
	else
		-- New player or load failure — use defaults
		local newData = {}
		for k, v in DEFAULT_DATA do
			if type(v) == "table" then
				newData[k] = table.clone(v)
			else
				newData[k] = v
			end
		end
		PlayerDataManager._cache[userId] = newData
	end

	return PlayerDataManager._cache[userId]
end

function PlayerDataManager.savePlayer(player: Player): boolean
	local userId = player.UserId
	local data = PlayerDataManager._cache[userId]
	if not data then return false end

	local success, err = pcall(function()
		dataStore:SetAsync("player_" .. userId, data)
	end)

	if not success then
		warn("[PlayerDataManager] Save failed for", userId, ":", err)
	end
	return success
end

function PlayerDataManager.getData(player: Player): PlayerData?
	return PlayerDataManager._cache[player.UserId]
end

function PlayerDataManager.removePlayer(player: Player)
	PlayerDataManager.savePlayer(player)
	PlayerDataManager._cache[player.UserId] = nil
end

--------------------------------------------------
-- Progression
--------------------------------------------------

function PlayerDataManager.addXP(player: Player, amount: number): boolean
	local data = PlayerDataManager._cache[player.UserId]
	if not data then return false end

	data.xp += amount
	local leveled = false

	while data.xp >= data.xpToNextLevel do
		data.xp -= data.xpToNextLevel
		data.level += 1
		data.xpToNextLevel = math.floor(100 * (1.15 ^ (data.level - 1)))
		leveled = true
	end

	return leveled
end

function PlayerDataManager.addSoulShards(player: Player, amount: number)
	local data = PlayerDataManager._cache[player.UserId]
	if not data then return end
	data.soulShards += amount
end

function PlayerDataManager.spendSoulShards(player: Player, amount: number): boolean
	local data = PlayerDataManager._cache[player.UserId]
	if not data or data.soulShards < amount then return false end
	data.soulShards -= amount
	return true
end

--------------------------------------------------
-- Inventory
--------------------------------------------------

function PlayerDataManager.addToInventory(player: Player, item: any): boolean
	local data = PlayerDataManager._cache[player.UserId]
	if not data then return false end

	table.insert(data.inventory, item)
	return true
end

function PlayerDataManager.equipItem(player: Player, itemId: string): boolean
	local data = PlayerDataManager._cache[player.UserId]
	if not data then return false end

	for _, item in data.inventory do
		if item.id == itemId then
			if item.slot == "weapon" then
				data.equippedWeapon = item
			elseif item.slot == "armor" then
				data.equippedArmor = item
			end
			return true
		end
	end
	return false
end

--------------------------------------------------
-- Run completion
--------------------------------------------------

function PlayerDataManager.recordRunComplete(player: Player, cleared: boolean, floorReached: number, duration: number)
	local data = PlayerDataManager._cache[player.UserId]
	if not data then return end

	data.totalRuns += 1
	if cleared then
		data.totalClears += 1
	end
	if floorReached > data.highestFloor then
		data.highestFloor = floorReached
	end
	if cleared and (not data.fastestClear or duration < data.fastestClear) then
		data.fastestClear = duration
	end
end

function PlayerDataManager.addKills(player: Player, count: number)
	local data = PlayerDataManager._cache[player.UserId]
	if not data then return end
	data.totalKills += count
end

--------------------------------------------------
-- Auto-save setup
--------------------------------------------------

function PlayerDataManager.setupAutoSave(interval: number?)
	local saveInterval = interval or 120 -- 2 minutes

	task.spawn(function()
		while true do
			task.wait(saveInterval)
			for _, player in Players:GetPlayers() do
				PlayerDataManager.savePlayer(player)
			end
		end
	end)

	-- Save on player leaving
	Players.PlayerRemoving:Connect(function(player)
		PlayerDataManager.removePlayer(player)
	end)
end

return PlayerDataManager
