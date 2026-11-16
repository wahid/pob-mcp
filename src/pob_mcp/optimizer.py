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

# Capped stats and PoB's "how far over the cap" stat. A node that only adds to the overcap
# shows no change in the capped stat, so removing two such nodes looks free when it isn't; the
# linear model therefore works with capped + overcap, which is linear in the nodes.
OVERCAP = {
    "SpellSuppressionChance": "SpellSuppressionChanceOverCap",
    "BlockChance": "BlockChanceOverCap",
    "SpellBlockChance": "SpellBlockChanceOverCap",
    "FireResist": "FireResistOverCap",
    "ColdResist": "ColdResistOverCap",
    "LightningResist": "LightningResistOverCap",
    "ChaosResist": "ChaosResistOverCap",
}
ELE_RESISTS = ["FireResist", "ColdResist", "LightningResist"]

PENALTY = 20.0  # utility lost per unit of relative shortfall on a constraint


@dataclass
class _Constraint:
    stat: str
    target: float
    scale: float
    kind: str  # "validity" or "target"
    lin: str = ""  # the stat the linear model uses for it (capped + overcap), default: stat

    def __post_init__(self):
        self.lin = self.lin or self.stat


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
    mip_seconds: float = 10
    stall_seconds: float = 3
    min_gain: float = 0.003
    weights: dict[str, float] | None = None
    log: object = None
    only: list | None = None             # node ids the tree may use (leveling stages)
    mastery_effects: dict | None = None  # {mastery id: effect}: offer just that effect
    check_validity: bool = True          # keep attribute requirements and unreserved mana/life
    total: int | None = None             # total points for the tree (overrides `points`)
    explain: bool = True                 # value each added/removed branch in the result
    pool: object = None       # MeasurePool: measure on several PoB processes
    progress: object = None   # callable(elapsed_seconds, max_seconds, message)
    cancel: object = None     # threading.Event: stop after the current step, keep the best tree
    history: list = field(default_factory=list)

    # -- PoB side ---------------------------------------------------------------------
    def _measure(self):
        stats = [self.metric or "@dps", "TotalEHP", *HITS,
                 "Str", "Dex", "Int", "ManaUnreserved", "LifeUnreserved",
                 *(self.targets or {}).keys()]
        capped = [t for t in (self.targets or {}) if t in OVERCAP]
        if self.check_validity:
            capped += [r for r in ELE_RESISTS if r not in capped]
        for c in capped:
            stats += [c, OVERCAP[c]]
        seen, req = set(), []
        for s in stats:
            if s not in seen:
                seen.add(s)
                req.append(s)
        params = {"stats": req, "lenient": True, "include_keystones": self.include_keystones,
                  "radius": self._reach()}
        for k in ("keep", "require", "avoid", "only"):
            if getattr(self, k):
                params[k] = getattr(self, k)
        if self.mastery_effects:
            params["mastery_effects"] = {str(k): v for k, v in self.mastery_effects.items()}
        t = time.time()
        model = self.pool.tree_model(params) if self.pool else self.engine.call("tree_model", params)
        self.measure_seconds = getattr(self, "measure_seconds", 0.0) + time.time() - t
        self.real_stats = list(model["stats"])
        # virtual stats: capped + overcap
        names = model["stats"]
        pos = {n: i for i, n in enumerate(names)}
        pairs = [(c, OVERCAP[c]) for c in capped if c in pos and OVERCAP[c] in pos]
        if pairs:
            def extend(d):
                if d is None:
                    return None
                return d + [d[pos[a]] + d[pos[b]] for a, b in pairs]
            for r in model["nodes"]:
                if r.get("d") is not None:
                    r["d"] = extend(r["d"])
                for m in r.get("m") or []:
                    if m.get("d") is not None:
                        m["d"] = extend(m["d"])
            model["stats"] = names + [a + "+overcap" for a, _ in pairs]
            for a, b in pairs:
                model["base"][a + "+overcap"] = model["base"][a] + model["base"][b]
        return model

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

    def _target(self, c):
        # the linear model aims a little above a target it has just missed in the real calculation
        return c.target + getattr(self, "margins", {}).get(c.stat, 0.0)

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
        bmin = 1.0
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
            if c.lin not in idx:
                continue
            i = idx[c.lin]
            cols, vals, const = stat_terms(i)
            s = var(0, np.inf, -PENALTY / c.scale)
            slacks.append((s, max(0.0, self._target(c) - base[c.lin])))
            # b + sum d x + const + s >= target
            row(cols + [s], vals + [1.0], self._target(c) - base[c.lin] - const, np.inf, "constraint " + c.stat)

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
        # warm start: the current tree, extended greedily along the linear model so the
        # solver starts from a good tree instead of having to find one itself
        cur_on = {k for k, r in enumerate(nodes) if r["alloc"] or r["type"] == "ClassStart"}
        cur_sel = {k: m["e"] for k, r in enumerate(nodes) for m in (r.get("m") or []) if m.get("sel")}
        roots_main = [k for k in root_arc if nodes[k]["type"] == "ClassStart" or nodes[k].get("entry")]
        on, sel = self._greedy(nodes, idx, base, cur_on, cur_sel, dist, adj, roots_main,
                               min(add_radius if add_radius is not None else 10 ** 9,
                                   budget - sum(1 for k in cur_on if nodes[k]["type"] != "ClassStart")))

        def build_start(on, sel):
            start = np.zeros(n)
            for k in on:
                start[x[k]] = 1
            for k, e in sel.items():
                start[y[(k, e)][0]] = 1
            D = self._delta(nodes, len(stats), on - cur_on, cur_on - on, sel, cur_sel)
            for (s_col, _), c in zip(slacks, [c for c in self.constraints if c.lin in idx]):
                start[s_col] = max(0.0, self._target(c) - base[c.lin] - D[idx[c.lin]])
            if z is not None:
                start[z] = min(10.0, min((base[stats[i]] - bmin + D[i]) / bmin for i in hit_is))
            parent, order = {}, []
            main = [k for k in root_arc if k in on and (nodes[k]["type"] == "ClassStart" or nodes[k].get("entry"))]
            leaps = [k for k in root_arc if k in on and k not in main]
            seen = set(main)
            queue = list(main)
            while True:
                while queue:
                    k = queue.pop(0)
                    order.append(k)
                    if nodes[k]["type"] == "Mastery":
                        continue
                    for j in adj.get(k, []):
                        if j in on and j not in seen:
                            seen.add(j)
                            parent[j] = k
                            queue.append(j)
                rest = [k for k in leaps if k not in seen]
                if not rest:
                    break
                seen.add(rest[0])
                queue = [rest[0]]
            size = {k: 1 for k in order}
            for k in reversed(order):
                if k in parent:
                    size[parent[k]] += size[k]
            ok = seen == on
            for k in order:
                if k in parent:
                    start[arcs[(parent[k], k)]] = size[k]
                else:
                    start[root_arc[k]] = size[k]
                    if nodes[k]["type"] != "ClassStart" and not nodes[k].get("entry") and size[k] > 1:
                        ok = False  # a leap node feeding others: not a valid start
            bad = []
            for c in range(n):
                if start[c] < lb[c] - 1e-7 or start[c] > ub[c] + 1e-7:
                    bad.append(f"bound of column {c}: {start[c]} not in [{lb[c]}, {ub[c]}]")
            for (cols, vals, lo, hi), tag in zip(rows, tags):
                a = float(sum(start[c] * v for c, v in zip(cols, vals)))
                if a < lo - 1e-6 or a > hi + 1e-6:
                    bad.append(f"{tag}: {a:.3f} not in [{lo}, {hi}]")
            return start, ok and not bad, bad

        start, warm_ok, bad = build_start(on, sel)
        greedy_used = warm_ok and on != cur_on
        greedy_problems = None
        if not warm_ok and on != cur_on:
            greedy_problems = bad[:5]
            start, warm_ok, bad = build_start(cur_on, cur_sel)
        if warm_ok:
            sol0 = highspy.HighsSolution()
            sol0.col_value = list(start)
            sol0.value_valid = True
            h.setSolution(sol0)
        # Stop once the incumbent has not improved for stall_seconds: the solver usually
        # finds its tree in a second or two and spends the rest proving optimality.
        t0 = time.time()
        # The stall clock only runs once the solver has beaten the warm start; until then it
        # gets the full mip_seconds to find a swap.
        start_obj = float(np.dot(np.array(cost), start)) if warm_ok else None
        inc = {"t": t0, "obj": None, "sol": None, "beat": False}

        def improved(ev):
            obj = ev.data_out.objective_function_value
            tol = 1e-4 * max(1.0, abs(obj))
            if inc["obj"] is None or obj > inc["obj"] + tol:
                inc["t"] = time.time()
            if start_obj is None or obj > start_obj + tol:
                inc["beat"] = True
            inc["obj"] = obj
            inc["sol"] = list(ev.data_out.mip_solution)

        def interrupt(ev):
            if inc["sol"] is not None and inc["beat"] and time.time() - inc["t"] > self.stall_seconds:
                ev.interrupt = True

        try:
            h.cbMipImprovingSolution.subscribe(improved)
            h.cbMipInterrupt.subscribe(interrupt)
        except AttributeError:  # older highspy: plain time limit
            pass
        h.run()
        status = h.modelStatusToString(h.getModelStatus())
        sol = h.getSolution().col_value
        if not sol or len(sol) < n or h.getInfo().primal_solution_status != 2:
            if inc["sol"] is not None and len(inc["sol"]) >= n:
                sol, status = inc["sol"], "stalled"  # interrupted: use the last incumbent
            else:
                return None, {"status": status, "warm": warm_ok, "warm_problems": bad[:5] or None}
        chosen = [nodes[k]["id"] for k in range(len(nodes))
                  if sol[x[k]] > 0.5 and nodes[k]["type"] != "ClassStart"]
        mast = {nodes[k]["id"]: e for (k, e), (v, _) in y.items() if sol[v] > 0.5}
        info = {"status": status, "seconds": round(time.time() - t0, 1), "vars": n, "rows": len(rows), "warm": warm_ok,
                "greedy_adds": len(on - cur_on) if greedy_used else 0, "greedy_problems": greedy_problems,
                "warm_problems": bad[:5] or None,
                "objective": inc["obj"] if status == "stalled" else h.getInfo().objective_function_value}
        return (chosen, mast), info

    # -- greedy start for the solver ----------------------------------------------------
    def _delta(self, nodes, nstat, added, removed, sel, cur_sel):
        """Linear stat change (per stat index) of a tree relative to the current one."""
        D = [0.0] * nstat

        def acc(d, sign):
            if d:
                for i, v in enumerate(d):
                    if v:
                        D[i] += sign * v
        for k in added:
            acc(nodes[k].get("d"), 1)
        for k in removed:
            acc(nodes[k].get("d"), -1)
        for k, e in cur_sel.items():
            if sel.get(k) != e:
                acc(next(m for m in nodes[k]["m"] if m["e"] == e).get("d"), -1)
        for k, e in sel.items():
            if cur_sel.get(k) != e:
                acc(next(m for m in nodes[k]["m"] if m["e"] == e).get("d"), 1)
        return D

    def _lin_utility(self, D, idx, base):
        w = self._weights()
        stats = list(idx)
        rel = lambda s: 1.0 / max(abs(base[s]), 1.0)
        u = 0.0
        if self.dps_stat in idx:
            u += w["dps"] * D[idx[self.dps_stat]] * rel(self.dps_stat)
        if "TotalEHP" in idx:
            u += w["ehp"] * D[idx["TotalEHP"]] * rel("TotalEHP")
        hit_is = [idx[h] for h in self.hit_stats if h in idx]
        if hit_is:
            bmin = max(min(base[stats[i]] for i in hit_is), 1.0)
            u += w["hit"] * min(10.0, min((base[stats[i]] - bmin + D[i]) / bmin for i in hit_is))
        for c in self.constraints:
            if c.lin in idx:
                u -= PENALTY / c.scale * max(0.0, self._target(c) - base[c.lin] - D[idx[c.lin]])
        return u

    def _greedy(self, nodes, idx, base, on, sel, dist, adj, roots, adds, depth=4):
        """Add the best short path (gain per point) repeatedly while it helps."""
        on, sel = set(on), dict(sel)
        cur_on, cur_sel = set(on), dict(sel)
        used_eff = set(sel.values())
        nstat = len(idx)
        D = [0.0] * nstat
        u = self._lin_utility(D, idx, base)

        def node_gain(k, D):
            r = nodes[k]
            best, eff = None, None
            d = r.get("d") or [0.0] * nstat
            if r.get("m"):
                for m in r["m"]:
                    if m["e"] in used_eff:
                        continue
                    dm = m.get("d") or [0.0] * nstat
                    D2 = [a + b + c for a, b, c in zip(D, d, dm)]
                    v = self._lin_utility(D2, idx, base)
                    if best is None or v > best[0]:
                        best, eff = (v, D2), m["e"]
                return (best or (None, None)), eff
            D2 = [a + b for a, b in zip(D, d)]
            return (self._lin_utility(D2, idx, base), D2), None

        left = adds
        while left > 0:
            # BFS outward from the tree through unallocated, reachable, allowed nodes
            prev = {}
            # grow only from the part of the tree connected to the class start (leap nodes
            # may carry a single unit of flow, so nothing can hang off them)
            conn, stack = set(), [k for k in roots if k in on]
            while stack:
                k = stack.pop()
                if k in conn:
                    continue
                conn.add(k)
                if nodes[k]["type"] != "Mastery":
                    stack.extend(j for j in adj.get(k, []) if j in on)
            frontier = [k for k in conn if nodes[k]["type"] != "Mastery"]
            seen = set(on)
            layers = []
            for _ in range(min(depth, left)):
                nxt = []
                for k in frontier:
                    for j in adj.get(k, []):
                        r = nodes[j]
                        if j in seen or j not in dist or r.get("avoid"):
                            continue
                        seen.add(j)
                        prev[j] = k
                        nxt.append(j)
                layers.append(nxt)
                frontier = [j for j in nxt if nodes[j]["type"] != "Mastery"]
            best = None
            for L, layer in enumerate(layers, 1):
                for j in layer:
                    path, k = [], j
                    while k not in on:
                        path.append(k)
                        k = prev[k]
                    Dp = D
                    for q in path[1:]:
                        d = nodes[q].get("d")
                        if d:
                            Dp = [a + b for a, b in zip(Dp, d)]
                    (v, D2), eff = node_gain(j, Dp)
                    if v is None:
                        continue
                    rate = (v - u) / L
                    if rate > 1e-9 and (best is None or rate > best[0]):
                        best = (rate, path, eff, v, D2)
            if best is None:
                break
            _, path, eff, u, D = best
            for q in path:
                on.add(q)
            if eff is not None:
                sel[path[0]] = eff
                used_eff.add(eff)
            left -= len(path)
        return on, sel

    # -- main loop ------------------------------------------------------------------------
    def _say(self, msg):
        if self.log:
            self.log(msg)
        if self.progress:
            try:
                self.progress(time.time() - self._t_start, self.max_seconds, msg)
            except Exception:  # noqa: BLE001 - progress is best-effort
                pass

    def _status(self, it, stats, start, used):
        def pct(k):
            a, b = start.get(k), stats.get(k)
            return f"{(b / a - 1) * 100:+.1f}%" if a and b is not None else "n/a"
        hits = [h for h in self.hit_stats if start.get(h) and stats.get(h) is not None]
        worst = min(hits, key=lambda h: stats[h]) if hits else None
        parts = [f"step {it}: DPS {pct(self.dps_stat)}", f"EHP {pct('TotalEHP')}"]
        if worst:
            parts.append(f"weakest max hit {stats[worst] / min(start[h] for h in hits) * 100 - 100:+.1f}%")
        parts.append(f"{used}/{self.total_points} points")
        return ", ".join(parts)

    def _explain(self, rows, before, after, mast_before, mast_after, limit=14):
        """Each added or removed branch, and each switched mastery effect, valued on the final
        tree: what an added branch is worth (stats lost without it) and what a removed one
        would give back."""
        def components(ids):
            ids, comps = set(ids), []
            while ids:
                stack, comp = [ids.pop()], []
                while stack:
                    k = stack.pop()
                    comp.append(k)
                    for j in (rows.get(k) or {}).get("links") or []:
                        if j in ids:
                            ids.discard(j)
                            stack.append(j)
                comps.append(comp)
            return comps

        def effect_text(i, e):
            for m in (rows.get(i) or {}).get("m") or []:
                if m["e"] == e:
                    return m.get("text")
            return None

        def label(comp, mast):
            names = []
            for i in comp:
                r = rows.get(i) or {}
                if r.get("type") in ("Notable", "Keystone"):
                    names.append(r.get("name"))
                elif r.get("type") == "Mastery":
                    names.append("Mastery: " + (effect_text(i, mast.get(i)) or r.get("name") or "?"))
            return sorted(n for n in names if n)

        groups, meta = [], []
        for comp in components(after - before):
            groups.append({"remove": comp})
            meta.append(("added", comp, label(comp, mast_after)))
        for comp in components(before - after):
            groups.append({"add": comp, "masteries": {str(i): mast_before[i] for i in comp if i in mast_before}})
            meta.append(("removed", comp, label(comp, mast_before)))
        for i in sorted(set(mast_before) & set(mast_after) & before & after):
            if mast_before[i] != mast_after[i]:
                groups.append({"masteries": {str(i): mast_before[i]}})
                meta.append(("mastery switched", [i], [f"{effect_text(i, mast_before[i])} -> {effect_text(i, mast_after[i])}"]))
        if not groups:
            return None
        out = self.engine.call("value_groups", {"groups": groups, "stats": self.stat_names})
        base = out["base"]
        hits = [h for h in self.hit_stats if base.get(h)]

        def pct(a, b):
            return round((a / b - 1) * 100, 1) if a is not None and b else None

        res = []
        for (kind, comp, names), st in zip(meta, out["groups"]):
            # added / switched: base is with it, st without -> worth = base vs st
            # removed: st is with it back -> what was given up = st vs base
            with_it, without = (st, base) if kind == "removed" else (base, st)
            entry = {"change": kind, "points": len(comp) if kind != "mastery switched" else 0, "nodes": names,
                     "dps": pct(with_it.get(self.dps_stat), without.get(self.dps_stat)),
                     "ehp": pct(with_it.get("TotalEHP"), without.get("TotalEHP"))}
            if hits:
                a = min((with_it[h] for h in hits if with_it.get(h) is not None), default=None)
                b = min((without[h] for h in hits if without.get(h) is not None), default=None)
                entry["weakestMaxHit"] = pct(a, b)
            entry["utility"] = round(self.utility(with_it) - self.utility(without), 4)
            res.append(entry)
        res.sort(key=lambda r: -abs(r["utility"]))
        shown = res[:limit]
        unmeasured = sorted({n for r in res if r["change"] == "removed" and not any(
            r.get(k) for k in ("dps", "ehp", "weakestMaxHit")) for n in r["nodes"]})
        return {
            "removedWithoutMeasuredValue": unmeasured or None,
            "removedNote": ("These were dropped because they change none of the scored stats (leech, charge "
                            "or flask upkeep, movement, quality of life). Pass them in `keep` if the build "
                            "relies on them.") if unmeasured else None,
            "note": ("Percentages are measured on the final tree: for an added branch, what the build has "
                     "because of it; for a removed branch, what putting it back would add (the price paid "
                     "for the points). Branches interact, so they don't sum exactly."),
            "branches": shown,
            "more": len(res) - len(shown) or None,
        }

    def run(self) -> dict:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of dps, balanced, defence (got {self.mode!r})")
        t_start = self._t_start = time.time()
        self.measure_seconds = self.solve_seconds = 0.0
        self._say("measuring the tree")
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
        self.stat_names = self.real_stats
        self.dps_stat = model["stats"][0]
        self.hit_stats = [h for h in HITS if h in model["stats"]]
        base = model["base"]
        used, budget = model["used"], model["budget"]
        if self.total is not None:
            self.total_points = int(self.total)
        elif self.points is not None:
            self.total_points = used + int(self.points)
        else:
            self.total_points = max(used, budget)

        cons = []
        for attr in ("Str", "Dex", "Int"):
            req = (model.get("required") or {}).get(attr) or 0
            if self.check_validity and req > 0 and attr in base and attr not in (self.targets or {}):
                cons.append(_Constraint(attr, float(req), max(float(req), 1.0), "validity"))
        for pool in ("Mana", "Life"):
            key = pool + "Unreserved"
            if (self.check_validity and ((model.get("reserved") or {}).get(pool) or 0) > 0 and key in base
                    and key not in (self.targets or {})):
                cons.append(_Constraint(key, 0.0, 100.0, "validity"))
        def lin(stat):
            return stat + "+overcap" if stat + "+overcap" in base else stat
        for stat, t in (self.targets or {}).items():
            cons.append(_Constraint(stat, float(t), max(abs(float(t)), 1.0), "target", lin(stat)))
        # elemental resistances that are capped stay capped
        for r in ELE_RESISTS:
            over = OVERCAP[r]
            if (self.check_validity and r not in (self.targets or {}) and r + "+overcap" in base
                    and (base.get(over) or 0) > 0):
                cons.append(_Constraint(r, float(base[r]), max(abs(float(base[r])), 1.0), "validity", r + "+overcap"))
        self.constraints = cons

        start_nodes = [r["id"] for r in model["nodes"] if r["alloc"] and r["type"] != "ClassStart"]
        start_mast = {r["id"]: m["e"] for r in model["nodes"] for m in (r.get("m") or []) if m.get("sel")}
        start_stats = dict(base)
        rows = {r["id"]: r for r in model["nodes"]}
        cur = _State(start_nodes, start_mast, dict(base), self.utility(base))
        if used > self.total_points:
            # over budget (a forced pick was just allocated): any tree within the points beats it
            cur.utility = -math.inf
        best = cur
        free = self.total_points - used
        radius = self.swap_radius
        measured = 1
        it = 0
        notes = []
        tried, rejected_on_model = set(), False
        self.margins, margin_retries = {}, 0
        while it < self.max_iters and time.time() - t_start < self.max_seconds:
            if self.cancel is not None and self.cancel.is_set():
                notes.append("cancelled: kept the best tree found so far")
                break
            it += 1
            model_free = self.total_points - model["excludedPoints"] - sum(
                1 for r in model["nodes"] if r["alloc"] and r["type"] != "ClassStart")
            adds = min(radius + min(max(model_free, 0), self.fill_step), self._reach())
            t = time.time()
            res, info = self._solve(model, max(radius, -model_free), adds)
            self.solve_seconds += time.time() - t
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
                gain = u - cur.utility
                cur = _State(nodes, mast, out["stats"], u)
                if u > best.utility:
                    best = cur
                entry["result"] = "accepted"
                self.history.append(entry)
                self._say(self._status(it, out["stats"], start_stats, len(nodes) + model["excludedPoints"]))
                # diminishing returns: every point is spent and the step barely helped
                if gain < self.min_gain and len(nodes) + model["excludedPoints"] >= self.total_points:
                    notes.append(f"stopped after iteration {it}: gain {gain:.4f} below {self.min_gain}")
                    break
                model = self._measure()
                for r in model["nodes"]:
                    rows[r["id"]] = r
                measured += 1
                radius = max(radius, self.swap_radius)
                tried, rejected_on_model = set(), False
                self.margins, margin_retries = {}, 0
            else:
                entry["result"] = "rejected"
                viol = self._violations(out["stats"])
                if viol:
                    entry["violations"] = viol
                self.history.append(entry)
                self._apply(cur.nodes, cur.masteries)
                # Rejected only because a constraint the model expected to hold was missed for
                # real (max hits and the like are not quite linear): aim above it by the miss
                # and solve the same step again, instead of giving up on steps this size.
                missed = [v for v in viol if (cur.stats.get(v["stat"]) or 0) >= v["target"] - 1e-6]
                plain = dict(out["stats"])
                for v in missed:
                    plain[v["stat"]] = v["target"]
                if missed and margin_retries < 3 and self.utility(plain) > cur.utility + 1e-5:
                    margin_retries += 1
                    for v in missed:
                        self.margins[v["stat"]] = self.margins.get(v["stat"], 0.0) + (v["target"] - v["value"]) * 1.5
                    entry["retry"] = "aiming above " + ", ".join(v["stat"] for v in missed)
                    self._say(f"step {it}: just missed {missed[0]['stat']}, solving again with a margin")
                    continue
                rejected_on_model = True
                self._say(f"step {it}: no better, trying a smaller change")
                radius //= 2
                if radius < 1:
                    break

        # leave the best tree applied, as one undo step
        final = self._apply(best.nodes, best.masteries, undo=True)
        stats = final["stats"]
        before, after = set(start_nodes), set(best.nodes)
        names = {i: r.get("name") for i, r in rows.items()}
        self.result_state = best
        changes = None
        if self.explain and (before != after or start_mast != best.masteries):
            try:
                changes = self._explain(rows, before, after, start_mast, best.masteries)
            except Exception as exc:  # noqa: BLE001 - the explanation is extra, never fail the run on it
                notes.append(f"could not value the changes: {exc}")
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
            "changes": changes,
            "iterations": it,
            "measurements": measured,
            "seconds": round(time.time() - t_start, 1),
            "timing": {"measure": round(self.measure_seconds, 1), "solve": round(self.solve_seconds, 1),
                       "jobs": getattr(self.pool, "jobs", 1) if self.pool else 1},
            "history": self.history,
            "notes": notes,
        }


# ---------------------------------------------------------------------------
# Around the tree: keystone variants and the amulet anoint
# ---------------------------------------------------------------------------

def _after(stats, changes):
    """Stats after a compare_edits result (it lists only what changed)."""
    out = dict(stats)
    for name, c in (changes or {}).items():
        if name in out and isinstance(c, dict) and isinstance(c.get("after"), (int, float)):
            out[name] = c["after"]
    return out


def _stats_now(engine, names):
    return engine.call("stats", {"keys": list(names)})


def optimize(engine, *, keystones=None, max_variants: int = 2, anoint: bool = False, **opts) -> dict:
    """TreeOptimizer.run(), then optionally:

    - `keystones`: "auto" or a list of names. Each candidate keystone is forced into the tree
      and the tree re-optimised around it; the best result (by the same score) is kept. A
      keystone often only pays once the rest of the tree is rebuilt for it, which a single
      step of the optimiser can't see.
    - `anoint`: the amulet anoint that scores best on the finished tree.
    """
    t0 = time.time()
    max_seconds = opts.get("max_seconds", 900)
    cancel, progress = opts.get("cancel"), opts.get("progress")
    want_variants = bool(keystones) and max_variants > 0
    base_opts = dict(opts)
    if want_variants:
        base_opts["max_seconds"] = max_seconds * 0.55
    base = TreeOptimizer(engine, **base_opts)
    res = base.run()
    best_u = base.utility(res["final"])

    def say(msg):
        if progress:
            try:
                progress(time.time() - t0, max_seconds, msg)
            except Exception:  # noqa: BLE001
                pass

    def stopped():
        return (cancel is not None and cancel.is_set()) or time.time() - t0 > max_seconds

    if want_variants and not stopped():
        tried = []
        best_xml = engine.call("export_xml")["xml"]
        listing = engine.call("keystones")["keystones"]
        by_name = {k["name"].lower(): k for k in listing}
        if keystones == "auto":
            cands = []
            reach = base._reach()
            for k in listing:
                if k.get("allocated") or not k.get("pathCost") or k["pathCost"] > reach:
                    continue
                try:
                    ch = engine.call("compare_edits", {"edits": [{"op": "allocate_node", "node": k["name"]}],
                                                       "extra_stats": base.stat_names})["changes"]
                except Exception:  # noqa: BLE001 - e.g. a keystone the build can't path to
                    continue
                stats = _after(res["final"], ch)
                cands.append((base.utility(stats) - best_u, k["name"]))
            # solo value is only a hint (the extra path points flatter it, the lost synergy hurts
            # it); anything not clearly ruinous is worth a real attempt, best first
            cands = [name for du, name in sorted(cands, reverse=True) if du > -0.35][:max_variants]
        else:
            cands = []
            for name in keystones:
                k = by_name.get(str(name).lower())
                if k is None:
                    raise ValueError(f"Unknown keystone: {name}")
                if not k.get("allocated"):
                    cands.append(k["name"])
            cands = cands[:max_variants]
        for n, name in enumerate(cands):
            if stopped():
                break
            say(f"trying keystone {name}")
            left = max_seconds - (time.time() - t0)
            v_opts = dict(opts)
            v_opts.update(max_seconds=max(20.0, left / (len(cands) - n)), total=base.total_points, points=None,
                          require=[name])
            try:
                v = TreeOptimizer(engine, **v_opts)
                v_res = v.run()
                u = base.utility(v_res["final"])
                ok = v_res["points"]["used"] <= base.total_points
            except Exception as exc:  # noqa: BLE001 - a variant that can't be built is just skipped
                tried.append({"keystone": name, "error": str(exc), "kept": False})
                engine.call("load_xml", {"xml": best_xml, "name": "Build"})
                continue
            prev = res["final"]
            entry = {"keystone": name, "scoreGain": round(u - best_u, 4),
                     "vsBestSoFar": {k: round(v_res["final"][k] / prev[k], 3) for k in v_res["ratios"]
                                     if prev.get(k) and v_res["final"].get(k) is not None},
                     # a keystone has side effects the score can't see, so it must clearly pay
                     "kept": bool(ok and u > best_u + 0.01)}
            if not ok:
                entry["note"] = "needs more points than the tree has"
            tried.append(entry)
            if entry["kept"]:
                entry["changes"] = v_res.get("changes")
                best_u = u
                best_xml = engine.call("export_xml")["xml"]
                res.update(final=v_res["final"], violations=v_res["violations"],
                           points={**res["points"], "used": v_res["points"]["used"]})
                res["added"] = sorted(set(res["added"]) | set(v_res["added"]))
                res["removed"] = sorted(set(res["removed"]) | set(v_res["removed"]))
            else:
                engine.call("load_xml", {"xml": best_xml, "name": "Build"})
        res["keystoneVariants"] = tried
        kept = [t["keystone"] for t in tried if t.get("kept")]
        if kept:
            res["notes"].append("keystone variant kept: " + ", ".join(kept))

    if anoint and not stopped():
        say("choosing the anoint")
        res["anoint"] = _best_anoint(engine, base, res, best_u)
        if res["anoint"].get("applied"):
            res["final"] = _stats_now(engine, base.stat_names)
            best_u = base.utility(res["final"])
            res["violations"] = base._violations(res["final"])

    start = res["start"]
    res["ratios"] = {k: (round(res["final"][k] / start[k], 3) if start.get(k) and res["final"].get(k) is not None else None)
                     for k in res["ratios"]}
    res["utility"]["final"] = round(best_u, 5)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _best_anoint(engine, opt, res, best_u, candidates: int = 8) -> dict:
    """Try the top anoints (ranked by PoB on the score's stats) for real and keep the best."""
    w = opt._weights()
    weights = {opt.dps_stat: max(w["dps"], 0.01)}
    if "TotalEHP" in res["final"] and w["ehp"] + w["hit"] > 0:
        weights["TotalEHP"] = w["ehp"] + w["hit"]
    try:
        ranked = engine.call("rank_anoints", {"weights": weights, "limit": candidates})
    except Exception as exc:  # noqa: BLE001 - no amulet, a talisman, ...
        return {"applied": False, "reason": str(exc)}
    tried = []
    for cand in ranked.get("results") or []:
        try:
            ch = engine.call("compare_edits", {"edits": [{"op": "anoint", "node": cand["name"]}],
                                               "extra_stats": opt.stat_names})["changes"]
        except Exception:  # noqa: BLE001
            continue
        stats = _after(res["final"], ch)
        tried.append((opt.utility(stats) - best_u, cand["name"], cand.get("oils")))
    tried.sort(key=lambda t: t[0], reverse=True)
    out = {"applied": False, "current": ranked.get("currentAnoints"),
           "tried": [{"name": n, "gain": round(g, 4)} for g, n, _ in tried[:5]]}
    if tried and tried[0][0] > 1e-4:
        gain, name, oils = tried[0]
        engine.call("apply_edits", {"edits": [{"op": "anoint", "node": name}]})
        out.update(applied=True, name=name, oils=oils, gain=round(gain, 4))
    else:
        out["reason"] = "no anoint beats the current one" if ranked.get("currentAnoints") else "no anoint improves the score"
    return out


# ---------------------------------------------------------------------------
# Leveling: the final tree as nested stages
# ---------------------------------------------------------------------------

def plan_leveling(engine, levels=None, mode: str = "balanced", weights=None, pool=None,
                  progress=None, cancel=None, max_seconds: float = 600) -> dict:
    """A tree spec per level, each the best tree for that level's points made only of nodes of
    the final tree, and each containing the previous one, so leveling never needs a respec.
    Stats are calculated at that level with the build's gear and gems."""
    t0 = time.time()
    levels = sorted(set(int(v) for v in (levels or [30, 50, 70, 90])))
    info = engine.call("leveling_info")
    final = engine.call("tree_model", {"stats": ["@dps"], "lenient": True, "radius": 0})
    final_ids = [r["id"] for r in final["nodes"] if r["alloc"] and r["type"] != "ClassStart"]
    final_mast = {r["id"]: m["e"] for r in final["nodes"] for m in (r.get("m") or []) if m.get("sel")}
    names = {r["id"]: r for r in final["nodes"]}
    allowed = set(final_ids)
    plan, prev_nodes, prev_mast = [], [], {}
    try:
        for n, level in enumerate(levels):
            if cancel is not None and cancel.is_set():
                break
            if progress:
                progress(time.time() - t0, max_seconds, f"level {level} tree")
            stage = engine.call("leveling_stage", {
                "final_spec": info["spec"], "level": level, "nodes": prev_nodes,
                "masteries": {str(k): v for k, v in prev_mast.items()}})
            budget = min(stage["budget"], len(final_ids))
            left = max(30.0, (max_seconds - (time.time() - t0)) / (len(levels) - n))
            opt = TreeOptimizer(engine, mode=mode, weights=weights, only=final_ids, mastery_effects=final_mast,
                                keep=list(prev_nodes), total=budget, check_validity=False, explain=False,
                                pool=pool, cancel=cancel, max_seconds=left, mip_seconds=5)
            res = opt.run()
            state = opt.result_state
            # points left over go to final-tree nodes the score doesn't value (leech, charges,
            # utility), nearest first, so the last stage ends up as the final tree
            spare = budget - len(state.nodes)
            if spare > 0:
                have, order = set(state.nodes), []
                frontier = [i for i in state.nodes] + [r["id"] for r in final["nodes"] if r["type"] == "ClassStart"]
                while frontier and len(order) < spare:
                    nxt = []
                    for i in frontier:
                        if names.get(i, {}).get("type") == "Mastery":
                            continue
                        for j in names.get(i, {}).get("links") or []:
                            if j in allowed and j not in have and len(order) < spare:
                                have.add(j)
                                order.append(j)
                                nxt.append(j)
                    frontier = nxt
                if order:
                    mast = dict(state.masteries)
                    mast.update({i: final_mast[i] for i in order if i in final_mast})
                    out = engine.call("set_tree", {"nodes": state.nodes + order, "no_undo": True,
                                                   "masteries": {str(k): v for k, v in mast.items()}})
                    if not out.get("missing") and not out.get("disconnected"):
                        state = _State(state.nodes + order, mast, {}, 0.0)
                        res["points"]["used"] = out["used"]
                    else:
                        opt._apply(state.nodes, state.masteries)
            new = [i for i in state.nodes if i not in set(prev_nodes)]
            key = [names[i]["name"] for i in new if names.get(i, {}).get("type") in ("Notable", "Keystone")]
            mast = []
            for i in new:
                if i in state.masteries:
                    text = next((m.get("text") for m in names[i].get("m") or [] if m["e"] == state.masteries[i]), None)
                    mast.append(text or names[i]["name"])
            st = _stats_now(engine, [opt.dps_stat, "Life", "TotalEHP"])
            plan.append({"spec": stage["spec"], "level": level, "pointsBudget": stage["budget"],
                         "pointsUsed": res["points"]["used"], "ascendancyPoints": stage["ascendancyPoints"],
                         "newNotables": sorted(key), "newMasteries": mast, "stats": st})
            prev_nodes, prev_mast = list(state.nodes), dict(state.masteries)
    finally:
        engine.call("leveling_finish", {"final_spec": info["spec"], "level": info["level"], "auto": info["auto"]})
    return {"plan": plan, "finalSpec": info["spec"], "method": "nested", "seconds": round(time.time() - t0, 1),
            "clusterJewelPoints": final["excludedPoints"] or None,
            "note": "Each level gets its own tree spec built only from nodes in the final tree, and each "
                    "contains the one before it, so no respec is needed on the way. newNotables / "
                    "newMasteries are what to pick up between stages. Stats are calculated at that level "
                    "with current gear and gems. Switch with switch_tree_spec. Passives inside cluster "
                    "jewels (clusterJewelPoints) are not part of the stages: they come last, in the final tree."}
