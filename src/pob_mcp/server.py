"""MCP server for Path of Building Community (PoE1).

Exposes PoB's real calculation engine to MCP clients: read builds, compute
stats, try what-if edits and export the result.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal, Optional

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver import Image
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP, Image
from pydantic import BaseModel, Field

from . import online
from .engine import PobEngine, PobError, encode_build_code

log = logging.getLogger("pob_mcp")
ORIG_CWD = Path.cwd()  # the engine chdirs into PoB's src folder

mcp = FastMCP(
    "path-of-building",
    instructions=(
        "Tools for Path of Building Community (Path of Exile 1). Load a build first with "
        "load_build (share code, pobb.in / pastebin / poe.ninja link, XML, or .xml file path). "
        "Use compare_changes to test ideas without changing the build, apply_changes to keep "
        "them, undo_last_change to roll back, and export_build to get a share code. "
        "To create a build from scratch: new_build(ascendancy, level), then apply_changes with "
        "create_socket_group / equip_unique / create_rare / allocate_node / set_mastery edits, "
        "auto_allocate_passives for the remaining points, and get_warnings to find gaps "
        "(resists, attributes, reservation) to fix. search_gems, search_uniques, "
        "search_item_bases, list_classes and find_passive_nodes look up game data. "
        "For route control on the passive tree: get_tree_map shows the tree as a condensed graph, "
        "find_routes gives alternative routes to a node (with via/avoid), and an allocate_path edit "
        "allocates exactly the nodes you list. "
        "For real gear use list_item_mods + create_rare affixes; fix_gaps closes resist and "
        "attribute gaps; trade_search and price_check use live market data; explain_stat, "
        "evaluate_scenarios and compare_builds help explain a build; plan_leveling makes "
        "leveling trees; render_tree draws the tree. "
        "All numbers come from Path of Building's own calculation engine."
    ),
)

engine: PobEngine | None = None
_history: list[str] = []          # XML snapshots for undo
_MAX_HISTORY = 20


def _engine() -> PobEngine:
    if engine is None:
        raise PobError("Engine not initialised")
    return engine


def _call(method: str, params: dict | None = None, need_build: bool = True) -> Any:
    e = _engine()
    if need_build:
        e.require_build()
    return e.call(method, params)


# ---------------------------------------------------------------------------
# Edit schema
# ---------------------------------------------------------------------------

EditOp = Literal[
    # character
    "set_class", "set_ascendancy", "set_level",
    # passive tree
    "allocate_node", "allocate_path", "deallocate_node", "set_mastery",
    "add_tree_spec", "switch_tree_spec", "rename_tree_spec", "delete_tree_spec",
    # skills
    "create_socket_group", "delete_socket_group",
    "add_gem", "remove_gem", "replace_gem", "set_gem",
    "set_main_skill", "set_group_enabled",
    # items
    "equip_item", "equip_unique", "create_rare", "unequip_slot",
    # configuration
    "set_config", "add_custom_mod",
]


class Edit(BaseModel):
    """One change to the build. Fill only the fields the chosen `op` uses."""

    model_config = {"populate_by_name": True}

    op: EditOp = Field(description=(
        "CHARACTER  set_class: class_name. set_ascendancy: ascendancy (also sets the class; 'None' clears). "
        "set_level: level. "
        "TREE  allocate_node/deallocate_node: node (allocating takes the shortest path; works for "
        "ascendancy nodes and jewel sockets). allocate_path: nodes (ids, e.g. a route's nodeIds from "
        "find_routes) are allocated exactly as given, no automatic pathing; every node must connect "
        "to the tree or to another listed node. set_mastery: node (mastery name, e.g. 'Life Mastery') + "
        "effect (text fragment or effectId from list_mastery_effects). "
        "SKILLS  create_socket_group: gems (list, active skill first) + optional slot, label, set_main, "
        "include_in_full_dps, level, quality. delete_socket_group: group. "
        "add_gem: group + gem (+level, quality). remove_gem: group + gem or gem_index. "
        "replace_gem: group + (old_gem or gem_index) + new_gem. set_gem: group + gem_index + "
        "level/quality/enabled. set_main_skill: group (+skill_part). "
        "set_group_enabled: group + enabled and/or include_in_full_dps. "
        "TREE SPECS  add_tree_spec: label (+copy=false for an empty tree); switch_tree_spec / "
        "delete_tree_spec: spec (index or title); rename_tree_spec: label (+spec). "
        "ITEMS  equip_unique: name + slot (+variant). create_rare: slot + base + EITHER affixes (exact "
        "mod ids from list_item_mods, optionally {id, roll 0-1}; guaranteed valid) OR mods (free-text "
        "lines; checked against the base's mod pool, with warnings, or an error if strict=true), "
        "+ optional crafted (one bench mod line), name, implicits (default: the base's implicit), "
        "item_level, quality, sockets. "
        "equip_item: slot + item_text (pasted item). unequip_slot: slot. "
        "CONFIG  set_config: var + value (see list_config_options). add_custom_mod: mod."
    ))
    node: Optional[str | int] = Field(None, description="Passive node name or numeric id")
    nodes: Optional[list[str | int]] = Field(None, description="allocate_path: node ids (or names) to allocate exactly")
    class_name: Optional[str] = Field(None, description="Class, e.g. 'Ranger' (see list_classes)")
    ascendancy: Optional[str] = Field(None, description="Ascendancy, e.g. 'Deadeye' (see list_classes)")
    effect: Optional[str] = Field(None, description="Mastery effect text fragment or effectId")
    slot: Optional[str] = Field(None, description="Item slot, e.g. 'Ring 1', 'Body Armour', 'Jewel 26725' (see list_item_slots)")
    item_text: Optional[str] = Field(None, description="Item text as copied from the game (Ctrl+C) or PoB")
    name: Optional[str] = Field(None, description="equip_unique: unique name. create_rare: item name")
    variant: Optional[str] = Field(None, description="Unique variant name fragment or number")
    base: Optional[str] = Field(None, description="Item base type, e.g. 'Thicket Bow' (see search_item_bases)")
    mods: Optional[list[str]] = Field(None, description="Explicit mod lines, written as in-game, e.g. '+90 to maximum Life'")
    affixes: Optional[list[str | dict[str, Any]]] = Field(None, description="Exact mod ids from list_item_mods, or {\"id\": ..., \"roll\": 0-1}")
    crafted: Optional[list[str]] = Field(None, description="One bench-crafted mod line")
    strict: Optional[bool] = Field(None, description="create_rare: reject mods that can't roll on the base")
    spec: Optional[str | int] = Field(None, description="Tree spec index or title")
    copy_: Optional[bool] = Field(None, alias="copy", description="add_tree_spec: copy the current tree (default true)")
    implicits: Optional[list[str]] = Field(None, description="Implicit mod lines; omit to use the base's implicit")
    item_level: Optional[int] = None
    sockets: Optional[str] = Field(None, description="Socket string, e.g. 'G-G-G-G-G-G'")
    gems: Optional[list[str]] = Field(None, description="Gem names for create_socket_group, active skill first")
    label: Optional[str] = None
    set_main: Optional[bool] = Field(None, description="Make the new group the main skill")
    group: Optional[int] = Field(None, description="Socket group index from get_skills")
    gem: Optional[str] = Field(None, description="Gem name ('Support' suffix optional)")
    gem_index: Optional[int] = Field(None, description="Gem index within the group (from get_skills)")
    old_gem: Optional[str] = None
    new_gem: Optional[str] = None
    level: Optional[int] = None
    quality: Optional[int] = None
    enabled: Optional[bool] = None
    include_in_full_dps: Optional[bool] = None
    skill_part: Optional[int] = None
    var: Optional[str] = Field(None, description="Config variable name")
    value: Any = Field(None, description="Config value: true/false, number, or choice value string")
    mod: Optional[str] = None


def _edits(edits: list[Edit]) -> list[dict]:
    out = []
    for e in edits:
        d = e.model_dump(exclude_none=True, by_alias=True)
        if e.op == "set_config":
            d["value"] = e.value
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Tools: loading and export
# ---------------------------------------------------------------------------

@mcp.tool()
def load_build(source: str, name: str | None = None) -> dict:
    """Load a build into Path of Building.

    `source` may be a PoB share code, a pobb.in / pastebin.com / poe.ninja / poedb build link,
    raw PoB XML, or a path to a .xml file (e.g. one from PoB's Builds folder).
    Returns class, ascendancy, level, main skill and points used.
    """
    res = _engine().load_build(source, name)
    _history.clear()
    return res


@mcp.tool()
def new_build(ascendancy: str | None = None, class_name: str | None = None,
              level: int | None = None) -> dict:
    """Start an empty build, optionally with an ascendancy (which also sets the class),
    a class, and a character level. Returns the point budget for that level.

    Without `level`, PoB's auto-level mode is on: the level is estimated from the points
    allocated, so stats change with level as you add passives. Pass a level (e.g. 90) to
    plan a build at a fixed level.

    Typical flow for creating a build: new_build -> create_socket_group (main skill, set_main)
    -> equip_unique / create_rare for gear -> allocate ascendancy notables -> allocate key
    passives/keystones -> auto_allocate_passives -> set_mastery -> get_warnings -> fix resists
    and attributes -> export_build.
    """
    e = _engine()
    res = e.call("new_build")
    e.loaded = True
    _history.clear()
    edits = []
    if class_name:
        edits.append({"op": "set_class", "class_name": class_name})
    if ascendancy:
        edits.append({"op": "set_ascendancy", "ascendancy": ascendancy})
    if level:
        edits.append({"op": "set_level", "level": level})
    if edits:
        res = e.call("apply_edits", {"edits": edits})["info"]
    return res


@mcp.tool()
def export_build(format: Literal["code", "xml"] = "code", save_to: str | None = None) -> dict:
    """Export the current build as a PoB share code (importable in PoB) or XML.

    If `save_to` is given, the XML is also written to that file path (PoB opens .xml builds
    from its Builds folder).
    """
    xml = _call("export_xml")["xml"]
    out: dict[str, Any] = {}
    if save_to:
        p = Path(os.path.expanduser(save_to))
        if not p.is_absolute():
            p = ORIG_CWD / p
        if p.suffix.lower() != ".xml":
            p = p.with_suffix(".xml")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(xml, encoding="utf-8")
        out["saved_to"] = str(p)
    if format == "xml":
        out["xml"] = xml
    else:
        out["code"] = encode_build_code(xml)
    return out


# ---------------------------------------------------------------------------
# Tools: reading
# ---------------------------------------------------------------------------

@mcp.tool()
def get_build_summary() -> dict:
    """Class, ascendancy, level, main skill and the headline stats (DPS, life, ES, EHP,
    max hit taken, resists, block, suppression, attributes, movement speed)."""
    return _call("summary")


@mcp.tool()
def get_stats(keys: list[str] | None = None, filter: str | None = None) -> dict:
    """Raw calculated stats from PoB's main output table.

    Pass `keys` for exact stat names (e.g. ["TotalDPS", "Life"]), or `filter` for a
    case-insensitive substring (e.g. "resist", "dps", "leech"). With neither, returns
    every scalar stat (large).
    """
    return _call("stats", {"keys": keys or [], "filter": filter})


@mcp.tool()
def get_skills() -> dict:
    """All socket groups with their gems (level, quality, enabled, support/active),
    which group is the main skill, and which count toward Full DPS. Group and gem
    indexes here are what the edit operations use."""
    return _call("skills")


@mcp.tool()
def get_items(include_text: bool = True) -> dict:
    """Equipped items by slot (gear, flasks, tree jewels). With include_text, each item's
    full text including mods is included."""
    res = _call("items")
    if not include_text:
        for it in res["items"]:
            it.pop("raw", None)
    return res


@mcp.tool()
def list_item_slots() -> dict:
    """Valid slot names for equipping items, plus allocated tree jewel sockets
    ('Jewel <nodeId>')."""
    return _call("slots")


@mcp.tool()
def get_passive_tree() -> dict:
    """Allocated keystones, notables, masteries and ascendancy nodes, points used,
    and the official passive tree URL."""
    return _call("tree")


@mcp.tool()
def find_passive_nodes(query: str, search_stats: bool = False, include_small: bool = False,
                       limit: int = 25) -> dict:
    """Search notables, keystones, jewel sockets (and your ascendancy's nodes) by name, or by stat text
    with search_stats=true. Unallocated results come first, sorted by how many points
    the path from your current tree costs (pathCost)."""
    return _call("find_nodes", {"query": query, "search_stats": search_stats,
                                "include_small": include_small, "limit": limit})


@mcp.tool()
def get_config(filter: str | None = None) -> dict:
    """Current Configuration tab values that differ from defaults (enemy type, conditions,
    charges, buffs...). Optional substring filter on variable name."""
    return _call("config", {"filter": filter})


@mcp.tool()
def list_config_options(query: str) -> dict:
    """Search all available Configuration options by variable name or label, with their
    type and allowed choices. Use the `var` with a set_config edit."""
    return _call("config_options", {"query": query}, need_build=False)


# ---------------------------------------------------------------------------
# Tools: what-if and editing
# ---------------------------------------------------------------------------

@mcp.tool()
def compare_changes(edits: list[Edit], extra_stats: list[str] | None = None) -> dict:
    """Try one or more edits, report how the headline stats change, then restore the
    build exactly as it was. Use this to evaluate options (a passive, an item, a gem
    swap, a config assumption) before committing to one.

    Returns `changes`: {stat: {before, after, change, pct}} for every stat that moved.
    Add stat names to `extra_stats` to track anything beyond the defaults.
    """
    return _call("compare_edits", {"edits": _edits(edits), "extra_stats": extra_stats or []})


@mcp.tool()
def apply_changes(edits: list[Edit]) -> dict:
    """Apply edits to the loaded build and keep them. Returns what changed.
    Use undo_last_change to roll back."""
    snapshot = _call("export_xml")["xml"]
    res = _call("apply_edits", {"edits": _edits(edits)})
    _history.append(snapshot)
    del _history[:-_MAX_HISTORY]
    res["undo_available"] = len(_history)
    return res


@mcp.tool()
def undo_last_change() -> dict:
    """Revert the most recent apply_changes call."""
    if not _history:
        raise PobError("Nothing to undo")
    xml = _history.pop()
    res = _engine().call("load_xml", {"xml": xml, "name": "Build"})
    res["undo_available"] = len(_history)
    return res


# ---------------------------------------------------------------------------
# Tools: optimisation
# ---------------------------------------------------------------------------

@mcp.tool()
def rank_passives(
    metric: str | None = None,
    mode: Literal["add", "remove"] = "add",
    max_path_cost: int = 8,
    sort: Literal["per_point", "total"] = "per_point",
    include_small: bool = False,
    include_ascendancy: bool = False,
    limit: int = 15,
) -> dict:
    """Rank passive nodes by how much they change a stat, using PoB's calculator.

    mode="add": every unallocated notable/keystone within `max_path_cost` points is
    evaluated with its whole path (so travel nodes count). Results are sorted by gain per
    point (or total gain with sort="total"). `nodeOnlyGain` is the node by itself.

    mode="remove": every allocated notable/keystone is removed along with the nodes that
    depend on it; sorted from least valuable per refunded point. Good for finding respec
    candidates. Run once per metric you care about (e.g. DPS, then TotalEHP).

    `metric` is any stat name from get_stats (e.g. "TotalEHP", "Life", "TotalDotDPS",
    "Minion.CombinedDPS"). Default: FullDPS if the build uses Full DPS, else the main
    skill's CombinedDPS (minion DPS for minion builds).
    """
    return _call("rank_passives", {
        "metric": metric, "mode": mode, "max_path_cost": max_path_cost,
        "sort": "total" if sort == "total" else "per_point",
        "include_small": include_small, "include_ascendancy": include_ascendancy, "limit": limit,
    })


@mcp.tool()
def rank_support_gems(
    group: int | None = None,
    replace_gem_index: int | None = None,
    metric: str | None = None,
    include_exceptional: bool = True,
    include_triggers: bool = False,
    include_current: bool = True,
    quality: int = 20,
    limit: int = 15,
) -> dict:
    """Rank every support gem that can support a socket group's skill (the same
    compatibility check PoB's gem picker uses).

    By default each support is tried as an extra gem in the group (default: the main skill
    group). Pass `replace_gem_index` (from get_skills) to rank replacements for that gem
    instead, which is what you want for a full link. `currentSupports` shows how much each
    support already in the group contributes (its loss if disabled), weakest first.

    Exceptional (awakened etc.) gems are included unless include_exceptional=false.
    Trigger supports (Cast when..., Cast on...) are excluded by default because they change
    how the skill is used and their DPS numbers are misleading.
    """
    return _call("rank_support_gems", {
        "group": group, "replace_gem_index": replace_gem_index, "metric": metric,
        "include_exceptional": include_exceptional, "include_triggers": include_triggers,
        "include_current": include_current, "quality": quality, "limit": limit,
    })


@mcp.tool()
def get_warnings() -> dict:
    """Problems with the build: PoB's own sidebar warnings (e.g. too many points, cost
    exceeding pool, jewel limits, missing anoints) plus uncapped elemental resists,
    negative chaos resist, unmet attribute requirements, over-reservation, gem errors and
    a disabled main skill."""
    return _call("warnings")


# ---------------------------------------------------------------------------
# Tools: build creation helpers
# ---------------------------------------------------------------------------

@mcp.tool()
def list_classes() -> dict:
    """All classes with their ascendancies and starting attributes."""
    return _call("list_classes")


@mcp.tool()
def search_gems(query: str = "", tags: list[str] | None = None, support: bool | None = None,
                include_vaal: bool = False, limit: int = 30) -> dict:
    """Search skill and support gems by name or tag text, e.g. query="lightning",
    tags=["Bow", "Projectile"], support=true for supports only. Returns tags, main
    attribute and a description."""
    return _call("search_gems", {"query": query, "tags": tags, "support": support,
                                 "include_vaal": include_vaal, "limit": limit})


@mcp.tool()
def search_uniques(query: str = "", item_type: str | None = None, search_mods: bool = False,
                   include_mods: bool = True, limit: int = 20) -> dict:
    """Search PoB's unique item database by name or base; with search_mods=true also by mod
    text (e.g. "critical strike chance"). item_type filters by type, e.g. "Boots", "Bow",
    "Jewel", "Ring". Equip results with an equip_unique edit."""
    return _call("search_uniques", {"query": query, "item_type": item_type,
                                    "search_mods": search_mods, "include_mods": include_mods,
                                    "limit": limit})


@mcp.tool()
def search_item_bases(query: str = "", item_type: str | None = None, limit: int = 25) -> dict:
    """Search item base types (for create_rare) by name and/or type, e.g. item_type="Bow",
    "Body Armour", "Ring", "Jewel". Highest level bases first, with implicits, defences or
    weapon stats."""
    return _call("search_item_bases", {"query": query, "item_type": item_type, "limit": limit})


@mcp.tool()
def get_tree_map(center: str | int | None = None, radius: int | None = None,
                 detail: Literal["names", "stats", "full"] | None = None,
                 format: Literal["text", "json"] = "text") -> dict:
    """The passive tree as a condensed graph, for planning routes yourself.

    Vertices are notables, keystones, jewel sockets, your class start and 'junction' small
    passives where paths branch. Edges are unbranched chains of small passives, with how many
    nodes they contain and the stats they grant. Each vertex shows how many points it costs to
    reach from your current tree. Read the `legend` in the result.

    With no center/radius you get the whole main tree (~13k tokens with detail="names", the
    default for the whole tree; ~42k with "stats"). Give `radius` (in passive points) to get the
    region around your allocated tree, or `center` (a node) + `radius` for another area; regions
    default to detail="stats". detail="full" adds the ids of every small passive on each edge,
    which you can pass straight to an allocate_path edit.
    """
    return _call("tree_map", {"center": center, "radius": radius, "detail": detail, "format": format})


@mcp.tool()
def find_routes(
    target: str | int,
    via: list[str | int] | None = None,
    avoid: list[str | int] | None = None,
    alternatives: int = 3,
    max_extra_points: int = 4,
    evaluate: bool = True,
    metrics: list[str] | None = None,
) -> dict:
    """Up to `alternatives` distinct routes from your current tree to `target`, cheapest first,
    following the same pathing rules as PoB.

    `via`: nodes the route must pass through, in order. `avoid`: nodes it must not use.
    Alternatives are at most `max_extra_points` more expensive than the cheapest route.
    Each route lists its cost, the notables/keystones it picks up, the stats of its small
    passives, and `nodeIds` to pass to an allocate_path edit. With evaluate=true each route is
    also scored with PoB's calculator on `metrics` (default: main DPS, TotalEHP, Life).
    """
    return _call("find_routes", {"target": target, "via": via or [], "avoid": avoid or [],
                                 "alternatives": alternatives, "max_extra_points": max_extra_points,
                                 "evaluate": evaluate, "metrics": metrics or []})


@mcp.tool()
def list_mastery_effects(node: str | None = None) -> dict:
    """Mastery effects. With `node` (e.g. "Life Mastery"), the options for that mastery;
    without, every mastery you can allocate right now (adjacent to an allocated notable).
    Each mastery costs one passive point. Apply with a set_mastery edit."""
    return _call("list_mastery_effects", {"node": node})


@mcp.tool()
def auto_allocate_passives(
    weights: dict[str, float] | None = None,
    points: int | None = None,
    max_path_cost: int = 6,
    include_keystones: bool = False,
    include_small: bool = False,
    avoid: list[str] | None = None,
    max_seconds: int = 40,
) -> dict:
    """Spend passive points automatically with a greedy optimiser that uses PoB's calculator.

    Each step evaluates every reachable notable (with its path) and takes the one with the
    best weighted gain per point. `weights` maps stats to importance, scored on % change,
    e.g. {"FullDPS": 1, "TotalEHP": 0.5} (default: main DPS 1 + TotalEHP 0.5). `points`
    defaults to all remaining points at the current level.

    Allocate build-defining picks (keystones, specific notables, ascendancy) yourself first;
    keystones are excluded here unless include_keystones=true. Masteries are not chosen
    automatically. If it hits the time limit the progress is kept; call it again to continue.
    Use undo_last_change to revert the whole run.
    """
    snapshot = _call("export_xml")["xml"]
    res = _call("auto_allocate", {
        "weights": weights, "points": points, "max_path_cost": max_path_cost,
        "include_keystones": include_keystones, "include_small": include_small,
        "avoid": avoid or [], "max_seconds": max_seconds,
    })
    _history.append(snapshot)
    del _history[:-_MAX_HISTORY]
    res["undo_available"] = len(_history)
    return res


# ---------------------------------------------------------------------------
# Tools: items and gaps
# ---------------------------------------------------------------------------

@mcp.tool()
def list_item_mods(base: str, item_level: int = 86, mod_type: Literal["Prefix", "Suffix"] | None = None,
                   query: str | None = None, tiers: int = 3, include_crafted: bool = False) -> dict:
    """Mods that can actually roll on an item base, grouped by mod group with their best tiers
    (tier 1 = best) and mod ids. Filter with mod_type and query (e.g. "resist", "life").
    Use the ids in a create_rare edit's `affixes` for items that can exist in the game: max 3
    prefixes and 3 suffixes (2+2 on jewels), one mod per group."""
    return _call("list_item_mods", {"base": base, "item_level": item_level, "mod_type": mod_type,
                                    "query": query, "tiers": tiers, "include_crafted": include_crafted},
                 need_build=False)


@mcp.tool()
def fix_gaps(mode: Literal["gear", "tree", "both"] = "both", chaos_target: int | None = None,
             attributes: bool = True, roll: float = 0.75, max_tree_cost: int = 3,
             apply: bool = False) -> dict:
    """Close uncapped elemental resists (and chaos up to `chaos_target`, and unmet attribute
    requirements) using valid suffix mods in free slots on rare gear, then the cheapest tree
    nodes. Returns the gaps before and after, the plan, the effect on headline stats, and the
    `edits`. By default it only previews; pass apply=true (or send the edits to apply_changes).
    `roll` is where in each mod's range values land (0 = minimum, 1 = maximum)."""
    plan = _call("fix_gaps", {"mode": mode, "chaos_target": chaos_target, "attributes": attributes,
                              "roll": roll, "max_tree_cost": max_tree_cost})
    if apply and plan.get("edits"):
        applied = apply_changes([Edit(**e) for e in plan["edits"]])
        plan["applied"] = True
        plan["undo_available"] = applied.get("undo_available")
        plan["note"] = "Applied. Use undo_last_change to revert."
    return plan


# ---------------------------------------------------------------------------
# Tools: trade and prices
# ---------------------------------------------------------------------------

@mcp.tool()
def trade_search(slot: str, weights: dict[str, float] | None = None, base: str | None = None,
                 league: str | None = None, better_than_current: bool = True,
                 max_stats: int = 20, online_only: bool = True) -> dict:
    """Build a pathofexile.com trade search for upgrades in one slot.

    PoB's calculator values every mod that can roll on the slot's item base (by its effect on
    `weights`, default main DPS + 0.5 x TotalEHP), and the search is a weighted sum of those
    stats sorted best first. With better_than_current, the minimum is set to your current item's
    weighted value. Returns the stat weights and links to open in a browser.
    `league` defaults to the current challenge league (looked up on poe.ninja).
    """
    w = _call("trade_weights", {"slot": slot, "weights": weights, "base": base, "max_stats": max_stats})
    if not w["stats"]:
        raise PobError("No mod on this base improves the chosen stats")
    lg = league or online.current_league()
    min_sum = w.get("currentWeightedSum") if better_than_current else None
    query = online.build_trade_query(w, min_sum=min_sum or None, online=online_only)
    return {"league": lg, "slot": slot, "category": w["category"], "currentItem": w.get("currentItem"),
            "minimumWeightedSum": min_sum or None, "statWeights": w["stats"],
            **online.trade_url(lg, query), "query": query}


@mcp.tool()
def price_check(names: list[str] | None = None, league: str | None = None) -> dict:
    """Current poe.ninja prices (chaos and divine) for unique items by name. With no names,
    prices every unique equipped in the build and gives the total. `league` defaults to the
    current challenge league."""
    total = None
    if not names:
        items = _call("items")["items"]
        names = sorted({i["name"].split(",")[0] for i in items if i.get("rarity") == "UNIQUE"})
        if not names:
            return {"prices": [], "note": "No uniques equipped"}
        total = True
    res = online.unique_prices(names, league)
    if total:
        res["totalChaos"] = round(sum(p.get("chaos") or 0 for p in res["prices"]), 1)
        res["totalDivine"] = round(sum(p.get("divine") or 0 for p in res["prices"]), 2)
    return res


# ---------------------------------------------------------------------------
# Tools: understanding a build
# ---------------------------------------------------------------------------

@mcp.tool()
def explain_stat(stat: str, mod_name: str | None = None, skill: bool = True, limit: int = 40) -> dict:
    """Where a stat comes from: PoB's breakdown (e.g. base x increased x more) and every
    modifier source (tree node, item, gem, base) with its value, as in PoB's Calcs tab.
    `stat` is a name from get_stats (e.g. "Life", "CritChance", "FireResist"). If the modifier
    name differs from the stat, pass mod_name (e.g. stat "TotalEHP" has no single modifier).
    skill=false looks at character-wide modifiers instead of the main skill's."""
    return _call("explain_stat", {"stat": stat, "mod_name": mod_name, "skill": skill, "limit": limit})


@mcp.tool()
def evaluate_scenarios(scenarios: dict[str, Any] | None = None, stats: list[str] | None = None) -> dict:
    """Evaluate the build under several Configuration setups in one call, then restore it.

    Default scenarios: mapping (normal enemies), boss (pinnacle) and uber (uber pinnacle). Pass
    your own as {"name": {"configVar": value, ...}} or {"name": "boss"} for a preset; see
    list_config_options for variables. Default stats: DPS figures, EHP, life, ES and max hits.
    """
    return _call("scenarios", {"scenarios": scenarios or {}, "stats": stats or []})


def _snapshot_view() -> dict:
    return {
        "summary": _call("summary"),
        "tree": _call("tree"),
        "skills": _call("skills"),
        "items": _call("items"),
    }


@mcp.tool()
def compare_builds(other: str) -> dict:
    """Compare the loaded build with another build (share code, link, XML or .xml path):
    headline stats, keystones/notables/ascendancy nodes on only one side, gems per skill
    group, and items per slot. The loaded build is left unchanged."""
    e = _engine()
    e.require_build()
    mine_xml = e.call("export_xml")["xml"]
    try:
        e.load_build(other, "Other build")
        theirs = _snapshot_view()
    finally:
        e.call("load_xml", {"xml": mine_xml, "name": "Build"})
    mine = _snapshot_view()

    stats = {}
    for k, a in mine["summary"]["stats"].items():
        b = theirs["summary"]["stats"].get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) > 1e-6:
            stats[k] = {"mine": a, "other": b, "difference": round(b - a, 2),
                        "pct": round((b - a) / abs(a) * 100, 1) if a else None}

    def only(key):
        m, t = set(mine["tree"].get(key) or []), set(theirs["tree"].get(key) or [])
        return {"onlyMine": sorted(m - t), "onlyOther": sorted(t - m)}

    def gem_sets(view):
        return {g["label"]: sorted(x["name"] for x in g["gems"]) for g in view["skills"]["groups"]}

    gm, gt = gem_sets(mine), gem_sets(theirs)
    groups = {}
    for label in sorted(set(gm) | set(gt)):
        if gm.get(label) != gt.get(label):
            groups[label] = {"mine": gm.get(label), "other": gt.get(label)}

    im = {i["slot"]: i["name"] for i in mine["items"]["items"]}
    it = {i["slot"]: i["name"] for i in theirs["items"]["items"]}
    items = {s: {"mine": im.get(s), "other": it.get(s)} for s in sorted(set(im) | set(it)) if im.get(s) != it.get(s)}

    def head(v):
        s = v["summary"]
        return {k: s.get(k) for k in ("className", "ascendancy", "level", "mainSkill", "passivePointsUsed")}

    return {
        "mine": head(mine), "other": head(theirs),
        "statDifferences": stats,
        "keystones": only("keystones"), "notables": only("notables"),
        "ascendancy": only("ascendancyNodes"), "masteries": only("masteries"),
        "skillGroups": groups, "items": items,
    }


# ---------------------------------------------------------------------------
# Tools: leveling, picture, saving, import
# ---------------------------------------------------------------------------

@mcp.tool()
def list_tree_specs() -> dict:
    """The build's passive tree specs (PoB keeps several, e.g. for leveling), with points used
    and which one is active. Manage them with add/switch/rename/delete_tree_spec edits."""
    return _call("tree_specs")


@mcp.tool()
def plan_leveling(levels: list[int] | None = None, weights: dict[str, float] | None = None) -> dict:
    """Create a leveling tree spec for each level (default 30, 50, 70, 90), each one built only
    from nodes in the current (final) tree, choosing the most valuable nodes first by `weights`
    (default main DPS + TotalEHP), with that level's passive and ascendancy points. The final
    tree stays active. Masteries are left out of the leveling trees."""
    snapshot = _call("export_xml")["xml"]
    res = _call("plan_leveling", {"levels": levels or [], "weights": weights})
    _history.append(snapshot)
    del _history[:-_MAX_HISTORY]
    res["undo_available"] = len(_history)
    return res


@mcp.tool()
def render_tree(highlight: list[str | int] | None = None, route_to: str | int | None = None,
                width: int = 1400, whole_tree: bool = False, save_to: str | None = None) -> list:
    """A picture of the passive tree: allocated nodes in gold, highlighted nodes in cyan.
    `route_to` highlights the cheapest route to that node (from find_routes); `highlight` takes
    node names or ids. By default the image is cropped around your tree; whole_tree=true shows
    everything. Optionally saves the PNG to `save_to`."""
    from .render import render_tree as draw
    ids = list(highlight or [])
    if route_to is not None:
        routes = _call("find_routes", {"target": route_to, "alternatives": 1, "evaluate": False})
        ids += routes["routes"][0]["nodeIds"]
    data = _call("tree_render", {"highlight": ids})
    png = draw(data, width=width, crop=not whole_tree)
    note = f"Passive tree, {sum(1 for n in data['nodes'] if n[4])} allocated nodes"
    if save_to:
        path = Path(os.path.expanduser(save_to))
        if not path.is_absolute():
            path = ORIG_CWD / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        note += f"; saved to {path}"
    return [note, Image(data=png, format="png")]


def _default_builds_dir() -> Path:
    if os.environ.get("POB_BUILDS_DIR"):
        return Path(os.path.expanduser(os.environ["POB_BUILDS_DIR"]))
    home = Path.home()
    candidates = [home / "Documents" / "Path of Building" / "Builds",
                  home / "OneDrive" / "Documents" / "Path of Building" / "Builds"]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


@mcp.tool()
def save_to_pob(name: str, folder: str | None = None, overwrite: bool = False) -> dict:
    """Save the build into Path of Building's Builds folder so it appears in the PoB app's build
    list. Default folder: Documents/Path of Building/Builds (set POB_BUILDS_DIR to change it)."""
    xml = _call("export_xml")["xml"]
    target_dir = Path(os.path.expanduser(folder)) if folder else _default_builds_dir()
    safe = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "Build"
    path = target_dir / f"{safe}.xml"
    if path.exists() and not overwrite:
        raise PobError(f"{path} already exists; pass overwrite=true to replace it")
    target_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return {"saved_to": str(path), "note": "In PoB, open the Builds list (or press F5) to see it."}


@mcp.tool()
def import_character(account: str | None = None, character: str | None = None, realm: str = "pc",
                     json_path: str | None = None) -> dict:
    """Import a character into a new build: from a public pathofexile.com profile (account name
    with its #1234 suffix, and character name; the profile's Characters tab must be public),
    or from a saved character JSON file (json_path). Passive tree, jewels, items and gems are
    imported, like PoB's Import tab."""
    if json_path:
        p = Path(os.path.expanduser(json_path))
        if not p.is_absolute():
            p = ORIG_CWD / p
        text = p.read_text(encoding="utf-8")
    elif account and character:
        import json as _json
        text = _json.dumps(online.fetch_character(account, character, realm))
    else:
        raise PobError("Pass account + character, or json_path")
    res = _engine().call("import_character", {"character_json": text})
    _engine().loaded = True
    _history.clear()
    return res


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _protect_stdout():
    """Lua (and PoB) may print to fd 1. Keep the real stdout for MCP only and
    send every other write on fd 1 to stderr."""
    real = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(real, "w", buffering=1, encoding="utf-8")


def main():
    global engine
    ap = argparse.ArgumentParser(description="Path of Building MCP server")
    ap.add_argument("--pob-path", help="Path to a PathOfBuilding checkout (or set POB_PATH)")
    ap.add_argument("--log-level", default=os.environ.get("POB_MCP_LOG", "INFO"))
    args = ap.parse_args()

    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _protect_stdout()
    engine = PobEngine(args.pob_path)
    engine.warm_up()  # boot PoB in the background (~5 s) while the client connects
    mcp.run("stdio")


if __name__ == "__main__":
    main()
