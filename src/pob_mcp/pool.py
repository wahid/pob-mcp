"""Extra Path of Building processes for work that splits cleanly.

One Lua state runs on one core. Measuring a passive tree is thousands of independent PoB
calculations, so ``tree_model`` can be dealt out over several processes, each with its own
copy of the build. The main engine always takes a share; a pool of N jobs starts N-1 helpers.
Helpers cost about 300 MB each and a few seconds to boot, and are kept for later calls.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import logging
import multiprocessing
import os
import threading

log = logging.getLogger("pob_mcp")


def default_jobs() -> int:
    env = os.environ.get("POB_MCP_JOBS")
    if env:
        try:
            return max(1, min(int(env), 16))
        except ValueError:
            pass
    return max(1, min(os.cpu_count() or 1, 4))


def _worker(conn, pob_path):
    # helpers must never write to the MCP stdio channel
    try:
        os.dup2(2, 1)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
    except OSError:
        pass
    from .engine import PobEngine

    engine = PobEngine(pob_path)
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        try:
            kind = msg[0]
            if kind == "sync":
                engine.call("load_xml", {"xml": msg[1], "name": "Build"})
                engine.loaded = True
                conn.send(("ok", None))
            else:
                conn.send(("ok", engine.call(msg[1], msg[2])))
        except Exception as exc:  # noqa: BLE001
            try:
                conn.send(("err", f"{type(exc).__name__}: {exc}"))
            except (OSError, ValueError):
                return


class _Helper:
    def __init__(self, ctx, pob_path):
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker, args=(child, pob_path), daemon=True)
        self.proc.start()
        child.close()

    def request(self, msg, timeout):
        self.conn.send(msg)
        if not self.conn.poll(timeout):
            raise TimeoutError("helper did not answer in time")
        status, value = self.conn.recv()
        if status != "ok":
            raise RuntimeError(value)
        return value

    def close(self):
        try:
            self.conn.send(None)
        except (OSError, ValueError):
            pass
        self.proc.join(timeout=2)
        if self.proc.is_alive():
            self.proc.kill()


class MeasurePool:
    """Splits ``tree_model`` between the main engine and helper processes."""

    def __init__(self, engine, jobs: int | None = None):
        self.engine = engine
        self.jobs = jobs if jobs is not None else default_jobs()
        self._helpers: list[_Helper] = []
        self._threads = None
        self._lock = threading.Lock()
        self.fallbacks = 0
        atexit.register(self.close)

    def _ensure(self, n):
        if len(self._helpers) < n:
            ctx = multiprocessing.get_context("spawn")
            pob_path = str(self.engine.src.parent) if self.engine.src else self.engine._pob_path
            while len(self._helpers) < n:
                self._helpers.append(_Helper(ctx, pob_path))
        if self._threads is None:
            self._threads = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="pob-pool")

    def close(self):
        helpers, self._helpers = self._helpers, []
        for h in helpers:
            h.close()

    def tree_model(self, params: dict, timeout: float = 600) -> dict:
        """Same result as engine.call("tree_model", params), measured on `jobs` processes."""
        n = self.jobs
        if n <= 1:
            return self.engine.call("tree_model", params)
        with self._lock:
            try:
                self._ensure(n - 1)
                xml = self.engine.call("export_xml")["xml"]

                def remote(i, helper):
                    helper.request(("sync", xml), timeout)
                    return helper.request(("call", "tree_model", {**params, "shard": [i, n]}), timeout)

                futures = [self._threads.submit(remote, i + 1, h) for i, h in enumerate(self._helpers[: n - 1])]
                parts = [self.engine.call("tree_model", {**params, "shard": [0, n]})]
                parts += [f.result() for f in futures]
                return _merge(parts)
            except Exception as exc:  # noqa: BLE001 - a broken helper must not break the run
                log.warning("Parallel measuring failed (%s); measuring on one process", exc)
                self.fallbacks += 1
                self.close()
                self.jobs = 1
                return self.engine.call("tree_model", params)


def _merge(parts: list[dict]) -> dict:
    out = dict(parts[0])
    ids = [r["id"] for r in parts[0]["nodes"]]
    for part in parts[1:]:
        if [r["id"] for r in part["nodes"]] != ids or part["stats"] != parts[0]["stats"]:
            raise RuntimeError("helper measured a different build")
    rows = []
    for k in range(len(ids)):
        row = next((p["nodes"][k] for p in parts if not p["nodes"][k].get("skip")), None)
        if row is None:
            raise RuntimeError(f"node {ids[k]} was not measured by any process")
        rows.append(dict(row))
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        same = r.pop("same", None)
        if same is not None and by_id[same].get("d") is not None:
            r["d"] = by_id[same]["d"]
    out["nodes"] = rows
    out["calcs"] = sum(p.get("calcs") or 0 for p in parts)
    return out
