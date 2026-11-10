# Saboteur (Blade Blast of Unloading): passive tree rebuild notes

**Final CombinedDPS: 16,913,644** (the original character had 18,551,839, so this is 91% of it). Points used: 123/123. All kept nodes (ascendancy, keystones, jewel sockets, cluster notables) are still allocated. The only warning is "Lightning resistance is 90% (cap 94%)", which was already in originalWarnings. The reset build's Int, mana and over-reservation warnings are gone.

| Stat | Final | Minimum |
|---|---|---|
| PhysicalMaximumHitTaken | 15,744 | 15,594 |
| Fire/Cold/LightningMaximumHitTaken | 59,249 | 58,707 |
| ChaosMaximumHitTaken | 30,809 | 30,527 |
| ManaUnreserved | 67 | 11 |
| LifeUnreserved | 5,807 | 4,995 |
| Int (requirement 237) | 277 | 237 |

## What limits the build
- Max hit is the real limit, not the life minimum. Lightning Melding sets every elemental max res to 89-90, so elemental damage taken is fixed. Chaos max hit (75% chaos res cap) then needs a pool of about 6,490. That takes roughly 33 or more points of life nodes.
- Mind Over Matter combined with the kept Eldritch Battery adds ES and unreserved mana to the pool. That made the life nodes cheaper and was worth about +0.8M DPS compared with the best build without MoM (15.0M).
- Small "6% life" and "Life and Energy Shield" passives are cheaper per point than the life notables. The optimiser only became competitive with `include_small=true`.

## What I tried
1. Strict auto_allocate with every minimum (plus Int 237) as targets, notables only: 8.4M. Blended target_weight 0.3: 11.8M. Lower weights missed the targets.
2. Two phases (DPS points first, then targets): 13.5M was valid. Spending more DPS points first left the build 2-4 points short on life.
3. Strict targets with include_small: 15.0M, valid.
4. Mind Over Matter pre-allocated, then strict targets with include_small: 15.83M.
5. Local search: deallocate a low-value branch, refill with the targets optimiser, keep the change if DPS rises and every target is still met. This got to 16.2M, then 16.75M, then **16.91M**, where it stopped improving, including with small nodes in the candidate list.
6. Other starting points that force Mark the Prey or Disciple+Throatseeker, followed by local search: 16.14M and 16.28M (worse). Swapping in strong DPS notables (Mark the Prey, Annihilation, Throatseeker...) always broke the chaos or phys max hit.
7. Mastery check: the current Critical, ES and Life choices are the best ones. Reservation Mastery "8% dmg per aura" is worth +1.9% but costs a point that is worth more elsewhere.
