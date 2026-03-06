-- ClickHandler: client-side click processing
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")

local ClickEvent = ReplicatedStorage:WaitForChild("ClickEvent")
local SellCodeEvent = ReplicatedStorage:WaitForChild("SellCodeEvent")
local BuyUpgradeEvent = ReplicatedStorage:WaitForChild("BuyUpgradeEvent")
local RebirthEvent = ReplicatedStorage:WaitForChild("RebirthEvent")
local DataUpdateEvent = ReplicatedStorage:WaitForChild("DataUpdateEvent")
local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))

local player = Players.LocalPlayer
local playerGui = player:WaitForChild("PlayerGui")

-- Wait for GUI
local mainGui = playerGui:WaitForChild("MainScreenGui")
local mainFrame = mainGui:WaitForChild("MainFrame")

-- UI References
local codeLabel = mainFrame:WaitForChild("CodeLabel")
local cashLabel = mainFrame:WaitForChild("CashLabel")
local rebirthLabel = mainFrame:WaitForChild("RebirthLabel")
local autoLabel = mainFrame:WaitForChild("AutoLabel")
local clickButton = mainFrame:WaitForChild("ClickButton")
local sellButton = mainFrame:WaitForChild("SellButton")
local rebirthButton = mainFrame:WaitForChild("RebirthButton")
local shopFrame = mainFrame:WaitForChild("ShopFrame")

-- Format numbers nicely
local function formatNumber(n)
	n = math.floor(n)
	if n >= 1000000 then
		return string.format("%.1fM", n / 1000000)
	elseif n >= 1000 then
		return string.format("%.1fK", n / 1000)
	end
	return tostring(n)
end

-- Current data cache
local currentData = {
	code = 0,
	cash = 0,
	rebirths = 0,
	click_multiplier = 1,
	auto_code_per_sec = 0,
}

-- Update UI
local function updateUI()
	codeLabel.Text = "Code: " .. formatNumber(currentData.code)
	cashLabel.Text = "Cash: $" .. formatNumber(currentData.cash)
	rebirthLabel.Text = "Rebirths: " .. currentData.rebirths
	autoLabel.Text = "Auto: " .. formatNumber(currentData.auto_code_per_sec) .. " code/s"

	local rebirthMult = GameConfig.REBIRTH_MULTIPLIER ^ currentData.rebirths
	if currentData.rebirths > 0 then
		rebirthLabel.Text = rebirthLabel.Text .. " (" .. string.format("%.1fx", rebirthMult) .. ")"
	end
end

-- Listen for data updates from server
DataUpdateEvent.OnClientEvent:Connect(function(data)
	currentData = data
	updateUI()
end)

-- Click to code
clickButton.MouseButton1Click:Connect(function()
	ClickEvent:FireServer()
end)

-- Sell code for cash
sellButton.MouseButton1Click:Connect(function()
	SellCodeEvent:FireServer()
end)

-- Rebirth
rebirthButton.MouseButton1Click:Connect(function()
	local success, msg = RebirthEvent:InvokeServer()
	if not success then
		rebirthButton.Text = msg
		task.wait(1.5)
		rebirthButton.Text = "REBIRTH ($" .. formatNumber(GameConfig.REBIRTH_COST) .. ")"
	end
end)

-- Create shop buttons dynamically
for i, upgrade in ipairs(GameConfig.UPGRADES) do
	local btn = Instance.new("TextButton")
	btn.Name = "Upgrade_" .. i
	btn.Size = UDim2.new(1, -10, 0, 50)
	btn.BackgroundColor3 = Color3.fromRGB(60, 60, 80)
	btn.TextColor3 = Color3.fromRGB(255, 255, 255)
	btn.Font = Enum.Font.GothamBold
	btn.TextSize = 14
	btn.TextWrapped = true
	btn.Text = upgrade.name .. " - $" .. formatNumber(upgrade.cost) .. "\n" .. upgrade.description
	btn.LayoutOrder = i
	btn.Parent = shopFrame

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 6)
	corner.Parent = btn

	btn.MouseButton1Click:Connect(function()
		local success, msg = BuyUpgradeEvent:InvokeServer(i)
		if success then
			btn.BackgroundColor3 = Color3.fromRGB(40, 120, 40)
			task.wait(0.3)
			btn.BackgroundColor3 = Color3.fromRGB(60, 60, 80)
		else
			btn.BackgroundColor3 = Color3.fromRGB(120, 40, 40)
			task.wait(0.5)
			btn.BackgroundColor3 = Color3.fromRGB(60, 60, 80)
		end
	end)
end

print("[TechStartupTycoon] ClickHandler loaded for " .. player.Name)
