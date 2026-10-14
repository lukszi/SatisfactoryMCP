"""The river material's absorption, read from the install.

``MI_SLW_River_Base_01`` is an instance of ``MM_SLW_River_01``: a parameter the instance does
not set takes its parent's, and the base material keeps its defaults in its cached expression
data (``material_defaults``). docs/map/water.md section 33, "The river's physics row".
"""

from __future__ import annotations

from typing import NamedTuple

from mapgen.gamedata.materials import material_parameters
from mapgen.gamedata.water.ocean import material_defaults
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, PackageView, ScriptObjects

__all__ = [
    "RIVER_ABSORPTION",
    "RIVER_MATERIAL",
    "RIVER_MURKINESS",
    "RiverAbsorption",
    "read_river_absorption",
]

RIVER_MATERIAL = "/Game/FactoryGame/Prototype/Art/WaterOverhaul/River/MI_SLW_River_Base_01"
#: The two parameters whose product is the river's absorption per metre.
RIVER_ABSORPTION, RIVER_MURKINESS = "WaterAbsorptionColour", "WaterMurkiness"

#: An instance chain longer than this is not followed.
_MAX_PARENTS = 4


class RiverAbsorption(NamedTuple):
    """The river's absorption per metre and the two parameters it is the product of."""

    per_m: tuple[float, float, float]
    colour: tuple[float, float, float]
    murkiness: float


def read_river_absorption(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex
) -> RiverAbsorption:
    """``WaterAbsorptionColour`` times ``WaterMurkiness`` of the river material, each from the
    nearest material along its parent chain that sets it.

    Raises ``KeyError`` when a material in the chain or either parameter is not found.
    """
    vectors: dict[str, tuple[float, float, float, float]] = {}
    scalars: dict[str, float] = {}
    asset: str | None = RIVER_MATERIAL
    for _ in range(_MAX_PARENTS):
        if asset is None:
            break
        path = index.path_for(asset)
        if path is None:
            raise KeyError(asset)
        view = PackageView(store.read_path(path), scripts)
        own = material_parameters(view)
        asset = own["parent"]
        if asset is None:
            base = material_defaults(view)
            vectors, scalars = {**base.vector, **vectors}, {**base.scalar, **scalars}
        else:
            vectors, scalars = {**own["vector"], **vectors}, {**own["scalar"], **scalars}
    colour = vectors[RIVER_ABSORPTION][:3]
    murkiness = scalars[RIVER_MURKINESS]
    per_m = (colour[0] * murkiness, colour[1] * murkiness, colour[2] * murkiness)
    return RiverAbsorption(per_m, colour, murkiness)
