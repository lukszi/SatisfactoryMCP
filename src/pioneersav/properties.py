"""Unreal's tagged property serialiser: the inside of one object's property block.

``objects.py`` hands this module a slice that is exactly one object's payload, and this turns
it into ``[[name, value], ...]``, the shape ``extract_save.props()`` reads. Every property
carries a **declared size**, so a type this module has never heard of costs that one property
and nothing else -- and after reading a value the cursor must be exactly ``size`` bytes past
the payload start, or this raises with the offset. Two tag layouts occur, keyed by the
object's own serialisation version, and both are unified into one ``TypeName`` tree before any
value is read so that there is one value reader per property type rather than two.

Object version **60**, UE5's ``FPropertyTag``::

    str  name
    ...  type name TREE          str name, i32 param count, then that many type names
    i32  size                    bytes of payload, counted from after the flags byte
    u8   flags                   see TAG_* below
    i32  array index             only when flags & TAG_ARRAY_INDEX
    16B  property guid           only when flags & TAG_PROPERTY_GUID
    ...  size bytes of payload

Object version **36/52**, UE4's. The same information, in fixed positions instead of a tree::

    str  name
    str  type
    i32  size
    i32  array index
    ...  type-specific tag data  the inner type of an array, a struct's name and guid,
                                 an enum's name, a bool's VALUE
    u8   has property guid
    16B  property guid           only when that byte is 1
    ...  size bytes of payload

The values are shaped to match what the projection already reads, quirks included, so
changing any of them is a silent behaviour change downstream: ``BoolProperty`` yields the raw
byte (16, 1 or 0) rather than a ``bool``; ``ByteProperty`` yields ``[enumName or None,
value]``; a struct serialised as a nested property list yields ``[values, types]``; and
``InventoryItem`` yields ``[itemClassPath, state]`` with the class as a bare path string.

The property list is not the whole payload. An actor opens with its parent reference and its
child-component references, and after the ``"None"`` terminator come trailing bytes whose
count tells a plain object from one carrying class-specific data. Those are handed on as an
offset and a length and decoded lazily -- see ``pioneersav.save``.
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

#: An int32 array index follows the flags byte, written only when the index is nonzero. Miss
#: it and the payload starts four bytes late, which the per-property size check catches.
TAG_ARRAY_INDEX = 0x01
#: A 16-byte property guid follows. Never seen set on any save here; read anyway.
TAG_PROPERTY_GUID = 0x02
#: Reserved-looking bit, never seen set. Refused rather than ignored, see _read_tag_ue5.
TAG_EXTENSIONS = 0x04
#: The payload is the type's own binary form, not a nested property list. It says *that* a
#: struct serialises itself and not how, so ``_NATIVE_STRUCTS`` is the only authority on how.
TAG_NATIVE_SERIALIZE = 0x08
#: A BoolProperty's value, and the only place it is stored on a version-60 object -- a
#: version-36 object writes 1 in its tag data instead. So a bool arrives as 16, 1 or 0.
TAG_BOOL_TRUE = 0x10

#: UE writes "None" as the name of the tag that ends a property list. It is a real tag name,
#: not a sentinel byte, so the list is walked and not searched.
_TERMINATOR = "None"

#: Guard on how deep property lists may nest inside one another. The deepest real one on this
#: disk is 4, and this is small enough to raise a ``ParseError`` long before CPython's
#: recursion limit turns the same bytes into a ``RecursionError`` with no byte offset in it.
_MAX_NESTING = 32

#: Bytes between the property list's ``"None"`` terminator and the end of an object's payload
#: when its class writes nothing of its own. A component always leaves one of these; an actor
#: with class-specific data leaves more. Which of the two a given object gets is not established.
PLAIN_TRAILER = (4, 8)

#: Guard on the type-name tree's branching factor. A MapProperty has two parameters and
#: nothing seen has more; 16 is loose enough to survive a patch and tight enough that a
#: misaligned cursor reading a float as a count fails here instead of allocating.
_MAX_TYPE_PARAMS = 16


@dataclass(slots=True)
class TypeName:
    """A property's type as a tree: ``ArrayProperty(StructProperty(InventoryStack(...)))``.

    Version 60 writes this tree literally. Version 36/52 writes the same information as fixed
    tag-data fields, and ``_read_tag_ue4`` reassembles it into this shape so that every value
    reader below is version-agnostic.
    """

    name: str
    params: list[TypeName] = field(default_factory=list)

    @property
    def inner(self) -> TypeName:
        """First parameter, or a nameless one -- an array whose element type went missing.

        A placeholder rather than a raise, so the element reader hits the unknown-type path,
        warns, and the property is skipped by its size.
        """
        return self.params[0] if self.params else TypeName("")

    def flat(self) -> list:
        """The tree as a flat list: each node is its name then its parameter count.

        A rendering choice, not a format fact, chosen because ``[name, n, ...]`` can be read
        back into the tree it came from. Nothing consumes it beyond ``struct_fields()``, which
        only needs each entry to start with a name.
        """
        if not self.params:
            return [self.name, 0]
        return [self.name, len(self.params), *(x for p in self.params for x in p.flat())]


@dataclass(slots=True)
class ParsedObject:
    """One object's property block, decoded.

    ``properties`` is the list of ``[name, value]`` pairs the projection consumes;
    ``property_types`` is the parallel list of types, kept because it is the only record of
    what a value *was* once it has been flattened into Python.
    """

    version: int
    #: Actors only: the object this one hangs off, and its component children.
    parent_reference: ObjectReference | None = None
    child_references: list[ObjectReference] = field(default_factory=list)
    properties: list[list] = field(default_factory=list)
    property_types: list[list] = field(default_factory=list)
    #: Absolute offset and length of everything after the property list's terminator: a 4- or
    #: 8-byte trailer, plus class-specific binary data on some actors. Not decoded here;
    #: ``pioneersav.save`` decodes the classes it knows.
    extra_offset: int = 0
    extra_length: int = 0
    #: The trailing class-specific bytes, decoded -- ``None`` when nothing knows the class.
    #: Filled on first access to ``actorSpecificInfo`` rather than during the parse.
    actor_specific_info: list | None = None
    #: Zero-argument decoder for this object's trailing bytes, or ``None`` when no reader
    #: exists for its class. Set at composition time, since only there is the class known.
    decode_trailer: object | None = None
    #: Anything skipped rather than understood, as ``(offset, what)``.
    warnings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def actorSpecificInfo(self) -> list | None:
        """The trailing class-specific bytes, decoded on first access.

        ``None`` rather than an empty list when no reader exists for the class: an empty list
        is what a decoded blob holding nothing looks like, and "nobody taught this parser that
        class" must not be able to pass for it.
        """
        if self.actor_specific_info is None and self.decode_trailer is not None:
            self.actor_specific_info = self.decode_trailer()
        return self.actor_specific_info


class _TooDeep(ParseError):
    """The nesting guard tripping, as a type ``attempt`` can tell apart from a bad guess.

    A ``ParseError`` still, so nothing outside this module has to know it exists: the sidecar's
    contract is that a bad file raises ``ParseError`` and only ``ParseError``, and this keeps
    it. It is separate only so that the speculative readings in ``attempt`` -- which exist to
    swallow a wrong guess and try the next one -- do not also swallow the one error that says
    the parser is about to run out of Python stack. Absorbed there it stops being a guess that
    failed and becomes a guess that gets retried all the way down.
    """


# ----------------------------------------------------------- struct bodies


def _vector(d: _Decoder) -> list[float]:
    """FVector: three doubles on a UE5 save, three floats on a UE4 one.

    **The width follows the writer, and the writer is the SAVE, not the object.** A UE5 game
    writes 24 bytes even into a version-36 object, so keying this on the object's version would
    be wrong on exactly those.
    """
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
    """FBox: min, max, and a validity byte, which is why the list has 7 entries. It inherits
    ``_vector``'s width, so it is 49 bytes on a UE5 save and 25 on a UE4 one.
    """
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
    """FFluidBox is one float: the litres currently in a pipe segment, in the same units the
    projection's ``inventories`` reports fluids in.
    """
    return d.r.f32()


def _client_identity_info(d: _Decoder) -> list:
    """``[offlineId, [[platform, idBytes], ...]]`` -- who owns a player state.

    A 32-hex-character offline id, then a count, then one (platform byte, length-prefixed
    blob) per platform the account is linked to. The blob is left as bytes: it is an
    account identifier, it is not ours to interpret, and nothing reads it.
    """
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

    State, when present, is another object reference naming the state class plus a **sized,
    nested property list** -- a rifle in the player's arm slot carries
    ``/Script/FactoryGame.FGWeaponItemState`` with its own ``CurrentAmmoCount``, which is the
    game's ammo counter and is why this is read rather than skipped.

    What comes out at element 0 is the **path string**, not an ``ObjectReference``, because
    ``_accumulate_inventory`` runs ``ref_class`` on it and that resolves a bare path string but
    would take the ``repr`` of a reference object and find nothing.
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
    """``FInventoryItem`` as two bare object references and nothing else.

    The descriptor, then the ``Equip_*_C`` actor this item instance is, or two empty strings.
    No has-state int32 and no nested property list.
    """
    r = d.r
    item_class = read_reference(r)
    return [item_class.path_name, read_reference(r).path_name or None]


def _inventory_item(d: _Decoder) -> list:
    """``FInventoryItem``, guessed from the object's version because nothing better is on hand.

    **Only reached when the record's declared size is not available** -- see ``_Decoder.struct``,
    which refereees the two readings by that size wherever it has one, which in every save in
    hand is everywhere. This is the fallback for a bare element of a container, and the gate
    below is a guess.

    It has to be a guess, because **which layout a record uses does not follow from any version
    number in the file.** That was worth establishing the hard way:

    * an object stamped version 46 in an autosave the game wrote at build 416835 uses the two
      references -- 16 declared bytes, four empty strings;
    * an object stamped version 46 in Episode 107, build 463028, uses the modern layout -- 99
      declared bytes, a reference and a has-state int32, where two references cannot fit.

    Same object version, same ``saveVersion`` 52, different layouts. Somewhere between those two
    builds the game began writing these legacy records the modern way without restamping them,
    so the object version says nothing and the save version says nothing. Only the bytes do.
    """
    if d.version < FIRST_MODERN_BODY:
        return _inventory_item_legacy(d)
    return _inventory_item_modern(d)


#: Self-serialising structs kept as raw bytes on purpose, so that they do not show up as
#: warnings and hide a real one. Both are identity handles the projection has no use for:
#: ``PlayerInfoHandle`` is which player placed a buildable, ``UniqueNetIdRepl`` an account id.
_OPAQUE_STRUCTS = frozenset({"PlayerInfoHandle", "UniqueNetIdRepl"})

#: Structs whose payload is raw numbers rather than a nested property list. The flags byte
#: says *that* a struct serialises itself; only this table says *how*, and a struct missing
#: from it is handed back as raw bytes with a warning rather than guessed at. Add nothing here
#: on the strength of its name: ``Vector_NetQuantize`` looks like it belongs and does not --
#: the map markers' ``Location`` is 135 bytes of tagged ``X``/``Y``/``Z`` DoubleProperties.
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


#: Struct names to try for a version-36/52 map KEY the bytes leave unnamed, narrowest first.
#: Only the key needs this: a map whose *value* is an unnamed struct is already handled, since
#: ``element`` routes it to ``property_list`` and that is what those values are.
#:
#: ``IntVector`` covers the foliage subsystem's ``mSaveData``, which keys its per-cell records
#: by world-partition cell coordinate; saveVersion 60 writes that type out in full for the same
#: map, so the newer format is the authority here rather than the guess being one.
#:
#: The empty name is an unnamed struct read as a property list, and stays last.
_UNNAMED_KEY_CANDIDATES = ("IntVector", "")

#: The same, for a version-36/52 SET element the bytes leave unnamed. ``Guid`` covers the
#: scanner's ``mDestroyedPickups`` and ``mLootedDropPods``, ``Vector`` covers
#: ``FGFoliageRemoval.mRemovalLocations``; saveVersion 60 names both types in full. ``Guid``
#: goes first because a 16-byte GUID cannot land as a 24-byte vector, and the empty name last.
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
    """A struct the bytes never name, which is only possible on version 36/52.

    UE4's tag data for a map or a set carries the element's *property* type -- literally the
    string ``"StructProperty"`` -- and stops there, because the engine got the struct's own
    name from reflection. Nothing then distinguishes a native ``IntVector`` key (12 raw bytes)
    from a struct written as a property list, and both occur in the very same map: the foliage
    subsystem's ``mSaveData`` has ``IntVector`` cell coordinates as keys and property lists as
    values. Version 60 has no such problem, since the type tree names the struct.
    """
    return type_name.name == "StructProperty" and not type_name.params


def _read_type_name(r: Reader, depth: int = 0) -> TypeName:
    """Version 60's type tree: a name, a parameter count, then that many subtrees.

    ``StructProperty(InventoryStack(/Script/FactoryGame))`` is how a struct carries both its
    name and its package, and the package is a parameter of the struct name rather than of the
    property -- which is what makes this a tree and not a flat list of extra fields.
    """
    at = r.pos
    name = r.string()
    count = r.i32()
    expect(
        0 <= count <= _MAX_TYPE_PARAMS,
        r.pos - 4,
        f"type name {name!r} at {at} claims {count} parameters; a MapProperty has two "
        "and nothing seen has more, so the cursor is not on a tag",
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
    #: Version 36/52 only: a BoolProperty keeps its value in the tag, where version 60
    #: keeps it in the flags byte. Normalised into ``flags`` by the reader.
    bool_value: int = 0


def _read_tag_ue5(r: Reader) -> _Tag:
    """Version 60's tag. The terminator is a bare name with no type after it, so the name must
    be checked before the type tree is read -- reading the tree first turns the bytes after
    ``"None"`` into a string length and a parameter count.
    """
    name = r.string()
    if name == _TERMINATOR:
        return _Tag(name=name, type=TypeName(""), size=0, index=0, flags=0)
    tag = _Tag(name=name, type=_read_type_name(r), size=0, index=0, flags=0)
    tag.size = r.i32()
    expect(tag.size >= 0, r.pos - 4, f"property {tag.name!r} declares size {tag.size}")
    # Check the extensions bit before reading the fields it would move, so that the offset in
    # the message is the flags byte and not the end of the array index or the guid.
    at_flags = r.pos
    tag.flags = r.i8()
    expect(
        not tag.flags & TAG_EXTENSIONS,
        at_flags,
        f"property {tag.name!r} sets tag bit 0x04, which is unset on every property of "
        "every save checked and whose payload is therefore unknown",
    )
    if tag.flags & TAG_ARRAY_INDEX:
        tag.index = r.i32()
    if tag.flags & TAG_PROPERTY_GUID:
        r.skip(16)
    return tag


def _read_tag_ue4(r: Reader) -> _Tag:
    """UE4's tag. Same information, different places -- see the module docstring.

    The one field that has no version-60 counterpart is the type-specific tag data, and
    it is read here rather than in the value readers so that everything below this line
    sees a single tag shape.
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
        r.skip(16)  # the struct's guid, zero on every occurrence seen
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
    # No native-serialise bit exists in this layout -- UE4 decided a struct's shape from the
    # struct itself -- so `_NATIVE_STRUCTS` is the whole authority on version 36/52 and an
    # unrecognised struct is read as a property list. Do not synthesise the bit here: a
    # creature spawner's `SpawnData` is a plain property list and would be skipped.
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
    """Reads one object's properties. It carries two versions, and they answer different
    questions.

    ``version`` is the **object's**, off its own entry, and it picks the tag layout (60 is
    UE5's ``FPropertyTag``, 36 and 52 are UE4's) and the one struct that genuinely differs
    between 36 and 52 in the same file, ``InventoryItem``.

    ``ue4_save`` is the **save's**, and it picks the width of ``FVector``, ``FQuat`` and
    ``FBox``, which follow the writer rather than the object -- see ``_vector``.
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
        """Read tags until the ``"None"`` terminator, refusing to run past ``limit``.

        ``limit`` is the end of the enclosing block or struct. A property list that is not
        terminated inside it means a size was wrong somewhere above, and stopping at the limit
        turns that into one loud failure instead of a wander through the next object's bytes.

        The depth guard lives here rather than in ``struct`` because every route back into a
        nested list goes through this method -- a struct, an array element, a map value, an
        ``InventoryItem``'s weapon state. Without it a payload of nothing but nested
        ``StructProperty`` tags dies of ``RecursionError``, which carries no byte offset.
        """
        if self.depth >= _MAX_NESTING:
            raise _TooDeep(
                f"at body offset {self.r.pos}: property lists nested more than "
                f"{_MAX_NESTING} deep; the deepest in any of the 31 readable saves is 4, "
                "so the cursor is not on a property tag"
            )
        self.depth += 1
        try:
            return self._property_list(limit)
        finally:
            self.depth -= 1

    def _property_list(self, limit: int) -> tuple[list[list], list[list]]:
        """The loop itself. Split out only so the depth guard can wrap it."""
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
            # Version 60 keeps the value in the flags byte and writes no payload at all;
            # version 36/52 keeps it in the tag data. Either way the raw byte is what
            # comes out -- 16, 1 or 0 -- because `truthy()` is what reads it.
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
            # `end` here is the property's own declared size, so it can referee the reading.
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
        """Skip to ``end`` and say so. The escape hatch the whole design rests on.

        ``end`` is always a **declared** end -- a property's own size, or the size of the
        container an untagged element sits in -- so skipping forwards to it costs one property
        and no more. Skipping *backwards* means the cursor is already past that end, which
        happens when an untagged element of a container was skipped by the whole container's
        length and the elements after it were read from the wrong place. Left unchecked the
        cursor is dragged back, the container lands on its declared end, the size check is
        satisfied, and a map comes back with fabricated keys and ``None`` values. Hence the
        refusal below: this is the guard for the patch that puts a container inside a
        container, and it turns that into an offset rather than a shorter factory.
        """
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
        """Try each reading in turn; keep the first that consumed the block exactly.

        The one place in this module where something is *guessed*, and the referee is the
        declared length: a reading that lands on the declared end byte-for-byte is kept, one
        that does not is discarded. Used for version-36/52 maps and sets, whose element struct
        types the bytes do not name (see ``_is_unnamed_struct``).

        Ordering matters: pass the narrowest guess first, because a wrong narrow reading
        desynchronises at once while a permissive one can absorb a lot before it fails.
        """
        start = self.r.pos
        mark = len(self.warnings)
        for decode in decoders:
            try:
                value = decode()
            except _TooDeep:
                # Not a wrong guess -- the stack guard. Retrying the next reading from the
                # same bytes would trip it again one frame lower down.
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
        """The enum a Byte/Enum property is typed by, or ``None`` for a plain byte.

        UE4 writes the literal string ``"None"`` there for a byte that is not an enum; UE5
        writes no parameter at all. Both collapse to Python ``None`` so that the same field
        has the same shape whichever version an object was written at.
        """
        inner = tag.type.inner.name
        return inner if inner and inner != _TERMINATOR else None

    def byte_value(self, tag: _Tag):
        """``[enumName, value]`` -- a name when the byte is an enum, else a raw byte.

        Both shapes are real: ``mLastAutoSaveId`` is a plain byte (``[None, 2]``) while
        ``mGamePhaseCosts[].gamePhase`` is an ``EGamePhase`` (``['EGamePhase',
        'EGP_MidGame']``), and readers take ``[-1]`` off whichever they get.
        """
        enum = self.enum_name(tag)
        return [enum, self.r.string() if enum else self.r.i8()]

    # -- structs ----------------------------------------------------------

    def struct(self, struct_type: TypeName, *, native_hint: bool, end: int, exact: bool = False):
        """A struct: raw numbers if its name is in the table, else a property list.

        The NAME decides, not the flags bit. The bit says a struct serialises itself but not
        how, and it is not reliably per-element: the foliage subsystem's ``mSaveData`` is one
        MapProperty with the bit set whose keys are native ``IntVector`` and whose values are
        nested property lists. It is used only as the second opinion that turns an unrecognised
        struct into a skip instead of a misparse.

        ``exact`` says ``end`` is *this* struct's own declared end rather than the end of a
        container it sits in, which is true only of a standalone ``StructProperty``. When it is,
        it settles ``InventoryItem``, whose layout no version in the file implies, and it
        refereees the property-list reading below instead of trusting it: a struct
        this module does not know, whose bytes are not a property list and whose
        self-serialising flag is **0**, then costs one property and a warning rather than the
        save. That combination is not hypothetical and not a base-game shape -- it is what mods
        write. ``FicsItCam``'s ``FICFrameRange`` is two int64s under a cleared flag, and reading
        it as a tagged property list takes a frame number for a string length and walks off the
        end of a 164 MB body.

        The fallback to a skip deliberately does **not** cover the ``_NATIVE_STRUCTS`` branch,
        ``InventoryItem`` included: there the declared size *chooses* between two readings this
        module knows, and only a record that is neither degrades to a skip. A struct this module
        claims to know and then quietly drops would hide exactly the kind of bug the
        ``InventoryItem`` gate was.
        """
        native = _NATIVE_STRUCTS.get(struct_type.name)
        if native is not None:
            if native is _inventory_item and exact:
                # The one native struct whose layout is not implied by any version in the file.
                # The two readings can never both land on the same byte -- the modern one is a
                # reference plus 4, the legacy one a reference plus another reference, which is
                # 8 bytes at its shortest -- so the declared size decides between them outright
                # rather than merely preferring one. Narrowest first, per `attempt`.
                return self.attempt(
                    "an InventoryItem that reads as neither layout",
                    end,
                    lambda: _inventory_item_modern(self),
                    lambda: _inventory_item_legacy(self),
                )
            return native(self)
        if native_hint:
            # Hand back the bytes rather than None, so the caller can see what it got and the
            # enclosing size check still balances.
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
        """An element count, bounded by the bytes its own block has left.

        The tag's declared size is checked *before* the payload is read, but the count lives
        INSIDE the payload, so on a file the game is halfway through rewriting it can be any
        int32 at all. The smallest element of any container is one byte -- a ``BoolProperty``
        element is exactly that -- so a count larger than the bytes left in the block cannot be
        true. Bound it by the block and not by a flat ceiling: a flat one large enough to be
        safe still lets a torn file read tens of megabytes of the following objects before the
        per-property size check notices.
        """
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
        """``i32 count`` then the elements, with no tag of their own.

        The element type comes from the array's own type tree, so the elements carry no
        framing. Structs are the one exception on version 36/52, which writes a struct header
        once for the whole array.
        """
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
            # An array of bytes is bytes, with no per-element enum name to consult.
            return list(r.bytes(count))
        if inner.name == "EnumProperty":
            return [r.string() for _ in range(count)]
        if inner.name == "TextProperty":
            return [self.text(end) for _ in range(count)]
        return self.unknown(f"array of {inner.name!r}", end)

    def struct_array(self, tag: _Tag, count: int, end: int):
        """The elements of a struct array, which the two versions frame differently.

        Version 60 writes the struct's type in the array's type tree and then nothing but the
        elements. Version 36/52 writes a full property tag *inside* the payload -- name,
        ``StructProperty``, the total size of all elements, the struct name and a guid -- and
        only then the elements. That inner tag is where a version-36 array keeps the struct's
        name, so it has to be read rather than skipped.
        """
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
            # One unknown element cannot be skipped -- elements have no size of their own --
            # but the array does have one, so the whole array goes and the object survives.
            return self.unknown(f"array of self-serialising {struct_type.name!r}", end)
        return [self.struct(struct_type, native_hint=native, end=end) for _ in range(count)]

    def set_(self, tag: _Tag, end: int):
        """``i32 removed, i32 count`` then the elements, reported as ``[type, values]``.

        The leading int32 is UE's "keys to remove" list, which a save never has content for.
        It is refused rather than skipped, so that the day it is not, this says so.
        """
        inner = tag.type.inner
        if self.ue4_tags and _is_unnamed_struct(inner):
            # The bare reading is offered separately from the array one because a set does not
            # frame its elements the way an array does: `array` routes a struct element type to
            # `struct_array`, which on version 36/52 expects a full property tag inside the
            # payload, and a set writes its elements end to end with none of that.
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
        """A set whose elements are written back to back with no per-element framing.

        Kept separate from ``_set_body`` rather than parameterised into it because that one
        delegates to ``array``, which brings the version-36/52 struct-array header with it.
        Both are offered to ``attempt`` and the declared length decides between them.
        """
        r = self.r
        removed = r.i32()
        expect(
            removed == 0,
            r.pos - 4,
            f"set {tag.name!r} declares {removed} removed elements; a saved set has no "
            "removal list and every one checked writes 0 here",
        )
        count = self._count(f"set {tag.name!r}", end)
        # The struct name, not "StructProperty": this branch exists because the bytes did
        # not name the struct, so the name that got it to parse is the informative one.
        label = inner.inner.name or inner.name
        return [label, [self.element(inner, end) for _ in range(count)]]

    def _set_body(self, tag: _Tag, inner: TypeName, end: int):
        """Split out from ``set_`` only so ``attempt`` can run it and throw it away."""
        r = self.r
        removed = r.i32()
        expect(
            removed == 0,
            r.pos - 4,
            f"set {tag.name!r} declares {removed} removed elements; a saved set has no "
            "removal list and every one checked writes 0 here",
        )
        values = self.array(
            _Tag(tag.name, TypeName("ArrayProperty", [inner]), 0, 0, tag.flags), end
        )
        return [inner.name, values]

    def map_(self, tag: _Tag, end: int):
        """``i32 removed, i32 count`` then key/value pairs, reported as ``[[k, v], ...]``.

        Keys and values each carry their own type from the map's type tree and neither has a
        tag, so this is the one container where the element readers have to be called with a
        type that came from two levels up.

        A map value is never itself a container: ``mItemsPickedUp`` looks like a map of maps
        and is not -- its value is a struct, and the inner map is a normal tagged property
        inside that struct's property list, which is why ``element`` needs no container branch.
        """
        r = self.r
        expect(
            len(tag.type.params) == 2,
            r.pos,
            f"map {tag.name!r} has {len(tag.type.params)} type parameters, expected a "
            "key type and a value type",
        )
        key_type, value_type = tag.type.params
        if self.ue4_tags and (_is_unnamed_struct(key_type) or _is_unnamed_struct(value_type)):
            # Only the KEY is substituted; see _UNNAMED_KEY_CANDIDATES.
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
        """Split out from ``map_`` only so ``attempt`` can run it and throw it away."""
        r = self.r
        removed = r.i32()
        expect(
            removed == 0,
            r.pos - 4,
            f"map {tag.name!r} declares {removed} removed keys; every map checked writes 0",
        )
        count = self._count(f"map {tag.name!r}", end)
        out = []
        for _ in range(count):
            key = self.element(key_type, end)
            value = self.element(value_type, end)
            out.append([key, value])
        return out

    def element(self, type_name: TypeName, end: int):
        """One untagged value of a known type: a map key or a map value.

        Do not pass the map's flags byte on. It is set when *either* side serialises itself,
        so on ``mSaveData`` -- native ``IntVector`` keys, property-list values -- passing it on
        makes the values skip as opaque bytes. ``_NATIVE_STRUCTS`` is the authority instead,
        and a struct that is native but unlisted fails loudly here rather than being dropped.
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

        Only history type 0xFF (none) occurs, because every ``mBlueprintName`` and sign label
        is a plain string the player typed rather than a localised lookup. Any other history
        type is skipped by size rather than half-decoded into a wrong label.

        The skip goes through ``unknown`` for its refusal to move backwards, which is what
        makes it safe inside an ARRAY of texts: an array element has no size of its own, so a
        foreign history on the first of several eats the rest of the array, and the second
        element then trips that guard with an offset instead of inventing entries.
        """
        r = self.r
        flags = r.i32()
        history = r.i8()
        if history != 0xFF:
            self.unknown(f"FText history type {history}", end)
            return [flags, history]
        has_invariant = r.i32()
        return [flags, history, has_invariant, r.string() if has_invariant else None]


#: Types whose payload is one fixed-width value, read the same way everywhere -- as a tagged
#: property, an array element, a map key -- because none of them has any framing beyond its own
#: width. The 16-bit variants are absent because none has ever appeared, and an unrecognised
#: type is skipped with a warning rather than read by a reader nobody has run against real
#: bytes. ``Int8Property`` yields raw ``bytes`` rather than an int; nothing in the projection
#: reads one.
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
    """Decode one object's property block.

    ``body`` and ``slot`` are what ``read_body`` produced; ``actor`` comes from the object's
    header, because the payload's opening reference lists exist only on actors and nothing in
    the payload itself says which kind this is.

    ``save_version`` is the *file's*, not the object's, and it exists for one reason:
    ``FVector``/``FQuat``/``FBox`` are float32 on a save the UE4 game wrote and float64 on one
    the UE5 game wrote, whatever version the individual object is stamped with. It defaults to
    the modern layout.
    """
    r = Reader(body, slot.offset)
    end = slot.end
    out = ParsedObject(version=slot.version)

    if actor:
        out.parent_reference = read_reference(r)
        out.child_references = read_references(r, end)
    if slot.version >= FIRST_UE5_OBJECT_VERSION:
        # The object-reference migration flag: one byte, 0 on every object seen, and the
        # reason a version-60 payload is a byte longer than a version-52 one holding the
        # same properties.
        r.i8()

    decoder = _Decoder(r, slot.version, out.warnings, save_version=save_version)
    out.properties, out.property_types = decoder.property_list(end)
    out.extra_offset = r.pos
    out.extra_length = end - r.pos

    # The only check that looks at the payload as a WHOLE rather than one property at a time.
    # Per-property size checks catch a wrong width, but not a property list that terminates
    # early: the leftover is a legitimate 4 or 8 bytes on an ordinary object and megabytes on a
    # buildable carrying class-specific data, so without this an object whose property NAME was
    # corrupted into the "None" terminator reads as a few properties with the rest filed
    # silently as trailing data -- a save that parses cleanly and reports a different factory.
    #
    # What can be bounded without inventing structure is `PLAIN_TRAILER`: nothing has a
    # trailer shorter than 4, so a list terminating ON the payload's end byte is wrong whatever
    # the object is; and a component's is 4 or 8 and never more, so for components the check is
    # exact. An ACTOR's is left unchecked, because bounding it needs the eight class names that
    # legitimately carry more and a ninth arriving in a patch would then refuse the save.
    expect(
        out.extra_length >= PLAIN_TRAILER[0],
        r.pos,
        f"the property list of {'an actor' if actor else 'a component'} ended "
        f"{out.extra_length} bytes before its {slot.length}-byte payload does, and every "
        f"one of 1,243,288 objects leaves at least {PLAIN_TRAILER[0]}. A property name was "
        "read as the list terminator, so the properties after it are missing",
    )
    if not actor:
        expect(
            out.extra_length in PLAIN_TRAILER,
            r.pos,
            f"a component's property list left {out.extra_length} bytes of its "
            f"{slot.length}-byte payload unread; every one of 567,856 components in the 31 "
            f"readable saves leaves exactly {' or '.join(map(str, PLAIN_TRAILER))}, so the "
            "list terminated early and the properties after that point are missing",
        )
    return out
