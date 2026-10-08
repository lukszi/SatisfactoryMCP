"""LOD 0's UVs and vertex normals: where the buffer walk finds them and how they decode.

The crown sprite raster reads them (docs/map/light-and-crowns.md section 36). A synthetic
buffer section, laid out as ``_serialize_buffers`` walks a cooked one.
"""

from __future__ import annotations

import struct

import numpy as np

from satisfactory_mcp.core.gameassets import staticmesh as sm
from satisfactory_mcp.core.gameassets.meshdata import Lod, NaniteResource, RenderData

UV_SETS = 2


def _bulk(size: int, data: bytes) -> bytes:
    return struct.pack("<ii", size, len(data) // size) + data


def _buffers(positions, uvs, normals, *, full_uvs=False, count_uvs=None) -> bytes:
    """One LOD's buffers: positions, tangents and UVs, no colours, one 16-bit index buffer,
    editor data and the reversed and ray-tracing buffers stripped."""
    n = len(positions)
    out = struct.pack("<BB", 1, 12)
    out += struct.pack("<ii", 12, n) + _bulk(12, np.asarray(positions, "<f4").tobytes())
    out += struct.pack("<BB", 0, 0) + struct.pack("<ii", UV_SETS, n) + struct.pack("<ii", 0, 0)
    basis = np.zeros((n, 2, 4), np.int8)
    basis[:, 1, :3] = np.round(np.asarray(normals) * 127)
    out += _bulk(8, basis.tobytes())
    both = np.stack([uvs, np.asarray(uvs) * 7], axis=1)  # set 1 is a decoy
    raw = both.astype("<f4" if full_uvs else "<f2").tobytes()
    if count_uvs is not None:
        raw = raw[: count_uvs * (8 if full_uvs else 4)]
    out += _bulk(8 if full_uvs else 4, raw)
    out += struct.pack("<BB", 0, 0) + struct.pack("<ii", 4, 0)
    index = struct.pack("<i", 0) + _bulk(2, np.array([0, 1, 2], "<u2").tobytes()) + b"\0" * 4
    out += index * 2  # the buffer, then its depth-only twin
    out += struct.pack("<iii", 0, 0, 0)  # the LOD's area sampler
    return out


def _walk(tail: bytes) -> tuple[Lod, RenderData]:
    lod = Lod(index=0, sections=[], max_deviation=0.0, cooked_out=False, inlined=True)
    sm._serialize_buffers(sm.Cursor(tail), lod)
    parsed: RenderData = {
        "lod_count_at": 0,
        "lods": [lod],
        "num_inlined_lods": 1,
        "nanite": NaniteResource(present=False),
        "nanite_at": 0,
        "tail_bytes": len(tail),
    }
    return lod, parsed


POSITIONS = [[0, 0, 0], [100, 0, 0], [0, 100, 50]]
UVS = np.array([[0.0, 0.0], [1.0, 0.25], [0.5, 1.0]], np.float32)
NORMALS = np.array([[0, 0, 1], [0, -1, 0], [0.6, 0, 0.8]], np.float32)


def test_the_walk_records_where_the_uvs_and_the_basis_are_and_reads_set_0():
    tail = _buffers(POSITIONS, UVS, NORMALS)
    lod, parsed = _walk(tail)
    assert (lod.uv_sets, lod.uv_stride, lod.tangent_stride) == (UV_SETS, 4, 8)
    found = sm.lod0_surface(tail, parsed)
    assert found is not None
    uvs, normals = found
    assert uvs.dtype == np.float32 and np.array_equal(uvs, UVS), "vertex-major, set 0"
    assert np.allclose(normals, NORMALS, atol=0.01)


def test_full_precision_uvs_read_as_float32():
    tail = _buffers(POSITIONS, UVS + 3.3, NORMALS, full_uvs=True)
    lod, parsed = _walk(tail)
    assert lod.uv_stride == 8
    found = sm.lod0_surface(tail, parsed)
    assert found is not None and np.array_equal(found[0], UVS + np.float32(3.3))


def test_a_texcoord_count_that_disagrees_records_no_surface_and_the_walk_goes_on():
    tail = _buffers(POSITIONS, UVS, NORMALS, count_uvs=5)
    lod, parsed = _walk(tail)
    assert lod.indices_at > 0, "the index buffer is still found"
    assert lod.uv_sets == 0 and sm.lod0_surface(tail, parsed) is None
