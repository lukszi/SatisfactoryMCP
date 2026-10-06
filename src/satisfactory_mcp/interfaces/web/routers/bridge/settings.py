"""``/api/settings``: the settings the page and chat share, for every world.

The store is ``domain/settings.py``; docs/shared-settings.md is the specification.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....core.filelock import LockTimeout
from .....core.schema import NewerSchema
from .....domain import settings as store
from .....domain.settings import SettingsChanges, SettingsValues
from ...serial import (
    ActorBody,
    busy_response,
    error_response,
    newer_schema_response,
    page_actor,
    settings_json,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class SettingsResponse(TypedDict):
    """``stored`` names the settings that were set rather than defaulted; ``by`` and
    ``updated`` are the last write, null before the first."""

    version: int
    values: SettingsValues
    stored: list[str]
    updated: float | None
    by: ActorBody | None


class SettingsPatchBody(TypedDict):
    values: SettingsChanges
    version: NotRequired[int | None]
    only_unset: NotRequired[bool]


class SettingsStaleResponse(TypedDict):
    """The 409 of a write whose ``version`` is not the current one: nothing was written."""

    error: str
    stale: bool
    settings: SettingsResponse


STORE_NAME = "the settings"


@router.get("/settings", response_model=SettingsResponse)
def shared_settings() -> Any:
    """The shared settings: chat's tools read the same file."""
    try:
        return settings_json(store.read())
    except NewerSchema as exc:
        return newer_schema_response(exc, STORE_NAME)


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
            page_actor(),
            version=body.get("version"),
            only_unset=bool(body.get("only_unset")),
        )
    except NewerSchema as exc:
        return newer_schema_response(exc, STORE_NAME)
    except LockTimeout as exc:
        return busy_response("settings", exc)
    except store.SettingsStale as exc:
        current = {k: v for k, v in exc.current.items() if k != "asked"}
        payload = {"error": str(exc), "stale": True, "settings": settings_json(current)}
        return JSONResponse(payload, status_code=409)
    except store.SettingsError as exc:
        return error_response(str(exc), 400)
    return settings_json(view)
