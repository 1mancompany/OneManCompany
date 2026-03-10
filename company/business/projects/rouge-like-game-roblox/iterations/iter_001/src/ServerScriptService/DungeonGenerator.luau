--[[
	DungeonGenerator.luau — Procedural dungeon generation (room + corridor)
	ServerScriptService/DungeonGenerator

	Generates a sequence of rooms connected by corridors.
	Uses the GDD rules:
	- First room is always combat (warmup)
	- Last room before boss is always rest
	- At least 1 shop and 1 treasure per floor
	- Branch choices every 2-3 rooms (2 options)
	- 10% chance of secret room per floor
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GameConfig = require(ReplicatedStorage.Shared.GameConfig)
local EnemyDatabase = require(ReplicatedStorage.Shared.EnemyDatabase)

local DungeonGenerator = {}

--------------------------------------------------
-- Room sequence generation
--------------------------------------------------

local function weightedRandom(weights: { [string]: number }): string
	local total = 0
	for _, w in weights do
		total += w
	end
	local roll = math.random() * total
	local cumulative = 0
	for key, w in weights do
		cumulative += w
		if roll <= cumulative then
			return key
		end
	end
	-- fallback
	for key in weights do
		return key
	end
	return "combat"
end

-- Generate the room sequence for a floor
function DungeonGenerator.generateFloorSequence(floorIndex: number, seed: number?): { any }
	if seed then
		math.randomseed(seed)
	end

	local floorDef = GameConfig.FLOORS[floorIndex]
	if not floorDef then
		warn("[DungeonGenerator] Invalid floor index:", floorIndex)
		return {}
	end

	local roomCount = math.random(floorDef.minRooms, floorDef.maxRooms)
	local rooms = {}

	-- Track guarantees
	local hasShop = false
	local hasTreasure = false
	local hasElite = false

	-- Room 1: always combat (warmup)
	table.insert(rooms, {
		index = 1,
		type = GameConfig.ROOM_TYPES.COMBAT,
		difficulty = "easy",
		theme = floorDef.theme,
	})

	-- Middle rooms (2 to roomCount-1)
	for i = 2, roomCount - 1 do
		local roomType

		-- Force guarantees in last 3 slots if not placed yet
		local remaining = roomCount - 1 - i
		if not hasShop and remaining <= 2 then
			roomType = GameConfig.ROOM_TYPES.SHOP
		elseif not hasTreasure and remaining <= 1 then
			roomType = GameConfig.ROOM_TYPES.TREASURE
		elseif not hasElite and remaining <= 0 then
			roomType = GameConfig.ROOM_TYPES.ELITE
		else
			roomType = weightedRandom(GameConfig.ROOM_WEIGHTS)
		end

		-- Track
		if roomType == GameConfig.ROOM_TYPES.SHOP then hasShop = true end
		if roomType == GameConfig.ROOM_TYPES.TREASURE then hasTreasure = true end
		if roomType == GameConfig.ROOM_TYPES.ELITE then hasElite = true end

		-- Determine difficulty for combat rooms
		local difficulty = "easy"
		local progress = i / roomCount
		if progress > 0.7 then
			difficulty = "hard"
		elseif progress > 0.4 then
			difficulty = "medium"
		end

		table.insert(rooms, {
			index = i,
			type = roomType,
			difficulty = difficulty,
			theme = floorDef.theme,
		})
	end

	-- Second-to-last room: rest (pre-boss preparation)
	table.insert(rooms, {
		index = roomCount,
		type = GameConfig.ROOM_TYPES.REST,
		difficulty = "none",
		theme = floorDef.theme,
	})

	-- Boss room
	table.insert(rooms, {
		index = roomCount + 1,
		type = GameConfig.ROOM_TYPES.BOSS,
		difficulty = "boss",
		theme = floorDef.theme,
		bossId = floorDef.bossId,
	})

	-- Secret room chance (10%)
	if math.random() < 0.1 then
		-- Attach secret to a random combat/treasure room
		for _, room in rooms do
			if room.type == GameConfig.ROOM_TYPES.COMBAT or room.type == GameConfig.ROOM_TYPES.TREASURE then
				room.hasSecret = true
				break
			end
		end
	end

	-- Add branch points every 2-3 rooms
	local nextBranch = math.random(2, 3)
	for i, room in rooms do
		if i == nextBranch and room.type ~= GameConfig.ROOM_TYPES.BOSS and room.type ~= GameConfig.ROOM_TYPES.REST then
			room.isBranch = true
			-- Generate alternative room type
			room.branchAlternative = weightedRandom(GameConfig.ROOM_WEIGHTS)
			nextBranch = i + math.random(2, 3)
		end
	end

	return rooms
end

--------------------------------------------------
-- Physical room building (3D geometry)
--------------------------------------------------

-- Create a single room as a Model with floor, walls, and optional props
function DungeonGenerator.buildRoom(roomData: any, position: Vector3): Model
	local model = Instance.new("Model")
	model.Name = "Room_" .. roomData.index .. "_" .. roomData.type

	local roomSize = GameConfig.ROOM_SIZE
	local wallH = GameConfig.WALL_HEIGHT
	local wallT = GameConfig.WALL_THICKNESS

	-- Floor
	local floor = Instance.new("Part")
	floor.Name = "Floor"
	floor.Anchored = true
	floor.Size = Vector3.new(roomSize.x, 1, roomSize.z)
	floor.Position = position
	floor.Material = Enum.Material.SmoothPlastic
	floor.BrickColor = DungeonGenerator._getFloorColor(roomData.type)
	floor.Parent = model

	-- Walls (4 sides)
	local wallDefs = {
		{ offset = Vector3.new(0, wallH / 2, roomSize.z / 2),  size = Vector3.new(roomSize.x, wallH, wallT) },  -- North
		{ offset = Vector3.new(0, wallH / 2, -roomSize.z / 2), size = Vector3.new(roomSize.x, wallH, wallT) },  -- South
		{ offset = Vector3.new(roomSize.x / 2, wallH / 2, 0),  size = Vector3.new(wallT, wallH, roomSize.z) },  -- East
		{ offset = Vector3.new(-roomSize.x / 2, wallH / 2, 0), size = Vector3.new(wallT, wallH, roomSize.z) },  -- West
	}

	for i, def in wallDefs do
		local wall = Instance.new("Part")
		wall.Name = "Wall_" .. i
		wall.Anchored = true
		wall.Size = def.size
		wall.Position = position + def.offset
		wall.Material = Enum.Material.Cobblestone
		wall.BrickColor = BrickColor.new("Dark stone grey")
		wall.Parent = model
	end

	-- Door openings (cut holes in north and south walls for corridors)
	-- Done by making the door section transparent/removing collision
	local doorWidth = GameConfig.CORRIDOR_WIDTH
	for _, wallPart in model:GetChildren() do
		if wallPart.Name == "Wall_1" or wallPart.Name == "Wall_2" then
			-- Add a door part (transparent gap)
			local door = Instance.new("Part")
			door.Name = "Door"
			door.Anchored = true
			door.Size = Vector3.new(doorWidth, wallH * 0.8, wallT + 0.1)
			door.Position = wallPart.Position - Vector3.new(0, wallH * 0.1, 0)
			door.Transparency = 1
			door.CanCollide = false
			door.Parent = model
		end
	end

	-- Room type decorations (basic colored indicators for MVP)
	DungeonGenerator._addRoomIndicator(model, roomData, position)

	model.Parent = workspace
	return model
end

-- Build corridor connecting two rooms
function DungeonGenerator.buildCorridor(fromPos: Vector3, toPos: Vector3): Model
	local model = Instance.new("Model")
	model.Name = "Corridor"

	local midPoint = (fromPos + toPos) / 2
	local direction = (toPos - fromPos).Unit
	local length = (toPos - fromPos).Magnitude
	local corridorW = GameConfig.CORRIDOR_WIDTH
	local wallH = GameConfig.WALL_HEIGHT

	-- Floor
	local floor = Instance.new("Part")
	floor.Name = "CorridorFloor"
	floor.Anchored = true
	floor.Size = Vector3.new(corridorW, 1, length)
	floor.CFrame = CFrame.lookAt(midPoint, toPos) * CFrame.new(0, 0, 0)
	floor.Material = Enum.Material.SmoothPlastic
	floor.BrickColor = BrickColor.new("Medium stone grey")
	floor.Parent = model

	-- Side walls
	for _, side in { -1, 1 } do
		local wall = Instance.new("Part")
		wall.Name = "CorridorWall"
		wall.Anchored = true
		wall.Size = Vector3.new(GameConfig.WALL_THICKNESS, wallH, length)
		wall.CFrame = floor.CFrame * CFrame.new(side * (corridorW / 2 + 1), wallH / 2, 0)
		wall.Material = Enum.Material.Cobblestone
		wall.BrickColor = BrickColor.new("Dark stone grey")
		wall.Parent = model
	end

	model.Parent = workspace
	return model
end

-- Build entire dungeon floor from room sequence
function DungeonGenerator.buildFloor(roomSequence: { any }): { Model }
	local models = {}
	local spacing = GameConfig.ROOM_SIZE.z + GameConfig.CORRIDOR_LENGTH
	local startPos = Vector3.new(0, 0, 0)

	for i, roomData in roomSequence do
		local pos = startPos + Vector3.new(0, 0, -(i - 1) * spacing)
		local roomModel = DungeonGenerator.buildRoom(roomData, pos)
		roomModel:SetAttribute("RoomIndex", roomData.index)
		roomModel:SetAttribute("RoomType", roomData.type)
		table.insert(models, roomModel)

		-- Build corridor to next room (except after last room)
		if i < #roomSequence then
			local nextPos = startPos + Vector3.new(0, 0, -i * spacing)
			local corridorFrom = pos + Vector3.new(0, 0, -GameConfig.ROOM_SIZE.z / 2)
			local corridorTo = nextPos + Vector3.new(0, 0, GameConfig.ROOM_SIZE.z / 2)
			local corridor = DungeonGenerator.buildCorridor(corridorFrom, corridorTo)
			table.insert(models, corridor)
		end
	end

	return models
end

-- Clean up all dungeon geometry
function DungeonGenerator.clearDungeon()
	for _, child in workspace:GetChildren() do
		if child:IsA("Model") and (child.Name:find("Room_") or child.Name == "Corridor") then
			child:Destroy()
		end
	end
end

--------------------------------------------------
-- Enemy spawning for a room
--------------------------------------------------

function DungeonGenerator.getEnemySpawns(roomData: any): { any }
	if roomData.type ~= GameConfig.ROOM_TYPES.COMBAT and roomData.type ~= GameConfig.ROOM_TYPES.ELITE then
		return {}
	end

	local spawnTable = EnemyDatabase.SpawnTables[roomData.theme]
	if not spawnTable then return {} end

	local tier = spawnTable[roomData.difficulty] or spawnTable.easy
	local count = math.random(tier.minCount, tier.maxCount)

	-- Elite rooms get +2 enemies and harder pool
	if roomData.type == GameConfig.ROOM_TYPES.ELITE then
		count += 2
		tier = spawnTable.hard or tier
	end

	local spawns = {}
	for _ = 1, count do
		local enemyId = tier.pool[math.random(#tier.pool)]
		table.insert(spawns, enemyId)
	end

	return spawns
end

--------------------------------------------------
-- Internal helpers
--------------------------------------------------

function DungeonGenerator._getFloorColor(roomType: string): BrickColor
	local colors = {
		combat    = BrickColor.new("Medium stone grey"),
		elite     = BrickColor.new("Dark red"),
		treasure  = BrickColor.new("Gold"),
		shop      = BrickColor.new("Bright green"),
		rest      = BrickColor.new("Pastel Blue"),
		boss      = BrickColor.new("Really red"),
		challenge = BrickColor.new("Bright orange"),
		secret    = BrickColor.new("Magenta"),
	}
	return colors[roomType] or BrickColor.new("Medium stone grey")
end

function DungeonGenerator._addRoomIndicator(model: Model, roomData: any, position: Vector3)
	local indicator = Instance.new("Part")
	indicator.Name = "RoomIndicator"
	indicator.Anchored = true
	indicator.Size = Vector3.new(4, 0.5, 4)
	indicator.Position = position + Vector3.new(0, 0.8, 0)
	indicator.Shape = Enum.PartType.Cylinder
	indicator.Material = Enum.Material.Neon
	indicator.BrickColor = DungeonGenerator._getFloorColor(roomData.type)
	indicator.Parent = model

	-- Billboard label
	local billboard = Instance.new("BillboardGui")
	billboard.Size = UDim2.new(0, 200, 0, 50)
	billboard.StudsOffset = Vector3.new(0, 10, 0)
	billboard.Adornee = indicator
	billboard.Parent = indicator

	local label = Instance.new("TextLabel")
	label.Size = UDim2.new(1, 0, 1, 0)
	label.BackgroundTransparency = 1
	label.Text = roomData.type:upper()
	label.TextColor3 = Color3.new(1, 1, 1)
	label.TextScaled = true
	label.Font = Enum.Font.GothamBold
	label.Parent = billboard
end

return DungeonGenerator
