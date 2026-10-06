"""A cooked ``UStaticMesh``: its LOD buffers, its collision hull, and its Nanite pages.

``packages`` stops at the property tags. Everything a ``StaticMesh`` export actually draws
itself with is in the untagged tail behind them -- ``FStaticMeshRenderData``, one
``FStaticMeshLODResources`` per LOD, then ``FNaniteResources`` -- and this module is the
walk over it. It reads bytes and hands back arrays; which meshes to open and what to do
with the triangles is the caller's business, the same division the rest of this package
keeps.

Layout follows the format as learned from CUE4Parse's implementation
(github.com/FabianFG/CUE4Parse). Nothing of theirs is copied: the field ORDER was read
from the C# and is checked here against the bytes rather than trusted.

Why the LOD array's offset is searched
--------------------------------------
The tail opens with a handful of fields whose width is not the same on every asset -- a
socket list, a nav-collision reference, an editor-only guid -- so the offset of the LOD
count is not a constant. Two routes are tried, in order:

1. **Derived.** The socket count is a field, not a guess: it is the ``int32`` immediately
   before the LOD count, so ``38 + 4*sockets`` is arithmetic. This is what opens
   ``GasPillar_01`` and ``GasPillar_02``, whose three ``StaticMeshSocket`` exports put
   their LOD array at 50 -- off the end of any fixed ladder.
2. **Searched**, over ``LOD_COUNT_SEARCH``, which covers every mesh with no sockets.

A wrong offset cannot survive either route, and that is the point: the section counts, the
position buffer's own header, and the Nanite page arithmetic all fail together on a
misread. A parse that holds together is not a coincidence a random offset can produce.

The seven gates
---------------
:func:`extract` returns geometry with the verdict of every check attached, because a
failure whose numbers cannot be looked at is a failure that cannot be fixed:

===  ===========================================================================
 #   check
===  ===========================================================================
 1   every section's ``MaxVertexIndex`` is below its LOD's vertex count
 2   the position buffer's ``(Stride, NumVertices)`` equals its BulkSerialize header
 3   ``NumRootPages`` Nanite pages tile ``RootData`` byte-exactly
 4   the streaming pages tile the ``.ubulk`` payload the Zen ``BulkDataMap`` names
 5   every decoded position is finite
 6   at least 99% of positions inside the mesh's own serialised ``Bounds``, padded
 7   ``max(index)`` is exactly the sections' ``MaxVertexIndex``
===  ===========================================================================

Gates 3 and 4 are **vacuous** on a mesh with no Nanite resources, which is a fifth of the
rock set. They report ``"n/a"`` rather than passing: a check that could not run is not
evidence, and counting it as one is how a decode regression hides.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np

from .iostore import IoStore
from .packages import (
    AssetIndex,
    PackageView,
    ScriptObjects,
    class_name_of,
    property_tags,
)

__all__ = [
    "BOUNDS_INSIDE_MIN",
    "BOUNDS_PAD_CM",
    "BOUNDS_PAD_FRACTION",
    "HULL_INSIDE_MIN",
    "TRIMESH_MARKER",
    "CollisionHull",
    "Cursor",
    "Lod",
    "NaniteResource",
    "PageSpan",
    "PageState",
    "ParseError",
    "Section",
    "bounds_pad",
    "bulk_size",
    "collision_hull",
    "extended_bounds",
    "extract",
    "inside_fraction",
    "load_nanite",
    "lod0_buffers",
    "page_table_problems",
    "parse_render_data",
    "render_tail",
    "static_mesh_export",
]

#: A ``FHierarchyNodeSlice`` is 52 bytes and the fanout is 4.
HIERARCHY_NODE_BYTES = 208

#: How far past its own ``ExtendedBounds`` a vertex may sit before the decode is disbelieved.
#: Both a gate and, per triangle downstream, a clamp against stray far vertices.
BOUNDS_PAD_CM = 4.0
BOUNDS_PAD_FRACTION = 0.05
BOUNDS_INSIDE_MIN = 0.99

#: The share of a collision hull's vertices that must sit inside the padded bounds.
HULL_INSIDE_MIN = 0.90

#: The cooked Chaos ``FTriangleMeshImplicitObject`` marker, and the window it is searched
#: in. Searched, never indexed: a fixed offset is a guess that keeps working until it does
#: not, and a misaligned read here produces plausible noise rather than an error.
TRIMESH_MARKER = struct.pack("<I", 267)
TRIMESH_SEARCH = (40, 400)

#: The ladder :func:`parse_render_data` falls back to. 38 on most meshes; the rest are the
#: widths the fields in front of the LOD count take on the assets that are not most meshes.
LOD_COUNT_SEARCH = (38, 36, 40, 34, 42, 30, 32, 44, 46)

#: Where the socket count sits, and the arithmetic it implies. Tried before the ladder.
SOCKET_COUNT_AT = 34
SOCKET_MAX = 64


class ParseError(Exception):
    """A tail that does not hold together. Never a silently different answer."""


class Cursor:
    """A forward-only reader that refuses to run off the end rather than wrapping."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0) -> None:
        self.buf = buf
        self.pos = pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.buf):
            raise ParseError(f"want {n} bytes at {self.pos}, have {len(self.buf) - self.pos}")
        out = self.buf[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def skip(self, n: int) -> None:
        if n < 0 or self.pos + n > len(self.buf):
            raise ParseError(f"skip {n} at {self.pos} runs off the end")
        self.pos += n

    def bulk_array(self) -> tuple[int, int, int]:
        """``TArray::BulkSerialize``: element size, count, and where the data starts."""
        size = self.i32()
        count = self.i32()
        at = self.pos
        self.skip(size * count)
        return size, count, at


def bounds_pad(low, high) -> float:
    """How far outside ``ExtendedBounds`` a vertex may sit, in centimetres."""
    return BOUNDS_PAD_CM + BOUNDS_PAD_FRACTION * float(np.max(np.asarray(high) - np.asarray(low)))


def inside_fraction(verts: np.ndarray, low, high, pad: float) -> float:
    """The share of ``verts`` inside ``[low - pad, high + pad]`` on every axis."""
    return float(((verts >= low - pad) & (verts <= high + pad)).all(axis=1).mean())


# --------------------------------------------------------------------------------------
# The render-data walk.
# --------------------------------------------------------------------------------------


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
    page_states: list[PageState] = field(default_factory=list)
    page_dependencies: list[int] = field(default_factory=list)
    pages: list[PageSpan] = field(default_factory=list)


def _section(cur: Cursor) -> Section:
    values = struct.unpack("<10i", cur.take(40))
    return Section(*values[:5])


def _index_buffer(cur: Cursor) -> tuple[bool, int, int]:
    is32 = cur.i32() != 0
    size, count, at = cur.bulk_array()
    cur.skip(4)  # bShouldExpandTo32Bit
    return is32, size * count, at


def _sampler(cur: Cursor) -> None:
    cur.skip(cur.i32() * 4)  # Prob
    cur.skip(cur.i32() * 4)  # Alias
    cur.skip(4)  # TotalWeight


def _serialize_buffers(cur: Cursor, lod: Lod) -> None:
    """The LOD's vertex and index buffers, gated on the strip flags rather than assumed.

    Reading the flags is what survives the extra UV set and the optional colour buffer: a
    fixed-offset walk past those fails on nine of this build's rock meshes and produces a
    plausible-looking vertex array on some of them, which is worse.
    """
    strip_global, strip_class = cur.u8(), cur.u8()

    lod.position_stride = cur.i32()
    lod.vertices = cur.i32()
    size, count, at = cur.bulk_array()
    lod.positions_at = at
    if count != lod.vertices or size != lod.position_stride:
        raise ParseError(
            f"position buffer says {lod.vertices}x{lod.position_stride}, array says {count}x{size}"
        )

    tangent_strip = cur.u8(), cur.u8()
    cur.i32()  # NumTexCoords
    vertex_count = cur.i32()
    cur.skip(8)  # bUseFullPrecisionUVs, bUseHighPrecisionTangentBasis
    if vertex_count != lod.vertices:
        raise ParseError(f"tangent buffer has {vertex_count} vertices, positions {lod.vertices}")
    if not tangent_strip[0] & 2:
        cur.bulk_array()  # tangents
        cur.bulk_array()  # texcoords

    colour_strip = cur.u8(), cur.u8()
    cur.skip(4)  # Stride
    colour_vertices = cur.i32()
    if not colour_strip[0] & 2 and colour_vertices > 0:
        cur.bulk_array()

    lod.index_32bit, lod.index_bytes, lod.indices_at = _index_buffer(cur)
    if not strip_class & 4:  # CDSF_ReversedIndexBuffer
        _index_buffer(cur)
    _index_buffer(cur)  # depth only
    if not strip_class & 4:
        _index_buffer(cur)
    if not strip_global & 1:  # editor data present -> wireframe indices
        _index_buffer(cur)
    if not strip_class & 8:  # CDSF_RayTracingResources
        cur.skip(24)  # RawDataHeader, 6 uint32, UE 5.6
        cur.bulk_array()

    for _ in lod.sections:
        _sampler(cur)
    _sampler(cur)


def _lod(cur: Cursor, index: int) -> Lod:
    start = cur.pos
    strip_global, _strip_class = cur.u8(), cur.u8()
    count = cur.i32()
    if not 0 <= count <= 64:
        raise ParseError(f"LOD {index}: implausible section count {count}")
    sections = [_section(cur) for _ in range(count)]
    cur.skip(56)  # FBoxSphereBounds, seven doubles
    lod = Lod(
        index=index,
        sections=sections,
        max_deviation=cur.f32(),
        cooked_out=cur.i32() != 0,
        inlined=cur.i32() != 0,
    )
    cur.skip(4)  # bHasRayTracingGeometry, UE 5.5+
    if not strip_global & 2 and not lod.cooked_out:
        if not lod.inlined:
            raise ParseError(f"LOD {index} is streamed, which this reader does not open")
        _serialize_buffers(cur, lod)
        cur.skip(12)  # the three buffer sizes
    lod.start, lod.end = start, cur.pos
    for section in sections:
        if lod.vertices and section.max_vertex >= lod.vertices:
            raise ParseError(
                f"LOD {index}: section max vertex {section.max_vertex} >= {lod.vertices}"
            )
    return lod


def _nanite(cur: Cursor) -> NaniteResource:
    strip_global, _strip_class = cur.u8(), cur.u8()
    if strip_global & 2:
        return NaniteResource(present=False)
    flags = cur.u32()
    bulk_index = cur.i32()
    root_bytes = cur.i32()
    root_data_at = cur.pos
    cur.skip(root_bytes)

    states = []
    for _ in range(cur.i32()):
        offset, size, page_size, deps_start = struct.unpack("<4I", cur.take(16))
        deps_num, depth, page_flags = struct.unpack("<H2B", cur.take(4))
        states.append(PageState(offset, size, page_size, deps_start, deps_num, depth, page_flags))

    cur.skip(cur.i32() * HIERARCHY_NODE_BYTES)
    cur.skip(cur.i32() * 4)  # hierarchy root offsets
    n_deps = cur.i32()
    deps = list(struct.unpack(f"<{n_deps}I", cur.take(n_deps * 4))) if n_deps else []
    cur.skip(cur.i32() * 48)  # FMatrix3x4 assembly transforms, UE 5.6+
    mesh_bounds = struct.unpack("<7f", cur.take(28))  # FBoxSphereBounds3f, UE 5.6+
    cur.skip(cur.i32() * 2)  # imposter atlas
    return NaniteResource(
        present=bool(states) or root_bytes > 0,
        resource_flags=flags,
        bulk_index=bulk_index,
        root_bytes=root_bytes,
        root_data_at=root_data_at,
        root_pages=cur.i32(),
        position_precision=cur.i32(),
        normal_precision=cur.i32(),
        input_triangles=cur.u32(),
        input_vertices=cur.u32(),
        clusters=cur.u32(),
        mesh_bounds=mesh_bounds,
        page_states=states,
        page_dependencies=deps,
    )


def _root_tiling_problems(nanite: NaniteResource) -> list[str]:
    """Gate 3: the root pages must tile ``RootData`` byte-exactly from zero."""
    if not nanite.page_states:
        return []
    problems: list[str] = []
    root = nanite.page_states[: nanite.root_pages]
    covered = sum(state.size for state in root)
    if covered != nanite.root_bytes:
        problems.append(f"root pages cover {covered} of {nanite.root_bytes} RootData bytes")
    at = 0
    for state in root:
        if state.offset != at:
            problems.append(f"root page starts at {state.offset}, expected {at}")
            break
        at += state.size
    return problems


def _stream_tiling_problems(nanite: NaniteResource, ubulk_bytes: int | None) -> list[str]:
    """Gate 4: the streaming pages must tile the ``.ubulk`` payload the ``BulkDataMap`` names."""
    if not nanite.page_states:
        return []
    problems: list[str] = []
    stream = nanite.page_states[nanite.root_pages :]
    at = 0
    for state in stream:
        if state.offset != at:
            problems.append(f"streaming page starts at {state.offset}, expected {at}")
            break
        at += state.size
    if ubulk_bytes is not None and stream and at != ubulk_bytes:
        problems.append(f"streaming pages cover {at} bytes, the .ubulk entry says {ubulk_bytes}")
    return problems


def page_table_problems(nanite: NaniteResource, bulk_size: int | None) -> list[str]:
    """Independent arithmetic on the page table. An empty list means every check passed.

    Gates 3 and 4: two statements the file makes about itself in two different places,
    which agree only if both were read correctly.
    """
    return _root_tiling_problems(nanite) + _stream_tiling_problems(nanite, bulk_size)


def _lod_count_candidates(tail: bytes) -> list[int]:
    """Where the LOD count may be: derived from the socket count first, then the ladder."""
    candidates: list[int] = []
    try:
        sockets = struct.unpack_from("<I", tail, SOCKET_COUNT_AT)[0]
        if 0 < sockets <= SOCKET_MAX:
            candidates.append(38 + 4 * sockets)
    except struct.error:
        pass
    return [*candidates, *LOD_COUNT_SEARCH]


def parse_render_data(tail: bytes, lod_count_at: int | None = None) -> dict:
    """The cooked render data of one ``StaticMesh`` export, from past its property tags.

    Without ``lod_count_at`` the offset is searched, and a candidate whose Nanite page table
    disagrees with itself is rejected like one that does not parse.
    """
    if lod_count_at is not None:
        return _parse_render_data_at(tail, lod_count_at)
    first: ParseError | None = None
    for candidate in _lod_count_candidates(tail):
        try:
            parsed = _parse_render_data_at(tail, candidate)
        except ParseError as exc:
            first = first or exc
            continue
        if page_table_problems(parsed["nanite"], None):
            first = first or ParseError(f"offset {candidate}: nanite page table disagrees")
            continue
        return parsed
    raise first or ParseError("no LOD array offset parses")


def _parse_render_data_at(tail: bytes, lod_count_at: int) -> dict:
    cur = Cursor(tail, lod_count_at)
    count = cur.i32()
    if not 1 <= count <= 8:
        raise ParseError(f"implausible LOD count {count} at {lod_count_at}")
    lods = [_lod(cur, i) for i in range(count)]
    inlined = cur.u8()
    nanite = _nanite(cur)
    return {
        "lod_count_at": lod_count_at,
        "lods": lods,
        "num_inlined_lods": inlined,
        "nanite": nanite,
        "nanite_at": lods[-1].end + 1,
        "tail_bytes": len(tail),
    }


# --------------------------------------------------------------------------------------
# Opening one mesh package.
# --------------------------------------------------------------------------------------


def static_mesh_export(view: PackageView) -> dict | None:
    """The package's ``StaticMesh`` export, or ``None``."""
    return next(
        (
            export
            for export in view.exports
            if class_name_of(view.class_of.get(export["slot"])) == "StaticMesh"
        ),
        None,
    )


def extended_bounds(view: PackageView, export: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """``ExtendedBounds`` as ``(low, high)`` in mesh-local centimetres, or ``None``.

    The mesh's own statement about where its vertices are, and therefore the only check
    available that does not need a second decoder: a misaligned read puts them elsewhere.
    """
    payload = view.props(export["slot"]).get("ExtendedBounds")
    if not payload:
        return None
    decoded = view.decode_struct(payload) or {}
    origin, extent = decoded.get("Origin"), decoded.get("BoxExtent")
    if not isinstance(origin, dict) or not isinstance(extent, dict):
        return None
    try:
        centre = np.array(struct.unpack("<3d", bytes.fromhex(origin["_raw"])[:24]))
        half_size = np.array(struct.unpack("<3d", bytes.fromhex(extent["_raw"])[:24]))
    except (KeyError, ValueError, struct.error):
        return None
    return centre - half_size, centre + half_size


def render_tail(view: PackageView, export: dict) -> bytes:
    """The untagged bytes behind an export's property tags."""
    body = view.pkg.body(export)
    _tags, end = property_tags(body, view.pkg.names)
    return body[end:]


def lod0_buffers(tail: bytes, parsed: dict) -> tuple[np.ndarray, np.ndarray, int] | None:
    """LOD0's ``(positions, triangles, max vertex)`` from a parsed walk, or None."""
    lod = parsed["lods"][0]
    if not lod.vertices or not lod.index_bytes or lod.position_stride != 12:
        return None
    verts = np.frombuffer(tail, "<f4", count=3 * lod.vertices, offset=lod.positions_at)
    width = 4 if lod.index_32bit else 2
    index_count = lod.index_bytes // width
    if index_count % 3:
        return None
    indices = np.frombuffer(tail, f"<u{width}", count=index_count, offset=lod.indices_at).reshape(
        -1, 3
    )
    return verts.reshape(-1, 3), indices.astype(np.int64), lod.max_vertex


def _lod0_by_search(tail: bytes) -> tuple[np.ndarray, np.ndarray, int] | None:
    """The fallback route: the section table at a fixed offset, then SEARCH for the indices.

    Kept because it does one thing the walk does not -- it finds the index buffer by
    matching its header instead of arriving at it -- and it is held to the same seven
    gates as the walk, so a mesh that comes out of here is not trusted more cheaply.
    """
    try:
        section_count = struct.unpack_from("<I", tail, 44)[0]
        if not 1 <= section_count <= 64:
            return None
        triangles = max_vertex = 0
        for section in range(section_count):
            values = struct.unpack_from("<10I", tail, 48 + section * 40)
            triangles += values[2]
            max_vertex = max(max_vertex, values[4])
        pos = 48 + section_count * 40 + 56 + 16
        stride, vertex_count = struct.unpack_from("<II", tail, pos + 2)
        element_size, count = struct.unpack_from("<II", tail, pos + 10)
        if (
            stride != 12
            or element_size != 12
            or count != vertex_count
            or vertex_count != max_vertex + 1
        ):
            return None
        at = pos + 18
        verts = np.frombuffer(tail, "<f4", count=vertex_count * 3, offset=at).reshape(-1, 3)
        at += vertex_count * 12
        for width in (2, 4):
            want = struct.pack("<III", 1 if width == 4 else 0, 1, triangles * 3 * width)
            probe = tail.find(want, at, at + 16384)
            if probe < 0:
                continue
            start = probe + 12
            if start + triangles * 3 * width > len(tail):
                continue
            indices = np.frombuffer(tail, f"<u{width}", count=triangles * 3, offset=start).reshape(
                -1, 3
            )
            if int(indices.max()) == max_vertex:
                return verts, indices.astype(np.int64), max_vertex
        return None
    except (struct.error, IndexError, ValueError):
        return None


def _render_walk(tail: bytes, out: dict, gates: dict) -> dict | None:
    """Gates 1 and 2: the render-data walk, which raises when either fails."""
    try:
        parsed = parse_render_data(tail)
    except ParseError as exc:
        gates["1_section_max_vertex"] = gates["2_position_buffer_header"] = False
        out["parse_error"] = str(exc)
        return None
    gates["1_section_max_vertex"] = True
    gates["2_position_buffer_header"] = True
    out["route"] = "render-data walk"
    out["lod_triangles"] = [lod.triangles for lod in parsed["lods"]]
    return parsed


def _nanite_gates(view: PackageView, parsed: dict | None, out: dict, gates: dict) -> None:
    """Gates 3 and 4, ``"n/a"`` on a mesh with no Nanite pages."""
    nanite = parsed["nanite"] if parsed is not None else None
    if nanite is None or not nanite.page_states:
        gates["3_nanite_root_tiling"] = gates["4_nanite_stream_tiling"] = "n/a"
        out["nanite"] = {"present": False}
        return
    root_problems = _root_tiling_problems(nanite)
    stream_problems = _stream_tiling_problems(nanite, bulk_size(view, nanite))
    gates["3_nanite_root_tiling"] = not root_problems
    gates["4_nanite_stream_tiling"] = not stream_problems
    out["nanite"] = {
        "present": True,
        "input_triangles": int(nanite.input_triangles),
        "clusters": int(nanite.clusters),
        "problems": root_problems + stream_problems,
    }


def _lod0_geometry(
    tail: bytes, parsed: dict | None, out: dict, gates: dict
) -> tuple[np.ndarray, np.ndarray, int] | None:
    """LOD0 off the walk, or off the index search, which then also passes gates 1 and 2."""
    got = lod0_buffers(tail, parsed) if parsed is not None else None
    if got is None:
        got = _lod0_by_search(tail)
        if got is not None:
            out["route"] = "index search"
            gates["1_section_max_vertex"] = gates["2_position_buffer_header"] = True
    return got


def _bounds_gate(verts: np.ndarray, bounds, out: dict, gates: dict) -> None:
    """Gate 6: nearly every position inside the mesh's own padded ``ExtendedBounds``."""
    if bounds is None:
        gates["6_inside_bounds"] = "n/a"
        out["inside_fraction"] = None
        return
    low, high = bounds
    inside = inside_fraction(verts, low, high, bounds_pad(low, high))
    gates["6_inside_bounds"] = inside >= BOUNDS_INSIDE_MIN
    out["inside_fraction"] = round(inside, 5)


def extract(store: IoStore, scripts: ScriptObjects, index: AssetIndex, mesh_path: str) -> dict:
    """LOD0 triangles for one placed mesh, with the verdict of every gate attached.

    ``ok`` is true only when every gate that could run passed. The geometry comes back even
    when it is false, deliberately: a caller that cannot look at the numbers of a failure
    cannot fix it. Nothing downstream is expected to consume a row whose ``ok`` is false.
    """
    out: dict = {"mesh": mesh_path, "gates": {}}
    package = index.path_for(mesh_path)
    if not package:
        out["error"] = "unresolved: no container path"
        out["ok"] = False
        return out
    out["package"] = package
    view = PackageView(store.read_path(package), scripts)
    mesh_export = static_mesh_export(view)
    if mesh_export is None:
        out["error"] = "no StaticMesh export"
        out["ok"] = False
        return out

    bounds = extended_bounds(view, mesh_export)
    tail = render_tail(view, mesh_export)
    gates = out["gates"]
    parsed = _render_walk(tail, out, gates)
    _nanite_gates(view, parsed, out, gates)
    got = _lod0_geometry(tail, parsed, out, gates)
    if got is None:
        out["error"] = out.get("parse_error", "no LOD0 buffers found")
        out["ok"] = False
        return out

    verts, indices, max_vertex = got
    gates["5_positions_finite"] = bool(np.isfinite(verts).all())
    gates["7_max_index_is_max_vertex"] = bool(int(indices.max()) == max_vertex)
    _bounds_gate(verts, bounds, out, gates)

    out["verts"] = verts.astype(np.float32)
    out["idx"] = indices.astype(np.int32)
    out["bounds_local_cm"] = None if bounds is None else [b.tolist() for b in bounds]
    out["view"] = view
    out["tail"] = tail
    out["parsed"] = parsed
    out["ok"] = all(verdict is True for verdict in gates.values() if verdict != "n/a")
    return out


class CollisionHull(NamedTuple):
    """A cooked collision trimesh, and the bounds pad it was checked with."""

    vertices: np.ndarray
    triangles: np.ndarray
    pad: float


def collision_hull(view: PackageView, low, high) -> tuple[CollisionHull | None, str | None]:
    """The cooked Chaos trimesh out of a ``BodySetup``, as ``(hull, why)``.

    Layout, past the export's property tags::

        ... header ...  <u32 267>  <u8 flag>  <u32 NumVerts>  <NumVerts * 3 float32>
        <u32 0>  <u32 NumTris>  <NumTris * 3 uint16, or uint32 when NumVerts >= 65536>

    Three checks decide whether the result is believed, and together they are what makes a
    misaligned read fail instead of returning plausible noise: the vertices are finite, 90%
    of them are inside the mesh's own bounds, and every index is below ``NumVerts``. The
    last one also picks the index width -- a uint16 view of a uint32 array reads indices
    about twice the vertex count, so it fails and the wider view is tried.

    ``why`` is a sentence rather than ``None`` on failure because 21 of this build's rock
    meshes legitimately ship no cooked trimesh and the sidecar names the reason for each.
    """
    body_setup = next(
        (
            export
            for export in view.exports
            if class_name_of(view.class_of.get(export["slot"])) == "BodySetup"
        ),
        None,
    )
    if body_setup is None:
        return None, "no BodySetup export"
    blob = render_tail(view, body_setup)
    at = blob.find(TRIMESH_MARKER, *TRIMESH_SEARCH)
    if at < 0:
        return None, "no 267 marker in the search window"
    count = struct.unpack_from("<I", blob, at + 5)[0]
    pos = at + 9
    if count <= 0 or pos + 12 * count > len(blob):
        return None, f"implausible vertex count {count}"
    verts = np.frombuffer(blob, "<f4", count=3 * count, offset=pos).reshape(count, 3)
    if not np.isfinite(verts).all():
        return None, "non-finite vertices"
    pad = bounds_pad(low, high)
    inside = inside_fraction(verts, low, high, pad)
    if inside <= HULL_INSIDE_MIN:
        return None, f"only {inside:.1%} of vertices inside ExtendedBounds"
    pos += 12 * count
    _zero, triangles = struct.unpack_from("<II", blob, pos)
    pos += 8
    for width, dtype in ((2, "<u2"), (4, "<u4")):
        if triangles > 0 and pos + 3 * triangles * width <= len(blob):
            candidate = np.frombuffer(blob, dtype, count=3 * triangles, offset=pos).reshape(
                triangles, 3
            )
            if candidate.max() < count:
                hull = CollisionHull(verts.astype(np.float32), candidate.astype(np.int32), pad)
                return hull, None
    return None, f"no index width fits {triangles} triangles over {count} vertices"


# --------------------------------------------------------------------------------------
# Nanite page bytes: inline root plus the streamed tail.
# --------------------------------------------------------------------------------------


def _bulk_entries(view: PackageView) -> list[dict] | None:
    """The package's ``BulkDataMap``, or None where its header does not hold together."""
    try:
        return view.pkg.bulk_entries()
    except (ValueError, struct.error, IndexError):
        return None


def bulk_size(view: PackageView, nanite: NaniteResource) -> int | None:
    """The size of the ``.ubulk`` payload the resource's ``BulkDataMap`` entry names."""
    entries = _bulk_entries(view)
    if entries is None or not 0 <= nanite.bulk_index < len(entries):
        return None
    return entries[nanite.bulk_index]["size"]


def load_nanite(
    store: IoStore, package_path: str, view: PackageView, parsed: dict, tail: bytes
) -> NaniteResource | None:
    """Fill a parsed :class:`NaniteResource` with its pages' actual bytes, or ``None``.

    The root pages are **inline** in the export's own tail -- 29% of one export on
    ``ArcCrooked_01`` -- and the streaming pages are a run of the sibling ``.ubulk`` that
    the Zen ``BulkDataMap`` locates. Both are checked to tile exactly before anything is
    decoded, which is gates 3 and 4 again at the point where being wrong would matter.
    """
    nanite: NaniteResource = parsed["nanite"]
    if not nanite.present or not nanite.page_states:
        return None
    root_blob = tail[nanite.root_data_at : nanite.root_data_at + nanite.root_bytes]

    bulk_blob = b""
    entries = _bulk_entries(view)
    if entries is not None and 0 <= nanite.bulk_index < len(entries):
        entry = entries[nanite.bulk_index]
        bulk_path = package_path.rsplit(".", 1)[0] + ".ubulk"
        if entry["size"] and bulk_path in store.by_path:
            raw = store.read_path(bulk_path)
            bulk_blob = raw[entry["offset"] : entry["offset"] + entry["size"]]

    pages: list[PageSpan] = []
    for page_index, state in enumerate(nanite.page_states):
        is_root = page_index < nanite.root_pages
        source = root_blob if is_root else bulk_blob
        data = source[state.offset : state.offset + state.size]
        if len(data) != state.size:
            raise ParseError(f"nanite page {page_index} wants {state.size} bytes, got {len(data)}")
        pages.append(
            PageSpan(
                index=page_index,
                offset=state.offset,
                size=state.size,
                page_size=state.page_size,
                deps_start=state.deps_start,
                deps_num=state.deps_num,
                depth=state.depth,
                flags=state.flags,
                is_root=is_root,
                data=data,
            )
        )
    nanite.pages = pages
    return nanite
