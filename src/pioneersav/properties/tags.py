"""The two property tag layouts, both read into one ``PropertyTag`` with a ``TypeName`` tree."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import expect
from ..reader import Reader

__all__ = [
    "TAG_ARRAY_INDEX",
    "TAG_BOOL_TRUE",
    "TAG_EXTENSIONS",
    "TAG_NATIVE_SERIALIZE",
    "TAG_PROPERTY_GUID",
    "TERMINATOR",
    "UNNAMED_KEY_CANDIDATES",
    "UNNAMED_SET_CANDIDATES",
    "PropertyTag",
    "TypeName",
    "is_unnamed_struct",
    "read_tag_ue4",
    "read_tag_ue5",
    "read_type_name",
    "unnamed_element_candidates",
]

#: An int32 array index follows the flags byte, written only when the index is nonzero.
TAG_ARRAY_INDEX = 0x01
#: A 16-byte property guid follows.
TAG_PROPERTY_GUID = 0x02
#: Tag extensions follow, in a layout nothing here reads; refused rather than ignored.
TAG_EXTENSIONS = 0x04
#: The payload is the type's own binary form. It says *that* a struct serialises itself, not
#: how: ``structs.NATIVE_STRUCTS`` is the only authority on how.
TAG_NATIVE_SERIALIZE = 0x08
#: A version-60 BoolProperty's value; version 36/52 writes 1 in its tag data instead.
TAG_BOOL_TRUE = 0x10

#: The name of the tag that ends a property list: a real tag name, so the list is walked.
TERMINATOR = "None"

#: Guard on the type-name tree's branching factor. A MapProperty has two parameters; 16 is
#: loose enough to survive a patch and tight enough that a misaligned cursor reading a float as
#: a count fails here instead of allocating.
_MAX_TYPE_PARAMS = 16

#: Struct names to try, narrowest first, for an unnamed version-36/52 map KEY; an unnamed value
#: is always a property list. Each is the type saveVersion 60 names for the same field; the
#: empty name, a property list, stays last.
UNNAMED_KEY_CANDIDATES = ("IntVector", "")

#: The same for an unnamed SET element. A 16-byte ``Guid`` cannot land as a 24-byte vector.
UNNAMED_SET_CANDIDATES = ("Guid", "Vector", "")


@dataclass(slots=True)
class TypeName:
    """A property's type as a tree: ``ArrayProperty(StructProperty(InventoryStack(...)))``.

    Version 60 writes the tree; ``read_tag_ue4`` rebuilds it from UE4's fixed tag data, so
    the value readers are version-agnostic.
    """

    name: str
    params: list[TypeName] = field(default_factory=list["TypeName"])

    @property
    def inner(self) -> TypeName:
        """First parameter, or a nameless one, which the readers skip as an unknown type."""
        return self.params[0] if self.params else TypeName("")

    def flat(self) -> list[str | int]:
        """The tree as ``[name, paramCount, ...]`` in prefix order, which reads back into it."""
        if not self.params:
            return [self.name, 0]
        return [self.name, len(self.params), *(x for p in self.params for x in p.flat())]


@dataclass(slots=True)
class PropertyTag:
    name: str
    type: TypeName
    size: int
    index: int
    flags: int
    #: Version 36/52 only: a BoolProperty's value, which version 60 keeps in the flags byte.
    bool_value: int = 0


def is_unnamed_struct(type_name: TypeName) -> bool:
    """A struct the bytes never name: a version-36/52 map or set element, whose tag data stops
    at ``"StructProperty"`` because UE4 took the struct's name from reflection."""
    return type_name.name == "StructProperty" and not type_name.params


def unnamed_element_candidates(
    element_type: TypeName, names: tuple[str, ...]
) -> tuple[TypeName, ...]:
    """Element types to try, most specific first. A named element comes back unchanged."""
    if not is_unnamed_struct(element_type):
        return (element_type,)
    return tuple(TypeName("StructProperty", [TypeName(n)]) for n in names)


def read_type_name(r: Reader, depth: int = 0) -> TypeName:
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
    return TypeName(name, [read_type_name(r, depth + 1) for _ in range(count)])


def read_tag_ue5(r: Reader) -> PropertyTag:
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
    if name == TERMINATOR:
        return PropertyTag(name=name, type=TypeName(""), size=0, index=0, flags=0)
    tag = PropertyTag(name=name, type=read_type_name(r), size=0, index=0, flags=0)
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


def read_tag_ue4(r: Reader) -> PropertyTag:
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
    if name == TERMINATOR:
        return PropertyTag(name=name, type=TypeName(""), size=0, index=0, flags=0)
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
    return PropertyTag(
        name=name,
        type=TypeName(type_name, params),
        size=size,
        index=index,
        flags=0,
        bool_value=bool_value,
    )
