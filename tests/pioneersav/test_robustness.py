"""What the parser must do to a file the game is halfway through rewriting.

Autosaves rewrite the save in place every few minutes, so torn files are routine. Each test
here pins one contract: **a ParseError carrying a byte offset, never a partial answer and
never an exception the sidecar can only report as a class name.** ``extract.main`` catches
exactly ``ParseError``; anything else reaches its bare ``except Exception`` and comes out as
``{"error": "RecursionError"}``, with no offset to look at.

Each case below was found by attacking a real save and each one produced a wrong outcome
before the check it tests existed. What was tried and did *not* find anything is worth
recording too, because it is why these four are the only ones here:

* **truncation** -- 1,750 cut points across a saveVersion 60 and a saveVersion 52 save,
  every byte of the header region and 240 points through the body: all refused already;
* **a torn autosave** -- the realistic shape of the failure, a prefix of the new chunk
  stream followed by a suffix of the old one, spliced at 14 chunk boundaries: refused by the
  body's self-declared size every time;
* **bit flips** -- 600 single-bit flips in the chunk stream: 599 refused, and the survivor sat
  in unused deflate padding and inflated byte-identically. Everything the property serialiser
  reads sits behind a per-chunk adler32, so only a deliberate re-compression gets past it;
* **lying length fields** -- every int32 and int64 the body walk reads, in both committed
  body fixtures, set to 0, 1, -1 and both int32 extremes: 1,488 refused, and of the 688 that
  parsed, nine changed the answer and all nine were a string length reading as a shorter or
  empty string, which is indistinguishable from a genuinely empty name.
"""

from __future__ import annotations

import json
import struct
import zlib

import pytest

from pioneersav import CHUNK_TAG, ObjectSlice, ParseError, read_info_bytes, read_object
from tests.support.paths import committed_fixture


def _string(text: str) -> bytes:
    """Unreal's length-prefixed string: the count includes the trailing null."""
    raw = text.encode() + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def _type_tree(name: str, *params: bytes) -> bytes:
    return _string(name) + struct.pack("<i", len(params)) + b"".join(params)


def _int_property(name: str, value: int) -> bytes:
    """A complete version-60 IntProperty: name, type tree, size, flags, payload."""
    return (
        _string(name)
        + _type_tree("IntProperty")
        + struct.pack("<i", 4)
        + b"\x00"  # no array index, no guid, not native, not a bool
        + struct.pack("<i", value)
    )


def _component_payload(body: bytes, trailer: bytes) -> bytes:
    """A version-60 component payload: the migration byte, a property list, a trailer."""
    return b"\x00" + body + _string("None") + trailer


def _slot(payload: bytes) -> ObjectSlice:
    return ObjectSlice(version=60, flag=0, offset=0, length=len(payload))


# ------------------------------------------------------------------ nesting depth


def _nested_structs(depth: int) -> bytes:
    """``depth`` StructProperty tags, each whose payload is the next one down."""
    inner = _string("None")
    for _ in range(depth):
        tree = _type_tree("StructProperty", _type_tree("Nested"))
        inner = (
            _string("P") + tree + struct.pack("<i", len(inner)) + b"\x00" + inner + _string("None")
        )
    return b"\x00" + inner + b"\x00\x00\x00\x00"


def test_a_deeply_nested_property_list_is_refused_rather_than_overflowing_the_stack():
    """Nested structs must hit a ParseError long before CPython's recursion limit.

    Every route back into a nested property list -- a struct, an array element, a map value,
    an ``InventoryItem``'s weapon state -- goes through ``PropertyDecoder.property_list``, and it
    used to recurse once per nested tag with nothing counting. 29,010 crafted bytes were
    enough: written over one real object's payload and run through the sidecar, the save came
    out as ``{"error": "RecursionError", "detail": "maximum recursion depth exceeded"}`` with
    a traceback on stderr, no offset, and no indication of where in a 44 MB body to look.

    If this regresses, the symptom is not a wrong answer. It is that the one exception type
    the sidecar catches stops being the only one a bad file can raise, which is the whole
    argument of ``pioneersav/errors.py``.
    """
    payload = _nested_structs(600)
    with pytest.raises(ParseError) as exc:
        read_object(payload, _slot(payload), actor=False)
    assert "nested more than 32 deep" in str(exc.value)
    # The offset is the point of the exercise: it says which byte to look at.
    assert "at body offset " in str(exc.value)


def test_the_depth_a_real_save_actually_uses_still_parses():
    """The bound has to be a bound, not a new limit on real data.

    The deepest property list in any of the 31 readable saves nests **4** deep. A guard set
    anywhere near that would refuse real saves, so this pins that four times the observed
    maximum is still comfortably inside it.
    """
    payload = _nested_structs(16)
    obj = read_object(payload, _slot(payload), actor=False)
    assert [p[0] for p in obj.properties] == ["P"]


# ------------------------------------------------- the payload must be consumed


def test_a_component_whose_property_list_stops_early_is_refused():
    """A list that terminates before its payload does means properties are missing.

    Every property is size-checked, so a wrong *width* fails on the property that has it.
    Nothing was checking the payload as a whole, and the leftover after the ``"None"``
    terminator absorbed anything: overwriting one property's NAME with the nine bytes that
    spell the terminator made a ``Build_ConstructorMk1`` read as 2 properties instead of 13,
    filing the other 1,352 bytes as trailing data with no error and no warning. Through the
    sidecar that was **exit 0 and a complete projection** -- 44,634 objects, 438 machines,
    and one constructor with no recipe and no inventories.

    A component is the half of a save where the check can be exact: all 567,856 components in
    the 31 readable saves leave exactly 4 or 8 bytes and never more. Injecting the terminator
    into every component of two real saves is refused 104 times out of 104 with this check
    and was silent 104 times out of 104 without it.
    """
    # 40 bytes of properties that the list will never reach, standing in for the ones a
    # truncated list loses.
    lost = b"".join(_int_property(f"mLost{i}", i) for i in range(3))
    payload = _component_payload(_int_property("mKept", 7), b"\x00\x00\x00\x00" + lost)
    with pytest.raises(ParseError) as exc:
        read_object(payload, _slot(payload), actor=False)
    message = str(exc.value)
    assert "component's property list left" in message
    assert "the properties after that point are missing" in message
    assert "at body offset " in message


@pytest.mark.parametrize("trailer_length", [4, 8])
def test_the_two_trailer_lengths_a_real_component_writes_are_accepted(trailer_length):
    """4 and 8 are both real and both must pass, or 46% of every save stops parsing.

    5,300 components in the readable saves leave 4 bytes and 562,556 leave 8. Neither is a
    special case of the other and there is no rule known that predicts which, so both are
    simply allowed.
    """
    payload = _component_payload(_int_property("mKept", 7), b"\x00" * trailer_length)
    obj = read_object(payload, _slot(payload), actor=False)
    assert obj.properties == [["mKept", 7]]
    assert obj.extra_length == trailer_length


@pytest.mark.parametrize("actor", [False, True])
def test_a_property_list_that_ends_on_the_last_payload_byte_is_refused(actor):
    """No object leaves a trailer shorter than 4, so a list ending flush with the end is wrong.

    This is the one part of the consumed-payload check that holds for actors as well as
    components: 1,243,288 objects across the 31 readable saves and the shortest trailer
    anywhere is 4 bytes. An actor's trailer is otherwise deliberately unchecked -- 88,066 of
    them legitimately carry class-specific data, in eight classes, and bounding that needs the
    whitelist that belongs to whoever decodes those bytes.
    """
    prefix = b"" if not actor else (_string("") * 2 + struct.pack("<i", 0))
    payload = prefix + _component_payload(_int_property("mKept", 7), b"")
    with pytest.raises(ParseError) as exc:
        read_object(payload, _slot(payload), actor=actor)
    assert "bytes before its" in str(exc.value)
    assert "at body offset " in str(exc.value)


# ----------------------------------------------------------- the file's two ends


def test_a_file_truncated_to_exactly_its_header_is_refused_at_the_header():
    """The PACKAGE_FILE_TAG check is the proof the header walk is right; it must always run.

    It used to be skipped when fewer than four bytes were left, which threw the proof away in
    the one case that needs it most. A real save cut to exactly its 453-byte header returned a
    fully populated ``SaveInfo``, and the refusal then came from two layers further down as
    ``read of 8 at 0 runs past end (0)`` -- an offset into a body that does not exist, which
    reads like a corrupt save rather than a file the game has only begun writing. That message
    is what a player sees next to the file's name in ``--list``.
    """
    raw = committed_fixture("save_header.bin").read_bytes()
    body_offset = read_info_bytes(raw).body_offset
    with pytest.raises(ParseError) as exc:
        read_info_bytes(raw[:body_offset])
    message = str(exc.value)
    assert f"the file ends at offset {body_offset}" in message
    assert "partly-written file" in message


def test_a_chunk_preamble_that_contradicts_its_own_maximum_is_refused():
    """The one preamble field that was read and discarded, and it showed.

    Overwriting each byte of a chunk preamble in turn and re-parsing the save -- 588 mutations
    over three chunks of a real file -- caught every field except this one: 99 undetected
    changes, all eight of the max-size bytes. The value is 131072 on all 9,125 chunks of all
    31 readable saves, but requiring the constant would refuse a save the day the game picks a
    different block size, so what is checked is the contradiction: a maximum smaller than the
    uncompressed size written beside it.
    """
    from pioneersav import decompress_body

    plain = b"body bytes that do not matter, only their length does" * 4
    blob = zlib.compress(plain)
    stream = (
        struct.pack(
            "<qqBqqqq", CHUNK_TAG, len(plain) - 1, 3, len(blob), len(plain), len(blob), len(plain)
        )
        + blob
    )
    with pytest.raises(ParseError) as exc:
        decompress_body(stream, 0)
    assert "declares a maximum of" in str(exc.value)
    assert "The preamble contradicts itself" in str(exc.value)


def test_a_body_region_with_no_chunks_at_all_says_so():
    """An empty chunk stream is a refusal, not an empty body.

    ``decompress_body`` returned ``b""`` for a region holding no chunks, and the level walk
    then reported ``read of 8 at 0 runs past end (0)``. ``header`` now refuses that file
    first, so this is defence for any other caller that reaches here -- and it is the message
    that names the actual problem.
    """
    from pioneersav import decompress_body

    with pytest.raises(ParseError) as exc:
        decompress_body(b"", 0)
    assert "no chunk at 0" in str(exc.value)


# ----------------------------------------------------- what the sidecar SAYS about all this

# Everything above pins the parser's refusals; these two pin the surface the projection layer
# sees them through, ``extract.main``: an exit code and one JSON object on stdout. Called
# in-process, since a subprocess would add the environment as a second thing that can fail.


def _run_cli(argv: list[str]) -> tuple[int, str]:
    """``extract.main`` with stdout captured. Returns (exit code, what it printed)."""
    import io
    from contextlib import redirect_stdout

    from satisfactory_mcp.core.saveio import extract

    out = io.StringIO()
    with redirect_stdout(out):
        code = extract.main(argv)
    return code, out.getvalue()


def test_a_torn_save_leaves_the_cli_saying_parse_error_on_stdout(tmp_path):
    """The refusal a caller can branch on, and the detail a reader can act on.

    ``projection.py`` runs this as a subprocess and reads its stdout as JSON, so a parse
    failure has to arrive as a well-formed object rather than as a traceback: a torn autosave
    is the ROUTINE case here, not the exceptional one, and the caller's job is to say "not
    yet" and try again. Three things are therefore load-bearing at once and none of them is
    checked by the ParseError tests above -- stdout stays parseable, ``error`` is the stable
    token ``parse_error`` rather than a Python class name, and the exit code is 1.

    The stderr traceback is deliberately not asserted: it is what ``main``'s bare
    ``except Exception`` does for the UNEXPECTED failures, and reaching it for a torn file is
    the bug (``{"error": "RecursionError"}`` -- true, useless, no offset), which is exactly
    what ``error == "parse_error"`` below rules out.
    """
    torn = tmp_path / "Han Solo_autosave_0.sav"
    torn.write_bytes(committed_fixture("save_header.bin").read_bytes()[:40])

    code, printed = _run_cli([str(torn)])
    assert code == 1
    body = json.loads(printed)
    assert body["error"] == "parse_error", printed
    assert body["path"] == str(torn)
    # The offset is the whole value of the detail line: it is what tells a torn file from a
    # corrupt one, and it is what ``--list`` prints beside the filename.
    assert "runs past end" in body["detail"]


def test_the_list_scan_buckets_every_unreadable_file_instead_of_aborting(tmp_path):
    """One bad save must not cost the reader the other sixty-two.

    This is the endpoint the world picker is built on, and the directory it scans is the
    player's real one: of 63 files there, 35 are pre-1.0 and cannot be parsed at all. A scan
    that stopped at the first failure would report no worlds on a machine that has several,
    and the web page's "no readable saves found" line would be a lie with a reason attached.

    So: a directory of nothing but junk still exits 0, still emits a complete document, and
    every file lands in ``unsupported`` carrying the reason it landed there -- which is the
    string ``app/world-picker.ts`` prints when it has nothing else to show.
    """
    (tmp_path / "wrong-magic.sav").write_bytes(b"not a save at all, not even close")
    (tmp_path / "empty.sav").write_bytes(b"")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "cut-short.sav").write_bytes(b"\x0e\x00\x00\x00<\x00\x00\x00")
    (tmp_path / "not-a-save.txt").write_bytes(b"ignored: the scan is .sav only")

    code, printed = _run_cli(["--list", str(tmp_path)])
    assert code == 0, printed
    body = json.loads(printed)
    assert body["saves"] == [], "junk was accepted as a save"
    bucketed = {row["filename"]: row for row in body["unsupported"]}
    assert set(bucketed) == {"wrong-magic.sav", "empty.sav", "cut-short.sav"}, (
        "the scan aborted, or it walked past the nested directory"
    )
    for name, row in sorted(bucketed.items()):
        assert row["reason"].strip(), f"{name} was rejected without saying why"
        # mtime and size ride along because the picker sorts and de-duplicates on them, and
        # a file that cannot be parsed still has both.
        assert row["mtime_ns"] > 0 and row["size"] >= 0, name
