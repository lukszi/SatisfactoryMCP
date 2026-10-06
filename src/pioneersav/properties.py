"""Unreal's tagged property serialiser: the inside of one object's property block.

``read_object`` turns one object's payload slice into the ``[[name, value], ...]`` pairs
``extract_save.props()`` reads. Every property declares its size, so an unknown type costs that
property alone, and a value that does not end exactly there raises with its offset. Both tag
layouts are normalised into one ``TypeName`` tree before any value is read, so there is one
value reader per type. Values keep the shapes the projection already reads, quirks included,
because changing one is a silent change downstream.

Layouts, value shapes and the evidence for both: docs/savparse-notes.md, ``### Properties``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ParseError, expect
from .objects import ObjectSlice
from .reader import Reader
from .references import ObjectReference, read_reference, read_references, read_soft_reference
from .versions import FIRST_MODERN_BODY, FIRST_UE5_OBJECT_VERSION

__all__ = [
    "PLAIN_TRAILER",
    "TAG_ARRAY_INDEX",
    "TAG_BOOL_TRUE",
    "TAG_NATIVE_SERIALIZE",
    "TAG_PROPERTY_GUID",
    "ParsedObject",
    "TypeName",
    "read_object",
]

#: An int32 array index follows the flags byte, written only when the index is nonzero.
TAG_ARRAY_INDEX = 0x01
#: A 16-byte property guid follows.
TAG_PROPERTY_GUID = 0x02
#: Tag extensions follow, in a layout nothing here reads; refused rather than ignored.
TAG_EXTENSIONS = 0x04
#: The payload is the type's own binary form. It says *that* a struct serialises itself, not
#: how: ``_NATIVE_STRUCTS`` is the only authority on how.
TAG_NATIVE_SERIALIZE = 0x08
#: A version-60 BoolProperty's value; version 36/52 writes 1 in its tag data instead.
TAG_BOOL_TRUE = 0x10

#: The name of the tag that ends a property list: a real tag name, so the list is walked.
_TERMINATOR = "None"

#: Guard on how deep property lists may nest: low enough to raise a located ``ParseError``
#: long before CPython's recursion limit raises a ``RecursionError`` with no offset in it.
_MAX_NESTING = 32

#: Bytes between the property list's ``"None"`` terminator and the end of an object's payload
#: when its class writes nothing of its own. A component always leaves one of these; an actor
#: with class-specific data leaves more. Which of the two a given object gets is not established.
PLAIN_TRAILER = (4, 8)

#: Guard on the type-name tree's branching factor. A MapProperty has two parameters; 16 is
#: loose enough to survive a patch and tight enough that a misaligned cursor reading a float as
#: a count fails here instead of allocating.
_MAX_TYPE_PARAMS = 16


@dataclass(slots=True)
class TypeName:
    """A property's type as a tree: ``ArrayProperty(StructProperty(InventoryStack(...)))``.

    Version 60 writes the tree; ``_read_tag_ue4`` rebuilds it from UE4's fixed tag data, so
    the value readers are version-agnostic.
    """

    name: str
    params: list[TypeName] = field(default_factory=list)

    @property
    def inner(self) -> TypeName:
        """First parameter, or a nameless one, which the readers skip as an unknown type."""
        return self.params[0] if self.params else TypeName("")

    def flat(self) -> list:
        """The tree as ``[name, paramCount, ...]`` in prefix order, which reads back into it."""
        if not self.params:
            return [self.name, 0]
        return [self.name, len(self.params), *(x for p in self.params for x in p.flat())]


@dataclass(slots=True)
class ParsedObject:
    """One object's property block, decoded.

    ``properties`` holds the ``[name, value]`` pairs the projection consumes;
    ``property_types`` the parallel types, the only record of what a value *was*.
    """

    version: int
    #: Actors only: the object this one hangs off, and its component children.
    parent_reference: ObjectReference | None = None
    child_references: list[ObjectReference] = field(default_factory=list)
    properties: list[list] = field(default_factory=list)
    property_types: list[list] = field(default_factory=list)
    #: Absolute span of everything after the terminator: the plain trailer, plus class-specific
    #: data on some actors, which ``pioneersav.save`` arranges to decode.
    extra_offset: int = 0
    extra_length: int = 0
    #: The trailing class-specific bytes, decoded on first access to ``actorSpecificInfo``.
    actor_specific_info: list | None = None
    #: Zero-argument decoder for the trailing bytes, set where the class is known; else ``None``.
    decode_trailer: object | None = None
    #: Anything skipped rather than understood, as ``(offset, what)``.
    warnings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def actorSpecificInfo(self) -> list | None:
        """The trailing class-specific bytes, decoded on first access.

        ``None``, never ``[]``, when no reader knows the class: an empty list would pass for a
        decoded blob holding nothing.
        """
        if self.actor_specific_info is None and self.decode_trailer is not None:
            self.actor_specific_info = self.decode_trailer()
        return self.actor_specific_info


class _TooDeep(ParseError):
    """The nesting guard tripping: a ``ParseError`` ``attempt()`` must not swallow."""


# ----------------------------------------------------------- struct bodies


def _vector(d: _Decoder) -> list[float]:
    """FVector: three doubles on a UE5 save, three floats on a UE4 one. The width follows the
    save, not the object: a UE5 game writes doubles even into a version-36 object."""
    r = d.r
    if d.ue4_save:
        return [r.f32(), r.f32(), r.f32()]
    return [r.f64(), r.f64(), r.f64()]


def _quat(d: _Decoder) -> list[float]:
    """FQuat: four doubles on a UE5 save and four floats on a UE4 one, as ``_vector``."""
    r = d.r
    if d.ue4_save:
        return [r.f32(), r.f32(), r.f32(), r.f32()]
    return [r.f64(), r.f64(), r.f64(), r.f64()]


def _box(d: _Decoder) -> list:
    """FBox: min, max and a validity byte, seven entries at ``_vector``'s width."""
    return [*_vector(d), *_vector(d), d.r.i8() != 0]


def _linear_color(d: _Decoder) -> list[float]:
    """FLinearColor stayed four *floats* through the UE5 upgrade; FVector did not."""
    r = d.r
    return [r.f32(), r.f32(), r.f32(), r.f32()]


def _guid(d: _Decoder) -> list[int]:
    """16 bytes, reported as two uint64s."""
    r = d.r
    return [r.u64(), r.u64()]


def _int_vector(d: _Decoder) -> list[int]:
    """FIntVector: a world-partition cell coordinate, e.g. the foliage grid's ``[-7,-25,-1]``."""
    r = d.r
    return [r.i32(), r.i32(), r.i32()]


def _fluid_box(d: _Decoder) -> float:
    """FFluidBox: one float, the fluid currently in a pipe segment."""
    return d.r.f32()


def _client_identity_info(d: _Decoder) -> list:
    """``[offlineId, [[platform, idBytes], ...]]``: who owns a player state, one length-prefixed
    account id per linked platform, left as bytes because nothing reads it."""
    r = d.r
    offline_id = r.string()
    count = r.i32()
    expect(0 <= count <= 64, r.pos - 4, f"a client identity claims {count} platforms")
    out = []
    for _ in range(count):
        platform = r.i8()
        out.append([platform, r.bytes(r.i32())])
    return [offline_id, out]


def _inventory_item_modern(d: _Decoder) -> list:
    """``FInventoryItem`` as an object reference, a has-state int32, and the state if there is one.

    State is the state class plus a sized, nested property list (a weapon's ammo counter), so
    it is read rather than skipped. Element 0 is the bare path string, not an
    ``ObjectReference``, because ``_accumulate_inventory``'s ``ref_class`` resolves only that.
    """
    r = d.r
    item_class = read_reference(r)
    has_state = r.i32()
    if not has_state:
        return [item_class.path_name, None]
    state_class = read_reference(r)
    size = r.i32()
    expect(
        0 <= size <= r.remaining,
        r.pos - 4,
        f"an item state claims {size} bytes with {r.remaining} left",
    )
    values, types = d.property_list(r.pos + size)
    return [item_class.path_name, [state_class.path_name, values, types]]


def _inventory_item_legacy(d: _Decoder) -> list:
    """``FInventoryItem`` as two bare object references: the descriptor, then the ``Equip_*_C``
    actor this item instance is, or two empty strings."""
    r = d.r
    item_class = read_reference(r)
    return [item_class.path_name, read_reference(r).path_name or None]


def _inventory_item(d: _Decoder) -> list:
    """``FInventoryItem`` where no declared size can referee the layout, so the version guesses.

    Only a bare container element lands here; ``_Decoder.struct`` lets the declared size choose
    everywhere else, because the layout follows no version in the file (savparse-notes.md).
    """
    if d.version < FIRST_MODERN_BODY:
        return _inventory_item_legacy(d)
    return _inventory_item_modern(d)


#: Self-serialising identity handles kept as raw bytes without a warning, so that they do not
#: bury a real one: which player placed a buildable, and an account id.
_OPAQUE_STRUCTS = frozenset({"PlayerInfoHandle", "UniqueNetIdRepl"})

#: Structs whose payload is raw numbers rather than a nested property list: the only authority
#: on how a struct serialises itself. Add nothing on the strength of its name --
#: ``Vector_NetQuantize`` is a tagged property list.
_NATIVE_STRUCTS = {
    "Vector": _vector,
    "Quat": _quat,
    "Box": _box,
    "LinearColor": _linear_color,
    "Guid": _guid,
    "IntVector": _int_vector,
    "FluidBox": _fluid_box,
    "ClientIdentityInfo": _client_identity_info,
    "InventoryItem": _inventory_item,
}


# ---------------------------------------------------------------- the tags


#: Struct names to try, narrowest first, for an unnamed version-36/52 map KEY; an unnamed value
#: is always a property list. Each is the type saveVersion 60 names for the same field; the
#: empty name, a property list, stays last.
_UNNAMED_KEY_CANDIDATES = ("IntVector", "")

#: The same for an unnamed SET element. A 16-byte ``Guid`` cannot land as a 24-byte vector.
_UNNAMED_SET_CANDIDATES = ("Guid", "Vector", "")


def _unnamed_element_candidates(
    element_type: TypeName, names: tuple[str, ...]
) -> tuple[TypeName, ...]:
    """Element types to try, most specific first. A named element comes back unchanged."""
    if not _is_unnamed_struct(element_type):
        return (element_type,)
    return tuple(TypeName("StructProperty", [TypeName(n)]) for n in names)


def _unnamed_key_candidates(key_type: TypeName) -> tuple[TypeName, ...]:
    """Map-key types to try, most specific first."""
    return _unnamed_element_candidates(key_type, _UNNAMED_KEY_CANDIDATES)


def _is_unnamed_struct(type_name: TypeName) -> bool:
    """A struct the bytes never name: a version-36/52 map or set element, whose tag data stops
    at ``"StructProperty"`` because UE4 took the struct's name from reflection."""
    return type_name.name == "StructProperty" and not type_name.params


def _read_type_name(r: Reader, depth: int = 0) -> TypeName:
    """Version 60's type tree: a name, a parameter count, then that many subtrees.

    A tree, not a flat list, because a struct's package is a parameter of the struct name:
    ``StructProperty(InventoryStack(/Script/FactoryGame))``.
    """
    at = r.pos
    name = r.string()
    count = r.i32()
    expect(
        0 <= count <= _MAX_TYPE_PARAMS,
        r.pos - 4,
        f"type name {name!r} at {at} claims {count} parameters; a property type takes 0 to "
        f"{_MAX_TYPE_PARAMS}, so the cursor is not on a tag",
    )
    expect(depth < 8, at, f"type name {name!r} nested more than 8 deep")
    return TypeName(name, [_read_type_name(r, depth + 1) for _ in range(count)])


@dataclass(slots=True)
class _Tag:
    name: str
    type: TypeName
    size: int
    index: int
    flags: int
    #: Version 36/52 only: a BoolProperty's value, which version 60 keeps in the flags byte.
    bool_value: int = 0


def _read_tag_ue5(r: Reader) -> _Tag:
    """Object version 60's tag, UE5's ``FPropertyTag``::

        str  name
        ...  type name tree   str name, i32 param count, then that many type names
        i32  size             bytes of payload, counted from after the flags byte
        u8   flags            see TAG_*
        i32  array index      only when flags & TAG_ARRAY_INDEX
        16B  property guid    only when flags & TAG_PROPERTY_GUID

    The terminator is a bare name with no type tree, so the name is checked first.
    """
    name = r.string()
    if name == _TERMINATOR:
        return _Tag(name=name, type=TypeName(""), size=0, index=0, flags=0)
    tag = _Tag(name=name, type=_read_type_name(r), size=0, index=0, flags=0)
    tag.size = r.i32()
    expect(tag.size >= 0, r.pos - 4, f"property {tag.name!r} declares size {tag.size}")
    # checked before the fields it would move, so the offset names the flags byte
    at_flags = r.pos
    tag.flags = r.i8()
    expect(
        not tag.flags & TAG_EXTENSIONS,
        at_flags,
        f"property {tag.name!r} sets tag bit 0x04, which announces tag extensions in a "
        "layout this parser does not read",
    )
    if tag.flags & TAG_ARRAY_INDEX:
        tag.index = r.i32()
    if tag.flags & TAG_PROPERTY_GUID:
        r.skip(16)
    return tag


def _read_tag_ue4(r: Reader) -> _Tag:
    """Object version 36/52's tag, UE4's: the same information in fixed positions::

        str  name
        str  type
        i32  size
        i32  array index
        ...  type-specific tag data   an array's inner type, a struct's name and guid,
                                      an enum's name, a bool's VALUE
        u8   has property guid
        16B  property guid            only when that byte is 1

    The tag data is folded into a ``TypeName`` here so the value readers see one tag shape.
    """
    name = r.string()
    if name == _TERMINATOR:
        return _Tag(name=name, type=TypeName(""), size=0, index=0, flags=0)
    type_name = r.string()
    size = r.i32()
    expect(size >= 0, r.pos - 4, f"property {name!r} declares size {size}")
    index = r.i32()

    params: list[TypeName] = []
    bool_value = 0
    if type_name in ("ArrayProperty", "SetProperty", "ByteProperty", "EnumProperty"):
        params = [TypeName(r.string())]
    elif type_name == "MapProperty":
        params = [TypeName(r.string()), TypeName(r.string())]
    elif type_name == "StructProperty":
        params = [TypeName(r.string())]
        r.skip(16)  # the struct's guid
    elif type_name == "BoolProperty":
        bool_value = r.i8()

    has_guid = r.i8()
    expect(
        has_guid in (0, 1),
        r.pos - 1,
        f"property {name!r} has a property-guid flag of {has_guid}; UE4 writes 0 or 1 "
        "there, so the tag data for this type is longer than assumed",
    )
    if has_guid:
        r.skip(16)
    # no native-serialise bit in UE4; never synthesise one, or SpawnData lists get skipped
    return _Tag(
        name=name,
        type=TypeName(type_name, params),
        size=size,
        index=index,
        flags=0,
        bool_value=bool_value,
    )


# -------------------------------------------------------------- the values


class _Decoder:
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
            raise _TooDeep(
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
                f"a property list ran to {limit} without its {_TERMINATOR!r} terminator",
            )
            tag = _read_tag_ue4(r) if self.ue4_tags else _read_tag_ue5(r)
            if tag.name == _TERMINATOR:
                return values, types
            expect(
                r.pos + tag.size <= limit,
                r.pos,
                f"property {tag.name!r} declares {tag.size} bytes, which runs "
                f"{r.pos + tag.size - limit} past the end of its block",
            )
            start = r.pos
            end = start + tag.size
            value = self.value(tag, end)
            expect(
                r.pos == end,
                r.pos,
                f"property {tag.name!r} of type {tag.type.name!r} declared {tag.size} "
                f"bytes but {r.pos - start} were read",
            )
            values.append([tag.name, value])
            types.append([tag.name, *tag.type.flat(), tag.flags])

    # -- one property -----------------------------------------------------

    def value(self, tag: _Tag, end: int):
        """Dispatch on the type name. ``end`` is where the payload must stop."""
        r = self.r
        name = tag.type.name
        reader = _SCALARS.get(name)
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
            return self.struct(tag.type.inner, native_hint=native, end=end, exact=True)
        if name == "ArrayProperty":
            return self.array(tag, end)
        if name == "SetProperty":
            return self.set_(tag, end)
        if name == "MapProperty":
            return self.map_(tag, end)
        if name == "TextProperty":
            return self.text(end)
        return self.unknown(f"property type {name!r}", end)

    def unknown(self, what: str, end: int):
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

    def attempt(self, what: str, end: int, *decoders):
        """Try each reading in turn; keep the first that lands exactly on the declared ``end``.

        Pass the narrowest reading first: a wrong narrow one fails at once, a permissive one
        can absorb a lot before it does.
        """
        start = self.r.pos
        mark = len(self.warnings)
        for decode in decoders:
            try:
                value = decode()
            except _TooDeep:
                # the stack guard, not a wrong guess: the next reading would trip it too
                raise
            except ValueError:
                pass
            else:
                if self.r.pos == end:
                    return value
            del self.warnings[mark:]
            self.r.pos = start
        return self.unknown(what, end)

    # -- enums ------------------------------------------------------------

    def enum_name(self, tag: _Tag) -> str | None:
        """The enum a Byte/Enum property is typed by, or ``None`` for a plain byte, whether UE4
        wrote the string ``"None"`` or UE5 wrote no parameter."""
        inner = tag.type.inner.name
        return inner if inner and inner != _TERMINATOR else None

    def byte_value(self, tag: _Tag):
        """``[enumName, value]``: ``[None, 2]`` for a plain byte, ``['EGamePhase',
        'EGP_MidGame']`` for an enum; readers take ``[-1]`` off either."""
        enum = self.enum_name(tag)
        return [enum, self.r.string() if enum else self.r.i8()]

    # -- structs ----------------------------------------------------------

    def struct(self, struct_type: TypeName, *, native_hint: bool, end: int, exact: bool = False):
        """A struct: raw numbers if its NAME is in ``_NATIVE_STRUCTS``, else a property list.
        ``exact`` means ``end`` is this struct's own declared end, which lets the size referee
        ``InventoryItem`` and turn an unknown non-list struct into a skip (savparse-notes.md).
        """
        native = _NATIVE_STRUCTS.get(struct_type.name)
        if native is not None:
            if native is _inventory_item and exact:
                # the two layouts can never land on the same byte, so the size decides outright
                return self.attempt(
                    "an InventoryItem that reads as neither layout",
                    end,
                    lambda: _inventory_item_modern(self),
                    lambda: _inventory_item_legacy(self),
                )
            return native(self)
        if native_hint:
            # bytes rather than None, so the caller sees what it got and the size balances
            if struct_type.name not in _OPAQUE_STRUCTS:
                self.warnings.append(
                    (self.r.pos, f"struct {struct_type.name!r} serialises itself, kept as bytes")
                )
            return self.r.bytes(end - self.r.pos)
        if exact:
            return self.attempt(
                f"struct {struct_type.name!r} that is neither native nor a property list",
                end,
                lambda: list(self.property_list(end)),
            )
        return list(self.property_list(end))

    # -- containers -------------------------------------------------------

    def _count(self, what: str, end: int) -> int:
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

    def array(self, tag: _Tag, end: int):
        """``i32 count`` then the elements, untagged: their type is the array's own parameter."""
        r = self.r
        count = self._count(f"array {tag.name!r}", end)
        inner = tag.type.inner
        if inner.name == "StructProperty":
            return self.struct_array(tag, count, end)
        element = _SCALARS.get(inner.name)
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
            return [self.text(end) for _ in range(count)]
        return self.unknown(f"array of {inner.name!r}", end)

    def struct_array(self, tag: _Tag, count: int, end: int):
        """The elements of a struct array. Version 36/52 opens the payload with a full property
        tag, the only place it names the struct, so that tag is read rather than skipped."""
        r = self.r
        struct_type = tag.type.inner.inner
        native = bool(tag.flags & TAG_NATIVE_SERIALIZE)
        if self.ue4_tags:
            inner = _read_tag_ue4(r)
            expect(
                inner.type.name == "StructProperty",
                r.pos,
                f"array {tag.name!r} of structs has an inner tag of type "
                f"{inner.type.name!r}, expected StructProperty",
            )
            struct_type = inner.type.inner
            native = False
            end = min(end, r.pos + inner.size)
        if native and struct_type.name not in _NATIVE_STRUCTS:
            # elements have no size of their own, so the whole array is skipped by its size
            return self.unknown(f"array of self-serialising {struct_type.name!r}", end)
        return [self.struct(struct_type, native_hint=native, end=end) for _ in range(count)]

    def set_(self, tag: _Tag, end: int):
        """``i32 removed, i32 count`` then the elements, reported as ``[type, values]``. A
        nonzero removal count is refused rather than skipped, so the day one appears says so."""
        inner = tag.type.inner
        if self.ue4_tags and _is_unnamed_struct(inner):
            # unframed readings first: a set has no struct-array header to read
            return self.attempt(
                f"version-{self.version} set {tag.name!r} of unnamed structs",
                end,
                *(
                    (lambda t=t: self._set_body_bare(tag, t, end))
                    for t in _unnamed_element_candidates(inner, _UNNAMED_SET_CANDIDATES)
                ),
                lambda: self._set_body(tag, inner, end),
            )
        return self._set_body(tag, inner, end)

    def _set_body_bare(self, tag: _Tag, inner: TypeName, end: int):
        """A set whose elements are written back to back, without the struct-array header the
        version-36/52 array reading expects."""
        r = self.r
        removed = r.i32()
        expect(
            removed == 0,
            r.pos - 4,
            f"set {tag.name!r} declares {removed} removed entries; a saved container has "
            "no removal list",
        )
        count = self._count(f"set {tag.name!r}", end)
        # the candidate struct's name: the bytes never named it, so the guess that fit is the label
        label = inner.inner.name or inner.name
        return [label, [self.element(inner, end) for _ in range(count)]]

    def _set_body(self, tag: _Tag, inner: TypeName, end: int):
        """The set's elements read as an array of its element type."""
        r = self.r
        removed = r.i32()
        expect(
            removed == 0,
            r.pos - 4,
            f"set {tag.name!r} declares {removed} removed entries; a saved container has "
            "no removal list",
        )
        values = self.array(
            _Tag(tag.name, TypeName("ArrayProperty", [inner]), 0, 0, tag.flags), end
        )
        return [inner.name, values]

    def map_(self, tag: _Tag, end: int):
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
        if self.ue4_tags and (_is_unnamed_struct(key_type) or _is_unnamed_struct(value_type)):
            # only the key is substituted; an unnamed value is always a property list
            return self.attempt(
                f"version-{self.version} map {tag.name!r} of unnamed structs",
                end,
                *(
                    (lambda k=k: self._map_body(tag, k, value_type, end))
                    for k in _unnamed_key_candidates(key_type)
                ),
            )
        return self._map_body(tag, key_type, value_type, end)

    def _map_body(self, tag: _Tag, key_type: TypeName, value_type: TypeName, end: int):
        """The map's pairs, read with the given key type."""
        r = self.r
        removed = r.i32()
        expect(
            removed == 0,
            r.pos - 4,
            f"map {tag.name!r} declares {removed} removed entries; a saved container has "
            "no removal list",
        )
        count = self._count(f"map {tag.name!r}", end)
        out = []
        for _ in range(count):
            key = self.element(key_type, end)
            value = self.element(value_type, end)
            out.append([key, value])
        return out

    def element(self, type_name: TypeName, end: int):
        """One untagged map key, map value or set element of a known type.

        Never pass the map's flags byte on: it is set when either side serialises itself.
        """
        r = self.r
        scalar = _SCALARS.get(type_name.name)
        if scalar is not None:
            return scalar(r)
        if type_name.name in ("ObjectProperty", "InterfaceProperty"):
            return read_reference(r)
        if type_name.name == "SoftObjectProperty":
            return read_soft_reference(r)
        if type_name.name == "StructProperty":
            return self.struct(type_name.inner, native_hint=False, end=end)
        if type_name.name == "ByteProperty":
            return r.i8()
        if type_name.name == "EnumProperty":
            return r.string()
        return self.unknown(f"untagged {type_name.name!r}", end)

    # -- text -------------------------------------------------------------

    def text(self, end: int):
        """FText, reported as ``[flags, historyType, hasCultureInvariant, string]``.

        Only history 0xFF, a plain typed string, is decoded; any other is skipped through
        ``unknown``, whose refusal to move backwards keeps an array of texts honest.
        """
        r = self.r
        flags = r.i32()
        history = r.i8()
        if history != 0xFF:
            self.unknown(f"FText history type {history}", end)
            return [flags, history]
        has_invariant = r.i32()
        return [flags, history, has_invariant, r.string() if has_invariant else None]


#: Types whose payload is one fixed-width value with no framing, read the same way as a tagged
#: property, an array element or a map key. No 16-bit reader: none has met real bytes, and an
#: unknown type is skipped with a warning. ``Int8Property`` yields raw ``bytes``.
_SCALARS = {
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


def read_object(
    body: bytes,
    slot: ObjectSlice,
    *,
    actor: bool,
    save_version: int = FIRST_MODERN_BODY,
) -> ParsedObject:
    """Decode one object's property block, the ``slot`` of ``body`` that ``read_body`` found.

    ``actor`` comes from the object's header, since only an actor's payload opens with
    reference lists and the payload does not say which it is. ``save_version`` is the file's,
    and picks the float width of ``FVector``/``FQuat``/``FBox``.
    """
    r = Reader(body, slot.offset)
    end = slot.end
    out = ParsedObject(version=slot.version)

    if actor:
        out.parent_reference = read_reference(r)
        out.child_references = read_references(r, end)
    if slot.version >= FIRST_UE5_OBJECT_VERSION:
        r.i8()  # the object-reference migration byte

    decoder = _Decoder(r, slot.version, out.warnings, save_version=save_version)
    out.properties, out.property_types = decoder.property_list(end)
    out.extra_offset = r.pos
    out.extra_length = end - r.pos

    # a list ending inside its trailer means a name was read as None
    expect(
        out.extra_length >= PLAIN_TRAILER[0],
        r.pos,
        f"the property list of {'an actor' if actor else 'a component'} ended "
        f"{out.extra_length} bytes before its {slot.length}-byte payload does, and no "
        f"object's trailer is shorter than {PLAIN_TRAILER[0]}. A property name was "
        "read as the list terminator, so the properties after it are missing",
    )
    # actor trailers stay unbounded: a patch may add a ninth class
    if not actor:
        expect(
            out.extra_length in PLAIN_TRAILER,
            r.pos,
            f"a component's property list left {out.extra_length} bytes of its "
            f"{slot.length}-byte payload unread, and a component's trailer is exactly "
            f"{' or '.join(map(str, PLAIN_TRAILER))} bytes, so the list terminated early "
            "and the properties after that point are missing",
        )
    return out
