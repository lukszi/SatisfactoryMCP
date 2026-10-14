"""``component_foliage_type``: a foliage component's type, read off its actor's map."""

from __future__ import annotations

import struct
from types import SimpleNamespace

from mapgen.gamedata.level.foliage_types import component_foliage_type

TYPES = {-3: "/Game/F/SmallShell_FoliageType", -4: "/Game/F/FT_SmallShell_Desert"}


def _view(tail: bytes) -> SimpleNamespace:
    """An actor at slot 0 whose body is ``tail``, owning components at slots 1 to 3."""
    exports = [{"slot": 0}, {"slot": 1}, {"slot": 2}, {"slot": 3}]
    return SimpleNamespace(
        exports=exports,
        outer_of={0: None, 1: 0, 2: 0, 3: 0},
        pkg=SimpleNamespace(body=lambda export: tail if export["slot"] == 0 else b""),
        import_path=lambda payload: TYPES.get(struct.unpack("<i", payload)[0]),
    )


def _entry(type_index: int, component_slot: int) -> bytes:
    """One ``FoliageInfos`` entry's head: the type, the implementation kind, the component."""
    return struct.pack("<ibi", type_index, 1, component_slot + 1)


def test_each_component_reads_the_type_whose_entry_names_it():
    # The component list before the map names slot 2 with no type in front of it.
    tail = b"\x07" * 9 + struct.pack("<ii", 2, 3) + _entry(-3, 1) + _entry(-4, 2) + b"\0" * 7
    view = _view(tail)
    assert component_foliage_type(view, 1) == TYPES[-3]  # type: ignore[arg-type]
    assert component_foliage_type(view, 2) == TYPES[-4]  # type: ignore[arg-type]
    assert component_foliage_type(view, 3) is None  # type: ignore[arg-type]
