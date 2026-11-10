# pob-mcp — Path of Building MCP server

An MCP server that runs **Path of Building Community** (Path of Exile 1) headlessly, so Claude
(or any MCP client) can read builds, get real PoB numbers, test changes and export the result.

It doesn't re-implement any game math: it boots PoB's own Lua code inside an embedded LuaJIT
(via [`lupa`](https://pypi.org/project/lupa/)), so every number matches what the PoB app shows
for the same build and config.

## Tools

**Reading and evaluating**

| Tool | What it does |
|---|---|
| `load_build` | Load a share code, pobb.in / pastebin / poe.ninja / poedb link, raw XML, or a `.xml` file |
| `get_build_summary` | Class, ascendancy, level, point budget, main skill + headline stats |
| `get_stats` | Any stat from PoB's output table, by exact name or substring filter |
| `get_skills` | Socket groups and gems (indexes used by edits) |
| `get_items` | Equipped gear, flasks and tree jewels, with full item text |
| `get_passive_tree` | Keystones, notables, masteries, ascendancy nodes, tree URL |
| `get_config` / `list_config_options` | Current Configuration tab values / all options and choices |
| `get_warnings` | PoB's own warnings plus uncapped resists, unmet requirements, over-reservation, point overspend, gem errors |

**Changing and optimising**

| Tool | What it does |
|---|---|
| `compare_changes` | **What-if**: apply edits, report stat deltas, then restore the build |
| `apply_changes` | Apply edits and keep them |
| `undo_last_change` | Roll back the last `apply_changes` or `auto_allocate_passives` |
| `rank_passives` | Rank reachable notables/keystones by gain per point (or allocated ones by least value, for respecs) for any stat |
| `rank_support_gems` | Rank every compatible support gem for a link, as an extra gem or as a replacement, plus what each current support contributes |
| `export_build` | Share code or XML, optionally saved to a file |

**Creating builds**

| Tool | What it does |
|---|---|
| `new_build` | Empty build, optionally with ascendancy (sets the class too), class and level |
| `list_classes` | Classes, ascendancies and starting attributes |
| `search_gems` | Skill and support gems by name or tags, with descriptions |
| `search_uniques` | PoB's unique database by name, base, type or mod text |
| `search_item_bases` | Item bases for crafting rares, with implicits and base stats |
| `find_passive_nodes` | Notables, keystones, jewel sockets and ascendancy nodes by name or stat text, with path cost |
| `list_mastery_effects` | Mastery options, for one mastery or every one you can allocate now |
| `list_item_slots` | Gear slots plus allocated tree jewel sockets |
| `auto_allocate_passives` | Greedy optimiser: spends points on the notables with the best weighted gain per point, e.g. `{"FullDPS": 1, "TotalEHP": 0.5}` |

### Edit operations

Used by `compare_changes` and `apply_changes`. Several edits can go in one call.

| Area | Operations |
|---|---|
| Character | `set_class`, `set_ascendancy`, `set_level` |
| Passive tree | `allocate_node` (shortest path; works for ascendancy nodes and jewel sockets), `deallocate_node`, `set_mastery` |
| Skills | `create_socket_group`, `delete_socket_group`, `add_gem`, `remove_gem`, `replace_gem`, `set_gem`, `set_main_skill`, `set_group_enabled` |
| Items | `equip_unique`, `create_rare` (base + mod lines; reports lines PoB can't parse), `equip_item` (pasted item text), `unequip_slot` |
| Config | `set_config`, `add_custom_mod` |

Gem names work with or without the "Support" suffix.

## Setup

1. **Get the PoB source** (the installed app lacks the headless support files):

   ```
   git clone https://github.com/PathOfBuildingCommunity/PathOfBuilding
   ```

   `git pull` it whenever PoB updates for a new league.

2. **Install this package** (Python 3.10+):

   ```
   pip install ./pob-mcp
   ```

   For development, use `pip install -e ./pob-mcp` so code changes take effect without
   reinstalling.

   This pulls in `mcp` and `lupa`. `lupa` ships its own LuaJIT, so there's nothing else to
   install — no system Lua, no luarocks.

3. **Add it to Claude Desktop** (`claude_desktop_config.json`):

   ```json
   {
     "mcpServers": {
       "path-of-building": {
         "command": "pob-mcp",
         "env": { "POB_PATH": "C:\\Users\\you\\PathOfBuilding" }
       }
     }
   }
   ```

   If `pob-mcp` isn't on PATH, use `"command": "python", "args": ["-m", "pob_mcp"]`.
   For Claude Code: `claude mcp add pob -e POB_PATH=/path/to/PathOfBuilding -- pob-mcp`.

`POB_PATH` can point at the checkout or its `src` folder; `--pob-path` works too. If unset, the
server looks in `~/PathOfBuilding`.

## Example prompts

- "Load https://pobb.in/xxxx and tell me my weakest defensive layer."
- "What are the best 5 notables for my next points, by DPS per point? And for EHP?"
- "Which support in my main 6-link is weakest, and what should replace it?"
- "I need 10 points for a respec. Which notables cost me the least damage?"
- "Is anything wrong with this build?"
- "Which of these three notables gives the most DPS per point: …?"
- "Compare my current ring with this one: (pasted item text)."
- "Swap Controlled Destruction for Elemental Focus in my main link and show the difference against a Pinnacle boss."
- "Apply those changes and give me the new share code."
- "Create a level 95 Elementalist Arc build with a Tabula Rasa, cap my resists, and give me the share code."
- "Make a Deadeye Lightning Arrow build that balances damage and EHP, then tell me what gear to look for."

## How it works

```
MCP client ⇄ stdio ⇄ server.py (FastMCP/MCPServer)
                         │  one worker thread
                         ▼
                  engine.py ── lupa (embedded LuaJIT 2.1)
                         │
                  bridge.lua ── boots PoB (Launch.lua, Modules, Classes, Data)
                                and exposes a small JSON API
```

- The first call takes ~5 seconds while PoB loads its data (the server starts this in the
  background as soon as it launches). After that, calls are fast; a what-if comparison
  takes well under a second.
- `rank_passives` and `rank_support_gems` use PoB's internal "misc calculator", the same
  fast path behind its node power heatmap and its gem-list DPS sort. Ranking every notable
  within 8 points takes a few seconds; ranking supports takes under a second.
- `compare_changes` snapshots the build as XML, applies the edits, recalculates, diffs the
  stats and reloads the snapshot, so the loaded build is never changed.
- Stats depend on the build's Configuration tab (enemy type, conditions, charges…). Use
  `get_config` to see assumptions and `set_config` edits to change them.

### Compatibility shims (bridge.lua)

- PoB's bundled LuaJIT accepts compound assignment (`x += 1`, `s ..= t`). Stock LuaJIT
  doesn't, so those lines are rewritten on load. Only files that fail to parse are touched.
- PoB's C `lua-utf8` module is replaced with stdlib string functions (only used for
  thousands separators and text-box caret movement).
- `Inflate`/`Deflate` use Python's zlib.
- Nothing is written to stdout except MCP traffic; PoB's console output goes to stderr.

### How an agent creates a build

1. `new_build` with an ascendancy and level.
2. `search_gems`, then a `create_socket_group` edit with `set_main` for the main skill, plus
   groups for auras, movement and so on.
3. Gear: `search_uniques` + `equip_unique`, or `search_item_bases` + `create_rare` with the mods
   it wants (resists, life, damage). Rares can be any combination of real mod lines, so the
   result is a target to trade or craft for, not a specific item that exists.
4. Ascendancy notables, keystones and must-have notables with `allocate_node`.
5. `auto_allocate_passives` for the remaining points, then `set_mastery` (each costs a point).
6. `get_warnings`, fix resists, attributes and reservation, and repeat.
7. `export_build` for the share code.

`auto_allocate_passives` is greedy: it picks the best notable per point one step at a time,
which is fast and usually good, but it doesn't plan long paths toward distant clusters. Guide
it by allocating a few target notables first, or using `avoid`. A full tree takes 15–40
seconds; if it reaches its time limit, the progress is kept and calling it again continues.

## Limitations

- PoE1 / PoB Community only (not PoB for PoE2).
- No character import from the PoE website (load a build code or XML instead).
- Cluster jewel notables and timeless-jewel seeds can't be edited with the edit ops; edit
  them in PoB, or use `add_custom_mod` to approximate a stat.
- `create_rare` accepts any mod lines, including combinations that can't roll together in
  the game. Treat crafted rares as targets.
- Weapon swap slots are hidden from `list_item_slots`.
