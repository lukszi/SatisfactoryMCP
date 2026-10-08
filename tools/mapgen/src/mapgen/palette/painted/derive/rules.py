"""Which game data each calibration entry is derived from, and how.

Every key of the palette's ``calibration`` block is walked and its material kind picks a rule; a
rule returns a linear albedo, the places that vote on its light, and the assets behind it. A key
no rule covers comes back with the reason. docs/map/calibration.md section 31.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from mapgen.colour import linear_from_oklab, oklab
from mapgen.gamedata.ground.landscape_albedo import (
    CANOPY_TEXTURE,
    LAYERS,
    MATERIAL,
    ROCK_TEXTURES,
    TEXTURES,
)
from mapgen.palette.painted.calibration import weighted_median
from mapgen.palette.painted.derive.scene import STRIDE, Scene
from mapgen.palette.painted.shapes import CalibrationArea, CalibrationStyle
from mapgen.palette.painted.trees import CANOPY_GREY, HUE_GATE_DEG, hue_gate
from satisfactory_mcp.core.arrays import BoolMask, F64Grid

__all__ = [
    "DESERT_ROCK_LIGHT",
    "Derivation",
    "entries",
    "scope_key",
    "untargeted_layers",
]

#: A texel is a layer's own at this share of its blend, as the painter measures.
PURE_SHARE = 0.7
MIN_TEXELS = 50
MIN_TREES = 20
BLUE_PALM = "BluePalm"
CORAL_CAPS = "MI_CoralTreeSmall_Platforms"
SHELLS = ("MI_Bigshell_01", "PlateauShell_Inst", "SmallShell_Inst")
#: The volume whose light the desert rock stands in: most of its placements are inside it.
DESERT_ROCK_LIGHT = "Atmosphere_DuneDesert"

_GAME = "/Game/FactoryGame/"
_BAKE = (
    f"{_GAME}Map/GameLevel01/Persistent_Level: LandscapeStreamingProxy_*_BaseColor "
    "(HLOD virtual texture, mip 0)"
)
_WEIGHTS = "LandscapeComponent weightmaps of every /GameLevel01/ level"
_WATER = (
    "water: the game's water absorbs but does not scatter, so its colour is the sky, "
    "clouds and fog, which the cooked assets do not expose"
)

_Where = tuple[F64Grid, F64Grid] | None


@dataclass
class Derivation:
    """One calibration key's rule and its result: an albedo, or the reason there is none."""

    key: str
    kind: str = ""
    rule: str = ""
    albedo: F64Grid | None = None
    where: _Where = None
    light: str | None = None
    assets: list[str] = field(default_factory=list[str])
    params: dict[str, list[float]] = field(default_factory=dict[str, list[float]])
    samples: str = ""
    error: str | None = None


def scope_key(areas: Sequence[str]) -> str:
    """An area entry's key prefix: ``areas[DuneDesert,DesertCanyons]``."""
    return "areas[" + ",".join(a.removeprefix("Area_") for a in areas) + "]"


def _rounded(values: Sequence[float] | F64Grid) -> list[float]:
    return [round(float(v), 5) for v in values]


# -- rule kinds --------------------------------------------------------------------------------


def bake_layer(scene: Scene, key: str, layer: str, scope: BoolMask) -> Derivation:
    """The median OKLab of the bake over the layer's pure texels in ``scope``."""
    if layer not in scene.planes:
        return Derivation(key, error=f"no {layer} weights in the landscape")
    pure = (scene.planes[layer] >= PURE_SHARE * np.maximum(scene.total, 255.0)) & scene.have & scope
    count = int(pure.sum())
    if count < MIN_TEXELS:
        return Derivation(key, error=f"{count} pure texels in scope (< {MIN_TEXELS})")
    lab = np.median(oklab(np.clip(scene.bake_linear[pure], 1e-7, None)), axis=0)
    rule = f"bake median over pure {layer} texels (weight >= {PURE_SHARE} of the blend)"
    return Derivation(
        key,
        "bake median",
        rule,
        linear_from_oklab(lab).astype(np.float64),
        scene.where(pure),
        assets=[_BAKE, f"{_WEIGHTS}: {layer}"],
        samples=f"{count} texels at {scene.meta['grid']['spacing_cm'] * STRIDE / 100:g} m",
    )


def layer_table(scene: Scene, key: str, layer: str) -> Derivation:
    """The paint table's albedo: the layer's texture mean times its material vector."""
    texture, vector = LAYERS[layer]
    value = np.ones(3)
    found = Derivation(key, "paint table", f"{layer}: texture mean x material vector")
    if texture:
        mean = scene.meta.get("texture_means_linear", {})[texture]
        value = value * np.asarray(mean)
        found.assets.append(_GAME + TEXTURES[texture])
        found.params["texture_mean_linear"] = _rounded(mean)
    if vector:
        colour = scene.meta.get("material_vectors", {})[vector][:3]
        value = value * np.asarray(colour)
        found.assets.append(f"{_GAME}{MATERIAL} vector '{vector}'")
        found.params[vector] = _rounded(colour)
    found.albedo = value
    return found


def cliff_rock(scene: Scene, key: str, family: str, scope: BoolMask | None) -> Derivation:
    """The cliff master's body texture, untinted: the game's own bake of its cliffs keeps the
    texture's hue, so the family's ``Color Tint`` is recorded, not multiplied."""
    means = scene.meta.get("texture_means_linear", {})
    missing = [t for t in ROCK_TEXTURES if t not in means]
    if missing:
        return Derivation(key, error=f"the store has no mean of {', '.join(missing)}")
    source = scene.meta.get("rock_families", {}).get(family, {})
    mean = np.mean([means[t] for t in ROCK_TEXTURES], axis=0)
    params = {"texture_mean_linear": _rounded(mean)}
    tint = source.get("tint")
    if tint:
        params["Color Tint, not applied"] = list(tint)
    return Derivation(
        key,
        "cliff body texture",
        f"mean of {', '.join(ROCK_TEXTURES)}, the {family} family's body; Color Tint not applied",
        mean,
        None if scope is None else scene.where(scope),
        assets=[_GAME + TEXTURES[t] for t in ROCK_TEXTURES],
        params=params,
    )


def family_rock(scene: Scene, key: str, family: str, scope: BoolMask | None) -> Derivation:
    """A family's rock: the cliff body, or for the desert family the landscape's sand-rock
    albedo under the Dune Desert's light (its own material has no texture to multiply)."""
    if family != "desert":
        return cliff_rock(scene, key, family, scope)
    found = layer_table(scene, key, "DesertRock_LayerInfo")
    found.kind = "sand-rock texture x vector"
    found.rule = "desert rock: the landscape's sand-rock albedo, the DesertRock paint-table entry"
    found.light = DESERT_ROCK_LIGHT
    return found


def family_top(scene: Scene, key: str, family: str) -> Derivation:
    """A family's top layer: the mean of its far albedo."""
    source = scene.meta.get("rock_families", {}).get(family, {})
    top = source.get("top")
    if not top:
        return Derivation(key, error=f"the {family} family overrides no top texture")
    return Derivation(
        key,
        "top texture",
        f"{family} family top layer: mean of its far albedo",
        np.asarray(top, np.float64),
        assets=[f"{source.get('material')} 'Far Albedo' = {source.get('top_texture')}"],
        params={"texture_mean_linear": list(top)},
    )


def _crowns(scene: Scene, key: str, keep: BoolMask, gate: F64Grid | None = None) -> Derivation:
    """The weighted median crown colour of the kept trees, each by its cover x scale^2."""
    rec = scene.records[keep]
    sp = rec["species"].astype(np.int64)
    linear = np.array([t.linear for t in scene.species], np.float32)
    lab = oklab(np.clip(linear, 1e-7, None))[sp]
    weight = np.array([t.area_m2 for t in scene.species])[sp] * rec["scale"].astype(np.float64) ** 2
    if gate is not None:
        inside = gate[keep] > 0.5
        rec, sp, lab, weight = rec[inside], sp[inside], lab[inside], (weight * gate[keep])[inside]
    if len(rec) < MIN_TREES:
        return Derivation(key, error=f"{len(rec)} trees in scope (< {MIN_TREES})")
    mix = np.bincount(sp, weights=weight, minlength=len(scene.species))
    order = [int(i) for i in np.argsort(-mix)[:3] if mix[i]]
    top = [f"{scene.species[i].name} {mix[i] / mix.sum():.0%}" for i in order]
    albedo = linear_from_oklab(weighted_median(lab, weight)).astype(np.float64)
    where = (rec["x"].astype(np.float64) / 100.0, rec["y"].astype(np.float64) / 100.0)
    return Derivation(
        key, albedo=albedo, where=where, samples=f"{len(rec)} trees; {', '.join(top)}"
    )


def species(scene: Scene, key: str, names: Sequence[str]) -> Derivation:
    """The crowns of the named species, from their top-down sprites."""
    ids = [i for i, t in enumerate(scene.species) if any(n in t.name for n in names)]
    found = _crowns(
        scene, key, np.isin(scene.records["species"], ids) & ~scene.meshed[scene.records["species"]]
    )
    found.kind = "species crowns"
    found.rule = (
        f"crown colour of {', '.join(scene.species[i].name for i in ids)}: each visible "
        "slot's albedo under its leaf mask, cover-weighted"
    )
    found.assets = [scene.species[i].mesh for i in ids]
    return found


def canopy(scene: Scene, key: str, claimed: Sequence[str], areas: Sequence[str] = ()) -> Derivation:
    """The green crowns no species or crown key claims, within ``HUE_GATE_DEG`` of the forest
    floor texture's hue."""
    ref = oklab(np.asarray(scene.meta.get("texture_means_linear", {})[CANOPY_TEXTURE], np.float32))
    hue = (ref[1:] / np.hypot(ref[1], ref[2])).astype(np.float32)
    linear = np.array([t.linear for t in scene.species], np.float32)
    sp = scene.records["species"].astype(np.int64)
    gate = hue_gate(oklab(np.clip(linear, 1e-7, None))[sp], hue, CANOPY_GREY).astype(np.float64)
    owned = [i for i, t in enumerate(scene.species) if any(k in t.name for k in claimed)]
    keep = ~np.isin(sp, owned) & ~scene.meshed[sp]
    if areas:
        keep &= scene.tree_mask(areas)
    found = _crowns(scene, key, keep, gate)
    found.kind = "canopy crowns"
    low, high = HUE_GATE_DEG
    found.rule = (
        f"weighted median crown colour within {low:g}-{high:g} deg of the hue of {CANOPY_TEXTURE}"
    )
    found.assets = [_GAME + TEXTURES[CANOPY_TEXTURE] + " (hue reference)"]
    return found


def material(scene: Scene, key: str, names: Sequence[str], label: str) -> Derivation:
    """The mean linear albedo of the named materials' base-colour textures."""
    found: dict[str, list[float]] = {}
    for t in scene.species:
        found.update({p: c for p, c in t.material_linear.items() if p.endswith(tuple(names))})
    for path, entry in scene.meta.get("mesh_materials", {}).items():
        if path.endswith(tuple(names)) and entry["linear"] is not None:
            found[path] = entry["linear"]
    if not found:
        return Derivation(key, error=f"no readable albedo for {label}")
    return Derivation(
        key,
        "material texture",
        f"{label}: base-colour texture mean, linear",
        np.mean(list(found.values()), axis=0),
        assets=sorted(found),
        params={p.rsplit("/", 1)[-1]: _rounded(c) for p, c in found.items()},
    )


# -- the entry table ---------------------------------------------------------------------------


def _scoped_layers(cal: CalibrationStyle, scene: Scene) -> dict[str, BoolMask]:
    """Per layer, the areas whose entries take it: the global target holds outside them."""
    out: dict[str, BoolMask] = {}
    for entry in cal.get("areas", []):
        for layer in entry.get("layers", {}):
            out[layer] = out.get(layer, np.zeros_like(scene.have)) | scene.area_mask(entry["areas"])
    return out


def _global_rows(scene: Scene, cal: CalibrationStyle) -> list[Derivation]:
    """The keys outside ``areas``."""
    scoped = _scoped_layers(cal, scene)

    def outside(layer: str) -> BoolMask:
        return ~scoped[layer] if layer in scoped else np.ones_like(scene.have)

    claimed = [*cal.get("species", {}), BLUE_PALM]
    rows = [bake_layer(scene, f"layers.{n}", n, outside(n)) for n in cal["layers"]]
    rows += [
        bake_layer(scene, f"derived.{n}", n, outside(rule["from"]))
        for n, rule in cal.get("derived", {}).items()
    ]
    if "canopy" in cal:
        rows.append(canopy(scene, "canopy", claimed))
    if "rock" in cal:
        rows.append(cliff_rock(scene, "rock", "cliff", None))
    rows += [family_rock(scene, f"families.{f}", f, None) for f in cal.get("families", {})]
    rows += [family_top(scene, f"tops.{f}", f) for f in cal.get("tops", {})]
    for mesh in cal.get("meshes", {}):
        if mesh == "coral":
            rows.append(material(scene, "meshes.coral", [CORAL_CAPS], "coral tree caps"))
        elif "seabed" in mesh:
            rows.append(Derivation(f"meshes.{mesh}", error=f"under the sea; {_WATER}"))
        else:
            rows.append(Derivation(f"meshes.{mesh}", error=f"no rule for mesh {mesh!r}"))
    for crown in cal.get("crowns", {}):
        rows.append(
            species(scene, f"crowns.{crown}", [BLUE_PALM] if crown == "blue_palm" else [crown])
        )
    rows += [species(scene, f"species.{name}", [name]) for name in cal.get("species", {})]
    return rows


def _area_rows(scene: Scene, cal: CalibrationStyle, entry: CalibrationArea) -> list[Derivation]:
    """One ``areas`` entry's keys, each measured inside its areas only."""
    scope, mask = scope_key(entry["areas"]), scene.area_mask(entry["areas"])
    families = {target: name for name, target in cal.get("families", {}).items()}
    rows: list[Derivation] = []
    layers = entry.get("layers", {})
    for layer in layers:
        rows.append(bake_layer(scene, f"{scope}.layers.{layer}", layer, mask))
        rows += [
            bake_layer(scene, f"{scope}.derived.{name}", name, mask)
            for name, rule in cal.get("derived", {}).items()
            if rule["from"] == layer and name not in layers
        ]
    if "rock" in entry:
        family = families.get(entry["rock"])
        if family:
            rows.append(family_rock(scene, f"{scope}.rock", family, mask))
        else:
            rows.append(cliff_rock(scene, f"{scope}.rock", "cliff", mask))
    if "canopy" in entry:
        claimed = [*cal.get("species", {}), BLUE_PALM]
        rows.append(canopy(scene, f"{scope}.canopy", claimed, entry["areas"]))
    for mesh in entry.get("meshes", {}):
        key = f"{scope}.meshes.{mesh}"
        if mesh == "shell":
            found = material(scene, key, SHELLS, "shell plates")
            found.where = scene.where(mask)
            rows.append(found)
        else:
            rows.append(Derivation(key, error=f"no rule for mesh {mesh!r}"))
    if "water" in entry:
        rows.append(Derivation(f"{scope}.water", error=_WATER))
    return rows


def entries(scene: Scene, cal: CalibrationStyle) -> list[Derivation]:
    """One derivation per key of the calibration block, in the block's order."""
    rows = _global_rows(scene, cal)
    for entry in cal.get("areas", []):
        rows += _area_rows(scene, cal, entry)
    return rows


def untargeted_layers(scene: Scene, cal: CalibrationStyle) -> list[Derivation]:
    """The landscape's colour layers no key targets, each over the whole map."""
    targeted = set(cal["layers"]) | set(cal.get("derived", {}))
    for entry in cal.get("areas", []):
        targeted |= set(entry.get("layers", {}))
    rows: list[Derivation] = []
    for layer in sorted(set(scene.planes) - targeted):
        found = bake_layer(scene, f"layers.{layer}", layer, np.ones_like(scene.have))
        if found.error and layer in LAYERS:
            found = layer_table(scene, f"layers.{layer}", layer)
        rows.append(found)
    return rows
