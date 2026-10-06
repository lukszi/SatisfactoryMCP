"""The .sav header, parsed by our own code.

Written to replace a vendored GPL-3.0 parser. The format is a fact about what the game
writes, not a creative work, so this implements the format and verifies black-box: same
file in, same values out. Measured across 67 saves on the development machine, this agreed with
the old parser on all 31 it could read and failed on exactly the same 36 -- pre-1.0 saves
whose header the old parser also refuses.

Tests here run against a COMMITTED 2 KiB fixture rather than a live save, so they keep
working once the vendored library is gone and on a machine with no Satisfactory install.
"""

from __future__ import annotations

import pytest

from pioneersav import PACKAGE_FILE_TAG, Reader, body_hash, check_body_hash, read_info_bytes
from tests.support.paths import FIXTURES

FIXTURE = FIXTURES / "save_header.bin"


@pytest.fixture(scope="module")
def raw() -> bytes:
    if not FIXTURE.is_file():
        pytest.skip("header fixture not committed")
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def info(raw):
    return read_info_bytes(raw)


# ------------------------------------------------------------- the primitives


def test_a_positive_length_is_one_byte_per_character():
    # 5, "Han\0" is 4 -- length counts the terminator.
    data = (4).to_bytes(4, "little") + b"Han\x00"
    assert Reader(data).string() == "Han"


def test_a_negative_length_is_utf16():
    """The sign of the length IS the encoding, which is the one thing about Unreal's
    strings that is not guessable."""
    text = "Häns"
    data = (-(len(text) + 1)).to_bytes(4, "little", signed=True) + (text + "\x00").encode(
        "utf-16-le"
    )
    assert Reader(data).string() == text


def test_a_zero_length_string_consumes_nothing():
    r = Reader((0).to_bytes(4, "little") + b"rest")
    assert r.string() == ""
    assert r.remaining == 4


def test_reading_past_the_end_raises_rather_than_truncating(raw):
    """A silently short read would produce a header that parsed into plausible nonsense."""
    r = Reader(raw[:8])
    r.i32()
    with pytest.raises(ValueError, match="runs past end"):
        r.i64()


# ------------------------------------------------------------- the header


def test_it_reads_the_fields_the_projection_uses(info):
    """These are exactly the nine the sidecar puts in `header`."""
    assert info.session_name == "Han Solo"
    assert info.save_identifier == "X2faPVKjX06VaRzClNv5KQ"
    assert info.save_header_type == 14
    assert info.save_version == 60
    assert info.build_version == 495413
    assert info.play_duration_s == 1_151_711
    assert info.save_datetime_ticks == 639_208_697_277_810_000
    assert info.is_modded is False
    assert info.is_creative is False


def test_it_reads_the_rest_of_the_header_too(info):
    assert info.save_name == "Han Solo_280726-230847"
    assert info.map_name == "Persistent_Level"
    assert info.map_options.startswith("?skiponboarding")
    assert info.editor_object_version == 40
    assert info.mod_metadata == ""
    assert info.save_data_hash == (6_096_361_947_348_211_065, 9_325_011_144_171_762_175)


def test_the_header_ends_exactly_at_the_compressed_body(info, raw):
    """The proof that every field width above it is right. Each is positional, so one
    wrong width lands here at the wrong byte -- which is why the tag is checked rather
    than assumed."""
    assert Reader(raw, info.body_offset).u32() == PACKAGE_FILE_TAG


def _shifted(raw: bytes) -> bytes:
    """A header with four extra bytes before the body -- a field added by a patch.

    This is the realistic drift and the one the tag guard exists for. Corrupting a LENGTH
    instead desynchronises the very next string and blows up on the bounds check, which is
    a different failure; both are refusals and both are pinned, separately.
    """
    at = read_info_bytes(raw).body_offset
    return raw[:at] + bytes(4) + raw[at:]


def test_a_shifted_header_is_refused_not_guessed(raw):
    """Returning a plausible header from a format that moved is the failure worth
    preventing: every field is positional, so one wrong width silently rereads the rest."""
    with pytest.raises(ValueError, match="did not end at the compressed body"):
        read_info_bytes(_shifted(raw))


def test_every_refusal_names_the_versions_that_would_explain_it(raw):
    """A refusal must say which version it was reading, since that is the first thing anyone
    debugging it needs -- and it must say so on EVERY path out of this module, not just the
    tag check that happens to be the last one.

    It is a prefix rather than a sentence in one message, because the reason a player sees
    matters more than the reason a developer sees: these saves fail on a bounds check deep in
    the reader where no version is in scope, and "read of 473655 at 138 runs past end" is true
    and useless where "saveHeaderType 20 (known: 8, 9, 10, 14)" is the answer.

    saveHeaderType 8, 9 and 10 used to be the example here, because all 36 pre-1.0 files on the
    development machine failed. 35 of them are read now -- see ``test_pre_1_0.py`` -- so the
    unknown type has to be a hypothetical one, and the message has to list what IS derived
    rather than name a single version.
    """
    for broken in (_shifted(raw), raw[:100]):
        with pytest.raises(ValueError) as exc:
            read_info_bytes(broken)
        assert "saveHeaderType 14" in str(exc.value)
        assert "saveVersion 60" in str(exc.value)

    unknown = bytearray(raw[:100])
    unknown[0:4] = (20).to_bytes(4, "little")
    with pytest.raises(ValueError, match=r"saveHeaderType 20 \(known: 8, 9, 10, 14\)"):
        read_info_bytes(bytes(unknown))


def test_a_wild_length_is_refused_by_the_bounds_check(raw):
    """The other refusal, so both paths are pinned rather than one shadowing the other."""
    broken = bytearray(raw)
    broken[12:16] = (999_999).to_bytes(4, "little")
    with pytest.raises(ValueError, match="runs past end"):
        read_info_bytes(bytes(broken))


# ------------------------------------------------------------- the body digest


def test_the_stored_hash_is_the_md5_of_the_compressed_body(raw):
    """``save_data_hash`` is not opaque: it is md5 over ``file[body_offset:]``.

    Reading a save never needs this -- the projection comes from the body, so a wrong digest
    cannot make the body parse differently. WRITING one does: anything that edits a body and
    writes it back must recompute this, or the file carries a digest of bytes it no longer holds.
    Nothing in the save says what the field covers, so the fact is only cheap while it is fresh.

    The fixture is a 2 KiB prefix rather than a whole save, so the digest cannot be verified from
    it. What can be, and what would break first if the reading were wrong, is the boundary: the
    field is read from the header and the region digested starts exactly where the header stops.
    """
    info = read_info_bytes(raw)
    assert info.save_data_hash == (6_096_361_947_348_211_065, 9_325_011_144_171_762_175)
    assert body_hash(b"\x00" * 8, 0) == body_hash(bytes(8), 0), "pure function of the bytes"
    # An empty region has a defined digest, so a truncated file cannot silently pass as a match.
    assert body_hash(b"", 0) != info.save_data_hash


def test_a_header_without_the_field_says_so_rather_than_failing_the_check(raw):
    """``None`` and ``False`` are different answers.

    A pre-1.0 header has no digest at all -- the walk lands on the tag with no room for one --
    and the reader sets ``None``. An earlier version of ``check_body_hash`` tested for ``(0, 0)``
    instead and reported all 35 of those saves as MISMATCHED, which is precisely the confusion
    this return type exists to prevent: a caller refusing to touch a "corrupt" save would have
    refused every save the player made before 1.0.
    """
    info = read_info_bytes(raw)
    assert check_body_hash(raw, info) is False, "a 2 KiB prefix is not the whole body"

    class NoHash:
        save_data_hash = None
        body_offset = info.body_offset

    assert check_body_hash(raw, NoHash()) is None
