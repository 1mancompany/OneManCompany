-- GameClient: Client-side input handling, camera, and UI updates
-- LocalScript placed in StarterPlayerScripts

local Players = game:GetService("Players")
local UserInputService = game:GetService("UserInputService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local TweenService = game:GetService("TweenService")

local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))

local player = Players.LocalPlayer
local camera = Workspace.CurrentCamera

-- RemoteEvents
local LaunchBirdEvent = ReplicatedStorage:WaitForChild("LaunchBird")
local UpdateUIEvent = ReplicatedStorage:WaitForChild("UpdateUI")
local LevelCompleteEvent = ReplicatedStorage:WaitForChild("LevelComplete")
local ResetLevelEvent = ReplicatedStorage:WaitForChild("ResetLevel")
local NextLevelEvent = ReplicatedStorage:WaitForChild("NextLevel")

-- Input state
local isPulling = false
local pullStartPos = nil
local currentPullDistance = 0
local canLaunch = true

-- Aim visualization parts
local aimLine = nil
local trajectoryParts = {}

---------------------------------------------
-- CAMERA SETUP
---------------------------------------------

local function setupCamera()
	camera.CameraType = Enum.CameraType.Scriptable

	-- Side-view camera for slingshot gameplay
	local cameraPos = Vector3.new(-20, 30, 80)
	local lookAt = Vector3.new(20, 10, 0)
	camera.CFrame = CFrame.lookAt(cameraPos, lookAt)
end

---------------------------------------------
-- AIM VISUALIZATION
---------------------------------------------

local function createAimLine()
	if aimLine then aimLine:Destroy() end
	aimLine = Instance.new("Part")
	aimLine.Name = "AimLine"
	aimLine.Anchored = true
	aimLine.CanCollide = false
	aimLine.Material = Enum.Material.Neon
	aimLine.Color = Color3.fromRGB(255, 255, 0)
	aimLine.Transparency = 0.3
	aimLine.Size = Vector3.new(0.3, 0.3, 1)
	aimLine.Parent = nil
end

local function clearTrajectory()
	for _, part in ipairs(trajectoryParts) do
		if part and part.Parent then
			part:Destroy()
		end
	end
	trajectoryParts = {}
	if aimLine and aimLine.Parent then
		aimLine.Parent = nil
	end
end

local function showTrajectory(origin, velocity)
	clearTrajectory()

	local gravity = Vector3.new(0, -GameConfig.GRAVITY, 0)
	local dt = 0.05
	local pos = origin
	local vel = velocity

	for i = 1, 30 do
		vel = vel + gravity * dt
		pos = pos + vel * dt

		-- Don't show below ground
		if pos.Y < 0 then break end

		local dot = Instance.new("Part")
		dot.Name = "TrajectoryDot"
		dot.Shape = Enum.PartType.Ball
		dot.Size = Vector3.new(0.5, 0.5, 0.5)
		dot.Position = pos
		dot.Anchored = true
		dot.CanCollide = false
		dot.Material = Enum.Material.Neon
		dot.Color = Color3.fromRGB(255, 255, 100)
		dot.Transparency = 0.2 + (i / 30) * 0.6
		dot.Parent = Workspace

		table.insert(trajectoryParts, dot)
	end
end

---------------------------------------------
-- INPUT HANDLING
---------------------------------------------

local function getSlingshotOrigin()
	return Vector3.new(
		GameConfig.SLINGSHOT_X,
		GameConfig.SLINGSHOT_HEIGHT + 2,
		0
	)
end

local function screenToLaunchVector(startScreen, endScreen)
	-- Pull direction is reversed: drag back to launch forward
	local dx = (startScreen.X - endScreen.X)
	local dy = (startScreen.Y - endScreen.Y)

	-- Scale screen pixels to world units
	local pullMagnitude = math.sqrt(dx * dx + dy * dy) * 0.08
	pullMagnitude = math.min(pullMagnitude, GameConfig.MAX_PULL_DISTANCE)

	-- Calculate 3D direction: primarily forward (negative Z from camera perspective)
	-- and upward based on vertical pull
	local horizontalAngle = math.atan2(dx, 1) * 0.3
	local verticalFactor = math.max(0.3, dy * 0.003)

	local direction = Vector3.new(
		math.sin(horizontalAngle) * 0.3 + 1, -- primarily forward (+X direction)
		verticalFactor,
		0  -- keep it on the XY plane for side-view gameplay
	).Unit

	return direction, pullMagnitude
end

-- Mouse/Touch down
UserInputService.InputBegan:Connect(function(input, gameProcessed)
	if gameProcessed then return end
	if not canLaunch then return end

	if input.UserInputType == Enum.UserInputType.MouseButton1
		or input.UserInputType == Enum.UserInputType.Touch then
		isPulling = true
		pullStartPos = input.Position
		createAimLine()
	end
end)

-- Mouse/Touch move
UserInputService.InputChanged:Connect(function(input, gameProcessed)
	if not isPulling then return end

	if input.UserInputType == Enum.UserInputType.MouseMovement
		or input.UserInputType == Enum.UserInputType.Touch then
		local currentPos = input.Position

		local direction, pullDist = screenToLaunchVector(pullStartPos, currentPos)
		currentPullDistance = pullDist

		-- Show trajectory preview
		local origin = getSlingshotOrigin()
		local launchVel = direction * pullDist * GameConfig.LAUNCH_MULTIPLIER
		showTrajectory(origin, launchVel)
	end
end)

-- Mouse/Touch release = LAUNCH
UserInputService.InputEnded:Connect(function(input, gameProcessed)
	if not isPulling then return end

	if input.UserInputType == Enum.UserInputType.MouseButton1
		or input.UserInputType == Enum.UserInputType.Touch then
		isPulling = false
		clearTrajectory()

		if currentPullDistance < 2 then
			-- Too small a pull, ignore
			return
		end

		local direction, pullDist = screenToLaunchVector(pullStartPos, input.Position)
		local origin = getSlingshotOrigin()

		-- Fire to server
		LaunchBirdEvent:FireServer(origin, direction, pullDist)

		-- Brief cooldown to prevent spam
		canLaunch = false
		task.delay(1, function()
			canLaunch = true
		end)
	end
end)

-- Keyboard shortcuts
UserInputService.InputBegan:Connect(function(input, gameProcessed)
	if gameProcessed then return end

	if input.KeyCode == Enum.KeyCode.R then
		ResetLevelEvent:FireServer()
	end
end)

---------------------------------------------
-- UI UPDATES
---------------------------------------------

local screenGui = player:WaitForChild("PlayerGui"):WaitForChild("GameHUD")

local function updateHUD(data)
	if not screenGui then return end

	local scoreLabel = screenGui:FindFirstChild("ScoreLabel", true)
	local levelLabel = screenGui:FindFirstChild("LevelLabel", true)
	local birdsLabel = screenGui:FindFirstChild("BirdsLabel", true)
	local levelNameLabel = screenGui:FindFirstChild("LevelNameLabel", true)

	if scoreLabel then
		scoreLabel.Text = "得分: " .. tostring(data.score or 0)
	end
	if levelLabel then
		levelLabel.Text = "关卡: " .. tostring(data.level or 1)
	end
	if birdsLabel then
		birdsLabel.Text = "剩余小鸟: " .. tostring(data.birdsRemaining or 0)
	end
	if levelNameLabel then
		levelNameLabel.Text = data.levelName or ""
	end

	-- Show game over overlay
	if data.gameOver then
		local overlay = screenGui:FindFirstChild("GameOverOverlay", true)
		if overlay then
			overlay.Visible = true
			local finalScore = overlay:FindFirstChild("FinalScoreLabel", true)
			if finalScore then
				finalScore.Text = "最终得分: " .. tostring(data.score or 0)
			end
		end
	end
end

UpdateUIEvent.OnClientEvent:Connect(function(data)
	updateHUD(data)
end)

---------------------------------------------
-- LEVEL COMPLETE HANDLER
---------------------------------------------

LevelCompleteEvent.OnClientEvent:Connect(function(data)
	local overlay = screenGui:FindFirstChild("LevelCompleteOverlay", true)
	if not overlay then return end

	overlay.Visible = true

	local titleLabel = overlay:FindFirstChild("TitleLabel", true)
	local detailLabel = overlay:FindFirstChild("DetailLabel", true)

	if data.victory then
		if titleLabel then titleLabel.Text = "恭喜通关! 🎉" end
		if detailLabel then
			detailLabel.Text = "全部 " .. tostring(data.totalLevels) .. " 关完成!\n最终得分: " .. tostring(data.score)
		end
	else
		if titleLabel then titleLabel.Text = "关卡完成!" end
		if detailLabel then
			local bonusText = data.bonus > 0 and ("\n剩余鸟奖励: +" .. tostring(data.bonus)) or ""
			detailLabel.Text = "得分: " .. tostring(data.score) .. bonusText
		end
	end
end)

---------------------------------------------
-- UI BUTTON HANDLERS (connected via ScreenGui)
---------------------------------------------

-- These get connected after ScreenGui loads
task.defer(function()
	local nextBtn = screenGui:FindFirstChild("NextLevelButton", true)
	local retryBtn = screenGui:FindFirstChild("RetryButton", true)
	local retryGameOverBtn = screenGui:FindFirstChild("RetryGameOverButton", true)

	if nextBtn then
		nextBtn.MouseButton1Click:Connect(function()
			NextLevelEvent:FireServer()
			local overlay = screenGui:FindFirstChild("LevelCompleteOverlay", true)
			if overlay then overlay.Visible = false end
		end)
	end

	if retryBtn then
		retryBtn.MouseButton1Click:Connect(function()
			ResetLevelEvent:FireServer()
			local overlay = screenGui:FindFirstChild("LevelCompleteOverlay", true)
			if overlay then overlay.Visible = false end
		end)
	end

	if retryGameOverBtn then
		retryGameOverBtn.MouseButton1Click:Connect(function()
			ResetLevelEvent:FireServer()
			local overlay = screenGui:FindFirstChild("GameOverOverlay", true)
			if overlay then overlay.Visible = false end
		end)
	end
end)

---------------------------------------------
-- INITIALIZATION
---------------------------------------------

setupCamera()
createAimLine()

print("[SlingRoyale Client] Initialized for player: " .. player.Name)
