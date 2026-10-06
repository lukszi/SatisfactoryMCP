"""Derived views over a save projection, joined against normalized game data.

Each subject -- unlocks, the build census, power, progression, research gates, hard drives,
overclocking, inventories, the map's collectible table, the save's identity -- is a small
dataclass of its own, built from the same two fields. ``WorldState`` holds them and
delegates; it is the context every other domain package takes as an argument.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any, ClassVar

from ...core.gamedata.model import GameData, Recipe
from ...core.saveio import projection as proj
from ...core.singleflight import Singleflight
from ..collectibles.removed import RemovedActors, observed_session
from ..collectibles.table import CollectibleTable, load_collectibles
from ..power.report import PowerLedger, wired_actors
from ..progression.harddrives import HardDriveDesk, HardDriveOffer
from ..progression.phases import PhaseLedger
from ..progression.research import ResearchGates
from ..progression.shards import OverclockBudget
from ..progression.unlocks import UnlockSet
from . import flow as world_flow
from . import sites as world_sites
from . import water as world_water
from .carriers import CarrierSet
from .census import BuildCensus
from .identity import SaveIdentity
from .inventory import Inventory

__all__ = ["CollectibleTable", "HardDriveOffer", "WorldState", "load_collectibles"]

#: The six expensive views that are pure functions of ``(projection, game)``, shared by every
#: state built over the same pair -- a request builds its own ``WorldState`` and the whole
#: ~0.8 s of graph, structures, pipe flow, conduit runs, the physical graph and proposals was
#: being paid per request per layer. Six names times three projections, matching the
#: projection memo's own depth, and ~7 MB of views per projection on the reference world
#: beside its own ~13 MB.
_DERIVED = Singleflight(maxsize=18)


@dataclass
class WorldState:
    """A save projection plus the game data needed to interpret it."""

    projection: dict
    game: GameData

    def _derived(self, name: str, build: Callable[[], Any]) -> Any:
        """A view shared by every state over this same projection and game data.

        Only for views that are pure functions of that pair and that no caller mutates: two
        requests hold the same object, so an in-place edit by one is an answer change for the
        other. ``plans`` and ``labels`` are the counter-example and are deliberately not here
        -- they are disk-backed stores other processes write to, so sharing them would
        hide one process's rename from the other until the next autosave.

        The stored entry holds the projection and the game data, and must keep doing so:
        the key is their ``id()``, and a freed object's address is reused.
        """
        key = (id(self.projection), id(self.game), name)
        return _DERIVED.get(key, lambda: (self.projection, self.game, build()))[2]

    # ---- facets ---------------------------------------------------------
    #
    # One cached_property each, so a facet is built at most once per state and the caches
    # inside it live as long as this object does.

    @cached_property
    def identity(self) -> SaveIdentity:
        return SaveIdentity(projection=self.projection)

    @cached_property
    def unlocks(self) -> UnlockSet:
        return UnlockSet(projection=self.projection, game=self.game)

    @cached_property
    def census(self) -> BuildCensus:
        return BuildCensus(projection=self.projection, game=self.game, unlocks=self.unlocks)

    @cached_property
    def inventory(self) -> Inventory:
        return Inventory(projection=self.projection, game=self.game)

    @cached_property
    def carriers(self) -> CarrierSet:
        return CarrierSet(game=self.game, unlocked_building_ids=self.unlocks.unlocked_building_ids)

    @cached_property
    def power(self) -> PowerLedger:
        return PowerLedger(
            projection=self.projection,
            game=self.game,
            paused_count=len(self.census.paused),
            wired=wired_actors(self.projection),
        )

    @cached_property
    def phases(self) -> PhaseLedger:
        return PhaseLedger(projection=self.projection, game=self.game, unlocks=self.unlocks)

    @cached_property
    def research(self) -> ResearchGates:
        return ResearchGates(
            projection=self.projection,
            game=self.game,
            unlocks=self.unlocks,
            inventory=self.inventory,
        )

    @cached_property
    def harddrive_desk(self) -> HardDriveDesk:
        return HardDriveDesk(projection=self.projection, game=self.game, unlocks=self.unlocks)

    @cached_property
    def overclock(self) -> OverclockBudget:
        return OverclockBudget(
            projection=self.projection,
            game=self.game,
            inventory=self.inventory,
            records=self.census.all_records(),
        )

    @cached_property
    def removed(self) -> RemovedActors:
        table = self.collectibles
        session = observed_session(table)
        name = self.header.get("session_name")
        return RemovedActors(
            projection=self.projection,
            table=table,
            observed=not session or not name or session == name,
        )

    # ---- identity ------------------------------------------------------

    @property
    def header(self) -> dict:
        return self.identity.header

    @property
    def world_id(self) -> str:
        return self.identity.world_id

    @property
    def session_name(self) -> str:
        """The save's session name; ``""`` when the header carries none."""
        return self.header.get("session_name") or ""

    @property
    def token(self) -> str:
        return self.identity.token

    @property
    def age_note(self) -> str:
        return self.identity.age_note

    @cached_property
    def graph(self):
        """The factory graph, ~50 ms. Identity, health and layout all want it.

        Shared, so ``FactoryGraph.adjacency``'s lazy index is built under concurrency: it
        publishes a fully built dict in one assignment, and two threads racing lose only the
        duplicated work.
        """
        from ..factories.build import build_graph

        return self._derived("graph", lambda: build_graph(self.projection))

    @cached_property
    def pipe_flow(self) -> list[dict]:
        """Which way each pipe carries fluid, ~13 ms. It walks the plumbing once per pipe,
        and every caller wants the whole answer rather than one row."""
        return self._derived("pipe_flow", lambda: world_flow.pipe_flow(self.projection))

    @cached_property
    def conduit_runs(self):
        """Belt and pipe runs as queryable geometry, ~170 ms. describe_location and the
        conduit search both want the whole set."""
        from . import conduits

        return self._derived(
            "conduit_runs",
            lambda: conduits.build_runs(self.projection, self.game, self.pipe_flow),
        )

    @cached_property
    def physical(self):
        """What actually feeds what, belt and pipe runs contracted away, ~19 ms. Health and
        the upstream walk both want the whole contraction."""
        from .logistics import build_physical_graph

        return self._derived("physical", lambda: build_physical_graph(self.projection, self.game))

    @cached_property
    def structures(self):
        """Foundation slabs -- what was physically built as one platform. ~85 ms."""
        from ..factories.structure import build_structures

        return self._derived("structures", lambda: build_structures(self.projection))

    @cached_property
    def proposals(self):
        """Coherence-scored factory proposals. ~0.5 s, the most expensive view here."""
        from ..factories.cohere import propose

        return self._derived(
            "proposals",
            lambda: propose(self.graph, self.game, self.projection, self.structures),
        )

    @cached_property
    def plans(self):
        """Live plans saved for this world, read-only; ``planlog.PlanLog`` writes them."""
        from ..planning.stored.planlog import PlanLog

        return PlanLog(self.world_id, self.session_name).view()

    @cached_property
    def labels(self):
        """Persisted factory names for this world."""
        from ..factories.labels import LabelStore

        return LabelStore.load(self.world_id, self.session_name)

    # ---- unlocks -------------------------------------------------------

    @property
    def available_recipe_ids(self) -> set[str]:
        return self.unlocks.available_recipe_ids

    @property
    def purchased_schematic_ids(self) -> set[str]:
        return self.unlocks.purchased_schematic_ids

    @property
    def unresolved_recipe_ids(self) -> set[str]:
        return self.unlocks.unresolved_recipe_ids

    def unlocked_recipes(self, kind: str = "part") -> list[Recipe]:
        return self.unlocks.unlocked_recipes(kind)

    def has_recipe(self, recipe_id: str) -> bool:
        return self.unlocks.has_recipe(recipe_id)

    @property
    def unlocked_alternates(self) -> list[Recipe]:
        return self.unlocks.unlocked_alternates

    @property
    def locked_alternates(self) -> list[Recipe]:
        return self.unlocks.locked_alternates

    @property
    def unlocked_building_ids(self) -> set[str]:
        return self.unlocks.unlocked_building_ids

    def can_build(self, building_id: str) -> bool:
        return self.unlocks.can_build(building_id)

    def dependencies_met(self, schematic_id: str) -> tuple[bool, list[str]]:
        return self.unlocks.dependencies_met(schematic_id)

    # ---- what is actually built ----------------------------------------

    @property
    def built_counts(self) -> dict[str, int]:
        return self.census.built_counts

    def built(self, building_id: str) -> int:
        return self.census.built(building_id)

    def unlocked_but_unbuilt(self) -> list[str]:
        return self.census.unlocked_but_unbuilt()

    @property
    def paused(self) -> list[dict]:
        return self.census.paused

    @property
    def misconfigured(self) -> list[dict]:
        return self.census.misconfigured

    @property
    def overclocked(self) -> list[dict]:
        return self.census.overclocked

    def all_records(self) -> list[dict]:
        return self.census.all_records()

    # ---- power ---------------------------------------------------------

    def power_report(self, *, biomass: bool = False) -> dict:
        return self.power.power_report(biomass=biomass)

    # ---- carriers, and the water they are drawn from --------------------

    BELT_NATIVE: ClassVar[str] = CarrierSet.BELT_NATIVE
    PIPE_NATIVE: ClassVar[str] = CarrierSet.PIPE_NATIVE

    def best_carrier(self, native: str, rate: str) -> tuple[str, float] | None:
        return self.carriers.best_carrier(native, rate)

    def best_belt(self) -> tuple[str, float] | None:
        return self.carriers.best_belt()

    def best_pipe(self) -> tuple[str, float] | None:
        return self.carriers.best_pipe()

    def water_volumes(self) -> dict:
        return world_water.water_volumes(self.projection)

    def site_water(self, x_m: float, y_m: float, **kw) -> world_water.SiteWater | None:
        return world_water.site_water(self.projection, x_m, y_m, **kw)

    # ---- progression ---------------------------------------------------

    EGP_TO_PHASE: ClassVar[dict[str, str]] = PhaseLedger.EGP_TO_PHASE

    def progression(self) -> dict:
        return self.phases.progression()

    def phase_requirements(self) -> dict:
        return self.phases.phase_requirements()

    # ---- overclocking ----------------------------------------------------

    SOMERSLOOP_ITEM: ClassVar[str] = OverclockBudget.SOMERSLOOP_ITEM
    MERCER_ITEM: ClassVar[str] = OverclockBudget.MERCER_ITEM

    def shard_budget(self) -> dict:
        return self.overclock.shard_budget()

    def sloop_budget(self) -> dict:
        return self.overclock.sloop_budget()

    # ---- research gates --------------------------------------------------

    CAPABILITY_FLAGS: ClassVar[dict[str, str]] = ResearchGates.CAPABILITY_FLAGS

    def has_capability(self, name: str) -> bool:
        return self.research.has_capability(name)

    def research_gate(self, name: str) -> dict | None:
        return self.research.research_gate(name)

    # ---- what the map placed, and what is left of it ----------------------

    OBSERVED: ClassVar[dict[str, str]] = RemovedActors.OBSERVED
    REMOVED_GROUPS: ClassVar[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
        RemovedActors.REMOVED_GROUPS
    )

    @cached_property
    def collectibles(self) -> CollectibleTable | None:
        """The map's placement table, or ``None`` when it has not been generated.

        Read through this module's own global rather than inside ``RemovedActors``, so a
        test can stand in a clone that has none.
        """
        return load_collectibles()

    @property
    def destroyed_keys(self) -> frozenset[tuple[str, str]]:
        return self.removed.destroyed_keys

    def placements(self, category: str | None = None, remaining_only: bool = False) -> list[dict]:
        return self.removed.placements(category, remaining_only)

    def nearest_placements(
        self, origin: tuple[float, float], category: str | None = None
    ) -> list[dict]:
        return self.removed.nearest_placements(origin, category)

    def collectible_census(self) -> list[dict]:
        return self.removed.collectible_census()

    def collected_summary(self, group: str | None = None) -> dict:
        return self.removed.collected_summary(group)

    def removed_group(self, name: str) -> str | None:
        return self.removed.removed_group(name)

    # ---- MAM / hard drives ---------------------------------------------

    @property
    def hard_drive_offers(self) -> list[HardDriveOffer]:
        return self.harddrive_desk.hard_drive_offers

    def spare_hard_drives(self) -> int:
        return self.harddrive_desk.spare_hard_drives(self.inventory)

    # ---- where the player is -------------------------------------------

    @property
    def players(self) -> list[dict]:
        return self.identity.players

    def player_position(self) -> tuple[float, float, float] | None:
        return self.identity.player_position()

    # ---- what is held ----------------------------------------------------

    def stock(self) -> dict[str, float]:
        return self.inventory.stock()

    def machine_buffers(self) -> dict[str, float]:
        return self.inventory.machine_buffers()

    # ---- sites ---------------------------------------------------------

    def infra_points(self) -> list[tuple[float, float]]:
        return world_sites.infra_points(self.all_records())

    def consumer_z(self, building_ids: tuple[str, ...] = ("Build_OilRefinery_C",)) -> float | None:
        return world_sites.consumer_z(self.all_records(), building_ids)

    def sites(self, link_m: float = 300.0) -> list[dict]:
        return world_sites.sites(self.all_records(), link_m)


def load_state(
    game: GameData,
    path: str | None = None,
    world: str | None = None,
    prefer_manual: bool = False,
    refresh: bool = False,
) -> WorldState:
    return WorldState(
        projection=proj.load_projection(path, world, prefer_manual, refresh), game=game
    )
