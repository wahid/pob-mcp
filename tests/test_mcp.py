"""Starts the real server over stdio and talks to it like an MCP client would."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from .conftest import POB_ROOT, requires_pob


def _run(coro):
    return asyncio.run(coro)


@requires_pob
def test_server_over_stdio(sample_xml):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "pob_mcp", "--pob-path", str(POB_ROOT), "--log-level", "WARNING"],
        )
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                for expected in ("load_build", "compare_changes", "find_routes", "fix_gaps",
                                 "render_tree", "plan_leveling", "trade_search", "rank_anoints",
                                 "list_cluster_jewel_options", "list_saved_builds", "list_item_sets"):
                    assert expected in names

                res = await s.call_tool("load_build", {"source": sample_xml})
                assert not getattr(res, "is_error", getattr(res, "isError", False))
                res = await s.call_tool("get_build_summary", {})
                data = json.loads(res.content[0].text)
                assert data["mainSkill"] == "Vortex"

                # errors reach the client with their message
                res = await s.call_tool("compare_changes", {"edits": [{"op": "allocate_node", "node": "Nope"}]})
                assert getattr(res, "is_error", getattr(res, "isError", False))
                assert "Passive node not found" in res.content[0].text

                res = await s.call_tool("render_tree", {"width": 500})
                assert any(getattr(c, "type", "") == "image" for c in res.content)

    _run(go())


@pytest.mark.online
def test_price_check_online():
    from pob_mcp import online
    res = online.unique_prices(["Tabula Rasa"])
    assert res["prices"] and res["prices"][0]["chaos"] is not None
