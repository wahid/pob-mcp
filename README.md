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
| `explain_stat` | Where a stat comes from: PoB's breakdown plus every modifier source (tree node, item, gem) with its value |
| `evaluate_scenarios` | The build against mapping, pinnacle-boss and uber-boss setups (or your own config sets) in one call |
| `compare_builds` | Diff the loaded build against another (stats, keystones, notables, gems, gear) |
| `render_tree` | A picture of the passive tree with allocated nodes and an optional route highlighted |
| `list_item_sets` / `list_config_sets` | The build's gear sets (e.g. budget vs endgame) and config sets (e.g. mapping vs bossing) |

**Changing and optimising**

| Tool | What it does |
|---|---|
| `compare_changes` | **What-if**: apply edits, report stat deltas, then restore the build |
| `apply_changes` | Apply edits and keep them |
| `undo_last_change` | Roll back the last `apply_changes` or `auto_allocate_passives` |
| `rank_passives` | Rank reachable notables/keystones by gain per point (or allocated ones by least value, for respecs) for any stat |
| `rank_support_gems` | Rank every compatible support gem for a link, as an extra gem or as a replacement, plus what each current support contributes |
| `fix_gaps` | Close resist and attribute gaps with valid suffixes in free slots on rare gear, then the cheapest tree nodes; preview or apply |
| `trade_search` | A pathofexile.com trade search for upgrades in a slot: every mod that can roll there is weighted by its PoB-calculated value |
| `price_check` | poe.ninja prices for uniques, or for everything unique the build wears |
| `rank_anoints` | Every anointable notable ranked by its value on this build, with the oils needed |
| `list_ascendancy_notables` | Every ascendancy's notables (or one) with exact stats, read live from PoB |
| `list_keystones` | All keystones with their stats, allocation and path cost |
| `export_build` | Share code or XML, optionally saved to a file |
| `save_to_pob` | Save into PoB's Builds folder so it shows up in the PoB app |

**Creating builds**

| Tool | What it does |
|---|---|
| `new_build` | Empty build, optionally with ascendancy (sets the class too), class and level |
| `list_classes` | Classes, ascendancies and starting attributes |
| `search_gems` | Skill and support gems by name or tags, with descriptions |
| `search_uniques` | PoB's unique database by name, base, type or mod text |
| `search_item_bases` | Item bases for crafting rares, with implicits and base stats |
| `list_item_mods` | The mods that can actually roll on a base, with tiers and mod ids for `create_rare` |
| `list_cluster_jewel_options` | Cluster jewel enchants per size, and the notables each can roll (prefix/suffix, stats) |
| `find_passive_nodes` | Notables, keystones, jewel sockets and ascendancy nodes by name or stat text, with path cost |
| `get_tree_map` | The passive tree as a condensed graph (notables, keystones, sockets and branch points, joined by chains of small passives with their stats); whole tree or a region |
| `find_routes` | 2–3 distinct cheapest routes to a node, with `via` / `avoid`, what each route picks up, and PoB-calculated stat changes |
| `list_mastery_effects` | Mastery options, for one mastery or every one you can allocate now |
| `list_item_slots` | Gear slots plus allocated tree jewel sockets |
| `auto_allocate_passives` | Greedy optimiser: spends points on the notables with the best weighted gain per point, e.g. `{"FullDPS": 1, "TotalEHP": 0.5}` |
| `plan_leveling` | Leveling trees (e.g. levels 30/50/70/90) built from the final tree, most valuable nodes first |
| `list_tree_specs` | The build's passive tree specs (final tree, leveling trees) |
| `import_character` | Import a character from a public pathofexile.com profile, or a saved character JSON |

**Working with PoB's Builds folder**

| Tool | What it does |
|---|---|
| `list_saved_builds` | Builds in PoB's Builds folder with class, ascendancy, level and last-modified time |
| `load_saved_build` | Load one by name; `save_to_pob` without a name then saves back to it |
| `watch_builds_folder` | Watch the folder for builds created, saved or deleted in the PoB app |
| `get_build_folder_changes` | What changed since the last check; optionally reload the loaded build from its file and show the stat changes |

Together these let you and the agent work on the same build: edit in PoB, save, and the agent
picks the change up; or let the agent save and press F5 in PoB.

### Edit operations

Used by `compare_changes` and `apply_changes`. Several edits can go in one call.

| Area | Operations |
|---|---|
| Character | `set_class`, `set_ascendancy`, `set_level` |
| Passive tree | `allocate_node` (shortest path; works for ascendancy nodes and jewel sockets), `allocate_path` (exactly the listed nodes, e.g. a route from `find_routes`), `deallocate_node`, `set_mastery` |
| Tree specs | `add_tree_spec`, `switch_tree_spec`, `rename_tree_spec`, `delete_tree_spec` |
| Cluster jewels | `create_cluster_jewel` (socket, enchant, passive count, notables; validated against what can roll, then allocated) |
| Anoints | `anoint` (any anointable notable on the amulet) |
| Item sets | `add_item_set`, `switch_item_set`, `rename_item_set`, `delete_item_set` |
| Config sets | `add_config_set` (with values), `switch_config_set`, `rename_config_set`, `delete_config_set` |
| Skills | `create_socket_group`, `delete_socket_group`, `add_gem`, `remove_gem`, `replace_gem`, `set_gem`, `set_main_skill`, `set_group_enabled` |
| Items | `equip_unique`, `create_rare` (exact `affixes` by mod id, always valid; or free-text `mods`, checked against the base's mod pool), `equip_item` (pasted item text), `unequip_slot` |
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

   This pulls in `mcp`, `lupa` and `pillow` (for tree pictures). `lupa` ships its own
   LuaJIT, so there's nothing else to install — no system Lua, no luarocks.

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

### Passive tree routing

`get_tree_map` gives the agent the tree in a form it can reason about. Of the tree's ~2,900
nodes, the map keeps notables, keystones, jewel sockets, the class start and the small
passives where paths branch (~970 points); every unbranched run of small passives becomes one
edge with its length and stats:

```
50338 N Ballistics +5 | 20% increased Projectile Speed; 20% increased Projectile Damage; +20 to Dexterity
465 J Projectile Damage and Attack Speed +2 | 4% increased Attack Speed; 10% increased Projectile Damage
465-50338 3 | 3x 4% increased Attack Speed
```

The whole main tree is ~13k tokens with names only; a 10-point region around the current
tree with full stats is ~7k. `find_routes` and `allocate_path` use the same pathing rules as
PoB: no passing through class starts, no continuing out of a mastery, no crossing between the
main tree and an ascendancy.

New builds without a level use PoB's auto-level mode, so the level (and level-based stats)
follows the points allocated. Pass a level to `new_build` to plan at a fixed level.

### How an agent creates a build

1. `new_build` with an ascendancy and level.
2. `search_gems`, then a `create_socket_group` edit with `set_main` for the main skill, plus
   groups for auras, movement and so on.
3. Gear: `search_uniques` + `equip_unique`, or `search_item_bases` + `list_item_mods` +
   `create_rare` with exact `affixes`, which guarantees an item that can exist (right mod pool,
   item level, 3 prefixes / 3 suffixes, one mod per group).
4. Ascendancy notables, keystones and must-have notables. For control over the route, look
   at the area with `get_tree_map`, compare options with `find_routes`, and allocate the
   chosen one with an `allocate_path` edit; `allocate_node` takes the shortest path.
5. `auto_allocate_passives` for the remaining points, then `set_mastery` (each costs a point).
6. `get_warnings`, then `fix_gaps` for resists and attributes.
7. Optionally `plan_leveling`, `trade_search` per slot and `price_check` for the uniques.
8. `export_build` for the share code, or `save_to_pob`.

`auto_allocate_passives` is greedy: it picks the best notable per point one step at a time,
which is fast and usually good, but it doesn't plan long paths toward distant clusters. Guide
it by allocating a few target notables first, or using `avoid`. A full tree takes 15–40
seconds; if it reaches its time limit, the progress is kept and calling it again continues.

### Online features

`trade_search`, `price_check` and `import_character` use the internet; everything else runs
offline.

- **Prices** come from [poe.ninja](https://poe.ninja/docs/api). Responses are cached for 15
  minutes, matching how often poe.ninja updates, and requests identify the app. poe.ninja asks
  that published apps route requests through their own backend; for personal use on your own
  machine this is that backend. Leave these tools unused if you'd rather not call poe.ninja.
- **Trade searches** are returned as links to open in your browser. pob-mcp doesn't query the
  trade site itself.
- **Character import** uses the same public profile pages as PoB's Import tab, so the
  profile's Characters tab must be public. Account names need their `#1234` suffix.

## Build-designer skill

`skills/pob-build-designer/` is a Claude skill that teaches build making: picking a concept
with enough power, scaling damage through the right buckets, layering defenses against
community benchmarks, realistic gear, and spotting inflated PoB numbers. It drives the
tools above step by step and reads game data live (`list_ascendancy_notables`,
`list_keystones`, `list_item_mods`, ...) instead of hard-coding it. The knowledge is
paraphrased from r/PathOfExileBuilds and r/pathofexile threads listed in
`references/sources.md`.

Install it in Claude Code by copying the folder to `~/.claude/skills/` (or a project's
`.claude/skills/`), or upload the packaged `pob-build-designer.skill` in the Claude app
under Settings → Capabilities → Skills. `evals/` holds two test briefs.

## Testing

```
pip install -e ".[test]"
POB_PATH=/path/to/PathOfBuilding pytest            # offline tests (~1 min)
POB_PATH=/path/to/PathOfBuilding pytest -m online  # poe.ninja check
```

The suite drives PoB through every tool area using test builds that ship with PoB, plus one
test that starts the real server over stdio. `.github/workflows/test.yml` runs it on every push
and weekly against the latest PoB, so a league update that breaks something shows up quickly.

## Limitations

- PoE1 / PoB Community only (not PoB for PoE2).
- No character import from the PoE website (load a build code or XML instead).
- Timeless jewel seeds can't be set with the edit ops; edit them in PoB or paste the jewel
  with `equip_item`.
- `create_rare` with free-text `mods` only warns about mods that can't roll (or errors with
  `strict`); use `affixes` for guaranteed-valid items. Influence, veiled, fractured and essence
  mods aren't in the pool; one bench craft can be added with `crafted`.
- `fix_gaps` fills free suffixes on rare gear and cheap tree nodes; it doesn't replace
  existing mods or swap uniques.
- Weapon swap slots are hidden from `list_item_slots`.
