"""The mip chain's arithmetic, and the one line that keeps the ocean blue.

Both are checks a generator makes before it decodes anything, and both are the kind of
thing that is right until somebody simplifies it. The chain totals are asserted against the
numbers the two container tools actually check their ``.ubulk`` files against -- 11,182,080
bytes of map slice, 11,173,888 of interface heightfield -- because deriving the total is
only worth anything if the derivation still lands on the file's real length.

``decode_bc1_rgba`` is exercised with stand-ins for ``texture2ddecoder`` and Pillow. That
is not a convenience: this suite runs on a machine with neither installed, and what is
under test is the argument pair, not anybody's block decoder.
"""

from __future__ import annotations

from satisfactory_mcp.core.gameassets.textures import (
    BC3_BLOCK_BYTES,
    bc1_mip_sizes,
    bc3_mip_sizes,
    decode_bc1_rgba,
    decode_bc3_rgba,
    decode_bgra8_rgba,
    inline_chain_side,
    raw_mip_sizes,
)


def test_the_map_slices_chain_is_the_length_the_generator_refuses_to_read_past():
    """Four 4096 px BC1 slices: six levels, 11,182,080 bytes, mip 0 the first 8,388,608."""
    chain = bc1_mip_sizes(4096, 6)

    assert [px for px, _size in chain] == [4096, 2048, 1024, 512, 256, 128]
    assert chain[0][1] == 8_388_608
    assert sum(size for _px, size in chain) == 11_182_080


def test_a_bc1_level_never_costs_less_than_one_block():
    """Below 4 px a side the level is still one 4x4 block, which is why this is not a ratio.

    The floor is the only part of BC1's arithmetic that is not multiplication, so it is the
    only part a rewrite can get wrong while every number a real texture produces stays
    right. Levels that small never appear in this project's chains -- which is exactly why
    the property is asserted here rather than left to be discovered by a re-cook.
    """
    assert bc1_mip_sizes(8, 4) == ((8, 32), (4, 8), (2, 8), (1, 8))
    assert bc1_mip_sizes(4096, 1) == ((4096, 8_388_608),)
    assert bc1_mip_sizes(4096, 0) == ()


def test_the_interface_heightfield_chain_is_texel_by_texel():
    """2048 down to 128 at two bytes a float16 texel, and no block floor anywhere in it."""
    chain = raw_mip_sizes(2048, 5, 2)

    assert [px for px, _size in chain] == [2048, 1024, 512, 256, 128]
    assert chain[0][1] == 2048 * 2048 * 2
    assert sum(size for _px, size in chain) == 11_173_888
    # Every level is exactly its own area: no padding, no minimum, unlike BC1 above.
    assert all(size == px * px * 2 for px, size in chain)
    assert raw_mip_sizes(4, 3, 1) == ((4, 16), (2, 4), (1, 1))


class _Decoder:
    """``texture2ddecoder`` as far as this module is concerned: one function."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, int, int]] = []

    def decode_bc1(self, raw: bytes, width: int, height: int) -> bytes:
        self.calls.append((raw, width, height))
        return b"%s as %dx%d BGRA" % (raw, width, height)

    def decode_bc3(self, raw: bytes, width: int, height: int) -> bytes:
        self.calls.append((raw, width, height))
        return b"%s as %dx%d BGRA" % (raw, width, height)


class _Imaging:
    """Pillow as far as this module is concerned: ``frombytes`` and what it was told."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def frombytes(self, mode, size, data, decoder_name, args):
        self.calls.append((mode, size, data, decoder_name, args))
        return "an image"


def test_the_decode_says_bgra_out_loud_because_rgba_also_produces_a_picture():
    """The raw/BGRA pair is the whole trick, and dropping it fails silently.

    ``decode_bc1`` hands back B, G, R, A. Read as ``"RGBA"`` the red and blue channels
    swap: the ocean goes orange and the result still looks like a stylised map, which is
    why this is asserted rather than eyeballed. The square is passed as one number because
    every level in this project is one.
    """
    decoder, imaging = _Decoder(), _Imaging()

    assert decode_bc1_rgba(decoder, imaging, b"blocks", 4096) == "an image"

    assert decoder.calls == [(b"blocks", 4096, 4096)]
    mode, size, data, decoder_name, args = imaging.calls[0]
    assert (mode, size) == ("RGBA", (4096, 4096))
    assert (decoder_name, args) == ("raw", "BGRA"), "reading BGRA as RGBA turns the ocean orange"
    assert data == b"blocks as 4096x4096 BGRA", "the decoder's own bytes, unmodified"


def test_the_item_icons_two_chains_are_the_lengths_the_generator_refuses_to_read_past():
    """The four ``.ubulk`` lengths every icon in the game has, derived rather than typed.

    ``tools/gen_item_icons.py`` builds its whole integrity check out of these: a bulk chunk
    holds the chain from the texture's own side down to 128 px, so its length names the
    (format, side) pair uniquely, and a file of any other length was re-cooked. The numbers
    on the right are what the container actually holds on build 495413 -- measured over all
    744 icons -- so this fails if either the arithmetic or the game's cook layout moves.
    """
    assert sum(size for _px, size in bc3_mip_sizes(256, 2)) == 81_920
    assert sum(size for _px, size in bc3_mip_sizes(512, 3)) == 344_064
    assert sum(size for _px, size in raw_mip_sizes(256, 2, 4)) == 327_680
    assert sum(size for _px, size in raw_mip_sizes(512, 3, 4)) == 1_376_256

    # And mip 0, which is the only level the generator ever decodes: the first N bytes with
    # no offset to guess, exactly as the map slices are read.
    assert bc3_mip_sizes(256, 1)[0][1] == 65_536
    assert bc3_mip_sizes(512, 1)[0][1] == 262_144


def test_the_three_inline_icons_chains_re_derive_and_nothing_else_does():
    """``inline_chain_side``: the ``.ubulk`` length check for a texture that has none.

    The three shapes are the three the container actually holds on build 495413 -- Liquid
    Biofuel's nine-entry BC3 chain to 1 px (block floor and all), the Explorer path's
    ten-entry BGRA chain, and the single-entry 8 px white swatch -- because the check is
    only worth having if the derivation lands on the real per-level sizes, which is the
    same reason the chain totals above are asserted against real file lengths.
    """
    biofuel = [65_536, 16_384, 4_096, 1_024, 256, 64, 16, 16, 16]
    assert inline_chain_side(biofuel, BC3_BLOCK_BYTES) == 256

    explorer = [1_048_576, 262_144, 65_536, 16_384, 4_096, 1_024, 256, 64, 16, 4]
    assert inline_chain_side(explorer, None) == 512

    assert inline_chain_side([256], None) == 8, "the white swatch: one 8 px BGRA level"

    # A partial chain still re-derives -- the depth comes from the entries, not a rule --
    # which is what keeps this the same posture as chain_length taking a count.
    assert inline_chain_side([65_536, 16_384], BC3_BLOCK_BYTES) == 256


def test_an_inline_chain_off_the_derivation_is_none_never_a_guess():
    """Every way a chain can fail to be the one entry 0 predicts, and each is a refusal.

    The failure mode being guarded is the ``.ubulk`` one transposed: a re-cooked texture
    whose levels are real bytes at wrong sizes would decode into a plausible picture of
    the wrong shape, so anything the arithmetic cannot re-derive is None and the caller
    counts it rather than decoding it.
    """
    assert inline_chain_side([], BC3_BLOCK_BYTES) is None, "no entries is no chain"
    assert inline_chain_side([65_537], BC3_BLOCK_BYTES) is None, "not whole blocks"
    assert inline_chain_side([48], BC3_BLOCK_BYTES) is None, "3 blocks is no square"
    assert inline_chain_side([100], None) is None, "5 px is square but no power of two"
    assert inline_chain_side([0], None) is None
    # One level off the halving breaks the WHOLE chain, not just that level.
    assert inline_chain_side([65_536, 16_384, 4_095], BC3_BLOCK_BYTES) is None
    # More entries than a chain from this side can have run past 1 px and off the table.
    assert inline_chain_side([16, 4, 4, 4], None) is None


def test_bc3_is_bc1s_grid_at_twice_the_bytes_and_the_same_block_floor():
    """One arithmetic, one number different -- which is why they share ``block_mip_sizes``.

    The alpha block is the whole of the difference: same 4x4 grid, same largest-first chain,
    same "a level narrower than four texels still costs one block". Asserting the RATIO
    rather than a second table of numbers is what stops the two drifting apart.
    """
    for px, count in ((4096, 6), (512, 3), (8, 4)):
        one, three = bc1_mip_sizes(px, count), bc3_mip_sizes(px, count)
        assert [side for side, _ in one] == [side for side, _ in three]
        assert [size * 2 for _, size in one] == [size for _, size in three]
    assert bc3_mip_sizes(8, 4) == ((8, 64), (4, 16), (2, 16), (1, 16))


def test_the_two_icon_decoders_say_bgra_out_loud_for_bc1s_reason():
    """Copper comes out cyan and a candy cane comes out blue, and both look deliberate.

    The failure mode is worse here than on the map, which is why it is asserted on both
    paths: an item icon read as ``"RGBA"`` is a plausible-looking picture of the wrong
    colour, sitting in a directory of 743 correct ones.

    The uncompressed path takes NO decoder and is the point of it being here at all: 111 of
    the icons are ``PF_B8G8R8A8``, already texels in the same B, G, R, A order, so the only
    thing left to get wrong is the argument pair -- and it should be got wrong or got right
    in one place for all three formats.
    """
    decoder, imaging = _Decoder(), _Imaging()

    assert decode_bc3_rgba(decoder, imaging, b"blocks", 512) == "an image"
    assert decoder.calls == [(b"blocks", 512, 512)]
    mode, size, data, decoder_name, args = imaging.calls[0]
    assert (mode, size) == ("RGBA", (512, 512))
    assert (decoder_name, args) == ("raw", "BGRA"), "reading BGRA as RGBA turns copper cyan"
    assert data == b"blocks as 512x512 BGRA", "the decoder's own bytes, unmodified"

    raw = bytes(256 * 256 * 4)
    assert decode_bgra8_rgba(imaging, raw, 256) == "an image"
    mode, size, data, decoder_name, args = imaging.calls[1]
    assert (mode, size, data) == ("RGBA", (256, 256), raw), "no decompression step at all"
    assert (decoder_name, args) == ("raw", "BGRA")
    assert len(decoder.calls) == 1, "the uncompressed path must not reach a block decoder"
