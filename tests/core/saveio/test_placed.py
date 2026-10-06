"""``extract._placed``: the lightweight records that are floor, and the ones that are not.

The subsystem blob holds one record per foundation, wall, ramp and catwalk in the world -- and
**173 of the 224,530 records across the 31 modern saves on this disk name no asset at all.**
Every other record carries both a paint swatch and the recipe it was built from; not one record
carries exactly one of the two. Re-measured over the whole folder on 2026-07-30, by walking each
body and decoding the subsystem's trailing bytes: 224,357 with both, 173 with neither, **0 with
one**. That is what makes "does any field name an asset" a clean split rather than a heuristic.

They sit on 7 of the 31 saves -- 29, 2, 27, 80, 2, 25 and 8 of them -- and span 8 ordinary
buildable classes (foundations, polished foundations, walls, a window, a gate, a ramp, catwalk
stairs and a straight catwalk), so they are not one odd class misbehaving.

**Why it is worth a test.** ``graph/structure.py`` turns these positions into foundation slabs
and ``spatial/elevation.py`` samples every one as ground height, so a record that is not a piece
*invents floor*. Measured today, the same save read both ways -- every record, then only the
placed ones:

| save | records | placed | slabs | biggest slabs |
|---|---|---|---|---|
| ``Han solo`` | 5,961 | 5,932 | **26 -> 25** | total tiles 3,544 -> 3,537 |
| ``Han Solo_260726-212757`` | 8,372 | 8,347 | 41 -> 41 | a slab of **521 -> 496** tiles |
| ``Han Solo_061125-003229`` | 7,017 | 6,990 | 37 -> 37 | a slab of **384 -> 358** tiles |

Both failure modes are here: on ``Han solo`` the phantom records cluster far enough from
anything real to become a slab of their own -- an entire 7-tile platform that was never built --
and on the other two they land against a real slab and pad it. Neither looks like a bug
downstream. A 521-tile platform is exactly as plausible as a 496-tile one.

**What a stale record actually looks like, because it is the reason the guard cannot be
cleverer.** All 173 are byte-for-byte ordinary apart from the seven reference slots being empty:
unit quaternion, unit scale, colours, the same trailing pair. The 80 on
``Han Solo_071225-235542`` all carry unit rotations and cluster inside a real 84 m x 44 m patch
of the map. So nothing in the transform separates them from floor, and a "sanity-check the
position" guard would pass all 173.

**The guard is deliberately not positional**, and the test that matters here is the one that
pins that. Our parser's instance record has the swatch at index 3 and the recipe at 10; the
vendored GPL parser -- the oracle this whole reimplementation was measured against, now
deleted -- had them at 2 and 7, because it did not surface the transform's scale. A guard
written as ``inst[3] or inst[10]`` would read different fields under the two shapes and would
have broken the projection parity that was the acceptance test. So the rotation test below is
not a curiosity: an index-based rewrite passes every other test in this file and fails that one.

Fixtures only, plus one integration test that re-measures the split over the real saves. The
committed lightweight fixtures come from saves with **no** stale records, which is why the fix
moved no committed number, and which is why every stale record here is synthetic: a real record
from the fixture with its references emptied, exactly the way the game wrote the 173.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from pioneersav import (
    ObjectReference,
    ParseError,
    read_body,
    read_info_bytes,
    read_lightweight,
)
from pioneersav.chunks import decompress_body
from pioneersav.lightweight import LIGHTWEIGHT_SUBSYSTEM
from pioneersav.objects import ActorHeader
from pioneersav.properties import read_object
from pioneersav.versions import FIRST_MODERN_BODY
from satisfactory_mcp.core.saveio.extract import Drops, _lightweight, _placed, _structures
from satisfactory_mcp.domain.factories.structure import build_structures
from tests.support.fanout import fanout_width, in_order
from tests.support.paths import FIXTURES

#: The foundation class in both committed fixtures. ``graph/structure.py`` only treats a record
#: as a floor tile if its class name says foundation, so the slab tests below need this one.
FOUNDATION = "Build_Foundation_8x1_01_C"


class Subsystem:
    """The one thing ``_lightweight`` and ``_structures`` read off an object.

    Both take the parsed object and go straight to ``actorSpecificInfo``; nothing else about
    ``FGLightweightBuildableSubsystem`` matters to them, and a stand-in keeps these tests on
    committed bytes instead of a save.
    """

    def __init__(self, blob) -> None:
        self.actorSpecificInfo = blob


def _blob(name: str) -> list:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"{name} not committed")
    raw = path.read_bytes()
    return read_lightweight(raw, 0, len(raw))


@pytest.fixture(scope="module")
def blob() -> list:
    """10 real instance records in 3 real classes, blob version 4 (a saveVersion 60 save)."""
    return _blob("save_lightweight.bin")


@pytest.fixture(scope="module")
def blob_v2() -> list:
    """The same from a saveVersion 52 save, where the record is five bytes shorter."""
    return _blob("save_lightweight_v2.bin")


def _records(blob) -> dict[str, list]:
    return {path.rsplit(".", 1)[-1]: items for path, items in blob[1:]}


def _stale(record: list) -> list:
    """A real record with every reference emptied -- what the game wrote on all 173.

    Emptying *every* reference rather than just the swatch and the recipe is the measurement:
    over the 31 saves not one stale record has a populated reference in any of the seven slots,
    so there is no partial case for the guard to arbitrate and none is invented here.
    """
    return [ObjectReference("", "") if isinstance(f, ObjectReference) else f for f in record]


def _moved(record: list, dx: float) -> list:
    """The same record shifted along x, so a phantom can be placed apart from a real slab."""
    out = list(record)
    out[1] = [record[1][0] + dx, record[1][1], record[1][2]]
    return out


def _salted(blob, *extra: list) -> list:
    """The blob with extra instance records appended to the foundation class.

    The records go into the *blob*, not into the projection's output, so ``_structures`` is the
    thing deciding what to emit. Appending them to the output instead would make the slab tests
    below pass whatever ``_placed`` does -- they would be testing ``build_structures``, which
    ``tests/domain/factories/test_select.py`` already covers.
    """
    return [
        blob[0],
        *(
            [path, [*items, *(extra if path.endswith(FOUNDATION) else ())]]
            for path, items in blob[1:]
        ),
    ]


def _before_the_fix(blob, *extra: list) -> dict:
    """What the projection emitted when every record was a piece: the counterfactual.

    Rows are appended to ``_structures``'s output rather than filtered out of it, so this is the
    pre-``_placed`` census over exactly the same positions -- which is what makes the slab
    numbers in the two tests below a comparison rather than an assertion about one number.
    """
    payload = _structures(Subsystem(blob), Drops())
    ci = payload["classes"].index(FOUNDATION)
    rows = [[ci, int(r[1][0]), int(r[1][1]), int(r[1][2])] for r in extra]
    return {"classes": payload["classes"], "instances": payload["instances"] + rows}


def _slabs(payload: dict):
    return build_structures({"structures": payload, "machines": []}).slabs


# ---------------------------------------------------- what the committed fixtures contain


def test_every_committed_record_is_a_placed_piece(blob, blob_v2):
    """Both fixtures come from saves with no stale records, and the tests below rely on it.

    If a future fixture is lifted from one of the 7 affected saves, the counts in the next
    test stop meaning "every record" and this says so first.
    """
    for name, b in (("v4", blob), ("v2", blob_v2)):
        records = [inst for items in _records(b).values() for inst in items]
        assert records, f"{name}: no records in the fixture"
        assert all(_placed(inst) for inst in records), f"{name}: a committed record reads stale"


def test_the_projection_emits_every_placed_record(blob, blob_v2):
    """The guard is a filter, not a rewrite: with nothing stale in the bytes, both projection
    fields are exactly the blob's census, at both blob versions."""
    censuses = (
        (
            blob,
            {
                "Build_Wall_Orange_Tris_8x1_C": 2,
                "Build_Wall_Orange_FlipTris_8x2_C": 4,
                FOUNDATION: 4,
            },
        ),
        (blob_v2, {"Build_Wall_8x4_01_C": 3, FOUNDATION: 3}),
    )
    for b, expected in censuses:
        assert _lightweight(Subsystem(b)) == expected
        assert len(_structures(Subsystem(b), Drops())["instances"]) == sum(expected.values())


# ------------------------------------------------------------------ the guard itself


def test_a_record_naming_no_asset_at_all_is_not_placed(blob_v2):
    """The 173, reproduced from a real record: empty the references and nothing else."""
    real = _records(blob_v2)[FOUNDATION][0]
    assert _placed(real)
    assert not _placed(_stale(real))


def test_the_transform_cannot_tell_a_stale_record_from_a_real_one(blob_v2):
    """Why the guard asks about assets and not about geometry.

    A stale record's rotation is a unit quaternion, its scale is 1,1,1 and its position is a
    real spot on the map -- measured on all 80 of ``Han Solo_071225-235542``'s, which cluster
    inside an 84 m x 44 m patch. Every check ``tests/pioneersav/test_lightweight.py`` uses to prove the
    record walk is aligned passes on a stale record too, which is exactly why a *parser* test
    could not have caught this and the projection needed its own.
    """
    stale = _stale(_records(blob_v2)[FOUNDATION][0])
    assert abs(sum(x * x for x in stale[0]) - 1.0) < 1e-9
    assert stale[2] == [1.0, 1.0, 1.0]
    assert all(abs(c) < 500_000 for c in stale[1]), "a plausible world position, in centimetres"


def test_the_guard_reads_the_fields_and_not_their_positions(blob_v2):
    """The regression the commit warned about, made falsifiable.

    Our record puts the swatch at index 3 and the recipe at 10; the deleted vendored parser put
    them at 2 and 7. Rotating the field list stands in for "some other engine's shape": the
    contents are unchanged, so a content-based guard is invariant under it and an index-based
    one is not. ``inst[3] or inst[10]`` passes every other test in this file and fails here on
    almost every rotation.
    """
    real = _records(blob_v2)[FOUNDATION][0]
    stale = _stale(real)
    for shift in range(1, len(real)):
        rotated = real[shift:] + real[:shift]
        assert _placed(rotated), f"a real record read as stale after rotating by {shift}"
        assert not _placed(stale[shift:] + stale[:shift]), f"stale read as placed at {shift}"


def test_a_record_of_nothing_but_numbers_is_not_placed():
    """Numbers are never assets, and the degenerate inputs must not raise.

    ``_placed`` is called on whatever the trailing-byte decoder produced, one class deep in the
    projection, so it has to answer for a shape nobody planned: it is the last thing between a
    malformed blob and a slab census. ``None`` is the case that matters -- a class whose instance
    list is not a list at all -- because an exception here is a save the sidecar cannot read.
    """
    assert not _placed([[0.0, 0.0, 0.0, 1.0], [100.0, 200.0, 300.0], [1.0, 1.0, 1.0], 0, -1])
    assert not _placed([])
    assert not _placed(None)


def test_a_record_naming_one_asset_of_the_two_counts_as_placed(blob_v2):
    """A decision, not a measurement, and recorded as one.

    **Zero** of the 224,530 records carry exactly one of the swatch and the recipe, so nothing
    on this disk says what such a record would mean. The guard keeps it, which errs towards
    reporting floor that exists over dropping floor that does. Pinned so that a rewrite to
    "swatch AND recipe" -- which reads equally well and is equally unsupported -- is a visible
    change of behaviour rather than a quiet one.
    """
    real = _records(blob_v2)[FOUNDATION][0]
    swatch_only = _stale(real)
    swatch_only[3] = real[3]
    recipe_only = _stale(real)
    recipe_only[10] = real[10]
    assert _placed(swatch_only) and _placed(recipe_only)


# -------------------------------------------------- what a stale record does downstream


def test_a_stale_record_is_dropped_from_both_projection_fields(blob_v2):
    """The fix, at the two functions that ship it: the class census and the transforms.

    Both are exercised because they filter separately -- ``_lightweight`` counts and
    ``_structures`` emits positions -- and an earlier shape of this fix guarded only one of
    them, which reads as a projection whose two lightweight fields disagree about how much
    floor there is.
    """
    stale = _stale(_records(blob_v2)[FOUNDATION][0])
    salted = Subsystem(
        [
            blob_v2[0],
            *(
                [path, [*items, stale] if path.endswith(FOUNDATION) else items]
                for path, items in blob_v2[1:]
            ),
        ]
    )
    assert _lightweight(salted) == _lightweight(Subsystem(blob_v2))
    drops = Drops()
    assert _structures(salted, drops) == _structures(Subsystem(blob_v2), Drops())
    # And a stale slot is NOT a warning. The drop channel says "this save carries records
    # this parser could not read"; a stale slot is one the game itself wrote as nothing, so
    # counting it would put a permanent complaint on 7 of the 31 saves that are all fine.
    assert sum(drops.values()) == 0


def test_a_stale_record_that_stands_apart_invents_a_whole_slab(blob_v2):
    """``Han solo.sav``: 26 slabs where 25 are built, the extra one 7 phantom tiles.

    The three real foundations in the fixture are one slab. A phantom 40 m away is out of
    linking range of everything, so before the fix it became a platform of its own -- which is
    the failure worth fixing, because a slab is what ``factory_sites`` names and what
    ``plan_layout`` offers to build on.
    """
    phantom = _moved(_stale(_records(blob_v2)[FOUNDATION][0]), 4_000.0)
    guarded = _slabs(_structures(Subsystem(_salted(blob_v2, phantom)), Drops()))
    assert [s.tiles for s in guarded] == [3], "the phantom must not be floor"

    was = _slabs(_before_the_fix(blob_v2, phantom))
    assert sorted(s.tiles for s in was) == [1, 3], "the same positions, counted the old way"


def test_a_stale_record_beside_a_slab_invents_floor_inside_it(blob_v2):
    """``Han Solo_260726-212757``: the same 41 slabs, one of them 521 tiles instead of 496.

    The slab count is unchanged here, which is the point -- nothing downstream looks wrong.
    The platform is simply bigger than the one the player built, and every consumer that asks
    "will this fit" gets a yes it should not.
    """
    phantom = _moved(_stale(_records(blob_v2)[FOUNDATION][0]), 800.0)  # one tile along
    assert [
        s.tiles for s in _slabs(_structures(Subsystem(_salted(blob_v2, phantom)), Drops()))
    ] == [3]
    assert [s.tiles for s in _slabs(_before_the_fix(blob_v2, phantom))] == [4]


# ------------------------------------------------------- the premise, over the real saves


def _saves_root() -> Path:
    """Where the player's saves are, or a skip. Same discovery as ``tests/pioneersav/test_vendor_parity``."""
    env = os.environ.get("SATISFACTORY_SAVES")
    candidates = [Path(env)] if env else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "FactoryGame" / "Saved" / "SaveGames")
    for root in candidates:
        if root.is_dir():
            return root
    pytest.skip("no save directory on this machine")
    raise AssertionError("unreachable")


def _subsystem_blob(path: str | Path) -> list | None:
    """The buildable blob out of one save, without decoding the other 44,000 objects.

    A full parse is ~2.5 s a save and 87 s for the folder, nearly all of it property bodies
    this needs none of. Walking the body and decoding the one object's property block instead
    is ~0.35 s a save, measured, which is what makes a whole-folder check affordable at all.

    ``None`` for a save with no such actor: on the 35 pre-1.0 saves the subsystem does not
    exist, because in 2021-2023 every foundation was an actor of its own.
    """
    data = Path(path).read_bytes()
    info = read_info_bytes(data)
    body = decompress_body(data, info.body_offset, old=info.save_version < FIRST_MODERN_BODY)
    for level in read_body(body, info.save_version).levels:
        for header, slot in zip(level.headers, level.objects, strict=True):
            if isinstance(header, ActorHeader) and header.typePath == LIGHTWEIGHT_SUBSYSTEM:
                obj = read_object(body, slot, actor=True, save_version=info.save_version)
                return read_lightweight(body, obj.extra_offset, obj.extra_length)
    return None


#: One save's contribution to the whole-folder split below, computed in a child process.
#:
#: The tallying moved in here with the parse rather than staying in the caller, and that is
#: the point of the shape: what crosses the pipe is three integers and a short list of names,
#: not the subsystem blob, which is a quarter of a million records on this disk. The parse is
#: pure Python, so this is a process rather than a thread for the same reason
#: ``tests/pioneersav/test_trailers`` is.
#:
#: Always a triple, and the first slot being ``None`` is the "nothing to count" answer: a save
#: this parser refused (the refusal rides in the third slot) or a save with no lightweight
#: subsystem at all. A folder that stops parsing must not pass as a folder with nothing to
#: say, so the refusals are carried back rather than swallowed.
def _split_one(path: str) -> tuple[dict[str, int] | None, list[str], str | None]:
    name = Path(path).name
    try:
        blob = _subsystem_blob(path)
    except ParseError as exc:
        return None, [], f"{name}: {exc}"
    if blob is None:
        return None, [], None
    counts = {"both": 0, "neither": 0, "one": 0}
    disagreements: list[str] = []
    for _cls, items in blob[1:]:
        for inst in items:
            named = sum(1 for i in (3, 10) if str(inst[i]))
            counts[("neither", "one", "both")[named]] += 1
            if _placed(inst) != (named == 2):
                disagreements.append(f"{name}: {inst[1]}")
    return counts, disagreements, None


@pytest.mark.integration
@pytest.mark.whole_folder
def test_no_save_on_disk_holds_a_record_with_one_asset_of_the_two():
    """The premise the guard rests on, re-measurable as the player keeps playing.

    ``_placed`` is only a clean split because "names a swatch" and "names a recipe" agree on
    every record in existence here. This walks every save the machine has and asserts that,
    rather than asserting the totals -- 224,357 / 173 / 0 over 31 saves on 2026-07-30 -- which
    change every time the game autosaves and would make a passing test a stale one.

    It is also the canary for the day the split stops being clean: a single record carrying one
    of the two would turn the guard back into a heuristic, and this is what would say so.

    A ``.sav`` this parser refuses is counted and otherwise ignored, not swallowed silently:
    ``SaveGames/ServerManager_V2.sav`` is 105 bytes of dedicated-server bookkeeping that sits
    beside the worlds and is not one, and the refusals are asserted on below so that a folder
    which stops parsing cannot pass as a folder with nothing to say.

    **Parsed in child processes, folded in sorted order.** Nothing here stops early, so the
    order does not decide which saves are measured -- but it decides what a failure SAYS.
    ``disagreements[:5]`` and ``refused`` are both asserted by their contents, so folding in
    whatever order the pool finished would have made those five names a different five on
    every run, and a report that changes between two runs of an unchanged parser is not a
    report.
    """
    counts = {"both": 0, "neither": 0, "one": 0}
    disagreements: list[str] = []
    saves, refused = 0, []
    paths = [str(p) for p in sorted(_saves_root().rglob("*.sav"))]
    width = fanout_width()
    with ProcessPoolExecutor(max_workers=width) as pool:
        for _path, (got_counts, got_disagreements, got_refused) in in_order(
            pool, paths, _split_one, width=width
        ):
            if got_refused is not None:
                refused.append(got_refused)
            if got_counts is None:
                continue
            saves += 1
            for bucket, n in got_counts.items():
                counts[bucket] += n
            disagreements.extend(got_disagreements)
    if not saves:
        pytest.skip(f"no save with a lightweight subsystem here; {len(refused)} refused")

    assert counts["one"] == 0, (
        f"{counts['one']} records over {saves} saves carry a swatch or a recipe but not both; "
        "_placed is a clean split no longer, and its docstring says it is one"
    )
    assert counts["neither"], (
        f"none of the {saves} saves holds a stale record, so this run proved nothing about the "
        "guard -- the 173 measured on 2026-07-30 sat on 7 of 31 saves"
    )
    assert disagreements[:5] == [], f"_placed disagrees with the split: {disagreements[:5]}"
    assert len(refused) <= 1, f"more than the known non-save was refused: {refused}"
