---
name: pob-build-designer
description: 'Path of Exile 1 build workshop on live Path of Building math (pob-mcp tools). Use this skill before answering any message where a PoE player wants a character made, chosen or fixed: recommending league starters or comparing build options; designing a build around a skill, unique, ascendancy or playstyle such as totems or minions; working within SSF, hardcore or divine budgets; or diagnosing a real character (low DPS, deaths, weak defenses, what to upgrade next, reviewing a pobb.in link or guide). It reads game data live and calculates real DPS, EHP and max hit, which beats answers from memory that go stale every league. Load it even for short, slangy messages such as "my RF jugg keeps dying" or "comfy totem starter, 2 div". Not for build-free trivia (mechanic definitions, quests, atlas strategy), creative writing, or programming on PoB files or the pob-mcp codebase.'
---

# PoE build designer

This skill teaches how experienced Path of Exile build makers think, and how to turn
that thinking into a real, calculated build with the pob-mcp tools. Path of Building
(PoB) does the arithmetic; your job is the judgment: picking a concept that has the
power to begin with, scaling it along few strong axes, layering defenses so nothing
one-shots the character, and keeping every number honest.

Game data changes every league. Never rely on remembered stat values, node names or
item mods. Read them live: `search_gems`, `list_ascendancy_notables`, `list_keystones`,
`search_uniques`, `list_item_mods`, `find_passive_nodes`, `list_mastery_effects`,
`price_check`. If you are unsure whether something still exists or still works the
way you remember, check it with a tool before building on it.

## Reference files (read when the step needs them)

- `references/damage-scaling.md`: damage formula, buckets, hits vs ailments vs DoT,
  crit, conversion, penetration, minions/totems/traps. Read before choosing how a
  concept scales, and whenever DPS is lower than expected.
- `references/defense.md`: defense layers, max hit, EHP, recovery, ailments, benchmarks
  per content tier. Read before the defense pass, always.
- `references/archetypes.md`: common build archetypes, what each one needs, and its
  usual weak spots. Read while picking a concept.
- `references/gear-and-progression.md`: realistic gear, budget tiers, buying order,
  leveling. Read before creating items and before writing the progression section.
- `references/pob-pitfalls.md`: where PoB numbers lie (config, uptime, conditional
  supports) and how to sanity-check a build or a guide. Read before reporting numbers.
- `references/sources.md`: the community threads this knowledge was distilled from.

## The design loop

Work through these steps in order. Each one has tools that make it concrete. Use
`compare_changes` to test an idea before `apply_changes`, and `undo_last_change` freely.
An `apply_changes` batch is all-or-nothing: if one edit fails (a misspelled gem or base),
nothing in the batch is applied, so fix the named edit and resend the batch.

### 1. Pin down the brief

You need to know, or sensibly assume and state:
- **Content goal**: league start / campaign, general mapping, juiced maps, pinnacle
  bosses, uber bosses, or "everything".
- **Budget**: league-start (near zero), a few divines, tens of divines, mirror tier.
- **Playstyle**: melee / ranged / caster / minion / totem-trap-mine / DoT; fast clear vs
  slow and tanky; any skill, unique or ascendancy they insist on.
- **Mode**: softcore/hardcore, trade/SSF. Hardcore and SSF push you toward stronger
  defenses and gear that can be crafted or found rather than bought.

If the user gave only a skill, assume softcore trade, mapping plus pinnacle bosses,
moderate budget, and say so at the top of your answer.

### 2. Choose a concept that is strong before you add anything

The most common failure in homemade builds is a concept that is weak from the start;
everything after that is swimming upstream. Every comfort you add later (defenses,
movement, clear) costs power, so the core must have power to spare.

A strong core usually has:
- **One main scaling vector, or several that are colinear**: one stat or mechanic that
  feeds several damage buckets at once, ideally defense too (for example a stacked
  attribute that grants flat damage, increased damage and a defensive pool). "100 of
  one thing beats 10 of ten things."
- **Big base damage** (see damage-scaling.md). Everything else multiplies it.
- **An ascendancy that multiplies the core** rather than adding unrelated value. Read
  the candidates with `list_ascendancy_notables` (filter by ascendancy when you can;
  the full list is large) and compare which ones touch the build's damage type,
  delivery and defense.
- **A natural defense nearby** on the tree, or one that comes with the scaling vector.

Avoid, unless the user wants it: splitting damage across attacks and spells, mixing
hit and DoT scaling, stacking many unrelated uniques, mechanics that need setup the
character cannot sustain.

Use `search_gems` for the skill's tags and base stats, `search_uniques` for build-enabling
items, `list_keystones` for keystones that change the rules. `popular_builds(ascendancy,
skill)` shows what this league's ladder players of that skill or ascendancy rely on
(keystones, uniques, anoints, masteries, gems); use it as a sanity check and a source of
ideas, not as a template. The ladder favours strong, expensive builds, and a build that
only works with the same mirror-tier uniques is not a fit for a modest budget. Sketch two or three
candidate concepts in a sentence each when the brief is open, pick one and say why.

### 3. Skeleton in PoB

1. `new_build(ascendancy=..., level=90)`. Plan at a fixed level (90 is a good default
   endgame target; 95+ for ubers); auto-level makes stats shift while you work.
2. `apply_changes` with `create_socket_group` for the main skill (`set_main: true`) and
   its likely supports. Then `rank_support_gems(replace_gem_index=...)` to test each
   slot. Prefer supports whose bonus is unconditional over ones PoB may overrate (see
   pob-pitfalls.md).
3. Add auras, heralds, curses, guard skill, movement skill and a mana/flask plan as
   socket groups. Watch reservation: `get_warnings` reports over-reservation and a
   large share of reserved life. Supports or effects that move reservation onto life
   shrink the pool that max hit and EHP use; only do that on purpose (low-life builds).
4. Set **realistic configuration** before reading any DPS (`audit_config` afterwards shows
   what each enabled condition is worth): `list_config_options`,
   then `set_config`. Use config sets (`add_config_set`) for "Mapping" and "Boss", and
   set `enemyIsBoss` explicitly in each ("None" for mapping, "Pinnacle" or "Uber" for
   bosses): PoB's own default may already be a boss.
   Only enable conditions the build actually maintains: charges it really generates,
   buffs it really has up, enemy conditions it really applies.

### 4. Gear baseline

Equip what a player at the target budget would really own (gear-and-progression.md):
- Build-enabling uniques with `equip_unique`. Check cost with `price_check`.
- Rares with `create_rare` using real mod ids from `list_item_mods` for that slot and
  base, at tiers a player can buy. A rare with five top-tier mods it cannot roll
  together is a fantasy item; do not use one to make a build look good.
- Base names must match PoB's (some carry a suffix, such as hybrid boots); look them
  up with `search_item_bases` before `create_rare`.
- Flasks: a life flask plus utility flasks (movement, defense, immunity suffixes), made
  with `create_flask` from `list_item_mods(base="Jade Flask")`-style mod ids. Equipped
  flasks are switched on; turn off any the build cannot keep up with `set_flask_active`.
  Without flasks PoB shows lower defense and damage than the real character has.
- Resistances: cap fire/cold/lightning at 75% (PoB already applies the post-campaign
  resistance penalty by default) mostly from gear, so the tree can go to damage and
  defense. Some overcap protects against maps with reduced resistances or curses.

### 5. Passive tree

1. `allocate_node` the ascendancy notables chosen in step 2, then the keystones.
2. `get_tree_map` once to see the tree's structure, then `find_routes` /
   `allocate_path` toward the clusters that serve the concept. Route through nodes
   that are useful on the way.
3. `auto_allocate_passives(weights=...)` to fill remaining points toward a weighted
   goal (for example main DPS plus TotalEHP plus Life), with `avoid` for anything
   you rejected. The optimiser is greedy and follows the weights literally, so give it
   the defense benchmarks as `targets` (for example `{"PhysicalMaximumHitTaken": 10000,
   "LightningMaximumHitTaken": 30000}` for T16 mapping): it closes those gaps first,
   then spends the rest on the weights without breaking them. If it reports a target
   it could not reach, the gap has to come from gear, flasks, gems or config.
   Avoidance layers that only pay off at their cap need their own target: spell
   suppression halves spell hits only at 100%, so one suppression node shows no gain and
   the optimiser never starts on it. Add `"SpellSuppressionChance": 100` (or a
   `BlockChance` / `SpellBlockChance` level) when the build is meant to have that layer.
   Targets cost damage. When the defense targets eat most of the tree, run the optimiser
   on fresh tree specs (`add_tree_spec` with `copy: false`; check `ascendancyPointsUsed`
   afterwards and re-allocate the ascendancy if needed) at two or three target levels
   and compare: that trade-off curve tells you, and the user, whether the gear and budget
   can support both goals. If even zero targets leave DPS short of the benchmark, the gear
   or the concept is the bottleneck, not the tree; say so and name the upgrades that move it.
   The optimiser also picks mastery effects (1 point each) as it goes. Pass the jewels
   you plan to use as `jewels` (create_rare specs, e.g. a Viridian Jewel with life and
   the build's damage/crit mods) and it values jewel sockets with them and sockets them;
   `anoint: true` anoints the amulet at the end without breaking mana or reservation.
   Planned rare jewels and masteries were worth about 2x boss DPS at the same defense
   targets in testing, so use them.
   When the damage nodes also feed the targets (strength, life or attribute stacking),
   try `target_mode: "slack"` as well: it chases damage while there are spare points and
   only turns to the targets when the points left get tight; compare it with the default.
   Split Personality grows with the socket's distance from the class start
   (`get_passive_tree` shows it under `distanceJewels`), so a shorter route weakens it.
   For tight builds (several targets close together), pass `lns_rounds: 15`: it drops the
   weakest branches and refills them, which is how the best rebuilds in testing were found.
   If time allows, repeat it yourself: deallocate 1-5 low-value notables, rerun the
   optimiser with the same targets on the freed points, and keep the result only if it is
   better. Check masteries whose condition the gear meets (for example armour + ES on every
   armour piece); they can be worth more than any notable.
4. Jewel sockets on the tree become item slots named `Jewel <node id>` once allocated
   (`list_item_slots` lists them). An empty socket is wasted points. Timeless jewels go in
   with `equip_timeless` (jewel, seed, conqueror).
5. Check any masteries you allocated yourself (`list_mastery_effects`, `set_mastery`).
6. `fix_gaps` to repair attributes and resists cheaply, then `get_warnings`.
7. `rank_passives(mode="remove")` to find dead weight; `rank_passives(mode="add")`
   per metric to find what to take instead. `rank_anoints` for the amulet.

### 6. Defense pass (do not skip)

Read defense.md, then check with `get_build_summary` and `evaluate_scenarios`:
- Max hit taken per damage type versus the benchmark for the content goal.
- Capped elemental resists; chaos resistance not deeply negative (unless CI or
  similar).
- At least one answer to many hits at once (block, suppression, evasion, high
  recovery on hit) and one answer to big single hits (pool size, mitigation).
- Recovery that keeps up: check life/ES regen and leech numbers against the pool.
- Answers to freeze/stun/shock/bleed/corrupted blood appropriate to the content.

Fix the biggest hole first. A build with 30% more damage and one gaping weakness
usually performs worse than one with balanced layers.

### 7. Damage pass

With defenses in place, raise damage: `explain_stat` on the main damage type (for
example `"Lightning"`) shows base and added damage, the increased and more multipliers
for each source damage type (including conversion), and the enemy-resistance multiplier; `"AverageHit"` shows the crit split and `"Speed"` the rate.
Find the bucket that is lowest relative to its potential and invest there. Re-run `rank_support_gems` and `rank_passives` after big changes;
rankings shift as buckets fill.

### 8. Progression, cost and export

- `plan_leveling` for 30/50/70/90 trees when the user will level the build.
- Item sets for budget stages (`add_item_set`): starter gear, then upgrades.
- `rank_gear_upgrades` for the next purchases: the single mod change per rare slot that
  gains the most (with what it replaces and any resist it would uncap). Then
  `trade_search` for those items and `price_check` for the uniques, to estimate cost.
- `render_tree` if the user wants to see the tree.
- `export_build` for a share code; `save_to_pob` to drop it into the PoB Builds folder.

## What to report

Give the user a build they can trust, not a sales pitch:

```
## <Build name>: <skill> <ascendancy>
**Concept**: one or two sentences on why the core is strong.
**Assumptions**: content goal, budget, level, mode (and that you assumed them, if so).
**Numbers** (state the config: mapping vs boss, which buffs/charges are on):
  DPS mapping / boss, life/ES, max hit by type, EHP, resists, suppression/block.
**Defense layers**: what handles big hits, many hits, DoT and ailments.
**Gear**: key uniques with rough prices, rare targets per slot.
**Progression**: what to buy first and what each upgrade adds.
**Weak spots**: honest list of what will kill it or what it struggles with.
**PoB code**: <code>
```

Always state which config the numbers came from, flag any number that depends on a
condition the player must maintain, and name the build's weak spots. A smaller honest
number is more useful than a large one the player will never see in game.

## Judging an existing build or guide

`load_build` the link or code (or `load_ladder_build` for a real ladder character from
poe.ninja), then work through pob-pitfalls.md (config, gear realism, uptime) and the
defense checklist above. `audit_config` switches off each enabled condition and shows
what it was worth, which is the fastest way to find inflated numbers; then judge which
of those the build really maintains. `rank_gear_upgrades` finds the best gear fixes.
Report what is inflated, what is missing, and the two or three changes with the biggest
effect, each tested with `compare_changes` so you can quote the real difference.
