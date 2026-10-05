"""``/api/settings``: the settings the page and chat share, for every world.

The store is ``domain/settings.py``; docs/shared-settings.md is the specification.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from ....core.filelock import LockTimeout
from ....core.schema import NewerSchema
from ....domain import settings as store
from ....domain.planning.planlog import Actor
from ..serial import ActorBody, _fail, _settings_json

__all__ = ["router"]

router = APIRouter(prefix="/api")

StageHeadroom = Literal["measured", "nameplate"]
SiteSnap = Literal["fine", "grid8"]


class SettingsValues(TypedDict):
    """Every shared setting, set or defaulted."""

    stage_headroom: StageHeadroom
    biomass: bool
    payback_hours: float
    overclock_last: bool
    site_snap: SiteSnap


class SettingsResponse(TypedDict):
    """``stored`` names the settings that were set rather than defaulted; ``by`` and
    ``updated`` are the last write, null before the first."""

    version: int
    values: SettingsValues
    stored: list[str]
    updated: float | None
    by: ActorBody | None


class SettingsChanges(TypedDict):
    """The settings to change; null puts one back to its default."""

    stage_headroom: NotRequired[StageHeadroom | None]
    biomass: NotRequired[bool | None]
    payback_hours: NotRequired[float | None]
    overclock_last: NotRequired[bool | None]
    site_snap: NotRequired[SiteSnap | None]


class SettingsPatchBody(TypedDict):
    values: SettingsChanges
    version: NotRequired[int | None]
    only_unset: NotRequired[bool]


class SettingsStaleResponse(TypedDict):
    """The 409 of a write whose ``version`` is not the current one: nothing was written."""

    error: str
    stale: bool
    settings: SettingsResponse


def _newer(exc: NewerSchema) -> JSONResponse:
    text = (
        f"the settings were saved by a newer version of satisfactory-mcp (schema {exc.found}; "
        f"this one reads up to {exc.known}). Upgrade to read them; nothing was changed"
    )
    return JSONResponse({"error": text, "newer_schema": True}, status_code=503)


@router.get("/settings", response_model=SettingsResponse)
def shared_settings() -> Any:
    """The shared settings: chat's tools read the same file."""
    try:
        return _settings_json(store.read())
    except NewerSchema as exc:
        return _newer(exc)


@router.patch(
    "/settings",
    response_model=SettingsResponse,
    responses={409: {"model": SettingsStaleResponse}},
)
def change_settings(body: Annotated[SettingsPatchBody, Body()]) -> Any:
    """Change shared settings; a ``version`` that is not the current one is a 409."""
    try:
        view = store.write(
            dict(body["values"]),
            Actor("page", "", os.getpid()),
            version=body.get("version"),
            only_unset=bool(body.get("only_unset")),
        )
    except NewerSchema as exc:
        return _newer(exc)
    except LockTimeout as exc:
        return _fail(f"settings are busy, nothing written: {exc}", 503)
    except store.SettingsStale as exc:
        current = {k: v for k, v in exc.current.items() if k != "asked"}
        payload = {"error": str(exc), "stale": True, "settings": _settings_json(current)}
        return JSONResponse(payload, status_code=409)
    except store.SettingsError as exc:
        return _fail(str(exc), 400)
    return _settings_json(view)
