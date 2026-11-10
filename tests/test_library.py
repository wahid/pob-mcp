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
