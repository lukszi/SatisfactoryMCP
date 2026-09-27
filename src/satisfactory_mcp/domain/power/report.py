"""The grid: what it can make, what it draws, and how much of that is real."""

from __future__ import annotations

from dataclasses import dataclass

from ...core.gamedata.constants import BUILDING_CLASS_ALIASES
from ...core.gamedata.model import Building, GameData

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

#: Hand-fed burners, the HUB's own included. Left out of generation unless asked for: no
#: automated supply of biomass or biofuel exists, so their MW is a player's arm, not a plant.
#: See docs/frontend_vision.md, "Decided 2026-09-27".
BIOMASS_BURNERS = frozenset(
    {
        "Build_GeneratorBiomass_C",
        "Build_GeneratorBiomass_Automated_C",
        "Build_GeneratorIntegratedBiomass_C",
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


def biomass_note(report: dict) -> str:
    """The one line naming what ``biomass=False`` left out, or "" when nothing was."""
    n = report.get("biomass_generators") or 0
    if not n:
        return ""
    return (
        f"+{report['biomass_mw']:,.0f} MW biomass not counted: {n} hand-fed burner(s) left "
        "out of generation and headroom; pass biomass=true to count them"
    )


def measured_share(record: dict) -> float | None:
    """How much of a machine's rated draw the save says it is really taking, 0..1.

    ``None`` where the machine keeps no productivity monitor. A caller weighting a machine's
    DRAW must charge such a machine IN FULL: no monitor is not evidence of idleness, and
    treating it as idle would make the measured figure optimistic in exactly the case
    nothing can check it. The safe direction inverts for output -- see
    ``domain/factories/query.py`` -- so a caller weighting production must not copy that
    rule across.
    """
    uptime = record.get("uptime") or {}
    window = uptime.get("window_s") or 0.0
    if window <= 0:
        return None
    return (uptime.get("produce_s") or 0.0) / window


def dry_inputs(game: GameData, record: dict) -> tuple[str, ...]:
    """Which of a generator's inputs its fuel inventory has run out of, by item name.

    Empty when it holds everything it burns, and empty when the record carries no fuel
    inventory at all -- an absent buffer is not an empty one.

    A coal plant's fuel inventory holds its coal AND its supplemental water, so asking
    whether that inventory is empty cannot see the failure worth catching: a full hopper
    behind a broken water pipe. Each class the generator needs is tested by name instead.
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
        return self.wired is None or record["instance"].rsplit(".", 1)[-1] in self.wired

    def power_report(self, *, biomass: bool = False) -> dict:
        """Generation capacity, and draw both nameplate and measured.

        Uptime is the projection's 300 s productivity monitor, carried by **524 of 570**
        records on the reference save. Both draws are reported because they answer different
        questions:

        * ``headroom_mw`` (nameplate) is the **safe** figure -- what is free if everything
          currently built ran at once. Energising a block can un-starve idle machines
          downstream, so this is the one not to exceed if you cannot watch it.
        * ``measured_headroom_mw`` is the **current** figure, weighted by how much of the
          factory is actually running.

        They are far apart: on the reference save nameplate draw is **6,901 MW** against a
        measured **2,389 MW**, so 649 MW of headroom nameplate against roughly
        **5,161 MW** actual.

        A machine with no productivity monitor is charged at full nameplate on both sides:
        unknown utilisation must not read as idle. Generators are capacity either way,
        since they burn to meet demand rather than at a rate of their own. Paused buildings
        are excluded from both sides.

        ``starved_generators`` is the exception to "generation is capacity": a plant with a
        dry input is not capacity, it is a number that will not appear when the grid asks
        for it. It names each one, since knowing WHICH plant is the whole value.

        With ``wired`` set, a record on no power edge is left out of both sides and counted
        under ``unwired_*`` instead, so the ledger is the sum of the circuits. The wire is
        tested first: a paused record on no wire is still counted there, at no MW, which is
        the rule ``assess`` lists its unwired machines by.

        Wired ``BIOMASS_BURNERS`` count only with ``biomass`` set. Otherwise they are left
        out of generation, both headrooms and the starved list, and summed under
        ``biomass_*`` instead, so a surface can say what it left out.
        """
        gen: dict[str, dict] = {}
        total_mw = 0.0
        variable: list[str] = []
        starved: list[dict] = []
        starved_mw = 0.0
        loose = {"generators": 0, "generation_mw": 0.0, "consumers": 0, "draw_mw": 0.0, "paused": 0}
        burners = 0
        burner_mw = 0.0
        for g in self.projection.get("generators", ()):
            b = generator_building(self.game, g["cls"])
            mw = 0.0
            if b is not None:
                clock = g.get("clock") or 1.0
                mw = b.power_production_mw * clock
                if not b.power_production_mw and b.variable_power_factor:
                    mw = b.variable_power_factor * clock  # geothermal: normal-geyser average
            if not self._is_wired(g):
                loose["generators"] += 1
                loose["generation_mw"] += 0.0 if g.get("paused") else mw
                continue
            if g.get("paused"):
                continue
            if not biomass and g["cls"] in BIOMASS_BURNERS:
                burners += 1
                burner_mw += mw
                continue
            if b is None:
                variable.append(g["cls"])  # the HUB's built-in burner, absent from Docs
                continue
            entry = gen.setdefault(g["cls"], {"name": b.name, "count": 0, "mw": 0.0})
            entry["count"] += 1
            entry["mw"] += mw
            total_mw += mw
            # The DRY INPUT decides; uptime only corroborates. A generator load-follows, so
            # it legitimately reads below 1.0 with full tanks and calling that starved would
            # condemn every healthy plant on a quiet grid. Zero is the corroboration, and a
            # plant with no monitor gets none -- so it is left alone rather than accused.
            missing = dry_inputs(self.game, g)
            if missing and measured_share(g) == 0.0:
                starved.append(
                    {
                        "instance": g["instance"].rsplit(".", 1)[-1],
                        "name": b.name,
                        "mw": mw,
                        "missing": list(missing),
                    }
                )
                starved_mw += mw

        draw = 0.0
        measured = 0.0
        monitored = 0
        unmonitored = 0
        paused = 0

        def _charge(rated: float | None, record: dict) -> None:
            """Add one machine to both totals, weighting the measured one by uptime."""
            nonlocal draw, measured, monitored, unmonitored, paused
            if not self._is_wired(record):
                loose["consumers"] += 1
                if record.get("paused"):
                    loose["paused"] += 1
                elif rated is not None:
                    loose["draw_mw"] += rated
                return
            if record.get("paused"):
                paused += 1
                return
            if rated is None:
                return
            draw += rated
            share = measured_share(record)
            if share is None:
                unmonitored += 1
                measured += rated
            else:
                monitored += 1
                measured += rated * share

        for m in self.projection.get("machines", ()):
            r = self.game.recipes.get(m.get("recipe") or "")
            clock = m.get("clock") or 1.0
            if r is not None:
                _charge(self.game.recipe_power_mw(r, clock), m)
            else:
                b = self.game.buildings.get(m["cls"])
                _charge(b.power_at(clock) if b else None, m)
        for e in self.projection.get("extractors", ()):
            b = self.game.buildings.get(e["cls"])
            _charge(b.power_at(e.get("clock") or 1.0) if b else None, e)

        return {
            "generation_mw": total_mw,
            "draw_mw": draw,
            "headroom_mw": total_mw - draw,
            "measured_draw_mw": measured,
            "measured_headroom_mw": total_mw - measured,
            "monitored": monitored,
            "unmonitored": unmonitored,
            "paused_consumers": paused,
            "utilisation": (measured / draw) if draw else 1.0,
            "by_generator": gen,
            "unmodellable": sorted(set(variable)),
            "paused_count": self.paused_count,
            "starved_generators": sorted(starved, key=lambda s: -s["mw"]),
            "starved_generation_mw": starved_mw,
            "unwired_generators": loose["generators"],
            "unwired_generation_mw": loose["generation_mw"],
            "unwired_consumers": loose["consumers"],
            "unwired_draw_mw": loose["draw_mw"],
            "unwired_paused": loose["paused"],
            "biomass_counted": biomass,
            "biomass_generators": burners,
            "biomass_mw": burner_mw,
        }
