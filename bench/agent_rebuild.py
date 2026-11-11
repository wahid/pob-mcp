"""Agent-style rebuild: reset the tree, force in picks chosen for the build (presets),
fill the rest with auto_allocate under the benchmark targets, then improve with LNS.
Usage: agent.py <build> <variant-name> <json list of preset edits> [lns_rounds]
Each preset edit is {"op": "allocate_node", "node": ...} or {"op": "set_mastery", ...}."""
import sys, json, time
sys.path.insert(0, '/home/claude/pob-mcp/src'); sys.path.insert(0, '/home/claude/pob-mcp/bench')
from pob_mcp.engine import PobEngine
import tree_rebuild as tb

b, name, presets = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
rounds = int(sys.argv[4]) if len(sys.argv) > 4 else 20
cfg = json.load(open(f'bench/work/{b}_targets.json'))
T, metric, orig = cfg['T'], cfg['metric'], cfg['orig']
e = PobEngine('/root/pob')
e.load_build(open(f'bench/work/{b}.xml', encoding='utf-8').read())
e.call('apply_edits', {'edits': [{'op': 'reset_tree'}]})
start = e.call('summary')['passivePointsUsed']
notes = []
for ed in presets:
    try:
        r = e.call('apply_edits', {'edits': [ed]})
        notes += r.get('notes', []) if isinstance(r, dict) else []
    except Exception as ex:
        print('preset failed', ed, str(ex)[:150])
used = e.call('summary')['passivePointsUsed']
budget = 123 if b == 'hierophant' else 122
t = time.time()
best = None
snap = e.call('export_xml')['xml']
for mode in ('repair', 'slack'):
    e.load_build(snap)
    left = budget - e.call('summary')['passivePointsUsed']
    r = e.call('auto_allocate', {'points': left, 'weights': {metric: 1}, 'targets': T, 'masteries': True,
                                 'max_seconds': 150, 'max_path_cost': 10, 'include_small': True,
                                 'target_mode': mode, 'lns_rounds': rounds, 'lns_seconds': 1500,
                                 'lns_max_drop': 12, 'lns_temperature': 2, 'lns_refills': 3,
                                 'lns_noise': 0.3, 'lns_regions': 2})
    x = e.call('export_xml')['xml']
    e.load_build(x)
    st = tb.measure(e)
    feas = all((st.get(k) or 0) >= v for k, v in T.items())
    ratio = st[metric] / orig
    print(f'{b} {name} {mode}: dps {ratio:.3f} feasible {feas} pts {st["pointsUsed"]} '
          f'kept {len(r.get("lns") or [])} {round(time.time() - t)}s', flush=True)
    if feas and (best is None or ratio > best[0]):
        best = (ratio, x, mode)
if best:
    open(f'bench/work/agent_{b}_{name}.xml', 'w', encoding='utf-8').write(best[1])
    print(f'BEST {b} {name} {best[0]:.3f} ({best[2]})')
