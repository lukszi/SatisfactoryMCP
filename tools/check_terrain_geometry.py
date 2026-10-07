"""Score a candidate cliff geometry against the shipped field, on the shipped field's rules.

    uv run --extra gen python tools/check_terrain_geometry.py

The heightfield's cliff layer is built from one geometry source; this asks whether a
different source would be better, and by how much. It calls the same rasteriser, transforms
and cull rules the field is built with (``mapgen.gamedata.meshes`` and ``mapgen.gamedata.level.sweep``),
so the geometry dict is the only thing that differs between a candidate and the field that
ships. Nothing here writes to ``data/local``.

Three rungs, one variable between them:

=========  ==============================================================================
rung        geometry
=========  ==============================================================================
``hull``    the cooked Chaos collision trimesh. What the shipped field is built from.
``lod0``    the render chain's LOD 0: about 2.5x the triangles, already parsed, free.
``best``    the Nanite leaf level where a mesh has one, LOD 0 where it does not.
=========  ==============================================================================

25 of this build's rock meshes carry no Nanite resource at all -- sea rocks, corals, part
of the cave interior set -- so ``best`` falls back to LOD 0 for them; a Nanite-only layer
loses about 365,000 texels against the hull layer while still looking like a field.

Only meshes that decode a collision hull are scored, on every rung, because coverage and
resolution are different questions. The 626 static resource nodes are always scored; a
foliage set passed with ``--foliage`` is 1,000x larger and the only one dense enough on
the cliff province to separate rungs half a point apart. Every table says whether it is
raw or median-detrended: on the cliff province the datum is -0.14 m, which moves the
median 3 mm and the p90 13 cm.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import NotRequired, TypeAlias, TypedDict

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT / "tools" / "mapgen" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mapgen.common import LOCAL_DIR, base_parser, require_gen
from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.level.landscape import LandscapeFrame, drop_offsets, landscape_frame
from mapgen.gamedata.level.sweep import Sweep, sweep_levels
from mapgen.gamedata.meshes import ROCK_DIRS, CookedMesh, MeshBounds
from mapgen.gamedata.nodes import node_rows
from mapgen.gamedata.rocks.cliffs import rasterise_cliffs
from mapgen.terrain.heightfield.validate import VALIDATION_TRIM
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I64Grid
from satisfactory_mcp.core.gameassets import nanite, staticmesh
from satisfactory_mcp.core.gameassets.container import CONTAINER, open_container, paks_dir
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
    ZenExport,
)
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The rungs, in the order the table prints them. ``hull`` first because it is what ships.
RUNGS = ("hull", "lod0", "best")

#: A mesh's triangles as ``(vertices, triangles)``, mesh-local centimetres.
Mesh: TypeAlias = tuple[F32Grid, I64Grid]

#: One rung's mesh, ready to rasterise: ``(vertices, triangles, low, high)``, bounds padded.
Geometry: TypeAlias = CookedMesh


class MeshRungs(TypedDict):
    """One mesh's three readings, with what went wrong reading each."""

    package: str
    bounds: staticmesh.Bounds
    hull: tuple[F32Grid, I64Grid, float] | None
    hull_note: str | None
    parse_error: NotRequired[str]
    lod0: NotRequired[Mesh]
    nanite: NotRequired[Mesh]
    nanite_problems: NotRequired[list[str]]
    nanite_clusters: NotRequired[int]


class RungRead(TypedDict):
    """``read_rungs``: every rock mesh read, how many were wanted, and why the rest were not."""

    meshes: dict[str, MeshRungs]
    wanted: int
    notes: dict[str, str]
    seconds: float


#: The five numbers of a score. The functional form, because two keys are not identifiers.
Metrics = TypedDict(
    "Metrics",
    {
        "median_abs_m": float,
        "p90_abs_m": float,
        "trimRMS90_m": float,
        "frac_lt_1m": float,
        "frac_lt_0.25m": float,
    },
)


class Unscored(TypedDict):
    """A probe set no candidate value falls on."""

    n: int
    coverage: float


#: A score: the metrics raw at the top level, then median-detrended under ``detrended``.
Scored = TypedDict(
    "Scored",
    {
        "n": int,
        "coverage": float,
        "offset_m": float,
        "median_abs_m": float,
        "p90_abs_m": float,
        "trimRMS90_m": float,
        "frac_lt_1m": float,
        "frac_lt_0.25m": float,
        "detrended": Metrics,
    },
)


class RungScore(TypedDict):
    """One rung of the table: what was rasterised, its grain, and a score per probe set."""

    meshes: int
    source_triangles: int
    rasterised_triangles: int
    placements: int
    covered_texels: int
    grain: dict[str, float]
    scores: dict[str, Scored | Unscored]


class GeometryTable(TypedDict):
    """What ``--out`` writes."""

    rungs: dict[str, RungScore]
    meshes_with_a_hull: int
    meshes_with_nanite: int


# --- Reading all three sources out of one open of each mesh. --------------------------


def read_rungs(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    meshes: list[str],
    progress: bool = True,
) -> RungRead:
    """Every rock mesh's hull, LOD0 and Nanite leaf, from one read of each package.

    One read, three answers, so the three rungs cannot disagree about which asset they
    were looking at.
    """
    wanted = [m for m in meshes if any(d in m for d in ROCK_DIRS)]
    out: dict[str, MeshRungs] = {}
    notes: dict[str, str] = {}
    started = time.time()
    for count, mesh in enumerate(wanted):
        package = index.path_for(mesh)
        if not package:
            notes[mesh] = "not in the container"
            continue
        try:
            view = PackageView(store.read_path(package), scripts)
        except Exception as exc:
            notes[mesh] = f"unreadable package: {type(exc).__name__}"
            continue
        export = staticmesh.static_mesh_export(view)
        if export is None:
            notes[mesh] = "no StaticMesh export"
            continue
        bounds = staticmesh.extended_bounds(view, export)
        if bounds is None:
            notes[mesh] = "no ExtendedBounds, so no decode could be checked"
            continue
        out[mesh] = _read_mesh_rungs(store, view, export, package, bounds)
        if progress and count % 25 == 0:
            print(f"  {count}/{len(wanted)} meshes, {time.time() - started:.0f}s", flush=True)
    return {"meshes": out, "wanted": len(wanted), "notes": notes, "seconds": time.time() - started}


def _read_mesh_rungs(
    store: IoStore, view: PackageView, export: ZenExport, package: str, bounds: staticmesh.Bounds
) -> MeshRungs:
    """One mesh's hull, LOD 0 and Nanite leaf, with what went wrong reading each."""
    low, high = bounds
    hull, why = staticmesh.collision_hull(view, low, high)
    row: MeshRungs = {
        "package": package,
        "bounds": (low, high),
        "hull": None if hull is None else (hull[0], hull[1].astype(np.int64), hull[2]),
        "hull_note": why,
    }

    tail = staticmesh.render_tail(view, export)
    try:
        parsed = staticmesh.parse_render_data(tail)
    except staticmesh.ParseError as exc:
        row["parse_error"] = str(exc)
        parsed = None
    if parsed is not None:
        got = staticmesh.lod0_buffers(tail, parsed)
        if got is not None:
            row["lod0"] = (got[0].astype(np.float32), got[1])
        resource = staticmesh.load_nanite(store, package, view, parsed, tail)
        if resource is not None:
            decoded = nanite.decode_resource(resource)
            problems = staticmesh.page_table_problems(
                resource, staticmesh.bulk_size(view, resource)
            )
            problems += nanite.identity_checks(resource, decoded)
            row["nanite"] = (decoded["positions"], decoded["triangles"])
            row["nanite_problems"] = problems
            row["nanite_clusters"] = decoded["total_clusters"]
    return row


def geometry_for(rung: str, read: RungRead) -> dict[str, Geometry]:
    """``{mesh: (verts, tris, low, high)}`` for one rung, over the hull-equivalent set only.

    A mesh with no hull is absent from every rung, not just from ``hull``, which is what
    keeps the three rows a comparison of resolution rather than of coverage.
    """
    out: dict[str, Geometry] = {}
    for mesh, row in read["meshes"].items():
        hull = row["hull"]
        if hull is None:
            continue
        low, high = row["bounds"]
        pad = hull[2]
        if rung == "hull":
            verts, tris = hull[0], hull[1]
        elif rung == "lod0":
            if "lod0" not in row:
                continue
            verts, tris = row["lod0"]
        else:
            source = row.get("nanite") or row.get("lod0")
            if source is None:
                continue
            verts, tris = source
        out[mesh] = CookedMesh(
            np.ascontiguousarray(verts, dtype=np.float32),
            np.ascontiguousarray(tris, dtype=np.int64),
            low - pad,
            high + pad,
        )
    return out


# --- The score vocabulary. One definition, used by every table this prints. -----------


def _metrics(sorted_abs: F64Grid) -> Metrics:
    cut = max(1, int(sorted_abs.size * VALIDATION_TRIM))
    return {
        "median_abs_m": round(float(np.median(sorted_abs)), 4),
        "p90_abs_m": round(float(np.percentile(sorted_abs, 90)), 4),
        "trimRMS90_m": round(float(np.sqrt((sorted_abs[:cut] ** 2).mean())), 4),
        "frac_lt_1m": round(float((sorted_abs < 1.0).mean()), 4),
        "frac_lt_0.25m": round(float((sorted_abs < 0.25).mean()), 4),
    }


def score(truth_m: F64Grid, field_m: F64Grid, n_total: int | None = None) -> Scored | Unscored:
    """The five numbers, raw at the top level and median-detrended underneath.

    Both conventions are live in this project: the cliff baseline is raw, and ``meta.json``
    validates its node set detrended.
    """
    total = len(truth_m) if n_total is None else n_total
    ok = np.isfinite(field_m) & np.isfinite(truth_m)
    if not ok.any():
        return {"n": 0, "coverage": 0.0}
    error = truth_m[ok] - field_m[ok]
    offset = float(np.median(error))
    return {
        "n": int(error.size),
        "coverage": round(float(error.size / total), 6),
        "offset_m": round(offset, 4),
        **_metrics(np.sort(np.abs(error))),
        "detrended": _metrics(np.sort(np.abs(error - offset))),
    }


def format_score_row(tag: str, scored: Scored | Unscored) -> str:
    if "median_abs_m" not in scored:
        return f"{tag:24s}  (no probes)"
    return (
        f"{tag:24s} n={scored['n']:>9,}  med {scored['median_abs_m']:7.4f}  "
        f"p90 {scored['p90_abs_m']:8.2f}  trimRMS90 {scored['trimRMS90_m']:7.3f}  "
        f"<1m {scored['frac_lt_1m']:.4f}  <0.25m {scored['frac_lt_0.25m']:.4f}"
    )


def world_to_texel(x_cm: F64Grid, y_cm: F64Grid) -> tuple[I64Grid, I64Grid, BoolMask]:
    """World centimetres -> the field's ``(row, col)`` texels, and which fall on the grid."""
    col = np.round((np.asarray(x_cm) - ORIGIN_X_CM) / SPACING_CM).astype(np.int64)
    row = np.round((np.asarray(y_cm) - ORIGIN_Y_CM) / SPACING_CM).astype(np.int64)
    on_grid = (col >= 0) & (col < GRID_PX) & (row >= 0) & (row < GRID_PX)
    return row, col, on_grid


def sample(grid_cm: F32Grid, x_cm: F64Grid, y_cm: F64Grid) -> F64Grid:
    """The candidate raster read in metres at world coordinates, NaN where it is silent."""
    row, col, on_grid = world_to_texel(x_cm, y_cm)
    value = grid_cm[np.clip(row, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1)]
    return np.where(on_grid & np.isfinite(value), value / 100.0, np.nan)


# --- Density, which is the claim this whole exercise can actually make. ---------------


#: Metres of world per output pixel over the 7,500 m box, at 16384 px and 32768 px.
#: Written as arithmetic so they cannot drift from the renders.
Z6_TEXEL_M = 7500.0 / 16384
Z7_TEXEL_M = 7500.0 / 32768

#: 100 geometric bins per decade, 1 mm to 1 km. Geometric is load-bearing: see
#: :func:`world_grain`.
GRAIN_DECADES = (-3, 3)
GRAIN_PER_DECADE = 100


def world_grain(
    geometry: dict[str, Geometry], placements: F64Grid, meshes: list[str]
) -> dict[str, float]:
    """Placement-weighted world-space triangle edge length: percentiles and the z6 share.

    Measured after the placement transform, never before: placement scale runs from a
    median of 1.07 to a p90 of 2.50 on this world, so the same mesh is fine enough for a
    0.458 m texel in one placement and not in another.

    The weights are float64. A float32 cumulative sum over 1.2e9 edge lengths saturates at
    2**24 and returns quantiles about ten times too small, silently.

    The bins are geometric, which is what makes 20,000 placements affordable: scaling every
    edge of a mesh is a *shift* along a geometric axis, so each mesh is histogrammed once
    and each placement costs one fractional shift of 600 bins. The fractional part is split
    linearly between neighbouring bins, so the answer is not quantised to the bin ratio.
    """
    lo, hi = GRAIN_DECADES
    count = (hi - lo) * GRAIN_PER_DECADE
    bins = np.logspace(lo, hi, count + 1)
    centres = np.sqrt(bins[:-1] * bins[1:])
    per_bin = np.log10(bins[1] / bins[0])
    weight = np.zeros(count, np.float64)
    counted = 0

    mesh_id_of = {mesh: i for i, mesh in enumerate(meshes)}
    for mesh, (verts, tris, _low, _high) in geometry.items():
        rows = placements[placements[:, 0] == mesh_id_of.get(mesh, -1)]
        if not len(rows):
            continue
        a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
        local = np.concatenate(
            [
                np.linalg.norm(b - a, axis=1),
                np.linalg.norm(c - b, axis=1),
                np.linalg.norm(a - c, axis=1),
            ]
        ).astype(np.float64)
        base, _edges = np.histogram(local / 100.0, bins=bins)  # unit scale, metres
        base = base.astype(np.float64)
        # An anisotropic scale stretches edges by direction; the mean of |scale| is the
        # scalar a pooled percentile can honestly use.
        for placement in rows:
            shift = np.log10(max(float(np.abs(placement[8:11]).mean()), 1e-9)) / per_bin
            whole, frac = int(np.floor(shift)), shift - np.floor(shift)
            for offset, share in ((whole, 1.0 - frac), (whole + 1, frac)):
                if share <= 0.0:
                    continue
                if offset >= 0:
                    weight[offset:] += share * base[: count - offset]
                else:
                    weight[:offset] += share * base[-offset:]
            counted += 1

    total = weight.sum()
    if total == 0:
        return {"placements": 0}
    cumulative = np.cumsum(weight) / total
    quantiles = {
        f"p{int(q * 100):02d}_m": round(float(centres[np.searchsorted(cumulative, q)]), 4)
        for q in (0.05, 0.25, 0.50, 0.75, 0.95)
    }
    return {
        "placements": counted,
        "edges": round(total),
        **quantiles,
        "frac_le_z6_texel": round(float(cumulative[np.searchsorted(centres, Z6_TEXEL_M)]), 4),
        "frac_le_z7_texel": round(float(cumulative[np.searchsorted(centres, Z7_TEXEL_M)]), 4),
    }


# --------------------------------------------------------------------------------------


def main() -> int:
    parser: argparse.ArgumentParser = base_parser((__doc__ or "").partition("\n")[0])
    parser.add_argument(
        "--rungs", default=",".join(RUNGS), help=f"which of {','.join(RUNGS)} to score"
    )
    parser.add_argument(
        "--foliage",
        type=Path,
        help="an (n, 3) .npy of ground-snapped world-cm probe positions, optional",
    )
    parser.add_argument("--foliage-mask", type=Path, help="a bool .npy selecting rows of it")
    parser.add_argument(
        "--field",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="the shipped field, whose provenance byte defines 'the cliff province'",
    )
    parser.add_argument("-o", "--out", type=Path, help="write the whole table as JSON here")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    require_gen("ooz")
    paks = paks_dir(args.game)
    if not (paks / f"{CONTAINER}.utoc").exists():
        print(f"no {CONTAINER}.utoc under {paks}")
        return 1
    loud = not args.quiet

    store = open_container(args.game)
    scripts = ScriptObjects(paks, oodle_decompress)
    index = AssetIndex(store)
    classes = ClassFacts(store, index)

    print("sweeping the world for placements and the landscape frame")
    sweep = sweep_levels(store, scripts, classes, MeshBounds(store, scripts, index), loud)
    frame = landscape_frame(sweep)

    print("reading every rock mesh's hull, LOD0 and Nanite leaf")
    read = read_rungs(store, scripts, index, sweep["meshes"], loud)
    hulls, nanites = _report_rungs_read(read)

    field = hf.load_field(args.field)
    if field is None:
        print(f"no shipped field at {args.field}; the cliff province cannot be defined")
        return 2
    probes, province = load_probes(field, args.foliage, args.foliage_mask)

    table: GeometryTable = {"rungs": {}, "meshes_with_a_hull": hulls, "meshes_with_nanite": nanites}
    for rung in args.rungs.split(","):
        if rung not in RUNGS:
            print(f"unknown rung {rung!r}")
            return 2
        table["rungs"][rung] = score_rung(rung, read, sweep, frame, probes, province, loud)

    if args.out:
        args.out.write_text(json.dumps(table, indent=1), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


def _report_rungs_read(read: RungRead) -> tuple[int, int]:
    """Print what the mesh read found; ``(meshes with a hull, meshes with Nanite)``."""
    hulls = sum(1 for r in read["meshes"].values() if r["hull"] is not None)
    nanites = sum(1 for r in read["meshes"].values() if "nanite" in r)
    broken = {
        m: problems for m, r in read["meshes"].items() if (problems := r.get("nanite_problems"))
    }
    print(
        f"  {len(read['meshes'])}/{read['wanted']} meshes opened in {read['seconds']:.0f}s: "
        f"{hulls} with a cooked hull, {nanites} with Nanite, {len(broken)} with a page or "
        f"identity problem"
    )
    for mesh, problems in broken.items():
        print(f"    {mesh.rsplit('/', 1)[-1]}: {'; '.join(problems)}")

    boundary_edges_by_mesh = {
        mesh: nanite.boundary_edges(decoded[1])
        for mesh, r in read["meshes"].items()
        if (decoded := r.get("nanite")) is not None
    }
    closed_mesh_count = sum(1 for v in boundary_edges_by_mesh.values() if v == 0)
    print(
        f"  closed 2-manifolds among the Nanite decodes: "
        f"{closed_mesh_count}/{len(boundary_edges_by_mesh)}"
    )
    return hulls, nanites


def load_probes(
    field: hf.Field, foliage: Path | None, foliage_mask: Path | None
) -> tuple[dict[str, F64Grid], dict[str, BoolMask]]:
    """The probe sets, and per set which probes stand on the shipped field's cliff province."""
    nodes = node_rows()
    probes: dict[str, F64Grid] = {
        "nodes": np.array([[n["x"], n["y"], n["z"]] for n in nodes], float)
    }
    if foliage is not None:
        points = np.load(foliage)
        if foliage_mask is not None:
            points = points[np.load(foliage_mask)]
        probes["foliage"] = points
    provenance = field.provenance_plane
    province: dict[str, BoolMask] = {}
    for name, pts in probes.items():
        row, col, _on_grid = world_to_texel(pts[:, 0], pts[:, 1])
        texels = (np.clip(row, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1))
        # Both cliff values: testing ``== PROV_CLIFF`` scores a v3 field on a quarter of
        # the probes and calls it the same measurement.
        province[name] = np.isin(provenance[texels], hf.PROV_CLIFF_VALUES)
        print(f"  {name}: {len(pts)} probes, {int(province[name].sum())} on the cliff province")
    return probes, province


def score_rung(
    rung: str,
    read: RungRead,
    sweep: Sweep,
    frame: LandscapeFrame,
    probes: dict[str, F64Grid],
    province: dict[str, BoolMask],
    loud: bool,
) -> RungScore:
    """Rasterise one rung's geometry into a whole-world field and score it on every probe set."""
    geometry = geometry_for(rung, read)
    triangles = sum(len(t) for _v, t, _lo, _hi in geometry.values())
    print(f"\nrung {rung}: {len(geometry)} meshes, {triangles} source triangles")
    cliffs = rasterise_cliffs(sweep, geometry, frame, loud)
    whole = np.full((GRID_PX, GRID_PX), np.nan, np.float32)
    dx, dy = drop_offsets(frame)
    whole[dy : dy + frame["height"], dx : dx + frame["width"]] = cliffs["z_cm"]
    entry: RungScore = {
        "meshes": len(geometry),
        "source_triangles": triangles,
        "rasterised_triangles": cliffs["triangles"],
        "placements": cliffs["placements_used"],
        "covered_texels": int(np.isfinite(whole).sum()),
        "grain": world_grain(geometry, sweep["placements"], sweep["meshes"]),
        "scores": {},
    }
    print(f"  {entry['covered_texels']} texels covered; grain {entry['grain']}")
    for name, pts in probes.items():
        pick = province[name]
        scored = score(
            pts[pick, 2] / 100.0,
            sample(whole, pts[pick, 0], pts[pick, 1]),
            int(pick.sum()),
        )
        entry["scores"][name] = scored
        print("  " + format_score_row(f"{name} (cliff province)", scored))
    return entry


if __name__ == "__main__":
    raise SystemExit(main())
