"""The container: a read-only UE5 IoStore reader, enough of one to get ``.umap`` bytes out.

A ``.utoc`` is the table of contents and the ``.ucas`` beside it is the blob; this reads them
and never writes one. Blocks are Oodle-compressed, so ``IoStore`` takes its decompressor as a
callable (DESIGN.md), and ``oodle_decompress`` below is the real one, ready to be handed in.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

#: What ``IoStore`` wants: ``(packed block, unpacked size) -> exactly that many bytes``.
Decompressor = Callable[[bytes, int], bytes]

#: The one line that fixes a missing ``gen`` extra. Spelled out rather than derived, because
#: this package is not allowed to know which generator is running.
GEN_INVOCATION = "uv run --extra gen python tools/gen_X.py"


class ContainerError(Exception):
    """The bytes are not the container this reader was promised.

    Its own error, rather than the save parser's ``ParseError``: a ``.utoc`` and a ``.sav``
    are different formats read for different reasons, and a caller that wants to tell "this
    install has no readable container" from "this save file tore mid-write" needs the two to
    be distinguishable. They were the same class only because both readers happened to grow
    up in the same file.
    """


class MissingGenExtra(RuntimeError):
    """The ``gen`` extra is not installed, so nothing here can decompress a block."""


def oodle_decompress(packed: bytes, unpacked_size: int) -> bytes:
    """Decompress one container block with ``ooz``, importing it at the point of use.

    The import is inside the function on purpose and the architecture test enforces it: the
    whole package has to stay importable with the ``gen`` extra absent, and a module-scope
    ``import ooz`` would make a missing optional dependency into an ``ImportError`` at
    collection time for anything that so much as names this module.

    ``ImportError`` becomes ``MissingGenExtra`` because ``No module named 'ooz'`` tells a
    reader what happened and not what to do about it.
    """
    try:
        import ooz
    except ImportError as exc:  # pragma: no cover - depends on the environment, not the code
        raise MissingGenExtra(
            "ooz (from pyooz) is not installed, so the game's container blocks cannot be "
            "decompressed. It is part of the optional `gen` extra, which is asked for on "
            f"the command line:\n    {GEN_INVOCATION}"
        ) from exc
    return ooz.decompress(packed, unpacked_size)


class _Cursor:
    """FString-aware cursor over the directory-index blob."""

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.buf, self.pos)[0]
        self.pos += 4
        return value

    def fstring(self) -> str:
        length = struct.unpack_from("<i", self.buf, self.pos)[0]
        self.pos += 4
        if length == 0:
            return ""
        if length < 0:  # negative length means UTF-16
            raw = self.buf[self.pos : self.pos - length * 2]
            self.pos += -length * 2
            return raw[:-2].decode("utf-16-le")
        raw = self.buf[self.pos : self.pos + length]
        self.pos += length
        return raw[:-1].decode("latin1")


class IoStore:
    """``.utoc`` table of contents plus random access into the ``.ucas`` blocks.

    The one non-obvious field: ``FIoOffsetAndLength`` packs a 5-byte offset and a 5-byte
    length **big-endian**, while every other integer in the format is little-endian.
    Reading them the natural way yields garbage offsets and a reader that appears to work
    on entry 0 and nowhere else.

    ``global.utoc`` has no directory index at all -- its chunks are found by the type byte
    of the 12-byte ``FIoChunkId``, which is why the ids are kept rather than skipped.
    """

    NONE32 = 0xFFFFFFFF
    MAGIC = b"-==--==--==--==-"

    def __init__(self, paks: Path, name: str, decompress: Decompressor) -> None:
        self.decompress = decompress
        toc_path = paks / f"{name}.utoc"
        self.cas_path = paks / f"{name}.ucas"
        blob = toc_path.read_bytes()
        if blob[:16] != self.MAGIC:
            raise ContainerError(f"{toc_path} is not a .utoc")
        self.version = blob[16]
        (
            header_size,
            self.entry_count,
            self.block_count,
            _block_entry_size,
            method_count,
            method_length,
            self.block_size,
            dir_size,
            _partitions,
        ) = struct.unpack_from("<9I", blob, 20)
        self.flags = blob[80]
        seed_count = struct.unpack_from("<I", blob, 84)[0]
        nonopt_count = struct.unpack_from("<I", blob, 96)[0]

        pos = header_size
        self.chunk_ids = blob[pos : pos + 12 * self.entry_count]
        pos += 12 * self.entry_count
        self.offlen = blob[pos : pos + 10 * self.entry_count]
        pos += 10 * self.entry_count
        pos += 4 * seed_count + 4 * nonopt_count  # perfect-hash tables
        self.blocks = blob[pos : pos + 12 * self.block_count]
        pos += 12 * self.block_count
        self.methods = []
        for _ in range(method_count):
            self.methods.append(blob[pos : pos + method_length].split(b"\0")[0].decode("latin1"))
            pos += method_length
        if self.flags & 0x04:  # Signed: a signature block sits before the directory index
            size = struct.unpack_from("<i", blob, pos)[0]
            pos += 4 + size * 2 + 20 * self.block_count

        self.paths: dict[int, str] = {}
        if dir_size:
            self._read_directory(blob[pos : pos + dir_size])
        self.by_path = {path: index for index, path in self.paths.items()}
        self.toc_bytes = len(blob)
        self.cas_bytes = self.cas_path.stat().st_size
        self.toc_mtime = datetime.fromtimestamp(toc_path.stat().st_mtime, UTC)
        self.blocks_read = 0
        self.bytes_out = 0
        # One handle for the whole run: reopening it per package is 40% of the read.
        self.cas = self.cas_path.open("rb")

    def _read_directory(self, blob: bytes) -> None:
        cur = _Cursor(blob)
        mount = cur.fstring()
        count = cur.u32()
        dirs = [struct.unpack_from("<4I", blob, cur.pos + i * 16) for i in range(count)]
        cur.pos += count * 16
        count = cur.u32()
        files = [struct.unpack_from("<3I", blob, cur.pos + i * 12) for i in range(count)]
        cur.pos += count * 12
        strings = [cur.fstring() for _ in range(cur.u32())]

        # Iterative, not recursive: the tree is ~4,500 deep in one chain here and the
        # obvious recursion needs a five-figure recursion limit to survive it.
        stack = [(0, mount)]
        while stack:
            index, prefix = stack.pop()
            while index != self.NONE32:
                name_index, first_child, next_sibling, first_file = dirs[index]
                name = strings[name_index] if name_index != self.NONE32 else ""
                path = prefix + name + "/" if name else prefix
                file_index = first_file
                while file_index != self.NONE32:
                    leaf, next_file, user_data = files[file_index]
                    self.paths[user_data] = path + strings[leaf]
                    file_index = next_file
                if first_child != self.NONE32:
                    stack.append((first_child, path))
                index = next_sibling

    def chunks_of_type(self, chunk_type: int) -> list[int]:
        """Entry indices whose ``FIoChunkId`` carries this type in its last byte."""
        return [i for i in range(self.entry_count) if self.chunk_ids[i * 12 + 11] == chunk_type]

    def read(self, index: int) -> bytes:
        raw = self.offlen[index * 10 : (index + 1) * 10]
        offset = int.from_bytes(raw[0:5], "big")
        length = int.from_bytes(raw[5:10], "big")
        first = offset // self.block_size
        last = (offset + max(length, 1) - 1) // self.block_size
        out = bytearray()
        for block in range(first, last + 1):
            entry = self.blocks[block * 12 : (block + 1) * 12]
            self.cas.seek(int.from_bytes(entry[0:5], "little"))
            packed = self.cas.read(int.from_bytes(entry[5:8], "little"))
            unpacked_size = int.from_bytes(entry[8:11], "little")
            if entry[11] == 0:
                out += packed[:unpacked_size]
            else:
                chunk = self.decompress(packed, unpacked_size)
                if len(chunk) != unpacked_size:
                    raise ContainerError(f"block {block}: got {len(chunk)}, wanted {unpacked_size}")
                out += chunk
            self.blocks_read += 1
        self.bytes_out += length
        return bytes(out[offset - first * self.block_size :][:length])

    def read_path(self, path: str) -> bytes:
        return self.read(self.by_path[path])
