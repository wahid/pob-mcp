"""Tree-rebuild benchmark: can the optimiser rebuild a real character's passive tree?

For each PoB build (XML) given on the command line:
  1. Measure the original: main DPS (FullDPS, CombinedDPS or minion DPS, whichever the build
     uses), life, ES, TotalEHP and max hit per damage type, under the build's own config.
  2. Wipe the tree with reset_tree, keeping the ascendancy, keystones and socketed jewels
     (gear, gems, config and jewels stay the same).
  3. Let auto_allocate_passives spend the same number of points: maximise the DPS while
     reaching (98% of) the original's max hit for every damage type.
  4. Compare. A rebuild "matches" when it reaches --dps-share of the DPS and stays above
     --hit-share of the original max hit on every damage type.

Usage: python bench/tree_rebuild.py --pob-path ~/PathOfBuilding builds/*.xml --out results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pob_mcp.engine import PobEngine  # noqa: E402

HIT_KEYS = ["PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ColdMaximumHitTaken",
            "LightningMaximumHitTaken", "ChaosMaximumHitTaken"]
OTHER_KEYS = ["Life", "EnergyShield", "TotalEHP", "FullDPS", "CombinedDPS", "Minion.CombinedDPS",
              "ManaUnreserved", "LifeUnreserved"]


def measure(e: PobEngine) -> dict:
    st = e.call("stats", {"keys": HIT_KEYS + OTHER_KEYS})
    summary = e.call("summary")
    st["pointsUsed"] = summary.get("passivePointsUsed")
    st["warnings"] = [w["message"] for w in e.call("warnings")["warnings"]]
    return st


def _shape(w: str) -> str:
    import re
    return re.sub(r"-?\d+(\.\d+)?", "#", w.split(":")[0])


def new_warnings(before: list[str], after: list[str]) -> list[str]:
    """Warnings the rebuild introduced: same kind of warning with other numbers isn't new,
    and a 'not enough Mana to use' list only counts the skills that weren't already there."""
    shapes = {_shape(w) for w in before}
    out = []
    for w in after:
        if _shape(w) not in shapes:
            out.append(w)
        elif ":" in w:
            old = next((b for b in before if _shape(b) == _shape(w)), "")
            extra = set(x.strip() for x in w.split(":", 1)[1].split(",")) - \
                set(x.strip() for x in old.split(":", 1)[1].split(",")) if ":" in old else set()
            if extra:
                out.append(w.split(":")[0] + ": " + ", ".join(sorted(extra)))
    return out


def pick_metric(st: dict) -> str:
    def f(v):
        return v if isinstance(v, (int, float)) else 0
    cands = {k: f(st.get(k)) for k in ("FullDPS", "CombinedDPS", "Minion.CombinedDPS")}
    return max(cands, key=cands.get)


def run_one(e: PobEngine, path: str, max_seconds: int, hit_target: float, max_path_cost: int = 8,
            lns_rounds: int = 0, strategies: str = "all") -> dict:
    xml = Path(path).read_text(encoding="utf-8")
    e.load_build(xml, Path(path).stem)
    orig = measure(e)
    metric = pick_metric(orig)
    e.call("apply_edits", {"edits": [{"op": "reset_tree"}]})
    after_reset = e.call("summary")["passivePointsUsed"]
    budget = max(0, (orig["pointsUsed"] or 0) - after_reset)
    targets = {k: round(orig[k] * hit_target) for k in HIT_KEYS
               if isinstance(orig.get(k), (int, float)) and orig[k] > 0}
    # the original was usable: keep at least its unreserved mana/life (skill costs must still fit)
    for k in ("ManaUnreserved", "LifeUnreserved"):
        if (orig.get(k) or 0) > 0:
            targets[k] = round(orig[k] * hit_target)
    t0 = time.time()
    reset_xml = e.call("export_xml")["xml"]
    best = None
    plans = (("targets-first", None, False), ("blend", 1.0, False), ("targets-first+small", None, True))
    if strategies != "all":
        plans = tuple(x for x in plans if x[0] in strategies.split(","))
    for strategy, tw, small in plans:
        e.load_build(reset_xml, Path(path).stem)
        args = {"points": budget, "weights": {metric: 1}, "targets": targets, "masteries": True,
                "max_seconds": max_seconds, "max_path_cost": max_path_cost}
        if tw is not None:
            args["target_weight"] = tw
        args["include_small"] = small
        if lns_rounds:
            args["lns_rounds"] = lns_rounds
            args["lns_seconds"] = max_seconds * 4
        res = e.call("auto_allocate", args)
        # measure what survives a save/reload, so session-only effects can't count
        xml_now = e.call("export_xml")["xml"]
        e.load_build(xml_now, Path(path).stem)
        st = measure(e)
        feasible = all(v["met"] for v in (res.get("targets") or {}).values()) and \
            all(v["met"] for v in (res.get("validity") or {}).values())
        dps = st.get(metric) if isinstance(st.get(metric), (int, float)) else 0
        key = (feasible, dps)
        if best is None or key > best[0]:
            best = (key, strategy, res, st, xml_now)
    _, strategy, res, new, xml_best = best
    e.load_build(xml_best, Path(path).stem)
    elapsed = time.time() - t0
    ratios = {}
    for k in [metric] + HIT_KEYS + ["Life", "EnergyShield", "TotalEHP"]:
        o, n = orig.get(k) or 0, new.get(k) or 0
        if isinstance(o, (int, float)) and isinstance(n, (int, float)) and o > 0:
            ratios[k] = round(n / o, 3)
    return {"build": Path(path).stem, "metric": metric, "budget": budget, "seconds": round(elapsed),
            "strategy": strategy,
            "stoppedBecause": res.get("stoppedBecause"), "targets": res.get("targets"),
            "targetNote": res.get("targetNote"), "swaps": res.get("swaps"), "original": orig, "rebuilt": new, "ratios": ratios,
            "newWarnings": new_warnings(orig["warnings"], new["warnings"]),
            "allocated": [(a["name"], a["type"], a.get("effect")) for a in res.get("allocated", [])]}


def score(r: dict, dps_share: float, hit_share: float) -> dict:
    rt = r["ratios"]
    dps = rt.get(r["metric"], 0)
    hits = [rt[k] for k in HIT_KEYS if k in rt]
    worst_hit = min(hits) if hits else 1
    bad = [w for w in r.get("newWarnings", []) if any(x in w for x in ("Mana", "Life", "reserved", "Str", "Dex", "Int", "requirement"))]
    valid = not bad
    return {"dps": dps, "worstHit": worst_hit, "valid": valid, "problems": bad,
            "match": valid and dps >= dps_share and worst_hit >= hit_share,
            "beatsOriginal": valid and dps >= 1 and worst_hit >= hit_share}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("builds", nargs="+")
    ap.add_argument("--pob-path", default=os.environ.get("POB_PATH"))
    ap.add_argument("--out", default="tree_rebuild_results.json")
    ap.add_argument("--max-seconds", type=int, default=300)
    ap.add_argument("--hit-target", type=float, default=0.98, help="share of original max hits to require")
    ap.add_argument("--dps-share", type=float, default=0.9)
    ap.add_argument("--max-path-cost", type=int, default=8)
    ap.add_argument("--lns-rounds", type=int, default=0, help="large-neighbourhood search rounds after the greedy fill")
    ap.add_argument("--strategies", default="all", help="comma list: targets-first, blend, targets-first+small")
    ap.add_argument("--save-dir", default=None, help="save each rebuilt build here as XML")
    ap.add_argument("--hit-share", type=float, default=0.95)
    a = ap.parse_args()
    e = PobEngine(a.pob_path)
    results = []
    for path in a.builds:
        try:
            r = run_one(e, path, a.max_seconds, a.hit_target, a.max_path_cost, a.lns_rounds, a.strategies)
            if a.save_dir:
                Path(a.save_dir).mkdir(parents=True, exist_ok=True)
                e.call("export_xml") and Path(a.save_dir, Path(path).stem + ".rebuilt.xml").write_text(
                    e.call("export_xml")["xml"], encoding="utf-8")
            r["score"] = score(r, a.dps_share, a.hit_share)
            s = r["score"]
            print(f"{r['build']:14s} {r['metric']:18s} DPS {s['dps']:.2f}  worst hit {s['worstHit']:.2f}  "
                  f"{'MATCH' if s['match'] else ('INVALID' if not s['valid'] else 'miss ')}  ({r['budget']} pts, {r['seconds']}s)", flush=True)
        except Exception as ex:  # keep going
            r = {"build": Path(path).stem, "error": str(ex)}
            print(f"{r['build']:14s} ERROR {ex}", flush=True)
        results.append(r)
        Path(a.out).write_text(json.dumps(results, indent=1))
    ok = [r for r in results if "score" in r]
    if ok:
        matches = sum(r["score"]["match"] for r in ok)
        med = sorted(r["score"]["dps"] for r in ok)[len(ok) // 2]
        print(f"\n{matches}/{len(ok)} rebuilds match (DPS >= {a.dps_share:.0%}, every max hit >= {a.hit_share:.0%}); "
              f"median DPS share {med:.2f}")


if __name__ == "__main__":
    main()
