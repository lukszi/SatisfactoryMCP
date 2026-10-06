"""Cooked (zen, IoStore-resident) packages: their headers, their exports, their properties.

The container hands over bytes; this turns them into exports with names, classes, outers and
tagged properties, and composes the transform chain that says where a placed actor is. Nothing
here opens a file or decides what is interesting: which packages to read is the caller's
business.
"""

from __future__ import annotations

from .classfacts import MOUNT_ROOTS, AssetIndex, ClassFacts
from .properties import property_tags, read_float, read_int32, read_triple, read_vector_array
from .transforms import (
    compose,
    local_transform,
    quat_mul,
    quat_rotate,
    root_component,
    rotator_to_quat,
    world_transform,
)
from .view import LEVEL_CLASS, PackageView, class_name_of
from .zen import BULK_ENTRY_BYTES, Package, ScriptObjects, apply_fname_number, bulk_data_entries

__all__ = [
    "BULK_ENTRY_BYTES",
    "LEVEL_CLASS",
    "MOUNT_ROOTS",
    "AssetIndex",
    "ClassFacts",
    "Package",
    "PackageView",
    "ScriptObjects",
    "apply_fname_number",
    "bulk_data_entries",
    "class_name_of",
    "compose",
    "local_transform",
    "property_tags",
    "quat_mul",
    "quat_rotate",
    "read_float",
    "read_int32",
    "read_triple",
    "read_vector_array",
    "root_component",
    "rotator_to_quat",
    "world_transform",
]
