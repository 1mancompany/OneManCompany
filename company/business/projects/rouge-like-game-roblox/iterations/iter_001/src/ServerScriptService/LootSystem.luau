--[[
	LootSystem.luau — Loot generation and item management
	ServerScriptService/LootSystem

	Handles: random item generation, rarity rolling, affix rolling,
	talent card selection, shop inventory, and treasure room loot.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local GameConfig = require(ReplicatedStorage.Shared.GameConfig)
local ItemDatabase = require(ReplicatedStorage.Shared.ItemDatabase)
local TalentCardDatabase = require(ReplicatedStorage.Shared.TalentCardDatabase)

local LootSystem = {}

--------------------------------------------------
-- Rarity rolling
--------------------------------------------------

local function rollRarity(weights: { [string]: number }): string
	local total = 0
	for _, w in weights do
		total += w
	end
	local roll = math.random() * total
	local cumulative = 0
	for rarity, w in weights do
		cumulative += w
		if roll <= cumulative then
			return rarity
		end
	end
	return "common"
end

--------------------------------------------------
-- Equipment generation
--------------------------------------------------

function LootSystem.generateEquipment(slot: string, classId: string?, forcedRarity: string?): any
	local rarity = forcedRarity or rollRarity(GameConfig.RARITY_WEIGHTS)

	-- Pick a base item from the appropriate pool
	local pool
	if slot == "weapon" then
		pool = {}
		for _, item in ItemDatabase.Weapons do
			if not classId or not item.classRestriction or item.classRestriction == classId then
				table.insert(pool, item)
			end
		end
	elseif slot == "armor" then
		pool = ItemDatabase.Armors
	else
		return nil
	end

	if #pool == 0 then return nil end

	local baseItem = pool[math.random(#pool)]
	local generated = {
		id = baseItem.id .. "_" .. math.random(100000, 999999),
		baseId = baseItem.id,
		name = baseItem.name,
		slot = baseItem.slot,
		rarity = rarity,
		classRestriction = baseItem.classRestriction,
		stats = {},
		affixes = {},
		specialEffect = baseItem.specialEffect,
	}

	-- Apply base stats with rarity multiplier
	local mult = ItemDatabase.RARITY_STAT_MULT[rarity] or 1.0
	if baseItem.baseAttack then
		generated.stats.attack = math.floor(baseItem.baseAttack * mult)
	end
	if baseItem.baseDefense then
		generated.stats.defense = math.floor(baseItem.baseDefense * mult)
	end
	if baseItem.baseHealth then
		generated.stats.maxHealth = math.floor(baseItem.baseHealth * mult)
	end

	-- Roll affixes
	local affixCount = ItemDatabase.RARITY_AFFIXES[rarity] or 0
	local usedStats = {}
	for _ = 1, affixCount do
		local affix = LootSystem._rollAffix(usedStats)
		if affix then
			table.insert(generated.affixes, affix)
			usedStats[affix.stat] = true
		end
	end

	return generated
end

function LootSystem._rollAffix(usedStats: { [string]: boolean }): any?
	-- Filter available stats
	local available = {}
	local totalWeight = 0
	for _, entry in ItemDatabase.STAT_POOL do
		if not usedStats[entry.stat] then
			table.insert(available, entry)
			totalWeight += entry.weight
		end
	end
	if #available == 0 then return nil end

	-- Weighted pick
	local roll = math.random() * totalWeight
	local cumulative = 0
	for _, entry in available do
		cumulative += entry.weight
		if roll <= cumulative then
			local value = entry.min + math.random() * (entry.max - entry.min)
			-- Round based on type
			if value < 1 then
				value = math.floor(value * 100) / 100
			else
				value = math.floor(value)
			end
			return {
				stat = entry.stat,
				value = value,
			}
		end
	end
	return nil
end

--------------------------------------------------
-- Talent card selection
--------------------------------------------------

-- Get 3 random talent cards for player to choose from
function LootSystem.rollTalentCards(classId: string, ownedTalentIds: { string }): { any }
	local pool = TalentCardDatabase.getPoolForClass(classId)

	-- Filter out already-owned talents
	local ownedSet = {}
	for _, id in ownedTalentIds do
		ownedSet[id] = true
	end

	local available = {}
	for _, card in pool do
		if not ownedSet[card.id] then
			table.insert(available, card)
		end
	end

	if #available == 0 then return {} end

	-- Weighted selection by rarity
	local selected = {}
	for _ = 1, math.min(3, #available) do
		local rarity = rollRarity(GameConfig.TALENT_RARITY_WEIGHTS)

		-- Find cards of this rarity
		local rarityPool = {}
		for _, card in available do
			if card.rarity == rarity then
				table.insert(rarityPool, card)
			end
		end

		-- Fallback to any available card if no match
		if #rarityPool == 0 then
			rarityPool = available
		end

		-- Pick random from rarity pool
		local pick = rarityPool[math.random(#rarityPool)]

		-- Remove from available to avoid duplicates
		table.insert(selected, pick)
		for i, card in available do
			if card.id == pick.id then
				table.remove(available, i)
				break
			end
		end
	end

	return selected
end

--------------------------------------------------
-- Enemy loot drops
--------------------------------------------------

function LootSystem.generateEnemyDrops(enemyId: string, classId: string, difficultyMult: number?): any
	local mult = difficultyMult or 1.0
	local drops = {
		soulShards = 0,
		xp = 0,
		equipment = nil,
	}

	local EnemyDatabase = require(ReplicatedStorage.Shared.EnemyDatabase)
	local enemyDef = EnemyDatabase.Enemies[enemyId]
	if not enemyDef then return drops end

	drops.soulShards = math.floor(enemyDef.soulShards * mult)
	drops.xp = enemyDef.xpReward

	-- Equipment drop chance (10% for normal, 25% for elite)
	local dropChance = 0.10
	if math.random() < dropChance then
		local slot = math.random() < 0.5 and "weapon" or "armor"
		drops.equipment = LootSystem.generateEquipment(slot, classId)
	end

	return drops
end

-- Boss loot
function LootSystem.generateBossDrops(bossId: string, classId: string): any
	local EnemyDatabase = require(ReplicatedStorage.Shared.EnemyDatabase)
	local bossDef = EnemyDatabase.Bosses[bossId]
	if not bossDef then return { soulShards = 0, xp = 0, equipment = {} } end

	local drops = {
		soulShards = bossDef.soulShards,
		xp = bossDef.xpReward,
		equipment = {},
	}

	-- Guaranteed equipment drops from loot table
	for _, entry in bossDef.lootTable do
		if math.random() < entry.chance then
			local rarity = entry.itemPool:find("epic") and "epic" or "rare"
			local slot = math.random() < 0.5 and "weapon" or "armor"
			local item = LootSystem.generateEquipment(slot, classId, rarity)
			if item then
				table.insert(drops.equipment, item)
			end
		end
	end

	return drops
end

--------------------------------------------------
-- Treasure room loot
--------------------------------------------------

function LootSystem.generateTreasureRoomLoot(classId: string, floorIndex: number): any
	return {
		soulShards = math.floor(GameConfig.SOUL_SHARD_BASE_DROP * 3 * floorIndex),
		equipment = LootSystem.generateEquipment(
			math.random() < 0.5 and "weapon" or "armor",
			classId,
			"rare" -- treasure rooms guarantee at least rare
		),
	}
end

--------------------------------------------------
-- Shop inventory
--------------------------------------------------

function LootSystem.generateShopInventory(classId: string, floorIndex: number): { any }
	local inventory = {}

	-- Always include consumables
	for _, item in ItemDatabase.ShopItems do
		table.insert(inventory, {
			shopItem = item,
			cost = math.floor(item.cost * (1 + (floorIndex - 1) * 0.2)),
		})
	end

	-- Add 2 random equipment pieces
	for _ = 1, 2 do
		local slot = math.random() < 0.5 and "weapon" or "armor"
		local equip = LootSystem.generateEquipment(slot, classId)
		if equip then
			local baseCost = ({ common = 30, uncommon = 50, rare = 80, epic = 150 })[equip.rarity] or 30
			table.insert(inventory, {
				shopItem = equip,
				cost = math.floor(baseCost * (1 + (floorIndex - 1) * 0.3)),
				isEquipment = true,
			})
		end
	end

	return inventory
end

return LootSystem
