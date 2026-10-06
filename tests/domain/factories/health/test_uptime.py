"""factory_health's states from measured uptime: starved, blocked, backed up, paused.

Every rule here was wrong before it was measured against a real save, so each test
pins the counter-example that corrected it.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.health import (
    ACTIONABLE,
    OK,
    STATES,
)
from tests.support.factory_health import (
    WINDOW,
    assess_records,
    machine,
    state_of,
    uptime_record,
)

pytestmark = pytest.mark.integration


def test_starvation_is_a_missing_ingredient_not_an_empty_input(game):
    """THE correction. Black Powder takes Coal and Sulfur; the assembler that motivated
    this held 100 Sulfur and no Coal. An "is the input empty" test called it well-fed and
    left eight machines filed as unexplained stalls."""
    # The recipe CLASS is Gunpowder; only its display name is Black Powder.
    name = "Build_AssemblerMk1_C_1"
    report = assess_records(
        game,
        machines=[
            machine(
                name,
                "Recipe_Gunpowder_C",
                uptime=uptime_record(0.0),
                buffers={
                    "in": {"items": {"Desc_Sulfur_C": 100}, "slots": 2},
                    "out": {"items": {}, "slots": 2},
                },
            )
        ],
    )
    assert state_of(report, name) == "starved"
    entry = report.machines[0]
    assert entry.cause == ("Coal",), "the missing ingredient is the whole point"
    assert "Sulfur" not in entry.cause


def test_blocked_is_checked_before_starved(game):
    """A blocked machine's input backs up too. The sample reads input 100/100 Iron Ingot
    and output 199/200 Iron Plate -- testing the input first calls it well-fed and misses
    that nothing is taking its plates."""
    name = "Build_ConstructorMk1_C_2"
    report = assess_records(
        game,
        machines=[
            machine(
                name,
                "Recipe_IronPlate_C",
                uptime=uptime_record(0.0),
                buffers={
                    "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
                    "out": {"items": {"Desc_IronPlate_C": 199}, "slots": 2},
                },
            )
        ],
    )
    assert state_of(report, name) == "blocked"
    assert report.blocked_on["Iron Plate"] == 1


def test_a_nearly_full_stack_counts_as_backed_up(game):
    """199 of 200 is backed up. Demanding exactly 100% hides a bottleneck that has just
    ticked one item forward."""
    full = machine(
        "Build_ConstructorMk1_C_3",
        "Recipe_IronPlate_C",
        uptime=uptime_record(0.0),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 199}, "slots": 2},
        },
    )
    half = machine(
        "Build_ConstructorMk1_C_4",
        "Recipe_IronPlate_C",
        uptime=uptime_record(0.0),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 100}, "slots": 2},
        },
    )
    report = assess_records(game, machines=[full, half])
    assert state_of(report, "Build_ConstructorMk1_C_3") == "blocked"
    assert state_of(report, "Build_ConstructorMk1_C_4") == "stalled"


def test_an_extractor_with_no_node_is_a_dead_node_not_starved(game):
    """A miner draws from its node and has no InputInventory, so an empty-input test
    reported every idle miner as starved. When mExtractableResource is ABSENT the miner
    is bound to nothing at all -- on the real save, three left behind when a game update
    removed their resource node."""
    name = "Build_MinerMk1_C_5"
    report = assess_records(
        game,
        extractors=[
            {
                "instance": f"L:P.{name}",
                "cls": "Build_MinerMk1_C",
                "pos": [0, 0, 0],
                "uptime": uptime_record(0.0),
            }
        ],
    )
    assert state_of(report, name) == "dead node"
    assert report.machines[0].cause == ("no resource node",)


def test_an_extractor_with_an_untabled_node_is_not_dead(game):
    """A water pump's node IS set; it points at an FGWaterVolume that is not a
    purity-table key. That is a gap in our table, not a fault in the factory."""
    name = "Build_WaterPump_C_6"
    report = assess_records(
        game,
        extractors=[
            {
                "instance": f"L:P.{name}",
                "cls": "Build_WaterPump_C",
                "pos": [0, 0, 0],
                "node": "L:P.FGWaterVolume42",
                "uptime": uptime_record(1.0),
            }
        ],
    )
    assert state_of(report, name) == "saturated"


def test_missing_produce_duration_is_a_real_zero(game):
    """UE omits SaveGame properties equal to their default, so an absent ProduceDuration
    means the machine produced for zero seconds -- not that the data is missing."""
    name = "Build_SmelterMk1_C_7"
    report = assess_records(
        game,
        machines=[
            machine(
                name,
                "Recipe_IngotIron_C",
                uptime={"window_s": WINDOW, "cur_window_s": 10.0, "producing": False},
                buffers={
                    "in": {"items": {"Desc_OreIron_C": 50}, "slots": 1},
                    "out": {"items": {}, "slots": 1},
                },
            )
        ],
    )
    assert report.machines[0].uptime == 0.0
    assert state_of(report, name) == "stalled"


def test_a_machine_with_no_monitor_is_unknown_not_zero(game):
    name = "Build_ConstructorMk1_C_8"
    report = assess_records(game, machines=[machine(name, "Recipe_IronPlate_C")])
    assert state_of(report, name) == "unmonitored"
    assert report.machines[0].uptime is None
    assert report.mean_uptime is None, "an unknown must not be averaged in as a zero"


def test_paused_outranks_every_other_diagnosis(game):
    name = "Build_ConstructorMk1_C_9"
    report = assess_records(
        game,
        machines=[
            machine(
                name,
                "Recipe_IronPlate_C",
                uptime=uptime_record(0.0),
                paused=True,
                buffers={
                    "in": {"items": {}, "slots": 1},
                    "out": {"items": {"Desc_IronPlate_C": 200}, "slots": 1},
                },
            )
        ],
    )
    assert state_of(report, name) == "paused"


def test_states_are_ordered_worst_first_and_ok_is_a_subset(game):
    assert OK <= set(STATES)
    assert STATES.index("blocked") < STATES.index("saturated")
    assert STATES.index("dead node") < STATES.index("starved")


def test_a_blocked_machine_needs_action_and_a_paused_one_does_not():
    """Decided 2026-09-26: a full output box is a problem, not a factory at rest."""
    assert "blocked" in ACTIONABLE
    assert not OK & set(ACTIONABLE)
    assert "paused" not in ACTIONABLE and "intermittent" not in ACTIONABLE
    assert list(ACTIONABLE) == [s for s in STATES if s in ACTIONABLE], "report order"


def test_worst_reports_only_what_needs_attention(game):
    good = machine("Build_ConstructorMk1_C_10", "Recipe_IronPlate_C", uptime=uptime_record(1.0))
    bad = machine(
        "Build_ConstructorMk1_C_11",
        "Recipe_IronPlate_C",
        uptime=uptime_record(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 1}},
    )
    report = assess_records(game, machines=[good, bad])
    assert [m.instance for m in report.worst()] == ["Build_ConstructorMk1_C_11"]
    assert report.mean_uptime == pytest.approx(0.5)
