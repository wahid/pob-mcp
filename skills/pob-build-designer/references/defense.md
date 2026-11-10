# Defense

## Contents
1. The four ways characters die
2. Layers and what each one stops
3. Reading PoB's defensive numbers
4. Benchmarks by content tier
5. Recovery
6. Ailments, stun and other killers
7. Choosing defenses that fit the offense
8. Defense checklist

## 1. The four ways characters die

Design defense against each of these separately. A build strong in one and absent in
another dies to the absent one.

1. **One big hit** (boss slam, crit from a rare, a large spell). Answered by the size of
   the pool and mitigation of that hit: max hit taken.
2. **Many hits at once** (a pack shotguns you, many projectiles). Answered by avoidance
   that rolls per hit (block, spell block, suppression, evasion) and by recovery triggered
   by being hit or blocking.
3. **Attrition** (sustained damage over a fight, degens). Answered by recovery: regen,
   leech, recovery on hit/block/kill, flasks.
4. **Damage over time and ailments** (ground degens, corrupted blood, freeze/stun locking
   you in place). Answered by DoT mitigation, immunities and recovery that works without
   hitting.

## 2. Layers and what each one stops

- **Pool (life, energy shield, both, mana via mind over matter-type effects, ward)**:
  the base of max hit. Everything else multiplies it.
- **Elemental resistances**: capped (75%) is mandatory for mapping. Maximum resistance
  above 75% is very strong because each point of max res cuts damage taken by a large
  relative amount at high resistances.
- **Chaos resistance**: negative chaos res is a common hole, especially against chaos
  bosses and degens. Aim positive unless the build is immune to chaos.
- **Armour**: reduces physical hits; strong against many small hits, weak against huge
  hits unless armour is very high (tens of thousands for big boss slams). Low amounts
  barely matter for max hit; if a build cannot reach high armour cheaply, other
  physical mitigation is often better.
- **Physical damage reduction from other sources** (endurance charges, guard skills,
  flat % from items/nodes) and **physical taken as elemental** (then hit by your
  resistances) are the main ways to raise physical max hit.
- **Evasion**: rolls per hit against attacks; good multi-hit defense, unreliable against
  single big hits. Pairs well with spell suppression.
- **Spell suppression**: at 100% chance, halves damage from every spell hit. A huge
  layer for builds that can cap it (dex/evasion side of the tree, gear).
- **Block and spell block**: per-hit avoidance up to 75% (higher with max block). The
  strongest answer to many hits, especially with recovery on block. Weak points: it
  does nothing against damage over time and degens, and some boss attacks hit hard
  enough that the 25% that lands still kills. Block builds still need a pool.
- **Damage taken as / damage taken from mana / energy shield before life**: spreads
  hits over more pools; check that the second pool has its own recovery.
- **Less damage taken / reduced damage taken**: rare and strong, often from
  ascendancies, keystones and some uniques.
- **Crit reduction**: monsters' crits can chunk you; reduced extra damage from crits
  helps in juiced maps.

Layering: two moderate layers of different kinds usually beat one maxed layer. A
physical "less" layer plus an elemental one, or block plus a big pool, covers more of
the four death types than stacking a single layer.

## 3. Reading PoB's defensive numbers

- **Max hit taken** (`PhysicalMaximumHitTaken`, `FireMaximumHitTaken`, ...): the largest
  single hit of that type you survive from full. The most useful single number for
  "will I get one-shot?".
- **TotalEHP**: average total damage you take before dying from a stream of hits,
  including avoidance (block, evasion, suppression) and partial recovery. It answers
  "how much punishment can I take?" and reflects multi-hit defense; it can look large
  for avoidance builds that still have a low max hit. Read it next to max hit, not
  instead of it.
- PoB's EHP depends on the enemy damage configuration. The defaults model an average
  enemy; for bosses, set the boss preset (`evaluate_scenarios`) and, if you want, the
  enemy crit chance/damage in config to see the effect of crits.
- Avoidance shows as an average; a 75% block build still takes the full hit 25% of the
  time.

## 4. Benchmarks by content tier

Community rules of thumb. They vary between players and patches; treat them as ranges,
and state in your answer which ones you aimed for.

| Content | Elemental max hit | Physical max hit | Notes |
|---|---|---|---|
| Entry white/yellow maps | ~15k | ~5k | Capped resists matter more than any number |
| Comfortable T16 mapping | ~30k+ | ~10k+ | Plus an answer to multi-hits |
| Pinnacle bosses | ~50k | ~15-20k | Know each boss's damage type |
| Juiced maps / uber bosses | ~75-100k | ~30k+ | Plus capped suppression or block, high recovery |

Chaos max hit: keep it well above zero, roughly in line with physical for most content;
some bosses and map mods hit hard with chaos.

A common rough ratio: physical max hit around a third of elemental max hit is normal
because there are fewer physical mitigation sources.

## 5. Recovery

Recovery is what makes a build feel tanky between hits.
- A useful target is regaining a meaningful share of the pool per second in combat
  (around a quarter of the pool per second is solid; top-end builds reach their full
  pool per second through recovery on hit/block/kill).
- Recovery that scales in combat (life gain on hit, leech, recovery on block,
  recovery-on-kill) handles juiced content better than flat regen.
- Leech does not work for damage over time skills; DoT builds need regen or other
  recovery.
- Energy shield builds rely on recharge, regen or ES leech; check which applies.
- Flasks: life flasks, plus utility flasks that grant immunities, armour/evasion or
  damage. Plan flask uptime (flask charge generation, or effects that keep them up).

## 6. Ailments, stun and other killers

- **Freeze**: being frozen is often lethal. Immunity or high avoidance (from flask
  suffixes, uniques, ascendancy, passive nodes) is a priority for anything beyond
  campaign.
- **Shock**: you take increased damage. Avoid or reduce effect.
- **Ignite/bleed/poison on you**: answered by immunities (flask suffixes cover many),
  ailment avoidance and chaos resistance for poison. Corrupted blood (stacking bleed
  from some monsters/maps) needs an immunity source.
- **Stun**: melee builds that get stunned lose uptime and die. Stun immunity or high
  stun threshold/avoidance.
- **Curses on you**, **reduced recovery map mods**, **reflect**: check what the build
  cannot run; mention it as a weak spot instead of hiding it.
- Ailment immunity options exist on flasks, uniques, ascendancies, auras and
  passive masteries; look them up with `list_item_mods`, `search_uniques` and
  `find_passive_nodes` (search words like "immune", "cannot be frozen", "avoid").

## 7. Choosing defenses that fit the offense

Start with the defense native to where the build's mandatory nodes already are:
- Strength side: life, armour, endurance charges, life regen.
- Dexterity side: evasion, spell suppression, movement speed.
- Intelligence side: energy shield, mana, spell block.
- Shield users: block and spell block naturally.
- Ascendancies supply the biggest layers (less damage taken, immunities, block,
  recovery). Read `list_ascendancy_notables` with defense in mind too.

Then layer something of a different kind on top: evasion builds add armour from a
hybrid base or a guard skill for physical max hit; armour builds add block or
suppression for multi-hits; ES builds add block, ward or a big recovery source.

Some unique/keystone choices close off defenses (no armour on a unique chest, a
keystone that removes life). Note those trade-offs when choosing the concept.

## 8. Defense checklist

Run before reporting any build:
- [ ] Fire/cold/lightning at 75% (or more for maps with less max res); chaos resist
      not deeply negative.
- [ ] Max hit of each type meets the benchmark for the stated content.
- [ ] At least one per-hit avoidance layer (block, suppression, evasion) or very high
      recovery on hit.
- [ ] Recovery adequate for the pool; works for the build's damage type (leech vs DoT).
- [ ] Freeze, stun and, for higher content, corrupted blood handled.
- [ ] Life is not mostly reserved (unless a deliberate low-life build): reserved life
      does not count toward max hit or EHP.
- [ ] Flasks equipped and their uptime plausible.
- [ ] Mana sustain works: cost per use against regen/leech; no running out mid-fight.
- [ ] Movement skill and guard skill present.
- [ ] `get_warnings` is empty or every warning is explained.
