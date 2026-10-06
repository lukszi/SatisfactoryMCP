"""The static resource-node table, loaded once per version of its file."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .... import config
from .. import geo

__all__ = ["EXTRACTOR_FOR_KIND", "KINDS", "PURITIES", "NodeTable", "load_nodes"]

#: Node purities and node kinds, as the table spells them.
PURITIES = ("pure", "normal", "impure")
KINDS = ("node", "well_sat", "geyser")


#: Extractor class -> what it can tap. A well satellite needs a Well Extractor AND
#: a Pressurizer on its parent core, so it is not interchangeable with a plain node.
EXTRACTOR_FOR_KIND = {
    "node": ("Build_MinerMk1_C", "Build_MinerMk2_C", "Build_MinerMk3_C", "Build_OilPump_C"),
    "well_sat": ("Build_FrackingExtractor_C",),
    "geyser": (),
}

#: What a node needs BESIDES an extractor, and cannot work without. The Pressurizer
#: produces nothing itself, so it never appears in EXTRACTOR_FOR_KIND -- but with no
#: Pressurizer on the core every satellite of that well yields exactly zero.
EXTRA_FOR_KIND = {"well_sat": ("Build_FrackingSmasher_C",)}

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

    def filter(
        self,
        resource: str | None = None,
        kind: str | None = None,
        purity: str | None = None,
        direction: str | None = None,
        origin: tuple[float, float] | None = None,
        half_angle: float = 60.0,
        center: tuple[float, float] | None = None,
        radius_m: float | None = None,
    ) -> list[dict]:
        out = self.nodes
        if resource:
            out = [n for n in out if n["resource"] == resource]
        if kind:
            out = [n for n in out if n["kind"] == kind]
        if purity:
            out = [n for n in out if n["purity"] == purity]
        if direction:
            out = [
                n for n in out if geo.in_direction(n["x"], n["y"], direction, origin, half_angle)
            ]
        if center is not None and radius_m is not None:
            out = [n for n in out if geo.distance_m((n["x"], n["y"]), center) <= radius_m]
        return list(out)


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
