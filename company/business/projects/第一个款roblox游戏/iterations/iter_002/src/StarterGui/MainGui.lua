-- MainGui: builds the entire UI programmatically
-- This is a LocalScript that creates the ScreenGui and all child elements

local Players = game:GetService("Players")
local player = Players.LocalPlayer
local playerGui = player:WaitForChild("PlayerGui")

-- Create ScreenGui
local screenGui = Instance.new("ScreenGui")
screenGui.Name = "MainScreenGui"
screenGui.ResetOnSpawn = false
screenGui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
screenGui.Parent = playerGui

-- Main container frame
local mainFrame = Instance.new("Frame")
mainFrame.Name = "MainFrame"
mainFrame.Size = UDim2.new(1, 0, 1, 0)
mainFrame.BackgroundColor3 = Color3.fromRGB(20, 20, 30)
mainFrame.BorderSizePixel = 0
mainFrame.Parent = screenGui

-- Title
local title = Instance.new("TextLabel")
title.Name = "Title"
title.Size = UDim2.new(1, 0, 0, 60)
title.Position = UDim2.new(0, 0, 0, 10)
title.BackgroundTransparency = 1
title.Text = "TECH STARTUP TYCOON"
title.TextColor3 = Color3.fromRGB(0, 255, 200)
title.Font = Enum.Font.GothamBold
title.TextSize = 32
title.Parent = mainFrame

-- Stats panel (top area)
local statsFrame = Instance.new("Frame")
statsFrame.Name = "StatsFrame"
statsFrame.Size = UDim2.new(0.5, -20, 0, 100)
statsFrame.Position = UDim2.new(0.25, 10, 0, 75)
statsFrame.BackgroundColor3 = Color3.fromRGB(30, 30, 50)
statsFrame.BorderSizePixel = 0
statsFrame.Parent = mainFrame

local statsCorner = Instance.new("UICorner")
statsCorner.CornerRadius = UDim.new(0, 10)
statsCorner.Parent = statsFrame

-- Code label
local codeLabel = Instance.new("TextLabel")
codeLabel.Name = "CodeLabel"
codeLabel.Size = UDim2.new(0.5, 0, 0, 30)
codeLabel.Position = UDim2.new(0, 10, 0, 10)
codeLabel.BackgroundTransparency = 1
codeLabel.Text = "Code: 0"
codeLabel.TextColor3 = Color3.fromRGB(100, 255, 100)
codeLabel.Font = Enum.Font.GothamBold
codeLabel.TextSize = 20
codeLabel.TextXAlignment = Enum.TextXAlignment.Left
codeLabel.Parent = mainFrame

-- Cash label
local cashLabel = Instance.new("TextLabel")
cashLabel.Name = "CashLabel"
cashLabel.Size = UDim2.new(0.5, 0, 0, 30)
cashLabel.Position = UDim2.new(0.5, 0, 0, 10)
cashLabel.BackgroundTransparency = 1
cashLabel.Text = "Cash: $0"
cashLabel.TextColor3 = Color3.fromRGB(255, 215, 0)
cashLabel.Font = Enum.Font.GothamBold
cashLabel.TextSize = 20
cashLabel.TextXAlignment = Enum.TextXAlignment.Left
cashLabel.Parent = mainFrame

-- Rebirth label
local rebirthLabel = Instance.new("TextLabel")
rebirthLabel.Name = "RebirthLabel"
rebirthLabel.Size = UDim2.new(0.5, 0, 0, 25)
rebirthLabel.Position = UDim2.new(0, 10, 0, 45)
rebirthLabel.BackgroundTransparency = 1
rebirthLabel.Text = "Rebirths: 0"
rebirthLabel.TextColor3 = Color3.fromRGB(200, 100, 255)
rebirthLabel.Font = Enum.Font.Gotham
rebirthLabel.TextSize = 16
rebirthLabel.TextXAlignment = Enum.TextXAlignment.Left
rebirthLabel.Parent = mainFrame

-- Auto-code label
local autoLabel = Instance.new("TextLabel")
autoLabel.Name = "AutoLabel"
autoLabel.Size = UDim2.new(0.5, 0, 0, 25)
autoLabel.Position = UDim2.new(0.5, 0, 0, 45)
autoLabel.BackgroundTransparency = 1
autoLabel.Text = "Auto: 0 code/s"
autoLabel.TextColor3 = Color3.fromRGB(100, 200, 255)
autoLabel.Font = Enum.Font.Gotham
autoLabel.TextSize = 16
autoLabel.TextXAlignment = Enum.TextXAlignment.Left
autoLabel.Parent = mainFrame

-- Click Button (big center button)
local clickButton = Instance.new("TextButton")
clickButton.Name = "ClickButton"
clickButton.Size = UDim2.new(0, 200, 0, 200)
clickButton.Position = UDim2.new(0.5, -100, 0.35, 0)
clickButton.BackgroundColor3 = Color3.fromRGB(0, 120, 200)
clickButton.Text = "WRITE\nCODE"
clickButton.TextColor3 = Color3.fromRGB(255, 255, 255)
clickButton.Font = Enum.Font.GothamBold
clickButton.TextSize = 28
clickButton.Parent = mainFrame

local clickCorner = Instance.new("UICorner")
clickCorner.CornerRadius = UDim.new(0.5, 0)
clickCorner.Parent = clickButton

-- Sell Button
local sellButton = Instance.new("TextButton")
sellButton.Name = "SellButton"
sellButton.Size = UDim2.new(0, 180, 0, 50)
sellButton.Position = UDim2.new(0.5, -90, 0.65, 10)
sellButton.BackgroundColor3 = Color3.fromRGB(40, 160, 40)
sellButton.Text = "SELL CODE FOR CASH"
sellButton.TextColor3 = Color3.fromRGB(255, 255, 255)
sellButton.Font = Enum.Font.GothamBold
sellButton.TextSize = 16
sellButton.Parent = mainFrame

local sellCorner = Instance.new("UICorner")
sellCorner.CornerRadius = UDim.new(0, 10)
sellCorner.Parent = sellButton

-- Rebirth Button
local rebirthButton = Instance.new("TextButton")
rebirthButton.Name = "RebirthButton"
rebirthButton.Size = UDim2.new(0, 180, 0, 50)
rebirthButton.Position = UDim2.new(0.5, -90, 0.75, 10)
rebirthButton.BackgroundColor3 = Color3.fromRGB(160, 40, 200)
rebirthButton.Text = "REBIRTH ($100K)"
rebirthButton.TextColor3 = Color3.fromRGB(255, 255, 255)
rebirthButton.Font = Enum.Font.GothamBold
rebirthButton.TextSize = 16
rebirthButton.Parent = mainFrame

local rebirthCorner = Instance.new("UICorner")
rebirthCorner.CornerRadius = UDim.new(0, 10)
rebirthCorner.Parent = rebirthButton

-- Shop Frame (right side scrolling)
local shopFrame = Instance.new("ScrollingFrame")
shopFrame.Name = "ShopFrame"
shopFrame.Size = UDim2.new(0.25, -10, 0.7, 0)
shopFrame.Position = UDim2.new(0.73, 0, 0.1, 0)
shopFrame.BackgroundColor3 = Color3.fromRGB(25, 25, 45)
shopFrame.BorderSizePixel = 0
shopFrame.ScrollBarThickness = 6
shopFrame.CanvasSize = UDim2.new(0, 0, 0, 400)
shopFrame.Parent = mainFrame

local shopCorner = Instance.new("UICorner")
shopCorner.CornerRadius = UDim.new(0, 10)
shopCorner.Parent = shopFrame

local shopLayout = Instance.new("UIListLayout")
shopLayout.SortOrder = Enum.SortOrder.LayoutOrder
shopLayout.Padding = UDim.new(0, 5)
shopLayout.Parent = shopFrame

local shopPadding = Instance.new("UIPadding")
shopPadding.PaddingTop = UDim.new(0, 5)
shopPadding.PaddingLeft = UDim.new(0, 5)
shopPadding.PaddingRight = UDim.new(0, 5)
shopPadding.Parent = shopFrame

-- Shop title
local shopTitle = Instance.new("TextLabel")
shopTitle.Name = "ShopTitle"
shopTitle.Size = UDim2.new(0.25, -10, 0, 35)
shopTitle.Position = UDim2.new(0.73, 0, 0.04, 0)
shopTitle.BackgroundTransparency = 1
shopTitle.Text = "UPGRADES"
shopTitle.TextColor3 = Color3.fromRGB(255, 200, 50)
shopTitle.Font = Enum.Font.GothamBold
shopTitle.TextSize = 20
shopTitle.Parent = mainFrame

print("[TechStartupTycoon] MainGui created")
