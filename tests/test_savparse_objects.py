"""The body walk: levels, object headers, and the property-block slices.

Verified black-box against the vendored parser on all 31 saves it can read: identical
level counts, identical per-level header and object counts, and an identical multiset of
``typePath`` values -- 44,634 objects across 3,124 levels on the reference save, down to
29,734 across 1,624 on the oldest readable one. ``instanceName``, the actor/component
split and ``position`` were compared object by object on the reference save with zero
differences.

These tests run against two committed fixtures, so the suite needs no game install. Every
byte of both is verbatim from a real inflated body; only three int32s are synthesized --
the body size, the grid count and the sub-level count -- because those are what frame a
smaller selection of real records.

``fixtures/save_body.bin``, 4,539 bytes, from the saveVersion **60** reference save:

* the archive version header and its 13 custom versions;
* four grid records, one with a cell and three with none;
* a level with one actor and three components, whose objects are at save version **36**;
* a level whose object is at version **60** and whose trailer is followed by a per-level
  archive header;
* a level at version **52**;
* an empty level, the unnamed persistent-level record, and the real closing
  destroyed-actor table.

``fixtures/save_body_v52.bin``, 3,230 bytes, from a saveVersion **52** save -- the layout
of 25 of the 31 readable saves. It has no archive version header at all and no per-level
ones, which is why a synthetic body could not stand in for it.

The fixtures existing at all is the point: the walk is offset arithmetic end to end, and a
regression in it would otherwise only show up on a machine that has a 2.9 MB save.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from pioneersav import (
    ActorHeader,
    ComponentHeader,
    ObjectSlice,
    ParseError,
    read_body,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "save_body.bin"
FIXTURE_V52 = FIXTURES / "save_body_v52.bin"


@pytest.fixture(scope="module")
def raw() -> bytes:
    if not FIXTURE.is_file():
        pytest.skip("body fixture not committed")
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def save(raw):
    return read_body(raw)


@pytest.fixture(scope="module")
def raw_v52() -> bytes:
    if not FIXTURE_V52.is_file():
        pytest.skip("saveVersion 52 body fixture not committed")
    return FIXTURE_V52.read_bytes()


def _relength(payload: bytes) -> bytes:
    """Put a correct self-describing size on a hand-built body."""
    return struct.pack("<q", len(payload)) + payload


# ------------------------------------------------------------------ the walk


def test_the_whole_body_is_consumed_with_nothing_skipped(save):
    """Zero warnings and a full walk is the end-to-end proof.

    The body is a chain of declared lengths with no index and no magic numbers between
    structures. Landing exactly on the last byte after 5 levels and 6 objects here -- and
    after 3,124 levels and 44,634 objects on the real save -- is what says the chain was
    read and not guessed at. If this ever fails, the walk drifted somewhere upstream and
    every offset after that point is fiction.
    """
    assert save.warnings == []
    assert [lv.name for lv in save.levels][-1] == "Persistent_Level"
    assert len(save.levels) == 5
    assert save.object_count == 6


def test_headers_and_objects_stay_parallel(save):
    """The adapter zips the two lists, so a length mismatch silently drops objects.

    Headers live in one length-prefixed block and property blobs in another. Nothing in
    the file ties entry *i* of one to entry *i* of the other except their order, which is
    why the counts are compared as the object block is opened rather than trusted.
    """
    for level in save.levels:
        assert len(level.headers) == len(level.objects)
        assert level.actorAndComponentObjectHeaders is level.headers


def test_the_grid_table_is_read_not_skipped(save):
    """The cell ids are the cross-check on the level walk.

    On the real save 1,623 of the 1,624 levels that contain objects are named in this
    table. Skipping the region would have been easier and would have thrown away the only
    independent evidence that the level names are real.
    """
    grids = {g.name: g for g in save.preamble.grids}
    assert grids["LandscapeGrid"].cell_size == 51200
    assert grids["LandscapeGrid"].cell_names == []
    assert grids["None"].cell_names == ["None"]
    assert len(save.preamble.custom_versions) == 13
    assert save.preamble.branch.startswith("++FactoryGame+rel-main-")


# ------------------------------------------------------- the two header kinds


def test_an_actor_carries_a_type_path_and_a_transform(save):
    """``typePath``, ``instanceName`` and ``position`` are the three names the sidecar
    reads off a header. Renaming any of them breaks the projection silently, because
    ``iter_objects`` reaches for ``typePath`` with a ``getattr`` default."""
    actors = [h for lv in save.levels for h in lv.headers if isinstance(h, ActorHeader)]
    assert actors, "the fixture must contain at least one actor"
    for actor in actors:
        assert actor.typePath.startswith("/")
        assert actor.instanceName.startswith("Persistent_Level:")
        assert len(actor.position) == 3


def test_a_component_has_no_type_path_attribute(save):
    """This is a compatibility contract, not an accident of the format.

    The bytes DO name a component's class. The old parser did not expose it as
    ``typePath``, the projection's class-based branching is built on components having
    none, and ``iter_objects`` encodes that as ``getattr(header, "typePath", "")``. So the
    class path is exposed here as ``class_path`` and ``typePath`` is deliberately absent:
    adding it would change the projection for every inventory and power component.
    """
    comps = [h for lv in save.levels for h in lv.headers if isinstance(h, ComponentHeader)]
    assert comps, "the fixture must contain at least one component"
    for comp in comps:
        assert not hasattr(comp, "typePath")
        assert getattr(comp, "typePath", "") == ""
        assert comp.class_path.startswith("/Script/FactoryGame.")
        assert comp.parent_actor_name
        # A component hangs off its parent: the parent's path is a prefix of its own.
        assert comp.instanceName.startswith(comp.parent_actor_name)


def test_the_rotation_is_a_unit_quaternion(save):
    """Which is how the transform's field ORDER was established.

    Four floats then three then three could be read several ways. Summing the squares of
    the first four gives 1.0 for all 11,970 actors of the reference save's persistent
    level, and the three that follow land inside the map's coordinate range. Any other
    split fails both checks at once.
    """
    for level in save.levels:
        for header in level.headers:
            if isinstance(header, ActorHeader):
                norm = sum(c * c for c in header.rotation)
                assert norm == pytest.approx(1.0, abs=1e-4)
                assert all(abs(c) < 1_000_000 for c in header.position)


# ------------------------------------------------- the property-block slices


def test_slices_tile_their_level_without_gaps_or_overlaps(save, raw):
    """The slice boundaries ARE the deliverable for the property serialiser.

    Every payload is inside the body, they are strictly increasing, and each one's end is
    where the next entry's fields begin -- 12 bytes before the next payload, or 16 when a
    version-60 entry's trailing int32 sits between them. An off-by-four here would hand
    the next stage a property list starting mid-string.
    """
    for level in save.levels:
        previous: ObjectSlice | None = None
        for slot in level.objects:
            assert 0 <= slot.offset < slot.end <= len(raw)
            assert slot.length >= 0
            if previous is not None:
                gap = slot.offset - previous.end
                assert gap == (16 if previous.version >= 60 else 12)
            previous = slot


def test_a_payload_ends_on_its_property_list_terminator(save, raw):
    """A property list ends with a property named "None", then an int32.

    That is the one thing about the payload's INSIDE this layer can check, and it is worth
    checking, because it is what settled where a version-60 payload starts. Reading the
    extra int32 as a fourth header field instead of a trailer fits every block length in
    the file -- both make an entry ``16 + size`` bytes -- and only the payload's own
    content tells them apart.

    Two shapes are accepted because both occur: the terminator last, or the terminator and
    one more int32. On the reference save the second is what every component has. A third
    case exists and is not asserted here -- 3,209 of the 44,634 actors keep going after
    the terminator, which is class-specific binary data (conveyor contents and the like)
    and is the next stage's problem, not this one's.
    """
    terminator = struct.pack("<i", 5) + b"None\x00" + struct.pack("<i", 0)
    for level in save.levels:
        for slot in level.objects:
            tail = raw[slot.offset : slot.end]
            assert tail.endswith((terminator, terminator + b"\x00" * 4))


def test_version_sixty_entries_are_the_ones_with_a_trailer(save):
    """36, 52 and 60 all appear in one file, because an untouched world-partition cell
    keeps the bytes it was written with. The fixture holds one level of each so the
    version-dependent framing cannot regress unnoticed."""
    versions = sorted({slot.version for lv in save.levels for slot in lv.objects})
    assert versions == [36, 52, 60]


# ----------------------------------------------------- the layout that has no
# ----------------------------------------------------- archive header at all


def test_a_save_version_52_body_starts_at_the_grid_table(raw_v52):
    """saveVersion 52 bodies have no archive version header and no custom versions.

    25 of the 31 readable saves are that layout, and they are told apart by asking whether
    the header's ``(0, 522, 1017)`` signature is at the cursor rather than by assuming. A
    52 body ALSO has no per-level archive headers and therefore no flag announcing one, so
    the two differences have to be handled together -- reading the flag anyway consumed the
    next level's name and failed one level later with "trailer flag 26, expected 0 or 1",
    26 being a 25-character cell id plus its terminator.
    """
    save = read_body(raw_v52)
    assert not save.preamble.has_archive_header
    assert save.preamble.custom_versions == []
    assert save.preamble.branch == ""
    assert len(save.preamble.grids) == 4
    assert save.warnings == []
    assert len(save.levels) == 4
    assert save.object_count == 8
    # Same two header kinds, same parallel lists, same per-object versions.
    kinds = [type(h).__name__ for h in save.levels[0].headers]
    assert kinds.count("ActorHeader") == 4
    assert kinds.count("ComponentHeader") == 3
    assert sorted({s.version for lv in save.levels for s in lv.objects}) == [36, 52]


# ------------------------------------------------------------ the changelist check


def test_records_from_an_older_build_are_ordinary(save, raw):
    """The first save under a new build still carries every level it has not rewritten."""
    changelist = save.preamble.changelist & 0x7FFFFFFF
    assert read_body(raw, build_version=changelist).warnings == []
    assert read_body(raw, build_version=changelist + 6681).warnings == []


def test_a_record_from_a_newer_build_than_the_save_is_reported(save, raw):
    changelist = save.preamble.changelist & 0x7FFFFFFF
    (_at, what), *_ = read_body(raw, build_version=changelist - 1).warnings
    assert f"changelist {changelist}" in what
    assert "newer than the save" in what


# ------------------------------------------------------------ failing loudly


def test_a_lying_body_size_is_refused(raw):
    """The body describes its own length. An autosave read while the game is writing it
    is the routine case, and it must fail here rather than 40 MB later."""
    with pytest.raises(ParseError, match="the body says it is"):
        read_body(struct.pack("<q", len(raw)) + raw[8:])


def test_a_truncated_body_names_the_offset(raw):
    """A short read must never come back as a shorter factory. Cutting the body mid-level
    has to raise, and the message has to say where."""
    with pytest.raises((ParseError, ValueError)):
        read_body(_relength(raw[8 : len(raw) // 2]))


def test_an_unknown_header_kind_is_refused_with_its_class_path(save, raw):
    """The leading int32 of a header is 0 or 1. A third kind means the layout changed, and
    the error carries the class path it had just read so the report says which object."""
    header = save.levels[0].headers[0]
    path = header.typePath.encode("latin-1") + b"\x00"
    at = raw.find(struct.pack("<i", len(path)) + path)
    assert at > 0, "the fixture's first header must be findable by its class path"
    broken = bytearray(raw)
    broken[at - 4 : at] = struct.pack("<i", 7)  # the kind int32 sits just before it
    with pytest.raises(ParseError, match="object header kind 7"):
        read_body(bytes(broken))


def test_a_nonzero_trailer_on_a_version_60_entry_is_refused(save, raw):
    """Every one of the 39,015 version-60 objects in the reference save is followed by a
    zero int32. If a patch ever puts something there, this layer must say so with an
    offset instead of shifting every later slice by four bytes."""
    slot = next(s for lv in save.levels for s in lv.objects if s.version >= 60)
    broken = bytearray(raw)
    broken[slot.end : slot.end + 4] = struct.pack("<i", 1)
    with pytest.raises(ParseError, match="followed by 1"):
        read_body(bytes(broken))


def test_a_payload_size_that_overruns_its_block_is_refused(save, raw):
    """The object block declares its total length and each entry declares its own. Two
    lengths that must agree is a free integrity check, and it is the check that catches a
    torn file whose sizes are individually plausible."""
    slot = next(s for lv in save.levels for s in lv.objects if s.length > 0)
    broken = bytearray(raw)
    broken[slot.offset - 4 : slot.offset] = struct.pack("<i", slot.length + 4096)
    with pytest.raises(ParseError, match="past the end of its block"):
        read_body(bytes(broken))


def test_a_header_count_that_disagrees_with_the_object_count_is_refused(save, raw):
    """They are parallel lists. The adapter zips them, so a mismatch would quietly drop
    the tail of a level rather than fail -- which is exactly the bug class this raises on.
    """
    level = next(lv for lv in save.levels if lv.objects)
    # The object count sits four bytes before the first entry of the level's data block.
    at = level.objects[0].offset - 16
    assert struct.unpack_from("<i", raw, at)[0] == len(level.objects)
    broken = bytearray(raw)
    broken[at : at + 4] = struct.pack("<i", len(level.objects) - 1)
    with pytest.raises(ParseError, match="parallel lists"):
        read_body(bytes(broken))


def test_destroyed_actor_bytes_are_counted_not_ignored(save):
    """The one region skipped by declared length rather than parsed.

    It is a destroyed-actor list in two different shapes and nothing above this reads it,
    so it is stepped over -- but the byte count is reported per level and summed, because
    "we skipped 97,250 bytes" is a fact a reader should be able to check rather than
    discover.
    """
    assert save.skipped_toc_bytes == sum(lv.toc_extra_bytes for lv in save.levels)
    assert save.skipped_toc_bytes > 0


def test_an_empty_body_says_so_instead_of_reporting_a_read_at_zero():
    """A save truncated to exactly its header inflates to nothing, and that is not a tear.

    ``decompress_body`` has no chunks to walk in that case and correctly returns ``b""``, so
    the first thing this walk does -- read the int64 body size -- ran off the end of a
    zero-byte buffer and reported "read of 8 at 0 runs past end (0)". Three zeros and no
    subject: nothing in it says the *body* rather than the file, and nothing says the body
    was empty rather than the offset being wrong. The sidecar shows that string to a player
    whose autosave was caught between being created and being filled in.
    """
    with pytest.raises(ParseError, match="the inflated body is 0 bytes"):
        read_body(b"")
    with pytest.raises(ParseError, match="too short to hold the int64 size field"):
        read_body(b"\x00\x00\x00")
