# Tree-rebuild benchmark results (November 2026, 19 poe.ninja ladder characters)

Best rebuild per build across the benchmark runs. Ratios are rebuilt / original, measured
after a save and reload. Match = valid, DPS >= 0.90 and every max hit >= 0.95.

| Build | DPS | Worst max hit | Valid | Match | Strategy |
|---|---|---|---|---|---|
| ascendant | 0.95 | 1.07 | yes | yes | slack |
| assassin | 1.14 | 1.00 | yes | yes | targets-first+small |
| berserker | 2.01 | 0.99 | yes | yes | slack |
| champion | 1.22 | 1.00 | yes | yes | targets-first |
| chieftain | 1.08 | 0.99 | yes | yes | targets-first+small |
| deadeye | 1.23 | 1.00 | yes | yes | slack |
| elementalist | 1.02 | 0.98 | yes | yes | slack |
| gladiator | 0.82 | 0.98 | yes |  | slack |
| guardian | 1.31 | 0.99 | yes | yes | repair |
| hierophant | 0.93 | 0.99 | yes | yes | targets-first |
| inquisitor | 1.25 | 0.99 | yes | yes | targets-first |
| juggernaut | 0.97 | 0.98 | yes | yes | slack |
| necromancer | 1.00 | 0.98 | yes | yes | targets-first+small |
| occultist | 1.26 | 0.99 | yes | yes | slack |
| pathfinder | 2.42 | 0.99 | yes | yes | repair |
| saboteur | 1.11 | 0.98 | yes | yes | repair |
| slayer | 1.55 | 0.99 | yes | yes | targets-first |
| trickster | 1.07 | 0.98 | yes | yes | targets-first |
| warden | 1.40 | 0.98 | yes | yes | repair |

18/19 match. A full rerun of all 19 builds with the final optimiser (slack, repair and
targets-first, 20 LNS rounds) matched 16 in one pass; the table keeps the best run per build.
Single runs vary: Ascendant and Necromancer reached 0.83 and 0.89 in that rerun, and `main`
before the LNS fixes gave the same numbers with the same settings.

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

## Hierophant and Gladiator

- **Hierophant** now matches without help (0.93) after the LNS fixes: LNS ends on the best tree
  it has seen, and refills are scored against the tree they came from. Forcing the original's
  four damage picks with `auto_allocate_passives(require=[...])` reaches 0.995 with every
  target met (`agent_hierophant_require.xml`).
- **Gladiator** is still at 0.82 (32 free points; Ambidexterity and Assured Strike need points that
  are all holding a target). `target_report` shows why: spell block (Versatile Combatant
  turns attack block above the cap into spell block) sits at 24, far below its target, because
  the optimiser never takes the block mastery and only has 8 points of attack block over the cap
  (the original has 22). Forcing the original's damage notables keeps the DPS but leaves the
  block targets far off.