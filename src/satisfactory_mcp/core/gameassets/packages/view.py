"""One package's exports, with classes, outers and lazily parsed properties."""

from __future__ import annotations

import collections
import struct

from ...jsontypes import JsonObject
from .properties import property_tags, read_float, read_int32
from .zen import Package, ScriptObjects, apply_fname_number

__all__ = ["LEVEL_CLASS", "PackageView", "class_name_of"]

#: The class of the export every map actor hangs off. This, not a path prefix, is what makes
#: an export an actor: a native class is a 62-bit hash rather than a path, so a ``/Game/``
#: prefix test silently drops every natively-classed actor there is.
LEVEL_CLASS = "/Script/Engine.Level"

_MASK62 = (1 << 62) - 1

#: ``FPackageObjectIndex`` kinds, in its top two bits.
_EXPORT_KIND = 0
_SCRIPT_IMPORT_KIND = 1
_PACKAGE_IMPORT_KIND = 2
_NULL_KIND = 3


def class_name_of(class_path: str | None) -> str:
    """The class name this table keys on, from either kind of class path.

    A blueprint class is named by its *package* -- ``/Game/.../BP_Crystal`` is the class
    ``BP_Crystal_C`` -- while a native one is a full object path, so the two need different
    tails. Anything else comes back as-is, so it is visible rather than silently binned.
    """
    if not class_path:
        return "<null class>"
    if class_path.startswith("/Game/"):
        return class_path.rsplit("/", 1)[-1] + "_C"
    if class_path.startswith("/Script/"):
        return class_path.rsplit(".", 1)[-1]
    return class_path


class PackageView:
    """One package's exports with lazily parsed properties and an outer -> children map."""

    def __init__(self, blob: bytes, scripts: ScriptObjects | None = None) -> None:
        self.pkg = Package(blob)
        self.scripts = scripts
        self.exports = self.pkg.exports()
        self.class_of: dict[int, str | None] = {}
        self.outer_of: dict[int, int | None] = {}
        self.children: dict[int, list[int]] = collections.defaultdict(list)
        self.level_slots: set[int] = set()
        self._props: dict[int, dict[str, bytes]] = {}
        self._kinds: dict[int, dict[str, str | None]] = {}
        self._bools: dict[int, dict[str, bool]] = {}
        for export in self.exports:
            slot = export["slot"]
            path = self.object_path(export["class"])
            self.class_of[slot] = path
            if path == LEVEL_CLASS:
                self.level_slots.add(slot)
            outer = export["outer"]
            if outer >> 62 == _EXPORT_KIND:
                parent = outer & _MASK62
                self.outer_of[slot] = parent
                self.children[parent].append(slot)
            else:
                self.outer_of[slot] = None

    def object_path(self, packed: int) -> str | None:
        """An ``FPackageObjectIndex`` as a readable path.

        Four kinds. ``Null`` is nothing; ``Export`` points inside this package, which no class
        normally does and one map actor's does; ``ScriptImport`` is the 62-bit hash a
        ``ScriptObjects`` table turns back into ``/Script/FactoryGame.Whatever``;
        ``PackageImport`` is ``(imported package index, export hash)`` with the package NAME in
        the header, which holds from UE 5.2 on and not before.
        """
        kind = packed >> 62
        if kind == _NULL_KIND:
            return None
        if kind == _EXPORT_KIND:
            return f"export:{packed & _MASK62}"
        if kind == _SCRIPT_IMPORT_KIND:
            return self.scripts.get(packed) if self.scripts else f"/Script/<hash:{packed:016x}>"
        slot = (packed & _MASK62) >> 32
        if slot >= len(self.pkg.imported_packages):
            return None
        return self.pkg.imported_packages[slot]

    def _parse_properties(self, slot: int) -> None:
        props: dict[str, bytes] = {}
        kinds: dict[str, str | None] = {}
        bools: dict[str, bool] = {}
        entries, _end = property_tags(self.pkg.body(self.exports[slot]), self.pkg.names)
        for tag in entries:
            if tag.name is None or tag.name in props:
                continue
            props[tag.name] = tag.payload
            kinds[tag.name] = tag.kind
            bools[tag.name] = bool(tag.flags)
        self._props[slot] = props
        self._kinds[slot] = kinds
        self._bools[slot] = bools

    def props(self, slot: int) -> dict[str, bytes]:
        if slot not in self._props:
            self._parse_properties(slot)
        return self._props[slot]

    def kinds(self, slot: int) -> dict[str, str | None]:
        if slot not in self._kinds:
            self._parse_properties(slot)
        return self._kinds[slot]

    def flag(self, slot: int, name: str) -> bool | None:
        """A ``BoolProperty``'s value, which lives in the tag rather than the payload."""
        if slot not in self._bools:
            self._parse_properties(slot)
        return self._bools[slot].get(name)

    def export_ref(self, payload: bytes) -> int | None:
        """An ``FPackageIndex`` pointing at an export in this same package, or None."""
        if len(payload) != 4:
            return None
        value = struct.unpack("<i", payload)[0]
        return value - 1 if value > 0 else None

    def _import_entry(self, payload: bytes) -> int | None:
        """The import-map ``FPackageObjectIndex`` an outward ``FPackageIndex`` names, or None.

        The negative half of an ``FPackageIndex`` indexes the import map.
        """
        if len(payload) != 4:
            return None
        value = struct.unpack("<i", payload)[0]
        if value >= 0:
            return None
        index = -value - 1
        if index >= len(self.pkg.imports):
            return None
        return self.pkg.imports[index]

    def import_path(self, payload: bytes) -> str | None:
        """An ``FPackageIndex`` pointing OUT of this package, as a path.

        It is how a spawner's ``mCreatureClass``, a deposit's ``mOverrideResourceClass`` and a
        cache's item class are reached at all.
        """
        packed = self._import_entry(payload)
        return None if packed is None else self.object_path(packed)

    def import_export_hash(self, payload: bytes) -> int | None:
        """The ``PublicExportHash`` an outward ``FPackageIndex`` names, or ``None``.

        The other half of :meth:`import_path`, and the half that is an IDENTITY. A
        ``PackageImport`` packs an imported-package slot and an index into this package's
        ``ImportedPublicExportHashes``; the slot yields a package name, which is not unique,
        while the hash names one export in the whole container. Matched against
        ``exports()[...]["public_hash"]`` on the far side, it says exactly which object.
        """
        packed = self._import_entry(payload)
        if packed is None or packed >> 62 != _PACKAGE_IMPORT_KIND:
            return None
        slot = packed & 0xFFFFFFFF
        hashes = self.pkg.imported_public_export_hashes
        return hashes[slot] if slot < len(hashes) else None

    def decode_struct(self, payload: bytes) -> JsonObject:
        """A nested tagged struct as plain values, one level of types deep.

        Anything this does not know is kept as ``{"_type": ..., "_raw": hex}`` rather than
        dropped, which is what made ``FInventoryItem`` legible: it has no tagged members at
        all, so its bytes had to survive to be read as an ``FPackageIndex``.
        """
        entries, _end = property_tags(payload, self.pkg.names, 0)
        out: JsonObject = {}
        for tag in entries:
            name, kind, raw = tag.name, tag.kind, tag.payload
            if name is None:
                continue
            if kind == "StructProperty":
                inner = self.decode_struct(raw)
                out[name] = inner if inner else {"_type": kind, "_raw": raw.hex()}
            elif kind in ("ObjectProperty", "ClassProperty", "SoftClassProperty"):
                ref = self.export_ref(raw)
                out[name] = self.import_path(raw) or (f"export:{ref}" if ref is not None else None)
            elif kind in ("EnumProperty", "NameProperty"):
                out[name] = self.read_fname(raw)
            elif kind == "IntProperty":
                out[name] = read_int32(raw)
            elif kind in ("FloatProperty", "DoubleProperty"):
                out[name] = read_float(raw)
            elif kind == "BoolProperty":
                out[name] = bool(tag.flags)
            else:
                out[name] = {"_type": kind, "_raw": raw[:32].hex()}
        return out

    def read_fname(self, payload: bytes) -> str | None:
        if len(payload) < 8:
            return None
        index, number = struct.unpack_from("<II", payload, 0)
        slot = index & 0x3FFFFFFF
        if (index >> 30) != 0 or slot >= len(self.pkg.names):
            return None
        return apply_fname_number(self.pkg.names[slot], number)
