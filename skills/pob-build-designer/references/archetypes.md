# Archetypes

Each archetype has a typical scaling path, a natural defense and predictable weak
spots. Use this to pick a concept and to know what to check. Specific skills, items and
ascendancies change by patch, so confirm them with the tools before committing.

## Self-cast hit spells
- **Scales with**: gem level, spell crit (if crit), cast speed, more multipliers from
  supports, elemental penetration/exposure, "as extra" damage.
- **Natural defense**: energy shield or life + spell block, or mind-over-matter style
  mana layering on intelligence side.
- **Weak spots**: physical max hit (few physical mitigation sources), mana sustain,
  standing still while casting.

## Attack (melee and bow)
- **Scales with**: weapon base damage and attack speed first, then flat added damage,
  crit, more multipliers, impale or "as extra".
- **Natural defense**: melee - armour, life, block (with shield) or endurance charges;
  bow - evasion, suppression.
- **Weak spots**: accuracy (check `HitChance`), melee range and stun, bow builds' lower
  physical max hit, heavy reliance on one expensive weapon.

## Damage over time (spells and skills like degens)
- **Scales with**: damage over time multiplier, skill gem level, increased damage, curse
  effect and resistance reduction, duration for stacking DoTs.
- **Natural defense**: any; DoT builds do not need to hit, so the tree budget can go to
  defense. They often end up tanky.
- **Weak spots**: no leech (needs regen), slow clear on some skills, no penetration.
- PoB numbers are reliable for DoTs because there is no hit chance or crit variance.

## Ailment builds (ignite, poison, bleed)
- **Scales with**: the hit's base damage, ailment-specific modifiers, damage over time
  multiplier, duration (poison), hit size (ignite/bleed), crit or crit-to-DoT keystones.
- **Weak spots**: confusing hit modifiers with ailment modifiers (see damage-scaling.md);
  ignite and bleed usually only take the strongest instance; poison needs a high hit
  rate.

## Minions
- **Scales with**: minion gem level, minion damage/speed modifiers, minion supports,
  buffs that affect minions, minion-specific gear.
- **Natural defense**: the player can invest in defense freely because player damage
  stats do nothing. Minions also absorb attention from monsters.
- **Minion survival**: minion life, minion resistances, block or taunt effects, regen,
  and auras/buffs. Weak minions die and DPS falls.
- **Weak spots**: minion damage often drops in heavily juiced content; minions' AI and
  pathing; some bosses' mechanics kill minions.

## Totems, traps, mines, brands
- **Scales with**: the player's damage modifiers plus delivery-specific modifiers
  (totem/trap/mine/brand damage, throwing/placement speed, number of totems).
- **Weak spots**: uptime: time spent placing, totems dying, traps not triggering, brands
  needing reattachment. PoB shows the damage when everything is in place. On-hit effects
  of the player (leech, life gain) often do not apply.

## Triggered skills (cast on crit, cast when..., trigger wands, etc.)
- **Scales with**: trigger rate (limited by cooldowns), the triggered spell's level and
  supports, and the triggering attack's rate.
- **Weak spots**: config; check the trigger rate PoB reports and whether its
  assumptions (crit rate, hits per second) hold.

## Stackers (attribute, accuracy, charges, life/ES stacking)
- **Scales with**: one stat that feeds several buckets at once via specific uniques or
  notables; growth is fast once that stat is high.
- **Weak spots**: expensive core items, little room for flexibility, early levels
  before the stacking items arrive. Check the start: a stacker with half its items is
  often weak.

## Choosing quickly
- Hardcore / SSF / league start: DoT, minion or tanky melee concepts with cheap
  core items are forgiving.
- Fast clear: projectile or chaining skills, large area skills, high movement speed,
  on-kill explosions.
- Bossing: single-target multipliers, consistent uptime, strong max hit.
- "I want to play X": keep X and choose the ascendancy and defenses that best support
  it; tell the user honestly where X will struggle.
