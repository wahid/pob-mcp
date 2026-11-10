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


# ---------------------------------------------------------------------------
# What players run (poe.ninja build ladder)
#
# poe.ninja's build pages are backed by an undocumented API: a protobuf search
# result plus "NDIC" string dictionaries. The decoding below is written against
# what the site serves (November 2026) and may need updating if it changes.
# ---------------------------------------------------------------------------

NINJA_BUILDS = "https://poe.ninja/poe1/api/builds"
_dict_cache: dict[str, list[str]] = {}

# Dimension name in the search result -> name in our output
BUILD_DIMENSIONS = {
    "class": "ascendancies", "skills": "mainSkills", "allgems": "allGems",
    "keypassives": "keystonesAndKeyNotables", "items": "uniques", "anointed": "anoints",
    "masteries": "masteries", "secondascendancy": "bloodlines", "weaponmode": "weaponSetups",
    "pantheon": "pantheon", "bandit": "bandit",
}


def _get_bytes(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise PobError(f"{url.split('?')[0]} returned HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise PobError(f"Could not reach {url.split('?')[0]}: {exc}") from exc


def _pb_fields(buf: bytes) -> list[tuple[int, int, object]]:
    """Generic protobuf wire-format decoder: [(field, wiretype, value)]."""
    out, i, n = [], 0, len(buf)

    def varint(i):
        r = s = 0
        while True:
            c = buf[i]
            i += 1
            r |= (c & 0x7F) << s
            s += 7
            if c < 0x80:
                return r, i

    while i < n:
        key, i = varint(i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, i = varint(i)
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        elif wt == 2:
            ln, i = varint(i)
            v, i = buf[i:i + ln], i + ln
        else:
            raise PobError("Unexpected data from poe.ninja (protobuf wire type %d)" % wt)
        out.append((field, wt, v))
    return out


def _ndic(buf: bytes) -> list[str]:
    """poe.ninja's NDIC string dictionary: header, block index, length bytes, strings."""
    import struct
    if buf[:4] != b"NDIC":
        raise PobError("Unexpected dictionary format from poe.ninja")
    count = struct.unpack_from("<I", buf, 12)[0]
    blocks = struct.unpack_from("<I", buf, 28)[0]
    len_bytes = struct.unpack_from("<I", buf, 32)[0]  # size of the varint length table
    i = 0x24 + 8 * blocks
    end = i + len_bytes
    lengths = []
    while i < end:
        r = sh = 0
        while True:
            c = buf[i]
            i += 1
            r |= (c & 0x7F) << sh
            sh += 7
            if c < 0x80:
                break
        lengths.append(r)
    if len(lengths) != count:
        raise PobError("Could not decode a poe.ninja dictionary (format changed?)")
    out = []
    for ln in lengths:
        out.append(buf[i:i + ln].decode("utf-8", "replace"))
        i += ln
    if i != len(buf):
        raise PobError("Could not decode a poe.ninja dictionary (format changed?)")
    return out


def _dictionary(hash_: str) -> list[str]:
    if hash_ not in _dict_cache:
        _dict_cache[hash_] = _ndic(_get_bytes(f"{NINJA_BUILDS}/dictionary/{hash_}"))
    return _dict_cache[hash_]


def _snapshot(league: str | None, kind: str) -> dict:
    state = _get_json("https://poe.ninja/poe1/api/data/index-state")
    snaps = [s for s in state.get("snapshotVersions", []) if s.get("type") == kind]
    if not snaps:
        raise PobError(f"poe.ninja has no '{kind}' build snapshots")
    if league:
        want = league.lower()
        for s in snaps:
            if want in (str(s.get("name", "")).lower(), str(s.get("url", "")).lower(),
                        str(s.get("snapshotName", "")).lower()):
                return s
        raise PobError(f"No poe.ninja build snapshot for league '{league}'. Available: "
                       + ", ".join(sorted({s['name'] for s in snaps})))
    return snaps[0]


def _search(snap: dict, params: dict) -> tuple[int, dict[str, str], list]:
    url = f"{NINJA_BUILDS}/{snap['version']}/search?" + urllib.parse.urlencode(params)
    with _lock:
        hit = _cache.get(url)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        raw = hit[1]
    else:
        raw = _get_bytes(url)
        with _lock:
            _cache[url] = (time.time(), raw)
    top_fields = _pb_fields(raw)
    if not top_fields or top_fields[0][1] != 2:
        raise PobError("Unexpected search result from poe.ninja (format changed?)")
    result = _pb_fields(top_fields[0][2])
    total = next((v for f, wt, v in result if f == 1 and wt == 0), 0)
    dicts: dict[str, str] = {}
    dims: list[tuple[str, str, list[tuple[int, int]]]] = []
    for f, wt, v in result:
        if wt != 2:
            continue
        if f == 6:  # dictionary reference: name, hash(es)
            parts = [x[2].decode() for x in _pb_fields(v) if x[1] == 2]
            if len(parts) >= 2:
                dicts[parts[0]] = parts[1]
        elif f == 2:  # dimension: id, dictionary name, entries (index, count)
            sub = _pb_fields(v)
            strs = [x[2].decode() for x in sub if x[0] in (1, 2) and x[1] == 2]
            if len(strs) < 2:
                continue
            entries = []
            for x in sub:
                if x[0] == 3 and x[1] == 2:
                    e = {ff: vv for ff, _, vv in _pb_fields(x[2])}
                    entries.append((int(e.get(1, 0)), int(e.get(2, 0))))
            dims.append((strs[0], strs[1], entries))
    return total, dicts, dims


def _canonical(value: str, names: list[str], what: str) -> str:
    import difflib
    by_lower = {n.lower(): n for n in names}
    v = value.strip().lower()
    if v in by_lower:
        return by_lower[v]
    close = difflib.get_close_matches(v, list(by_lower), n=5, cutoff=0.6)
    hint = (" Did you mean: " + ", ".join(by_lower[c] for c in close) + "?") if close else ""
    raise PobError(f"poe.ninja has no {what} named '{value}'.{hint}")


_GENERIC_ITEM = ("Normal ", "Magic ", "Rare ")


def build_stats(league: str | None = None, ascendancy: str | None = None, skill: str | None = None,
                top: int = 10, kind: str = "exp") -> dict:
    """Share of ladder characters using each ascendancy, skill, keystone, unique, anoint and
    mastery, optionally filtered by ascendancy and/or main skill."""
    snap = _snapshot(league, kind)
    params = {"overview": snap["snapshotName"], "type": kind}
    total, dicts, dims = _search(snap, params)
    if ascendancy or skill:
        dim_dict = {d: dn for d, dn, _ in dims}
        if ascendancy:
            params["class"] = _canonical(ascendancy, _dictionary(dicts[dim_dict["class"]]), "class or ascendancy")
        if skill:
            params["skills"] = _canonical(skill, _dictionary(dicts[dim_dict["skills"]]), "main skill")
        total, dicts, dims = _search(snap, params)

    out: dict = {"league": snap["name"], "characters": total,
                 "filters": {k: v for k, v in params.items() if k not in ("overview", "type")},
                 "source": "poe.ninja builds ladder (top characters by experience)"}
    if not total:
        out["note"] = "No ladder characters match these filters."
        return out
    for dim_id, dict_name, entries in dims:
        key = BUILD_DIMENSIONS.get(dim_id)
        if not key or dict_name not in dicts:
            continue
        names = _dictionary(dicts[dict_name])
        rows = [(names[i] if i < len(names) else f"#{i}", c) for i, c in entries if c > 0]
        rows.sort(key=lambda r: -r[1])
        if dim_id == "items":
            generic = [r for r in rows if r[0].startswith(_GENERIC_ITEM)]
            rows = [r for r in rows if not r[0].startswith(_GENERIC_ITEM)]
            out["itemRarityBySlot"] = [{"name": n, "count": c, "pct": round(100 * c / total, 1)}
                                       for n, c in generic[:top]]
        out[key] = [{"name": n, "count": c, "pct": round(100 * c / total, 1)} for n, c in rows[:top]]
    return out
