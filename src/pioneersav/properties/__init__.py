"""Unreal's tagged property serialiser: the inside of one object's property block.

``read_object`` turns one object's payload slice into the ``[[name, value], ...]`` pairs
``extract_save.props()`` reads. Every property declares its size, so an unknown type costs that
property alone, and a value that does not end exactly there raises with its offset. Both tag
layouts are normalised into one ``TypeName`` tree before any value is read, so there is one
value reader per type. Values keep the shapes the projection already reads, quirks included,
because changing one is a silent change downstream.

Layouts, value shapes and the evidence for both: docs/savparse-notes.md, ``### Properties``.
"""

from .payload import PLAIN_TRAILER, ParsedObject, read_object
from .tags import (
    TAG_ARRAY_INDEX,
    TAG_BOOL_TRUE,
    TAG_NATIVE_SERIALIZE,
    TAG_PROPERTY_GUID,
    TypeName,
)

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
