"""Which build an artifact came from, and the write that cannot leave it half-said.

``core.gameassets.provenance`` is what four generators use to date what they cut and to
refuse to overwrite something they cannot show is theirs. None of it needs the game: the
version file is JSON, the executable scan is a byte search, the sidecar walk is dicts, and
the install is a rename -- so everything here runs on a synthetic install in ``tmp_path``,
which is also the only way to exercise the failures a machine with the game installed
never sees.

The two that would ship quietly if they broke, and are asserted hardest:

* the sidecar walk answers ``None`` for every shape of sidecar that is not the expected
  one, rather than raising -- a staleness guard that crashes on an old sidecar is a guard
  that stops people refreshing artifacts;
* ``install_directory`` leaves either the whole new directory or the whole old one, and
  never a staging directory for a reader to find.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satisfactory_mcp.core.gameassets import provenance as prov

#: What Steam has on disk for build 495413, trimmed to the keys the pin string reads.
VERSION_JSON = {
    "MajorVersion": 5,
    "MinorVersion": 6,
    "PatchVersion": 1,
    "Changelist": 495413,
    "BranchName": "++FactoryGame+rel-main-1.2.0",
    "BuildId": "495413",
    "GameVersion": "1.2.3.1",
}

#: The literal the executable scan looks for, as build 495413 spells it.
BRANCH = "++FactoryGame+rel-main-1.2.0-CL-495413"


def fake_install(root, version=VERSION_JSON, name="FactoryGameSteam-Win64-Shipping"):
    """An install directory with just the one file the pin is read out of."""
    binaries = root / "Engine" / "Binaries" / "Win64"
    binaries.mkdir(parents=True, exist_ok=True)
    (binaries / f"{name}.version").write_text(json.dumps(version), encoding="utf-8")
    return root


def fake_exe(root, blob, name="FactoryGameSteam-Win64-Shipping.exe"):
    """A "shipping executable" that is only its version resource, where one would be."""
    binaries = root / "FactoryGame" / "Binaries" / "Win64"
    binaries.mkdir(parents=True, exist_ok=True)
    (binaries / name).write_bytes(blob)
    return root


# --------------------------------------------------------------------------------------
# The installed build.
# --------------------------------------------------------------------------------------


def test_the_pin_string_is_the_shape_every_artifact_records(tmp_path):
    """One spelling of a build, because a pin is compared by equality and nothing else.

    ``installed_build`` exists so that a heightfield, a node table and a map sheet cut from
    the same install carry the same string byte for byte -- a guard that compares two
    spellings of build 495413 refuses to refresh anything, forever.
    """
    pin, raw = prov.installed_build(fake_install(tmp_path))
    assert pin == (
        "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    )
    assert raw == VERSION_JSON, "the caller needs the raw fields for the sidecar too"


def test_a_version_file_missing_a_field_still_pins_rather_than_crashing(tmp_path):
    """A future engine that renames a key must not take the generator down with it.

    The pin is then visibly wrong -- it says ``None`` -- which is the point: a run that
    stops is a run nobody can diagnose, and a pin nobody can match refuses the overwrite
    that would have replaced a good artifact with an undatable one.
    """
    pin, _raw = prov.installed_build(fake_install(tmp_path, version={"Changelist": 495413}))
    assert pin == "buildVersion 495413 (engine branch None), the installed build"


def test_the_wrong_directory_is_told_apart_from_a_broken_one(tmp_path):
    """``InstallNotFound``, not ``SystemExit`` and not a stray ``IndexError``.

    Pointing ``--game`` at the parent of the install, or at a Steam library, is the single
    most likely operator mistake there is, and the message has to name the glob that failed
    and the directory it failed under. The fix -- which flag to pass -- belongs to the
    generator, which is why it is not in here.
    """
    with pytest.raises(prov.InstallNotFound) as caught:
        prov.installed_build(tmp_path)
    message = str(caught.value)
    assert prov.VERSION_GLOB in message and str(tmp_path) in message
    assert "--game" not in message, "core states the fact; the tool states the fix"


def test_the_executable_scan_reads_the_branch_the_engine_wrote(tmp_path):
    """The other build string the game states, and the one collectibles' sidecar quotes.

    UTF-16 in a binary, so the test builds the surroundings a real resource has: bytes
    before it that must not be read, and the double NUL that ends it.
    """
    blob = b"\x00\x01padding" + BRANCH.encode("utf-16-le") + b"\x00\x00" + b"more junk"
    assert prov.installed_build_from_exe(fake_exe(tmp_path, blob)) == BRANCH


def test_an_unterminated_branch_string_is_cut_rather_than_run_on(tmp_path):
    """A resource with no double NUL after it must not return the rest of the file.

    200 bytes is 100 UTF-16 characters, which is far past any branch name, so the cap only
    ever fires on a binary that is not shaped the way this scan assumes -- and then it
    yields a visibly odd string rather than a megabyte of executable.
    """
    blob = BRANCH.encode("utf-16-le") + b"AB" * 4000
    got = prov.installed_build_from_exe(fake_exe(tmp_path, blob))
    assert got is not None
    assert got.startswith(BRANCH)
    assert len(got.encode("utf-16-le")) == prov.BRANCH_MAX_BYTES


def test_an_install_whose_executable_says_nothing_is_none_not_an_error(tmp_path):
    """``None`` is a sidecar field the caller writes as null; an exception is a dead run."""
    assert prov.installed_build_from_exe(tmp_path) is None
    assert prov.installed_build_from_exe(fake_exe(tmp_path, b"no version resource here")) is None


# --------------------------------------------------------------------------------------
# Reading back what an artifact already on disk says.
# --------------------------------------------------------------------------------------


def test_the_sidecar_walk_finds_the_build_it_is_pointed_at():
    """The three real paths, in the three shapes the generators actually record."""
    heightmap = {"sources": {"game": {"game_version_pinned": "buildVersion 495413, a test"}}}
    assert prov.read_str_path(heightmap, ("sources", "game", "game_version_pinned")) == (
        "buildVersion 495413, a test"
    )

    sheet = {"_meta": {"sources": {"map_slices": {"game_version_pinned": "b"}}}}
    assert prov.read_str_path(sheet, ("_meta", "sources", "map_slices", "game_version_pinned")) == (
        "b"
    )


@pytest.mark.parametrize(
    ("sidecar", "why"),
    [
        ({}, "an empty sidecar"),
        ({"sources": {}}, "a sidecar that stops halfway down the path"),
        ({"sources": {"game": None}}, "a null where a block was expected"),
        ({"sources": {"game": {"game_version_pinned": 495413}}}, "a build recorded as a number"),
        ({"sources": ["game"]}, "a list where a dict was expected"),
        ("not a sidecar at all", "a JSON file that is not an object"),
        (None, "nothing at all"),
    ],
)
def test_anything_that_does_not_name_a_build_reads_as_none(sidecar, why):
    """Every one of these is "this artifact does not say", which is what the guard needs.

    A staleness guard reads a file somebody else's generator wrote, possibly years ago,
    possibly truncated. Raising on any of these shapes would turn "I cannot date this
    artifact, so I will not overwrite it" into a traceback, and the operator's next move
    would be ``--force``.
    """
    assert prov.read_str_path(sidecar, ("sources", "game", "game_version_pinned")) is None, why


# --------------------------------------------------------------------------------------
# Putting an artifact in place.
# --------------------------------------------------------------------------------------


def test_the_directory_arrives_whole_and_says_what_it_wrote(tmp_path):
    out = tmp_path / "heightmap"
    written = prov.install_directory(out, {"a.bin": b"12345", "meta.json": b"{}"})

    assert written == {"a.bin": 5, "meta.json": 2}
    assert (out / "a.bin").read_bytes() == b"12345"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["heightmap"]


def test_replacing_an_artifact_leaves_no_trace_of_the_old_one(tmp_path):
    """The retired copy is deleted, and nothing a reader could open is left behind.

    The old directory is renamed aside rather than deleted first, so the window in which
    nothing is in place is a rename wide -- but the aside copy must not survive the call,
    or the next run finds a directory it did not write.
    """
    out = tmp_path / "heightmap"
    prov.install_directory(out, {"height.i16.z": b"old", "gone.bin": b"stale"})
    prov.install_directory(out, {"height.i16.z": b"new"})

    assert sorted(p.name for p in out.iterdir()) == ["height.i16.z"], "a stale file survived"
    assert (out / "height.i16.z").read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["heightmap"]


def test_what_an_interrupted_run_left_behind_is_cleared_rather_than_added_to(tmp_path):
    """A staging directory from a run that died is not a directory to resume into.

    Its files are from another build and possibly another format, and the run that finds
    them wants an empty directory. This is the case the ``.incoming`` name exists for: it
    is not served, so a crashed run costs disk and nothing else.
    """
    out = tmp_path / "heightmap"
    staging = tmp_path / ("heightmap" + prov.STAGING_SUFFIX)
    staging.mkdir()
    (staging / "half-written.bin").write_bytes(b"from a run that died")

    prov.install_directory(out, {"height.i16.z": b"new"})

    assert sorted(p.name for p in out.iterdir()) == ["height.i16.z"]
    assert not staging.exists()


# --------------------------------------------------------------------------------------
# The forward check: do the artifacts on disk still describe the build installed here?
# --------------------------------------------------------------------------------------
#
# The other staleness guard, ``skew_from_meta``, reads the node table's own cross-validation
# block -- and ``check_projection`` in tools/gen_resource_nodes.py refuses to emit a block
# whose deltas are past the rounding floor, so the shipped table can only ever hold a PIN and
# that guard returns None unconditionally. It cannot fire on a game update moving nodes, which
# is the event it exists for. This one can: it asks the install.

#: The pin `fake_install`'s version JSON produces, spelled the way `installed_build` spells it.
INSTALLED_PIN = (
    "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
)
OTHER_PIN = "buildVersion 400000 (engine branch ++FactoryGame+rel-main-1.1.0), the installed build"


def pinned_artifact(data, parts, where, pin):
    """One generated sidecar, with its pin nested where its own generator puts it."""
    path = data.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar: dict = {}
    node = sidecar
    for key in where:
        node = node.setdefault(key, {})
    node[prov.PIN_KEY] = pin
    path.write_text(json.dumps(sidecar), encoding="utf-8")


def test_artifacts_cut_from_this_build_say_nothing(tmp_path):
    game = fake_install(tmp_path / "game")
    data = tmp_path / "data"
    for _name, parts, where in prov.PINNED_ARTIFACTS:
        pinned_artifact(data, parts, where, INSTALLED_PIN)
    assert prov.stale_artifacts(game, data) == []


def test_an_artifact_from_another_build_is_named_out_loud(tmp_path):
    game = fake_install(tmp_path / "game")
    data = tmp_path / "data"
    name, parts, where = prov.PINNED_ARTIFACTS[0]
    pinned_artifact(data, parts, where, OTHER_PIN)
    (note,) = prov.stale_artifacts(game, data)
    assert name in note
    assert "400000" in note and "495413" in note
    assert "may not be where the game now puts them" in note
    assert "tools/" in note


def test_every_pinned_artifact_is_actually_reached(tmp_path):
    """A key path spelled wrong reads as "no pin", which reads as silence -- which is what
    this guard must never be by accident. Each entry is exercised through its own nesting."""
    game = fake_install(tmp_path / "game")
    data = tmp_path / "data"
    for _name, parts, where in prov.PINNED_ARTIFACTS:
        pinned_artifact(data, parts, where, OTHER_PIN)
    (note,) = prov.stale_artifacts(game, data)
    assert f"{len(prov.PINNED_ARTIFACTS)} generated table(s)" in note
    for name, _parts, _where in prov.PINNED_ARTIFACTS:
        assert name in note


def test_an_artifact_that_was_never_generated_is_not_stale(tmp_path):
    """``data/local/`` is gitignored, so a fresh clone has three of the six and no drift."""
    assert prov.stale_artifacts(fake_install(tmp_path / "game"), tmp_path / "data") == []


def test_a_sidecar_too_old_to_carry_a_pin_is_not_stale(tmp_path):
    game = fake_install(tmp_path / "game")
    data = tmp_path / "data"
    _name, parts, _where = prov.PINNED_ARTIFACTS[0]
    (data / Path(*parts).parent).mkdir(parents=True, exist_ok=True)
    data.joinpath(*parts).write_text(json.dumps({"_meta": {}}), encoding="utf-8")
    assert prov.stale_artifacts(game, data) == []


def test_no_install_means_no_comparison_rather_than_a_complaint(tmp_path):
    data = tmp_path / "data"
    _name, parts, where = prov.PINNED_ARTIFACTS[0]
    pinned_artifact(data, parts, where, OTHER_PIN)
    assert prov.stale_artifacts(tmp_path / "no-game", data) == []


def test_a_truncated_sidecar_is_skipped_rather_than_raised(tmp_path):
    game = fake_install(tmp_path / "game")
    data = tmp_path / "data"
    _name, parts, _where = prov.PINNED_ARTIFACTS[0]
    (data / Path(*parts).parent).mkdir(parents=True, exist_ok=True)
    data.joinpath(*parts).write_text("{ truncated", encoding="utf-8")
    assert prov.stale_artifacts(game, data) == []


@pytest.mark.integration
def test_the_shipped_tables_match_the_build_installed_here():
    """Green until a game update lands, and the whole point of it is the day it is not."""
    from satisfactory_mcp import config

    assert prov.stale_artifacts(config.game_root(), config.data_dir()) == []
