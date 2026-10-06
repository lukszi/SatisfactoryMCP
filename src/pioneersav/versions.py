"""Where the format changed, as named thresholds rather than magic numbers.

Six ``saveVersion``s are one format with fields added, so every layer reads one walk with
version-gated fields and these constants are the gates. **A threshold is where a change was
observed, not where the game made it**: no save exists between saveVersion 36 and 51, so
every difference in that gap arrives at one constant and none of them can be attributed to a
particular patch. "Header ends at" below is the offset of ``header.PACKAGE_FILE_TAG``, which
is the referee for the header layout -- a candidate field list is right when the walk lands
exactly there.

| saveHeaderType | saveVersion | header ends at | body shape |
|---|---|---|---|
| 8 | 25 | 146 | no level list |
| 9 | 28 | 159 | no level list |
| 10 | 30, 36 | 186 | named levels, int32 sizes |
| 14 | 52 | 283 or 297 | grid table, int64 sizes |
| 14 | 60 | 450, 453 or 457 | + archive version header |
"""

from __future__ import annotations

__all__ = [
    "FIRST_LEVEL_LIST",
    "FIRST_MODERN_BODY",
    "FIRST_UE5_OBJECT_VERSION",
    "HEADER_TYPE_SAVE_IDENTIFIER",
    "HEADER_TYPE_SAVE_NAME",
    "KNOWN_HEADER_TYPES",
]

#: The saveVersion at which the body gained a **list of levels**. Below it there is no level
#: list at all: one flat run of object headers, each naming its own level in ``root_object``.
FIRST_LEVEL_LIST = 30

#: The saveVersion at and above which the body has all of the following. They change together
#: because no save exists between 36 and 51 to separate them:
#:
#: * the body's own size and every level's two block sizes widen from **int32 to int64**;
#: * a **world-partition grid table** appears before the level list;
#: * an object **header** gains its ``EObjectFlags`` word;
#: * an object **entry** gains a leading version int32 and a flag int32, so below this the
#:   entry is a bare size and the object's version is not in the bytes at all -- it has to be
#:   taken from the save;
#: * a level **trailer** gains its leading version int32;
#: * the persistent level's destroyed-actor list becomes **grouped by partition cell**;
#: * ``FVector``, ``FQuat`` and ``FBox`` are written as **float32** below this and float64 at
#:   and above it.
#:
#: ``FInventoryItem`` is gated on this constant only where it has to be guessed. Which layout
#: a record uses does NOT follow from any version in the file -- see
#: ``properties.structs.read_inventory_item`` -- so where the record's declared size is known it
#: is refereed by that size instead.
FIRST_MODERN_BODY = 52

#: The OBJECT version, off each object's own entry, at and above which an object has:
#:
#: * UE5's property tag layout, with a type-name tree and a flags byte;
#: * one migration byte before its property list;
#: * a trailing int32 after its payload in the object block.
FIRST_UE5_OBJECT_VERSION = 60


#: saveHeaderType at which the header started with the save's own name.
HEADER_TYPE_SAVE_NAME = 14

#: saveHeaderType at which ``save_identifier`` appeared.
HEADER_TYPE_SAVE_IDENTIFIER = 10

#: Every saveHeaderType whose field list has been derived from real bytes. **Not a gate**: an
#: unknown type is still walked, because ``PACKAGE_FILE_TAG`` is the verdict and a patch may
#: bump the type without moving a field -- which is what happened between 8 and 9, where
#: nothing in the header moved at all. It is here so a refusal can say what IS known.
KNOWN_HEADER_TYPES = (8, 9, 10, 14)
