"""The Zen package header: summary, name batches, import and export maps, ``BulkDataMap``,
and the global ``ScriptObjects`` table that names native classes."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import TypedDict

from ..iostore import ContainerError, Decompressor, IoStore

__all__ = [
    "BULK_ENTRY_BYTES",
    "BulkEntry",
    "Package",
    "ScriptObjects",
    "apply_fname_number",
    "bulk_data_entries",
]

#: Word indices into the ``FZenPackageSummary``'s leading uint32 array.
_HEADER_SIZE = 1
_IMPORTED_HASHES = 6
_IMPORT_MAP = 7
_EXPORT_MAP = 8
_IMPORTED_PACKAGE_NAMES = 12

#: The summary's word count; the package's own name batch follows it.
_SUMMARY_WORDS = 15

#: One ``FByteBulkData`` entry in the Zen header's ``BulkDataMap``: three uint64 (offset,
#: duplicate offset, size), a uint32 of flags, three pad bytes, then the cooked-index byte.
BULK_ENTRY_BYTES = 32


class BulkEntry(TypedDict):
    """One ``FByteBulkData`` of the ``BulkDataMap``: where its payload is, and how it is kept."""

    index: int
    offset: int
    duplicate_offset: int
    size: int
    flags: int
    cooked_index: int


def _name_batch(blob: bytes, pos: int) -> tuple[list[str], int]:
    """An ``FNameBatch``: count, byte length, hash version, hashes, headers, then strings."""
    count = struct.unpack_from("<I", blob, pos)[0]
    pos += 8 + 8 + 8 * count
    headers = blob[pos : pos + 2 * count]
    pos += 2 * count
    names: list[str] = []
    for index in range(count):
        header = struct.unpack_from(">H", headers, index * 2)[0]
        length = header & 0x7FFF
        if header & 0x8000:  # UTF-16
            names.append(blob[pos : pos + length * 2].decode("utf-16-le", "replace"))
            pos += length * 2
        else:
            names.append(blob[pos : pos + length].decode("utf-8", "replace"))
            pos += length
    return names, pos


def _fname_numbers(blob: bytes, pos: int, count: int, limit: int) -> list[int]:
    """The ``uint32[count]`` of ``FName`` numbers that follows an imported-package batch.

    A name batch carries strings only, so the numbers follow it as a plain array. On a
    layout this does not fit every number reads as zero: a name with no suffix rather than
    a wrong name.
    """
    if count <= 0 or pos + 4 * count > limit:
        return [0] * max(count, 0)
    return list(struct.unpack_from(f"<{count}I", blob, pos))


def bulk_data_entries(blob: bytes, names_end: int, first_section: int) -> list[BulkEntry]:
    """The Zen header's ``BulkDataMap``, which is what an ``FByteBulkData`` indexes into.

    It sits between the name batch and the first section offset the summary names, behind a
    UE 5.4+ alignment pad, and is bounded by ``first_section`` so a misread length raises
    rather than walking over the import map.

    An INLINE entry's ``offset`` -- see ``textures.INLINE_BULK_FLAG`` for which those are --
    is relative to the start of the export-data segment, so its payload is at
    ``header_size + offset`` in the same blob; a streamed entry's is an offset into the
    sibling ``.ubulk``. Both kinds sit side by side in an ordinary icon's map.
    """
    try:
        (pad,) = struct.unpack_from("<Q", blob, names_end)
        pos = names_end + 8 + pad
        (size,) = struct.unpack_from("<q", blob, pos)
        pos += 8
        if size < 0 or pos + size > first_section:
            raise ValueError(f"bulk data map of {size} bytes does not fit before {first_section}")
        out: list[BulkEntry] = []
        for index in range(size // BULK_ENTRY_BYTES):
            at = pos + index * BULK_ENTRY_BYTES
            offset, duplicate, length, flags = struct.unpack_from("<3QI", blob, at)
            out.append(
                {
                    "index": index,
                    "offset": offset,
                    "duplicate_offset": duplicate,
                    "size": length,
                    "flags": flags,
                    "cooked_index": blob[at + 28],
                }
            )
        return out
    except struct.error as exc:
        raise ValueError(f"bulk data map runs off the package header: {exc}") from exc


def apply_fname_number(base: str, number: int) -> str:
    """UE's own spelling of an ``FName``: ``("Foo", 4)`` is written ``Foo_3``. One function
    because three readers need the identical off-by-one -- the package's own name map,
    ``ScriptObjects``, and the imported-package names."""
    return base if number == 0 else f"{base}_{number - 1}"


class ScriptObjects:
    """``/Script/...`` object paths, out of ``global.utoc``'s ScriptObjects chunk.

    A cooked package refers to a native class by an ``FPackageObjectIndex`` of kind
    ``ScriptImport``, which is a 62-bit hash of the lowercased object path and carries no text.
    The only way back is this table: a name batch, an ``int32`` count, then one 32-byte
    ``FScriptObjectEntry`` per object holding its name, its own hash and its Outer's. Walking
    ``OuterIndex`` composes the full path -- package, then ``.`` for a top-level object and
    ``:`` for anything nested inside one. Without it every natively-classed actor in the map is
    unidentifiable.
    """

    CHUNK_TYPE = 5
    NONE = 0xFFFFFFFFFFFFFFFF

    def __init__(self, paks: Path, decompress: Decompressor) -> None:
        store = IoStore(paks, "global", decompress)
        chunks = store.chunks_of_type(self.CHUNK_TYPE)
        if not chunks:
            raise ContainerError("global.utoc holds no ScriptObjects chunk")
        blob = store.read(chunks[0])
        names, pos = _name_batch(blob, 0)
        count = struct.unpack_from("<i", blob, pos)[0]
        pos += 4
        entries: dict[int, tuple[str, int]] = {}
        for index in range(count):
            name_index, number, own, outer, _cdo = struct.unpack_from(
                "<IIQQQ", blob, pos + index * 32
            )
            slot = name_index & 0x3FFFFFFF
            base = names[slot] if slot < len(names) else f"<oob{slot}>"
            entries[own] = (apply_fname_number(base, number), outer)
        self.entries = entries
        self.paths: dict[int, str] = {}
        for own in entries:
            self.paths[own] = self._path(own)
        self.object_count = count
        self.package_count = sum(1 for _name, outer in entries.values() if outer == self.NONE)
        self.chunk_bytes = len(blob)

    def _path(self, own: int, depth: int = 0) -> str:
        cached = self.paths.get(own)
        if cached is not None:
            return cached
        if own == self.NONE or depth > 32:
            return ""
        entry = self.entries.get(own)
        if entry is None:
            return f"<unresolved:{own:016x}>"
        name, outer = entry
        above = self._path(outer, depth + 1)
        if not above:
            path = name
        elif above.startswith("/") and "." not in above:
            path = f"{above}.{name}"  # package -> top-level object
        else:
            path = f"{above}:{name}"  # nested subobject
        self.paths[own] = path
        return path

    def get(self, own: int) -> str:
        return self.paths.get(own) or f"/Script/<unresolved:{own:016x}>"


class Package:
    """``FZenPackageSummary`` plus the import and export maps."""

    EXPORT_SIZE = 72

    #: Where ``PublicExportHash`` sits inside one 72-byte ``FExportMapEntry``: after the
    #: cooked offset and size, the object name, and the outer, class, super and template
    #: indices -- seven 8-byte fields.
    EXPORT_PUBLIC_HASH_AT = 56

    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        words = struct.unpack_from(f"<{_SUMMARY_WORDS}I", blob, 0)
        self.header_size = words[_HEADER_SIZE]
        import_offset, export_offset = words[_IMPORT_MAP], words[_EXPORT_MAP]
        self.export_offset = export_offset
        self.names, self.names_end = _name_batch(blob, 4 * _SUMMARY_WORDS)
        # Where the header's sections begin, i.e. where the BulkDataMap must END.
        sections = words[_IMPORTED_HASHES : _IMPORTED_PACKAGE_NAMES + 1]
        self.first_section = min([word for word in sections if word] or [self.header_size])
        # ``ImportedPublicExportHashes``, which a PackageImport's low 32 bits INDEX; without
        # it a cross-package reference resolves only to a package NAME, which is not unique.
        hashes_offset = words[_IMPORTED_HASHES]
        self.imported_public_export_hashes: list[int] = []
        if hashes_offset and import_offset > hashes_offset:
            count = (import_offset - hashes_offset) // 8
            self.imported_public_export_hashes = list(
                struct.unpack_from(f"<{count}Q", blob, hashes_offset)
            )
        # The summary carries no export count: the map ends where the next section starts.
        after = min(
            [word for word in words[_EXPORT_MAP + 1 :] if word > export_offset]
            or [self.header_size]
        )
        self.export_count = (after - export_offset) // self.EXPORT_SIZE
        # The import map is an FPackageObjectIndex[] filling the gap to the export map.
        self.imports: list[int] = []
        if import_offset and export_offset > import_offset:
            count = (export_offset - import_offset) // 8
            self.imports = list(struct.unpack_from(f"<{count}Q", blob, import_offset))
        # ``ImportedPackageNames``: a name batch, then one uint32 FName NUMBER per name; the
        # batch alone drops the ``_3`` of ``SM_MERGED_BP_CaveFloor2_3``.
        self.imported_packages: list[str] = []
        offset = words[_IMPORTED_PACKAGE_NAMES]
        if offset and offset < self.header_size:
            try:
                names, end = _name_batch(blob, offset)
                numbers = _fname_numbers(blob, end, len(names), self.header_size)
                self.imported_packages = [
                    apply_fname_number(name, number)
                    for name, number in zip(names, numbers, strict=True)
                ]
            except (struct.error, IndexError):
                self.imported_packages = []

    def name(self, index: int, number: int) -> str:
        kind, slot = index >> 30, index & 0x3FFFFFFF
        if kind == 0:
            base = self.names[slot] if slot < len(self.names) else f"<oob{slot}>"
        else:
            # Kind 2 is the global name map, in global.utoc; nothing read here is global.
            base = f"<kind{kind}:{slot}>"
        return apply_fname_number(base, number)

    def exports(self) -> list[dict]:
        out = []
        for slot in range(self.export_count):
            pos = self.export_offset + slot * self.EXPORT_SIZE
            offset, size = struct.unpack_from("<QQ", self.blob, pos)
            name_index, name_number = struct.unpack_from("<II", self.blob, pos + 16)
            outer, class_index = struct.unpack_from("<2Q", self.blob, pos + 24)
            (public_hash,) = struct.unpack_from("<Q", self.blob, pos + self.EXPORT_PUBLIC_HASH_AT)
            out.append(
                {
                    "slot": slot,
                    "offset": offset,
                    "size": size,
                    "name": self.name(name_index, name_number),
                    "outer": outer,
                    "class": class_index,
                    # What another package's import refers to this export BY. Unique across
                    # the container, where the package name is not.
                    "public_hash": public_hash,
                }
            )
        return out

    def body(self, export: dict) -> bytes:
        start = self.header_size + export["offset"]
        return self.blob[start : start + export["size"]]

    def bulk_entries(self) -> list[BulkEntry]:
        """This package's ``BulkDataMap``: one entry per ``FByteBulkData``, or ``ValueError``.
        An inline entry's payload is ``blob[header_size + offset :][: size]``."""
        return bulk_data_entries(self.blob, self.names_end, self.first_section)
