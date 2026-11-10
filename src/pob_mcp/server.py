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
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

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
    "allocate_node", "deallocate_node", "set_mastery",
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

    op: EditOp = Field(description=(
        "CHARACTER  set_class: class_name. set_ascendancy: ascendancy (also sets the class; 'None' clears). "
        "set_level: level. "
        "TREE  allocate_node/deallocate_node: node (allocating takes the shortest path; works for "
        "ascendancy nodes and jewel sockets). set_mastery: node (mastery name, e.g. 'Life Mastery') + "
        "effect (text fragment or effectId from list_mastery_effects). "
        "SKILLS  create_socket_group: gems (list, active skill first) + optional slot, label, set_main, "
        "include_in_full_dps, level, quality. delete_socket_group: group. "
        "add_gem: group + gem (+level, quality). remove_gem: group + gem or gem_index. "
        "replace_gem: group + (old_gem or gem_index) + new_gem. set_gem: group + gem_index + "
        "level/quality/enabled. set_main_skill: group (+skill_part). "
        "set_group_enabled: group + enabled and/or include_in_full_dps. "
        "ITEMS  equip_unique: name + slot (+variant). create_rare: slot + base + mods (list of mod "
        "lines) + optional name, implicits (default: the base's implicit), item_level, quality, sockets. "
        "equip_item: slot + item_text (pasted item). unequip_slot: slot. "
        "CONFIG  set_config: var + value (see list_config_options). add_custom_mod: mod."
    ))
    node: Optional[str] = Field(None, description="Passive node name or numeric id")
    class_name: Optional[str] = Field(None, description="Class, e.g. 'Ranger' (see list_classes)")
    ascendancy: Optional[str] = Field(None, description="Ascendancy, e.g. 'Deadeye' (see list_classes)")
    effect: Optional[str] = Field(None, description="Mastery effect text fragment or effectId")
    slot: Optional[str] = Field(None, description="Item slot, e.g. 'Ring 1', 'Body Armour', 'Jewel 26725' (see list_item_slots)")
    item_text: Optional[str] = Field(None, description="Item text as copied from the game (Ctrl+C) or PoB")
    name: Optional[str] = Field(None, description="equip_unique: unique name. create_rare: item name")
    variant: Optional[str] = Field(None, description="Unique variant name fragment or number")
    base: Optional[str] = Field(None, description="Item base type, e.g. 'Thicket Bow' (see search_item_bases)")
    mods: Optional[list[str]] = Field(None, description="Explicit mod lines, written as in-game, e.g. '+90 to maximum Life'")
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
    return [e.model_dump(exclude_none=True) if e.op != "set_config" else
            {**e.model_dump(exclude_none=True), "value": e.value} for e in edits]


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
    a class, and a character level (default 1). Returns the point budget for that level.

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
