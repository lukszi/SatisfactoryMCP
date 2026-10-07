"""Advisors: the cheap list of things worth a look, and what the player hid of it.

``advisory`` is the row contract, ``rules`` computes the rows, ``store`` keeps dismiss and
snooze, and ``current`` joins the two the way the page and chat both read them.
docs/advisors_contract.md is the specification.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.schema import NewerSchema
from ...core.singleflight import Singleflight
from .. import settings
from . import advisory, rules, store
from .advisory import KINDS, PER_KIND, SEVERITIES, TONE, VISIBLE, WORDS, Advisory, Spot
from .store import Active, Hidden
from .views import AdviceOptions

if TYPE_CHECKING:
    from ...core.gamedata.model import GameData
    from ...core.saveio.schema import Projection
    from ..world.state import WorldState

__all__ = [
    "KINDS",
    "PER_KIND",
    "SEVERITIES",
    "TONE",
    "VISIBLE",
    "WORDS",
    "Advisory",
    "Current",
    "Spot",
    "advisory",
    "current",
    "options",
    "rules",
    "store",
]

#: (projection, game, labels version, plan heads, options) -> ranked rows. The projection and
#: game are held by the entry: the key is their ``id()``.
_ROWS: Singleflight[Hashable, tuple[Projection, GameData, list[Advisory]]] = Singleflight(maxsize=8)


@dataclass
class Current:
    """One read: every firing row, which are active, which are hidden, and the play clock."""

    items: list[Advisory]
    active: list[Active]
    hidden: list[Hidden]
    version: int
    play_s: float
    notes: list[str]

    def find(self, adv_id: str) -> Advisory | None:
        return next((a for a in self.items if a.id == adv_id), None)


def options(
    biomass: bool | None = None, headroom: str | None = None
) -> tuple[AdviceOptions, list[str]]:
    """The shared settings the pass reads, and a note per one that could not be read."""
    notes: list[str] = []
    values: Mapping[str, settings.SettingValue]
    try:
        values = settings.read()["values"]
    except (NewerSchema, OSError) as exc:
        values = {k: s.default for k, s in settings.SPECS.items()}
        notes.append(f"shared settings unreadable, defaults used: {exc}")
    out: AdviceOptions = {
        "biomass": bool(values["biomass"]) if biomass is None else biomass,
        "headroom": str(values["stage_headroom"]) if headroom is None else headroom,
        "box_fed": bool(values["advice_box_fed"]),
    }
    return out, notes


def _rows(st: WorldState, opts: AdviceOptions, spoilers: bool) -> list[Advisory]:
    key = (
        id(st.projection),
        id(st.game),
        st.labels.version,
        tuple(sorted((state.key, state.rev) for state in rules.plan_heads(st))),
        tuple(sorted(opts.items())),
        spoilers,
    )
    held = _ROWS.get(
        key, lambda: (st.projection, st.game, rules.compute(st, spoilers=spoilers, **opts))
    )
    return held[2]


def current(
    st: WorldState,
    *,
    biomass: bool | None = None,
    headroom: str | None = None,
    spoilers: bool = False,
) -> Current:
    """What fires on ``st`` with this world's hidden entries applied. ``NewerSchema`` when the
    store is from a newer version."""
    opts, notes = options(biomass, headroom)
    data = store.read(st.world_id)
    rows = _rows(st, opts, spoilers)
    items = rules.with_ids(rows, data["hidden"].keys())
    play_s = float(st.header.get("play_duration_s") or 0.0)
    active, hidden = store.split(items, data, play_s)
    return Current(items, active, hidden, data["version"], play_s, notes)
