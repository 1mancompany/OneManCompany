-- GameServer: Main server-side game logic
-- Handles bird launching, damage, scoring, and level management

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")
local Workspace = game:GetService("Workspace")

local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))
local LevelData = require(ReplicatedStorage:WaitForChild("LevelData"))

-- RemoteEvents
local LaunchBirdEvent = ReplicatedStorage:WaitForChild("LaunchBird")
local UpdateUIEvent = ReplicatedStorage:WaitForChild("UpdateUI")
local LevelCompleteEvent = ReplicatedStorage:WaitForChild("LevelComplete")
local ResetLevelEvent = ReplicatedStorage:WaitForChild("ResetLevel")
local NextLevelEvent = ReplicatedStorage:WaitForChild("NextLevel")

-- Game state (per-server, single player mode for now)
local currentLevel = 1
local score = 0
local birdsRemaining = 0
local pigsAlive = 0
local activeBlocks = {} -- maps part -> {durability, maxDurability, type}
local activePigs = {} -- maps part -> {health}
local activeBirds = {} -- cleanup tracking
local levelFolder = nil -- Workspace folder for current level

---------------------------------------------
-- LEVEL BUILDING
---------------------------------------------

local function clearLevel()
	if levelFolder then
		levelFolder:Destroy()
	end
	levelFolder = Instance.new("Folder")
	levelFolder.Name = "LevelContent"
	levelFolder.Parent = Workspace

	activeBlocks = {}
	activePigs = {}

	-- Clean up stray birds
	for bird, _ in pairs(activeBirds) do
		if bird and bird.Parent then
			bird:Destroy()
		end
	end
	activeBirds = {}
end

local function createBlock(blockDef, basePosition)
	local blockType = GameConfig.BLOCK_TYPES[blockDef.type] or GameConfig.BLOCK_TYPES.WOOD

	local block = Instance.new("Part")
	block.Name = "Block_" .. blockDef.type
	block.Size = Vector3.new(blockDef.w, blockDef.h, blockDef.d)
	block.Position = basePosition + Vector3.new(blockDef.x, blockDef.y + blockDef.h / 2, blockDef.z)
	block.Anchored = false
	block.Color = blockType.color
	block.Material = blockType.material
	block.Transparency = blockType.transparency or 0

	block.CustomPhysicalProperties = PhysicalProperties.new(
		GameConfig.BLOCK_DENSITY,
		GameConfig.BLOCK_FRICTION,
		GameConfig.BLOCK_ELASTICITY,
		1, 1
	)

	block.Parent = levelFolder

	activeBlocks[block] = {
		durability = blockType.durability,
		maxDurability = blockType.durability,
		type = blockDef.type,
	}

	return block
end

local function createPig(pigDef, basePosition)
	local pig = Instance.new("Part")
	pig.Name = "Pig"
	pig.Shape = Enum.PartType.Ball
	pig.Size = GameConfig.PIG_SIZE
	pig.Position = basePosition + Vector3.new(pigDef.x, pigDef.y + GameConfig.PIG_SIZE.Y / 2, pigDef.z)
	pig.Anchored = false
	pig.Color = GameConfig.PIG_COLOR
	pig.Material = GameConfig.PIG_MATERIAL

	pig.CustomPhysicalProperties = PhysicalProperties.new(
		0.5, 0.4, 0.3, 1, 1
	)

	-- Add a face decal (front)
	local face = Instance.new("Decal")
	face.Name = "PigFace"
	face.Face = Enum.NormalId.Front
	-- Using a simple texture; in production you'd upload a custom asset
	face.Color3 = Color3.fromRGB(50, 120, 40)
	face.Parent = pig

	-- Snout indicator (small part welded on)
	local snout = Instance.new("Part")
	snout.Name = "Snout"
	snout.Shape = Enum.PartType.Cylinder
	snout.Size = Vector3.new(1, 1.5, 1.5)
	snout.Color = Color3.fromRGB(60, 160, 50)
	snout.Material = Enum.Material.SmoothPlastic
	snout.CanCollide = false
	snout.Massless = true

	local weld = Instance.new("WeldConstraint")
	weld.Part0 = pig
	weld.Part1 = snout
	weld.Parent = snout

	snout.CFrame = pig.CFrame * CFrame.new(0, -0.3, -GameConfig.PIG_SIZE.Z / 2 - 0.3)
		* CFrame.Angles(0, 0, math.rad(90))
	snout.Parent = levelFolder

	pig.Parent = levelFolder

	activePigs[pig] = {
		health = GameConfig.PIG_HEALTH,
		snout = snout,
	}

	return pig
end

local function buildLevel(levelNum)
	clearLevel()

	local levelDef = LevelData.Levels[levelNum]
	if not levelDef then
		-- All levels complete; loop or show victory
		return false
	end

	local basePosition = Vector3.new(GameConfig.STRUCTURE_X_START, GameConfig.GROUND_THICKNESS / 2, 0)

	-- Build blocks
	for _, blockDef in ipairs(levelDef.blocks) do
		createBlock(blockDef, basePosition)
	end

	-- Build pigs
	for _, pigDef in ipairs(levelDef.pigs) do
		createPig(pigDef, basePosition)
	end

	birdsRemaining = levelDef.birds or GameConfig.BIRDS_PER_LEVEL
	pigsAlive = #levelDef.pigs

	return true
end

---------------------------------------------
-- BIRD LAUNCHING
---------------------------------------------

local function createBird(origin, direction, pullDistance)
	local bird = Instance.new("Part")
	bird.Name = "Bird"
	bird.Shape = Enum.PartType.Ball
	bird.Size = GameConfig.BIRD_SIZE
	bird.Position = origin
	bird.Color = GameConfig.BIRD_COLOR
	bird.Material = Enum.Material.SmoothPlastic
	bird.CanCollide = true

	bird.CustomPhysicalProperties = PhysicalProperties.new(
		GameConfig.BIRD_DENSITY,
		GameConfig.BIRD_FRICTION,
		GameConfig.BIRD_ELASTICITY,
		1, 1
	)

	bird.Parent = Workspace

	-- Apply launch velocity
	local launchSpeed = pullDistance * GameConfig.LAUNCH_MULTIPLIER
	local launchVelocity = direction.Unit * launchSpeed

	local attachment = Instance.new("Attachment")
	attachment.Parent = bird

	local linearVelocity = Instance.new("LinearVelocity")
	linearVelocity.Attachment0 = attachment
	linearVelocity.VectorVelocity = launchVelocity
	linearVelocity.MaxForce = math.huge
	linearVelocity.RelativeTo = Enum.ActuatorRelativeTo.World
	linearVelocity.Parent = bird

	-- Release velocity constraint after brief moment so physics takes over
	task.delay(0.15, function()
		if linearVelocity and linearVelocity.Parent then
			linearVelocity:Destroy()
		end
	end)

	-- Track for cleanup
	activeBirds[bird] = true

	-- Collision handler
	bird.Touched:Connect(function(hit)
		if not hit or not hit.Parent then return end
		local speed = bird.AssemblyLinearVelocity.Magnitude

		-- Damage blocks
		if activeBlocks[hit] then
			local blockData = activeBlocks[hit]
			local damage = 0
			if speed > 15 then
				damage = 2
			elseif speed > 5 then
				damage = 1
			end

			if damage > 0 then
				blockData.durability = blockData.durability - damage
				if blockData.durability <= 0 then
					-- Destroy block
					score = score + GameConfig.SCORE_BLOCK_DESTROY
					activeBlocks[hit] = nil
					hit:Destroy()
				else
					-- Damage visual: lighten color
					score = score + GameConfig.SCORE_BLOCK_DAMAGE
					local ratio = blockData.durability / blockData.maxDurability
					hit.Color = hit.Color:Lerp(Color3.new(1, 1, 1), 1 - ratio)
					hit.Transparency = math.min(0.5, hit.Transparency + 0.15)
				end
				broadcastUI()
			end
		end

		-- Damage pigs
		if activePigs[hit] then
			local pigData = activePigs[hit]
			if speed > 3 then
				pigData.health = pigData.health - 1
				if pigData.health <= 0 then
					score = score + GameConfig.SCORE_PIG_DESTROY
					pigsAlive = pigsAlive - 1
					-- Destroy pig and its snout
					if pigData.snout and pigData.snout.Parent then
						pigData.snout:Destroy()
					end
					activePigs[hit] = nil
					hit:Destroy()

					-- Create burst particles
					createExplosionEffect(hit.Position)

					broadcastUI()
					checkLevelComplete()
				else
					-- Pig takes damage - change color
					hit.Color = Color3.fromRGB(180, 220, 80)
					broadcastUI()
				end
			end
		end
	end)

	-- Auto-cleanup after lifetime
	task.delay(GameConfig.PROJECTILE_LIFETIME, function()
		if bird and bird.Parent then
			activeBirds[bird] = nil
			bird:Destroy()
		end
	end)

	return bird
end

local function createExplosionEffect(position)
	-- Simple particle burst using small parts
	for i = 1, 8 do
		local particle = Instance.new("Part")
		particle.Name = "Particle"
		particle.Size = Vector3.new(0.5, 0.5, 0.5)
		particle.Shape = Enum.PartType.Ball
		particle.Color = Color3.fromRGB(
			math.random(150, 255),
			math.random(200, 255),
			math.random(50, 100)
		)
		particle.Material = Enum.Material.Neon
		particle.Position = position
		particle.CanCollide = false
		particle.Anchored = false
		particle.Parent = Workspace

		-- Random velocity outward
		local angle = math.rad(i * 45)
		local vel = Vector3.new(math.cos(angle) * 15, 15, math.sin(angle) * 15)
		particle.AssemblyLinearVelocity = vel

		task.delay(1.5, function()
			if particle and particle.Parent then
				particle:Destroy()
			end
		end)
	end
end

---------------------------------------------
-- GAME FLOW
---------------------------------------------

function broadcastUI()
	for _, player in ipairs(Players:GetPlayers()) do
		UpdateUIEvent:FireClient(player, {
			score = score,
			level = currentLevel,
			birdsRemaining = birdsRemaining,
			pigsAlive = pigsAlive,
			levelName = LevelData.Levels[currentLevel] and LevelData.Levels[currentLevel].name or "",
		})
	end
end

function checkLevelComplete()
	if pigsAlive <= 0 then
		-- Award bonus for unused birds
		local bonus = birdsRemaining * GameConfig.SCORE_BIRD_UNUSED
		score = score + bonus

		task.delay(1.5, function()
			for _, player in ipairs(Players:GetPlayers()) do
				LevelCompleteEvent:FireClient(player, {
					score = score,
					level = currentLevel,
					bonus = bonus,
					totalLevels = #LevelData.Levels,
				})
			end
		end)
	end
end

local function checkGameOver()
	if birdsRemaining <= 0 and pigsAlive > 0 then
		-- Wait a moment for physics to settle
		task.delay(3, function()
			-- Re-check: pigs might have been destroyed by falling blocks
			if pigsAlive > 0 then
				for _, player in ipairs(Players:GetPlayers()) do
					UpdateUIEvent:FireClient(player, {
						score = score,
						level = currentLevel,
						birdsRemaining = 0,
						pigsAlive = pigsAlive,
						gameOver = true,
					})
				end
			end
		end)
	end
end

local function startLevel(levelNum)
	currentLevel = levelNum
	local success = buildLevel(levelNum)
	if not success then
		-- All levels beaten - show victory
		for _, player in ipairs(Players:GetPlayers()) do
			LevelCompleteEvent:FireClient(player, {
				score = score,
				level = currentLevel - 1,
				victory = true,
				totalLevels = #LevelData.Levels,
			})
		end
		return
	end
	broadcastUI()
end

---------------------------------------------
-- EVENT HANDLERS
---------------------------------------------

LaunchBirdEvent.OnServerEvent:Connect(function(player, origin, direction, pullDistance)
	-- Validate
	if typeof(origin) ~= "Vector3" or typeof(direction) ~= "Vector3" then
		return
	end
	if birdsRemaining <= 0 then return end

	pullDistance = math.clamp(pullDistance, 1, GameConfig.MAX_PULL_DISTANCE)

	birdsRemaining = birdsRemaining - 1
	createBird(origin, direction, pullDistance)
	broadcastUI()

	-- Check if out of birds after launch settles
	if birdsRemaining <= 0 then
		checkGameOver()
	end
end)

ResetLevelEvent.OnServerEvent:Connect(function(player)
	score = math.max(0, score - 100) -- small penalty for reset
	startLevel(currentLevel)
end)

NextLevelEvent.OnServerEvent:Connect(function(player)
	startLevel(currentLevel + 1)
end)

---------------------------------------------
-- INITIALIZATION
---------------------------------------------

-- Build the ground/baseplate
local function setupWorld()
	-- Ground
	local ground = Instance.new("Part")
	ground.Name = "Ground"
	ground.Size = Vector3.new(GameConfig.MAP_WIDTH, GameConfig.GROUND_THICKNESS, GameConfig.MAP_DEPTH)
	ground.Position = Vector3.new(0, -GameConfig.GROUND_THICKNESS / 2, 0)
	ground.Anchored = true
	ground.Color = Color3.fromRGB(80, 160, 60)
	ground.Material = Enum.Material.Grass
	ground.Parent = Workspace

	-- Slingshot base (visual)
	local slingshotBase = Instance.new("Part")
	slingshotBase.Name = "SlingshotBase"
	slingshotBase.Size = Vector3.new(2, GameConfig.SLINGSHOT_HEIGHT, 2)
	slingshotBase.Position = Vector3.new(
		GameConfig.SLINGSHOT_X, GameConfig.SLINGSHOT_HEIGHT / 2, 0
	)
	slingshotBase.Anchored = true
	slingshotBase.Color = GameConfig.SLINGSHOT_COLOR
	slingshotBase.Material = Enum.Material.Wood
	slingshotBase.Parent = Workspace

	-- Slingshot fork left
	local forkL = Instance.new("Part")
	forkL.Name = "SlingshotForkL"
	forkL.Size = Vector3.new(1, 4, 1)
	forkL.Position = Vector3.new(GameConfig.SLINGSHOT_X - 1.5, GameConfig.SLINGSHOT_HEIGHT + 1, 0)
	forkL.Anchored = true
	forkL.Color = GameConfig.SLINGSHOT_COLOR
	forkL.Material = Enum.Material.Wood
	forkL.Rotation = Vector3.new(0, 0, -15)
	forkL.Parent = Workspace

	-- Slingshot fork right
	local forkR = Instance.new("Part")
	forkR.Name = "SlingshotForkR"
	forkR.Size = Vector3.new(1, 4, 1)
	forkR.Position = Vector3.new(GameConfig.SLINGSHOT_X + 1.5, GameConfig.SLINGSHOT_HEIGHT + 1, 0)
	forkR.Anchored = true
	forkR.Color = GameConfig.SLINGSHOT_COLOR
	forkR.Material = Enum.Material.Wood
	forkR.Rotation = Vector3.new(0, 0, 15)
	forkR.Parent = Workspace

	-- Spawn location
	local spawn = Instance.new("SpawnLocation")
	spawn.Name = "SpawnLocation"
	spawn.Size = Vector3.new(8, 1, 8)
	spawn.Position = Vector3.new(GameConfig.SLINGSHOT_X - 10, 1, 0)
	spawn.Anchored = true
	spawn.CanCollide = true
	spawn.Color = Color3.fromRGB(100, 100, 100)
	spawn.Material = Enum.Material.SmoothPlastic
	spawn.Transparency = 1
	spawn.Parent = Workspace

	-- Background wall (sky-colored, far back)
	local sky = Instance.new("Part")
	sky.Name = "SkyWall"
	sky.Size = Vector3.new(GameConfig.MAP_WIDTH * 2, 200, 2)
	sky.Position = Vector3.new(0, 80, -GameConfig.MAP_DEPTH)
	sky.Anchored = true
	sky.Color = Color3.fromRGB(135, 206, 235)
	sky.Material = Enum.Material.SmoothPlastic
	sky.CanCollide = false
	sky.Parent = Workspace
end

-- Player join handler
Players.PlayerAdded:Connect(function(player)
	-- Small delay to let character load
	task.delay(1, function()
		broadcastUI()
	end)
end)

-- Setup
setupWorld()
startLevel(1)

print("[SlingRoyale Server] Game initialized with " .. #LevelData.Levels .. " levels")
