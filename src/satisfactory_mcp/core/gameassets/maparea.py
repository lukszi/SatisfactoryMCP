"""The game's own map areas: a 4096x4096 raster, and what each palette index is called.

The ``FGMapAreaTexture`` ``MapareatexturePersistentLevel`` carries ``mAreaData`` -- palette
indices, row 0 north, the game's own biome boundaries rasterised at 1.83 m -- and
``mColorToArea``, naming the ``UFGMapArea`` each index means and its texel bounding box. Nothing
here is artwork: the raster is indices, the areas are identifiers, and the names are the
game's own string-table keys.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .packages import PackageView, ScriptObjects, property_tags

__all__ = [
    "MAP_AREA_CLASS",
    "MAP_AREA_DIR",
    "MAP_AREA_PATH",
    "MAP_AREA_PROPS",
    "MAP_AREA_TEXELS",
    "NO_MANS_LAND",
    "Area",
    "MapAreas",
    "read_map_areas",
]

#: The texture, mount-relative, inside ``FactoryGame-Windows.utoc``.
MAP_AREA_PATH = (
    "../../../FactoryGame/Content/FactoryGame/Interface/UI/Minimap/"
    "MapAreaPersistenLevel/MapareatexturePersistentLevel.uasset"
)

#: And the directory the individual area assets share with it.
MAP_AREA_DIR = (
    "../../../FactoryGame/Content/FactoryGame/Interface/UI/Minimap/MapAreaPersistenLevel/"
)

#: What the texture has to be for this reader to know how to read it. The asset's own
#: ``mDataWidth`` is checked against this rather than trusted from it: a re-cooked texture at
#: another size is the game changing.
MAP_AREA_TEXELS = 4096

#: The class the properties hang off, and the properties themselves.
MAP_AREA_CLASS = "/Script/FactoryGame.FGMapAreaTexture"
MAP_AREA_PROPS = ("mAreaData", "mColorPalette", "mColorToArea", "mDataWidth")

#: One ``mColorToArea`` entry: the area object, then its extent in texels.
MAP_AREA_ENTRY_FIELDS = ("MapArea", "MinX", "MinY", "MaxX", "MaxY")

#: The asset stem the game uses for everything it does not name -- the outer coast and the
#: ocean past it. Not "unknown": there is an object for it and it has a display name of its
#: own, which is why callers can label it rather than blank it.
NO_MANS_LAND = "Area_NoMansLand"

#: ``FText`` history type ``StringTableEntry``: an ``FName`` table id then an ``FString`` key.
_TEXT_HISTORY_STRING_TABLE = 11

#: The property each area asset states its name in.
_DISPLAY_NAME = "mDisplayName"


@dataclass(frozen=True)
class Area:
    """One ``Area_*`` asset: which file it is, which package name it shares, what it is called.

    ``asset`` is the identity, being the only one of the three that is unique. ``stem`` is the
    package name shared with its siblings, which is what a colour table is keyed by. ``key`` is
    the game's own localisation key and the one a display name should be built from.
    """

    asset: str
    stem: str
    key: str | None
    string_table: str | None


@dataclass(frozen=True)
class MapAreas:
    """The decoded texture: indices, their areas, and the palette that was ignored."""

    width: int
    #: ``width * width`` palette indices, one byte each, row 0 north and column 0 west.
    texels: bytes
    #: Per palette index, the area it means -- ``None`` where the entry names no object.
    areas: tuple[Area | None, ...]
    #: Per palette index, its ``mColorToArea`` extent ``(min_x, min_y, max_x, max_y)``.
    boxes: tuple[tuple[int, int, int, int], ...]
    #: ``mColorPalette``, RGBA. The game's minimap legend, decoded for the record only.
    palette: tuple[tuple[int, int, int, int], ...]

    def named(self, index: int) -> bool:
        """Whether this index is a named region rather than no-man's-land or nothing."""
        area = self.areas[index]
        return area is not None and area.stem != NO_MANS_LAND

    @property
    def assets(self) -> tuple[str, ...]:
        """Every distinct area asset the raster's palette reaches, sorted."""
        return tuple(sorted({a.asset for a in self.areas if a is not None}))

    @property
    def keys(self) -> tuple[str, ...]:
        """Every distinct localisation key the palette reaches, sorted."""
        return tuple(sorted({a.key for a in self.areas if a is not None and a.key}))


class MapAreaError(Exception):
    """The asset is not the shape this reader knows how to read. Every message says what was
    expected and what was found, because the only response is to go and look at the asset."""


def read_map_areas(store, scripts: ScriptObjects) -> MapAreas:
    """Decode the map-area texture and resolve every palette index to one area asset. A shape
    that is not the known one means the asset was re-cooked, i.e. the game changed, and every
    check below refuses rather than decoding whatever is there."""
    view = _view(store, MAP_AREA_PATH, scripts)
    export = next(
        (e for e in view.exports if (view.class_of[e["slot"]] or "") == MAP_AREA_CLASS), None
    )
    if export is None:
        found = ", ".join(sorted({str(view.class_of[e["slot"]]) for e in view.exports})) or "none"
        raise MapAreaError(
            f"{MAP_AREA_PATH} has no {MAP_AREA_CLASS} export (found: {found}). The asset is no "
            "longer the class this reader knows."
        )
    props = view.props(export["slot"])
    missing = [name for name in MAP_AREA_PROPS if name not in props]
    if missing:
        raise MapAreaError(
            f"{MAP_AREA_CLASS} is missing {', '.join(missing)} -- the properties this reader "
            "reads. The class changed shape; refusing to guess at the rest."
        )

    width = struct.unpack("<i", props["mDataWidth"])[0]
    if width != MAP_AREA_TEXELS:
        raise MapAreaError(
            f"mDataWidth is {width}, not {MAP_AREA_TEXELS}. The texture was re-cooked at "
            "another size, so every corner measured against it was measured against a "
            "different picture."
        )
    raw = props["mAreaData"]
    count = struct.unpack_from("<i", raw, 0)[0]
    if count != width * width or len(raw) != 4 + width * width:
        raise MapAreaError(
            f"mAreaData says {count} texels in {len(raw)} bytes, but a {width}x{width} array "
            f"of palette indices is {width * width} in {4 + width * width}. The array is not "
            "the shape its own width says."
        )
    texels = raw[4 : 4 + width * width]

    palette_raw = props["mColorPalette"]
    entries = struct.unpack_from("<i", palette_raw, 0)[0]
    palette = tuple(tuple(palette_raw[4 + i * 4 : 8 + i * 4]) for i in range(entries))  # type: ignore[arg-type]

    areas, boxes = _colour_to_area(store, view, props["mColorToArea"])
    if len(areas) != entries:
        raise MapAreaError(
            f"mColorPalette has {entries} entries and mColorToArea {len(areas)}. The two "
            "halves of one lookup disagree about how many indices there are."
        )
    used = max(texels) + 1
    if used > entries:
        raise MapAreaError(
            f"the raster uses index {used - 1} but the palette stops at {entries - 1}. "
            "Refusing to hand back a texel whose area has no entry."
        )
    return MapAreas(width=width, texels=texels, areas=areas, boxes=boxes, palette=palette)


def _colour_to_area(
    store, view: PackageView, blob: bytes
) -> tuple[tuple[Area | None, ...], tuple[tuple[int, int, int, int], ...]]:
    """``mColorToArea`` -> one :class:`Area` per palette index, plus each index's extent.

    A ``TArray<FStruct>`` in Zen's tagged form is a count then one property stream per element.
    The elements are not a fixed width and only the parser that read one knows where it ended,
    hence the cursor rather than slicing.
    """
    by_hash = _areas_by_export_hash(store, view.scripts)
    count = struct.unpack_from("<i", blob, 0)[0]
    areas: list[Area | None] = []
    boxes: list[tuple[int, int, int, int]] = []
    pos = 4
    for index in range(count):
        tags, end = property_tags(blob, view.pkg.names, pos)
        fields = {name: payload for name, _kind, payload, _value in tags}
        if "MapArea" not in fields:
            raise MapAreaError(
                "an mColorToArea entry carries no MapArea reference -- the struct this reader "
                f"reads is {', '.join(MAP_AREA_ENTRY_FIELDS)} and it is no longer that"
            )
        hash_ = view.import_export_hash(fields["MapArea"])
        if hash_ is None:
            # A null reference: this build has one, a single-texel index naming no object.
            # Kept as None, and a caller decides what an unnamed texel means.
            areas.append(None)
        else:
            area = by_hash.get(hash_)
            if area is None:
                path = view.import_path(fields["MapArea"])
                raise MapAreaError(
                    f"mColorToArea index {index} names {path or 'an object'} by export hash "
                    f"{hash_:#018x}, and no Area_* asset under {MAP_AREA_DIR} publishes it. "
                    "The areas moved, or one of them is no longer where the texture looks."
                )
            areas.append(area)
        boxes.append(
            tuple(  # type: ignore[arg-type]
                struct.unpack("<i", fields[key])[0] if key in fields else 0
                for key in ("MinX", "MinY", "MaxX", "MaxY")
            )
        )
        pos = end
    return tuple(areas), tuple(boxes)


def _areas_by_export_hash(store, scripts: ScriptObjects | None) -> dict[int, Area]:
    """Every ``Area_*`` asset beside the texture, keyed by the hash an import names it by.

    By ``PublicExportHash``, never by package name: this build ships thirty-five ``Area_*``
    assets under eighteen package names, and such a pair is not always one place -- the two
    ``Area_crater`` assets carry different display names. A directory scan rather than a list,
    because a list goes stale silently: ``Area_EasternDuneForest_1`` is never referenced.
    """
    out: dict[int, Area] = {}
    for path in sorted(store.by_path):
        if not path.startswith(MAP_AREA_DIR) or "/Area_" not in path:
            continue
        view = _view(store, path, scripts)
        asset = path.rsplit("/", 1)[-1].removesuffix(".uasset")
        stem = _package_stem(view) or asset
        key, table = _display_name(view)
        area = Area(asset=asset, stem=stem, key=key, string_table=table)
        for export in view.exports:
            out[export["public_hash"]] = area
    if not out:
        raise MapAreaError(
            f"no Area_* assets under {MAP_AREA_DIR}. The map areas moved or were renamed, "
            "which means the game changed; nothing built on them can be trusted until that "
            "is looked at."
        )
    return out


def _package_stem(view: PackageView) -> str | None:
    """The package name an asset declares for itself, leaf only. Not unique, and kept anyway:
    it is the identifier a colour table is keyed by, and deriving it from the file name instead
    would pick up the ``_1`` suffix."""
    for name in view.pkg.names:
        if name.startswith("/Game/") and "/Area_" in name:
            return name.rsplit("/", 1)[-1]
    return None


def _display_name(view: PackageView) -> tuple[str | None, str | None]:
    """``mDisplayName`` as ``(key, string table)``, or ``(None, None)``.

    The property is an ``FText``: ``int32`` flags, one history byte, then the history's payload.
    History 11 is a string-table entry, whose payload is an ``FName`` table id and an ``FString``
    key. Anything else comes back as nothing rather than guessed at, because inventing a label
    defeats a table whose point is that the labels come from the game. The KEY is the answer
    (``Area_Savanna_1`` announcing ``Locations/RockyDesert`` joins two areas into one region);
    the localised string lives in ``AllStringTables.locres``, which this does not open.
    """
    for export in view.exports:
        payload = view.props(export["slot"]).get(_DISPLAY_NAME)
        if payload is None or len(payload) < 17 or payload[4] != _TEXT_HISTORY_STRING_TABLE:
            continue
        index, number = struct.unpack_from("<II", payload, 5)
        length = struct.unpack_from("<i", payload, 13)[0]
        if length <= 0 or 17 + length > len(payload):
            continue
        return payload[17 : 17 + length - 1].decode("utf-8"), view.pkg.name(index, number)
    return None, None


def _view(store, path: str, scripts: ScriptObjects | None) -> PackageView:
    if path not in store.by_path:
        raise MapAreaError(
            f"{path} is not in the container. The map areas moved or were renamed, which "
            "means the game changed; nothing here can be trusted until that is looked at."
        )
    return PackageView(store.read_path(path), scripts)
