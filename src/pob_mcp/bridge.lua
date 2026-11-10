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
	}
end

local function resolveNode(ref)
	local spec = B().spec
	local id = tonumber(ref)
	if id and spec.nodes[id] then return spec.nodes[id] end
	local want = tostring(ref):lower()
	local fallback
	for _, node in pairs(spec.nodes) do
		if node.dn and node.dn:lower() == want then
			-- Prefer the reachable / non-ascendancy-cluster match
			if node.path or node.alloc then return node end
			fallback = fallback or node
		end
	end
	return fallback
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
	for _, slot in ipairs(B().itemsTab.orderedSlots) do
		if not slot.nodeId then names[#names + 1] = slot.slotName end
	end
	return { slots = names }
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
		if node.dn and (node.type == "Notable" or node.type == "Keystone" or p.include_small or node.ascendancyName) then
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
	if not group then error("No socket group with index " .. tostring(idx) .. " (see get_skills)") end
	return group
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
		nameSpec = e.gem, level = e.level or 20, quality = e.quality or 0,
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
	target.nameSpec = e.new_gem
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
