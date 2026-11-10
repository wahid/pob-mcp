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
	frame()
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
	local res = {}
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
		local r, max = out[elem .. "Resist"], out[elem .. "ResistMax"] or 75
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
	itemsTab:PopulateSlots()
	itemsTab:AddUndoState()
	notes[#notes + 1] = ("Equipped %s%s in %s"):format(item.name or "item", what or "", slotName)
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
	local base = data.itemBases[e.base]
	if not base then
		for name, b in pairs(data.itemBases) do
			if name:lower() == tostring(e.base):lower() then base = b e.base = name break end
		end
	end
	if not base then error("Unknown item base: " .. tostring(e.base) .. " (see search_item_bases)") end
	local lines = { "Rarity: " .. (e.rarity or "RARE"), e.name or "Crafted Item", e.base }
	lines[#lines + 1] = "Item Level: " .. tostring(e.item_level or 86)
	if e.quality then lines[#lines + 1] = "Quality: " .. tostring(e.quality) end
	if e.sockets then lines[#lines + 1] = "Sockets: " .. e.sockets end
	local implicits = e.implicits
	if implicits == nil and base.implicit then
		implicits = {}
		for line in base.implicit:gmatch("[^\n]+") do implicits[#implicits + 1] = line end
	end
	lines[#lines + 1] = "Implicits: " .. tostring(#(implicits or {}))
	for _, l in ipairs(implicits or {}) do lines[#lines + 1] = l end
	for _, l in ipairs(e.mods or {}) do lines[#lines + 1] = l end
	local item = new("Item"):Item(table.concat(lines, "\n"))
	if not item.base then error("Could not build item from base " .. e.base) end
	-- Report mod lines PoB could not parse, so the agent can fix wording
	local unsupported = {}
	for _, ml in ipairs(item.explicitModLines or {}) do
		if ml.extra then unsupported[#unsupported + 1] = ml.line end
	end
	item:NormaliseQuality()
	item:BuildModList()
	equipParsed(item, e.slot, notes)
	if #unsupported > 0 then
		notes[#notes + 1] = "Warning: PoB does not understand these mod lines: " .. table.concat(unsupported, " | ")
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

	-- Validate stats up front
	do
		local _, base = build.calcsTab:GetMiscCalculator()
		for stat in pairs(weights) do
			if readStat(base, stat) == nil then
				error(("Unknown or non-numeric stat '%s'"):format(stat), 0)
			end
		end
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
					cache[key] = weightedScore(weights, bases, calcFunc({ addNodes = { [node] = true } }, useFull))
				end
				cands[#cands + 1] = { node = node, est = cache[key] / #node.path }
			end
		end
		if #cands == 0 then stopReason = "no reachable candidates within budget" break end
		table.sort(cands, function(a, b) return a.est > b.est end)

		-- Pass 2: exact value of the whole path for the shortlist
		local best, bestScore
		for i = 1, math.min(shortlist, #cands) do
			local node = cands[i].node
			local set = {}
			for _, pn in ipairs(node.path) do set[pn] = true end
			local sc = weightedScore(weights, bases, calcFunc({ addNodes = set }, useFull)) / #node.path
			if not bestScore or sc > bestScore then best, bestScore = node, sc end
		end
		if not best or bestScore <= 0 then stopReason = "no remaining node improves the weighted stats" break end

		local cost = #best.path
		spec:AllocNode(best)
		spent = spent + cost
		rebuild()
		chosen[#chosen + 1] = { name = best.dn, type = best.type, cost = cost,
			scorePerPoint = math.floor(bestScore * 100 + 0.5) / 100 }
	end
	spec:AddUndoState()

	local after = {}
	for stat in pairs(weights) do after[stat] = readStat(output(), stat) end
	return { weights = weights, pointsBudget = budget, pointsSpent = spent,
		allocated = chosen, stoppedBecause = stopReason or "budget used", stats = after, info = info() }
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
