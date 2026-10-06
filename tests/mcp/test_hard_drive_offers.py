"""The pending-drive list: what each option is actually offering.

`advise_hard_drive_pick` ranks ONE drive by counterfactual LP; this is the list you read
first, and it held every option's granted recipes and used them only to decide whether to
append "(nothing new)". Choosing between two drives is choosing between those lists.

Judged against the committed projection, which carries 25 unclaimed drives.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.interfaces.mcp.tools import harddrives as harddrive_tools

pytestmark = pytest.mark.integration


@pytest.fixture
def tools(state, monkeypatch):
    monkeypatch.setattr(harddrive_tools, "_state", lambda save=None, world=None, as_of=None: state)
    return harddrive_tools


def _row(out: str, drive_id: int) -> str:
    return next(line for line in out.splitlines() if line.startswith(f"{drive_id}\t"))


def test_an_option_says_what_its_recipes_make_and_where(tools):
    """The name of an alternate schematic IS the name of the recipe it grants, so listing
    the recipes by name would have printed the option twice. What it makes, at what rate,
    in which machine, is the part that decides between two drives -- and the part that
    used to cost a recipe_detail call per option."""
    row = _row(tools.list_pending_hard_drive_choices(), 5)
    assert "Alternate: Heavy Flexible Frame (3.75 Heavy Modular Frame @Manufacturer)" in row
    assert "Alternate: Molded Steel Pipe (50 Steel Pipe @Foundry)" in row


def test_an_option_that_grants_no_recipe_still_says_what_it_is(tools):
    """The two non-recipe cases the old code could already tell apart, kept: inventory
    slots are a real reward, and an alternate this world already has is not."""
    out = tools.list_pending_hard_drive_choices()
    assert "Inflated Pocket Dimension (+6 inventory slots)" in _row(out, 22)
    assert "Alternate: Charcoal (nothing new)" in _row(out, 34)


def test_a_multi_recipe_option_lists_every_one(tools):
    """Quartz Purification grants two recipes in two machines, which is exactly the case a
    single-line summary would have hidden."""
    row = _row(tools.list_pending_hard_drive_choices(), 23)
    assert "@Refinery" in row and "@Blender" in row


def test_both_hard_drive_tools_name_the_drive_last_spent(tools):
    """mLastUsedHardDriveID had no consumer. It is continuity, not a selector: the drive
    it names is settled and gone from the pending list, and saying so is what stops a
    resuming reader from looking for it there."""
    assert "drive 36 was the last one analysed" in tools.list_pending_hard_drive_choices()
    assert "drive 36 was the last one analysed" in tools.advise_hard_drive_pick(hard_drive_id=34)


def test_the_list_is_bounded_and_says_when_it_cut(tools):
    """25 drives at two options each is most of this response's character budget, so the
    table takes a limit like every other one -- and says how many it did not show."""
    out = tools.list_pending_hard_drive_choices(limit=5)
    rows = [line for line in out.splitlines() if "\t" in line][1:]
    assert len(rows) == 5
    assert "# 25 match(es), showing 5 from offset 0" in out
