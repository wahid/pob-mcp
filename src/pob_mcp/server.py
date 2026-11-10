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
    "allocate_node", "deallocate_node",
    "equip_item", "unequip_slot",
    "add_gem", "remove_gem", "replace_gem", "set_gem",
    "set_main_skill", "set_group_enabled",
    "set_config", "set_level", "add_custom_mod",
]


class Edit(BaseModel):
    """One change to the build. Fill only the fields the chosen `op` uses."""

    op: EditOp = Field(description=(
        "allocate_node/deallocate_node: node. "
        "equip_item: slot + item_text. unequip_slot: slot. "
        "add_gem: group + gem (+level, quality). remove_gem: group + gem or gem_index. "
        "replace_gem: group + (old_gem or gem_index) + new_gem (+level, quality). "
        "set_gem: group + gem_index + any of level/quality/enabled. "
        "set_main_skill: group (+skill_part). set_group_enabled: group + enabled and/or include_in_full_dps. "
        "set_config: var + value (see list_config_options). set_level: level. "
        "add_custom_mod: mod (a modifier line, e.g. '10% more Damage')."
    ))
    node: Optional[str] = Field(None, description="Passive node name or numeric id")
    slot: Optional[str] = Field(None, description="Item slot, e.g. 'Ring 1', 'Body Armour' (see list_item_slots)")
    item_text: Optional[str] = Field(None, description="Item text as copied from the game (Ctrl+C) or PoB")
    group: Optional[int] = Field(None, description="Socket group index from get_skills")
    gem: Optional[str] = Field(None, description="Gem name")
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
def new_build() -> dict:
    """Start an empty level-1 build (Scion, no tree, no items)."""
    res = _engine().call("new_build")
    _engine().loaded = True
    _history.clear()
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
    """Valid slot names for equip_item / unequip_slot."""
    return _call("slots")


@mcp.tool()
def get_passive_tree() -> dict:
    """Allocated keystones, notables, masteries and ascendancy nodes, points used,
    and the official passive tree URL."""
    return _call("tree")


@mcp.tool()
def find_passive_nodes(query: str, search_stats: bool = False, include_small: bool = False,
                       limit: int = 25) -> dict:
    """Search notables/keystones (and your ascendancy's nodes) by name, or by stat text
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
