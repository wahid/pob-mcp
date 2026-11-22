"""PoB Builds folder: save, list, load, watch."""

from __future__ import annotations

import time

import pytest

import pob_mcp.server as server
from pob_mcp import library


@pytest.fixture()
def srv(occ):
    server.engine = occ
    yield server
    library.watcher.stop()


def test_save_list_load(srv, tmp_path):
    saved = srv.save_to_pob(name="Occ Vortex", folder=str(tmp_path))
    assert (tmp_path / "Occ Vortex.xml").is_file()
    builds = srv.list_saved_builds(folder=str(tmp_path))["builds"]
    assert builds[0]["className"] == "Witch" and builds[0]["ascendancy"] == "Occultist"
    info = srv.load_saved_build("occ vortex", folder=str(tmp_path))
    assert info["mainSkill"] == "Vortex"
    # save back to the same file
    with pytest.raises(Exception, match="overwrite"):
        srv.save_to_pob()
    assert srv.save_to_pob(overwrite=True)["saved_to"] == saved["saved_to"]


def test_watch_and_reload(srv, tmp_path):
    srv.save_to_pob(name="Watched", folder=str(tmp_path))
    srv.load_saved_build("Watched", folder=str(tmp_path))
    srv.watch_builds_folder(folder=str(tmp_path))
    # change the file as if edited and saved in PoB: lower the character level
    path = tmp_path / "Watched.xml"
    time.sleep(1.1)
    path.write_text(path.read_text(encoding="utf-8").replace('level="99"', 'level="80"', 1), encoding="utf-8")
    (tmp_path / "New.xml").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    time.sleep(4.5)
    res = srv.get_build_folder_changes(reload_if_changed=True)
    kinds = {(c["path"], c["change"]) for c in res["changes"]}
    assert ("Watched.xml", "modified") in kinds and ("New.xml", "created") in kinds
    assert res["loadedFileChanged"] and res["reloaded"]
    assert res["statChanges"]["Life"]["change"] < 0
    assert srv.get_build_folder_changes()["changes"] == []


def test_own_saves_are_not_reported(srv, tmp_path):
    srv.save_to_pob(name="Mine", folder=str(tmp_path))
    srv.watch_builds_folder(folder=str(tmp_path))
    time.sleep(1.1)
    srv.save_to_pob(name="Mine", folder=str(tmp_path), overwrite=True)
    time.sleep(4.5)
    assert srv.get_build_folder_changes()["changes"] == []


def test_pick_within_budget():
    from pob_mcp.online import pick_within_budget
    cands = [
        {"slot": "Boots", "name": "A", "score": 11, "chaos": 4},
        {"slot": "Boots", "name": "B", "score": 8, "chaos": 1},
        {"slot": "Belt", "name": "C", "score": 5, "chaos": 50},
        {"slot": "Ring 1", "name": "D", "score": 6, "chaos": 30},
        {"slot": "Ring 2", "name": "D", "score": 6, "chaos": 30},
    ]
    rich = pick_within_budget(cands, 300)
    assert {c["name"] for c in rich} == {"A", "C", "D"} and len(rich) == 3  # D once, best boots
    poor = pick_within_budget(cands, 31)
    assert sum(c["chaos"] for c in poor) <= 31 and {c["name"] for c in poor} == {"B", "D"}
    assert pick_within_budget(cands, 0.5) == []


def test_trade_query_price_cap():
    from pob_mcp.online import build_trade_query
    q = build_trade_query({"stats": [{"tradeId": "x", "weight": 1}], "category": "armour.boots"},
                          max_price=50, currency="chaos")
    assert q["query"]["filters"]["trade_filters"]["filters"]["price"] == {"max": 50, "option": "chaos"}


def test_timeless_trade_links():
    import base64, gzip, json
    from pob_mcp.timeless import trade_links
    ids = ["explicit.pseudo_timeless_jewel_cadiro", "explicit.pseudo_timeless_jewel_victario",
           "explicit.pseudo_timeless_jewel_caspiro"]
    r = trade_links("Standard", ids, 1, [79620])
    def decode(url):
        raw = url.rsplit("/", 1)[1].replace("-", "+").replace("_", "/")
        return json.loads(gzip.decompress(base64.b64decode(raw)))
    one = decode(r["trade"])["query"]["stats"][0]
    assert one["type"] == "count" and one["filters"] == [{"id": ids[0], "value": {"min": 79620, "max": 79620}}]
    anyc = decode(r["tradeAnyConqueror"])["query"]["stats"][0]
    assert {f["id"] for f in anyc["filters"]} == set(ids)
