# Trickster (Exsanguinate mines): tree rebuild notes

## Final result (/home/claude/bench/agent/trickster.agent.xml)
- CombinedDPS: **69.22M** (reset start 21.5M; original 159.9M)
- Points: 123 / 123. Ascendancy is 8/8; Ghost Dance and Lethe Shade are kept, and all 7 jewel sockets (with their cluster notables) are still allocated.
- Warnings: only "Chaos resistance is negative (-18%)", which was already in originalWarnings. Attributes are met and no mana warnings remain.

| Stat | Result | Minimum |
|---|---|---|
| PhysicalMaximumHitTaken | 10901 | 10836 |
| Fire/Cold/LightningMaximumHitTaken | 35816 | 35601 |
| ChaosMaximumHitTaken | 5369 | 5327 |
| ManaUnreserved | 136 | 85 |
| LifeUnreserved | 3548 | 2871 |

## What I tried
1. I ran auto_allocate on the reset with all minimums as targets (plus Str 100 and Int 173). The defence targets were met at about 72M, but mana stayed over-reserved: Hatred, Grace, Zealotry and Skitterbots need a lot of reservation efficiency.
2. Reservation package: Charisma, the 8% Reservation Efficiency small node, Mana Mastery (12% reservation efficiency) and Leader of the Pack, with Sovereignty already coming from the amulet anoint. Mana is cleared only with all of these.
   - Pitfall: when Runegraft of Treachery is allocated without picking an effect, it shows its effects (including reservation) inside the session. Those effects are gone after an export and reload, so I checked every result again after reloading.
3. Optimiser variants: strict targets, target_weight 0.2/0.3/0.5/1/2/3, path cost 6 or 9, and a DPS-first stage followed by targets. Valid results landed between 38M and 60M. The DPS-only ceiling with no defence targets was about 76M at 105 points, before the run timed out.
4. Best path: I took the target_weight=1 tree (72M, mana-invalid), added the reservation package (135 points), then ran my own local search. It trims the lowest DPS-loss branches (small passives included) while every minimum stays met, then tries adding a node and trimming again. That gave 68.7M, then 69.2M (it added Influence). Repeated swap passes, sorting by total gain, and a mastery effect sweep found nothing better. The tree is tight on all the max-hit targets at once.

## Notes
- Max hit for every damage type depends on the life+ES pool, so all of them are binding together. The phys and elemental margins are only about 0.5%.
- Big damage nodes left out for lack of points: Heart of Ice, Mark the Prey, Heartseeker/Throatseeker, Supreme Ego (it breaks mana).
