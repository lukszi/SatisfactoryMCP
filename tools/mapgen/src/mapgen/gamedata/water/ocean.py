"""The ocean material's absorption, read from its own parameter defaults in the install.

``MM_OceanMaster`` is a base material, so it has no instance parameters: its defaults live
in the cooked material's cached expression data, after the export's tagged properties.
docs/map/water.md section 33, "The sea's physics row".
"""

from __future__ import annotations

import struct
from typing import NamedTuple

from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    PackageView,
    PropertyTag,
    ScriptObjects,
    tagged_properties,
)

__all__ = [
    "ABSORPTION",
    "MURKINESS",
    "OCEAN_MATERIAL",
    "MaterialDefaults",
    "OceanAbsorption",
    "material_defaults",
    "read_ocean_absorption",
]

OCEAN_MATERIAL = "/Game/Developers/gabrielestigliano/Ocean/MM_OceanMaster"
#: The two parameters whose product is the ocean's absorption per metre.
ABSORPTION, MURKINESS = "Ocean Absorption Color", "Ocean Murkyness"

#: ``RuntimeEntries`` is indexed by parameter type: scalars first, then vectors.
_SCALAR, _VECTOR = 0, 1


class MaterialDefaults(NamedTuple):
    """A base material's scalar and vector parameter defaults, by name."""

    scalar: dict[str, float]
    vector: dict[str, tuple[float, float, float, float]]


class OceanAbsorption(NamedTuple):
    """The ocean's absorption per metre, and the two parameters it is the product of."""

    per_m: tuple[float, float, float]
    colour: tuple[float, float, float]
    murkiness: float


def _cached_stream(view: PackageView) -> list[PropertyTag]:
    """The tags of the material's cached expression data; empty when it saved none.

    After the export's tag stream come its GUID flag (and GUID) and then a flag saying the
    cached data follows, as its own tag stream.
    """
    names = list(view.pkg.names)
    body = view.pkg.body(view.exports[0])
    _tags, pos = tagged_properties(body, names, 1)
    if pos + 8 > len(body):
        return []
    has_guid = struct.unpack_from("<i", body, pos)[0]
    pos += 4 + (16 if has_guid else 0)
    saved = struct.unpack_from("<i", body, pos)[0]
    return tagged_properties(body, names, pos + 4)[0] if saved == 1 else []


def _parameter_names(entry: bytes, names: list[str]) -> list[str]:
    """One ``RuntimeEntries`` element's parameter names, in the order its values are kept."""
    found: list[str] = []
    for tag in tagged_properties(entry, names, 0)[0]:
        if tag.name != "ParameterInfoSet":
            continue
        count = struct.unpack_from("<i", tag.payload, 4)[0]
        pos = 8
        for _ in range(count):
            info, pos = tagged_properties(tag.payload, names, pos)
            name = next(t.payload for t in info if t.name == "Name")
            found.append(names[struct.unpack_from("<I", name)[0]])
    return found


def material_defaults(view: PackageView) -> MaterialDefaults:
    """A base material's scalar and vector parameter defaults; empty for a material instance
    or a cook without cached expression data."""
    names = list(view.pkg.names)
    stream = _cached_stream(view)
    entries = {t.array_index: t.payload for t in stream if t.name == "RuntimeEntries"}
    values = {t.name: t.payload for t in stream if t.name in ("ScalarValues", "VectorValues")}
    scalar: dict[str, float] = {}
    vector: dict[str, tuple[float, float, float, float]] = {}
    if _SCALAR in entries and "ScalarValues" in values:
        raw = values["ScalarValues"]
        keys = _parameter_names(entries[_SCALAR], names)
        floats = struct.unpack_from(f"<{len(keys)}f", raw, 4)
        scalar = {k: float(v) for k, v in zip(keys, floats, strict=True)}
    if _VECTOR in entries and "VectorValues" in values:
        raw = values["VectorValues"]
        for i, key in enumerate(_parameter_names(entries[_VECTOR], names)):
            r, g, b, a = struct.unpack_from("<4f", raw, 4 + 16 * i)
            vector[key] = (float(r), float(g), float(b), float(a))
    return MaterialDefaults(scalar, vector)


def read_ocean_absorption(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex
) -> OceanAbsorption:
    """``Ocean Absorption Color`` times ``Ocean Murkyness`` of the ocean material.

    Raises ``KeyError`` when the material or either parameter is not in the install.
    """
    path = index.path_for(OCEAN_MATERIAL)
    if path is None:
        raise KeyError(OCEAN_MATERIAL)
    defaults = material_defaults(PackageView(store.read_path(path), scripts))
    colour = defaults.vector[ABSORPTION][:3]
    murkiness = defaults.scalar[MURKINESS]
    per_m = (colour[0] * murkiness, colour[1] * murkiness, colour[2] * murkiness)
    return OceanAbsorption(per_m, colour, murkiness)
