"""Settings the page and chat share: one file in the user data dir, for every world.

docs/shared-settings.md is the specification.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, cast

from typing_extensions import TypedDict

from .. import config
from ..core import atomic, filelock, schema
from ..core.jsontypes import JsonObject, JsonValue
from .planning.stored.planlog import Actor

__all__ = [
    "SCHEMA",
    "SPECS",
    "SettingValue",
    "SettingsChanges",
    "SettingsError",
    "SettingsStale",
    "SettingsValues",
    "SettingsView",
    "SiteSnap",
    "Spec",
    "StageHeadroom",
    "check",
    "read",
    "value",
    "write",
]

SCHEMA = 1

StageHeadroom = Literal["measured", "nameplate"]
SiteSnap = Literal["fine", "grid8"]
SettingValue: TypeAlias = bool | str | float


class SettingsValues(TypedDict):
    """Every shared setting, set or defaulted."""

    stage_headroom: StageHeadroom
    biomass: bool
    payback_hours: float
    overclock_last: bool
    site_snap: SiteSnap
    advice_box_fed: bool


class SettingsChanges(TypedDict):
    """The settings to change; null puts one back to its default."""

    stage_headroom: NotRequired[StageHeadroom | None]
    biomass: NotRequired[bool | None]
    payback_hours: NotRequired[float | None]
    overclock_last: NotRequired[bool | None]
    site_snap: NotRequired[SiteSnap | None]
    advice_box_fed: NotRequired[bool | None]


class SettingsDoc(TypedDict):
    """The settings file: ``values`` holds only the settings that were set."""

    schema: int
    version: int
    values: JsonObject
    updated: float | None
    by: dict[str, str | int] | None


class SettingsView(TypedDict):
    """Every setting's value, the keys set rather than defaulted, and the last write; ``by``
    is ``Actor.to_dict``'s."""

    version: int
    values: dict[str, SettingValue]
    stored: list[str]
    updated: float | None
    by: dict[str, str | int] | None


class StaleSettings(SettingsView):
    """The settings as they are, and the ``version`` the refused write named."""

    asked: int


@dataclass(frozen=True)
class Spec:
    """One setting: ``kind`` is ``choice``, ``switch`` or ``number``."""

    kind: str
    default: SettingValue
    hint: str
    options: tuple[str, ...] = ()
    low: float = 0.0
    high: float = 0.0


SPECS: dict[str, Spec] = {
    "stage_headroom": Spec(
        "choice",
        "measured",
        "the save's figure a plan with no stored startup headroom is staged against",
        options=("measured", "nameplate"),
    ),
    "biomass": Spec("switch", False, "count hand-fed biomass burners as generation and headroom"),
    "payback_hours": Spec(
        "number",
        0.0,
        "hours of play a plan's extra machines must pay back in saved power; 0 builds plainly",
        low=0.0,
        high=100.0,
    ),
    "overclock_last": Spec(
        "switch", False, "build a row one machine short and overclock its last machine"
    ),
    "site_snap": Spec(
        "choice",
        "fine",
        "how a moved plan pad snaps: fine is 1 m and 15° steps, "
        "grid8 the 8 m world grid and 90° steps",
        options=("fine", "grid8"),
    ),
    "advice_box_fed": Spec(
        "switch", False, "advise when a machine fed only from a storage box has emptied it"
    ),
}


class SettingsError(ValueError):
    """A settings change that cannot be honoured, worded for the player."""


class SettingsStale(SettingsError):
    def __init__(self, current: StaleSettings) -> None:
        super().__init__(f"the settings changed elsewhere since version {current['asked']}")
        self.current = current


def check(key: str, raw: object) -> SettingValue:
    """``raw`` as ``key`` stores it; ``SettingsError`` when it is not one of its values."""
    spec = SPECS.get(key)
    if spec is None:
        raise SettingsError(f"no setting “{key}”; known: {', '.join(SPECS)}")
    if spec.kind == "switch":
        if isinstance(raw, bool):
            return raw
        raise SettingsError(f"{key} is true or false, not {raw!r}")
    if spec.kind == "choice":
        if isinstance(raw, str) and raw in spec.options:
            return raw
        raise SettingsError(f"{key} is one of {', '.join(spec.options)}, not {raw!r}")
    if isinstance(raw, int | float) and not isinstance(raw, bool) and spec.low <= raw <= spec.high:
        return float(raw)
    raise SettingsError(f"{key} is a number from {spec.low:g} to {spec.high:g}, not {raw!r}")


def _raw() -> SettingsDoc:
    path = config.settings_path()
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    schema.check(raw, SCHEMA, path)
    if not isinstance(raw, dict):
        raw = {}
    values, version, by = raw.get("values"), raw.get("version"), raw.get("by")
    updated = raw.get("updated")
    return {
        "schema": SCHEMA,
        "version": int(version) if isinstance(version, int | float) else 0,
        "values": dict(values) if isinstance(values, dict) else {},
        "updated": updated if isinstance(updated, int | float) else None,
        "by": cast("dict[str, str | int]", by) if isinstance(by, dict) else None,
    }


def _view(data: SettingsDoc) -> SettingsView:
    values: dict[str, SettingValue] = {}
    stored: list[str] = []
    for key, spec in SPECS.items():
        try:
            values[key] = check(key, data["values"][key])
            stored.append(key)
        except (KeyError, SettingsError):
            values[key] = spec.default
    return {
        "version": data["version"],
        "values": values,
        "stored": stored,
        "updated": data["updated"],
        "by": data["by"],
    }


def read() -> SettingsView:
    """``{version, values, stored, updated, by}``: every setting's value, the keys that were
    set rather than defaulted, and the last write. ``NewerSchema`` for a newer file."""
    return _view(_raw())


def value(key: str) -> SettingValue:
    return read()["values"][key]


def write(
    changes: Mapping[str, object],
    actor: Actor,
    version: int | None = None,
    only_unset: bool = False,
) -> SettingsView:
    """Apply ``changes`` (``None`` clears one back to its default) and return ``read()``.

    ``version`` refuses with ``SettingsStale`` when the file moved since; ``only_unset``
    skips keys already set, which is how a browser's old local value is adopted once.
    """
    wanted = {k: None if v is None else check(k, v) for k, v in changes.items()}
    path = config.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.held(path):
        data = _raw()
        if version is not None and version != data["version"]:
            raise SettingsStale({**_view(data), "asked": version})
        dirty = False
        for key, new in wanted.items():
            had = key in data["values"]
            if only_unset and had:
                continue
            if new is None:
                if had:
                    del data["values"][key]
                    dirty = True
            elif not had or data["values"][key] != new:
                data["values"][key] = new
                dirty = True
        if dirty:
            data["version"] += 1
            data["updated"] = time.time()
            data["by"] = actor.to_dict()
            atomic.write_text(path, json.dumps(data, ensure_ascii=False))
    return _view(data)
