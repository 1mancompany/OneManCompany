-- GameConfig: Shared configuration for Sling Royale (愤怒的小鸟 Roblox Edition)
-- Used by both server and client scripts

local GameConfig = {}

-- Physics
GameConfig.GRAVITY = 196.2 -- Roblox default workspace gravity
GameConfig.MAX_PULL_DISTANCE = 25
GameConfig.LAUNCH_MULTIPLIER = 4.5
GameConfig.PROJECTILE_LIFETIME = 8 -- seconds

-- Bird (projectile) settings
GameConfig.BIRD_SIZE = Vector3.new(3, 3, 3)
GameConfig.BIRD_DENSITY = 2.0
GameConfig.BIRD_FRICTION = 0.3
GameConfig.BIRD_ELASTICITY = 0.4
GameConfig.BIRD_COLOR = Color3.fromRGB(220, 50, 50) -- Red bird
GameConfig.BIRDS_PER_LEVEL = 5

-- Block settings
GameConfig.BLOCK_DENSITY = 0.8
GameConfig.BLOCK_FRICTION = 0.5
GameConfig.BLOCK_ELASTICITY = 0.2

-- Block types with durability
GameConfig.BLOCK_TYPES = {
	WOOD = { durability = 2, color = Color3.fromRGB(139, 90, 43), material = Enum.Material.Wood },
	STONE = { durability = 4, color = Color3.fromRGB(130, 130, 130), material = Enum.Material.Slate },
	ICE = { durability = 1, color = Color3.fromRGB(180, 220, 255), material = Enum.Material.Ice, transparency = 0.3 },
}

-- Pig settings
GameConfig.PIG_SIZE = Vector3.new(3.5, 3.5, 3.5)
GameConfig.PIG_COLOR = Color3.fromRGB(100, 200, 80)
GameConfig.PIG_HEALTH = 2
GameConfig.PIG_MATERIAL = Enum.Material.SmoothPlastic

-- Scoring
GameConfig.SCORE_PIG_DESTROY = 500
GameConfig.SCORE_BLOCK_DESTROY = 50
GameConfig.SCORE_BLOCK_DAMAGE = 10
GameConfig.SCORE_BIRD_UNUSED = 200 -- bonus for each unused bird

-- Map dimensions
GameConfig.MAP_WIDTH = 200
GameConfig.MAP_DEPTH = 60
GameConfig.GROUND_THICKNESS = 4
GameConfig.SLINGSHOT_X = -60 -- X position of slingshot
GameConfig.STRUCTURE_X_START = 20 -- where structures begin

-- UI
GameConfig.LEVEL_TRANSITION_TIME = 2
GameConfig.CAMERA_OFFSET = CFrame.new(0, 40, 60) -- overhead/behind camera

-- Slingshot visual
GameConfig.SLINGSHOT_HEIGHT = 12
GameConfig.SLINGSHOT_COLOR = Color3.fromRGB(101, 67, 33)

return GameConfig
