--[[
	EnemyAI.luau — Enemy AI behavior framework
	ServerScriptService/EnemyAI

	Behavior types:
	- chase_explode: Slowly chase player, explode on contact (Wandering Ghost)
	- ranged_kite: Keep distance, fire projectiles (Bone Archer)
	- charge_spin: Charge at player, spin attack (Shadow Knight)
	- shield_reflect: Shield up, reflect projectiles (Rune Guardian)
	- boss_melee_summon: Melee + summon minions (Lazarus P1)
	- boss_ranged_tracking: Tracking projectiles (Lazarus P2)
	- boss_dual_mode: Alternates melee/ranged (Lazarus P3)
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")

local EnemyDatabase = require(ReplicatedStorage.Shared.EnemyDatabase)

local EnemyAI = {}

-- Active AI instances: { [Model]: AIState }
EnemyAI._instances = {}

export type AIState = {
	enemyId: string,
	model: Model,
	behavior: string,
	target: Player?,
	attackTimer: number,
	specialTimer: number,
	state: string, -- "idle" | "chase" | "attack" | "retreat" | "charge" | "shield"
	data: any, -- behavior-specific data
}

--------------------------------------------------
-- Spawn & register enemy
--------------------------------------------------

function EnemyAI.spawnEnemy(enemyId: string, position: Vector3, parentModel: Model?): Model?
	local enemyDef = EnemyDatabase.Enemies[enemyId]
	if not enemyDef then
		warn("[EnemyAI] Unknown enemy:", enemyId)
		return nil
	end

	-- Create enemy model
	local model = Instance.new("Model")
	model.Name = enemyId

	-- Body (simple humanoid representation)
	local body = Instance.new("Part")
	body.Name = "HumanoidRootPart"
	body.Anchored = false
	body.Size = Vector3.new(4, 5, 4)
	body.Position = position + Vector3.new(0, 3, 0)
	body.Material = Enum.Material.SmoothPlastic
	body.BrickColor = EnemyAI._getEnemyColor(enemyId)
	body.Parent = model

	-- Make it the primary part
	model.PrimaryPart = body

	-- Humanoid for health display and pathfinding compatibility
	local humanoid = Instance.new("Humanoid")
	humanoid.MaxHealth = enemyDef.health
	humanoid.Health = enemyDef.health
	humanoid.WalkSpeed = enemyDef.moveSpeed
	humanoid.Parent = model

	-- Health bar
	local billboard = Instance.new("BillboardGui")
	billboard.Name = "HealthBar"
	billboard.Size = UDim2.new(0, 100, 0, 15)
	billboard.StudsOffset = Vector3.new(0, 4, 0)
	billboard.Adornee = body
	billboard.Parent = model

	local bg = Instance.new("Frame")
	bg.Name = "Background"
	bg.Size = UDim2.new(1, 0, 1, 0)
	bg.BackgroundColor3 = Color3.new(0.2, 0.2, 0.2)
	bg.BorderSizePixel = 0
	bg.Parent = billboard

	local fill = Instance.new("Frame")
	fill.Name = "Fill"
	fill.Size = UDim2.new(1, 0, 1, 0)
	fill.BackgroundColor3 = Color3.new(0.8, 0.1, 0.1)
	fill.BorderSizePixel = 0
	fill.Parent = bg

	local nameLabel = Instance.new("TextLabel")
	nameLabel.Name = "EnemyName"
	nameLabel.Size = UDim2.new(1, 0, 0, 20)
	nameLabel.Position = UDim2.new(0, 0, -1.5, 0)
	nameLabel.BackgroundTransparency = 1
	nameLabel.Text = enemyDef.name
	nameLabel.TextColor3 = Color3.new(1, 0.3, 0.3)
	nameLabel.TextScaled = true
	nameLabel.Font = Enum.Font.GothamBold
	nameLabel.Parent = billboard

	model.Parent = parentModel or workspace

	-- Register AI state
	EnemyAI._instances[model] = {
		enemyId = enemyId,
		model = model,
		behavior = enemyDef.behavior,
		target = nil,
		attackTimer = enemyDef.attackCooldown,
		specialTimer = 0,
		state = "idle",
		data = {
			attackCooldown = enemyDef.attackCooldown,
			attackRange = enemyDef.attackRange,
			detectionRange = enemyDef.detectionRange,
			moveSpeed = enemyDef.moveSpeed,
			attack = enemyDef.attack,
			-- Behavior-specific
			chargeSpeed = enemyDef.chargeSpeed,
			projectileSpeed = enemyDef.projectileSpeed,
			shieldHealth = enemyDef.shieldHealth,
		},
	}

	return model
end

function EnemyAI.removeEnemy(model: Model)
	EnemyAI._instances[model] = nil
	if model.Parent then
		model:Destroy()
	end
end

function EnemyAI.clearAll()
	for model in EnemyAI._instances do
		if model.Parent then
			model:Destroy()
		end
	end
	table.clear(EnemyAI._instances)
end

--------------------------------------------------
-- AI update (called every frame from GameManager)
--------------------------------------------------

function EnemyAI.update(dt: number)
	local players = Players:GetPlayers()
	if #players == 0 then return end

	for model, ai in EnemyAI._instances do
		if not model.Parent or not model.PrimaryPart then
			EnemyAI._instances[model] = nil
			continue
		end

		-- Find nearest player target
		ai.target = EnemyAI._findNearestPlayer(model, players)
		if not ai.target then continue end

		local targetChar = ai.target.Character
		if not targetChar or not targetChar:FindFirstChild("HumanoidRootPart") then
			continue
		end

		local myPos = model:GetPivot().Position
		local targetPos = targetChar.HumanoidRootPart.Position
		local dist = (targetPos - myPos).Magnitude

		-- Tick attack timer
		ai.attackTimer = math.max(0, ai.attackTimer - dt)
		ai.specialTimer = math.max(0, ai.specialTimer - dt)

		-- Dispatch to behavior
		local handler = EnemyAI._behaviors[ai.behavior]
		if handler then
			handler(ai, model, targetPos, dist, dt)
		else
			-- Default: chase and attack
			EnemyAI._behaviors.chase_explode(ai, model, targetPos, dist, dt)
		end
	end
end

--------------------------------------------------
-- Behavior implementations
--------------------------------------------------

EnemyAI._behaviors = {}

-- Wandering Ghost: slowly chase, explode on contact
EnemyAI._behaviors.chase_explode = function(ai: AIState, model: Model, targetPos: Vector3, dist: number, dt: number)
	local humanoid = model:FindFirstChildOfClass("Humanoid")
	if not humanoid then return end

	if dist <= ai.data.attackRange then
		-- Explode attack
		if ai.attackTimer <= 0 then
			ai.state = "attack"
			ai.attackTimer = ai.data.attackCooldown
			EnemyAI._dealDamageToNearby(ai, model, ai.data.attackRange, ai.data.attack)
		end
	elseif dist <= ai.data.detectionRange then
		-- Chase
		ai.state = "chase"
		humanoid:MoveTo(targetPos)
	else
		ai.state = "idle"
	end
end

-- Bone Archer: keep distance, fire projectiles
EnemyAI._behaviors.ranged_kite = function(ai: AIState, model: Model, targetPos: Vector3, dist: number, dt: number)
	local humanoid = model:FindFirstChildOfClass("Humanoid")
	if not humanoid then return end

	local preferredDist = ai.data.attackRange * 0.7

	if dist < preferredDist * 0.5 then
		-- Too close, retreat
		ai.state = "retreat"
		local awayDir = (model:GetPivot().Position - targetPos).Unit
		humanoid:MoveTo(model:GetPivot().Position + awayDir * 15)
	elseif dist <= ai.data.attackRange and ai.attackTimer <= 0 then
		-- Fire projectile
		ai.state = "attack"
		ai.attackTimer = ai.data.attackCooldown
		EnemyAI._fireProjectile(ai, model, targetPos)
	elseif dist > ai.data.attackRange and dist <= ai.data.detectionRange then
		-- Approach to range
		ai.state = "chase"
		humanoid:MoveTo(targetPos)
	else
		ai.state = "idle"
	end
end

-- Shadow Knight: charge at player, spin attack
EnemyAI._behaviors.charge_spin = function(ai: AIState, model: Model, targetPos: Vector3, dist: number, dt: number)
	local humanoid = model:FindFirstChildOfClass("Humanoid")
	if not humanoid then return end

	if ai.state == "charge" then
		-- Currently charging
		ai.data._chargeTimer = (ai.data._chargeTimer or 0) - dt
		if ai.data._chargeTimer <= 0 then
			-- Spin attack at destination
			EnemyAI._dealDamageToNearby(ai, model, ai.data.attackRange * 1.5, ai.data.attack * 1.5)
			ai.state = "idle"
			ai.attackTimer = ai.data.attackCooldown
		end
		return
	end

	if dist <= ai.data.detectionRange and ai.attackTimer <= 0 then
		-- Start charge
		ai.state = "charge"
		ai.data._chargeTimer = 0.5
		humanoid.WalkSpeed = ai.data.chargeSpeed or 40
		humanoid:MoveTo(targetPos)
	elseif dist <= ai.data.detectionRange then
		-- Slowly approach
		ai.state = "chase"
		humanoid.WalkSpeed = ai.data.moveSpeed
		humanoid:MoveTo(targetPos)
	else
		ai.state = "idle"
	end
end

-- Rune Guardian: shield up, reflect projectiles
EnemyAI._behaviors.shield_reflect = function(ai: AIState, model: Model, targetPos: Vector3, dist: number, dt: number)
	local humanoid = model:FindFirstChildOfClass("Humanoid")
	if not humanoid then return end

	ai.data._shieldUp = ai.data._shieldUp == nil and true or ai.data._shieldUp

	if dist <= ai.data.attackRange then
		if ai.attackTimer <= 0 then
			-- Drop shield briefly to attack
			ai.data._shieldUp = false
			ai.state = "attack"
			ai.attackTimer = ai.data.attackCooldown
			EnemyAI._dealDamageToNearby(ai, model, ai.data.attackRange, ai.data.attack)

			-- Raise shield again after delay
			task.delay(1.0, function()
				if EnemyAI._instances[model] then
					ai.data._shieldUp = true
				end
			end)
		end
	elseif dist <= ai.data.detectionRange then
		ai.state = "shield"
		humanoid:MoveTo(targetPos)
	else
		ai.state = "idle"
	end
end

--------------------------------------------------
-- Boss behaviors
--------------------------------------------------

EnemyAI._behaviors.boss_melee_summon = function(ai: AIState, model: Model, targetPos: Vector3, dist: number, dt: number)
	local humanoid = model:FindFirstChildOfClass("Humanoid")
	if not humanoid then return end

	-- Summon minions on timer
	ai.data._summonTimer = (ai.data._summonTimer or 8.0) - dt
	if ai.data._summonTimer <= 0 then
		ai.data._summonTimer = 8.0
		local pos = model:GetPivot().Position
		for _ = 1, 3 do
			local offset = Vector3.new(math.random(-15, 15), 0, math.random(-15, 15))
			EnemyAI.spawnEnemy("wandering_ghost", pos + offset)
		end
	end

	-- Melee chase & attack
	if dist <= 10 and ai.attackTimer <= 0 then
		ai.attackTimer = 1.5
		EnemyAI._dealDamageToNearby(ai, model, 10, ai.data.attack * 1.2)
	elseif dist <= 60 then
		humanoid:MoveTo(targetPos)
	end
end

EnemyAI._behaviors.boss_ranged_tracking = function(ai: AIState, model: Model, targetPos: Vector3, dist: number, dt: number)
	local humanoid = model:FindFirstChildOfClass("Humanoid")
	if not humanoid then return end

	-- Keep medium distance
	if dist < 20 then
		local away = (model:GetPivot().Position - targetPos).Unit
		humanoid:MoveTo(model:GetPivot().Position + away * 10)
	elseif dist > 40 then
		humanoid:MoveTo(targetPos)
	end

	-- Fire tracking projectiles
	if ai.attackTimer <= 0 then
		ai.attackTimer = 1.0
		EnemyAI._fireProjectile(ai, model, targetPos)
	end
end

EnemyAI._behaviors.boss_dual_mode = function(ai: AIState, model: Model, targetPos: Vector3, dist: number, dt: number)
	-- Alternate between melee and ranged every 5 seconds
	ai.data._modeTimer = (ai.data._modeTimer or 5.0) - dt
	if ai.data._modeTimer <= 0 then
		ai.data._modeTimer = 5.0
		ai.data._isRanged = not ai.data._isRanged
	end

	if ai.data._isRanged then
		EnemyAI._behaviors.boss_ranged_tracking(ai, model, targetPos, dist, dt)
	else
		EnemyAI._behaviors.boss_melee_summon(ai, model, targetPos, dist, dt)
	end
end

--------------------------------------------------
-- Combat helpers
--------------------------------------------------

function EnemyAI._dealDamageToNearby(ai: AIState, model: Model, radius: number, damage: number)
	local pos = model:GetPivot().Position
	for _, player in Players:GetPlayers() do
		local char = player.Character
		if char and char:FindFirstChild("HumanoidRootPart") then
			local dist = (char.HumanoidRootPart.Position - pos).Magnitude
			if dist <= radius then
				-- Fire damage event to CombatSystem (via RemoteEvent)
				local event = ReplicatedStorage:FindFirstChild("EnemyAttackEvent")
				if event then
					event:FireServer(player, damage, model)
				end
				-- Direct call fallback (for server-side integration)
				local CombatSystem = require(script.Parent.CombatSystem)
				CombatSystem.damagePlayer(player, damage, model)
			end
		end
	end
end

function EnemyAI._fireProjectile(ai: AIState, model: Model, targetPos: Vector3)
	local startPos = model:GetPivot().Position + Vector3.new(0, 2, 0)
	local direction = (targetPos - startPos).Unit
	local speed = ai.data.projectileSpeed or 40

	-- Create projectile part
	local proj = Instance.new("Part")
	proj.Name = "EnemyProjectile"
	proj.Anchored = true
	proj.CanCollide = false
	proj.Size = Vector3.new(1.5, 1.5, 1.5)
	proj.Shape = Enum.PartType.Ball
	proj.Position = startPos
	proj.Material = Enum.Material.Neon
	proj.BrickColor = BrickColor.new("Bright red")
	proj.Parent = workspace

	-- Move projectile
	local damage = ai.data.attack
	task.spawn(function()
		local elapsed = 0
		local maxTime = 3.0
		while elapsed < maxTime and proj.Parent do
			local dt = task.wait()
			elapsed += dt
			proj.Position += direction * speed * dt

			-- Check hit against players
			for _, player in Players:GetPlayers() do
				local char = player.Character
				if char and char:FindFirstChild("HumanoidRootPart") then
					local dist = (char.HumanoidRootPart.Position - proj.Position).Magnitude
					if dist <= 4 then
						local CombatSystem = require(script.Parent.CombatSystem)
						CombatSystem.damagePlayer(player, damage, nil)
						proj:Destroy()
						return
					end
				end
			end
		end
		if proj.Parent then
			proj:Destroy()
		end
	end)
end

function EnemyAI._findNearestPlayer(model: Model, players: { Player }): Player?
	local myPos = model:GetPivot().Position
	local nearest = nil
	local nearestDist = math.huge

	for _, player in players do
		local char = player.Character
		if char and char:FindFirstChild("HumanoidRootPart") then
			local dist = (char.HumanoidRootPart.Position - myPos).Magnitude
			if dist < nearestDist then
				nearest = player
				nearestDist = dist
			end
		end
	end

	return nearest
end

function EnemyAI._getEnemyColor(enemyId: string): BrickColor
	local colors = {
		wandering_ghost = BrickColor.new("Pastel light blue"),
		bone_archer     = BrickColor.new("Institutional white"),
		shadow_knight   = BrickColor.new("Black"),
		rune_guardian   = BrickColor.new("Deep blue"),
	}
	return colors[enemyId] or BrickColor.new("Medium stone grey")
end

return EnemyAI
