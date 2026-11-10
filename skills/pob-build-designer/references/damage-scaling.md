# Damage scaling

## Contents
1. The formula and its buckets
2. Base damage first
3. Increased vs more
4. Colinear scaling and stackers
5. Crit: when it is worth it
6. Lowering enemy defenses
7. Hits vs ailments vs damage over time
8. Conversion and "as extra"
9. Delivery: self-cast, attacks, minions, totems, traps, mines, triggers
10. How much DPS is enough
11. Diagnosing low damage with the tools

## 1. The formula and its buckets

For a hit build, damage per second is roughly:

```
(base + added flat) x (1 + sum of increased) x (product of more) x crit factor
  x hit rate (speed x hit chance) x enemy damage taken / resist / mitigation
```

Each bucket multiplies the others, so the gains from one bucket depend on how full
the others are. Adding 50% increased damage to a build that already has 500% increased
is about +8%; adding 50% to a build with 100% is +25%. Investment has diminishing returns
inside a bucket and full returns across buckets. When choosing where to put a point, an
item mod or a gem slot, put it in the bucket that is lowest relative to what is
reachable. `explain_stat` shows each bucket's current total.

## 2. Base damage first

Base damage is the biggest number in the formula and every other bucket multiplies it.
Builds stuck at a few hundred thousand DPS have often ignored it.

- Attacks: weapon physical/elemental damage and attack speed are the base. A better
  weapon is often the largest single upgrade. Flat added damage to attacks matters a
  lot for attack builds and little for spells.
- Spells: gem level is the base. +levels to the gem (from gear, supports like Empower
  equivalents, quality) raise it a lot because spell base damage grows steeply with
  level. Flat added spell damage helps less than gem levels for most high-level spells.
- Extra base from interactions: some uniques and mods add a share of one stat as flat
  damage, or add damage per attribute/charge. These can multiply base damage, which is
  why many strong builds are built around them.
- Damage-over-time skills have their own base per second, also scaled by gem level.

## 3. Increased vs more

- "Increased"/"reduced" all add into one pool. Generic increased damage on the tree is
  weak once that pool is large.
- "More"/"less" multiply separately. Support gems, ascendancy notables, keystones and some
  uniques provide them. They are the most valuable damage sources.
- Damage taken modifiers on the enemy (shock, withered, exposure-like effects, "enemies
  take increased damage") form their own bucket and are strong for that reason.

## 4. Colinear scaling and stackers

The strongest builds find a stat where stacking it scales several buckets at once,
often defense too. Examples of the pattern (verify the current items and nodes with
tools, they change by patch):
- an attribute that grants flat damage via one item, increased damage via another and
  a defensive pool on its own;
- accuracy or charges that grant flat damage, speed and crit;
- life or energy shield stacking where the pool also feeds damage.

When a stat feeds three multipliers, stacking it grows damage roughly with the cube of
the investment. Look for these when designing; they let a build "just stack one thing".
Stackers often skip crit or tree damage because the stacked stat already supplies it.

## 5. Crit: when it is worth it

Crit adds a bucket (chance x multiplier) but costs points, gear mods and often a
support slot, and it needs accuracy for attacks.

- Worth it for hit builds that can reach high chance (roughly half of hits or more)
  with good multiplier. A small amount of crit is usually worse than putting the same
  investment elsewhere.
- Usually not worth it for: damage over time and ailment builds (crit multi does not
  scale ailments unless a specific keystone makes it, see section 7), builds with "never
  crit"/"cannot crit" mechanics, stackers whose scaling already fills the buckets.
- Crit also enables on-crit effects (freeze/shock application, charge generation),
  which can be the real reason to take it.
- For attacks, accuracy counts twice for crit builds (hit chance and the crit roll
  are both affected), so check `HitChance`.

## 6. Lowering enemy defenses

Enemy resistance and damage taken are a bucket that is easy to forget:
- Curses that lower resistances, exposure, and penetration are often worth more than
  any single damage node, especially against bosses with base resistances.
- Shock, wither, intimidate and similar effects increase damage taken.
- Bosses have resistances and less effect of curses; set the config to boss and look at
  the difference with `evaluate_scenarios`.
- Penetration applies only to hits, not to damage over time or ailments. Resistance
  reduction (curses, exposure) applies to both.

## 7. Hits vs ailments vs damage over time

These scale differently. A build should be clear about which one it is.

- **Hits** scale with everything in section 1, including crit and penetration.
- **Ailments from hits** (ignite, bleed, poison) take the hit's base damage and are
  scaled by modifiers that apply to the ailment: generic damage, damage-type
  modifiers, damage over time and damage over time multiplier, and ailment-specific
  modifiers. Modifiers that apply only to hits do not apply. Rules to remember:
  - Hit-only modifiers (attack/spell damage, projectile/area damage on the hit,
    "with hits") do not scale the ailment in current mechanics. Check with
    `explain_stat` rather than assuming.
  - Penetration and "lucky damage" on hits do not carry over.
  - Crit multiplier does not apply to ailments unless a specific keystone converts it
    into damage over time multiplier for ailments.
  - Crit chance can still matter for ailments via ailment chance or crit-only
    application mechanics.
  - Ignite and bleed are usually single-instance (the strongest one applies), so hit
    size matters more than hit speed; poison stacks, so hit rate and duration matter.
- **Pure damage over time skills** scale with damage over time multiplier and the
  skill's own base; hit-only modifiers do nothing. Their damage is steady and does not
  miss, which makes PoB numbers unusually reliable for them, but they cannot leech.

A practical sign of confusion: a build stacking attack damage and crit multi but whose
DPS is mostly ignite. Use `get_stats(filter="dps")` to see where damage actually comes from.

## 8. Conversion and "as extra"

- Conversion changes damage type in a fixed order (physical to lightning to cold to
  fire to chaos). Converted damage benefits from modifiers for every type it passed
  through, which can double-dip on modifier pools.
- "Gain X% of Y as extra Z" adds damage on top without removing the original. These
  mods are among the strongest scaling in the game because they multiply base damage.
- Conversion can cause resistances and penetration to apply against a different
  resistance; check the final type distribution in `explain_stat`.

## 9. Delivery

- **Self-cast spells / attacks**: player damage modifiers apply. Speed is a bucket.
- **Minions**: only modifiers that say "minions" (and minion supports) scale them; the
  player's own damage modifiers do nothing. Minion builds scale through minion gems,
  minion gear (helmet, weapons, amulet), and auras/buffs that minions share. PoB reports
  minion DPS separately (`Minion.CombinedDPS`).
- **Totems, traps, mines, brands**: player damage modifiers apply, plus delivery-specific
  ones. The player is not the one using the skill, so on-hit-by-you effects may not
  apply. Uptime and placement time matter (pob-pitfalls.md).
- **Triggered skills**: damage depends on trigger rate and cooldowns, which PoB models but
  which are easy to misconfigure.

## 10. How much DPS is enough

Community rule-of-thumb benchmarks (sustained, realistic config, not optimistic "all
buffs up" numbers); treat them as ranges, not exact thresholds:
- Early maps: ~150k+ DPS feels fine.
- Comfortable T16 mapping: ~1-3M for hit builds; DoT builds feel fine with less because
  damage does not miss, roughly 1M.
- Pinnacle bosses: several million sustained.
- Uber pinnacle bosses: around 10M+ sustained helps a lot; less is possible with very
  strong defenses and skill.
Clear speed in maps depends more on area coverage, projectile count, chaining, movement
speed and on-kill effects than on single-target DPS. A boss-killer and a mapper scale
differently; say which one you designed.

## 11. Diagnosing low damage with the tools

1. `get_stats(filter="dps")`: which component carries the damage (hit, ignite, poison,
   minion)?
2. `explain_stat` on the main damage type ("Physical", "Lightning", ...): base vs added
   damage, total hit after increased/more, and the enemy-resistance multiplier. Then
   `"AverageHit"` (crit split), `"Speed"`, `"CritChance"`. Totals like CombinedDPS have
   no breakdown of their own.
3. `rank_support_gems(replace_gem_index=...)` on each support: is a weak gem in the link?
4. `rank_passives(mode="add")` and `mode="remove"`: what nodes are better per point?
5. Check config (pob-pitfalls.md): are boss-relevant debuffs (curse, exposure, shock)
   actually configured?
6. Check base damage: weapon, gem level, flat added, "as extra" sources.
