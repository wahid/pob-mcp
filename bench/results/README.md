# Tree-rebuild benchmark results (November 2026, 19 poe.ninja ladder characters)

Best rebuild per build across the benchmark runs. Ratios are rebuilt / original, measured
after a save and reload. Match = valid, DPS >= 0.90 and every max hit >= 0.95.

| Build | DPS | Worst max hit | Valid | Match | Strategy |
|---|---|---|---|---|---|
| ascendant | 0.95 | 1.07 | yes | yes | slack |
| assassin | 1.14 | 1.00 | yes | yes | targets-first+small |
| berserker | 0.97 | 1.00 | yes | yes | blend |
| champion | 1.22 | 1.00 | yes | yes | targets-first |
| chieftain | 1.08 | 0.99 | yes | yes | targets-first+small |
| deadeye | 0.91 | 1.04 | yes | yes | targets-first |
| elementalist | 0.91 | 0.99 | yes | yes | slack |
| gladiator | 0.82 | 0.98 | yes |  | slack |
| guardian | 1.17 | 1.04 | yes | yes | targets-first+small |
| hierophant | 0.85 | 0.99 | yes |  | slack |
| inquisitor | 1.03 | 1.00 | yes | yes | targets-first+small |
| juggernaut | 0.93 | 0.98 | yes | yes | slack |
| necromancer | 1.00 | 0.98 | yes | yes | targets-first+small |
| occultist | 0.93 | 1.01 | yes | yes | targets-first+small |
| pathfinder | 1.93 | 1.03 | yes | yes | targets-first |
| saboteur | 0.94 | 0.98 | yes | yes | slack |
| slayer | 1.55 | 0.99 | yes | yes | targets-first |
| trickster | 0.96 | 1.00 | yes | yes | slack |
| warden | 0.96 | 0.99 | yes | yes | targets-first+small |

17/19 match. The builds that matched in the earlier runs (three strategies, no LNS) were not
rerun with the final optimiser: a full 19-build run takes several hours.

## What moved the eight misses (11/19 to 17/19)

- **reset_tree lost jewels.** Sockets inside a large cluster jewel reuse tree socket ids and
  only exist once the cluster is allocated again. They were re-allocated too early and
  silently dropped, losing Forbidden Flame/Flesh and other jewels. They now get a second pass.
- **Split Personality** grows with the socket's distance from the class start. reset_tree now
  keeps the original route to it, and `get_passive_tree` reports `distanceJewels`.
- **Impossible Escape / Thread of Hope** nodes cost 1 point, but PoB still reports a long path.
  The optimiser and `find_passive_nodes` now use the real cost (Prismatic Skin: 1, not 9).
- **Runegrafts** sit on mastery nodes. They were never candidates, crashed PoB's calculator
  when removed, and were dropped on save. They are now normal candidates and saved properly.
- **Avoidance layers.** Spell suppression only halves spell hits at 100%, so no single node
  shows value. The benchmark keeps the original's suppression and block chances as targets.
- **New target modes.** `slack` spends on damage while there are spare points. `repair` spends
  everything on damage first, then trades the cheapest damage branches for the targets.
- **Optimiser reach and finish.** Masteries up to 6 points away (Hierophant's lightning
  conversion mastery is worth +26% at 5 points away). Path cost up to 10. A short finishing
  pass when the time limit hits, so points are not left unspent. Correct freed-point counting
  in LNS. Optional annealing in LNS. Routes that avoid Unnatural Instinct radii.

## Remaining misses

- **Gladiator** (32 free points): every max-hit and block target is met within 1-2%. Adding
  Ambidexterity (+15% DPS) or Assured Strike (+7%) needs 2-3 points that are all holding a target.
- **Hierophant** (70 free points): same picture. Life, mana and physical max hit are each
  within 1-2% of the target, and the damage notables the original has (Crackling Speed,
  Disciple of the Forbidden, Primal Manifestation) cost points the targets use.
  An earlier hand rebuild by Claude with the skill reached 0.94.
