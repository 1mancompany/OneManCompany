-- GameConfig: shared constants for Tech Startup Tycoon
local GameConfig = {}

-- Currency
GameConfig.CODE_PER_CLICK = 1
GameConfig.CODE_TO_CASH_RATE = 10  -- 10 code = 1 cash

-- Upgrades: { name, cost, type, value, description }
GameConfig.UPGRADES = {
	{
		name = "Better Keyboard",
		cost = 10,
		type = "click_multiplier",
		value = 2,
		description = "+1 Code per click",
	},
	{
		name = "Gaming Monitor",
		cost = 50,
		type = "click_multiplier",
		value = 3,
		description = "+2 Code per click",
	},
	{
		name = "Intern",
		cost = 100,
		type = "auto_coder",
		value = 1,
		description = "Generates 1 Code/sec automatically",
	},
	{
		name = "Junior Dev",
		cost = 500,
		type = "auto_coder",
		value = 5,
		description = "Generates 5 Code/sec automatically",
	},
	{
		name = "Senior Dev",
		cost = 2000,
		type = "auto_coder",
		value = 20,
		description = "Generates 20 Code/sec automatically",
	},
	{
		name = "Server Rack",
		cost = 5000,
		type = "auto_coder",
		value = 50,
		description = "Generates 50 Code/sec automatically",
	},
	{
		name = "AI Assistant",
		cost = 20000,
		type = "auto_coder",
		value = 200,
		description = "Generates 200 Code/sec automatically",
	},
}

-- Rebirth
GameConfig.REBIRTH_COST = 100000  -- Cash needed to rebirth
GameConfig.REBIRTH_MULTIPLIER = 1.5  -- Each rebirth multiplies all earnings

-- DataStore
GameConfig.DATASTORE_NAME = "TechStartupTycoon_PlayerData"

-- Auto-save interval (seconds)
GameConfig.AUTO_SAVE_INTERVAL = 60

return GameConfig
