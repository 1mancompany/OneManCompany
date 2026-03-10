--[[
	ClientMain.luau — Client bootstrap script
	StarterPlayerScripts/ClientMain (LocalScript)

	Initializes all client modules in correct order and shows
	the welcome/start screen with controls.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local player = Players.LocalPlayer

-- Require client modules (they are ModuleScripts in StarterPlayerScripts)
local UIManager = require(script.Parent.UIManager)
local CombatClient = require(script.Parent.CombatClient)
local InputController = require(script.Parent.InputController)

-- Initialize in order: UI first (creates HUD), then combat VFX, then input
UIManager.init()
CombatClient.init()

-- InputController needs CombatHUD to exist for mobile binds, give a small delay
task.defer(function()
	InputController.init()
end)

--------------------------------------------------
-- Welcome / Start Run Screen
--------------------------------------------------

local function showWelcomeScreen()
	local gui = player:WaitForChild("PlayerGui")

	local screen = Instance.new("ScreenGui")
	screen.Name = "WelcomeScreen"
	screen.ResetOnSpawn = false
	screen.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
	screen.DisplayOrder = 10
	screen.Parent = gui

	-- Full background
	local bg = Instance.new("Frame")
	bg.Size = UDim2.new(1, 0, 1, 0)
	bg.BackgroundColor3 = Color3.new(0.05, 0.02, 0.1)
	bg.BackgroundTransparency = 0.3
	bg.BorderSizePixel = 0
	bg.Parent = screen

	-- Title
	local title = Instance.new("TextLabel")
	title.Size = UDim2.new(0, 500, 0, 80)
	title.Position = UDim2.new(0.5, -250, 0.08, 0)
	title.BackgroundTransparency = 1
	title.Text = "SOUL RIFT"
	title.TextColor3 = Color3.new(1, 0.85, 0)
	title.Font = Enum.Font.GothamBold
	title.TextSize = 56
	title.Parent = screen

	local subtitle = Instance.new("TextLabel")
	subtitle.Size = UDim2.new(0, 500, 0, 30)
	subtitle.Position = UDim2.new(0.5, -250, 0.08, 80)
	subtitle.BackgroundTransparency = 1
	subtitle.Text = "Roguelike Action RPG"
	subtitle.TextColor3 = Color3.new(0.7, 0.7, 0.8)
	subtitle.Font = Enum.Font.Gotham
	subtitle.TextSize = 18
	subtitle.Parent = screen

	-- Controls panel
	local controlsPanel = Instance.new("Frame")
	controlsPanel.Size = UDim2.new(0, 420, 0, 320)
	controlsPanel.Position = UDim2.new(0.5, -210, 0.25, 0)
	controlsPanel.BackgroundColor3 = Color3.new(0.08, 0.08, 0.12)
	controlsPanel.BackgroundTransparency = 0.2
	controlsPanel.BorderSizePixel = 0
	controlsPanel.Parent = screen

	local panelCorner = Instance.new("UICorner")
	panelCorner.CornerRadius = UDim.new(0, 16)
	panelCorner.Parent = controlsPanel

	local panelStroke = Instance.new("UIStroke")
	panelStroke.Color = Color3.new(0.3, 0.25, 0.5)
	panelStroke.Thickness = 2
	panelStroke.Parent = controlsPanel

	local controlsTitle = Instance.new("TextLabel")
	controlsTitle.Size = UDim2.new(1, 0, 0, 35)
	controlsTitle.Position = UDim2.new(0, 0, 0, 10)
	controlsTitle.BackgroundTransparency = 1
	controlsTitle.Text = "CONTROLS"
	controlsTitle.TextColor3 = Color3.new(1, 0.85, 0)
	controlsTitle.Font = Enum.Font.GothamBold
	controlsTitle.TextSize = 20
	controlsTitle.Parent = controlsPanel

	local controls = {
		{ key = "W A S D", action = "Move" },
		{ key = "Mouse", action = "Look around" },
		{ key = "Q", action = "Skill 1 — Swift Slash" },
		{ key = "E", action = "Skill 2 — Sword Wave" },
		{ key = "R", action = "Skill 3 — Counter Dash" },
		{ key = "F", action = "Ultimate (when energy full)" },
		{ key = "Space", action = "Dodge (i-frames)" },
		{ key = "T", action = "Toggle auto-attack" },
	}

	for i, ctrl in ipairs(controls) do
		local row = Instance.new("Frame")
		row.Size = UDim2.new(1, -40, 0, 28)
		row.Position = UDim2.new(0, 20, 0, 45 + (i - 1) * 32)
		row.BackgroundTransparency = 1
		row.Parent = controlsPanel

		local keyLabel = Instance.new("TextLabel")
		keyLabel.Size = UDim2.new(0, 100, 1, 0)
		keyLabel.BackgroundColor3 = Color3.new(0.15, 0.15, 0.2)
		keyLabel.BackgroundTransparency = 0.3
		keyLabel.Text = ctrl.key
		keyLabel.TextColor3 = Color3.new(1, 1, 1)
		keyLabel.Font = Enum.Font.Code
		keyLabel.TextSize = 14
		keyLabel.Parent = row

		local keyCorner = Instance.new("UICorner")
		keyCorner.CornerRadius = UDim.new(0, 4)
		keyCorner.Parent = keyLabel

		local actionLabel = Instance.new("TextLabel")
		actionLabel.Size = UDim2.new(1, -115, 1, 0)
		actionLabel.Position = UDim2.new(0, 115, 0, 0)
		actionLabel.BackgroundTransparency = 1
		actionLabel.Text = ctrl.action
		actionLabel.TextColor3 = Color3.new(0.8, 0.8, 0.85)
		actionLabel.Font = Enum.Font.Gotham
		actionLabel.TextSize = 14
		actionLabel.TextXAlignment = Enum.TextXAlignment.Left
		actionLabel.Parent = row
	end

	-- Start Run Button
	local startBtn = Instance.new("TextButton")
	startBtn.Name = "StartRunButton"
	startBtn.Size = UDim2.new(0, 280, 0, 60)
	startBtn.Position = UDim2.new(0.5, -140, 0.82, 0)
	startBtn.BackgroundColor3 = Color3.new(0.2, 0.6, 0.9)
	startBtn.Text = "START DUNGEON RUN"
	startBtn.TextColor3 = Color3.new(1, 1, 1)
	startBtn.Font = Enum.Font.GothamBold
	startBtn.TextSize = 22
	startBtn.BorderSizePixel = 0
	startBtn.Parent = screen

	local btnCorner = Instance.new("UICorner")
	btnCorner.CornerRadius = UDim.new(0, 12)
	btnCorner.Parent = startBtn

	local btnStroke = Instance.new("UIStroke")
	btnStroke.Color = Color3.new(0.4, 0.7, 1)
	btnStroke.Thickness = 2
	btnStroke.Parent = startBtn

	-- Pulse animation on button
	task.spawn(function()
		while startBtn.Parent do
			TweenService:Create(startBtn, TweenInfo.new(0.8, Enum.EasingStyle.Sine, Enum.EasingDirection.InOut), {
				BackgroundColor3 = Color3.new(0.3, 0.7, 1),
			}):Play()
			task.wait(0.8)
			if not startBtn.Parent then break end
			TweenService:Create(startBtn, TweenInfo.new(0.8, Enum.EasingStyle.Sine, Enum.EasingDirection.InOut), {
				BackgroundColor3 = Color3.new(0.2, 0.6, 0.9),
			}):Play()
			task.wait(0.8)
		end
	end)

	-- Start run on click
	local startRunEvent = ReplicatedStorage:WaitForChild("StartRunEvent", 10)

	startBtn.Activated:Connect(function()
		if startRunEvent then
			startRunEvent:FireServer()
		end
		-- Fade out welcome screen
		TweenService:Create(bg, TweenInfo.new(0.5), {
			BackgroundTransparency = 1,
		}):Play()
		for _, child in screen:GetDescendants() do
			if child:IsA("TextLabel") or child:IsA("TextButton") then
				TweenService:Create(child, TweenInfo.new(0.5), {
					TextTransparency = 1,
				}):Play()
			end
			if child:IsA("Frame") then
				TweenService:Create(child, TweenInfo.new(0.5), {
					BackgroundTransparency = 1,
				}):Play()
			end
			if child:IsA("UIStroke") then
				TweenService:Create(child, TweenInfo.new(0.5), {
					Transparency = 1,
				}):Play()
			end
		end
		task.delay(0.6, function()
			screen:Destroy()
		end)
	end)
end

--------------------------------------------------
-- Controls hint (persistent small overlay during gameplay)
--------------------------------------------------

local function showControlsHint()
	local gui = player:WaitForChild("PlayerGui")

	local screen = Instance.new("ScreenGui")
	screen.Name = "ControlsHint"
	screen.ResetOnSpawn = false
	screen.DisplayOrder = 5
	screen.Parent = gui

	local hint = Instance.new("TextLabel")
	hint.Size = UDim2.new(0, 350, 0, 25)
	hint.Position = UDim2.new(0.5, -175, 1, -25)
	hint.BackgroundTransparency = 1
	hint.Text = "Q/E/R: Skills | F: Ultimate | Space: Dodge | T: Auto-attack"
	hint.TextColor3 = Color3.new(0.6, 0.6, 0.65)
	hint.Font = Enum.Font.Gotham
	hint.TextSize = 11
	hint.Parent = screen

	-- Fade out after 30 seconds
	task.delay(30, function()
		if hint.Parent then
			TweenService:Create(hint, TweenInfo.new(2), {
				TextTransparency = 1,
			}):Play()
		end
	end)
end

--------------------------------------------------
-- Boot
--------------------------------------------------

-- Wait for character to load
player.CharacterAdded:Wait()
task.wait(1)

showWelcomeScreen()
showControlsHint()

print("[ClientMain] Soul Rift client initialized")
