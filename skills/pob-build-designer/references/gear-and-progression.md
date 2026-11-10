# Gear and progression

## Contents
1. Budget tiers
2. Making realistic items
3. Buying order
4. Slot priorities
5. Jewels, cluster jewels and anoints
6. Leveling and progression stages

## 1. Budget tiers

State the tier you designed for. Typical shape:
- **League start / SSF**: campaign uniques and self-crafted or found rares with a few
  good mods each. Resistances and life are the priority. Concepts that need a rare
  unique to function do not work here.
- **Low budget (a few divines)**: core cheap uniques, rares with life + resists + one
  or two useful damage/defense mods, a basic 6-link if needed.
- **Medium (tens of divines)**: good rares with several targeted mods, key build uniques,
  cluster jewels, a better weapon.
- **High / mirror tier**: corrupted and double-influenced items, top-tier crafts. Do
  not present these numbers as what a normal player will see.

Use `price_check` on uniques and `trade_search` on key rares to estimate cost.

## 2. Making realistic items

- Use `list_item_mods(base=...)` for the mods that can actually roll on that base,
  their tiers and ranges. Build rares with `create_rare` using those mod ids.
- A normal good rare has: a few mods at high tiers and the rest at mid tiers. Five or six
  top-tier mods on one item is extremely expensive or impossible. A prefix/suffix limit
  of three each applies.
- Crafted mods: one bench craft per item (unless the item has "can have multiple
  crafted modifiers"). Mark them `crafted` so they count correctly.
- Uniques: use `equip_unique` with the right variant; many uniques have variants or
  ranges, and PoB uses the given roll.
- Influenced/fractured/synthesised mods, corruptions and enchants raise cost a lot; use
  them only for high-budget plans and say so.

## 3. Buying order

A useful default order for a new character:
1. Gear that enables the build (core uniques, the weapon for attack builds, the gem
   links). The build does not function without them.
2. Resistances and attribute requirements, so the character is playable.
3. The biggest defensive hole (max hit, suppression/block, ailment immunity).
4. Damage upgrades by bucket, starting from the bucket that is lowest.
5. Quality-of-life: movement speed, mana sustain, flask uptime.

Buy uniques before rares when both are needed, since unique choices fix which slots
must supply resists and attributes. Keep some resists flexible (rings, amulet, belt,
boots) so later uniques can be fitted. Getting stats from gear rather than tree
points is usually cheaper in the long run.

Jewels found via the trade search with less common mods can be nearly as good as the
obvious mods for much less currency.

## 4. Slot priorities

Rough guide (adjust to the build):
- Weapon: base damage for attack builds; +levels or spell damage for spell builds;
  minion weapon mods for minion builds.
- Body armour: the 6-link host; large defensive base; some bodies give big damage
  implicits.
- Helmet/gloves/boots: life/ES, resists, attributes; enchant-like bonuses at high budget.
- Rings/amulet/belt: resists and attributes filler, plus damage or recovery mods.
- Amulet anoint: `rank_anoints` finds the best notable for any metric.

## 5. Jewels, cluster jewels and anoints

- Regular jewels: life/ES plus damage, or immunities/avoidance on abyss/special bases.
- Cluster jewels: the sockets and notables they provide can carry minion, stacker and
  ailment builds. Use `list_cluster_jewel_options` and `create_cluster_jewel`. For many
  builds their main value is the extra jewel sockets and a few efficient notables.
- Timeless jewels and similar: powerful but complicated; only include them if the
  build needs it and the user is high budget.

## 6. Leveling and progression stages

- `plan_leveling` creates trees for levels 30/50/70/90 from the final tree.
- The campaign usually needs a different skill or setup than the final build; mention
  what to level with when the main skill or core uniques are not available early.
- Use item sets for stages: "Starter", "Mid", "Endgame". Use tree specs for respec
  points (for example before and after getting a key unique).
- Each stage should be playable on its own: capped resists, mana sustain, enough
  damage for the content at that stage.
