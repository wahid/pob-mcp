"""Embedded Path of Building engine.

Runs PoB's own Lua code inside LuaJIT (via the `lupa` package) so stats come
from PoB's real calculation engine. All Lua work happens on one dedicated
thread because a Lua state is not thread-safe.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import logging
import os
import re
import threading
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

log = logging.getLogger("pob_mcp")

BRIDGE = Path(__file__).with_name("bridge.lua")
_ORIG_CWD = Path.cwd()


try:  # surface our messages to the model (MCP SDK 2.x hides other exceptions)
    from mcp.server.mcpserver.exceptions import ToolError as _ToolErrorBase
except ImportError:  # MCP SDK 1.x
    try:
        from mcp.server.fastmcp.exceptions import ToolError as _ToolErrorBase
    except ImportError:
        _ToolErrorBase = RuntimeError


class PobError(_ToolErrorBase):
    pass


# ---------------------------------------------------------------------------
# Build code helpers
# ---------------------------------------------------------------------------

def decode_build_code(code: str) -> str:
    """PoB share code (url-safe base64 of zlib-deflated XML) -> XML text."""
    s = re.sub(r"\s+", "", code)
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return zlib.decompress(base64.b64decode(s)).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise PobError(f"Not a valid Path of Building build code: {exc}") from exc


def encode_build_code(xml: str) -> str:
    data = zlib.compress(xml.encode("utf-8"), 9)
    return base64.b64encode(data).decode("ascii").replace("+", "-").replace("/", "_")


_RAW_URLS = [
    (re.compile(r"^https?://(?:www\.)?pobb\.in/(?:u/[^/]+/)?([\w-]+)/?$"), "https://pobb.in/{}/raw"),
    (re.compile(r"^https?://(?:www\.)?pastebin\.com/(?:raw/)?(\w+)/?$"), "https://pastebin.com/raw/{}"),
    (re.compile(r"^https?://(?:www\.)?poe\.ninja/(?:poe1/)?pob/(?:raw/)?(\w+)/?$"), "https://poe.ninja/pob/raw/{}"),
    (re.compile(r"^https?://(?:www\.)?poedb\.tw/pob/(\w+)(?:/raw)?/?$"), "https://poedb.tw/pob/{}/raw"),
]


def fetch_build_url(url: str, timeout: float = 20) -> str:
    raw = None
    for pat, tmpl in _RAW_URLS:
        m = pat.match(url.strip())
        if m:
            raw = tmpl.format(m.group(1))
            break
    if raw is None:
        raw = url.strip()
    req = urllib.request.Request(raw, headers={"User-Agent": "pob-mcp/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read().decode("utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        raise PobError(f"Could not download build from {raw}: {exc}") from exc


def resolve_build_input(source: str) -> str:
    """Accepts XML text, a share code, a build-site URL, or a local file path."""
    s = source.strip()
    if s.startswith("<"):
        return s
    if re.match(r"^https?://", s):
        s = fetch_build_url(s)
        if s.startswith("<"):
            return s
        return decode_build_code(s)
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        p = _ORIG_CWD / p
    if len(s) < 1024 and p.suffix.lower() in {".xml", ".txt"} and p.is_file():
        text = p.read_text(encoding="utf-8").strip()
        return text if text.startswith("<") else decode_build_code(text)
    return decode_build_code(s)


def _round(v):
    if isinstance(v, float):
        return round(v, 2) if abs(v) < 1e15 else v
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_round(x) for x in v]
    return v


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def find_pob_src(explicit: str | None = None) -> Path:
    """Locate the `src` folder of a PathOfBuilding git checkout."""
    candidates = []
    if explicit:
        candidates.append(Path(os.path.expanduser(explicit)))
    if os.environ.get("POB_PATH"):
        candidates.append(Path(os.path.expanduser(os.environ["POB_PATH"])))
    candidates += [Path.home() / "PathOfBuilding", Path.home() / "src" / "PathOfBuilding"]
    installed_copy = None
    for c in candidates:
        for d in (c / "src", c):
            if (d / "_SimpleGraphic.def.lua").is_file() and (d / "Launch.lua").is_file():
                return d.resolve()
            if (d / "Launch.lua").is_file():
                installed_copy = d
    hint = ""
    if installed_copy:
        hint = (f" Found {installed_copy}, but that looks like an installed copy of PoB, which lacks the "
                "headless support files.")
    raise PobError(
        "Could not find a Path of Building source checkout." + hint + " Clone it with "
        "`git clone https://github.com/PathOfBuildingCommunity/PathOfBuilding` and set POB_PATH "
        "(or --pob-path) to that folder."
    )


class PobEngine:
    def __init__(self, pob_path: str | None = None):
        self._pob_path = pob_path
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pob-lua")
        self._lua = None
        self._call = None
        self._boot_lock = threading.Lock()
        self.loaded = False
        self.src: Path | None = None

    # -- runs on the Lua thread -------------------------------------------------
    def _boot(self):
        from lupa import luajit21 as lupa_jit  # bundled LuaJIT 2.1

        self.src = find_pob_src(self._pob_path)
        log.info("Booting Path of Building from %s", self.src)
        os.chdir(self.src)
        lua = lupa_jit.LuaRuntime(unpack_returned_tuples=True, encoding=None)

        def inflate(data):
            if isinstance(data, str):
                data = data.encode("latin-1")
            return zlib.decompress(bytes(data), zlib.MAX_WBITS | 32)

        def deflate(data):
            if isinstance(data, str):
                data = data.encode("latin-1")
            return zlib.compress(bytes(data), 9)

        loader = lua.eval("function(src, name) return assert(loadstring(src, name)) end")
        fn = loader(BRIDGE.read_bytes(), b"@bridge.lua")
        fn(inflate, deflate)
        self._call = lua.globals()[b"pob_call"]
        self._lua = lua

    def _invoke(self, method: str, params: dict | None):
        if self._lua is None:
            self._boot()
        raw = self._call(method.encode(), json.dumps(params or {}).encode("utf-8"))
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        res = json.loads(raw)
        if not res.get("ok"):
            log.debug("Lua traceback: %s", res.get("traceback"))
            raise PobError(res.get("error") or "unknown error")
        return _round(res.get("result"))

    # -- public, thread-safe ------------------------------------------------------
    def call(self, method: str, params: dict | None = None):
        return self._pool.submit(self._invoke, method, params).result()

    def warm_up(self):
        return self._pool.submit(lambda: self._lua or self._boot())

    def load_build(self, source: str, name: str | None = None) -> dict:
        xml = resolve_build_input(source)
        res = self.call("load_xml", {"xml": xml, "name": name or "Imported build"})
        self.loaded = True
        return res

    def require_build(self):
        if not self.loaded:
            raise PobError("No build loaded. Call load_build (or new_build) first.")
