"""The export's files: tile trees copied by level, the built page, the manifests, the pruning.

A tile folder's name carries its build tag, so a file once written never changes: a rerun
skips every file already there at the same size, and a new render lands in new folders.
Tiles are hard-linked to the render's own files where the volume allows, else copied.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from mapgen.share.manifest import TreeCopy
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "MANIFESTS",
    "PAGE_ENTRY",
    "Count",
    "copy_page",
    "copy_tree",
    "count_tree",
    "page_files",
    "prune",
    "tiles",
    "trees_bitmap",
    "write_manifest",
]

#: A tile's file name: ``{x}_{y}`` and one of the suffixes the trees use.
TILE_NAME = re.compile(r"^(\d{1,5})_(\d{1,5})(\.nrm|\.hz)?\.(png|webp)$")

#: The two mutable files besides the page; written last, so a half-copied render is never live.
MANIFESTS = ("data/maps.json", "data/site.json")

#: The built page's entry, renamed to the export's index.
PAGE_ENTRY = "share.html"


@dataclass
class Count:
    files: int = 0
    bytes: int = 0
    copied: int = 0
    linked: int = 0

    def add(self, other: Count) -> None:
        self.files += other.files
        self.bytes += other.bytes
        self.copied += other.copied
        self.linked += other.linked


def tiles(tree: TreeCopy, max_z: int) -> Iterator[tuple[int, os.DirEntry[str]]]:
    """(z, entry) for every tile of levels 0..min(tree depth, max_z), names checked, by name."""
    for z in range(min(tree.max_z, max_z) + 1):
        level = tree.source / str(z)
        if not level.is_dir():
            continue
        with os.scandir(level) as found:
            entries = sorted(found, key=lambda e: e.name)
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and TILE_NAME.match(entry.name):
                yield z, entry


def count_tree(tree: TreeCopy, max_z: int) -> Count:
    count = Count()
    for _z, entry in tiles(tree, max_z):
        count.files += 1
        count.bytes += entry.stat().st_size
    return count


def _link(source: Path, part: Path) -> bool:
    """Hard-link ``source`` at ``part``; False where that fails (another volume, no links)."""
    part.unlink(missing_ok=True)
    try:
        os.link(source, part)
    except OSError:
        return False
    return True


def copy_tree(tree: TreeCopy, out: Path, max_z: int, link: bool = True) -> Count:
    """Place one tree's levels in ``out``, linked or copied."""
    count = Count()
    made: set[Path] = set()
    for z, entry in tiles(tree, max_z):
        size = entry.stat().st_size
        count.files += 1
        count.bytes += size
        target = out / tree.target / str(z) / entry.name
        if target.parent not in made:
            target.parent.mkdir(parents=True, exist_ok=True)
            made.add(target.parent)
        if target.is_file() and target.stat().st_size == size:
            continue
        part = target.with_name(target.name + ".part")
        if link and _link(Path(entry.path), part):
            count.linked += 1
        else:
            shutil.copyfile(entry.path, part)
            count.copied += 1
        os.replace(part, target)
    return count


def trees_bitmap(tree: TreeCopy, max_z: int) -> str:
    """Base64 of one bit per tile of levels 0..max_z, set where the sparse tree has a file.

    Bit ``(4**z - 1) / 3 + y * 2**z + x``, least significant first: frontend share/site.ts.
    """
    depth = min(tree.max_z, max_z)
    bits = bytearray(((4 ** (depth + 1) - 1) // 3 + 7) // 8)
    for z, entry in tiles(tree, max_z):
        found = TILE_NAME.match(entry.name)
        assert found is not None
        x, y = int(found.group(1)), int(found.group(2))
        if x < 1 << z and y < 1 << z:
            at = (4**z - 1) // 3 + y * (1 << z) + x
            bits[at >> 3] |= 1 << (at & 7)
    return base64.b64encode(bytes(bits)).decode("ascii")


def page_files(page: Path) -> list[tuple[str, Path]]:
    """(name in the export, file) for the built page's files but its entry, sorted."""
    entry = page / PAGE_ENTRY
    if not entry.is_file():
        raise FileNotFoundError(
            f"{entry} is missing: build the page first with `npm run build:share` in "
            "src/satisfactory_mcp/interfaces/web/frontend/"
        )
    found = ((p.relative_to(page).as_posix(), p) for p in sorted(page.rglob("*")))
    return [(rel, path) for rel, path in found if path.is_file() and rel != PAGE_ENTRY]


def copy_page(page: Path, out: Path) -> list[str]:
    """The built page into ``out``: everything but its entry, which is the caller's to swap in
    last. Returns the files under ``assets/`` the entry refers to."""
    for rel, path in page_files(page):
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, out / rel)
    html = (page / PAGE_ENTRY).read_text(encoding="utf-8")
    return sorted(set(re.findall(r'(?:src|href)="\./(assets/[^"]+)"', html)))


def write_manifest(out: Path, rel: str, text: str) -> None:
    """Write one mutable file in a single rename, so a reader never sees half of it."""
    target = out / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    part.write_text(text, encoding="utf-8", newline="\n")
    os.replace(part, target)


def json_text(doc: JsonObject) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def prune(out: Path, folders: set[str], assets: set[str]) -> list[str]:
    """Delete the tile folders and page assets the new manifests no longer name."""
    gone: list[str] = []
    tiles = out / "t"
    if tiles.is_dir():
        for child in sorted(tiles.iterdir()):
            if "t/" + child.name not in folders:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
                gone.append("t/" + child.name)
    built = out / "assets"
    if built.is_dir():
        for child in sorted(built.iterdir()):
            if "assets/" + child.name not in assets:
                child.unlink()
                gone.append("assets/" + child.name)
    return gone
