# Hierophant (Storm Burst totems) tree rebuild: notes

**Final CombinedDPS: 2,576,584** (the original character had 2,738,313, so this is about 94% of it). 123 of 123 passive points used, no warnings (same as `originalWarnings` = []).

## Minimums vs final
| Stat | Minimum | Final |
|---|---|---|
| PhysicalMaximumHitTaken | 19625 | 20045 |
| Fire/Cold MaximumHitTaken | 60258 | 61421 |
| LightningMaximumHitTaken | 57848 | 58964 |
| ChaosMaximumHitTaken | 18615 | 18788 |
| ManaUnreserved | 3195 | 3229 |
| LifeUnreserved | 3381 | 3408 |
| Str req (Vortex Ward body) | 173 | 174 |

The kept picks are all still allocated: ascendancy (8 points), the 4 keystones and the 6 jewel sockets. The Items, Skills and Config sections are byte-identical to the reset file.

## What I tried
1. `auto_allocate_passives` with strict targets (all minimums plus Str 173 to clear the Vortex Ward requirement): 1.91M. Strict targets push life, mana and armour first.
2. Blended `target_weight` sweep: 0.05 reached 4.8M but missed every defence target. 0.1 reached 2.37M but was 323 short on physical max hit. 0.12 and above were feasible but gave only 1.9 to 2.0M.
3. Pre-allocating DPS before the target fill did worse (1.7 to 1.8M).
4. Mastery checks:
   - Totem Mastery "5% of Damage from Hits is taken from your nearest Totem's Life" adds about 5% to every max hit. With it preset, the best feasible run was 2.37M.
   - **Armour and Energy Shield Mastery (node 30393), "100% increased Armour and ES from Body Armour if Helmet/Gloves/Boots all have Armour and ES"**, fits the all-Paladin gear. For 3 points it gave +2.4k physical and +5.4k elemental max hit. The optimiser never found it on its own.
5. Repair passes on near-feasible trees: swap brute force, and delta-estimate combos verified in PoB. Best was 2.42M without the AES mastery.
6. Swapped Foresight, Sanctity and Dynamo for the AES mastery. Then ran a large-neighbourhood search: drop 1 to 7 notables, refill with the target-constrained optimiser, and keep any feasible improvement. This reached **2.577M**. Later rounds stayed at the same value.

## Final tree
- **Masteries:** AES body-armour mastery, Totem crit (40%), Lightning 40% physical-to-lightning conversion, Life 15% (no life mods on body armour), Duration 10% more, Crit +25% multiplier vs uniques.
- **Notables:** Annihilation, Arcane Chemistry, Arcane Potency, Arcanist's Dominion, Constitution, Crackling Speed, Cruel Preparation, Deep Thoughts, Discipline and Training, Enduring Bond, Exceptional Performance, Faith and Steel, Forethought, Heart and Soul, Light of Divinity, Lightning Walker, Mental Rapidity, Might, Path of the Warrior, Purity of Flesh, Reflexes, Retribution, Sentinel, Shaman's Dominion, Shaper.

## Binding constraints
Every minimum is within about 1 to 2%: life 0.8%, chaos 0.9%, mana 1.1%, physical 2.1% and elemental 1.9% over its minimum. Life is the hardest one to satisfy.
