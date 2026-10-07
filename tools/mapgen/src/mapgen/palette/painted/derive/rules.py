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
from mapgen.palette.painted.trees import CANOPY_GREY, hue_gate
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


def bake_layer(s: Scene, key: str, layer: str, scope: BoolMask) -> Derivation:
    """The median OKLab of the bake over the layer's pure texels in ``scope``."""
    if layer not in s.planes:
        return Derivation(key, error=f"no {layer} weights in the landscape")
    pure = (s.planes[layer] >= PURE_SHARE * np.maximum(s.total, 255.0)) & s.have & scope
    count = int(pure.sum())
    if count < MIN_TEXELS:
        return Derivation(key, error=f"{count} pure texels in scope (< {MIN_TEXELS})")
    lab = np.median(oklab(np.clip(s.bake_linear[pure], 1e-7, None)), axis=0)
    rule = f"bake median over pure {layer} texels (weight >= {PURE_SHARE} of the blend)"
    return Derivation(
        key,
        "bake median",
        rule,
        linear_from_oklab(lab).astype(np.float64),
        s.where(pure),
        assets=[_BAKE, f"{_WEIGHTS}: {layer}"],
        samples=f"{count} texels at {s.meta['grid']['spacing_cm'] * STRIDE / 100:g} m",
    )


def layer_table(s: Scene, key: str, layer: str) -> Derivation:
    """The paint table's albedo: the layer's texture mean times its material vector."""
    texture, vector = LAYERS[layer]
    value = np.ones(3)
    found = Derivation(key, "paint table", f"{layer}: texture mean x material vector")
    if texture:
        mean = s.meta.get("texture_means_linear", {})[texture]
        value = value * np.asarray(mean)
        found.assets.append(_GAME + TEXTURES[texture])
        found.params["texture_mean_linear"] = _rounded(mean)
    if vector:
        colour = s.meta.get("material_vectors", {})[vector][:3]
        value = value * np.asarray(colour)
        found.assets.append(f"{_GAME}{MATERIAL} vector '{vector}'")
        found.params[vector] = _rounded(colour)
    found.albedo = value
    return found


def cliff_rock(s: Scene, key: str, family: str, scope: BoolMask | None) -> Derivation:
    """The cliff macro and detail textures' mean times the family's ``Color Tint``."""
    means = s.meta.get("texture_means_linear", {})
    source = s.meta.get("rock_families", {}).get(family, {})
    tint = source.get("tint")
    if not tint:
        return Derivation(key, error=f"the {family} family has no Color Tint")
    mean = np.mean([means[t] for t in ROCK_TEXTURES], axis=0)
    return Derivation(
        key,
        "cliff texture x tint",
        f"mean of {', '.join(ROCK_TEXTURES)} x {family} family Color Tint",
        mean * np.asarray(tint),
        None if scope is None else s.where(scope),
        assets=[_GAME + TEXTURES[t] for t in ROCK_TEXTURES]
        + [f"{source.get('material')} vector 'Color Tint'"],
        params={"texture_mean_linear": _rounded(mean), "Color Tint": list(tint)},
    )


def family_rock(s: Scene, key: str, family: str, scope: BoolMask | None) -> Derivation:
    """A family's rock: its cliff tint, or for the desert family the landscape's sand-rock
    albedo under the Dune Desert's light (its own material has no texture to multiply)."""
    if family != "desert":
        return cliff_rock(s, key, family, scope)
    found = layer_table(s, key, "DesertRock_LayerInfo")
    found.kind = "sand-rock texture x vector"
    found.rule = "desert rock: the landscape's sand-rock albedo, the DesertRock paint-table entry"
    found.light = DESERT_ROCK_LIGHT
    return found


def family_top(s: Scene, key: str, family: str) -> Derivation:
    """A family's top layer: the mean of its far albedo."""
    source = s.meta.get("rock_families", {}).get(family, {})
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


def _crowns(s: Scene, key: str, keep: BoolMask, gate: F64Grid | None = None) -> Derivation:
    """The weighted median crown colour of the kept trees, each by its cover x scale^2."""
    rec = s.records[keep]
    sp = rec["species"].astype(np.int64)
    linear = np.array([t.linear for t in s.species], np.float32)
    lab = oklab(np.clip(linear, 1e-7, None))[sp]
    weight = np.array([t.area_m2 for t in s.species])[sp] * rec["scale"].astype(np.float64) ** 2
    if gate is not None:
        inside = gate[keep] > 0.5
        rec, sp, lab, weight = rec[inside], sp[inside], lab[inside], (weight * gate[keep])[inside]
    if len(rec) < MIN_TREES:
        return Derivation(key, error=f"{len(rec)} trees in scope (< {MIN_TREES})")
    mix = np.bincount(sp, weights=weight, minlength=len(s.species))
    order = [int(i) for i in np.argsort(-mix)[:3] if mix[i]]
    top = [f"{s.species[i].name} {mix[i] / mix.sum():.0%}" for i in order]
    albedo = linear_from_oklab(weighted_median(lab, weight)).astype(np.float64)
    where = (rec["x"].astype(np.float64) / 100.0, rec["y"].astype(np.float64) / 100.0)
    return Derivation(
        key, albedo=albedo, where=where, samples=f"{len(rec)} trees; {', '.join(top)}"
    )


def species(s: Scene, key: str, names: Sequence[str]) -> Derivation:
    """The crowns of the named species, from their top-down sprites."""
    ids = [i for i, t in enumerate(s.species) if any(n in t.name for n in names)]
    found = _crowns(s, key, np.isin(s.records["species"], ids) & ~s.meshed[s.records["species"]])
    found.kind = "species crowns"
    found.rule = (
        f"crown colour of {', '.join(s.species[i].name for i in ids)}: each visible "
        "slot's albedo under its leaf mask, cover-weighted"
    )
    found.assets = [s.species[i].mesh for i in ids]
    return found


def canopy(s: Scene, key: str, claimed: Sequence[str], areas: Sequence[str] = ()) -> Derivation:
    """The green crowns no species or crown key claims, within 20-40 degrees of the forest
    floor texture's hue."""
    ref = oklab(np.asarray(s.meta.get("texture_means_linear", {})[CANOPY_TEXTURE], np.float32))
    hue = (ref[1:] / np.hypot(ref[1], ref[2])).astype(np.float32)
    linear = np.array([t.linear for t in s.species], np.float32)
    sp = s.records["species"].astype(np.int64)
    gate = hue_gate(oklab(np.clip(linear, 1e-7, None))[sp], hue, CANOPY_GREY).astype(np.float64)
    owned = [i for i, t in enumerate(s.species) if any(k in t.name for k in claimed)]
    keep = ~np.isin(sp, owned) & ~s.meshed[sp]
    if areas:
        keep &= s.tree_mask(areas)
    found = _crowns(s, key, keep, gate)
    found.kind = "canopy crowns"
    found.rule = f"weighted median crown colour within 20-40 deg of the hue of {CANOPY_TEXTURE}"
    found.assets = [_GAME + TEXTURES[CANOPY_TEXTURE] + " (hue reference)"]
    return found


def material(s: Scene, key: str, names: Sequence[str], label: str) -> Derivation:
    """The mean linear albedo of the named materials' base-colour textures."""
    found: dict[str, list[float]] = {}
    for t in s.species:
        found.update({p: c for p, c in t.material_linear.items() if p.endswith(tuple(names))})
    for path, entry in s.meta.get("mesh_materials", {}).items():
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


def _scoped_layers(cal: CalibrationStyle, s: Scene) -> dict[str, BoolMask]:
    """Per layer, the areas whose entries take it: the global target holds outside them."""
    out: dict[str, BoolMask] = {}
    for entry in cal.get("areas", []):
        for layer in entry.get("layers", {}):
            out[layer] = out.get(layer, np.zeros_like(s.have)) | s.area_mask(entry["areas"])
    return out


def _global_rows(s: Scene, cal: CalibrationStyle) -> list[Derivation]:
    """The keys outside ``areas``."""
    scoped = _scoped_layers(cal, s)

    def outside(layer: str) -> BoolMask:
        return ~scoped[layer] if layer in scoped else np.ones_like(s.have)

    claimed = [*cal.get("species", {}), BLUE_PALM]
    rows = [bake_layer(s, f"layers.{n}", n, outside(n)) for n in cal["layers"]]
    rows += [
        bake_layer(s, f"derived.{n}", n, outside(rule["from"]))
        for n, rule in cal.get("derived", {}).items()
    ]
    if "canopy" in cal:
        rows.append(canopy(s, "canopy", claimed))
    if "rock" in cal:
        rows.append(cliff_rock(s, "rock", "cliff", None))
    rows += [family_rock(s, f"families.{f}", f, None) for f in cal.get("families", {})]
    rows += [family_top(s, f"tops.{f}", f) for f in cal.get("tops", {})]
    for mesh in cal.get("meshes", {}):
        if mesh == "coral":
            rows.append(material(s, "meshes.coral", [CORAL_CAPS], "coral tree caps"))
        elif "seabed" in mesh:
            rows.append(Derivation(f"meshes.{mesh}", error=f"under the sea; {_WATER}"))
        else:
            rows.append(Derivation(f"meshes.{mesh}", error=f"no rule for mesh {mesh!r}"))
    for crown in cal.get("crowns", {}):
        rows.append(species(s, f"crowns.{crown}", [BLUE_PALM] if crown == "blue_palm" else [crown]))
    rows += [species(s, f"species.{name}", [name]) for name in cal.get("species", {})]
    return rows


def _area_rows(s: Scene, cal: CalibrationStyle, entry: CalibrationArea) -> list[Derivation]:
    """One ``areas`` entry's keys, each measured inside its areas only."""
    scope, mask = scope_key(entry["areas"]), s.area_mask(entry["areas"])
    families = {target: name for name, target in cal.get("families", {}).items()}
    rows: list[Derivation] = []
    layers = entry.get("layers", {})
    for layer in layers:
        rows.append(bake_layer(s, f"{scope}.layers.{layer}", layer, mask))
        rows += [
            bake_layer(s, f"{scope}.derived.{name}", name, mask)
            for name, rule in cal.get("derived", {}).items()
            if rule["from"] == layer and name not in layers
        ]
    if "rock" in entry:
        family = families.get(entry["rock"])
        if family:
            rows.append(family_rock(s, f"{scope}.rock", family, mask))
        else:
            rows.append(cliff_rock(s, f"{scope}.rock", "cliff", mask))
    if "canopy" in entry:
        claimed = [*cal.get("species", {}), BLUE_PALM]
        rows.append(canopy(s, f"{scope}.canopy", claimed, entry["areas"]))
    for mesh in entry.get("meshes", {}):
        key = f"{scope}.meshes.{mesh}"
        if mesh == "shell":
            found = material(s, key, SHELLS, "shell plates")
            found.where = s.where(mask)
            rows.append(found)
        else:
            rows.append(Derivation(key, error=f"no rule for mesh {mesh!r}"))
    if "water" in entry:
        rows.append(Derivation(f"{scope}.water", error=_WATER))
    return rows


def entries(s: Scene, cal: CalibrationStyle) -> list[Derivation]:
    """One derivation per key of the calibration block, in the block's order."""
    rows = _global_rows(s, cal)
    for entry in cal.get("areas", []):
        rows += _area_rows(s, cal, entry)
    return rows


def untargeted_layers(s: Scene, cal: CalibrationStyle) -> list[Derivation]:
    """The landscape's colour layers no key targets, each over the whole map."""
    targeted = set(cal["layers"]) | set(cal.get("derived", {}))
    for entry in cal.get("areas", []):
        targeted |= set(entry.get("layers", {}))
    rows: list[Derivation] = []
    for layer in sorted(set(s.planes) - targeted):
        found = bake_layer(s, f"layers.{layer}", layer, np.ones_like(s.have))
        if found.error and layer in LAYERS:
            found = layer_table(s, f"layers.{layer}", layer)
        rows.append(found)
    return rows
