"""Exact passive-tree optimiser.

The greedy optimiser in bridge.lua adds one node at a time, so it misses long paths and nodes
that only pay off together. This one treats the tree as a whole:

1. PoB measures every node's stat change on the current tree (``tree_model``): what adding an
   unallocated node gives, what removing an allocated one costs, and every mastery effect.
2. A mixed-integer program picks the best connected tree within the point budget. Connectivity
   is a single-commodity flow from the class start, so paths are exact and paid for. Mastery
   effects can each be taken once. Validity (attribute requirements, unreserved mana/life)
   and any user targets are constraints.
3. PoB applies the tree and calculates it for real. If it is better the stats are measured
   again on the new tree and the program is solved again; if not, the step was too far for the
   linear estimate, so it is retried with fewer changes allowed (a trust region).

The objective is a weighted log-utility: w_dps*log(DPS) + w_ehp*log(TotalEHP) +
w_hit*log(weakest max hit). Linearised at the current tree, each node is worth its relative
change, so the modes trade damage and defence by proportions, not raw numbers.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

HITS = [
    "PhysicalMaximumHitTaken",
    "FireMaximumHitTaken",
    "ColdMaximumHitTaken",
    "LightningMaximumHitTaken",
    "ChaosMaximumHitTaken",
]

MODES = {
    "dps": {"dps": 1.0, "ehp": 0.1, "hit": 0.1},
    "balanced": {"dps": 0.5, "ehp": 0.25, "hit": 0.25},
    "defence": {"dps": 0.1, "ehp": 0.5, "hit": 0.5},
}
MODES["defense"] = MODES["defence"]

PENALTY = 20.0  # utility lost per unit of relative shortfall on a constraint


@dataclass
class _Constraint:
    stat: str
    target: float
    scale: float
    kind: str  # "validity" or "target"


@dataclass
class _State:
    nodes: list[int]
    masteries: dict[int, int]
    stats: dict[str, float]
    utility: float


@dataclass
class TreeOptimizer:
    engine: object
    mode: str = "balanced"
    metric: str | None = None
    targets: dict[str, float] | None = None
    points: int | None = None
    keep: list | None = None
    require: list | None = None
    avoid: list | None = None
    include_keystones: bool = False
    max_seconds: float = 900
    max_iters: int = 40
    swap_radius: int = 8
    fill_step: int = 10
    reach: int | None = None
    mip_seconds: float = 30
    weights: dict[str, float] | None = None
    log: object = None
    history: list = field(default_factory=list)

    # -- PoB side ---------------------------------------------------------------------
    def _measure(self):
        stats = [self.metric or "@dps", "TotalEHP", *HITS,
                 "Str", "Dex", "Int", "ManaUnreserved", "LifeUnreserved",
                 *(self.targets or {}).keys()]
        seen, req = set(), []
        for s in stats:
            if s not in seen:
                seen.add(s)
                req.append(s)
        params = {"stats": req, "lenient": True, "include_keystones": self.include_keystones,
                  "radius": self._reach()}
        for k in ("keep", "require", "avoid"):
            if getattr(self, k):
                params[k] = getattr(self, k)
        return self.engine.call("tree_model", params)

    def _reach(self):
        # nodes further than this from the current tree can't be reached in one step
        return self.reach if self.reach is not None else self.swap_radius + self.fill_step

    def _apply(self, nodes, masteries, undo=False):
        return self.engine.call("set_tree", {
            "nodes": nodes,
            "masteries": {str(k): v for k, v in masteries.items()},
            "stats": self.stat_names,
            "no_undo": not undo,
        })

    # -- scoring ----------------------------------------------------------------------
    def _weights(self):
        w = dict(MODES[self.mode])
        if self.weights:
            w.update(self.weights)
        return w

    def utility(self, stats: dict) -> float:
        w = self._weights()
        u = w["dps"] * math.log(max(stats.get(self.dps_stat) or 0, 1.0))
        if "TotalEHP" in stats:
            u += w["ehp"] * math.log(max(stats.get("TotalEHP") or 0, 1.0))
        hits = [stats[h] for h in self.hit_stats if stats.get(h) is not None]
        if hits:
            u += w["hit"] * math.log(max(min(hits), 1.0))
        for c in self.constraints:
            v = stats.get(c.stat)
            if v is None:
                continue
            short = max(0.0, c.target - v) / c.scale
            u -= PENALTY * short
        return u

    def _violations(self, stats):
        out = []
        for c in self.constraints:
            v = stats.get(c.stat)
            if v is not None and v < c.target - 1e-6:
                out.append({"stat": c.stat, "value": v, "target": c.target, "kind": c.kind})
        return out

    # -- the integer program ------------------------------------------------------------
    def _solve(self, model, remove_radius, add_radius):
        import highspy

        stats = model["stats"]
        idx = {s: i for i, s in enumerate(stats)}
        base = model["base"]
        nodes = model["nodes"]
        w = self._weights()
        n_id = {r["id"]: k for k, r in enumerate(nodes)}

        lb, ub, cost, integ = [], [], [], []

        def var(lo, hi, c=0.0, integer=False):
            lb.append(lo)
            ub.append(hi)
            cost.append(c)
            integ.append(1 if integer else 0)
            return len(lb) - 1

        rows = []  # (cols, vals, lo, hi)

        tags = []

        def row(cols, vals, lo, hi, tag=""):
            rows.append((cols, vals, lo, hi))
            tags.append(tag)

        dps_i = idx.get(self.dps_stat)
        ehp_i = idx.get("TotalEHP")
        hit_is = [idx[h] for h in self.hit_stats if h in idx]
        rel = lambda i: 1.0 / max(abs(base[stats[i]]), 1.0)

        def obj_of(d):
            if not d:
                return 0.0
            c = 0.0
            if dps_i is not None:
                c += w["dps"] * d[dps_i] * rel(dps_i)
            if ehp_i is not None:
                c += w["ehp"] * d[ehp_i] * rel(ehp_i)
            return c

        # distance from the current tree: further than add_radius can't be reached this step
        dist = {k: 0 for k, r in enumerate(nodes) if r["alloc"] or r["type"] == "ClassStart"}
        for k, r in enumerate(nodes):
            if r.get("entry") and k not in dist:
                dist[k] = 1
        frontier = list(dist)
        while frontier and add_radius is not None:
            nxt = []
            for k in frontier:
                if nodes[k]["type"] == "Mastery":
                    continue  # no path continues through a mastery
                for o in nodes[k]["links"]:
                    j = n_id.get(o)
                    if j is not None and j not in dist and dist[k] < add_radius:
                        dist[j] = dist[k] + 1
                        nxt.append(j)
            frontier = nxt
        # node variables
        x = []
        for k, r in enumerate(nodes):
            lo = 1 if (r["fixed"] or r.get("require")) else 0
            hi = 0 if r.get("avoid") else 1
            if add_radius is not None and k not in dist and not r.get("leap") and not r.get("require"):
                hi = 0
            if r["type"] == "ClassStart":
                lo = hi = 1
            x.append(var(lo, hi, obj_of(r.get("d")), True))
        # mastery effect variables
        y = {}  # (node k, effect) -> var
        effects = {}
        for k, r in enumerate(nodes):
            for m in r.get("m") or []:
                lo = 1 if m.get("want") else 0
                v = var(lo, 1, obj_of(m.get("d")), True)
                y[(k, m["e"])] = (v, m)
                effects.setdefault(m["e"], []).append(v)
        for k, r in enumerate(nodes):
            if r.get("m"):
                cols = [y[(k, m["e"])][0] for m in r["m"]] + [x[k]]
                row(cols, [1.0] * len(r["m"]) + [-1.0], 0, 0, "mastery " + str(r["id"]))
        for e, vs in effects.items():
            if len(vs) > 1:
                row(vs, [1.0] * len(vs), -np.inf, 1)

        # linear stat change: list of (var, coef) and the constant from the current tree
        def stat_terms(i):
            cols, vals, const = [], [], 0.0
            for k, r in enumerate(nodes):
                d = r.get("d")
                if d and d[i]:
                    cols.append(x[k])
                    vals.append(d[i])
                    if r["alloc"]:
                        const -= d[i]
                for m in r.get("m") or []:
                    dm = m.get("d")
                    if dm and dm[i]:
                        cols.append(y[(k, m["e"])][0])
                        vals.append(dm[i])
                        if m.get("sel"):
                            const -= dm[i]
            return cols, vals, const

        # weakest max hit: z <= (b_k + delta_k - bmin) / bmin for every type
        z = None
        if hit_is:
            bmin = max(min(base[stats[i]] for i in hit_is), 1.0)
            z = var(-np.inf, 10.0, w["hit"])
            z0 = 0.0
            for i in hit_is:
                cols, vals, const = stat_terms(i)
                # z*bmin - sum d x <= b_k - bmin + const
                row(cols + [z], [-v for v in vals] + [bmin], -np.inf, base[stats[i]] - bmin + const, "hit " + stats[i])

        # constraints, soft with a penalised slack
        slacks = []
        for c in self.constraints:
            if c.stat not in idx:
                continue
            i = idx[c.stat]
            cols, vals, const = stat_terms(i)
            s = var(0, np.inf, -PENALTY / c.scale)
            slacks.append((s, max(0.0, c.target - base[c.stat])))
            # b + sum d x + const + s >= target
            row(cols + [s], vals + [1.0], c.target - base[c.stat] - const, np.inf, "constraint " + c.stat)

        # point budget
        budget = self.total_points - model["excludedPoints"]
        cols = [x[k] for k, r in enumerate(nodes) if r["type"] != "ClassStart"]
        row(cols, [1.0] * len(cols), -np.inf, budget, "budget")

        # trust region
        rem = [x[k] for k, r in enumerate(nodes) if r["alloc"] and not r["fixed"] and r["type"] != "ClassStart"]
        if rem and remove_radius is not None:
            row(rem, [1.0] * len(rem), len(rem) - remove_radius, np.inf)
        add = [x[k] for k, r in enumerate(nodes) if not r["alloc"]]
        if add and add_radius is not None:
            row(add, [1.0] * len(add), -np.inf, add_radius)

        # connectivity: flow from a virtual root; every chosen node consumes one unit
        M = float(budget + 2)
        inflow = {k: [] for k in range(len(nodes))}
        outflow = {k: [] for k in range(len(nodes))}
        arcs, root_arc, adj = {}, {}, {}
        for k, r in enumerate(nodes):
            if r["type"] == "Mastery":
                continue  # masteries are leaves: no path continues through them
            for o in r["links"]:
                j = n_id.get(o)
                if j is None or nodes[j]["type"] == "ClassStart":
                    continue
                f = var(0, M)
                arcs[(k, j)] = f
                adj.setdefault(k, []).append(j)
                outflow[k].append(f)
                inflow[j].append(f)
                row([f, x[k]], [1.0, -M], -np.inf, 0, "arc")
        for k, r in enumerate(nodes):
            cap = None
            if r["type"] == "ClassStart" or r.get("entry"):
                cap = M
            elif r.get("leap") or (r["fixed"] and not r["links"]):
                cap = 1.0
            if cap is not None:
                root_arc[k] = var(0, cap)
                inflow[k].append(root_arc[k])
        for k in range(len(nodes)):
            cols = inflow[k] + outflow[k] + [x[k]]
            vals = [1.0] * len(inflow[k]) + [-1.0] * len(outflow[k]) + [-1.0]
            row(cols, vals, 0, 0, "flow " + str(nodes[k]["id"]))

        # build and solve
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", float(self.mip_seconds))
        h.setOptionValue("mip_rel_gap", 1e-3)
        n = len(lb)
        inf = highspy.kHighsInf
        clamp = lambda v: inf if v == np.inf else (-inf if v == -np.inf else v)
        h.addVars(n, np.array([clamp(v) for v in lb]), np.array([clamp(v) for v in ub]))
        h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(cost))
        h.changeColsIntegrality(n, np.arange(n, dtype=np.int32),
                                np.array([highspy.HighsVarType.kInteger if t else highspy.HighsVarType.kContinuous for t in integ]))
        starts, index, value, lower, upper = [], [], [], [], []
        for cols, vals, lo, hi in rows:
            starts.append(len(index))
            index.extend(cols)
            value.extend(vals)
            lower.append(clamp(lo))
            upper.append(clamp(hi))
        h.addRows(len(rows), np.array(lower), np.array(upper), len(index),
                  np.array(starts, dtype=np.int32), np.array(index, dtype=np.int32), np.array(value))
        h.changeObjectiveSense(highspy.ObjSense.kMaximize)
        # warm start from the current tree: flows along a BFS tree from the class start
        start = np.zeros(n)
        for k, r in enumerate(nodes):
            if r["alloc"] or r["type"] == "ClassStart":
                start[x[k]] = 1
            for m in r.get("m") or []:
                if m.get("sel"):
                    start[y[(k, m["e"])][0]] = 1
        for s, v in slacks:
            start[s] = v
        if z is not None:
            start[z] = 0.0
        on = {k for k, r in enumerate(nodes) if r["alloc"] or r["type"] == "ClassStart"}
        parent, order = {}, []
        roots = [k for k in root_arc if k in on]
        seen = set(roots)
        queue = list(roots)
        while queue:
            k = queue.pop(0)
            order.append(k)
            for j in adj.get(k, []):
                if j in on and j not in seen:
                    seen.add(j)
                    parent[j] = k
                    queue.append(j)
        size = {k: 1 for k in order}
        for k in reversed(order):
            if k in parent:
                size[parent[k]] += size[k]
        warm_ok = seen == on
        for k in order:
            if k in parent:
                start[arcs[(parent[k], k)]] = size[k]
            else:
                start[root_arc[k]] = size[k]
                if nodes[k]["type"] != "ClassStart" and size[k] > 1:
                    warm_ok = False  # a leap node feeding others: not a valid start
        bad = []
        for c in range(n):
            if start[c] < lb[c] - 1e-7 or start[c] > ub[c] + 1e-7:
                bad.append(f"bound of column {c}")
        for (cols, vals, lo, hi), tag in zip(rows, tags):
            a = float(sum(start[c] * v for c, v in zip(cols, vals)))
            if a < lo - 1e-6 or a > hi + 1e-6:
                bad.append(f"{tag}: {a:.3f} not in [{lo}, {hi}]")
        if bad:
            warm_ok = False
        if warm_ok:
            sol0 = highspy.HighsSolution()
            sol0.col_value = list(start)
            sol0.value_valid = True
            h.setSolution(sol0)
        t0 = time.time()
        h.run()
        status = h.modelStatusToString(h.getModelStatus())
        sol = h.getSolution().col_value
        if not sol or len(sol) < n or h.getInfo().primal_solution_status != 2:
            return None, {"status": status, "warm": warm_ok, "warm_problems": bad[:5] or None}
        chosen = [nodes[k]["id"] for k in range(len(nodes))
                  if sol[x[k]] > 0.5 and nodes[k]["type"] != "ClassStart"]
        mast = {nodes[k]["id"]: e for (k, e), (v, _) in y.items() if sol[v] > 0.5}
        info = {"status": status, "seconds": round(time.time() - t0, 1), "vars": n, "rows": len(rows), "warm": warm_ok,
                "warm_problems": bad[:5] or None,
                "objective": h.getInfo().objective_function_value}
        return (chosen, mast), info

    # -- main loop ------------------------------------------------------------------------
    def _say(self, msg):
        if self.log:
            self.log(msg)

    def run(self) -> dict:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of dps, balanced, defence (got {self.mode!r})")
        t_start = time.time()
        # required picks are allocated up front (with their paths) and then kept fixed
        if self.require:
            edits = []
            for ref in self.require:
                if isinstance(ref, dict):
                    edits.append({"op": "allocate_node", "node": ref["node"]})
                    if ref.get("effect") is not None:
                        edits.append({"op": "set_mastery", "node": ref["node"], "effect": ref["effect"]})
                else:
                    edits.append({"op": "allocate_node", "node": ref})
            self.engine.call("apply_edits", {"edits": edits})
            self.keep = list(self.keep or []) + [r["node"] if isinstance(r, dict) else r for r in self.require]
            self.require = None
        model = self._measure()
        self.stat_names = model["stats"]
        self.dps_stat = model["stats"][0]
        self.hit_stats = [h for h in HITS if h in model["stats"]]
        base = model["base"]
        used, budget = model["used"], model["budget"]
        if self.points is not None:
            self.total_points = used + int(self.points)
        else:
            self.total_points = max(used, budget)

        cons = []
        for attr in ("Str", "Dex", "Int"):
            req = (model.get("required") or {}).get(attr) or 0
            if req > 0 and attr in base and attr not in (self.targets or {}):
                cons.append(_Constraint(attr, float(req), max(float(req), 1.0), "validity"))
        for pool in ("Mana", "Life"):
            key = pool + "Unreserved"
            if ((model.get("reserved") or {}).get(pool) or 0) > 0 and key in base and key not in (self.targets or {}):
                cons.append(_Constraint(key, 0.0, 100.0, "validity"))
        for stat, t in (self.targets or {}).items():
            cons.append(_Constraint(stat, float(t), max(abs(float(t)), 1.0), "target"))
        self.constraints = cons

        start_nodes = [r["id"] for r in model["nodes"] if r["alloc"] and r["type"] != "ClassStart"]
        start_mast = {r["id"]: m["e"] for r in model["nodes"] for m in (r.get("m") or []) if m.get("sel")}
        start_stats = dict(base)
        cur = _State(start_nodes, start_mast, dict(base), self.utility(base))
        best = cur
        free = self.total_points - used
        radius = self.swap_radius
        measured = 1
        it = 0
        notes = []
        tried, rejected_on_model = set(), False
        while it < self.max_iters and time.time() - t_start < self.max_seconds:
            it += 1
            model_free = self.total_points - model["excludedPoints"] - sum(
                1 for r in model["nodes"] if r["alloc"] and r["type"] != "ClassStart")
            adds = min(radius + min(max(model_free, 0), self.fill_step), self._reach())
            res, info = self._solve(model, radius, adds)
            if res is None:
                notes.append(f"iteration {it}: solver gave no tree ({info.get('status')})")
                break
            nodes, mast = res
            tried.add(radius)
            if set(nodes) == set(cur.nodes) and mast == cur.masteries:
                self.history.append({"iter": it, "radius": radius, "result": "no change", **info})
                # the linear model is at its optimum within this radius: look further once,
                # unless a larger step was already tried on this model
                if radius * 2 in tried or radius * 2 > self.swap_radius * 4 or rejected_on_model:
                    break
                radius *= 2
                continue
            out = self._apply(nodes, mast)
            if out.get("missing") or out.get("disconnected"):
                self.history.append({"iter": it, "radius": radius, "result": "invalid tree",
                                     "missing": out.get("missing"), "disconnected": out.get("disconnected"), **info})
                self._apply(cur.nodes, cur.masteries)
                radius //= 2
                if radius < 1:
                    break
                continue
            u = self.utility(out["stats"])
            entry = {"iter": it, "radius": radius, "utility": round(u, 5), "dps": out["stats"].get(self.dps_stat),
                     "ehp": out["stats"].get("TotalEHP"), "solver": info}
            if u > cur.utility + 1e-5:
                cur = _State(nodes, mast, out["stats"], u)
                if u > best.utility:
                    best = cur
                entry["result"] = "accepted"
                self.history.append(entry)
                self._say(f"iteration {it}: accepted, utility {u:.4f}, {self.dps_stat} {entry['dps']}")
                model = self._measure()
                measured += 1
                radius = max(radius, self.swap_radius)
                tried, rejected_on_model = set(), False
            else:
                entry["result"] = "rejected"
                rejected_on_model = True
                self.history.append(entry)
                self._apply(cur.nodes, cur.masteries)
                radius //= 2
                if radius < 1:
                    break

        # leave the best tree applied, as one undo step
        final = self._apply(best.nodes, best.masteries, undo=True)
        stats = final["stats"]
        before, after = set(start_nodes), set(best.nodes)
        names = {r["id"]: r.get("name") for r in model["nodes"]}
        return {
            "mode": self.mode,
            "weights": self._weights(),
            "metric": self.dps_stat,
            "points": {"total": self.total_points, "used": final["used"], "free": free},
            "start": {k: start_stats.get(k) for k in self.stat_names},
            "final": {k: stats.get(k) for k in self.stat_names},
            "ratios": {k: (round(stats[k] / start_stats[k], 3) if start_stats.get(k) and stats.get(k) is not None else None)
                       for k in [self.dps_stat, "TotalEHP", *self.hit_stats] if k in stats},
            "utility": {"start": round(self.utility(start_stats), 5), "final": round(best.utility, 5)},
            "violations": self._violations(stats),
            "added": sorted(n for n in (names.get(i) for i in after - before) if n),
            "removed": sorted(n for n in (names.get(i) for i in before - after) if n),
            "iterations": it,
            "measurements": measured,
            "seconds": round(time.time() - t_start, 1),
            "history": self.history,
            "notes": notes,
        }
