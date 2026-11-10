# pob-mcp — Path of Building MCP server

An MCP server that runs **Path of Building Community** (Path of Exile 1) headlessly, so Claude
(or any MCP client) can read builds, get real PoB numbers, test changes and export the result.

It doesn't re-implement any game math: it boots PoB's own Lua code inside an embedded LuaJIT
(via [`lupa`](https://pypi.org/project/lupa/)), so every number matches what the PoB app shows
for the same build and config.

## Tools

| Tool | What it does |
|---|---|
| `load_build` | Load a share code, pobb.in / pastebin / poe.ninja / poedb link, raw XML, or a `.xml` file |
| `new_build` | Start an empty build |
| `get_build_summary` | Class, ascendancy, level, main skill + headline stats |
| `get_stats` | Any stat from PoB's output table, by exact name or substring filter |
| `get_skills` | Socket groups and gems (indexes used by edits) |
| `get_items` | Equipped gear, flasks and tree jewels, with full item text |
| `list_item_slots` | Valid slot names |
| `get_passive_tree` | Keystones, notables, masteries, ascendancy nodes, tree URL |
| `find_passive_nodes` | Search nodes by name or stat text, sorted by path cost from your tree |
| `get_config` / `list_config_options` | Current Configuration tab values / all options and choices |
| `compare_changes` | **What-if**: apply edits, report stat deltas, then restore the build |
| `apply_changes` | Apply edits and keep them |
| `undo_last_change` | Roll back the last `apply_changes` |
| `export_build` | Share code or XML, optionally saved to a file |

Edit operations (used by `compare_changes` / `apply_changes`):
`allocate_node`, `deallocate_node`, `equip_item`, `unequip_slot`, `add_gem`, `remove_gem`,
`replace_gem`, `set_gem`, `set_main_skill`, `set_group_enabled`, `set_config`, `set_level`,
`add_custom_mod`.

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
- "Which of these three notables gives the most DPS per point: …?"
- "Compare my current ring with this one: (pasted item text)."
- "Swap Controlled Destruction for Elemental Focus in my main link and show the difference against a Pinnacle boss."
- "Apply those changes and give me the new share code."

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

## Limitations

- PoE1 / PoB Community only (not PoB for PoE2).
- No character import from the PoE website (load a build code or XML instead).
- Mastery selection, cluster jewel contents and timeless-jewel seeds are read from the
  build but can't be edited with the edit ops; edit them in PoB, or use `add_custom_mod`
  to approximate a stat.
