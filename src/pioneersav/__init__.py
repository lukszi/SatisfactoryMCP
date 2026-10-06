"""A parser for Satisfactory .sav files, covering what this project reads.

The scope is the parts the projection uses, which is three entry points' worth:
``read_info`` for the header, ``read_body`` for the object walk, ``read_full_save`` for both
plus decoded properties. Anything it does not understand is skipped by its declared length
rather than guessed at, so an unknown property costs that property and not the save.
"""

from .chunks import CHUNK_TAG, OLD_CHUNK_TAG, decompress_body
from .errors import ParseError
from .header import (
    PACKAGE_FILE_TAG,
    SaveInfo,
    body_hash,
    check_body_hash,
    read_info,
    read_info_bytes,
)
from .lightweight import LIGHTWEIGHT_SUBSYSTEM, read_lightweight
from .objects import (
    ActorHeader,
    ComponentHeader,
    Level,
    ObjectSlice,
    SaveBody,
    read_body,
)
from .properties import ParsedObject, TypeName, read_object
from .reader import Reader
from .references import ObjectReference
from .save import ParsedLevel, ParsedSave, read_full_save, read_full_save_bytes
from .trailers import TRAILER_READERS, read_trailer
from .values import Properties, PropertyTypes, SaveValue
from .versions import FIRST_LEVEL_LIST, FIRST_MODERN_BODY, KNOWN_HEADER_TYPES

__all__ = [
    "CHUNK_TAG",
    "FIRST_LEVEL_LIST",
    "FIRST_MODERN_BODY",
    "KNOWN_HEADER_TYPES",
    "LIGHTWEIGHT_SUBSYSTEM",
    "OLD_CHUNK_TAG",
    "PACKAGE_FILE_TAG",
    "TRAILER_READERS",
    "ActorHeader",
    "ComponentHeader",
    "Level",
    "ObjectReference",
    "ObjectSlice",
    "ParseError",
    "ParsedLevel",
    "ParsedObject",
    "ParsedSave",
    "Properties",
    "PropertyTypes",
    "Reader",
    "SaveBody",
    "SaveInfo",
    "SaveValue",
    "TypeName",
    "body_hash",
    "check_body_hash",
    "decompress_body",
    "read_body",
    "read_full_save",
    "read_full_save_bytes",
    "read_info",
    "read_info_bytes",
    "read_lightweight",
    "read_object",
    "read_trailer",
]
