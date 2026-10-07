"""Material-instance parameters, and the materials a mesh's sections use."""

from __future__ import annotations

import struct
from typing import TypeAlias

from satisfactory_mcp.core.gameassets.packages import PackageView, property_tags

__all__ = [
    "Vector4",
    "material_parameters",
    "material_parent",
    "mesh_materials",
    "scalar_parameters",
    "texture_parameters",
    "vector_parameters",
]

#: A ``VectorParameterValues`` entry: a linear colour, RGBA.
Vector4: TypeAlias = tuple[float, float, float, float]


def mesh_materials(view: PackageView, export: dict[str, int]) -> list[str | None]:
    """``StaticMaterials`` in slot order: the material each section's index names."""
    payload = view.props(export["slot"]).get("StaticMaterials", b"")
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos = 4
    out: list[str | None] = []
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        path = None
        for name, kind, raw, _value in tags:
            if name == "MaterialInterface" and kind == "ObjectProperty":
                path = view.import_path(raw)
        out.append(path)
    return out


def _parameter_array(view: PackageView, array: str) -> list[tuple[str, str | None, bytes]]:
    """``(name, value kind, value payload)`` of every entry of one ``*ParameterValues``."""
    payload = view.props(0).get(array, b"")
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos = 4
    out: list[tuple[str, str | None, bytes]] = []
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        name: str | None = None
        kind: str | None = None
        value = b""
        for tag, tag_kind, raw, _v in tags:
            if tag == "ParameterInfo" and tag_kind == "StructProperty":
                inner, _end = property_tags(raw, view.pkg.names, 0)
                for key, inner_kind, inner_raw, _iv in inner:
                    if key == "Name" and inner_kind == "NameProperty":
                        name = view.read_fname(inner_raw)
            elif tag == "ParameterValue":
                kind, value = tag_kind, raw
        if name:
            out.append((name, kind, value))
    return out


def scalar_parameters(view: PackageView) -> dict[str, float]:
    """A material instance's own scalar parameters, by name."""
    found: dict[str, float] = {}
    for name, _kind, raw in _parameter_array(view, "ScalarParameterValues"):
        if len(raw) == 4:
            found[name] = struct.unpack("<f", raw)[0]
    return found


def vector_parameters(view: PackageView) -> dict[str, Vector4]:
    """A material instance's own vector parameters, by name."""
    found: dict[str, Vector4] = {}
    for name, _kind, raw in _parameter_array(view, "VectorParameterValues"):
        if len(raw) == 16:
            r, g, b, a = struct.unpack("<4f", raw)
            found[name] = (float(r), float(g), float(b), float(a))
    return found


def texture_parameters(view: PackageView) -> dict[str, str | None]:
    """A material instance's own texture parameters, by name; ``None`` where unresolved."""
    found: dict[str, str | None] = {}
    for name, kind, raw in _parameter_array(view, "TextureParameterValues"):
        if kind == "ObjectProperty":
            found[name] = view.import_path(raw)
    return found


def material_parent(view: PackageView) -> str | None:
    """The ``Parent`` a material instance names, or ``None`` for a base material."""
    raw = view.props(0).get("Parent")
    return view.import_path(raw) if raw else None


def material_parameters(view: PackageView) -> dict:
    """A material instance's own scalar, vector and texture parameters, and its parent."""
    return {
        "scalar": scalar_parameters(view),
        "vector": vector_parameters(view),
        "texture": texture_parameters(view),
        "parent": material_parent(view),
    }
