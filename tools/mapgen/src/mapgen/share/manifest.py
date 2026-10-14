"""What the shared site's two manifests say, built from the registry and the layers' sidecars.

Everything that reaches ``data/maps.json`` and ``data/site.json`` is copied key by key from
the allowlists here, so a field the registry or a sidecar grows later stays out until it is
added on purpose. ``validate`` checks a finished document against the same lists.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from mapgen.common import Refusal
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from satisfactory_mcp.domain.maps import axes, pyramids, registry

__all__ = [
    "LIGHT_SCHEMA",
    "MAPS_SCHEMA",
    "PARTS",
    "SITE_SCHEMA",
    "LayerPlan",
    "TreeCopy",
    "default_ids",
    "maps_document",
    "plan_layer",
    "render_id",
    "validate",
]

#: What an export can hold of a layer: the live light (unlit colour, trees, normals and
#: horizons), the baked 1x tiles WebGL-less browsers draw, and their @2x twins.
PARTS = ("lit", "baked", "dense")

#: The exit code of an export refused before it wrote anything.
REFUSED = 1

#: A layer id as the registry writes them, and the only shape a folder name is built from.
LAYER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")

#: A schema is a dict of allowed keys (``"*"``: any id), a one-item list for a list of that,
#: or ``None`` for a scalar or a list of scalars.
Schema = dict[str, "Schema"] | list["Schema"] | None

HEADER_SCHEMA: Schema = {
    "X-Map-Bounds-M": None,
    "X-Map-Layer": None,
    "X-Map-Tile-Px": None,
    "X-Map-Tile-Max-Z": None,
    "X-Map-Build": None,
    "X-Map-Tile-2x-Px": None,
    "X-Map-Tile-2x-Max-Z": None,
    "X-Map-Light": None,
}

#: The light model keys the page's shader reads (frontend map/litlayer.ts ``LightHeader``).
LIGHT_MODEL_KEYS = (
    "dirs",
    "normalise_min_el",
    "shadow_soft_deg",
    "shadow_floor",
    "shadow_floor_knee",
    "shadow_fill",
    "hz_cells",
    "crown_cell",
    "hz_gutter",
    "titan_cell",
    "ao_cell",
    "canopy_cell",
    "canopy_floor_deg",
    "band_cell",
)
LIGHT_PARAM_KEYS = ("space", "ambient", "sky", "sun", "tone_knee", "tone_white", "crowns")

LIGHT_SCHEMA: Schema = {
    "build": None,
    "max_z": None,
    "unlit_max_z": None,
    "params": dict.fromkeys(LIGHT_PARAM_KEYS),
    "baked_sun": None,
    "model": {
        **dict.fromkeys(LIGHT_MODEL_KEYS),
        "spans": {"path_el_deg": None},
        "trees": {},
    },
    "parts": {"trees": {"max_z": None, "sparse": None}},
}

MAPS_ROW_SCHEMA: Schema = {
    "id": None,
    "title": None,
    "name": None,
    "tone": None,
    "layer": None,
    "kind": None,
    "status": None,
    "in_switcher": None,
    "default": None,
    "size_px": None,
    "max_z": None,
    "freshness": {"stale": None},
}
MAPS_SCHEMA: Schema = {
    "version": None,
    "default": None,
    "types": [MAPS_ROW_SCHEMA],
    "jobs": None,
    "plain_tone": None,
}
SITE_SCHEMA: Schema = {
    "version": None,
    "render": None,
    "layers": {
        "*": {
            "headers": HEADER_SCHEMA,
            "dir": None,
            "light": None,
            "suffix": {"unlit": None, "trees": None},
            "trees": None,
        }
    },
}


def validate(doc: JsonValue, schema: Schema, where: str = "") -> list[str]:
    """Every key in ``doc`` that ``schema`` does not allow, and every value of the wrong shape."""
    if schema is None:
        items = doc if isinstance(doc, list) else [doc]
        bad = [i for i in items if isinstance(i, (dict, list))]
        return [f"{where or '/'}: a nested value where a plain one belongs"] if bad else []
    if isinstance(schema, list):
        if not isinstance(doc, list):
            return [f"{where}: not a list"]
        return [p for i, item in enumerate(doc) for p in validate(item, schema[0], f"{where}[{i}]")]
    if not isinstance(doc, dict):
        return [f"{where}: not an object"]
    found: list[str] = []
    for key, value in doc.items():
        if key in schema:
            found.extend(validate(value, schema[key], f"{where}/{key}"))
        elif "*" in schema and LAYER_ID.match(key):
            found.extend(validate(value, schema["*"], f"{where}/{key}"))
        else:
            found.append(f"{where}/{key}: key not on the allowlist")
    return found


def _pick(source: JsonObject, keys: tuple[str, ...]) -> JsonObject:
    return {k: source[k] for k in keys if k in source}


def light_header(raw: pyramids.LightHeader, max_z: int) -> JsonObject:
    """``X-Map-Light`` cut to what the shader reads, every depth capped at ``max_z``."""
    model = raw["model"]
    spans = pyramids.json_object(model.get("spans"))
    trees = pyramids.json_object(raw["parts"].get("trees"))
    kept: JsonObject = _pick(model, LIGHT_MODEL_KEYS)
    if "path_el_deg" in spans:
        kept["spans"] = {"path_el_deg": spans["path_el_deg"]}
    if "trees" in model:
        kept["trees"] = {}  # the page reads only that the block is there
    out: JsonObject = {
        "build": raw["build"],
        "max_z": min(raw["max_z"], max_z),
        "unlit_max_z": min(raw["unlit_max_z"], max_z),
        "params": _pick(pyramids.json_object(raw["params"]), LIGHT_PARAM_KEYS),
        "baked_sun": raw["baked_sun"],
        "model": kept,
        "parts": {},
    }
    if trees:
        out["parts"] = {
            "trees": {"max_z": min(_int(trees.get("max_z")), max_z), "sparse": trees.get("sparse")}
        }
    return out


def _int(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _tag(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class TreeCopy:
    """One tile tree to copy: from ``source``, levels 0..``max_z``, to ``target`` in the export."""

    source: Path
    target: str
    max_z: int


@dataclass
class LayerPlan:
    """One layer's ``site.json`` row, its tile trees, and its sparse trees tree if it has one."""

    id: str
    dir: str
    headers: dict[str, str]
    light: str = ""
    suffix: dict[str, str] = field(default_factory=dict[str, str])
    copies: list[TreeCopy] = field(default_factory=list[TreeCopy])
    sparse: TreeCopy | None = None
    #: The ``trees`` bitmap, set once the sparse tree is copied.
    trees: str | None = None

    def row(self) -> JsonObject:
        out: JsonObject = {
            "headers": dict[str, JsonValue](self.headers),
            "dir": self.dir,
            "light": self.light,
            "suffix": dict[str, JsonValue](self.suffix),
        }
        if self.trees is not None:
            out["trees"] = self.trees
        return out


def default_ids() -> list[str]:
    """The ready, listed types drawn in the same render set as the default one."""
    view = registry.view()
    chosen = view["default"]
    rows = {row["id"]: row for row in view["types"]}
    if chosen not in rows:
        raise Refusal(REFUSED, "no default map type is set; name the layers with --ids")
    base = rows[chosen]["entry"]["dir"].split("/")[0]
    return [
        ident
        for ident, row in rows.items()
        if row["status"] == "ready"
        and row["entry"].get("in_switcher", True)
        and row["entry"]["kind"] == "render"
        and row["entry"]["dir"].startswith(base + "/")
    ]


def _light_folder(root: Path) -> str:
    meta = pyramids.sidecar_meta_block(root / pyramids.MAP_RENDER_SIDECAR_NAME)
    tiles = pyramids.json_object(meta.get("tiles"))
    model = pyramids.json_object(meta.get("light"))
    return "t/light-" + _tag(model.get("digest"), tiles.get("count"), tiles.get("bytes"))


def plan_layer(ident: str, max_z: int, parts: set[str]) -> LayerPlan:
    """What one layer contributes: its capped probe headers, its folders, the trees to copy."""
    if not LAYER_ID.match(ident):
        raise Refusal(REFUSED, f"{ident!r} is not a layer id")
    folder = pyramids.layer_dir(ident)
    if folder is None:
        raise Refusal(REFUSED, f"{ident} is not a ready map type; see the Maps tab in Settings")
    pyramid = pyramids.map_pyramid(ident)
    headers = pyramids.pyramid_headers(ident, pyramid)
    lit = pyramids.light(ident) if "lit" in parts else None
    here = "t/" + ident + "-" + _tag(pyramid["build"], lit["header"]["build"] if lit else "")
    headers["X-Map-Tile-Max-Z"] = str(min(pyramid["max_z"], max_z))
    dense = "dense" in parts and pyramid["max_2x_z"] is not None
    if dense:
        headers["X-Map-Tile-2x-Max-Z"] = str(min(pyramid["max_2x_z"] or 0, max_z))
    else:
        headers.pop("X-Map-Tile-2x-Px", None)
        headers.pop("X-Map-Tile-2x-Max-Z", None)
    plan = LayerPlan(ident, here, headers)
    if "baked" in parts:
        plan.copies.append(TreeCopy(folder / "tiles", here + "/tiles", pyramid["max_z"]))
    if dense:
        plan.copies.append(
            TreeCopy(folder / "tiles@2x", here + "/tiles@2x", pyramid["max_2x_z"] or 0)
        )
    if lit is not None:
        header = light_header(lit["header"], max_z)
        headers["X-Map-Light"] = json.dumps(header, separators=(",", ":"))
        light_dir = _light_folder(lit["root"])
        plan.light = light_dir
        plan.suffix = {"unlit": lit["suffix"]["unlit"], "trees": lit["suffix"]["trees"]}
        plan.copies.append(TreeCopy(lit["root"] / "tiles", light_dir, lit["max_z"]))
        plan.copies.append(TreeCopy(lit["unlit"], here + "/unlit", lit["unlit_max_z"]))
        if lit["trees"] is not None:
            plan.sparse = TreeCopy(lit["trees"], here + "/trees", lit["trees_max_z"])
            plan.copies.append(plan.sparse)
    return plan


def maps_document(ids: list[str]) -> JsonObject:
    """``data/maps.json``: the exported types as the page's switcher reads them, nothing else."""
    view = registry.view()
    rows = {row["id"]: row for row in view["types"]}
    chosen = view["default"] if view["default"] in ids else ids[0]
    types: list[JsonValue] = []
    for ident in ids:
        row = rows[ident]
        entry = row["entry"]
        types.append(
            {
                "id": ident,
                "title": row["title"].replace(" ★", "").strip(),
                "name": row["name"],
                "tone": axes.style_tone(row["axes"]),
                "layer": entry.get("layer") or "",
                "kind": entry.get("kind") or "render",
                "status": "ready",
                "in_switcher": True,
                "default": ident == chosen,
                "size_px": entry.get("size_px"),
                "max_z": entry.get("max_z"),
                "freshness": {"stale": []},
            }
        )
    return {"version": 1, "default": chosen, "types": types, "jobs": [], "plain_tone": "dark"}


def render_id(folders: list[str]) -> str:
    """A short id for one export: the digest of every tile folder it points at."""
    return _tag(*sorted(folders))
