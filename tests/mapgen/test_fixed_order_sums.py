"""A pixel's colour sums are elementwise in one fixed order, so a band draws the same bytes at
any width, in any pieces, on any threads, and with any BLAS.

docs/map/renders.md section 40, "Fixed-order sums". Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from mapgen import colour
from mapgen.colour import (
    by_luminance,
    linear_from_oklab,
    luminance,
    oklab,
    srgb_to_linear,
    through_matrix,
    tone,
)
from mapgen.palette.painted import band as painted_band
from mapgen.palette.painted.surfaces import _mean3x3
from mapgen.palette.relief import relief_colours
from mapgen.palette.styles import PAINTED_PALETTE
from tests.support.map_scenes import painted_ground_stub, relief_ground

MAPGEN = Path(colour.__file__).parent

#: Wider than 16384 columns, where OpenBLAS summed a row's luminance in another order.
WIDE = 16_500

#: Column pieces and thread counts, each drawn against the whole row.
PIECES = (16_385, 2_049, 509)
THREADS = (1, 4)

f32 = np.float32


def _bits(array):
    return np.asarray(array).dtype, np.shape(array), np.asarray(array).tobytes()


def _scalar_sum(c, weights):
    """``weighted_channels``'s order, one float32 scalar at a time."""
    flat = np.asarray(c, np.float32).reshape(-1, 3)
    out = np.empty(len(flat), np.float32)
    w0, w1, w2 = (f32(w) for w in weights)
    for k, (c0, c1, c2) in enumerate(flat):
        out[k] = f32(f32(f32(c0 * w0) + f32(c1 * w1)) + f32(c2 * w2))
    return out.reshape(np.shape(c)[:-1])


def _in_pieces(draw, width, piece, threads, halo=0):
    """``draw(lo, hi)`` over column pieces ``piece`` wide on ``threads``, cropped and joined."""
    edges = [(lo, min(lo + piece, width)) for lo in range(0, width, piece)]

    def one(edge):
        lo, hi = edge
        a, b = max(lo - halo, 0), min(hi + halo, width)
        return draw(a, b)[:, lo - a : hi - a]

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return np.concatenate(list(pool.map(one, edges)), axis=1)


def test_the_sums_are_the_scalar_reference_to_the_bit():
    rng = np.random.default_rng(42)
    c = (rng.random((5, 211, 3)) * 2.0 - 0.25).astype(np.float32)
    assert _bits(luminance(c)) == _bits(_scalar_sum(c, colour.LUMA))
    for matrix in (colour._M1, colour._M2, colour._M1_INV, colour._M2_INV):
        got = through_matrix(c, matrix)
        want = np.stack([_scalar_sum(c, row) for row in matrix], -1)
        assert _bits(got) == _bits(want)
    lms = np.cbrt(np.stack([_scalar_sum(c.clip(0, None), row) for row in colour._M1], -1))
    assert _bits(oklab(c.clip(0, None))) == _bits(through_matrix(lms, colour._M2))
    assert luminance(c.astype(np.float64)).dtype == np.float64, "a float64 colour stays float64"


def test_the_sums_are_the_luminance_and_the_inverse_matrices_are_inverses():
    rng = np.random.default_rng(5)
    c = rng.random((1000, 3)).astype(np.float32)
    exact = c.astype(np.float64) @ colour.LUMA.astype(np.float64)
    assert np.abs(luminance(c) - exact).max() <= 2 * np.finfo(np.float32).eps
    for m, inverse in ((colour._M1, colour._M1_INV), (colour._M2, colour._M2_INV)):
        product = m.astype(np.float64) @ inverse.astype(np.float64)
        np.testing.assert_allclose(product, np.eye(3), atol=1e-5)
    back = linear_from_oklab(oklab(c))
    np.testing.assert_allclose(back, c, atol=2e-5)


@pytest.mark.parametrize(
    ("given", "bits"),
    [
        ([0.704345703125, 0.905029296875, 0.72119140625], [0x3F595E04]),
        ([0.226318359375, 0.853759765625, 0.52587890625], [0x3F325A75]),
    ],
)
def test_a_pinned_luminance(given, bits):
    """Colours whose fused multiply-adds, in either order OpenBLAS took, round elsewhere."""
    got = luminance(np.array(given, np.float32))
    assert np.atleast_1d(np.asarray(got, np.float32)).view(np.uint32).tolist() == bits


def test_a_pinned_matrix_product():
    c = np.array([0.675537109375, 0.798095703125, 0.5771484375], np.float32)
    pins = {"_M2": [0x3F45F098, 0xBEAF17C1, 0x3E33B644],
            "_M1_INV": [0x3E7D5CA6, 0x3F83B534, 0x3ED7B6E0]}  # fmt: skip
    for name, bits in pins.items():
        assert through_matrix(c, getattr(colour, name)).view(np.uint32).tolist() == bits, name


def test_the_colour_sums_read_no_width_no_piece_and_no_thread():
    rng = np.random.default_rng(11)
    c = (rng.random((3, 33_000, 3)) * 1.6).astype(np.float32)
    knee, white = 0.6, 1.6

    def toned(lo, hi):
        return by_luminance(c[:, lo:hi], tone, knee, white)

    def round_trip(lo, hi):
        return linear_from_oklab(oklab(c[:, lo:hi]))

    for draw in (toned, round_trip):
        whole = draw(0, c.shape[1])
        for piece in (32_768, 16_385, 7):
            for threads in THREADS:
                got = _in_pieces(draw, c.shape[1], piece, threads)
                assert _bits(got) == _bits(whole), (draw.__name__, piece, threads)


def _painted_band():
    """A one-row painted band ``WIDE`` long, every plane varying along it, half under water."""
    rng = np.random.default_rng(3)
    shape = (1, WIDE)
    albedo = [rng.uniform(0.1, 0.7, shape).astype(np.float32) for _ in range(3)]
    zero = np.zeros(shape, np.float32)
    cover = np.where(rng.random(shape) < 0.5, 0.0, rng.random(shape)).astype(np.float32)
    water = {"depth_m": rng.uniform(0.0, 4.0, shape).astype(np.float32), "cover": cover,
             "edge": rng.random(shape).astype(np.float32) * 0.2,
             "ocean": (cover > 0.5).astype(np.float32), "above_m": zero + 1.0,
             "below_m": zero + 0.5}  # fmt: skip
    scene = {"z_m": rng.uniform(-20.0, 80.0, shape).astype(np.float32),
             "borrow": rng.uniform(0.8, 1.2, shape).astype(np.float32),
             "ndl": rng.uniform(0.5, 1.6, shape).astype(np.float32), "ndl_flat": f32(1.0),
             "rock_weight": zero, "mesh_weight": None, "water": water,
             "water_optics": None}  # fmt: skip
    return scene, albedo


def _cut(planes, lo, hi):
    if isinstance(planes, dict):
        return {k: _cut(v, lo, hi) for k, v in planes.items()}
    if isinstance(planes, np.ndarray) and planes.ndim == 2:
        return planes[:, lo:hi]
    return planes


def test_the_painted_band_is_the_same_bytes_whole_and_in_pieces_on_threads():
    scene, albedo = _painted_band()

    def draw(lo, hi):
        ground = painted_ground_stub(hi - lo)
        ground.albedo = [plane[:, lo:hi] for plane in albedo]
        return painted_band.painted_colours(_cut(scene, lo, hi), ground, _same, _same)

    whole = draw(0, WIDE)
    shown = luminance(srgb_to_linear(whole))
    assert (shown > PAINTED_PALETTE["tone"]["knee"]).any(), "the shoulder reads the luminance"
    for piece in PIECES:
        for threads in THREADS:
            got = _in_pieces(draw, WIDE, piece, threads)
            assert _bits(got) == _bits(whole), (piece, threads)


def test_the_relief_band_is_the_same_bytes_whole_and_in_pieces_on_threads():
    rng = np.random.default_rng(8)
    shape = (24, WIDE)
    ground = relief_ground("relief")
    cover = np.where(rng.random(shape) < 0.6, 0.0, rng.random(shape)).astype(np.float32)
    water = {"cover": cover, "depth": rng.random(shape).astype(np.float32),
             "depth_m": rng.uniform(0.0, 6.0, shape).astype(np.float32),
             "ocean": (rng.random(shape) < 0.5).astype(np.float32)}  # fmt: skip
    scene = {"z_m": rng.uniform(0.0, 100.0, shape).astype(np.float32), "spacing_m": 1.0,
             "borrow": rng.uniform(0.8, 1.2, shape).astype(np.float32), "water": water,
             "unlit": False}  # fmt: skip

    def draw(lo, hi):
        return relief_colours(_cut(scene, lo, hi), ground, _same, _same)

    whole = draw(0, WIDE)
    for piece in PIECES:
        got = _in_pieces(draw, WIDE, piece, 4, halo=8)
        assert _bits(got) == _bits(whole), piece


def _same(plane):
    return plane


def test_the_three_by_three_mean_is_scipy_s_where_its_running_sum_is_exact():
    rng = np.random.default_rng(1)
    a = rng.random((40, 300)).astype(np.float32)
    for mode in ("reflect", "nearest"):
        assert _bits(_mean3x3(a)) == _bits(ndimage.uniform_filter(a, 3, mode=mode))
    tiny = np.where(rng.random(a.shape) < 0.5, a * f32(1e-12), a).astype(np.float32)
    whole = _mean3x3(tiny)
    for lo, hi in ((0, 64), (37, 101), (151, 300)):
        got = _mean3x3(tiny[:, lo:hi])[:, 1:-1]
        assert _bits(got) == _bits(whole[:, lo + 1 : hi - 1]), "no running sum across a row"


#: Where the draw and paint path reaches BLAS or LAPACK, by name: none but the open sea's
#: sparse membrane, whose solve is an open question (docs/map/renders.md section 40,
#: "Fixed-order sums").
BLAS_NAMES = {"dot", "vdot", "inner", "matmul", "tensordot", "einsum", "inv", "solve", "lstsq"}
KNOWN = {"palette/water/open_sea.py:membrane"}


def _blas_sites(path: Path) -> set[str]:
    """``file:function`` of every ``@`` and every call by one of ``BLAS_NAMES`` in ``path``."""
    rel = path.relative_to(MAPGEN).as_posix()
    found: set[str] = set()

    def visit(node: ast.AST, where: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            where = node.name
        matmul = isinstance(node, ast.BinOp | ast.AugAssign) and isinstance(node.op, ast.MatMult)
        called = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in BLAS_NAMES)  # fmt: skip
        if matmul or called:
            found.add(f"{rel}:{where}")
        for child in ast.iter_child_nodes(node):
            visit(child, where)

    visit(ast.parse(path.read_text(encoding="utf-8")), "<module>")
    return found


def test_the_draw_and_paint_path_calls_no_blas():
    paths = [MAPGEN / "colour.py", MAPGEN / "terrain" / "sample.py"]
    for package in ("render", "palette", "lighting"):
        paths += sorted((MAPGEN / package).rglob("*.py"))
    found = set().union(*(_blas_sites(path) for path in paths))
    assert found == KNOWN
