"""The map job runner, against a fake generator: queue, progress, cancel, and re-adoption.

The fake is a ``mapgen`` package written into a scratch ``tools/`` (the runner starts
``python -m mapgen renders``, and the shim path runs the same file) that prints the real generator's
progress lines and writes a one-tile pyramid, so the runner, the registry and the event
stream are exercised end to end in a second or two per job. Nothing touches the game.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.maps import jobs as store
from satisfactory_mcp.domain.maps import presets, registry
from satisfactory_mcp.interfaces.web import childproc
from satisfactory_mcp.interfaces.web.mapjobs import MapJobRunner
from satisfactory_mcp.interfaces.web.watch import KIND_MAPS, SaveWatcher

FAKE = r"""
import argparse, json, os, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("command", nargs="?")
p.add_argument("--game"); p.add_argument("--field"); p.add_argument("--out-dir")
p.add_argument("--renders-name"); p.add_argument("--size", type=int)
p.add_argument("--layer", action="append"); p.add_argument("--kernel-only", action="store_true")
p.add_argument("--no-top", action="store_true"); p.add_argument("--cache-dir")
p.add_argument("--keep-direct", action="store_true")
a = p.parse_args()
pause = float(os.environ.get("FAKE_PAUSE", "0.05"))
print("field: 7500x7500 at 1 m, build buildVersion 502094 (x), the installed build", flush=True)
for i in range(0, 101, 25):
    print(f"  direct.cache: {i:4.1f}% of {a.size}x{a.size} at 0.2289 m, 1 M texels, 1s", flush=True)
    time.sleep(pause)
for layer in a.layer or ["terrain", "satellite"]:
    print(f"drawing {layer} at {a.size}x{a.size}", flush=True)
    for i in (13.3, 50.8, 88.3):
        print(f"  {layer}: {i:4.1f}% of {a.size}x{a.size} in 1.0s", flush=True)
        time.sleep(pause)
    if os.environ.get("FAKE_FAIL"):
        print("something broke in the cutter", flush=True)
        sys.exit(3)
    out = Path(a.out_dir) / a.renders_name / layer
    (out / "tiles" / "0").mkdir(parents=True, exist_ok=True)
    (out / "tiles" / "0" / "0_0.png").write_bytes(b"png")
    meta = {"_meta": {"generator": "tools/gen_map_renders.py", "layer": layer,
            "tiles": {"bytes": 3, "max_z": 2},
            "provenance": {"schema": 1, "game": {"cl": 502094},
                "inputs": {"heightfield": {"cl": 502094, "generator_version": 5, "planes": [],
                                           "digest": "sha256:hf"}},
                "renderer": {"family": "render", "recipe": 5, "version": 1, "label": "PCHIP",
                             "size_px": a.size},
                "style": {"id": "terrain-hypsometric", "version": 1, "label": layer}}}}
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    for z in range(3):
        print(f"  pyramid z{z}: 256x256, 1 tiles, 0.10 MB", flush=True)
    print(f"wrote {out}  1 tiles over z0..z2 (0.0 MB)", flush=True)
print("done in 1s", flush=True)
"""


def install_fake(tools: Path) -> None:
    """The fake as the shim path and as the ``mapgen`` package the runner starts."""
    (tools / "gen_map_renders.py").write_text(FAKE, encoding="utf-8")
    package = tools / "mapgen" / "src" / "mapgen"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(FAKE, encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    (tmp_path / "data" / "local" / "heightmap").mkdir(parents=True)
    (tmp_path / "data" / "local" / "heightmap" / "meta.json").write_text(
        json.dumps({"generator_version": 5}), encoding="utf-8"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    install_fake(tools)
    monkeypatch.setattr(presets, "tools_dir", lambda: tools)
    monkeypatch.setattr(config, "game_root", lambda: tmp_path / "game")
    monkeypatch.setattr(registry, "game_cl", lambda: 502094)
    monkeypatch.setattr(
        presets,
        "can_generate",
        lambda: {"gen": True, "tools": True, "game": True, "heightfield": True, "ok": True,
                 "reason": None},
    )  # fmt: skip
    monkeypatch.setenv("FAKE_PAUSE", "0.02")
    return tmp_path


async def _until(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.05)


def _run(coro):
    return asyncio.run(coro)


PREVIEW = {"layers": ["terrain"], "size": 1024}


def test_a_job_runs_reports_progress_and_registers_what_it_wrote(env):
    async def main():
        watcher = SaveWatcher(root=env, notes=())
        events = watcher.subscribe()
        runner = MapJobRunner(watcher)
        await runner.start()
        job = runner.submit("render", PREVIEW, "a preview", None)
        assert job["status"] == "queued" and job["produces"] == ["terrain-r6-502094"]
        assert registry.read()["types"]["terrain-r6-502094"]["status"] == "building"
        assert registry.directory("terrain-r6-502094") is None
        await _until(lambda: job["status"] not in ("queued", "running"))
        await runner.stop()
        seen = []
        while not events.empty():
            event = events.get_nowait()
            assert event.kind == KIND_MAPS
            seen.append(event.data["job"]["status"] if event.data["job"] else None)
        return job, seen, watcher

    job, seen, watcher = _run(main())
    assert job["status"] == "done", job
    assert job["pct"] == 1.0 and job["exit_code"] == 0
    assert seen[0] == "running" and seen[-1] == "done"
    assert watcher.latest[KIND_MAPS].data["job"]["status"] == "done"
    entry = registry.read()["types"]["terrain-r6-502094"]
    assert entry["status"] == "ready" and entry["label"] == "a preview"
    assert entry["dir"] == f"maps/{job['id']}/terrain"
    assert registry.directory("terrain-r6-502094") is not None
    assert registry.read()["history"][-1]["preset"] == "render"
    on_disk = json.loads((store.jobs_dir() / f"{job['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "done"
    assert any("drawing terrain" in line for line in store.log_tail(job["id"]))


def test_one_runs_at_a_time_and_the_queue_holds_four(env, monkeypatch):
    monkeypatch.setenv("FAKE_PAUSE", "0.3")

    async def main():
        runner = MapJobRunner(SaveWatcher(root=env, notes=()))
        await runner.start()
        first = runner.submit("render", PREVIEW, None, None)
        await _until(lambda: first["status"] == "running")
        queued = [runner.submit("render", PREVIEW, None, None) for _ in range(4)]
        with pytest.raises(presets.QueueFull):
            runner.submit("render", PREVIEW, None, None)
        assert sum(j["status"] == "running" for j in runner.jobs.values()) == 1
        ids = [i for j in queued for i in j["produces"]]
        assert len(set(ids)) == 4, "every queued job reserves an id of its own"
        removed = await runner.cancel(queued[-1]["id"])
        assert removed["status"] == "cancelled"
        assert queued[-1]["produces"][0] not in registry.read()["types"]
        await runner.cancel(first["id"])
        await _until(lambda: first["status"] == "cancelled")
        for job in queued[:-1]:
            await runner.cancel(job["id"])
        await _until(lambda: runner.run is None)
        await runner.stop()
        return first

    first = _run(main())
    assert first["status"] == "cancelled"
    assert first["produces"][0] not in registry.read()["types"]
    assert not (registry.maps_dir() / first["id"] / "terrain").exists()


def test_a_failing_generator_leaves_a_failed_job_and_no_type(env, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "1")

    async def main():
        runner = MapJobRunner(SaveWatcher(root=env, notes=()))
        await runner.start()
        job = runner.submit("render", PREVIEW, None, None)
        await _until(lambda: job["status"] not in ("queued", "running"))
        await runner.stop()
        return job

    job = _run(main())
    assert job["status"] == "failed" and job["exit_code"] == 3
    assert job["error_line"] == "something broke in the cutter"
    assert "terrain-r6-502094" not in registry.read()["types"]


def test_a_restarted_server_re_adopts_a_running_child_and_interrupts_a_dead_one(env, monkeypatch):
    monkeypatch.setenv("FAKE_PAUSE", "0.15")
    plan = presets.plan("render", PREVIEW, "j-old", 502094, registry.taken_ids())
    registry.register(plan["produces"])
    log = store.log_path("j-old")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as handle:
        child = childproc.launch(
            [sys.executable, "-u", str(presets.tools_dir() / "gen_map_renders.py"), *plan["argv"]],
            handle,
            str(env),
            dict(__import__("os").environ),
        )
    alive = {
        "id": "j-old", "preset": "render", "options": plan["options"], "label": None,
        "produces": list(plan["produces"]), "replaces": None, "status": "running",
        "created": time.time(), "started": time.time(), "pid": child.pid,
        "pid_created": child.created,
    }  # fmt: skip
    store.save(alive)
    dead = {**alive, "id": "j-dead", "pid": 4, "pid_created": 1, "produces": []}
    store.save(dead)

    async def main():
        watcher = SaveWatcher(root=env, notes=())
        runner = MapJobRunner(watcher, recover=True)
        await runner.start()
        assert runner.run is not None and runner.run.job["id"] == "j-old"
        assert watcher.latest[KIND_MAPS].data["job"]["id"] == "j-old"
        await _until(lambda: runner.jobs["j-old"]["status"] == "done")
        await runner.stop()
        return runner

    runner = _run(main())
    child.popen.wait(timeout=10)
    assert runner.jobs["j-dead"]["status"] == "interrupted"
    assert registry.read()["types"]["terrain-r6-502094"]["status"] == "ready"


def test_every_preset_writes_only_under_data_local(env):
    local = registry.local_dir().resolve()
    for preset, options in (
        ("render", {"layers": ["terrain", "satellite"], "size": 32768, "keep_cache": True}),
        ("artwork", {"enhance": True}),
        ("heightmap", {}),
        ("caves", {}),
        ("rocks", {}),
    ):
        argv = presets.plan(preset, options, "j1", 502094, set())["argv"]
        paths = [argv[i + 1] for i, a in enumerate(argv) if a.startswith("--") and i + 1 < len(argv)
                 and a in ("--out-dir", "--field", "--caves-dir", "--cache-dir")]  # fmt: skip
        assert paths, preset
        for path in paths:
            assert Path(path).resolve().is_relative_to(local), (preset, path)
        game = argv[argv.index("--game") + 1]
        assert Path(game) == config.game_root()
        assert "--force" not in argv or preset in presets.INPUT_PRESETS


def test_progress_reads_a_recorded_full_render_log():
    lines = (Path(__file__).parent / "fixtures" / "map_render_full.log").read_text(encoding="utf-8")
    options = presets.normalise("render", {"size": 32768})
    progress = store.Progress(presets.stage_plan("render", options), 32768)
    seen = []
    for line in lines.splitlines():
        progress.feed(line)
        seen.append((progress.stage, round(progress.pct() or 0, 3)))
    stages = [stage for stage, _ in seen]
    for wanted in ("sweep", "direct", "top", "draw:terrain", "cut:terrain", "draw:satellite",
                   "cut:satellite"):  # fmt: skip
        assert wanted in stages, wanted
    pcts = [pct for _, pct in seen]
    assert pcts == sorted(pcts), "progress never runs backwards"
    assert progress.finished and progress.pct() == 1.0
    halfway = dict(seen)["draw:terrain"]
    assert 0.4 < halfway < 0.8
    assert progress.eta(100.0) is None


def test_stage_lines_drive_progress_where_the_regexes_cannot():
    """``painted`` has no human-line pattern; its ``::stage`` lines carry it draw to cut."""
    from satisfactory_mcp.core import mapprogress

    progress = store.Progress({"prep": 1.0, "draw:painted": 1.0, "cut:painted": 1.0}, size=1024)
    progress.feed(mapprogress.encode_stage("draw:painted", 0.0))
    progress.feed(mapprogress.encode_stage("draw:painted", 0.5))
    assert (progress.stage, progress.fraction) == ("draw:painted", 0.5)
    assert progress.stage_words() == "drawing painted"
    progress.feed("  pyramid z0: 256x256, 1 tiles, 0.10 MB")
    assert progress.stage == "cut:painted"
    progress.feed(mapprogress.encode_stage("cut:painted", 1.0))
    assert progress.pct() == 1.0
    assert mapprogress.decode("::stage {not json") is None
    assert mapprogress.decode(mapprogress.encode_plan([("sweep", 36)])).steps == (("sweep", 36.0),)
