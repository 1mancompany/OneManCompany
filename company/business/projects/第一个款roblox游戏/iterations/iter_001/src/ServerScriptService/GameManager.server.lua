-- GameManager: main server script for Tech Startup Tycoon
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))
local DataManager = require(script.Parent:WaitForChild("DataManager"))

-- Create RemoteEvents / RemoteFunctions
local function createRemote(className, name)
	local remote = Instance.new(className)
	remote.Name = name
	remote.Parent = ReplicatedStorage
	return remote
end

local ClickEvent = createRemote("RemoteEvent", "ClickEvent")
local SellCodeEvent = createRemote("RemoteEvent", "SellCodeEvent")
local BuyUpgradeEvent = createRemote("RemoteFunction", "BuyUpgradeEvent")
local RebirthEvent = createRemote("RemoteFunction", "RebirthEvent")
local DataUpdateEvent = createRemote("RemoteEvent", "DataUpdateEvent")

-- Send updated data to client
local function syncData(player)
	local data = DataManager.getData(player)
	if data then
		DataUpdateEvent:FireClient(player, data)
	end
end

-- Handle click
ClickEvent.OnServerEvent:Connect(function(player)
	local data = DataManager.getData(player)
	if not data then return end

	local rebirthBonus = GameConfig.REBIRTH_MULTIPLIER ^ data.rebirths
	local codeGained = data.click_multiplier * rebirthBonus
	data.code = data.code + codeGained
	data.total_code_generated = data.total_code_generated + codeGained

	syncData(player)
end)

-- Handle sell code
SellCodeEvent.OnServerEvent:Connect(function(player)
	local data = DataManager.getData(player)
	if not data then return end
	if data.code < GameConfig.CODE_TO_CASH_RATE then return end

	local cashGained = math.floor(data.code / GameConfig.CODE_TO_CASH_RATE)
	data.cash = data.cash + cashGained
	data.total_cash_earned = data.total_cash_earned + cashGained
	data.code = data.code % GameConfig.CODE_TO_CASH_RATE

	syncData(player)
end)

-- Handle buy upgrade
BuyUpgradeEvent.OnServerInvoke = function(player, upgradeIndex)
	local data = DataManager.getData(player)
	if not data then return false, "No data" end

	local upgrade = GameConfig.UPGRADES[upgradeIndex]
	if not upgrade then return false, "Invalid upgrade" end

	if data.cash < upgrade.cost then
		return false, "Not enough cash"
	end

	data.cash = data.cash - upgrade.cost

	if upgrade.type == "click_multiplier" then
		data.click_multiplier = upgrade.value
	elseif upgrade.type == "auto_coder" then
		data.auto_code_per_sec = data.auto_code_per_sec + upgrade.value
	end

	-- Track owned upgrades
	if not data.upgrades_owned[tostring(upgradeIndex)] then
		data.upgrades_owned[tostring(upgradeIndex)] = 0
	end
	data.upgrades_owned[tostring(upgradeIndex)] = data.upgrades_owned[tostring(upgradeIndex)] + 1

	syncData(player)
	return true, "Purchased " .. upgrade.name
end

-- Handle rebirth
RebirthEvent.OnServerInvoke = function(player)
	local data = DataManager.getData(player)
	if not data then return false, "No data" end

	if data.cash < GameConfig.REBIRTH_COST then
		return false, "Need " .. GameConfig.REBIRTH_COST .. " Cash to rebirth"
	end

	-- Reset progress but keep rebirths
	data.code = 0
	data.cash = 0
	data.click_multiplier = 1
	data.auto_code_per_sec = 0
	data.upgrades_owned = {}
	data.rebirths = data.rebirths + 1

	syncData(player)
	return true, "Rebirth #" .. data.rebirths .. "! Multiplier: " .. (GameConfig.REBIRTH_MULTIPLIER ^ data.rebirths) .. "x"
end

-- Auto-code generation (runs every second on server)
local autoCodeTimer = 0
RunService.Heartbeat:Connect(function(dt)
	autoCodeTimer = autoCodeTimer + dt
	if autoCodeTimer < 1 then return end
	autoCodeTimer = 0

	for _, player in ipairs(Players:GetPlayers()) do
		local data = DataManager.getData(player)
		if data and data.auto_code_per_sec > 0 then
			local rebirthBonus = GameConfig.REBIRTH_MULTIPLIER ^ data.rebirths
			local codeGained = data.auto_code_per_sec * rebirthBonus
			data.code = data.code + codeGained
			data.total_code_generated = data.total_code_generated + codeGained
			syncData(player)
		end
	end
end)

-- Auto-save
local saveTimer = 0
RunService.Heartbeat:Connect(function(dt)
	saveTimer = saveTimer + dt
	if saveTimer < GameConfig.AUTO_SAVE_INTERVAL then return end
	saveTimer = 0

	for _, player in ipairs(Players:GetPlayers()) do
		DataManager.saveData(player)
	end
end)

-- Player join/leave
Players.PlayerAdded:Connect(function(player)
	DataManager.loadData(player)
	syncData(player)
end)

Players.PlayerRemoving:Connect(function(player)
	DataManager.saveData(player)
	DataManager.clearCache(player)
end)

-- Save all on shutdown
game:BindToClose(function()
	for _, player in ipairs(Players:GetPlayers()) do
		DataManager.saveData(player)
	end
end)

print("[TechStartupTycoon] GameManager loaded successfully")
