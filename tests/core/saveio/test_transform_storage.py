"""The projection's storage: every container and fluid buffer, and what each holds."""

from __future__ import annotations

from satisfactory_mcp.core.saveio.extract import inventories
from satisfactory_mcp.core.saveio.extract.registers import FLUID_BUFFER_CLASSES, STORAGE_CLASSES


def test_storage_is_every_container_and_buffer_and_nothing_else(projection):
    """The class list, held against the census the way the attachments' is.

    Every row's class has to be one the projection also counted as built, and the count has to
    match exactly: a container in ``building_counts`` and missing here is a box the map would
    not draw, and the reverse would be a box that is not there.
    """
    counts = projection["building_counts"]
    rows = projection["storage"]
    assert len(rows) == 151, "the reference world's containers and buffers"
    assert {r["cls"] for r in rows} <= set(STORAGE_CLASSES + FLUID_BUFFER_CLASSES)
    for cls in {r["cls"] for r in rows}:
        assert sum(1 for r in rows if r["cls"] == cls) == counts[cls], cls


def test_a_splitter_is_not_storage_even_though_it_owns_a_storage_inventory(projection):
    """The trap this key's class list exists to avoid, stated as a test.

    Every splitter and merger in the world owns a component literally named
    ``StorageInventory``, holding the one to three items physically inside the junction. A key
    built by matching that component name would report 848 more "containers" than exist, draw
    every one of them a second time over the belt layer that already has them, and count items
    in transit as stock.
    """
    attached = {r["instance"] for r in projection["attachments"]}
    stored = {r["instance"] for r in projection["storage"]}
    assert attached and stored
    assert not attached & stored
    assert len(stored) * 5 < len(attached), "the splitters outnumber the containers five to one"


def test_a_container_row_is_a_placement_and_its_contents(projection):
    """What a container IS, is where it stands and what is in it -- and nothing borrowed.

    Same posture as the attachment row next door: no recipe and no clock, because a container
    runs neither, and a null column claiming otherwise would be an invention.
    """
    solids = [r for r in projection["storage"] if "items" in r]
    assert len(solids) == 146
    for r in solids:
        assert set(r) == {"cls", "instance", "pos", "yaw", "items", "slots"}
        assert len(r["pos"]) == 3
        assert -180.0 <= r["yaw"] <= 180.0
        assert r["slots"] > 0, "a container with no slots at all is not a container"
        for item, amount in r["items"]:
            assert item.startswith("Desc_")
            assert isinstance(amount, (int, float)) and amount > 0
        # Biggest first, so a popup showing the top few shows the few worth showing.
        assert [n for _i, n in r["items"]] == sorted((n for _i, n in r["items"]), reverse=True)
    assert sum(1 for r in solids if r["items"]) == 125, "the ones the player has actually filled"


def test_a_containers_contents_are_its_own_and_they_add_up(projection):
    """The join, checked against a total the projection reached a different way.

    ``inventories["storage"]`` has summed these same stacks since schema 11 -- by bucketing
    component NAMES, with no idea which actor owns which -- so it is an independent count of
    the same items, and a mis-joined or double-counted inventory would not match it.

    **It used to match with a remainder, and the remainder was the bug.** Schema 15 recorded
    it as a finding and tolerated it: the bucket rule matched three substrings where the row
    join uses STORAGE_CLASSES, so the 6 Personal Storage Boxes, the HUB's built-in container
    and the Blueprint Designer's were in one and not the other, and 10,667 units over 31 item
    classes were bucketed as machine buffers -- material ``stock()`` will not spend. Schema 16
    made the bucket the same membership test, and there is nothing left over: the two now
    agree item for item, which is the strongest form this cross-check can take.

    A freight wagon would still be a legitimate remainder in ``bucketed`` -- it is stock and it
    is not a container, so it has no row here -- and no save in the reference directory has
    one. Asserted as an exact match rather than as an inequality because that is what this
    world says; a wagon arriving here should be a failure somebody reads, not a silent pass.
    """
    per_row: dict[str, float] = {}
    for r in projection["storage"]:
        for item, amount in r.get("items", ()):
            per_row[item] = per_row.get(item, 0) + amount
    bucketed = projection["inventories"]["storage"]
    assert per_row and bucketed
    assert per_row == bucketed, "the two counts of the same stacks disagree"
    # And the eight containers the old rule could not see are really in there, so that this
    # is a statement about the fix rather than about two empty sums.
    named = ("StorageContainer", "CentralStorage", "FreightWagon")
    outside: dict[str, float] = {}
    for r in projection["storage"]:
        if any(tag in r["cls"] for tag in named):
            continue
        for item, amount in r.get("items", ()):
            outside[item] = outside.get(item, 0) + amount
    assert len(outside) == 31 and sum(outside.values()) == 10667


def test_a_fluid_buffer_takes_its_fluid_from_the_network_that_claims_it(projection):
    """A buffer stores a level and never names the fluid; the plumbing around it does.

    Exactly the join a pipe's ``fluid`` uses, and for the same reason -- it is the game's own
    ``FGPipeNetwork`` answer rather than an inference from what the buffer is plugged into.
    """
    buffers = [r for r in projection["storage"] if "stored_m3" in r]
    assert len(buffers) == 5
    fluids = {r["fluid"] for r in projection["pipe_networks"]}
    for r in buffers:
        assert set(r) == {"cls", "instance", "pos", "yaw", "fluid", "stored_m3"}
        assert r["fluid"] in fluids, r["fluid"]
        assert "items" not in r and "slots" not in r
        # Cubic metres, and inside the capacity the dump states for the class -- 400 on a
        # Fluid Buffer, 2,400 on an Industrial one. A litres reading would be 1000x over.
        cap = 2400.0 if r["cls"] == "Build_IndustrialTank_C" else 400.0
        assert 0.0 <= r["stored_m3"] <= cap, (r["cls"], r["stored_m3"])
    assert any(r["stored_m3"] > 300 for r in buffers), "one of them is nearly full"


def test_storage_is_ordered_so_two_saves_of_one_world_can_be_diffed(projection):
    """Stable between runs, which is the projection's posture wherever it emits a list."""
    rows = projection["storage"]
    assert [(r["cls"], r["instance"]) for r in rows] == sorted(
        (r["cls"], r["instance"]) for r in rows
    )


def test_a_storage_actor_with_no_inventory_component_is_still_a_container():
    """An empty box is a box. The join is a lookup, and a miss has to mean "nothing in it".



    A container the player has never touched may have no ``StorageInventory`` written at all --

    UE omits a SaveGame property still at its default -- and dropping the row would take the

    box off the map for the crime of being empty.

    """

    rows = inventories.storage(
        [("Build_StorageContainerMk1_C", "x.Build_StorageContainerMk1_C_1", [1, 2, 3], 90.0, None)],
        {},
        [],
    )

    assert rows == [
        {
            "cls": "Build_StorageContainerMk1_C",
            "instance": "x.Build_StorageContainerMk1_C_1",
            "pos": [1, 2, 3],
            "yaw": 90.0,
            "items": [],
            "slots": 0,
        }
    ]


def test_a_buffer_no_network_claims_keeps_its_level_and_loses_its_fluid():
    """Drawn with contents unknown beats not drawn -- the refusal ``routes.pipes`` already makes."""

    rows = inventories.storage(
        [("Build_PipeStorageTank_C", "x.Build_PipeStorageTank_C_1", [0, 0, 0], 0.0, 12.5)], {}, []
    )

    assert rows[0]["fluid"] is None

    assert rows[0]["stored_m3"] == 12.5

    # And a level that will not read as a number is null rather than zero: an unreadable

    # buffer is not an empty one.

    unreadable = inventories.storage(
        [("Build_IndustrialTank_C", "i", [0, 0, 0], 0.0, "brimming")], {}, []
    )

    assert unreadable[0]["stored_m3"] is None
