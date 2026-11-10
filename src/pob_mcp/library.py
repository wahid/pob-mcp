"""PoB's Builds folder: listing saved builds and watching for changes made in the PoB app."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from .engine import PobError

_HEADER = re.compile(r"<Build\b([^>]*)>", re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def default_builds_dir() -> Path:
    if os.environ.get("POB_BUILDS_DIR"):
        return Path(os.path.expanduser(os.environ["POB_BUILDS_DIR"]))
    home = Path.home()
    candidates = [home / "Documents" / "Path of Building" / "Builds",
                  home / "OneDrive" / "Documents" / "Path of Building" / "Builds"]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _header(path: Path) -> dict:
    """Class, ascendancy and level from the <Build> tag, without loading the build."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return {}
    m = _HEADER.search(head)
    if not m:
        return {}
    attrs = dict(_ATTR.findall(m.group(1)))
    return {"className": attrs.get("className"), "ascendancy": attrs.get("ascendClassName"),
            "level": int(attrs["level"]) if attrs.get("level", "").isdigit() else None}


def list_builds(folder: Path, query: str | None = None) -> list[dict]:
    if not folder.is_dir():
        raise PobError(f"Builds folder not found: {folder} (set POB_BUILDS_DIR or pass folder)")
    out = []
    q = query.lower() if query else None
    for p in sorted(folder.rglob("*.xml")):
        rel = p.relative_to(folder).as_posix()
        if q and q not in rel.lower():
            continue
        st = p.stat()
        out.append({"name": p.stem, "path": rel, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                    **_header(p)})
    return out


def find_build(folder: Path, name: str) -> Path:
    direct = Path(os.path.expanduser(name))
    if direct.is_absolute() and direct.is_file():
        return direct
    want = name.lower().removesuffix(".xml")
    matches = [p for p in folder.rglob("*.xml")
               if p.stem.lower() == want or p.relative_to(folder).as_posix().lower().removesuffix(".xml") == want]
    if not matches:
        matches = [p for p in folder.rglob("*.xml") if want in p.stem.lower()]
    if not matches:
        raise PobError(f"No saved build matching '{name}' in {folder}")
    if len(matches) > 1:
        names = ", ".join(p.relative_to(folder).as_posix() for p in matches[:10])
        raise PobError(f"'{name}' matches several builds: {names}. Use the path.")
    return matches[0]


class Watcher:
    """Polls the Builds folder and records created, modified and deleted builds."""

    def __init__(self):
        self.folder: Path | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mtimes: dict[str, float] = {}
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self.own_writes: dict[str, float] = {}  # path -> mtime of the version we wrote ourselves

    def _scan(self) -> dict[str, float]:
        res = {}
        for p in self.folder.rglob("*.xml"):
            try:
                res[str(p)] = p.stat().st_mtime
            except OSError:
                pass
        return res

    def start(self, folder: Path, interval: float = 2.0):
        self.stop()
        if not folder.is_dir():
            raise PobError(f"Builds folder not found: {folder}")
        self.folder = folder
        self._mtimes = self._scan()
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval):
                try:
                    now = self._scan()
                except OSError:
                    continue
                with self._lock:
                    for path, mt in now.items():
                        if self.own_writes.get(path) == mt:
                            continue  # the version we saved ourselves
                        if path not in self._mtimes:
                            self._record(path, "created")
                        elif mt != self._mtimes[path]:
                            self._record(path, "modified")
                    for path in self._mtimes.keys() - now.keys():
                        self._record(path, "deleted")
                    self._mtimes = now

        self._thread = threading.Thread(target=loop, name="pob-builds-watch", daemon=True)
        self._thread.start()

    def note_own_write(self, path: Path):
        try:
            self.own_writes[str(path)] = path.stat().st_mtime
        except OSError:
            pass

    def _record(self, path: str, kind: str):
        rel = Path(path).relative_to(self.folder).as_posix() if self.folder else path
        self._events.append({"path": rel, "fullPath": path, "change": kind,
                             "time": time.strftime("%H:%M:%S")})
        del self._events[:-200]

    def stop(self):
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def take_events(self) -> list[dict]:
        with self._lock:
            ev, self._events = self._events, []
        return ev


watcher = Watcher()
