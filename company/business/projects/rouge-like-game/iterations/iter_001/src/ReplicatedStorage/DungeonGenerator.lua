-- DungeonGenerator: procedural dungeon floor generation
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))

local DungeonGenerator = {}

local ROOM = GameConfig.ROOM_SIZE
local HEIGHT = GameConfig.ROOM_HEIGHT

-- Seed-based random for deterministic floors
local function seededRandom(seed)
	local rng = Random.new(seed)
	return rng
end

-- Create a basic material part
local function makePart(parent, name, size, position, color, material, transparency)
	local part = Instance.new("Part")
	part.Name = name
	part.Size = size
	part.Position = position
	part.Anchored = true
	part.Color = color or Color3.fromRGB(60, 60, 60)
	part.Material = material or Enum.Material.Slate
	part.Transparency = transparency or 0
	part.Parent = parent
	return part
end

-- Build the floor (ground plate)
local function buildFloor(parent, origin)
	makePart(parent, "Floor", Vector3.new(ROOM, 1, ROOM),
		origin + Vector3.new(0, -0.5, 0),
		Color3.fromRGB(40, 40, 50), Enum.Material.Slate)
end

-- Build walls with an entrance gap
local function buildWalls(parent, origin, hasExit)
	local wallThickness = 3
	-- North wall
	makePart(parent, "WallNorth", Vector3.new(ROOM, HEIGHT, wallThickness),
		origin + Vector3.new(0, HEIGHT / 2, -ROOM / 2),
		Color3.fromRGB(50, 50, 60), Enum.Material.Cobblestone)
	-- South wall (with exit gap if hasExit)
	if hasExit then
		local gapWidth = 10
		local sideWidth = (ROOM - gapWidth) / 2
		makePart(parent, "WallSouthL", Vector3.new(sideWidth, HEIGHT, wallThickness),
			origin + Vector3.new(-(sideWidth / 2 + gapWidth / 2), HEIGHT / 2, ROOM / 2),
			Color3.fromRGB(50, 50, 60), Enum.Material.Cobblestone)
		makePart(parent, "WallSouthR", Vector3.new(sideWidth, HEIGHT, wallThickness),
			origin + Vector3.new((sideWidth / 2 + gapWidth / 2), HEIGHT / 2, ROOM / 2),
			Color3.fromRGB(50, 50, 60), Enum.Material.Cobblestone)
	else
		makePart(parent, "WallSouth", Vector3.new(ROOM, HEIGHT, wallThickness),
			origin + Vector3.new(0, HEIGHT / 2, ROOM / 2),
			Color3.fromRGB(50, 50, 60), Enum.Material.Cobblestone)
	end
	-- East wall
	makePart(parent, "WallEast", Vector3.new(wallThickness, HEIGHT, ROOM),
		origin + Vector3.new(ROOM / 2, HEIGHT / 2, 0),
		Color3.fromRGB(50, 50, 60), Enum.Material.Cobblestone)
	-- West wall
	makePart(parent, "WallWest", Vector3.new(wallThickness, HEIGHT, ROOM),
		origin + Vector3.new(-ROOM / 2, HEIGHT / 2, 0),
		Color3.fromRGB(50, 50, 60), Enum.Material.Cobblestone)
end

-- Place torches/lights for ambience
local function buildLighting(parent, origin, rng)
	local torchPositions = {
		origin + Vector3.new(-ROOM / 2 + 2, 8, -ROOM / 2 + 2),
		origin + Vector3.new(ROOM / 2 - 2, 8, -ROOM / 2 + 2),
		origin + Vector3.new(-ROOM / 2 + 2, 8, ROOM / 2 - 2),
		origin + Vector3.new(ROOM / 2 - 2, 8, ROOM / 2 - 2),
	}
	for _, pos in ipairs(torchPositions) do
		local torch = makePart(parent, "Torch", Vector3.new(1, 3, 1), pos,
			Color3.fromRGB(139, 90, 43), Enum.Material.Wood)
		local light = Instance.new("PointLight")
		light.Color = Color3.fromRGB(255, 150, 50)
		light.Brightness = 1.5
		light.Range = 20
		light.Parent = torch
		-- Fire effect
		local fire = Instance.new("Fire")
		fire.Size = 3
		fire.Heat = 5
		fire.Parent = torch
	end
end

-- Place random obstacles (pillars, crates)
local function buildObstacles(parent, origin, rng, count)
	for i = 1, count do
		local x = rng:NextNumber(-ROOM / 2 + 5, ROOM / 2 - 5)
		local z = rng:NextNumber(-ROOM / 2 + 5, ROOM / 2 - 5)
		local height = rng:NextNumber(3, 8)
		local width = rng:NextNumber(2, 5)
		local obstacle = makePart(parent, "Obstacle_" .. i,
			Vector3.new(width, height, width),
			origin + Vector3.new(x, height / 2, z),
			Color3.fromRGB(70, 70, 80), Enum.Material.Cobblestone)
	end
end

-- Determine which enemies to spawn on this floor
function DungeonGenerator.getEnemiesForFloor(floorNum)
	local enemies = {}
	for name, data in pairs(GameConfig.ENEMIES) do
		if floorNum >= data.floorMin and floorNum <= data.floorMax then
			table.insert(enemies, { name = name, data = data })
		end
	end
	return enemies
end

-- Get spawn positions for enemies within the room
function DungeonGenerator.getEnemySpawnPositions(origin, count, seed)
	local rng = seededRandom(seed)
	local positions = {}
	local margin = 8
	for i = 1, count do
		local x = rng:NextNumber(-ROOM / 2 + margin, ROOM / 2 - margin)
		local z = rng:NextNumber(-ROOM / 2 + margin, ROOM / 2 - margin)
		table.insert(positions, origin + Vector3.new(x, 3, z))
	end
	return positions
end

-- Calculate enemy count for this floor
function DungeonGenerator.getEnemyCount(floorNum)
	local base = GameConfig.ENEMIES_PER_FLOOR_BASE
	local scale = GameConfig.ENEMIES_PER_FLOOR_SCALE
	return math.floor(base + floorNum * scale)
end

-- Build a complete dungeon floor
-- Returns: the folder containing all floor geometry
function DungeonGenerator.buildFloor(floorNum, origin, parentFolder)
	local seed = floorNum * 12345 + 67890
	local rng = seededRandom(seed)

	local folder = Instance.new("Folder")
	folder.Name = "Floor_" .. floorNum
	folder.Parent = parentFolder

	local isBossFloor = (floorNum % GameConfig.BOSS_FLOOR_INTERVAL == 0)
	local hasExit = true  -- All floors have exit (blocked until enemies cleared)

	-- Build room geometry
	buildFloor(folder, origin)
	buildWalls(folder, origin, hasExit)
	buildLighting(folder, origin, rng)

	local obstacleCount = rng:NextInteger(2, 5)
	if isBossFloor then
		obstacleCount = 1  -- Boss rooms are more open
	end
	buildObstacles(folder, origin, rng, obstacleCount)

	-- Exit door (locked until floor cleared)
	local doorPart = makePart(folder, "ExitDoor",
		Vector3.new(10, HEIGHT, 2),
		origin + Vector3.new(0, HEIGHT / 2, ROOM / 2),
		Color3.fromRGB(180, 0, 0), Enum.Material.Neon, 0.5)
	doorPart:SetAttribute("Locked", true)
	doorPart:SetAttribute("FloorNum", floorNum)

	-- Spawn point indicator
	local spawnMarker = makePart(folder, "SpawnPoint",
		Vector3.new(4, 0.2, 4),
		origin + Vector3.new(0, 0.1, -ROOM / 2 + 8),
		Color3.fromRGB(0, 200, 0), Enum.Material.Neon, 0.3)

	-- Floor number indicator (billboard)
	local floorLabel = Instance.new("BillboardGui")
	floorLabel.Name = "FloorLabel"
	floorLabel.Size = UDim2.new(4, 0, 2, 0)
	floorLabel.StudsOffset = Vector3.new(0, 12, -ROOM / 2 + 2)
	floorLabel.Adornee = spawnMarker
	floorLabel.AlwaysOnTop = true
	floorLabel.Parent = folder

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(1, 0, 1, 0)
	label.BackgroundTransparency = 1
	label.Text = isBossFloor and ("BOSS - Floor " .. floorNum) or ("Floor " .. floorNum)
	label.TextColor3 = isBossFloor and Color3.fromRGB(255, 50, 50) or Color3.fromRGB(255, 255, 255)
	label.TextScaled = true
	label.Font = Enum.Font.GothamBold
	label.Parent = floorLabel

	return folder, isBossFloor
end

-- Clean up a floor
function DungeonGenerator.destroyFloor(floorFolder)
	if floorFolder then
		floorFolder:Destroy()
	end
end

return DungeonGenerator
