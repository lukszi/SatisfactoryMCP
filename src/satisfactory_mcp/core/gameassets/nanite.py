"""A pure-Python Nanite page decoder for UE 5.6 -- positions and triangle indices only.

The format was learned by reading CUE4Parse's implementation
(github.com/FabianFG/CUE4Parse: ``FNaniteStreamableData``, ``FPageDiskHeader``,
``FCluster``, ``FFixupChunk``, ``NaniteUtils``). Nothing is copied -- the bit layouts and
the strip-index algorithm are reimplemented fresh from a read of the C# and checked here
against the bytes on disk, the same way the save parser was written against its format.

What it is for
--------------
The rock and cliff meshes of this world ship two descriptions of themselves: a cooked
collision hull at about 1 m mean edge length, and the Nanite leaf level at **0.48 m**
world-space median edge. The terrain field was built from the first, and this is the
second. It does not make the field more *accurate* -- that was measured across a 12x
triangle range and the error is topological, not geometric -- it makes it **denser**, and
density is what a renderer needs before it can stop interpolating: 55% of the cliff
province becomes honest at 0.458 m per pixel, against 6% from the hull.

Deliberately partial
--------------------
Attributes -- normals, tangents, colours, UVs -- are **skipped**. A heightfield needs
positions and topology, and the position stream is the FIRST of the low/mid/high byte
planes, so skipping the rest costs nothing to reach and removes most of the
version-branched surface. Only the **leaf** cluster level is assembled; Nanite is already
a multi-resolution pyramid and the coarser levels are decoded but not returned.

What makes a wrong decode loud
------------------------------
Three checks, each cheap and each of a different kind:

* the assembled leaf triangle count equals the resource's own ``NumInputTriangles``;
* the decoded cluster count equals its ``NumClusters``;
* on a mesh that is closed in source -- 84 of this build's 156 -- :func:`boundary_edges`
  is **zero**. One bit wrong in the strip decode or in vertex-reference resolution
  shatters that into thousands of boundary edges, which makes it the most sensitive
  falsifier available and it costs one sort.

Scope: UE 5.4 <= version < 5.7 (Satisfactory build 495413 is 5.6.1). Outside it, the
cluster bit layout and the vertex-reference encoding both move, and this reader would
produce plausible arrays rather than an error -- which is why the three checks above are
not optional decoration.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TypedDict

import numpy as np

from ..arrays import F32Grid, F64Grid, I32Grid, I64Grid, U8Grid
from .meshdata import NaniteResource

NANITE_FIXUP_MAGIC = 0x464E
MAX_CLUSTERS_PER_PAGE_BITS = 8  # 5.4+: max(17-9, 15-9) = 8
CLUSTER_FLOAT4S = 8
GPU_PAGE_HEADER_SIZE = 16
MIN_POSITION_PRECISION = -20  # 5.4+


class DecodeError(Exception):
    pass


class DecodedMesh(TypedDict):
    """One mesh's leaf level in mesh-local centimetres, welded, and what was counted on the way."""

    positions: F32Grid
    triangles: I64Grid
    total_clusters: int
    leaf_clusters: int
    vertices_before_weld: int


# ---------------------------------------------------------------- bit helpers
# C# shift operators mask the count to 5 bits; several call sites rely on that
# (FirstBitHigh returning 0xFFFFFFFF, and a foundBitIndex-1 of -1), so the mask is
# reproduced explicitly rather than left to Python's unbounded shifts.


def get_bits(value: int, num_bits: int, offset: int) -> int:
    return (value >> (offset & 31)) & ((1 << num_bits) - 1)


def get_bits_signed(value: int, num_bits: int, offset: int) -> int:
    v = get_bits(value, num_bits, offset)
    sign = 1 << (num_bits - 1)
    return (v ^ sign) - sign


def bit_align_u32(high: int, low: int, shift: int) -> int:
    shift &= 31
    result = low >> shift
    if shift:
        result |= (high << (32 - shift)) & 0xFFFFFFFF
    return result & 0xFFFFFFFF


def first_bit_high(x: int) -> int:
    return 0xFFFFFFFF if x == 0 else x.bit_length() - 1


def popcount(x: int) -> int:
    return (x & 0xFFFFFFFF).bit_count()


def precision_scale(exponent: int) -> float:
    """``1.0f`` with ``exponent`` subtracted from its biased exponent field, i.e. 2**-e."""
    bits = 0x3F800000 - (exponent << 23)
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


# ---------------------------------------------------------------- structures


def _no_rows() -> I64Grid:
    """A ``(0, 3)`` array: a cluster's vertices and triangles until its page is decoded."""
    return np.zeros((0, 3), dtype=np.int64)


@dataclass
class Cluster:
    index: int
    num_verts: int
    num_tris: int
    pos_start: tuple[int, int, int]
    bits_per_index: int
    pos_precision: int
    pos_scale: float
    pos_bits: tuple[int, int, int]
    box_center: tuple[float, float, float]
    box_extent: tuple[float, float, float]
    lod_error: float
    edge_length: float
    flags: int
    material_encoding: int
    raw_pos: I64Grid = field(default_factory=_no_rows)  # (num_verts, 3), page-local quantised
    tris: I64Grid = field(default_factory=_no_rows)  # (num_tris, 3), cluster-local vertex ids

    @property
    def is_leaf(self) -> bool:
        """The encoder negates EdgeLength on clusters that are leaves at full detail."""
        return self.edge_length < 0


@dataclass
class ClusterDiskHeader:
    index_data: int
    page_cluster_map: int
    vertex_ref_data: int
    low_bytes: int
    mid_bytes: int
    high_bytes: int
    num_vertex_refs: int
    num_prev_ref_before_dwords: int
    num_prev_new_before_dwords: int


@dataclass
class Page:
    index: int
    num_clusters: int
    num_vertex_refs: int
    strip_bitmask_offset: int
    vertex_ref_bitmask_offset: int
    decode_info_offset: int
    disk_header_at: int
    gpu_header_at: int
    clusters: list[Cluster] = field(default_factory=list[Cluster])
    headers: list[ClusterDiskHeader] = field(default_factory=list[ClusterDiskHeader])


# ---------------------------------------------------------------- page reader


class PageReader:
    """Byte access into one page's buffer, with the unaligned-dword read the
    index stream needs. Reads past the end return zero, matching the C# guard."""

    __slots__ = ("buf", "n")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.n = len(buf)

    def u32(self, at: int) -> int:
        if at < 0 or at + 4 > self.n:
            return int.from_bytes(self.buf[max(at, 0) : at + 4].ljust(4, b"\0"), "little")
        return struct.unpack_from("<I", self.buf, at)[0]

    def u8(self, at: int) -> int:
        return self.buf[at] if 0 <= at < self.n else 0

    def unaligned_dword(self, base: int, bit_offset: int) -> int:
        byte_address = base + (bit_offset >> 3)
        aligned = byte_address & ~3
        shift = ((byte_address - aligned) << 3) | (bit_offset & 7)
        return bit_align_u32(self.u32(aligned + 4), self.u32(aligned), shift)


# ---------------------------------------------------------------- header parse


def parse_page_headers(data: bytes, page_index: int) -> tuple[Page, PageReader]:
    magic, num_clusters, num_hier_fixups, num_cluster_fixups = struct.unpack_from("<4H", data, 0)
    if magic != NANITE_FIXUP_MAGIC:
        raise DecodeError(f"page {page_index}: fixup magic 0x{magic:04x}, expected 0x464e")
    at = 8 + 16 * num_hier_fixups + 8 * num_cluster_fixups

    disk_header_at = at
    (
        disk_header_clusters,
        _num_raw_float4s,
        num_vertex_refs,
        decode_info_offset,
        strip_bitmask_offset,
        vertex_ref_bitmask_offset,
    ) = struct.unpack_from("<6I", data, at)
    at += 24
    if disk_header_clusters != num_clusters:
        raise DecodeError(
            f"page {page_index}: fixup says {num_clusters} clusters, "
            f"disk header {disk_header_clusters}"
        )

    headers: list[ClusterDiskHeader] = []
    for _ in range(num_clusters):
        fields = struct.unpack_from("<9I", data, at)
        at += 36
        headers.append(ClusterDiskHeader(*fields))

    gpu_header_at = at
    gpu_packed = struct.unpack_from("<I", data, at)[0]
    gpu_clusters = get_bits(gpu_packed, 16, 0)
    if gpu_clusters != num_clusters:
        raise DecodeError(
            f"page {page_index}: gpu header says {gpu_clusters} clusters, fixup {num_clusters}"
        )

    page = Page(
        index=page_index,
        num_clusters=num_clusters,
        num_vertex_refs=num_vertex_refs,
        strip_bitmask_offset=strip_bitmask_offset,
        vertex_ref_bitmask_offset=vertex_ref_bitmask_offset,
        decode_info_offset=decode_info_offset,
        disk_header_at=disk_header_at,
        gpu_header_at=gpu_header_at,
        headers=headers,
    )
    return page, PageReader(data)


def parse_clusters(page: Page, reader: PageReader) -> None:
    """The packed cluster structs, stored SOA: eight float4 rows, cluster-major within
    each row. Only the fields a position/topology decode needs are unpacked."""
    cluster_count = page.num_clusters
    origin = page.gpu_header_at + GPU_PAGE_HEADER_SIZE

    def row_address(row: int, cluster_index: int) -> int:
        return origin + 16 * cluster_count * row + 16 * cluster_index

    for cluster_index in range(cluster_count):
        counts_row = row_address(0, cluster_index)
        num_verts_pos_offset = reader.u32(counts_row)
        num_verts = get_bits(num_verts_pos_offset, 14, 0)  # 5.6 widened this from 9 bits
        num_tris_index_offset = reader.u32(counts_row + 4)
        num_tris = get_bits(num_tris_index_offset, 8, 0)

        position_row = row_address(1, cluster_index)
        pos_start = struct.unpack_from("<3i", reader.buf, position_row)
        packed = reader.u32(position_row + 12)
        bits_per_index = get_bits(packed, 3, 0) + 1
        pos_precision = get_bits(packed, 6, 3) + MIN_POSITION_PRECISION
        pos_bits = (get_bits(packed, 5, 9), get_bits(packed, 5, 14), get_bits(packed, 5, 19))

        bounds_row = row_address(3, cluster_index)
        box_center = struct.unpack_from("<3f", reader.buf, bounds_row)
        lod_error, edge_length = struct.unpack_from("<2e", reader.buf, bounds_row + 12)

        extent_row = row_address(4, cluster_index)
        box_extent = struct.unpack_from("<3f", reader.buf, extent_row)
        flags = get_bits(reader.u32(extent_row + 12), 4, 0)

        material_row = row_address(5, cluster_index)
        material_encoding = reader.u32(material_row + 12)

        page.clusters.append(
            Cluster(
                index=cluster_index,
                num_verts=num_verts,
                num_tris=num_tris,
                pos_start=pos_start,
                bits_per_index=bits_per_index,
                pos_precision=pos_precision,
                pos_scale=precision_scale(pos_precision),
                pos_bits=pos_bits,
                box_center=box_center,
                box_extent=box_extent,
                lod_error=float(lod_error),
                edge_length=float(edge_length),
                flags=flags,
                material_encoding=material_encoding,
            )
        )


# ---------------------------------------------------------------- index decode


def triangle_indices(
    reader: PageReader,
    page: Page,
    disk_header: ClusterDiskHeader,
    cluster_index: int,
    tri_index: int,
) -> tuple[int, int, int]:
    """One triangle out of the strip bitmask. Three parallel bitmasks per dword of
    triangles say, per triangle, whether it starts a strip, turns left, and reuses a
    vertex; everything else is a prefix-count over those masks."""
    dword_index = tri_index >> 5
    bit_index = tri_index & 31

    at = page.disk_header_at + page.strip_bitmask_offset + (cluster_index * 4 + dword_index) * 12
    s_mask, l_mask, w_mask = struct.unpack_from("<3I", reader.buf, at)
    sl_mask = s_mask & l_mask
    head_ref_vertex_mask = (sl_mask | ~s_mask) & w_mask & 0xFFFFFFFF

    prev_bits_mask = (1 << bit_index) - 1

    if dword_index == 0:
        prev_ref_before = prev_new_before = 0
    else:
        off = dword_index * 10 - 10
        prev_ref_before = get_bits(disk_header.num_prev_ref_before_dwords, 10, off)
        prev_new_before = get_bits(disk_header.num_prev_new_before_dwords, 10, off)

    cur_prev_ref = (popcount(sl_mask & prev_bits_mask) << 1) + popcount(w_mask & prev_bits_mask)
    cur_prev_new = (popcount(s_mask & prev_bits_mask) << 1) + bit_index - cur_prev_ref

    num_prev_ref = prev_ref_before + cur_prev_ref
    num_prev_new = prev_new_before + cur_prev_new

    is_start = get_bits_signed(s_mask, 1, bit_index)  # -1 true, 0 false
    is_left = get_bits_signed(l_mask, 1, bit_index)
    is_ref = get_bits_signed(w_mask, 1, bit_index)

    base_vertex = num_prev_new - 1
    read_base = page.disk_header_at + disk_header.index_data
    index_data = reader.unaligned_dword(read_base, (num_prev_ref + ~is_start) * 5)

    if is_start:
        minus_num_refs = (is_left << 1) + is_ref
        next_vertex = num_prev_new
        if minus_num_refs <= -1:
            x = base_vertex - (index_data & 31)
            index_data >>= 5
        else:
            x, next_vertex = next_vertex, next_vertex + 1
        if minus_num_refs <= -2:
            y = base_vertex - (index_data & 31)
            index_data >>= 5
        else:
            y, next_vertex = next_vertex, next_vertex + 1
        if minus_num_refs <= -3:
            z = base_vertex - (index_data & 31)
        else:
            z = next_vertex
        return x & 0xFFFFFFFF, y & 0xFFFFFFFF, z & 0xFFFFFFFF

    prev_bit_index = bit_index - 1
    is_prev_start = get_bits_signed(s_mask, 1, prev_bit_index)
    is_prev_head_ref = get_bits_signed(head_ref_vertex_mask, 1, prev_bit_index)
    num_prev_new_in_tri = is_prev_start & (
        3 - ((get_bits(l_mask, 1, prev_bit_index) << 1) | get_bits(w_mask, 1, prev_bit_index))
    )

    y = base_vertex + (is_prev_head_ref & (num_prev_new_in_tri - (index_data & 31)))
    z = num_prev_new + (is_ref & (-1 - get_bits(index_data, 5, 5)))

    # The third vertex is not encoded: it is found by scanning back for the previous
    # strip start or opposite-handed triangle and re-deriving its vertex numbering.
    search_mask = s_mask | (l_mask ^ (is_left & 0xFFFFFFFF))
    found_bit_index = first_bit_high(search_mask & prev_bits_mask)
    is_found_case_s = get_bits_signed(s_mask, 1, found_bit_index)

    found_prev_bits_mask = (1 << (found_bit_index & 31)) - 1
    f_cur_prev_ref = (popcount(sl_mask & found_prev_bits_mask) << 1) + popcount(
        w_mask & found_prev_bits_mask
    )
    f_cur_prev_new = (
        (popcount(s_mask & found_prev_bits_mask) << 1)
        + (found_bit_index & 0xFFFFFFFF)
        - f_cur_prev_ref
    )
    found_num_prev_new = prev_new_before + f_cur_prev_new
    found_num_prev_ref = prev_ref_before + f_cur_prev_ref

    found_num_refs = (get_bits(l_mask, 1, found_bit_index) << 1) + get_bits(
        w_mask, 1, found_bit_index
    )
    is_before_found_ref = get_bits(head_ref_vertex_mask, 1, found_bit_index - 1)

    read_offset = is_left if is_found_case_s else 1
    found_index_data = reader.unaligned_dword(read_base, (found_num_prev_ref - read_offset) * 5)
    found_index = (found_num_prev_new - 1) - get_bits(found_index_data, 5, 0)

    if is_found_case_s:
        condition = found_num_refs >= 1 - is_left
        found_new_vertex = found_num_prev_new + (is_left & (1 if found_num_refs == 0 else 0))
    else:
        condition = is_before_found_ref != 0
        found_new_vertex = found_num_prev_new - 1
    x = found_index if condition else found_new_vertex

    if is_left:
        y, z = z, y
    return x & 0xFFFFFFFF, y & 0xFFFFFFFF, z & 0xFFFFFFFF


def decode_indices(
    reader: PageReader, page: Page, cluster: Cluster, disk_header: ClusterDiskHeader
) -> None:
    tris = np.empty((cluster.num_tris, 3), dtype=np.int64)
    for t in range(cluster.num_tris):
        x, y, z = triangle_indices(reader, page, disk_header, cluster.index, t)
        # rotate to a canonical winding start, as the runtime does
        if y < min(x, z):
            x, y, z = y, z, x
        elif z < min(x, y):
            x, y, z = z, x, y
        tris[t] = (x, y, z)
    cluster.tris = tris


# ---------------------------------------------------------------- vertex maps


def vertex_ref_maps(
    reader: PageReader, page: Page, cluster: Cluster, disk_header: ClusterDiskHeader
) -> tuple[I64Grid, I64Grid]:
    """Split the cluster's vertex slots into 'stored here' and 'a reference to another
    cluster', from a 256-bit per-cluster bitmask."""
    base = page.disk_header_at + page.vertex_ref_bitmask_offset + cluster.index * 32
    dwords = [reader.u32(base + d * 4) for d in range(8)]

    prev_counts = [0] * 8
    running = 0
    for d in range(8):
        prev_counts[d] = running
        running += popcount(dwords[d])

    ref_to_vertex = np.empty(disk_header.num_vertex_refs, dtype=np.int64)
    num_non_ref = cluster.num_verts - disk_header.num_vertex_refs
    non_ref_to_vertex = np.empty(num_non_ref, dtype=np.int64)

    for v in range(cluster.num_verts):
        d, b = v >> 5, v & 31
        mask = dwords[d]
        num_prev_ref = popcount(get_bits(mask, b, 0)) + prev_counts[d]
        if mask & (1 << b):
            ref_to_vertex[num_prev_ref] = v
        else:
            non_ref_to_vertex[v - num_prev_ref] = v
    return ref_to_vertex, non_ref_to_vertex


def decode_positions(
    reader: PageReader, page: Page, cluster: Cluster, disk_header: ClusterDiskHeader
) -> I64Grid:
    """The non-referenced vertices' quantised positions.

    Stored as a delta stream in low/mid/high byte planes: plane k holds byte k of every
    value, so a 3-byte value costs three separate contiguous runs. Values are zigzag
    deltas from the previous vertex, seeded at the midpoint of each axis' range.
    """
    num_non_ref = cluster.num_verts - disk_header.num_vertex_refs
    if num_non_ref == 0:
        return np.zeros((0, 3), dtype=np.int64)

    bx, by, bz = cluster.pos_bits
    bytes_per_value = (max(bx, by, bz) + 7) // 8
    count = 3 * num_non_ref

    def plane(offset: int) -> U8Grid:
        at = page.disk_header_at + offset
        return np.frombuffer(reader.buf, dtype=np.uint8, count=count, offset=at)

    packed = plane(disk_header.low_bytes).astype(np.uint32)
    if bytes_per_value >= 2:
        packed = packed | (plane(disk_header.mid_bytes).astype(np.uint32) << 8)
    if bytes_per_value >= 3:
        packed = packed | (plane(disk_header.high_bytes).astype(np.uint32) << 16)
    packed = packed.reshape(num_non_ref, 3)

    # zigzag -> signed delta -> running sum, seeded at each axis' midpoint
    delta = (packed >> 1).astype(np.int64) ^ -(packed & 1).astype(np.int64)
    seed = np.array([1 << (bx - 1), 1 << (by - 1), 1 << (bz - 1)], dtype=np.int64)
    values = np.cumsum(delta, axis=0) + seed

    mask = np.array([(1 << bx) - 1, (1 << by) - 1, (1 << bz) - 1], dtype=np.int64)
    return (values & mask) + np.asarray(cluster.pos_start, dtype=np.int64)


# ---------------------------------------------------------------- driver


def decode_page(data: bytes, page_index: int) -> tuple[Page, PageReader, list[I64Grid]]:
    page, reader = parse_page_headers(data, page_index)
    parse_clusters(page, reader)

    ref_maps: list[I64Grid] = []
    for cluster in page.clusters:
        disk_header = page.headers[cluster.index]
        decode_indices(reader, page, cluster, disk_header)
        ref_to_vertex, non_ref_to_vertex = vertex_ref_maps(reader, page, cluster, disk_header)
        raw = np.zeros((cluster.num_verts, 3), dtype=np.int64)
        non_ref_pos = decode_positions(reader, page, cluster, disk_header)
        if len(non_ref_to_vertex):
            raw[non_ref_to_vertex] = non_ref_pos
        cluster.raw_pos = raw
        ref_maps.append(ref_to_vertex)
    return page, reader, ref_maps


def resolve_vertex_references(
    pages: list[Page],
    readers: list[PageReader],
    ref_maps: list[list[I64Grid]],
    page_index: int,
    page_dependencies: list[int],
    deps_start: int,
) -> None:
    """Fill the vertex slots that point at a vertex in another cluster, possibly in an
    earlier page. Only the quantised position is carried across; the destination
    cluster's own scale is applied when it is dequantised."""
    page, reader = pages[page_index], readers[page_index]
    for cluster in page.clusters:
        disk_header = page.headers[cluster.index]
        if disk_header.num_vertex_refs == 0:
            continue
        ref_to_vertex = ref_maps[page_index][cluster.index]
        prev = 0
        for ref in range(disk_header.num_vertex_refs):
            vertex_index = int(ref_to_vertex[ref])
            page_cluster_index = reader.u8(page.disk_header_at + disk_header.vertex_ref_data + ref)
            page_cluster_data = reader.u32(
                page.disk_header_at + disk_header.page_cluster_map + page_cluster_index * 4
            )
            parent_page_index = page_cluster_data >> MAX_CLUSTERS_PER_PAGE_BITS
            src_local_cluster = get_bits(page_cluster_data, MAX_CLUSTERS_PER_PAGE_BITS, 0)

            coded = reader.u8(
                page.disk_header_at + disk_header.vertex_ref_data + ref + page.num_vertex_refs
            )
            prev = ((coded >> 1) ^ -(coded & 1)) + prev  # zigzag delta, 5.4+
            src_vertex = prev & 0xFF

            if parent_page_index:
                parent = page_dependencies[deps_start + parent_page_index - 1]
                src_cluster = pages[parent].clusters[src_local_cluster]
            else:
                src_cluster = page.clusters[src_local_cluster]
            cluster.raw_pos[vertex_index] = src_cluster.raw_pos[src_vertex]


def decode_resource(resource: NaniteResource) -> DecodedMesh:
    """Decode every page of one mesh. Returns per-cluster geometry plus the leaf level
    assembled into a single (positions, triangles) pair in mesh local space."""
    pages: list[Page] = []
    readers: list[PageReader] = []
    ref_maps: list[list[I64Grid]] = []

    for span in resource.pages:
        page, reader, refs = decode_page(span.data, span.index)
        pages.append(page)
        readers.append(reader)
        ref_maps.append(refs)

    for span in resource.pages:
        resolve_vertex_references(
            pages, readers, ref_maps, span.index, resource.page_dependencies, span.deps_start
        )

    leaf_pos: list[F64Grid] = []
    leaf_tris: list[I64Grid] = []
    total_clusters = 0
    leaf_clusters = 0
    base = 0
    for page in pages:
        for cluster in page.clusters:
            total_clusters += 1
            if not cluster.is_leaf:
                continue
            leaf_clusters += 1
            leaf_pos.append(cluster.raw_pos.astype(np.float64) * cluster.pos_scale)
            leaf_tris.append(cluster.tris + base)
            base += cluster.num_verts

    positions = (
        np.concatenate(leaf_pos).astype(np.float32) if leaf_pos else np.zeros((0, 3), np.float32)
    )
    triangles = (
        np.concatenate(leaf_tris).astype(np.int64) if leaf_tris else np.zeros((0, 3), np.int64)
    )
    raw_vertices = len(positions)
    positions, triangles = weld(positions, triangles)
    return {
        "positions": positions,
        "triangles": triangles,
        "total_clusters": total_clusters,
        "leaf_clusters": leaf_clusters,
        "vertices_before_weld": raw_vertices,
    }


#: How finely positions are compared when welding cluster seams. Positions are
#: centimetres, so this is 10 micrometres -- four orders of magnitude below the finest
#: quantisation step Nanite uses on these meshes and eight below the 1 m grid any of it
#: ends up on, but far enough above float32's last bits to catch a vertex that two
#: clusters encoded through different origins.
WELD_DECIMALS = 3


def weld(positions: F32Grid, triangles: I64Grid) -> tuple[F32Grid, I64Grid]:
    """Merge the duplicate vertices Nanite leaves at every cluster boundary.

    A cluster is a self-contained strip: it carries its own copy of any vertex it shares
    with a neighbour, because the GPU wants to draw it without looking anywhere else. So a
    decode that concatenates clusters produces a triangle soup whose *topology* is right
    and whose *vertex identity* is not -- and every cluster seam then reads as a hole. That
    is why an unwelded decode of a closed rock reports thousands of boundary edges and
    zero closed manifolds: the falsifier fires on the assembly, not on the decode.

    The grouping is done on rounded positions and the **output keeps the original float32
    values** of the first vertex in each group, so nothing is quantised on the way through
    -- the rounding decides only which vertices are the same vertex.

    The triangles are unchanged in every sense that matters downstream: same count, same
    positions, same winding. Only the indices are renumbered.
    """
    if len(positions) == 0 or len(triangles) == 0:
        return positions, triangles
    key = np.round(positions.astype(np.float64), WELD_DECIMALS)
    _unique, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return positions[first], inverse.reshape(-1)[triangles].astype(np.int64)


def boundary_edges(triangles: I32Grid | I64Grid) -> int:
    """How many edges of this triangle soup are used by exactly one face.

    Zero on a closed 2-manifold, and thousands on a decode that got one bit wrong -- which
    is the point. It is also the finding that says why denser triangles cannot fix this
    field's tail: a *closed* rock includes its own underside, and max-Z over a closed mesh
    is precisely the operation that answers with the overhang.
    """
    if triangles.size == 0:
        return 0
    edges = np.concatenate(
        [triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]], axis=0
    )
    edges = np.sort(edges, axis=1)
    _unique, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum())


def identity_checks(resource: NaniteResource, decoded: DecodedMesh) -> list[str]:
    """What the mesh says about itself against what came out. Empty means agreement.

    Not a summary of the decode: two independent statements in the file -- the resource
    header's ``NumInputTriangles`` and ``NumClusters`` -- against two counts assembled from
    the pages. They can only agree if the page walk, the cluster unpack and the strip
    decode were all right, and they cost nothing to compare.
    """
    problems: list[str] = []
    if decoded["leaf_clusters"] and decoded["total_clusters"] != int(resource.clusters):
        problems.append(
            f"decoded {decoded['total_clusters']} clusters, the resource says "
            f"{int(resource.clusters)}"
        )
    if len(decoded["triangles"]) != int(resource.input_triangles):
        problems.append(
            f"assembled {len(decoded['triangles'])} leaf triangles, the resource says "
            f"{int(resource.input_triangles)} input triangles"
        )
    return problems
