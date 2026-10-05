"""The level sweep: foliage, the landscape frame and the baseline raster."""

from __future__ import annotations

import struct
import time

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.mesh import is_water_class, water_actor_box
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    class_name_of,
    property_tags,
    quat_rotate,
    read_int32,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.gameassets.textures import raw_mip_sizes

__all__ = [
    "BASELINE_BYTES",
    "BASELINE_MIPS",
    "BASELINE_OFFSET_CM",
    "BASELINE_PATH",
    "BASELINE_PX",
    "BASELINE_SCALE_CM_PER_RAW",
    "FILL_FLOOR_CM",
    "FOLIAGE_CLASSES",
    "LANDSCAPE_COMPONENT_QUADS",
    "LANDSCAPE_N",
    "LANDSCAPE_ORIGIN_Z_CM",
    "LANDSCAPE_PER_UNIT",
    "LANDSCAPE_SCALE_CM",
    "LANDSCAPE_SECTION_ORIGIN",
    "LANDSCAPE_ZERO",
    "LEVEL_DIR",
    "LEVEL_SUFFIX",
    "SEAM_DISAGREEMENT_MAX",
    "TOP_FOLIAGE_MESHES",
    "TRANSFORM_TOLERANCE",
    "decode_baseline",
    "drop_offsets",
    "flagged_tags",
    "foliage_instances",
    "instance_matrices",
    "is_top_foliage",
    "landscape_frame",
    "read_baseline",
    "sweep_levels",
]


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


#: ``LandscapeSectionOffset - location / scale`` on every proxy, in landscape quads. The
#: terrain plane's georeference is derived from it, so a cook that moves it must fail here.
LANDSCAPE_SECTION_ORIGIN = 508.0

#: Quads per landscape component: neighbouring components share one edge row of samples.
LANDSCAPE_COMPONENT_QUADS = LANDSCAPE_N - 1

#: Shared edge samples on which two components' GrassData disagree. 46 on build 502094;
#: far more means the stitch is reading something other than heights.
SEAM_DISAGREEMENT_MAX = 100


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


# --------------------------------------------------------------------------------------
# Stages 1 and 2: one sweep of the world's packages, two harvests out of it.
# --------------------------------------------------------------------------------------


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


def sweep_levels(
    store, scripts, classes, meshes, progress: bool = True, extra_foliage=None, read_actor=None
) -> dict:
    """One pass over every ``*.umap`` of the world: landscape, placements, water actors.

    All three harvests need the same ``PackageView`` of the same 4,521 packages, and
    building that view is the whole cost of the pass, so they share it. Returns raw material
    and nothing interpreted. Foliage ``extra_foliage`` accepts lands in ``extra_foliage``;
    whatever ``read_actor(view, slot, class path, classes)`` returns for a level actor, in
    ``actors``.
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
    extra: dict[str, list[np.ndarray]] = {}
    actors: list = []
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
            if read_actor is not None and view.outer_of.get(slot) in view.level_slots:
                found = read_actor(view, slot, class_path, classes)
                if found is not None:
                    actors.append(found)
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
                found = foliage_instances(
                    view,
                    slot,
                    classes,
                    wanted=lambda m: is_top_foliage(m) or bool(extra_foliage and extra_foliage(m)),
                )
                if found is not None:
                    harvest = foliage if is_top_foliage(found[0]) else extra
                    harvest.setdefault(found[0], []).append(found[1])
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
        "extra_foliage": {mesh: np.concatenate(parts) for mesh, parts in extra.items()},
        "actors": actors,
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
