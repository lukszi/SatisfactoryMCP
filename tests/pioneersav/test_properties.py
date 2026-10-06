"""Unreal's property serialiser: the values, the shapes, and the escape hatches.

Verified black-box against the vendored parser on all 31 saves it can read -- 1,243,288
objects, 2,269,824 properties, and **not one object whose property names or order differ**.
On the reference save, 82,660 properties compare identical on 79,752; the 2,908 that differ
are three explained classes (2,906 in the unread second element of an inventory ``Item``, one
``Guid`` spelling, one empty soft-object sub-path), none of which the projection reads.
Running ``extract``'s whole projection through this instead of the vendored parser gives
an identical result in every field except ``lightweight_counts`` and ``structures``, which
come from the trailing class-specific bytes this stage deliberately does not decode.

These tests run against committed real bytes, so the suite needs no game install and will
still pass once the vendored parser is deleted.

``fixtures/save_properties.bin``, 26,200 bytes, is 18 real property blocks lifted verbatim
from the reference save, chosen so that every distinct code path in ``properties.py`` is
exercised by bytes the game actually wrote:

* both tag layouts -- version 60's type-name tree and version 36/52's fixed tag data;
* every container: array of scalars, of references, of structs, of bytes; a map keyed by an
  object reference; a set of native structs;
* every native struct the table knows and both struct framings (raw numbers vs a nested
  property list), including the version-36/52 struct-array header that lives *inside* the
  payload;
* the awkward ones: an ``InventoryItem`` with weapon state, ``FText``, ``FSoftObjectPath``'s
  three strings, an enum ``ByteProperty`` next to a plain one, and a self-serialising struct
  with no known layout.

The container format is invented here and only this test reads it: an int32 count, then per
entry a name, the object version, an is-actor flag, a length, and that many payload bytes.

Two sections use hand-built bytes instead, and each says why where it starts: the escape
hatches, which no save on this disk reaches, and the version-36/52 containers whose element
struct the bytes never name, whose three occurrences are all in fields the projection does not
read -- so the parity digest cannot notice them breaking.

What would go wrong without these: the whole module is offset arithmetic over a
self-describing format, and the only thing that catches a wrong width is the declared size.
A regression that reads four bytes too few does not produce a wrong number -- it produces a
``ParseError`` on some unrelated property thousands of objects later, and these tests say
which reader broke.
"""

from __future__ import annotations

import struct

import pytest

from pioneersav import ObjectSlice, ParseError, Reader
from pioneersav.properties import (
    TAG_BOOL_TRUE,
    TAG_NATIVE_SERIALIZE,
    ObjectReference,
    read_object,
)
from tests.support.paths import FIXTURES

FIXTURE = FIXTURES / "save_properties.bin"
BODY_FIXTURE = FIXTURES / "save_body.bin"


@pytest.fixture(scope="module")
def blocks() -> dict[str, tuple[bytes, ObjectSlice, bool]]:
    """Every fixture entry, keyed by the object's short instance name."""
    if not FIXTURE.is_file():
        pytest.skip("property fixture not committed")
    raw = FIXTURE.read_bytes()
    r = Reader(raw)
    out = {}
    for _ in range(r.i32()):
        name = r.string()
        version, is_actor, length = r.i32(), r.i32(), r.i32()
        out[name] = (raw, ObjectSlice(version, 0, r.pos, length), bool(is_actor))
        r.pos += length
    return out


def parse(blocks, name):
    raw, slot, actor = blocks[name]
    return read_object(raw, slot, actor=actor)


def props(parsed) -> dict:
    """The same flattening ``extract.props`` does, so the tests read the same shape."""
    return {p[0]: p[1] for p in parsed.properties}


# --------------------------------- hand-built tags, for the paths no save reaches
#
# Everything above and below works on real bytes, which is the right default. Four cases
# cannot: they are the paths that no save on this disk contains, and that absence is exactly
# why they need pinning. A census over all 31 readable saves -- 1,243,288 objects, 2,269,824
# properties -- found map and set elements of only four types (``ObjectProperty``,
# ``StructProperty``, ``IntProperty``, ``Int64Property``), all of which have readers, and
# ``FText`` history 0xFF 576 times and nothing else. So the fallback that skips an untagged
# element, and the bound on a container's element count, have never run against a real save
# and no other test in this suite can tell whether they still work.


def _s(text: str) -> bytes:
    """A length-prefixed string, the count including the trailing null."""
    if text == "":
        return struct.pack("<i", 0)
    return struct.pack("<i", len(text) + 1) + text.encode("latin-1") + b"\0"


def _tree(name: str, *params: bytes) -> bytes:
    """One node of a version-60 type-name tree: a name, a count, then that many nodes."""
    return _s(name) + struct.pack("<i", len(params)) + b"".join(params)


def _tag(name: str, type_tree: bytes, payload: bytes, *, size: int | None = None) -> bytes:
    """A version-60 property tag with no flags set, and optionally a size that lies."""
    declared = len(payload) if size is None else size
    return _s(name) + type_tree + struct.pack("<i", declared) + b"\0" + payload


def _component(*tags: bytes) -> tuple[bytes, ObjectSlice]:
    """A whole version-60 component payload: migration byte, tags, ``"None"``, trailer."""
    payload = b"\0" + b"".join(tags) + _s("None") + b"\0\0\0\0"
    return payload, ObjectSlice(60, 0, 0, len(payload))


def _old_tag(name: str, type_name: str, tag_data: bytes, payload: bytes) -> bytes:
    """A version-36/52 tag: name, type, size, array index, type-specific data, guid flag.

    ``tag_data`` is what that type writes between the array index and the guid flag -- the
    element type for an array or a set, both element types for a map, nothing for a scalar.
    That field is the whole reason the tests below exist: it is where UE4 keeps the
    information version 60 puts in its type tree, and for a container of structs it keeps
    less of it.
    """
    return (
        _s(name) + _s(type_name) + struct.pack("<ii", len(payload), 0) + tag_data + b"\0" + payload
    )


def _old_component(*tags: bytes) -> tuple[bytes, ObjectSlice]:
    """A version-52 component payload. No migration byte -- that is version 60's."""
    payload = b"".join(tags) + _s("None") + b"\0\0\0\0"
    return payload, ObjectSlice(52, 0, 0, len(payload))


# ------------------------------------------------------------ every block parses


def test_every_block_is_consumed_without_a_warning(blocks):
    """The whole point of the size fields: each property lands exactly where it said.

    ``read_object`` raises the moment a property's payload does not match its declared
    length, so a clean pass over all 18 blocks means every reader in the module agrees with
    the bytes. Zero warnings means no struct or type was skipped -- if a refactor drops a
    native struct from the table this goes from 0 to nonzero rather than failing outright,
    which is exactly the failure a plain smoke test would miss.
    """
    for name in blocks:
        parsed = parse(blocks, name)
        assert parsed.warnings == [], f"{name}: {parsed.warnings}"
        assert parsed.properties, f"{name}: no properties decoded"


def test_trailing_bytes_are_reported_not_consumed(blocks):
    """A property list never eats the object's trailer, and never stops short of it.

    Trailing length is 4 or 8 on an ordinary object -- measured across the reference save as
    4 on 17,382 actors and 8 on 20,700 components -- so anything else here means the
    terminator was found in the wrong place. This is the check that would catch a reader
    that swallowed the ``"None"`` tag's four following bytes.
    """
    for name in blocks:
        parsed = parse(blocks, name)
        assert parsed.extra_length in (4, 8), f"{name}: {parsed.extra_length} trailing bytes"
        _, slot, _ = blocks[name]
        assert parsed.extra_offset + parsed.extra_length == slot.end


# ------------------------------------------------------------------- scalars


def test_strings_bytes_and_plain_enums(blocks):
    """``StrProperty``, ``NameProperty`` and a ByteProperty that is not an enum.

    ``mLastAutoSaveId`` is the case that makes ByteProperty two-shaped: it has no enum, so
    it reads ``[None, 2]`` -- rotation slot 2 of the game's three autosaves. A reader that
    always expected an enum name would read the raw byte as a string length and run off the
    end of the object.
    """
    p = props(parse(blocks, "BP_GameMode_C_2147477547"))
    assert p["mSaveSessionName"] == "Han Solo"
    assert p["mStartingPointTagName"] == "Northern Forest"
    assert p["mLastAutoSaveId"] == [None, 2]


def test_enum_property_carries_its_enum_and_its_value(blocks):
    """``EnumProperty`` on the version-52 tag layout, where the enum name is tag data.

    The rock is destroyed, and the value keeps the ``EnumName::Value`` spelling the game
    writes rather than being split, because ``_phase_costs`` and friends do the splitting.
    """
    p = props(parse(blocks, "BP_DestructibleLargeRock_C_UAID_40B076DF2F793BDF01_1196764038"))
    assert p["mDestructibleActorState"] == [
        "EDestructibleActorState",
        "EDestructibleActorState::DSS_Destroyed",
    ]


def test_int64_and_arrays_of_scalars(blocks):
    """Arrays hold no per-element tag, so the element width comes from the array's type.

    ``mTotalPoints`` is two int64s and ``mCurrentPointLevels`` two int32s in the same
    object, which is the pairing that catches a reader that hardcoded four bytes: the
    resource sink's 6,639,935 banked points do not fit in an int32's worth of AWESOME
    coupons, and the two arrays would swap their values.
    """
    p = props(parse(blocks, "ResourceSinkSubsystem"))
    assert p["mTotalResourceSinkPoints"] == 6639935
    assert p["mTotalPoints"] == [22420905, 197000]
    assert p["mCurrentPointLevels"] == [135, 32]
    assert p["mNumResourceSinkCoupons"] == 13


def test_object_references_expose_the_name_the_projection_reads(blocks):
    """``extract.ref_path`` reads ``.pathName`` and nothing else.

    It also documents that ``repr()`` on a reference finds nothing, so ``__str__`` returning
    the path is part of the contract rather than a convenience.
    """
    p = props(parse(blocks, "FGPipeNetwork_2146897496"))
    fluid = p["mFluidDescriptor"]
    assert isinstance(fluid, ObjectReference)
    assert fluid.pathName.endswith("Desc_LiquidFuel.Desc_LiquidFuel_C")
    assert str(fluid) == fluid.pathName
    assert fluid.levelName == ""
    # An InterfaceProperty is a reference too, and the pipe network's integrant list is
    # the only place the projection meets one.
    assert p["mPipeNetworkID"] == 5
    assert all(isinstance(x, ObjectReference) for x in p["mFluidIntegrantScriptInterfaces"])


def test_soft_object_path_is_three_strings(blocks):
    """``FSoftObjectPath`` = package, asset, sub-path -- not a two-string reference.

    Reading it as an ordinary reference leaves four bytes over, and the size check turns
    that into a ParseError on the *next* property. The declared 76 bytes for one element
    only add up with the third string present.
    """
    p = props(parse(blocks, "IconDatabaseSubsystem"))
    ((ref, sub_path),) = p["mGlobalIconLibraries"]
    assert ref.levelName == "/Game/FactoryGame/-Shared/Blueprint/IconLibrary"
    assert ref.pathName == "IconLibrary"
    assert sub_path == ""


def test_text_property_keeps_the_string_the_player_typed(blocks):
    """FText, whose payload shape depends on a history byte that is always 0xFF here.

    Blueprint names and sign labels are player-typed strings, never localised lookups, so
    every FText in the save is the culture-invariant form. Any other history type is
    skipped rather than half-read, which would otherwise surface as a wrong blueprint name.
    """
    p = props(parse(blocks, "FGBlueprintProxy_2147399895"))
    assert p["mBlueprintName"] == [18, 255, 1, "1 Assembler"]


# ------------------------------------------------------------- native structs


def test_box_is_six_doubles_and_a_validity_flag(blocks):
    """A blueprint's local bounds, which the game shows as the designer's build volume.

    Seven values, not six: the trailing byte is FBox's ``IsValid``. Dropping it makes the
    payload one byte short and the property after it unreadable.
    """
    p = props(parse(blocks, "FGBlueprintProxy_2147399895"))
    box = p["mLocalBounds"]
    assert box == [-100.0, -1500.0, -0.000244140625, 900.0, 1100.0, 800.000244140625, True]


def test_vector_is_doubles_even_on_a_version_52_object(blocks):
    """The width follows the WRITER, not the object version stamped in the entry.

    ``mRemovedWorldLocations`` sits on a version-52 object and holds 3 elements in a
    72-byte block -- 24 bytes each. Reading them as UE4 floats would halve the block and
    put the gas volume's cleared rocks at coordinates off the map.
    """
    p = props(parse(blocks, "BP_VolumeGas_19"))
    assert p["mRemovedWorldLocations"] == [
        [187145.0, -134300.0, 17905.0],
        [189590.0, -132745.0, 18385.0],
        [188710.0, -137105.0, 17885.0],
    ]


def test_fluid_box_is_a_bare_float(blocks):
    """``FFluidBox`` is four bytes of litres, and it is not wrapped in a list.

    A coal generator's water connection holding 6.9 is the same unit the projection reports
    fluids in, and the shape matters: a one-element list would make every arithmetic
    consumer of it raise instead of add.
    """
    p = props(parse(blocks, "Build_GeneratorCoal_C_2145936929.FGPipeConnectionFactory"))
    assert p["mFluidBox"] == pytest.approx(6.9117841720581055)
    assert p["mPipeNetworkID"] == 46


def test_set_of_native_structs_reports_its_element_type(blocks):
    """``SetProperty`` yields ``[elementTypeName, values]``, and a ``Guid`` is two uint64s.

    The scanner's destroyed-pickup set is where a set of self-serialising structs occurs at
    all, and its leading "keys to remove" int32 -- 0 on every set in every save -- is
    refused rather than skipped, so the day a save writes one this says so.
    """
    p = props(parse(blocks, "ScannableSubsystem"))
    kind, values = p["mDestroyedPickups"]
    assert kind == "StructProperty"
    assert values[0] == [4921498246365915192, 9440798797560266936]
    assert all(len(v) == 2 for v in values)


# ------------------------------------------------- structs as property lists


def test_a_struct_can_be_a_nested_property_list(blocks):
    """``[values, types]``, which is the shape ``extract.struct_fields`` unwraps.

    ``FactoryCustomizationData`` is a struct with one property in it, and it arrives as a
    property list rather than raw numbers -- the flags byte on it is 0 where ``Box`` reads
    8. Getting that branch backwards makes every painted building's swatch unreadable.
    """
    from satisfactory_mcp.core.saveio.extract import struct_fields

    p = props(parse(blocks, "Build_ConveyorPole_C_2147055418"))
    assert p["mHeight"] == 300.0
    fields = struct_fields(p["mCustomizationData"])
    assert set(fields) == {"SwatchDesc"}
    assert fields["SwatchDesc"].pathName.endswith("SwatchDesc_Slot0.SwatchDesc_Slot0_C")


def test_transform_is_a_property_list_of_native_structs(blocks):
    """A conveyor lift's top transform: tagged outside, raw doubles inside.

    ``Transform`` is a property list whose ``Translation`` is a native ``Vector`` and whose
    ``Rotation``, when present, is a native ``Quat`` of four doubles. Both framings in one
    property is what makes it worth pinning.
    """
    from satisfactory_mcp.core.saveio.extract import struct_fields

    p = props(parse(blocks, "Build_ConveyorLiftMk2_C_2146699202"))
    assert struct_fields(p["mTopTransform"])["Translation"] == [0.0, 0.0, 2400.0]


def test_feet_offsets_are_a_struct_array_the_projection_reads(blocks):
    """``mCachedFeetOffset``: an array of two-field structs, one per building leg.

    Named in the notes as a real consumer, and the one place a plain ``ByteProperty`` sits
    next to a ``FloatProperty`` inside a struct. The leg indices 1 and 2 and the -0.0005
    offsets are a workbench standing on flat ground.
    """
    from satisfactory_mcp.core.saveio.extract import struct_fields

    p = props(parse(blocks, "Build_WorkBench_C_2146928207.FGFactoryLegs"))
    legs = [struct_fields(e) for e in p["mCachedFeetOffset"]]
    assert [leg["FeetIndex"] for leg in legs] == [[None, 1], [None, 2]]
    assert legs[0]["OffsetZ"] == pytest.approx(-0.00048828125)


# ---------------------------------------------------------- inventory stacks


def test_an_inventory_stack_reads_as_the_projection_expects(blocks):
    """The one property with real consumers: ``_accumulate_inventory``.

    It does ``ref_class(fields["Item"][0])``, and ``ref_class`` resolves a bare path string
    but takes the ``repr`` of a reference object and finds nothing. So element 0 of ``Item``
    must be a **string**. 100 iron ingots in a constructor's input is the whole chain --
    array, struct element, native ``InventoryItem``, path -- working end to end.
    """
    from satisfactory_mcp.core.saveio.extract import _accumulate_inventory

    p = props(parse(blocks, "Build_ConstructorMk1_C_2147441119.InputInventory"))
    totals: dict = {}
    _accumulate_inventory(p["mInventoryStacks"], totals)
    assert totals == {"Desc_IronIngot_C": 100}
    assert p["mArbitrarySlotSizes"] == [0]


def test_an_item_can_carry_its_own_state(blocks):
    """An equipped item's state is a sized property list nested inside the stack.

    The player's head slot holds a gas mask with 70 seconds of filter left on it -- a value
    the game shows on the HUD, which is what makes this a real reading and not a plausible
    one. It is the branch behind the int32 that is 0 on 15,000 ordinary stacks and 1 here;
    reading it as always-absent leaves the state's bytes in place and breaks the array.
    """
    from satisfactory_mcp.core.saveio.extract import struct_fields

    p = props(parse(blocks, "Char_Player_C_2147219546.HeadSlot"))
    stack = struct_fields(p["mInventoryStacks"][0])
    item_class, state = stack["Item"]
    assert item_class.endswith("BP_EquipmentDescriptorGasmask_C")
    state_class, values, types = state
    assert state_class == "/Script/FactoryGame.FGGasMaskItemState"
    assert [v[0] for v in values] == [t[0] for t in types]
    assert dict(values)["FilterCountdown"] == pytest.approx(70.0)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("FGItemPickup_Spawnable182", 8),  # version 36: an extra int32 in the item
        ("FGItemPickup_Spawnable_UAID_40B076DF2F79B1CC01_1364301741", 7),  # version 52
    ],
)
def test_version_36_items_carry_one_int32_more_than_version_52(blocks, name, expected):
    """The single place versions 36 and 52 disagree, and it cost 87 objects to find.

    They share a tag layout, so treating them as one "old" format is right everywhere
    except ``InventoryItem``, where version 36 writes a second int32 after the class
    reference and version 52 does not. Both of these blocks hold motors from a crash site;
    if the versions were folded together, one of the two would over- or under-read by
    exactly four bytes.
    """
    from satisfactory_mcp.core.saveio.extract import struct_fields

    p = props(parse(blocks, name))
    fields = struct_fields(p["mPickupItems"])
    assert fields["Item"][0].endswith("Desc_Motor_C")
    assert fields["NumItems"] == expected


# ----------------------------------------------------------------- the maps


def test_a_map_is_a_list_of_key_value_pairs(blocks):
    """``MapProperty`` keys and values are untagged, typed only by the map's own type tree.

    The calendar subsystem's two maps are keyed by an object reference. A map that read a
    per-element tag would find a property name where a reference is.
    """
    p = props(parse(blocks, "EventSubsystem"))
    for entry in p["mCalendarData"]:
        key, value = entry
        assert isinstance(key, ObjectReference)
        assert key.pathName.endswith("ChristmasCalendarRewards2025_C")
        assert isinstance(value, list)


# --------------------------------------------------- the old tag layout, whole


def test_the_version_36_body_fixture_parses_end_to_end():
    """Every object in the committed saveVersion 60 body, properties included.

    ``save_body.bin`` was cut for the level walk, not for this module, which is what makes
    it a useful second opinion: its objects are at versions 36, 52 and 60, its inventory
    stack exercises the version-36 struct-array header that lives inside the payload, and
    nothing in it was chosen to make the property reader look good.
    """
    if not BODY_FIXTURE.is_file():
        pytest.skip("body fixture not committed")
    from pioneersav import ActorHeader, read_body

    raw = BODY_FIXTURE.read_bytes()
    save = read_body(raw)
    seen = 0
    versions = set()
    for level in save.levels:
        for header, slot in zip(level.headers, level.objects):
            parsed = read_object(raw, slot, actor=isinstance(header, ActorHeader))
            assert parsed.warnings == []
            assert parsed.extra_length in (4, 8)
            versions.add(slot.version)
            seen += 1
    assert seen == 6
    assert versions == {36, 52, 60}


def test_an_actor_payload_opens_with_its_reference_lists():
    """Actors carry a parent reference and their components' names; components do not.

    Nothing inside the payload says which kind of object it is, so ``read_object`` takes it
    from the header. Passing ``actor=False`` for an actor reads the parent reference's level
    name as a property name -- which is why this asserts on the reference and not just on
    the property count.
    """
    if not BODY_FIXTURE.is_file():
        pytest.skip("body fixture not committed")
    from pioneersav import ActorHeader, read_body

    raw = BODY_FIXTURE.read_bytes()
    save = read_body(raw)
    actors = [
        (h, s)
        for lv in save.levels
        for h, s in zip(lv.headers, lv.objects)
        if isinstance(h, ActorHeader)
    ]
    header, slot = actors[0]
    parsed = read_object(raw, slot, actor=True)
    assert header.instance_name.endswith("BP_DropPod10")
    assert parsed.parent_reference == ObjectReference("", "")
    # A drop pod owns its power info, its inventory and its power input, and those three
    # names are the components that follow it in the same level's object list.
    assert [ref.pathName.rsplit(".", 1)[-1] for ref in parsed.child_references] == [
        "PowerInfoComponent",
        "Inventory",
        "PowerInput",
    ]


# --------------------------------------------------------- the escape hatches


def test_an_unknown_property_type_is_skipped_by_its_declared_length(blocks):
    """The rule the whole module rests on: a strange type costs one property, not the save.

    The tag is edited in place to name a type that does not exist, which is what a game
    patch adding one looks like from here. The property must come back as ``None`` with a
    warning, and -- the part that matters -- every property AFTER it must still be read,
    which only works if the skip used the declared size.
    """
    raw, slot, actor = blocks["BP_GameMode_C_2147477547"]
    patched = bytearray(raw)
    at = patched.index(b"StrProperty\0", slot.offset, slot.end)
    patched[at : at + 11] = b"WatProperty"
    parsed = read_object(bytes(patched), slot, actor=actor)
    values = props(parsed)
    assert values["mSaveSessionName"] is None
    assert len(parsed.warnings) == 1
    assert "WatProperty" in parsed.warnings[0][1]
    # The two properties after the unknown one are untouched.
    assert values["mLastAutoSaveId"] == [None, 2]
    assert values["mStartingPointTagName"] == "Northern Forest"


def test_an_unknown_self_serialising_struct_comes_back_as_bytes(blocks):
    """A struct with the native bit set and no entry in the table is not guessed at.

    Renaming ``Box`` gives exactly the situation a new engine struct creates. The bytes are
    handed back rather than dropped, so a caller can see what it got, and the size check
    still balances -- the object survives with one opaque property.
    """
    raw, slot, actor = blocks["FGBlueprintProxy_2147399895"]
    patched = bytearray(raw)
    at = patched.index(b"Box\0", slot.offset, slot.end)
    patched[at : at + 3] = b"Bxo"
    parsed = read_object(bytes(patched), slot, actor=actor)
    value = props(parsed)["mLocalBounds"]
    assert isinstance(value, bytes)
    assert len(value) == 49  # six doubles and the validity byte
    assert any("Bxo" in w for _, w in parsed.warnings)


def test_a_property_that_overruns_its_block_raises_with_the_offset(blocks):
    """A size that lies must fail loudly, because a torn autosave is routine here.

    The declared size of the first property is inflated past the end of the object. The
    error names the property and the overrun rather than returning the properties that
    happened to fit, which would be a shorter factory reported as a real one.
    """
    raw, slot, actor = blocks["BP_GameMode_C_2147477547"]
    patched = bytearray(raw)
    # A version-60 tag is the name, then the type tree (a name and its parameter count),
    # then the size -- so the size sits four bytes past the end of the type's name.
    at = patched.index(b"StrProperty\0", slot.offset, slot.end) + len("StrProperty\0") + 4
    patched[at : at + 4] = struct.pack("<i", 10_000)
    with pytest.raises(ParseError) as err:
        read_object(bytes(patched), slot, actor=actor)
    assert "mSaveSessionName" in str(err.value)
    assert "past the end" in str(err.value)


def test_a_property_list_with_no_terminator_raises(blocks):
    """Stopping at the block's end beats wandering into the next object's bytes.

    The ``"None"`` tag is a real property name, so a list is walked until it appears. If a
    size error means it never does, the walk has to stop somewhere, and the only honest
    place is the end of the block it was given.
    """
    raw, slot, actor = blocks["BP_GameMode_C_2147477547"]
    # 17 bytes shorter is this object's 8-byte trailer plus the 9-byte terminator tag,
    # so the last property ends exactly on the limit and there is no "None" left to find.
    # That is what a size which was wrong one layer up looks like from in here.
    truncated = ObjectSlice(slot.version, slot.flag, slot.offset, slot.length - 17)
    with pytest.raises(ParseError) as err:
        read_object(raw, truncated, actor=actor)
    assert "terminator" in str(err.value)


def test_the_last_untagged_element_of_a_container_is_skipped_by_the_container_size(blocks):
    """A map element has no size of its own; the map's own size covers the last one.

    Real bytes: the calendar subsystem's ``mCalendarsOpenedByPlayers`` has exactly one
    entry, and its value type is renamed to one that does not exist -- what a game patch
    introducing a struct looks like from in here. The key still reads, the value comes back
    as ``None`` with a warning, and the *other* map on the same object is untouched. Without
    that recovery a single unknown map value would cost the whole object.
    """
    raw, slot, actor = blocks["EventSubsystem"]
    patched = bytearray(raw)
    at = patched.index(b"mCalendarsOpenedByPlayers", slot.offset, slot.end)
    at = patched.index(b"StructProperty\0", at, slot.end)
    patched[at : at + 14] = b"StrucXProperty"
    parsed = read_object(bytes(patched), slot, actor=actor)
    ((key, value),) = props(parsed)["mCalendarsOpenedByPlayers"]
    assert key.pathName.endswith("ChristmasCalendarRewards2025_C")
    assert value is None
    assert [w for _, w in parsed.warnings] == ["skipped 168 bytes: untagged 'StrucXProperty'"]
    assert props(parsed)["mCalendarData"], "the other map on the same object still reads"


def test_an_untagged_element_that_cannot_be_skipped_fails_instead_of_inventing_a_map():
    """The same skip is a LIE when the element is not the last one, and must not happen.

    A map of two pairs whose value type has no reader: skipping the first value by the
    map's length lands the cursor on the map's end, so the second key is read out of
    whatever follows the map and the second value's skip drags the cursor *backwards* onto
    the end again. The declared size then balances and the object parses -- and it parsed,
    before this: two pairs, both values ``None``, the second key invented out of the
    terminator's length prefix, no error at all. That is the one outcome this parser is not
    allowed to produce, so the backwards skip raises with the offset instead.
    """
    pair = struct.pack("<i", 1) + b"\x01\x02\x03\x04"
    body, slot = _component(
        _tag(
            "m",
            _tree("MapProperty", _tree("IntProperty"), _tree("FooProperty")),
            struct.pack("<ii", 0, 2) + pair + pair,
        )
    )
    with pytest.raises(ParseError) as err:
        read_object(body, slot, actor=False)
    assert "cannot skip untagged 'FooProperty'" in str(err.value)
    assert "past the end" in str(err.value)


def test_an_ftext_with_a_foreign_history_inside_an_array_does_not_invent_labels():
    """FText is variable-length, so one unreadable history costs the array, not one label.

    Only history 0xFF occurs in any save, and a tagged ``TextProperty`` with another one is
    skipped by its declared size -- fine, that size is real. Inside an ARRAY there is no
    per-element size, so the first foreign history consumes the rest of the array and the
    second element is read from the array's end. That used to come back as
    ``[[0, 3], [5, 78]]``: two blueprint names invented from the bytes after the array, one
    of them a "history type" that is really a string length. It now raises.
    """
    body, slot = _component(
        _tag(
            "arr",
            _tree("ArrayProperty", _tree("TextProperty")),
            struct.pack("<i", 2)
            + (struct.pack("<i", 0) + b"\x03" + b"junk")
            + (struct.pack("<i", 0) + b"\xff" + struct.pack("<i", 0)),
        )
    )
    with pytest.raises(ParseError) as err:
        read_object(body, slot, actor=False)
    assert "FText history type" in str(err.value)


def test_a_container_count_bigger_than_its_block_is_refused_at_the_count():
    """A count is inside the payload, so the tag's size does not bound it -- this does.

    The array below declares an honest 12 bytes and then claims 1,000 elements. The
    per-property size check does catch it eventually, but only after reading 4,004 bytes of
    whatever follows the object, which on a real save is the next 44 MB of body: a planted
    count of 9,000,000 in a 40 MB body took 13.4 s and 79 MB to fail that way. Autosaves are
    rewritten under this parser every few minutes, so a torn count is routine and has to
    fail where it is read. The message must name the count, not the property's size, which
    is what says the bound did the refusing.
    """
    body, slot = _component(
        _tag(
            "arr",
            _tree("ArrayProperty", _tree("IntProperty")),
            struct.pack("<iii", 1000, 1, 2),
            size=12,
        )
    )
    with pytest.raises(ParseError) as err:
        read_object(body + bytes(4096), slot, actor=False)
    assert "claims 1000 elements with 8 bytes left" in str(err.value)


def test_an_empty_container_is_empty_and_not_absent():
    """Count 0 must short-circuit to an empty container, never to ``None``.

    Unreal omits an empty SaveGame array from the file entirely in some cases, so the ones
    that ARE written are easy to assume away -- and they are common: 918 empty struct arrays
    (a blueprint's ``lastEditedBy``), 2,531 empty maps (``BuiltPerPlayer`` on an unshared
    game) and 862 empty sets across the 31 saves. The shape is what matters here rather than
    the count: a consumer iterating a ``None`` raises, and ``_accumulate_inventory`` and
    ``struct_fields`` both iterate whatever they are handed.
    """
    body, slot = _component(
        _tag("arr", _tree("ArrayProperty", _tree("IntProperty")), struct.pack("<i", 0)),
        _tag(
            "structs",
            _tree("ArrayProperty", _tree("StructProperty", _tree("Vector"))),
            struct.pack("<i", 0),
        ),
        _tag(
            "m",
            _tree("MapProperty", _tree("IntProperty"), _tree("IntProperty")),
            struct.pack("<ii", 0, 0),
        ),
        _tag("st", _tree("SetProperty", _tree("IntProperty")), struct.pack("<ii", 0, 0)),
        _tag("after", _tree("IntProperty"), struct.pack("<i", 42)),
    )
    parsed = read_object(body, slot, actor=False)
    assert parsed.warnings == []
    assert props(parsed) == {
        "arr": [],
        "structs": [],
        "m": [],
        "st": ["IntProperty", []],
        "after": 42,
    }


# --------------------------------------------------------------- the flag bits


def test_the_bool_bit_and_the_native_bit_are_the_documented_ones():
    """The two flag bits every value in this module leans on, pinned as numbers.

    16 is what ``extract.truthy`` documents receiving for a true BoolProperty, and 8
    is what separates a struct that serialises itself from one written as a property list.
    Changing either constant would silently invert building states across the projection.
    """
    assert TAG_BOOL_TRUE == 0x10
    assert TAG_NATIVE_SERIALIZE == 0x08


# ------------------------------------- version-36/52 containers of structs the bytes do not name
#
# UE4's tag data for a map or a set carries the element's *property* type -- literally the string
# "StructProperty" -- and stops there, because the engine got the struct's name from reflection.
# So three properties on every saveVersion 52 save cannot be told apart from a property list by
# anything in the file: the foliage subsystem's `mSaveData` and the scanner's `mDestroyedPickups`
# and `mLootedDropPods`, 918,917 bytes on the reference v52 save. They are read by offering
# candidate struct types to `attempt` and keeping the one that lands exactly on the property's
# declared end -- `IntVector` for a map key, `Guid` then `Vector` for a set element, each of them
# a type version 60 writes out in full for the same field.
#
# That reading shipped with no test of its own. The only thing exercising it was a real save, and
# the check there is a digest of the projection -- which reads none of these three fields, so the
# digest would not move if they broke. Nothing else can reach it: version 60 names its structs,
# and both committed property fixtures are blocks the game wrote, neither of which contains a
# saveVersion-52 map or set of unnamed structs. Hence hand-built bytes, in the shapes the census
# over all 31 saves says occur.
#
# Two things here are deliberately NOT tested, because no test of them could fail today:
#
# * that the candidates are tried narrowest-first. No two of them can land on the same bytes --
#   16, 24 and a property list of any length differ -- so a test asserting the order would only
#   restate the constant it reads.
# * that `attempt` throws away the warnings a rejected candidate produced. It was measured for:
#   a map whose keys are property lists and whose values hold an unknown property type produces
#   the same two warnings with the discard and without it, because the `IntVector` candidate
#   fails on the first key and never reaches a value. Nothing observable distinguishes the two,
#   so the discard is unpinned and this comment is the record of that.


def _old_set(name: str, payload: bytes) -> bytes:
    """A set whose tag says its elements are structs and does not say which struct."""
    return _old_tag(name, "SetProperty", _s("StructProperty"), payload)


def test_an_unnamed_map_key_is_read_as_the_int_vector_version_60_names():
    """``FoliageRemovalSubsystem.mSaveData``: cell coordinate keys, 12 raw bytes each.

    Version 60's type tree for this very field reads ``StructProperty 1 IntVector 1
    /Script/CoreUObject``, so ``IntVector`` is the newer format stating the answer rather than a
    guess -- and ``[-7, -25, -1]`` below is a real cell from the reference save's foliage grid.
    Before this reading the whole property was skipped by its declared size.
    """
    keys = ((-7, -25, -1), (3, 4, 5))
    body = struct.pack("<ii", 0, len(keys)) + b"".join(
        struct.pack("<iiii", *k, i) for i, k in enumerate(keys)
    )
    payload, slot = _old_component(
        _old_tag("mSaveData", "MapProperty", _s("StructProperty") + _s("IntProperty"), body)
    )
    parsed = read_object(payload, slot, actor=False, save_version=52)
    assert parsed.warnings == [], "a landed candidate leaves no warning behind"
    assert props(parsed)["mSaveData"] == [[[-7, -25, -1], 0], [[3, 4, 5], 1]]


def test_an_unnamed_map_key_that_is_a_property_list_still_reads():
    """The candidate that is not native: the same tag bytes, a key written as a property list.

    This is the reading that existed before ``IntVector`` was offered, and it has to survive --
    it is why the empty struct name stays last in the candidate list. The referee is the only
    thing choosing between the two: 12 raw bytes cannot land as a property list and a property
    list cannot land as 12 raw bytes, so the same tag resolves both ways from the payload alone.
    """
    key = (
        _s("X")
        + _s("IntProperty")
        + struct.pack("<ii", 4, 0)
        + b"\0"
        + struct.pack("<i", 9)
        + _s("None")
    )
    body = struct.pack("<ii", 0, 2) + (key + struct.pack("<i", 1)) * 2
    payload, slot = _old_component(
        _old_tag("mListData", "MapProperty", _s("StructProperty") + _s("IntProperty"), body)
    )
    parsed = read_object(payload, slot, actor=False, save_version=52)
    assert parsed.warnings == []
    (first_key, first_value), _second = props(parsed)["mListData"]
    assert first_value == 1
    values, types = first_key
    assert values == [["X", 9]], "the key is a nested property list, not three ints"
    assert [t[:2] for t in types] == [["X", "IntProperty"]]


def test_an_unnamed_set_element_is_read_as_a_guid():
    """``ScannableSubsystem.mDestroyedPickups``: 6,968 bytes = 8 + 435 x 16, bare GUIDs.

    "Bare" is the part that needed its own reader. A set's elements carry none of the framing
    an array's do -- on version 36/52 an array of structs writes a full property tag inside its
    payload -- so routing this through the array path could never have landed, whatever struct
    was guessed.
    """
    body = struct.pack("<ii", 0, 2) + struct.pack("<QQ", 1, 2) + struct.pack("<QQ", 3, 4)
    payload, slot = _old_component(_old_set("mDestroyedPickups", body))
    parsed = read_object(payload, slot, actor=False, save_version=52)
    assert parsed.warnings == []
    assert props(parsed)["mDestroyedPickups"] == ["Guid", [[1, 2], [3, 4]]]


def test_the_same_set_tag_of_vectors_is_read_as_vectors():
    """``FGFoliageRemoval.mRemovalLocations``, the other set shape, and the referee at work.

    Byte for byte the tag is identical to the GUID set above -- ``SetProperty`` of
    ``StructProperty``, nothing more -- and only the payload's length decides. A ``Guid``
    reading of two elements consumes 32 bytes and this block is 48, so it is discarded and
    ``Vector`` lands. Twenty-four bytes because a saveVersion 52 file writes doubles; on the
    pre-1.0 saves the same field is float32 and 12, which
    ``test_pre_1_0.py`` covers on real bytes.
    """
    body = (
        struct.pack("<ii", 0, 2)
        + struct.pack("<ddd", 1.0, 2.0, 3.0)
        + struct.pack("<ddd", 4.0, 5.0, 6.0)
    )
    payload, slot = _old_component(_old_set("mRemovalLocations", body))
    parsed = read_object(payload, slot, actor=False, save_version=52)
    assert parsed.warnings == [], "a landed candidate leaves no warning behind"
    assert props(parsed)["mRemovalLocations"] == [
        "Vector",
        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
    ]


def test_a_set_no_candidate_lands_on_is_skipped_and_said_so():
    """No candidate fits: the property is skipped by its declared size, with a warning.

    12 bytes for one element is neither a GUID nor a vector nor a property list, which is what
    a patch introducing a new struct into one of these sets would look like from in here. It has
    to cost that property and not the object -- and it has to be *said*, because a silent skip
    is how a field nobody reads today becomes a field somebody reads tomorrow and finds empty.
    """
    payload, slot = _old_component(
        _old_set("mMystery", struct.pack("<ii", 0, 1) + b"\x01" * 12),
        _old_tag("after", "IntProperty", b"", struct.pack("<i", 42)),
    )
    parsed = read_object(payload, slot, actor=False, save_version=52)
    assert props(parsed) == {"mMystery": None, "after": 42}, "the next property still reads"
    assert [w for _, w in parsed.warnings] == [
        "skipped 20 bytes: version-52 set 'mMystery' of unnamed structs"
    ]
