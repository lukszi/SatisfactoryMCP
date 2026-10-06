"""``read_full_save``: the composition, and the switch that lets the sidecar pick a parser.

The four layers below were each tested against their own committed fixture. Nothing tested
them *together*, and the composition is where the two decisions that can silently ruin a
parse live: the inflated body has to stay alive (every object slice is an absolute index
into it, and nothing is copied), and every failure below has to arrive as one exception
type, because the sidecar catches exactly one at the save boundary.

These tests build a complete .sav in memory out of the fixtures already committed -- the
real header prefix from ``save_header.bin``, and ``save_body.bin`` re-compressed into the
chunk stream the game writes. That is the only way to exercise the whole path with no game
install, and it means a change in any one layer that breaks the seam between two of them
fails here rather than on a machine that happens to have a 2.9 MB save.

Parity is measured elsewhere and not repeated here: the same save through both parsers now
agrees on **all 19** projection fields, on all 31 readable saves. What *is* pinned here is the
composition's half of how the trailing class-specific bytes are handled -- the subsystem that
holds every foundation is decoded (``test_lightweight.py`` owns the record itself),
and the seven classes that are not decoded read as ``None`` rather than as an empty list, so a
class nobody has taught this parser cannot pass for a class that had nothing in it.
"""

from __future__ import annotations

import importlib.util
import struct
import zlib

import pytest

from pioneersav import CHUNK_TAG, ParseError, read_full_save_bytes, read_info_bytes
from tests.support.paths import FIXTURES, REPO_ROOT

HEADER_FIXTURE = FIXTURES / "save_header.bin"
BODY_FIXTURE = FIXTURES / "save_body.bin"
SIDECAR = REPO_ROOT / "src" / "satisfactory_mcp" / "core" / "saveio" / "extract.py"

#: The chunk size the game writes on every save seen. Reproduced rather than shortened so
#: the assembled file is a real chunk stream and not a special case of one.
MAX_CHUNK = 131072


def _chunk_stream(body: bytes) -> bytes:
    """Compress ``body`` into the game's chunk framing.

    The 49-byte preamble writes the compressed and uncompressed sizes TWICE, identically;
    ``decompress_body`` compares the two copies, so a helper that wrote only one would be
    testing a file shape the game never produces.
    """
    out = b""
    for start in range(0, len(body), MAX_CHUNK):
        piece = body[start : start + MAX_CHUNK]
        blob = zlib.compress(piece)
        out += struct.pack(
            "<qqBqqqq",
            CHUNK_TAG,
            MAX_CHUNK,
            3,  # zlib
            len(blob),
            len(piece),
            len(blob),
            len(piece),
        )
        out += blob
    return out


@pytest.fixture(scope="module")
def header_prefix() -> bytes:
    if not HEADER_FIXTURE.is_file():
        pytest.skip("header fixture not committed")
    raw = HEADER_FIXTURE.read_bytes()
    # Exactly up to where the compressed body starts -- the fixture is a 2 KiB file prefix,
    # so it carries some of the real first chunk, which must not be left in front of ours.
    return raw[: read_info_bytes(raw).body_offset]


@pytest.fixture(scope="module")
def body() -> bytes:
    if not BODY_FIXTURE.is_file():
        pytest.skip("body fixture not committed")
    return BODY_FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def sav(header_prefix, body) -> bytes:
    return header_prefix + _chunk_stream(body)


@pytest.fixture(scope="module")
def save(sav):
    return read_full_save_bytes(sav)


def _load_sidecar(name: str):
    """Import ``extract`` under a private module name.

    ``importlib.reload`` would mutate the copy in ``sys.modules`` and leak the engine
    choice into every later test, and the engine is resolved at import time on purpose.
    """
    spec = importlib.util.spec_from_file_location(name, SIDECAR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- the composition


def test_a_whole_save_parses_into_the_shape_the_projection_reads(save):
    """The adapter surface, asserted as a shape rather than trusted.

    ``extract.iter_objects`` zips ``level.actorAndComponentObjectHeaders`` with
    ``level.objects`` and reads ``obj.properties`` as ``[name, value]`` pairs. Every one of
    those three names is an alias over a differently-spelled field, so a rename anywhere
    below would leave the sidecar walking zero objects and reporting an empty factory with
    no error at all -- which is the failure this file exists to prevent.
    """
    assert [lv.name for lv in save.levels][-1] == "Persistent_Level"
    assert len(save.levels) == 5
    assert save.object_count == 6
    assert save.warnings == []

    for level in save.levels:
        assert level.actorAndComponentObjectHeaders is level.headers
        assert len(level.headers) == len(level.objects)
        for header, obj in zip(level.headers, level.objects, strict=True):
            assert getattr(header, "instanceName", None)
            for pair in obj.properties:
                assert isinstance(pair, list) and len(pair) == 2
                assert isinstance(pair[0], str)

    # The fixture was cut to cover all three object versions; the composition must handle
    # a save that mixes them, because every real save does.
    versions = {obj.version for lv in save.levels for obj in lv.objects}
    assert versions == {36, 52, 60}


def test_properties_that_the_projection_actually_reads_come_through(save):
    """One end-to-end value, not just a shape.

    The fixture's version-36 inventory component is the single most load-bearing property
    in the projection: ``mInventoryStacks`` feeds every inventory bucket, the machine
    buffers and the slotted-shard census. A composition that returned well-formed but
    empty property lists would satisfy the shape test above and report a world with
    nothing in it.
    """
    named = {
        header.instanceName: obj
        for level in save.levels
        for header, obj in zip(level.headers, level.objects, strict=True)
    }
    stacks = [
        obj
        for obj in named.values()
        if any(pair[0] == "mInventoryStacks" for pair in obj.properties)
    ]
    assert stacks, "the fixture's FGInventoryComponent lost its stacks"
    props = dict(stacks[0].properties)
    assert isinstance(props["mInventoryStacks"], list)
    assert props["mInventoryStacks"], "an inventory with zero slots is not what was cut"


def test_the_inflated_body_is_retained_so_undecoded_bytes_stay_addressable(save, body):
    """``extra_offset`` is an index into the body, not a copy of it.

    3,209 actors on the reference save carry class-specific bytes after their property
    list -- conveyor chains, power lines, and the lightweight-buildable subsystem's 3.1 MB.
    Whoever decodes those next needs the bytes they point at. If ``ParsedSave`` stopped
    holding the body, every one of those offsets would address a buffer that had been
    freed, and the failure would look like a decoding bug rather than a lifetime bug.
    """
    assert save.body == body
    for level in save.levels:
        for obj in level.objects:
            assert obj.extra_length > 0, "every object has at least a 4-byte trailer"
            chunk = save.body[obj.extra_offset : obj.extra_offset + obj.extra_length]
            assert len(chunk) == obj.extra_length


def test_an_actor_with_bytes_nothing_accounts_for_is_reported(save):
    """The check that knowing all eight trailing-byte classes buys.

    A component's trailer has always been length-checked; an actor's could not be, because an
    actor of one of those eight classes legitimately leaves megabytes. Now that every one has a
    reader, an actor that is neither one of them nor leaving a plain 4 or 8 bytes gets a warning
    naming the class and the count -- which is what a property list that stopped early looks
    like, and it used to be entirely silent.

    Measured first: over all 31 readable saves, 500,350 actors leave 4 bytes and 87,016 leave 8,
    88,066 are one of the eight classes, and **nothing** is left over. So this fires on no save
    on this disk, which is why the fixture has to be doctored to test that it fires at all.
    """
    from pioneersav.objects import ActorHeader
    from pioneersav.properties import ParsedObject
    from pioneersav.save import PLAIN_TRAILER, _attach_trailer

    assert not [w for w in save.warnings if "trailing bytes" in w[1]], (
        "no real save on this disk has an unaccounted-for actor trailer"
    )

    header = ActorHeader(
        type_path="/Game/Mods/Whatever/Build_Mystery.Build_Mystery_C",
        root_object="Persistent_Level",
        instance_name="Persistent_Level:PersistentLevel.Build_Mystery_C_1",
        object_flags=0,
        need_transform=0,
        rotation=(0.0, 0.0, 0.0, 1.0),
        position=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        was_placed_in_level=0,
    )
    warnings: list[tuple[int, str]] = []
    obj = ParsedObject(version=60, extra_offset=1234, extra_length=9_001)
    _attach_trailer(b"", header, obj, warnings)
    assert obj.decode_trailer is None, "an unknown class gets no decoder"
    assert len(warnings) == 1
    at, what = warnings[0]
    assert at == 1234, "the offset is what makes it findable in a 44 MB body"
    assert "Build_Mystery_C left 9001 trailing bytes" in what

    for length in PLAIN_TRAILER:
        quiet: list[tuple[int, str]] = []
        _attach_trailer(b"", header, ParsedObject(version=60, extra_length=length), quiet)
        assert quiet == [], f"{length} bytes is what an ordinary actor leaves"


def test_an_undecoded_class_reads_as_None_and_not_as_empty(save):
    """A class whose trailing bytes nothing decodes must be distinguishable from one that
    was decoded and held nothing.

    ``_lightweight`` and ``_structures`` both read ``getattr(obj, "actorSpecificInfo",
    None)``, and both map ``None`` to ``{}`` and ``{"classes": [], "instances": []}``. That
    is the same output an empty list would give -- which is exactly why the *attribute*
    must not be an empty list: the day someone adds a partial decode for conveyor chains,
    ``None`` is what says "this one is not done" rather than "this one was empty". A silent
    undercount here reads like a real answer: a foundation census reporting 40 slabs where
    8,347 pieces are built looks no different from a correct one.

    No object in this fixture is the lightweight subsystem, so every one of them is the
    not-decoded case. The decoded case is ``test_lightweight.py``.
    """
    sidecar = _load_sidecar("_extract_save_no_asi")
    for level in save.levels:
        for obj in level.objects:
            assert obj.actor_specific_info is None
            assert obj.actorSpecificInfo is None
            assert sidecar._lightweight(obj) == {}
            assert sidecar._structures(obj, sidecar.Drops()) == {"classes": [], "instances": []}


# --------------------------------------------------------------- one failure type


def test_a_truncated_header_is_a_parse_error_that_names_the_save_version(sav):
    """Every layer's failure has to arrive as ``ParseError``, and say where it was.

    ``header`` and ``chunks`` predate ``ParseError`` and raised bare ``ValueError``. The
    sidecar catches one type at the save boundary, so half the failures fell through to its
    bare-``Exception`` handler and were reported as ``{"error": "ValueError"}`` -- a class
    name with no offset, for a file that is simply mid-rewrite.

    The version fields in the message are what makes a refusal actionable, and the header is the
    one layer that can refuse a *layout* rather than damage: it is the only part of a save that
    says which format it is. saveHeaderType 8, 9 and 10 are read now -- see
    ``test_pre_1_0.py`` -- so the refusal that has to stay legible is the one for a type
    nobody has derived, and it has to list what is derived rather than name a single version.
    """
    with pytest.raises(ParseError, match=r"saveHeaderType 14.*saveVersion 60"):
        read_full_save_bytes(sav[:100])

    unknown = bytearray(sav[:100])
    unknown[0:4] = struct.pack("<i", 20)
    with pytest.raises(ParseError, match=r"saveHeaderType 20 \(known: 8, 9, 10, 14\)"):
        read_full_save_bytes(bytes(unknown))


def test_a_torn_chunk_stream_is_a_parse_error_with_an_offset(sav, header_prefix):
    """An autosave rewrites the file in place every few minutes, so a half-written chunk
    stream is routine. It must fail at the tear rather than inflating garbage into the object
    walk and failing somewhere unrelated a level later.

    This asserted only "runs past end" until truncating two real saves at fourteen fractions
    of their length showed that every single one of those twenty-eight tears lands here, and
    that the message named neither the chunk nor the shortfall. It now has to name both,
    because the whole point of failing at the tear is being able to see that it *was* a tear.
    """
    with pytest.raises(ParseError, match="chunk at 453 declares 1506 compressed bytes"):
        read_full_save_bytes(sav[: len(sav) - 200])
    with pytest.raises(ParseError, match="shortfall of 200"):
        read_full_save_bytes(sav[: len(sav) - 200])

    # A chunk whose tag is wrong is the same class of damage from the other direction. The
    # byte flipped is in the tag's HIGH half: the low half is PACKAGE_FILE_TAG, which the
    # header checks as the proof it walked to the right place, so damaging that instead
    # would never reach the chunk reader at all.
    mangled = bytearray(sav)
    mangled[len(header_prefix) + 4] ^= 0xFF
    with pytest.raises(ParseError, match="not a chunk stream here"):
        read_full_save_bytes(bytes(mangled))


def test_a_body_that_lies_about_its_own_size_is_a_parse_error(header_prefix, body):
    """The body's leading int64 must equal the bytes that follow it.

    This is the cheapest possible check that the chunk stream inflated to a whole body, and
    it is the one that catches a save whose last chunk was never written -- the sizes all
    agree per chunk, so nothing below this notices.
    """
    short = struct.pack("<q", len(body) - 8) + body[8:-64]
    with pytest.raises(ParseError, match="the body says it is"):
        read_full_save_bytes(header_prefix + _chunk_stream(short))


def test_a_read_past_the_end_of_the_body_is_a_parse_error_too(header_prefix, body):
    """The dullest failure and the commonest: the walk runs off the end.

    It comes from ``Reader._take``, the bottom layer, which is why ``ParseError`` is defined
    below the reader rather than beside the level walk. A bare ``ValueError`` here was the
    other half of the sidecar's misreported failures.
    """
    truncated = body[:-64]
    payload = struct.pack("<q", len(truncated) - 8) + truncated[8:]
    with pytest.raises(ParseError):
        read_full_save_bytes(header_prefix + _chunk_stream(payload))


# --------------------------------------------------------------- the parser it replaced


def test_the_sidecar_has_exactly_one_parser_and_it_is_ours():
    """The switch is gone with the library it chose between.

    ``SATISFACTORY_SAVPARSE`` existed for one purpose: running a save through both parsers and
    diffing the projection, so that "they agree" was a measurement rather than an argument. With
    the vendored GPL-3.0 ``sat_sav_parse`` deleted there is no second option, and a switch with
    one branch is a place for a stale code path to hide.

    What this pins is that no import of the deleted library survives anywhere the sidecar
    touches -- a leftover ``import sav_parse`` in a rarely-taken branch would not fail until a
    user hit it.
    """
    sidecar = _load_sidecar("_extract_save_single")
    assert sidecar.read_full_save.__module__.startswith("pioneersav")
    assert ParseError in sidecar.PARSE_ERROR
    assert not hasattr(sidecar, "ENGINE"), "the engine switch should be gone, not defaulted"
    # Imports and path manipulation, not mentions: the module comment names the deleted library
    # on purpose, to say why the switch is gone. Prose about it is documentation; an import of
    # it is a live dependency, and only the second is a defect.
    lines = SIDECAR.read_text(encoding="utf-8").splitlines()
    code = [ln.split("#", 1)[0] for ln in lines]
    assert not [ln for ln in code if ln.strip().startswith(("import sav_parse", "from sav_parse"))]
    assert not [ln for ln in code if "vendor" in ln and "sys.path" in ln]
    assert not [ln for ln in code if "SATISFACTORY_SAVPARSE" in ln]


def test_the_deleted_library_is_really_gone():
    """The licence exposure was the library being in the repository, so its absence is the
    thing worth asserting -- not that some branch prefers ours.

    It lived at ``sidecar/vendor/sat_sav_parse``, and the whole ``sidecar/`` directory went
    when the parser became ``src/pioneersav``, so the directory's absence is what says it.
    """
    assert not (REPO_ROOT / "sidecar").exists()
