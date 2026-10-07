"""The four pre-1.0 save layouts: saveHeaderType 8, 9 and 10, saveVersion 25 to 36.

35 of the 66 saves on the development machine are one player's 2021-2023 history and were
refused by every parser this project has had. They are readable now, and they are readable as
**the same walk with version-gated fields** rather than as a second parser -- see
``savparse/versions.py`` for the thresholds. These tests are what keeps that true: a
regression that re-forks the walk, or that widens a field back to its modern size, fails here.

What each committed fixture is, and all of it is real bytes:

* ``save_header_v25/v28/v30/v36.bin`` -- 512-byte file prefixes, one per old
  ``saveHeaderType``/``saveVersion`` pair. Enough to cover the whole header, the first chunk's
  48-byte preamble and the start of its zlib blob.
* ``save_body_v25.bin`` -- 52,185 bytes, a **complete** saveVersion 25 body: the flat header
  run, the flat object run and the real closing destroyed-actor list of 48 refs. 30 objects
  over 20 classes, actors and components, including a belt that carries its own trailing bytes
  and all three ``root_object`` values that occur on this disk. Only the two counts are
  synthesised, because those frame a smaller pick of real records.
* ``save_body_v36.bin`` -- 19,969 bytes, a complete saveVersion 36 body: three real named level
  records, the unnamed persistent record, and the two lists that close it. Their
  destroyed-actor lists are kept whole -- 6, 13 and 21 refs in the header blocks and 40 in the
  trailers -- and only the body size, the level count and each record's two block sizes and two
  counts are synthesised.
* ``save_properties_ue4.bin`` -- 18,922 bytes, 22 real property blocks, one per changed code
  path at each of the four old save versions: a float32 ``Vector``, ``Quat`` and ``Box``, an
  ``InventoryItem`` whose second reference is populated with a real ``Equip_*_C`` actor, an
  ordinary inventory whose second reference is empty, and the foliage set of unnamed structs.
  Same container format as ``save_properties.bin`` plus one int32 for the save version.

**The referee, everywhere here, is landing.** Nothing in this format announces its own layout,
and there is **no second opinion**: the vendored GPL parser refuses all 35 of these files too,
so every claim below was derived from the bytes and cross-checked only against other bytes.
What makes that safe is that a wrong field list does not produce wrong numbers -- it fails on a
declared length. A header walk must end exactly on ``PACKAGE_FILE_TAG``; a body walk must
consume its last byte; a property must land exactly on its declared size.
"""

from __future__ import annotations

import itertools
import struct
import zlib

import pytest

from pioneersav import (
    CHUNK_TAG,
    FIRST_LEVEL_LIST,
    FIRST_MODERN_BODY,
    OLD_CHUNK_TAG,
    ActorHeader,
    ComponentHeader,
    ObjectSlice,
    ParseError,
    Reader,
    decompress_body,
    read_body,
    read_full_save_bytes,
    read_info_bytes,
    read_object,
)
from pioneersav.chunks import OLD_PREAMBLE_BYTES, PREAMBLE_BYTES
from pioneersav.header import PACKAGE_FILE_TAG
from pioneersav.save import UNDECODED_TRAILER_CLASSES
from tests.support.paths import committed_fixture

#: The measured tag offset for each old layout, over all 35 files: 12 saves land on 146, 9 on
#: 159 and 14 on 186. Unlike saveHeaderType 14 -- whose ``save_name`` is variable-length and
#: which ends at 283, 297, 450, 453 or 457 depending on the file -- each old type has exactly
#: ONE value, and that is the finding rather than an incidental detail.
TAG_OFFSET = {25: 146, 28: 159, 30: 186, 36: 186}

#: The chunk size the game writes, unchanged from 2021 to 2026.
MAX_CHUNK = 131072

OLD_VERSIONS = (25, 28, 30, 36)


def header_fixture(version: int) -> bytes:
    return committed_fixture(f"save_header_v{version}.bin").read_bytes()


def body_fixture(version: int) -> bytes:
    return committed_fixture(f"save_body_v{version}.bin").read_bytes()


@pytest.fixture(scope="module")
def body_v25() -> bytes:
    return body_fixture(25)


@pytest.fixture(scope="module")
def body_v36() -> bytes:
    return body_fixture(36)


@pytest.fixture(scope="module")
def ue4_blocks() -> dict[str, tuple[bytes, ObjectSlice, bool, int]]:
    """Every UE4 property block, keyed by name: ``(raw, slot, is_actor, save_version)``."""
    raw = committed_fixture("save_properties_ue4.bin").read_bytes()
    r = Reader(raw)
    out = {}
    for _ in range(r.i32()):
        name = r.string()
        version, save_version, is_actor, length = r.i32(), r.i32(), r.i32(), r.i32()
        out[name] = (raw, ObjectSlice(version, 0, r.pos, length), bool(is_actor), save_version)
        r.pos += length
    return out


def parse_ue4(blocks, name):
    raw, slot, actor, save_version = blocks[name]
    return read_object(raw, slot, actor=actor, save_version=save_version)


def props(parsed) -> dict:
    return {p[0]: p[1] for p in parsed.properties}


# ============================================================ the header layouts


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_each_old_header_ends_exactly_on_the_compressed_body(version):
    """The one referee this layout has, and the only reason the field list can be believed.

    Every field is positional, so a wrong width anywhere lands here at the wrong byte -- and
    with no oracle for these files (the vendored parser refuses them too), landing on a 4-byte
    tag at a **predicted** offset is the whole argument. The offsets are asserted as constants
    rather than read back from the walk, so a change that moves a field AND moves the
    expectation cannot pass quietly.
    """
    raw = header_fixture(version)
    info = read_info_bytes(raw)
    assert info.save_version == version
    assert info.body_offset == TAG_OFFSET[version]
    assert Reader(raw, info.body_offset).u32() == PACKAGE_FILE_TAG


@pytest.mark.parametrize(("version", "header_type"), [(25, 8), (28, 9), (30, 10), (36, 10)])
def test_the_old_header_types_are_two_field_lists_and_not_four(version, header_type):
    """saveHeaderType 8 and 9 are the SAME field list; 10 adds exactly one field.

    That is what lets one walk cover all four, and it is measured rather than assumed: the
    146 -> 159 step between types 8 and 9 is entirely string growth (``map_options`` gains
    ``?skiponboarding`` and swaps ``SV_FriendsOnly`` for ``SV_Private``, +12 net, and
    ``session_name`` grows by 1), and 159 -> 186 is the 27 bytes of ``save_identifier``. If
    someone later decides type 9 must have a field of its own, this is what says the tag
    offsets leave no room for one.
    """
    info = read_info_bytes(header_fixture(version))
    assert info.save_header_type == header_type
    assert info.map_name == "Persistent_Level"
    has_identifier = header_type >= 10
    assert bool(info.save_identifier) is has_identifier
    if has_identifier:
        # The same world identity the 31 modern saves carry, across four years of play and
        # three header versions. Nothing but a real field could agree with the modern one.
        assert info.save_identifier == "X2faPVKjX06VaRzClNv5KQ"
    assert TAG_OFFSET[version] == 159 + (27 if has_identifier else 0) - (
        13 if header_type == 8 else 0
    )


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_an_old_header_has_no_save_name_and_says_so_with_an_empty_string(version):
    """``save_name`` arrives at saveHeaderType 14, and its absence here is MEASURED.

    Not inferred from the byte budget: all 12 saveHeaderType 8 saves land on offset 146 while
    their file names run from ``Vanilla_autosave_0`` (18 characters) to
    ``Vanilla_autosave_0-04.21.21-00.08.33`` (36). An 18-character spread against a constant
    tag offset means the field is not in the file.

    It is ``""`` and not the file name, because filling it in from the path would make a header
    report something the header does not contain -- and ``read_info_bytes`` has no path anyway.
    """
    assert read_info_bytes(header_fixture(version)).save_name == ""


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_the_fields_an_old_header_lacks_read_as_None_rather_than_as_zero(version):
    """ "Absent" must not be confusable with "present and false".

    ``save_data_hash`` and ``is_creative`` are type-14 fields; the walk lands on the tag with no
    budget for them, so they cannot be in an old header. Returning ``(0, 0)`` and ``False``
    would have said "this save has a zero hash and creative mode off", which is a claim about
    the world. ``None`` says the file was never asked.
    """
    info = read_info_bytes(header_fixture(version))
    assert info.save_data_hash is None
    assert info.is_creative is None
    assert info.isCreativeModeEnabled is None


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_session_visibility_agrees_with_what_map_options_spells_out(version):
    """The strongest result in the old header, because it is checked against a *sibling field*.

    Every other field here is pinned only by position. This one is pinned by meaning too:
    ``map_options`` writes the visibility as text, and the byte agrees with it 35 times out of
    35 with no exception -- ``(0, 'SV_Private')`` on 23 saves and ``(1, 'SV_FriendsOnly')`` on
    12. A single off-by-one in any earlier field would break the correlation.
    """
    info = read_info_bytes(header_fixture(version))
    spelled = "SV_FriendsOnly" if info.session_visibility else "SV_Private"
    assert spelled in info.map_options
    assert info.session_visibility in (0, 1)
    # And the session name is in there too, which is a second independent anchor on the two
    # strings around it: 35 of 35.
    assert f"?sessionName={info.session_name}" in info.map_options


def test_an_unknown_save_header_type_is_refused_with_its_value_and_the_known_ones():
    """A type whose field list this has never seen must fail loudly, naming both.

    The verdict is the tag, not a whitelist, and that is a decision worth stating rather than
    implying. Refusing every unrecognised type would lose a save on the day the game bumps the
    type without moving a field -- which is exactly what happened between saveHeaderType 8 and
    9, where nothing in the header moved at all. So an unknown type is walked as the nearest
    layout at or below it and then judged: land on ``PACKAGE_FILE_TAG`` at the predicted offset
    and the field list was right, provably; land anywhere else and the file is refused.

    That is not the lightweight blob's version 2/4 mistake, and the difference is the referee. A
    blob version has none, so reading 2 as 4 desynchronised 25 saves silently. Here a wrong
    field list has to hit a 4-byte constant at an exact offset to pass.
    """
    for wrong in (14, 99):  # both take the modern layout, which is 35 bytes too long here
        raw = bytearray(header_fixture(36))
        raw[0:4] = struct.pack("<i", wrong)
        with pytest.raises(ParseError) as exc:
            read_info_bytes(bytes(raw))
        assert f"saveHeaderType {wrong}" in str(exc.value)
        assert "saveVersion 36" in str(exc.value)
    assert "known: 8, 9, 10, 14" in str(exc.value), "the message has to say what IS derived"

    # The other direction, and the reason a whitelist was not used: type 12 is unknown, reads
    # the type-10 field list, and lands exactly on the tag -- so it is accepted, deliberately.
    # If that ever stops being the intent, this is the line that has to change first.
    accepted = bytearray(header_fixture(36))
    accepted[0:4] = struct.pack("<i", 12)
    info = read_info_bytes(bytes(accepted))
    assert info.save_header_type == 12
    assert info.body_offset == TAG_OFFSET[36]


# ============================================================ the 48-byte preamble


def old_chunk(payload: bytes, *, tag: int = OLD_CHUNK_TAG, lie: bool = False) -> bytes:
    """One pre-1.0 chunk: six int64s and a zlib blob, with no compressor byte.

    Both size copies are written, because the old preamble keeps the duplicated pair the modern
    one does -- 48 bytes is 49 minus exactly that one byte, not minus a whole size pair.
    """
    blob = zlib.compress(payload)
    second_plain = len(payload) + 1 if lie else len(payload)
    return (
        struct.pack("<qqqqqq", tag, MAX_CHUNK, len(blob), len(payload), len(blob), second_plain)
        + blob
    )


def old_chunk_stream(body: bytes) -> bytes:
    return b"".join(old_chunk(body[at : at + MAX_CHUNK]) for at in range(0, len(body), MAX_CHUNK))


def test_the_old_preamble_is_48_bytes_and_the_difference_is_one_field():
    """49 minus the compressor byte, and that arithmetic is the claim being pinned.

    A reading that dropped a whole size *pair* instead would also be shorter and would also
    inflate the first chunk, because the pair is redundant -- and it would then fail on the
    second chunk. Asserting the constant here means a refactor cannot quietly re-derive it.
    """
    assert PREAMBLE_BYTES - OLD_PREAMBLE_BYTES == 1
    assert OLD_PREAMBLE_BYTES == 6 * 8
    payload = b"foundation" * 900
    assert decompress_body(old_chunk(payload), 0, old=True) == payload


def test_the_old_chunks_concatenate_into_one_stream():
    a, b = b"belt" * 700, b"pipe" * 500
    assert decompress_body(old_chunk(a) + old_chunk(b), 0, old=True) == a + b


def test_the_old_preambles_duplicated_sizes_are_still_checked():
    """The old preamble keeps the only integrity check this format has; using one copy and
    ignoring the other would throw it away on exactly the files with no oracle."""
    with pytest.raises(ParseError, match="disagrees with itself"):
        decompress_body(old_chunk(b"x" * 100, lie=True), 0, old=True)


def test_the_two_preambles_are_not_interchangeable():
    """Each framing must refuse the other's bytes, or ``old=`` could be wrong and unnoticed.

    Read a 48-byte chunk with the modern walk and the tag's high half is the low half of the
    max-chunk-size field, so the tag check fails at once. The other direction is the one that
    matters more, because it *nearly* works: the modern preamble's compressor byte slides into
    the sizes and the first chunk can still inflate, so the failure has to be caught -- and it
    is, on the next chunk's tag.
    """
    old = old_chunk(b"q" * 200)
    with pytest.raises(ParseError, match="expected 0x222222229e2a83c1"):
        decompress_body(old, 0, old=False)

    payload = b"z" * 200
    blob = zlib.compress(payload)
    modern = (
        struct.pack(
            "<qqBqqqq", CHUNK_TAG, MAX_CHUNK, 3, len(blob), len(payload), len(blob), len(payload)
        )
        + blob
    )
    with pytest.raises(ParseError):
        decompress_body(modern + modern, 0, old=True)


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_the_real_old_preamble_matches_the_derived_one_field_for_field(version):
    """Six int64s off a real 2021-2023 save, checked one at a time.

    The hand-built chunk above proves the reader round-trips its own writer, which is not the
    same as proving the writer matches the game. This reads the game's own bytes: the tag with
    a **zero high half** (the one field that differs from the modern preamble beyond the missing
    byte), the same 131072 maximum the game still writes today, both size copies equal, and the
    zlib header immediately after -- so there is no room for a compressor byte.
    """
    raw = header_fixture(version)
    at = read_info_bytes(raw).body_offset
    r = Reader(raw, at)
    tag, max_plain = r.i64(), r.i64()
    first, second = (r.i64(), r.i64()), (r.i64(), r.i64())
    assert tag == OLD_CHUNK_TAG
    assert tag >> 32 == 0, "the high half is 0x22222222 only from saveVersion 52"
    assert max_plain == MAX_CHUNK
    assert first == second
    assert 0 < first[0] <= max_plain and first[1] == MAX_CHUNK
    assert r.pos == at + OLD_PREAMBLE_BYTES
    assert r.bytes(2) == b"\x78\x9c", "plain zlib starts where the compressor byte would be"


# ============================================================ the body, shape A


def test_a_body_written_before_the_level_list_has_no_level_list(body_v25):
    """saveVersion 25 and 28: two flat runs and a closing list, and that is the whole body.

    No grid table, no archive version header, no level count and no block sizes. A parser that
    read a level count here would take the header count -- 25,385 on the save this was cut from
    -- as the number of levels and then read a class path as a level name.
    """
    assert 25 < FIRST_LEVEL_LIST
    save = read_body(body_v25, 25)
    assert save.preamble.grids == []
    assert save.preamble.has_archive_header is False
    assert save.preamble.declared_size == len(body_v25) - 4, "an int32 size, not an int64"


def test_the_old_body_size_is_an_int32(body_v25):
    """``len(body) - 4``, where saveVersion 52 writes ``len(body) - 8``.

    Cheap, and the first thing that catches a chunk stream that inflated to the wrong length --
    the per-chunk sizes all agree, so nothing below notices a last chunk that was never
    written. Reading it as an int64 would make the check pass only by coincidence.
    """
    with pytest.raises(ParseError, match="the body says it is"):
        read_body(struct.pack("<i", len(body_v25)) + body_v25[4:], 25)
    # And the modern width applied to an old body fails too, rather than reading a plausible
    # size out of the first two fields.
    with pytest.raises(ParseError):
        read_body(body_v25, FIRST_MODERN_BODY)


def test_a_flat_body_is_grouped_by_the_level_each_header_names(body_v25):
    """The file says nothing about levels; every header says which one it is in.

    Three ``root_object`` values occur across the 21 saves at this layout, and all three are in
    this fixture. Grouping by that field is the only reading under which every level has the
    name the bytes give it -- one flat level would have to be *called* something, and calling it
    ``Persistent_Level`` would be wrong for the objects rooted in the two exploration levels.

    ``header[i]`` must still describe ``object[i]`` inside each group, which is what the zip
    below asserts: grouping that shuffled one list and not the other would attach every
    inventory to the wrong machine and produce a complete, plausible, wrong factory.
    """
    save = read_body(body_v25, 25)
    names = [lv.name for lv in save.levels]
    assert names == ["Persistent_Level", "Persistent_Exploration", "Persistent_Exploration_2"]
    for level in save.levels:
        assert level.headers, "an empty group cannot occur -- groups come FROM the headers"
        assert len(level.headers) == len(level.objects)
        for header in level.headers:
            assert header.root_object == level.name


def test_an_old_object_header_has_no_object_flags_word(body_v25):
    """The ``EObjectFlags`` word arrives at saveVersion 52; below it the transform follows the
    instance name directly.

    Reading four bytes anyway put a quaternion component into ``need_transform`` and shifted
    every float by one slot. The values that catch it are the ones the game constrains, and they
    are asserted here rather than the absence alone: a unit quaternion, 0/1 booleans, and a
    scale that is not nonsense. Over all 35 saves that is 596,799 actors and **zero** non-unit
    quaternions.
    """
    save = read_body(body_v25, 25)
    actors = [h for lv in save.levels for h in lv.headers if isinstance(h, ActorHeader)]
    components = [h for lv in save.levels for h in lv.headers if isinstance(h, ComponentHeader)]
    assert actors and components, "the fixture was cut to hold both kinds"
    for header in actors + components:
        assert header.object_flags is None
    for actor in actors:
        assert actor.need_transform in (0, 1)
        assert actor.was_placed_in_level in (0, 1)
        assert abs(sum(c * c for c in actor.rotation) - 1.0) < 1e-4
        assert all(abs(c) < 1e6 for c in actor.position)
    for component in components:
        assert component.parent_actor_name


def test_an_old_object_entry_is_a_bare_size_so_the_version_comes_from_the_save(body_v25):
    """There is no version int32 and no flag int32 in the entry below saveVersion 52.

    Which means the object's serialisation version is **not in the file** -- the one place this
    parser supplies a version rather than reading one, and the reason ``read_body`` takes
    ``save_version`` at all. If a refactor ever tries to sniff it from the bytes instead, this
    is the test that says there is nothing there to sniff.
    """
    save = read_body(body_v25, 25)
    slots = [slot for lv in save.levels for slot in lv.objects]
    assert {slot.version for slot in slots} == {25}
    assert {slot.flag for slot in slots} == {0}
    # Consecutive entries are 4 bytes of size plus the payload, with nothing between them.
    ordered = sorted(slots, key=lambda s: s.offset)
    for previous, following in itertools.pairwise(ordered):
        assert following.offset == previous.end + 4


def test_an_absurd_header_count_in_a_flat_body_is_bounded_by_the_bytes_left(body_v25):
    """The flat header run has no enclosing block, so the count needs its own bound.

    Every other count in this parser is bounded by the length-prefixed block it lives in. This
    one is not -- there is no block below saveVersion 30 -- and a flat ceiling is not a check:
    the same mistake on a container's element count made a torn file read 36 MB of the following
    objects and take 13.4 s before the size check noticed. An object header is an int32 kind plus
    three length-prefixed strings, so 16 bytes is a real floor and the bound is exact enough.
    """
    broken = bytearray(body_v25)
    broken[4:8] = struct.pack("<i", 9_000_000)
    with pytest.raises(ParseError, match="object headers with .* bytes left"):
        read_body(bytes(broken), 25)


def test_a_flat_body_closes_with_one_bare_destroyed_actor_list(body_v25):
    """Exactly one list lands on the last byte -- not zero, and not two.

    The closing table is the only structure that can prove the whole walk consumed the file, and
    on this layout its shape had to be established by counting: one bare ``[i32 count][refs]``
    ends every one of the 21 shape-A bodies exactly, two runs off the end, and none leaves
    bytes over. 45 to 53 refs on a saveVersion 25 save.
    """
    save = read_body(body_v25, 25)
    assert len(save.closing_destroyed) == 48
    assert save.trailer_destroyed == [], "there are no level trailers without levels"
    assert save.warnings == [], "a leftover byte after the closing list would show up here"
    for level_name, actor_path in save.closing_destroyed:
        assert level_name.startswith("Persistent_")
        assert ":PersistentLevel." in actor_path


# ============================================================ the body, shape B


def test_a_named_level_body_walks_its_levels_and_consumes_its_last_byte(body_v36):
    """saveVersion 30 and 36: a level count, named records with **int32** block sizes.

    That the sizes are int32 is excluded from the other side rather than merely preferred: on a
    174-byte TOC the four bytes after the size are the header count ``1``, so an int64 reading
    would have to accept ``174 + (1 << 32)`` as a block length.
    """
    save = read_body(body_v36, 36)
    assert save.preamble.grids == [], "no world partition below saveVersion 52"
    assert save.preamble.declared_size == len(body_v36) - 4
    assert [lv.name for lv in save.levels][-1] == "Persistent_Level"
    assert len(save.levels) == 4
    assert save.object_count == 32
    assert save.warnings == []
    for level in save.levels[:-1]:
        assert level.name.startswith("Level /Game/FactoryGame/Map/GameLevel01/Tile_")
    for level in save.levels:
        assert len(level.headers) == len(level.objects)


def test_the_old_persistent_level_writes_an_UNGROUPED_destroyed_list(body_v36):
    """From saveVersion 52 the persistent level groups its destroyed actors by partition cell.
    Below it there is no world partition, so it writes the same bare list a sub-level does.

    Reading it as grouped turns the count into a string length, and the landing check on the
    header block's declared end is what catches that -- which is why this is a measurement and
    not a preference.
    """
    save = read_body(body_v36, 36)
    persistent = save.levels[-1]
    assert persistent.name == "Persistent_Level"
    assert persistent.toc_extra_bytes == 4, "an empty bare list is 4 bytes; a grouped one is not"
    assert persistent.destroyed == []


def test_both_of_the_old_destroyed_lists_are_read_and_merged(body_v36):
    """A level's header block and its trailer are two different lists and both must be read.

    Over the 14 saves at this layout they hold the same set on 12 and differ on 2, so picking
    either one alone loses actors on some saves -- and these lists are the only record of what
    the player has collected, since the map's slugs and spheres are never written into a save at
    all. Merging is deduplicated by ``(cell, path)``.
    """
    save = read_body(body_v36, 36)
    per_level = {lv.name: len(lv.destroyed) for lv in save.levels}
    assert sorted(per_level.values()) == [0, 6, 13, 21]
    assert len(save.trailer_destroyed) == 40
    assert len(save.closing_destroyed) == 5
    merged = save.destroyed_actors
    assert len(merged) == len(set(merged)), "the merge deduplicates"
    assert set(merged) >= {ref for lv in save.levels for ref in lv.destroyed}
    assert set(merged) >= set(save.trailer_destroyed) | set(save.closing_destroyed)


def test_a_named_level_body_closes_with_two_bare_lists(body_v36):
    """``[i32 0][i32 5][5 refs]`` -- and which of the two readings this is cannot be settled.

    Read here as *the unnamed persistent record carrying a trailer like every other level, then
    one closing list*, which makes both old shapes close identically. It reads equally well as
    two ungrouped closing lists, and the first list is empty on all 14 saves, so no value
    depends on the choice. What IS measured is the count: exactly two bare lists land on the
    last byte, one leaves bytes over and three run off the end.
    """
    save = read_body(body_v36, 36)
    assert len(save.closing_destroyed) == 5
    assert save.warnings == []

    # And a third list has nowhere to come from: appending an empty one and re-declaring the
    # size leaves the walk finishing four bytes early, which is what `warnings` is for. Stated
    # as the count landing rather than as a raise, because reading too FEW lists is the failure
    # mode that does not raise -- it silently leaves the collectible record short.
    longer = body_v36[4:] + b"\x00\x00\x00\x00"
    grown = read_body(struct.pack("<i", len(longer)) + longer, 36)
    assert grown.warnings, "a fourth list's worth of bytes must not be absorbed silently"
    assert "after the closing destroyed-actor list" in grown.warnings[0][1]


# ============================================================ the UE4 property layouts


def test_every_ue4_property_block_is_consumed_without_a_warning(ue4_blocks):
    """22 real blocks across all four old save versions, each landing exactly on its size.

    The declared size is the only thing in this format that catches a wrong width, and a clean
    pass over all 22 means every reader agrees with bytes the game wrote in 2021-2023. Zero
    warnings means nothing was skipped: a struct dropped from the table would move this from 0
    to nonzero rather than failing, which a smoke test would not notice.
    """
    assert len(ue4_blocks) == 22
    for name in ue4_blocks:
        parsed = parse_ue4(ue4_blocks, name)
        assert parsed.warnings == [], name
        assert parsed.properties, name


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_a_ue4_box_is_25_bytes_because_its_vectors_are_float32(ue4_blocks, version):
    """UE5 widened ``FVector`` from float to double; a UE4 save writes the narrow one.

    ``mLevelBounds`` declares **25** bytes here -- six float32s and the validity byte -- against
    49 on a modern save. Over the 35 files that is 16,595 occurrences the double reading
    over-read by exactly 24 each. The values are checked, not just the width: a foliage cell's
    bounds are a box with min below max on every axis, which a float32 sequence read as float64
    cannot produce.
    """
    parsed = parse_ue4(ue4_blocks, f"box_v{version}")
    bounds = props(parsed).get("mLevelBounds")
    assert bounds is not None and len(bounds) == 7
    assert bounds[-1] is True, "the validity byte"
    for low, high in zip(bounds[0:3], bounds[3:6], strict=True):
        assert low < high
        assert abs(low) < 1e6 and abs(high) < 1e6


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_a_ue4_quat_is_four_float32s(ue4_blocks, version):
    """16 bytes, not 32. Pinned by the value the game constrains: a rotation is a UNIT
    quaternion, and four float32s misread as two float64s are not."""
    parsed = parse_ue4(ue4_blocks, f"quat_v{version}")
    found = [
        value
        for _name, value in _walk_values(parsed.properties)
        if isinstance(value, list) and len(value) == 4 and all(isinstance(x, float) for x in value)
    ]
    units = [v for v in found if abs(sum(c * c for c in v) - 1.0) < 1e-4]
    assert units, f"no unit quaternion in the v{version} block"


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_a_ue4_vector_is_three_float32s_and_lands_in_the_world(ue4_blocks, version):
    """12 bytes, not 24 -- 89,000 occurrences of ``Location``/``SpawnLocation``/``Translation``
    across the 35 files, every one of which the double reading over-read by exactly 12.

    The corroboration is the coordinate: the map is roughly +-400,000 uu across, and a float32
    triple read as a float64 pair lands nowhere near it.
    """
    parsed = parse_ue4(ue4_blocks, f"vector_v{version}")
    triples = [
        value
        for _name, value in _walk_values(parsed.properties)
        if isinstance(value, list) and len(value) == 3 and all(isinstance(x, float) for x in value)
    ]
    assert triples, f"no vector in the v{version} block"
    assert any(all(abs(c) < 500_000 for c in t) for t in triples)


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_the_save_version_and_not_the_object_version_picks_the_float_width(ue4_blocks, version):
    """Passing the modern save version at an old block fails on the size check.

    This is the direction that makes the choice a measurement. A version-36 object exists inside
    saveVersion-52 saves and **none of them reaches a vector**, so nothing on this disk says
    what width such an object would use -- keying on the object version would be a guess about
    them. Keying on the save is verified both ways: 31 saves read wide and 35 read narrow, and
    getting it backwards fails loudly here instead of returning coordinates in another
    hemisphere.
    """
    raw, slot, actor, _save_version = ue4_blocks[f"box_v{version}"]
    with pytest.raises(ParseError):
        read_object(raw, slot, actor=actor, save_version=FIRST_MODERN_BODY)


@pytest.mark.parametrize("version", OLD_VERSIONS)
def test_an_old_inventory_item_is_two_object_references(ue4_blocks, version):
    """``FInventoryItem`` below object version 52 is the descriptor, then an equipment ACTOR.

    Not the modern ``i32 has_state`` plus an optional nested state list. The shipped reader's
    "version 36 items write a second int32 here, 0 on every one of them" was those two
    references' two zero length prefixes read as two integers -- byte-equivalent whenever the
    reference is empty, which is why the 1,092 version-36 items in the 31 modern saves have
    always parsed, and wrong on the 177 populated ones, which occur only in these 35 files.

    Both cases are in the fixture: ``equip_*`` carries a real ``Equip_*_C`` path, and
    ``stacks_*`` carries the empty reference that reads back as ``None``. Without this test the
    populated case silently loses the whole struct's alignment and takes the rest of the object
    with it.
    """
    populated = _items(props(parse_ue4(ue4_blocks, f"equip_v{version}"))["mInventoryStacks"])
    assert populated, f"no inventory item at all in the v{version} equip block"
    equipped = [item for item in populated if item[1] is not None]
    assert equipped, f"no populated second reference in the v{version} block"
    for descriptor, actor_path in equipped:
        assert descriptor.startswith("/Game/FactoryGame/"), "element 0 is still the descriptor"
        assert actor_path.startswith("Persistent_Level:PersistentLevel.Equip_")

    # ...and an unpopulated one still reads as None, which is what the projection has always
    # seen and what makes the change byte- and value-neutral on the 31 modern saves.
    empty = _items(props(parse_ue4(ue4_blocks, f"stacks_v{version}"))["mInventoryStacks"])
    assert empty, f"no inventory item at all in the v{version} stacks block"
    assert all(item[1] is None for item in empty)


@pytest.mark.parametrize("version", (30, 36))
def test_the_foliage_set_of_unnamed_vectors_is_recovered_not_skipped(ue4_blocks, version):
    """``FGFoliageRemoval.mRemovalLocations`` was the last warning on the 35 files -- 844 of
    them, all this one field.

    UE4's tag data for a set names the element's *property* type and stops there, so the struct
    behind it is genuinely not in the file. ``Vector`` is a candidate rather than a guess
    because saveVersion 60 writes that type out in full for the same field, and the candidate is
    only kept when the walk lands exactly on the declared end. What comes out is real world
    coordinates.
    """
    parsed = parse_ue4(ue4_blocks, f"foliage_v{version}")
    assert parsed.warnings == []
    locations = props(parsed)["mRemovalLocations"]
    assert isinstance(locations, list) and locations
    for entry in _flatten_vectors(locations):
        assert len(entry) == 3
        assert all(abs(c) < 500_000 for c in entry)


def _items(stacks) -> list[list]:
    """Every ``Item`` value in an ``mInventoryStacks`` array, at whatever depth it sits.

    A stack is a struct whose fields are ``Item`` and ``NumItems``, and the struct arrives as
    ``[values, types]``, so the value is three lists deep. Selecting by field NAME rather than
    by the shape of the value is deliberate: an empty slot's descriptor is the empty string, and
    a shape-based filter that required a path would silently skip exactly the case this test
    needs -- which is what it did on the first attempt.
    """
    return [
        value
        for name, value in _walk_values(stacks)
        if name == "Item" and isinstance(value, list) and len(value) == 2
    ]


def _walk_values(value, name=""):
    """Every ``(name, value)`` at every depth, so a nested struct field can be asserted on."""
    yield name, value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str):
                yield from _walk_values(item[1], item[0])
            else:
                yield from _walk_values(item, name)


def _flatten_vectors(value):
    for _name, item in _walk_values(value):
        if isinstance(item, list) and len(item) == 3 and all(isinstance(x, float) for x in item):
            yield item


# ============================================================ the whole file


def whole_save(version: int) -> bytes:
    """A complete pre-1.0 .sav, assembled from the two fixtures for that save version.

    The header prefix and the body both come from the same real file, so the save version in the
    header is the one the body was written at -- which is the point: nothing else tells the body
    walk which layout it is looking at.
    """
    header = header_fixture(version)
    return header[: read_info_bytes(header).body_offset] + old_chunk_stream(body_fixture(version))


@pytest.mark.parametrize("version", (25, 36))
def test_a_whole_pre_1_0_save_reads_into_the_shape_the_projection_consumes(version):
    """End to end: header, 48-byte chunks, body walk, every property block.

    This is the seam test. Each layer is version-gated on one number read once, in
    ``read_full_save_bytes``, and a layer that stopped honouring it would fail here rather than
    on a machine that happens to have a 2021 save. ``extract.readers.iter_objects`` zips
    ``level.actorAndComponentObjectHeaders`` with ``level.objects`` and reads ``obj.properties``
    as ``[name, value]`` pairs, so all three names are asserted.
    """
    save = read_full_save_bytes(whole_save(version))
    assert save.info.save_version == version
    assert save.levels and save.object_count
    for level in save.levels:
        assert level.actorAndComponentObjectHeaders is level.headers
        for header, obj in zip(level.headers, level.objects, strict=True):
            assert getattr(header, "instanceName", None)
            assert obj.version == version, "the entry has no version; the save supplies it"
            for pair in obj.properties:
                assert isinstance(pair, list) and len(pair) == 2
                assert isinstance(pair[0], str)


@pytest.mark.parametrize("version", (25, 36))
def test_a_pre_1_0_save_produces_no_warnings_at_all(version):
    """Zero, on all 35 files. Which is the acceptance criterion, not a nicety.

    ``warnings`` is where this parser puts everything it skipped rather than understood, so a
    nonzero count on a file it claims to read means part of the world is missing. The three
    things that would break it: a struct whose width moved, a class leaving trailing bytes that
    nothing is known to leave, and a leftover byte after the closing list.
    """
    assert read_full_save_bytes(whole_save(version)).warnings == []


def test_a_pre_1_0_belt_carries_its_own_items_and_that_is_recorded_not_decoded():
    """There is no ``FGConveyorChainActor`` before 1.0: every belt holds its own items.

    50,532 actors and 39.5 MB across the 35 files. Nothing here decodes them, and the honest
    outcome is ``actorSpecificInfo is None`` -- "not decoded" -- rather than an empty list,
    which is what a decoded blob holding nothing looks like. A partial decode would turn an
    undercount into a silent one.

    The class list is also why the trailing-bytes check survives on these saves at all: without
    it every one of those 50,532 actors would warn, and 50,532 warnings hide the one that
    matters.
    """
    save = read_full_save_bytes(whole_save(25))
    belts = [
        (header, obj)
        for level in save.levels
        for header, obj in zip(level.headers, level.objects, strict=True)
        if "ConveyorBelt" in (getattr(header, "typePath", "") or "")
    ]
    assert belts, "the fixture was cut to include a belt"
    for header, obj in belts:
        assert header.typePath in UNDECODED_TRAILER_CLASSES
        assert obj.extra_length > 8, "a belt's items are in its trailing bytes"
        assert obj.decode_trailer is None, "no reader has been verified against 2021 bytes"
        assert obj.actorSpecificInfo is None, "None means not decoded, never 'decoded, empty'"


def test_no_modern_trailer_reader_is_attached_on_a_pre_1_0_save():
    """``Build_PowerLine_C`` has the same class path in 2021 as in 2026 and different bytes.

    So the modern reader must not be attached on an old save: it would be handed 2021 bytes and
    produce numbers nobody has checked, lazily, at whatever point a caller first looked. Nothing
    in the projection reads a power line's trailer today, which is exactly why this would go
    unnoticed without a test.
    """
    save = read_full_save_bytes(whole_save(36))
    for level in save.levels:
        for obj in level.objects:
            assert obj.decode_trailer is None
            assert obj.actorSpecificInfo is None


def test_an_unknown_class_leaving_bytes_on_an_old_save_is_warned_about():
    """The check the measured class list buys, and it has to be shown to still fire.

    A property list that stopped early looks exactly like an actor with class-specific bytes, and
    on these saves eleven classes legitimately have them. A twelfth -- a mod, a belt tier the
    player never unlocked, or a real desynchronisation -- must say so. Nothing on this disk does,
    so the header has to be doctored to make it happen at all.
    """
    from pioneersav.properties import ParsedObject
    from pioneersav.save import PLAIN_TRAILER, _attach_trailer

    header = ActorHeader(
        type_path="/Game/FactoryGame/Buildable/Factory/ConveyorBeltMk5/Build_ConveyorBeltMk5.Build_ConveyorBeltMk5_C",
        root_object="Persistent_Level",
        instance_name="Persistent_Level:PersistentLevel.Build_ConveyorBeltMk5_C_1",
        object_flags=None,
        need_transform=0,
        rotation=(0.0, 0.0, 0.0, 1.0),
        position=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        was_placed_in_level=0,
    )
    warnings: list[tuple[int, str]] = []
    obj = ParsedObject(version=25, extra_offset=99, extra_length=4_242)
    _attach_trailer(b"", header, obj, warnings, 25)
    assert obj.decode_trailer is None
    assert len(warnings) == 1
    at, what = warnings[0]
    assert at == 99
    assert "Build_ConveyorBeltMk5_C left 4242 trailing bytes" in what
    assert "saveVersion 25" in what

    for length in PLAIN_TRAILER:
        quiet: list[tuple[int, str]] = []
        _attach_trailer(b"", header, ParsedObject(version=25, extra_length=length), quiet, 25)
        assert quiet == [], f"{length} bytes is what an ordinary old actor leaves"
