"""The destroyed-actor lists: the save's only record of what has been collected.

The world is not saved. Every power slug, mushroom, Mercer sphere and crashed drop pod sits
where the map put it, so a save cannot record collectibles by listing what exists -- it
records the **negative**, which map-placed actors are gone. Three lists hold that, and the
walk used to step over two of them by declared length.

**What is being pinned, and why it is byte-exact.** There is no flag saying which shape a
list is in and no separator inside one. A sub-level writes a bare ``[i32 count][refs]``; the
persistent level writes the same thing grouped by world-partition cell,
``[i32 groups][str cell][i32 count][refs]``. Reading the wrong shape does not fail on any
field -- it reads plausible strings and stops in the wrong place. What catches it is the
landing: after the list the cursor must be exactly on the header block's declared end. That
predicate holds for all 3,124 levels of the reference save, and one test below shows what it
rejects (reading the persistent level's grouped list as a bare one yields one bogus reference
and lands 351 bytes short of the end).

``fixtures/save_body_destroyed.bin``, 3,063 bytes, is a real body cut down to the levels that
carry these lists. Every byte is verbatim from the saveVersion 60 reference save except the
body's own size, the grid count (inherited from ``save_body.bin``), the sub-level count, and
the persistent level's two block sizes and its header count -- that last one because its real
header block is 32,515 headers and what this fixture is about is the 385 bytes after them.

It holds, in order:

* ``AHMDFO6QF4K7AA0N6ETSVWH0T`` -- one destroyed actor, in BOTH its header block and its
  trailer. The same actor written twice is the normal case: 854 of the reference save's 889
  are in both lists, which is why the three are merged rather than picked from.
* ``F3WI9LDL58SN8N6DYIW2OF3RO`` -- two, again in both lists, so the count loop runs more than
  once.
* ``0O2UIH8ZOBYWRN8PY7727SVBT`` -- an EMPTY header block and one actor in its trailer only.
  Both halves matter: a level with no list at all is a different case from one with a
  zero-length list, and a trailer-only actor is what proves the merge is a union.
* the persistent level, whose grouped list holds five looted drop pods and crashed ships;
* the real closing table, 12 references in 8 cell groups -- whose first five are the very
  drop pods above, so the merge has something to deduplicate at every level.
"""

from __future__ import annotations

import struct

import pytest

from pioneersav import Level, ParseError, Reader, SaveBody, read_body
from pioneersav.objects import _read_destroyed_block, _read_destroyed_refs
from tests.support.paths import FIXTURES

FIXTURE = FIXTURES / "save_body_destroyed.bin"

#: The three sub-levels, in fixture order. 25-character partition-cell ids.
LEVEL_ONE = "AHMDFO6QF4K7AA0N6ETSVWH0T"
LEVEL_TWO = "F3WI9LDL58SN8N6DYIW2OF3RO"
LEVEL_EMPTY = "0O2UIH8ZOBYWRN8PY7727SVBT"


@pytest.fixture(scope="module")
def raw() -> bytes:
    if not FIXTURE.is_file():
        pytest.skip("destroyed-actor body fixture not committed")
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def save(raw):
    return read_body(raw)


def _toc_at(raw: bytes, name: str) -> int:
    """Offset of a named level's ``i64 toc size``, found from its name string.

    Positional, not a search for the list itself: the name is length-prefixed, so its first
    occurrence in the body is the level record that owns it (the references inside a list
    spell the cell again, but always later).
    """
    key = struct.pack("<i", len(name) + 1) + name.encode("latin-1") + b"\x00"
    at = raw.index(key)
    return at + len(key)


def _grouped_blob(raw: bytes) -> bytes:
    """The persistent level's grouped list, verbatim: one group named ``Persistent_Level``."""
    start = raw.index(struct.pack("<i", 1) + struct.pack("<i", 17) + b"Persistent_Level\x00")
    return raw[start : start + 385]


# ------------------------------------------------------------------ the two shapes


def test_the_whole_body_is_consumed_with_every_list_read(save):
    """The end-to-end proof, and the reason these lists stopped being skipped.

    97,250 bytes of the reference save were stepped over by declared length before this. The
    walk now reads them and still lands on the body's last byte with no warnings, which is
    what says the two shapes were derived rather than guessed at.
    """
    assert save.warnings == []
    assert [lv.name for lv in save.levels] == [
        LEVEL_ONE,
        LEVEL_TWO,
        LEVEL_EMPTY,
        "Persistent_Level",
    ]


def test_a_sub_level_writes_one_bare_list_keyed_to_itself(save):
    """``[i32 count][refs]``, and each reference names the cell it is in.

    The cell on every reference of a sub-level's own list is that level's name -- true for
    all 859 of the reference save's header-block entries. It is the redundancy that makes the
    bare shape checkable at all, since nothing else in those bytes says which level they
    belong to.
    """
    levels = {lv.name: lv for lv in save.levels}
    assert [path for _cell, path in levels[LEVEL_ONE].destroyed] == [
        "Persistent_Level:PersistentLevel.BP_Ship32_2"
    ]
    assert [path.rsplit(".", 1)[-1] for _cell, path in levels[LEVEL_TWO].destroyed] == [
        "BP_MercerShrine_C_UAID_40B076DF2F7960BA01_1834450474",
        "BP_WAT73",
    ]
    for name in (LEVEL_ONE, LEVEL_TWO):
        assert {cell for cell, _path in levels[name].destroyed} == {name}


def test_the_persistent_level_writes_the_same_thing_grouped_by_cell(save):
    """``[i32 groups][str cell][i32 count][refs]`` -- a different shape, not a different list.

    Which shape a level uses is decided by the level and never announced: the persistent
    level's actors lived in world-partition cells it does not own, so it has to name them.
    The five here are looted drop pods and crashed ships.
    """
    persistent = save.levels[-1]
    assert persistent.name == "Persistent_Level"
    assert [path.rsplit(".", 1)[-1] for _cell, path in persistent.destroyed] == [
        "BP_DropPod3_1",
        "BP_DropPod3_8",
        "BP_Ship34",
        "BP_DropPod3_7",
        "BP_DropPod1_0",
    ]


def test_the_grouped_shape_walks_more_than_one_group(raw):
    """The reference save writes exactly one group there, so the loop is exercised here.

    Two groups are made by repeating the real one and rewriting the count -- the only
    synthesized bytes in this module, and worth it: a shape read correctly once tells you
    nothing about whether the reader advances to the next group or re-reads the first.
    """
    one = _grouped_blob(raw)
    assert struct.unpack_from("<i", one)[0] == 1, "the real list is a single group"
    doubled = struct.pack("<i", 2) + one[4:] + one[4:]
    refs = _read_destroyed_block(Reader(doubled), "P", len(doubled), grouped=True)
    assert len(refs) == 10
    assert refs[:5] == refs[5:]


def test_a_level_with_no_list_is_not_a_level_with_an_empty_one(save):
    """``0O2UIH8ZOBYWRN8PY7727SVBT``'s header block ends on its last header, with no count
    at all -- so ``_read_destroyed_block`` is not called for it. The other levels spend four
    bytes on a zero. Treating "no bytes left" as "read a count" would run into the object
    block's own size field, and the two cases are four bytes apart."""
    empty = next(lv for lv in save.levels if lv.name == LEVEL_EMPTY)
    assert empty.toc_extra_bytes == 0
    assert empty.destroyed == []
    # The others' lists cost exactly what the block had left over.
    assert [lv.toc_extra_bytes for lv in save.levels] == [83, 200, 0, 385]
    assert save.skipped_toc_bytes == sum(lv.toc_extra_bytes for lv in save.levels)


# ------------------------------------------------------------ the landing check


def test_reading_the_grouped_shape_as_a_bare_list_is_caught_by_where_it_lands(raw):
    """THE verification argument, as a test.

    Neither shape fails on a field when read as the other. Reading the persistent level's
    385-byte grouped list as a bare one succeeds -- it returns one reference, built out of
    the group count and the cell name -- and stops 351 bytes early. Nothing inside the bytes
    says that is wrong. The block's declared end does.
    """
    grouped = _grouped_blob(raw)
    reader = Reader(grouped)
    misread = _read_destroyed_refs(reader, "as if bare", len(grouped))
    assert len(misread) == 1, "the wrong shape does not fail, which is the point"
    assert reader.pos == 34 and reader.pos != len(grouped)


@pytest.mark.parametrize("count", [1, 3])
def test_a_list_that_ends_anywhere_but_the_blocks_end_is_refused(raw, count):
    """One reference too few or too many, which is what a shape error looks like.

    Both directions have to raise. Too few leaves bytes in the block that the walk would
    then read as the object block's size field; too many reads the object block's fields as
    strings. Neither is detectable from the list's own contents -- 3 references still pass
    the 8-bytes-each plausibility bound -- so the block's end is the only witness.
    """
    at = _toc_at(raw, LEVEL_TWO)
    broken = bytearray(raw)
    broken[at + 12 : at + 16] = struct.pack("<i", count)
    with pytest.raises(ParseError, match="The list's shape is wrong, not its length"):
        read_body(bytes(broken))


def test_a_header_block_that_is_four_bytes_too_long_is_refused(raw):
    """The same check from the other side: the list is right and the block size is not.

    Worth its own test because this is what a format change would look like -- one more field
    after the list -- and the walk must stop and say so rather than absorb the difference by
    reading the next structure four bytes late.
    """
    at = _toc_at(raw, LEVEL_TWO)
    broken = bytearray(raw)
    broken[at : at + 8] = struct.pack("<q", struct.unpack_from("<q", raw, at)[0] + 4)
    with pytest.raises(ParseError, match="its destroyed-actor list ended at 997"):
        read_body(bytes(broken))


# ---------------------------------------------------------------- failing loudly


def test_a_truncated_list_is_refused_rather_than_returned_short(raw):
    """An autosave read while the game is writing it is the routine case.

    A count of 2 with one and a half references after it must raise. It cannot be caught by
    the count -- 2 references need at least 16 bytes and there are 100 -- so it is caught
    where the reference's string runs off the end.
    """
    at = _toc_at(raw, LEVEL_TWO)
    blob = raw[at + 12 : at + 12 + 200]
    assert len(_read_destroyed_refs(Reader(blob), "whole", len(blob))) == 2
    for cut in (40, 100, 199):
        short = blob[:cut]
        with pytest.raises(ParseError, match="runs past end"):
            _read_destroyed_refs(Reader(short), "cut", len(short))


def test_an_absurd_count_is_refused_without_looping_over_it(raw):
    """A torn file makes a count arbitrary, and two billion references must be an error
    rather than twenty minutes of string reads. The bound is the smallest a reference can
    be -- two empty strings, 8 bytes -- checked against the bytes actually left in the
    block, so it needs no guess about what a plausible number of collectibles is."""
    at = _toc_at(raw, LEVEL_TWO)
    broken = bytearray(raw)
    broken[at + 12 : at + 16] = struct.pack("<i", 2_000_000_000)
    with pytest.raises(ParseError, match="2000000000 destroyed actors do not fit in the 196"):
        read_body(bytes(broken))


def test_an_absurd_group_count_is_refused_the_same_way(raw):
    """The grouped shape's outer count has no such arithmetic available -- a group is a cell
    name plus a count, and an empty group is 8 bytes -- so it is bounded separately. 100,000
    is far above the 1,289 cells any grid in this save declares and far below a number that
    would hang."""
    broken = bytearray(raw)
    at = raw.index(struct.pack("<i", 1) + struct.pack("<i", 17) + b"Persistent_Level\x00")
    broken[at : at + 4] = struct.pack("<i", 500_000)
    with pytest.raises(ParseError, match="claims 500000 cell groups"):
        read_body(bytes(broken))


# ------------------------------------------------------------------- the merge


def test_the_three_lists_are_merged_and_deduplicated(save):
    """Three lists, not one written three times, and no one of them is a superset.

    On the reference save: 854 actors in both the header blocks and the trailers, 5 only in
    the header blocks, 30 only in the trailers, and all 12 of the closing table's already
    somewhere else -- 889 distinct. This fixture reproduces every one of those relations in
    miniature, and the arithmetic below is the whole argument for why ``destroyed_actors``
    is a union: taking any single list would lose actors, and concatenating them would
    double-count.
    """
    header_block = [ref for lv in save.levels for ref in lv.destroyed]
    assert len(header_block) == 8  # 1 + 2 on sub-levels, 5 on the persistent level
    assert len(save.trailer_destroyed) == 4
    assert len(save.closing_destroyed) == 12

    merged = save.destroyed_actors
    assert len(merged) == 16
    assert len(merged) < len(header_block) + len(save.trailer_destroyed) + len(
        save.closing_destroyed
    ), "concatenating would double-count"
    assert set(merged) == set(header_block) | set(save.trailer_destroyed) | set(
        save.closing_destroyed
    )
    assert len(set(merged)) == len(merged), "the merge deduplicates"

    # Each list contributes something no other one has, so none may be dropped.
    trailer_only = set(save.trailer_destroyed) - set(header_block)
    assert {path.rsplit(".", 1)[-1] for _cell, path in trailer_only} == {"BP_Ship_C_5"}
    assert set(save.closing_destroyed) - set(header_block), "the closing table adds shrines"
    # And the closing table repeats the persistent level's five drop pods verbatim, which is
    # what there is to deduplicate.
    assert len(set(save.closing_destroyed) & set(header_block)) == 5


def test_the_merge_is_over_the_pair_not_the_path():
    """Two levels can record the same actor path, and both records are real.

    The key is ``(cell, path)``. Deduplicating on the path alone would silently drop one of
    them; deduplicating on nothing would report a slug twice. Built here rather than taken
    from the fixture because no save on this disk happens to contain the collision, and the
    behaviour has to be defined before one does.
    """
    path = "Persistent_Level:PersistentLevel.BP_Crystal_C_1"
    body = SaveBody(
        preamble=None,
        levels=[
            Level(name="A", headers=[], objects=[], destroyed=[("A", path), ("B", path)]),
            Level(name="B", headers=[], objects=[], destroyed=[("B", path)]),
        ],
        trailer_destroyed=[("A", path)],
        closing_destroyed=[("B", path), ("C", path)],
    )
    assert body.destroyed_actors == [("A", path), ("B", path), ("C", path)]


def test_the_merge_keeps_first_seen_order():
    """Not a fact about the format -- a fact about this function, and one the sidecar relies
    on being stable. ``_removed`` sorts what it gets, so the order here only has to be
    deterministic; a set would not be, and the projection would stop being byte-comparable
    between the two parsers."""
    body = SaveBody(
        preamble=None,
        levels=[Level(name="A", headers=[], objects=[], destroyed=[("A", "x"), ("A", "y")])],
        trailer_destroyed=[("A", "y"), ("A", "z")],
        closing_destroyed=[("A", "w")],
    )
    assert body.destroyed_actors == [("A", "x"), ("A", "y"), ("A", "z"), ("A", "w")]
