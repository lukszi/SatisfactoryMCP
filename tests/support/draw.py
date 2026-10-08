"""``render_layers`` as the draw tests call it: the ground's inputs as keywords beside the rest.

``render_layer`` is one layer alone, in memory, which is what most of them draw.
"""

from __future__ import annotations

from dataclasses import fields

from mapgen.render.draw.compose import GroundInputs, render_layers

#: The keywords that are the ground's, not the pass's.
GROUND_KEYWORDS = frozenset(field.name for field in fields(GroundInputs))


def draw_layers(layers, field, biome_width, borrow, size, progress, height_dm=None, **keywords):
    """``render_layers`` with ``height_dm`` and every ``GroundInputs`` field as a keyword."""
    ground = {key: keywords.pop(key) for key in list(keywords) if key in GROUND_KEYWORDS}
    inputs = GroundInputs(height_dm=height_dm, **ground)
    args = (layers, field, biome_width, borrow, size, progress, inputs)
    return render_layers(*args, **keywords)


def render_layer(
    layer,
    field,
    biome_width,
    borrow,
    size,
    progress,
    height_dm=None,
    relief=None,
    **keywords,
):
    """One layer alone, in memory, with its own ``relief``."""
    reliefs = None if relief is None else {layer: relief}
    args = (field, biome_width, borrow, size, progress, height_dm)
    return draw_layers((layer,), *args, relief=reliefs, **keywords)[layer]
