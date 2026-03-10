--[[
	InputController.luau — Player input handling (PC + Mobile)
	StarterPlayerScripts/InputController

	Handles: movement, skill buttons, dodge, auto-attack toggle.
	Sends actions to server via RemoteEvents.
]]

local Players = game:GetService("Players")
local UserInputService = game:GetService("UserInputService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local ContextActionService = game:GetService("ContextActionService")

local player = Players.LocalPlayer
local InputController = {}

-- Remote events (created by GameManager)
local skillEvent: RemoteEvent = nil
local dodgeEvent: RemoteEvent = nil
local ultimateEvent: RemoteEvent = nil

-- State
local autoAttack = true
local autoAttackTimer = 0
local AUTO_ATTACK_INTERVAL = 0.5

--------------------------------------------------
-- Initialization
--------------------------------------------------

function InputController.init()
	-- Wait for remote events
	skillEvent = ReplicatedStorage:WaitForChild("SkillEvent", 10)
	dodgeEvent = ReplicatedStorage:WaitForChild("DodgeEvent", 10)
	ultimateEvent = ReplicatedStorage:WaitForChild("UltimateEvent", 10)

	-- PC keyboard bindings
	InputController._bindKeyboard()

	-- Mobile touch buttons (created by UIManager)
	InputController._bindMobile()

	-- Auto-attack loop
	RunService.Heartbeat:Connect(function(dt)
		InputController._updateAutoAttack(dt)
	end)
end

--------------------------------------------------
-- Keyboard bindings (PC)
--------------------------------------------------

function InputController._bindKeyboard()
	UserInputService.InputBegan:Connect(function(input, gameProcessed)
		if gameProcessed then return end

		if input.KeyCode == Enum.KeyCode.Q then
			InputController.useSkill(1)
		elseif input.KeyCode == Enum.KeyCode.E then
			InputController.useSkill(2)
		elseif input.KeyCode == Enum.KeyCode.R then
			InputController.useSkill(3)
		elseif input.KeyCode == Enum.KeyCode.F then
			InputController.useUltimate()
		elseif input.KeyCode == Enum.KeyCode.Space then
			InputController.dodge()
		elseif input.KeyCode == Enum.KeyCode.T then
			autoAttack = not autoAttack
		end
	end)
end

--------------------------------------------------
-- Mobile bindings
--------------------------------------------------

function InputController._bindMobile()
	-- Touch buttons are handled by UIManager
	-- This connects to the button events
	local gui = player:WaitForChild("PlayerGui", 10)
	if not gui then return end

	local combatUI = gui:FindFirstChild("CombatHUD")
	if not combatUI then return end

	local function connectButton(name: string, callback: () -> ())
		local btn = combatUI:FindFirstChild(name, true)
		if btn and btn:IsA("GuiButton") then
			btn.Activated:Connect(callback)
		end
	end

	connectButton("Skill1Button", function() InputController.useSkill(1) end)
	connectButton("Skill2Button", function() InputController.useSkill(2) end)
	connectButton("Skill3Button", function() InputController.useSkill(3) end)
	connectButton("UltimateButton", function() InputController.useUltimate() end)
	connectButton("DodgeButton", function() InputController.dodge() end)
end

--------------------------------------------------
-- Actions
--------------------------------------------------

function InputController.useSkill(index: number)
	if skillEvent then
		skillEvent:FireServer(index)
	end
end

function InputController.useUltimate()
	if ultimateEvent then
		ultimateEvent:FireServer()
	end
end

function InputController.dodge()
	if dodgeEvent then
		dodgeEvent:FireServer()
	end

	-- Client-side dodge movement (responsive feel, server validates)
	local character = player.Character
	if not character then return end
	local rootPart = character:FindFirstChild("HumanoidRootPart")
	if not rootPart then return end

	local moveDir = character.Humanoid.MoveDirection
	if moveDir.Magnitude < 0.1 then
		moveDir = rootPart.CFrame.LookVector
	end

	-- Quick dash effect
	local dodgeDist = 20
	local bodyVelocity = Instance.new("BodyVelocity")
	bodyVelocity.MaxForce = Vector3.new(50000, 0, 50000)
	bodyVelocity.Velocity = moveDir.Unit * dodgeDist / 0.3
	bodyVelocity.Parent = rootPart

	task.delay(0.3, function()
		if bodyVelocity.Parent then
			bodyVelocity:Destroy()
		end
	end)
end

--------------------------------------------------
-- Auto-attack
--------------------------------------------------

function InputController._updateAutoAttack(dt: number)
	if not autoAttack then return end

	autoAttackTimer -= dt
	if autoAttackTimer > 0 then return end
	autoAttackTimer = AUTO_ATTACK_INTERVAL

	-- Find nearest enemy in range
	local character = player.Character
	if not character or not character:FindFirstChild("HumanoidRootPart") then
		return
	end

	local pos = character.HumanoidRootPart.Position
	local attackRange = 10 -- default melee range

	local nearestEnemy = nil
	local nearestDist = attackRange

	for _, obj in workspace:GetChildren() do
		if obj:IsA("Model") and obj:FindFirstChild("Humanoid") and obj ~= character then
			local hrp = obj:FindFirstChild("HumanoidRootPart")
			if hrp then
				local dist = (hrp.Position - pos).Magnitude
				if dist < nearestDist then
					nearestEnemy = obj
					nearestDist = dist
				end
			end
		end
	end

	if nearestEnemy and skillEvent then
		-- Auto-attack uses skill slot 1 (basic attack)
		skillEvent:FireServer(1, nearestEnemy)
	end
end

--------------------------------------------------
-- Target aiming (mouse/touch)
--------------------------------------------------

function InputController.getAimPosition(): Vector3?
	local mouse = player:GetMouse()
	if mouse then
		return mouse.Hit.Position
	end
	return nil
end

return InputController
