"""Cut a real 1 m heightmap of this world out of the installed game.

    uv run --extra gen python tools/gen_world_heightmap.py

Five stages fuse into one field: the cooked UE Landscape heightfield (a
``LandscapeComponent``'s ``GrassData`` blob opens with 128x128 uint16 height samples, one
per 1 m quad), the rock meshes' own cooked geometry rasterised as a max-Z overlay, the
2048 px ``HeightData_Test`` interface raster as fill outside the landscape frame, and the
game's own water actors for where the water is and how high it stands. The landscape is the
sculpted terrain and nothing else -- every cliff, mesa and boulder is a placed static mesh
-- so the overlay is what makes the tail of the error distribution bearable, and the run
proves that per build by sampling the finished field at every static resource node and
refusing to write if the trimmed RMS misses ``VALIDATION_TRIM_RMS_MAX_M``.

It writes ``data/local/heightmap/``, six files, about 18 MB::

    height.i16.z  7500x7500 int16 decimetres, row-delta + zlib, -32768 = no data
    prov.u8.z     0 no-data, 1 landscape, 3 fill, 4 cliff interpolated, 5 cliff direct
    density.u8.z  source vertices per texel over the cliff layer, clamped at 255
    water.i16.z   water surface Z, same grid and no-data
    waterq.u8.z   0 dry, 1 water with a measured depth, 2 water whose depth is unknowable
    meta.json     georeference, game build, generator version, coverage, measured accuracy

``--caves`` writes only the cave masks, to ``data/local/caves/`` (docs/spatial-and-map.md
section 23).

The georeference is ``x_cm = -324700 + col*100``, ``y_cm = -375000 + row*100``, and it is
**vertex-aligned**: a texel's height belongs to that point exactly, not to a cell around it.
The two cliff provenance values are one layer split by how the texel was answered, so a
reader that knows only 4 sees 5 as "not landscape, not fill, not no-data" and is right. The
codec lives in ``satisfactory_mcp.domain.spatial.heightfield`` and the container reader in
``satisfactory_mcp.core.gameassets``; both are imported rather than reimplemented, and
``ooz`` is imported inside the latter so a machine without the ``gen`` extra still imports
every module and runs the tests.

Everything written here is derived from Coffee Stain's cooked assets, read out of the
reader's own install into a gitignored directory: the generator is committed and its output
never is.
"""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.core.gameassets import nanite, staticmesh
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
    class_name_of,
    property_tags,
    quat_rotate,
    read_int32,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    install_directory,
    installed_build,
    read_str_path,
)
from satisfactory_mcp.core.gameassets.textures import decode_bc1_rgba, raw_mip_sizes
from satisfactory_mcp.domain.spatial import caves
from satisfactory_mcp.domain.spatial import heightfield as hf
from tools._common import base_parser, require_gen

# The map sheet's four slices are one asset with one layout, imported from the generator
# that owns them: two descriptions of one texture are two chances to describe different
# textures.
from tools.gen_map_image import SHEET_PX, SLICES, TILE_PX, read_slice

#: Which packages are swept. Everything terrain lives under one world.
LEVEL_DIR = "/GameLevel01/"
LEVEL_SUFFIX = ".umap"

#: The interface raster the fill layer is cut from.
BASELINE_PATH = (
    "../../../FactoryGame/Content/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test.ubulk"
)
BASELINE_PX = 2048

#: The mip chain of that raster, largest-first, at two bytes per texel: 2048 down to 128. A
#: file of another length means the raster was re-cooked, i.e. the game changed.
BASELINE_MIPS = raw_mip_sizes(BASELINE_PX, 5, 2)
BASELINE_BYTES = sum(size for _px, size in BASELINE_MIPS)

#: The raster's own box, metres of world per texel column. The in-game map square.
BASELINE_BOX_CM = (-324700.0, 425300.0, -375000.0, 375000.0)

#: ``z_cm = scale*raw + offset``, from a robust three-pass fit of the float16 values against
#: the 626 static nodes: 569 inliers, 1.07 m RMS, 3.897 m per quantisation step. Recorded
#: rather than re-fitted per run, because a calibration that moves silently is not one.
BASELINE_SCALE_CM_PER_RAW = 99364.40751843198
BASELINE_OFFSET_CM = -52282.12831764497

#: The fill's no-data test, and the trap in it. ``raw == 0`` is the blank value and decodes
#: to -522.8 m; the world's own floor is -255 m. Anything below this is the raster's blank
#: tail, not sea bed, and testing ``raw > 0`` instead leaks 138,481 texels of it into the
#: field as a false sea floor. A decoded height, so it cannot be read as a raw one.
FILL_FLOOR_CM = -26000.0

#: The interface raster's own resolution, for the accuracy the fill layer inherits.
FILL_HORIZONTAL_M = 7500.0 / BASELINE_PX
FILL_VERTICAL_M = BASELINE_SCALE_CM_PER_RAW / 255.0 / 100.0

#: ``ComponentSizeQuads`` is 127, so a landscape component is 128x128 height samples.
LANDSCAPE_N = 128

#: The height encoding, and what this run refuses to proceed without: the proxies state
#: their own scale and origin, and a build that disagrees with these gets an error rather
#: than a field wrong by a factor.
LANDSCAPE_SCALE_CM = 100.0
LANDSCAPE_ORIGIN_Z_CM = 100.0
LANDSCAPE_ZERO = 32768.0
LANDSCAPE_PER_UNIT = 128.0

#: How far a measured proxy transform may sit from those numbers before the run stops. A
#: hundredth of a centimetre: floating-point noise in a double, and nothing else.
TRANSFORM_TOLERANCE = 0.01

#: The output grid. 7500 x 7500 at 1 m, vertex-aligned, over the in-game map square.
GRID_PX = 7500
SPACING_CM = 100.0
ORIGIN_X_CM = -324700.0
ORIGIN_Y_CM = -375000.0

#: Which mesh trees carry terrain geometry. Everything else placed in the world is a tree,
#: a plant, a building or a prop, and none of those are ground.
ROCK_DIRS = ("/World/Environment/Rock/", "/World/Environment/Caves/")

#: Actors whose meshes must never enter the field. The resource-node mesh is the whole list
#: and the reason is circularity: the field is validated against the node table.
EXCLUDED_OWNERS = frozenset({"NodeMeshActor_C"})

#: A mesh basename containing this is an arch, and an arch is a roof: a max-Z fold would put
#: it over the ground beneath it, so it is dropped before the fold rather than masked after.
#: Masking after blanks a texel an arch won even where a real rock stood second in it.
ARCH_MARK = "Arc"

#: Scaled local extent past which a placement is scenery rather than terrain: the sky dome
#: and the ocean shells. 600 m is an order of magnitude above the largest real rock.
OVERSIZE_CM = 60000.0

#: Where the cliff layer's triangles may come from, finest first. The set of MESHES is
#: still decided by the cooked collision hull; this is only which of a mesh's own
#: descriptions of itself gets rasterised.
CLIFF_SOURCES = ("nanite", "lod0", "hull")

#: How far past its own ``ExtendedBounds`` a vertex may sit. Both a decode check -- most of
#: a candidate's vertices must be inside, which a misread stream cannot manage -- and, per
#: triangle, a clamp against stray far vertices. The sidecar records how often it fires.
BOUNDS_PAD_CM = staticmesh.BOUNDS_PAD_CM
BOUNDS_PAD_FRACTION = staticmesh.BOUNDS_PAD_FRACTION
BOUNDS_INSIDE_MIN = staticmesh.BOUNDS_INSIDE_MIN

#: Metres of world per output pixel at the two candidate zoom levels, over the 7,500 m box.
Z6_TEXEL_M = 7500.0 / 16384
Z7_TEXEL_M = 7500.0 / 32768

#: How many source vertices a texel needs before its height is a measurement rather than an
#: interpolation across a triangle wider than itself. ``tools/gen_map_renders.py`` imports
#: this rule and evaluates it at its own texel size, so it is stated here only once.
DIRECT_SAMPLES_MIN = 1

#: The rasteriser's scatter buffer, in candidate texels. Bounded so a 21,000-placement run
#: holds a few hundred MB rather than the whole 120 M-triangle scatter at once.
RASTER_FLUSH = 6_000_000

#: A water actor is one whose class name carries a water word and one of the game's own
#: class prefixes. Deliberately a shape rather than a list: 849 actors on build 495413
#: across nine classes, and a build that adds a tenth should be found, not missed.
WATER_CLASS_TOKENS = ("Water", "Ocean", "Lake", "River")
WATER_CLASS_PREFIXES = ("BP_", "FG", "BPW")

#: Which of those classes are a water SURFACE, i.e. whose box top may set a level. The
#: exclusions are the argument. ``BP_WaterFallTool_02_C`` is water and is not a surface --
#: its box top is the lip of the fall, tens of metres above the pool it feeds -- and the
#: two ``BP_WaterPlane_C`` are developer backdrops carrying no transform at all.
WATER_SURFACE_CLASSES = frozenset(
    {
        "FGWaterVolume",
        "BP_Water_C",
        "BP_LakeWater_C",
        "BPW_OceanSplineTool_02_C",
        "BP_TranslucentWater_C",
        "BP_River_PROT_C",
    }
)

#: Component classes that can state a box. Order of preference is in ``_component_box``.
WATER_BOX_COMPONENTS = frozenset(
    {
        "BoxComponent",
        "BrushComponent",
        "StaticMeshComponent",
        "InstancedStaticMeshComponent",
        "HierarchicalInstancedStaticMeshComponent",
    }
)

#: The plane the water blueprints draw themselves with. Their cooked instances name no
#: ``StaticMesh`` -- the construction script assigns it -- so this asset's own
#: ``ExtendedBounds`` stands in: of 215 such planes, the 187 whose centre falls inside an
#: ``FGWaterVolume`` sit on that volume's top to a median of 1.3 cm. Read from the container
#: rather than hard-coded, so a resized plane moves it.
WATER_PLANE_MESH = "/Game/FactoryGame/World/Environment/Water/Mesh/WaterPlane"

#: The artwork classifier: blue minus red on the game's own map sheet, one threshold. The
#: histogram is bimodal with nothing between the modes, and at this value it called 3 of the
#: node table's 626 rows -- all of which stand on dry ground -- water.
WATER_ARTWORK_BLUE_OVER_RED = 25

#: The four gates the water stage refuses to write past, each aimed at one way the recipe
#: can come apart without looking wrong: dry nodes read as water means the colour classifier
#: drifted or the sheet moved; Spire Coast recall is the region that exposed the flatness
#: detector this stage replaced; an ocean level far from the median top of the ocean-spline
#: boxes means the level is coming from the wrong boxes; and artwork water standing over no
#: box at all is what a misregistration of more than a few texels looks like from here.
WATER_FP_MAX = 0.01
WATER_SPIRE_RECALL_MIN = 0.95
WATER_OCEAN_TOLERANCE_M = 0.5
WATER_UNCOVERED_MAX = 0.01

#: The class whose boxes are the ocean, and the region whose recall is the gate. Both are
#: names in data the run reads rather than judgements this file makes.
WATER_OCEAN_CLASS = "BPW_OceanSplineTool_02_C"
WATER_GATE_REGION = "Spire Coast"
REGION_TABLE = ROOT / "data" / "region_names.json"

#: The node table the run validates against, and the gate it has to clear. This pipeline
#: measures 0.368 m trimmed RMS and the interface raster alone manages 1.08 m, so 0.5 m is
#: the band in which a decode regression cannot pass as a refresh.
NODE_TABLE = ROOT / "data" / "world_resource_nodes.json"
VALIDATION_TRIM = 0.90
VALIDATION_TRIM_RMS_MAX_M = 0.5

#: How many nodes a provenance layer needs before its measured accuracy is believed rather
#: than its derived quantisation step quoted. Nodes are not spread evenly, and the fill
#: layer in particular is mostly ocean where nothing stands.
ACCURACY_MIN_SAMPLES = 30

LOCAL_DIR = ROOT / "data" / "local"

#: Bumped when the pipeline changes what it writes, so a sidecar dates its own field. 3 is
#: the cliff layer taking the Nanite leaf over the collision hull, the ``density.u8.z``
#: plane, and the provenance value that says which side of one sample per texel a cliff
#: texel is on. 4 adds ``terrain.u16.z`` and ``top.i16.z`` beside an unchanged ground.
GENERATOR_VERSION = 4

#: ``LandscapeSectionOffset - location / scale`` on every proxy, in landscape quads. The
#: terrain plane's georeference is derived from it, so a cook that moves it must fail here.
LANDSCAPE_SECTION_ORIGIN = 508.0

#: Quads per landscape component: neighbouring components share one edge row of samples.
LANDSCAPE_COMPONENT_QUADS = LANDSCAPE_N - 1

#: Shared edge samples on which two components' GrassData disagree. 46 on build 502094;
#: far more means the stitch is reading something other than heights.
SEAM_DISAGREEMENT_MAX = 100

#: The bare terrain against nodes standing on the landscape layer, median absolute.
TERRAIN_NODE_MEDIAN_MAX_M = 0.3

#: Foliage-painted rocks with a blocking collision trimesh. They are instances inside
#: foliage components, so the placement sweep never sees them; only ``top.i16.z`` carries them.
TOP_FOLIAGE_MESHES = frozenset(
    {
        "SM_Boulder_01",
        "SM_Boulder_02",
        "SM_Boulder_03",
        "SM_Boulder_04",
        "SM_Boulder_06",
        "SM_SeaRock_06",
    }
)
FOLIAGE_CLASSES = frozenset({"FoliageInstancedStaticMeshComponent", "FGFoliageInstancedSMC"})

#: Rock meshes kept out of every layer by name. CliffPillar_03 is passable in game.
EXCLUDED_MESHES = frozenset({"CliffPillar_03"})

#: Where the sidecar records the build, and what the staleness guard reads back.
PIN_PATH = ("sources", "game", "game_version_pinned")


# --------------------------------------------------------------------------------------
# Stages 1 and 2: one sweep of the world's packages, two harvests out of it.
# --------------------------------------------------------------------------------------


def rotation_matrix(pitch: float, yaw: float, roll: float) -> np.ndarray:
    """UE's ``FRotationMatrix``: rows are the local X, Y, Z axes in world space.

    Written out rather than composed from three rotations, because UE's order and sign
    conventions are its own.
    """
    p, y, r = np.radians([pitch, yaw, roll])
    sp, cp = np.sin(p), np.cos(p)
    sy, cy = np.sin(y), np.cos(y)
    sr, cr = np.sin(r), np.cos(r)
    return np.array(
        [
            [cp * cy, cp * sy, sp],
            [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
            [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp],
        ]
    )


def _grass_data_heights(tail: bytes) -> np.ndarray | None:
    """The ``128*128`` uint16 height samples out of a ``LandscapeComponent``'s tail.

    Past the property tags the export carries a bool, a GUID and a float, then the
    ``GrassData`` map: an element count, a ``TMap`` of that many 8-byte entries, and the
    ``TArray<uint8>`` whose first ``2*NumElements`` bytes are the heights. Every offset is
    read from a length in the blob; a component that is not 128 samples square is skipped
    rather than reinterpreted.
    """
    try:
        num = struct.unpack_from("<I", tail, 24)[0]
        entries = struct.unpack_from("<I", tail, 28)[0]
        pos = 32 + 8 * entries
        total = struct.unpack_from("<I", tail, pos)[0]
        pos += 4
    except struct.error:
        return None
    want = LANDSCAPE_N * LANDSCAPE_N
    if num != want or total < 2 * num or pos + 2 * num > len(tail):
        return None
    return np.frombuffer(tail, dtype="<u2", count=num, offset=pos).reshape(LANDSCAPE_N, LANDSCAPE_N)


def flagged_tags(body: bytes, names: list[str], pos: int = 1) -> tuple[dict[str, bytes], int]:
    """Top-level ``{name: payload}`` of a tag stream whose tags carry the 5.x flag byte.

    ``property_tags`` reads the byte after ``Size`` as a bool value; on a foliage component
    it is a flag set that announces an array index, a GUID or an extension block, and
    skipping those wrongly loses ``StaticMesh``. Indexed array elements are left out.
    """
    out: dict[str, bytes] = {}
    limit = len(body)

    def skip_type(at: int) -> int:
        inner = struct.unpack_from("<i", body, at + 8)[0]
        at += 12
        for _ in range(inner):
            at = skip_type(at)
        return at

    while pos + 8 <= limit:
        index, number = struct.unpack_from("<II", body, pos)
        slot = index & 0x3FFFFFFF
        name = names[slot] if (index >> 30) == 0 and slot < len(names) else None
        pos += 8
        if (index == 0 and number == 0) or (name == "None" and number == 0):
            break
        try:
            pos = skip_type(pos)
            size = struct.unpack_from("<i", body, pos)[0]
            flags = body[pos + 4]
        except (struct.error, IndexError, RecursionError):
            break
        pos += 5
        array_index = 0
        if flags & 1:
            array_index = struct.unpack_from("<i", body, pos)[0]
            pos += 4
        if flags & 2:
            pos += 16
        if flags & 4:
            extension = body[pos]
            pos += 1
            if extension & 2:
                pos += 2
        if size < 0 or pos + size > limit:
            break
        if name and array_index == 0:
            out[name] = body[pos : pos + size]
        pos += size
    return out, pos


def instance_matrices(tail: bytes, expect: int | None) -> np.ndarray | None:
    """``PerInstanceSMData`` as (n, 4, 4) float64 world-relative matrices, or ``None``.

    Bulk-serialised as an element size of 128 (one FMatrix of doubles) and a count, found by
    scanning the first 4 KiB past the tags; a candidate must have a unit last column.
    """
    for off in range(min(len(tail) - 8, 4096)):
        element, count = struct.unpack_from("<ii", tail, off)
        if element != 128 or count <= 0 or off + 8 + count * 128 > len(tail):
            continue
        if expect is not None and count != expect:
            continue
        mats = np.frombuffer(tail, "<f8", count=count * 16, offset=off + 8).reshape(count, 4, 4)
        if np.allclose(mats[:, 3, 3], 1.0) and np.allclose(mats[:, :3, 3], 0.0):
            return mats
    return None


def is_top_foliage(mesh: str) -> bool:
    return mesh.rsplit("/", 1)[-1] in TOP_FOLIAGE_MESHES


def foliage_instances(
    view, slot: int, classes, wanted=is_top_foliage
) -> tuple[str, np.ndarray] | None:
    """One foliage component's mesh and world matrices, for meshes ``wanted`` accepts."""
    body = view.pkg.body(view.exports[slot])
    props, end = flagged_tags(body, view.pkg.names)
    reference = props.get("StaticMesh")
    mesh = view.import_path(reference) if reference else None
    if not mesh or not wanted(mesh):
        return None
    built = props.get("NumBuiltInstances")
    expect = struct.unpack("<i", built)[0] if built and len(built) == 4 else None
    mats = instance_matrices(body[end:], expect)
    if mats is None:
        return None

    def triple(key: str, default: tuple[float, float, float]) -> np.ndarray:
        raw = props.get(key, b"")
        return np.array(struct.unpack("<3d", raw) if len(raw) == 24 else default)

    own = triple("RelativeLocation", (0.0, 0.0, 0.0))
    parent = view.export_ref(props["AttachParent"]) if "AttachParent" in props else None
    transform = world_transform(view, parent, classes)[0] if parent is not None else None
    if transform is None:
        transform = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
    location, quat, scale = transform
    rotation = np.stack([np.array(quat_rotate(quat, tuple(axis))) for axis in np.eye(3)])
    size = np.array(scale)
    world = mats.copy()
    world[:, :3, :3] = (mats[:, :3, :3] * size[None, None, :]) @ rotation
    world[:, 3, :3] = ((mats[:, 3, :3] + own) * size) @ rotation + np.array(location)
    return mesh, world


def sweep_levels(store, scripts, classes, meshes, progress: bool = True) -> dict:
    """One pass over every ``*.umap`` of the world: landscape, placements, water actors.

    All three harvests need the same ``PackageView`` of the same 4,521 packages, and
    building that view is the whole cost of the pass, so they share it. Returns raw material
    and nothing interpreted.
    """
    components: list[tuple[int, int, np.ndarray]] = []
    proxies: list[tuple[float, float, float, float, float, float]] = []
    #: (mesh id, owner id, x, y, z, pitch, yaw, roll, sx, sy, sz)
    placements: list[tuple[float, ...]] = []
    #: (class name, (x0, y0, z0, x1, y1, z1)) in world centimetres, for the water stage.
    water: list[tuple[str, tuple[float, ...]]] = []
    water_actors: dict[str, int] = {}
    water_boxless: list[tuple[str, str, str]] = []
    box_sources: dict[str, int] = {}
    mesh_ids: dict[str, int] = {}
    owner_ids: dict[str, int] = {}
    foliage: dict[str, list[np.ndarray]] = {}
    unreadable = 0
    malformed = 0
    started = time.time()

    def count_unreadable(_path: str, _exc: Exception) -> None:
        nonlocal unreadable
        unreadable += 1

    paths = level_paths(store, contains=LEVEL_DIR, suffix=LEVEL_SUFFIX)
    for index, total, path, view in walk_levels(
        store, scripts, paths=paths, on_unreadable=count_unreadable
    ):
        # An actor names its own root; a StaticMeshComponent that is not one is a
        # decoration hanging off something else, and its transform is relative to a parent
        # this sweep does not walk. Built first so the placement loop can just look up.
        root_owner: dict[int, str] = {}
        for slot, class_path in view.class_of.items():
            root = root_component(view, slot)
            if root is not None:
                root_owner[root] = class_name_of(class_path)

        for slot, class_path in view.class_of.items():
            name = class_name_of(class_path)
            if name == "LandscapeStreamingProxy":
                props = view.props(slot)
                offset = props.get("LandscapeSectionOffset")
                root = view.export_ref(props.get("RootComponent", b""))
                if not offset or len(offset) != 8 or root is None:
                    continue
                section_x, section_y = struct.unpack("<2i", offset)
                location = view.props(root).get("RelativeLocation")
                scale = view.props(root).get("RelativeScale3D")
                if not location or len(location) != 24 or not scale or len(scale) != 24:
                    continue
                lx, ly, lz = struct.unpack("<3d", location)
                sx, sy, sz = struct.unpack("<3d", scale)
                proxies.append((section_x - lx / sx, section_y - ly / sy, lz, sx, sy, sz))
            elif name == "LandscapeComponent":
                props = view.props(slot)
                base_x = read_int32(props.get("SectionBaseX", b"\0\0\0\0"))
                base_y = read_int32(props.get("SectionBaseY", b"\0\0\0\0"))
                body = view.pkg.body(view.exports[slot])
                _tags, end = property_tags(body, view.pkg.names)
                heights = _grass_data_heights(body[end:])
                if heights is None:
                    malformed += 1
                    continue
                components.append((base_x, base_y, heights))
            elif name == "StaticMeshComponent":
                if slot not in root_owner:
                    continue
                props = view.props(slot)
                reference = props.get("StaticMesh")
                location = props.get("RelativeLocation")
                if reference is None or location is None or len(location) != 24:
                    continue
                mesh = view.import_path(reference)
                if not mesh:
                    continue
                rotation = props.get("RelativeRotation")
                scale = props.get("RelativeScale3D")
                x, y, z = struct.unpack("<3d", location)
                turn = (0.0, 0.0, 0.0)
                if rotation and len(rotation) == 24:
                    turn = struct.unpack("<3d", rotation)
                size = (1.0, 1.0, 1.0)
                if scale and len(scale) == 24:
                    size = struct.unpack("<3d", scale)
                pitch, yaw, roll = turn
                sx, sy, sz = size
                mesh_id = mesh_ids.setdefault(mesh, len(mesh_ids))
                owner_id = owner_ids.setdefault(root_owner[slot], len(owner_ids))
                placements.append((mesh_id, owner_id, x, y, z, pitch, yaw, roll, sx, sy, sz))
            elif name in FOLIAGE_CLASSES:
                found = foliage_instances(view, slot, classes)
                if found is not None:
                    foliage.setdefault(found[0], []).append(found[1])
            elif is_water_class(name):
                water_actors[name] = water_actors.get(name, 0) + 1
                box, sources = water_actor_box(view, slot, classes, meshes)
                for source in sources:
                    box_sources[source] = box_sources.get(source, 0) + 1
                if box is None:
                    water_boxless.append(
                        (name, view.exports[slot]["name"], path.rsplit("/", 1)[-1])
                    )
                else:
                    water.append((name, box))

        if progress and index % 500 == 0:
            print(
                f"  {index}/{total} packages, {len(components)} landscape components, "
                f"{len(placements)} placements, {time.time() - started:.0f}s",
                flush=True,
            )

    return {
        "packages": len(paths),
        "unreadable": unreadable,
        "malformed_components": malformed,
        "components": components,
        "proxies": proxies,
        "placements": np.array(placements, dtype=np.float64) if placements else np.zeros((0, 11)),
        "meshes": [m for m, _ in sorted(mesh_ids.items(), key=lambda kv: kv[1])],
        "owners": [o for o, _ in sorted(owner_ids.items(), key=lambda kv: kv[1])],
        "water": water,
        "water_actors": water_actors,
        "water_boxless": water_boxless,
        "water_box_sources": box_sources,
        "foliage": {mesh: np.concatenate(parts) for mesh, parts in foliage.items()},
        "seconds": time.time() - started,
    }


def landscape_frame(sweep: dict) -> dict:
    """Stitch the components into one raster and pin it to the world. Nothing resampled.

    That the proxies all state the same origin, scale and Z offset is checked rather than
    assumed: a build that split the landscape into frames with different transforms would
    otherwise stitch into a plausible, wrong field.
    """
    components = sweep["components"]
    proxies = sweep["proxies"]
    if not components or not proxies:
        raise SystemExit(
            "no LandscapeComponent or no LandscapeStreamingProxy was found in "
            f"{sweep['packages']} packages. The landscape moved or was renamed, which means "
            "the game changed; nothing here can be trusted until that is looked at."
        )

    def _one(values, label: str) -> float:
        distinct = sorted({round(v, 3) for v in values})
        if len(distinct) != 1:
            raise SystemExit(
                f"the landscape proxies disagree about {label}: {distinct[:6]}. This file "
                "stitches one frame with one transform, and cannot stitch several."
            )
        return distinct[0]

    origin_x = _one((p[0] for p in proxies), "their world origin in X")
    origin_y = _one((p[1] for p in proxies), "their world origin in Y")
    origin_z = _one((p[2] for p in proxies), "their Z offset")
    scale_x = _one((p[3] for p in proxies), "their X scale")
    scale_y = _one((p[4] for p in proxies), "their Y scale")
    scale_z = _one((p[5] for p in proxies), "their Z scale")

    for measured, expected, label in (
        (scale_x, LANDSCAPE_SCALE_CM, "X scale"),
        (scale_y, LANDSCAPE_SCALE_CM, "Y scale"),
        (scale_z, LANDSCAPE_SCALE_CM, "Z scale"),
        (origin_z, LANDSCAPE_ORIGIN_Z_CM, "Z offset"),
        (origin_x, LANDSCAPE_SECTION_ORIGIN, "section origin in X"),
        (origin_y, LANDSCAPE_SECTION_ORIGIN, "section origin in Y"),
    ):
        if abs(measured - expected) > TRANSFORM_TOLERANCE:
            raise SystemExit(
                f"the landscape's {label} is {measured}, not the {expected} this file was "
                "measured against. The height encoding depends on it, so decoding anyway "
                "would produce a field that is wrong by a factor rather than by an offset."
            )

    xs = [c[0] for c in components]
    ys = [c[1] for c in components]
    min_x, min_y = min(xs), min(ys)
    off_lattice = sum(
        1
        for x, y in zip(xs, ys, strict=True)
        if (x - min_x) % LANDSCAPE_COMPONENT_QUADS or (y - min_y) % LANDSCAPE_COMPONENT_QUADS
    )
    if off_lattice:
        raise SystemExit(
            f"{off_lattice} landscape components are not on the {LANDSCAPE_COMPONENT_QUADS}-quad "
            "lattice. The component size changed, so the shared edges this stitch assumes "
            "are not shared any more."
        )
    width = max(xs) + LANDSCAPE_N - min_x
    height = max(ys) + LANDSCAPE_N - min_y

    raw = np.zeros((height, width), dtype="<u2")
    covered = np.zeros((height, width), dtype=bool)
    seam_disagreements = 0
    for base_x, base_y, heights in components:
        row, col = base_y - min_y, base_x - min_x
        cut = (slice(row, row + LANDSCAPE_N), slice(col, col + LANDSCAPE_N))
        seam_disagreements += int((covered[cut] & (raw[cut] != heights)).sum())
        raw[cut] = heights
        covered[cut] = True
    if seam_disagreements > SEAM_DISAGREEMENT_MAX:
        raise SystemExit(
            f"{seam_disagreements} shared edge samples disagree between neighbouring "
            f"components, against at most {SEAM_DISAGREEMENT_MAX}. The GrassData read is "
            "off, or the cook stopped keeping it in step with the heights."
        )

    # raw == 0 inside a component that IS present is a landscape hole -- a cave mouth or a
    # deliberately cut-out section -- not a height of -255 m. Left as no data for the cliff
    # layer to fill or for nothing to.
    hole = covered & (raw == 0)
    good = covered & ~hole
    _labelled, blobs = ndimage.label(hole)

    z_cm = (raw.astype(np.float32) - LANDSCAPE_ZERO) / LANDSCAPE_PER_UNIT * scale_z + origin_z
    raw[~covered] = 0
    return {
        "raw": raw,
        "seam_disagreements": seam_disagreements,
        "z_cm": z_cm,
        "good": good,
        "width": width,
        "height": height,
        "x0_cm": (min_x - origin_x) * scale_x,
        "y0_cm": (min_y - origin_y) * scale_y,
        "scale_cm": scale_x,
        "origin_z_cm": origin_z,
        "components": len(components),
        "coverage": float(covered.mean()),
        "hole_texels": int(hole.sum()),
        "hole_blobs": int(blobs),
    }


def drop_offsets(frame: dict) -> tuple[int, int]:
    """Where the landscape frame lands in the output grid, in whole texels.

    Asserted rather than rounded into: the landscape is a 1 m grid and so is the output, so
    a fractional offset means one of the two moved, and resampling would smooth a real
    heightfield to cover it.
    """
    dx = (frame["x0_cm"] - ORIGIN_X_CM) / SPACING_CM
    dy = (frame["y0_cm"] - ORIGIN_Y_CM) / SPACING_CM
    for value, axis in ((dx, "X"), (dy, "Y")):
        if abs(value - round(value)) > 1e-6:
            raise SystemExit(
                f"the landscape frame sits {value:.4f} texels from the output origin in "
                f"{axis}, which is not a whole number. The landscape and the output grid "
                "are both 1 m, so this cannot be dropped in index-aligned any more, and "
                "this file will not silently resample a real heightfield to hide that."
            )
    return round(dx), round(dy)


# --------------------------------------------------------------------------------------
# Stage 3: the cooked Chaos triangle meshes, and the max-Z overlay they rasterise into.
# --------------------------------------------------------------------------------------


def finer_source(store, package: str, view, export, low, high) -> tuple[str, tuple] | None:
    """The finest geometry this mesh ships, and which one that was -- or ``None``.

    Nanite first, LOD 0 second: at the placement transform their median world edges are
    0.48 m and 1.33 m against the collision hull's 2.43 m. 25 of this build's rock meshes
    carry no Nanite resource at all -- sea rocks, corals, part of the cave interior set --
    so a Nanite-only layer loses about 365,000 texels and still looks like a field.

    A finer source is accepted only if it clears the same bounds check the hull does: the
    mesh's own serialised ``ExtendedBounds`` is the one statement available that does not
    come from this reader, so it is what stops plausible garbage from shipping.
    """
    tail = staticmesh.render_tail(view, export)
    try:
        parsed = staticmesh.parse_render_data(tail)
    except staticmesh.ParseError:
        return None

    resource = staticmesh.load_nanite(store, package, view, parsed, tail)
    if resource is not None:
        decoded = nanite.decode_resource(resource)
        problems = staticmesh.page_table_problems(resource, staticmesh.bulk_size(view, resource))
        problems += nanite.identity_checks(resource, decoded)
        if not problems and len(decoded["triangles"]):
            candidate = (decoded["positions"], decoded["triangles"])
            if _inside_bounds(candidate[0], low, high):
                return "nanite", candidate

    got = staticmesh.lod0_buffers(tail, parsed)
    if got is not None and len(got[1]) and _inside_bounds(got[0], low, high):
        return "lod0", (got[0], got[1])
    return None


def _inside_bounds(verts: np.ndarray, low, high) -> bool:
    if not np.isfinite(verts).all():
        return False
    pad = BOUNDS_PAD_CM + BOUNDS_PAD_FRACTION * float(np.max(np.asarray(high) - np.asarray(low)))
    inside = ((verts >= low - pad) & (verts <= high + pad)).all(axis=1)
    return bool(inside.mean() >= BOUNDS_INSIDE_MIN)


def read_mesh_geometry(store, scripts, index, meshes: list[str], progress: bool = True) -> dict:
    """The finest geometry every placed rock mesh ships, over the hull-equivalent set.

    Only ``ROCK_DIRS`` are opened: a tree's collision is a tree, and the point of this layer
    is the geometry the landscape does not contain.

    **The cooked collision hull decides the SET**, and a mesh with no hull is skipped -- 21
    of the 130 use ``CTF_UseSimpleAndComplex`` and ship only convex hulls. Extending the
    layer to the 120 hull-less meshes costs 1.66 points of ``frac_lt_0.25m`` and 10.9 m of
    p90 under a max-Z sampler, because they are cave pillars, cave holes and merged cave
    floors: roofs.
    """
    wanted = [m for m in meshes if any(d in m for d in ROCK_DIRS)]
    geometry: dict[str, tuple] = {}
    sources: dict[str, str] = {}
    failures: dict[str, str] = {}
    hull_tris = 0
    closed = 0
    manifolds = 0
    checked_manifold = 0
    started = time.time()
    for count, mesh in enumerate(wanted):
        package = index.path_for(mesh)
        if not package:
            failures[mesh] = "not in the container"
            continue
        try:
            view = PackageView(store.read_path(package), scripts)
        except Exception as exc:
            failures[mesh] = f"unreadable package: {type(exc).__name__}"
            continue
        export = staticmesh.static_mesh_export(view)
        if export is None:
            failures[mesh] = "no StaticMesh export"
            continue
        bounds = staticmesh.extended_bounds(view, export)
        if bounds is None:
            failures[mesh] = "no ExtendedBounds, so a decode could not be checked"
            continue
        low, high = bounds
        hull, why = staticmesh.collision_hull(view, low, high)
        if hull is None:
            failures[mesh] = why
            continue
        hull_verts, hull_tris_array, pad = hull
        hull_tris += hull_tris_array.shape[0]
        # The closed-manifold Euler relation on the hull. Not a gate -- cave walls, floors
        # and merged arch pieces are open shells and are meant to be -- but noise satisfies
        # it essentially never, so the count is evidence that this is geometry.
        if hull_tris_array.shape[0] == 2 * hull_verts.shape[0] - 4:
            closed += 1

        chosen = finer_source(store, package, view, export, low, high)
        if chosen is None:
            source, (verts, tris) = "hull", (hull_verts, hull_tris_array)
        else:
            source, (verts, tris) = chosen
        if source == "nanite":
            checked_manifold += 1
            manifolds += nanite.boundary_edges(tris) == 0
        sources[mesh] = source
        geometry[mesh] = (
            np.ascontiguousarray(verts, dtype=np.float32),
            np.ascontiguousarray(tris, dtype=np.int64),
            low - pad,
            high + pad,
        )
        if progress and count % 25 == 0:
            print(f"  {count}/{len(wanted)} rock meshes, {time.time() - started:.0f}s", flush=True)
    return {
        "geometry": geometry,
        "sources": sources,
        "by_source": {s: sum(1 for v in sources.values() if v == s) for s in CLIFF_SOURCES},
        "failures": failures,
        "wanted": len(wanted),
        "closed_manifolds": closed,
        "nanite_closed": manifolds,
        "nanite_checked": checked_manifold,
        "hull_triangles": hull_tris,
        "verts": sum(v.shape[0] for v, _t, _lo, _hi in geometry.values()),
        "tris": sum(t.shape[0] for _v, t, _lo, _hi in geometry.values()),
        "seconds": time.time() - started,
    }


class MaxZRaster:
    """Scatter-max rasteriser over the landscape frame: the highest triangle wins a texel.

    Triangles arrive faster than they can be reduced -- 120 M of them across the placements
    -- so candidates are buffered and folded in batches by a lexsort on (texel, z) and a
    take-last.
    """

    def __init__(self, width: int, height: int, x0_cm: float, y0_cm: float, scale: float) -> None:
        self.width, self.height = width, height
        self.x0, self.y0, self.scale = x0_cm, y0_cm, scale
        self.z = np.full(height * width, -np.inf, dtype=np.float32)
        self.src = np.zeros(height * width, dtype=np.uint16)
        self.density = np.zeros(height * width, dtype=np.uint32)
        self._idx: list[np.ndarray] = []
        self._z: list[np.ndarray] = []
        self._s: list[np.ndarray] = []
        self._n = 0
        self._samples: list[np.ndarray] = []
        self._sample_n = 0

    def count_samples(self, points: np.ndarray) -> None:
        """Record which texel each SOURCE VERTEX landed in. The density plane, accumulated.

        Not the fold's question: the fold answers every texel a triangle covers, however
        large the triangle, while this counts only the texels the geometry sampled. A texel
        with no samples still has a height, and that height is a plane interpolation.

        Floored, not rounded, to match ``add``, which samples at ``col + 0.5`` and writes to
        ``col``. Two conventions here would put the density plane half a texel away from the
        heights it describes.
        """
        col = np.floor((points[:, 0] - self.x0) / self.scale).astype(np.int64)
        row = np.floor((points[:, 1] - self.y0) / self.scale).astype(np.int64)
        ok = (col >= 0) & (col < self.width) & (row >= 0) & (row < self.height)
        if not ok.any():
            return
        self._samples.append(row[ok] * self.width + col[ok])
        self._sample_n += int(ok.sum())
        if self._sample_n > RASTER_FLUSH:
            self.flush_samples()

    def flush_samples(self) -> None:
        """Reduce the buffered sample texels into the density plane.

        Sorted and run-length counted rather than ``bincount``-ed: a bincount over the frame
        allocates a 43-million-element temporary on every one of dozens of flushes.
        """
        if not self._samples:
            return
        idx = np.concatenate(self._samples)
        self._samples, self._sample_n = [], 0
        unique, counts = np.unique(idx, return_counts=True)
        self.density[unique] += counts.astype(np.uint32)

    def flush(self) -> None:
        if not self._idx:
            return
        idx = np.concatenate(self._idx)
        z = np.concatenate(self._z)
        src = np.concatenate(self._s)
        self._idx, self._z, self._s, self._n = [], [], [], 0
        order = np.lexsort((z, idx))
        idx, z, src = idx[order], z[order], src[order]
        last = np.empty(idx.size, bool)
        last[-1] = True
        last[:-1] = idx[1:] != idx[:-1]
        idx, z, src = idx[last], z[last], src[last]
        better = z > self.z[idx]
        self.z[idx[better]] = z[better]
        self.src[idx[better]] = src[better]

    def add(self, tri: np.ndarray, source_id: int) -> None:
        """Buffer every texel covered by ``tri`` (M, 3, 3) in world cm, with its plane Z.

        Bucketed by bounding-box span so one vectorised barycentric test runs over a whole
        bucket at a fixed candidate-grid size, instead of every triangle paying for the
        largest one's box.
        """
        fx = (tri[:, :, 0] - self.x0) / self.scale
        fy = (tri[:, :, 1] - self.y0) / self.scale
        z = tri[:, :, 2]
        x0 = np.floor(fx.min(1) - 0.5)
        x1 = np.ceil(fx.max(1) + 0.5)
        y0 = np.floor(fy.min(1) - 0.5)
        y1 = np.ceil(fy.max(1) + 0.5)
        span = np.maximum(x1 - x0, y1 - y0).astype(np.int32)
        for size in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            pick = (span <= size) & (span > (size // 2 if size > 1 else 0))
            if not pick.any():
                continue
            steps = np.arange(size + 1, dtype=np.float32)
            ox, oy = np.meshgrid(steps, steps)
            gx = x0[pick][:, None] + ox.ravel()[None, :] + 0.5
            gy = y0[pick][:, None] + oy.ravel()[None, :] + 0.5
            ax, ay = fx[pick, 0][:, None], fy[pick, 0][:, None]
            bx, by = fx[pick, 1][:, None], fy[pick, 1][:, None]
            cx, cy = fx[pick, 2][:, None], fy[pick, 2][:, None]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
            l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
            l3 = 1.0 - l1 - l2
            col = np.floor(gx).astype(np.int32)
            row = np.floor(gy).astype(np.int32)
            ok = (
                (l1 >= -1e-6)
                & (l2 >= -1e-6)
                & (l3 >= -1e-6)
                & (col >= 0)
                & (col < self.width)
                & (row >= 0)
                & (row < self.height)
            )
            if not ok.any():
                continue
            plane = l1 * z[pick, 0][:, None] + l2 * z[pick, 1][:, None] + l3 * z[pick, 2][:, None]
            self._idx.append(row[ok].astype(np.int64) * self.width + col[ok])
            self._z.append(plane[ok].astype(np.float32))
            self._s.append(np.full(int(ok.sum()), source_id, np.uint16))
            self._n += int(ok.sum())
        if self._n > RASTER_FLUSH:
            self.flush()

    def result(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        self.flush()
        self.flush_samples()
        z = self.z.reshape(self.height, self.width)
        return (
            np.where(np.isfinite(z), z, np.nan).astype(np.float32),
            self.src.reshape(self.height, self.width),
            self.density.reshape(self.height, self.width),
        )


def winding_sign(verts: np.ndarray, tris: np.ndarray) -> float:
    """+1 if this mesh's triangle normals point outward, -1 if inward, 0 if it cannot tell.

    A max-Z field wants only the up-facing half of a closed rock, and which half that is
    depends on the winding the cooker emitted, so it is measured per mesh from the
    divergence of the face normals about the centroid. 0 is an open shell, where the
    question is meaningless and every triangle is therefore kept.
    """
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    normals = np.cross(b - a, c - a)
    centre = verts.mean(0)
    divergence = float((normals * ((a + b + c) / 3 - centre)).sum())
    scale = float(np.abs(normals).sum() * np.abs(verts - centre).max()) + 1e-9
    ratio = divergence / scale
    return 1.0 if ratio > 0.02 else (-1.0 if ratio < -0.02 else 0.0)


def rasterise_cliffs(sweep: dict, geometry: dict, frame: dict, progress: bool = True) -> dict:
    """Transform, cull and rasterise every placed rock into a 1 m max-Z overlay, in cm.

    Culling, in the order it costs least: an excluded owner, a mesh with no cooked geometry,
    an arch, an oversized shell, then the downward-facing half of the triangles, then the
    triangles outside the mesh's own padded bounds. That last cull is per triangle rather
    than per mesh, because the defect it removes is one stray vertex in an otherwise good
    mesh.
    """
    placements = sweep["placements"]
    meshes, owners = sweep["meshes"], sweep["owners"]
    windings = {m: winding_sign(v, t) for m, (v, t, _lo, _hi) in geometry.items()}
    raster = MaxZRaster(
        frame["width"], frame["height"], frame["x0_cm"], frame["y0_cm"], frame["scale_cm"]
    )
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    dropped = {"owner": 0, "excluded_mesh": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    used = 0
    triangles = 0
    samples = 0
    clamped = 0
    started = time.time()
    for count, row in enumerate(placements):
        mesh_id, owner_id = int(row[0]), int(row[1])
        mesh = meshes[mesh_id]
        if owners[owner_id] in EXCLUDED_OWNERS:
            dropped["owner"] += 1
            continue
        if mesh.rsplit("/", 1)[-1] in EXCLUDED_MESHES:
            dropped["excluded_mesh"] += 1
            continue
        if mesh not in geometry:
            dropped["no_geometry"] += 1
            continue
        if mesh_id in arch_ids:
            dropped["arch"] += 1
            continue
        verts, tris, low, high = geometry[mesh]
        scale = row[8:11].astype(np.float32)
        if float(np.abs(verts * scale).max()) > OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        # The bounds clamp is applied in LOCAL space, where the mesh's own ExtendedBounds
        # live, so it costs one comparison per vertex instead of a transformed box per
        # placement -- and it is the same box for all 200 copies of a rock.
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        if not keep.all():
            good = keep[tris].all(axis=1)
            clamped += int((~good).sum())
            tris = tris[good]
            if tris.size == 0:
                continue
        world = (verts * scale) @ matrix + row[2:5].astype(np.float32)
        facing = windings[mesh] * np.sign(scale[0] * scale[1] * scale[2])
        if facing != 0:
            corner = world[tris[:, 0]]
            normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
            tris = tris[(normals[:, 2] * facing) > 0]
        if tris.shape[0]:
            raster.add(world[tris], mesh_id + 1)
            triangles += tris.shape[0]
            # The density plane counts the vertices of the triangles that SURVIVED the
            # facing cull, not every vertex of the mesh: a downward-facing vertex is not a
            # sample of the surface this field describes, and counting it would call a
            # texel measured because the underside of a rock passed over it.
            surviving = np.unique(tris)
            raster.count_samples(world[surviving])
            samples += surviving.size
        used += 1
        if progress and count % 4000 == 0:
            print(
                f"  {count}/{len(placements)} placements, {used} rasterised, "
                f"{triangles / 1e6:.1f} M triangles, {time.time() - started:.0f}s",
                flush=True,
            )
    z_cm, _src, density = raster.result()
    return {
        "z_cm": z_cm,
        "density": density,
        "placements_total": len(placements),
        "placements_used": used,
        "dropped": dropped,
        "arch_meshes": len(arch_ids & set(np.unique(placements[:, 0]).astype(int))),
        "triangles": int(triangles),
        "samples": int(samples),
        "triangles_out_of_bounds": clamped,
        "seconds": time.time() - started,
    }


def read_hull(store, scripts, index, mesh: str) -> tuple[np.ndarray, np.ndarray] | None:
    """A mesh's cooked collision trimesh, mesh-local cm, or ``None`` where it ships none."""
    package = index.path_for(mesh)
    if not package:
        return None
    try:
        view = PackageView(store.read_path(package), scripts)
    except Exception:
        return None
    export = staticmesh.static_mesh_export(view)
    bounds = staticmesh.extended_bounds(view, export) if export is not None else None
    if bounds is None:
        return None
    hull, _why = staticmesh.collision_hull(view, *bounds)
    if hull is None:
        return None
    return hull[0].astype(np.float32), hull[1].astype(np.int64)


def rasterise_top(sweep: dict, frame: dict, store, scripts, index, progress: bool = True) -> dict:
    """Arches and foliage boulders as a 1 m max-Z overlay, from their collision trimeshes.

    These are what the ground deliberately leaves out: an arch is a roof over buildable
    ground, and a boulder is painted foliage the placement sweep never sees.
    """
    started = time.time()
    raster = MaxZRaster(
        frame["width"], frame["height"], frame["x0_cm"], frame["y0_cm"], frame["scale_cm"]
    )
    placements, meshes, owners = sweep["placements"], sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    hulls: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
    arches = boulders = triangles = 0
    missing: set[str] = set()
    for row in placements:
        mesh_id = int(row[0])
        if mesh_id not in arch_ids or owners[int(row[1])] in EXCLUDED_OWNERS:
            continue
        mesh = meshes[mesh_id]
        if mesh not in hulls:
            hulls[mesh] = read_hull(store, scripts, index, mesh)
        if hulls[mesh] is None:
            missing.add(mesh)
            continue
        verts, tris = hulls[mesh]
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        world = (verts * row[8:11].astype(np.float32)) @ matrix + row[2:5].astype(np.float32)
        raster.add(world[tris], 1)
        arches += 1
        triangles += tris.shape[0]
    for mesh, mats in sweep["foliage"].items():
        if mesh not in hulls:
            hulls[mesh] = read_hull(store, scripts, index, mesh)
        if hulls[mesh] is None:
            missing.add(mesh)
            continue
        verts, tris = hulls[mesh]
        for start in range(0, len(mats), 512):
            chunk = mats[start : start + 512].astype(np.float32)
            world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
            raster.add(world[:, tris].reshape(-1, 3, 3), 2)
        boulders += len(mats)
        triangles += tris.shape[0] * len(mats)
    z_cm, _src, _density = raster.result()
    if progress:
        print(f"  top overlay: {arches} arches, {boulders} boulders, {time.time() - started:.0f}s")
    return {
        "z_cm": z_cm,
        "arch_placements": arches,
        "foliage_instances": boulders,
        "foliage_by_mesh": {m.rsplit("/", 1)[-1]: len(v) for m, v in sweep["foliage"].items()},
        "meshes_without_trimesh": sorted(m.rsplit("/", 1)[-1] for m in missing),
        "triangles": int(triangles),
        "seconds": time.time() - started,
    }


def compose_top(height_dm: np.ndarray, frame: dict, top: dict) -> tuple[np.ndarray, int]:
    """``height_dm`` max-folded with the overlay, and how many texels the overlay raised."""
    dx, dy = drop_offsets(frame)
    out = height_dm.copy()
    window = out[dy : dy + frame["height"], dx : dx + frame["width"]]
    over_dm = np.clip(np.round(np.nan_to_num(top["z_cm"], nan=-1e9) / 10.0), -32767, 32767)
    over_dm = np.where(np.isfinite(top["z_cm"]), over_dm, hf.NODATA).astype(np.int16)
    raise_ = (over_dm != hf.NODATA) & ((window == hf.NODATA) | (over_dm > window))
    window[raise_] = over_dm[raise_]
    return out, int(raise_.sum())


def validate_terrain(frame: dict, prov: np.ndarray) -> dict:
    """The bare terrain against the nodes standing on the landscape layer."""
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    x = np.array([n["x"] for n in nodes], float)
    y = np.array([n["y"] for n in nodes], float)
    z = np.array([n["z"] for n in nodes], float) / 100.0
    col = np.round((x - frame["x0_cm"]) / frame["scale_cm"]).astype(int)
    row = np.round((y - frame["y0_cm"]) / frame["scale_cm"]).astype(int)
    inside = (col >= 0) & (col < frame["width"]) & (row >= 0) & (row < frame["height"])
    gc = np.clip(np.round((x - ORIGIN_X_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    gr = np.clip(np.round((y - ORIGIN_Y_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    rc, cc = np.clip(row, 0, frame["height"] - 1), np.clip(col, 0, frame["width"] - 1)
    good = inside & frame["good"][rc, cc] & (prov[gr, gc] == hf.PROV_LANDSCAPE)
    errors = np.abs(z[good] - frame["z_cm"][rc[good], cc[good]] / 100.0)
    return {
        "n": int(good.sum()),
        "medabs_m": round(float(np.median(errors)), 4) if errors.size else None,
        "p90_m": round(float(np.percentile(errors, 90)), 4) if errors.size else None,
        "gate_m": TERRAIN_NODE_MEDIAN_MAX_M,
    }


# --------------------------------------------------------------------------------------
# Stage 4: the interface raster, as fill outside the landscape frame.
# --------------------------------------------------------------------------------------


def decode_baseline(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The interface raster's float16 texels to world centimetres, and where it says anything.

    Split out from the read so the rule is a pure function a test can hold. The no-data test
    is on the DECODED height against ``FILL_FLOOR_CM``: ``raw > 0`` looks equivalent and is
    not, because the blank value is ``raw == 0`` and it decodes to about -522 m.
    """
    z_cm = values.astype(np.float32) * BASELINE_SCALE_CM_PER_RAW + BASELINE_OFFSET_CM
    return z_cm, z_cm > FILL_FLOOR_CM


def read_baseline(store) -> tuple[np.ndarray, np.ndarray]:
    """``HeightData_Test`` as world centimetres, with the mask of where it says anything.

    The length check is the integrity check: 2048 down to 128 at two bytes a texel is one
    number, and a file that is not that long was re-cooked at another size or mip count.
    """
    if BASELINE_PATH not in store.by_path:
        raise SystemExit(
            "HeightData_Test is not in the container. The interface raster moved or was "
            "renamed, which means the game changed; the fill layer has no source."
        )
    raw = store.read_path(BASELINE_PATH)
    if len(raw) != BASELINE_BYTES:
        chain = ", ".join(f"{px}x{px}" for px, _size in BASELINE_MIPS)
        raise SystemExit(
            f"HeightData_Test.ubulk is {len(raw)} bytes, expected exactly {BASELINE_BYTES} "
            f"-- the mip chain {chain} at two bytes per float16 texel. A different length "
            "means the raster was re-cooked, so refusing to decode mip 0 out of a file "
            "whose layout is no longer known."
        )
    values = np.frombuffer(raw[: BASELINE_PX * BASELINE_PX * 2], dtype="<f2").reshape(
        BASELINE_PX, BASELINE_PX
    )
    return decode_baseline(values)


def baseline_indices() -> tuple[np.ndarray, np.ndarray]:
    """Which baseline texel each output column and row falls in. Nearest, never blended.

    The fill is 3.66 m data read at 1 m, so an interpolation would draw a smooth surface out
    of a raster that has none and hide the coarseness the provenance byte declares.
    """
    x0, x1, y0, y1 = BASELINE_BOX_CM
    columns = ORIGIN_X_CM + np.arange(GRID_PX) * SPACING_CM
    rows = ORIGIN_Y_CM + np.arange(GRID_PX) * SPACING_CM
    bi = np.clip(
        ((columns - x0) / (x1 - x0) * BASELINE_PX - 0.5).round().astype(int), 0, BASELINE_PX - 1
    )
    bj = np.clip(
        ((rows - y0) / (y1 - y0) * BASELINE_PX - 0.5).round().astype(int), 0, BASELINE_PX - 1
    )
    return bi, bj


# --------------------------------------------------------------------------------------
# Stage 5, part one: the world-space bounding box of every water actor.
# --------------------------------------------------------------------------------------


def is_water_class(name: str) -> bool:
    """Whether a class name is one of the world's water actors. A shape, not a list."""
    return name.startswith(WATER_CLASS_PREFIXES) and any(t in name for t in WATER_CLASS_TOKENS)


def _bounds_pair(found: dict[str, bytes]) -> tuple[tuple, tuple] | None:
    """``(Origin, BoxExtent)`` out of an already-parsed tag set, as two double triples."""
    origin, extent = found.get("Origin", b""), found.get("BoxExtent", b"")
    if len(origin) != 24 or len(extent) != 24:
        return None
    return struct.unpack("<3d", origin), struct.unpack("<3d", extent)


def _extended_bounds(view: PackageView) -> tuple[tuple, tuple] | None:
    """A ``StaticMesh``'s own ``ExtendedBounds``: the mesh's local box about its origin."""
    for export in view.exports:
        payload = view.props(export["slot"]).get("ExtendedBounds")
        if not payload:
            continue
        entries, _end = property_tags(payload, view.pkg.names, 0)
        pair = _bounds_pair({name: raw for name, _kind, raw, _value in entries})
        if pair is not None:
            return pair
    return None


def _box_sphere_bounds(payload: bytes, names) -> tuple[tuple, tuple] | None:
    """An ``FBoxSphereBounds``, unwrapping the ``CachedBounds`` container it arrives in."""
    entries, _end = property_tags(payload, names, 0)
    found = {name: raw for name, _kind, raw, _value in entries}
    if "Value" in found:
        return _box_sphere_bounds(found["Value"], names)
    return _bounds_pair(found)


def _agg_geom_box(payload: bytes, names) -> tuple[list[float], list[float]] | None:
    """The union of every convex element's ``ElemBox`` in a cooked ``FKAggregateGeom``.

    This is where an ``FGWaterVolume`` keeps its shape. A cooked BSP brush holds its
    vertices in WORLD space and its component transform is legitimately the identity, so 270
    of these decode with no ``RelativeLocation`` anywhere on the actor. An ``FBox`` is 3
    doubles of min, 3 of max and a validity byte.
    """
    entries, _end = property_tags(payload, names, 0)
    low = [math.inf] * 3
    high = [-math.inf] * 3
    found = 0
    for name, _kind, array, _value in entries:
        if name not in ("ConvexElems", "BoxElems") or len(array) < 4:
            continue
        count = struct.unpack_from("<I", array, 0)[0]
        position = 4
        for _ in range(count):
            elements, position = property_tags(array, names, position)
            for inner, _k, blob, _v in elements:
                if inner == "ElemBox" and len(blob) >= 48:
                    minimum = struct.unpack_from("<3d", blob, 0)
                    maximum = struct.unpack_from("<3d", blob, 24)
                    for axis in range(3):
                        low[axis] = min(low[axis], minimum[axis])
                        high[axis] = max(high[axis], maximum[axis])
                    found += 1
            if position >= len(array):
                break
    return (low, high) if found else None


def _corners_to_world(low, high, transform) -> tuple[list[float], list[float]]:
    """A local box through a world transform, eight corners at a time.

    Corner by corner rather than centre-plus-extent, because a rotated volume's world AABB
    is the box AROUND the rotated box, not the unrotated box moved. 486 of the 837 water
    actors are rotated.
    """
    location, rotation, scale = transform
    out_low = [math.inf] * 3
    out_high = [-math.inf] * 3
    for x in (low[0], high[0]):
        for y in (low[1], high[1]):
            for z in (low[2], high[2]):
                turned = quat_rotate(rotation, (x * scale[0], y * scale[1], z * scale[2]))
                for axis in range(3):
                    value = location[axis] + turned[axis]
                    out_low[axis] = min(out_low[axis], value)
                    out_high[axis] = max(out_high[axis], value)
    return out_low, out_high


class MeshBounds:
    """``ExtendedBounds`` per static mesh, read once each, plus the water plane's own.

    A cache because the plane-backed blueprints all name the same mesh: 215 of them asking
    the container would be 215 package reads for one answer.
    """

    def __init__(self, store, scripts, index) -> None:
        self.store, self.scripts, self.index = store, scripts, index
        self._cache: dict[str, tuple | None] = {}

    def of(self, mesh_path: str) -> tuple[tuple, tuple] | None:
        if mesh_path not in self._cache:
            bounds = None
            package = self.index.path_for(mesh_path)
            if package:
                try:
                    bounds = _extended_bounds(
                        PackageView(self.store.read_path(package), self.scripts)
                    )
                except Exception:
                    bounds = None
            self._cache[mesh_path] = bounds
        return self._cache[mesh_path]

    @property
    def plane(self) -> tuple[tuple, tuple] | None:
        return self.of(WATER_PLANE_MESH)


def _component_box(view: PackageView, slot: int, name: str, meshes: MeshBounds):
    """One component's LOCAL box and where it came from, or ``(None, None)``.

    Four sources, tried in the order they are trustworthy: the component's own
    ``BoxExtent``, a BSP volume's cooked ``BrushBodySetup.AggGeom``, an instanced
    component's ``CachedBounds``, and a ``StaticMeshComponent``'s mesh ``ExtendedBounds``.
    That last falls back to the water plane's when the cooked instance names no mesh, which
    is the normal case here and is flagged in the returned source name rather than hidden.
    """
    props = view.props(slot)
    if len(props.get("BoxExtent", b"")) == 24:
        extent = struct.unpack("<3d", props["BoxExtent"])
        return ([-e for e in extent], list(extent)), "BoxComponent.BoxExtent"
    if name == "BrushComponent":
        setup = view.export_ref(props.get("BrushBodySetup", b""))
        geometry = view.props(setup).get("AggGeom") if setup is not None else None
        box = _agg_geom_box(geometry, view.pkg.names) if geometry else None
        return (box, "BrushBodySetup.AggGeom") if box else (None, None)
    if name in ("InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent"):
        cached = props.get("CachedBounds")
        pair = _box_sphere_bounds(cached, view.pkg.names) if cached else None
        source = "InstancedStaticMeshComponent.CachedBounds"
    elif name == "StaticMeshComponent":
        mesh = view.import_path(props.get("StaticMesh", b"")) if "StaticMesh" in props else None
        pair = meshes.of(mesh) if mesh else None
        source = "StaticMesh.ExtendedBounds"
        if pair is None:
            pair = meshes.plane
            source = "WaterPlane.ExtendedBounds (assumed)"
    else:
        return None, None
    if pair is None:
        return None, None
    origin, extent = pair
    low = [origin[axis] - extent[axis] for axis in range(3)]
    high = [origin[axis] + extent[axis] for axis in range(3)]
    return (low, high), source


def water_actor_box(view: PackageView, actor: int, classes, meshes: MeshBounds):
    """One water actor's world AABB in centimetres, and the box sources it came from.

    The union over every box-like component in the actor's export subtree, each taken to
    world space through its own composed ``AttachParent`` chain.

    The last block is a refusal: a mesh's ``ExtendedBounds`` is centred on the mesh's own
    origin, so an actor whose only box is an assumed plane and which states no transform
    anywhere would land at the world origin -- a parse artefact, not a placement. It cannot
    catch a ``BrushComponent``, whose vertices are already world-space and whose identity
    transform is correct.
    """
    stack = [actor]
    seen: set[int] = set()
    low = [math.inf] * 3
    high = [-math.inf] * 3
    sources: set[str] = set()
    positioned = view.props(actor).get("RelativeLocation") is not None
    while stack:
        slot = stack.pop()
        if slot in seen:
            continue
        seen.add(slot)
        stack.extend(view.children.get(slot, []))
        name = class_name_of(view.class_of.get(slot))
        if name not in WATER_BOX_COMPONENTS:
            continue
        local, source = _component_box(view, slot, name, meshes)
        if local is None:
            continue
        transform, _parent = world_transform(view, slot, classes)
        if transform is None:
            transform = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
        if view.props(slot).get("RelativeLocation") is not None or transform[0] != (0.0, 0.0, 0.0):
            positioned = True
        corner_low, corner_high = _corners_to_world(local[0], local[1], transform)
        for axis in range(3):
            low[axis] = min(low[axis], corner_low[axis])
            high[axis] = max(high[axis], corner_high[axis])
        sources.add(source)
    if not sources or not all(math.isfinite(v) for v in low + high):
        return None, sources
    if not positioned and all("assumed" in source for source in sources):
        return None, set()
    return tuple(low + high), sources


# --------------------------------------------------------------------------------------
# Stage 5, part two: the artwork's plan shape, the boxes' level, and the combine.
# --------------------------------------------------------------------------------------


def artwork_water_mask(store, decoder, image_mod) -> np.ndarray:
    """The game's own map artwork, classified into water, on this file's 1 m grid.

    ``B - R``, because the artwork's water is the only blue thing on it: terrain, cliffs,
    biome tints and the grid are all warm. Nearest-neighbour down to the 1 m grid, because
    8192 px over the same 7500 m box is 0.92 m to the pixel and interpolating a hard-edged
    mask would only invent a soft one.

    The slices come from the container through ``tools/gen_map_image.py``'s reader, not from
    ``map.png``, so this stage does not depend on that generator having been run.
    """
    sheet = np.zeros((SHEET_PX, SHEET_PX), dtype=bool)
    for name in SLICES:
        raw = read_slice(store, name)
        pixels = np.asarray(decode_bc1_rgba(decoder, image_mod, raw, TILE_PX).convert("RGB"))
        blue_over_red = pixels[:, :, 2].astype(np.int16) - pixels[:, :, 0].astype(np.int16)
        col, row = (int(v) for v in name.split("_")[1].split("-"))
        sheet[row * TILE_PX : (row + 1) * TILE_PX, col * TILE_PX : (col + 1) * TILE_PX] = (
            blue_over_red >= WATER_ARTWORK_BLUE_OVER_RED
        )
    index = np.clip((np.arange(GRID_PX) * SHEET_PX / GRID_PX).astype(np.int32), 0, SHEET_PX - 1)
    return sheet[index][:, index]


def water_box_tops(boxes: list[tuple[str, tuple[float, ...]]]) -> tuple[np.ndarray, int]:
    """The highest surface-class box top standing over each texel, in metres, or ``nan``.

    A box's top IS the surface of the volume it bounds, so where several overlap in plan the
    highest is the one visible from above. The save's 23 water extractors all sit inside a
    volume and every one of them stands on its box's top to within 0.005 cm.
    """
    tops = np.full((GRID_PX, GRID_PX), np.nan, np.float32)
    used = 0
    for name, box in boxes:
        if name not in WATER_SURFACE_CLASSES:
            continue
        x0, y0, _z0, x1, y1, z1 = box
        # Vertex-aligned, so a texel is covered when its own point lies inside the box.
        col0 = max(0, math.ceil((x0 - ORIGIN_X_CM) / SPACING_CM))
        col1 = min(GRID_PX, math.floor((x1 - ORIGIN_X_CM) / SPACING_CM) + 1)
        row0 = max(0, math.ceil((y0 - ORIGIN_Y_CM) / SPACING_CM))
        row1 = min(GRID_PX, math.floor((y1 - ORIGIN_Y_CM) / SPACING_CM) + 1)
        if col1 <= col0 or row1 <= row0:
            continue
        used += 1
        window = tops[row0:row1, col0:col1]
        top = np.float32(z1 / 100.0)
        np.maximum(window, top, out=window, where=np.isfinite(window))
        window[~np.isfinite(window)] = top
    return tops, used


def region_mask(name: str) -> np.ndarray | None:
    """One named region of ``data/region_names.json``, on this grid. Independent evidence.

    That table is derived from the game's own ``FGMapAreaTexture`` -- exact area boundaries
    at 1.83 m, downsampled to 256 m -- and from nothing in this pipeline, which is the only
    reason a recall measured against it means anything. ``None`` if the table or the name is
    missing: a gate that cannot find its own reference must say so rather than pass.
    """
    if not REGION_TABLE.is_file():
        return None
    table = json.loads(REGION_TABLE.read_text(encoding="utf-8"))
    letters = {region: key for key, region in table["legend"].items()}
    if name not in letters:
        return None
    letter = letters[name]
    grid = table["region_grid"]
    meta = table["grid_meta"]
    cells = np.array([[1 if ch == letter else 0 for ch in row] for row in grid], dtype=bool)
    columns = ORIGIN_X_CM + np.arange(GRID_PX) * SPACING_CM
    rows = ORIGIN_Y_CM + np.arange(GRID_PX) * SPACING_CM
    ci = np.clip(((columns - meta["x0"]) / meta["cell"]).astype(int), 0, meta["nx"] - 1)
    ri = np.clip(((rows - meta["y0"]) / meta["cell"]).astype(int), 0, meta["ny"] - 1)
    return cells[ri][:, ci]


def water_surface(mask: np.ndarray, boxes: list, height_dm: np.ndarray, prov: np.ndarray) -> dict:
    """The artwork's plan shape given the water volumes' level, and what is left unknown.

    The level of a wet texel is the highest box top over it; where nothing covers it -- 17
    texels of 18.3 million on build 495413 -- the median of its own drawn body's covered
    tops stands in, and a body with no box anywhere is dropped rather than guessed at. Then
    the one gate: where the ground was measured at 1 m and stands above that level there is
    no water, which is a rock in a lake and takes 0.04 km2 off the mask. Where the ground is
    the fill layer or nothing at all no such test is possible in either direction, so the
    texel is water whose depth this file does not know, and ``waterq.u8.z`` says that rather
    than a subtraction implying it.
    """
    tops, used = water_box_tops(boxes)
    covered = np.isfinite(tops)
    labelled, bodies = ndimage.label(mask, structure=np.ones((3, 3), bool))

    level = np.where(mask & covered, tops, np.nan).astype(np.float32)
    orphan = mask & ~covered
    if orphan.any() and bodies:
        where = np.nonzero(mask & covered)
        medians = np.asarray(
            ndimage.median(tops[where], labels=labelled[where], index=np.arange(1, bodies + 1)),
            dtype=np.float32,
        )
        lookup = np.concatenate([[np.nan], medians]).astype(np.float32)
        level[orphan] = lookup[labelled[orphan]]

    terrain_m = np.where(height_dm == hf.NODATA, np.nan, height_dm / hf.DM_PER_M).astype(np.float32)
    # Both cliff values: a depth is knowable wherever the ground under the water was
    # measured at 1 m, and whether a source vertex landed in the texel has nothing to do
    # with that. Listing only 4 would call three quarters of the cliff province
    # depth-unknown over a distinction that exists for the renderer.
    measurable = ((prov == hf.PROV_LANDSCAPE) | np.isin(prov, hf.PROV_CLIFF_VALUES)) & np.isfinite(
        terrain_m
    )
    standing_out = measurable & np.isfinite(level) & (level <= terrain_m)
    level[standing_out] = np.nan

    wet = np.isfinite(level)
    quality = np.where(
        wet, np.where(measurable, hf.WATER_MEASURED, hf.WATER_LEVEL_ONLY), hf.WATER_DRY
    ).astype(np.uint8)
    depths = (level - terrain_m)[wet & measurable]
    return {
        "level_m": level,
        "quality": quality,
        "boxes_rasterised": used,
        "bodies": int(bodies),
        "artwork_texels": int(mask.sum()),
        "uncovered_texels": int(orphan.sum()),
        "orphan_bodies": int((mask & ~covered & ~np.isfinite(level)).sum()),
        "dropped_standing_out_texels": int(standing_out.sum()),
        "water_texels": int(wet.sum()),
        "measured_texels": int((quality == hf.WATER_MEASURED).sum()),
        "level_only_texels": int((quality == hf.WATER_LEVEL_ONLY).sum()),
        "depth_p50_m": round(float(np.median(depths)), 3) if depths.size else None,
        "depth_p90_m": round(float(np.percentile(depths, 90)), 3) if depths.size else None,
    }


def validate_water(surface: dict, mask: np.ndarray, boxes: list) -> dict:
    """The four gates, each measured against something this stage did not make.

    Returns every number whether it passes or not; ``main`` decides what to do about it. A
    gate that could not find its own reference reports ``None`` and is treated as a failure:
    "the check did not run" and "the check passed" are different things.
    """
    wet = surface["quality"] != hf.WATER_DRY
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    x = np.array([n["x"] for n in nodes], float)
    y = np.array([n["y"] for n in nodes], float)
    col = np.clip(np.round((x - ORIGIN_X_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    row = np.clip(np.round((y - ORIGIN_Y_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)

    region = region_mask(WATER_GATE_REGION)
    recall = None
    region_truth = 0
    if region is not None and (region & mask).any():
        region_truth = int((region & mask).sum())
        recall = float((wet & region & mask).sum() / region_truth)

    tops = [box[5] / 100.0 for name, box in boxes if name == WATER_OCEAN_CLASS]
    labelled, count = ndimage.label(mask, structure=np.ones((3, 3), bool))
    ocean_level = None
    ocean_reference = None
    if tops and count:
        sizes = np.bincount(labelled.ravel())
        biggest = int(np.argmax(sizes[1:]) + 1)
        values = surface["level_m"][(labelled == biggest) & wet]
        ocean_reference = float(np.median(tops))
        if values.size:
            ocean_level = float(np.median(values))

    return {
        "dry_node_false_positive": {
            "nodes": len(nodes),
            "called_water": int(wet[row, col].sum()),
            "fraction": round(float(wet[row, col].mean()), 6),
            "gate_max": WATER_FP_MAX,
            "against": str(NODE_TABLE.relative_to(ROOT)).replace("\\", "/"),
            "why": (
                "every static resource node stands on dry ground, so a node the channel "
                "calls water is a false positive with no interpretation needed"
            ),
        },
        "region_recall": {
            "region": WATER_GATE_REGION,
            "artwork_texels": region_truth,
            "recall": None if recall is None else round(recall, 6),
            "gate_min": WATER_SPIRE_RECALL_MIN,
            "against": ("data/region_names.json, the game's own map areas downsampled to 256 m"),
            "why": (
                "this region is where the flatness detector this stage replaced scored "
                "worst -- 35.8% recall against the artwork -- so it is the one that says "
                "whether the replacement actually replaced it"
            ),
        },
        "ocean_level": {
            "assigned_m": None if ocean_level is None else round(ocean_level, 3),
            "box_median_m": None if ocean_reference is None else round(ocean_reference, 3),
            "boxes": len(tops),
            "offset_m": (
                None
                if ocean_level is None or ocean_reference is None
                else round(abs(ocean_level - ocean_reference), 4)
            ),
            "gate_max_m": WATER_OCEAN_TOLERANCE_M,
            "why": (
                f"the largest drawn body is the ocean and every {WATER_OCEAN_CLASS} box "
                "states the ocean's own surface, so the two have to agree or the level is "
                "being taken from the wrong volumes"
            ),
        },
        "artwork_over_a_box": {
            "uncovered_texels": surface["uncovered_texels"],
            "fraction": round(surface["uncovered_texels"] / max(surface["artwork_texels"], 1), 6),
            "gate_max": WATER_UNCOVERED_MAX,
            "why": (
                "the mask and the volumes are two descriptions of one world, so water the "
                "artwork draws where no volume stands means they have come apart -- which "
                "is what a misregistered sheet looks like from here"
            ),
        },
    }


def water_gate_failures(checks: dict) -> list[str]:
    """Which of the four gates did not pass, as sentences. Empty means write the field."""
    failures = []
    node = checks["dry_node_false_positive"]
    if node["fraction"] > node["gate_max"]:
        failures.append(
            f"the channel calls {node['called_water']} of {node['nodes']} static resource "
            f"nodes water ({node['fraction'] * 100:.2f}%), past the {node['gate_max'] * 100:.0f}% "
            "gate. Every one of those nodes stands on dry ground, so the artwork classifier "
            "or the sheet's registration has moved."
        )
    region = checks["region_recall"]
    if region["recall"] is None or region["recall"] < region["gate_min"]:
        measured = "unmeasurable" if region["recall"] is None else f"{region['recall'] * 100:.1f}%"
        failures.append(
            f"{region['region']} recall against the artwork is {measured}, under the "
            f"{region['gate_min'] * 100:.0f}% gate. That region is what exposed the detector "
            "this stage replaced, and it is exposing this one."
        )
    ocean = checks["ocean_level"]
    if ocean["offset_m"] is None or ocean["offset_m"] > ocean["gate_max_m"]:
        failures.append(
            "the ocean's assigned level "
            + (
                "could not be measured at all"
                if ocean["offset_m"] is None
                else f"is {ocean['offset_m']:.2f} m from the median {WATER_OCEAN_CLASS} box top"
            )
            + f", past the {ocean['gate_max_m']} m gate."
        )
    covered = checks["artwork_over_a_box"]
    if covered["fraction"] > covered["gate_max"]:
        failures.append(
            f"{covered['fraction'] * 100:.2f}% of the artwork's water stands over no water "
            f"volume at all, past the {covered['gate_max'] * 100:.0f}% gate. The mask and "
            "the boxes have stopped describing the same world."
        )
    return failures


# --------------------------------------------------------------------------------------
# Composition, validation, and what gets written.
# --------------------------------------------------------------------------------------


def compose(frame: dict, cliffs: dict, baseline_cm: np.ndarray, valid: np.ndarray) -> dict:
    """Fuse the layers into the output grid: fill, then landscape, then cliff over both.

    The fill is everywhere the interface raster says anything, so it goes down first and is
    the answer only where nothing better arrives. The landscape drops in index-aligned over
    its own frame. The cliff overlay then wins any texel where real geometry stands above
    the sculpted ground, and any texel the landscape left as a hole: a cave mouth's rock is
    still a measurement.
    """
    dx, dy = drop_offsets(frame)
    bi, bj = baseline_indices()

    z_m = np.where(valid, baseline_cm / 100.0, np.nan).astype(np.float32)[np.ix_(bj, bi)]
    prov = np.where(np.isnan(z_m), hf.PROV_NODATA, hf.PROV_FILL).astype(np.uint8)

    land_m = np.where(frame["good"], frame["z_cm"] / 100.0, np.nan).astype(np.float32)
    sub_prov = np.where(frame["good"], hf.PROV_LANDSCAPE, hf.PROV_NODATA).astype(np.uint8)

    cliff_m = (cliffs["z_cm"] / 100.0).astype(np.float32)
    take = np.isfinite(cliff_m) & (~np.isfinite(land_m) | (cliff_m > land_m))
    sub_z = np.where(take, cliff_m, land_m)
    # Two cliff values, one layer, equally accurate at 1 m: 5 says a source vertex landed in
    # this texel, 4 says the rasteriser reached it by interpolating the plane of a triangle
    # wider than the texel. The split exists for renders drawing finer than 1 m.
    direct = take & (cliffs["density"] >= DIRECT_SAMPLES_MIN)
    sub_prov = np.where(take, hf.PROV_CLIFF, sub_prov)
    sub_prov = np.where(direct, hf.PROV_CLIFF_DIRECT, sub_prov).astype(np.uint8)
    sub_density = np.where(take, np.minimum(cliffs["density"], 255), 0).astype(np.uint8)

    window = np.isfinite(sub_z)
    z_m[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_z[window]
    prov[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_prov[window]
    density = np.zeros((GRID_PX, GRID_PX), np.uint8)
    density[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_density[window]

    known = np.isfinite(z_m)
    height_dm = np.where(known, np.clip(np.round(z_m * 10.0), -32767, 32767), hf.NODATA)
    error = np.abs(height_dm.astype(np.float32) / 10.0 - z_m)[known]
    cliff = np.isin(prov, hf.PROV_CLIFF_VALUES)
    return {
        "height_dm": height_dm.astype(np.int16),
        "prov": prov,
        "density": density,
        "drop": (dx, dy),
        "coverage": {
            hf.PROV_NAMES[value]: float((prov == value).mean())
            for value in (
                hf.PROV_NODATA,
                hf.PROV_LANDSCAPE,
                hf.PROV_FILL,
                hf.PROV_CLIFF,
                hf.PROV_CLIFF_DIRECT,
            )
        },
        "cliff_texels": int(cliff.sum()),
        "cliff_direct_fraction": float((prov == hf.PROV_CLIFF_DIRECT).sum() / max(cliff.sum(), 1)),
        "density_p50": int(np.median(density[cliff])) if cliff.any() else 0,
        "z_range_m": [float(np.nanmin(z_m)), float(np.nanmax(z_m))],
        "quantisation_max_m": float(error.max()),
        "quantisation_rms_m": float(np.sqrt((error**2).mean())),
    }


def sample_grid(height_dm: np.ndarray, x_cm: np.ndarray, y_cm: np.ndarray) -> np.ndarray:
    """Read the field at world coordinates, in metres, ``nan`` where it knows nothing.

    The same rounding ``Field.texel`` does on the other side, so the number this run
    validates on is the number the server answers with.
    """
    col = np.round((x_cm - ORIGIN_X_CM) / SPACING_CM).astype(int)
    row = np.round((y_cm - ORIGIN_Y_CM) / SPACING_CM).astype(int)
    on = (col >= 0) & (col < GRID_PX) & (row >= 0) & (row < GRID_PX)
    values = height_dm[np.clip(row, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1)]
    return np.where(on & (values != hf.NODATA), values.astype(np.float64) / 10.0, np.nan)


def error_stats(errors: np.ndarray, total: int) -> dict:
    """Median offset, then the spread about it: median absolute, P90, and trimmed RMS.

    The offset is removed because a constant bias is a georeference question and this pass
    guards the decode. The trim is about caves: about 44 nodes sit UNDER the surface, in
    cave mouths, arches and overhangs that no single-valued heightmap can represent, so an
    untrimmed RMS measures the map's topology rather than this file's arithmetic.
    """
    finite = errors[~np.isnan(errors)]
    if finite.size == 0:
        return {"n": 0, "coverage": 0.0}
    offset = float(np.median(finite))
    spread = np.sort(np.abs(finite - offset))
    trimmed = spread[: max(int(spread.size * VALIDATION_TRIM), 1)]
    return {
        "n": int(finite.size),
        "coverage": round(finite.size / total, 4),
        "offset_m": round(offset, 4),
        "medabs_m": round(float(np.median(spread)), 4),
        "p90_m": round(float(np.percentile(spread, 90)), 4),
        "trim90_rms_m": round(float(np.sqrt((trimmed**2).mean())), 4),
        "under_1m": int((spread < 1.0).sum()),
    }


def validate(height_dm: np.ndarray, prov: np.ndarray) -> dict:
    """Measure the built field against the static node table, whole and per layer.

    The whole-field number is the gate; the per-layer ones go in the sidecar so a reading can
    quote the accuracy of the layer that answered it, this field being a fifth of a metre
    good in the middle and four metres good at the edge.
    """
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    x = np.array([n["x"] for n in nodes], float)
    y = np.array([n["y"] for n in nodes], float)
    z = np.array([n["z"] for n in nodes], float) / 100.0
    errors = z - sample_grid(height_dm, x, y)
    col = np.clip(np.round((x - ORIGIN_X_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    row = np.clip(np.round((y - ORIGIN_Y_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    layers = prov[row, col]
    per_layer = {}
    for value in (hf.PROV_LANDSCAPE, hf.PROV_FILL, hf.PROV_CLIFF, hf.PROV_CLIFF_DIRECT):
        pick = layers == value
        per_layer[hf.PROV_NAMES[value]] = error_stats(
            np.where(pick, errors, np.nan), int(pick.sum())
        )
    # The cliff province whole as well as split: the whole is what a field before the split
    # measured, so keeping it is what makes two runs' cliff accuracy comparable.
    cliff = np.isin(layers, hf.PROV_CLIFF_VALUES)
    per_layer["cliff, both"] = error_stats(np.where(cliff, errors, np.nan), int(cliff.sum()))
    return {
        "against": str(NODE_TABLE.relative_to(ROOT)).replace("\\", "/"),
        "nodes": len(nodes),
        "field": error_stats(errors, len(nodes)),
        "per_layer": per_layer,
        "method": (
            "every static resource node's own Z against the field read at its coordinate, "
            "with the median offset removed and the worst 10% trimmed. The trim is not "
            "cosmetic: about 44 nodes sit under the surface in caves, arches and overhangs, "
            "which no single-valued heightmap can represent and which the interface raster "
            "fails by the same test."
        ),
        "gate_m": VALIDATION_TRIM_RMS_MAX_M,
        "reference": (
            "the workflow that proved this pipeline measured 0.368 m trimmed RMS and "
            "0.210 m median absolute on this set; the interface raster alone measures "
            "1.080 m and 0.854 m, and its own scale and offset were fitted on these nodes."
        ),
    }


def accuracy_block(validation: dict) -> dict:
    """What each provenance value means, and how well it was measured to do.

    ``accuracy_m`` is the measured median absolute error where enough nodes fell on that
    layer to mean anything and the layer's own vertical step where they did not, and
    ``accuracy_from`` says which of the two it is.
    """
    derived = {
        hf.PROV_LANDSCAPE: (1.0, LANDSCAPE_SCALE_CM / LANDSCAPE_PER_UNIT / 100.0),
        hf.PROV_CLIFF: (1.0, 0.0),
        hf.PROV_CLIFF_DIRECT: (1.0, 0.0),
        hf.PROV_FILL: (FILL_HORIZONTAL_M, FILL_VERTICAL_M),
    }
    notes = {
        hf.PROV_LANDSCAPE: (
            "the cooked UE Landscape heightfield: a true 1 m grid, 7.8 mm vertical "
            "quantisation, no resampling anywhere between the component and this texel"
        ),
        hf.PROV_CLIFF: (
            "rasterised triangles from a placed rock or cliff, at a texel NO SOURCE VERTEX "
            "landed in: the height is this file's own plane interpolation across a triangle "
            "wider than the texel. Exactly as accurate as value 5 at 1 m, and not a "
            "measurement below it -- which is the only thing the two values distinguish"
        ),
        hf.PROV_CLIFF_DIRECT: (
            "rasterised triangles from a placed rock or cliff, at a texel at least one "
            "source vertex landed in. density.u8.z says how many. This is where a render "
            "finer than 1 m is reading geometry rather than a kernel"
        ),
        hf.PROV_FILL: (
            "the 2048 px HeightData_Test interface raster, outside the landscape frame. "
            "3.66 m horizontally and 3.897 m per quantisation step: this is the old "
            "baseline unchanged, and it is the coarsest thing in the field"
        ),
    }
    out: dict[str, dict] = {
        str(hf.PROV_NODATA): {
            "name": hf.PROV_NAMES[hf.PROV_NODATA],
            "accuracy_m": None,
            "note": (
                "open ocean past the landscape edge, and two cave-mouth blobs. Explicit, "
                "never zero-filled: say nothing here."
            ),
        }
    }
    for value, (horizontal, vertical) in derived.items():
        name = hf.PROV_NAMES[value]
        measured = validation["per_layer"].get(name, {})
        enough = measured.get("n", 0) >= ACCURACY_MIN_SAMPLES
        # A cliff split too thin to believe falls back to the province WHOLE rather than to
        # a derived step: a rasterised triangle has no vertical quantisation, so the derived
        # floor of 0.1 m would be a better number than the layer has ever measured.
        fallback = validation["per_layer"].get("cliff, both", {})
        pooled = value in hf.PROV_CLIFF_VALUES and fallback.get("n", 0) >= ACCURACY_MIN_SAMPLES
        if enough:
            accuracy, source = (
                measured["medabs_m"],
                (
                    f"measured: median absolute error over {measured['n']} static resource "
                    "nodes that fell on this layer"
                ),
            )
        elif pooled:
            accuracy, source = (
                fallback["medabs_m"],
                (
                    f"measured over the cliff province WHOLE ({fallback['n']} nodes), because "
                    f"only {measured.get('n', 0)} fell on this half of it and that is fewer "
                    f"than {ACCURACY_MIN_SAMPLES}. The two halves differ in what a finer render "
                    "may claim, not in how accurate they are at 1 m"
                ),
            )
        else:
            accuracy, source = (
                round(max(vertical, 0.1), 3),
                (
                    f"derived: this layer's own vertical step, because only "
                    f"{measured.get('n', 0)} nodes fell on it and that is fewer than "
                    f"{ACCURACY_MIN_SAMPLES}"
                ),
            )
        out[str(value)] = {
            "name": name,
            "horizontal_m": round(horizontal, 4),
            "vertical_step_m": round(vertical, 4),
            "accuracy_m": accuracy,
            "accuracy_from": source,
            "measured": measured,
            "note": notes[value],
        }
    return out


def build_meta(
    *,
    build_pin: str,
    build_raw: dict,
    sweep: dict,
    frame: dict,
    meshes: dict,
    cliffs: dict,
    field: dict,
    water: dict,
    water_checks: dict,
    validation: dict,
    files: dict,
    decoders: dict[str, str],
    timings: dict,
) -> dict:
    """The sidecar the loader reads, plus the provenance a reader needs to date the field."""
    dx, dy = field["drop"]
    return {
        "description": (
            "A 1 m terrain heightfield of the Satisfactory world, cut from the reader's own "
            "installed game by tools/gen_world_heightmap.py. All of it is local: data/local/ "
            "is gitignored and no terrain raster is ever committed to this repository."
        ),
        "generator": "tools/gen_world_heightmap.py",
        "generator_version": GENERATOR_VERSION,
        "transcribed": datetime.now(UTC).date().isoformat(),
        "grid": {
            "width": GRID_PX,
            "height": GRID_PX,
            "spacing_cm": SPACING_CM,
            "x0_cm": ORIGIN_X_CM,
            "y0_cm": ORIGIN_Y_CM,
            "georeference": (
                f"x_cm = {ORIGIN_X_CM:.0f} + col*{SPACING_CM:.0f}, "
                f"y_cm = {ORIGIN_Y_CM:.0f} + row*{SPACING_CM:.0f}"
            ),
            "alignment": (
                "vertex-aligned: a texel's height belongs to that point exactly, not to a "
                "cell around it, so a reader rounds to the nearest vertex rather than "
                "flooring into a cell"
            ),
            "axes": "game axes -- +X east, +Y south, so row 0 is the northern edge",
        },
        "units": "decimetres above sea level, int16",
        "nodata": hf.NODATA,
        "files": files,
        "provenance": accuracy_block(validation),
        "coverage": {
            **{k: round(v, 6) for k, v in field["coverage"].items()},
            "known": round(1.0 - field["coverage"][hf.PROV_NAMES[hf.PROV_NODATA]], 6),
        },
        "z_range_m": [round(v, 2) for v in field["z_range_m"]],
        "density": {
            "file": hf.DENSITY_NAME,
            "content": ("source vertices per texel, clamped at 255, zero outside the cliff layer"),
            "rule": (
                f"a texel with at least {DIRECT_SAMPLES_MIN} sample is provenance "
                f"{hf.PROV_CLIFF_DIRECT} (direct); below that it is {hf.PROV_CLIFF} "
                "(interpolated). Counted after the facing cull, so a vertex on the "
                "underside of a rock does not make the ground beneath it a measurement"
            ),
            "cliff_texels": field["cliff_texels"],
            "direct_fraction": round(field["cliff_direct_fraction"], 4),
            "median_samples_per_cliff_texel": field["density_p50"],
            "z6_texel_m": round(Z6_TEXEL_M, 4),
            "z7_texel_m": round(Z7_TEXEL_M, 4),
            "why": (
                "a renderer drawing finer than this field's own 1 m spacing has to decide "
                "whether it is resampling a measurement or an interpolant, and nothing "
                "else in the field can tell it. The honest claim this supports is about "
                "DENSITY and never about accuracy: the geometry ladder from the collision "
                "hull to the Nanite leaf is worth half a point of frac_lt_0.25m and moves "
                "p90 by nothing, because the cliff province's error is topological -- a "
                "max-Z field answering with a cave roof over the floor a probe stands on"
            ),
        },
        "container": {
            "quantisation_max_m": round(field["quantisation_max_m"], 4),
            "quantisation_rms_m": round(field["quantisation_rms_m"], 4),
            "why": (
                "int16 decimetres. The rounding costs the RMS above, which is an eighth of "
                "the field's own measured accuracy; int32 would double the file for nothing."
            ),
        },
        "validation": validation,
        "sources": {
            "game": {
                "install": "the reader's own Satisfactory install",
                "licence": (
                    "Coffee Stain Studios' own cooked assets, read locally. Not committed, "
                    "not redistributed, and served to localhost only."
                ),
                "game_version_pinned": build_pin,
                "game_version_raw": {
                    key: build_raw.get(key)
                    for key in ("Changelist", "BranchName", "BuildId", "GameVersion")
                },
            },
            "landscape": {
                "class": "LandscapeComponent",
                "derivation": (
                    f"GrassData height, {LANDSCAPE_N}x{LANDSCAPE_N} uint16 per component; "
                    f"world_z_cm = (h - {LANDSCAPE_ZERO:.0f})/{LANDSCAPE_PER_UNIT:.0f}"
                    f"*{frame['scale_cm']:.0f} + {frame['origin_z_cm']:.0f}"
                ),
                "components": frame["components"],
                "frame": [frame["width"], frame["height"]],
                "frame_origin_cm": [frame["x0_cm"], frame["y0_cm"]],
                "drop_texels": [dx, dy],
                "resampling": (
                    "none. The frame origin sits a whole number of texels from this grid's, "
                    "which the run asserts rather than rounds into, so every landscape "
                    "sample is its own texel"
                ),
                "coverage_of_frame": round(frame["coverage"], 4),
                "holes": {
                    "texels": frame["hole_texels"],
                    "blobs": frame["hole_blobs"],
                    "note": (
                        "raw == 0 inside a component that is present: a landscape hole or "
                        "cave mouth, left as no data rather than read as -255 m"
                    ),
                },
            },
            "cliffs": {
                "class": "StaticMesh render data / BodySetup / FTriangleMeshImplicitObject",
                "recipe": (
                    "the finest description each rock mesh ships, over the set of meshes "
                    "the cooked collision hull defines: the Nanite leaf level where there "
                    "is one, LOD 0 where there is not, the hull itself where neither "
                    "parses. The MESH SET and every placement cull are unchanged from the "
                    "hull-only field, so a before and after differ in triangles alone"
                ),
                "sources": meshes["by_source"],
                "source_order": list(CLIFF_SOURCES),
                "why_not_nanite_only": (
                    "25 rock meshes carry no Nanite resource at all -- sea rocks, corals, "
                    "part of the cave interior set -- and a Nanite-only layer loses about "
                    "365,000 texels against the hull layer while still looking like a field"
                ),
                "why_not_every_mesh": (
                    "extending past the hull-equivalent set costs 1.66 points of "
                    "frac_lt_0.25m and 10.9 m of p90 on 839,506 foliage probes: the 120 "
                    "meshes with no cooked hull are cave pillars, cave holes and merged "
                    "cave floors, and a max-Z field that starts drawing roofs gets worse at "
                    "'where is the ground' no matter how fine its triangles are"
                ),
                "hull_derivation": (
                    "cooked Chaos collision trimesh, found by searching for the 267 (0x10B) "
                    "marker, never at a fixed offset; NumVerts float32 triples then NumTris "
                    "index triples, width chosen by validating max(index) < NumVerts"
                ),
                "nanite_derivation": (
                    "FNaniteResources: root pages inline in the StaticMesh export's tail, "
                    "streaming pages out of the .ubulk the Zen BulkDataMap names, decoded "
                    "in pure Python by core.gameassets.nanite. Leaf clusters only; "
                    "positions and topology only; cluster seams welded at 10 um"
                ),
                "rock_meshes_seen": meshes["wanted"],
                "meshes_decoded": len(meshes["geometry"]),
                "meshes_without_cooked_trimesh": len(meshes["failures"]),
                "closed_manifolds": meshes["closed_manifolds"],
                "closed_manifold_check": (
                    f"{meshes['closed_manifolds']} of {len(meshes['geometry'])} HULLS "
                    "satisfy NumTris == 2*NumVerts - 4 exactly, and "
                    f"{meshes['nanite_closed']} of {meshes['nanite_checked']} Nanite "
                    "decodes have zero boundary edges once cluster seams are welded. The "
                    "rest are the open shells -- cave walls, floors, ceilings and merged "
                    "arch pieces -- and are meant to be. One bit wrong in the strip decode "
                    "shatters the second number into thousands of boundary edges, which is "
                    "what makes it worth computing"
                ),
                "vertices": meshes["verts"],
                "triangles_in_source": meshes["tris"],
                "hull_triangles_in_source": meshes["hull_triangles"],
                "placements_total": cliffs["placements_total"],
                "placements_rasterised": cliffs["placements_used"],
                "placements_dropped": cliffs["dropped"],
                "triangles_rasterised": cliffs["triangles"],
                "vertex_samples_rasterised": cliffs["samples"],
                "triangles_outside_bounds": cliffs["triangles_out_of_bounds"],
                "exclusions": (
                    "NodeMeshActor_C, because predicting a resource node's Z from the mesh "
                    "drawn under it would be circular and the node table is what validates "
                    f"this field; anything whose basename contains '{ARCH_MARK}', because a "
                    "max-Z field puts an arch roof over the ground beneath it and masking "
                    "them improved every metric on all three validation sets; and anything "
                    f"whose scaled extent exceeds {OVERSIZE_CM:.0f} cm, which is the sky "
                    "dome and the ocean shells"
                ),
            },
            "fill": {
                "asset": "/Game/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test",
                "derivation": (
                    f"{BASELINE_PX}x{BASELINE_PX} float16 mip 0; "
                    f"z_cm = {BASELINE_SCALE_CM_PER_RAW:.4f}*raw + {BASELINE_OFFSET_CM:.4f}, "
                    "from a robust fit against the 626 static nodes (569 inliers, 1.07 m RMS)"
                ),
                "nodata_rule": (
                    f"decoded z > {FILL_FLOOR_CM / 100:.0f} m, NOT raw > 0. The blank value "
                    "decodes to about -522 m, so the naive test leaks 138,481 texels of "
                    "blank into the field as a false sea floor"
                ),
                "role": "outside the landscape frame only; the old baseline, unchanged",
            },
            "water": {
                "recipe": (
                    "the game's own map artwork for the plan shape, the cooked water "
                    "volumes' own bounding boxes for the level. Neither source is asked "
                    "for what it does not know: the artwork has no Z at all, and the "
                    "volumes are far too sparse to draw a coastline with"
                ),
                "shape": {
                    "asset": (
                        "/Game/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/Map_<c>-<r>, "
                        "the same four BC1 slices tools/gen_map_image.py draws"
                    ),
                    "classifier": f"blue - red >= {WATER_ARTWORK_BLUE_OVER_RED} on the 8192 sheet",
                    "registration": (
                        "(0, 0) sheet pixels, measured by a +/-2 px sweep rather than "
                        "assumed. The sheet's box and this grid's are the same 7500 m "
                        "square, so the resample is nearest at 0.92 m to the pixel"
                    ),
                    "artwork_water_km2": round(water["artwork_texels"] / 1e6, 4),
                },
                "level": {
                    "actors": sum(sweep["water_actors"].values()),
                    "by_class": dict(sorted(sweep["water_actors"].items())),
                    "with_a_world_box": len(sweep["water"]),
                    "without_a_box": len(sweep["water_boxless"]),
                    "boxless": [
                        {"class": cls, "actor": actor, "cell": cell}
                        for cls, actor, cell in sweep["water_boxless"]
                    ],
                    "box_sources": dict(sorted(sweep["water_box_sources"].items())),
                    "surface_classes": sorted(WATER_SURFACE_CLASSES),
                    "boxes_rasterised": water["boxes_rasterised"],
                    "rule": (
                        "the highest surface-class box top standing over the texel. A box "
                        "top is a surface, so where several overlap in plan the highest is "
                        "the one visible from above; one median per drawn body was measured "
                        "to invent up to 157 m of depth over 0.06 km2, because the ocean "
                        "and its rivers are one drawn shape spanning 141 m of box top"
                    ),
                    "oracle": (
                        "the save's 23 water extractors all sit inside a volume box and "
                        "stand on its top to within 0.005 cm, which is what says a box top "
                        "is the water surface rather than merely near it"
                    ),
                },
                "combine": {
                    "bodies": water["bodies"],
                    "texels_with_no_box_over_them": water["uncovered_texels"],
                    "texels_dropped_as_ground_above_the_level": water[
                        "dropped_standing_out_texels"
                    ],
                    "water_km2": round(water["water_texels"] / 1e6, 4),
                    "depth_measured_km2": round(water["measured_texels"] / 1e6, 4),
                    "depth_unknown_km2": round(water["level_only_texels"] / 1e6, 4),
                    "depth_p50_m": water["depth_p50_m"],
                    "depth_p90_m": water["depth_p90_m"],
                    "unknown_depth_rule": (
                        "where the ground under the water is the fill layer or no data, "
                        "the depth is not knowable and waterq.u8.z says so. Nothing may "
                        "gate on water > terrain there: the fill raster's 3.9 m step "
                        "routinely rounds above a sea surface 17 m down, which reads the "
                        "open ocean as dry"
                    ),
                },
                "validation": water_checks,
                "accuracy_m": 0.05,
                "supersedes": (
                    "a flatness detector over the interface raster, which found 20.4% of "
                    "the sheet as water against the artwork's 39.0% -- 46.7% recall, 35.8% "
                    "over Spire Coast -- and invented plateau lakes on flat mesas. Its "
                    "failures were structural: 3.9 m of quantisation against 2.1 m of "
                    "water, rivers below the raster's resolution, and a fill province where "
                    "the terrain it compared against IS the water surface"
                ),
                "role": (
                    "INFORMATION ONLY. Nothing downstream moves ground because of it, and "
                    "that stays measured rather than assumed: a lake gate built on the old "
                    "detector made the field worse, nodes trim90 0.93 against 0.77."
                ),
            },
        },
        "sweep": {
            "packages": sweep["packages"],
            "unreadable": sweep["unreadable"],
            "malformed_components": sweep["malformed_components"],
            "placements": len(sweep["placements"]),
            "distinct_meshes": len(sweep["meshes"]),
        },
        "decoders": {
            "oodle": {
                "name": "pyooz",
                "version": decoders.get("pyooz", "unknown"),
                "import_name": "ooz",
                "licence": "GPL-3.0",
                "role": (
                    "container block decompression, offline, at generation time only. An "
                    "OPTIONAL dependency: the `gen` extra in pyproject.toml, pinned exactly "
                    "because it decides these bytes, and asked for on the command line -- "
                    "`uv run --extra gen python tools/gen_world_heightmap.py`. It is "
                    "imported at module scope nowhere, and lazily inside one function of "
                    "satisfactory_mcp.core.gameassets.iostore, so the server and the test "
                    "suite run with it absent. No part of it is in the output."
                ),
            },
            "texture": {
                "name": "texture2ddecoder",
                "version": decoders.get("texture2ddecoder", "unknown"),
                "pillow": decoders.get("pillow", "unknown"),
                "role": (
                    "BC1 blocks of the four map slices, for the water channel's plan shape. "
                    "The same two the map image is drawn with, and optional in the same "
                    "way: both are handed to core.gameassets.textures as arguments, so this "
                    "file imports neither at module scope."
                ),
            },
            "container": (
                "satisfactory_mcp.core.gameassets.iostore's IoStore reader, imported by "
                "name. It was tools/gen_world_collectibles.py's, imported by file path, "
                "until the four generators that read the same container came to share one "
                "copy of it."
            ),
            "codec": "satisfactory_mcp.domain.spatial.heightfield, imported so there is one",
        },
        "timings_s": timings,
        "known_defects": [
            (
                "about a fifth of the nominal box is no-data: open ocean past the landscape "
                "edge plus two cave-mouth blobs. Explicit, never zero-filled."
            ),
            (
                "the cave and overhang tail is irreducible. About 44 nodes sit UNDER the "
                "surface, and no single-valued heightmap can represent them; the interface "
                "raster fails 51 by the same test. A two-layer field is the principled fix "
                "and costs one extra rasteriser pass."
            ),
            (
                "21 of the rock meshes ship no cooked trimesh (CTF_UseSimpleAndComplex): "
                "SM_RockPile_*, SM_Cave_Pillar_*, SmoothRock_01 and a few others. Small and "
                "rare; their AggGeom convex hulls are still open for a later pass."
            ),
            (
                f"{len(sweep['water_boxless'])} water actors ship no bounding box at all -- "
                "FGWaterVolume brushes with no cooked BrushBodySetup, one FGRiverSpline, and "
                "two developer backdrop planes with no transform. The artwork mask carries "
                "their plan shape and neighbouring volumes carry their level; what is left "
                f"over is {water['uncovered_texels']} texels of drawn water standing over no "
                "box at all, which take their body's median."
            ),
            (
                "the water channel states a LEVEL everywhere and a DEPTH only where the "
                f"ground under it was measured at 1 m: {water['level_only_texels'] / 1e6:.2f} "
                "km2 of it, most of the ocean, is depth-unknown. It is information only."
            ),
        ],
        "staleness": (
            "sources.game.game_version_pinned is the build this field was cut from, in the "
            "same shape data/resource_nodes.json uses, so a field and a node table from "
            "different builds are comparable on sight. tools/gen_world_heightmap.py refuses "
            "to overwrite this directory unless the sidecar names the build then installed; "
            "--force says it anyway. Terrain moves every patch, and the standing rule is "
            "that a pinned artifact announces drift rather than answering silently wrong."
        ),
    }


def pinned_build(meta: dict) -> str | None:
    """The build an existing sidecar names, or None if it names none."""
    return read_str_path(meta, PIN_PATH)


# --------------------------------------------------------------------------------------
# The cave masks (``--caves``): their own directory, read beside any field version.
# --------------------------------------------------------------------------------------

CAVES_VERSION = 1

#: Foliage under these mesh trees is cave decoration. ``SM_NonCave_*`` stalactites hang
#: under open-air overhangs, a median 12 m below the surface, and would flag cliffs.
CAVE_MARKER_DIRS = ("/Caves/", "/CaveFloor/")
CAVE_MARKER_EXCLUDED = "SM_NonCave_"

#: An ``FGAmbientVolume`` whose ``mAmbientSettings`` basename contains this is a cave.
CAVE_SOUND_MARK = "cave"

CAVE_CELL_CM = 800.0
CAVE_BUFFER_CELLS = 2
CAVE_MASK_PX = int(GRID_PX * SPACING_CM / CAVE_CELL_CM) + 2


def is_cave_marker(mesh: str) -> bool:
    return any(d in mesh for d in CAVE_MARKER_DIRS) and CAVE_MARKER_EXCLUDED not in mesh


def convex_elems(payload: bytes, names: list[str]) -> list[np.ndarray]:
    """``AggGeom.ConvexElems[*].VertexData`` as local (n, 3) float64 arrays."""
    agg, _ = flagged_tags(payload, names, pos=0)
    raw = agg.get("ConvexElems")
    if not raw or len(raw) < 4:
        return []
    out = []
    pos = 4
    for _ in range(struct.unpack_from("<I", raw, 0)[0]):
        element, pos = flagged_tags(raw, names, pos=pos)
        data = element.get("VertexData", b"")
        count = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
        if count and len(data) == 4 + 24 * count:
            out.append(np.frombuffer(data, "<f8", 3 * count, 4).reshape(-1, 3))
    return out


def cave_volume_hulls(view, slot: int, classes) -> list[np.ndarray] | None:
    """A cave ambient volume's brush hulls in world cm, or ``None`` if it is not a cave."""
    settings = view.props(slot).get("mAmbientSettings")
    path = view.import_path(settings) if settings else None
    if not path or CAVE_SOUND_MARK not in path.rsplit("/", 1)[-1].lower():
        return None
    root = root_component(view, slot)
    if root is None:
        return []
    brush = view.props(root).get("BrushBodySetup")
    body_setup = view.export_ref(brush) if brush else None
    transform = world_transform(view, root, classes)[0]
    if body_setup is None or transform is None:
        return []
    tags, _ = flagged_tags(view.pkg.body(view.exports[body_setup]), view.pkg.names)
    if "AggGeom" not in tags:
        return []
    location, quat, scale = transform
    rotation = np.stack([np.array(quat_rotate(quat, tuple(axis))) for axis in np.eye(3)])
    return [
        (local * np.array(scale)) @ rotation + np.array(location)
        for local in convex_elems(tags["AggGeom"], view.pkg.names)
    ]


def sweep_caves(store, scripts, classes, progress: bool = True) -> dict:
    """Cave sound-volume hulls and cave-decoration positions, in one pass over the world."""
    hulls: list[np.ndarray] = []
    markers: list[np.ndarray] = []
    volumes = empty = 0
    started = time.time()
    paths = level_paths(store, contains=LEVEL_DIR, suffix=LEVEL_SUFFIX)
    for index, total, _path, view in walk_levels(store, scripts, paths=paths):
        for slot, class_path in view.class_of.items():
            name = class_name_of(class_path)
            if name == "FGAmbientVolume":
                found = cave_volume_hulls(view, slot, classes)
                if found is None:
                    continue
                volumes += 1
                empty += not found
                hulls += found
            elif name in FOLIAGE_CLASSES:
                instances = foliage_instances(view, slot, classes, wanted=is_cave_marker)
                if instances is not None:
                    markers.append(instances[1][:, 3, :3].copy())
        if progress and index % 1000 == 0:
            print(f"  {index}/{total} packages, {time.time() - started:.0f}s", flush=True)
    return {
        "hulls": hulls,
        "volumes": volumes,
        "volumes_without_hull": empty,
        "markers": np.concatenate(markers) if markers else np.zeros((0, 3)),
        "seconds": time.time() - started,
    }


def _cells(x_cm: np.ndarray, y_cm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    col = np.floor((x_cm - ORIGIN_X_CM) / CAVE_CELL_CM).astype(int)
    row = np.floor((y_cm - ORIGIN_Y_CM) / CAVE_CELL_CM).astype(int)
    return row, col


def build_caves(found: dict, height_dm: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
    """The mask and hull arrays for ``caves.npz``, plus the counts the sidecar records."""
    from scipy.spatial import ConvexHull, QhullError

    mask = np.zeros((CAVE_MASK_PX, CAVE_MASK_PX), np.uint8)
    marks = found["markers"]
    ground = sample_grid(height_dm, marks[:, 0], marks[:, 1])
    under = marks[:, 2] / 100.0 < ground - caves.INSIDE_DEPTH_M
    row, col = _cells(marks[under, 0], marks[under, 1])
    on = (row >= 0) & (row < CAVE_MASK_PX) & (col >= 0) & (col < CAVE_MASK_PX)
    hit = np.zeros(mask.shape, bool)
    hit[row[on], col[on]] = True
    hit = ndimage.binary_dilation(hit, iterations=CAVE_BUFFER_CELLS)
    mask[hit] |= caves.BIT_MARKERS

    planes, starts, boxes = [], [0], []
    degenerate = 0
    centres = (np.arange(CAVE_MASK_PX) + 0.5) * CAVE_CELL_CM
    for verts in found["hulls"]:
        try:
            solid = ConvexHull(verts)
            flat = ConvexHull(verts[:, :2])
        except (QhullError, ValueError):
            degenerate += 1
            continue
        planes.append(solid.equations)
        starts.append(starts[-1] + len(solid.equations))
        boxes.append(np.r_[verts.min(0), verts.max(0)])
        r0, c0 = _cells(verts[:, 0].min(keepdims=True), verts[:, 1].min(keepdims=True))
        r1, c1 = _cells(verts[:, 0].max(keepdims=True), verts[:, 1].max(keepdims=True))
        r0, c0 = max(int(r0[0]), 0), max(int(c0[0]), 0)
        r1, c1 = min(int(r1[0]), CAVE_MASK_PX - 1), min(int(c1[0]), CAVE_MASK_PX - 1)
        if r1 < r0 or c1 < c0:
            continue
        xs = ORIGIN_X_CM + centres[c0 : c1 + 1]
        ys = ORIGIN_Y_CM + centres[r0 : r1 + 1]
        gx, gy = np.meshgrid(xs, ys)
        # Every cell the plan touches, so the reader may skip the 3D test where the bit is off.
        eq = flat.equations
        reach = CAVE_CELL_CM * math.sqrt(0.5)
        touch = gx[..., None] * eq[:, 0] + gy[..., None] * eq[:, 1] + eq[:, 2] <= reach
        mask[r0 : r1 + 1, c0 : c1 + 1][touch.all(-1)] |= caves.BIT_HULL

    arrays = {
        "mask": mask,
        "planes": np.concatenate(planes) if planes else np.zeros((0, 4)),
        "starts": np.array(starts, np.int64),
        "boxes": np.array(boxes, np.float64).reshape(-1, 6),
    }
    cell_km2 = (CAVE_CELL_CM / 1e5) ** 2
    counts = {
        "volumes": found["volumes"],
        "volumes_without_hull": found["volumes_without_hull"],
        "hulls": len(boxes),
        "hulls_degenerate": degenerate,
        "markers": len(marks),
        "markers_under_ground": int(under.sum()),
        "marker_km2": round(float(((mask & caves.BIT_MARKERS) != 0).sum()) * cell_km2, 2),
        "hull_km2": round(float(((mask & caves.BIT_HULL) != 0).sum()) * cell_km2, 2),
        "flagged_km2": round(float((mask != 0).sum()) * cell_km2, 2),
    }
    return arrays, counts


def write_caves(args, build_pin: str, build_raw) -> int:
    """``--caves``: sweep the world for the two cave signals and write ``--caves-dir``."""
    out_dir: Path = args.caves_dir
    if out_dir.exists() and not args.force:
        print(f"{out_dir} already exists. Pass --force to replace it.")
        return 3
    field = hf.load_field(args.field, cache=False)
    if field is None:
        print(
            f"no terrain field at {args.field}: the cave markers are kept only where they "
            "stand under the ground, which needs one. Run this tool without --caves first."
        )
        return 4
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    started = time.time()
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    classes = ClassFacts(store, AssetIndex(store))
    print("sweeping the world for cave sound volumes and cave decoration")
    found = sweep_caves(store, scripts, classes, not args.quiet)
    arrays, counts = build_caves(found, field._height_dm)
    seconds = round(time.time() - started, 1)
    for name, value in counts.items():
        print(f"  {name:>22}: {value}")
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    meta = {
        "description": (
            "Where caves are under this world's single-valued terrain field: a safety flag, "
            "not a floor. Cut from the reader's own install by "
            "tools/gen_world_heightmap.py --caves; never committed."
        ),
        "generator": "tools/gen_world_heightmap.py --caves",
        "caves_version": CAVES_VERSION,
        "transcribed": datetime.now(UTC).strftime("%Y-%m-%d"),
        "sources": {"game": {"game_version_pinned": build_pin, "build_raw": build_raw}},
        "field": {"directory": field.directory.name, "build": field.build},
        "grid": {
            "width": CAVE_MASK_PX,
            "height": CAVE_MASK_PX,
            "cell_cm": CAVE_CELL_CM,
            "x0_cm": ORIGIN_X_CM,
            "y0_cm": ORIGIN_Y_CM,
            "georeference": "cell (row, col) covers x0 + col*cell .. x0 + (col+1)*cell; floored",
        },
        "mask_bits": {
            str(caves.BIT_MARKERS): (
                f"cave decoration under {CAVE_MARKER_DIRS} more than "
                f"{caves.INSIDE_DEPTH_M:g} m below the ground, buffered by "
                f"{CAVE_BUFFER_CELLS} cells"
            ),
            str(caves.BIT_HULL): "every cell the plan of a cave sound volume's convex hull touches",
        },
        "hulls": (
            "planes (n, 4) as nx, ny, nz, d in cm, outward: inside where n.p + d <= 0; "
            "starts (hulls + 1) slices planes per hull; boxes (hulls, 6) min and max xyz"
        ),
        "counts": counts,
        "seconds": seconds,
    }
    written = install_directory(
        out_dir,
        {
            caves.DATA_NAME: buffer.getvalue(),
            caves.META_NAME: json.dumps(meta, indent=1).encode("utf-8"),
        },
    )
    total = sum(written.values())
    print(f"wrote {out_dir}  {total} B  ({total / 1e3:.0f} kB) in {seconds:.0f}s")
    return 0


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="destination directory for the rasters and meta.json (gitignored)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a field this run cannot show was cut from the installed build",
    )
    parser.add_argument("--quiet", action="store_true", help="no per-stage progress lines")
    parser.add_argument(
        "--caves",
        action="store_true",
        help="write only the cave masks to --caves-dir, reading the field at --field",
    )
    parser.add_argument("--caves-dir", type=Path, default=LOCAL_DIR / caves.DIR_NAME)
    parser.add_argument("--field", type=Path, default=LOCAL_DIR / hf.DIR_NAME)
    args = parser.parse_args()

    # Three of the extra: the water channel's plan shape is the map sheet's own BC1 slices,
    # so this generator decodes textures as well as container blocks.
    decoders = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    pyooz_version = decoders["pyooz"]
    import texture2ddecoder
    from PIL import Image

    try:
        build_pin, build_raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc} -- point --game at the install holding FactoryGame/ and Engine/")
        return 1
    print(f"installed build: {build_pin}")
    if args.caves:
        return write_caves(args, build_pin, build_raw)

    out_dir: Path = args.out_dir
    if out_dir.is_dir() and not args.force:
        existing: dict = {}
        try:
            loaded = json.loads((out_dir / hf.META_NAME).read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError, TypeError):
            existing = {}
        pinned = pinned_build(existing)
        if pinned != build_pin:
            print(
                f"{out_dir} already exists and this run cannot show it was cut from the "
                f"installed build.\n"
                f"  installed: {build_pin}\n"
                f"  that field: {pinned or 'no meta.json, or no build recorded in it'}\n"
                "Terrain moves every patch and the repository's own tables are pinned to a "
                "build the new field may no longer agree with, so drift is announced rather "
                "than overwritten. Pass --force to overwrite it anyway."
            )
            return 3

    if not NODE_TABLE.is_file():
        print(
            f"{NODE_TABLE} is not present, so this run could not validate the field it "
            "built. A heightmap nobody measured is not one this file will write."
        )
        return 4

    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the world from {paks} with pyooz {pyooz_version}")
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    index = AssetIndex(store)
    print(
        f"  .utoc v{store.version}, {store.entry_count} entries, "
        f"{store.block_size // 1024} KiB blocks, methods {store.methods}"
    )
    loud = not args.quiet
    timings: dict[str, float] = {}

    # ---- stages 1, 2 and the water actors: one sweep ------------------------------------
    print("sweeping the world's packages for landscape, placements and water volumes")
    classes = ClassFacts(store, index)
    water_meshes = MeshBounds(store, scripts, index)
    sweep = sweep_levels(store, scripts, classes, water_meshes, loud)
    timings["sweep"] = round(sweep["seconds"], 1)
    print(
        f"  {sweep['packages']} packages in {sweep['seconds']:.0f}s: "
        f"{len(sweep['components'])} landscape components, {len(sweep['placements'])} "
        f"placements over {len(sweep['meshes'])} distinct meshes "
        f"({sweep['unreadable']} unreadable, {sweep['malformed_components']} malformed)"
    )
    print(
        f"  {sum(sweep['water_actors'].values())} water actors over "
        f"{len(sweep['water_actors'])} classes, {len(sweep['water'])} with a world box, "
        f"{len(sweep['water_boxless'])} without"
    )

    started = time.time()
    frame = landscape_frame(sweep)
    dx, dy = drop_offsets(frame)
    timings["landscape"] = round(time.time() - started, 1)
    print(
        f"  landscape {frame['width']}x{frame['height']} m at "
        f"({frame['x0_cm']:.0f}, {frame['y0_cm']:.0f}) cm, {frame['coverage'] * 100:.1f}% "
        f"covered, {frame['hole_texels']} hole texels in {frame['hole_blobs']} blobs"
    )
    print(f"  drops into the output grid at texel ({dx}, {dy}), exactly -- no resampling")
    print(f"  {frame['seam_disagreements']} shared edge samples disagree between components")

    # ---- stage 3: cliff collision ------------------------------------------------------
    print("decoding the finest geometry every placed rock ships")
    meshes = read_mesh_geometry(store, scripts, index, sweep["meshes"], loud)
    timings["mesh_decode"] = round(meshes["seconds"], 1)
    print(
        f"  {len(meshes['geometry'])}/{meshes['wanted']} rock meshes decoded in "
        f"{meshes['seconds']:.0f}s: {meshes['verts']} vertices, {meshes['tris']} triangles "
        f"({meshes['hull_triangles']} in the hulls they replace), sources "
        f"{meshes['by_source']}"
    )
    print(
        f"  {meshes['closed_manifolds']} hulls satisfy the Euler relation; "
        f"{meshes['nanite_closed']}/{meshes['nanite_checked']} Nanite decodes have zero "
        "boundary edges after welding"
    )
    if not meshes["geometry"]:
        print(
            "not one rock mesh decoded. The cooked collision layout changed, which is the "
            "whole of what makes this field better than the interface raster. Refusing."
        )
        return 5
    print("rasterising them into a 1 m max-Z overlay")
    cliffs = rasterise_cliffs(sweep, meshes["geometry"], frame, loud)
    timings["rasterise"] = round(cliffs["seconds"], 1)
    print(
        f"  {cliffs['placements_used']}/{cliffs['placements_total']} placements, "
        f"{cliffs['triangles'] / 1e6:.1f} M triangles in {cliffs['seconds']:.0f}s; "
        f"dropped {cliffs['dropped']}"
    )

    # ---- stage 4: fill -----------------------------------------------------------------
    started = time.time()
    baseline_cm, baseline_valid = read_baseline(store)
    timings["fill"] = round(time.time() - started, 1)
    print(f"  interface raster decoded, {baseline_valid.mean() * 100:.1f}% of it says something")

    # ---- compose -----------------------------------------------------------------------
    started = time.time()
    field = compose(frame, cliffs, baseline_cm, baseline_valid)
    timings["compose"] = round(time.time() - started, 1)
    for name, fraction in field["coverage"].items():
        print(f"  {name:>10}: {fraction * 100:6.2f}% of the box")
    print(
        f"  z range {field['z_range_m'][0]:.1f} .. {field['z_range_m'][1]:.1f} m; "
        f"int16-decimetre quantisation RMS {field['quantisation_rms_m']:.4f} m"
    )
    print(
        f"  cliff province {field['cliff_texels']} texels, "
        f"{field['cliff_direct_fraction'] * 100:.1f}% with a source vertex in them "
        f"(median {field['density_p50']} samples per cliff texel)"
    )

    # ---- the top overlay: arches and foliage boulders over the ground -------------------
    print("rasterising arches and foliage boulders for the top plane")
    top = rasterise_top(sweep, frame, store, scripts, index, loud)
    timings["top"] = round(top["seconds"], 1)
    top_dm, top_raised = compose_top(field["height_dm"], frame, top)
    print(
        f"  {top['arch_placements']} arch placements, {top['foliage_instances']} foliage "
        f"instances {top['foliage_by_mesh']}; raised {top_raised} texels over the ground"
    )

    # ---- stage 5: water, after the terrain it is measured against ----------------------
    print("classifying the map artwork's water and levelling it on the water volumes")
    started = time.time()
    mask = artwork_water_mask(store, texture2ddecoder, Image)
    water = water_surface(mask, sweep["water"], field["height_dm"], field["prov"])
    water_checks = validate_water(water, mask, sweep["water"])
    timings["water"] = round(time.time() - started, 1)
    print(
        f"  artwork water {water['artwork_texels'] / 1e6:.3f} km2 over "
        f"{water['bodies']} bodies; {water['boxes_rasterised']} surface boxes rasterised"
    )
    print(
        f"  channel {water['water_texels'] / 1e6:.3f} km2: "
        f"{water['measured_texels'] / 1e6:.3f} km2 with a measured depth, "
        f"{water['level_only_texels'] / 1e6:.3f} km2 depth-unknown; dropped "
        f"{water['dropped_standing_out_texels'] / 1e6:.3f} km2 where the ground stands above it"
    )
    node_check = water_checks["dry_node_false_positive"]
    region_check = water_checks["region_recall"]
    ocean_check = water_checks["ocean_level"]
    covered_check = water_checks["artwork_over_a_box"]
    print(
        f"    dry-node false positives {node_check['called_water']}/{node_check['nodes']} "
        f"= {node_check['fraction'] * 100:.2f}% (gate {node_check['gate_max'] * 100:.0f}%)"
    )
    print(
        f"    {region_check['region']} recall "
        + (
            "unmeasurable"
            if region_check["recall"] is None
            else f"{region_check['recall'] * 100:.2f}%"
        )
        + f" (gate {region_check['gate_min'] * 100:.0f}%)"
    )
    print(
        f"    ocean level {ocean_check['assigned_m']} m against {ocean_check['boxes']} box "
        f"tops at {ocean_check['box_median_m']} m: offset {ocean_check['offset_m']} m "
        f"(gate {ocean_check['gate_max_m']} m)"
    )
    print(
        f"    artwork water over no box {covered_check['uncovered_texels']} texels "
        f"= {covered_check['fraction'] * 100:.4f}% (gate {covered_check['gate_max'] * 100:.0f}%)"
    )
    failures = water_gate_failures(water_checks)
    if failures:
        for sentence in failures:
            print(f"  {sentence}")
        print("Refusing to write a water channel that does not pass its own gates.")
        return 7

    started = time.time()
    validation = validate(field["height_dm"], field["prov"])
    timings["validate"] = round(time.time() - started, 1)
    whole = validation["field"]
    print(
        f"  validated on {whole['n']}/{validation['nodes']} nodes: trimmed RMS "
        f"{whole['trim90_rms_m']:.3f} m, median absolute {whole['medabs_m']:.3f} m, "
        f"P90 {whole['p90_m']:.2f} m, {whole['under_1m']} within a metre"
    )
    for name, stats in validation["per_layer"].items():
        if stats["n"]:
            print(
                f"    {name:>10}: n={stats['n']:3d} medabs {stats['medabs_m']:.3f} m, "
                f"trimmed RMS {stats['trim90_rms_m']:.3f} m"
            )
    if whole["trim90_rms_m"] > VALIDATION_TRIM_RMS_MAX_M:
        print(
            f"trimmed RMS is {whole['trim90_rms_m']:.3f} m against a gate of "
            f"{VALIDATION_TRIM_RMS_MAX_M} m. Something in the decode moved: the workflow "
            "that proved this pipeline measured 0.368 m, and a field this far out would be "
            "a plausible-looking raster that is quietly metres wrong. Refusing to write."
        )
        return 6

    terrain_check = validate_terrain(frame, field["prov"])
    print(
        f"  bare terrain on {terrain_check['n']} landscape nodes: median absolute "
        f"{terrain_check['medabs_m']} m (gate {TERRAIN_NODE_MEDIAN_MAX_M} m)"
    )
    if terrain_check["medabs_m"] is None or terrain_check["medabs_m"] > TERRAIN_NODE_MEDIAN_MAX_M:
        print("the bare terrain plane does not sit on the nodes. Refusing to write it.")
        return 8

    started = time.time()
    water_dm = np.where(
        np.isfinite(water["level_m"]),
        np.clip(np.round(np.nan_to_num(water["level_m"]) * hf.DM_PER_M), -32767, 32767),
        hf.NODATA,
    ).astype(np.int16)
    payload = {
        hf.HEIGHT_NAME: hf.encode_i16(field["height_dm"]),
        hf.PROV_NAME: hf.encode_u8(field["prov"]),
        hf.WATER_NAME: hf.encode_i16(water_dm),
        hf.WATER_QUALITY_NAME: hf.encode_u8(water["quality"]),
        hf.DENSITY_NAME: hf.encode_u8(field["density"]),
        hf.TERRAIN_NAME: hf.encode_u16(frame["raw"]),
        hf.TOP_NAME: hf.encode_i16(top_dm),
    }
    timings["encode"] = round(time.time() - started, 1)
    files = {
        hf.HEIGHT_NAME: {
            "content": f"{GRID_PX}x{GRID_PX} int16 decimetres, row-delta then zlib",
            "bytes": len(payload[hf.HEIGHT_NAME]),
        },
        hf.PROV_NAME: {
            "content": (
                "which layer answered each texel: 0 no-data, 1 landscape, 3 fill, "
                "4 cliff geometry interpolated across a triangle wider than the texel, "
                "5 cliff geometry with at least one source vertex in the texel. A reader "
                "that knows only 4 sees 5 as 'not landscape, not fill, not no-data', which "
                "is what 4 meant before the split. zlib, no delta"
            ),
            "bytes": len(payload[hf.PROV_NAME]),
        },
        hf.DENSITY_NAME: {
            "content": (
                "source vertices per texel over the cliff layer, clamped at 255, zero "
                "elsewhere. The interface between the geometry and anything that draws it. "
                "zlib, no delta"
            ),
            "bytes": len(payload[hf.DENSITY_NAME]),
        },
        hf.WATER_NAME: {
            "content": "water surface Z, same grid, same no-data. Information only",
            "bytes": len(payload[hf.WATER_NAME]),
        },
        hf.WATER_QUALITY_NAME: {
            "content": (
                f"{hf.WATER_DRY} dry, {hf.WATER_MEASURED} water with a depth measured "
                f"against 1 m terrain, {hf.WATER_LEVEL_ONLY} water whose level is known and "
                "whose depth is not. zlib, no delta"
            ),
            "bytes": len(payload[hf.WATER_QUALITY_NAME]),
        },
        hf.TERRAIN_NAME: {
            "content": (
                f"{frame['width']}x{frame['height']} uint16 raw landscape units on terrain_grid, "
                "0 = hole or outside the landscape, row-delta over the int16 bits then zlib"
            ),
            "bytes": len(payload[hf.TERRAIN_NAME]),
        },
        hf.TOP_NAME: {
            "content": (
                f"{GRID_PX}x{GRID_PX} int16 decimetres: height.i16.z max-folded with arch and "
                "foliage-boulder collision trimeshes, row-delta then zlib"
            ),
            "bytes": len(payload[hf.TOP_NAME]),
        },
    }
    meta = build_meta(
        build_pin=build_pin,
        build_raw=build_raw,
        sweep=sweep,
        frame=frame,
        meshes=meshes,
        cliffs=cliffs,
        field=field,
        water=water,
        water_checks=water_checks,
        validation=validation,
        files=files,
        decoders=decoders,
        timings=timings,
    )
    meta["planes"] = {
        "ground": hf.HEIGHT_NAME,
        "terrain": hf.TERRAIN_NAME,
        "top": hf.TOP_NAME,
    }
    meta["terrain_grid"] = {
        "width": frame["width"],
        "height": frame["height"],
        "spacing_cm": frame["scale_cm"],
        "x0_cm": frame["x0_cm"],
        "y0_cm": frame["y0_cm"],
        "zero": LANDSCAPE_ZERO,
        "units_per_m": LANDSCAPE_PER_UNIT * 100.0 / LANDSCAPE_SCALE_CM,
        "offset_m": frame["origin_z_cm"] / 100.0,
        "georeference": "x_cm = x0_cm + col*spacing_cm; z_m = (raw - zero) / units_per_m + offset_m",
        "seam_disagreements": frame["seam_disagreements"],
        "seam_rule": "stitched in sweep order; a later component overwrites the shared edge",
        "validation": terrain_check,
    }
    meta["top"] = {
        key: top[key]
        for key in (
            "arch_placements",
            "foliage_instances",
            "foliage_by_mesh",
            "meshes_without_trimesh",
            "triangles",
        )
    }
    meta["top"]["raised_texels"] = top_raised
    payload[hf.META_NAME] = json.dumps(meta, indent=1).encode("utf-8")
    written = install_directory(out_dir, payload)
    total = sum(written.values())
    print(f"wrote {out_dir}  {total} B  ({total / 1e6:.1f} MB)")
    for name, size in written.items():
        print(f"  {name:>14}  {size:>10} B")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
