"""Material-instance parameters, and the materials a mesh's sections use."""

from __future__ import annotations

import struct

from satisfactory_mcp.core.gameassets.packages import PackageView, property_tags

__all__ = [
    "material_parameters",
    "mesh_materials",
]


def mesh_materials(view: PackageView, export: dict) -> list[str | None]:
    """``StaticMaterials`` in slot order: the material each section's index names."""
    payload = view.props(export["slot"]).get("StaticMaterials", b"")
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos, out = 4, []
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        path = None
        for name, kind, raw, _value in tags:
            if name == "MaterialInterface" and kind == "ObjectProperty":
                path = view.import_path(raw)
        out.append(path)
    return out


def _parameter_array(view: PackageView, payload: bytes) -> list[tuple[str, str, bytes]]:
    count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
    pos, out = 4, []
    for _ in range(count):
        tags, pos = property_tags(payload, view.pkg.names, pos)
        name, kind, value = None, None, b""
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


def material_parameters(view: PackageView) -> dict:
    """A material instance's own scalar, vector and texture parameters, and its parent."""
    props = view.props(0)
    found: dict = {"scalar": {}, "vector": {}, "texture": {}, "parent": None}
    for name, _kind, raw in _parameter_array(view, props.get("ScalarParameterValues", b"")):
        if len(raw) == 4:
            found["scalar"][name] = struct.unpack("<f", raw)[0]
    for name, _kind, raw in _parameter_array(view, props.get("VectorParameterValues", b"")):
        if len(raw) == 16:
            found["vector"][name] = tuple(float(v) for v in struct.unpack("<4f", raw))
    for name, kind, raw in _parameter_array(view, props.get("TextureParameterValues", b"")):
        if kind == "ObjectProperty":
            found["texture"][name] = view.import_path(raw)
    if "Parent" in props:
        found["parent"] = view.import_path(props["Parent"])
    return found
