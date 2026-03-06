-- PlayerController: client-side input handling for Shadow Dungeon: Descent
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local UserInputService = game:GetService("UserInputService")
local RunService = game:GetService("RunService")

local player = Players.LocalPlayer
local mouse = player:GetMouse()

-- Wait for remotes
local AttackEvent = ReplicatedStorage:WaitForChild("AttackEvent")
local UseAbilityEvent = ReplicatedStorage:WaitForChild("UseAbilityEvent")
local UseUltimateEvent = ReplicatedStorage:WaitForChild("UseUltimateEvent")
local UsePotionEvent = ReplicatedStorage:WaitForChild("UsePotionEvent")
local EquipItemEvent = ReplicatedStorage:WaitForChild("EquipItemEvent")
local EnemyUpdateEvent = ReplicatedStorage:WaitForChild("EnemyUpdateEvent")
local DataUpdateEvent = ReplicatedStorage:WaitForChild("DataUpdateEvent")

-- ── Enemy Visual Tracking ───────────────────────────────
local enemyModels = {}  -- [id] = { model, hpBar, ... }

local function createEnemyModel(enemyInfo)
	local model = Instance.new("Model")
	model.Name = "Enemy_" .. enemyInfo.id

	-- Body part
	local body = Instance.new("Part")
	body.Name = "Body"
	body.Size = enemyInfo.size or Vector3.new(4, 5, 4)
	body.Position = enemyInfo.position
	body.Anchored = true
	body.CanCollide = true
	body.Color = enemyInfo.color or Color3.fromRGB(200, 50, 50)
	body.Material = Enum.Material.SmoothPlastic
	body.Parent = model

	-- Humanoid-like properties
	local clickDetector = Instance.new("ClickDetector")
	clickDetector.MaxActivationDistance = 20
	clickDetector.Parent = body

	-- Name tag
	local billboard = Instance.new("BillboardGui")
	billboard.Size = UDim2.new(4, 0, 1.5, 0)
	billboard.StudsOffset = Vector3.new(0, 4, 0)
	billboard.Adornee = body
	billboard.AlwaysOnTop = true
	billboard.Parent = model

	local nameLabel = Instance.new("TextLabel")
	nameLabel.Size = UDim2.new(1, 0, 0.5, 0)
	nameLabel.BackgroundTransparency = 1
	nameLabel.Text = enemyInfo.name .. (enemyInfo.isBoss and " [BOSS]" or "")
	nameLabel.TextColor3 = enemyInfo.isBoss and Color3.fromRGB(255, 50, 50) or Color3.fromRGB(255, 255, 255)
	nameLabel.TextScaled = true
	nameLabel.Font = Enum.Font.GothamBold
	nameLabel.Parent = billboard

	-- HP bar background
	local hpBarBg = Instance.new("Frame")
	hpBarBg.Size = UDim2.new(0.8, 0, 0.15, 0)
	hpBarBg.Position = UDim2.new(0.1, 0, 0.55, 0)
	hpBarBg.BackgroundColor3 = Color3.fromRGB(40, 0, 0)
	hpBarBg.BorderSizePixel = 0
	hpBarBg.Parent = billboard

	local hpBarFill = Instance.new("Frame")
	hpBarFill.Name = "Fill"
	hpBarFill.Size = UDim2.new(1, 0, 1, 0)
	hpBarFill.BackgroundColor3 = Color3.fromRGB(200, 30, 30)
	hpBarFill.BorderSizePixel = 0
	hpBarFill.Parent = hpBarBg

	model.PrimaryPart = body
	model.Parent = workspace

	-- Click to attack
	clickDetector.MouseClick:Connect(function()
		AttackEvent:FireServer(enemyInfo.id)
	end)

	enemyModels[enemyInfo.id] = {
		model = model,
		body = body,
		hpBarFill = hpBarFill,
		nameLabel = nameLabel,
	}
end

local function updateEnemyModel(enemyData)
	local entry = enemyModels[enemyData.id]
	if not entry then return end

	-- Update HP bar
	local ratio = math.clamp(enemyData.hp / math.max(enemyData.maxHp, 1), 0, 1)
	entry.hpBarFill.Size = UDim2.new(ratio, 0, 1, 0)

	if ratio > 0.5 then
		entry.hpBarFill.BackgroundColor3 = Color3.fromRGB(50, 200, 50)
	elseif ratio > 0.25 then
		entry.hpBarFill.BackgroundColor3 = Color3.fromRGB(255, 200, 0)
	else
		entry.hpBarFill.BackgroundColor3 = Color3.fromRGB(200, 30, 30)
	end

	-- Update position
	if enemyData.position then
		entry.body.Position = enemyData.position
	end

	-- Visual effects for status
	if enemyData.stunned then
		entry.body.Color = Color3.fromRGB(255, 255, 0)
	elseif enemyData.burning then
		entry.body.Color = Color3.fromRGB(255, 100, 0)
	end

	-- Remove if dead
	if enemyData.hp <= 0 then
		entry.model:Destroy()
		enemyModels[enemyData.id] = nil
	end
end

local function clearAllEnemies()
	for id, entry in pairs(enemyModels) do
		entry.model:Destroy()
	end
	enemyModels = {}
end

-- ── Enemy Update Handler ────────────────────────────────
EnemyUpdateEvent.OnClientEvent:Connect(function(info)
	if info.type == "spawn" then
		clearAllEnemies()
		for _, enemyInfo in ipairs(info.enemies) do
			createEnemyModel(enemyInfo)
		end
	elseif info.type == "update" then
		for _, enemyData in ipairs(info.enemies) do
			updateEnemyModel(enemyData)
		end
	end
end)

-- ── Keyboard Input ──────────────────────────────────────
local function findNearestEnemy()
	local character = player.Character
	if not character then return nil end
	local hrp = character:FindFirstChild("HumanoidRootPart")
	if not hrp then return nil end

	local nearest = nil
	local nearestDist = math.huge

	for id, entry in pairs(enemyModels) do
		if entry.body and entry.body.Parent then
			local dist = (entry.body.Position - hrp.Position).Magnitude
			if dist < nearestDist then
				nearestDist = dist
				nearest = id
			end
		end
	end

	return nearest
end

UserInputService.InputBegan:Connect(function(input, gameProcessed)
	if gameProcessed then return end

	-- Q = Ability
	if input.KeyCode == Enum.KeyCode.Q then
		UseAbilityEvent:FireServer()
	end

	-- E = Ultimate
	if input.KeyCode == Enum.KeyCode.E then
		UseUltimateEvent:FireServer()
	end

	-- F = Potion
	if input.KeyCode == Enum.KeyCode.F then
		UsePotionEvent:FireServer()
	end

	-- Left Click = Attack nearest enemy
	if input.UserInputType == Enum.UserInputType.MouseButton1 then
		local target = findNearestEnemy()
		if target then
			AttackEvent:FireServer(target)
		end
	end
end)

-- ── Damage Flash Effect ─────────────────────────────────
local damageOverlay = Instance.new("Frame")
damageOverlay.Size = UDim2.new(1, 0, 1, 0)
damageOverlay.BackgroundColor3 = Color3.fromRGB(255, 0, 0)
damageOverlay.BackgroundTransparency = 1
damageOverlay.BorderSizePixel = 0
damageOverlay.ZIndex = 100

local playerGui = player:WaitForChild("PlayerGui")
local overlayGui = Instance.new("ScreenGui")
overlayGui.Name = "DamageOverlay"
overlayGui.IgnoreGuiInset = true
overlayGui.Parent = playerGui
damageOverlay.Parent = overlayGui

local CombatFeedbackEvent = ReplicatedStorage:WaitForChild("CombatFeedbackEvent")
CombatFeedbackEvent.OnClientEvent:Connect(function(info)
	if info.type == "enemy_attack" or info.type == "boss_damage" then
		-- Flash red
		damageOverlay.BackgroundTransparency = 0.6
		task.spawn(function()
			for i = 1, 10 do
				damageOverlay.BackgroundTransparency = 0.6 + (i / 10) * 0.4
				task.wait(0.03)
			end
			damageOverlay.BackgroundTransparency = 1
		end)
	end
end)

print("[ShadowDungeon] PlayerController loaded")
