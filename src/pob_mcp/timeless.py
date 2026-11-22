"""Find the timeless jewel seed and socket that suit a build best.

1. Scan (Lua, a few seconds): for every jewel socket that is allocated or within `max_path`
   points, every seed of the jewel: what it turns each notable in radius into, valued on the
   current build with the optimiser's score. Notables in radius that aren't allocated count
   when the seed makes them worth their points.
2. Trials (a fraction of a second each): the best candidates are equipped for real (socket
   path and picked notables allocated) and calculated by PoB. Points spent beyond the current
   tree are charged at the value of a typical point.
3. Rebuild (minutes, optional): for the best `rebuild` candidates, optimize_tree rebuilds the
   whole tree around the jewel within the build's current points, which is the honest answer
   to "what is this jewel worth to us".
"""

from __future__ import annotations

import time

from . import online
from .optimizer import ELE_RESISTS, TreeOptimizer

JEWELS = {
    "glorious vanity": ("Glorious Vanity", ["Doryani", "Xibaqua", "Ahuana"]),
    "lethal pride": ("Lethal Pride", ["Kaom", "Rakiata", "Akoya"]),
    "brutal restraint": ("Brutal Restraint", ["Asenath", "Nasima", "Balbala"]),
    "militant faith": ("Militant Faith", ["Avarius", "Dominus", "Maxarius"]),
    "elegant hubris": ("Elegant Hubris", ["Cadiro", "Victario", "Caspiro"]),
    "heroic tragedy": ("Heroic Tragedy", ["Vorana", "Uhtred", "Medved"]),
}


def resolve(jewel: str, conqueror: str) -> tuple[str, str]:
    key = str(jewel or "").strip().lower()
    if key not in JEWELS:
        raise ValueError(f"Unknown timeless jewel {jewel!r}; one of: {', '.join(v[0] for v in JEWELS.values())}")
    name, who = JEWELS[key]
    for c in who:
        if c.lower() == str(conqueror or "").strip().lower():
            return name, c
    raise ValueError(f"Unknown conqueror {conqueror!r} for {name}; one of: {', '.join(who)}")


def trade_links(league: str, trade_ids: list[str] | None, conqueror_index: int, seeds: list[int]) -> dict:
    """Trade searches for these seeds: with this conqueror, and with any conqueror (the
    conqueror only decides which keystone a keystone in radius becomes; the notables come
    from the seed alone)."""
    if not trade_ids:
        return {}

    def query(ids):
        filters = [{"id": tid, "value": {"min": s, "max": s}} for s in seeds for tid in ids]
        return {"query": {"status": {"option": "online"},
                          "stats": [{"type": "count", "value": {"min": 1}, "filters": filters}]},
                "sort": {"price": "asc"}}

    exact = online.trade_url(league, query([trade_ids[conqueror_index - 1]]))
    anyc = online.trade_url(league, query(trade_ids))
    return {"trade": exact["url"], "tradeAnyConqueror": anyc["url"]}


def find(engine, jewel: str, conqueror: str, *, mode: str = "balanced", weights=None, targets=None,
         sockets=None, max_path: int = 8, max_pick: int = 3, per_socket: int = 3, trials: int = 12,
         replace_jewels: bool = False,
         rebuild: int = 2, apply: bool = True, max_seconds: float = 900, league: str | None = None,
         pool=None, progress=None, cancel=None) -> dict:
    t0 = time.time()
    name, conq = resolve(jewel, conqueror)

    def say(msg):
        if progress:
            try:
                progress(time.time() - t0, max_seconds, msg)
            except Exception:  # noqa: BLE001 - progress is best-effort
                pass

    def stopped():
        return (cancel is not None and cancel.is_set()) or time.time() - t0 > max_seconds

    scorer = TreeOptimizer(engine, mode=mode, weights=weights, targets=targets, explain=False)
    base = scorer.prepare()
    base = {**base, **{k: v or 0 for k, v in engine.call("stats", {"keys": ["ReqStr", "ReqDex", "ReqInt"]}).items()}}
    for k in ("ReqStr", "ReqDex", "ReqInt"):
        base.setdefault(k, 0)
    start_u = scorer.utility(base)
    total_points = scorer.total_points
    w = scorer._weights()
    # the score's slope at the current build, in percent of score per unit of each stat
    grad: dict[str, float] = {}
    if base.get(scorer.dps_stat):
        grad[scorer.dps_stat] = w["dps"] * 100 / base[scorer.dps_stat]
    if base.get("TotalEHP") and w["ehp"]:
        grad["TotalEHP"] = grad.get("TotalEHP", 0) + w["ehp"] * 100 / base["TotalEHP"]
    hits = [h for h in scorer.hit_stats if base.get(h)]
    if hits and w["hit"]:
        weakest = min(hits, key=lambda h: base[h])
        grad[weakest] = grad.get(weakest, 0) + w["hit"] * 100 / base[weakest]

    say(f"scanning every {name} seed in the sockets near the tree")
    scan = engine.call("timeless_scan", {"jewel": name, "conqueror": conq, "grad": grad, "sockets": sockets,
                                          "max_path": max_path, "max_pick": max_pick, "per_socket": per_socket,
                                          "replace_jewels": replace_jewels})
    lam = scan["lambda"]
    cands = []
    for s in scan["sockets"]:
        for c in s["seeds"]:
            est = c["score"] - (s["pathCost"] or 0) * lam
            cands.append({"socket": s, "seed": c["seed"], "estimate": est, "picks": c["picks"], "changes": c["changes"]})
    cands.sort(key=lambda c: c["estimate"], reverse=True)
    if not cands:
        return {"jewel": name, "conqueror": conq, "best": None, "alternatives": [],
                "currentTimeless": scan.get("current"), "socketsScanned": 0,
                "verdict": (f"No free jewel socket is allocated or within {max_path} points of the tree "
                            "(raise max_path, or replace_jewels=true to consider sockets holding jewels)")}

    stats = list(dict.fromkeys([*scorer.stat_names, "Life", "ReqStr", "ReqDex", "ReqInt"]))
    base = {**base, **engine.call("stats", {"keys": ["Life"]})}
    checked, done = [], set()

    def trial(c):
        tr = engine.call("timeless_trial", {"jewel": name, "conqueror": conq, "seed": c["seed"],
                                            "socket": c["socket"]["socket"], "picks": c["picks"], "stats": stats})
        u = scorer.utility(tr["stats"])
        net = (u - start_u) * 100 - tr["extraPoints"] * lam
        checked.append({**c, "trial": tr, "net": net})
        done.add((c["socket"]["socket"], c["seed"]))
        return net

    # The scan only sees what changes with the seed. One real check per socket gives what the
    # socket itself costs or gives whatever the seed (small passives blanked or buffed, a
    # keystone converted, its path), which corrects every estimate for that socket.
    offset = {}
    socket_best = {}
    for c in cands:
        socket_best.setdefault(c["socket"]["socket"], c)
    for n, (sid, c) in enumerate(socket_best.items()):
        if stopped():
            break
        say(f"checking the socket near {c['socket']['near']} ({n + 1}/{len(socket_best)})")
        offset[sid] = trial(c) - c["estimate"]
    for c in cands:
        c["estimate"] += offset.get(c["socket"]["socket"], 0.0)
    cands.sort(key=lambda c: c["estimate"], reverse=True)
    for c in cands:
        if len(done) >= len(socket_best) + max(0, trials) or stopped():
            break
        if (c["socket"]["socket"], c["seed"]) not in done:
            say(f"checking seed {c['seed']} in the socket near {c['socket']['near']}")
            trial(c)
    checked.sort(key=lambda c: c["net"], reverse=True)

    def pct(a, b):
        return round((a / b - 1) * 100, 1) if a is not None and b else None

    def describe(c, final=None, rebuilt=None):
        s, tr = c["socket"], c["trial"]
        st = final or tr["stats"]
        out = {
            "seed": c["seed"], "socket": s["socket"], "socketNear": s["near"],
            "socketAllocated": bool(s.get("allocated")), "socketPathCost": s["pathCost"],
            "replacesJewel": tr.get("replaced"),
            "notables": [{"node": x["node"], "becomes": x.get("becomes"), "adds": x.get("adds"),
                          "allocated": bool(x.get("allocated")), "pointsToReach": x.get("cost"),
                          "valuePct": round(x["gain"], 2)} for x in c["changes"]],
            "pickUp": tr.get("picked") or None,
            "extraPoints": tr["extraPoints"],
            "changePct": {"dps": pct(st.get(scorer.dps_stat), base.get(scorer.dps_stat)),
                          "ehp": pct(st.get("TotalEHP"), base.get("TotalEHP")),
                          "life": pct(st.get("Life"), base.get("Life"))},
            "scoreGainPct": round(c["net"], 2) if rebuilt is None else rebuilt,
        }
        breaks = scorer._violations({**tr["stats"], **(final or {})} if final else tr["stats"])
        if breaks:
            out["breaks"] = [{"stat": b["stat"], "value": b["value"], "needs": b["target"]} for b in breaks]
        if s.get("keystonesInRadius"):
            out["keystonesInRadius"] = s["keystonesInRadius"]
        if tr.get("otherHistoric"):
            out["note"] = f"replaces your {tr['otherHistoric']} (only one Historic jewel counts)"
        return out

    rebuilt = []
    baseline = None
    snapshot = engine.call("export_xml")["xml"]
    # the rebuilds keep what the current build has capped: a jewel swap can cost resistance,
    # and a rebuild that starts uncapped wouldn't know to win it back
    rebuild_targets = dict(targets or {})
    for c in scorer.constraints:
        if c.kind == "validity" and c.stat in ELE_RESISTS:
            rebuild_targets.setdefault(c.stat, c.target)
    if rebuild > 0 and checked and not stopped():
        # The optimiser can usually improve a tree without any jewel too, so the jewel is
        # judged against the same rebuild without it.
        runs = min(rebuild, len(checked)) + 1
        say("rebuilding the tree without the jewel, for comparison")
        try:
            opt = TreeOptimizer(engine, mode=mode, weights=weights, targets=rebuild_targets, total=total_points,
                                pool=pool, cancel=cancel, explain=False,
                                max_seconds=max(30.0, (max_seconds - (time.time() - t0)) / runs - 5))
            res = opt.run()
            final = {**res["final"], **engine.call("stats", {"keys": ["Life"]})}
            baseline = {"final": final, "utility": scorer.utility(final)}
        finally:
            engine.call("load_xml", {"xml": snapshot, "name": "Build"})
        for n, c in enumerate(checked[:rebuild]):
            if stopped():
                break
            left = max_seconds - (time.time() - t0)
            say(f"rebuilding the tree around seed {c['seed']} ({n + 1}/{min(rebuild, len(checked))})")
            engine.call("timeless_trial", {"jewel": name, "conqueror": conq, "seed": c["seed"],
                                           "socket": c["socket"]["socket"], "picks": c["picks"],
                                           "stats": stats, "keep": True})
            try:
                opt = TreeOptimizer(engine, mode=mode, weights=weights, targets=rebuild_targets, total=total_points,
                                    keep=[c["socket"]["socket"]], pool=pool, cancel=cancel, explain=False,
                                    max_seconds=max(30.0, left / max(1, min(rebuild, len(checked)) - n) - 5))
                res = opt.run()
                final = {**res["final"], **engine.call("stats", {"keys": ["Life"]})}
                ref = baseline["utility"] if baseline else start_u
                gain = round((scorer.utility(final) - ref) * 100, 2)
                rebuilt.append({"cand": c, "final": final, "gain": gain, "xml": engine.call("export_xml")["xml"],
                                "points": res["points"]["used"], "violations": res["violations"]})
            finally:
                engine.call("load_xml", {"xml": snapshot, "name": "Build"})
        rebuilt.sort(key=lambda r: r["gain"], reverse=True)

    results = []
    if rebuilt:
        for r in rebuilt:
            d = describe(r["cand"], final=r["final"], rebuilt=r["gain"])
            d["rebuilt"] = True
            if baseline:
                b = baseline["final"]
                d["vsRebuildWithoutJewelPct"] = {
                    "dps": pct(r["final"].get(scorer.dps_stat), b.get(scorer.dps_stat)),
                    "ehp": pct(r["final"].get("TotalEHP"), b.get("TotalEHP")),
                    "life": pct(r["final"].get("Life"), b.get("Life"))}
            d["pointsUsed"] = r["points"]
            d["extraPoints"] = 0
            if r["violations"]:
                d["missedTargets"] = r["violations"]
            results.append(d)
    rebuilt_keys = {(r["cand"]["socket"]["socket"], r["cand"]["seed"]) for r in rebuilt}
    for c in checked:
        if (c["socket"]["socket"], c["seed"]) not in rebuilt_keys:
            results.append(describe(c))

    # rebuilt results are scored against the rebuild without the jewel, the rest against the
    # current tree; within each group the order is by score
    league = league or _league()
    applied = None
    if apply and results and results[0]["scoreGainPct"] > 0:
        best = results[0]
        if rebuilt:
            engine.call("load_xml", {"xml": rebuilt[0]["xml"], "name": "Build"})
        else:
            c = checked[0]
            engine.call("timeless_trial", {"jewel": name, "conqueror": conq, "seed": c["seed"],
                                           "socket": c["socket"]["socket"], "picks": c["picks"],
                                           "stats": stats, "keep": True})
        applied = {"seed": best["seed"], "socket": best["socket"], "treeRebuilt": bool(rebuilt)}
    for r in results:
        if league:
            r.update(trade_links(league, scan.get("tradeIds"), scan["conquerorIndex"], [r["seed"]]))
    top_seeds = sorted({r["seed"] for r in results[:5]})
    out = {
        "jewel": name, "conqueror": conq, "mode": mode,
        "currentTimeless": scan.get("current"),
        "best": results[0] if results else None,
        "alternatives": results[1:6],
        "applied": applied,
        "socketsScanned": len(scan["sockets"]),
        "seedsPerSocket": _seed_count(name),
        "pointValuePct": round(lam, 3),
        "seconds": round(time.time() - t0, 1),
        "note": ("scoreGainPct is the gain in the optimiser's score for this mode. "
                 + ("Rebuilt results re-optimise the whole tree around the jewel within your current points and "
                    "are compared with the same rebuild without the jewel, so they are the honest number "
                    "(changePct is against your current tree). " if rebuilt else
                    "Results are the jewel added to the current tree; points it needs beyond the tree are charged "
                    "at pointValuePct each. Run with rebuild >= 1 (or optimize_tree after) for the honest number. ")
                 + "The conqueror only changes a keystone in radius, so if you don't use that keystone the "
                   "tradeAnyConqueror link finds the same seed cheaper."),
    }
    if league and top_seeds:
        out["tradeTopSeeds"] = trade_links(league, scan.get("tradeIds"), scan["conquerorIndex"], top_seeds).get("trade")
    if baseline:
        b = baseline["final"]
        out["rebuildWithoutJewel"] = {
            "changePct": {"dps": pct(b.get(scorer.dps_stat), base.get(scorer.dps_stat)),
                          "ehp": pct(b.get("TotalEHP"), base.get("TotalEHP"))},
            "scoreGainPct": round((baseline["utility"] - start_u) * 100, 2),
            "note": "what optimize_tree alone does for this tree, without any jewel"}
    for r in results[1:]:
        if not r.get("rebuilt") and rebuilt:
            r["unverified"] = "not rebuilt: scored against the current tree, extra points charged"
    best_gain = results[0]["scoreGainPct"] if results else 0
    hopeful = [r for r in results[1:] if r.get("unverified") and r["scoreGainPct"] > 0]
    if best_gain > 0:
        b = results[0]
        out["verdict"] = (f"Seed {b['seed']} in the socket near {b['socketNear']}: +{best_gain}% score"
                          + (" after rebuilding the tree around it." if b.get("rebuilt") else
                             " on the current tree (not rebuilt)."))
    elif hopeful:
        seeds = ", ".join(str(r["seed"]) for r in hopeful[:3])
        out["verdict"] = (f"Not confirmed: the rebuilt candidates lose, but seeds {seeds} look up to "
                          f"+{hopeful[0]['scoreGainPct']}% on the current tree. Rebuild more (rebuild=4) to check them.")
    elif scan.get("current"):
        out["verdict"] = (f"Keep your {scan['current']['name']}: no seed checked beats it "
                          f"(every result replaces it, since only one Historic jewel counts).")
    else:
        out["verdict"] = f"No {name} seed checked improves this build."
    if name == "Militant Faith":
        out["note"] += " Militant Faith's devotion mods are random and not part of the search."
    return out


def _league():
    try:
        return online.current_league()
    except Exception:  # noqa: BLE001 - offline: no trade links
        return None


def _seed_count(name):
    lo, hi, step = {"Lethal Pride": (10000, 18000, 1), "Brutal Restraint": (500, 8000, 1),
                    "Militant Faith": (2000, 10000, 1), "Elegant Hubris": (2000, 160000, 20),
                    "Heroic Tragedy": (100, 8000, 1)}.get(name, (0, 0, 1))
    return (hi - lo) // step + 1
