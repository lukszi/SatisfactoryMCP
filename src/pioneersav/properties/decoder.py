"""The value readers: one object's property list, its containers, structs and text."""

from __future__ import annotations

from ..errors import ParseError, expect
from ..reader import Reader
from ..references import read_reference, read_soft_reference
from ..versions import FIRST_MODERN_BODY, FIRST_UE5_OBJECT_VERSION
from .structs import (
    NATIVE_STRUCTS,
    OPAQUE_STRUCTS,
    read_inventory_item,
    read_inventory_item_legacy,
    read_inventory_item_modern,
)
from .tags import (
    TAG_BOOL_TRUE,
    TAG_NATIVE_SERIALIZE,
    TERMINATOR,
    UNNAMED_KEY_CANDIDATES,
    UNNAMED_SET_CANDIDATES,
    PropertyTag,
    TypeName,
    is_unnamed_struct,
    read_tag_ue4,
    read_tag_ue5,
    unnamed_element_candidates,
)

__all__ = ["SCALAR_READERS", "NestingTooDeep", "PropertyDecoder"]

#: Guard on how deep property lists may nest: low enough to raise a located ``ParseError``
#: long before CPython's recursion limit raises a ``RecursionError`` with no offset in it.
_MAX_NESTING = 32

#: Types whose payload is one fixed-width value with no framing, read the same way as a tagged
#: property, an array element or a map key. No 16-bit reader: none has met real bytes, and an
#: unknown type is skipped with a warning. ``Int8Property`` yields raw ``bytes``.
SCALAR_READERS = {
    "IntProperty": Reader.i32,
    "Int64Property": Reader.i64,
    "UInt64Property": Reader.u64,
    "UInt32Property": Reader.u32,
    "Int8Property": lambda r: r.bytes(1),
    "FloatProperty": Reader.f32,
    "DoubleProperty": Reader.f64,
    "StrProperty": Reader.string,
    "NameProperty": Reader.string,
}


class NestingTooDeep(ParseError):
    """The nesting guard tripping: a ``ParseError`` ``first_exact_fit()`` must not swallow."""


class PropertyDecoder:
    """Reads one object's properties, keyed by two different versions.

    ``version`` is the object's own: it picks the tag layout and guesses ``InventoryItem``.
    ``ue4_save`` is the save's: it picks the width of ``FVector``, ``FQuat`` and ``FBox``,
    which follow the writer rather than the object.
    """

    __slots__ = ("depth", "r", "ue4_save", "ue4_tags", "version", "warnings")

    def __init__(
        self,
        r: Reader,
        version: int,
        warnings: list[tuple[int, str]],
        *,
        save_version: int = FIRST_MODERN_BODY,
    ) -> None:
        self.r = r
        self.version = version
        self.warnings = warnings
        self.ue4_tags = version < FIRST_UE5_OBJECT_VERSION
        self.ue4_save = save_version < FIRST_MODERN_BODY
        self.depth = 0

    # -- the list ---------------------------------------------------------

    def property_list(self, limit: int) -> tuple[list[list], list[list]]:
        """Read tags until the ``"None"`` terminator, refusing to run past ``limit``, the end of
        the enclosing block or struct.

        The depth guard lives here because every route into a nested list passes through.
        """
        if self.depth >= _MAX_NESTING:
            raise NestingTooDeep(
                f"at body offset {self.r.pos}: property lists nested more than "
                f"{_MAX_NESTING} deep, so the cursor is not on a property tag"
            )
        self.depth += 1
        try:
            return self._property_list(limit)
        finally:
            self.depth -= 1

    def _property_list(self, limit: int) -> tuple[list[list], list[list]]:
        """The loop itself, split out so the depth guard can wrap it."""
        r = self.r
        values: list[list] = []
        types: list[list] = []
        while True:
            expect(
                r.pos < limit,
                r.pos,
                f"a property list ran to {limit} without its {TERMINATOR!r} terminator",
            )
            tag = read_tag_ue4(r) if self.ue4_tags else read_tag_ue5(r)
            if tag.name == TERMINATOR:
                return values, types
            expect(
                r.pos + tag.size <= limit,
                r.pos,
                f"property {tag.name!r} declares {tag.size} bytes, which runs "
                f"{r.pos + tag.size - limit} past the end of its block",
            )
            start = r.pos
            end = start + tag.size
            value = self.read_value(tag, end)
            expect(
                r.pos == end,
                r.pos,
                f"property {tag.name!r} of type {tag.type.name!r} declared {tag.size} "
                f"bytes but {r.pos - start} were read",
            )
            values.append([tag.name, value])
            types.append([tag.name, *tag.type.flat(), tag.flags])

    # -- one property -----------------------------------------------------

    def read_value(self, tag: PropertyTag, end: int):
        """Dispatch on the type name. ``end`` is where the payload must stop."""
        r = self.r
        name = tag.type.name
        reader = SCALAR_READERS.get(name)
        if reader is not None:
            return reader(r)
        if name == "BoolProperty":
            # the raw byte (16, 1 or 0), because the projection's truthy() reads it
            return tag.bool_value if self.ue4_tags else (tag.flags & TAG_BOOL_TRUE)
        if name in ("ObjectProperty", "InterfaceProperty"):
            return read_reference(r)
        if name == "SoftObjectProperty":
            return read_soft_reference(r)
        if name == "ByteProperty":
            return self.byte_value(tag)
        if name == "EnumProperty":
            return [self.enum_name(tag), r.string()]
        if name == "StructProperty":
            native = bool(tag.flags & TAG_NATIVE_SERIALIZE)
            return self.read_struct(tag.type.inner, native_hint=native, end=end, exact=True)
        if name == "ArrayProperty":
            return self.read_array(tag, end)
        if name == "SetProperty":
            return self.read_set(tag, end)
        if name == "MapProperty":
            return self.read_map(tag, end)
        if name == "TextProperty":
            return self.read_text(end)
        return self.skip_unknown(f"property type {name!r}", end)

    def skip_unknown(self, what: str, end: int):
        """Skip forwards to the declared ``end`` with a warning: the escape hatch the design
        rests on. Never backwards, which would fabricate a container's later elements."""
        r = self.r
        expect(
            end >= r.pos,
            r.pos,
            f"cannot skip {what}: the cursor is already {r.pos - end} bytes past the end "
            "this skip was given, so an untagged element earlier in the same container was "
            "skipped by the container's own length and everything after it was read from "
            "the wrong place",
        )
        self.warnings.append((r.pos, f"skipped {end - r.pos} bytes: {what}"))
        r.pos = end

    def first_exact_fit(self, what: str, end: int, *decoders):
        """Try each reading in turn; keep the first that lands exactly on the declared ``end``.

        Pass the narrowest reading first: a wrong narrow one fails at once, a permissive one
        can absorb a lot before it does.
        """
        start = self.r.pos
        mark = len(self.warnings)
        for decode in decoders:
            try:
                value = decode()
            except NestingTooDeep:
                # the stack guard, not a wrong guess: the next reading would trip it too
                raise
            except ValueError:
                pass
            else:
                if self.r.pos == end:
                    return value
            del self.warnings[mark:]
            self.r.pos = start
        return self.skip_unknown(what, end)

    # -- enums ------------------------------------------------------------

    def enum_name(self, tag: PropertyTag) -> str | None:
        """The enum a Byte/Enum property is typed by, or ``None`` for a plain byte, whether UE4
        wrote the string ``"None"`` or UE5 wrote no parameter."""
        inner = tag.type.inner.name
        return inner if inner and inner != TERMINATOR else None

    def byte_value(self, tag: PropertyTag):
        """``[enumName, value]``: ``[None, 2]`` for a plain byte, ``['EGamePhase',
        'EGP_MidGame']`` for an enum; readers take ``[-1]`` off either."""
        enum = self.enum_name(tag)
        return [enum, self.r.string() if enum else self.r.i8()]

    # -- structs ----------------------------------------------------------

    def read_struct(
        self, struct_type: TypeName, *, native_hint: bool, end: int, exact: bool = False
    ):
        """A struct: raw numbers if its NAME is in ``NATIVE_STRUCTS``, else a property list.
        ``exact`` means ``end`` is this struct's own declared end, which lets the size referee
        ``InventoryItem`` and turn an unknown non-list struct into a skip (savparse-notes.md).
        """
        native = NATIVE_STRUCTS.get(struct_type.name)
        if native is not None:
            if native is read_inventory_item and exact:
                # the two layouts can never land on the same byte, so the size decides outright
                return self.first_exact_fit(
                    "an InventoryItem that reads as neither layout",
                    end,
                    lambda: read_inventory_item_modern(self),
                    lambda: read_inventory_item_legacy(self),
                )
            return native(self)
        if native_hint:
            # bytes rather than None, so the caller sees what it got and the size balances
            if struct_type.name not in OPAQUE_STRUCTS:
                self.warnings.append(
                    (self.r.pos, f"struct {struct_type.name!r} serialises itself, kept as bytes")
                )
            return self.r.bytes(end - self.r.pos)
        if exact:
            return self.first_exact_fit(
                f"struct {struct_type.name!r} that is neither native nor a property list",
                end,
                lambda: list(self.property_list(end)),
            )
        return list(self.property_list(end))

    # -- containers -------------------------------------------------------

    def _read_element_count(self, what: str, end: int) -> int:
        """An element count, bounded by the bytes its own block has left: no element is shorter
        than one byte, and the count lives inside the payload, where a torn file makes it
        anything."""
        r = self.r
        count = r.i32()
        expect(
            0 <= count <= end - r.pos,
            r.pos - 4,
            f"{what} claims {count} elements with {end - r.pos} bytes left in its block, "
            "and no element of a container is shorter than one byte",
        )
        return count

    def _expect_no_removals(self, what: str) -> None:
        """A set's or map's leading removal count, which a saved container leaves at 0."""
        r = self.r
        removed = r.i32()
        expect(
            removed == 0,
            r.pos - 4,
            f"{what} declares {removed} removed entries; a saved container has no removal list",
        )

    def read_array(self, tag: PropertyTag, end: int):
        """``i32 count`` then the elements, untagged: their type is the array's own parameter."""
        r = self.r
        count = self._read_element_count(f"array {tag.name!r}", end)
        inner = tag.type.inner
        if inner.name == "StructProperty":
            return self.read_struct_array(tag, count, end)
        element = SCALAR_READERS.get(inner.name)
        if element is not None:
            return [element(r) for _ in range(count)]
        if inner.name in ("ObjectProperty", "InterfaceProperty"):
            return [read_reference(r) for _ in range(count)]
        if inner.name == "SoftObjectProperty":
            return [read_soft_reference(r) for _ in range(count)]
        if inner.name == "BoolProperty":
            return [r.i8() for _ in range(count)]
        if inner.name == "ByteProperty":
            return list(r.bytes(count))
        if inner.name == "EnumProperty":
            return [r.string() for _ in range(count)]
        if inner.name == "TextProperty":
            return [self.read_text(end) for _ in range(count)]
        return self.skip_unknown(f"array of {inner.name!r}", end)

    def read_struct_array(self, tag: PropertyTag, count: int, end: int):
        """The elements of a struct array. Version 36/52 opens the payload with a full property
        tag, the only place it names the struct, so that tag is read rather than skipped."""
        r = self.r
        struct_type = tag.type.inner.inner
        native = bool(tag.flags & TAG_NATIVE_SERIALIZE)
        if self.ue4_tags:
            inner = read_tag_ue4(r)
            expect(
                inner.type.name == "StructProperty",
                r.pos,
                f"array {tag.name!r} of structs has an inner tag of type "
                f"{inner.type.name!r}, expected StructProperty",
            )
            struct_type = inner.type.inner
            native = False
            end = min(end, r.pos + inner.size)
        if native and struct_type.name not in NATIVE_STRUCTS:
            # elements have no size of their own, so the whole array is skipped by its size
            return self.skip_unknown(f"array of self-serialising {struct_type.name!r}", end)
        return [self.read_struct(struct_type, native_hint=native, end=end) for _ in range(count)]

    def read_set(self, tag: PropertyTag, end: int):
        """``i32 removed, i32 count`` then the elements, reported as ``[type, values]``. A
        nonzero removal count is refused rather than skipped, so the day one appears says so."""
        inner = tag.type.inner
        if self.ue4_tags and is_unnamed_struct(inner):
            # unframed readings first: a set has no struct-array header to read
            return self.first_exact_fit(
                f"version-{self.version} set {tag.name!r} of unnamed structs",
                end,
                *(
                    (lambda t=t: self._read_set_unframed(tag, t, end))
                    for t in unnamed_element_candidates(inner, UNNAMED_SET_CANDIDATES)
                ),
                lambda: self._read_set_as_array(tag, inner, end),
            )
        return self._read_set_as_array(tag, inner, end)

    def _read_set_unframed(self, tag: PropertyTag, inner: TypeName, end: int):
        """A set whose elements are written back to back, without the struct-array header the
        version-36/52 array reading expects."""
        self._expect_no_removals(f"set {tag.name!r}")
        count = self._read_element_count(f"set {tag.name!r}", end)
        # the candidate struct's name: the bytes never named it, so the guess that fit is the label
        label = inner.inner.name or inner.name
        return [label, [self.read_untagged(inner, end) for _ in range(count)]]

    def _read_set_as_array(self, tag: PropertyTag, inner: TypeName, end: int):
        """The set's elements read as an array of its element type."""
        self._expect_no_removals(f"set {tag.name!r}")
        values = self.read_array(
            PropertyTag(tag.name, TypeName("ArrayProperty", [inner]), 0, 0, tag.flags), end
        )
        return [inner.name, values]

    def read_map(self, tag: PropertyTag, end: int):
        """``i32 removed, i32 count`` then untagged key/value pairs, reported as
        ``[[k, v], ...]``, each side typed by the map's own parameters."""
        r = self.r
        expect(
            len(tag.type.params) == 2,
            r.pos,
            f"map {tag.name!r} has {len(tag.type.params)} type parameters, expected a "
            "key type and a value type",
        )
        key_type, value_type = tag.type.params
        if self.ue4_tags and (is_unnamed_struct(key_type) or is_unnamed_struct(value_type)):
            # only the key is substituted; an unnamed value is always a property list
            return self.first_exact_fit(
                f"version-{self.version} map {tag.name!r} of unnamed structs",
                end,
                *(
                    (lambda k=k: self._read_map_entries(tag, k, value_type, end))
                    for k in unnamed_element_candidates(key_type, UNNAMED_KEY_CANDIDATES)
                ),
            )
        return self._read_map_entries(tag, key_type, value_type, end)

    def _read_map_entries(
        self, tag: PropertyTag, key_type: TypeName, value_type: TypeName, end: int
    ):
        """The map's pairs, read with the given key type."""
        self._expect_no_removals(f"map {tag.name!r}")
        count = self._read_element_count(f"map {tag.name!r}", end)
        entries = []
        for _ in range(count):
            key = self.read_untagged(key_type, end)
            value = self.read_untagged(value_type, end)
            entries.append([key, value])
        return entries

    def read_untagged(self, type_name: TypeName, end: int):
        """One untagged map key, map value or set element of a known type.

        Never pass the map's flags byte on: it is set when either side serialises itself.
        """
        r = self.r
        scalar = SCALAR_READERS.get(type_name.name)
        if scalar is not None:
            return scalar(r)
        if type_name.name in ("ObjectProperty", "InterfaceProperty"):
            return read_reference(r)
        if type_name.name == "SoftObjectProperty":
            return read_soft_reference(r)
        if type_name.name == "StructProperty":
            return self.read_struct(type_name.inner, native_hint=False, end=end)
        if type_name.name == "ByteProperty":
            return r.i8()
        if type_name.name == "EnumProperty":
            return r.string()
        return self.skip_unknown(f"untagged {type_name.name!r}", end)

    # -- text -------------------------------------------------------------

    def read_text(self, end: int):
        """FText, reported as ``[flags, historyType, hasCultureInvariant, string]``.

        Only history 0xFF, a plain typed string, is decoded; any other is skipped through
        ``skip_unknown``, whose refusal to move backwards keeps an array of texts honest.
        """
        r = self.r
        flags = r.i32()
        history = r.i8()
        if history != 0xFF:
            self.skip_unknown(f"FText history type {history}", end)
            return [flags, history]
        has_invariant = r.i32()
        return [flags, history, has_invariant, r.string() if has_invariant else None]
