"""Cooked (zen, IoStore-resident) packages: their headers, their exports, their properties.

The container hands over bytes; this turns them into exports with names, classes, outers and
tagged properties, and composes the transform chain that says where a placed actor is. Nothing
here opens a file or decides what is interesting: which packages to read is the caller's
business.
"""

from __future__ import annotations

from .classfacts import MOUNT_ROOTS, AssetIndex, ClassFacts
from .properties import (
    PropertyTag,
    RelativeTransform,
    Vec3,
    property_tags,
    read_float,
    read_int32,
    read_triple,
    read_vector_array,
    tagged_properties,
)
from .transforms import (
    Quat,
    Transform,
    compose,
    local_transform,
    quat_mul,
    quat_rotate,
    root_component,
    rotator_to_quat,
    world_transform,
)
from .view import LEVEL_CLASS, PackageView, class_name_of
from .zen import (
    BULK_ENTRY_BYTES,
    BulkEntry,
    Package,
    ScriptObjects,
    ZenExport,
    apply_fname_number,
    bulk_data_entries,
)

__all__ = [
    "BULK_ENTRY_BYTES",
    "LEVEL_CLASS",
    "MOUNT_ROOTS",
    "AssetIndex",
    "BulkEntry",
    "ClassFacts",
    "Package",
    "PackageView",
    "PropertyTag",
    "Quat",
    "RelativeTransform",
    "ScriptObjects",
    "Transform",
    "Vec3",
    "ZenExport",
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
    "tagged_properties",
    "world_transform",
]
