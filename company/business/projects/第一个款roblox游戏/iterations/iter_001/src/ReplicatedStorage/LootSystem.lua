-- LootSystem: item generation with weighted rarity rolls
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GameConfig = require(ReplicatedStorage:WaitForChild("GameConfig"))

local LootSystem = {}

local rng = Random.new()

-- Roll a rarity based on weighted probabilities
-- luckBonus: shifts weight toward rarer items (0 = normal)
function LootSystem.rollRarity(luckBonus)
	local rarities = GameConfig.RARITY
	local totalWeight = 0
	local adjustedWeights = {}

	for i, r in ipairs(rarities) do
		local w = r.weight
		-- Luck bonus: reduce common weight, increase rare weight
		if i == 1 then
			w = math.max(10, w - luckBonus * 5)
		elseif i >= 3 then
			w = w + luckBonus
		end
		adjustedWeights[i] = w
		totalWeight = totalWeight + w
	end

	local roll = rng:NextNumber(0, totalWeight)
	local cumulative = 0
	for i, w in ipairs(adjustedWeights) do
		cumulative = cumulative + w
		if roll <= cumulative then
			return rarities[i]
		end
	end
	return rarities[1] -- fallback to Common
end

-- Generate a random item for a given floor level
function LootSystem.generateItem(floorNum, luckBonus)
	local rarity = LootSystem.rollRarity(luckBonus or 0)
	local itemTypes = GameConfig.ITEM_TYPES

	-- Pick random item type
	local base = itemTypes[rng:NextInteger(1, #itemTypes)]

	local item = {
		name = rarity.name .. " " .. base.name,
		slot = base.slot,
		rarity = rarity.name,
		rarityColor = rarity.color,
		level = floorNum,
	}

	-- Calculate stats based on rarity + floor scaling
	local floorScale = 1 + (floorNum - 1) * 0.1
	local mult = rarity.multiplier * floorScale

	if base.baseAtk then
		item.atk = math.floor(base.baseAtk * mult)
	end
	if base.baseDef then
		item.def = math.floor(base.baseDef * mult)
	end
	if base.baseHp then
		item.hp = math.floor(base.baseHp * mult)
	end
	if base.baseSpeed then
		item.speed = math.floor(base.baseSpeed * mult)
	end
	if base.classPref then
		item.classPref = base.classPref
	end

	return item
end

-- Generate a potion drop
function LootSystem.generatePotion()
	local potions = GameConfig.POTIONS
	local potion = potions[rng:NextInteger(1, #potions)]
	return {
		name = potion.name,
		slot = "potion",
		effect = potion.effect,
		value = potion.value,
		duration = potion.duration,
		rarity = "Common",
		rarityColor = Color3.fromRGB(200, 200, 200),
	}
end

-- Determine drops from an enemy kill
-- Returns: list of items (can be empty)
function LootSystem.rollDrops(enemyData, floorNum, luckBonus)
	local drops = {}

	-- 40% chance for item drop
	if rng:NextNumber() < 0.4 then
		table.insert(drops, LootSystem.generateItem(floorNum, luckBonus or 0))
	end

	-- 30% chance for potion
	if rng:NextNumber() < 0.3 then
		table.insert(drops, LootSystem.generatePotion())
	end

	-- Soul gem based on enemy soulGemChance
	if enemyData.soulGemChance and rng:NextNumber() < enemyData.soulGemChance then
		table.insert(drops, {
			name = "Soul Gem",
			slot = "soul_gem",
			value = 1,
			rarity = "Epic",
			rarityColor = Color3.fromRGB(160, 50, 255),
		})
	end

	return drops
end

-- Boss drops (guaranteed)
function LootSystem.rollBossDrops(bossData, floorNum, luckBonus)
	local drops = {}

	-- Guaranteed equipment drop (high rarity boost)
	table.insert(drops, LootSystem.generateItem(floorNum, (luckBonus or 0) + 5))

	-- Guaranteed soul gems
	table.insert(drops, {
		name = "Soul Gem Bundle",
		slot = "soul_gem",
		value = bossData.soulGemReward or 5,
		rarity = "Legendary",
		rarityColor = Color3.fromRGB(255, 200, 0),
	})

	-- 50% chance for second item
	if rng:NextNumber() < 0.5 then
		table.insert(drops, LootSystem.generateItem(floorNum, (luckBonus or 0) + 3))
	end

	-- Guaranteed health potion
	table.insert(drops, LootSystem.generatePotion())

	return drops
end

return LootSystem
