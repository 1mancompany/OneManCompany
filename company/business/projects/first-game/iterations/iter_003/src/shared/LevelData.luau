-- LevelData: Level definitions for Sling Royale
-- Each level defines block positions and pig positions
-- Coordinates are relative to STRUCTURE_X_START

local LevelData = {}

-- Block definition: {x, y, z, width, height, depth, type}
-- Pig definition: {x, y, z}

LevelData.Levels = {
	-- Level 1: Simple tower (tutorial)
	{
		name = "初级训练",
		birds = 3,
		blocks = {
			-- Two pillars with a beam on top
			{x = 0, y = 2, z = 0, w = 2, h = 8, d = 4, type = "WOOD"},
			{x = 12, y = 2, z = 0, w = 2, h = 8, d = 4, type = "WOOD"},
			{x = 6, y = 7, z = 0, w = 16, h = 2, d = 4, type = "WOOD"},
		},
		pigs = {
			{x = 6, y = 2, z = 0},
		},
	},

	-- Level 2: Double decker
	{
		name = "双层堡垒",
		birds = 4,
		blocks = {
			-- Ground floor
			{x = 0, y = 2, z = 0, w = 2, h = 8, d = 4, type = "WOOD"},
			{x = 14, y = 2, z = 0, w = 2, h = 8, d = 4, type = "WOOD"},
			{x = 7, y = 7, z = 0, w = 18, h = 2, d = 4, type = "WOOD"},
			-- Second floor pillars
			{x = 3, y = 10, z = 0, w = 2, h = 6, d = 4, type = "WOOD"},
			{x = 11, y = 10, z = 0, w = 2, h = 6, d = 4, type = "WOOD"},
			{x = 7, y = 14, z = 0, w = 12, h = 2, d = 4, type = "STONE"},
		},
		pigs = {
			{x = 7, y = 2, z = 0},
			{x = 7, y = 16, z = 0},
		},
	},

	-- Level 3: Fortress with ice
	{
		name = "冰雪要塞",
		birds = 4,
		blocks = {
			-- Left wall
			{x = -2, y = 2, z = 0, w = 2, h = 10, d = 4, type = "STONE"},
			-- Right wall
			{x = 16, y = 2, z = 0, w = 2, h = 10, d = 4, type = "STONE"},
			-- Ice roof
			{x = 7, y = 8, z = 0, w = 22, h = 2, d = 4, type = "ICE"},
			-- Inner supports (wood - weak point)
			{x = 4, y = 2, z = 0, w = 2, h = 6, d = 4, type = "WOOD"},
			{x = 10, y = 2, z = 0, w = 2, h = 6, d = 4, type = "WOOD"},
			-- Upper tower
			{x = 5, y = 10, z = 0, w = 2, h = 6, d = 4, type = "ICE"},
			{x = 9, y = 10, z = 0, w = 2, h = 6, d = 4, type = "ICE"},
			{x = 7, y = 14, z = 0, w = 8, h = 2, d = 4, type = "WOOD"},
		},
		pigs = {
			{x = 7, y = 2, z = 0},
			{x = 7, y = 16, z = 0},
			{x = 0, y = 2, z = 0},
		},
	},

	-- Level 4: Pyramid
	{
		name = "金字塔之谜",
		birds = 5,
		blocks = {
			-- Bottom row (5 blocks)
			{x = 0, y = 1.5, z = 0, w = 4, h = 3, d = 4, type = "STONE"},
			{x = 5, y = 1.5, z = 0, w = 4, h = 3, d = 4, type = "STONE"},
			{x = 10, y = 1.5, z = 0, w = 4, h = 3, d = 4, type = "STONE"},
			{x = 15, y = 1.5, z = 0, w = 4, h = 3, d = 4, type = "STONE"},
			{x = 20, y = 1.5, z = 0, w = 4, h = 3, d = 4, type = "STONE"},
			-- Second row (3 blocks)
			{x = 5, y = 5, z = 0, w = 4, h = 3, d = 4, type = "WOOD"},
			{x = 10, y = 5, z = 0, w = 4, h = 3, d = 4, type = "WOOD"},
			{x = 15, y = 5, z = 0, w = 4, h = 3, d = 4, type = "WOOD"},
			-- Top row (1 block)
			{x = 10, y = 8.5, z = 0, w = 4, h = 3, d = 4, type = "ICE"},
		},
		pigs = {
			{x = 2.5, y = 4, z = 0},
			{x = 12.5, y = 4, z = 0},
			{x = 10, y = 11, z = 0},
		},
	},

	-- Level 5: The Fortress
	{
		name = "终极堡垒",
		birds = 5,
		blocks = {
			-- Outer walls (stone)
			{x = -3, y = 4, z = -6, w = 2, h = 12, d = 4, type = "STONE"},
			{x = -3, y = 4, z = 6, w = 2, h = 12, d = 4, type = "STONE"},
			{x = 17, y = 4, z = -6, w = 2, h = 12, d = 4, type = "STONE"},
			{x = 17, y = 4, z = 6, w = 2, h = 12, d = 4, type = "STONE"},
			-- Front wall (ice - breakable)
			{x = -3, y = 4, z = 0, w = 2, h = 12, d = 8, type = "ICE"},
			-- Roof beams
			{x = 7, y = 11, z = -6, w = 24, h = 2, d = 4, type = "WOOD"},
			{x = 7, y = 11, z = 6, w = 24, h = 2, d = 4, type = "WOOD"},
			{x = 7, y = 11, z = 0, w = 24, h = 2, d = 8, type = "WOOD"},
			-- Inner platforms
			{x = 5, y = 5, z = 0, w = 8, h = 1, d = 8, type = "WOOD"},
			{x = 12, y = 5, z = 0, w = 8, h = 1, d = 8, type = "WOOD"},
		},
		pigs = {
			{x = 5, y = 2, z = 0},
			{x = 12, y = 2, z = 0},
			{x = 5, y = 7, z = 0},
			{x = 12, y = 7, z = 0},
		},
	},
}

return LevelData
