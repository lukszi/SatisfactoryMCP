"""The static resource-node table, loaded once per version of its file."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .... import config
from .. import geo

__all__ = [
    "EXTRACTOR_FOR_KIND",
    "GEYSER_CONSUMER",
    "KINDS",
    "PURITIES",
    "SUPPORT_BUILDINGS_FOR_KIND",
    "NodeTable",
    "load_nodes",
]

#: Node purities and node kinds, as the table spells them.
PURITIES = ("pure", "normal", "impure")
KINDS = ("node", "well_sat", "geyser")

#: Node kind -> the extractors that can tap it. A well satellite needs a Well Extractor
#: AND a Pressurizer on its parent core, so it is not interchangeable with a plain node.
EXTRACTOR_FOR_KIND = {
    "node": ("Build_MinerMk1_C", "Build_MinerMk2_C", "Build_MinerMk3_C", "Build_OilPump_C"),
    "well_sat": ("Build_FrackingExtractor_C",),
    "geyser": (),
}

#: What a node needs BESIDES an extractor: no Pressurizer on the core, every satellite of
#: that well yields exactly zero.
SUPPORT_BUILDINGS_FOR_KIND = {"well_sat": ("Build_FrackingSmasher_C",)}

#: A geyser is not extracted at all; it is a placement target for this generator.
GEYSER_CONSUMER = "Build_GeneratorGeoThermal_C"


@dataclass
class NodeTable:
    nodes: list[dict]
    meta: dict

    def __len__(self) -> int:
        return len(self.nodes)

    def by_resource(self, resource: str) -> list[dict]:
        return [n for n in self.nodes if n["resource"] == resource]

    def by_instance(self) -> dict[str, dict]:
        return {n["instance"]: n for n in self.nodes}

    def within(self, center: tuple[float, float], radius_m: float) -> list[dict]:
        """The nodes within ``radius_m`` of ``center`` (cm), in table order."""
        return [n for n in self.nodes if geo.distance_m((n["x"], n["y"]), center) <= radius_m]


#: The loaded table, keyed by the file and its mtime: ``tools/gen_resource_nodes.py``
#: regenerates the artifact on the reader's own machine, and keying on the mtime is what
#: picks the new one up without a restart. One entry only -- the previous table is dead the
#: moment a newer one is read.
_TABLE: dict[tuple[str, int], NodeTable] = {}


def load_nodes() -> NodeTable:
    path = config.data_dir() / "resource_nodes.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing -- run: uv run python tools/gen_resource_nodes.py")
    key = (str(path), path.stat().st_mtime_ns)
    hit = _TABLE.get(key)
    if hit is not None:
        return hit
    payload = json.loads(path.read_text(encoding="utf-8"))
    table = NodeTable(nodes=payload["nodes"], meta=payload.get("_meta", {}))
    _TABLE.clear()
    _TABLE[key] = table
    return table
