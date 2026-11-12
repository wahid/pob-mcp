# Changelog

## 0.6.0

New tools
- `optimize_tree`: exact optimiser for the whole passive tree. PoB measures every node and
  mastery effect, an integer program (HiGHS) picks the best connected tree within the
  points, PoB recalculates it, repeat. `mode` dps / balanced / defence (or your own
  `weights`), with `targets`, `require`, `avoid`, `keep`; rebuilds an existing tree as well
  as filling an empty one. `apply=false` previews the plan and leaves the build as it was.
  On the 19-build tree-rebuild benchmark it matches 17 (median 1.18x the original DPS,
  about 4 minutes per build).
- `target_report`: per target stat its value, slack, cap and over-cap, which allocated picks
  hold it up, and its modifier sources; tightest first.
- `compare_trees`: diff only the passive trees of the loaded build and another, with stat
  ratios.

Optimiser (`auto_allocate_passives`)
- `require`: nodes or `{node, effect}` masteries forced in first; they stay through LNS.
- `profile=true` reports how many PoB recalculations a run made and what they cost.
- LNS: can start from the current tree, keep chosen nodes, search from a tree that misses
  its targets, re-pick mastery effects, randomised multi-refill and region moves; ends on the
  best tree seen.

Other
- New dependencies: `highspy` and `numpy`.
- Tool inputs that set run time or problem size are capped on the server.
- CI workflows run with a read-only `GITHUB_TOKEN`.
- Tree-rebuild benchmark: `--jobs N` runs builds in parallel, one PoB per process.

## 0.5.0

Published on PyPI as `pathofbuilding-mcp` (`pip install pathofbuilding-mcp`); the command is
now `pathofbuilding-mcp`.

Optimiser and tree fixes (from the tree-rebuild benchmark, now 17/19 matches)
- `auto_allocate_passives`: `target_mode` "slack" and "repair"; masteries up to 6 points
  away; default `max_path_cost` 10; Impossible Escape / Thread of Hope nodes cost 1 point;
  runegrafts are candidates and are saved; routes that avoid Unnatural Instinct radii; a
  short finishing pass instead of leaving points unspent at the time limit; LNS counts freed
  points correctly and spends leftovers.
- `reset_tree` keeps jewels in cluster-jewel sockets and the route to Split Personality.
- `get_passive_tree`: `distanceJewels`, and `include_nodes` to list every allocated node.
- `rank_passives(mode="remove")` no longer crashes on builds with runegrafts.

## 0.4.0

New tools
- `load_ladder_build`: load a real poe.ninja ladder character (random, top, by rank, or by
  account and character), filtered by ascendancy and/or main skill. Random picks skip
  characters that look ungeared.
- `popular_builds`: what ladder players run (share of ascendancies, main skills,
  keystones, uniques, anoints, masteries, gems).
- `audit_config`: switches off each enabled Configuration condition and shows what it was
  worth for DPS, EHP and max hits.
- `rank_gear_upgrades`: the best single mod change per rare slot, with resist-cap warnings.
- `read_build_guide` tool, `design_build` / `review_build` prompts and `pob-guide://`
  resources serve the build-design guide to any MCP client (`POB_MCP_GUIDE=0` turns it off).
- `list_ascendancy_notables`, `list_keystones`.

Edit operations
- `create_flask`, `set_flask_active`: magic flasks from mod ids; equipped flasks are on.
- `equip_timeless`: timeless jewels by jewel, seed and conqueror.

Optimiser (`auto_allocate_passives`)
- `targets`: minimum stats met first and never broken afterwards.
- Picks mastery effects; values jewel sockets with planned `jewels` and sockets them;
  `anoint` anoints the amulet without breaking mana or reservation.
- About 2x faster by default (`fast`), with the same results in testing.

Fixes and smaller changes
- `explain_stat` on a damage type shows base, added, increased and more per source type,
  conversion and enemy resistance.
- `allocate_node` refuses to overspend ascendancy points; warns past the passive budget.
- Warning for a large share of reserved life.
- Mod group names given as affix ids point to the right tier ids.
- Data lookups (`search_gems`, `search_item_bases`, ...) work before a build is loaded.

Skill
- `skills/pob-build-designer`: build-design method distilled from r/PathOfExileBuilds,
  with evals.
