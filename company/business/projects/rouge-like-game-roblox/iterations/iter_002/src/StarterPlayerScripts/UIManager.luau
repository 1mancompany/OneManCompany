--[[
	UIManager.luau — UI creation and management
	StarterPlayerScripts/UIManager

	Creates: Combat HUD, talent card picker, room transition screen,
	death screen, results screen.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local player = Players.LocalPlayer
local UIManager = {}

--------------------------------------------------
-- Initialization
--------------------------------------------------

function UIManager.init()
	UIManager._createCombatHUD()

	-- Listen for talent card events
	local combatEvent = ReplicatedStorage:WaitForChild("CombatEvent", 10)
	if combatEvent then
		combatEvent.OnClientEvent:Connect(function(eventType, data)
			if eventType == "TalentCards" then
				UIManager.showTalentPicker(data.cards)
			elseif eventType == "RunComplete" then
				UIManager.showResultsScreen(data)
			elseif eventType == "PlayerDied" then
				UIManager.showDeathScreen()
			elseif eventType == "RoomTransition" then
				UIManager.showRoomTransition(data.roomType, data.roomIndex)
			end
		end)
	end
end

--------------------------------------------------
-- Combat HUD
--------------------------------------------------

function UIManager._createCombatHUD()
	local gui = player:WaitForChild("PlayerGui")

	local screenGui = Instance.new("ScreenGui")
	screenGui.Name = "CombatHUD"
	screenGui.ResetOnSpawn = false
	screenGui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
	screenGui.Parent = gui

	-- Health bar
	UIManager._createHealthBar(screenGui)

	-- Energy bar
	UIManager._createEnergyBar(screenGui)

	-- Skill buttons
	UIManager._createSkillButtons(screenGui)

	-- Room info
	UIManager._createRoomInfo(screenGui)

	-- Minimap placeholder
	UIManager._createMinimap(screenGui)
end

function UIManager._createHealthBar(parent: ScreenGui)
	local container = Instance.new("Frame")
	container.Name = "HealthBarContainer"
	container.Size = UDim2.new(0, 250, 0, 25)
	container.Position = UDim2.new(0, 20, 0, 20)
	container.BackgroundColor3 = Color3.new(0.15, 0.15, 0.15)
	container.BorderSizePixel = 0
	container.Parent = parent

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 6)
	corner.Parent = container

	local fill = Instance.new("Frame")
	fill.Name = "HealthBarFill"
	fill.Size = UDim2.new(1, 0, 1, 0)
	fill.BackgroundColor3 = Color3.new(0.2, 0.8, 0.2)
	fill.BorderSizePixel = 0
	fill.Parent = container

	local fillCorner = Instance.new("UICorner")
	fillCorner.CornerRadius = UDim.new(0, 6)
	fillCorner.Parent = fill

	local text = Instance.new("TextLabel")
	text.Name = "HealthText"
	text.Size = UDim2.new(1, 0, 1, 0)
	text.BackgroundTransparency = 1
	text.Text = "100 / 100"
	text.TextColor3 = Color3.new(1, 1, 1)
	text.Font = Enum.Font.GothamBold
	text.TextSize = 14
	text.Parent = container
end

function UIManager._createEnergyBar(parent: ScreenGui)
	local container = Instance.new("Frame")
	container.Name = "EnergyBarContainer"
	container.Size = UDim2.new(0, 250, 0, 12)
	container.Position = UDim2.new(0, 20, 0, 50)
	container.BackgroundColor3 = Color3.new(0.15, 0.15, 0.15)
	container.BorderSizePixel = 0
	container.Parent = parent

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 4)
	corner.Parent = container

	local fill = Instance.new("Frame")
	fill.Name = "EnergyBarFill"
	fill.Size = UDim2.new(0, 0, 1, 0)
	fill.BackgroundColor3 = Color3.new(0.3, 0.5, 1)
	fill.BorderSizePixel = 0
	fill.Parent = container

	local fillCorner = Instance.new("UICorner")
	fillCorner.CornerRadius = UDim.new(0, 4)
	fillCorner.Parent = fill
end

function UIManager._createSkillButtons(parent: ScreenGui)
	local container = Instance.new("Frame")
	container.Name = "SkillBar"
	container.Size = UDim2.new(0, 350, 0, 70)
	container.Position = UDim2.new(0.5, -175, 1, -90)
	container.BackgroundTransparency = 1
	container.Parent = parent

	local layout = Instance.new("UIListLayout")
	layout.FillDirection = Enum.FillDirection.Horizontal
	layout.HorizontalAlignment = Enum.HorizontalAlignment.Center
	layout.Padding = UDim.new(0, 8)
	layout.Parent = container

	local buttonDefs = {
		{ name = "Skill1Button", label = "Q", color = Color3.new(0.3, 0.6, 0.9) },
		{ name = "Skill2Button", label = "E", color = Color3.new(0.9, 0.4, 0.3) },
		{ name = "Skill3Button", label = "R", color = Color3.new(0.4, 0.8, 0.4) },
		{ name = "DodgeButton",  label = "⚡", color = Color3.new(0.9, 0.85, 0.3) },
		{ name = "UltimateButton", label = "F", color = Color3.new(0.8, 0.3, 0.9) },
	}

	for _, def in buttonDefs do
		local btn = Instance.new("TextButton")
		btn.Name = def.name
		btn.Size = UDim2.new(0, 60, 0, 60)
		btn.BackgroundColor3 = def.color
		btn.Text = def.label
		btn.TextColor3 = Color3.new(1, 1, 1)
		btn.Font = Enum.Font.GothamBold
		btn.TextSize = 22
		btn.BorderSizePixel = 0
		btn.Parent = container

		local corner = Instance.new("UICorner")
		corner.CornerRadius = UDim.new(0, 10)
		corner.Parent = btn

		-- Cooldown overlay
		local cdOverlay = Instance.new("Frame")
		cdOverlay.Name = "CooldownOverlay"
		cdOverlay.Size = UDim2.new(1, 0, 0, 0)
		cdOverlay.Position = UDim2.new(0, 0, 1, 0)
		cdOverlay.AnchorPoint = Vector2.new(0, 1)
		cdOverlay.BackgroundColor3 = Color3.new(0, 0, 0)
		cdOverlay.BackgroundTransparency = 0.5
		cdOverlay.BorderSizePixel = 0
		cdOverlay.Parent = btn

		local cdCorner = Instance.new("UICorner")
		cdCorner.CornerRadius = UDim.new(0, 10)
		cdCorner.Parent = cdOverlay
	end
end

function UIManager._createRoomInfo(parent: ScreenGui)
	local label = Instance.new("TextLabel")
	label.Name = "RoomInfo"
	label.Size = UDim2.new(0, 200, 0, 30)
	label.Position = UDim2.new(1, -220, 0, 20)
	label.BackgroundTransparency = 1
	label.Text = "Room 1 / 10"
	label.TextColor3 = Color3.new(1, 1, 1)
	label.Font = Enum.Font.GothamBold
	label.TextSize = 18
	label.TextXAlignment = Enum.TextXAlignment.Right
	label.Parent = parent
end

function UIManager._createMinimap(parent: ScreenGui)
	local frame = Instance.new("Frame")
	frame.Name = "Minimap"
	frame.Size = UDim2.new(0, 120, 0, 120)
	frame.Position = UDim2.new(1, -140, 0, 55)
	frame.BackgroundColor3 = Color3.new(0.1, 0.1, 0.1)
	frame.BackgroundTransparency = 0.3
	frame.BorderSizePixel = 0
	frame.Parent = parent

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 8)
	corner.Parent = frame

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(1, 0, 1, 0)
	label.BackgroundTransparency = 1
	label.Text = "MINIMAP"
	label.TextColor3 = Color3.new(0.5, 0.5, 0.5)
	label.Font = Enum.Font.Gotham
	label.TextSize = 12
	label.Parent = frame
end

--------------------------------------------------
-- Talent Card Picker
--------------------------------------------------

function UIManager.showTalentPicker(cards: { any })
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local screen = Instance.new("ScreenGui")
	screen.Name = "TalentPicker"
	screen.Parent = gui

	-- Darken background
	local bg = Instance.new("Frame")
	bg.Size = UDim2.new(1, 0, 1, 0)
	bg.BackgroundColor3 = Color3.new(0, 0, 0)
	bg.BackgroundTransparency = 0.5
	bg.Parent = screen

	-- Title
	local title = Instance.new("TextLabel")
	title.Size = UDim2.new(0, 400, 0, 50)
	title.Position = UDim2.new(0.5, -200, 0.15, 0)
	title.BackgroundTransparency = 1
	title.Text = "Choose a Talent Card"
	title.TextColor3 = Color3.new(1, 0.85, 0)
	title.Font = Enum.Font.GothamBold
	title.TextSize = 28
	title.Parent = screen

	-- Card container
	local cardContainer = Instance.new("Frame")
	cardContainer.Size = UDim2.new(0, 600, 0, 300)
	cardContainer.Position = UDim2.new(0.5, -300, 0.3, 0)
	cardContainer.BackgroundTransparency = 1
	cardContainer.Parent = screen

	local layout = Instance.new("UIListLayout")
	layout.FillDirection = Enum.FillDirection.Horizontal
	layout.HorizontalAlignment = Enum.HorizontalAlignment.Center
	layout.Padding = UDim.new(0, 20)
	layout.Parent = cardContainer

	local rarityColors = {
		common = Color3.new(0.7, 0.7, 0.7),
		rare = Color3.new(0.3, 0.5, 1),
		epic = Color3.new(0.6, 0.2, 0.8),
		legendary = Color3.new(1, 0.75, 0),
	}

	local talentEvent = ReplicatedStorage:FindFirstChild("TalentSelectEvent")

	for i, card in cards do
		local cardFrame = Instance.new("TextButton")
		cardFrame.Name = "Card_" .. i
		cardFrame.Size = UDim2.new(0, 170, 0, 280)
		cardFrame.BackgroundColor3 = Color3.new(0.12, 0.12, 0.15)
		cardFrame.BorderSizePixel = 0
		cardFrame.Text = ""
		cardFrame.Parent = cardContainer

		local corner = Instance.new("UICorner")
		corner.CornerRadius = UDim.new(0, 12)
		corner.Parent = cardFrame

		local stroke = Instance.new("UIStroke")
		stroke.Color = rarityColors[card.rarity] or Color3.new(0.5, 0.5, 0.5)
		stroke.Thickness = 3
		stroke.Parent = cardFrame

		-- Rarity label
		local rarityLabel = Instance.new("TextLabel")
		rarityLabel.Size = UDim2.new(1, -20, 0, 25)
		rarityLabel.Position = UDim2.new(0, 10, 0, 10)
		rarityLabel.BackgroundTransparency = 1
		rarityLabel.Text = card.rarity:upper()
		rarityLabel.TextColor3 = rarityColors[card.rarity] or Color3.new(1, 1, 1)
		rarityLabel.Font = Enum.Font.GothamBold
		rarityLabel.TextSize = 12
		rarityLabel.TextXAlignment = Enum.TextXAlignment.Left
		rarityLabel.Parent = cardFrame

		-- Card name
		local nameLabel = Instance.new("TextLabel")
		nameLabel.Size = UDim2.new(1, -20, 0, 40)
		nameLabel.Position = UDim2.new(0, 10, 0, 45)
		nameLabel.BackgroundTransparency = 1
		nameLabel.Text = card.name
		nameLabel.TextColor3 = Color3.new(1, 1, 1)
		nameLabel.Font = Enum.Font.GothamBold
		nameLabel.TextSize = 18
		nameLabel.TextWrapped = true
		nameLabel.TextXAlignment = Enum.TextXAlignment.Left
		nameLabel.Parent = cardFrame

		-- Description
		local descLabel = Instance.new("TextLabel")
		descLabel.Size = UDim2.new(1, -20, 0, 120)
		descLabel.Position = UDim2.new(0, 10, 0, 100)
		descLabel.BackgroundTransparency = 1
		descLabel.Text = card.description
		descLabel.TextColor3 = Color3.new(0.8, 0.8, 0.8)
		descLabel.Font = Enum.Font.Gotham
		descLabel.TextSize = 14
		descLabel.TextWrapped = true
		descLabel.TextXAlignment = Enum.TextXAlignment.Left
		descLabel.TextYAlignment = Enum.TextYAlignment.Top
		descLabel.Parent = cardFrame

		-- Select button area
		cardFrame.Activated:Connect(function()
			if talentEvent then
				talentEvent:FireServer(card.id)
			end
			screen:Destroy()
		end)

		-- Hover effect
		cardFrame.MouseEnter:Connect(function()
			TweenService:Create(cardFrame, TweenInfo.new(0.2), {
				Size = UDim2.new(0, 180, 0, 290),
			}):Play()
		end)
		cardFrame.MouseLeave:Connect(function()
			TweenService:Create(cardFrame, TweenInfo.new(0.2), {
				Size = UDim2.new(0, 170, 0, 280),
			}):Play()
		end)
	end

	-- Auto-close after timeout
	task.delay(30, function()
		if screen.Parent then
			-- Auto-select first card
			if talentEvent and #cards > 0 then
				talentEvent:FireServer(cards[1].id)
			end
			screen:Destroy()
		end
	end)
end

--------------------------------------------------
-- Room transition
--------------------------------------------------

function UIManager.showRoomTransition(roomType: string, roomIndex: number)
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local combatHUD = gui:FindFirstChild("CombatHUD")
	if combatHUD then
		local roomInfo = combatHUD:FindFirstChild("RoomInfo")
		if roomInfo then
			roomInfo.Text = string.format("Room %d — %s", roomIndex, roomType:upper())
		end
	end

	-- Brief flash overlay
	local screen = Instance.new("ScreenGui")
	screen.Name = "TransitionOverlay"
	screen.Parent = gui

	local overlay = Instance.new("Frame")
	overlay.Size = UDim2.new(1, 0, 1, 0)
	overlay.BackgroundColor3 = Color3.new(0, 0, 0)
	overlay.BackgroundTransparency = 0
	overlay.Parent = screen

	TweenService:Create(overlay, TweenInfo.new(1.0, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), {
		BackgroundTransparency = 1,
	}):Play()

	task.delay(1.5, function()
		screen:Destroy()
	end)
end

--------------------------------------------------
-- Death and results screens
--------------------------------------------------

function UIManager.showDeathScreen()
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local screen = Instance.new("ScreenGui")
	screen.Name = "DeathScreen"
	screen.Parent = gui

	local bg = Instance.new("Frame")
	bg.Size = UDim2.new(1, 0, 1, 0)
	bg.BackgroundColor3 = Color3.new(0.3, 0, 0)
	bg.BackgroundTransparency = 0.4
	bg.Parent = screen

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(0, 400, 0, 80)
	label.Position = UDim2.new(0.5, -200, 0.35, 0)
	label.BackgroundTransparency = 1
	label.Text = "YOU DIED"
	label.TextColor3 = Color3.new(0.8, 0.1, 0.1)
	label.Font = Enum.Font.GothamBold
	label.TextSize = 48
	label.Parent = screen

	task.delay(3, function()
		if screen.Parent then
			screen:Destroy()
		end
	end)
end

function UIManager.showResultsScreen(data: any)
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local screen = Instance.new("ScreenGui")
	screen.Name = "ResultsScreen"
	screen.Parent = gui

	local bg = Instance.new("Frame")
	bg.Size = UDim2.new(1, 0, 1, 0)
	bg.BackgroundColor3 = Color3.new(0, 0, 0)
	bg.BackgroundTransparency = 0.3
	bg.Parent = screen

	local panel = Instance.new("Frame")
	panel.Size = UDim2.new(0, 400, 0, 350)
	panel.Position = UDim2.new(0.5, -200, 0.2, 0)
	panel.BackgroundColor3 = Color3.new(0.12, 0.12, 0.15)
	panel.BorderSizePixel = 0
	panel.Parent = screen

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, 16)
	corner.Parent = panel

	local title = Instance.new("TextLabel")
	title.Size = UDim2.new(1, 0, 0, 50)
	title.BackgroundTransparency = 1
	title.Text = data.cleared and "RUN COMPLETE!" or "RUN OVER"
	title.TextColor3 = data.cleared and Color3.new(0.2, 1, 0.4) or Color3.new(1, 0.3, 0.3)
	title.Font = Enum.Font.GothamBold
	title.TextSize = 28
	title.Parent = panel

	local stats = {
		{ "Rooms Cleared", tostring(data.roomsCleared or 0) },
		{ "Enemies Defeated", tostring(data.enemiesKilled or 0) },
		{ "XP Earned", tostring(data.xpEarned or 0) },
		{ "Soul Shards", tostring(data.soulShards or 0) },
		{ "Time", string.format("%d:%02d", math.floor((data.duration or 0) / 60), math.floor((data.duration or 0) % 60)) },
	}

	for i, stat in stats do
		local row = Instance.new("Frame")
		row.Size = UDim2.new(1, -40, 0, 35)
		row.Position = UDim2.new(0, 20, 0, 60 + (i - 1) * 40)
		row.BackgroundTransparency = 1
		row.Parent = panel

		local nameLabel = Instance.new("TextLabel")
		nameLabel.Size = UDim2.new(0.6, 0, 1, 0)
		nameLabel.BackgroundTransparency = 1
		nameLabel.Text = stat[1]
		nameLabel.TextColor3 = Color3.new(0.7, 0.7, 0.7)
		nameLabel.Font = Enum.Font.Gotham
		nameLabel.TextSize = 16
		nameLabel.TextXAlignment = Enum.TextXAlignment.Left
		nameLabel.Parent = row

		local valueLabel = Instance.new("TextLabel")
		valueLabel.Size = UDim2.new(0.4, 0, 1, 0)
		valueLabel.Position = UDim2.new(0.6, 0, 0, 0)
		valueLabel.BackgroundTransparency = 1
		valueLabel.Text = stat[2]
		valueLabel.TextColor3 = Color3.new(1, 0.85, 0)
		valueLabel.Font = Enum.Font.GothamBold
		valueLabel.TextSize = 18
		valueLabel.TextXAlignment = Enum.TextXAlignment.Right
		valueLabel.Parent = row
	end

	-- Return to hub button
	local returnBtn = Instance.new("TextButton")
	returnBtn.Size = UDim2.new(0, 200, 0, 45)
	returnBtn.Position = UDim2.new(0.5, -100, 1, -60)
	returnBtn.BackgroundColor3 = Color3.new(0.3, 0.6, 0.9)
	returnBtn.Text = "Return to Hub"
	returnBtn.TextColor3 = Color3.new(1, 1, 1)
	returnBtn.Font = Enum.Font.GothamBold
	returnBtn.TextSize = 18
	returnBtn.BorderSizePixel = 0
	returnBtn.Parent = panel

	local btnCorner = Instance.new("UICorner")
	btnCorner.CornerRadius = UDim.new(0, 8)
	btnCorner.Parent = returnBtn

	returnBtn.Activated:Connect(function()
		local returnEvent = ReplicatedStorage:FindFirstChild("ReturnToHubEvent")
		if returnEvent then
			returnEvent:FireServer()
		end
		screen:Destroy()
	end)
end

--------------------------------------------------
-- Energy bar update
--------------------------------------------------

function UIManager.updateEnergyBar(current: number, max: number)
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local hud = gui:FindFirstChild("CombatHUD")
	if not hud then return end

	local fill = hud:FindFirstChild("EnergyBarFill", true)
	if fill then
		local pct = math.clamp(current / max, 0, 1)
		TweenService:Create(fill, TweenInfo.new(0.2), {
			Size = UDim2.new(pct, 0, 1, 0),
		}):Play()

		-- Glow when full
		if pct >= 1 then
			fill.BackgroundColor3 = Color3.new(0.5, 0.7, 1)
		else
			fill.BackgroundColor3 = Color3.new(0.3, 0.5, 1)
		end
	end
end

return UIManager
