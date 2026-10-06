"""``/api/world/conduits``: belt and pipe runs near a place, or every fluid network.

The runs are ``conduit_search.search``'s and the networks ``conduit_search.networks``',
the calls ``search_conduits`` makes, so the page and the chat answer one question one
way. Handler names are operation_ids (wire rule 1 of docs/web-wire.md).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from fastapi import APIRouter, Query, Request

from .....domain.spatial.places import resolve_place
from .....domain.world import conduit_search
from .....domain.world import conduits as conduits_mod
from ...serial import choice_refusal, cm_to_m, error_response, require_world, resource_name

__all__ = ["router"]

router = APIRouter(prefix="/api/world")

CONDUIT_VIEWS = ("runs", "networks")


class RunEnd(TypedDict):
    x_m: float
    y_m: float
    z_m: float
    plugs: str | None


class RunRow(TypedDict):
    """One belt chain or pipe piece; ``lines_m`` are the drawn polylines, as trace sends."""

    id: str
    kind: Literal["belt", "lift", "pipe"]
    label: str
    pieces: int
    length_m: float
    a: RunEnd
    b: RunEnd
    z_min_m: float
    z_max_m: float
    directed: bool
    basis: str | None
    carries: str | None
    rate: float | None
    network: int | None
    via: list[str]
    distance_m: float
    lines_m: list[list[tuple[float, float]]]


class NetworkRow(TypedDict):
    network: int | None
    carries: str | None
    pieces: int
    length_m: float
    x_m: float
    y_m: float
    z_min_m: float
    z_max_m: float
    distance_m: float
    touches: list[str]


class ConduitsResponse(TypedDict):
    """``total`` counts every match; ``runs``/``networks`` hold one page of them."""

    view: Literal["runs", "networks"]
    where: str
    where_to: str
    radius_m: float
    to_radius_m: float | None
    runs: list[RunRow]
    networks: list[NetworkRow]
    total: int
    offset: int
    belts: int
    pipes: int
    belt_m: float
    pipe_m: float
    fluids: list[str]
    bridged: list[str]
    notes: list[str]
    age_note: str


def _run_end_json(end) -> RunEnd:
    return {"x_m": cm_to_m(end.x), "y_m": cm_to_m(end.y), "z_m": cm_to_m(end.z), "plugs": end.plugs}


def _run_row(run, origin, game) -> RunRow:
    return {
        "id": run.ident,
        "kind": run.kind,
        "label": run.label,
        "pieces": run.pieces,
        "length_m": round(run.length_m, 1),
        "a": _run_end_json(run.a),
        "b": _run_end_json(run.b),
        "z_min_m": round(run.z_min_m, 1),
        "z_max_m": round(run.z_max_m, 1),
        "directed": run.directed,
        "basis": run.basis,
        "carries": (resource_name(game, run.fluid) if run.fluid else None),
        "rate": None if run.rate is None else round(run.rate, 2),
        "network": run.network,
        "via": list(run.via),
        "distance_m": run.dist_m(*origin),
        "lines_m": [[(cm_to_m(p[0]), cm_to_m(p[1])) for p in line] for line in run.lines],
    }


def _networks_reply(networks: list, where: str, game, base: dict, page: slice) -> dict:
    """Every fluid network, nearest first, one page of them with totals over them all."""
    return {
        **base,
        "where": where,
        "networks": [
            {
                "network": network.network,
                "carries": resource_name(game, network.fluid) if network.fluid else None,
                "pieces": network.pieces,
                "length_m": round(network.length_m, 1),
                "x_m": cm_to_m(network.centre[0]),
                "y_m": cm_to_m(network.centre[1]),
                "z_min_m": round(network.z_min_m, 1),
                "z_max_m": round(network.z_max_m, 1),
                "distance_m": network.distance_m,
                "touches": network.touches,
            }
            for network in networks[page]
        ],
        "total": len(networks),
        "pipes": sum(network.pieces for network in networks),
        "pipe_m": round(sum(network.length_m for network in networks), 1),
        "fluids": sorted({resource_name(game, net.fluid) for net in networks if net.fluid}),
    }


def _runs_reply(found, game, base: dict, page: slice) -> dict:
    """The runs a search found, longest first, one page of them with totals over them all."""
    belts, pipes = found.belts, found.pipes
    return {
        **base,
        "where": found.where,
        "where_to": found.where_to,
        "to_radius_m": found.to_radius_m,
        "runs": [_run_row(run, found.origin, game) for run in found.hits[page]],
        "total": len(found.hits),
        "belts": len(belts),
        "pipes": len(pipes),
        "belt_m": round(sum(run.length_m for run in belts), 1),
        "pipe_m": round(sum(run.length_m for run in pipes), 1),
        "fluids": sorted({resource_name(game, run.fluid) for run in pipes if run.fluid}),
        "bridged": found.bridged,
    }


@router.get("/conduits", response_model=ConduitsResponse)
def world_conduits(
    request: Request,
    near: str = "me",
    radius_m: Annotated[float, Query(ge=1, le=2000)] = conduits_mod.NEAR_RADIUS_M,
    to: str | None = None,
    to_radius_m: Annotated[float | None, Query(ge=1, le=2000)] = None,
    conduit_kind: str = "all",
    view: str = "runs",
    network: int | None = None,
    run: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Belt and pipe runs near a place (and ``to`` a second one), or every fluid network.

    The runs are ``search_conduits``'s, longest first. ``network`` lists every pipe of one
    fluid network and ``run`` one run by id; both ignore the radii.
    """
    view = view.strip().casefold()
    kind = conduit_kind.strip().casefold()
    refusal = choice_refusal(view, CONDUIT_VIEWS, "view") or choice_refusal(
        kind, (*conduit_search.KINDS, "all"), "conduit_kind"
    )
    if refusal:
        return error_response(refusal)
    if view == "networks" and kind == "belt":
        return error_response("view=networks lists fluid networks; a belt chain belongs to none")
    st = require_world(request, save, world)
    game = request.app.state.game()
    base = {
        "view": view,
        "where_to": "",
        "radius_m": radius_m,
        "to_radius_m": None,
        "runs": [],
        "networks": [],
        "offset": offset,
        "belts": 0,
        "pipes": 0,
        "belt_m": 0.0,
        "pipe_m": 0.0,
        "fluids": [],
        "bridged": [],
        "notes": [],
        "age_note": st.age_note,
    }
    page = slice(offset, offset + limit)
    if view == "networks":
        try:
            origin, where = resolve_place(st, near)
        except ValueError as exc:
            return error_response(f"! {exc}")
        return _networks_reply(conduit_search.networks(st, origin), where, game, base, page)

    found = conduit_search.search(
        st,
        near,
        radius_m,
        to=to,
        to_radius_m=to_radius_m,
        kind=None if kind == "all" else kind,
        network=network,
        run=run,
    )
    if found.error:
        return error_response(found.error)
    return _runs_reply(found, game, base, page)
