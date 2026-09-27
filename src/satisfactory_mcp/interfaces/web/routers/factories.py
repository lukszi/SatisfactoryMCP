"""``/api/factories``: the names the player gave, and the proposals for the rest.

The two halves are one question: a proposal whose machines the player has already named is
not a proposal, so the named set is built before the proposed one is filtered against it.

**The boxes are tuples, not lists.** ``centroid_m`` is exactly two numbers and ``bbox_m``
exactly four, so pydantic emits ``prefixItems`` and typegen turns them into
``[number, number]`` and ``[number, number, number, number]``, which the page indexes
without a length guard. Declared ``list[float]`` they would arrive as ``number[]``.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.factories import identity as fidentity
from ....domain.factories import naming
from ....domain.spatial import geo
from ....domain.spatial import regions as spatial_regions
from ..serial import _fail, _m, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ factories


class FactoryRow(TypedDict):
    """A factory the player named, and the extent of the machines it is anchored to.

    ``centroid_m`` is never null: a label remembers where it was even when nothing it named
    is still standing. ``bbox_m`` is null in exactly that case, because ``geo.bbox`` refuses
    to invent a zero box at the world centre for an empty set -- so a demolished factory
    keeps its name and its remembered middle and loses only the ability to be flown to.

    ``notes`` is not nullable: an unannotated factory sends the empty string.
    """

    name: str
    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float] | None
    machines: int
    notes: str


class ProposalRow(TypedDict):
    """A cluster the coherence pass found that no label speaks for.

    ``index`` is the position in the FULL proposal list rather than in this filtered one, so
    a ``proposal:N`` selector resolves to the same cluster here and in the MCP tools.
    """

    index: int
    label: str
    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float] | None
    machines: int
    score: float
    spread_m: float


class FactoriesResponse(TypedDict):
    labels: list[FactoryRow]
    proposals: list[ProposalRow]


@router.get("/factories", response_model=FactoriesResponse)
def factories(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    style: str = naming.DEFAULT_STYLE,
) -> Any:
    """Named factories and the coherence-scored proposals for the unnamed rest.

    Each row carries ``bbox_m`` -- ``[x_min, y_min, x_max, y_max]`` in metres, game axes --
    alongside its centroid, because a centroid alone cannot frame a viewport. It is computed
    here rather than client-side, since the client is sent the anchor machines' count and
    not the machines, and it is ``null`` when nothing in the set is still standing.

    A proposal whose machines the player has already named is not a proposal: the clusterer
    runs over the whole world, so it rediscovers every named factory, and any proposal in
    which named anchors are the majority is dropped here.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    if style not in naming.STYLES:
        return _fail(f"unknown style {style!r}; known: {', '.join(naming.STYLES)}", 400)
    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError:
        rmap = None
    names = naming.proposal_names(st, st.proposals, style, rmap)
    placed = fidentity.positions(st.projection)

    def _bbox_m(machines) -> list[float] | None:
        box = geo.bbox([placed[m][:2] for m in machines if m in placed])
        return None if box is None else [_m(v) for v in box]

    named = [
        {
            "name": label.name,
            "centroid_m": [_m(label.centroid[0]), _m(label.centroid[1])],
            "bbox_m": _bbox_m(label.anchors),
            "machines": len(label.anchors),
            "notes": label.notes,
        }
        for label in sorted(st.labels.labels, key=lambda x: -len(x.anchors))
    ]

    proposals = []
    for index, pr in enumerate(st.proposals):
        if st.labels.covers(pr.machines):
            continue  # already named by the player; the label speaks for it
        cand = fidentity.describe(pr.machines, st.graph, st.game, st.projection, "proposal")
        proposals.append(
            {
                "index": index,
                "label": names[index],
                "centroid_m": [_m(cand.centroid[0]), _m(cand.centroid[1])],
                "bbox_m": _bbox_m(pr.machines),
                "machines": pr.size,
                "score": round(pr.cohesion, 3),
                "spread_m": round(cand.spread_m, 1),
            }
        )
    return {"labels": named, "proposals": proposals}
