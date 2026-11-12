"""Engine-level tests: each one drives PoB through the same JSON API the MCP tools use."""

from __future__ import annotations

import pytest

from pob_mcp.engine import PobError, decode_build_code, encode_build_code

from .conftest import edit


# ---------------------------------------------------------------------------
# Loading, reading, exporting
# ---------------------------------------------------------------------------

def test_load_and_summary(occ):
    s = occ.call("summary")
    assert (s["className"], s["ascendancy"], s["mainSkill"]) == ("Witch", "Occultist", "Vortex")
    assert s["level"] == 99
    assert s["stats"]["TotalDotDPS"] > 0
    assert s["stats"]["Life"] > 1000


def test_read_tools(occ):
    assert len(occ.call("skills")["groups"]) >= 5
    items = occ.call("items")["items"]
    assert any(i["slot"] == "Body Armour" for i in items)
    tree = occ.call("tree")
    assert "Elemental Overload" in tree["keystones"]
    assert tree["url"].startswith("https://www.pathofexile.com/passive-skill-tree/")
    assert occ.call("stats", {"keys": ["Life"]})["Life"] > 0


def test_export_roundtrip(occ):
    xml = occ.call("export_xml")["xml"]
    assert decode_build_code(encode_build_code(xml)) == xml
    occ.load_build(encode_build_code(xml))
    assert occ.call("summary")["mainSkill"] == "Vortex"


def test_bad_build_code(engine):
    with pytest.raises(PobError):
        engine.load_build("definitely not a build code")


# ---------------------------------------------------------------------------
# What-if and editing
# ---------------------------------------------------------------------------

def test_compare_restores_build(occ):
    before = occ.call("summary")["stats"]
    res = occ.call("compare_edits", {"edits": [{"op": "deallocate_node", "node": "Elemental Overload"}]})
    assert res["changes"]["CombinedDPS"]["change"] < 0
    assert occ.call("summary")["stats"] == before


def test_edit_errors_are_clear(occ):
    with pytest.raises(PobError, match="Passive node not found"):
        occ.call("apply_edits", {"edits": [{"op": "allocate_node", "node": "Not A Node"}]})
    with pytest.raises(PobError, match="Unrecognised gem"):
        occ.call("apply_edits", {"edits": [{"op": "add_gem", "group": 5, "gem": "Not A Gem"}]})


def test_gem_name_with_support_suffix(deadeye):
    groups = deadeye.call("skills")["groups"]
    names = [g["name"] for g in groups[0]["gems"]]
    assert "Mirage Archer" in names


def test_config_changes_dps(occ):
    res = occ.call("compare_edits", {"edits": [{"op": "set_config", "var": "enemyIsBoss", "value": "None"}]})
    assert res["changes"]["CombinedDPS"]["change"] > 0


# ---------------------------------------------------------------------------
# Optimisation
# ---------------------------------------------------------------------------

def test_rank_passives(occ):
    add = occ.call("rank_passives", {"limit": 5})
    assert add["results"] and add["results"][0]["gainPerPoint"] > 0
    remove = occ.call("rank_passives", {"mode": "remove", "limit": 3})
    assert remove["results"]


def test_rank_support_gems(occ):
    res = occ.call("rank_support_gems", {"limit": 5})
    assert res["results"]
    names = {g["name"] for g in res["results"]}
    assert "Cast when Stunned" not in names  # trigger supports excluded by default
    assert res["currentSupports"]


def test_warnings_on_empty_character(engine):
    engine.call("new_build")
    engine.loaded = True
    kinds = {w["kind"] for w in engine.call("warnings")["warnings"]}
    assert "resist" in kinds


# ---------------------------------------------------------------------------
# Building from scratch
# ---------------------------------------------------------------------------

def test_new_build_setup(deadeye):
    s = deadeye.call("summary")
    assert (s["className"], s["ascendancy"], s["level"]) == ("Ranger", "Deadeye", 90)
    assert s["mainSkill"] == "Lightning Arrow"
    assert s["passivePointsAvailable"] == 89 + 23 + 1  # +1 for killing all bandits (default)
    assert s["stats"]["CombinedDPS"] > 0


def test_auto_allocate(deadeye):
    res = deadeye.call("auto_allocate", {"points": 12, "weights": {"CombinedDPS": 1}})
    assert 0 < res["pointsSpent"] <= 12
    assert res["allocated"]


def test_mastery(deadeye):
    edit(deadeye, {"op": "allocate_node", "node": "Heartseeker"})
    masteries = deadeye.call("list_mastery_effects")["masteries"]
    assert masteries
    m = masteries[0]
    res = edit(deadeye, {"op": "set_mastery", "node": m["name"], "effect": str(m["effects"][0]["effectId"])})
    assert m["name"] in res["notes"][0]


def test_uniques_and_bases(deadeye):
    assert deadeye.call("search_uniques", {"query": "tabula"})["uniques"][0]["name"] == "Tabula Rasa"
    edit(deadeye, {"op": "equip_unique", "name": "Tabula Rasa", "slot": "Body Armour"})
    assert any(i["name"].startswith("Tabula Rasa") for i in deadeye.call("items")["items"])
    assert deadeye.call("search_item_bases", {"item_type": "Bow", "limit": 3})["bases"]


# ---------------------------------------------------------------------------
# Valid item mods and gap fixing
# ---------------------------------------------------------------------------

def test_list_item_mods(engine):
    res = engine.call("list_item_mods", {"base": "Two-Stone Ring", "query": "resist"})
    groups = {g["group"] for g in res["groups"]}
    assert {"FireResistance", "ColdResistance"} <= groups
    assert res["limits"] == {"prefixes": 3, "suffixes": 3}


def test_create_rare_affixes(deadeye):
    raw = deadeye.call("items")["items"][0]["raw"]
    assert "Prefix: {range:0.5}LocalAddedLightningDamageTwoHand10" in raw
    with pytest.raises(PobError, match="can't roll"):
        edit(deadeye, {"op": "create_rare", "slot": "Ring 1", "base": "Two-Stone Ring",
                       "affixes": ["LocalAddedLightningDamageTwoHand10"]})
    with pytest.raises(PobError, match="same group"):
        edit(deadeye, {"op": "create_rare", "slot": "Ring 1", "base": "Two-Stone Ring",
                       "affixes": ["FireResist5", "FireResist6"]})


def test_create_rare_text_validation(deadeye):
    res = edit(deadeye, {"op": "create_rare", "slot": "Ring 1", "base": "Two-Stone Ring",
                         "mods": ["+40% to Fire Resistance", "+500 to Strength"]})
    assert any("may not be craftable" in n for n in res["notes"])
    with pytest.raises(PobError, match="can't exist"):
        edit(deadeye, {"op": "create_rare", "slot": "Ring 1", "base": "Two-Stone Ring",
                       "strict": True, "mods": ["+500 to Strength"]})


def test_fix_gaps(deadeye):
    edit(deadeye, *[{"op": "create_rare", "slot": s, "base": b, "mods": ["+70 to maximum Life"]}
                    for s, b in [("Helmet", "Lion Pelt"), ("Gloves", "Slink Gloves"),
                                 ("Boots", "Slink Boots"), ("Body Armour", "Zodiac Leather"),
                                 ("Belt", "Stygian Vise")]])
    plan = deadeye.call("fix_gaps", {})
    assert plan["gapsBefore"] and plan["edits"]
    total_before = sum(plan["gapsBefore"].values())
    total_after = sum(plan["gapsAfter"].values()) if plan["gapsAfter"] else 0
    assert total_after < total_before
    # preview only: the build is unchanged
    assert deadeye.call("fix_gaps", {})["gapsBefore"] == plan["gapsBefore"]


# ---------------------------------------------------------------------------
# Tree routing
# ---------------------------------------------------------------------------

def test_tree_map(deadeye):
    whole = deadeye.call("tree_map", {})
    assert 500 < whole["vertexCount"] < 2000
    assert len(whole["vertices"]) < 80_000  # stays compact
    region = deadeye.call("tree_map", {"radius": 6})
    assert region["vertexCount"] < whole["vertexCount"]


def test_routes_and_allocate_path(deadeye):
    routes = deadeye.call("find_routes", {"target": "Heartseeker"})["routes"]
    assert len(routes) >= 2
    assert routes[0]["cost"] <= routes[1]["cost"]
    res = edit(deadeye, {"op": "allocate_path", "nodes": routes[1]["nodeIds"]})
    assert res["info"]["passivePointsUsed"] == routes[1]["cost"]
    with pytest.raises(PobError, match="don't connect"):
        edit(deadeye, {"op": "allocate_path", "nodes": ["Mind Over Matter"]})


# ---------------------------------------------------------------------------
# Understanding a build
# ---------------------------------------------------------------------------

def test_explain_stat(occ):
    res = occ.call("explain_stat", {"stat": "Life"})
    assert res["breakdown"]
    assert any(s["source"].startswith("Tree:") for s in res["sources"]) or res["sources"]
    assert res["totals"]["base"] > 0


def test_scenarios(occ):
    res = occ.call("scenarios", {})["scenarios"]
    assert res["mapping"]["stats"]["CombinedDPS"] > res["uber"]["stats"]["CombinedDPS"]


def test_trade_weights(occ):
    w = occ.call("trade_weights", {"slot": "Ring 1"})
    assert w["category"] == "accessory.ring"
    assert w["stats"] and all(s["tradeId"].startswith(("explicit.", "pseudo.")) for s in w["stats"])


# ---------------------------------------------------------------------------
# Leveling, rendering, import
# ---------------------------------------------------------------------------

def test_plan_leveling(occ):
    res = occ.call("plan_leveling", {"levels": [40, 80]})
    used = [p["pointsUsed"] for p in res["plan"]]
    assert used[0] < used[1]
    specs = occ.call("tree_specs")["specs"]
    assert len(specs) == 3 and specs[0]["active"]


def test_tree_render_data(occ):
    data = occ.call("tree_render", {"highlight": ["Wicked Ward"]})
    assert len(data["nodes"]) > 1000 and data["edges"]
    from pob_mcp.render import render_tree
    png = render_tree(data, width=600)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_import_character_json(engine):
    from .conftest import POB_ROOT
    text = (POB_ROOT / "spec" / "System" / "SampleCharacter.json").read_text(encoding="utf-8")
    info = engine.call("import_character", {"character_json": text})
    engine.loaded = True
    assert (info["className"], info["ascendancy"], info["level"]) == ("Templar", "Hierophant", 99)
    assert len(engine.call("items")["items"]) > 10


# ---------------------------------------------------------------------------
# Item sets, config sets, anoints, cluster jewels
# ---------------------------------------------------------------------------

def test_item_sets(occ):
    edit(occ, {"op": "add_item_set", "label": "Budget"}, {"op": "unequip_slot", "slot": "Body Armour"})
    sets = occ.call("item_sets")["itemSets"]
    assert [s["title"] for s in sets] == ["Default", "Budget"] and sets[1]["active"]
    budget_life = occ.call("summary")["stats"]["Life"]
    edit(occ, {"op": "switch_item_set", "set": "Default"})
    assert occ.call("summary")["stats"]["Life"] > budget_life
    edit(occ, {"op": "delete_item_set", "set": "Budget"})
    assert len(occ.call("item_sets")["itemSets"]) == 1


def test_config_sets(occ):
    boss = occ.call("summary")["stats"]["CombinedDPS"]
    edit(occ, {"op": "add_config_set", "label": "Mapping", "values": {"enemyIsBoss": "None"}})
    assert occ.call("summary")["stats"]["CombinedDPS"] > boss
    edit(occ, {"op": "switch_config_set", "set": 1})
    assert occ.call("summary")["stats"]["CombinedDPS"] == boss
    titles = [s["title"] for s in occ.call("config_sets")["configSets"]]
    assert titles == ["Default", "Mapping"]


def test_anoints(occ):
    res = occ.call("rank_anoints", {"limit": 3})
    assert res["currentAnoints"] and res["results"]
    best = res["results"][0]
    assert best["oils"]
    edit(occ, {"op": "anoint", "node": best["name"]})
    amulet = next(i for i in occ.call("items")["items"] if i["slot"] == "Amulet")
    assert f"Allocates {best['name']}" in amulet["raw"]
    assert amulet["raw"].count("Allocates") == 1
    with pytest.raises(PobError, match="can't be anointed"):
        edit(occ, {"op": "anoint", "node": best["name"], "slot": "Ring 1"})


def test_cluster_jewels(engine):
    engine.call("new_build")
    engine.loaded = True
    edit(engine, {"op": "set_ascendancy", "ascendancy": "Elementalist"}, {"op": "set_level", "level": 95},
         {"op": "create_socket_group", "set_main": True, "gems": ["Fireball"]})
    opts = engine.call("cluster_jewel_options", {"size": "Large", "skill": "Fire Damage"})["notables"]
    prefixes = [n["name"] for n in opts if n["affix"] == "Prefix"]
    suffixes = [n["name"] for n in opts if n["affix"] == "Suffix"]
    picks = prefixes[:2] + suffixes[:1]
    with pytest.raises(PobError, match="max 2"):
        edit(engine, {"op": "create_cluster_jewel", "socket": "Jewel 7960", "skill": "Fire Damage",
                      "notables": prefixes[:3]})
    edit(engine, {"op": "create_cluster_jewel", "socket": "Jewel 7960", "skill": "Fire Damage",
                  "passives": 8, "notables": picks})
    assert set(picks) <= set(engine.call("tree")["notables"])
    with pytest.raises(PobError, match="can't roll"):
        edit(engine, {"op": "create_cluster_jewel", "socket": "Jewel 7960", "skill": "Fire Damage",
                      "notables": ["Heart of Oak"]})


def test_game_data_overviews(engine):
    engine.call("new_build")
    asc = engine.call("ascendancy_notables", {"ascendancy": "Deadeye"})["ascendancies"]
    assert len(asc) == 1 and asc[0]["class"] == "Ranger" and asc[0]["notables"]
    allasc = engine.call("ascendancy_notables", {})["ascendancies"]
    assert {"Juggernaut", "Necromancer", "Champion"} <= {a["ascendancy"] for a in allasc}
    keys = {k["name"] for k in engine.call("keystones")["keystones"]}
    assert {"Chaos Inoculation", "Resolute Technique"} <= keys


def test_damage_type_breakdown_and_life_reservation(engine):
    engine.call("new_build")
    engine.call("apply_edits", {"edits": [
        {"op": "set_level", "level": 90},
        {"op": "create_socket_group", "set_main": True, "gems": ["Fireball"]},
        {"op": "create_socket_group", "gems": ["Wrath", "Arrogance"]}]})
    br = engine.call("explain_stat", {"stat": "Fire"})["breakdown"]
    assert any("Base damage" in line for line in br)
    msgs = [w["message"] for w in engine.call("warnings")["warnings"]]
    assert any("of life is reserved" in m for m in msgs)


def test_flasks(deadeye):
    before = deadeye.call("stats", {"keys": ["Evasion"]})["Evasion"]
    res = deadeye.call("apply_edits", {"edits": [
        {"op": "create_flask", "base": "Jade Flask", "affixes": ["FlaskEffectReducedDuration3", "FlaskBuffEvasionWhileHealing4"]}]})
    assert "Flask 1" in res["notes"][0]
    on = deadeye.call("stats", {"keys": ["Evasion"]})["Evasion"]
    assert on > before
    deadeye.call("apply_edits", {"edits": [{"op": "set_flask_active", "slot": "Flask 1", "enabled": False}]})
    assert deadeye.call("stats", {"keys": ["Evasion"]})["Evasion"] == before
    with pytest.raises(PobError, match="one prefix"):
        deadeye.call("apply_edits", {"edits": [{"op": "create_flask", "base": "Jade Flask",
                                                "affixes": ["FlaskIncreasedDuration6", "FlaskEffectReducedDuration3"]}]})


def test_auto_allocate_targets(deadeye):
    life = deadeye.call("stats", {"keys": ["Life"]})["Life"]
    res = deadeye.call("auto_allocate", {"points": 25, "weights": {"CombinedDPS": 1},
                                         "targets": {"Life": life + 150}, "max_seconds": 60})
    assert res["targets"]["Life"]["met"]
    assert res["allocated"][0].get("forTargets")


def test_timeless_jewel(deadeye):
    edit(deadeye, {"op": "allocate_node", "node": 34483})
    res = edit(deadeye, {"op": "equip_timeless", "name": "Lethal Pride", "seed": 11403, "conqueror": "Kaom",
                         "slot": "Jewel 34483"})
    assert "seed 11403" in res["notes"][-1] or any("seed 11403" in n for n in res["notes"])
    with pytest.raises(PobError, match="outside"):
        edit(deadeye, {"op": "equip_timeless", "name": "Brutal Restraint", "seed": 99, "conqueror": "Balbala",
                       "slot": "Jewel 34483"})


def test_auto_allocate_masteries_and_jewels(deadeye):
    jewel = {"base": "Viridian Jewel", "affixes": ["PercentIncreasedLifeJewel", "LightningDamageJewel"]}
    res = deadeye.call("auto_allocate", {"points": 30, "weights": {"CombinedDPS": 1, "Life": 1},
                                         "jewels": [jewel], "max_seconds": 90, "max_path_cost": 9})
    types = {a["type"] for a in res["allocated"]}
    assert "Mastery" in types
    assert res.get("jewelsPlaced") in (0, 1)


def test_gear_upgrades_and_config_audit(deadeye):
    res = deadeye.call("gear_upgrades", {"per_slot": 2})
    assert res["upgrades"] and res["upgrades"][0]["slot"] == "Weapon 1"
    deadeye.call("apply_edits", {"edits": [{"op": "set_config", "var": "useFrenzyCharges", "value": True}]})
    audit = deadeye.call("config_audit", {})
    assert any(c["var"] == "useFrenzyCharges" for c in audit["conditions"])


def test_minion_stats_readable(engine):
    engine.call("new_build")
    engine.loaded = True
    engine.call("apply_edits", {"edits": [
        {"op": "set_ascendancy", "ascendancy": "Necromancer"}, {"op": "set_level", "level": 80},
        {"op": "create_socket_group", "set_main": True, "gems": ["Summon Raging Spirit", "Minion Damage"]}]})
    st = engine.call("stats", {"keys": ["Minion.CombinedDPS", "Minion.Life"]})
    assert st.get("Minion.CombinedDPS", 0) > 0 and st.get("Minion.Life", 0) > 0
    assert any(k.startswith("Minion.") for k in engine.call("stats", {"filter": "minion.life"}))
    sc = engine.call("scenarios", {"stats": ["Minion.CombinedDPS"]})["scenarios"]
    assert sc["mapping"]["stats"]["Minion.CombinedDPS"] > 0
    res = engine.call("compare_edits", {"edits": [{"op": "remove_gem", "group": 1, "gem": "Minion Damage"}]})
    assert "Minion.CombinedDPS" in res["changes"]


def test_reset_tree_keeps_ascendancy(deadeye):
    asc_before = deadeye.call("summary")["ascendancyPointsUsed"]
    res = edit(deadeye, {"op": "reset_tree"})
    assert "Reset the tree" in res["notes"][0]
    s = deadeye.call("summary")
    assert s["ascendancyPointsUsed"] == asc_before
    assert s["passivePointsUsed"] <= 10


def test_reset_tree_keeps_split_personality_route(occ):
    nodes = occ.call("tree", {"nodes": True})["nodes"]
    sockets = [n["id"] for n in nodes if n["type"] == "Socket" and n["id"] < 65536]
    assert sockets
    edit(occ, {"op": "equip_unique", "name": "Split Personality", "slot": f"Jewel {sockets[0]}"})
    before = occ.call("tree")["distanceJewels"][0]["distance"]
    assert before > 0
    res = edit(occ, {"op": "reset_tree"})
    assert any("route" in n for n in res["notes"])
    assert occ.call("tree")["distanceJewels"][0]["distance"] == before


def test_auto_allocate_slack_mode(deadeye):
    life = deadeye.call("stats", {"keys": ["Life"]})["Life"]
    res = deadeye.call("auto_allocate", {"points": 20, "weights": {"CombinedDPS": 1}, "max_seconds": 60,
                                         "targets": {"Life": life * 1.1}, "target_mode": "slack"})
    assert res["slackTries"]
    assert res["targets"]["Life"]["met"]


def test_auto_allocate_keeps_build_valid(deadeye):
    res = deadeye.call("auto_allocate", {"points": 30, "weights": {"CombinedDPS": 1}, "max_seconds": 60})
    assert all(v["met"] for v in (res.get("validity") or {}).values())


def test_auto_allocate_require(deadeye):
    before = deadeye.call("tree")["notables"]
    assert "Heartseeker" not in before
    res = deadeye.call("auto_allocate", {"points": 15, "weights": {"CombinedDPS": 1},
                                         "require": ["Heartseeker"], "max_seconds": 30})
    assert "Heartseeker" in deadeye.call("tree")["notables"]
    assert res["required"]["points"] >= 1
    assert res["required"]["points"] + res["pointsSpent"] <= 15


def test_auto_allocate_require_over_budget(deadeye):
    with pytest.raises(PobError, match="require"):
        deadeye.call("auto_allocate", {"points": 1, "weights": {"CombinedDPS": 1},
                                       "require": ["Heartseeker", "Ambush"]})


def test_target_report(deadeye):
    life = deadeye.call("stats", {"keys": ["Life"]})["Life"]
    res = deadeye.call("target_report", {"targets": {"Life": life + 500, "BlockChance": 0}})
    rows = {r["stat"]: r for r in res["targets"]}
    assert rows["Life"]["met"] is False and rows["Life"]["slackPct"] < 0
    assert res["tightest"] == "Life"
    assert res["metric"]["value"] > 0


def test_compare_trees(deadeye):
    xml = deadeye.call("export_xml")["xml"]
    edit(deadeye, {"op": "allocate_node", "node": "Heartseeker"})
    res = deadeye.call("compare_trees", {"xml": xml})
    assert "Heartseeker" in res["current"]["notables"]
    assert res["points"]["current"] > res["points"]["other"]
    assert "Heartseeker" in deadeye.call("tree")["notables"]  # current build kept
