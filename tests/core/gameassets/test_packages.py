"""The two ways a cooked cross-package reference used to resolve to nothing.

Both defects were silent -- an unresolvable reference is a ``None``, and every caller
treats ``None`` as "this world does not contain that", which is exactly what a missing
asset looks like. Six of the largest meshes in the world were absent from the terrain
field for a year because of it, and nothing anywhere reported a failure.

So they are pinned here, on **synthesised bytes** rather than on the installed game: a
test that needs a 12 GB container is a test that runs on one machine, and what is under
test is a header layout and a lookup rule, neither of which needs a real asset to state.
The layouts below are the ones build 495413 writes, verified against it before being
typed here.
"""

from __future__ import annotations

import struct

import pytest

from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    Package,
    apply_fname_number,
    bulk_data_entries,
)


def name_batch(names: list[str]) -> bytes:
    """An ``FNameBatch`` over ASCII names: count, byte length, hash version, hashes,
    two-byte headers, then the strings. Only the shape matters; the hashes are not read."""
    strings = b"".join(n.encode("utf-8") for n in names)
    out = struct.pack("<II", len(names), len(strings))
    out += struct.pack("<Q", 0)  # hash version
    out += b"\0" * (8 * len(names))  # per-name hashes
    out += b"".join(struct.pack(">H", len(n)) for n in names)  # UTF-8, length in low bits
    return out + strings


def zen_package(imported: list[str], numbers: list[int] | None = None) -> bytes:
    """The smallest ``FZenPackageSummary`` this reader will walk, with an imported-package
    section at the end of the header: a name batch, then one uint32 FName number per name.
    """
    own_names = name_batch(["Package"])
    imported_at = 60 + len(own_names)
    section = name_batch(imported)
    if numbers is not None:
        section += struct.pack(f"<{len(numbers)}I", *numbers)
    header_size = imported_at + len(section)
    words = [0] * 15
    words[1] = header_size
    words[7] = words[8] = header_size  # import and export maps: empty, at the end
    words[12] = imported_at
    return struct.pack("<15I", *words) + own_names + section


# ------------------------------------------------------------------ the FName number


def test_fname_number_is_the_ue_spelling() -> None:
    assert apply_fname_number("Foo", 0) == "Foo"
    assert apply_fname_number("Foo", 4) == "Foo_3"


def test_imported_package_names_carry_their_fname_number() -> None:
    """``("SM_MERGED_BP_CaveFloor2", 4)`` is the package ``SM_MERGED_BP_CaveFloor2_3``.

    This is the whole of the second bug: the name batch holds strings and the numbers are
    a separate array right behind it, so a reader that stops at the batch spells every
    auto-numbered asset without its number and then cannot find it.
    """
    blob = zen_package(["/Game/A/SM_Plain", "/Game/A/SM_MERGED_BP_CaveFloor2"], [0, 4])
    assert Package(blob).imported_packages == [
        "/Game/A/SM_Plain",
        "/Game/A/SM_MERGED_BP_CaveFloor2_3",
    ]


def test_a_header_with_no_number_array_degrades_to_the_old_answer() -> None:
    """An older cook, or a layout change, must read as it always did rather than wrongly.

    The array is identified by position and length alone, so the one guard available is
    that there be room for it. Where there is not, every number is zero -- which is
    precisely the behaviour this replaced.
    """
    blob = zen_package(["/Game/A/SM_Plain", "/Game/A/SM_Other"], None)
    assert Package(blob).imported_packages == ["/Game/A/SM_Plain", "/Game/A/SM_Other"]


def test_a_truncated_imported_section_costs_the_names_and_not_the_package() -> None:
    """A header that stops inside the imported section loses the imports and nothing else.

    One package in 4,521 that will not parse must not cost the other 4,520, and a package
    whose imports are unreadable is still a package with exports.
    """
    blob = zen_package(["/Game/A/SM_Plain"], [0])
    imported_at = struct.unpack_from("<15I", blob, 0)[12]
    package = Package(blob[: imported_at + 8])
    assert package.imported_packages == []
    assert package.names == ["Package"]


# ------------------------------------------------------------------ the bulk data map


def bulk_map_blob(entries: list[tuple[int, int, int, int]], pad: int = 8) -> bytes:
    """A header fragment holding a ``BulkDataMap`` at position 16: the UE 5.4+ alignment
    pad, the int64 byte length, then one 32-byte entry per (offset, size, flags, cooked)."""
    packed = b"".join(
        struct.pack("<3QI", offset, 2**64 - 1, size, flags) + bytes([cooked, 0, 0, 0])
        for offset, size, flags, cooked in entries
    )
    return bytes(16) + struct.pack("<Q", pad) + bytes(pad) + struct.pack("<q", len(packed)) + packed


def test_the_bulk_data_map_reads_both_kinds_of_entry_the_icons_actually_meet() -> None:
    """One inline level (flags 0x48) and one ``.ubulk`` level (0x00010501), side by side.

    That is what every ordinary icon's map looks like on build 495413 -- the big mips
    streamed, the small ones inline -- and the flags word surviving verbatim is what lets
    ``textures.INLINE_BULK_FLAG`` tell the two apart downstream. The alignment pad in
    front of the map is exercised because it is the part a UE version bump moved last.
    """
    blob = bulk_map_blob([(297, 65_536, 0x48, 0), (0, 16_384, 0x00010501, 1)])
    out = bulk_data_entries(blob, 16, len(blob))

    assert [(e["index"], e["offset"], e["size"]) for e in out] == [
        (0, 297, 65_536),
        (1, 0, 16_384),
    ]
    assert [e["flags"] for e in out] == [0x48, 0x00010501]
    assert [e["cooked_index"] for e in out] == [0, 1]


def test_a_bulk_data_map_that_does_not_fit_its_header_is_a_valueerror() -> None:
    """The bound is ``first_section``: a misread length must fail, not walk the imports.

    And a blob too short for the length words at all is the same ``ValueError`` rather
    than a raw ``struct.error``, so a caller has one exception to catch -- ``staticmesh``
    turns it into its own ``ParseError``, the icon generator into an ``unresolved`` line.
    """
    blob = bulk_map_blob([(297, 65_536, 0x48, 0)])
    with pytest.raises(ValueError):
        bulk_data_entries(blob, 16, len(blob) - 1)
    with pytest.raises(ValueError):
        bulk_data_entries(b"", 0, 0)


# ------------------------------------------------------------------ the namesake guard


class FakeStore:
    def __init__(self, paths: list[str]) -> None:
        self.by_path = {path: index for index, path in enumerate(paths)}


GAME = "../../../FactoryGame/Content/FactoryGame"
ENGINE = "../../../Engine/Content"


@pytest.fixture
def index() -> AssetIndex:
    return AssetIndex(
        FakeStore(
            [
                f"{GAME}/Equipment/MedKit/Mesh/SM_Medkit_01.uasset",
                f"{GAME}/World/Environment/Tropical_Forest/Trees/Trunks/trunks7.uasset",
                f"{GAME}/World/Environment/Rock/Mesh/CliffPillar_01.uasset",
                f"{GAME}/World/Environment/Caves/Mesh/CliffPillar_01.uasset",
                f"{GAME}/World/Environment/Rock/Mesh/CliffPillar_01.ubulk",
                f"{ENGINE}/BasicShapes/Sphere.uasset",
                f"{ENGINE}/BasicShapes/Plane.uasset",
            ]
        )
    )


def test_an_engine_path_resolves(index: AssetIndex) -> None:
    """An ``/Engine/`` reference carries no ``/Game/``, so splitting on ``/Game/`` handed
    the namesake guard the whole path and it could never match."""
    assert index.path_for("/Engine/BasicShapes/Sphere") == f"{ENGINE}/BasicShapes/Sphere.uasset"
    assert index.path_for("/Engine/BasicShapes/Plane") == f"{ENGINE}/BasicShapes/Plane.uasset"


def test_the_container_may_spell_a_directory_in_another_case(index: AssetIndex) -> None:
    assert (
        index.path_for("/Game/FactoryGame/Equipment/Medkit/Mesh/SM_Medkit_01")
        == f"{GAME}/Equipment/MedKit/Mesh/SM_Medkit_01.uasset"
    )
    assert (
        index.path_for("/Game/FactoryGame/World/Environment/Tropical_Forest/trees/Trunks/trunks7")
        == f"{GAME}/World/Environment/Tropical_Forest/Trees/Trunks/trunks7.uasset"
    )


def test_the_namesake_guard_still_chooses_by_directory(index: AssetIndex) -> None:
    """Two packages share the leaf ``CliffPillar_01``. The directory picks between them --
    which the first version could not do at all, because it kept only the first path."""
    assert (
        index.path_for("/Game/FactoryGame/World/Environment/Caves/Mesh/CliffPillar_01")
        == f"{GAME}/World/Environment/Caves/Mesh/CliffPillar_01.uasset"
    )
    assert (
        index.path_for("/Game/FactoryGame/World/Environment/Rock/Mesh/CliffPillar_01")
        == f"{GAME}/World/Environment/Rock/Mesh/CliffPillar_01.uasset"
    )


def test_a_reference_to_nothing_still_resolves_to_nothing(index: AssetIndex) -> None:
    assert index.path_for("/Game/FactoryGame/World/Environment/Rock/Mesh/SM_NotHere") is None
    assert index.path_for("/Game/Somewhere/Else/CliffPillar_01") is None


def test_only_uasset_entries_are_indexed(index: AssetIndex) -> None:
    """The container holds a ``.ubulk`` beside every mesh and it is not the package."""
    got = index.path_for("/Game/FactoryGame/World/Environment/Rock/Mesh/CliffPillar_01")
    assert got is not None and got.endswith(".uasset")
