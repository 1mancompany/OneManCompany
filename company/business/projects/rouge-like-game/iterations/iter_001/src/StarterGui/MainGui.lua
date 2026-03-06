-- MainGui: client-side UI for Shadow Dungeon: Descent
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local player = Players.LocalPlayer
local playerGui = player:WaitForChild("PlayerGui")

-- Wait for remotes
local DataUpdateEvent = ReplicatedStorage:WaitForChild("DataUpdateEvent")
local FloorUpdateEvent = ReplicatedStorage:WaitForChild("FloorUpdateEvent")
local CombatFeedbackEvent = ReplicatedStorage:WaitForChild("CombatFeedbackEvent")
local LootDropEvent = ReplicatedStorage:WaitForChild("LootDropEvent")
local GameOverEvent = ReplicatedStorage:WaitForChild("GameOverEvent")
local SelectClassEvent = ReplicatedStorage:WaitForChild("SelectClassEvent")
local StartRunEvent = ReplicatedStorage:WaitForChild("StartRunEvent")
local BuySoulUpgradeEvent = ReplicatedStorage:WaitForChild("BuySoulUpgradeEvent")
local EquipItemEvent = ReplicatedStorage:WaitForChild("EquipItemEvent")

-- ── ScreenGui ───────────────────────────────────────────
local screenGui = Instance.new("ScreenGui")
screenGui.Name = "ShadowDungeonGui"
screenGui.ResetOnSpawn = false
screenGui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
screenGui.Parent = playerGui

local currentData = nil

-- ── Utility ─────────────────────────────────────────────
local function makeFrame(parent, name, size, position, color, transparency)
	local frame = Instance.new("Frame")
	frame.Name = name
	frame.Size = size
	frame.Position = position
	frame.BackgroundColor3 = color or Color3.fromRGB(20, 20, 30)
	frame.BackgroundTransparency = transparency or 0.3
	frame.BorderSizePixel = 0
	frame.Parent = parent

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 8)
	corner.Parent = frame

	return frame
end

local function makeLabel(parent, name, text, size, position, color, textSize)
	local label = Instance.new("TextLabel")
	label.Name = name
	label.Text = text
	label.Size = size or UDim2.new(1, 0, 0, 30)
	label.Position = position or UDim2.new(0, 0, 0, 0)
	label.BackgroundTransparency = 1
	label.TextColor3 = color or Color3.fromRGB(255, 255, 255)
	label.TextScaled = false
	label.TextSize = textSize or 18
	label.Font = Enum.Font.GothamBold
	label.TextXAlignment = Enum.TextXAlignment.Left
	label.Parent = parent
	return label
end

local function makeButton(parent, name, text, size, position, color)
	local btn = Instance.new("TextButton")
	btn.Name = name
	btn.Text = text
	btn.Size = size
	btn.Position = position
	btn.BackgroundColor3 = color or Color3.fromRGB(60, 60, 100)
	btn.TextColor3 = Color3.fromRGB(255, 255, 255)
	btn.TextSize = 16
	btn.Font = Enum.Font.GothamBold
	btn.BorderSizePixel = 0
	btn.Parent = parent

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 6)
	corner.Parent = btn

	return btn
end

-- ── HUD (top bar: HP, Floor, Soul Gems) ─────────────────
local hudFrame = makeFrame(screenGui, "HUD",
	UDim2.new(0, 400, 0, 80), UDim2.new(0.5, -200, 0, 10))

local hpBarBg = makeFrame(hudFrame, "HpBarBg",
	UDim2.new(0.9, 0, 0, 20), UDim2.new(0.05, 0, 0, 10),
	Color3.fromRGB(40, 0, 0), 0)

local hpBarFill = makeFrame(hpBarBg, "HpBarFill",
	UDim2.new(1, 0, 1, 0), UDim2.new(0, 0, 0, 0),
	Color3.fromRGB(200, 30, 30), 0)

local hpLabel = makeLabel(hpBarBg, "HpText", "HP: 0/0",
	UDim2.new(1, 0, 1, 0), UDim2.new(0, 5, 0, 0),
	Color3.fromRGB(255, 255, 255), 14)
hpLabel.TextXAlignment = Enum.TextXAlignment.Center

local floorLabel = makeLabel(hudFrame, "FloorLabel", "Floor: 0",
	UDim2.new(0.3, 0, 0, 25), UDim2.new(0.05, 0, 0, 40),
	Color3.fromRGB(255, 200, 50), 16)

local soulGemLabel = makeLabel(hudFrame, "SoulGemLabel", "Soul Gems: 0",
	UDim2.new(0.35, 0, 0, 25), UDim2.new(0.35, 0, 0, 40),
	Color3.fromRGB(180, 100, 255), 16)

local potionLabel = makeLabel(hudFrame, "PotionLabel", "Potions: 0",
	UDim2.new(0.25, 0, 0, 25), UDim2.new(0.72, 0, 0, 40),
	Color3.fromRGB(100, 255, 100), 16)

hudFrame.Visible = false

-- ── Ability Buttons (bottom) ────────────────────────────
local abilityFrame = makeFrame(screenGui, "Abilities",
	UDim2.new(0, 400, 0, 60), UDim2.new(0.5, -200, 1, -70))

local attackBtn = makeButton(abilityFrame, "AttackBtn", "Attack [LMB]",
	UDim2.new(0, 90, 0, 40), UDim2.new(0, 5, 0.5, -20),
	Color3.fromRGB(180, 50, 50))

local abilityBtn = makeButton(abilityFrame, "AbilityBtn", "Ability [Q]",
	UDim2.new(0, 90, 0, 40), UDim2.new(0, 100, 0.5, -20),
	Color3.fromRGB(50, 100, 200))

local ultimateBtn = makeButton(abilityFrame, "UltimateBtn", "Ultimate [E]",
	UDim2.new(0, 90, 0, 40), UDim2.new(0, 195, 0.5, -20),
	Color3.fromRGB(200, 100, 0))

local potionBtn = makeButton(abilityFrame, "PotionBtn", "Potion [F]",
	UDim2.new(0, 90, 0, 40), UDim2.new(0, 290, 0.5, -20),
	Color3.fromRGB(50, 180, 50))

abilityFrame.Visible = false

-- ── Class Selection Screen ──────────────────────────────
local classSelectFrame = makeFrame(screenGui, "ClassSelect",
	UDim2.new(0, 500, 0, 400), UDim2.new(0.5, -250, 0.5, -200),
	Color3.fromRGB(15, 15, 25), 0.1)

local titleLabel = makeLabel(classSelectFrame, "Title", "SHADOW DUNGEON: DESCENT",
	UDim2.new(1, 0, 0, 50), UDim2.new(0, 0, 0, 10),
	Color3.fromRGB(255, 100, 50), 28)
titleLabel.TextXAlignment = Enum.TextXAlignment.Center

local subtitleLabel = makeLabel(classSelectFrame, "Subtitle", "Choose Your Class",
	UDim2.new(1, 0, 0, 30), UDim2.new(0, 0, 0, 60),
	Color3.fromRGB(200, 200, 200), 20)
subtitleLabel.TextXAlignment = Enum.TextXAlignment.Center

-- Class buttons
local classes = {
	{ name = "Warrior", desc = "HP: 150 | ATK: 15 | DEF: 12\nAbility: Shield Bash (Stun)", color = Color3.fromRGB(200, 50, 50) },
	{ name = "Mage",    desc = "HP: 80 | ATK: 25 | DEF: 5\nAbility: Fireball (AoE Burn)", color = Color3.fromRGB(80, 80, 220) },
	{ name = "Archer",  desc = "HP: 100 | ATK: 20 | DEF: 8\nAbility: Power Shot (Pierce)", color = Color3.fromRGB(50, 180, 50) },
}

for i, cls in ipairs(classes) do
	local yPos = 100 + (i - 1) * 90

	local classBtn = makeButton(classSelectFrame, cls.name .. "Btn", cls.name,
		UDim2.new(0, 150, 0, 70), UDim2.new(0, 30, 0, yPos), cls.color)
	classBtn.TextSize = 22

	local descLabel = makeLabel(classSelectFrame, cls.name .. "Desc", cls.desc,
		UDim2.new(0, 280, 0, 70), UDim2.new(0, 190, 0, yPos),
		Color3.fromRGB(180, 180, 180), 14)
	descLabel.TextWrapped = true

	classBtn.MouseButton1Click:Connect(function()
		local success, msg = SelectClassEvent:InvokeServer(cls.name)
		if success then
			classSelectFrame.Visible = false
			hudFrame.Visible = true
			abilityFrame.Visible = true
			StartRunEvent:FireServer()
		end
	end)
end

-- Stats display on class select
local statsFrame = makeFrame(classSelectFrame, "StatsDisplay",
	UDim2.new(0.9, 0, 0, 40), UDim2.new(0.05, 0, 0, 370))

local statsLabel = makeLabel(statsFrame, "StatsText", "Best Floor: 0 | Runs: 0 | Soul Gems: 0",
	UDim2.new(1, 0, 1, 0), UDim2.new(0, 0, 0, 0),
	Color3.fromRGB(180, 180, 180), 14)
statsLabel.TextXAlignment = Enum.TextXAlignment.Center

-- ── Soul Upgrade Shop (accessible from class select) ────
local shopBtn = makeButton(classSelectFrame, "ShopBtn", "Soul Upgrades",
	UDim2.new(0, 140, 0, 35), UDim2.new(0.5, -70, 0, 330),
	Color3.fromRGB(160, 50, 255))

local shopFrame = makeFrame(screenGui, "SoulShop",
	UDim2.new(0, 350, 0, 350), UDim2.new(0.5, -175, 0.5, -175),
	Color3.fromRGB(20, 10, 30), 0.1)
shopFrame.Visible = false

makeLabel(shopFrame, "ShopTitle", "Soul Gem Upgrades",
	UDim2.new(1, 0, 0, 35), UDim2.new(0, 0, 0, 5),
	Color3.fromRGB(200, 150, 255), 22).TextXAlignment = Enum.TextXAlignment.Center

local shopCloseBtn = makeButton(shopFrame, "CloseBtn", "X",
	UDim2.new(0, 30, 0, 30), UDim2.new(1, -35, 0, 5),
	Color3.fromRGB(180, 50, 50))
shopCloseBtn.MouseButton1Click:Connect(function()
	shopFrame.Visible = false
end)

local upgradeButtons = {}
local upgradeNames = { "Vitality", "Strength", "Armor", "Agility", "Lucky" }

for i, upgName in ipairs(upgradeNames) do
	local yPos = 45 + (i - 1) * 55
	local upFrame = makeFrame(shopFrame, upgName .. "Frame",
		UDim2.new(0.9, 0, 0, 45), UDim2.new(0.05, 0, 0, yPos),
		Color3.fromRGB(30, 20, 40))

	local upLabel = makeLabel(upFrame, "Label", upgName .. " Lv.0",
		UDim2.new(0.5, 0, 1, 0), UDim2.new(0, 10, 0, 0),
		Color3.fromRGB(220, 220, 220), 15)

	local upBtn = makeButton(upFrame, "BuyBtn", "Buy (10 SG)",
		UDim2.new(0, 100, 0, 30), UDim2.new(1, -110, 0.5, -15),
		Color3.fromRGB(100, 50, 180))

	upgradeButtons[upgName] = { label = upLabel, button = upBtn }

	upBtn.MouseButton1Click:Connect(function()
		local success, msg = BuySoulUpgradeEvent:InvokeServer(upgName)
		-- UI will update via DataUpdateEvent
	end)
end

shopBtn.MouseButton1Click:Connect(function()
	shopFrame.Visible = not shopFrame.Visible
end)

-- ── Game Over Screen ────────────────────────────────────
local gameOverFrame = makeFrame(screenGui, "GameOver",
	UDim2.new(0, 400, 0, 250), UDim2.new(0.5, -200, 0.5, -125),
	Color3.fromRGB(30, 0, 0), 0.1)
gameOverFrame.Visible = false

makeLabel(gameOverFrame, "GOTitle", "YOU DIED",
	UDim2.new(1, 0, 0, 50), UDim2.new(0, 0, 0, 20),
	Color3.fromRGB(255, 50, 50), 36).TextXAlignment = Enum.TextXAlignment.Center

local goStatsLabel = makeLabel(gameOverFrame, "GOStats", "",
	UDim2.new(0.8, 0, 0, 80), UDim2.new(0.1, 0, 0, 80),
	Color3.fromRGB(200, 200, 200), 16)
goStatsLabel.TextWrapped = true
goStatsLabel.TextXAlignment = Enum.TextXAlignment.Center

local retryBtn = makeButton(gameOverFrame, "RetryBtn", "Try Again",
	UDim2.new(0, 150, 0, 40), UDim2.new(0.5, -75, 0, 180),
	Color3.fromRGB(80, 130, 80))

retryBtn.MouseButton1Click:Connect(function()
	gameOverFrame.Visible = false
	classSelectFrame.Visible = true
	hudFrame.Visible = false
	abilityFrame.Visible = false
end)

-- ── Combat Log (bottom-left) ────────────────────────────
local logFrame = makeFrame(screenGui, "CombatLog",
	UDim2.new(0, 300, 0, 200), UDim2.new(0, 10, 1, -210),
	Color3.fromRGB(10, 10, 20), 0.5)

local logScroll = Instance.new("ScrollingFrame")
logScroll.Size = UDim2.new(1, -10, 1, -10)
logScroll.Position = UDim2.new(0, 5, 0, 5)
logScroll.BackgroundTransparency = 1
logScroll.ScrollBarThickness = 4
logScroll.CanvasSize = UDim2.new(0, 0, 0, 0)
logScroll.AutomaticCanvasSize = Enum.AutomaticSize.Y
logScroll.Parent = logFrame

local logLayout = Instance.new("UIListLayout")
logLayout.SortOrder = Enum.SortOrder.LayoutOrder
logLayout.Padding = UDim.new(0, 2)
logLayout.Parent = logScroll

local logOrder = 0
local function addLogMessage(text, color)
	logOrder = logOrder + 1
	local msg = Instance.new("TextLabel")
	msg.Size = UDim2.new(1, 0, 0, 18)
	msg.BackgroundTransparency = 1
	msg.TextColor3 = color or Color3.fromRGB(200, 200, 200)
	msg.Text = text
	msg.TextSize = 12
	msg.Font = Enum.Font.Gotham
	msg.TextXAlignment = Enum.TextXAlignment.Left
	msg.TextWrapped = true
	msg.AutomaticSize = Enum.AutomaticSize.Y
	msg.LayoutOrder = logOrder
	msg.Parent = logScroll

	-- Auto-scroll to bottom
	logScroll.CanvasPosition = Vector2.new(0, logScroll.AbsoluteCanvasSize.Y)

	-- Limit log entries
	local children = logScroll:GetChildren()
	local labels = {}
	for _, c in ipairs(children) do
		if c:IsA("TextLabel") then table.insert(labels, c) end
	end
	if #labels > 50 then
		labels[1]:Destroy()
	end
end

logFrame.Visible = false

-- ── Event Handlers ──────────────────────────────────────
DataUpdateEvent.OnClientEvent:Connect(function(data)
	currentData = data

	-- Update HUD
	if data.hp and data.maxHp then
		local ratio = math.clamp(data.hp / math.max(data.maxHp, 1), 0, 1)
		hpBarFill.Size = UDim2.new(ratio, 0, 1, 0)
		hpBarFill.BackgroundColor3 = ratio > 0.5
			and Color3.fromRGB(50, 200, 50)
			or (ratio > 0.25 and Color3.fromRGB(255, 200, 0) or Color3.fromRGB(200, 30, 30))
		hpLabel.Text = "HP: " .. math.floor(data.hp) .. "/" .. data.maxHp
	end

	floorLabel.Text = "Floor: " .. (data.currentFloor or 0)
	soulGemLabel.Text = "Gems: " .. (data.soulGems or 0)
	potionLabel.Text = "Potions: " .. (data.potions or 0)

	-- Update stats on class select
	statsLabel.Text = "Best Floor: " .. (data.bestFloor or 0)
		.. " | Runs: " .. (data.totalRuns or 0)
		.. " | Soul Gems: " .. (data.soulGems or 0)

	-- Update soul shop
	for _, upDef in ipairs(upgradeNames) do
		local info = upgradeButtons[upDef]
		if info then
			local level = (data.soulUpgrades and data.soulUpgrades[upDef]) or 0
			info.label.Text = upDef .. " Lv." .. level
			local cost = (level + 1) * 10
			if upDef == "Lucky" then cost = (level + 1) * 30 end
			info.button.Text = "Buy (" .. cost .. " SG)"
		end
	end
end)

FloorUpdateEvent.OnClientEvent:Connect(function(info)
	if info.type == "floor_cleared" then
		addLogMessage("Floor " .. info.floor .. " CLEARED! Walk to the exit.", Color3.fromRGB(50, 255, 50))
	else
		local text = info.isBoss
			and ("BOSS FLOOR " .. info.floor .. "!")
			or ("Floor " .. info.floor .. " — " .. info.enemyCount .. " enemies")
		addLogMessage(text, info.isBoss and Color3.fromRGB(255, 50, 50) or Color3.fromRGB(255, 200, 50))
		logFrame.Visible = true
	end
end)

CombatFeedbackEvent.OnClientEvent:Connect(function(info)
	if info.type == "player_attack" then
		local color = info.killed and Color3.fromRGB(255, 100, 0) or Color3.fromRGB(255, 255, 100)
		addLogMessage("Hit! " .. info.damage .. " damage" .. (info.killed and " — KILLED!" or ""), color)
	elseif info.type == "enemy_attack" then
		addLogMessage(info.enemyName .. " hits you for " .. info.damage .. " damage!", Color3.fromRGB(255, 80, 80))
	elseif info.type == "ability" then
		addLogMessage("Used " .. info.abilityName .. "!", Color3.fromRGB(100, 150, 255))
	elseif info.type == "ultimate" then
		addLogMessage("ULTIMATE: " .. info.ultimateName .. "!", Color3.fromRGB(255, 200, 0))
	elseif info.type == "potion" then
		addLogMessage("Healed " .. info.healAmount .. " HP!", Color3.fromRGB(50, 255, 50))
	elseif info.type == "boss_special" then
		addLogMessage("BOSS uses " .. (info.special.type or "special") .. "!", Color3.fromRGB(255, 0, 0))
	elseif info.type == "boss_damage" then
		addLogMessage("Boss deals " .. info.damage .. " damage!", Color3.fromRGB(255, 0, 0))
	end
end)

LootDropEvent.OnClientEvent:Connect(function(drops)
	for _, item in ipairs(drops) do
		local color = item.rarityColor or Color3.fromRGB(200, 200, 200)
		addLogMessage("Loot: " .. item.name, color)
	end
end)

GameOverEvent.OnClientEvent:Connect(function(info)
	hudFrame.Visible = false
	abilityFrame.Visible = false
	logFrame.Visible = false
	gameOverFrame.Visible = true
	goStatsLabel.Text = "Best Floor: " .. (info.floor or 0)
		.. "\nSoul Gems: " .. (info.soulGems or 0)
		.. "\nTotal Runs: " .. (info.totalRuns or 0)
end)

-- Start with class selection visible
classSelectFrame.Visible = true

print("[ShadowDungeon] MainGui loaded")
