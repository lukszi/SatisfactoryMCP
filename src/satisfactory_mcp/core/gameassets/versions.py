"""Which renderer, style and reader versions are current, for the generators and the server alike.

The server judges a map against these without importing ``tools/``, which a wheel does not
ship, and the generators import them rather than defining their own, so the two cannot
disagree. Bumping a number here is what turns every older map's chip to "re-render
available" or "stale"; docs/maps_contract.md §3 has the rules.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = [
    "ARTWORK_RECIPES",
    "CAVES_VERSION",
    "HEIGHTFIELD_GENERATOR_VERSION",
    "LIGHTS",
    "PAINT_GENERATOR_VERSION",
    "PLAIN_TONE",
    "PROVENANCE_SCHEMA",
    "READER_VERSIONS",
    "RENDER_RECIPES",
    "RENDER_RECIPE_CURRENT",
    "RENDER_RECIPE_KERNEL_ONLY",
    "STYLES",
    "ArtworkRecipe",
    "HeightfieldNeed",
    "LightModel",
    "RecipeNeeds",
    "RenderRecipe",
    "Style",
]


class HeightfieldNeed(TypedDict):
    """The heightfield a recipe draws from: its oldest usable version and the planes it opens."""

    min_version: int
    planes: list[str]


class RecipeNeeds(TypedDict, total=False):
    """What a recipe needs on disk before it can draw; an artwork recipe needs nothing."""

    heightfield: HeightfieldNeed


class RenderRecipe(TypedDict):
    """One ``RENDER_RECIPES`` entry."""

    label: str
    sampler: str
    two_regime: bool
    version: int
    requires: RecipeNeeds


class ArtworkRecipe(TypedDict):
    """One ``ARTWORK_RECIPES`` entry."""

    label: str
    version: int
    requires: RecipeNeeds


class Style(TypedDict):
    """One ``STYLES`` entry: its labels, the layer it draws, its version and its tone."""

    label: str
    name: str
    layer: str
    version: int
    tone: str


class LightModel(TypedDict):
    """One ``LIGHTS`` entry."""

    label: str
    version: int


#: The shape of ``_meta.provenance``.
PROVENANCE_SCHEMA = 1

#: ``tools/gen_world_heightmap.py``'s output version; its sidecar's ``generator_version``.
HEIGHTFIELD_GENERATOR_VERSION = 6

#: ``caves/meta.json``'s ``caves_version``.
CAVES_VERSION = 1

#: ``tools/gen_paint_layers.py``'s output version; ``paint/meta.json``'s ``generator_version``.
PAINT_GENERATOR_VERSION = 3

#: How the inputs a render reads straight from the install are decoded. ``cliff_geometry``
#: is the heightfield generator's own sweep and decode, imported by the renders.
READER_VERSIONS = {
    "biome_raster": 1,
    "artwork_sheet": 1,
    "cliff_geometry": HEIGHTFIELD_GENERATOR_VERSION,
    "render_meshes": 4,
    "river_splines": 1,
    "rock_families": 3,
    "titan_trees": 2,
    "waterfalls": 1,
}

#: ``tools/gen_map_renders.py`` recipes. ``requires`` names the heightfield the recipe needs:
#: a minimum generator version and the planes it opens.
RENDER_RECIPES: dict[int, RenderRecipe] = {
    1: {"label": "bilinear", "sampler": "bilinear", "two_regime": False, "version": 1,
        "requires": {"heightfield": {"min_version": 1, "planes": ["height"]}}},
    2: {"label": "Catmull-Rom", "sampler": "catmull-rom", "two_regime": False, "version": 1,
        "requires": {"heightfield": {"min_version": 2, "planes": ["height", "waterq"]}}},
    3: {"label": "two-regime", "sampler": "catmull-rom", "two_regime": True, "version": 1,
        "requires": {"heightfield": {"min_version": 3, "planes": ["height", "density"]}}},
    4: {"label": "two-regime", "sampler": "catmull-rom", "two_regime": True, "version": 1,
        "requires": {"heightfield": {"min_version": 4,
                                     "planes": ["height", "density", "terrain", "top"]}}},
    5: {"label": "PCHIP", "sampler": "pchip", "two_regime": True, "version": 1,
        "requires": {"heightfield": {"min_version": 4,
                                     "planes": ["height", "density", "terrain", "top"]}}},
    6: {"label": "crisp shore", "sampler": "pchip", "two_regime": True, "version": 1,
        "requires": {"heightfield": {"min_version": 4,
                                     "planes": ["height", "density", "terrain", "top",
                                                "water", "waterq"]}}},
    7: {"label": "river splines", "sampler": "pchip", "two_regime": True, "version": 1,
        "requires": {"heightfield": {"min_version": 4,
                                     "planes": ["height", "density", "terrain", "top",
                                                "water", "waterq"]}}},
}  # fmt: skip
RENDER_RECIPE_CURRENT = 7
RENDER_RECIPE_KERNEL_ONLY = 2

#: ``tools/gen_map_image.py``'s enhancement recipes: 0 is the game's own sheet, cut plainly.
ARTWORK_RECIPES: dict[int, ArtworkRecipe] = {
    0: {"label": "plain", "version": 1, "requires": {}},
    1: {"label": "ESRGAN", "version": 1, "requires": {}},
    2: {"label": "ESRGAN", "version": 1, "requires": {}},
}

#: Palettes. A render's style ``id`` is its palette file's name under
#: ``tools/mapgen/src/mapgen/palette/palettes/``,
#: and the version is bumped when a palette changes on purpose; the file's hash is the digest.
#: ``tone`` is the base's lightness, which the page's overlay colours follow. ``name`` is what
#: the switcher and chat call a map of the style (docs/maps_contract.md §3.5).
STYLES: dict[str, Style] = {
    "terrain-hypsometric": {"label": "terrain", "name": "Terrain", "layer": "terrain",
                            "version": 10, "tone": "light"},
    "satellite-biome": {"label": "satellite", "name": "Satellite", "layer": "satellite",
                        "version": 10, "tone": "light"},
    "satellite-painted": {"label": "game-painted", "name": "Painted", "layer": "painted",
                          "version": 21, "tone": "light"},
    "relief-muted": {"label": "relief", "name": "Relief", "layer": "relief", "version": 8,
                     "tone": "light"},
    "relief-night": {"label": "relief dark", "name": "Relief (dark)", "layer": "relief-dark",
                     "version": 8, "tone": "dark"},
    "artwork": {"label": "artwork", "name": "Game map", "layer": "map", "version": 1,
                "tone": "light"},
}  # fmt: skip

#: The tone of no imagery at all: the page's own dark sea.
PLAIN_TONE = "dark"

#: Light models. A render drawn unlit names the one its lighting pyramid was baked for; a
#: version bump offers a relight, never a stale chip.
LIGHTS: dict[str, LightModel] = {
    "sun": {"label": "live sun", "version": 2},
}
