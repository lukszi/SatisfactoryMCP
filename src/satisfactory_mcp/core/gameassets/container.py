"""The game's container and the map sheet inside it: what every map generator opens first."""

from __future__ import annotations

from pathlib import Path

from .imaging import BlockDecoder, ImageFactory, ImageT
from .iostore import IoStore, oodle_decompress
from .textures import bc1_mip_sizes, decode_bc1_rgba

__all__ = [
    "CONTAINER",
    "MIP0_BYTES",
    "MIP_SIZES",
    "SHEET_PX",
    "SLICES",
    "SLICE_DIR",
    "TILE_PX",
    "UBULK_BYTES",
    "open_container",
    "paks_dir",
    "read_artwork_sheet",
    "read_slice",
]

#: The container every map generator reads, under ``FactoryGame/Content/Paks``.
CONTAINER = "FactoryGame-Windows"

#: The mount-relative directory holding the four slices, inside FactoryGame-Windows.utoc.
SLICE_DIR = "../../../FactoryGame/Content/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/"

#: The suffix is ``<col>-<row>``: 0-0 is NW, 1-0 NE, 0-1 SW, 1-1 SE, proven per run by
#: ``seam_residuals``.
SLICES = ("Map_0-0", "Map_1-0", "Map_0-1", "Map_1-1")

TILE_PX = 4096
SHEET_PX = TILE_PX * 2

#: The ``.ubulk`` mip chain, largest-first: 4096 down to 128, BC1's 8 bytes per 4x4 block.
#: Derived so that the file-length check below is arithmetic rather than a typed-in number.
MIP_SIZES = bc1_mip_sizes(TILE_PX, 6)
MIP0_BYTES = MIP_SIZES[0][1]
UBULK_BYTES = sum(size for _px, size in MIP_SIZES)


def paks_dir(game: Path) -> Path:
    """Where an install keeps its containers."""
    return game / "FactoryGame" / "Content" / "Paks"


def open_container(game: Path) -> IoStore:
    """The install's ``FactoryGame-Windows`` container, blocks decompressed by ``ooz``."""
    return IoStore(paks_dir(game), CONTAINER, oodle_decompress)


def read_slice(store: IoStore, name: str) -> bytes:
    """Mip 0's BC1 blocks for one slice, with the length check that guards the layout."""
    path = f"{SLICE_DIR}{name}.ubulk"
    if path not in store.by_path:
        raise SystemExit(
            f"{name}.ubulk is not in the container. The map slices moved or were renamed, "
            "which means the game changed; nothing here can be trusted until that is "
            "looked at."
        )
    raw = store.read_path(path)
    if len(raw) != UBULK_BYTES:
        chain = ", ".join(f"{px}x{px}" for px, _size in MIP_SIZES)
        raise SystemExit(
            f"{name}.ubulk is {len(raw)} bytes, expected exactly {UBULK_BYTES} -- the mip "
            f"chain {chain} at 8 bytes per 4x4 BC1 block. A different length means the "
            "texture was re-cooked at another size or mip count, i.e. the game changed. "
            "Refusing to decode mip 0 out of a file whose layout is no longer known."
        )
    return raw[:MIP0_BYTES]


def read_artwork_sheet(
    store: IoStore, decoder: BlockDecoder, image_mod: ImageFactory[ImageT]
) -> ImageT:
    """The game's own 8192 px map sheet, stitched out of its four BC1 slices.

    Read from the container rather than from ``data/local/map.png``: the PNG is the same
    pixels, but a reader may not have run the tool that writes it.
    """
    sheet = image_mod.new("RGB", (SHEET_PX, SHEET_PX))
    for name in SLICES:
        col, row = (int(value) for value in name.split("_")[1].split("-"))
        slice_image = decode_bc1_rgba(decoder, image_mod, read_slice(store, name), TILE_PX)
        sheet.paste(slice_image.convert("RGB"), (col * TILE_PX, row * TILE_PX))
    return sheet
