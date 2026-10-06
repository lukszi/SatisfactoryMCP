"""``python -m mapgen renders`` refuses a folder holding tiles of a type the registry lists.

The ``in_use_local`` tree holds the default map in ``renders-v4``, its manifest written by the
server's own registry. A run that gets past the guard stops at ``require_gen``, before
anything reads the game or writes a tile.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mapgen.tiles.inuse import IN_USE, MANIFEST, OVERRIDE, held_types, in_use_refusal
from satisfactory_mcp.domain.maps import presets, registry
from tests.support.map_jobs import (
    PNG_MAGIC,
    Passed,
    ident_of,
    render_meta,
    run_renders,
    write_pyramid,
)


def link(kind: str, target: Path, at: Path) -> None:
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("directory junctions are a Windows feature")
        import _winapi

        _winapi.CreateJunction(str(target), str(at))
        return
    try:
        at.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this account cannot make a directory symlink here: {exc}")


def test_the_reader_finds_the_manifest_the_registry_writes(in_use_local):
    assert in_use_local / MANIFEST == registry.manifest_path()


def test_a_render_into_the_folder_of_a_registered_type_is_refused(
    in_use_local, monkeypatch, capsys
):
    painted = ident_of("renders-v4/painted")
    assert set(held_types(in_use_local, in_use_local / "renders-v4")) == {
        painted,
        ident_of("renders-v4/terrain"),
    }
    assert run_renders(monkeypatch, "--renders-name", "renders-v4") == IN_USE
    out = capsys.readouterr().out
    assert f"{painted} (the default)" in out
    assert "--renders-name <new>" in out and OVERRIDE in out
    assert (
        in_use_local / "renders-v4" / "painted" / "tiles" / "0" / "0_0.png"
    ).read_bytes() == PNG_MAGIC


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_the_refusal_follows_a_link_to_the_folder(in_use_local, monkeypatch, capsys, kind):
    link(kind, in_use_local / "renders-v4", in_use_local / "renders")
    assert run_renders(monkeypatch) == IN_USE
    out = capsys.readouterr().out
    assert f"{ident_of('renders-v4/painted')} (the default)" in out
    assert "renders-v4" in out, "the message says where the link leads"


def test_force_is_not_the_override(in_use_local, monkeypatch):
    assert run_renders(monkeypatch, "--renders-name", "renders-v4", "--force") == IN_USE


def test_the_override_writes_into_a_registered_folder_anyway(in_use_local, monkeypatch):
    assert in_use_refusal(in_use_local, in_use_local / "renders-v4", overwrite=True) is None
    with pytest.raises(Passed):
        run_renders(monkeypatch, "--renders-name", "renders-v4", OVERRIDE)


def test_a_new_or_unregistered_folder_passes(in_use_local, monkeypatch):
    write_pyramid(
        in_use_local / "renders-scratch" / "terrain", "meta.json", render_meta("terrain", 6, 5)
    )
    for name in ("renders-v5", "renders-scratch"):
        with pytest.raises(Passed):
            run_renders(monkeypatch, "--renders-name", name)


def test_without_a_manifest_the_guard_passes(in_use_local, monkeypatch, tmp_path):
    registry.manifest_path().unlink()
    assert held_types(in_use_local, in_use_local / "renders-v4") == []
    with pytest.raises(Passed):
        run_renders(monkeypatch, "--renders-name", "renders-v4")
    fresh = tmp_path / "fresh-clone" / "data" / "local"
    assert held_types(fresh, fresh / "renders") == []


@pytest.mark.parametrize("flow", ["new", "re-render", "restyle"])
def test_every_runner_flow_passes_the_guard(in_use_local, monkeypatch, runner, flow):
    painted = ident_of("renders-v4/painted")
    options: dict = {"layers": ["painted"], "size": 1024}
    if flow == "restyle":
        options["restyle"] = True
        for part in presets.CACHE_PARTS:
            (presets.cache_dir(1024) / part).mkdir(parents=True)
            (presets.cache_dir(1024) / part / "meta.json").write_text("{}", encoding="utf-8")
    job = runner.submit("render", options, None, None if flow == "new" else painted)
    assert job["command"] == "renders" and OVERRIDE not in job["argv"]
    assert registry.read()["types"][job["produces"][0]]["status"] == "building"
    with pytest.raises(Passed):
        run_renders(monkeypatch, *job["argv"])
    assert in_use_refusal(in_use_local, in_use_local / "renders-v4", overwrite=False), (
        "still guarded"
    )
