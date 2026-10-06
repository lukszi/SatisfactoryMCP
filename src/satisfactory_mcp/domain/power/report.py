"""The grid: what it can make, what it draws, and how much of that is real."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.constants import BUILDING_CLASS_ALIASES
from ...core.gamedata.model import Building, GameData
from ...core.saveio.records import instance_leaf
from .views import GeneratorTotal, PowerReport, StarvedEntry

__all__ = [
    "BIOMASS_BURNERS",
    "NO_FUEL",
    "PowerLedger",
    "biomass_note",
    "dry_input_classes",
    "dry_inputs",
    "generator_building",
    "measured_share",
    "starved_cause",
    "wired_actors",
]

#: Stands in for a fuel class where the save records none -- a hand-fed burner sitting
#: empty. Not an item name; it is printed as it reads.
NO_FUEL = "(no fuel)"

#: Rated hand-fed burners, out of generation unless asked for: their MW is a player's arm,
#: not a plant (frontend_vision.md, "Decided 2026-09-27").
BIOMASS_BURNERS = frozenset(
    {
        "Build_GeneratorBiomass_C",
        "Build_GeneratorBiomass_Automated_C",
    }
)


def wired_actors(projection: dict) -> frozenset[str] | None:
    """Every actor at either end of a power edge, or ``None`` when the projection carries no
    power layer at all -- an older projection, where no record may be called unwired."""
    payload = projection.get("graph") or {}
    if "power" not in payload:
        return None
    actors = payload.get("actors") or []
    return frozenset(actors[i] for row in payload["power"] for i in row[:2] if 0 <= i < len(actors))


def generator_building(game: GameData, cls: str) -> Building | None:
    """The game-data building for a generator class, through the save-to-dump aliases."""
    return game.buildings.get(cls) or game.buildings.get(BUILDING_CLASS_ALIASES.get(cls, ""))


def starved_cause(missing: list[str] | tuple[str, ...]) -> str:
    """What a starved generator lacks, in one phrase."""
    named = [m for m in missing if m != NO_FUEL]
    return "out of " + ", ".join(named) if named else "no fuel loaded"


def biomass_note(report: PowerReport) -> str:
    """The one line naming what ``biomass=False`` left out, or "" when nothing was."""
    n = report.get("biomass_generators") or 0
    if not n or (report.get("biomass_mw") or 0) <= 0:
        return ""
    return (
        f"+{report['biomass_mw']:,.0f} MW biomass not counted: {n} hand-fed burner(s) left "
        "out of generation and headroom; pass biomass=true to count them"
    )


def measured_share(record: dict) -> float | None:
    """How much of a machine's rated draw the save says it is really taking, 0..1.

    ``None`` where the machine keeps no productivity monitor. A caller weighting a machine's
    DRAW must charge such a machine IN FULL: no monitor is not evidence of idleness. The safe
    direction inverts for output -- see ``domain/factories/query.py`` -- so a caller
    weighting production must not copy that rule across.
    """
    uptime = record.get("uptime") or {}
    window = uptime.get("window_s") or 0.0
    if window <= 0:
        return None
    return (uptime.get("produce_s") or 0.0) / window


def dry_inputs(game: GameData, record: dict) -> tuple[str, ...]:
    """Which of a generator's inputs its fuel inventory has run out of, by item name.

    Empty when it holds everything it burns, and empty when the record carries no fuel
    inventory at all -- an absent buffer is not an empty one. Each class is tested by name,
    because a coal plant's one inventory holds coal AND water, and a full hopper behind a
    broken water pipe is the failure worth catching.
    """
    return tuple(
        NO_FUEL if cls == NO_FUEL else game.item_name(cls)
        for cls in dry_input_classes(game, record)
    )


def dry_input_classes(game: GameData, record: dict) -> tuple[str, ...]:
    """`dry_inputs` by item class, for callers that have to join on one. Carries the bare
    ``NO_FUEL`` marker through, which is a state and not a class."""
    fuel = (record.get("buffers") or {}).get("fuel")
    if fuel is None:
        return ()
    held = fuel.get("items") or {}
    building = game.buildings.get(record.get("cls", ""))
    burning = record.get("fuel")
    spec = None
    if building is not None and burning:
        spec = next((f for f in building.fuels if f.fuel_class == burning), None)
    if spec is None:
        return () if any(v > 0 for v in held.values()) else (NO_FUEL,)
    wanted = [spec.fuel_class]
    if building.requires_supplemental and spec.supplemental_class:
        wanted.append(spec.supplemental_class)
    return tuple(cls for cls in wanted if not held.get(cls))


def _generator_mw(building: Building | None, record: dict) -> float:
    if building is None:
        return 0.0
    clock = record.get("clock") or 1.0
    if not building.power_production_mw and building.variable_power_factor:
        return building.variable_power_factor * clock  # geothermal: normal-geyser average
    return building.power_production_mw * clock


@dataclass
class _GenerationTally:
    """Generation capacity, the plants that are not capacity, and what was left out."""

    by_generator: dict[str, GeneratorTotal] = field(default_factory=dict)
    total_mw: float = 0.0
    unmodellable: list[str] = field(default_factory=list)
    starved: list[StarvedEntry] = field(default_factory=list)
    starved_mw: float = 0.0
    unwired: int = 0
    unwired_mw: float = 0.0
    burners: int = 0
    burner_mw: float = 0.0


@dataclass
class _DrawTally:
    """Draw, nameplate and measured, and what was left out of it."""

    draw_mw: float = 0.0
    measured_mw: float = 0.0
    monitored: int = 0
    unmonitored: int = 0
    paused: int = 0
    unwired: int = 0
    unwired_mw: float = 0.0
    unwired_paused: int = 0

    def charge(self, rated: float | None, record: dict, wired: bool) -> None:
        """Add one machine to both totals, weighting the measured one by uptime."""
        if not wired:
            self.unwired += 1
            if record.get("paused"):
                self.unwired_paused += 1
            elif rated is not None:
                self.unwired_mw += rated
            return
        if record.get("paused"):
            self.paused += 1
            return
        if rated is None:
            return
        self.draw_mw += rated
        share = measured_share(record)
        if share is None:
            self.unmonitored += 1
            self.measured_mw += rated
        else:
            self.monitored += 1
            self.measured_mw += rated * share


@dataclass
class PowerLedger:
    """Generation and draw over one save.

    ``paused_count`` is the build census's count, passed in because this class only needs
    the total to report it.
    """

    projection: dict
    game: GameData
    paused_count: int = 0
    wired: frozenset[str] | None = None

    def _is_wired(self, record: dict) -> bool:
        return self.wired is None or instance_leaf(record["instance"]) in self.wired

    def _generation(self, biomass: bool) -> _GenerationTally:
        tally = _GenerationTally()
        for generator_record in self.projection.get("generators", ()):
            cls = generator_record["cls"]
            building = generator_building(self.game, cls)
            mw = _generator_mw(building, generator_record)
            if not self._is_wired(generator_record):
                tally.unwired += 1
                tally.unwired_mw += 0.0 if generator_record.get("paused") else mw
                continue
            if generator_record.get("paused"):
                continue
            if not biomass and cls in BIOMASS_BURNERS:
                tally.burners += 1
                tally.burner_mw += mw
                continue
            if building is None:
                tally.unmodellable.append(cls)  # the HUB's built-in burner, absent from Docs
                continue
            entry = tally.by_generator.setdefault(
                cls, {"name": building.name, "count": 0, "mw": 0.0}
            )
            entry["count"] += 1
            entry["mw"] += mw
            tally.total_mw += mw
            # The DRY INPUT decides and a zero uptime corroborates: a generator load-follows,
            # and one with no monitor is left alone rather than accused.
            missing = dry_inputs(self.game, generator_record)
            if missing and measured_share(generator_record) == 0.0:
                tally.starved.append(
                    {
                        "instance": instance_leaf(generator_record["instance"]),
                        "name": building.name,
                        "mw": mw,
                        "missing": list(missing),
                    }
                )
                tally.starved_mw += mw
        return tally

    def _draw(self) -> _DrawTally:
        tally = _DrawTally()
        for machine_record in self.projection.get("machines", ()):
            recipe = self.game.recipes.get(machine_record.get("recipe") or "")
            clock = machine_record.get("clock") or 1.0
            if recipe is not None:
                rated = self.game.recipe_power_mw(recipe, clock)
            else:
                building = self.game.buildings.get(machine_record["cls"])
                rated = building.power_at(clock) if building else None
            tally.charge(rated, machine_record, self._is_wired(machine_record))
        for extractor_record in self.projection.get("extractors", ()):
            building = self.game.buildings.get(extractor_record["cls"])
            rated = building.power_at(extractor_record.get("clock") or 1.0) if building else None
            tally.charge(rated, extractor_record, self._is_wired(extractor_record))
        return tally

    def power_report(self, *, biomass: bool = False) -> PowerReport:
        """Generation capacity, and draw both nameplate and measured (save-projection §6.1a).

        ``headroom_mw`` is the safe figure, what is free if everything built ran at once;
        ``measured_headroom_mw`` the current one. Unmonitored machines are charged in full,
        unwired records and (without ``biomass``) hand-fed burners are summed apart.
        """
        generation = self._generation(biomass)
        draw = self._draw()
        total_mw = generation.total_mw
        return {
            "generation_mw": total_mw,
            "draw_mw": draw.draw_mw,
            "headroom_mw": total_mw - draw.draw_mw,
            "measured_draw_mw": draw.measured_mw,
            "measured_headroom_mw": total_mw - draw.measured_mw,
            "monitored": draw.monitored,
            "unmonitored": draw.unmonitored,
            "paused_consumers": draw.paused,
            "utilisation": (draw.measured_mw / draw.draw_mw) if draw.draw_mw else 1.0,
            "by_generator": generation.by_generator,
            "unmodellable": sorted(set(generation.unmodellable)),
            "paused_count": self.paused_count,
            "starved_generators": sorted(generation.starved, key=lambda s: -s["mw"]),
            "starved_generation_mw": generation.starved_mw,
            "unwired_generators": generation.unwired,
            "unwired_generation_mw": generation.unwired_mw,
            "unwired_consumers": draw.unwired,
            "unwired_draw_mw": draw.unwired_mw,
            "unwired_paused": draw.unwired_paused,
            "biomass_counted": biomass,
            "biomass_generators": generation.burners,
            "biomass_mw": generation.burner_mw,
        }
