"""The export as one uncompressed tar stream, read straight from the render folders.

Every entry is a regular file: mode 0644, uid and gid 0 with no names, mtime from its source.
No directory entries, links or compression. docs/map/sharing.md.
"""

from __future__ import annotations

import io
import os
import tarfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from mapgen.share.files import Count, tiles
from mapgen.share.manifest import TreeCopy

__all__ = ["Member", "tile_members", "write"]

FILE_MODE = 0o644


@dataclass(frozen=True)
class Member:
    """One file of the archive: a source file, or ``data`` written by the export."""

    name: str
    path: Path | None = None
    data: bytes = b""


def tile_members(tree: TreeCopy, max_z: int) -> Iterator[Member]:
    for z, entry in tiles(tree, max_z):
        yield Member(f"{tree.target}/{z}/{entry.name}", Path(entry.path))


def _info(name: str, size: int, mtime: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = FILE_MODE
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.size = size
    info.mtime = mtime
    return info


def write(stream: BinaryIO, members: Iterable[Member]) -> Count:
    """Write ``members`` in order to ``stream``; a ``data`` member takes the newest mtime so far."""
    count = Count()
    newest = 0
    with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for member in members:
            if member.path is None:
                info = _info(member.name, len(member.data), newest)
                tar.addfile(info, io.BytesIO(member.data))
            else:
                with member.path.open("rb") as handle:
                    stat = os.fstat(handle.fileno())
                    mtime = int(stat.st_mtime)
                    info = _info(member.name, stat.st_size, mtime)
                    newest = max(newest, mtime)
                    tar.addfile(info, handle)
            count.files += 1
            count.bytes += info.size
    return count
