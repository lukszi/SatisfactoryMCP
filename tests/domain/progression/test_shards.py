"""Power Shards: the clock rule, the shards machines hold, and slugs as craftable stock.

The shards in the save's "machine" inventory are the ones already inside machines, not shards on
hand, and how many a machine holds is not recoverable from its clock.
"""

from __future__ import annotations

import copy

import pytest

from satisfactory_mcp.core.gamedata.constants import (
    POTENTIAL_SHARD_SLOTS,
    max_clock,
    shards_for_clock,
)
from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration


def test_slug_yields_come_from_the_recipes_not_from_memory(game):
    """Blue 1, Yellow 2, Purple 5 are read off Power Shard (1)/(2)/(5). Hardcoding them
    would be exactly the game-knowledge guessing this project refuses elsewhere."""
    yields = {game.item_name(k): v for k, v in game.slug_yields().items()}
    assert yields == {"Blue Power Slug": 1.0, "Yellow Power Slug": 2.0, "Purple Power Slug": 5.0}


def test_the_synthetic_shard_recipe_is_not_treated_as_a_slug(game):
    """Synthetic Power Shard also makes shards, but from Time Crystal, Dark Matter
    Crystal, Quartz and Photonic Matter. That is a production chain, not something
    lying in a crate, so it must not inflate the craftable pool."""
    names = {game.item_name(k) for k in game.slug_yields()}
    assert "Quartz Crystal" not in names
    assert "Time Crystal" not in names
    assert all("Slug" in n for n in names)


def _budget(game, projection):
    from satisfactory_mcp.domain.world.state import WorldState

    return WorldState(projection=projection, game=game).shard_budget()


def _proj(depot=None, player=None, storage=None, machines=()):
    return {
        "depot": depot or {},
        "inventories": {"player": player or {}, "storage": storage or {}, "machine": {}},
        "machines": list(machines),
        "extractors": [],
        "generators": [],
    }


def test_slugs_in_the_depot_count_as_craftable_not_free(game):
    """The reference save holds 93 Blue, 58 Yellow and 39 Purple in the Dimensional
    Depot -- 404 shards against 22 already crafted. Reporting only the crafted pool
    understated what the player could overclock with by ~19x."""
    budget = _budget(
        game,
        _proj(
            depot={
                "Desc_Crystal_C": 93,
                "Desc_Crystal_mk2_C": 58,
                "Desc_Crystal_mk3_C": 39,
                "Desc_CrystalShard_C": 22,
            }
        ),
    )
    assert budget["free"] == 22
    assert budget["craftable"] == 404
    assert budget["potential"] == 426


def test_slugs_are_found_wherever_stock_looks(game):
    """Carried, in a storage container, or in the Depot -- all three are spendable, so
    all three count."""
    for place in ("player", "storage"):
        budget = _budget(game, _proj(**{place: {"Desc_Crystal_mk3_C": 4}}))
        assert budget["craftable"] == 20, place
        assert budget["by_place"], place


def test_shards_inside_machines_are_never_counted_as_free(game):
    """The trap this whole area exists to avoid: the 97 shards on the reference save are
    all in InventoryPotential components, so reading the machine bucket as stock
    overstates the free pool more than 4x."""
    projection = _proj()
    projection["inventories"]["machine"] = {"Desc_CrystalShard_C": 97}
    budget = _budget(game, projection)
    assert budget["free"] == 0
    assert budget["craftable"] == 0


def test_craftable_is_reported_apart_from_free(game):
    """Crafting is a manual step, so slugs are potential and must never be folded into
    a number the player reads as available now."""
    budget = _budget(game, _proj(depot={"Desc_Crystal_C": 10}))
    assert budget["free"] == 0
    assert budget["craftable"] == 10
    assert budget["potential"] == 10


# --------------------------------------------------------------- the shard rule


def test_a_shard_adds_max_clock_from_docs_not_from_a_hardcoded_2_point_5(game):
    """mExtraPotential = 0.5 is in Docs.json under FGPowerShardDescriptor. Only the
    slot count is game knowledge, so the 2.5 ceiling is computed, not written down."""
    shards = game.clock_shards()
    assert shards == {"Desc_CrystalShard_C": 0.5}
    assert max_clock(shards["Desc_CrystalShard_C"]) == 2.5


def test_the_somersloop_shares_the_native_class_and_must_not_count_as_a_shard(game):
    """Desc_WAT1_C sits in the same FGPowerShardDescriptor group as the Power Shard, so
    selecting the group would count Somersloops as overclocking capacity. It has
    mExtraPotential 0 and mExtraProductionBoost 1, and filtering on the field excludes
    it without either class being named in code."""
    assert game.items["Desc_WAT1_C"].extra_potential == 0.0
    assert "Desc_WAT1_C" not in game.clock_shards()


def test_clock_2_0_needs_two_shards_not_three_despite_float_saves(game):
    """Saved clocks are floats and 2.0 arrives as 1.9999999 often enough to matter. A
    bare ceil() on (clock - 1) / 0.5 rounds that up to 3 and reports a 200% machine as
    holding a shard it does not."""
    assert shards_for_clock(2.0, 0.5) == 2
    assert shards_for_clock(1.9999999, 0.5) == 2
    assert shards_for_clock(1.5, 0.5) == 1
    assert shards_for_clock(2.5, 0.5) == 3


def test_underclocking_and_100_percent_need_no_shards(game):
    """5 of the 46 clocked buildings on the reference save are UNDERclocked (down to
    0.333). Treating any clock != 1.0 as an overclock would invent shards for them."""
    assert shards_for_clock(1.0, 0.5) == 0
    assert shards_for_clock(0.333333, 0.5) == 0


def test_shards_needed_never_exceeds_the_slot_count(game):
    """A clock above the ceiling cannot happen in game, but a modded or future save
    could carry one, and reporting "needs 5 shards" for a 3-slot building is worse than
    reporting the cap."""
    assert shards_for_clock(10.0, 0.5) == POTENTIAL_SHARD_SLOTS


# ------------------------------------------------------------- the shard budget


def test_committed_shards_are_read_from_slots_never_derived_from_clock(state):
    """THE correction for this feature. Two buildings on this save run at clock 2.0
    while holding 3 shards: a shard raises the MAXIMUM clock and the slider is set
    separately, so the player left a slot filled and pulled the clock back.

    Summing shards_for_clock over the 42 overclocked buildings gives 98. Reading
    InventoryPotential gives 100. The 2 missing ones are really spent and really not
    available to build with.
    """
    budget = state.shard_budget()
    derived = sum(h["needed"] for h in budget["holders"])
    assert derived == 98
    assert budget["committed"] == 100
    assert budget["measured"] is True

    slack = [h for h in budget["holders"] if h["idle"]]
    assert len(slack) == 2
    assert all(h["clock"] == 2.0 and h["slotted"] == 3 for h in slack)


def test_shards_on_hand_exclude_the_ones_already_inside_machines(state):
    """The save's "machine" inventory bucket holds 100 Power Shards and every one of
    them is in an InventoryPotential component, i.e. already installed. Reading that
    total as shards on hand overstates the free pool by more than 5x -- the player can
    actually spend 19, all of them in the Dimensional Depot."""
    assert state.projection["inventories"]["machine"]["Desc_CrystalShard_C"] == 100
    budget = state.shard_budget()
    assert budget["free"] == 19.0
    assert budget["committed"] == 100
    assert budget["owned"] == 119.0


def test_every_overclocked_building_holds_shards_and_no_other_building_does(state):
    """A cross-check that the two halves agree: 42 buildings have clock > 1.0 and 42
    hold shards, and they are the same 42. An underclocked building holding a shard, or
    an overclocked one holding none, would mean the slot inventory is not what it looks
    like."""
    budget = state.shard_budget()
    overclocked = {r["instance"].rsplit(".", 1)[-1] for r in state.overclocked if r["clock"] > 1.0}
    holders = {h["instance"] for h in budget["holders"]}
    assert len(overclocked) == 42
    assert holders == overclocked


def test_a_projection_without_slot_data_reports_unmeasured_rather_than_zero(state):
    """Schema 9 added potential_slots. An older cached projection has none, and
    reporting "0 shards committed" for it would be a confident wrong answer where
    "unknown" is the true one."""
    projection = copy.deepcopy(state.projection)
    for key in ("machines", "extractors", "generators"):
        for record in projection[key]:
            record.pop("potential_slots", None)
    st = WorldState(projection=projection, game=state.game)
    budget = st.shard_budget()
    assert budget["measured"] is False
    assert budget["committed"] == 0
