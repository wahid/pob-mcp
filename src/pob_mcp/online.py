"""Online helpers: poe.ninja prices, trade-site search links, character import.

These are the only parts of pob-mcp that talk to the internet. Requests are
cached and identify the app with a User-Agent, as poe.ninja asks.
"""

from __future__ import annotations

import base64
import gzip
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .engine import PobError

USER_AGENT = "pob-mcp/0.3 (Path of Building MCP server; personal use)"
NINJA = "https://poe.ninja/poe1/api/economy"
POE = "https://www.pathofexile.com"
UNIQUE_TYPES = ["UniqueWeapon", "UniqueArmour", "UniqueAccessory", "UniqueFlask", "UniqueJewel"]
CACHE_SECONDS = 15 * 60  # poe.ninja refreshes PoE1 data about every 15 minutes

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _get_json(url: str, timeout: float = 20, cache: bool = True):
    with _lock:
        hit = _cache.get(url)
        if cache and hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PobError(f"{url.split('?')[0]} returned HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise PobError(f"Could not reach {url.split('?')[0]}: {exc}") from exc
    if cache:
        with _lock:
            _cache[url] = (time.time(), data)
    return data


# ---------------------------------------------------------------------------
# Leagues and prices (poe.ninja)
# ---------------------------------------------------------------------------

def current_league() -> str:
    """The current temporary challenge league, according to poe.ninja."""
    leagues = _get_json(f"{NINJA}/leagues")
    if isinstance(leagues, list) and leagues:
        first = leagues[0]
        return first.get("name") or first.get("id")
    raise PobError("poe.ninja returned no leagues")


def _league_id(league: str) -> str:
    """poe.ninja wants its own league id; map a display name to it when possible."""
    try:
        for lg in _get_json(f"{NINJA}/leagues"):
            if league.lower() in (str(lg.get("name", "")).lower(), str(lg.get("id", "")).lower()):
                return lg.get("id") or league
    except PobError:
        pass
    return league


def unique_prices(names: list[str], league: str | None = None) -> dict:
    league = league or current_league()
    lid = _league_id(league)
    wanted = {n.lower(): n for n in names}
    found: dict[str, dict] = {}
    for t in UNIQUE_TYPES:
        if len(found) == len(wanted):
            break
        data = _get_json(f"{NINJA}/stash/current/item/overview?league={urllib.parse.quote(lid)}&type={t}")
        for line in data.get("lines", []):
            key = str(line.get("name", "")).lower()
            if key in wanted:
                entry = {
                    "name": line.get("name"), "base": line.get("baseType"),
                    "chaos": line.get("chaosValue"), "divine": line.get("divineValue"),
                    "listings": line.get("listingCount"), "variant": line.get("variant"),
                    "links": line.get("links"),
                }
                prev = found.get(key)
                # Prefer the unlinked/base variant: the cheapest common listing
                if prev is None or (line.get("links") or 0) < (prev.get("links") or 0):
                    found[key] = entry
    missing = [wanted[k] for k in wanted if k not in found]
    return {"league": league, "prices": list(found.values()), "notFound": missing,
            "source": "poe.ninja (prices update about every 15 minutes)"}


# ---------------------------------------------------------------------------
# Trade-site search links
# ---------------------------------------------------------------------------

def trade_url(league: str, query: dict, realm: str = "pc") -> dict:
    """Two forms of the same search: PoB's compressed form and the plain ?q= form."""
    payload = json.dumps(query, separators=(",", ":"))
    encoded = base64.b64encode(gzip.compress(payload.encode("utf-8"))).decode("ascii")
    encoded = encoded.replace("+", "-").replace("/", "_")
    lg = urllib.parse.quote(league, safe="")
    root = f"{POE}/trade/search" + ("" if realm == "pc" else f"/{realm}")
    return {
        "url": f"{root}/{lg}/{encoded}",
        "url_plain": f"{root}/{lg}?q={urllib.parse.quote(payload, safe='')}",
    }


def build_trade_query(weights: dict, min_sum: float | None = None, online: bool = True,
                      required: list[dict] | None = None) -> dict:
    filters = [{"id": s["tradeId"], "value": {"weight": s["weight"]}} for s in weights["stats"]]
    weight_group: dict = {"type": "weight", "filters": filters}
    if min_sum:
        weight_group["value"] = {"min": round(min_sum, 1)}
    stats = [weight_group]
    if required:
        stats.append({"type": "and", "filters": required})
    query = {
        "query": {
            "status": {"option": "online" if online else "any"},
            "stats": stats,
            "filters": {
                "type_filters": {"filters": {
                    "category": {"option": weights["category"]},
                    "rarity": {"option": "nonunique"},
                }},
            },
        },
        "sort": {"statgroup.0": "desc"},
    }
    return query


# ---------------------------------------------------------------------------
# Character import (pathofexile.com, public profiles)
# ---------------------------------------------------------------------------

def fetch_character(account: str, character: str, realm: str = "pc") -> dict:
    """Downloads a character from a public profile, in the shape PoB's importer expects."""
    q = (f"accountName={urllib.parse.quote(account, safe='')}"
         f"&character={urllib.parse.quote(character, safe='')}&realm={realm}")
    try:
        items = _get_json(f"{POE}/character-window/get-items?{q}", cache=False)
        passives = _get_json(f"{POE}/character-window/get-passive-skills?{q}", cache=False)
    except PobError as exc:
        if "403" in str(exc):
            raise PobError("pathofexile.com refused access. The profile's Characters tab must be public "
                           "(Privacy settings on pathofexile.com), and the account name must include its "
                           "#1234 suffix.") from exc
        if "404" in str(exc):
            raise PobError(f"Character '{character}' not found on account '{account}'.") from exc
        raise
    if not isinstance(items, dict) or "character" not in items:
        raise PobError("Unexpected response from pathofexile.com (is the profile public?)")
    char = dict(items["character"])
    char["equipment"] = items.get("items", [])
    char["passives"] = passives
    char["jewels"] = passives.get("items", []) if isinstance(passives, dict) else []
    return char
