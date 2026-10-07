"""What a cooked texture's bytes are: how long the mip chain is, and the blocks unpacked.

A mip chain is stored largest-first with nothing between the levels, so **its length is the
integrity check**: a ``.ubulk`` of any other length was re-cooked and is not decoded on a
guess. The decoders hand back **BGRA**, so Pillow is told ``"raw", "BGRA"``; read as
``"RGBA"`` the picture survives a glance with red and blue swapped.
"""

from __future__ import annotations

import math

from .imaging import BlockDecoder, ImageFactory, ImageT

#: Bytes per 4x4 block. BC1 (``PF_DXT1``) spends 8 and carries at most one bit of alpha; BC3
#: (``PF_DXT5``) spends 16, the extra eight being an interpolated alpha block. Same grid, same
#: chain, so one arithmetic serves both and only this number changes.
BC1_BLOCK_BYTES = 8
BC3_BLOCK_BYTES = 16

#: The side of a BC block, and the floor a level cannot go below: a 2x2 level is still one
#: whole block on disk, which is the only part of this arithmetic that is not multiplication.
BLOCK_PX = 4

#: The flag bit a Zen ``BulkDataMap`` entry carries when its payload is cooked INLINE in the
#: package's own export-data segment (UE's ``BULKDATA_ForceInlinePayload``). On build 495413
#: every inline mip level carries flags ``0x48`` and every streamed one ``0x00010501``.
INLINE_BULK_FLAG = 0x40


def block_mip_sizes(px: int, count: int, block_bytes: int) -> tuple[tuple[int, int], ...]:
    """``((side, bytes), ...)`` for a block-compressed chain of ``count`` levels from ``px``. A
    level narrower than four texels still costs one whole block, which is why this cannot be
    :func:`raw_mip_sizes` with a fractional texel size."""
    return tuple(
        ((px >> i), (max(px >> i, BLOCK_PX) // BLOCK_PX) ** 2 * block_bytes) for i in range(count)
    )


def bc1_mip_sizes(px: int, count: int) -> tuple[tuple[int, int], ...]:
    """``((side, bytes), ...)`` for a BC1 (DXT1) chain of ``count`` levels from ``px``."""
    return block_mip_sizes(px, count, BC1_BLOCK_BYTES)


def bc3_mip_sizes(px: int, count: int) -> tuple[tuple[int, int], ...]:
    """``((side, bytes), ...)`` for a BC3 (DXT5) chain of ``count`` levels from ``px``. 634 of
    the game's 747 item icons are cooked this way: an icon is a cut-out and needs the
    interpolated alpha BC1 has not got."""
    return block_mip_sizes(px, count, BC3_BLOCK_BYTES)


def raw_mip_sizes(px: int, count: int, texel_bytes: int) -> tuple[tuple[int, int], ...]:
    """The same, for a chain stored texel by texel: float16 heights, say, at two bytes. No
    block floor and no padding -- an uncompressed level is exactly as long as it looks."""
    return tuple(((px >> i), (px >> i) ** 2 * texel_bytes) for i in range(count))


def inline_chain_side(
    sizes: tuple[int, ...] | list[int], block_bytes: int | None, texel_bytes: int = 4
) -> int | None:
    """The mip-0 side of an inline chain whose bulk entries are ``sizes`` bytes, or ``None``.

    The side is derived from the first entry alone and the WHOLE list must then be exactly the
    chain that side predicts, halving by halving with the block floor included. ``block_bytes``
    picks the block arithmetic (BC3's 16, BC1's 8); ``None`` means raw texels at ``texel_bytes``
    each. A first entry that is no square, a side that is no power of two, or one later level
    off the derivation all give ``None``: a chain this reader cannot re-derive is a layout it
    no longer knows.
    """
    if not sizes:
        return None
    if block_bytes is not None:
        units, remainder = divmod(sizes[0], block_bytes)
        side = math.isqrt(units) * BLOCK_PX
        expected = block_mip_sizes(side, len(sizes), block_bytes)
    else:
        units, remainder = divmod(sizes[0], texel_bytes)
        side = math.isqrt(units)
        expected = raw_mip_sizes(side, len(sizes), texel_bytes)
    if remainder or side <= 0 or side & (side - 1):
        return None
    if [size for _side, size in expected] != list(sizes):
        return None
    return side


def decode_bc1_rgba(
    decoder: BlockDecoder, image_mod: ImageFactory[ImageT], raw: bytes, px: int
) -> ImageT:
    """One square BC1 level as an image."""
    return image_mod.frombytes("RGBA", (px, px), decoder.decode_bc1(raw, px, px), "raw", "BGRA")


def decode_bc3_rgba(
    decoder: BlockDecoder, image_mod: ImageFactory[ImageT], raw: bytes, px: int
) -> ImageT:
    """One square BC3 level as an image."""
    return image_mod.frombytes("RGBA", (px, px), decoder.decode_bc3(raw, px, px), "raw", "BGRA")


def decode_bgra8_rgba(image_mod: ImageFactory[ImageT], raw: bytes, px: int) -> ImageT:
    """One square ``PF_B8G8R8A8`` level as an image, taking no decoder because it is already
    texels. 113 of the game's 747 item icons are cooked uncompressed, four bytes a texel."""
    return image_mod.frombytes("RGBA", (px, px), raw, "raw", "BGRA")
