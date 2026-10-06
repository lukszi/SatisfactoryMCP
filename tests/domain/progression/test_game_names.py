"""Every class a response prints reads as words, never as an engine id."""

from __future__ import annotations

import re

from satisfactory_mcp.domain.progression.ladder import SchematicLadder

ENGINE_ID = re.compile(r"^(Desc|Build|BP|Recipe)_\w*_C$")

#: Classes the reference saves hold that Docs.json describes badly or not at all.
SEEN_IN_SAVES = (
    "Desc_HardDrive_C",
    "Desc_ResourceSinkCoupon_C",
    "Desc_BoomBox_C",
    "BP_EquipmentDescriptorCup_C",
    "Desc_AssemblerMk1_C",
    "Build_CentralStorage_C",
    "Build_GeneratorBiomass_C",
    "Build_GeneratorIntegratedBiomass_C",
    "Build_StorageIntegrated_C",
    "Build_StorageBlueprint_C",
)


def test_item_name_never_returns_an_engine_id(game):
    for cls in [*game.items, *SEEN_IN_SAVES]:
        assert not ENGINE_ID.match(game.item_name(cls)), cls


def test_named_classes_read_as_the_game_names_them(game):
    assert game.item_name("Desc_AssemblerMk1_C") == "Assembler"
    assert game.building_name("Build_CentralStorage_C") == "Dimensional Depot Uploader"
    assert game.building_name("Build_GeneratorBiomass_C") == "Biomass Burner"
    assert game.item_name("Desc_ResourceSinkCoupon_C") == "FICSIT Coupon"


def test_retired_mam_nodes_are_offered_nowhere(state):
    ladder = SchematicLadder(game=state.game, unlocks=state.unlocks, inventory=state.inventory)
    names = [r.schematic.name for r in ladder.rungs("EST_MAM")]
    assert names, "the MAM ladder is not empty"
    assert not [n for n in names if not n or n == "SPWN" or n.startswith("Discontinued")]
    assert "Research_Sulfur_3_2_C" not in {r.schematic.cls for r in ladder.rungs("EST_MAM")}
