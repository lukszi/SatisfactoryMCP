"""FGLightweightBuildableSubsystem's trailing bytes: every foundation and wall in the save.

These buildables exist in no actor header, so this blob is the only record of them -- 8,347
pieces on the reference save, 224,530 across the 31 readable ones -- and it is where the
projection's ``structures`` and ``lightweight_counts`` come from. Verified black-box against
the vendored parser: same class census, same instance positions, on every save on disk.

``fixtures/save_lightweight.bin``, 4,148 bytes, is 10 real instance records in 3 real classes
lifted verbatim from the reference save. Only the class count and the two instance counts are
rewritten, to match the subset; every byte inside a record is a byte the game wrote. Four of
the records are foundations, two of them the anomalous kind whose last two fields read 6 and 0
where every other record reads 0 and -1.

``fixtures/save_lightweight_v2.bin``, 2,374 bytes, is the same thing from a saveVersion 52
save, where the blob is version 2 and the record is five bytes shorter. That is 25 of the 31
readable saves on this disk, so it is the commoner case rather than the legacy one -- and the
version check refused all 25 until this was derived, which is why both are pinned.

What these tests are guarding is a walk over fixed-size records with no separators. The
record is 162 bytes plus two length-prefixed references, and if any field's width is wrong
the walk desynchronises -- so the failure never looks like a wrong position, it looks like a
class path that is not one, several classes later. Pinning the widths here is what turns that
into a named test.
"""

from __future__ import annotations

import struct

import pytest

from pioneersav import ParseError, read_lightweight
from pioneersav.lightweight import RECORD_BYTES, VERSION
from tests.support.paths import committed_fixture

SWATCH = "/Game/FactoryGame/Buildable/-Shared/Customization/Swatches/SwatchDesc_Slot16.SwatchDesc_Slot16_C"
FOUNDATION = "Build_Foundation_8x1_01_C"


@pytest.fixture(scope="module")
def raw() -> bytes:
    return committed_fixture("save_lightweight.bin").read_bytes()


@pytest.fixture(scope="module")
def raw_v2() -> bytes:
    return committed_fixture("save_lightweight_v2.bin").read_bytes()


@pytest.fixture(scope="module")
def blob(raw):
    return read_lightweight(raw, 0, len(raw))


@pytest.fixture(scope="module")
def blob_v2(raw_v2):
    return read_lightweight(raw_v2, 0, len(raw_v2))


def _classes(blob) -> dict[str, list]:
    return {path.rsplit(".", 1)[-1]: items for path, items in blob[1:]}


# ------------------------------------------------------------- the shape


def test_it_reads_the_version_and_every_class(blob):
    assert blob[0] == VERSION == 4
    assert list(_classes(blob)) == [
        "Build_Wall_Orange_Tris_8x1_C",
        "Build_Wall_Orange_FlipTris_8x2_C",
        FOUNDATION,
    ]


def test_the_instance_counts_are_the_census(blob):
    """``lightweight_counts`` is exactly this, per class."""
    assert [len(items) for items in _classes(blob).values()] == [2, 4, 4]


def test_the_shape_is_the_one_the_projection_reads(blob):
    """``extract`` walks ``[version, [classPath, [instance, ...]], ...]`` and takes the
    position from ``inst[1]``. Both are load-bearing, so both are pinned here rather than
    left to the projection tests to discover."""
    assert isinstance(blob[0], int)
    for entry in blob[1:]:
        path, items = entry
        assert path.startswith("/Game/") and path.endswith("_C")
        for inst in items:
            assert len(inst[0]) == 4, "rotation is a quaternion"
            assert len(inst[1]) == 3, "position is a vector"


# ------------------------------------------------------------- the values


def test_a_foundation_lands_where_the_game_put_it(blob):
    first = _classes(blob)[FOUNDATION][0]
    assert first[1] == [10400.0, -248800.0, -1750.0]


def test_every_rotation_is_a_unit_quaternion(blob):
    """The strongest check available on the transform without a second parser: four doubles
    read at the wrong offset do not normalise."""
    for items in _classes(blob).values():
        for inst in items:
            assert abs(sum(x * x for x in inst[0]) - 1.0) < 1e-9


def test_scale_is_uniform_and_the_format_has_it(blob):
    """Absent from the vendored parser's output, present in the bytes, 1,1,1 on all 224,530
    instances measured. Kept because reading it is what keeps the walk aligned."""
    for items in _classes(blob).values():
        for inst in items:
            assert inst[2] == [1.0, 1.0, 1.0]


def test_only_the_paint_slot_and_the_recipe_are_populated(blob):
    """Five of the seven references are empty on every instance of every save here. They are
    read as references rather than skipped so that a save which populates one still parses."""
    for items in _classes(blob).values():
        for inst in items:
            populated = [i for i in (3, 4, 5, 6, 8, 10, 11) if str(inst[i])]
            assert populated == [3, 10]


def test_the_recipe_names_the_buildable_the_class_does(blob):
    for short, items in _classes(blob).items():
        stem = short.removeprefix("Build_").removesuffix("_C")
        for inst in items:
            assert str(inst[10]).endswith(f"Recipe_{stem}_C")


def test_the_swatch_is_the_paint_slot(blob):
    assert str(_classes(blob)[FOUNDATION][0][3]) == SWATCH


def test_the_colours_are_two_rgba_quadruples(blob):
    for items in _classes(blob).values():
        for inst in items:
            assert inst[7] == [[0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, 1.0]]


def test_the_two_trailing_fields_covary_on_some_records(blob):
    """33 of the reference save's 4,617 foundations read 6 and 0 where the rest read 0 and
    -1; four of them are in this fixture. What the pair means is not established, and the
    point of the test is that both are read at the right width -- a wrong one here shifts
    the next record's quaternion, which the unit-length check above would then catch."""
    tails = [(inst[13], inst[14]) for inst in _classes(blob)[FOUNDATION]]
    assert tails == [(6, 0), (6, 0), (0, -1), (0, -1)]


# ------------------------------------------------------------- version 2, the commoner one


def test_version_two_reads_the_same_buildables(blob_v2):
    assert blob_v2[0] == 2
    assert list(_classes(blob_v2)) == ["Build_Wall_8x4_01_C", FOUNDATION]
    assert [len(items) for items in _classes(blob_v2).values()] == [3, 3]


def test_version_two_drops_the_last_two_fields(blob, blob_v2):
    """The whole difference between the two versions, stated as the field count. Anything
    else that changed would show up as a walk that does not consume the blob.

    16 and 14 rather than 15 and 13 because every record now ends with its type-specific data
    list. The *difference* is what this test is about and it is still two.
    """
    v4 = _classes(blob)[FOUNDATION][0]
    v2 = _classes(blob_v2)[FOUNDATION][0]
    assert len(v4) == 16
    assert len(v2) == 14
    assert len(v4) - len(v2) == 2
    assert RECORD_BYTES[4] - RECORD_BYTES[2] == 5, "one uint8 and one int32"


def test_a_record_with_no_type_specific_data_ends_with_an_empty_list(blob, blob_v2):
    """The count at index 12 and the list at the end have to agree, and on both fixtures the
    count is 0 -- foundations and walls carry no per-piece data. A beam does: on the build save
    7,687 of 43,516 instances carry one `BuildableBeamLightweightData` block holding the length
    that piece was drawn to. That is the case this field exists for, and reading the count as
    the constant 0 is what left the walk 116 bytes short on the first beam.
    """
    for b in (blob, blob_v2):
        for items in _classes(b).values():
            for inst in items:
                assert inst[12] == 0
                assert inst[-1] == []


def test_version_two_transforms_are_still_transforms(blob_v2):
    """Same checks as version 4: a record read at the wrong width does not produce unit
    quaternions and unit scale, it produces noise."""
    for items in _classes(blob_v2).values():
        for inst in items:
            assert abs(sum(x * x for x in inst[0]) - 1.0) < 1e-9
            assert inst[2] == [1.0, 1.0, 1.0]
            assert len(inst[1]) == 3


def test_version_two_still_names_the_recipe_and_the_swatch(blob_v2):
    for short, items in _classes(blob_v2).items():
        stem = short.removeprefix("Build_").removesuffix("_C")
        for inst in items:
            assert str(inst[10]).endswith(f"Recipe_{stem}_C")
            assert "/Customization/Swatches/" in str(inst[3])


# ------------------------------------------------------------- the refusals


def test_an_unknown_version_is_refused_and_named(raw):
    """A patch that changes the record would otherwise be read as this one, and the walk
    would fail somewhere with no hint of why."""
    bumped = bytearray(raw)
    bumped[4:8] = struct.pack("<i", 5)
    with pytest.raises(ParseError, match=r"are version 5, this reads \[2, 4\]"):
        read_lightweight(bytes(bumped), 0, len(bumped))


def test_a_known_version_is_not_read_as_the_other_one(raw_v2):
    """Version 2's record is five bytes shorter, so reading it as version 4 overruns the
    first record and lands inside the second. Getting this wrong refused 25 of the 31
    readable saves outright, which is the failure this pins."""
    faked = bytearray(raw_v2)
    faked[4:8] = struct.pack("<i", 4)
    with pytest.raises(ParseError):
        read_lightweight(bytes(faked), 0, len(faked))


def test_a_desynchronised_walk_says_which_class_it_lost(raw):
    """One instance short: the walk resumes mid-record and reads a quaternion where a class
    path should be. What it must report is the class it was looking for -- that number is
    what localises the damage -- and not the bounds check that a negative string length
    would otherwise trip first."""
    broken = bytearray(raw)
    at = raw.index(b"_C\x00") + 3  # the instance count after the first class path
    broken[at : at + 4] = struct.pack("<i", 1)
    with pytest.raises(ParseError, match="expected buildable class 2 of 3.*out of step"):
        read_lightweight(bytes(broken), 0, len(broken))


def test_a_class_count_past_the_end_is_refused_too(raw):
    """The other way to desynchronise: the count says four classes and the blob holds three,
    so the walk arrives at the end rather than mid-record."""
    broken = bytearray(raw)
    broken[8:12] = struct.pack("<i", 4)
    with pytest.raises(ParseError, match="expected buildable class 4 of 4|runs past end"):
        read_lightweight(bytes(broken), 0, len(broken))


def test_a_truncated_blob_is_refused_rather_than_half_returned(raw):
    with pytest.raises(ParseError, match="runs past end|does not fit"):
        read_lightweight(raw[:-400], 0, len(raw) - 400)


def test_bytes_left_over_are_refused(raw):
    """The whole-blob check: the classes must account for every byte the object declared.
    A record read four bytes short leaves a remainder here even when nothing else complains."""
    with pytest.raises(ParseError, match="bytes short of the object's"):
        read_lightweight(raw + bytes(16), 0, len(raw) + 16)


def test_an_absurd_instance_count_is_refused_without_reading_it(raw):
    """The count is attacker-controlled in the sense that matters here -- a torn file makes
    it arbitrary -- and 2 billion instances must not become 2 billion reads."""
    broken = bytearray(raw)
    at = raw.index(b"/Game/") + raw[raw.index(b"/Game/") :].index(b"\x00") + 1
    broken[at : at + 4] = struct.pack("<i", 2_000_000_000)
    with pytest.raises(ParseError, match="does not fit"):
        read_lightweight(bytes(broken), 0, len(broken))
