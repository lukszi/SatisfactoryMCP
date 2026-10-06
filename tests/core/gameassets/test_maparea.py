"""The map-area reader: the arithmetic that resolves an index, and what the game ships.

Two halves, deliberately.

The first runs anywhere. ``import_export_hash`` is four lines of packing arithmetic and it is
the whole reason this reader can tell ``Area_crater_1`` from ``Area_crater_2``; ``_display_name``
walks an ``FText`` whose history is a string-table reference. Both are exercised against
hand-built payloads, because what is under test is the layout, not anybody's container.

The second needs the installed game and is marked ``integration``. It pins what build 495413
actually ships -- 37 palette indices, 35 area assets under 18 package names, 19 distinct
localisation keys -- because those counts are the assumptions every consumer of
``data/region_names.json`` rests on, and a re-cook that changes one of them must be loud.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from satisfactory_mcp.core.gameassets import maparea
from satisfactory_mcp.core.gameassets.maparea import Area, MapAreas
from satisfactory_mcp.core.gameassets.packages import PackageView

# --------------------------------------------------------------------------------------
# The arithmetic, with no container in sight.
# --------------------------------------------------------------------------------------


class _Pkg:
    """``Package`` as far as ``import_export_hash`` is concerned: two lists."""

    def __init__(self, imports, hashes):
        self.imports = list(imports)
        self.imported_public_export_hashes = list(hashes)


class _View(PackageView):
    """The real method under test, over a stub package: no bytes, no container, no scripts."""

    def __init__(self, imports, hashes):
        self.pkg = _Pkg(imports, hashes)


def _package_import(package_slot: int, hash_slot: int) -> int:
    """An ``FPackageObjectIndex`` of kind ``PackageImport``, the way the engine packs one."""
    return (2 << 62) | (package_slot << 32) | hash_slot


def _index(value: int) -> bytes:
    """An ``FPackageIndex`` pointing OUT of the package: negative, one-based."""
    return (-value - 1).to_bytes(4, "little", signed=True)


def test_an_import_resolves_to_the_hash_its_low_bits_index():
    """The low 32 bits are an INDEX into a table, not the hash itself.

    This is the fact the whole reader turns on. Read as a hash directly they come out
    0, 1, 2, ... -- sequential, plausible, and matching no export anywhere -- so a resolution
    built on them would fail to find every area and a reader that fell back to the package
    name would silently pick the wrong one of a same-named pair.
    """
    view = _View([_package_import(7, 2), _package_import(3, 0)], [0xAA, 0xBB, 0xCC])

    assert view.import_export_hash(_index(0)) == 0xCC
    assert view.import_export_hash(_index(1)) == 0xAA


def test_a_reference_that_is_not_a_package_import_resolves_to_nothing():
    """Script imports and nulls have no public export hash, and must not be read as one.

    ``ScriptImport`` packs a 62-bit type hash whose low bits are meaningful and are not an
    index; taking them as one indexes into the hash table with a number that means something
    else. ``None`` is the answer, and the caller's job is to know an unnamed index is a fact.
    """
    view = _View([(1 << 62) | 5, (3 << 62)], [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])

    assert view.import_export_hash(_index(0)) is None
    assert view.import_export_hash(_index(1)) is None


def test_an_out_of_range_reference_answers_none_rather_than_raising():
    """Every bound is checked, because this walks bytes from a file that may have changed."""
    view = _View([_package_import(0, 9)], [0xAA])

    assert view.import_export_hash(_index(0)) is None  # hash slot past the table
    assert view.import_export_hash(_index(4)) is None  # import slot past the map
    assert view.import_export_hash(b"\x01\x00\x00\x00") is None  # an EXPORT, not an import
    assert view.import_export_hash(b"\x00\x00") is None  # not four bytes at all


class _NamePkg:
    def __init__(self, names):
        self.names = list(names)

    def name(self, index, number):
        base = self.names[index]
        return base if number == 0 else f"{base}_{number - 1}"


class _NameView:
    """``PackageView`` as far as ``_display_name`` is concerned: exports and their props."""

    def __init__(self, names, props):
        self.pkg = _NamePkg(names)
        self.exports = [{"slot": slot} for slot in range(len(props))]
        self._props = props

    def props(self, slot):
        return self._props[slot]


def _ftext_string_table(table_index: int, key: str) -> bytes:
    """``mDisplayName`` as the area assets serialise it: flags, history 11, FName, FString."""
    encoded = key.encode("utf-8") + b"\x00"
    return (
        (0).to_bytes(4, "little")
        + bytes([maparea._TEXT_HISTORY_STRING_TABLE])
        + table_index.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + len(encoded).to_bytes(4, "little", signed=True)
        + encoded
    )


def test_a_display_name_is_read_as_its_table_and_key():
    view = _NameView(
        ["World_Data"],
        [
            {"mZoneType": b"\xff\xff\xff\xff"},
            {"mDisplayName": _ftext_string_table(0, "Locations/Swamp")},
        ],
    )

    assert maparea._display_name(view) == ("Locations/Swamp", "World_Data")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00\x00\x00\x00\x00",  # history 0: a plain literal, not a table reference
        b"\x00\x00\x00\x00\x0b\x00\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\xff",  # length lies
    ],
    ids=["empty", "wrong-history", "length-past-the-end"],
)
def test_a_display_name_this_reader_cannot_read_is_nothing_rather_than_a_guess(payload):
    """An invented label in a table whose whole point is first-party labels is the worst
    outcome available, so anything unreadable comes back as ``None``."""
    view = _NameView(["World_Data"], [{"mDisplayName": payload}])

    assert maparea._display_name(view) == (None, None)


def _areas(*specs) -> MapAreas:
    return MapAreas(
        width=2,
        texels=bytes(range(len(specs))),
        areas=tuple(
            None
            if spec is None
            else Area(asset=spec[0], stem=spec[1], key=spec[2], string_table="World_Data")
            for spec in specs
        ),
        boxes=tuple((0, 0, 1, 1) for _ in specs),
        palette=tuple((0, 0, 0, 255) for _ in specs),
    )


def test_named_separates_a_region_from_no_mans_land_and_from_nothing():
    """Three outcomes, not two: a named area, the game's own name for unnamed ground, and an
    index that references no object at all. Collapsing the last two would lose the fact that
    this build has one of each."""
    areas = _areas(
        ("Area_Swamp_1", "Area_Swamp", "Locations/Swamp"),
        ("Area_NoMansLand_1", maparea.NO_MANS_LAND, "Locations/NoMansLand"),
        None,
    )

    assert areas.named(0)
    assert not areas.named(1)
    assert not areas.named(2)
    assert areas.assets == ("Area_NoMansLand_1", "Area_Swamp_1")
    assert areas.keys == ("Locations/NoMansLand", "Locations/Swamp")


# --------------------------------------------------------------------------------------
# What build 495413 actually ships.
# --------------------------------------------------------------------------------------

GAME = Path("G:/SteamLibrary/steamapps/common/Satisfactory")
PAKS = GAME / "FactoryGame" / "Content" / "Paks"


@pytest.fixture(scope="module")
def installed():
    """The map areas as read out of the reader's own installed container."""
    if not (PAKS / "FactoryGame-Windows.utoc").exists():
        pytest.skip(f"needs the installed game at {GAME}")
    from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
    from satisfactory_mcp.core.gameassets.packages import ScriptObjects

    store = IoStore(PAKS, "FactoryGame-Windows", oodle_decompress)
    return maparea.read_map_areas(store, ScriptObjects(PAKS, oodle_decompress))


@pytest.mark.integration
def test_the_raster_is_the_square_this_reader_knows(installed):
    assert installed.width == maparea.MAP_AREA_TEXELS
    assert len(installed.texels) == installed.width**2
    assert len(installed.areas) == len(installed.palette) == 37


@pytest.mark.integration
def test_every_palette_index_resolves_to_one_asset_or_says_it_names_nothing(installed):
    """The resolution is total: no index is left as "some Area_RedJungle".

    One index in this build names no object -- a single texel -- and it is ``None`` here
    rather than absent, because "the game left this unnamed" is a different fact from "this
    reader could not tell".
    """
    unresolved = [i for i, area in enumerate(installed.areas) if area is None]

    assert unresolved == [29]
    assert installed.texels.count(29) == 1
    assert all(area.asset for area in installed.areas if area is not None)


@pytest.mark.integration
def test_the_asset_names_are_not_unique_and_the_display_keys_are_what_differ(installed):
    """The measurement the whole resolution exists for.

    ``Area_crater_1`` and ``Area_crater_2`` share a package name and name two different
    regions; so do the two ``Area_RedJungle`` assets. Resolving by package name would answer
    one of each pair at random, and the region table would carry whichever it got.
    """
    by_stem: dict[str, set[str]] = {}
    for area in installed.areas:
        if area is not None:
            by_stem.setdefault(area.stem, set()).add(area.key or "")

    assert by_stem["Area_crater"] == {"Locations/BlueCrater", "Locations/CraterLakes"}
    assert by_stem["Area_RedJungle"] == {"Locations/RedJungle", "Locations/JungleSpires"}
    # ...and the converse: two differently-named assets that are one named region.
    assert by_stem["Area_Savanna"] == {"Locations/RockyDesert"}


@pytest.mark.integration
def test_every_area_states_its_name_through_the_same_string_table(installed):
    """One table, ``World_Data``, or the naming is coming from somewhere unaccounted for."""
    assert {area.string_table for area in installed.areas if area is not None} == {"World_Data"}
    assert all(
        (area.key or "").startswith("Locations/") for area in installed.areas if area is not None
    )


@pytest.mark.integration
def test_the_keys_are_the_nineteen_this_build_ships(installed):
    """Pinned by name, because the region table's display names are built from exactly these.

    A key that appears here and not in ``gen_region_names.DISPLAY_NAMES`` is a region the
    generator would refuse to name, which is the failure this catches one layer earlier.
    """
    assert installed.keys == (
        "Locations/AbyssCliffs",
        "Locations/BlueCrater",
        "Locations/CraterLakes",
        "Locations/DesertCanyon",
        "Locations/DuneDesert",
        "Locations/GrassFields",
        "Locations/JungleSpires",
        "Locations/LakeForest",
        "Locations/MazeCanyon",
        "Locations/NoMansLand",
        "Locations/NorthernForest",
        "Locations/RedBambooFields",
        "Locations/RedJungle",
        "Locations/RockyDesert",
        "Locations/SouthernForest",
        "Locations/SpireCoast",
        "Locations/Swamp",
        "Locations/TitanForest",
        "Locations/WesternDuneForest",
    )


@pytest.mark.integration
def test_no_mans_land_is_most_of_the_texture_and_is_a_named_object(installed):
    """43% of the raster, which is the ocean and the outer coast, and the game has an object
    for it. That is what makes "unnamed ground" labellable rather than blank."""
    unnamed = sum(
        installed.texels.count(i)
        for i, area in enumerate(installed.areas)
        if not installed.named(i)
    )

    assert 0.40 < unnamed / len(installed.texels) < 0.46
    assert maparea.NO_MANS_LAND in {a.stem for a in installed.areas if a is not None}
