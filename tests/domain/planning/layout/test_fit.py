"""Fitting an abstract layout against a factory that already exists.

build_layout has no coordinates on purpose, so "scope a layout to a factory" cannot mean
placing blocks. It means answering the two questions the abstract layout leaves open:
does it fit on the platform already poured, and what of it already stands there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from satisfactory_mcp.domain.factories.structure import build_structures
from satisfactory_mcp.domain.planning.fit import assess_fit

pytestmark = pytest.mark.integration


@dataclass
class _Block:
    name: str
    building_id: str
    recipe: str | None
    machines: int


@dataclass
class _Layout:
    foundations: int
    blocks: list = field(default_factory=list)


SMELTER = "Build_SmelterMk1_C_1"
CONSTRUCTOR = "Build_ConstructorMk1_C_2"


def _projection(tiles: int = 9) -> dict:
    return {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [[0, (i % 3) * 800, (i // 3) * 800, 0] for i in range(tiles)],
        },
        "machines": [
            {
                "instance": f"L:P.{SMELTER}",
                "cls": "Build_SmelterMk1_C",
                "recipe": "Recipe_IngotIron_C",
                "pos": [0, 0, 100],
            },
            {
                "instance": f"L:P.{CONSTRUCTOR}",
                "cls": "Build_ConstructorMk1_C",
                "recipe": "Recipe_IronRod_C",
                "pos": [800, 0, 100],
            },
        ],
        "extractors": [],
        "generators": [],
    }


def _fit(layout, projection=None, machines=(SMELTER, CONSTRUCTOR)):
    projection = projection or _projection()
    return assess_fit("probe", list(machines), layout, build_structures(projection), projection)


def test_a_layout_within_the_existing_platform_fits():
    report = _fit(_Layout(foundations=4))
    assert report.tiles == 9
    assert report.fits and report.shortfall == 0
    assert "fits on the existing platform" in report.headline()


def test_a_layout_larger_than_the_platform_reports_the_shortfall():
    """Not a failure: floors stack, so the honest answer is how many tiles short."""
    report = _fit(_Layout(foundations=30))
    assert not report.fits
    assert report.shortfall == 21
    assert "21 more tiles" in report.headline()


def test_a_block_already_standing_is_not_work():
    """Matching by (building, recipe) turns "build 2 blocks" into "build 1"."""
    layout = _Layout(
        foundations=4,
        blocks=[
            _Block("Iron Ingot", "Build_SmelterMk1_C", "Recipe_IngotIron_C", 1),
            _Block("Iron Plate", "Build_ConstructorMk1_C", "Recipe_IronPlate_C", 4),
        ],
    )
    report = _fit(layout)
    assert report.standing == ["Iron Ingot"]
    assert report.to_build == ["Iron Plate"]
    assert report.machines_standing == 1
    assert report.machines_to_build == 4


def test_a_block_needing_more_machines_than_stand_there_is_still_work():
    """One smelter does not satisfy a block of six."""
    layout = _Layout(
        foundations=4,
        blocks=[_Block("Iron Ingot", "Build_SmelterMk1_C", "Recipe_IngotIron_C", 6)],
    )
    report = _fit(layout)
    assert report.to_build == ["Iron Ingot"]
    assert report.machines_standing == 0


def test_two_blocks_cannot_both_claim_the_same_standing_machine():
    """The count is consumed as it matches, or a single smelter would satisfy every
    Iron Ingot block in the layout."""
    layout = _Layout(
        foundations=4,
        blocks=[
            _Block("Iron Ingot (1/2)", "Build_SmelterMk1_C", "Recipe_IngotIron_C", 1),
            _Block("Iron Ingot (2/2)", "Build_SmelterMk1_C", "Recipe_IngotIron_C", 1),
        ],
    )
    report = _fit(layout)
    assert report.standing == ["Iron Ingot (1/2)"]
    assert report.to_build == ["Iron Ingot (2/2)"]


def test_a_ground_built_factory_has_nothing_to_fit_into():
    """Two of the player's factories are built on bare ground. Reporting 0 tiles as
    "does not fit" would be true but useless; say there is no platform."""
    projection = _projection(tiles=0)
    projection["structures"]["instances"] = []
    report = _fit(_Layout(foundations=10), projection)
    assert report.tiles == 0
    assert "no foundations at all" in report.headline()


def test_machines_off_the_slab_are_flagged_as_understating_the_footprint():
    projection = _projection()
    projection["machines"].append(
        {
            "instance": "L:P.Build_SmelterMk1_C_9",
            "cls": "Build_SmelterMk1_C",
            "recipe": "Recipe_IngotIron_C",
            "pos": [900_000, 900_000, 0],
        }
    )
    report = _fit(
        _Layout(foundations=4), projection, machines=(SMELTER, CONSTRUCTOR, "Build_SmelterMk1_C_9")
    )
    assert any("no foundation" in n for n in report.notes)


def test_standing_is_reported_as_present_never_as_correct():
    layout = _Layout(
        foundations=4,
        blocks=[_Block("Iron Ingot", "Build_SmelterMk1_C", "Recipe_IngotIron_C", 1)],
    )
    report = _fit(layout)
    assert any("present" in n and "not correct" in n for n in report.notes)
