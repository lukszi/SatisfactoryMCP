"""Score a candidate cliff geometry against the shipped field, on the shipped field's rules.

    uv run --extra gen python tools/check_terrain_geometry.py

``tools/gen_world_heightmap.py`` writes one field from one geometry source; this asks
whether a different source would be better, and by how much. It imports that generator and
calls its rasteriser, its transforms and its cull rules, so the geometry dict is the only
thing that differs between a candidate and the field that ships. Nothing here writes to
``data/local``.

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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools" / "mapgen" / "src"))

from mapgen.common import LOCAL_DIR, base_parser, require_gen
from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.mesh import ROCK_DIRS, MeshBounds, rasterise_cliffs
from mapgen.gamedata.sweep import drop_offsets, landscape_frame, sweep_levels
from mapgen.terrain.validate import NODE_TABLE
from satisfactory_mcp.core.gameassets import nanite as nan
from satisfactory_mcp.core.gameassets import staticmesh as sm
from satisfactory_mcp.core.gameassets.container import open_container
from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
)
from satisfactory_mcp.domain.spatial import heightfield as hf

#: The rungs, in the order the table prints them. ``hull`` first because it is what ships.
RUNGS = ("hull", "lod0", "best")

#: The trim the score vocabulary uses, matching the generator's own ``VALIDATION_TRIM``.
TRIM = 0.90


# --- Reading all three sources out of one open of each mesh. --------------------------


def read_rungs(store, scripts, index, meshes: list[str], progress: bool = True) -> dict:
    """Every rock mesh's hull, LOD0 and Nanite leaf, from one read of each package.

    One read, three answers, so the three rungs cannot disagree about which asset they
    were looking at.
    """
    wanted = [m for m in meshes if any(d in m for d in ROCK_DIRS)]
    out: dict[str, dict] = {}
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
        export = sm.static_mesh_export(view)
        if export is None:
            notes[mesh] = "no StaticMesh export"
            continue
        bounds = sm.extended_bounds(view, export)
        if bounds is None:
            notes[mesh] = "no ExtendedBounds, so no decode could be checked"
            continue
        low, high = bounds
        row: dict = {"package": package, "bounds": (low, high)}

        hull, why = sm.collision_hull(view, low, high)
        row["hull"] = None if hull is None else (hull[0], hull[1].astype(np.int64), hull[2])
        row["hull_note"] = why

        tail = sm.render_tail(view, export)
        try:
            parsed = sm.parse_render_data(tail)
        except sm.ParseError as exc:
            row["parse_error"] = str(exc)
            parsed = None
        if parsed is not None:
            got = sm.lod0_buffers(tail, parsed)
            if got is not None:
                row["lod0"] = (got[0].astype(np.float32), got[1])
            resource = sm.load_nanite(store, package, view, parsed, tail)
            if resource is not None:
                decoded = nan.decode_resource(resource)
                problems = sm.page_table_problems(resource, sm.bulk_size(view, resource))
                problems += nan.identity_checks(resource, decoded)
                row["nanite"] = (decoded["positions"], decoded["triangles"])
                row["nanite_problems"] = problems
                row["nanite_clusters"] = decoded["total_clusters"]
        out[mesh] = row
        if progress and count % 25 == 0:
            print(f"  {count}/{len(wanted)} meshes, {time.time() - started:.0f}s", flush=True)
    return {"meshes": out, "wanted": len(wanted), "notes": notes, "seconds": time.time() - started}


def geometry_for(rung: str, read: dict) -> dict:
    """``{mesh: (verts, tris, low, high)}`` for one rung, over the hull-equivalent set only.

    A mesh with no hull is absent from every rung, not just from ``hull``, which is what
    keeps the three rows a comparison of resolution rather than of coverage.
    """
    out: dict[str, tuple] = {}
    for mesh, row in read["meshes"].items():
        if row.get("hull") is None:
            continue
        low, high = row["bounds"]
        pad = row["hull"][2]
        if rung == "hull":
            verts, tris = row["hull"][0], row["hull"][1]
        elif rung == "lod0":
            if "lod0" not in row:
                continue
            verts, tris = row["lod0"]
        else:
            source = row.get("nanite") or row.get("lod0")
            if source is None:
                continue
            verts, tris = source
        out[mesh] = (
            np.ascontiguousarray(verts, dtype=np.float32),
            np.ascontiguousarray(tris, dtype=np.int64),
            low - pad,
            high + pad,
        )
    return out


# --- The score vocabulary. One definition, used by every table this prints. -----------


def _metrics(sorted_abs: np.ndarray) -> dict:
    cut = max(1, int(sorted_abs.size * TRIM))
    return {
        "median_abs_m": round(float(np.median(sorted_abs)), 4),
        "p90_abs_m": round(float(np.percentile(sorted_abs, 90)), 4),
        "trimRMS90_m": round(float(np.sqrt((sorted_abs[:cut] ** 2).mean())), 4),
        "frac_lt_1m": round(float((sorted_abs < 1.0).mean()), 4),
        "frac_lt_0.25m": round(float((sorted_abs < 0.25).mean()), 4),
    }


def score(truth_m: np.ndarray, field_m: np.ndarray, n_total: int | None = None) -> dict:
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


def row(tag: str, s: dict) -> str:
    if not s.get("n"):
        return f"{tag:24s}  (no probes)"
    return (
        f"{tag:24s} n={s['n']:>9,}  med {s['median_abs_m']:7.4f}  p90 {s['p90_abs_m']:8.2f}  "
        f"trimRMS90 {s['trimRMS90_m']:7.3f}  <1m {s['frac_lt_1m']:.4f}  "
        f"<0.25m {s['frac_lt_0.25m']:.4f}"
    )


def sample(grid_cm: np.ndarray, x_cm, y_cm) -> np.ndarray:
    """The candidate raster read in metres at world coordinates, NaN where it is silent."""
    col = np.round((np.asarray(x_cm) - ORIGIN_X_CM) / SPACING_CM).astype(np.int64)
    row_i = np.round((np.asarray(y_cm) - ORIGIN_Y_CM) / SPACING_CM).astype(np.int64)
    on = (col >= 0) & (col < GRID_PX) & (row_i >= 0) & (row_i < GRID_PX)
    value = grid_cm[np.clip(row_i, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1)]
    return np.where(on & np.isfinite(value), value / 100.0, np.nan)


# --- Density, which is the claim this whole exercise can actually make. ---------------


#: Metres of world per output pixel over the 7,500 m box, at 16384 px and 32768 px.
#: Written as arithmetic so they cannot drift from the renders.
Z6_TEXEL_M = 7500.0 / 16384
Z7_TEXEL_M = 7500.0 / 32768

#: 100 geometric bins per decade, 1 mm to 1 km. Geometric is load-bearing: see
#: :func:`world_grain`.
GRAIN_DECADES = (-3, 3)
GRAIN_PER_DECADE = 100


def world_grain(geometry: dict, placements: np.ndarray, meshes: list[str]) -> dict:
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
                if share == 0.0:
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
    parser: argparse.ArgumentParser = base_parser(__doc__.splitlines()[0])
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
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
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
    hulls = sum(1 for r in read["meshes"].values() if r.get("hull") is not None)
    nanites = sum(1 for r in read["meshes"].values() if "nanite" in r)
    broken = {
        m: r["nanite_problems"] for m, r in read["meshes"].items() if r.get("nanite_problems")
    }
    print(
        f"  {len(read['meshes'])}/{read['wanted']} meshes opened in {read['seconds']:.0f}s: "
        f"{hulls} with a cooked hull, {nanites} with Nanite, {len(broken)} with a page or "
        f"identity problem"
    )
    for mesh, problems in broken.items():
        print(f"    {mesh.rsplit('/', 1)[-1]}: {'; '.join(problems)}")

    closed = {}
    for mesh, r in read["meshes"].items():
        if "nanite" in r:
            closed[mesh] = nan.boundary_edges(r["nanite"][1])
    manifolds = sum(1 for v in closed.values() if v == 0)
    print(f"  closed 2-manifolds among the Nanite decodes: {manifolds}/{len(closed)}")

    # -- the probe sets ------------------------------------------------------------------
    field = hf.load_field(args.field)
    if field is None:
        print(f"no shipped field at {args.field}; the cliff province cannot be defined")
        return 2
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    node_pts = np.array([[n["x"], n["y"], n["z"]] for n in nodes], float)

    probes = {"nodes": node_pts}
    if args.foliage is not None:
        points = np.load(args.foliage)
        if args.foliage_mask is not None:
            points = points[np.load(args.foliage_mask)]
        probes["foliage"] = points
    province = {}
    for name, pts in probes.items():
        col = np.clip(np.round((pts[:, 0] - ORIGIN_X_CM) / 100.0).astype(int), 0, 7499)
        row_i = np.clip(np.round((pts[:, 1] - ORIGIN_Y_CM) / 100.0).astype(int), 0, 7499)
        # Both cliff values: testing ``== PROV_CLIFF`` scores a v3 field on a quarter of
        # the probes and calls it the same measurement.
        province[name] = np.isin(field._prov[row_i, col], hf.PROV_CLIFF_VALUES)
        print(f"  {name}: {len(pts)} probes, {int(province[name].sum())} on the cliff province")

    # -- the ladder ----------------------------------------------------------------------
    table: dict = {"rungs": {}, "meshes_with_a_hull": hulls, "meshes_with_nanite": nanites}
    for rung in args.rungs.split(","):
        if rung not in RUNGS:
            print(f"unknown rung {rung!r}")
            return 2
        geometry = geometry_for(rung, read)
        triangles = sum(len(t) for _v, t, _lo, _hi in geometry.values())
        print(f"\nrung {rung}: {len(geometry)} meshes, {triangles} source triangles")
        cliffs = rasterise_cliffs(sweep, geometry, frame, loud)
        whole = np.full((GRID_PX, GRID_PX), np.nan, np.float32)
        dx, dy = drop_offsets(frame)
        whole[dy : dy + frame["height"], dx : dx + frame["width"]] = cliffs["z_cm"]
        entry = {
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
            got = score(
                pts[pick, 2] / 100.0,
                sample(whole, pts[pick, 0], pts[pick, 1]),
                int(pick.sum()),
            )
            entry["scores"][name] = got
            print("  " + row(f"{name} (cliff province)", got))
        table["rungs"][rung] = entry

    if args.out:
        args.out.write_text(json.dumps(table, indent=1), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
