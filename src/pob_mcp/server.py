"""MCP server for Path of Building Community (PoE1).

Exposes PoB's real calculation engine to MCP clients: read builds, compute
stats, try what-if edits and export the result.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal, Optional

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver import Image
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP, Image
from pydantic import BaseModel, Field

from . import library, online
from .engine import PobEngine, PobError, encode_build_code, resolve_build_input

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
        "leveling trees; render_tree draws the tree. Item sets and config sets keep alternatives "
        "(budget gear, mapping vs boss); rank_anoints and create_cluster_jewel cover anoints and "
        "cluster jewels; create_flask equips magic flasks; popular_builds shows what ladder players "
        "run (poe.ninja); read_build_guide (or the design_build / review_build prompts) gives the "
        "build-design method to follow when creating or judging a build; "
        "list_saved_builds / load_saved_build / watch_builds_folder work with "
        "PoB's own Builds folder. "
        "All numbers come from Path of Building's own calculation engine."
    ),
)

engine: PobEngine | None = None
_history: list[str] = []          # XML snapshots for undo
_source: dict = {}                # {"path": Path, "mtime": float} when loaded from a saved build
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
    "create_cluster_jewel", "anoint",
    "add_item_set", "switch_item_set", "rename_item_set", "delete_item_set",
    "add_config_set", "switch_config_set", "rename_config_set", "delete_config_set",
    # skills
    "create_socket_group", "delete_socket_group",
    "add_gem", "remove_gem", "replace_gem", "set_gem",
    "set_main_skill", "set_group_enabled",
    # items
    "equip_item", "equip_unique", "create_rare", "create_flask", "set_flask_active", "equip_timeless", "reset_tree", "unequip_slot",
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
        "CLUSTER JEWELS  create_cluster_jewel: socket (jewel socket id or 'Jewel <id>') + skill (enchant "
        "name, e.g. 'Fire Damage'; see list_cluster_jewel_options) + optional size, passives, notables "
        "(names), mods, item_level, name, allocate ('notables' default, 'all', 'none'). "
        "ANOINT  anoint: node (anointable notable) + optional slot (default Amulet). "
        "ITEM SETS  add_item_set: label (+copy=false for empty gear); switch/delete_item_set: set (index or "
        "title); rename_item_set: label (+set). CONFIG SETS  add_config_set: label + optional values "
        "({configVar: value}) and copy=false to start from defaults; switch/delete_config_set: set; "
        "rename_config_set: label (+set). "
        "TREE SPECS  add_tree_spec: label (+copy=false for an empty tree); switch_tree_spec / "
        "delete_tree_spec: spec (index or title); rename_tree_spec: label (+spec). "
        "ITEMS  equip_unique: name + slot (+variant). create_rare: slot + base + EITHER affixes (exact "
        "mod ids from list_item_mods, optionally {id, roll 0-1}; guaranteed valid) OR mods (free-text "
        "lines; checked against the base's mod pool, with warnings, or an error if strict=true), "
        "+ optional crafted (one bench mod line), name, implicits (default: the base's implicit), "
        "item_level, quality, sockets. "
        "create_flask: base (e.g. 'Quicksilver Flask', 'Divine Life Flask') + affixes (at most one "
        "prefix and one suffix id from list_item_mods on that base) + optional slot (default: first "
        "empty Flask slot), quality (default 20), enabled=false to equip it switched off. Equipped "
        "flasks are switched on, which is what PoB counts. set_flask_active: slot ('Flask 1'..'Flask 5') "
        "+ enabled. "
        "reset_tree: wipe the passive tree; keep (default ['ascendancy', 'keystones', 'jewels']) "
        "re-allocates those picks with their paths (jewels = sockets holding jewels and the "
        "cluster jewel notables). Mastery choices are cleared. "
        "equip_timeless: name (Lethal Pride, Brutal Restraint, Glorious Vanity, Elegant Hubris, "
        "Militant Faith) + seed (the number on the jewel) + conqueror (e.g. 'Kaom') + slot "
        "('Jewel <node id>') or node (an allocated jewel socket). "
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
    affixes: Optional[list[str | dict[str, Any]]] = Field(None, description="Exact mod ids from list_item_mods, or {\"id\": ..., \"roll\": 0-1} (default roll 0.5, the middle of the range)")
    crafted: Optional[list[str]] = Field(None, description="One bench-crafted mod line")
    strict: Optional[bool] = Field(None, description="create_rare: reject mods that can't roll on the base")
    spec: Optional[str | int] = Field(None, description="Tree spec index or title")
    set: Optional[str | int] = Field(None, description="Item set or config set index or title")
    values: Optional[dict[str, Any]] = Field(None, description="add_config_set: config values {var: value}")
    socket: Optional[str | int] = Field(None, description="create_cluster_jewel: jewel socket node id or 'Jewel <id>'")
    size: Optional[Literal["Large", "Medium", "Small"]] = None
    skill: Optional[str] = Field(None, description="Cluster jewel enchant, e.g. 'Fire Damage'")
    passives: Optional[int] = Field(None, description="Cluster jewel passive count")
    notables: Optional[list[str]] = Field(None, description="Cluster jewel notable names")
    allocate: Optional[Literal["notables", "all", "none"]] = None
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
    seed: Optional[int] = Field(None, description="equip_timeless: the timeless jewel's seed number")
    conqueror: Optional[str] = Field(None, description="equip_timeless: conqueror name, e.g. 'Kaom', 'Xibaqua', 'Cadiga'")
    keep: Optional[list[str]] = Field(None, description="reset_tree: what to keep: ascendancy, keystones, jewels")


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
    _source.clear()
    p = Path(os.path.expanduser(source.strip())) if len(source) < 1024 and not source.strip().startswith("<") else None
    if p is not None and not p.is_absolute():
        p = ORIG_CWD / p
    if p is not None and p.suffix.lower() == ".xml" and p.is_file():
        _source.update(path=p, mtime=p.stat().st_mtime)
    return res


@mcp.tool()
def new_build(ascendancy: str | None = None, class_name: str | None = None,
              level: int | None = None) -> dict:
    """Start an empty build, optionally with an ascendancy (which also sets the class),
    a class, and a character level. Returns the point budget for that level.

    Without `level`, PoB's auto-level mode is on: the level is estimated from the points
    allocated, so stats change with level as you add passives. Pass a level (e.g. 90) to
    plan a build at a fixed level. Note that PoB's default configuration treats the enemy as a
    Pinnacle boss; set enemyIsBoss (or use config sets) for mapping numbers.

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
def get_passive_tree(include_nodes: bool = False) -> dict:
    """Allocated keystones, notables, masteries and ascendancy nodes, points used,
    and the official passive tree URL. `distanceJewels` lists jewels that grow with their
    socket's distance from the class start (Split Personality) with that distance.
    include_nodes=true adds every allocated node (id, name, type, stat text), small
    passives included, for diffing two trees."""
    return _call("tree", {"nodes": include_nodes})


@mcp.tool()
def find_passive_nodes(query: str, search_stats: bool = False, include_small: bool = False,
                       limit: int = 25) -> dict:
    """Search notables, keystones, jewel sockets (and your ascendancy's nodes) by name, or by stat text
    with search_stats=true. Unallocated results come first, sorted by how many points
    the path from your current tree costs (pathCost)."""
    return _call("find_nodes", {"query": query, "search_stats": search_stats,
                                "include_small": include_small, "limit": limit}, need_build=False)


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
    return _call("list_classes", need_build=False)


@mcp.tool()
def search_gems(query: str = "", tags: list[str] | None = None, support: bool | None = None,
                include_vaal: bool = False, limit: int = 30) -> dict:
    """Search skill and support gems by name or tag text, e.g. query="lightning",
    tags=["Bow", "Projectile"], support=true for supports only. Returns tags, main
    attribute and a description."""
    return _call("search_gems", {"query": query, "tags": tags, "support": support,
                                 "include_vaal": include_vaal, "limit": limit}, need_build=False)


@mcp.tool()
def search_uniques(query: str = "", item_type: str | None = None, search_mods: bool = False,
                   include_mods: bool = True, limit: int = 20) -> dict:
    """Search PoB's unique item database by name or base; with search_mods=true also by mod
    text (e.g. "critical strike chance"). item_type filters by type, e.g. "Boots", "Bow",
    "Jewel", "Ring". Equip results with an equip_unique edit."""
    return _call("search_uniques", {"query": query, "item_type": item_type,
                                    "search_mods": search_mods, "include_mods": include_mods,
                                    "limit": limit}, need_build=False)


@mcp.tool()
def search_item_bases(query: str = "", item_type: str | None = None, limit: int = 25) -> dict:
    """Search item base types (for create_rare) by name and/or type, e.g. item_type="Bow",
    "Body Armour", "Ring", "Jewel". Highest level bases first, with implicits, defences or
    weapon stats."""
    return _call("search_item_bases", {"query": query, "item_type": item_type, "limit": limit}, need_build=False)


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
    return _call("list_mastery_effects", {"node": node}, need_build=False)


@mcp.tool()
def auto_allocate_passives(
    weights: dict[str, float] | None = None,
    points: int | None = None,
    max_path_cost: int = 10,
    include_keystones: bool = False,
    include_small: bool | None = None,
    avoid: list[str] | None = None,
    max_seconds: int = 40,
    targets: dict[str, float] | None = None,
    masteries: bool = True,
    jewels: list[dict[str, Any]] | None = None,
    anoint: bool = False,
    fast: bool = True,
    target_weight: float | None = None,
    target_mode: Literal["first", "slack", "repair"] = "first",
    refine: bool = True,
    lns_rounds: int = 0,
    lns_seconds: int = 120,
    require: list[str | int | dict[str, Any]] | None = None,
    profile: bool = False,
) -> dict:
    """Spend passive points automatically with a greedy optimiser that uses PoB's calculator.

    Each step evaluates every reachable notable (with its path) and takes the one with the
    best weighted gain per point. `weights` maps stats to importance, scored on % change,
    e.g. {"FullDPS": 1, "TotalEHP": 0.5} (default: main DPS 1 + TotalEHP 0.5). `points`
    defaults to all remaining points at the current level.

    Allocate build-defining picks (keystones, specific notables, ascendancy) yourself first;
    keystones are excluded here unless include_keystones=true. Mastery effects next to
    allocated notables are candidates too (1 point each, at most one effect per mastery type;
    masteries=false to skip). If it hits the time limit the progress is kept; call it again to
    continue.
    Use undo_last_change to revert the whole run.

    include_small (default: on when `targets` are given) also considers small passives,
    which are often the cheapest way to reach life and max-hit targets; it is slower.

    `targets` sets minimums to reach first, e.g. {"PhysicalMaximumHitTaken": 10000,
    "LightningMaximumHitTaken": 30000}: while any is unmet, points go to the nodes that close
    the gaps fastest; after that, to the weights, never dropping a met target below its
    minimum. The result reports each target's status. If the tree cannot close a gap, the
    remaining points go to the weights and `targetNote` says so.

    `jewels` lists jewels to socket, as create_rare specs ({"base": "Viridian Jewel",
    "affixes": [...]}): unallocated jewel sockets then become candidates, valued with the next
    jewel in the list, which is socketed when its socket is chosen. `anoint=true` finally
    anoints the amulet with the best notable for the same weights (skipping anoints that
    would break mana or reservation).

    By default unmet targets come strictly first. `target_weight` (e.g. 1) blends instead:
    each node scores its weighted gain plus target_weight x its progress toward the unmet
    targets (in % of each gap), which keeps more damage when targets are expensive.
    target_mode="slack" spends on the weights while the points left comfortably exceed what
    the unmet targets still need (estimated from the best target progress per point), and
    switches to the targets when that slack runs out; nodes that lower a target are skipped.
    It suits builds whose damage nodes also feed the targets (strength or life stacking).
    target_mode="repair" spends every point on the weights first, then repeatedly trades
    the cheapest damage branches for target nodes until every target is met (falling back
    to targets-first if it cannot).
    After spending, a refinement pass (refine=true) swaps the least valuable added branches
    for better nodes elsewhere while keeping every target met; `swaps` lists what it changed.

    lns_rounds > 0 adds a large-neighbourhood search afterwards: each round drops a few of
    the least valuable branches it added, refills the points with the same targets, and keeps
    the result only if it is better and still meets every target (`lns` lists the kept
    rounds). 10-20 rounds noticeably improve tight builds; budget lns_seconds accordingly.
    Masteries up to 6 points away (with their path) are candidates, so gear-conditional
    masteries are found too.

    fast=true (default) reuses earlier per-node estimates between full refreshes every few
    steps and scores the shortlist exactly; fast=false re-scores everything every step.

    `require` forces picks in before the search, e.g. ["Lightning Walker", "Heart and Soul",
    {"node": 30393, "effect": "from Equipped Body Armour"}] (a mastery takes its effect as
    text or id). They are allocated nearest first so their paths share nodes, paid for out of
    `points`, and the LNS never drops them; `required` in the result shows their cost. Use it
    for the picks a build is built around: the optimiser fills the rest under the targets.

    profile=true adds `profile`: how many PoB recalculations the run made, their average cost
    and their share of the run time.
    """
    snapshot = _call("export_xml")["xml"]
    res = _call("auto_allocate", {
        "weights": weights, "points": points, "max_path_cost": max_path_cost,
        "include_keystones": include_keystones, "include_small": include_small,
        "avoid": avoid or [], "max_seconds": max_seconds, "targets": targets or {},
        "masteries": masteries, "jewels": jewels or [], "anoint": anoint, "fast": fast,
        "target_weight": target_weight, "target_mode": target_mode, "refine": refine,
        "lns_rounds": lns_rounds, "lns_seconds": lns_seconds, "require": require or [],
        "profile": profile,
    })
    _history.append(snapshot)
    del _history[:-_MAX_HISTORY]
    res["undo_available"] = len(_history)
    return res


@mcp.tool()
def optimize_tree(
    mode: Literal["dps", "balanced", "defence"] = "balanced",
    points: int | None = None,
    targets: dict[str, float] | None = None,
    metric: str | None = None,
    keep: list[str | int] | None = None,
    require: list[str | int | dict[str, Any]] | None = None,
    avoid: list[str | int] | None = None,
    include_keystones: bool = False,
    weights: dict[str, float] | None = None,
    max_seconds: int = 900,
    mip_seconds: int = 30,
    swap_radius: int = 16,
) -> dict:
    """Re-optimise the whole passive tree with an exact solver (integer programming) instead of
    the greedy one. Unlike auto_allocate_passives it sees long paths, swaps whole regions, and
    can rebuild an existing tree, not just add to it.

    `mode` picks the balance:
    - "dps": damage first (weights dps 1, EHP 0.1, weakest max hit 0.1)
    - "balanced": dps 0.5, EHP 0.25, weakest max hit 0.25
    - "defence": dps 0.1, EHP 0.5, weakest max hit 0.5
    The score is a weighted sum of log(stat), so each weight is the value of a relative gain:
    in balanced mode +10% DPS is worth the same as +10% EHP and +10% weakest max hit together.
    `weights` overrides parts, e.g. {"dps": 1, "ehp": 0, "hit": 0} with `targets` for
    "max DPS subject to these minimums". `metric` is the damage stat (default: the build's
    main DPS stat).

    How it works: PoB measures every node and mastery effect on the current tree; HiGHS picks
    the best connected tree within the points (paths exact, each mastery effect once,
    attribute requirements and unreserved mana/life kept, `targets` as minimums); PoB applies
    and recalculates it; better trees are measured again and re-solved, worse ones retried with
    fewer changes. Ascendancy, keystones, jewel sockets, cluster jewels and the route to
    distance-scaling jewels stay (`keep` adds more; `require` forces nodes or
    {"node", "effect"} masteries in; `avoid` keeps nodes out). `points` adds that many points
    to the current count (default: fill to the level's budget).

    A run takes a few minutes (each re-measure is a few thousand PoB calculations). The result
    has start/final stats, ratios, what was added and removed, and the per-iteration history.
    undo_last_change reverts it.
    """
    from .optimizer import TreeOptimizer

    e = _engine()
    e.require_build()
    snapshot = e.call("export_xml")["xml"]
    opt = TreeOptimizer(e, mode=mode, metric=metric, targets=targets, points=points, keep=keep,
                        require=require, avoid=avoid, include_keystones=include_keystones,
                        weights=weights, max_seconds=max_seconds, mip_seconds=mip_seconds,
                        swap_radius=swap_radius)
    res = opt.run()
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
def popular_builds(ascendancy: str | None = None, skill: str | None = None, league: str | None = None,
                   top: int = 10) -> dict:
    """What ladder players actually run, from poe.ninja's build data (current league by
    default): how many characters match, and the share using each ascendancy, main skill,
    keystone/key notable, unique, anoint, mastery, bloodline and pantheon. Filter by
    `ascendancy` (e.g. "Deadeye") and/or main `skill` (e.g. "Lightning Arrow").

    Use it to sanity-check a design against what works this league (which uniques and
    keystones a skill's players rely on), not as a template to copy. The ladder is biased
    toward strong, popular and expensive builds. poe.ninja's API is undocumented, so this can
    break if the site changes."""
    return online.build_stats(league=league, ascendancy=ascendancy, skill=skill, top=max(1, min(top, 50)))


@mcp.tool()
def load_ladder_build(ascendancy: str | None = None, skill: str | None = None,
                      account: str | None = None, character: str | None = None,
                      pick: str = "random", league: str | None = None) -> dict:
    """Load a real ladder character from poe.ninja into Path of Building (current league by
    default). Give `account` and `character` for a specific one, or filter the ladder by
    `ascendancy` and/or main `skill` and choose with `pick`: "random" (default), "top", or a
    rank number ("7"). "random" skips characters that look ungeared (poe.ninja EHP under 10k
    or DPS under 100k), which are common on the experience ladder; pick="any" doesn't.
    Returns the build summary, the ladder row (poe.ninja's DPS/EHP figures) and the other
    candidates. Uses poe.ninja's undocumented API."""
    import random as _random
    candidates = None
    if not (account and character):
        lad = online.ladder_characters(league=league, ascendancy=ascendancy, skill=skill)
        candidates = lad["characters"]
        if not candidates:
            raise PobError("No ladder characters match these filters")
        if pick == "top":
            row = candidates[0]
        elif str(pick).isdigit():
            idx = int(pick) - 1
            if not 0 <= idx < len(candidates):
                raise PobError(f"Rank {pick} is out of range (1-{len(candidates)})")
            row = candidates[idx]
        else:
            pool = candidates
            if pick != "any":
                geared = [c for c in candidates
                          if c.get("ehpValue", 0) >= 10_000 and c.get("dpsValue", 0) >= 100_000]
                pool = geared or candidates
            row = _random.choice(pool)
        account, character = row["account"], row["character"]
    else:
        row = {"account": account, "character": character}
    data = online.ladder_character(account, character, league=league)
    res = _engine().load_build(data["pathOfBuildingExport"], f"{character} (poe.ninja)")
    _history.clear()
    _source.clear()
    out = {"loaded": res, "ladder": {**row, "league": data.get("league"), "level": data.get("level"),
                                     "class": data.get("class"),
                                     "mainSkills": [s.get("name") or (s.get("allGems") or [{}])[0].get("name")
                                                    for s in (data.get("skills") or [])][:6],
                                     "updated": data.get("updatedUtc")}}
    if candidates:
        out["otherCandidates"] = [c for c in candidates if c is not row][:10]
    return out


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
def target_report(targets: dict[str, float], metric: str | None = None, top: int = 5,
                  sources: int = 5) -> dict:
    """Where each defensive target stands and what holds it up, tightest first.

    `targets` are minimums as for auto_allocate_passives, e.g. {"LifeUnreserved": 3000,
    "SpellBlockChance": 65}. Per target: value, slackPct (how far above the minimum, negative
    = unmet), cap and overCap where PoB tracks them (block, suppression, resists: block above
    the cap still matters for Versatile Combatant), its largest modifier sources, and
    heldUpBy: the allocated notables, keystones and masteries it depends on most, with the
    value it would drop to without each. The same list is given for `metric` (default: main
    DPS). Use it before a tree change to see which picks are load-bearing, and after a failed
    auto_allocate to see what blocks a target."""
    return _call("target_report", {"targets": targets, "metric": metric, "top": top, "sources": sources})


@mcp.tool()
def compare_trees(other: str, stats: list[str] | None = None) -> dict:
    """Compare the current build's passive tree with another build's (`other`: share code,
    build link, XML or .xml path, as for load_build). Lists the keystones, notables, masteries
    and ascendancy notables only one of them has, points and small-passive counts, and the
    headline stats of both with current/other ratios (add more with `stats`). The current
    build stays loaded and unchanged."""
    src = other.strip()
    if len(src) < 1024 and not src.startswith("<") and src.lower().endswith(".xml"):
        path = Path(os.path.expanduser(src))
        src = str(path if path.is_absolute() else ORIG_CWD / path)
    xml = resolve_build_input(src)
    return _call("compare_trees", {"xml": xml, "stats": stats or []})


@mcp.tool()
def explain_stat(stat: str, mod_name: str | None = None, skill: bool = True, limit: int = 40) -> dict:
    """Where a stat comes from: PoB's breakdown (e.g. base x increased x more) and every
    modifier source (tree node, item, gem, base) with its value, as in PoB's Calcs tab.
    `stat` is a name from get_stats (e.g. "Life", "CritChance", "FireResist"). If the modifier
    name differs from the stat, pass mod_name (e.g. stat "TotalEHP" has no single modifier).
    skill=false looks at character-wide modifiers instead of the main skill's.

    For hit damage, pass a damage type ("Physical", "Lightning", "Cold", "Fire", "Chaos"):
    the breakdown shows base damage, added damage, total hit, conversion and the effective
    multiplier from enemy resistances, plus each type's share of the total. "AverageHit"
    shows the crit/non-crit split and "Speed" the attack/cast rate."""
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


@mcp.tool()
def save_to_pob(name: str | None = None, folder: str | None = None, overwrite: bool = False) -> dict:
    """Save the build into Path of Building's Builds folder so it appears in the PoB app's build
    list. Default folder: Documents/Path of Building/Builds (set POB_BUILDS_DIR to change it).
    Without `name`, saves back to the file the build was loaded from (needs overwrite=true)."""
    xml = _call("export_xml")["xml"]
    if name:
        target_dir = Path(os.path.expanduser(folder)) if folder else library.default_builds_dir()
        safe = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "Build"
        path = target_dir / f"{safe}.xml"
    elif _source.get("path"):
        path = _source["path"]
    else:
        raise PobError("Pass a name (this build wasn't loaded from a saved file)")
    if path.exists() and not overwrite:
        raise PobError(f"{path} already exists; pass overwrite=true to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    library.watcher.note_own_write(path)  # don't report our own save as a change
    _source.update(path=path, mtime=path.stat().st_mtime)
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
# Tools: item sets, config sets, anoints, cluster jewels
# ---------------------------------------------------------------------------

@mcp.tool()
def list_item_sets() -> dict:
    """The build's item sets (e.g. budget vs endgame gear), which one is active and how many
    slots each fills. Manage them with add/switch/rename/delete_item_set edits."""
    return _call("item_sets")


@mcp.tool()
def list_config_sets() -> dict:
    """The build's configuration sets (e.g. mapping vs bossing assumptions) with their values,
    and which one is active. Manage them with add/switch/rename/delete_config_set edits."""
    return _call("config_sets")


@mcp.tool()
def rank_anoints(weights: dict[str, float] | None = None, slot: str = "Amulet",
                 replace_current: bool = True, limit: int = 15) -> dict:
    """Rank every anointable notable by its value on this build, using PoB's calculator, with
    the oils needed. By default each is compared against the amulet without its current anoint,
    i.e. what you'd get by re-anointing. `weights` default: main DPS + 0.5 x TotalEHP.
    Apply with an anoint edit."""
    return _call("rank_anoints", {"weights": weights, "slot": slot,
                                  "replace_current": replace_current, "limit": limit})


@mcp.tool()
def list_cluster_jewel_options(size: Literal["Large", "Medium", "Small"] = "Large",
                               skill: str | None = None, item_level: int = 84) -> dict:
    """Cluster jewel options. Without `skill`: the enchants (small passive types) for that size,
    with passive counts, sockets and the notable limit. With `skill` (e.g. "Fire Damage"): every
    notable that can roll with it, whether it's a prefix or suffix, and its stats. Create one
    with a create_cluster_jewel edit."""
    return _call("cluster_jewel_options", {"size": size, "skill": skill, "item_level": item_level},
                 need_build=False)


@mcp.tool()
def rank_gear_upgrades(weights: dict[str, float] | None = None, slots: list[str] | None = None,
                       max_tier: int = 2, roll: float = 0.5, per_slot: int = 3) -> dict:
    """Which single mod change on each rare item gains the most, using PoB's calculator.

    For every equipped rare (or the given `slots`), each mod that can roll on the base and
    isn't on the item yet (tiers 1..`max_tier`, at `roll` 0-1 of its range) is tried: in a
    free prefix/suffix, or replacing the item's weakest mod of that type. Returns the best
    `per_slot` per slot, sorted by weighted gain (default weights: main DPS 1 + TotalEHP 0.5),
    with what it replaces and the effect on DPS, EHP, life, max hits and resists. Uniques
    are listed as skipped. Use trade_search on the upgraded item to see what it costs."""
    return _call("gear_upgrades", {"weights": weights, "slots": slots, "max_tier": max_tier,
                                   "roll": roll, "per_slot": per_slot})


@mcp.tool()
def audit_config(stats: list[str] | None = None) -> dict:
    """Check the Configuration tab for inflated numbers: switches off each condition that is
    turned on (enemy shocked, on full life, charges, buffs, ...) one at a time and reports
    what it was worth for the main DPS, EHP and max hits (or `stats`). Conditions with no
    effect are listed separately. Use it before trusting or reporting a build's numbers."""
    return _call("config_audit", {"stats": stats or []})


@mcp.tool()
def list_ascendancy_notables(ascendancy: str | None = None) -> dict:
    """Every ascendancy's notables with their exact stats for the current PoB version, grouped
    by class (or one ascendancy). Bloodline ascendancies are listed too. The full list is large
    (~9k tokens); pass `ascendancy` when you only need one."""
    return _call("ascendancy_notables", {"ascendancy": ascendancy}, need_build=False)


@mcp.tool()
def list_keystones() -> dict:
    """All keystones on the passive tree with their exact stats, plus whether each is allocated
    and its path cost from the current tree."""
    return _call("keystones", need_build=False)


# ---------------------------------------------------------------------------
# The build-design guide (the pob-build-designer skill), served to any client
# ---------------------------------------------------------------------------

def _guide_dir() -> Path | None:
    if os.environ.get("POB_MCP_GUIDE", "1").strip().lower() in ("0", "false", "no", "off"):
        return None  # guide switched off (e.g. for baseline evaluations)
    here = Path(__file__).resolve().parent
    for cand in (here / "skill",  # installed wheel
                 here.parents[1] / "skills" / "pob-build-designer"):  # source checkout
        if (cand / "SKILL.md").is_file():
            return cand
    return None


def _guide_files() -> dict[str, Path]:
    d = _guide_dir()
    if d is None:
        return {}
    files = {"SKILL.md": d / "SKILL.md"}
    for f in sorted((d / "references").glob("*.md")):
        files[f.stem] = f
    return files


def _read_guide(name: str) -> str:
    files = _guide_files()
    if not files:
        raise PobError("The build-design guide is not installed with this server")
    key = name.strip()
    if key.lower() in ("", "skill", "skill.md", "main", "index"):
        key = "SKILL.md"
    key = key.removeprefix("references/").removesuffix(".md") if key != "SKILL.md" else key
    if key not in files:
        raise PobError(f"No guide section '{name}'. Available: {', '.join(files)}")
    text = files[key].read_text(encoding="utf-8")
    if key == "SKILL.md" and text.startswith("---"):
        text = text.split("---", 2)[2].lstrip()  # drop the skill frontmatter
    return text


@mcp.tool()
def read_build_guide(section: str = "SKILL.md") -> dict:
    """The build-design method for these tools: how to pick a strong concept, scale damage,
    layer defenses to community benchmarks, choose realistic gear and avoid inflated PoB
    numbers. Read "SKILL.md" (the default) before designing or judging a build, then the
    sections it points to: damage-scaling, defense, archetypes, gear-and-progression,
    pob-pitfalls, sources."""
    files = _guide_files()
    return {"section": section, "sections": list(files), "text": _read_guide(section)}


@mcp.prompt()
def design_build(brief: str) -> str:
    """Design a Path of Exile build from a brief (skill, budget, content goal...)."""
    return ("Design a Path of Exile 1 build with the Path of Building tools, following this "
            "method. Read the reference sections it names with read_build_guide when a step "
            f"needs them.\n\nBrief: {brief}\n\n---\n\n{_read_guide('SKILL.md')}")


@mcp.prompt()
def review_build(build: str) -> str:
    """Judge an existing build or guide (PoB code, pobb.in link or .xml path)."""
    return ("Load this build with load_build and judge it with the method below: check the "
            "configuration, gear realism and defenses, then quantify the two or three best "
            f"changes with compare_changes.\n\nBuild: {build}\n\n---\n\n{_read_guide('SKILL.md')}"
            f"\n\n---\n\n{_read_guide('pob-pitfalls')}")


@mcp.resource("pob-guide://{section}", mime_type="text/markdown")
def guide_resource(section: str) -> str:
    """A section of the build-design guide (SKILL, damage-scaling, defense, ...)."""
    return _read_guide(section)


# ---------------------------------------------------------------------------
# Tools: the PoB Builds folder
# ---------------------------------------------------------------------------

def _builds_dir(folder: str | None) -> Path:
    return Path(os.path.expanduser(folder)) if folder else library.default_builds_dir()


@mcp.tool()
def list_saved_builds(query: str | None = None, folder: str | None = None) -> dict:
    """Builds saved in Path of Building's Builds folder (including subfolders), with class,
    ascendancy, level and last-modified time. `query` filters by name or path."""
    d = _builds_dir(folder)
    builds = library.list_builds(d, query)
    return {"folder": str(d), "count": len(builds), "builds": builds}


@mcp.tool()
def load_saved_build(name: str, folder: str | None = None) -> dict:
    """Load a build from PoB's Builds folder by name (or relative path). save_to_pob without a
    name then saves back to the same file."""
    path = library.find_build(_builds_dir(folder), name)
    return load_build(str(path))


@mcp.tool()
def watch_builds_folder(enabled: bool = True, folder: str | None = None) -> dict:
    """Start (or stop) watching PoB's Builds folder for builds created, saved or deleted in the
    PoB app. Changes are collected in the background; get them with get_build_folder_changes."""
    if not enabled:
        library.watcher.stop()
        return {"watching": False}
    d = _builds_dir(folder)
    library.watcher.start(d)
    return {"watching": True, "folder": str(d)}


@mcp.tool()
def get_build_folder_changes(reload_if_changed: bool = False) -> dict:
    """Changes in the Builds folder since the last call (start watching first), and whether the
    loaded build's own file was modified in PoB. With reload_if_changed=true the loaded build is
    reloaded from its file and the stat changes are reported. Reloading discards unsaved edits
    made here."""
    events = library.watcher.take_events()
    res: dict[str, Any] = {"watching": library.watcher.running, "changes": events}
    src = _source.get("path")
    if src is not None:
        changed = src.exists() and src.stat().st_mtime != _source.get("mtime")
        res["loadedFile"] = str(src)
        res["loadedFileChanged"] = changed
        if changed and reload_if_changed:
            before = _call("summary")
            load_build(str(src))
            after = _call("summary")
            diffs = {}
            for k, a in before["stats"].items():
                b = after["stats"].get(k)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) > 1e-6:
                    diffs[k] = {"before": a, "after": b, "change": round(b - a, 2)}
            res["reloaded"] = True
            res["statChanges"] = diffs
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
