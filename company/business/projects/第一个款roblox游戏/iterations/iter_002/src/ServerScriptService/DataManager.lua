-- DataManager: handles player data persistence via DataStoreService
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local GameConfig = require(game.ReplicatedStorage:WaitForChild("GameConfig"))

local DataManager = {}

local dataStore
local success, result = pcall(function()
	return DataStoreService:GetDataStore(GameConfig.DATASTORE_NAME)
end)
if success then
	dataStore = result
else
	warn("[DataManager] DataStore unavailable, using memory-only storage: " .. tostring(result))
end

local DEFAULT_DATA = {
	code = 0,
	cash = 0,
	rebirths = 0,
	click_multiplier = 1,
	auto_code_per_sec = 0,
	upgrades_owned = {},
	total_code_generated = 0,
	total_cash_earned = 0,
}

-- In-memory cache
local playerData = {}

function DataManager.loadData(player)
	local key = "Player_" .. player.UserId
	local success, data = false, nil
	if dataStore then
		success, data = pcall(function()
			return dataStore:GetAsync(key)
		end)
	end

	if success and data then
		-- Merge with defaults in case of schema changes
		for k, v in pairs(DEFAULT_DATA) do
			if data[k] == nil then
				data[k] = v
			end
		end
		playerData[player.UserId] = data
	else
		playerData[player.UserId] = table.clone(DEFAULT_DATA)
	end

	return playerData[player.UserId]
end

function DataManager.saveData(player)
	local data = playerData[player.UserId]
	if not data then return false end

	if not dataStore then return true end
	local key = "Player_" .. player.UserId
	local success, err = pcall(function()
		dataStore:SetAsync(key, data)
	end)

	if not success then
		warn("[DataManager] Failed to save data for " .. player.Name .. ": " .. tostring(err))
	end
	return success
end

function DataManager.getData(player)
	return playerData[player.UserId]
end

function DataManager.clearCache(player)
	playerData[player.UserId] = nil
end

return DataManager
