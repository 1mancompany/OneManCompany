--[[
	CombatClient.luau — Client-side combat visual effects
	StarterPlayerScripts/CombatClient

	Handles: damage numbers, hit effects, screen shake,
	skill VFX, health bar updates.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")
local TweenService = game:GetService("TweenService")

local player = Players.LocalPlayer
local CombatClient = {}

--------------------------------------------------
-- Initialization
--------------------------------------------------

function CombatClient.init()
	local combatEvent = ReplicatedStorage:WaitForChild("CombatEvent", 10)
	if combatEvent then
		combatEvent.OnClientEvent:Connect(function(eventType, data)
			CombatClient._handleEvent(eventType, data)
		end)
	end
end

--------------------------------------------------
-- Event dispatcher
--------------------------------------------------

function CombatClient._handleEvent(eventType: string, data: any)
	if eventType == "DamageDealt" then
		CombatClient._showDamageNumber(data.target, data.damage, data.isCrit, false)
		CombatClient._updateEnemyHealthBar(data.target, data.targetHealth, data.targetMaxHealth)
		CombatClient._hitEffect(data.target)

	elseif eventType == "DamageTaken" then
		CombatClient._showDamageNumber(player.Character, data.damage, false, true)
		CombatClient._screenShake(0.3, 3)
		CombatClient._updatePlayerHUD(data.currentHealth, data.maxHealth)

	elseif eventType == "EnemyKilled" then
		CombatClient._deathEffect(data.target)
		CombatClient._showReward(data.xp, data.soulShards)

	elseif eventType == "Healed" then
		CombatClient._showHealNumber(data.amount)

	elseif eventType == "TalentCards" then
		CombatClient._showTalentPicker(data.cards)

	elseif eventType == "RoomCleared" then
		CombatClient._showRoomCleared()
	end
end

--------------------------------------------------
-- Damage numbers (floating text)
--------------------------------------------------

function CombatClient._showDamageNumber(target: Instance?, damage: number, isCrit: boolean, isPlayerDamage: boolean)
	if not target then return end

	local rootPart = nil
	if target:IsA("Model") then
		rootPart = target:FindFirstChild("HumanoidRootPart")
	end
	if not rootPart then return end

	local billboard = Instance.new("BillboardGui")
	billboard.Size = UDim2.new(0, 100, 0, 40)
	billboard.StudsOffset = Vector3.new(
		math.random(-3, 3),
		math.random(2, 5),
		math.random(-3, 3)
	)
	billboard.Adornee = rootPart
	billboard.AlwaysOnTop = true
	billboard.Parent = rootPart

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(1, 0, 1, 0)
	label.BackgroundTransparency = 1
	label.Font = Enum.Font.GothamBold
	label.TextScaled = true

	if isPlayerDamage then
		label.Text = "-" .. tostring(damage)
		label.TextColor3 = Color3.new(1, 0.2, 0.2)
	elseif isCrit then
		label.Text = tostring(damage) .. "!"
		label.TextColor3 = Color3.new(1, 0.85, 0)
		label.TextSize = 28
	else
		label.Text = tostring(damage)
		label.TextColor3 = Color3.new(1, 1, 1)
	end

	label.Parent = billboard

	-- Float up and fade
	local tween = TweenService:Create(billboard, TweenInfo.new(1.0, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), {
		StudsOffset = billboard.StudsOffset + Vector3.new(0, 4, 0),
	})
	local fadeTween = TweenService:Create(label, TweenInfo.new(0.8, Enum.EasingStyle.Quad, Enum.EasingDirection.In), {
		TextTransparency = 1,
	})

	tween:Play()
	task.delay(0.3, function()
		fadeTween:Play()
	end)

	task.delay(1.2, function()
		billboard:Destroy()
	end)
end

--------------------------------------------------
-- Hit and death effects
--------------------------------------------------

function CombatClient._hitEffect(target: Instance?)
	if not target or not target:IsA("Model") then return end
	local rootPart = target:FindFirstChild("HumanoidRootPart")
	if not rootPart then return end

	-- Flash white briefly
	local originalColor = rootPart.Color
	rootPart.Color = Color3.new(1, 1, 1)
	task.delay(0.1, function()
		if rootPart.Parent then
			rootPart.Color = originalColor
		end
	end)

	-- Hit particle
	local attachment = Instance.new("Attachment")
	attachment.Parent = rootPart

	local particles = Instance.new("ParticleEmitter")
	particles.Color = ColorSequence.new(Color3.new(1, 0.5, 0))
	particles.Size = NumberSequence.new({ NumberSequenceKeypoint.new(0, 1), NumberSequenceKeypoint.new(1, 0) })
	particles.Lifetime = NumberRange.new(0.2, 0.4)
	particles.Speed = NumberRange.new(10, 20)
	particles.Rate = 0
	particles.SpreadAngle = Vector2.new(180, 180)
	particles.Parent = attachment

	particles:Emit(8)
	task.delay(0.5, function()
		attachment:Destroy()
	end)
end

function CombatClient._deathEffect(target: Instance?)
	if not target or not target:IsA("Model") then return end

	-- Fade out all parts
	for _, part in target:GetDescendants() do
		if part:IsA("BasePart") then
			local tween = TweenService:Create(part, TweenInfo.new(0.5), {
				Transparency = 1,
			})
			tween:Play()
		end
	end

	-- Soul particles rising
	local rootPart = target:FindFirstChild("HumanoidRootPart")
	if rootPart then
		local attachment = Instance.new("Attachment")
		attachment.Parent = rootPart

		local particles = Instance.new("ParticleEmitter")
		particles.Color = ColorSequence.new(Color3.new(0.5, 0.8, 1))
		particles.Size = NumberSequence.new({ NumberSequenceKeypoint.new(0, 0.5), NumberSequenceKeypoint.new(1, 0) })
		particles.Lifetime = NumberRange.new(0.5, 1.0)
		particles.Speed = NumberRange.new(5, 15)
		particles.Rate = 0
		particles.SpreadAngle = Vector2.new(30, 30)
		particles.EmissionDirection = Enum.NormalId.Top
		particles.Parent = attachment

		particles:Emit(15)
		task.delay(1.5, function()
			attachment:Destroy()
		end)
	end
end

--------------------------------------------------
-- Screen effects
--------------------------------------------------

function CombatClient._screenShake(duration: number, intensity: number)
	local camera = workspace.CurrentCamera
	if not camera then return end

	local startTime = tick()
	local originalCFrame = camera.CFrame

	local conn
	conn = game:GetService("RunService").RenderStepped:Connect(function()
		local elapsed = tick() - startTime
		if elapsed >= duration then
			conn:Disconnect()
			return
		end

		local factor = 1 - (elapsed / duration)
		local offset = Vector3.new(
			(math.random() - 0.5) * intensity * factor,
			(math.random() - 0.5) * intensity * factor,
			0
		)
		camera.CFrame = camera.CFrame * CFrame.new(offset)
	end)
end

--------------------------------------------------
-- HUD updates
--------------------------------------------------

function CombatClient._updatePlayerHUD(currentHealth: number, maxHealth: number)
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local hud = gui:FindFirstChild("CombatHUD")
	if not hud then return end

	local healthBar = hud:FindFirstChild("HealthBarFill", true)
	if healthBar then
		local pct = math.clamp(currentHealth / maxHealth, 0, 1)
		TweenService:Create(healthBar, TweenInfo.new(0.3), {
			Size = UDim2.new(pct, 0, 1, 0),
		}):Play()

		-- Color change based on health
		if pct <= 0.25 then
			healthBar.BackgroundColor3 = Color3.new(0.8, 0.1, 0.1)
		elseif pct <= 0.5 then
			healthBar.BackgroundColor3 = Color3.new(0.9, 0.6, 0.1)
		else
			healthBar.BackgroundColor3 = Color3.new(0.2, 0.8, 0.2)
		end
	end

	local healthText = hud:FindFirstChild("HealthText", true)
	if healthText then
		healthText.Text = tostring(math.floor(currentHealth)) .. " / " .. tostring(math.floor(maxHealth))
	end
end

function CombatClient._updateEnemyHealthBar(enemyModel: Instance?, currentHealth: number, maxHealth: number)
	if not enemyModel or not enemyModel:IsA("Model") then return end

	local healthBar = enemyModel:FindFirstChild("HealthBar", true)
	if not healthBar then return end

	local fill = healthBar:FindFirstChild("Fill", true)
	if fill then
		local pct = math.clamp(currentHealth / maxHealth, 0, 1)
		fill.Size = UDim2.new(pct, 0, 1, 0)
	end
end

--------------------------------------------------
-- UI popups
--------------------------------------------------

function CombatClient._showReward(xp: number, soulShards: number)
	-- Simple floating text at screen center
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local screen = gui:FindFirstChild("CombatHUD") or gui:FindFirstChildOfClass("ScreenGui")
	if not screen then return end

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(0, 300, 0, 40)
	label.Position = UDim2.new(0.5, -150, 0.4, 0)
	label.BackgroundTransparency = 1
	label.Text = string.format("+%d XP  +%d Soul Shards", xp, soulShards)
	label.TextColor3 = Color3.new(1, 0.85, 0)
	label.TextScaled = true
	label.Font = Enum.Font.GothamBold
	label.Parent = screen

	TweenService:Create(label, TweenInfo.new(2.0, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), {
		Position = UDim2.new(0.5, -150, 0.3, 0),
		TextTransparency = 1,
	}):Play()

	task.delay(2.5, function()
		label:Destroy()
	end)
end

function CombatClient._showHealNumber(amount: number)
	local character = player.Character
	if not character then return end

	CombatClient._showDamageNumber(character, amount, false, false)
end

function CombatClient._showRoomCleared()
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local screen = gui:FindFirstChild("CombatHUD") or gui:FindFirstChildOfClass("ScreenGui")
	if not screen then return end

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(0, 400, 0, 60)
	label.Position = UDim2.new(0.5, -200, 0.35, 0)
	label.BackgroundTransparency = 1
	label.Text = "ROOM CLEARED"
	label.TextColor3 = Color3.new(0.2, 1, 0.4)
	label.TextScaled = true
	label.Font = Enum.Font.GothamBold
	label.TextStrokeTransparency = 0.5
	label.Parent = screen

	TweenService:Create(label, TweenInfo.new(1.5), {
		TextTransparency = 1,
		TextStrokeTransparency = 1,
	}):Play()

	task.delay(2.0, function()
		label:Destroy()
	end)
end

function CombatClient._showTalentPicker(cards: { any })
	-- Talent card selection is handled by UIManager
	-- This just forwards the data
	local gui = player:FindFirstChild("PlayerGui")
	if not gui then return end

	local uiManager = gui:FindFirstChild("UIManagerScript")
	if uiManager then
		uiManager:SetAttribute("TalentCards", game:GetService("HttpService"):JSONEncode(cards))
	end
end

return CombatClient
