"""The save's destroyed-actor list, joined against the map's placement table."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

from ...core.saveio.records import instance_leaf
from ..spatial import geo
from .table import CollectibleTable, name_stem, removed_actor_class

__all__ = ["RemovedActors", "observed_session"]


def observed_session(table: CollectibleTable | None) -> str | None:
    """The session whose saves the table's seen/unseen states were read from."""
    if table is None:
        return None
    return ((table.meta.get("source") or {}).get("status") or {}).get("session") or None


@dataclass
class RemovedActors:
    """What this save has taken off the map, and what that leaves standing.

    Holds the placement table rather than loading it: whether a caller can see one is the
    caller's business, and with ``table=None`` every answer here degrades to the save-only
    name-prefix census and says so.
    """

    projection: dict
    table: CollectibleTable | None
    #: False when the table's seen/unseen states were read from another world's saves.
    observed: bool = True

    @cached_property
    def destroyed_keys(self) -> frozenset[tuple[str, str]]:
        """``(cell, instance name)`` of every actor this save records as gone.

        The pair, never the bare name, for the reason ``CollectibleTable.by_key`` gives.
        """
        removed = self.projection.get("removed") or {}
        cells: list[str] = removed.get("cells") or []
        return frozenset(
            (cells[ix] if 0 <= ix < len(cells) else "", leaf)
            for ix, leaf in removed.get("instances") or []
        )

    #: The placement table's ``state`` -> what it means about a placement this save has NOT
    #: collected. ``never_streamed`` is a placement no save has ever loaded, and must never
    #: be presented as standing there.
    OBSERVED: ClassVar[dict[str, str]] = {
        "present": "standing",
        "unknown": "never_streamed",
        "collected": "gone_in_a_later_save",
    }

    def placements(self, category: str | None = None, remaining_only: bool = False) -> list[dict]:
        """Map placements annotated with what THIS save says about each one.

        ``collected`` is exact and about the loaded save. ``observed`` is the one field that
        is not: it comes from the placement table's scan of every save on disk, which is what
        lets a placement nobody has visited be reported as such rather than as standing.
        """
        table = self.table
        if table is None:
            return []
        gone = self.destroyed_keys
        source = table.by_category.get(category, []) if category else table.rows
        out: list[dict] = []
        for row in source:
            name = instance_leaf(row["instance"])
            collected = (row["cell"], name) in gone
            if collected and remaining_only:
                continue
            out.append(
                {
                    "category": row["category"],
                    "cls": row["class"],
                    "name": name,
                    "cell": row["cell"],
                    "pos": (row["x"], row["y"], row["z"]),
                    "collected": collected,
                    #: ``None`` on a state this code does not know, never a nearest guess.
                    "observed": (
                        None
                        if collected or not self.observed
                        else self.OBSERVED.get(row.get("state") or "")
                    ),
                    "looted": row.get("looted"),
                    "contents": row.get("contents"),
                    "unlock_cost": row.get("unlock_cost"),
                    "hazard": row.get("hazard") or {},
                }
            )
        return out

    def nearest_placements(
        self, origin: tuple[float, float], category: str | None = None
    ) -> list[dict]:
        """Remaining placements, nearest first, each with a planar distance in metres.

        Only remaining ones, since the question is "where do I go and get one". Planar,
        because Z spans a few hundred metres against a 7 km map and mixing them in would
        flatter a placement directly up a cliff face.
        """
        rows = self.placements(category, remaining_only=True)
        for row in rows:
            row["distance_m"] = geo.distance_m((row["pos"][0], row["pos"][1]), origin)
        rows.sort(key=lambda r: r["distance_m"])
        return rows

    def collectible_census(self) -> list[dict]:
        """Per category: what the map placed, what this save collected, and what is left.

        ``placed`` is the map's own count and ``collected`` this save's own destroyed list,
        so ``remaining`` is arithmetic between two exact numbers -- for every category the
        game records. Where it does not, ``remaining`` is ``None`` and not a number: see
        ``CollectibleTable.state_tracked``. It then splits by how much has been *observed*,
        so a placement in a cell no save ever streamed in counts as remaining while being
        reported as ``never_streamed``. Counted off ``placements`` rather than off the table
        again, so that what can be listed is exactly what was counted.
        """
        table = self.table
        if table is None:
            return []
        tally: dict[str, dict] = {}
        for placement in self.placements():
            counted = tally.setdefault(
                placement["category"],
                {
                    "collected": 0,
                    #: Standing but already emptied. Only a drop pod can be both.
                    "looted_and_standing": 0,
                    **dict.fromkeys((*self.OBSERVED.values(), "unstated"), 0),
                },
            )
            if placement["collected"]:
                counted["collected"] += 1
                continue
            #: ``unstated`` catches a table newer than this code, rather than a state this
            #: code does not know quietly landing in one it does.
            counted[placement["observed"] or "unstated"] += 1
            if placement["looted"]:
                counted["looted_and_standing"] += 1

        rows: list[dict] = []
        for category in table.categories:
            counted = tally[category]
            placed = len(table.by_category[category])
            tracked = table.state_tracked(category)
            rows.append(
                {
                    "category": category,
                    "cls": table.cls_of(category),
                    "placed": placed,
                    #: None, not placed-minus-zero, when a collection would leave no record.
                    "remaining": (placed - counted["collected"]) if tracked else None,
                    **counted,
                    "state_tracked": tracked,
                    "pedestal_of": table.pedestal_of(category),
                    "note": table.note_for(category),
                }
            )
        return rows

    def collected_summary(self, group: str | None = None) -> dict:
        """What this save records as collected off the map, resolved against the map itself.

        The destroyed list IS the collected list (save-projection.md §6.11), and only the
        join to the placement table by ``(cell, name)`` gives an entry a class. Without the
        table this degrades to the name-prefix census under ``source: "save-only"``.
        """
        removed = self.projection.get("removed") or {}
        cells: list[str] = removed.get("cells") or []
        instances: list = removed.get("instances") or []
        counts: dict[str, int] = removed.get("counts") or {}
        table = self.table
        out: dict = {
            #: Every destroyed record, including the classes the map table does not track.
            "total": len(instances) or sum(counts.values()),
            "cells": len(cells),
            "source": "save-only" if table is None else "map",
        }
        if table is None:
            return self._removed_by_name(out, instances, cells, group)

        collected: dict[str, int] = {}
        stems: dict[str, int] = {}
        for ix, leaf in instances:
            row = table.by_key.get((cells[ix] if 0 <= ix < len(cells) else "", leaf))
            if row is None:
                stem = name_stem(leaf)
                stems[stem] = stems.get(stem, 0) + 1
            else:
                collected[row["category"]] = collected.get(row["category"], 0) + 1
        out["groups"] = dict(sorted(collected.items(), key=lambda kv: -kv[1]))
        out["resolved"] = sum(collected.values())
        out["unresolved"] = sum(stems.values())
        # Destroyed records the map places nothing at, by name stem: a class the table
        # excludes (``_meta.excluded``), or an actor the map never placed, like a dropped pickup.
        out["unresolved_stems"] = dict(sorted(stems.items(), key=lambda kv: (-kv[1], kv[0])))
        out["census"] = self.collectible_census()
        if group is None:
            return out
        if group not in table.by_category:
            out["error"] = f"unknown group {group!r}; the map places: {sorted(table.by_category)}"
            return out
        out["group"] = group
        out["actors"] = [p for p in self.placements(group) if p["collected"]]
        return out

    def _removed_by_name(
        self, out: dict, instances: list, cells: list[str], group: str | None
    ) -> dict:
        """The census the save can build on its own: counts by name prefix, and wrong.

        A fresh clone has no placement table, and "collected 889 things" is still worth
        having. Every caller must label it: this cannot say what remains, and the counts it
        does give are known to misfile one actor in fourteen.
        """
        grouped: dict[str, int] = {}
        unmatched: dict[str, int] = {}
        for _ix, leaf in instances:
            label = self.removed_group(leaf)
            if label is None:
                cls = removed_actor_class(leaf)
                unmatched[cls] = unmatched.get(cls, 0) + 1
            else:
                grouped[label] = grouped.get(label, 0) + 1
        out["groups"] = dict(sorted(grouped.items(), key=lambda kv: -kv[1]))
        if unmatched:
            out["other"] = dict(sorted(unmatched.items(), key=lambda kv: -kv[1]))
        if group is None:
            return out
        known = [g for g, _p, _s in self.REMOVED_GROUPS]
        if group not in known:
            out["error"] = f"unknown group {group!r}; known: {known}"
            return out
        out["group"] = group
        out["actors"] = [
            {"name": leaf, "cell": cells[ix] if 0 <= ix < len(cells) else "", "pos": None}
            for ix, leaf in instances
            if self.removed_group(leaf) == group
        ]
        return out

    #: The name-only fallback when the placement table is absent: ``(label, prefixes,
    #: strict)``, first match wins. Measurably wrong with no fix (save-projection.md §6.11).
    REMOVED_GROUPS: ClassVar[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
        ("slug_purple", ("BP_Crystal_mk3",), False),
        ("slug_yellow", ("BP_Crystal_mk2",), False),
        ("slug_blue", ("BP_Crystal",), False),
        ("somersloop", ("BP_WAT1",), True),
        ("mercer_sphere", ("BP_WAT2",), True),
        ("artifact_unsplit", ("BP_WAT",), False),
        ("mercer_shrine", ("BP_MercerShrine",), False),
        ("crash_site", ("BP_DropPod", "BP_Ship", "BP_CrashSiteDebris"), False),
        ("flora", ("BP_Shroom", "BP_SporeFlower", "BP_NutBush", "BP_BerryBush"), False),
        ("debris", ("BP_DebrisActor", "BP_Rock", "BP_Boulder", "BP_Destructible"), False),
        ("dropped_pickup", ("FGItemPickup_Spawnable",), False),
    )

    def removed_group(self, name: str) -> str | None:
        """Which name-prefix group a removed actor belongs to, for the fallback census only.

        First match wins, and a ``strict`` group only accepts a name spelling its class out
        with ``_C``. Neither guard is enough -- see ``REMOVED_GROUPS``.
        """
        for label, prefixes, strict in self.REMOVED_GROUPS:
            if strict:
                if any(name.startswith(f"{p}_C") for p in prefixes):
                    return label
            elif name.startswith(prefixes):
                return label
        return None
