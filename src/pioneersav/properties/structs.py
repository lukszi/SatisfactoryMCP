"""Structs that serialise themselves as raw numbers, and the table that says which ones do."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..errors import expect
from ..references import read_reference
from ..versions import FIRST_MODERN_BODY

if TYPE_CHECKING:
    from ..values import SaveValue
    from .decoder import PropertyDecoder

__all__ = [
    "NATIVE_STRUCTS",
    "OPAQUE_STRUCTS",
    "read_box",
    "read_client_identity_info",
    "read_fluid_box",
    "read_guid",
    "read_int_vector",
    "read_inventory_item",
    "read_inventory_item_legacy",
    "read_inventory_item_modern",
    "read_linear_color",
    "read_quat",
    "read_vector",
]


def read_vector(decoder: PropertyDecoder) -> list[float]:
    """FVector: three doubles on a UE5 save, three floats on a UE4 one. The width follows the
    save, not the object: a UE5 game writes doubles even into a version-36 object."""
    r = decoder.r
    if decoder.ue4_save:
        return [r.f32(), r.f32(), r.f32()]
    return [r.f64(), r.f64(), r.f64()]


def read_quat(decoder: PropertyDecoder) -> list[float]:
    """FQuat: four doubles on a UE5 save and four floats on a UE4 one, as ``read_vector``."""
    r = decoder.r
    if decoder.ue4_save:
        return [r.f32(), r.f32(), r.f32(), r.f32()]
    return [r.f64(), r.f64(), r.f64(), r.f64()]


def read_box(decoder: PropertyDecoder) -> list[float | bool]:
    """FBox: min, max and a validity byte, seven entries at ``read_vector``'s width."""
    return [*read_vector(decoder), *read_vector(decoder), decoder.r.i8() != 0]


def read_linear_color(decoder: PropertyDecoder) -> list[float]:
    """FLinearColor stayed four *floats* through the UE5 upgrade; FVector did not."""
    r = decoder.r
    return [r.f32(), r.f32(), r.f32(), r.f32()]


def read_guid(decoder: PropertyDecoder) -> list[int]:
    """16 bytes, reported as two uint64s."""
    r = decoder.r
    return [r.u64(), r.u64()]


def read_int_vector(decoder: PropertyDecoder) -> list[int]:
    """FIntVector: a world-partition cell coordinate, e.g. the foliage grid's ``[-7,-25,-1]``."""
    r = decoder.r
    return [r.i32(), r.i32(), r.i32()]


def read_fluid_box(decoder: PropertyDecoder) -> float:
    """FFluidBox: one float, the fluid currently in a pipe segment."""
    return decoder.r.f32()


def read_client_identity_info(decoder: PropertyDecoder) -> list[SaveValue]:
    """``[offlineId, [[platform, idBytes], ...]]``: who owns a player state, one length-prefixed
    account id per linked platform, left as bytes because nothing reads it."""
    r = decoder.r
    offline_id = r.string()
    count = r.i32()
    expect(0 <= count <= 64, r.pos - 4, f"a client identity claims {count} platforms")
    platforms: list[SaveValue] = []
    for _ in range(count):
        platform = r.i8()
        platforms.append([platform, r.bytes(r.i32())])
    return [offline_id, platforms]


def read_inventory_item_modern(decoder: PropertyDecoder) -> list[SaveValue]:
    """``FInventoryItem`` as an object reference, a has-state int32, and the state if there is one.

    State is the state class plus a sized, nested property list (a weapon's ammo counter), so
    it is read rather than skipped. Element 0 is the bare path string, not an
    ``ObjectReference``, because ``_accumulate_inventory``'s ``ref_class`` resolves only that.
    """
    r = decoder.r
    item_class = read_reference(r)
    has_state = r.i32()
    if not has_state:
        return [item_class.path_name, None]
    state_class = read_reference(r)
    size = r.i32()
    expect(
        0 <= size <= r.remaining,
        r.pos - 4,
        f"an item state claims {size} bytes with {r.remaining} left",
    )
    values, types = decoder.property_list(r.pos + size)
    return [item_class.path_name, [state_class.path_name, values, types]]


def read_inventory_item_legacy(decoder: PropertyDecoder) -> list[SaveValue]:
    """``FInventoryItem`` as two bare object references: the descriptor, then the ``Equip_*_C``
    actor this item instance is, or two empty strings."""
    r = decoder.r
    item_class = read_reference(r)
    return [item_class.path_name, read_reference(r).path_name or None]


def read_inventory_item(decoder: PropertyDecoder) -> list[SaveValue]:
    """``FInventoryItem`` where no declared size can referee the layout, so the version guesses.

    Only a bare container element lands here; ``read_struct`` lets the declared size choose
    everywhere else, because the layout follows no version in the file (savparse-notes.md).
    """
    if decoder.version < FIRST_MODERN_BODY:
        return read_inventory_item_legacy(decoder)
    return read_inventory_item_modern(decoder)


#: Self-serialising identity handles kept as raw bytes without a warning, so that they do not
#: bury a real one: which player placed a buildable, and an account id.
OPAQUE_STRUCTS = frozenset({"PlayerInfoHandle", "UniqueNetIdRepl"})

#: Structs whose payload is raw numbers rather than a nested property list: the only authority
#: on how a struct serialises itself. Add nothing on the strength of its name --
#: ``Vector_NetQuantize`` is a tagged property list.
NATIVE_STRUCTS: dict[str, Callable[[PropertyDecoder], SaveValue]] = {
    "Vector": read_vector,
    "Quat": read_quat,
    "Box": read_box,
    "LinearColor": read_linear_color,
    "Guid": read_guid,
    "IntVector": read_int_vector,
    "FluidBox": read_fluid_box,
    "ClientIdentityInfo": read_client_identity_info,
    "InventoryItem": read_inventory_item,
}
