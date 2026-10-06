"""How high the ground is: measured where the terrain field exists, sampled everywhere else.

Neither Docs.json nor the save carries terrain, so without a field the answer is a
POPULATION with its own count and spread, never an interpolated surface -- 608 resource
nodes (which rest ON the ground, so their Z *is* terrain, and which need no save at all),
8,347 foundation and wall pieces from ``FGLightweightBuildableSubsystem``, and 570
production buildings from their actor transforms. Nodes and built structures are separate
populations because a foundation is wherever the player put it -- level across a slope, on
stilts over a cliff -- so its Z is **built elevation, not ground**, and where the two
disagree that disagreement is the fill depth. ``spatial.heightfield`` adds a fourth kind of
evidence on a machine whose owner ran the generator: it is reported beside the populations,
carries the accuracy measured for the layer that answered, and is never averaged in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.saveio import rows as saverows
from . import geo, heightfield

__all__ = ["Elevation", "Sample", "probe", "sample_points"]

#: Terrain-truth sources. A node rests on the ground; anything built does not have to.
GROUND_SOURCES = frozenset({"node"})

#: Ground samples needed before a fill depth is quoted at all. Three is not a statistical
#: threshold, it is a refusal: one node is a point, and a point is not a ground level.
MIN_GROUND_SAMPLES = 3


@dataclass
class Sample:
    source: str
    x: float
    y: float
    z: float
    distance_m: float = 0.0


@dataclass
class Elevation:
    """Known elevations near a point, kept as populations rather than one number.

    ``terrain`` stands apart from ``samples``: it is one texel read at exactly this
    coordinate, not a thing somebody's save says is standing somewhere near it, so folding
    it in would put a 0.2 m reading into a median with points 40 m away. It is ``None``
    where this machine has no field, which is most of them, and where the field has no data
    at this coordinate.
    """

    x: float
    y: float
    radius_m: float
    samples: list[Sample] = field(default_factory=list)
    terrain: heightfield.Reading | None = None

    @property
    def terrain_m(self) -> float | None:
        """The field's own answer in metres, or ``None`` if it has none here."""
        return None if self.terrain is None else self.terrain.z_m

    def heights_m(self, *sources: str) -> list[float]:
        keep = set(sources) if sources else None
        return sorted(s.z / 100.0 for s in self.samples if keep is None or s.source in keep)

    @property
    def ground_m(self) -> list[float]:
        """Metres, from sources that genuinely rest on terrain."""
        return self.heights_m(*GROUND_SOURCES)

    @property
    def built_m(self) -> list[float]:
        return sorted(s.z / 100.0 for s in self.samples if s.source not in GROUND_SOURCES)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.samples:
            out[s.source] = out.get(s.source, 0) + 1
        return out

    @staticmethod
    def median_of(values: list[float]) -> float | None:
        if not values:
            return None
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0

    def median(self, *sources: str) -> float | None:
        return self.median_of(self.heights_m(*sources))

    def spread(self, *sources: str) -> float | None:
        vals = self.heights_m(*sources)
        return (max(vals) - min(vals)) if vals else None

    @property
    def nearest(self) -> Sample | None:
        return min(self.samples, key=lambda s: s.distance_m) if self.samples else None

    @property
    def fill_m(self) -> float | None:
        """How far the built surface sits above the ground samples, in metres.

        ``None`` unless BOTH populations are present, since one side of a difference cannot
        produce it, and ``None`` again below ``MIN_GROUND_SAMPLES``: on a developed site the
        structures outnumber the nodes by hundreds to one, and one node's height is not a
        ground level. A large positive number is foundation already stacked here; a negative
        one means the nodes nearby stand above the platform.
        """
        ground_m, built_m = self.ground_m, self.built_m
        if len(ground_m) < MIN_GROUND_SAMPLES or not built_m:
            return None
        return self.median_of(built_m) - self.median_of(ground_m)


def sample_points(node_table=None, state=None) -> list[Sample]:
    """Every point whose elevation is known, from whatever sources are available.

    ``state`` is optional: without a save only the node table contributes, which still
    covers the whole map, so unexplored ground gets an answer too.
    """
    out: list[Sample] = []
    for n in getattr(node_table, "nodes", ()) or ():
        z = n.get("z")
        if z is not None:
            out.append(Sample("node", n["x"], n["y"], float(z)))
    if state is None:
        return out

    for record in state.all_records():
        pos = record.get("pos")
        if pos and len(pos) >= 3:
            out.append(Sample("building", float(pos[0]), float(pos[1]), float(pos[2])))

    # Foundations arrive as flat [class_index, x, y, z] rows rather than as records, and are
    # by far the densest source. ``core.saveio.rows`` decodes them, so a malformed row costs
    # one sample rather than the whole probe.
    for piece in saverows.iter_structures(state.projection):
        out.append(Sample("structure", piece.x, piece.y, piece.z))
    return out


def probe(
    x: float,
    y: float,
    samples: list[Sample],
    radius_m: float = 200.0,
    terrain_field: heightfield.Field | None = None,
    hint_z_cm: float | None = None,
) -> Elevation:
    """Elevation samples within ``radius_m`` of a point. Coordinates in centimetres.

    ``terrain_field`` is passed in, and its ``None`` default means "no field was consulted"
    rather than "look one up": reaching for ``data/local/`` here would make every caller's
    answer depend on whether somebody had run a generator. The interface layer decides
    whether to offer one. ``hint_z_cm`` is a known z at the point, the player's own.
    """
    out = Elevation(x=x, y=y, radius_m=radius_m)
    if terrain_field is not None:
        out.terrain = terrain_field.z(x, y, hint_z_cm=hint_z_cm)
    for s in samples:
        d = geo.distance_m((x, y), (s.x, s.y))
        if d <= radius_m:
            out.samples.append(Sample(s.source, s.x, s.y, s.z, d))
    out.samples.sort(key=lambda s: s.distance_m)
    return out
