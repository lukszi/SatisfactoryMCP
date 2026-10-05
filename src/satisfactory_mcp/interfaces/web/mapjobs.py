"""The map generation runner: one job at a time, a queue of four, progress over the event stream.

One asyncio task owns at most one generator process. Its output goes to a log FILE, never a
pipe, so a chatty child cannot block and a restarted server can re-adopt a child still
running: the job file records its pid and creation time. Every state change and at most one
progress update per two seconds is published as a ``maps`` event. docs/maps_contract.md §5.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

from ... import config
from ...domain.maps import jobs as store
from ...domain.maps import presets, registry
from .childproc import Child, kill_tree, launch
from .watch import KIND_MAPS, WatchEvent

__all__ = ["QUEUE_MAX", "MapJobRunner"]

log = logging.getLogger(__name__)

QUEUE_MAX = 4
TICK_S = 0.5
PUBLISH_EVERY_S = 2.0


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    paths = [str(presets.mapgen_src())]
    root = config.source_root()
    if root is not None:
        paths.insert(0, str(root))
    if inherited := env.get("PYTHONPATH"):
        paths.append(inherited)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class _Run:
    """The job being run now, and what has been read of its log."""

    def __init__(self, job: dict, child: Child) -> None:
        self.job = job
        self.child = child
        self.offset = 0
        self.partial = b""
        self.cancelled = False
        stages = presets.stage_plan(job["preset"], job["options"])
        self.progress = store.Progress(stages, job["options"].get("size"))
        self.published = 0.0
        self.stage = ""


class MapJobRunner:
    """Queue, run, watch and report map generation jobs for one web process."""

    def __init__(self, watcher, recover: bool = False, python: str | None = None) -> None:
        self.watcher = watcher
        self.recover = recover
        self.python = python or sys.executable
        self.jobs: dict[str, dict] = {}
        self.run: _Run | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    # ---- views ------------------------------------------------------------

    def queued(self) -> list[dict]:
        return [j for j in self.jobs.values() if j["status"] == "queued"]

    def view(self, job: dict) -> dict:
        now = time.time()
        started, ended = job.get("started"), job.get("ended")
        elapsed = ((ended or now) - started) if started else None
        return {**job, "elapsed_s": round(elapsed, 1) if elapsed is not None else None}

    def snapshot(self) -> list[dict]:
        """Running and queued jobs, then the last ten finished ones, newest first."""
        active = [self.view(j) for j in self.jobs.values() if j["status"] in store.ACTIVE]
        active.sort(key=lambda j: (j["status"] != "running", j.get("created") or 0))
        final = [j for j in self.jobs.values() if j["status"] in store.FINAL]
        final.sort(key=lambda j: j.get("ended") or j.get("created") or 0, reverse=True)
        return active + [self.view(j) for j in final[: store.RECENT_KEEP]]

    def busy_ids(self) -> frozenset[str]:
        """Types a queued or running job writes or replaces: not to be deleted under it."""
        held: set[str] = set()
        for job in self.jobs.values():
            if job["status"] in store.ACTIVE:
                held.update(job.get("produces") or [])
                if job.get("replaces"):
                    held.add(job["replaces"])
        return frozenset(held)

    def announce(self, job: dict | None = None) -> None:
        """Tell every page: this job moved, or the registry did."""
        data = {
            "job": self.view(job) if job else None,
            "queued": [j["id"] for j in self.queued()],
            "registry_version": registry.read()["version"],
        }
        ident = job["id"] if job else "registry"
        self.watcher.publish(WatchEvent(KIND_MAPS, ident, time.time(), data))

    # ---- requests ---------------------------------------------------------

    def submit(self, preset: str, options: dict, label: str | None, replaces: str | None) -> dict:
        """Queue a job; a ``PresetError`` (``QueueFull``, ``DiskShort``) says why not."""
        if len(self.queued()) >= QUEUE_MAX:
            raise presets.QueueFull(
                f"the queue holds {QUEUE_MAX} jobs already; wait for one to finish"
            )
        checked = presets.can_generate()
        if not checked["ok"]:
            raise presets.PresetError(checked["reason"])
        options = presets.normalise(preset, options)
        if preset == "render" and not checked["heightfield"]:
            raise presets.PresetError("render maps need the heightfield first")
        if replaces is not None and replaces not in registry.read()["types"]:
            raise registry.MapsUnknown(f"no map type “{replaces}” to replace")
        cost = presets.estimate(preset, options)
        if not cost["ok"]:
            raise presets.DiskShort(cost["reason"])
        ident = store.new_id()
        plan = presets.plan(preset, options, ident, registry.game_cl(), registry.taken_ids())
        if label:
            for entry in plan["produces"].values():
                entry["label"] = label.strip()[:80] or None
        for entry in plan["produces"].values():
            entry["replaces"] = replaces
        registry.register(plan["produces"])
        job = {
            "id": ident,
            "preset": preset,
            "options": plan["options"],
            "label": label,
            "script": plan["script"],
            "command": plan["command"],
            "argv": plan["argv"],
            "produces": list(plan["produces"]),
            "replaces": replaces,
            "status": "queued",
            "created": time.time(),
            "started": None,
            "ended": None,
            "pid": None,
            "pid_created": None,
            "exit_code": None,
            "stage": "",
            "stage_words": "queued",
            "pct": None,
            "eta_s": None,
            "peak_rss": None,
            "error_line": None,
            "last_line": None,
            "estimate_s": cost["seconds"],
        }
        store.save(job)
        self.jobs[ident] = job
        if self._wake is not None:
            self._wake.set()
        return job

    async def cancel(self, ident: str) -> dict:
        """Cancel the running job (its process tree is killed) or take a queued one off."""
        job = self.jobs.get(ident)
        if job is None:
            raise KeyError(ident)
        if job["status"] == "queued":
            await asyncio.to_thread(self._finish, job, "cancelled", None)
            self.announce(job)
        elif job["status"] == "running" and self.run is not None and self.run.job is job:
            self.run.cancelled = True
            await asyncio.to_thread(kill_tree, job["pid"])
        return job

    # ---- the loop ---------------------------------------------------------

    async def start(self) -> None:
        self._wake = asyncio.Event()
        if self.recover:
            await asyncio.to_thread(self._recover)
            if self.run is not None:
                self.announce(self.run.job)
        self._task = asyncio.create_task(self._loop(), name="map-jobs")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.run is not None:
            self.run.child.close()

    def _recover(self) -> None:
        """Re-adopt a generator a previous server left running; mark the rest interrupted."""
        registry.ensure()
        registry.purge_trash()
        for job in store.load_all():
            self.jobs[job["id"]] = job
            if job["status"] != "running":
                continue
            child = Child.adopt(int(job.get("pid") or 0), job.get("pid_created"))
            if child is not None and self.run is None:
                self.run = _Run(job, child)
                log.info("re-adopted map job %s (pid %s)", job["id"], job["pid"])
            else:
                self._finish(job, "interrupted", None)

    async def _loop(self) -> None:
        assert self._wake is not None
        while True:
            try:
                if self.run is not None:
                    await self._watch(self.run)
                    continue
                job = next(iter(self.queued()), None)
                if job is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                    except TimeoutError:
                        pass
                    continue
                await self._launch(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("map job runner failed a step; carrying on", exc_info=True)
                await asyncio.sleep(1.0)

    async def _launch(self, job: dict) -> None:
        cost = await asyncio.to_thread(presets.estimate, job["preset"], job["options"])
        if not cost["ok"]:
            job["error_line"] = cost["reason"]
            await asyncio.to_thread(self._finish, job, "failed", None)
            self.announce(job)
            return
        child = await asyncio.to_thread(self._spawn, job)
        job.update(status="running", started=time.time(), pid=child.pid, pid_created=child.created)
        job["stage_words"] = "starting"
        await asyncio.to_thread(store.save, job)
        self.run = _Run(job, child)
        self.announce(job)

    def _spawn(self, job: dict) -> Child:
        name = job.get("command") or presets.COMMANDS[job["preset"]]
        command = [self.python, "-u", "-m", "mapgen", name, *job["argv"]]
        path = store.log_path(job["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            return launch(command, handle, str(config.REPO_ROOT), _child_env())

    def _read_log(self, run: _Run) -> bool:
        """Feed every complete new line to the progress; True when anything was read."""
        try:
            with open(store.log_path(run.job["id"]), "rb") as handle:
                handle.seek(run.offset)
                chunk = handle.read()
        except OSError:
            return False
        if not chunk:
            return False
        run.offset += len(chunk)
        lines = (run.partial + chunk).split(b"\n")
        run.partial = lines.pop()
        for raw in lines:
            line = raw.decode("utf-8", "replace").rstrip("\r")
            run.progress.feed(line)
            if line.strip():
                run.job["last_line"] = line.strip()[:300]
        return True

    async def _watch(self, run: _Run) -> None:
        job = run.job
        while True:
            await asyncio.to_thread(self._read_log, run)
            started = job.get("started") or time.time()
            job["stage"] = run.progress.stage
            job["stage_words"] = run.progress.stage_words()
            pct = run.progress.pct()
            job["pct"] = round(pct, 4) if pct is not None else None
            eta = run.progress.eta(time.time() - started)
            job["eta_s"] = round(eta) if eta is not None else None
            rss = run.child.peak_rss()
            if rss:
                job["peak_rss"] = max(rss, job.get("peak_rss") or 0)
            code = run.child.exit_code()
            if code is not None:
                if run.partial:
                    run.progress.feed(run.partial.decode("utf-8", "replace"))
                run.child.close()
                self.run = None
                if run.cancelled:
                    status = "cancelled"
                elif code == 0:
                    status = "done"
                else:
                    status = "failed"
                await asyncio.to_thread(self._finish, job, status, code)
                self.announce(job)
                return
            now = time.monotonic()
            if job["stage"] != run.stage or now - run.published >= PUBLISH_EVERY_S:
                run.stage, run.published = job["stage"], now
                self.announce(job)
            await asyncio.sleep(TICK_S)

    def _finish(self, job: dict, status: str, code: int | None) -> None:
        """Record how a job ended and register or discard what it wrote; blocking, unannounced."""
        job["exit_code"] = code
        job["ended"] = time.time()
        if status == "done":
            missing = [i for i in job.get("produces") or [] if not registry.finish(i, job["id"])]
            if missing:
                status = "failed"
                job["error_line"] = f"exited 0 but wrote no pyramid for {', '.join(missing)}"
            else:
                job["pct"] = 1.0
                registry.record_history(
                    {
                        "job": job["id"],
                        "preset": job["preset"],
                        "options": job["options"],
                        "seconds": round(job["ended"] - (job.get("started") or job["ended"])),
                        "peak_rss": job.get("peak_rss"),
                        "ended": job["ended"],
                    }
                )
        if status != "done":
            for ident in job.get("produces") or []:
                registry.discard(ident)
            if status == "failed" and not job.get("error_line"):
                job["error_line"] = job.get("last_line") or f"exit code {code}"
        job["status"] = status
        job["stage_words"] = status
        job["eta_s"] = None
        store.save(job)
