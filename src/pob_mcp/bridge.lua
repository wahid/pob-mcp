-- pob_mcp bridge: boots Path of Building headlessly and exposes a small JSON API.
-- Loaded by pob_mcp/engine.py inside an embedded LuaJIT (lupa), with the
-- working directory set to PoB's `src` folder.
---@diagnostic disable: lowercase-global

local POB_INFLATE, POB_DEFLATE = ...   -- zlib helpers supplied by Python

-------------------------------------------------------------------------------
-- Environment shims
-------------------------------------------------------------------------------
arg = {}
package.path = "../runtime/lua/?.lua;../runtime/lua/?/init.lua;./?.lua;" .. package.path

-- Never write to stdout: it carries the MCP protocol.
local function errprint(...)
	local parts = {}
	for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
	io.stderr:write(table.concat(parts, "\t"), "\n")
end
print = errprint

-- PoB's bundled runtime uses a C utf8 module; the stdlib string functions are a
-- good-enough stand-in for headless use (only number formatting and text
-- caret movement use it).
do
	local u = {}
	for _, k in ipairs({ "find", "gsub", "match", "sub", "reverse", "len", "lower", "upper",
		"gmatch", "byte", "char", "format", "rep" }) do
		u[k] = string[k]
	end
	u.next = function(s, i, d)
		local n = (i or 0) + (d or 1)
		if n < 1 or n > #s then return nil end
		return n
	end
	package.preload["lua-utf8"] = function() return u end
end

-- PoB ships a patched LuaJIT that accepts compound assignment (`x += 1`,
-- `s ..= "x"`). Stock LuaJIT does not, so rewrite those lines on load.
local raw_loadfile = loadfile
local function fixCompound(src)
	return (src:gsub("([\n\r])([ \t]*)([%a_][%w_%.%[%]\"']*)[ \t]*(%.%.)=[ \t]*([^\n\r]+)", "%1%2%3 = %3 %4 (%5)")
		:gsub("([\n\r])([ \t]*)([%a_][%w_%.%[%]\"']*)[ \t]*([%+%-%*/%%%^])=[ \t]*([^\n\r]+)", "%1%2%3 = %3 %4 (%5)"))
end
local function readAll(name)
	local h = io.open(name, "rb")
	if not h then return nil end
	local s = h:read("*a")
	h:close()
	return s
end
local function loadFixed(name)
	local src = readAll(name)
	if not src then return nil end
	src = "\n" .. src:gsub("^#[^\n]*", "")
	local f = loadstring(src, "@" .. name)
	if f then return f end
	return loadstring(fixCompound(src), "@" .. name)
end
function loadfile(name, ...)
	local f, err = raw_loadfile(name, ...)
	if f or not name then return f, err end
	local f2 = loadFixed(name)
	if f2 then return f2 end
	return f, err
end
function dofile(name)
	local f = assert(loadfile(name))
	return f()
end
-- Same fix for require()
table.insert(package.loaders, 2, function(modName)
	local path = package.searchpath(modName, package.path)
	if not path then return nil end
	local f, err = raw_loadfile(path)
	if f then return f end
	local f2 = loadFixed(path)
	if f2 then return f2 end
	error(err)
end)

-------------------------------------------------------------------------------
-- Boot PoB (mirrors HeadlessWrapper.lua without its blocking io.read)
-------------------------------------------------------------------------------
dofile("_SimpleGraphic.def.lua")
print = errprint
function GetVirtualScreenSize() return 1920, 1080 end
function Inflate(data)
	local ok, res = pcall(POB_INFLATE, data)
	if ok and res then return res end
	return nil, "inflate failed"
end
function Deflate(data)
	local ok, res = pcall(POB_DEFLATE, data)
	if ok and res then return res end
	return nil, "deflate failed"
end

__callbackTable__ = {}
function runCallback(name, ...)
	if __callbackTable__[name] then
		return __callbackTable__[name](...)
	elseif __mainObject__ and __mainObject__[name] then
		return __mainObject__[name](__mainObject__, ...)
	end
end

local l_require = require
function require(name)
	if name == "lcurl.safe" then return end
	return l_require(name)
end

dofile("Launch.lua")
__mainObject__.continuousIntegrationMode = false
runCallback("OnInit")
runCallback("OnFrame")
if __mainObject__.promptMsg then
	error("Path of Building failed to start: " .. tostring(__mainObject__.promptMsg))
end

local main = __mainObject__.main
local json = require("dkjson")

local function B() return main.modes["BUILD"] end

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------
local function cleanText(s)
	if type(s) ~= "string" then return s end
	return (s:gsub("%^x%x%x%x%x%x%x", ""):gsub("%^%d", ""))
end

local function num(v)
	if type(v) ~= "number" then return v end
	if v ~= v then return nil end
	if v == math.huge then return "inf" end
	if v == -math.huge then return "-inf" end
	return v
end

local function frame()
	runCallback("OnFrame")
	if __mainObject__.promptMsg then
		-- PoB traps errors and shows them in a popup; surface them instead
		local msg = cleanText(__mainObject__.promptMsg)
		__mainObject__.promptMsg = nil
		error(msg, 0)
	end
end

local function rebuild()
	local build = B()
	build.buildFlag = true
	-- In auto-level mode PoB re-estimates the level after calculating, which needs
	-- another pass for stats to match the new level
	for _ = 1, 3 do
		local level = build.characterLevel
		frame()
		if build.characterLevel == level then break end
		build.buildFlag = true
	end
end

local function output()
	return B().calcsTab.mainOutput or {}
end

local SUMMARY_KEYS = {
	-- offence
	"FullDPS", "CombinedDPS", "TotalDPS", "TotalDotDPS", "WithBleedDPS", "WithIgniteDPS", "WithPoisonDPS",
	"AverageHit", "AverageDamage", "Speed", "HitChance", "CritChance", "CritMultiplier",
	"ImpaleDPS", "CullingDPS", "TotalPoisonDPS", "IgniteDPS", "BleedDPS",
	-- pools
	"Life", "LifeUnreserved", "EnergyShield", "Mana", "ManaUnreserved", "Ward",
	"LifeRegenRecovery", "EnergyShieldRegenRecovery", "ManaRegenRecovery", "LifeLeechGainRate",
	-- defence
	"TotalEHP", "PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ColdMaximumHitTaken",
	"LightningMaximumHitTaken", "ChaosMaximumHitTaken",
	"Armour", "Evasion", "PhysicalDamageReduction", "MeleeEvadeChance",
	"FireResist", "ColdResist", "LightningResist", "ChaosResist",
	"FireResistOverCap", "ColdResistOverCap", "LightningResistOverCap", "ChaosResistOverCap",
	"BlockChance", "SpellBlockChance", "SpellSuppressionChance", "AttackDodgeChance",
	"SpellDodgeChance",
	-- misc
	"Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
	"MovementSpeedMod", "EffectiveMovementSpeedMod", "PowerChargesMax", "FrenzyChargesMax",
	"EnduranceChargesMax", "ManaCost", "LifeCost",
}

local function pickStats(keys)
	local out = output()
	local res = {}
	for _, k in ipairs(keys) do
		local v = out[k]
		if type(v) == "number" or type(v) == "string" or type(v) == "boolean" then
			res[k] = num(v)
		end
	end
	return res
end

local function allStats(filter)
	local out = output()
	local res = {}
	local pat = filter and filter:lower()
	for k, v in pairs(out) do
		if type(k) == "string" and (type(v) == "number" or type(v) == "boolean" or type(v) == "string") then
			if not pat or k:lower():find(pat, 1, true) then
				res[k] = num(v)
			end
		end
	end
	return res
end

local function socketGroupLabel(group)
	if group.displayLabel then return cleanText(group.displayLabel) end
	if group.label and group.label ~= "" then return group.label end
	local names = {}
	for _, g in ipairs(group.gemList or {}) do
		if g.gemData and not g.gemData.grantedEffect.support then
			names[#names + 1] = g.gemData.name
		end
	end
	return table.concat(names, ", ")
end

local function mainSkillName()
	local build = B()
	local group = build.skillsTab.socketGroupList[build.mainSocketGroup]
	if not group then return nil end
	local env = build.calcsTab.mainEnv
	if env and env.player and env.player.mainSkill and env.player.mainSkill.activeEffect then
		local eff = env.player.mainSkill.activeEffect
		return eff.grantedEffect and eff.grantedEffect.name or socketGroupLabel(group)
	end
	return socketGroupLabel(group)
end

-- Passive points at the current level, assuming every quest reward was taken
local function pointBudget()
	local build = B()
	local extra = build.calcsTab and build.calcsTab.mainOutput and build.calcsTab.mainOutput.ExtraPoints or 0
	local level = build.characterLevel or 1
	return math.min(level - 1, 99) + 23 + extra
end

local function info()
	local build = B()
	local spec = build.spec
	local used, ascUsed = 0, 0
	if spec then used, ascUsed = spec:CountAllocNodes() end
	return {
		name = build.buildName,
		className = spec and spec.curClassName,
		ascendancy = spec and spec.curAscendClassName,
		level = build.characterLevel,
		bandit = build.bandit or (build.configTab and build.configTab.input.bandit),
		treeVersion = spec and spec.treeVersion,
		targetVersion = build.targetVersion,
		mainSkill = mainSkillName(),
		mainSocketGroup = build.mainSocketGroup,
		passivePointsUsed = used,
		ascendancyPointsUsed = ascUsed,
		passivePointsAvailable = pointBudget(),
		passivePointsRemaining = pointBudget() - used,
		ascendancyPointsAvailable = 8,
	}
end

local function resolveNode(ref)
	local spec = B().spec
	local id = tonumber(ref)
	if id and spec.nodes[id] then return spec.nodes[id] end
	local want = tostring(ref):lower()
	-- Several nodes can share a name (e.g. "Life Mastery"): prefer allocated, then
	-- the one with the shortest path from the current tree
	local best, bestScore
	for _, node in pairs(spec.nodes) do
		if node.dn and node.dn:lower() == want then
			local score = node.alloc and -1 or (node.path and #node.path or 10000)
			if node.ascendancyName and node.ascendancyName ~= spec.curAscendClassName then score = score + 20000 end
			if not bestScore or score < bestScore then best, bestScore = node, score end
		end
	end
	return best
end

local function nodeInfo(node)
	return {
		id = node.id,
		name = node.dn,
		type = node.type,
		allocated = node.alloc and true or false,
		ascendancy = node.ascendancyName,
		pathCost = node.path and #node.path or nil,
		stats = node.sd,
	}
end

-------------------------------------------------------------------------------
-- API (each function returns a JSON string)
-------------------------------------------------------------------------------
local API = {}

function API.new_build()
	main:SetMode("BUILD", false, "New build")
	frame()
	return info()
end

function API.load_xml(p)
	main:SetMode("BUILD", false, p.name or "Imported build", p.xml)
	frame()
	if __mainObject__.promptMsg then
		local msg = cleanText(__mainObject__.promptMsg)
		__mainObject__.promptMsg = nil
		error(msg)
	end
	local build = B()
	if not build.spec then error("Build failed to load (invalid XML?)") end
	return info()
end

function API.export_xml()
	return { xml = B():SaveDB("code") }
end

function API.summary()
	local res = info()
	res.stats = pickStats(SUMMARY_KEYS)
	return res
end

function API.stats(p)
	if p.keys and #p.keys > 0 then
		return pickStats(p.keys)
	end
	return allStats(p.filter)
end

function API.skills()
	local build = B()
	local groups = {}
	for i, group in ipairs(build.skillsTab.socketGroupList) do
		local gems = {}
		for j, g in ipairs(group.gemList or {}) do
			gems[#gems + 1] = {
				index = j,
				name = g.gemData and g.gemData.name or g.nameSpec,
				level = g.level,
				quality = g.quality,
				enabled = g.enabled ~= false,
				support = g.gemData and g.gemData.grantedEffect and g.gemData.grantedEffect.support or false,
				error = g.errMsg,
			}
		end
		groups[#groups + 1] = {
			index = i,
			label = socketGroupLabel(group),
			slot = group.slot,
			enabled = group.enabled ~= false,
			includeInFullDPS = group.includeInFullDPS or false,
			isMain = (i == build.mainSocketGroup),
			source = group.source,
			gems = gems,
		}
	end
	return { mainSocketGroup = build.mainSocketGroup, groups = groups }
end

function API.items()
	local itemsTab = B().itemsTab
	local res = {}
	for _, slot in ipairs(itemsTab.orderedSlots) do
		if slot.selItemId and slot.selItemId ~= 0 and not slot.nodeId then
			local item = itemsTab.items[slot.selItemId]
			if item then
				res[#res + 1] = {
					slot = slot.slotName,
					id = item.id,
					name = item.name,
					rarity = item.rarity,
					base = item.baseName,
					raw = item:BuildRaw(),
				}
			end
		end
	end
	-- Tree-socketed jewels
	for nodeId, itemId in pairs(B().spec.jewels or {}) do
		local item = itemsTab.items[itemId]
		local node = B().spec.nodes[nodeId]
		if item and node and node.alloc then
			res[#res + 1] = {
				slot = "Jewel socket " .. nodeId,
				id = item.id,
				name = item.name,
				rarity = item.rarity,
				base = item.baseName,
				raw = item:BuildRaw(),
			}
		end
	end
	return { items = res }
end

function API.slots()
	local names = {}
	local itemsTab = B().itemsTab
	local spec = B().spec
	for _, slot in ipairs(itemsTab.orderedSlots) do
		local shown = true
		if slot.IsShown then
			local ok, res = pcall(slot.IsShown, slot)
			shown = not ok or res
		end
		if not slot.nodeId and shown then names[#names + 1] = slot.slotName end
	end
	local sockets = {}
	for nodeId, slot in pairs(itemsTab.sockets) do
		local node = spec.nodes[nodeId]
		if node and node.alloc then
			sockets[#sockets + 1] = { slot = slot.slotName, nodeId = nodeId,
				occupied = slot.selItemId ~= 0 and slot.selItemId ~= nil }
		end
	end
	table.sort(sockets, function(a, b) return a.nodeId < b.nodeId end)
	return { slots = names, allocatedJewelSockets = sockets,
		note = "Allocate a jewel socket node on the tree to get more 'Jewel <id>' slots" }
end

function API.tree()
	local spec = B().spec
	local keystones, notables, ascendancy, masteries = {}, {}, {}, {}
	local smallCount = 0
	for _, node in pairs(spec.allocNodes) do
		if node.ascendancyName then
			if node.type ~= "AscendClassStart" and not node.isAscendancyStart then
				ascendancy[#ascendancy + 1] = node.dn
			end
		elseif node.type == "Keystone" then
			keystones[#keystones + 1] = node.dn
		elseif node.type == "Notable" then
			notables[#notables + 1] = node.dn
		elseif node.type == "Mastery" then
			local eff = spec.masterySelections and spec.masterySelections[node.id]
			local text = node.dn
			if eff and spec.tree.masteryEffects and spec.tree.masteryEffects[eff] then
				text = text .. ": " .. table.concat(spec.tree.masteryEffects[eff].sd or {}, " / ")
			end
			masteries[#masteries + 1] = text
		elseif node.type == "Normal" then
			smallCount = smallCount + 1
		end
	end
	table.sort(keystones); table.sort(notables); table.sort(ascendancy); table.sort(masteries)
	local used, ascUsed = spec:CountAllocNodes()
	return {
		className = spec.curClassName,
		ascendancy = spec.curAscendClassName,
		treeVersion = spec.treeVersion,
		pointsUsed = used,
		ascendancyPointsUsed = ascUsed,
		keystones = keystones,
		notables = notables,
		ascendancyNodes = ascendancy,
		masteries = masteries,
		smallPassives = smallCount,
		url = spec:EncodeURL("https://www.pathofexile.com/passive-skill-tree/"),
	}
end

function API.find_nodes(p)
	local spec = B().spec
	local q = (p.query or ""):lower()
	local res = {}
	for _, node in pairs(spec.nodes) do
		if node.dn and (node.type == "Notable" or node.type == "Keystone" or node.type == "Socket" or p.include_small or node.ascendancyName) then
			local hit = node.dn:lower():find(q, 1, true)
			if not hit and p.search_stats then
				for _, line in ipairs(node.sd or {}) do
					if line:lower():find(q, 1, true) then hit = true break end
				end
			end
			if hit and (not node.ascendancyName or node.ascendancyName == spec.curAscendClassName) then
				res[#res + 1] = nodeInfo(node)
			end
		end
	end
	-- Unallocated nodes first, cheapest path first; allocated nodes last
	table.sort(res, function(a, b)
		if a.allocated ~= b.allocated then return not a.allocated end
		if (a.pathCost or 999) ~= (b.pathCost or 999) then return (a.pathCost or 999) < (b.pathCost or 999) end
		return a.name < b.name
	end)
	local limit = p.limit or 25
	while #res > limit do table.remove(res) end
	return { nodes = res }
end

function API.config(p)
	local configTab = B().configTab
	local res = {}
	for k, v in pairs(configTab.input) do
		if not p.filter or k:lower():find(p.filter:lower(), 1, true) then
			res[k] = num(v)
		end
	end
	return { input = res }
end

function API.config_options(p)
	local res = {}
	local q = p.query and p.query:lower()
	for _, varData in ipairs(LoadModule("Modules/ConfigOptions")) do
		if varData.var and varData.type then
			local label = cleanText(varData.label or "")
			if not q or varData.var:lower():find(q, 1, true) or label:lower():find(q, 1, true) then
				local entry = { var = varData.var, type = varData.type, label = label, section = nil }
				if varData.list then
					entry.choices = {}
					for _, c in ipairs(varData.list) do
						entry.choices[#entry.choices + 1] = { value = c.val, label = cleanText(c.label) }
					end
				end
				res[#res + 1] = entry
			end
		end
	end
	return { options = res }
end

-------------------------------------------------------------------------------
-- Edits. Each edit is a table { op = "...", ... }. Returns a list of notes.
-------------------------------------------------------------------------------
local EDIT = {}

function EDIT.allocate_node(e, notes)
	local spec = B().spec
	local node = resolveNode(e.node)
	if not node then error("Passive node not found: " .. tostring(e.node)) end
	if node.alloc then notes[#notes + 1] = node.dn .. " already allocated" return end
	if not node.path then error("Passive node is not reachable: " .. node.dn) end
	local cost = #node.path
	local used, ascUsed = spec:CountAllocNodes()
	if node.ascendancyName and node.ascendancyName == spec.curAscendClassName then
		if ascUsed + cost > 8 then
			error(("%s costs %d ascendancy point%s (incl. path) but only %d of 8 are left. Deallocate another ascendancy notable first.")
				:format(node.dn, cost, cost == 1 and "" or "s", 8 - ascUsed), 0)
		end
	elseif not node.ascendancyName and used + cost > pointBudget() then
		notes[#notes + 1] = ("Warning: this goes over the passive point budget (%d of %d used after this)"):format(used + cost, pointBudget())
	end
	spec:AllocNode(node)
	spec:AddUndoState()
	notes[#notes + 1] = ("Allocated %s (%d point%s incl. path)"):format(node.dn, cost, cost == 1 and "" or "s")
end

function EDIT.deallocate_node(e, notes)
	local spec = B().spec
	local node = resolveNode(e.node)
	if not node then error("Passive node not found: " .. tostring(e.node)) end
	if not node.alloc then notes[#notes + 1] = node.dn .. " was not allocated" return end
	local n = #(node.depends or {})
	spec:DeallocNode(node)
	spec:AddUndoState()
	notes[#notes + 1] = ("Deallocated %s (%d node%s removed incl. dependents)"):format(node.dn, n, n == 1 and "" or "s")
end

function EDIT.equip_item(e, notes)
	local itemsTab = B().itemsTab
	local slot = itemsTab.slots[e.slot]
	if not slot then error("Unknown item slot: " .. tostring(e.slot) .. " (see list_item_slots)") end
	local text = e.item_text:gsub("\r\n", "\n")
	local item = new("Item"):Item(text)
	if not item.base then error("Could not parse item text (unknown base type?)") end
	item:NormaliseQuality()
	item:BuildModList()
	itemsTab:AddItem(item, true)
	slot:SetSelItemId(item.id)
	itemsTab:PopulateSlots()
	itemsTab:AddUndoState()
	notes[#notes + 1] = ("Equipped %s in %s"):format(item.name or "item", e.slot)
end

function EDIT.unequip_slot(e, notes)
	local itemsTab = B().itemsTab
	local slot = itemsTab.slots[e.slot]
	if not slot then error("Unknown item slot: " .. tostring(e.slot)) end
	slot:SetSelItemId(0)
	itemsTab:PopulateSlots()
	itemsTab:AddUndoState()
	notes[#notes + 1] = "Emptied " .. e.slot
end

local function getGroup(idx)
	local skillsTab = B().skillsTab
	local group = skillsTab.socketGroupList[tonumber(idx) or -1]
	if not group then error("No socket group with index " .. tostring(idx) .. " (see get_skills)", 0) end
	return group
end

-- Accepts "Mirage Archer", "Mirage Archer Support", any case
local function normalizeGemName(name)
	if type(name) ~= "string" then return name end
	local data = B().data
	local want = name:lower():gsub("^%s+", ""):gsub("%s+$", "")
	local stripped = want:gsub(" support$", "")
	for _, gem in pairs(data.gems) do
		local n = gem.name:lower()
		if n == want or n == stripped or n == stripped .. " support" then return gem.name end
	end
	return name
end

local function finishGroup(group)
	local skillsTab = B().skillsTab
	skillsTab:ProcessSocketGroup(group)
	skillsTab:AddUndoState()
	for _, g in ipairs(group.gemList) do
		if g.errMsg then error(("Gem '%s': %s"):format(tostring(g.nameSpec), cleanText(g.errMsg))) end
	end
end

function EDIT.add_gem(e, notes)
	local group = getGroup(e.group)
	table.insert(group.gemList, {
		nameSpec = normalizeGemName(e.gem), level = e.level or 20, quality = e.quality or 0,
		qualityId = "Default", enabled = true, enableGlobal1 = true, enableGlobal2 = true, count = 1,
	})
	finishGroup(group)
	notes[#notes + 1] = ("Added %s to group %s"):format(e.gem, tostring(e.group))
end

function EDIT.remove_gem(e, notes)
	local group = getGroup(e.group)
	local idx = tonumber(e.gem_index)
	if not idx then
		for i, g in ipairs(group.gemList) do
			local n = g.gemData and g.gemData.name or g.nameSpec
			if n and n:lower() == tostring(e.gem):lower() then idx = i break end
		end
	end
	if not idx or not group.gemList[idx] then error("Gem not found in group " .. tostring(e.group)) end
	local removed = group.gemList[idx]
	table.remove(group.gemList, idx)
	finishGroup(group)
	notes[#notes + 1] = ("Removed %s from group %s"):format(removed.nameSpec or "gem", tostring(e.group))
end

function EDIT.replace_gem(e, notes)
	local group = getGroup(e.group)
	local target
	for _, g in ipairs(group.gemList) do
		local n = g.gemData and g.gemData.name or g.nameSpec
		if (e.gem_index and tonumber(e.gem_index) == _) or (n and e.old_gem and n:lower() == e.old_gem:lower()) then
			target = g break
		end
	end
	if not target then error("Gem to replace not found in group " .. tostring(e.group)) end
	local old = target.nameSpec
	target.nameSpec = normalizeGemName(e.new_gem)
	target.gemId = nil
	target.skillId = nil
	if e.level then target.level = e.level end
	if e.quality then target.quality = e.quality end
	finishGroup(group)
	notes[#notes + 1] = ("Replaced %s with %s in group %s"):format(tostring(old), e.new_gem, tostring(e.group))
end

function EDIT.set_gem(e, notes)
	local group = getGroup(e.group)
	local g = group.gemList[tonumber(e.gem_index) or -1]
	if not g then error("No gem at index " .. tostring(e.gem_index)) end
	if e.level ~= nil then g.level = e.level end
	if e.quality ~= nil then g.quality = e.quality end
	if e.enabled ~= nil then g.enabled = e.enabled end
	finishGroup(group)
	notes[#notes + 1] = ("Updated gem %s in group %s"):format(g.nameSpec or "?", tostring(e.group))
end

function EDIT.set_main_skill(e, notes)
	local group = getGroup(e.group)
	local build = B()
	build.mainSocketGroup = tonumber(e.group)
	if e.skill_part then group.mainActiveSkill = tonumber(e.skill_part) end
	notes[#notes + 1] = "Main skill group set to " .. tostring(e.group) .. " (" .. socketGroupLabel(group) .. ")"
end

function EDIT.set_group_enabled(e, notes)
	local group = getGroup(e.group)
	if e.enabled ~= nil then group.enabled = e.enabled end
	if e.include_in_full_dps ~= nil then group.includeInFullDPS = e.include_in_full_dps end
	B().skillsTab:AddUndoState()
	notes[#notes + 1] = "Updated group " .. tostring(e.group)
end

function EDIT.set_config(e, notes)
	local configTab = B().configTab
	local v = e.value
	if v == json.null then v = nil end
	configTab.input[e.var] = v
	if configTab.UpdateControls then configTab:UpdateControls() end
	configTab:BuildModList()
	configTab:AddUndoState()
	notes[#notes + 1] = ("Config %s = %s"):format(e.var, tostring(v))
end

function EDIT.set_level(e, notes)
	local build = B()
	build.characterLevel = math.max(1, math.min(100, tonumber(e.level)))
	build.characterLevelAutoMode = false
	if build.controls and build.controls.characterLevel then
		build.controls.characterLevel:SetText(tostring(build.characterLevel))
	end
	build.configTab:BuildModList()
	notes[#notes + 1] = "Character level set to " .. build.characterLevel
end

function EDIT.add_custom_mod(e, notes)
	-- Appends lines to the Config tab's custom modifiers box
	local configTab = B().configTab
	local cur = configTab.input.customMods or ""
	configTab.input.customMods = (cur ~= "" and (cur .. "\n") or "") .. e.mod
	configTab:BuildModList()
	configTab:AddUndoState()
	notes[#notes + 1] = "Added custom modifier: " .. e.mod
end

local function applyEdits(edits)
	local notes = {}
	for i, e in ipairs(edits or {}) do
		local fn = EDIT[e.op]
		if not fn then error(("Edit #%d: unknown op '%s'"):format(i, tostring(e.op))) end
		local ok, err = pcall(fn, e, notes)
		if not ok then
			err = tostring(err):gsub("^[^\n:]+%.lua:%d+: ", "")
			error(("Edit #%d (%s) failed: %s"):format(i, e.op, err), 0)
		end
	end
	rebuild()
	return notes
end

local function diff(before, after, keys)
	local res = setmetatable({}, { __jsontype = "object" }) -- "{}" rather than "[]" when nothing changed
	for _, k in ipairs(keys) do
		local a, b = before[k], after[k]
		if type(a) == "number" and type(b) == "number" then
			if math.abs(b - a) > 1e-6 then
				res[k] = { before = a, after = b, change = b - a,
					pct = (a ~= 0) and ((b - a) / math.abs(a) * 100) or nil }
			end
		elseif a ~= b then
			res[k] = { before = a, after = b }
		end
	end
	return res
end

function API.apply_edits(p)
	local before = pickStats(SUMMARY_KEYS)
	local notes = applyEdits(p.edits)
	local after = pickStats(SUMMARY_KEYS)
	return { notes = notes, changes = diff(before, after, SUMMARY_KEYS), info = info() }
end

function API.compare_edits(p)
	local build = B()
	local snapshot = build:SaveDB("code")
	local name = build.buildName
	local keys = SUMMARY_KEYS
	if p.extra_stats then
		keys = {}
		for _, k in ipairs(SUMMARY_KEYS) do keys[#keys + 1] = k end
		for _, k in ipairs(p.extra_stats) do keys[#keys + 1] = k end
	end
	local before = pickStats(keys)
	local ok, notesOrErr = pcall(applyEdits, p.edits)
	local after = ok and pickStats(keys) or nil
	-- Always restore the original build
	main:SetMode("BUILD", false, name, snapshot)
	frame()
	if not ok then error(notesOrErr, 0) end
	return { notes = notesOrErr, changes = diff(before, after, keys) }
end

-------------------------------------------------------------------------------
-- Optimisation: passive ranking, support gem ranking, warnings
-------------------------------------------------------------------------------

-- Reads a stat from an output table; "Minion.CombinedDPS" style paths allowed.
local function readStat(out, stat)
	local v = out
	for part in stat:gmatch("[^%.]+") do
		if type(v) ~= "table" then return nil end
		v = v[part]
	end
	if type(v) == "number" and v == v and v ~= math.huge and v ~= -math.huge then return v end
	return nil
end

local function usesFullDPS()
	for _, group in ipairs(B().skillsTab.socketGroupList) do
		if group.includeInFullDPS and group.enabled ~= false then return true end
	end
	return false
end

-- Picks the stat to optimise when the caller doesn't name one.
local function defaultMetric()
	local out = output()
	if usesFullDPS() and (out.FullDPS or 0) > 0 then return "FullDPS" end
	if out.Minion and (readStat(out, "Minion.CombinedDPS") or 0) > 0 then return "Minion.CombinedDPS" end
	return "CombinedDPS"
end

local function metricInfo(p)
	local metric = (p.metric and p.metric ~= "") and p.metric or defaultMetric()
	local useFull = metric == "FullDPS"
	local calcFunc, base = B().calcsTab:GetMiscCalculator()
	local baseVal = readStat(base, metric)
	if baseVal == nil then
		error(("Unknown or non-numeric stat '%s' (see get_stats for names; minion stats use 'Minion.<Stat>')"):format(metric), 0)
	end
	return metric, useFull, calcFunc, baseVal
end

local function gainEntry(entry, val, baseVal)
	entry.value = val
	entry.gain = val and (val - baseVal) or nil
	entry.pct = (val and baseVal ~= 0) and ((val - baseVal) / math.abs(baseVal) * 100) or nil
	return entry
end

function API.rank_passives(p)
	local build = B()
	local spec = build.spec
	local metric, useFull, calcFunc, baseVal = metricInfo(p)
	local mode = p.mode or "add"
	local maxCost = p.max_path_cost or 8
	local limit = p.limit or 15
	local granted = build.calcsTab.mainEnv.grantedPassives or {}
	local results = {}
	local evaluated = 0
	local cache = {}

	local function wanted(node)
		if not node.dn or node.modKey == "" or granted[node.id] then return false end
		if node.type == "Mastery" or node.type == "ClassStart" or node.type == "AscendClassStart" or node.isAscendancyStart then
			return false
		end
		if node.ascendancyName then
			return p.include_ascendancy and node.ascendancyName == spec.curAscendClassName
		end
		if node.type == "Normal" then return p.include_small or false end
		return node.type == "Notable" or node.type == "Keystone"
	end

	if mode == "add" then
		for _, node in pairs(spec.nodes) do
			if not node.alloc and node.path and #node.path > 0 and #node.path <= maxCost and wanted(node) then
				-- Single-node value (cached by identical mods), then the full path
				local key = node.modKey
				if not cache[key] then
					cache[key] = readStat(calcFunc({ addNodes = { [node] = true } }, useFull), metric)
					evaluated = evaluated + 1
				end
				local nodeVal = cache[key]
				local pathVal = nodeVal
				local cost = #node.path
				if cost > 1 then
					local set = {}
					for _, pn in ipairs(node.path) do set[pn] = true end
					pathVal = readStat(calcFunc({ addNodes = set }, useFull), metric)
					evaluated = evaluated + 1
				end
				local e = gainEntry({ id = node.id, name = node.dn, type = node.type, cost = cost,
					ascendancy = node.ascendancyName, stats = node.sd }, pathVal, baseVal)
				e.nodeOnlyGain = nodeVal and (nodeVal - baseVal) or nil
				e.gainPerPoint = e.gain and e.gain / cost or nil
				results[#results + 1] = e
			end
		end
		local sortKey = p.sort == "total" and "gain" or "gainPerPoint"
		table.sort(results, function(a, b) return (a[sortKey] or -math.huge) > (b[sortKey] or -math.huge) end)
	elseif mode == "remove" then
		for _, node in pairs(spec.allocNodes) do
			if wanted(node) then
				local set, n = {}, 0
				for _, dn in ipairs(node.depends or { node }) do set[dn] = true n = n + 1 end
				local val = readStat(calcFunc({ removeNodes = set }, useFull), metric)
				evaluated = evaluated + 1
				local e = gainEntry({ id = node.id, name = node.dn, type = node.type, pointsRefunded = n,
					ascendancy = node.ascendancyName, stats = node.sd }, val, baseVal)
				e.lossPerPoint = e.gain and (-e.gain / math.max(n, 1)) or nil
				results[#results + 1] = e
			end
		end
		-- Least valuable first: smallest loss per refunded point
		table.sort(results, function(a, b)
			local la, lb = a.lossPerPoint or math.huge, b.lossPerPoint or math.huge
			if la ~= lb then return la < lb end
			return (a.pointsRefunded or 0) > (b.pointsRefunded or 0)
		end)
	else
		error("mode must be 'add' or 'remove'", 0)
	end

	local total = #results
	while #results > limit do table.remove(results) end
	return { metric = metric, base = baseVal, mode = mode, candidates = total,
		calculations = evaluated, results = results }
end

function API.rank_support_gems(p)
	local build = B()
	local skillsTab = build.skillsTab
	local group = getGroup(p.group or build.mainSocketGroup)
	local metric, useFull, calcFunc, baseVal = metricInfo(p)
	local limit = p.limit or 15
	local gemList = group.gemList

	-- Supports that can affect the group's active skills (same test PoB's gem picker uses)
	local skills = group.displaySkillList or {}
	if not skills[1] then error("Socket group has no active skill to support", 0) end
	local replaceIdx = tonumber(p.replace_gem_index)
	if replaceIdx and not gemList[replaceIdx] then error("No gem at index " .. replaceIdx, 0) end

	-- Effects already in the link (incl. awakened/normal pairs), except the one being replaced
	local inGroup = {}
	for i, g in ipairs(gemList) do
		if g.gemData and i ~= replaceIdx then
			local ge = g.gemData.grantedEffect
			inGroup[ge.id] = true
			if ge.plusVersionOf then inGroup[ge.plusVersionOf] = true end
		end
	end
	local slotIdx = replaceIdx or (#gemList + 1)
	local saved = gemList[slotIdx] and copyTable(gemList[slotIdx], true)

	local results, checked = {}, 0
	for gemId, gemData in pairs(build.data.gems) do
		local ge = gemData.grantedEffect
		local exceptional = (ge.plusVersionOf ~= nil) or (gemData.tagString or ""):match("Exceptional") ~= nil
		local isTrigger = (gemData.tagString or ""):lower():find("trigger", 1, true) ~= nil
			or (gemData.name:match("^Cast ") ~= nil)
		if ge.support and not ge.legacy and not inGroup[ge.id]
			and not (ge.plusVersionOf and inGroup[ge.plusVersionOf])
			and (p.include_exceptional ~= false or not exceptional)
			and (p.include_triggers or not isTrigger) then
			local ok = false
			for _, s in ipairs(skills) do
				if calcLib.canGrantedEffectSupportActiveSkill(ge, s) then ok = true break end
			end
			if ok then
				local level = skillsTab:ProcessGemLevel(gemData)
				gemList[slotIdx] = {
					level = level, quality = p.quality or 20, qualityId = "Default", count = 1,
					enabled = true, enableGlobal1 = true, enableGlobal2 = true,
					gemId = gemId, nameSpec = gemData.name, skillId = gemData.grantedEffectId,
					gemData = gemData,
				}
				local okCalc, out = pcall(calcFunc, nil, useFull)
				checked = checked + 1
				if okCalc then
					results[#results + 1] = gainEntry({ name = gemData.name, level = level,
						exceptional = exceptional }, readStat(out, metric), baseVal)
				end
			end
		end
	end
	-- Restore the group
	gemList[slotIdx] = saved
	if saved == nil then gemList[slotIdx] = nil end
	skillsTab:ProcessSocketGroup(group)

	table.sort(results, function(a, b) return (a.value or -math.huge) > (b.value or -math.huge) end)

	-- How much each current support contributes (value lost if disabled)
	local current = {}
	if p.include_current ~= false then
		for i, g in ipairs(gemList) do
			if g.gemData and g.gemData.grantedEffect.support and g.enabled ~= false then
				g.enabled = false
				local okCalc, out = pcall(calcFunc, nil, useFull)
				g.enabled = true
				if okCalc then
					local v = readStat(out, metric)
					current[#current + 1] = { index = i, name = g.gemData.name,
						contribution = v and (baseVal - v) or nil,
						pct = (v and v ~= 0) and ((baseVal - v) / math.abs(v) * 100) or nil }
				end
			end
		end
		table.sort(current, function(a, b) return (a.contribution or 0) < (b.contribution or 0) end)
		skillsTab:ProcessSocketGroup(group)
	end

	local total = #results
	while #results > limit do table.remove(results) end
	return {
		metric = metric, base = baseVal, group = p.group or build.mainSocketGroup,
		label = socketGroupLabel(group),
		mode = replaceIdx and ("replace gem " .. replaceIdx .. " (" .. tostring(saved and saved.nameSpec) .. ")") or "add to group",
		candidates = total, results = results, currentSupports = current,
	}
end

function API.warnings()
	local build = B()
	local out = output()
	local res = {}
	local seen = {}
	local function add(kind, msg)
		msg = cleanText(msg)
		if not seen[msg] then
			seen[msg] = true
			res[#res + 1] = { kind = kind, message = msg }
		end
	end

	-- PoB's own sidebar warnings
	if build.RefreshStatList then pcall(build.RefreshStatList, build) end
	for _, line in ipairs(build.controls and build.controls.warnings and build.controls.warnings.lines or {}) do
		add("pob", line)
	end

	-- Extra checks
	for _, elem in ipairs({ "Fire", "Cold", "Lightning" }) do
		local env = build.calcsTab.mainEnv
		local max = env and env.player.modDB:Sum("BASE", nil, elem .. "ResistMax") or 75
		if max == 0 then max = 75 end
		local r = out[elem .. "Resist"]
		if type(r) == "number" and r < max then
			add("resist", ("%s resistance is %d%% (cap %d%%)"):format(elem, r, max))
		end
	end
	if type(out.ChaosResist) == "number" and out.ChaosResist < 0 then
		add("resist", ("Chaos resistance is negative (%d%%)"):format(out.ChaosResist))
	end
	for _, attr in ipairs({ "Str", "Dex", "Int" }) do
		local have, need = out[attr], out["Req" .. attr]
		if type(have) == "number" and type(need) == "number" and need > have then
			add("requirement", ("%s requirement not met: need %d, have %d"):format(attr, need, have))
		end
	end
	for _, pool in ipairs({ "Life", "Mana" }) do
		local unres = out[pool .. "Unreserved"]
		if type(unres) == "number" and unres < 0 then
			add("reservation", ("%s is over-reserved (%d unreserved)"):format(pool, unres))
		end
	end
	local lifeRes = out.LifeReservedPercent
	if type(lifeRes) == "number" and lifeRes >= 40 and (out.Life or 0) > 1 then
		add("reservation", ("%d%% of life is reserved (%d unreserved). Max hit and EHP only use the unreserved part; "
			.. "fine for a deliberate low-life build, otherwise move reservations to mana."):format(lifeRes, out.LifeUnreserved or 0))
	end
	for _, group in ipairs(build.skillsTab.socketGroupList) do
		for _, g in ipairs(group.gemList or {}) do
			if g.errMsg then
				add("gem", ("Group %s: %s"):format(socketGroupLabel(group), g.errMsg))
			end
		end
	end
	local used, ascUsed = build.spec:CountAllocNodes()
	if used > pointBudget() then
		add("points", ("%d passive points allocated but only %d available at level %d"):format(used, pointBudget(), build.characterLevel))
	end
	if ascUsed > 8 then add("points", ("%d ascendancy points allocated (max 8)"):format(ascUsed)) end
	local env = build.calcsTab.mainEnv
	local skill = env and env.player and env.player.mainSkill
	if skill and skill.infoMessage then add("info", skill.infoMessage) end
	if skill and skill.disableReason then add("skill", "Main skill disabled: " .. skill.disableReason) end

	return { count = #res, warnings = res }
end

-------------------------------------------------------------------------------
-- Item mod pools (used by create_rare, list_item_mods, fix_gaps, trade weights)
-------------------------------------------------------------------------------

local function findBase(name)
	local data = B().data
	if data.itemBases[name] then return name, data.itemBases[name] end
	local want = tostring(name):lower()
	for n, b in pairs(data.itemBases) do
		if n:lower() == want then return n, b end
	end
end

-- A blank rare of the given base, used to ask PoB which mods can roll on it
local function blankRare(baseName, ilvl)
	local item = new("Item"):Item(("Rarity: RARE\nBlank\n%s\nCrafted: true\nItem Level: %d"):format(baseName, ilvl or 86))
	if item.base then item:Craft() end -- sets the affix limits
	return item
end

-- Natural explicit mods for an item (no unique-only, corrupted, influence or
-- veiled mods unless the item has that influence)
local function modPool(item, ilvl)
	local data = B().data
	local natural = item.affixes == data.itemMods.Item and data.itemMods.Explicit or item.affixes
	local list = {}
	for modId, mod in pairs(natural or {}) do
		if (mod.type == "Prefix" or mod.type == "Suffix") and mod.weightKey
			and item:GetModSpawnWeight(mod) > 0 then
			list[#list + 1] = { id = modId, mod = mod, allowed = (mod.level or 1) <= (ilvl or 86) }
		end
	end
	-- Tier 1 = highest level in its group for this base
	local byGroup = {}
	for _, m in ipairs(list) do
		local g = (m.mod.group or m.id) .. "|" .. m.mod.type
		byGroup[g] = byGroup[g] or {}
		table.insert(byGroup[g], m)
	end
	for _, g in pairs(byGroup) do
		table.sort(g, function(a, b) return (a.mod.level or 0) > (b.mod.level or 0) end)
		for i, m in ipairs(g) do m.tier = i end
	end
	return list, byGroup
end

local function modText(mod)
	local t = {}
	for _, line in ipairs(mod) do t[#t + 1] = line end
	return table.concat(t, " / ")
end

function API.list_item_mods(p)
	local baseName = findBase(p.base)
	if not baseName then error("Unknown item base: " .. tostring(p.base) .. " (see search_item_bases)", 0) end
	local ilvl = p.item_level or 86
	local item = blankRare(baseName, ilvl)
	local _, byGroup = modPool(item, ilvl)
	local q = p.query and p.query:lower()
	local groups = {}
	for _, list in pairs(byGroup) do
		local top = list[1]
		local include = not p.mod_type or top.mod.type:lower() == p.mod_type:lower()
		if include and q then
			include = false
			for _, m in ipairs(list) do
				if modText(m.mod):lower():find(q, 1, true) or (m.mod.group or ""):lower():find(q, 1, true) then include = true break end
			end
		end
		if include then
			local tiers = {}
			for _, m in ipairs(list) do
				if m.allowed and #tiers < (p.tiers or 3) then
					tiers[#tiers + 1] = { id = m.id, tier = m.tier, level = m.mod.level, text = modText(m.mod) }
				end
			end
			if #tiers > 0 then
				groups[#groups + 1] = { group = top.mod.group, type = top.mod.type, tags = top.mod.modTags, tiers = tiers }
			end
		end
	end
	table.sort(groups, function(a, b)
		if a.type ~= b.type then return a.type < b.type end
		return (a.group or "") < (b.group or "")
	end)
	local crafted
	if p.include_crafted then
		crafted = {}
		for _, m in ipairs(B().data.masterMods or {}) do
			if m.types and m.types[item.type] and (not q or modText(m):lower():find(q, 1, true)) then
				crafted[#crafted + 1] = { type = m.type, text = modText(m), level = m.level }
			end
		end
	end
	return {
		base = baseName, itemLevel = ilvl,
		limits = { prefixes = item.affixLimit / 2, suffixes = item.affixLimit / 2 },
		note = "Use the tier ids in create_rare's `affixes`. Tier 1 is the best; tiers above the item level are left out.",
		groups = groups, crafted = crafted,
	}
end

-- "+(115-129) to maximum Life" -> Lua pattern capturing numbers, plus the ranges
local function templatePattern(tmpl)
	local ranges = {}
	local s = tmpl:gsub("%((%-?[%d%.]+)%-(%-?[%d%.]+)%)", function(a, b)
		ranges[#ranges + 1] = { tonumber(a), tonumber(b) }
		return "\1"
	end)
	s = s:gsub("[%^%$%(%)%%%.%[%]%*%+%-%?]", "%%%0")
	s = s:gsub("\1", "(%%-?[%%d%%.]+)")
	return "^" .. s .. "$", ranges
end

local function lineMatches(tmpl, line)
	local pat, ranges = templatePattern(tmpl)
	local caps = { line:match(pat) }
	if #caps == 0 then
		-- templates without ranges match literally
		return #ranges == 0 and tmpl:lower() == line:lower()
	end
	for i, r in ipairs(ranges) do
		local v = tonumber(caps[i])
		local lo, hi = math.min(r[1], r[2]), math.max(r[1], r[2])
		if not v or v < lo - 0.001 or v > hi + 0.001 then return false end
	end
	return true
end

-- Matches free-text mod lines to the item's mod pool.
-- Returns matched affixes {id, type, group, lines}, and the unmatched lines.
local function matchModLines(item, lines, ilvl)
	local pool = modPool(item, ilvl)
	table.sort(pool, function(a, b)
		if #a.mod ~= #b.mod then return #a.mod > #b.mod end
		return (a.mod.level or 0) > (b.mod.level or 0)
	end)
	local used, matched = {}, {}
	for _, m in ipairs(pool) do
		if m.allowed then
			local idx = {}
			for _, tmpl in ipairs(m.mod) do
				local found
				for i, line in ipairs(lines) do
					if not used[i] and not idx[i] and lineMatches(tmpl, line) then found = i break end
				end
				if not found then idx = nil break end
				idx[found] = true
			end
			if idx and next(idx) then
				local ls = {}
				for i in pairs(idx) do used[i] = true ls[#ls + 1] = lines[i] end
				matched[#matched + 1] = { id = m.id, type = m.mod.type, group = m.mod.group, lines = ls }
			end
		end
	end
	local unmatched = {}
	for i, line in ipairs(lines) do if not used[i] then unmatched[#unmatched + 1] = line end end
	return matched, unmatched
end

local function affixProblems(matched, limit)
	local problems, nP, nS, groups = {}, 0, 0, {}
	for _, m in ipairs(matched) do
		if m.type == "Prefix" then nP = nP + 1 else nS = nS + 1 end
		if groups[m.group] then problems[#problems + 1] = "two mods from the same group (" .. tostring(m.group) .. ")" end
		groups[m.group] = true
	end
	if nP > limit then problems[#problems + 1] = ("%d prefixes (max %d)"):format(nP, limit) end
	if nS > limit then problems[#problems + 1] = ("%d suffixes (max %d)"):format(nS, limit) end
	return problems, nP, nS
end

-------------------------------------------------------------------------------
-- Build creation: character, tree, skills, items
-------------------------------------------------------------------------------

local function findClass(name)
	local spec = B().spec
	local want = tostring(name):lower()
	for classId, class in pairs(spec.tree.classes) do
		if class.name:lower() == want then return classId, class end
	end
	return nil
end

local function findAscendancy(name)
	local spec = B().spec
	local want = tostring(name):lower()
	for classId, class in pairs(spec.tree.classes) do
		for ascId, asc in pairs(class.classes or {}) do
			if ascId ~= 0 and asc.name and asc.name:lower() == want then
				return classId, ascId, asc
			end
		end
	end
	return nil
end

local function selectClass(classId, notes)
	local spec = B().spec
	if spec.curClassId == classId then return end
	local used = spec:CountAllocNodes()
	if used > 0 and not spec:IsClassConnected(classId) then
		spec:ResetNodes()
		notes[#notes + 1] = "Passive tree reset (new class start is not connected to the old tree)"
	end
	spec:SelectClass(classId)
	spec:AddUndoState()
end

function API.list_classes()
	local spec = B().spec
	local res = {}
	for classId, class in pairs(spec.tree.classes) do
		local ascs = {}
		for ascId, asc in pairs(class.classes or {}) do
			if ascId ~= 0 and asc.name then ascs[#ascs + 1] = asc.name end
		end
		table.sort(ascs)
		res[#res + 1] = { id = classId, name = class.name, ascendancies = ascs,
			base = { str = class.base_str, dex = class.base_dex, int = class.base_int } }
	end
	table.sort(res, function(a, b) return a.id < b.id end)
	return { treeVersion = spec.treeVersion, classes = res }
end

function EDIT.set_class(e, notes)
	local classId, class = findClass(e.class_name)
	if not classId then error("Unknown class: " .. tostring(e.class_name) .. " (see list_classes)") end
	selectClass(classId, notes)
	notes[#notes + 1] = "Class set to " .. class.name
end

function EDIT.set_ascendancy(e, notes)
	local spec = B().spec
	local want = tostring(e.ascendancy):lower()
	if want == "none" or want == "" then
		spec:SelectAscendClass(0)
		spec:AddUndoState()
		notes[#notes + 1] = "Ascendancy cleared"
		return
	end
	local classId, ascId, asc = findAscendancy(e.ascendancy)
	if not classId then error("Unknown ascendancy: " .. tostring(e.ascendancy) .. " (see list_classes)") end
	if classId ~= spec.curClassId then
		selectClass(classId, notes)
		notes[#notes + 1] = "Class set to " .. spec.tree.classes[classId].name
	end
	spec:SelectAscendClass(ascId)
	spec:AddUndoState()
	notes[#notes + 1] = "Ascendancy set to " .. asc.name
end

-- Masteries ------------------------------------------------------------------

local function masteryOptions(node)
	local spec = B().spec
	local opts = {}
	for _, effect in ipairs(node.masteryEffects or {}) do
		local data = spec.tree.masteryEffects[effect.effect]
		local takenBy = isValueInTable(spec.masterySelections, effect.effect)
		opts[#opts + 1] = {
			effectId = effect.effect,
			text = table.concat(data and data.sd or effect.stats or {}, " / "),
			selected = spec.masterySelections[node.id] == effect.effect,
			available = (not takenBy) or takenBy == node.id,
		}
	end
	return opts
end

function API.list_mastery_effects(p)
	local spec = B().spec
	local res = {}
	if p.node then
		local node = resolveNode(p.node)
		if not node or node.type ~= "Mastery" then error("Not a mastery node: " .. tostring(p.node), 0) end
		res[1] = { id = node.id, name = node.dn, allocated = node.alloc or false,
			pathCost = node.path and #node.path or nil, effects = masteryOptions(node) }
	else
		-- Every mastery that can be allocated right now (adjacent to an allocated notable)
		local seen = {}
		for _, node in pairs(spec.nodes) do
			if node.type == "Mastery" and node.masteryEffects and #node.masteryEffects > 0
				and (node.alloc or (node.path and #node.path == 1)) and not seen[node.dn] then
				seen[node.dn] = true
				res[#res + 1] = { id = node.id, name = node.dn, allocated = node.alloc or false,
					effects = masteryOptions(node) }
			end
		end
		table.sort(res, function(a, b) return a.name < b.name end)
	end
	return { masteries = res }
end

function EDIT.set_mastery(e, notes)
	local spec = B().spec
	local node = resolveNode(e.node)
	if not node or node.type ~= "Mastery" then error("Not a mastery node: " .. tostring(e.node)) end
	if not node.alloc and not node.path then
		error(node.dn .. " is not reachable; allocate an adjacent notable first")
	end
	local chosen
	local wantId = tonumber(e.effect)
	local wantText = tostring(e.effect or ""):lower()
	for _, opt in ipairs(masteryOptions(node)) do
		if opt.effectId == wantId or (not wantId and opt.text:lower():find(wantText, 1, true)) then
			chosen = opt
			break
		end
	end
	if not chosen then error("No effect matching '" .. tostring(e.effect) .. "' on " .. node.dn .. " (see list_mastery_effects)") end
	if not chosen.available then error("That effect is already used on another " .. node.dn) end
	local effect = spec.tree.masteryEffects[chosen.effectId]
	node.sd = effect.sd
	node.allMasteryOptions = false
	spec.tree:ProcessStats(node)
	spec.masterySelections[node.id] = effect.id
	local cost = 0
	if not node.alloc then
		cost = #node.path
		spec:AllocNode(node)
	end
	spec:AddUndoState()
	notes[#notes + 1] = ("%s: %s%s"):format(node.dn, chosen.text,
		cost > 0 and (" (%d passive point%s)"):format(cost, cost == 1 and "" or "s") or "")
end

-- Skills ----------------------------------------------------------------------

function API.search_gems(p)
	local data = B().data
	local q = (p.query or ""):lower()
	local res = {}
	for gemId, gem in pairs(data.gems) do
		local ge = gem.grantedEffect
		if ge and not ge.legacy and (p.include_vaal or not gem.name:match("^Vaal ")) then
			local isSupport = ge.support and true or false
			if p.support == nil or p.support == isSupport then
				local tags = gem.tagString or ""
				local lname = gem.name:lower()
				-- 0 exact, 1 name prefix, 2 name contains, 3 tag match
				local rank = (q == "" and 2) or (lname == q or lname == q .. " support") and 0
					or (lname:sub(1, #q) == q and 1) or (lname:find(q, 1, true) and 2)
					or (tags:lower():find(q, 1, true) and 3) or nil
				local ok = rank ~= nil
				if ok and p.tags then
					for _, t in ipairs(p.tags) do
						if not tags:lower():find(t:lower(), 1, true) then ok = false break end
					end
				end
				if ok then
					res[#res + 1] = {
						rank = rank,
						name = gem.name, support = isSupport, tags = tags,
						maxLevel = gem.naturalMaxLevel,
						attribute = (gem.reqStr or 0) >= 50 and "str" or (gem.reqDex or 0) >= 50 and "dex"
							or (gem.reqInt or 0) >= 50 and "int" or "none",
						description = ge.description and cleanText(ge.description) or nil,
					}
				end
			end
		end
	end
	table.sort(res, function(a, b)
		if a.rank ~= b.rank then return a.rank < b.rank end
		return a.name < b.name
	end)
	for _, r in ipairs(res) do r.rank = nil end
	local total = #res
	local limit = p.limit or 30
	while #res > limit do table.remove(res) end
	return { total = total, gems = res }
end

function EDIT.create_socket_group(e, notes)
	local build = B()
	local skillsTab = build.skillsTab
	if e.slot and not build.itemsTab.slots[e.slot] then
		error("Unknown slot: " .. tostring(e.slot) .. " (see list_item_slots)")
	end
	local group = { label = e.label or "", enabled = e.enabled ~= false, slot = e.slot,
		includeInFullDPS = e.include_in_full_dps or false, gemList = {} }
	for _, name in ipairs(e.gems or {}) do
		group.gemList[#group.gemList + 1] = {
			nameSpec = normalizeGemName(name), level = 20, quality = e.quality or 20, qualityId = "Default",
			enabled = true, enableGlobal1 = true, enableGlobal2 = true, count = 1,
		}
	end
	table.insert(skillsTab.socketGroupList, group)
	local idx = #skillsTab.socketGroupList
	skillsTab:ProcessSocketGroup(group)
	for _, g in ipairs(group.gemList) do
		if g.errMsg then
			table.remove(skillsTab.socketGroupList, idx)
			error(("Gem '%s': %s"):format(tostring(g.nameSpec), cleanText(g.errMsg)))
		end
		-- Use the gem's normal max level rather than a blanket 20
		if g.gemData and not e.level then g.level = skillsTab:ProcessGemLevel(g.gemData) end
		if e.level then g.level = e.level end
	end
	skillsTab:ProcessSocketGroup(group)
	skillsTab:AddUndoState()
	if e.set_main or #skillsTab.socketGroupList == 1 then
		build.mainSocketGroup = idx
		notes[#notes + 1] = ("Created group %d (%s) and made it the main skill"):format(idx, socketGroupLabel(group))
	else
		notes[#notes + 1] = ("Created group %d (%s)"):format(idx, socketGroupLabel(group))
	end
end

function EDIT.delete_socket_group(e, notes)
	local build = B()
	local list = build.skillsTab.socketGroupList
	local idx = tonumber(e.group)
	local group = getGroup(idx)
	local label = socketGroupLabel(group)
	table.remove(list, idx)
	if build.mainSocketGroup == idx then build.mainSocketGroup = 1
	elseif build.mainSocketGroup > idx then build.mainSocketGroup = build.mainSocketGroup - 1 end
	build.skillsTab:AddUndoState()
	notes[#notes + 1] = ("Deleted group %d (%s); later groups shift up by one"):format(idx, label)
end

-- Items -------------------------------------------------------------------------

local function itemSlotType(item)
	return item.base and (item.base.type) or item.type
end

function API.search_uniques(p)
	local q = (p.query or ""):lower()
	local wantType = p.item_type and p.item_type:lower()
	local res = {}
	for _, item in pairs(main.uniqueDB.list) do
		local t = itemSlotType(item) or ""
		if not wantType or t:lower():find(wantType, 1, true) then
			local hit = q == "" or item.title:lower():find(q, 1, true) or item.baseName:lower():find(q, 1, true)
			local lines = {}
			for _, ml in ipairs(item.explicitModLines or {}) do
				lines[#lines + 1] = cleanText(ml.line)
				if not hit and p.search_mods and ml.line:lower():find(q, 1, true) then hit = true end
			end
			if hit then
				res[#res + 1] = { name = item.title, base = item.baseName, type = t,
					levelReq = item.requirements and item.requirements.level or nil,
					variants = item.variantList, mods = p.include_mods ~= false and lines or nil }
			end
		end
	end
	table.sort(res, function(a, b) return a.name < b.name end)
	local total = #res
	local limit = p.limit or 20
	while #res > limit do table.remove(res) end
	return { total = total, uniques = res }
end

function API.search_item_bases(p)
	local q = (p.query or ""):lower()
	local wantType = p.item_type and p.item_type:lower()
	local res = {}
	for name, base in pairs(B().data.itemBases) do
		local t = base.type or ""
		if (not wantType or t:lower():find(wantType, 1, true) or (base.subType or ""):lower():find(wantType, 1, true))
			and (q == "" or name:lower():find(q, 1, true)) and not base.hidden then
			local entry = { name = name, type = t, subType = base.subType, implicit = base.implicit,
				levelReq = base.req and base.req.level or nil }
			if base.armour then
				entry.defences = {}
				for k, v in pairs(base.armour) do
					if type(v) == "number" then entry.defences[k] = v end
				end
			end
			if base.weapon then
				entry.weapon = {}
				for k, v in pairs(base.weapon) do
					if type(v) == "number" then entry.weapon[k] = v end
				end
			end
			res[#res + 1] = entry
		end
	end
	table.sort(res, function(a, b)
		if (a.levelReq or 0) ~= (b.levelReq or 0) then return (a.levelReq or 0) > (b.levelReq or 0) end
		return a.name < b.name
	end)
	local total = #res
	local limit = p.limit or 25
	while #res > limit do table.remove(res) end
	return { total = total, bases = res }
end

function setFlaskActive(slotName, state)
	local itemsTab = B().itemsTab
	local slot = itemsTab.slots[slotName]
	if not slot then error("Unknown item slot: " .. tostring(slotName)) end
	slot.active = state
	if slot.controls and slot.controls.activate then slot.controls.activate.state = state end
	if itemsTab.activeItemSet and itemsTab.activeItemSet[slotName] then
		itemsTab.activeItemSet[slotName].active = state
	end
end

local function equipParsed(item, slotName, notes, what)
	local itemsTab = B().itemsTab
	local slot = itemsTab.slots[slotName]
	if not slot then error("Unknown item slot: " .. tostring(slotName) .. " (see list_item_slots)") end
	if slot.nodeId and not B().spec.nodes[slot.nodeId].alloc then
		error(slotName .. " is a tree jewel socket that is not allocated")
	end
	if not itemsTab:IsItemValidForSlot(item, slotName) then
		error(("%s can't go in %s"):format(item.name or "Item", slotName))
	end
	itemsTab:AddItem(item, true)
	slot:SetSelItemId(item.id)
	if slotName:match("^Flask %d$") then
		-- Flasks only count in PoB's calculations while their slot is switched on
		setFlaskActive(slotName, true)
	end
	itemsTab:PopulateSlots()
	itemsTab:AddUndoState()
	notes[#notes + 1] = ("Equipped %s%s in %s"):format(item.name or "item", what or "", slotName)
end

-- Error for an affix id that isn't in a base's pool; points to tier ids when a group name was given
local function badAffixError(id, baseName, pool)
	local tiers = {}
	for mid, m in pairs(pool) do
		if m.mod.group == id then tiers[#tiers + 1] = { id = mid, tier = m.tier } end
	end
	if #tiers > 0 then
		table.sort(tiers, function(a, b) return a.tier < b.tier end)
		local ids = {}
		for i = 1, math.min(3, #tiers) do ids[#ids + 1] = tiers[i].id end
		error(("%s is a mod group, not a mod id. Use a tier id, e.g. %s (see list_item_mods)"):format(id, table.concat(ids, ", ")), 0)
	end
	error(("%s can't roll on %s (see list_item_mods)"):format(id, baseName), 0)
end

function EDIT.create_flask(e, notes)
	local baseName, base = findBase(e.base)
	if not baseName or not base.flask then
		error("Unknown flask base: " .. tostring(e.base) .. " (see search_item_bases with item_type Flask)")
	end
	local itemsTab = B().itemsTab
	local slotName = e.slot
	if not slotName then
		for i = 1, 5 do
			local sl = itemsTab.slots["Flask " .. i]
			if sl and (not sl.selItemId or sl.selItemId == 0) then slotName = "Flask " .. i break end
		end
		slotName = slotName or "Flask 1"
	end
	local ilvl = e.item_level or 86
	local probe = new("Item"):Item(("Rarity: MAGIC\n%s\nItem Level: %d"):format(baseName, ilvl))
	local pool = {}
	for _, m in ipairs((modPool(probe, ilvl))) do pool[m.id] = m end
	local lines = { "Rarity: MAGIC", baseName, "Crafted: true" }
	local nP, nS = 0, 0
	for _, a in ipairs(e.affixes or {}) do
		local id, roll = tostring(a), 1
		if type(a) == "table" then id, roll = tostring(a.id), tonumber(a.roll) or 1 end
		local m = pool[id]
		if not m then badAffixError(id, baseName, pool) end
		if not m.allowed then error(("%s needs item level %d"):format(id, m.mod.level)) end
		if m.mod.type == "Prefix" then nP = nP + 1 else nS = nS + 1 end
		lines[#lines + 1] = ("%s: {range:%s}%s"):format(m.mod.type, tostring(roll), id)
	end
	if nP > 1 or nS > 1 then error("A magic flask has at most one prefix and one suffix") end
	lines[#lines + 1] = "Quality: " .. tostring(e.quality or 20)
	lines[#lines + 1] = "Item Level: " .. tostring(ilvl)
	local implicits = e.implicits or {}
	lines[#lines + 1] = "Implicits: " .. tostring(#implicits)
	for _, l in ipairs(implicits) do lines[#lines + 1] = l end
	local item = new("Item"):Item(table.concat(lines, "\n"))
	if #(e.affixes or {}) > 0 then item:Craft() end
	item:NormaliseQuality()
	item:BuildModList()
	equipParsed(item, slotName, notes)
	if e.enabled == false then setFlaskActive(slotName, false) end
end

function EDIT.set_flask_active(e, notes)
	local slotName = e.slot or (e.socket and ("Flask " .. tostring(e.socket)))
	if not slotName or not tostring(slotName):match("^Flask %d$") then error("set_flask_active needs slot 'Flask 1'..'Flask 5'") end
	setFlaskActive(slotName, e.enabled ~= false)
	notes[#notes + 1] = ("%s %s"):format(slotName, e.enabled ~= false and "switched on" or "switched off")
end

function EDIT.equip_unique(e, notes)
	local want = tostring(e.name):lower()
	local src = main.uniqueDB.byTitle[want]
	if not src then
		for _, item in pairs(main.uniqueDB.list) do
			if item.title:lower() == want or item.name:lower():find(want, 1, true) == 1 then src = item break end
		end
	end
	if not src then error("Unique not found: " .. tostring(e.name) .. " (see search_uniques)") end
	local item = new("Item"):Item(src:BuildRaw())
	local note = ""
	if e.variant and item.variantList then
		local vi = tonumber(e.variant)
		if not vi then
			for i, v in ipairs(item.variantList) do
				if v:lower():find(tostring(e.variant):lower(), 1, true) then vi = i break end
			end
		end
		if not vi or not item.variantList[vi] then error("Variant not found: " .. tostring(e.variant)) end
		item.variant = vi
		note = " (" .. item.variantList[vi] .. ")"
	end
	item:NormaliseQuality()
	item:BuildModList()
	equipParsed(item, e.slot, notes, note)
end

function EDIT.create_rare(e, notes)
	local data = B().data
	local baseName, base = findBase(e.base)
	if not baseName then error("Unknown item base: " .. tostring(e.base) .. " (see search_item_bases)") end
	local ilvl = e.item_level or 86
	local lines = { "Rarity: " .. (e.rarity or "RARE"), e.name or "Crafted Item", baseName }
	local hasAffixes = e.affixes and #e.affixes > 0
	if hasAffixes then
		-- Exact affixes by mod id (from list_item_mods), validated against the pool
		local probe = blankRare(baseName, ilvl)
		local pool = {}
		for _, m in ipairs(modPool(probe, ilvl)) do pool[m.id] = m end
		local limit = probe.affixLimit / 2
		local nP, nS, groups = 0, 0, {}
		lines[#lines + 1] = "Crafted: true"
		for _, a in ipairs(e.affixes) do
			local id = type(a) == "table" and (a.id or a.mod_id) or a
			local roll = type(a) == "table" and a.roll or 1
			local m = pool[id]
			if not m then badAffixError(id, baseName, pool) end
			if not m.allowed then error(("Mod %s needs item level %d"):format(id, m.mod.level)) end
			if groups[m.mod.group] then error("Two mods from the same group: " .. tostring(m.mod.group)) end
			groups[m.mod.group] = true
			if m.mod.type == "Prefix" then nP = nP + 1 else nS = nS + 1 end
			lines[#lines + 1] = ("%s: {range:%s}%s"):format(m.mod.type, tostring(roll), id)
		end
		if nP > limit or nS > limit then
			error(("Too many affixes: %d prefixes and %d suffixes (max %d each)"):format(nP, nS, limit))
		end
	end
	lines[#lines + 1] = "Item Level: " .. tostring(ilvl)
	if e.quality then lines[#lines + 1] = "Quality: " .. tostring(e.quality) end
	if e.sockets then lines[#lines + 1] = "Sockets: " .. e.sockets end
	local implicits = e.implicits
	if implicits == nil and base.implicit then
		implicits = {}
		for line in base.implicit:gmatch("[^\n]+") do implicits[#implicits + 1] = itemLib.applyRange(line, 1) end
	end
	lines[#lines + 1] = "Implicits: " .. tostring(#(implicits or {}))
	for _, l in ipairs(implicits or {}) do lines[#lines + 1] = l end
	for _, l in ipairs(e.mods or {}) do lines[#lines + 1] = l end
	for _, l in ipairs(e.crafted or {}) do lines[#lines + 1] = "{crafted}" .. l end
	local item = new("Item"):Item(table.concat(lines, "\n"))
	if not item.base then error("Could not build item from base " .. baseName) end
	if hasAffixes then item:Craft() end

	-- Mod lines PoB can't parse at all
	local unsupported = {}
	for _, ml in ipairs(item.explicitModLines or {}) do
		if ml.extra then unsupported[#unsupported + 1] = ml.line end
	end
	-- Free-text mods: check they can roll on this base, and affix limits
	local warnings = {}
	if e.mods and #e.mods > 0 then
		local matched, unmatched = matchModLines(blankRare(baseName, ilvl), e.mods, ilvl)
		local problems = affixProblems(matched, item.affixLimit > 0 and item.affixLimit / 2 or 3)
		if hasAffixes then
			local probe = blankRare(baseName, ilvl)
			local _, nP, nS = affixProblems(matched, 99)
			local tot = #e.affixes + nP + nS + #unmatched
			if tot > (probe.affixLimit or 6) then problems[#problems + 1] = ("%d explicit mods in total (max %d)"):format(tot, probe.affixLimit or 6) end
		end
		for _, line in ipairs(unmatched) do
			warnings[#warnings + 1] = "not a natural mod for this base/item level: " .. line
		end
		for _, pr in ipairs(problems) do warnings[#warnings + 1] = pr end
		if e.strict and #warnings > 0 then
			error("Item can't exist: " .. table.concat(warnings, "; ") .. ". Use list_item_mods to pick valid mods, or set strict=false.")
		end
	end
	if e.crafted and #e.crafted > 1 then warnings[#warnings + 1] = "only one crafted (bench) mod is allowed" end

	item:NormaliseQuality()
	item:BuildModList()
	equipParsed(item, e.slot, notes)
	if #unsupported > 0 then
		notes[#notes + 1] = "Warning: PoB does not understand these mod lines: " .. table.concat(unsupported, " | ")
	end
	if #warnings > 0 then
		notes[#notes + 1] = "Warning: this item may not be craftable: " .. table.concat(warnings, "; ")
	end
end

-- Greedy passive allocation ----------------------------------------------------

local function weightedScore(weights, bases, out)
	local score = 0
	for stat, w in pairs(weights) do
		local v = readStat(out, stat)
		local b = bases[stat]
		if v and b then
			if b ~= 0 then
				score = score + w * (v - b) / math.abs(b) * 100
			elseif v > 0 then
				score = score + w * 100
			end
		end
	end
	return score
end

function API.auto_allocate(p)
	local build = B()
	local spec = build.spec
	local weights = p.weights
	if not weights or next(weights) == nil then weights = { [defaultMetric()] = 1, TotalEHP = 0.5 } end
	local budget = p.points or (pointBudget() - spec:CountAllocNodes())
	local maxCost = p.max_path_cost or 6
	local shortlist = p.shortlist or 12
	local deadline = os.clock() + (p.max_seconds or 40)
	local chosen, spent, stopReason = {}, 0, nil
	local granted = {}
	local avoid = {}
	for _, n in ipairs(p.avoid or {}) do avoid[tostring(n):lower()] = true end
	-- Minimum targets, e.g. { PhysicalMaximumHitTaken = 10000 }: met first, then kept
	local targets = {}
	for stat, v in pairs(p.targets or {}) do targets[stat] = tonumber(v) end
	local targetsGivenUp = false

	-- Validate stats up front
	do
		local _, base = build.calcsTab:GetMiscCalculator()
		for _, tbl in ipairs({ weights, targets }) do
			for stat in pairs(tbl) do
				if readStat(base, stat) == nil then
					error(("Unknown or non-numeric stat '%s'"):format(stat), 0)
				end
			end
		end
	end

	-- Score while some target is unmet: progress toward the unmet targets (each one as a
	-- share of its remaining gap), with the weighted stats only as a tie-break. Once all
	-- targets are met: the weighted stats, and a candidate that breaks a target is rejected.
	local function unmetTargets(out)
		local list = {}
		for stat, t in pairs(targets) do
			local v = readStat(out, stat) or 0
			if v < t then list[stat] = v end
		end
		return list
	end
	local function scoreOut(bases, baseUnmet, out)
		local ws = weightedScore(weights, bases, out)
		if not targetsGivenUp and next(baseUnmet) then
			local ts = 0
			for stat, bv in pairs(baseUnmet) do
				local t = targets[stat]
				local v = math.min(readStat(out, stat) or 0, t)
				ts = ts + (v - bv) / (t - bv) * 100
			end
			return ts * 1000 + ws * 0.01, ts
		end
		for stat, t in pairs(targets) do
			if (bases[stat] or 0) >= t and (readStat(out, stat) or 0) < t then return -math.huge end
		end
		return ws
	end

	while spent < budget do
		if os.clock() > deadline then
			stopReason = "time limit reached; progress is kept, call auto_allocate again to continue"
			break
		end
		local calcFunc, base = build.calcsTab:GetMiscCalculator()
		granted = build.calcsTab.mainEnv.grantedPassives or {}
		local bases = {}
		for stat in pairs(weights) do bases[stat] = readStat(base, stat) end
		for stat in pairs(targets) do bases[stat] = readStat(base, stat) end
		local baseUnmet = unmetTargets(base)
		local remaining = budget - spent
		local useFull = weights.FullDPS ~= nil

		-- Pass 1: node on its own (cached by identical mods), normalised by path cost
		local cands, cache = {}, {}
		for _, node in pairs(spec.nodes) do
			if not node.alloc and node.path and #node.path > 0 and #node.path <= math.min(maxCost, remaining)
				and node.dn and node.modKey ~= "" and not granted[node.id] and not node.ascendancyName
				and (node.type == "Notable" or (p.include_keystones and node.type == "Keystone")
					or (p.include_small and node.type == "Normal"))
				and not avoid[node.dn:lower()] then
				local key = node.modKey
				if cache[key] == nil then
					cache[key] = scoreOut(bases, baseUnmet, calcFunc({ addNodes = { [node] = true } }, useFull))
				end
				cands[#cands + 1] = { node = node, est = cache[key] / #node.path }
			end
		end
		if #cands == 0 then stopReason = "no reachable candidates within budget" break end
		table.sort(cands, function(a, b) return a.est > b.est end)

		-- Pass 2: exact value of the whole path for the shortlist
		local best, bestScore, bestTarget
		for i = 1, math.min(shortlist, #cands) do
			local node = cands[i].node
			local set = {}
			for _, pn in ipairs(node.path) do set[pn] = true end
			local sc, ts = scoreOut(bases, baseUnmet, calcFunc({ addNodes = set }, useFull))
			sc = sc / #node.path
			if not bestScore or sc > bestScore then best, bestScore, bestTarget = node, sc, ts end
		end
		if not targetsGivenUp and next(baseUnmet) and (not best or (bestTarget or 0) <= 0) then
			-- Nothing on the tree moves the unmet targets: carry on with the weights only
			targetsGivenUp = true
			goto continue
		end
		if not best or bestScore <= 0 then stopReason = "no remaining node improves the weighted stats" break end

		local cost = #best.path
		spec:AllocNode(best)
		spent = spent + cost
		rebuild()
		chosen[#chosen + 1] = { name = best.dn, type = best.type, cost = cost,
			forTargets = (not targetsGivenUp and next(baseUnmet) ~= nil) or nil,
			scorePerPoint = math.floor(bestScore * 100 + 0.5) / 100 }
		::continue::
	end
	spec:AddUndoState()

	local after = {}
	for stat in pairs(weights) do after[stat] = readStat(output(), stat) end
	local res = { weights = weights, pointsBudget = budget, pointsSpent = spent,
		allocated = chosen, stoppedBecause = stopReason or "budget used", stats = after, info = info() }
	if next(targets) then
		local status = {}
		for stat, t in pairs(targets) do
			local v = readStat(output(), stat) or 0
			status[stat] = { target = t, value = num(v), met = v >= t }
			after[stat] = num(v)
		end
		res.targets = status
		local anyUnmet = false
		for _, st in pairs(status) do if not st.met then anyUnmet = true end end
		if targetsGivenUp then
			res.targetNote = "No passive node within reach of the remaining points moves the unmet targets, so those points went to the weights. "
				.. "Close the gap with gear, gems, flasks or config (or a larger max_path_cost)."
		elseif anyUnmet then
			res.targetNote = "The point budget ran out before every target was met. Close the gap with gear, gems, flasks or config, or free points elsewhere."
		end
	end
	return res
end

-------------------------------------------------------------------------------
-- Tree routing: condensed map, route alternatives, exact path allocation
-------------------------------------------------------------------------------

-- Same traversal rules as PoB's own pathing (PassiveSpec:BuildAllDependsAndPaths):
-- no passing through class starts, no continuing out of a mastery, no crossing
-- between the main tree and an ascendancy.
local function canStep(from, to, fromDist)
	return from.type ~= "Mastery"
		and to.type ~= "ClassStart" and to.type ~= "AscendClassStart"
		and (from.ascendancyName == to.ascendancyName or ((fromDist or 1) == 0 and not to.ascendancyName))
end

local KEY_TYPES = { Notable = true, Keystone = true, Socket = true, ClassStart = true }

local function isMainTree(node)
	-- Includes passives added by socketed cluster jewels; excludes the placeholder
	-- "proxy" nodes PoB keeps for empty cluster sockets
	return not node.ascendancyName and not node.isProxy
end

-- "+10 to Strength", "+10 to Strength" -> "2x +10 to Strength"
local function summariseStats(nodes)
	local counts, order = {}, {}
	for _, n in ipairs(nodes) do
		for _, line in ipairs(n.sd or {}) do
			if not counts[line] then order[#order + 1] = line counts[line] = 0 end
			counts[line] = counts[line] + 1
		end
	end
	local out = {}
	for _, line in ipairs(order) do
		out[#out + 1] = counts[line] > 1 and (counts[line] .. "x " .. line) or line
	end
	return out
end

local function adjacentMastery(node)
	for _, l in ipairs(node.linked or {}) do
		if l.type == "Mastery" then return l end
	end
end

-- Unit-cost BFS distance (in passive points) from a set of start nodes, main tree only
local function bfsDistances(starts, maxDist)
	local dist, queue, head = {}, {}, 1
	for _, n in ipairs(starts) do dist[n.id] = 0 queue[#queue + 1] = n end
	while head <= #queue do
		local n = queue[head]; head = head + 1
		local d = dist[n.id]
		if d < maxDist then
			for _, o in ipairs(n.linked or {}) do
				if not dist[o.id] and isMainTree(o) and o.type ~= "Mastery" and n.type ~= "Mastery" then
					dist[o.id] = d + 1
					queue[#queue + 1] = o
				end
			end
		end
	end
	return dist
end

function API.tree_map(p)
	local spec = B().spec
	-- Whole tree defaults to names only (~13k tokens); a region defaults to full stats
	local detail = p.detail or ((p.center or p.radius) and "stats" or "names")
	local withStats = detail ~= "names"

	-- Region filter
	local inRegion
	if p.center or p.radius then
		local starts = {}
		if p.center then
			local c = resolveNode(p.center)
			if not c then error("Node not found: " .. tostring(p.center), 0) end
			starts[1] = c
		else
			for _, n in pairs(spec.allocNodes) do
				if isMainTree(n) then starts[#starts + 1] = n end
			end
		end
		inRegion = bfsDistances(starts, p.radius or 10)
	end

	-- Graph contraction. Vertices: notables, keystones, jewel sockets, your class start,
	-- and small passives where paths branch or end (junctions). Edges: unbranched chains
	-- of small passives between two vertices.
	local myStart = spec.curClass and spec.nodes[spec.curClass.startNodeId]
	local function usable(n)
		if not isMainTree(n) or n.type == "Mastery" then return false end
		if n.type == "ClassStart" then return n == myStart end
		return true
	end
	local function nbs(n)
		local t = {}
		for _, o in ipairs(n.linked or {}) do if usable(o) then t[#t + 1] = o end end
		return t
	end
	local isVertex = {}
	for id, node in pairs(spec.nodes) do
		if usable(node) and (KEY_TYPES[node.type] or #nbs(node) ~= 2) then isVertex[id] = true end
	end

	local vlist = {}
	for id in pairs(isVertex) do
		local node = spec.nodes[id]
		if not inRegion or inRegion[id] then
			local vtype = node.type == "Normal" and "Junction" or node.type
			local v = { id = id, name = node.dn, type = vtype, allocated = node.alloc or nil }
			if withStats and node.type ~= "ClassStart" then v.stats = node.sd end
			local m = adjacentMastery(node)
			if m then v.mastery = m.dn end
			if inRegion then v.distance = inRegion[id] end
			if node.path and not node.alloc then v.pathCost = #node.path end
			vlist[#vlist + 1] = v
		end
	end

	local edges, seen = {}, {}
	for id in pairs(isVertex) do
		local v = spec.nodes[id]
		if not inRegion or inRegion[id] then
			for _, first in ipairs(nbs(v)) do
				local chain, prev, cur = {}, v, first
				local guard = 0
				while cur and not isVertex[cur.id] and guard < 200 do
					chain[#chain + 1] = cur
					local nextNode
					for _, o in ipairs(nbs(cur)) do if o ~= prev then nextNode = o break end end
					prev, cur = cur, nextNode
					guard = guard + 1
				end
				if cur and cur.id ~= id and (not inRegion or inRegion[cur.id]) then
					local ids = {}
					for _, c in ipairs(chain) do ids[#ids + 1] = c.id end
					table.sort(ids)
					local a, b = math.min(id, cur.id), math.max(id, cur.id)
					local k = a .. ":" .. b .. ":" .. table.concat(ids, ",")
					if not seen[k] then
						seen[k] = true
						local allocCount = 0
						for _, c in ipairs(chain) do if c.alloc then allocCount = allocCount + 1 end end
						local edge = { a = a, b = b, smallNodes = #chain }
						if allocCount > 0 then edge.allocatedSmall = allocCount end
						if withStats and #chain > 0 then edge.stats = summariseStats(chain) end
						if detail == "full" and #chain > 0 then
							edge.smallIds = {}
							-- keep walking order from a to b
							local ordered = {}
							for _, c in ipairs(chain) do ordered[#ordered + 1] = c.id end
							if id ~= a then
								for i = 1, math.floor(#ordered / 2) do
									ordered[i], ordered[#ordered - i + 1] = ordered[#ordered - i + 1], ordered[i]
								end
							end
							edge.smallIds = ordered
						end
						edges[#edges + 1] = edge
					end
				end
			end
		end
	end
	table.sort(vlist, function(a, b) return a.id < b.id end)
	table.sort(edges, function(x, y) if x.a ~= y.a then return x.a < y.a end return x.b < y.b end)

	if p.format ~= "json" then
		-- Compact text: one line per vertex / edge
		local letter = { Notable = "N", Keystone = "K", Socket = "S", Junction = "J", ClassStart = "C" }
		local vl, el = {}, {}
		for _, v in ipairs(vlist) do
			local line = ("%d %s%s %s"):format(v.id, letter[v.type] or "?", v.allocated and "*" or "", v.name or "")
			if v.pathCost then line = line .. " +" .. v.pathCost end
			if v.mastery then line = line .. " [" .. v.mastery .. "]" end
			if v.stats and #v.stats > 0 then line = line .. " | " .. table.concat(v.stats, "; ") end
			vl[#vl + 1] = line
		end
		for _, e in ipairs(edges) do
			local line = ("%d-%d %d"):format(e.a, e.b, e.smallNodes)
			if e.allocatedSmall then line = line .. " (" .. e.allocatedSmall .. " alloc)" end
			if e.stats then line = line .. " | " .. table.concat(e.stats, "; ") end
			if e.smallIds then line = line .. " ids:" .. table.concat(e.smallIds, ",") end
			el[#el + 1] = line
		end
		return {
			legend = "VERTICES: '<id> <type><*=allocated> <name> +<points to reach from current tree> "
				.. "[adjacent mastery] | stats'. Types: N notable, K keystone, S jewel socket, J junction "
				.. "(small passive where paths branch), C your class start. EDGES: '<a>-<b> <n> | stats' = "
				.. "a and b are joined by an unbranched chain of n small passives (0 = adjacent) granting "
				.. "those stats. Each unallocated node costs 1 point. Allocate with allocate_path "
				.. "(ids) or get exact routes from find_routes.",
			region = inRegion and { center = p.center or "allocated tree", radius = p.radius or 10 } or "whole tree",
			vertexCount = #vlist, edgeCount = #edges,
			vertices = table.concat(vl, "\n"),
			edges = table.concat(el, "\n"),
		}
	end
	return {
		legend = "Vertices are notables, keystones, jewel sockets, your class start, and 'Junction' "
			.. "small passives where paths branch. An edge a-b is an unbranched chain of `smallNodes` "
			.. "small passives (0 = directly adjacent); `stats` is what that chain grants. Crossing an "
			.. "edge to an unallocated vertex costs smallNodes + 1 points, minus allocatedSmall. "
			.. "Every vertex and small passive costs 1 point; allocated ones are free.",
		region = inRegion and { center = p.center or "allocated tree", radius = p.radius or 10 } or "whole tree",
		vertexCount = #vlist, edgeCount = #edges,
		vertices = vlist, edges = edges,
	}
end

-- Minimal binary heap keyed on .d
local function heapPush(h, item)
	h[#h + 1] = item
	local i = #h
	while i > 1 do
		local parent = math.floor(i / 2)
		if h[parent].d <= h[i].d then break end
		h[parent], h[i] = h[i], h[parent]
		i = parent
	end
end
local function heapPop(h)
	local top = h[1]
	local last = table.remove(h)
	if #h > 0 then
		h[1] = last
		local i = 1
		while true do
			local l, r, s = i * 2, i * 2 + 1, i
			if h[l] and h[l].d < h[s].d then s = l end
			if h[r] and h[r].d < h[s].d then s = r end
			if s == i then break end
			h[s], h[i] = h[i], h[s]
			i = s
		end
	end
	return top
end

-- Cheapest path from any "free" node (allocated or already chosen) to target.
-- Node costs: 0 if free, else 1 + penalty. Returns the paid nodes, tree side first.
local function dijkstra(target, free, penalty, avoid)
	local spec = B().spec
	local dist, prev, h = {}, {}, {}
	for id, n in pairs(free) do
		dist[id] = 0
		heapPush(h, { d = 0, n = n })
	end
	while #h > 0 do
		local item = heapPop(h)
		local n, d = item.n, item.d
		if d == dist[n.id] then
			if n == target then break end
			for _, o in ipairs(n.linked or {}) do
				if not avoid[o.id] and canStep(n, o, free[n.id] and 0 or 1) then
					local cost = free[o.id] and 0 or (1 + (penalty[o.id] or 0))
					local nd = d + cost
					if nd < (dist[o.id] or math.huge) then
						dist[o.id] = nd
						prev[o.id] = n
						heapPush(h, { d = nd, n = o })
					end
				end
			end
		end
	end
	if not dist[target.id] then return nil end
	local path, cur = {}, target
	while cur and not free[cur.id] do
		table.insert(path, 1, cur)
		cur = prev[cur.id]
	end
	return path
end

local function routeThrough(stops, avoid, penalty)
	local spec = B().spec
	local free = {}
	for id, n in pairs(spec.allocNodes) do free[id] = n end
	local all = {}
	for _, stop in ipairs(stops) do
		if not free[stop.id] then
			local seg = dijkstra(stop, free, penalty, avoid)
			if not seg then return nil, stop end
			for _, n in ipairs(seg) do
				all[#all + 1] = n
				free[n.id] = n
			end
		end
	end
	return all
end

local function describeRoute(nodes)
	local picks, smalls, ids = {}, {}, {}
	for _, n in ipairs(nodes) do
		ids[#ids + 1] = n.id
		if n.type == "Normal" then smalls[#smalls + 1] = n
		else picks[#picks + 1] = { id = n.id, name = n.dn, type = n.type } end
	end
	return { cost = #nodes, nodeIds = ids, keyNodes = picks, smallNodeStats = summariseStats(smalls) }
end

function API.find_routes(p)
	local build = B()
	local spec = build.spec
	local target = resolveNode(p.target)
	if not target then error("Node not found: " .. tostring(p.target), 0) end
	if target.alloc then error(target.dn .. " is already allocated", 0) end

	local stops = {}
	for _, v in ipairs(p.via or {}) do
		local n = resolveNode(v)
		if not n then error("Via node not found: " .. tostring(v), 0) end
		stops[#stops + 1] = n
	end
	stops[#stops + 1] = target
	local avoid = {}
	for _, v in ipairs(p.avoid or {}) do
		local n = resolveNode(v)
		if not n then error("Avoid node not found: " .. tostring(v), 0) end
		if n == target then error("Can't avoid the target", 0) end
		avoid[n.id] = true
	end

	local want = p.alternatives or 3
	local maxExtra = p.max_extra_points or 4
	local penalty, routes, keys = {}, {}, {}
	local shortest
	for attempt = 1, want * 4 do
		local nodes, blocked = routeThrough(stops, avoid, penalty)
		if not nodes then
			if attempt == 1 then
				error(("No route to %s (blocked by the avoid list or not reachable from the tree)"):format(blocked.dn), 0)
			end
			break
		end
		local cost = #nodes
		shortest = shortest or cost
		if cost > shortest + maxExtra then break end
		local idsSorted = {}
		for _, n in ipairs(nodes) do idsSorted[#idsSorted + 1] = n.id end
		table.sort(idsSorted)
		local key = table.concat(idsSorted, ",")
		if not keys[key] then
			keys[key] = true
			routes[#routes + 1] = nodes
			if #routes >= want then break end
		end
		-- Discourage reusing this route's nodes so the next search finds a different one
		for _, n in ipairs(nodes) do
			local isStop = false
			for _, s in ipairs(stops) do if s == n then isStop = true end end
			if not isStop then penalty[n.id] = (penalty[n.id] or 0) + 1.5 end
		end
	end

	-- Optional evaluation with PoB's calculator
	local metrics = p.metrics
	if (not metrics or #metrics == 0) and p.evaluate ~= false then
		metrics = { defaultMetric(), "TotalEHP", "Life" }
	end
	local calcFunc, base
	if p.evaluate ~= false then calcFunc, base = build.calcsTab:GetMiscCalculator() end

	local out = {}
	for i, nodes in ipairs(routes) do
		local r = describeRoute(nodes)
		r.route = i
		r.extraPointsVsShortest = r.cost - shortest
		if calcFunc then
			local set = {}
			for _, n in ipairs(nodes) do set[n] = true end
			local res = calcFunc({ addNodes = set }, metrics[1] == "FullDPS")
			r.statChanges = {}
			for _, m in ipairs(metrics) do
				local b, a = readStat(base, m), readStat(res, m)
				if a and b then
					r.statChanges[m] = { change = a - b, pct = b ~= 0 and (a - b) / math.abs(b) * 100 or nil }
				end
			end
		end
		out[#out + 1] = r
	end
	return {
		target = { id = target.id, name = target.dn, type = target.type },
		via = #stops > 1 and (function() local t = {} for i = 1, #stops - 1 do t[i] = stops[i].dn end return t end)() or nil,
		routes = out,
		note = "Allocate a route with an allocate_path edit using its nodeIds.",
	}
end

function EDIT.allocate_path(e, notes)
	local spec = B().spec
	local want = {}
	for _, ref in ipairs(e.nodes or {}) do
		local n = resolveNode(ref)
		if not n then error("Passive node not found: " .. tostring(ref)) end
		if n.type == "Mastery" then error(n.dn .. " is a mastery; use set_mastery for it") end
		if n.type == "ClassStart" or n.type == "AscendClassStart" then error("Can't allocate a start node: " .. n.dn) end
		if not n.alloc then want[#want + 1] = n end
	end
	if #want == 0 then notes[#notes + 1] = "All listed nodes were already allocated" return end

	-- Grow from the allocated tree; every listed node must connect via allocated
	-- nodes or other listed nodes, using PoB's traversal rules.
	local connected, remaining = {}, {}
	for id in pairs(spec.allocNodes) do connected[id] = true end
	for _, n in ipairs(want) do remaining[n.id] = n end
	local order = {}
	local progress = true
	while progress do
		progress = false
		for id, n in pairs(remaining) do
			for _, o in ipairs(n.linked or {}) do
				if connected[o.id] and canStep(o, n, 0) then
					connected[id] = true
					remaining[id] = nil
					order[#order + 1] = n
					progress = true
					break
				end
			end
		end
	end
	local bad = {}
	for _, n in pairs(remaining) do bad[#bad + 1] = ("%s (%d)"):format(n.dn, n.id) end
	if #bad > 0 then
		error("These nodes don't connect to the tree or to the other listed nodes: " .. table.concat(bad, ", ")
			.. ". Add the nodes in between (find_routes gives complete routes).")
	end

	local rebuildClusters = false
	for _, n in ipairs(order) do
		rebuildClusters = rebuildClusters or n.expansionJewel ~= nil
		n.alloc = true
		spec.allocNodes[n.id] = n
		if n.isMultipleChoiceOption then
			for _, opt in ipairs(n.linked[1].linked) do
				if opt.isMultipleChoiceOption and opt.alloc and opt ~= n then
					opt.alloc = false
					spec.allocNodes[opt.id] = nil
				end
			end
		end
	end
	if rebuildClusters then spec:BuildClusterJewelGraphs() else spec:BuildAllDependsAndPaths() end
	spec:AddUndoState()
	local named = {}
	for _, n in ipairs(order) do if n.type ~= "Normal" then named[#named + 1] = n.dn end end
	notes[#notes + 1] = ("Allocated %d node%s along the given path%s"):format(#order, #order == 1 and "" or "s",
		#named > 0 and (" incl. " .. table.concat(named, ", ")) or "")
end

-------------------------------------------------------------------------------
-- Shared helpers for temporary changes
-------------------------------------------------------------------------------

-- Runs fn() and then restores the build exactly as it was.
local function withRestore(fn)
	local build = B()
	local snapshot = build:SaveDB("code")
	local name = build.buildName
	local ok, res = pcall(fn)
	main:SetMode("BUILD", false, name, snapshot)
	frame()
	if not ok then error(res, 0) end
	return res
end

local function resistCap(elem)
	local env = B().calcsTab.mainEnv
	local cap = env and env.player.modDB:Sum("BASE", nil, elem .. "ResistMax")
	if not cap or cap == 0 then cap = 75 end
	return math.min(cap, 90)
end

-------------------------------------------------------------------------------
-- 1. Item mod pools and valid crafted rares
-------------------------------------------------------------------------------

-------------------------------------------------------------------------------
-- 2. Resist and attribute gap fixing
-------------------------------------------------------------------------------

local RES_ELEMS = { "Fire", "Cold", "Lightning" }
local ATTRS = { Str = "Strength", Dex = "Dexterity", Int = "Intelligence" }

local function currentGaps(p)
	local out = output()
	local gaps = {}
	for _, elem in ipairs(RES_ELEMS) do
		local cap = resistCap(elem)
		local total = out[elem .. "ResistTotal"] or out[elem .. "Resist"] or 0
		if total < cap then gaps[elem] = cap - total end
	end
	if p.chaos_target then
		local total = out.ChaosResistTotal or out.ChaosResist or 0
		if total < p.chaos_target then gaps.Chaos = p.chaos_target - total end
	end
	if p.attributes ~= false then
		for short in pairs(ATTRS) do
			local have, need = out[short] or 0, out["Req" .. short] or 0
			if need > have then gaps[short] = need - have end
		end
	end
	return gaps
end

-- What a mod line gives toward the gaps
local function lineContribution(line)
	local c = {}
	local l = line:lower()
	local v = tonumber(l:match("^%+(%d+)"))
	if not v then return c end
	if l:find("to all elemental resistances") then
		c.Fire, c.Cold, c.Lightning = v, v, v
	elseif l:find("resistance") then
		for _, elem in ipairs({ "Fire", "Cold", "Lightning", "Chaos" }) do
			if l:find(elem:lower()) then c[elem] = v end
		end
	elseif l:find("to all attributes") then
		c.Str, c.Dex, c.Int = v, v, v
	else
		for short, long in pairs(ATTRS) do
			if l:find(long:lower()) then c[short] = v end
		end
	end
	return c
end

local GAP_SLOTS = { "Helmet", "Body Armour", "Gloves", "Boots", "Amulet", "Ring 1", "Ring 2", "Belt", "Weapon 2" }

local function planGearGaps(gaps, roll, notes)
	local build = B()
	local itemsTab = build.itemsTab
	local edits = {}
	-- Collect rares with free suffixes and their candidate suffix mods
	local slots = {}
	for _, slotName in ipairs(GAP_SLOTS) do
		local slot = itemsTab.slots[slotName]
		local item = slot and slot.selItemId ~= 0 and itemsTab.items[slot.selItemId]
		if item and item.rarity == "RARE" and item.base and item.affixes then
			local lines = {}
			for _, ml in ipairs(item.explicitModLines or {}) do
				if not ml.crafted and not ml.implicit then lines[#lines + 1] = ml.line end
			end
			local matched, unmatched = matchModLines(item, lines, item.itemLevel or 86)
			local total = (item.affixLimit and item.affixLimit > 0) and item.affixLimit or 6
			local limit = total / 2
			local _, nP, nS = affixProblems(matched, limit)
			local free = math.min(limit - nS, total - nP - nS - #unmatched)
			if free > 0 then
				local usedGroups = {}
				for _, m in ipairs(matched) do usedGroups[m.group] = true end
				local cands = {}
				for _, m in ipairs(modPool(item, item.itemLevel or 86)) do
					if m.allowed and m.tier and m.mod.type == "Suffix" and #m.mod == 1 and not usedGroups[m.mod.group] then
						local line = itemLib.applyRange(m.mod[1], roll)
						local c = lineContribution(line)
						if next(c) then cands[#cands + 1] = { id = m.id, group = m.mod.group, line = line, contrib = c, tier = m.tier } end
					end
				end
				slots[#slots + 1] = { name = slotName, item = item, free = free, cands = cands, added = {}, usedGroups = usedGroups }
			end
		end
	end
	-- Greedy: the mod that covers the most remaining gap
	while next(gaps) do
		local best, bestSlot, bestCover
		for _, s in ipairs(slots) do
			if s.free > 0 then
				for _, c in ipairs(s.cands) do
					if not s.usedGroups[c.group] then
						local cover = 0
						for k, v in pairs(c.contrib) do
							if gaps[k] then cover = cover + math.min(v, gaps[k]) end
						end
						-- prefer higher tiers on ties
						cover = cover - c.tier * 0.01
						if cover > 0 and (not bestCover or cover > bestCover) then best, bestSlot, bestCover = c, s, cover end
					end
				end
			end
		end
		if not best then break end
		bestSlot.free = bestSlot.free - 1
		bestSlot.usedGroups[best.group] = true
		table.insert(bestSlot.added, best.line)
		for k, v in pairs(best.contrib) do
			if gaps[k] then
				gaps[k] = gaps[k] - v
				if gaps[k] <= 0 then gaps[k] = nil end
			end
		end
	end
	for _, s in ipairs(slots) do
		if #s.added > 0 then
			local raw = s.item:BuildRaw()
			for _, line in ipairs(s.added) do raw = raw .. "\n" .. line end
			edits[#edits + 1] = { op = "equip_item", slot = s.name, item_text = raw }
			notes[#notes + 1] = ("%s: add %s"):format(s.name, table.concat(s.added, ", "))
		end
	end
	return edits
end

local function planTreeGaps(gaps, maxCost, notes)
	local build = B()
	local spec = build.spec
	local edits = {}
	local keywords = { "resist", "strength", "dexterity", "intelligence", "attributes" }
	for _ = 1, 30 do
		if not next(gaps) then break end
		local calcFunc, base = build.calcsTab:GetMiscCalculator()
		local best, bestScore
		for _, node in pairs(spec.nodes) do
			if not node.alloc and node.path and #node.path > 0 and #node.path <= maxCost
				and node.type ~= "Mastery" and not node.ascendancyName and node.sd then
				local text = table.concat(node.sd, " "):lower()
				local relevant = false
				for _, k in ipairs(keywords) do if text:find(k, 1, true) then relevant = true break end end
				if relevant then
					local set = {}
					for _, pn in ipairs(node.path) do set[pn] = true end
					local out = calcFunc({ addNodes = set })
					local cover = 0
					for k, need in pairs(gaps) do
						local key = (k == "Str" or k == "Dex" or k == "Int") and k or (k .. "ResistTotal")
						local gain = (out[key] or 0) - (base[key] or 0)
						if gain > 0 then cover = cover + math.min(gain, need) end
					end
					local sc = cover / #node.path
					if sc > 0 and (not bestScore or sc > bestScore) then best, bestScore = node, sc end
				end
			end
		end
		if not best then break end
		local ids = {}
		for _, pn in ipairs(best.path) do ids[#ids + 1] = pn.id end
		spec:AllocNode(best)
		rebuild()
		edits[#edits + 1] = { op = "allocate_path", nodes = ids }
		notes[#notes + 1] = ("Tree: %s (%d point%s)"):format(best.dn, #ids, #ids == 1 and "" or "s")
		local fresh = currentGaps({ chaos_target = gaps.Chaos and (output().ChaosResistTotal or 0) + gaps.Chaos or nil })
		for k in pairs(gaps) do gaps[k] = fresh[k] end
	end
	return edits
end

function API.fix_gaps(p)
	local before = currentGaps(p)
	if not next(before) then return { gapsBefore = {}, edits = {}, note = "Nothing to fix: resists capped and attributes met" } end
	local mode = p.mode or "both"
	local roll = p.roll or 0.75
	local maxCost = p.max_tree_cost or 3
	return withRestore(function()
		local statsBefore = pickStats(SUMMARY_KEYS)
		local notes, edits = {}, {}
		local gaps = {}
		for k, v in pairs(before) do gaps[k] = v end
		if mode == "gear" or mode == "both" then
			for _, e in ipairs(planGearGaps(gaps, roll, notes)) do edits[#edits + 1] = e end
			if #edits > 0 then applyEdits(edits) end
			gaps = currentGaps(p)
		end
		if (mode == "tree" or mode == "both") and next(gaps) then
			for _, e in ipairs(planTreeGaps(gaps, maxCost, notes)) do edits[#edits + 1] = e end
		end
		local after = currentGaps(p)
		return {
			gapsBefore = before, gapsAfter = after, edits = edits, plan = notes,
			changes = diff(statsBefore, pickStats(SUMMARY_KEYS), SUMMARY_KEYS),
			note = #edits > 0 and "Preview only. Pass these edits to apply_changes, or call fix_gaps with apply=true."
				or "No free suffixes on rare gear and no cheap tree nodes found.",
		}
	end)
end

-------------------------------------------------------------------------------
-- 3. Trade stat weights for a slot
-------------------------------------------------------------------------------

local PSEUDO = {
	["3372524247"] = "pseudo.pseudo_total_fire_resistance",
	["4220027924"] = "pseudo.pseudo_total_cold_resistance",
	["1671376347"] = "pseudo.pseudo_total_lightning_resistance",
	["2923486259"] = "pseudo.pseudo_total_chaos_resistance",
	["4080418644"] = "pseudo.pseudo_total_strength",
	["3261801346"] = "pseudo.pseudo_total_dexterity",
	["328541901"] = "pseudo.pseudo_total_intelligence",
}

function API.trade_weights(p)
	local build = B()
	local itemsTab = build.itemsTab
	local slot = itemsTab.slots[p.slot]
	if not slot then error("Unknown slot: " .. tostring(p.slot), 0) end
	local current = slot.selItemId ~= 0 and itemsTab.items[slot.selItemId] or nil
	local baseName = p.base and findBase(p.base) or (current and current.baseName)
	if not baseName then error("Slot is empty; pass `base` to choose an item base", 0) end
	local function calcItem(extraLine)
		local raw = ("Rarity: RARE\nTest\n%s\nItem Level: 86\nImplicits: 0"):format(baseName)
		if extraLine then raw = raw .. "\n" .. extraLine end
		local it = new("Item"):Item(raw)
		it:BuildModList()
		return it
	end
	local blank = calcItem()
	local weights = p.weights
	if not weights or next(weights) == nil then weights = { [defaultMetric()] = 1, TotalEHP = 0.5 } end
	local calcFunc = build.calcsTab:GetMiscCalculator()
	local baseOut = calcFunc({ repSlotName = p.slot, repItem = blank })
	local bases = {}
	for stat in pairs(weights) do bases[stat] = readStat(baseOut, stat) end

	local results = {}
	local seen = {}
	local _, byGroup = modPool(blankRare(baseName, 86), 86)
	local bestPerGroup = {}
	for _, list in pairs(byGroup) do
		for _, m in ipairs(list) do
			if m.allowed then bestPerGroup[#bestPerGroup + 1] = m break end
		end
	end
	for _, m in ipairs(bestPerGroup) do
		if #m.mod == 1 and m.mod.tradeHashes and not seen[m.mod.group] then
			do
				local hash
				for h in pairs(m.mod.tradeHashes) do hash = tostring(h) end
				local line = itemLib.applyRange(m.mod[1], 1)
				local value = tonumber(line:match("([%d%.]+)"))
				if hash and value and value > 0 then
					seen[m.mod.group] = true
					local out = calcFunc({ repSlotName = p.slot, repItem = calcItem(line) })
					local score = 0
					for stat, w in pairs(weights) do
						local b, a = bases[stat], readStat(out, stat)
						if a and b then
							if b ~= 0 then score = score + w * (a - b) / math.abs(b) * 100
							elseif a > 0 then score = score + w * 100 end
						end
					end
					if score > 0.001 then
						results[#results + 1] = {
							tradeId = PSEUDO[hash] or ("explicit.stat_" .. hash),
							text = m.mod[1], exampleValue = value,
							weightPerUnit = score / value, scoreAtExample = score,
						}
					end
				end
			end
		end
	end
	table.sort(results, function(a, b) return a.scoreAtExample > b.scoreAtExample end)
	while #results > (p.max_stats or 25) do table.remove(results) end
	-- Normalise so the most valuable stat has weight 10 per unit-of-best-roll
	local top = results[1] and results[1].weightPerUnit or 1
	for _, r in ipairs(results) do r.weight = math.floor(r.weightPerUnit / top * 1000 + 0.5) / 100 end

	-- Weighted value of the current item, to use as a minimum
	local currentSum
	if current then
		currentSum = 0
		for _, ml in ipairs(current.explicitModLines or {}) do
			local v = tonumber(ml.line:match("([%d%.]+)"))
			if v then
				for _, r in ipairs(results) do
					if lineMatches(r.text, ml.line) then currentSum = currentSum + v * r.weight break end
				end
			end
		end
	end
	local tradeHelpers = require("Classes.TradeHelpers")
	local category = tradeHelpers.getTradeCategory(p.slot, current or blank)
	return {
		slot = p.slot, base = baseName, category = category, weightsUsed = weights,
		currentItem = current and current.name or nil, currentWeightedSum = currentSum,
		stats = results,
	}
end

-------------------------------------------------------------------------------
-- 4. Stat breakdowns and modifier sources
-------------------------------------------------------------------------------

local function describeSource(source)
	if type(source) ~= "string" then return tostring(source) end
	local spec = B().spec
	local nodeId = source:match("^Tree:(%d+)")
	if nodeId then
		local n = spec.nodes[tonumber(nodeId)]
		return "Tree: " .. (n and n.dn or nodeId)
	end
	local itemName = source:match("^Item:%d+:(.+)")
	if itemName then return "Item: " .. itemName end
	return (source:gsub(":", ": ", 1))
end

local function breakdownLines(b)
	local lines = {}
	if type(b) ~= "table" then return lines end
	for _, l in ipairs(b) do
		if type(l) == "string" then lines[#lines + 1] = cleanText(l) end
	end
	if type(b.damageTypes) == "table" and #b.damageTypes > 0 then
		-- Per source damage type (after conversion): base, increased and more, as in the Calcs tab
		lines[#lines + 1] = "Scaling by source damage type:"
		for _, r in ipairs(b.damageTypes) do
			local parts = {}
			if r.base then parts[#parts + 1] = "base " .. r.base end
			if r.inc then parts[#parts + 1] = "increased " .. r.inc end
			if r.more then parts[#parts + 1] = "more " .. r.more end
			if r.convSrc then parts[#parts + 1] = "converted/gained in " .. r.convSrc end
			if r.convDst then parts[#parts + 1] = "converts " .. r.convDst end
			if r.gainDst then parts[#parts + 1] = r.gainDst end
			local line = ("  %s: %s = %s"):format(tostring(r.source), table.concat(parts, ", "), tostring(r.total or "?"))
			lines[#lines + 1] = (line:gsub("(%d+%.%d%d)%d+", "%1"))
		end
	end
	if b.slots then
		for _, s in ipairs(b.slots) do
			lines[#lines + 1] = ("%s: %s"):format(tostring(s.label or s.slotName or "slot"), tostring(s.base or s.total or ""))
		end
	end
	return lines
end

function API.explain_stat(p)
	local build = B()
	-- The Calcs-tab environment is the one PoB builds breakdowns for
	local env = build.calcsTab.calcsEnv or build.calcsTab.mainEnv
	local player = env.player
	local stat = p.stat
	local res = { stat = stat, value = num(output()[stat]) }
	res.breakdown = breakdownLines(player.breakdown and player.breakdown[stat])
	local skill = player.mainSkill
	if #res.breakdown == 0 and skill and skill.breakdown then
		res.breakdown = breakdownLines(skill.breakdown[stat])
	end
	-- Hit damage per type (Physical, Lightning, ...) and per-hand stats live under MainHand/OffHand
	if #res.breakdown == 0 and player.breakdown then
		for _, hand in ipairs({ "MainHand", "OffHand" }) do
			local hb = player.breakdown[hand]
			local lines = breakdownLines(hb and hb[stat])
			if #lines > 0 then
				if #res.breakdown > 0 then res.breakdown[#res.breakdown + 1] = "" end
				res.breakdown[#res.breakdown + 1] = hand .. ":"
				for _, l in ipairs(lines) do res.breakdown[#res.breakdown + 1] = l end
				local ho = output()[hand]
				if res.value == nil and type(ho) == "table" then res.value = num(ho[stat]) end
			end
		end
	end
	-- Modifier sources
	local modName = p.mod_name or stat:gsub("^Total", ""):gsub("Unreserved$", "")
	local sources = {}
	for _, modType in ipairs({ "BASE", "INC", "MORE", "OVERRIDE" }) do
		local okA, rows = pcall(function()
			if skill and skill.skillModList and p.skill ~= false then
				return skill.skillModList:Tabulate(modType, skill.skillCfg, modName)
			end
			return player.modDB:Tabulate(modType, nil, modName)
		end)
		if okA then
			for _, row in ipairs(rows or {}) do
				if type(row.value) == "number" and row.value ~= 0 then
					sources[#sources + 1] = { type = modType, value = row.value, source = describeSource(row.mod.source),
						flags = (row.mod.flags ~= 0 or row.mod.keywordFlags ~= 0) and "conditional" or nil }
				end
			end
		end
	end
	table.sort(sources, function(a, b)
		if a.type ~= b.type then return a.type < b.type end
		return math.abs(a.value) > math.abs(b.value)
	end)
	local limit = p.limit or 40
	while #sources > limit do table.remove(sources) end
	res.modName = modName
	-- Totals by modifier type (PoB only writes a breakdown when there is something to combine)
	local base, inc, more = 0, 0, 1
	for _, src in ipairs(sources) do
		if not src.flags then
			if src.type == "BASE" then base = base + src.value
			elseif src.type == "INC" then inc = inc + src.value
			elseif src.type == "MORE" then more = more * (1 + src.value / 100) end
		end
	end
	res.totals = { base = base, increased = inc, moreMultiplier = math.floor(more * 1000 + 0.5) / 1000 }
	res.sources = sources
	if #res.breakdown == 0 and #sources == 0 then
		res.note = "No breakdown or sources found. Stat names are as in get_stats; try mod_name for the "
			.. "underlying modifier, e.g. stat 'CritChance' / mod_name 'CritChance', stat 'Life' / mod_name 'Life'."
	end
	return res
end

-------------------------------------------------------------------------------
-- 5. Scenarios
-------------------------------------------------------------------------------

local SCENARIO_PRESETS = {
	mapping = { enemyIsBoss = "None" },
	boss = { enemyIsBoss = "Pinnacle" },
	uber = { enemyIsBoss = "Uber" },
}
local SCENARIO_STATS = { "FullDPS", "CombinedDPS", "TotalDPS", "TotalDotDPS", "TotalEHP", "Life", "EnergyShield",
	"PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ColdMaximumHitTaken", "LightningMaximumHitTaken",
	"ChaosMaximumHitTaken" }

function API.scenarios(p)
	local scen = p.scenarios
	if not scen or next(scen) == nil then scen = SCENARIO_PRESETS end
	local stats = (p.stats and #p.stats > 0) and p.stats or SCENARIO_STATS
	local out = {}
	for name, cfg in pairs(scen) do
		if type(cfg) == "string" then cfg = SCENARIO_PRESETS[cfg] or error("Unknown preset " .. cfg, 0) end
		out[name] = withRestore(function()
			local configTab = B().configTab
			for var, value in pairs(cfg) do configTab.input[var] = value end
			configTab:BuildModList()
			rebuild()
			return { config = cfg, stats = pickStats(stats) }
		end)
	end
	return { scenarios = out, presets = SCENARIO_PRESETS }
end

-------------------------------------------------------------------------------
-- 7. Tree specs and leveling plans
-------------------------------------------------------------------------------

local TREE_URL = "https://www.pathofexile.com/passive-skill-tree/"

function API.tree_specs()
	local treeTab = B().treeTab
	local res = {}
	for i, spec in ipairs(treeTab.specList) do
		local used, asc = spec:CountAllocNodes()
		res[#res + 1] = { index = i, title = spec.title or ("Tree " .. i), active = i == treeTab.activeSpec,
			pointsUsed = used, ascendancyPointsUsed = asc, className = spec.curClassName,
			ascendancy = spec.curAscendClassName }
	end
	return { specs = res }
end

local function findSpec(ref)
	local treeTab = B().treeTab
	local idx = tonumber(ref)
	if idx and treeTab.specList[idx] then return idx end
	for i, spec in ipairs(treeTab.specList) do
		if (spec.title or ""):lower() == tostring(ref):lower() then return i end
	end
	error("No tree spec " .. tostring(ref) .. " (see list_tree_specs)")
end

local function addSpec(title, copyFrom)
	local build = B()
	local treeTab = build.treeTab
	local src = copyFrom or build.spec
	local spec = new("PassiveSpec"):PassiveSpec(build, src.treeVersion)
	spec.title = title
	if copyFrom then
		spec:DecodeURL(src:EncodeURL(TREE_URL))
	else
		spec:SelectClass(src.curClassId)
		spec:SelectAscendClass(src.curAscendClassId)
	end
	table.insert(treeTab.specList, spec)
	return #treeTab.specList, spec
end

function EDIT.add_tree_spec(e, notes)
	local treeTab = B().treeTab
	local idx = addSpec(e.label or ("Tree " .. (#treeTab.specList + 1)), e.copy ~= false and B().spec or nil)
	treeTab:SetActiveSpec(idx)
	notes[#notes + 1] = ("Added tree spec %d (%s) and made it active"):format(idx, e.label or "")
end

function EDIT.switch_tree_spec(e, notes)
	local idx = findSpec(e.spec)
	B().treeTab:SetActiveSpec(idx)
	notes[#notes + 1] = "Active tree spec: " .. idx
end

function EDIT.rename_tree_spec(e, notes)
	local idx = findSpec(e.spec or B().treeTab.activeSpec)
	B().treeTab.specList[idx].title = e.label
	notes[#notes + 1] = ("Renamed spec %d to %s"):format(idx, e.label)
end

function EDIT.delete_tree_spec(e, notes)
	local treeTab = B().treeTab
	local idx = findSpec(e.spec)
	if #treeTab.specList == 1 then error("Can't delete the only tree spec") end
	table.remove(treeTab.specList, idx)
	treeTab:SetActiveSpec(math.min(treeTab.activeSpec == idx and 1 or treeTab.activeSpec, #treeTab.specList))
	notes[#notes + 1] = "Deleted spec " .. idx
end

local QUEST_POINTS = { { 1, 0 }, { 12, 2 }, { 22, 4 }, { 32, 6 }, { 40, 7 }, { 44, 9 }, { 50, 12 }, { 54, 15 }, { 60, 18 }, { 64, 20 }, { 67, 23 } }
local function pointsAtLevel(level)
	local q = 0
	for _, a in ipairs(QUEST_POINTS) do if level >= a[1] then q = a[2] end end
	return (level - 1) + q
end
local function ascPointsAtLevel(level)
	if level >= 75 then return 8 elseif level >= 68 then return 6 elseif level >= 55 then return 4
	elseif level >= 33 then return 2 end
	return 0
end

-- Allocates, greedily by weighted value per point, nodes of `allowed` reachable
-- through `allowed` nodes only, until `budget` points are spent.
local function growWithin(spec, allowed, budget, weights, ascBudget)
	local build = B()
	local spent, ascSpent = 0, 0
	local picked = {}
	while true do
		local calcFunc, base = build.calcsTab:GetMiscCalculator()
		local bases = {}
		for stat in pairs(weights) do bases[stat] = readStat(base, stat) end
		-- BFS from allocated nodes through allowed nodes only
		local prev, queue, head = {}, {}, 1
		for id, n in pairs(spec.allocNodes) do prev[id] = false queue[#queue + 1] = n end
		while head <= #queue do
			local n = queue[head]; head = head + 1
			for _, o in ipairs(n.linked or {}) do
				if prev[o.id] == nil and allowed[o.id] and canStep(n, o, n.alloc and 0 or 1) then
					prev[o.id] = n
					queue[#queue + 1] = o
				end
			end
		end
		local best, bestScore, bestPath
		for id, node in pairs(allowed) do
			if not node.alloc and prev[id] ~= nil and (node.type ~= "Normal" or node.ascendancyName) then
				local path, cur = {}, node
				while cur and not cur.alloc do table.insert(path, 1, cur) cur = prev[cur.id] end
				local isAsc = node.ascendancyName ~= nil
				local fits = isAsc and (ascSpent + #path <= ascBudget) or (not isAsc and spent + #path <= budget)
				if fits and #path > 0 then
					local set = {}
					for _, pn in ipairs(path) do set[pn] = true end
					local sc = weightedScore(weights, bases, calcFunc({ addNodes = set })) / #path
					if isAsc then sc = sc + 1000 end -- ascendancy points are free to spend
					if not bestScore or sc > bestScore then best, bestScore, bestPath = node, sc, path end
				end
			end
		end
		if not best then break end
		for _, pn in ipairs(bestPath) do
			pn.alloc = true
			spec.allocNodes[pn.id] = pn
		end
		spec:BuildAllDependsAndPaths()
		if best.ascendancyName then ascSpent = ascSpent + #bestPath else spent = spent + #bestPath end
		picked[#picked + 1] = best.dn
		rebuild()
	end
	-- Spend leftover points on remaining allowed nodes (travel/small nodes)
	local progress = true
	while progress and spent < budget do
		progress = false
		for id, node in pairs(allowed) do
			if spent >= budget then break end
			if not node.alloc and not node.ascendancyName then
				for _, o in ipairs(node.linked or {}) do
					if o.alloc and canStep(o, node, 0) then
						node.alloc = true
						spec.allocNodes[id] = node
						spent = spent + 1
						progress = true
						break
					end
				end
			end
		end
	end
	spec:BuildAllDependsAndPaths()
	rebuild()
	return spent, ascSpent, picked
end

function API.plan_leveling(p)
	local build = B()
	local treeTab = build.treeTab
	local finalIdx = treeTab.activeSpec
	local final = build.spec
	local levels = (p.levels and #p.levels > 0) and p.levels or { 30, 50, 70, 90 }
	table.sort(levels)
	local weights = p.weights
	if not weights or next(weights) == nil then weights = { [defaultMetric()] = 1, TotalEHP = 1 } end
	local allowed = {}
	for id, n in pairs(final.allocNodes) do
		if n.type ~= "Mastery" then allowed[id] = n end
	end
	local finalUsed = final:CountAllocNodes()
	local savedLevel, savedAuto = build.characterLevel, build.characterLevelAutoMode
	local plan = {}
	for _, level in ipairs(levels) do
		local budget = math.min(pointsAtLevel(level), finalUsed)
		local idx, spec = addSpec(("Level %d"):format(level), nil)
		treeTab:SetActiveSpec(idx)
		build.characterLevel = level
		build.characterLevelAutoMode = false
		rebuild()
		-- nodes are shared objects per spec; map allowed ids to this spec's nodes
		local allowedHere = {}
		for id in pairs(allowed) do allowedHere[id] = spec.nodes[id] end
		local spent, ascSpent, picked = growWithin(spec, allowedHere, budget, weights, ascPointsAtLevel(level))
		local stats = pickStats({ defaultMetric(), "Life", "TotalEHP" })
		plan[#plan + 1] = { spec = idx, level = level, pointsBudget = budget, pointsUsed = spent,
			ascendancyPoints = ascSpent, keyNodesInOrder = picked, stats = stats }
	end
	treeTab:SetActiveSpec(finalIdx)
	build.characterLevel, build.characterLevelAutoMode = savedLevel, savedAuto
	rebuild()
	return { plan = plan, finalSpec = finalIdx,
		note = "Each level gets its own tree spec, built only from nodes in the final tree. "
			.. "Stats are calculated at that level with current gear and gems. Switch with switch_tree_spec." }
end

-------------------------------------------------------------------------------
-- 8. Tree render data
-------------------------------------------------------------------------------

function API.tree_render(p)
	local spec = B().spec
	local highlight = {}
	for _, id in ipairs(p.highlight or {}) do
		local n = resolveNode(id)
		if n then highlight[n.id] = true end
	end
	local nodes, edges, seen = {}, {}, {}
	for id, n in pairs(spec.nodes) do
		if n.x and n.y then
			if isMainTree(n) then
				nodes[#nodes + 1] = { id, math.floor(n.x), math.floor(n.y),
					({ Normal = "n", Notable = "N", Keystone = "K", Socket = "S", Mastery = "M", ClassStart = "C" })[n.type] or "n",
					n.alloc and 1 or 0, highlight[id] and 1 or 0 }
				for _, o in ipairs(n.linked or {}) do
					if isMainTree(o) and o.x and n.type ~= "Mastery" and o.type ~= "Mastery" then
						local a, b = math.min(id, o.id), math.max(id, o.id)
						local k = a .. ":" .. b
						if not seen[k] then
							seen[k] = true
							edges[#edges + 1] = { a, b }
						end
					end
				end
			end
		end
	end
	local labels = {}
	for id, n in pairs(spec.nodes) do
		if isMainTree(n) and n.x and (n.type == "Keystone" or (n.alloc and n.type == "Notable") or highlight[id] and n.type ~= "Normal") then
			labels[#labels + 1] = { id, n.dn }
		end
	end
	return { nodes = nodes, edges = edges, labels = labels, className = spec.curClassName,
		ascendancy = spec.curAscendClassName }
end

-------------------------------------------------------------------------------
-- 9. Character import
-------------------------------------------------------------------------------

function API.import_character(p)
	local charData = json.decode(p.character_json)
	if type(charData) ~= "table" then error("Invalid character JSON", 0) end
	if charData.character then charData = charData.character end
	main:SetMode("BUILD", false, (charData.name or "Imported character"))
	frame()
	local build = B()
	build.importTab:ImportPassiveTreeAndJewels(charData, true)
	frame()
	if charData.equipment or charData.items then
		charData.equipment = charData.equipment or charData.items
		build.importTab:ImportItemsAndSkills(charData, true, true, false)
	end
	rebuild()
	return info()
end

-------------------------------------------------------------------------------
-- Item sets and config sets
-------------------------------------------------------------------------------

local function findOrdered(orderList, sets, ref, what)
	local idx = tonumber(ref)
	if idx and orderList[idx] then return orderList[idx], idx end
	for i, id in ipairs(orderList) do
		local title = sets[id].title or ("Default")
		if title:lower() == tostring(ref):lower() then return id, i end
	end
	error(("No %s '%s'"):format(what, tostring(ref)))
end

function API.item_sets()
	local itemsTab = B().itemsTab
	local res = {}
	for i, id in ipairs(itemsTab.itemSetOrderList) do
		local set = itemsTab.itemSets[id]
		local equipped = 0
		for slotName, s in pairs(set) do
			if type(s) == "table" and s.selItemId and s.selItemId ~= 0 then equipped = equipped + 1 end
		end
		if id == itemsTab.activeItemSetId then
			equipped = 0
			for _, slot in pairs(itemsTab.slots) do
				if not slot.nodeId and slot.selItemId ~= 0 then equipped = equipped + 1 end
			end
		end
		res[#res + 1] = { index = i, title = set.title or "Default", active = id == itemsTab.activeItemSetId,
			equippedSlots = equipped }
	end
	return { itemSets = res }
end

function EDIT.add_item_set(e, notes)
	local itemsTab = B().itemsTab
	local set = itemsTab:NewItemSet()
	set.title = e.label or ("Item set " .. (#itemsTab.itemSetOrderList + 1))
	if e.copy ~= false then
		for slotName, slot in pairs(itemsTab.slots) do
			if not slot.nodeId then set[slotName] = { selItemId = slot.selItemId, active = slot.active } end
		end
	end
	table.insert(itemsTab.itemSetOrderList, set.id)
	itemsTab:SetActiveItemSet(set.id)
	itemsTab:AddUndoState()
	notes[#notes + 1] = ("Added item set %d (%s)%s and made it active"):format(#itemsTab.itemSetOrderList, set.title,
		e.copy ~= false and ", copied from the current gear" or ", empty")
end

function EDIT.switch_item_set(e, notes)
	local itemsTab = B().itemsTab
	local id, i = findOrdered(itemsTab.itemSetOrderList, itemsTab.itemSets, e.set, "item set")
	itemsTab:SetActiveItemSet(id)
	notes[#notes + 1] = ("Active item set: %d (%s)"):format(i, itemsTab.itemSets[id].title or "Default")
end

function EDIT.rename_item_set(e, notes)
	local itemsTab = B().itemsTab
	local id = e.set and findOrdered(itemsTab.itemSetOrderList, itemsTab.itemSets, e.set, "item set") or itemsTab.activeItemSetId
	itemsTab.itemSets[id].title = e.label
	notes[#notes + 1] = "Renamed item set to " .. tostring(e.label)
end

function EDIT.delete_item_set(e, notes)
	local itemsTab = B().itemsTab
	if #itemsTab.itemSetOrderList == 1 then error("Can't delete the only item set") end
	local id, i = findOrdered(itemsTab.itemSetOrderList, itemsTab.itemSets, e.set, "item set")
	table.remove(itemsTab.itemSetOrderList, i)
	itemsTab.itemSets[id] = nil
	if itemsTab.activeItemSetId == id then
		itemsTab.activeItemSet = nil -- don't write the deleted set back
		itemsTab:SetActiveItemSet(itemsTab.itemSetOrderList[1])
	end
	notes[#notes + 1] = "Deleted item set " .. i
end

function API.config_sets()
	local configTab = B().configTab
	local res = {}
	for i, id in ipairs(configTab.configSetOrderList) do
		local set = configTab.configSets[id]
		local values = {}
		for k, v in pairs(set.input) do
			if v ~= nil and v ~= false and v ~= "" and k ~= "customMods" then values[k] = num(v) end
		end
		res[#res + 1] = { index = i, title = set.title or "Default", active = id == configTab.activeConfigSetId,
			values = values }
	end
	return { configSets = res }
end

function EDIT.add_config_set(e, notes)
	local configTab = B().configTab
	local set = configTab:NewConfigSet(nil, e.label or ("Config " .. (#configTab.configSetOrderList + 1)))
	if e.copy ~= false then
		for k, v in pairs(configTab.input) do set.input[k] = v end
		for k, v in pairs(configTab.placeholder) do set.placeholder[k] = v end
	end
	for k, v in pairs(e.values or {}) do
		if v == json.null then v = nil end
		set.input[k] = v
	end
	table.insert(configTab.configSetOrderList, set.id)
	configTab:SetActiveConfigSet(set.id)
	configTab:AddUndoState()
	notes[#notes + 1] = ("Added config set %d (%s) and made it active"):format(#configTab.configSetOrderList, set.title)
end

function EDIT.switch_config_set(e, notes)
	local configTab = B().configTab
	local id, i = findOrdered(configTab.configSetOrderList, configTab.configSets, e.set, "config set")
	configTab:SetActiveConfigSet(id)
	notes[#notes + 1] = ("Active config set: %d (%s)"):format(i, configTab.configSets[id].title or "Default")
end

function EDIT.rename_config_set(e, notes)
	local configTab = B().configTab
	local id = e.set and findOrdered(configTab.configSetOrderList, configTab.configSets, e.set, "config set") or configTab.activeConfigSetId
	configTab.configSets[id].title = e.label
	notes[#notes + 1] = "Renamed config set to " .. tostring(e.label)
end

function EDIT.delete_config_set(e, notes)
	local configTab = B().configTab
	if #configTab.configSetOrderList == 1 then error("Can't delete the only config set") end
	local id, i = findOrdered(configTab.configSetOrderList, configTab.configSets, e.set, "config set")
	table.remove(configTab.configSetOrderList, i)
	configTab.configSets[id] = nil
	if configTab.activeConfigSetId == id then configTab:SetActiveConfigSet(configTab.configSetOrderList[1]) end
	notes[#notes + 1] = "Deleted config set " .. i
end

-------------------------------------------------------------------------------
-- Anoints
-------------------------------------------------------------------------------

local function anointsOn(item)
	local res = {}
	for _, list in ipairs({ item.enchantModLines or {}, item.implicitModLines or {}, item.explicitModLines or {} }) do
		for _, ml in ipairs(list) do
			local name = ml.line:match("^Allocates (.+)$")
			if name then res[#res + 1] = name end
		end
	end
	return res
end

local function withoutAnoints(item)
	local copy = new("Item"):Item(item:BuildRaw())
	for i = #copy.enchantModLines, 1, -1 do
		if copy.enchantModLines[i].line:match("^Allocates ") then table.remove(copy.enchantModLines, i) end
	end
	copy:BuildAndParseRaw()
	return copy
end

function API.rank_anoints(p)
	local build = B()
	local spec = build.spec
	local itemsTab = build.itemsTab
	local slotName = p.slot or "Amulet"
	local slot = itemsTab.slots[slotName]
	local item = slot and slot.selItemId ~= 0 and itemsTab.items[slot.selItemId] or nil
	local weights = p.weights
	if not weights or next(weights) == nil then weights = { [defaultMetric()] = 1, TotalEHP = 0.5 } end
	local calcFunc = build.calcsTab:GetMiscCalculator()
	local current = item and anointsOn(item) or {}
	local baseOverride = {}
	if item and #current > 0 and p.replace_current ~= false then
		baseOverride = { repSlotName = slotName, repItem = withoutAnoints(item) }
	end
	local baseOut = calcFunc(baseOverride, weights.FullDPS ~= nil)
	local bases = {}
	for stat in pairs(weights) do bases[stat] = readStat(baseOut, stat) end
	local results = {}
	for _, node in pairs(spec.nodes) do
		if node.recipe and #node.recipe > 0 and node.type == "Notable" and not node.alloc and not node.ascendancyName then
			local override = { addNodes = { [node] = true } }
			for k, v in pairs(baseOverride) do override[k] = v end
			local out = calcFunc(override, weights.FullDPS ~= nil)
			local score = weightedScore(weights, bases, out)
			local changes = {}
			for stat in pairs(weights) do
				local a, b = readStat(out, stat), bases[stat]
				if a and b then changes[stat] = { change = a - b, pct = b ~= 0 and (a - b) / math.abs(b) * 100 or nil } end
			end
			results[#results + 1] = { name = node.dn, oils = node.recipe, score = score, changes = changes, stats = node.sd }
		end
	end
	table.sort(results, function(a, b) return a.score > b.score end)
	local limit = p.limit or 15
	while #results > limit do table.remove(results) end
	return {
		slot = slotName, item = item and item.name or nil, currentAnoints = current,
		comparedAgainst = (#current > 0 and p.replace_current ~= false) and "the item without its current anoint" or "the current build",
		weights = weights, results = results,
		note = "Apply with an anoint edit. Notables you already have allocated are left out.",
	}
end

function EDIT.anoint(e, notes)
	local build = B()
	local itemsTab = build.itemsTab
	local slotName = e.slot or "Amulet"
	local slot = itemsTab.slots[slotName]
	local item = slot and slot.selItemId ~= 0 and itemsTab.items[slot.selItemId]
	if not item then error("Nothing equipped in " .. slotName) end
	local node = resolveNode(e.node)
	if not node or not node.recipe or #node.recipe == 0 then error(tostring(e.node) .. " can't be anointed") end
	if not (item.base.type == "Amulet" or item.canBeAnointed) or item.base.subType == "Talisman" then
		error(slotName .. " item can't be anointed")
	end
	local newItem = withoutAnoints(item)
	table.insert(newItem.enchantModLines, 1, { crafted = true, line = "Allocates " .. node.dn })
	newItem:BuildAndParseRaw()
	itemsTab:AddItem(newItem, true)
	slot:SetSelItemId(newItem.id)
	itemsTab:PopulateSlots()
	itemsTab:AddUndoState()
	notes[#notes + 1] = ("Anointed %s with %s (oils: %s)"):format(slotName, node.dn, table.concat(node.recipe, ", "))
end

-------------------------------------------------------------------------------
-- Cluster jewels
-------------------------------------------------------------------------------

local CLUSTER_SOCKETS = { Large = 2, Medium = 1, Small = 0 }
local CLUSTER_NOTABLE_LIMIT = { Large = 3, Medium = 2, Small = 1 }

local function clusterJewelData(size)
	local name = (size or "Large"):gsub("^%l", string.upper) .. " Cluster Jewel"
	local jd = B().data.clusterJewels.jewels[name]
	if not jd then error("Cluster jewel size must be Large, Medium or Small", 0) end
	return name, jd
end

local function findClusterSkill(jd, ref)
	local want = tostring(ref):lower()
	for tag, sk in pairs(jd.skills) do
		local enchant = table.concat(sk.enchant or {}, " "):lower()
		if tag:lower() == want or sk.name:lower() == want or enchant:find(want, 1, true) then return tag, sk end
	end
end

-- Notables a cluster jewel with this enchant can roll
local function clusterNotables(skillTag, ilvl)
	local res = {}
	for modId, mod in pairs(B().data.itemMods.JewelCluster or {}) do
		local name = mod[1] and mod[1]:match("^1 Added Passive Skill is (.+)$")
		if name and (mod.level or 1) <= (ilvl or 84) then
			for i, key in ipairs(mod.weightKey or {}) do
				if key == skillTag then
					if (mod.weightVal[i] or 0) > 0 then res[name] = { modId = modId, type = mod.type, level = mod.level } end
					break
				end
			end
		end
	end
	return res
end

function API.cluster_jewel_options(p)
	local name, jd = clusterJewelData(p.size)
	local tree = B().spec.tree
	if not p.skill then
		local skills = {}
		for tag, sk in pairs(jd.skills) do
			skills[#skills + 1] = { name = sk.name, tag = tag, enchant = sk.enchant and sk.enchant[1] }
		end
		table.sort(skills, function(a, b) return a.name < b.name end)
		return { jewel = name, passives = { min = jd.minNodes, max = jd.maxNodes },
			sockets = CLUSTER_SOCKETS[jd.size], maxNotables = CLUSTER_NOTABLE_LIMIT[jd.size], skills = skills }
	end
	local tag, sk = findClusterSkill(jd, p.skill)
	if not tag then error("No " .. name .. " enchant matching '" .. tostring(p.skill) .. "'", 0) end
	local list = {}
	for nName, info in pairs(clusterNotables(tag, p.item_level)) do
		local node = tree.clusterNodeMap and tree.clusterNodeMap[nName]
		list[#list + 1] = { name = nName, affix = info.type, level = info.level, stats = node and node.sd or nil }
	end
	table.sort(list, function(a, b) return a.name < b.name end)
	return { jewel = name, skill = sk.name, enchant = sk.enchant and sk.enchant[1], notables = list,
		note = ("A %s can have up to %d notables (at most 2 prefix and 2 suffix notables)."):format(name,
			CLUSTER_NOTABLE_LIMIT[jd.size]) }
end

-- Nodes a socketed cluster jewel added (PoB's own subgraph record), incl. its nested sockets
local function clusterSubgraphNodes(socket)
	local spec = B().spec
	for _, sg in pairs(spec.subGraphs or {}) do
		if sg.parentSocket and sg.parentSocket.id == socket.id then
			local out = {}
			for _, n in ipairs(sg.nodes or {}) do
				if n.type ~= "Mastery" then out[#out + 1] = n end
			end
			return out
		end
	end
	return {}
end

function EDIT.create_cluster_jewel(e, notes)
	local build = B()
	local spec = build.spec
	local socket = resolveNode(tostring(e.socket or ""):gsub("^Jewel ", ""))
	if not socket or not socket.expansionJewel then
		error("Not a cluster jewel socket: " .. tostring(e.socket) .. " (use find_passive_nodes 'Jewel Socket'; "
			.. "outer sockets take Large, Medium or Small jewels)")
	end
	local socketSize = socket.expansionJewel.size
	local size = e.size or ({ [2] = "Large", [1] = "Medium", [0] = "Small" })[socketSize]
	local name, jd = clusterJewelData(size)
	if jd.sizeIndex > socketSize then error(("%s doesn't fit this socket"):format(name)) end
	local tag, sk = findClusterSkill(jd, e.skill or "")
	if not tag then error("No " .. name .. " enchant matching '" .. tostring(e.skill) .. "' (see list_cluster_jewel_options)") end
	local ilvl = e.item_level or 84
	local passives = e.passives or jd.minNodes
	if passives < jd.minNodes or passives > jd.maxNodes then
		error(("%s has %d-%d passives"):format(name, jd.minNodes, jd.maxNodes))
	end
	local allowed = clusterNotables(tag, ilvl)
	local picked = e.notables or {}
	if #picked > CLUSTER_NOTABLE_LIMIT[jd.size] then
		error(("%s can have at most %d notables"):format(name, CLUSTER_NOTABLE_LIMIT[jd.size]))
	end
	local nPrefix, nSuffix = 0, 0
	for _, nName in ipairs(picked) do
		local found
		for an, info in pairs(allowed) do
			if an:lower() == tostring(nName):lower() then found = info nName = an break end
		end
		if not found then error(("%s can't roll on a %s with %s"):format(nName, name, sk.name)) end
		if found.type == "Prefix" then nPrefix = nPrefix + 1 else nSuffix = nSuffix + 1 end
	end
	if nPrefix > 2 or nSuffix > 2 then error("Too many prefix or suffix notables (max 2 of each)") end

	if not socket.alloc then
		if not socket.path then error("Socket is not reachable") end
		spec:AllocNode(socket)
	end
	local sockets = e.sockets or CLUSTER_SOCKETS[jd.size]
	local lines = { "Rarity: RARE", e.name or "Crafted Cluster", name, "Item Level: " .. ilvl }
	local implicit = { "{enchant}Adds " .. passives .. " Passive Skills" }
	if sockets > 0 then
		implicit[#implicit + 1] = ("{enchant}%d Added Passive Skill%s %s Jewel Socket%s"):format(sockets,
			sockets == 1 and "" or "s", sockets == 1 and "is a" or "are", sockets == 1 and "" or "s")
	end
	for _, l in ipairs(sk.enchant or {}) do implicit[#implicit + 1] = "{enchant}" .. l end
	lines[#lines + 1] = "Implicits: " .. #implicit
	for _, l in ipairs(implicit) do lines[#lines + 1] = l end
	for _, nName in ipairs(picked) do lines[#lines + 1] = "1 Added Passive Skill is " .. nName end
	for _, l in ipairs(e.mods or {}) do lines[#lines + 1] = l end
	local item = new("Item"):Item(table.concat(lines, "\n"))
	if not item.clusterJewel then error("PoB did not recognise the cluster jewel") end
	item:BuildModList()

	local itemsTab = build.itemsTab
	local slot = itemsTab.sockets[socket.id]
	itemsTab:AddItem(item, true)
	slot:SetSelItemId(item.id)
	itemsTab:PopulateSlots()
	itemsTab:AddUndoState()
	rebuild()
	spec = B().spec
	socket = spec.nodes[socket.id]

	-- Allocate the new passives
	local mode = e.allocate or "notables"
	local usedBefore = spec:CountAllocNodes()
	if mode == "all" then
		for _, n in ipairs(clusterSubgraphNodes(socket)) do
			if not n.alloc then
				n.alloc = true
				spec.allocNodes[n.id] = n
			end
		end
		spec:BuildAllDependsAndPaths()
	elseif mode == "notables" then
		-- cheapest first; paths change after each allocation
		for _ = 1, 10 do
			local best
			for _, n in ipairs(clusterSubgraphNodes(socket)) do
				if n.type == "Notable" and not n.alloc and n.path and (not best or #n.path < #best.path) then best = n end
			end
			if not best then break end
			spec:AllocNode(best)
		end
	end
	local count = spec:CountAllocNodes() - usedBefore
	spec:AddUndoState()
	notes[#notes + 1] = ("Socketed %s (%s, %d passives%s) in %s; %d passive point%s allocated"):format(name, sk.name,
		passives, #picked > 0 and (": " .. table.concat(picked, ", ")) or "", "Jewel " .. socket.id, count,
		count == 1 and "" or "s")
end

-------------------------------------------------------------------------------
-- Game-data overviews (read live from the tree data)
-------------------------------------------------------------------------------

function API.ascendancy_notables(p)
	local spec = B().spec
	local want = p.ascendancy and tostring(p.ascendancy):lower()
	local byAsc = {}
	for _, node in pairs(spec.nodes) do
		local asc = node.ascendancyName
		if asc and node.type == "Notable" and (not want or asc:lower() == want) then
			byAsc[asc] = byAsc[asc] or {}
			table.insert(byAsc[asc], { name = node.dn, stats = node.sd })
		end
	end
	-- Which class each ascendancy belongs to
	local classOf = {}
	for ascName, entry in pairs(spec.tree.ascendNameMap or {}) do
		classOf[ascName] = entry.class and entry.class.name
	end
	for _, class in pairs(spec.tree.classes) do
		for ascId, asc in pairs(class.classes or {}) do
			if ascId ~= 0 and asc.name and not classOf[asc.name] then classOf[asc.name] = class.name end
		end
	end
	local res = {}
	for asc, list in pairs(byAsc) do
		table.sort(list, function(a, b) return a.name < b.name end)
		local cls = classOf[asc]
		if not cls or cls == "alternate_ascendancies" then cls = "Bloodline (any class)" end
		res[#res + 1] = { ascendancy = asc, class = cls, notables = list }
	end
	table.sort(res, function(a, b)
		if (a.class or "") ~= (b.class or "") then return (a.class or "") < (b.class or "") end
		return a.ascendancy < b.ascendancy
	end)
	if want and #res == 0 then error("No ascendancy named " .. tostring(p.ascendancy) .. " (see list_classes)", 0) end
	return { treeVersion = spec.treeVersion, ascendancies = res }
end

function API.keystones()
	local spec = B().spec
	local res = {}
	for _, node in pairs(spec.nodes) do
		if node.type == "Keystone" and not node.ascendancyName and not node.isProxy and not node.expansionSkill then
			res[#res + 1] = { name = node.dn, stats = node.sd, allocated = node.alloc or nil,
				pathCost = (not node.alloc and node.path) and #node.path or nil }
		end
	end
	table.sort(res, function(a, b) return a.name < b.name end)
	return { treeVersion = spec.treeVersion, keystones = res }
end

-------------------------------------------------------------------------------
-- Dispatcher used by Python
-------------------------------------------------------------------------------
function pob_call(method, paramsJson)
	local fn = API[method]
	if not fn then
		return json.encode({ ok = false, error = "unknown method " .. tostring(method) })
	end
	local params = paramsJson and json.decode(paramsJson) or {}
	local ok, res = xpcall(function() return fn(params) end, function(err)
		return { msg = tostring(err), tb = debug.traceback() }
	end)
	if ok then
		return json.encode({ ok = true, result = res })
	end
	return json.encode({ ok = false, error = cleanText(res.msg), traceback = res.tb })
end

return true
