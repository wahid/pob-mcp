# PoB pitfalls and honest numbers

PoB calculates exactly what it is told. Most inflated builds are not bugs; they are
configs and items that do not match how the character is really played.

## Config inflation

Check `get_config` for every build you report. Common sources of fake damage:
- Conditions the build does not maintain: "enemy is shocked/chilled/intimidated",
  "on full life/low life", charges at maximum when nothing generates them, buffs like
  Onslaught or Fortify the player does not have up.
- Every flask toggled on as if permanently up without flask uptime support.
- Enemy debuffs from curses or exposure the build does not apply.
- Boss vs normal enemy: report both (`evaluate_scenarios`). Boss config lowers DPS
  (resistances, less curse effect) and raises the damage you take.

Rule: a condition belongs in config only if the build sustains it in normal play. If
the build sustains it part of the time, report the number both with and without it.

## Uptime and delivery

- Skills with setup (totems, traps, mines, brands, channelled skills, stacking DoTs,
  buffs that build up) only reach PoB's number after setup. Note the ramp-up time.
- Damage that requires standing still or being in melee range is less usable than the
  same number at range while moving.
- Cooldown-gated and trigger skills: check the trigger rate PoB uses.
- Full DPS summing multiple skills assumes all of them are used together.
- Extra projectiles against one target (Barrage-style supports, shotgunning): PoB's
  single-target number assumes they all hit. Against a large boss many do; against a
  small or moving one far fewer. Report such numbers as an upper bound.
- Guard skills (Steelskin and similar) raise max hit only while active; report max hit
  with and without them and treat the lower one as the baseline.

## Supports PoB overrates

Support gems with conditions PoB cannot see:
- Smaller area of effect (concentrated-effect style) helps single target in PoB but
  hurts clear in game.
- Supports that need a specific state (enemy hit recently, close range, charges) look
  great if the config says the state is always true.
- Supports that need the build to do something specific: Trinity only pays off when hits
  deal two or three elements, so a pure-lightning build gets almost nothing from it (and
  setting its resonance to maximum in config inflates the number). Check what a support
  needs, and look at its real contribution in `rank_support_gems`' `currentSupports`.
- Supports and buffs that need you to spend mana or life, cast or attack (Arcane Surge,
  Lifetap-style, Inspiration) look strong on skills that spend nothing, such as
  Righteous Fire or most auras. PoB may still count them; in game they do nothing.
- Prefer supports with unconditional more multipliers unless the condition really holds.
`rank_support_gems` reports what PoB calculates; apply this judgment on top.

## Optimiser picks with drawbacks

`auto_allocate_passives` scores what PoB calculates. Mastery effects and notables whose
cost PoB doesn't model (for example losing life or energy shield per skill use) can win
on paper; the result marks suspect masteries with `check`. Read every mastery it chose
and `avoid` the ones whose drawback matters for how the build is played.

## Interactions that may combine badly

Some unique or keystone combinations look strong in PoB but conflict in game
(mechanics that consume the same charges, effects that can't both be active, keystones
that remove the thing another item needs). When a combination feels too good, read
the item and node text with `search_uniques` / `find_passive_nodes` and think through
whether both can be active together.

## Gear realism

- Rares with many top-tier mods, or mods the base cannot roll, are fantasy items
  (check with `list_item_mods`).
- Mirror-tier gear is fine to show as a ceiling but not as the build's expected power.
- Unique rolls set to the best variant/maximum: say so or set typical rolls.

## Defense blind spots

PoB does not show everything:
- Being stun-locked, frozen, slowed or surrounded is not in EHP.
- Degens and ground effects are only partly modelled.
- Map mods (reflect, no regen, reduced max res, extra damage) change the build's real
  defense; mention what it cannot run.

## Checking someone else's build or guide

1. Load it, read `get_config`: what is toggled on?
2. `evaluate_scenarios` for mapping vs boss.
3. `get_items`: are the rares realistic for the stated budget? Price key uniques.
4. Defense checklist from defense.md, with max hits per type.
5. Look for guide-bait signs: DPS shown only with every buff and debuff, no defensive
   numbers shown, expensive items hidden in "endgame" while the headline number uses
   them, a playstyle the clip cannot show (standing still at a boss).
6. Report the realistic numbers next to the advertised ones.
