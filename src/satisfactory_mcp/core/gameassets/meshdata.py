"""The records a ``UStaticMesh`` walk produces: LODs, the Nanite resource, the gate verdicts.

``staticmesh`` reads the bytes and fills these in; ``nanite`` decodes the pages they locate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, NamedTuple, Required, TypeAlias

from typing_extensions import TypedDict

from ..arrays import F32Grid, I32Grid

__all__ = [
    "CollisionHull",
    "GateVerdict",
    "Lod",
    "MeshGates",
    "NaniteResource",
    "NaniteSummary",
    "PageSpan",
    "PageState",
    "RenderData",
    "Section",
]


@dataclass
class Section:
    material: int
    first_index: int
    triangles: int
    min_vertex: int
    max_vertex: int


@dataclass
class Lod:
    index: int
    sections: list[Section]
    max_deviation: float
    cooked_out: bool
    inlined: bool
    position_stride: int = 0
    vertices: int = 0
    positions_at: int = 0
    index_bytes: int = 0
    index_32bit: bool = False
    indices_at: int = 0
    tangent_stride: int = 0
    tangents_at: int = 0
    uv_sets: int = 0
    uv_stride: int = 0
    uvs_at: int = 0
    start: int = 0
    end: int = 0

    @property
    def triangles(self) -> int:
        return sum(section.triangles for section in self.sections)

    @property
    def max_vertex(self) -> int:
        return max((section.max_vertex for section in self.sections), default=-1)


class PageState(NamedTuple):
    """One ``FPageStreamingState`` row of the page table."""

    offset: int
    size: int
    page_size: int
    deps_start: int
    deps_num: int
    depth: int
    flags: int


@dataclass
class PageSpan:
    """One Nanite page: where its bytes live, what the table claims, and the bytes."""

    index: int
    offset: int
    size: int
    page_size: int
    deps_start: int
    deps_num: int
    depth: int
    flags: int
    is_root: bool
    data: bytes = b""


@dataclass
class NaniteResource:
    """``FNaniteResources``, with the offsets a page decode needs kept rather than dropped."""

    present: bool
    resource_flags: int = 0
    bulk_index: int = -1
    root_bytes: int = 0
    root_data_at: int = 0
    root_pages: int = 0
    position_precision: int = 0
    normal_precision: int = 0
    input_triangles: int = 0
    input_vertices: int = 0
    clusters: int = 0
    mesh_bounds: tuple[float, ...] = ()
    page_states: list[PageState] = field(default_factory=list[PageState])
    page_dependencies: list[int] = field(default_factory=list[int])
    pages: list[PageSpan] = field(default_factory=list[PageSpan])


class RenderData(TypedDict):
    """``parse_render_data``: the LOD array, where it was found, and the Nanite resource."""

    lod_count_at: int
    lods: list[Lod]
    num_inlined_lods: int
    nanite: NaniteResource
    nanite_at: int
    tail_bytes: int


class CollisionHull(NamedTuple):
    """A cooked collision trimesh, and the bounds pad it was checked with."""

    vertices: F32Grid
    triangles: I32Grid
    pad: float


#: One gate's verdict: passed, failed, or ``"n/a"`` where the gate could not run.
GateVerdict: TypeAlias = bool | Literal["n/a"]

#: Every gate's verdict, keyed ``"<number>_<what it checks>"``.
MeshGates: TypeAlias = dict[str, GateVerdict]


class NaniteSummary(TypedDict, total=False):
    """An extract's ``nanite`` entry: whether the mesh has pages, and what its gates found."""

    present: Required[bool]
    input_triangles: int
    clusters: int
    problems: list[str]
