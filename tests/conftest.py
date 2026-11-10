"""Shared fixtures. The suite needs a Path of Building checkout:

    git clone https://github.com/PathOfBuildingCommunity/PathOfBuilding
    POB_PATH=/path/to/PathOfBuilding pytest

Tests that need the internet are marked `online` and skipped unless you run
`pytest -m online`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pob_mcp.engine import PobEngine, PobError, find_pob_src


def _pob_root() -> Path | None:
    try:
        return find_pob_src(os.environ.get("POB_PATH")).parent
    except PobError:
        return None


POB_ROOT = _pob_root()
requires_pob = pytest.mark.skipif(POB_ROOT is None, reason="Set POB_PATH to a PathOfBuilding checkout")


def pytest_configure(config):
    config.addinivalue_line("markers", "online: needs internet access (poe.ninja, pathofexile.com)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m") and "online" in config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="online test; run with -m online")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def engine():
    if POB_ROOT is None:
        pytest.skip("Set POB_PATH to a PathOfBuilding checkout")
    return PobEngine(str(POB_ROOT))


@pytest.fixture(scope="session")
def sample_xml():
    """A test build that ships with PoB (Witch Occultist, Vortex)."""
    if POB_ROOT is None:
        pytest.skip("Set POB_PATH to a PathOfBuilding checkout")
    return (POB_ROOT / "spec" / "TestBuilds" / "3.13" / "OccVortex.xml").read_text(encoding="utf-8")


@pytest.fixture()
def occ(engine, sample_xml):
    """The sample build, freshly loaded for each test."""
    engine.load_build(sample_xml)
    return engine


@pytest.fixture()
def deadeye(engine):
    """A fresh level 90 Deadeye with Lightning Arrow and a bow."""
    engine.call("new_build")
    engine.loaded = True
    engine.call("apply_edits", {"edits": [
        {"op": "set_ascendancy", "ascendancy": "Deadeye"},
        {"op": "set_level", "level": 90},
        {"op": "create_socket_group", "set_main": True, "gems": ["Lightning Arrow", "Mirage Archer"]},
        {"op": "create_rare", "slot": "Weapon 1", "base": "Thicket Bow",
         "affixes": ["LocalAddedLightningDamageTwoHand10", "LocalIncreasedPhysicalDamagePercent8"]},
    ]})
    return engine


def edit(engine, *ops):
    return engine.call("apply_edits", {"edits": list(ops)})
