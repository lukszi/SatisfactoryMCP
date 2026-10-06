"""Cut data/local/icons/ -- one PNG per item -- out of the installed game.

    uv run --extra gen python tools/gen_item_icons.py

An icon is Coffee Stain's artwork, so this is a loader like ``tools/gen_map_image.py``: it
reads the reader's own install into gitignored ``data/local/`` and commits none of it.
``Docs/en-US.json`` names each item's ``Texture2D``; the formats and the measured coverage are
in docs/DEVELOPING.md.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT / "tools" / "mapgen" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mapgen.common import LOCAL_DIR, base_parser, require_gen
from satisfactory_mcp.core.gameassets.container import CONTAINER, paks_dir
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    install_directory,
    installed_build,
    installed_build_from_exe,
    read_str_path,
)
from satisfactory_mcp.core.gameassets.textures import (
    BC3_BLOCK_BYTES,
    INLINE_BULK_FLAG,
    bc3_mip_sizes,
    decode_bc3_rgba,
    decode_bgra8_rgba,
    inline_chain_side,
    raw_mip_sizes,
)
from satisfactory_mcp.core.gamedata.loader import load_docs

#: Derived from ``--game``: the icons and the class names that point at them must come
#: from ONE install, and a ``SATISFACTORY_DOCS`` pointing elsewhere would mix two builds.
DOCS_SUFFIX = Path("CommunityResources") / "Docs" / "en-US.json"

MOUNT = "../../../FactoryGame/Content/"

#: ``Texture2D /Game/Foo/Bar/Icon_256.Icon_256`` -> ``/Game/Foo/Bar/Icon_256``: what
#: follows the first dot is the object name inside the package, always the same word again.
ICON_PATH_RE = re.compile(r"Texture2D\s+(/Game/\S+?)\.")

#: ``mSmallIcon`` and ``mPersistentBigIcon`` hold the identical string on all 747 classes
#: that have either, so there is one texture per item and nothing to choose between.
ICON_FIELD = "mSmallIcon"

#: Having a physical form is what makes a docs class an item, the same test
#: ``gamedata.normalize._build_items`` applies: 13 native classes carry ``mForm``, and
#: restricting to ``FGItemDescriptor`` would miss the biomass and the nuclear fuel rods.
FORM_FIELD = "mForm"

#: BC3 blocks; the other format is raw BGRA texels. Measured: there is no third, and no BC7.
BC3_FORMAT = "PF_DXT5"
PIXEL_FORMATS = (BC3_FORMAT, "PF_B8G8R8A8")

#: Where the bulk chain stops. A cooked ``Texture2D`` keeps its smallest levels inline and
#: streams the rest; stated as the tail because the mip count differs per size.
MIP_TAIL_PX = 128

#: The sides a cooked icon is allowed to be, largest first. Wider than what the game ships
#: so a re-cook at another size is read rather than refused.
CANDIDATE_PX = (2048, 1024, 512, 256, 128)

#: 256 is the smaller of the two sizes the game authors, so it is the only default that
#: invents no pixel: sources at or under it are untouched and 512 px ones halve exactly.
ICON_PX = 256

#: The three ways a class ends up under ``unresolved``. Machine-readable, because
#: :data:`KIND_NO_ICON` means the frontend's text tile is correct forever while the other
#: two mean a picture exists and this reader missed it.
KIND_NO_ICON = "no-icon-in-docs"
KIND_NOT_IN_CONTAINER = "asset-not-in-container"
KIND_UNDECODED = "undecoded"

ICONS_DIR_NAME = "icons"
MANIFEST_NAME = "manifest.json"

#: One path, used by the writer and by the staleness guard, so the two cannot disagree
#: about where the pin lives.
BUILD_PIN_PATH = ("_meta", "source", "game_version_pinned")


def chain_length(px: int, is_bc3: bool) -> int:
    """Total bytes of the ``.ubulk`` chain for one square side, in BC3 or in raw BGRA.

    Both chains run from ``px`` down to :data:`MIP_TAIL_PX` inclusive.
    """
    count = max(px, MIP_TAIL_PX).bit_length() - MIP_TAIL_PX.bit_length() + 1
    sizes = bc3_mip_sizes(px, count) if is_bc3 else raw_mip_sizes(px, count, 4)
    return sum(size for _side, size in sizes)


def bulk_layouts() -> dict[tuple[str, int], int]:
    """``{(pixel format, ubulk length): side}`` -- every chain this reader can name.

    The whole integrity check, and exactly one (format, side) pair produces any given
    length. A length that is not in here is a texture cooked at a size or a mip tail this
    file cannot read, so that icon is skipped and counted rather than decoded on a guess.
    """
    return {
        (fmt, chain_length(px, fmt == BC3_FORMAT)): px
        for fmt in PIXEL_FORMATS
        for px in CANDIDATE_PX
    }


def container_stem(icon: str) -> str | None:
    """``Texture2D /Game/Foo/Icon_256.Icon_256`` -> the mount-relative stem, or ``None``.

    ``None`` for the three classes whose ``mSmallIcon`` is literally ``"None"`` and for any
    value this pattern does not recognise: both mean the dump names no picture, which is a
    coverage number rather than an error.
    """
    match = ICON_PATH_RE.search(icon or "")
    if match is None:
        return None
    return MOUNT + match.group(1).replace("/Game/", "", 1)


def icon_classes(dump) -> list[tuple[str, str]]:
    """``[(class name, icon asset path), ...]`` from a loaded docs dump, sorted so a manifest
    diff is readable. Classes with no icon keep an empty path and are counted by the caller."""
    out = []
    for classes in dump.by_native.values():
        for entry in classes:
            if FORM_FIELD not in entry or "ClassName" not in entry:
                continue
            out.append((str(entry["ClassName"]), str(entry.get(ICON_FIELD) or "").strip()))
    return sorted(out)


def path_index(store: IoStore) -> dict[str, str]:
    """Lowercased container path -> the path as the container spells it.

    Five items -- the MAM, the Cyberwagon, the Medkit and both Golf Carts -- differ from
    the container's spelling only in a directory's case, and an exact lookup loses them
    while looking like five items with no artwork.
    """
    return {path.lower(): path for path in store.paths.values()}


def pixel_format(package_names) -> str | None:
    """The ``PF_`` constant in a package's name table, or ``None`` if it holds none of ours.

    The format the asset states, never one inferred from a length, which is what keeps the
    length check independent instead of circular.
    """
    found = [name for name in package_names if name in PIXEL_FORMATS]
    return found[0] if len(found) == 1 else None


def _decode_mip0(decoder, image_mod, raw: bytes, px: int, is_bc3: bool):
    """Mip 0's bytes as an RGBA image, through the decoder for its format."""
    if is_bc3:
        return decode_bc3_rgba(decoder, image_mod, raw, px)
    return decode_bgra8_rgba(image_mod, raw, px)


def decode_bulk_icon(package_mod, decoder, image_mod, blob: bytes, bulk: bytes, layouts: dict):
    """``((image, source side, pixel format), None)`` for one icon, or ``(None, reason)``.

    The three refusals are counted and named in the manifest rather than raised, because
    one re-cooked icon must not cost the other 746.
    """
    fmt = pixel_format(package_mod.Package(blob).names)
    if fmt is None:
        return None, "no known PF_ constant in the package name table"
    px = layouts.get((fmt, len(bulk)))
    if px is None:
        return None, f"{fmt} .ubulk is {len(bulk)} B, which is no chain from {CANDIDATE_PX}"
    is_bc3 = fmt == BC3_FORMAT
    mip0 = (bc3_mip_sizes(px, 1) if is_bc3 else raw_mip_sizes(px, 1, 4))[0][1]
    if len(bulk) < mip0:
        return None, f"{fmt} .ubulk is shorter than its own mip 0"
    return (_decode_mip0(decoder, image_mod, bulk[:mip0], px, is_bc3), px, fmt), None


def decode_inline_icon(package_mod, decoder, image_mod, blob: bytes):
    """The same contract as :func:`decode_bulk_icon`, for a texture with no ``.ubulk`` at all.

    Three of the 747 icons cook their whole mip chain inline in the ``.uasset``. The Zen
    header's ``BulkDataMap`` names every level's offset and length, one entry per mip, with
    the offset relative to the export-data segment, so mip 0 is
    ``blob[header_size + offset :][: size]`` of the first entry and no property tail is
    parsed.

    The length check transposes rather than disappears: the entry list must be exactly the
    chain :func:`~textures.inline_chain_side` re-derives from its largest level, and every
    entry must say it is inline -- one pointing into a ``.ubulk`` that is not in the
    container is a cook this reader does not know.
    """
    pkg = package_mod.Package(blob)
    fmt = pixel_format(pkg.names)
    if fmt is None:
        return None, "no known PF_ constant in the package name table"
    try:
        entries = pkg.bulk_entries()
    except ValueError as exc:
        return None, f"no .ubulk, and the bulk data map is unreadable ({exc})"
    if not entries:
        return None, "no .ubulk and an empty bulk data map: nowhere the mips could be"
    if not all(entry["flags"] & INLINE_BULK_FLAG for entry in entries):
        return None, "no .ubulk in the container, yet not every bulk entry is inline"
    is_bc3 = fmt == BC3_FORMAT
    sizes = [entry["size"] for entry in entries]
    px = inline_chain_side(sizes, BC3_BLOCK_BYTES if is_bc3 else None)
    if px is None:
        return None, f"{fmt} inline entries of {sizes} B are no mip chain this reader knows"
    first = entries[0]
    raw = blob[pkg.header_size + first["offset"] :][: first["size"]]
    if len(raw) != first["size"]:
        return None, "inline mip 0 runs off the end of the package"
    return (_decode_mip0(decoder, image_mod, raw, px, is_bc3), px, fmt), None


def to_png(image_mod, image, px: int, want: int) -> bytes:
    """One decoded level as PNG bytes at ``want`` px, resampled only when it has to be.

    A level already at the wanted size is returned untouched rather than round-tripped
    through a resize that would be the identity with a filter's rounding on top. Nothing is
    ever enlarged: an 8 px source asked for at 256 stays 8, because an upscale is a picture
    this file invented sitting in the directory looking like the rest.
    """
    import io

    if px > want:
        image = image.resize((want, want), image_mod.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def pinned_build(out_dir: Path) -> str | None:
    """The build the icons already on disk say they were cut from, or ``None``."""
    try:
        existing = json.loads((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return read_str_path(existing, BUILD_PIN_PATH)


def build_manifest(*, pin: str, branch: str | None, docs, entries: dict, unresolved: dict, stats):
    """The sidecar: what a reader needs to know before trusting a directory of pictures.

    ``source`` says which install these came out of, down to the docs dump's sha256, and is
    what the staleness guard reads. ``icons`` is the map a client uses. ``unresolved`` is
    what did not make it, per class, as ``{"kind", "detail"}``.
    """
    return {
        "_meta": {
            "generator": "tools/gen_item_icons.py",
            "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": {
                "game_version_pinned": pin,
                "game_build_string": branch,
                "container": f"{CONTAINER}.utoc",
                "docs": {"path": str(docs.path), "sha256": docs.sha256, "bytes": docs.size},
                "icon_field": ICON_FIELD,
                "pixel_formats": list(PIXEL_FORMATS),
                "note": (
                    "mip 0 of each Texture2D's .ubulk, decoded from the format the package "
                    "names and written as PNG at the square side under counts.written_px, "
                    "never upscaled. The .ubulk holds the chain from the texture's own side "
                    f"down to {MIP_TAIL_PX} px, so its length is a free check that the layout "
                    "is still the one this reader knows; a length off that table is skipped "
                    "and listed under unresolved. A texture with NO .ubulk keeps its whole "
                    "chain inline in the .uasset instead, one bulk-map entry per level, and "
                    "is read from there under the same check transposed: the entry sizes "
                    "must be exactly the chain re-derived from the largest one. unresolved "
                    "entries carry a machine-readable kind: no-icon-in-docs means the game "
                    "ships no picture and the text tile is correct forever; "
                    "asset-not-in-container and undecoded mean a picture exists and this "
                    "reader missed it."
                ),
            },
            "counts": stats,
            "staleness": (
                "game_version_pinned is the build these pictures were cut from. "
                "tools/gen_item_icons.py refuses to replace this directory from another "
                "build unless --force is passed, because artwork from two builds in one "
                "directory answers questions instead of failing."
            ),
        },
        "icons": entries,
        "unresolved": unresolved,
    }


@dataclass
class IconCut:
    """What one pass over the item classes produced: files, manifest entries, refusals."""

    payload: dict[str, bytes] = field(default_factory=dict)
    entries: dict[str, dict] = field(default_factory=dict)
    unresolved: dict[str, dict] = field(default_factory=dict)
    #: Source side in px -> how many icons came from a texture that size.
    sides: dict[int, int] = field(default_factory=dict)


def cut_icons(
    store: IoStore,
    classes: list[tuple[str, str]],
    want_px: int,
    *,
    package_mod,
    decoder,
    image_mod,
    quiet: bool,
    started: float,
) -> IconCut:
    """Every class's icon as PNG bytes at ``want_px``; what cannot be cut is named, not raised."""
    by_lower = path_index(store)
    layouts = bulk_layouts()
    print(
        f"{len(classes)} item classes carry {FORM_FIELD}; container holds "
        f"{len(store.paths)} paths, {len(layouts)} .ubulk layouts derived"
    )
    cut = IconCut()
    for name, icon in classes:
        stem = container_stem(icon)
        if stem is None:
            cut.unresolved[name] = {
                "kind": KIND_NO_ICON,
                "detail": f"the dump names no icon ({icon or 'empty'}); "
                "the frontend's text tile is this class's correct rendering",
            }
            continue
        asset = by_lower.get((stem + ".uasset").lower())
        bulk_path = by_lower.get((stem + ".ubulk").lower())
        if asset is None:
            cut.unresolved[name] = {
                "kind": KIND_NOT_IN_CONTAINER,
                "detail": f"{stem}.uasset is not in the container",
            }
            continue
        if bulk_path is not None:
            decoded, why = decode_bulk_icon(
                package_mod,
                decoder,
                image_mod,
                store.read_path(asset),
                store.read_path(bulk_path),
                layouts,
            )
        else:
            # No .ubulk is not a missing picture: three of the 747 cook the chain inline.
            decoded, why = decode_inline_icon(
                package_mod, decoder, image_mod, store.read_path(asset)
            )
        if decoded is None:
            cut.unresolved[name] = {"kind": KIND_UNDECODED, "detail": f"{stem}: {why}"}
            continue
        image, px, fmt = decoded
        blob = to_png(image_mod, image, px, want_px)
        cut.payload[f"{name}.png"] = blob
        cut.entries[name] = {
            "file": f"{name}.png",
            "source_px": px,
            "source_format": fmt,
            "bytes": len(blob),
        }
        cut.sides[px] = cut.sides.get(px, 0) + 1
        if not quiet and len(cut.entries) % 100 == 0:
            print(f"  {len(cut.entries)} decoded ({time.time() - started:.0f}s)")
    return cut


def main() -> int:
    parser = base_parser("Cut one PNG per item out of the installed game into data/local/icons/.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=LOCAL_DIR / ICONS_DIR_NAME,
        help="where the PNGs and manifest.json go (default: data/local/icons/)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace icons cut from another build instead of refusing",
    )
    parser.add_argument(
        "--px",
        type=int,
        default=ICON_PX,
        help=(
            f"square side to write, in pixels (default {ICON_PX}). Measured on build 495413: "
            "256 is 41.1 MB, 128 is 13.8 MB, 96 is 8.5 MB, 64 is 4.3 MB. Nothing is ever "
            "upscaled, so a source smaller than this stays its own size."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="one line per 100 icons, not per one")
    args = parser.parse_args()
    if args.px < 1:
        print(f"--px is a pixel side, so it has to be at least 1; got {args.px}")
        return 2

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    import texture2ddecoder as decoder
    from PIL import Image as image_mod

    from satisfactory_mcp.core.gameassets import packages as package_mod

    started = time.time()
    try:
        pin, _raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc}\nPass --game <install> if the game is not where this expects it.")
        return 1
    branch = installed_build_from_exe(args.game)
    print(f"install: {args.game}\n  {pin}")

    out_dir: Path = args.out_dir
    if not args.force and (out_dir / MANIFEST_NAME).is_file():
        already = pinned_build(out_dir)
        if already != pin:
            print(
                f"{out_dir} already holds icons and this run cannot show they came from the "
                "install now on disk.\n"
                f"  installed:  {pin}\n"
                f"  those PNGs: {already or 'no manifest.json, or no build recorded in it'}\n"
                "Artwork from two builds in one directory answers questions instead of "
                "failing. Pass --force to replace it anyway."
            )
            return 3

    docs_path = args.game / DOCS_SUFFIX
    if not docs_path.is_file():
        print(f"no docs dump at {docs_path}; the icons are named by the classes in it")
        return 1
    paks = paks_dir(args.game)
    if not (paks / f"{CONTAINER}.utoc").exists():
        print(f"no {CONTAINER}.utoc under {paks}")
        return 1

    docs = load_docs(docs_path)
    classes = icon_classes(docs)
    store = IoStore(paks, CONTAINER, oodle_decompress)
    cut = cut_icons(
        store,
        classes,
        args.px,
        package_mod=package_mod,
        decoder=decoder,
        image_mod=image_mod,
        quiet=args.quiet,
        started=started,
    )
    stats = {
        "item_classes": len(classes),
        "icons_written": len(cut.entries),
        "unresolved": len(cut.unresolved),
        "bytes": sum(e["bytes"] for e in cut.entries.values()),
        "written_px": args.px,
        "source_px": {str(px): count for px, count in sorted(cut.sides.items())},
        "seconds": round(time.time() - started, 1),
        "decoders": {name: version for name, version in sorted(versions.items())},
    }
    manifest = build_manifest(
        pin=pin,
        branch=branch,
        docs=docs,
        entries=cut.entries,
        unresolved=cut.unresolved,
        stats=stats,
    )
    cut.payload[MANIFEST_NAME] = json.dumps(manifest, indent=1).encode("utf-8")
    install_directory(out_dir, cut.payload)
    print_summary(out_dir, cut, stats)
    return 0


def print_summary(out_dir: Path, cut: IconCut, stats: dict) -> None:
    px = stats["written_px"]
    print(
        f"wrote {out_dir}  {len(cut.entries)} icons at {px}x{px} "
        f"({stats['bytes'] / 1e6:.1f} MB) from "
        + ", ".join(f"{count}x{side}px" for side, count in sorted(cut.sides.items()))
        + f"  ({stats['seconds']:.0f}s)"
    )
    if cut.unresolved:
        print(f"  {len(cut.unresolved)} class(es) got no picture; manifest.json names each one:")
        for name, entry in sorted(cut.unresolved.items())[:8]:
            print(f"    {name}: [{entry['kind']}] {entry['detail']}")
    print("none of it is committed: data/local/ is gitignored and stays that way.")


if __name__ == "__main__":
    raise SystemExit(main())
