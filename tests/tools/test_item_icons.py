"""``tools/gen_item_icons.py``: the decisions it makes before it decodes anything.

The pure half of the generator -- which asset a docs path names, which ``.ubulk`` layouts it
will read, and what it does with one it will not -- asserted with no game install, no
container and no ``gen`` extra. That is deliberate and it is the same split
``tests/mapgen/test_generators.py`` makes: what is worth pinning here is the reasoning, and the
reasoning is the part that runs on a clone.

The decode itself is not faked into a picture. ``decode_icon`` is driven with stand-ins for
``Package``, ``texture2ddecoder`` and Pillow, so what is under test is which branch it takes
and which refusal it names, not anybody's block decoder -- ``tests/core/gameassets/test_textures.py``
owns the argument pair those decoders are called with.
"""

from __future__ import annotations

import pytest

from tools import gen_item_icons as gen


def test_the_bulk_layouts_are_the_four_lengths_the_container_actually_holds():
    """Every icon in the game is one of these, and a fifth length is a re-cook.

    The table is DERIVED from the mip arithmetic rather than typed, which is the point: it
    keeps the check honest when the chain tail or the block size changes, instead of the
    check drifting away from the thing it checks. Measured over all 744 icons on build
    495413 -- these four and nothing else.
    """
    layouts = gen.bulk_layouts()

    assert layouts[("PF_DXT5", 81_920)] == 256
    assert layouts[("PF_DXT5", 344_064)] == 512
    assert layouts[("PF_B8G8R8A8", 327_680)] == 256
    assert layouts[("PF_B8G8R8A8", 1_376_256)] == 512
    # No length names two sides, in either format, which is what makes the lookup a decision
    # rather than a guess -- and is not obvious, because the chains overlap heavily.
    assert len(layouts) == len(gen.PIXEL_FORMATS) * len(gen.CANDIDATE_PX)
    assert 81_920 not in {length for fmt, length in layouts if fmt == "PF_B8G8R8A8"}


def test_a_chain_runs_from_the_textures_own_side_down_to_the_tail_and_no_further():
    """Why the lengths above are what they are: the small mips are cooked inline, not here.

    A 256 px BC3 icon is two levels in its ``.ubulk`` and a 512 is three -- not seven and
    eight, which is what a chain running down to 1 px would be. The count is derived from
    the tail rather than passed in, so this is the property that would break first if a
    future cook streamed the whole chain.
    """
    assert gen.chain_length(256, block=True) == 81_920
    assert gen.chain_length(512, block=True) == 344_064
    assert gen.chain_length(gen.MIP_TAIL_PX, block=True) == 16_384, "the tail alone is one level"
    assert gen.chain_length(gen.MIP_TAIL_PX, block=False) == gen.MIP_TAIL_PX**2 * 4


@pytest.mark.parametrize(
    ("icon", "expected"),
    [
        (
            (
                "Texture2D /Game/FactoryGame/Resource/Parts/IronPlate/UI/"
                "IconDesc_IronPlates_256.IconDesc_IronPlates_256"
            ),
            gen.MOUNT + "FactoryGame/Resource/Parts/IronPlate/UI/IconDesc_IronPlates_256",
        ),
        ("None", None),
        ("", None),
        ("Texture2D /Engine/Something/Else.Else", None),
    ],
)
def test_a_docs_icon_path_becomes_a_container_stem_or_says_it_cannot(icon, expected):
    """``mSmallIcon`` is a UE object path; the container wants a mount-relative stem.

    ``None`` for the three classes whose value is literally ``"None"`` and for anything this
    pattern does not recognise -- both are "the dump names no picture", which the generator
    counts as coverage rather than treating as an error.
    """
    assert gen.container_stem(icon) == expected


def test_the_pixel_format_comes_off_the_package_and_an_unknown_one_is_a_refusal():
    """Read from the asset, so the ``.ubulk`` length stays an INDEPENDENT check.

    Inferring the format from the length instead would make the two agree by construction
    and the check vacuous. Two entries is a refusal too: a package naming both formats is
    not one this reader can decode on a coin toss.
    """
    assert gen.pixel_format(["ImportedSize", "PF_DXT5"]) == "PF_DXT5"
    assert gen.pixel_format(["PF_B8G8R8A8"]) == "PF_B8G8R8A8"
    assert gen.pixel_format(["ImportedSize"]) is None
    assert gen.pixel_format(["PF_BC7"]) is None, "no BC7 is in this game, and a guess is worse"
    assert gen.pixel_format(["PF_DXT5", "PF_B8G8R8A8"]) is None


class _Packages:
    """``core.gameassets.packages`` as far as the generator is concerned: ``Package.names``."""

    def __init__(self, names) -> None:
        self._names = names

    def Package(self, blob):  # a class name on the real module, not a method name
        return self

    @property
    def names(self):
        return self._names


class _Imaging:
    """Pillow's ``frombytes``, remembering the square it was asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def frombytes(self, mode, size, data, decoder_name, args):
        self.calls.append((mode, size, len(data), decoder_name, args))
        return "an image"


class _Decoder:
    def decode_bc3(self, raw, width, height):
        return bytes(width * height * 4)


def _decode(names, bulk):
    return gen.decode_icon(
        _Packages(names), _Decoder(), _Imaging(), b"asset", bulk, gen.bulk_layouts()
    )


def test_a_good_icon_decodes_mip_zero_and_reports_the_side_it_came_from():
    """The whole happy path: name the format, look the length up, take the first level.

    ``source_px`` is carried back rather than recomputed by the caller because it is what
    decides whether the PNG is resampled at all -- and it is recorded per icon in the
    manifest, so a reader can see which of their icons are the game's own pixels.
    """
    decoded, why = _decode(["PF_DXT5"], bytes(344_064))
    assert why is None
    image, px, fmt = decoded
    assert (image, px, fmt) == ("an image", 512, "PF_DXT5")

    decoded, why = _decode(["PF_B8G8R8A8"], bytes(327_680))
    assert why is None
    assert decoded[1:] == (256, "PF_B8G8R8A8")


@pytest.mark.parametrize(
    ("names", "bulk_bytes", "expected"),
    [
        (["ImportedSize"], 344_064, "no known PF_ constant"),
        (["PF_DXT5"], 999, "which is no chain from"),
        (["PF_DXT5"], 0, "which is no chain from"),
    ],
)
def test_an_icon_this_reader_cannot_name_is_skipped_and_says_why(names, bulk_bytes, expected):
    """One re-cooked icon must not cost the other 743, and must not be guessed at either.

    Every refusal comes back as a SENTENCE that lands in the manifest under ``unresolved``,
    because a coverage number with no list behind it is a claim rather than a measurement.
    A length off the derived table specifically means the game changed, which is the one
    thing a reader has to be told rather than have worked around.
    """
    decoded, why = _decode(names, bytes(bulk_bytes))
    assert decoded is None
    assert expected in why


class _InlinePackages:
    """The generator's view of a package whose mips are cooked inline: names, the
    header size, and a ``bulk_entries()`` that answers what the Zen header would."""

    def __init__(self, names, entries, header_size=100) -> None:
        self._names = names
        self._entries = entries
        self.header_size = header_size

    def Package(self, blob):  # a class name on the real module, not a method name
        return self

    @property
    def names(self):
        return self._names

    def bulk_entries(self):
        if isinstance(self._entries, Exception):
            raise self._entries
        return self._entries


def _entry(offset, size, flags=0x48):
    """One BulkDataMap entry as ``packages.bulk_data_entries`` shapes it. 0x48 is what
    every inline level actually carries on build 495413; the ``.ubulk`` halves carry
    0x00010501, and the single bit 0x40 is what the generator tests."""
    return {
        "index": 0,
        "offset": offset,
        "duplicate_offset": 2**64 - 1,
        "size": size,
        "flags": flags,
        "cooked_index": 0,
    }


def _decode_inline(names, entries, blob=None):
    if blob is None:
        # header_size 100 + offset 0, so mip 0 is blob[100:100+size] by construction.
        size = entries[0]["size"] if entries and not isinstance(entries, Exception) else 0
        blob = bytes(100) + bytes(size)
    return gen.decode_inline_icon(_InlinePackages(names, entries), _Decoder(), _Imaging(), blob)


def test_an_icon_with_no_ubulk_decodes_its_inline_mip_zero_from_the_export_segment():
    """The recovery that turned 744 of 750 into 747: no ``.ubulk`` is not no picture.

    The three shapes asserted are the container's own -- a 256 px BC3 chain of nine, a
    512 px BGRA chain of ten, and the single-entry 8 px swatch -- and the payload is read
    at ``header_size + offset``, which is the one offset convention the whole path rests
    on: it was measured (960 + 297 landed exactly on the bytes the tag walk predicts), and
    a future cook that moves it fails the length slice rather than decoding garbage.
    """
    sizes = [65_536, 16_384, 4_096, 1_024, 256, 64, 16, 16, 16]
    offsets, at = [], 0
    for size in sizes:
        offsets.append(at)
        at += size + 16  # 12 bytes of mip dimensions + the next entry's index int32
    entries = [_entry(offset, size) for offset, size in zip(offsets, sizes, strict=True)]
    blob = bytes(100) + bytes(at)

    decoded, why = _decode_inline(["PF_DXT5"], entries, blob)
    assert why is None
    image, px, fmt = decoded
    assert (image, px, fmt) == ("an image", 256, "PF_DXT5")

    decoded, why = _decode_inline(["PF_B8G8R8A8"], [_entry(0, 256)])
    assert why is None
    assert decoded[1:] == (8, "PF_B8G8R8A8"), "the white swatch is 8 px and stays 8 px"


@pytest.mark.parametrize(
    ("names", "entries", "expected"),
    [
        (["ImportedSize"], [_entry(0, 256)], "no known PF_ constant"),
        (["PF_DXT5"], ValueError("short header"), "bulk data map is unreadable"),
        (["PF_DXT5"], [], "nowhere the mips could be"),
        (["PF_DXT5"], [_entry(0, 65_536, flags=0x00010501)], "not every bulk entry is inline"),
        (["PF_DXT5"], [_entry(0, 65_537)], "no mip chain this reader knows"),
        (["PF_B8G8R8A8"], [_entry(0, 100)], "no mip chain this reader knows"),
    ],
)
def test_an_inline_icon_this_reader_cannot_re_derive_is_skipped_and_says_why(
    names, entries, expected
):
    """The ``.ubulk`` refusals, transposed: every exit is a sentence for the manifest.

    The non-inline flag case is the one worth a second look: an entry pointing into a
    ``.ubulk`` that is not in the container is a cook this reader does not know, and
    treating its offset as an export-segment offset would slice plausible bytes out of
    the wrong file region -- a picture, not an error.
    """
    decoded, why = _decode_inline(names, entries)
    assert decoded is None
    assert expected in why


def test_an_inline_mip_that_runs_off_the_package_is_a_refusal_not_a_short_decode():
    """A truncated blob must not become a half-picture: the slice is length-checked."""
    decoded, why = _decode_inline(["PF_B8G8R8A8"], [_entry(0, 256)], blob=bytes(100) + bytes(255))
    assert decoded is None
    assert "runs off the end of the package" in why


def test_the_written_size_never_upscales_and_never_resamples_what_it_need_not():
    """Two properties of ``to_png``, and both are about not inventing pixels.

    194 of the 744 icons are 256 px in the container, so at the default they are written as
    the game authored them with no filter in the path at all. And a source SMALLER than the
    wanted size stays its own size rather than being blown up into a picture this file made
    -- which would sit in the directory looking exactly like the 743 that are real.
    """
    image_mod = pytest.importorskip("PIL.Image")
    made = image_mod.new("RGBA", (256, 256), (10, 20, 30, 255))

    same = image_mod.open(_as_png(gen.to_png(image_mod, made, 256, 256)))
    assert same.size == (256, 256)

    smaller = image_mod.open(_as_png(gen.to_png(image_mod, made, 256, 512)))
    assert smaller.size == (256, 256), "asked for 512, and 256 is what there was"

    halved = image_mod.open(_as_png(gen.to_png(image_mod, made, 256, 128)))
    assert halved.size == (128, 128)


def _as_png(blob: bytes):
    import io

    return io.BytesIO(blob)
