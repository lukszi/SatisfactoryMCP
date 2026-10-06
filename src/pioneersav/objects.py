"""The inflated body: preamble, world-partition grids, levels and object headers.

This layer sits between ``chunks.decompress_body`` and the property serialiser, and answers
one question -- *where is every object's property blob* -- by walking, never by searching.
Every region declares its own length and the walk asserts that each declared length lands
exactly where the next structure begins, so a save torn mid-write fails on the first size that
does not add up instead of yielding a shorter factory. Which fields are present is gated on
``save_version``; see ``versions.py`` for the thresholds.

The body, at saveVersion 52 and above::

    i64  body size                 len(body) - 8, self-describing
    ...  archive version header    saveVersion 60 only; 26B + the engine branch string
    i32  custom version count      60 only; then that many (16-byte GUID, i32 version)
    i32  grid count                then that many world-partition grids
    i32  sub-level count
    ...  sub-level records         each named after a partition cell
    ...  the persistent level      the SAME record, with NO name string
    ...  a destroyed-actor table keyed by level name, closing the body

One level record. The two blocks are parallel lists, ``header[i]`` describing ``object[i]``,
and they are NOT interleaved::

    str  name                      (absent on the persistent level)
    i64  toc size ; [ i32 header count ][ headers ][ destroyed-actor list ]
    i64  data size ; [ i32 object count ][ object entries ]
    i32  version (52 or 60)        \\
    i32  destroyed count ; refs     |  the trailer, absent on the persistent level
    i32  archive-follows flag      /

Below saveVersion 30 there is no level list at all: one flat run of headers, then one run of
entries, then a bare destroyed-actor list closing the body. Every size field is an int32 below
saveVersion 52 and an int64 at and above it.

An object's serialisation version is per object rather than per save -- an untouched
world-partition cell keeps the bytes it was written with, so 36, 52 and 60 all appear in one
file. A version-60 payload carries one extra byte immediately before its property list (after
the reference lists on an actor, at the very start on a component); it is inside the slice, so
``properties.py`` reads it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ParseError, expect
from .reader import Reader
from .versions import FIRST_LEVEL_LIST, FIRST_MODERN_BODY

__all__ = [
    "ARCHIVE_HEADER_FIXED_LEN",
    "ARCHIVE_HEADER_LEN",
    "CHANGELIST_MASK",
    "ActorHeader",
    "BodyPreamble",
    "ComponentHeader",
    "Grid",
    "Level",
    "ObjectSlice",
    "SaveBody",
    "read_body",
]

#: Bytes of the archive version header BEFORE the engine branch string: four int32s, three
#: uint16s of engine version, and the changelist.
ARCHIVE_HEADER_FIXED_LEN = 26

#: Total archive-header length observed on the 1.2.0 branch. The branch string that closes the
#: header is length-prefixed and its length varies by engine branch --
#: '++FactoryGame+rel-main-1.2.0' is 28 characters, '++FactoryGame+rel-main-anniversary-2026'
#: is 39 -- so the total is NOT a constant. Kept for reference; do not gate on it.
ARCHIVE_HEADER_LEN = 59

#: Mask that takes the changelist out of the uint32 beside the engine version. The top bit is
#: set on every occurrence on this disk and its meaning is unknown, so it is stripped for the
#: comparison against ``buildVersion`` rather than treated as part of the number.
CHANGELIST_MASK = 0x7FFFFFFF

#: The two int32s that open the archive header. They are the signature used to recognise it
#: when a level record says one follows -- a positional check, not a search.
_ARCHIVE_MARK = (0, 522)


@dataclass
class ObjectSlice:
    """One object's property block, as a slice into the inflated body.

    ``offset``/``length`` are absolute into the same ``bytes`` object that ``read_body`` was
    given, so nothing is copied for a 44 MB save.
    """

    #: Save version this object was serialised at: 36, 52 and 60 all occur.
    version: int
    #: Second int32 of the entry. 1 on version 36/52 objects, 0 on version 60 ones.
    flag: int
    #: Absolute offset of the first property byte.
    offset: int
    #: Byte length of the property block, exactly as the entry declared it.
    length: int

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass
class ActorHeader:
    """An actor: a placed thing with a transform.

    ``rotation`` is a quaternion in x, y, z, w order. ``position`` is centimetres in the
    game's world frame.
    """

    type_path: str
    root_object: str
    instance_name: str
    #: ``None`` below saveVersion 52, where the word is simply not in the file.
    object_flags: int | None
    need_transform: int
    rotation: tuple[float, float, float, float]
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    was_placed_in_level: int

    # Aliases rather than renamed fields: the adapter reads these names, and this module
    # stays snake_case.
    @property
    def typePath(self) -> str:
        return self.type_path

    @property
    def instanceName(self) -> str:
        return self.instance_name


@dataclass
class ComponentHeader:
    """A component: no transform, and a parent actor it hangs off. Inventories, power
    connections and power info are all components.

    The bytes DO name a component's class, and this exposes it as ``class_path``, NOT as
    ``typePath``. ``iter_objects`` reads ``getattr(header, "typePath", "")`` and the
    projection's class-based branching is built on components resolving to the empty string,
    so adding that attribute would silently change the output for every one of them.
    """

    class_path: str
    root_object: str
    instance_name: str
    #: ``None`` below saveVersion 52, where the word is simply not in the file.
    object_flags: int | None
    parent_actor_name: str

    @property
    def instanceName(self) -> str:
        return self.instance_name


@dataclass
class Grid:
    """One world-partition grid: a cell size and the cells that have saved content."""

    name: str
    cell_size: int
    content_id: int
    cell_names: list[str] = field(default_factory=list)


@dataclass
class BodyPreamble:
    """Everything before the level list, kept because it is cheap and diagnostic.

    ``version_fields`` through ``custom_versions`` come from the archive version header, which
    **only saveVersion 60 bodies have**: on a 52 body the grid table starts immediately after
    the size. They are ``None``/empty there rather than faked.
    """

    declared_size: int
    grids: list[Grid]
    version_fields: tuple[int, int, int, int] | None = None
    #: Unreal engine version the body was written by, as ``(major, minor, patch)`` from three
    #: uint16s. ``None`` on a body with no archive header, because a zero triple would be a
    #: claim about the writer.
    engine_version: tuple[int, int, int] | None = None
    #: The uint32 after the engine version; its low 31 bits are the header's ``buildVersion``.
    #: Kept unmasked, so a caller sees the top bit rather than a number this module has edited.
    changelist: int | None = None
    branch: str = ""
    custom_versions: list[tuple[bytes, int]] = field(default_factory=list)

    @property
    def has_archive_header(self) -> bool:
        return self.version_fields is not None


@dataclass
class Level:
    """One level: parallel lists of headers and property-block slices.

    ``name`` is a 25-character partition-cell id for a sub-level, and ``"Persistent_Level"``
    for the one unnamed record at the end -- the file gives that record no name at all.
    """

    name: str
    headers: list[ActorHeader | ComponentHeader]
    objects: list[ObjectSlice]

    #: Bytes of the TOC block left over after the headers -- the destroyed-actor list. Kept as
    #: a number so a caller can see it is nonzero even though it is read.
    toc_extra_bytes: int = 0
    #: Actors the save records as gone: ``(level cell, actor path)`` pairs from this level's
    #: header block. See ``_read_destroyed_block``.
    destroyed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def actorAndComponentObjectHeaders(self) -> list[ActorHeader | ComponentHeader]:
        """The name the adapter reads."""
        return self.headers


@dataclass
class SaveBody:
    preamble: BodyPreamble
    levels: list[Level]
    #: Anything skipped rather than understood, as ``(offset, what)``. A future patch that adds
    #: a structure should show up here rather than as silently wrong output.
    warnings: list[tuple[int, str]] = field(default_factory=list)
    #: Destroyed actors from the sub-level trailers, and from the table that closes the body.
    #: Kept apart from ``Level.destroyed`` because the three are three different lists; see
    #: ``destroyed_actors``.
    trailer_destroyed: list[tuple[str, str]] = field(default_factory=list)
    closing_destroyed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def object_count(self) -> int:
        return sum(len(lv.objects) for lv in self.levels)

    @property
    def destroyed_actors(self) -> list[tuple[str, str]]:
        """Every actor the save records as gone, from all three lists, deduplicated.

        The world is not saved -- every slug, mushroom, Mercer sphere and drop pod sits where
        the map put it -- so a save records collectibles by the negative: which map-placed
        actors are gone. It keeps three lists of that, one trailing each level's header block,
        one in each sub-level's trailer and one closing the body, and they overlap partially
        rather than repeating each other, so all three have to be merged.
        """
        seen: dict[tuple[str, str], None] = {}
        for lv in self.levels:
            for ref in lv.destroyed:
                seen[ref] = None
        for ref in self.trailer_destroyed:
            seen[ref] = None
        for ref in self.closing_destroyed:
            seen[ref] = None
        return list(seen)

    @property
    def skipped_toc_bytes(self) -> int:
        """Destroyed-actor bytes stepped over by declared length, summed over levels. A number
        rather than thousands of per-level warnings, and expected to be nonzero.
        """
        return sum(lv.toc_extra_bytes for lv in self.levels)


def _at_archive_header(r: Reader) -> bool:
    """Is an archive version header at the cursor?

    Needed because a saveVersion 52 body has none -- its grid table starts right after the body
    size -- and one function reads both. Mistaking a 52 body for a 60 one would need it to
    declare zero grids, then 522 levels, then a 1017-byte level name; no save has fewer than
    six grids.
    """
    if r.remaining < 12:
        return False
    look = Reader(r.data, r.pos)
    return (look.i32(), look.i32(), look.i32()) == (*_ARCHIVE_MARK, 1017)


def _read_archive_header(
    r: Reader,
    warnings: list[tuple[int, str]],
    build_version: int | None = None,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int], int, str]:
    """The version header: four int32s, the engine version, the changelist, the branch.

    26 fixed bytes, then the branch as a length-prefixed string, so the total varies by build:
    59 bytes on ``rel-main-1.2.0``, 70 on ``rel-main-anniversary-2026``.

    Read field by field so a change is caught here. The same header appears at the front of the
    body and again between level records -- around 1,900 times on the reference save -- so it is
    one function rather than an inline skip.

    The engine version is three uint16s of major/minor/patch; the bytes carry no label, and it
    is constant on this disk, so that reading is an interpretation of one value.
    ``changelist & CHANGELIST_MASK`` is the build that wrote THIS record, not the save: the
    first save of the reference world under build 502094 carries 279 headers on 502094 and
    1,628 still on 495413, the levels the new build had not rewritten yet. So a changelist at
    or below the header's ``buildVersion`` is ordinary, and only one ABOVE it -- a record from
    a build newer than the one that wrote the file -- is a finding. It **warns rather than
    refuses**, and runs only when the caller supplies ``build_version``, since a value
    invented here would only ever match itself.
    """
    start = r.pos
    fields = (r.i32(), r.i32(), r.i32(), r.i32())
    expect(
        fields[:2] == _ARCHIVE_MARK,
        start,
        f"expected an archive version header starting {_ARCHIVE_MARK}, found {fields[:2]}",
    )
    # Unpacked here rather than by a Reader primitive: this is the only uint16 anything in this
    # parser reads, and reader.py's vocabulary is worth keeping small.
    raw = r.bytes(6)
    engine_version = (
        int.from_bytes(raw[0:2], "little"),
        int.from_bytes(raw[2:4], "little"),
        int.from_bytes(raw[4:6], "little"),
    )
    changelist_at = r.pos
    changelist = r.u32()
    if build_version is not None and changelist & CHANGELIST_MASK > build_version:
        what = (
            f"the body says changelist {changelist & CHANGELIST_MASK} "
            f"(from {changelist:#010x}) and the header says buildVersion {build_version}; "
            "a record written by a build newer than the save itself is reported rather "
            "than refused"
        )
        if what not in [w for _at, w in warnings]:
            warnings.append((changelist_at, what))
    branch_at = r.pos
    branch = r.string()
    expect(
        branch_at - start == ARCHIVE_HEADER_FIXED_LEN,
        start,
        f"archive header read {branch_at - start} bytes before the engine branch string, "
        f"expected {ARCHIVE_HEADER_FIXED_LEN}",
    )
    return fields, engine_version, changelist, branch


def _read_custom_versions(r: Reader) -> list[tuple[bytes, int]]:
    """UE's custom-version array: a count, then (GUID, version) pairs. The count is what
    terminates it; there is no sentinel.
    """
    count = r.i32()
    expect(0 <= count <= 4096, r.pos - 4, f"custom version count {count} is not plausible")
    return [(r.bytes(16), r.i32()) for _ in range(count)]


def _read_grids(r: Reader) -> list[Grid]:
    """The world-partition table: 7 grids on a saveVersion 60 body, 6 on a 52 one.

    Shape per grid: name, cell size, a u32, then a count of cells, each a 25-character base-36
    id and a u32 of its own.

    Every level that contains objects is named here except the persistent level, which is not a
    partition cell. The converse does not hold -- most empty levels are absent from the table
    -- so this is a one-way correspondence and not a set equality.
    """
    count = r.i32()
    expect(0 <= count <= 256, r.pos - 4, f"grid count {count} is not plausible")
    grids = []
    for _ in range(count):
        name = r.string()
        cell_size = r.i32()
        content_id = r.u32()
        n_cells = r.i32()
        expect(
            0 <= n_cells <= 1_000_000,
            r.pos - 4,
            f"grid {name!r} claims {n_cells} cells",
        )
        cells = []
        for _ in range(n_cells):
            cells.append(r.string())
            r.u32()  # per-cell content id, unread by anything above this
        grids.append(Grid(name=name, cell_size=cell_size, content_id=content_id, cell_names=cells))
    return grids


def _read_header(r: Reader, save_version: int) -> ActorHeader | ComponentHeader:
    """One object header, of either kind. Both open the same way -- class path, root object,
    instance name, then UE's ``EObjectFlags``.

    **Which kind it is comes from the leading int32, not from the flags.** Eight distinct flags
    values occur across the saves on this disk, and although the ``RF_DefaultSubObject`` bit
    does separate actors from components in the common case, it is not an invariant.

    **The flags word arrives at saveVersion 52.** Below it the transform follows the instance
    name directly. It is ``None`` rather than 0 there, because 0 is a legal flags word and
    "absent" must not read as "no flags set".
    """
    at = r.pos
    kind = r.i32()
    class_path = r.string()
    root_object = r.string()
    instance_name = r.string()
    flags = r.u32() if save_version >= FIRST_MODERN_BODY else None
    if kind == 1:
        need_transform = r.i32()
        rotation = (r.f32(), r.f32(), r.f32(), r.f32())
        position = (r.f32(), r.f32(), r.f32())
        scale = (r.f32(), r.f32(), r.f32())
        return ActorHeader(
            type_path=class_path,
            root_object=root_object,
            instance_name=instance_name,
            object_flags=flags,
            need_transform=need_transform,
            rotation=rotation,
            position=position,
            scale=scale,
            was_placed_in_level=r.i32(),
        )
    if kind == 0:
        return ComponentHeader(
            class_path=class_path,
            root_object=root_object,
            instance_name=instance_name,
            object_flags=flags,
            parent_actor_name=r.string(),
        )
    raise ParseError(
        f"at body offset {at}: object header kind {kind}, expected 0 (component) or "
        f"1 (actor). The class path read as {class_path!r}"
    )


def _read_object_entry(r: Reader, save_version: int) -> ObjectSlice:
    """One object entry: three int32s, the payload, and on version 60 a trailing int32 that is
    0 on every object seen. The size counts from immediately after itself, which is what puts
    that trailing int32 at the far end rather than in the head.

    The payload is NOT parsed here; its boundaries are the deliverable. ``r`` is left at the
    FIRST payload byte, and the caller steps over the payload and reads the version-60 trailer,
    because only the caller knows where the block ends and can refuse a size that runs past it.

    **Below saveVersion 52 the entry is a bare size.** There is no version int32 and no flag
    int32, so the object's serialisation version is *not in the bytes* and has to be taken from
    the save. That is the one place in this parser where a version is supplied rather than
    read, and it is why ``read_body`` takes ``save_version`` at all.
    """
    if save_version >= FIRST_MODERN_BODY:
        version = r.i32()
        flag = r.i32()
    else:
        version, flag = save_version, 0
    size = r.i32()
    expect(size >= 0, r.pos - 4, f"object entry declares a negative payload size {size}")
    return ObjectSlice(version=version, flag=flag, offset=r.pos, length=size)


def _read_destroyed_refs(r: Reader, where: str, limit: int) -> list[tuple[str, str]]:
    """A count, then that many ``(level name, actor path)`` pairs."""
    at = r.pos
    count = r.i32()
    expect(
        0 <= count <= 1_000_000 and r.pos + count * 8 <= limit,
        at,
        f"{where}: {count} destroyed actors do not fit in the {limit - r.pos} bytes left",
    )
    return [(r.string(), r.string()) for _ in range(count)]


def _read_destroyed_block(
    r: Reader, name: str, end: int, *, grouped: bool
) -> list[tuple[str, str]]:
    """The destroyed-actor list trailing a level's header block.

    Two shapes, and which one appears is decided by the level rather than by a flag in the
    file: a sub-level writes one bare ``[i32 count][refs]``, and the persistent level writes it
    grouped by the world-partition cell the actors lived in,
    ``[i32 groups][str cell][i32 count][refs]``. Reading the wrong shape lands off the block's
    declared end, which the caller checks.
    """
    if not grouped:
        return _read_destroyed_refs(r, f"level {name!r}", end)
    at = r.pos
    groups = r.i32()
    expect(
        0 <= groups <= 100_000,
        at,
        f"level {name!r}: its destroyed-actor list claims {groups} cell groups",
    )
    out: list[tuple[str, str]] = []
    for _ in range(groups):
        cell = r.string()
        out.extend(_read_destroyed_refs(r, f"level {name!r} cell {cell!r}", end))
    return out


def _read_block_size(r: Reader, save_version: int) -> int:
    """A level's TOC or data size: int32 below saveVersion 52, int64 at and above it."""
    return r.i64() if save_version >= FIRST_MODERN_BODY else r.i32()


def _read_level(r: Reader, *, named: bool, save_version: int) -> Level:
    """One level record. ``named=False`` is the persistent level at the very end.

    The two block sizes are the load-bearing part. Headers are walked one by one and
    must finish inside the TOC block; objects are walked one by one and must finish
    exactly ON the data block's declared end. The first catches a header layout change,
    the second catches a payload size that lies.
    """
    name = r.string() if named else "Persistent_Level"

    toc_size_at = r.pos
    toc_size = _read_block_size(r, save_version)
    toc_start = r.pos
    toc_end = toc_start + toc_size
    expect(
        0 <= toc_size <= r.remaining,
        toc_size_at,
        f"level {name!r} declares a {toc_size}-byte header block, {r.remaining} left",
    )
    header_count = r.i32()
    expect(
        0 <= header_count <= 10_000_000,
        r.pos - 4,
        f"level {name!r} claims {header_count} object headers",
    )
    headers: list[ActorHeader | ComponentHeader] = []
    for _ in range(header_count):
        expect(
            r.pos < toc_end,
            r.pos,
            f"level {name!r}: header {len(headers)} of {header_count} starts past the "
            f"end of its {toc_size}-byte block",
        )
        headers.append(_read_header(r, save_version))
    extra = toc_end - r.pos
    expect(
        extra >= 0,
        r.pos,
        f"level {name!r}: {header_count} headers overran the header block by {-extra} bytes",
    )
    # Grouped by partition cell on the persistent level, but only from saveVersion 52: there is
    # no world partition below that, so the old persistent record writes the same bare list a
    # sub-level does, and reading it as grouped turns the count into a string length.
    grouped = not named and save_version >= FIRST_MODERN_BODY
    destroyed = _read_destroyed_block(r, name, toc_end, grouped=grouped) if extra else []
    expect(
        r.pos == toc_end,
        r.pos,
        f"level {name!r}: its destroyed-actor list ended at {r.pos}, but the header block "
        f"declared {toc_end}. The list's shape is wrong, not its length",
    )

    data_size_at = r.pos
    data_size = _read_block_size(r, save_version)
    data_start = r.pos
    data_end = data_start + data_size
    expect(
        0 <= data_size <= r.remaining,
        data_size_at,
        f"level {name!r} declares a {data_size}-byte object block, {r.remaining} left",
    )
    object_count = r.i32()
    expect(
        object_count == header_count,
        r.pos - 4,
        f"level {name!r} has {header_count} headers but {object_count} objects. They are "
        "parallel lists; a mismatch means one of the two blocks was misread",
    )
    objects = []
    for _ in range(object_count):
        slot = _read_object_entry(r, save_version)
        expect(
            slot.end <= data_end,
            slot.offset - 4,
            f"level {name!r}: object {len(objects)} declares {slot.length} bytes, which "
            f"runs {slot.end - data_end} past the end of its block",
        )
        objects.append(slot)
        r.pos = slot.end
        if slot.version >= 60:
            trailing = r.i32()
            expect(
                trailing == 0,
                r.pos - 4,
                f"level {name!r}: version {slot.version} object {len(objects) - 1} is "
                f"followed by {trailing}, and every one of the 39,015 in the reference "
                "save is followed by 0. Something after the payload is not understood",
            )
    expect(
        r.pos == data_end,
        r.pos,
        f"level {name!r}: {object_count} object payloads ended at {r.pos}, but the block "
        f"declared {data_end}. One payload size is wrong",
    )

    return Level(
        name=name,
        headers=headers,
        objects=objects,
        toc_extra_bytes=extra,
        destroyed=destroyed,
    )


def _read_level_trailer(
    r: Reader,
    name: str,
    warnings: list[tuple[int, str]],
    *,
    save_version: int,
    versioned_archive: bool,
    build_version: int | None = None,
) -> list[tuple[str, str]]:
    """A sub-level's trailer: a version, a destroyed-actor list, and maybe a flag.

    On a saveVersion 60 body the flag says whether an archive version header follows, and the
    correlation is checked rather than trusted -- the header's own signature has to be there.
    On a saveVersion 52 body there is no flag and no per-level archive headers: the next
    level's name follows the destroyed-actor list directly.

    Below saveVersion 52 there is no version int32 either, so the trailer is nothing but the
    destroyed-actor list. That list and the header block's do not always hold the same actors,
    which is why both are read and merged rather than one taken as authoritative.
    """
    if save_version >= FIRST_MODERN_BODY:
        version = r.i32()
        expect(
            version in (52, 60),
            r.pos - 4,
            f"level {name!r} trailer version {version}, expected 52 or 60",
        )
    destroyed = _read_destroyed_refs(r, f"level {name!r} trailer", len(r.data))
    if not versioned_archive:
        return destroyed
    flag = r.i32()
    expect(flag in (0, 1), r.pos - 4, f"level {name!r} trailer flag {flag}, expected 0 or 1")
    if flag:
        # Checked, not skipped: nearly every archive header in a body is one of these per-level
        # ones, so a body assembled from two builds' level records is caught here rather than
        # only at the front.
        _read_archive_header(r, warnings, build_version)
        _read_custom_versions(r)
    return destroyed


def _read_final_destroyed_table(
    r: Reader, warnings: list[tuple[int, str]], save_version: int
) -> list[tuple[str, str]]:
    """The body's last structure: destroyed actors, grouped by level name, in two lists per
    group -- looted drop pods and crashed ships, then Mercer shrines.

    Parsed rather than skipped because it is the only thing that can prove the whole walk
    consumed the file: landing exactly on the last byte is what says every level count, block
    size and payload size before it was right.

    Below saveVersion 52 it is one bare ``[i32 count][refs]`` list rather than a table grouped
    by level, and a body with a level list (saveVersion 30 and 36) is preceded by one more such
    list, read as the unnamed persistent record's trailer.
    """
    if save_version < FIRST_MODERN_BODY:
        out = _read_destroyed_refs(r, "the closing destroyed-actor list", len(r.data))
        if r.remaining:
            warnings.append((r.pos, f"{r.remaining} bytes after the closing destroyed-actor list"))
        return out
    at = r.pos
    groups = r.i32()
    expect(0 <= groups <= 100_000, at, f"the closing table claims {groups} level groups")
    out: list[tuple[str, str]] = []
    for _ in range(groups):
        name = r.string()
        for which in (1, 2):
            out.extend(
                _read_destroyed_refs(r, f"closing table, level {name!r}, list {which}", len(r.data))
            )
    if r.remaining:
        warnings.append((r.pos, f"{r.remaining} bytes after the closing destroyed-actor table"))
    return out


def _read_flat_levels(r: Reader, save_version: int) -> list[Level]:
    """Every object in a body written before the level list existed, grouped by its own level.

    Below saveVersion 30 the body holds one run of headers and one run of entries and says
    nothing about levels -- no count, no names, no block sizes. The level is in each header's
    ``root_object`` instead, and three values occur: ``Persistent_Level``,
    ``Persistent_Exploration`` and ``Persistent_Exploration_2``. Grouping by that field is what
    gives every level the name the bytes give it; one flat level would have to be *called*
    something, and any name would be wrong for the objects rooted elsewhere.

    Groups are in first-appearance order and order within a group is the file's, so
    ``header[i]`` still describes ``object[i]``. Neither run is bounded by a declared size --
    there is no block to end -- so the only referee is the whole-body one, that the closing
    destroyed-actor list lands on the last byte.
    """
    at = r.pos
    header_count = r.i32()
    # Bounded by the bytes left rather than by a flat ceiling, because there is no enclosing
    # block to bound it: an object header is an int32 kind plus three length-prefixed strings,
    # so the shortest conceivable one is 16 bytes. A flat ceiling on a count that lives inside
    # the payload lets a torn file allocate for millions of records before anything notices.
    expect(
        0 <= header_count <= (r.remaining) // 16,
        at,
        f"the body claims {header_count} object headers with {r.remaining} bytes left, and a "
        "header is at least sixteen",
    )
    headers = [_read_header(r, save_version) for _ in range(header_count)]

    at = r.pos
    object_count = r.i32()
    expect(
        object_count == header_count,
        at,
        f"the body has {header_count} headers but {object_count} objects. They are parallel "
        "lists; a mismatch means the header run was misread",
    )
    slots = []
    for index in range(object_count):
        slot = _read_object_entry(r, save_version)
        expect(
            slot.end <= len(r.data),
            slot.offset - 4,
            f"object {index} declares {slot.length} bytes, which runs "
            f"{slot.end - len(r.data)} past the end of the body",
        )
        slots.append(slot)
        r.pos = slot.end

    grouped: dict[str, Level] = {}
    for header, slot in zip(headers, slots, strict=True):
        level = grouped.get(header.root_object)
        if level is None:
            level = grouped[header.root_object] = Level(
                name=header.root_object, headers=[], objects=[]
            )
        level.headers.append(header)
        level.objects.append(slot)
    return list(grouped.values())


def read_body(
    body: bytes, save_version: int = FIRST_MODERN_BODY, build_version: int | None = None
) -> SaveBody:
    """Walk the inflated body up to (not into) the property blocks.

    ``body`` is the concatenation of the inflated chunks. The returned slices index into it, so
    it must stay alive for as long as they are used -- nothing is copied, which is what keeps a
    44 MB body at a quarter of a second.

    ``save_version`` picks which fields are there; see ``versions.py``. It is a parameter and
    not a sniff because **an old body does not say**: below saveVersion 52 an object entry
    carries no version of its own. The two modern layouts, 52 and 60, do tell themselves apart
    from the bytes, which is why the default is the modern one.

    ``build_version`` is the header's, and passing it arms the changelist check in
    ``_read_archive_header``. Left ``None`` -- what a caller with a bare body must do -- the
    changelist is read and reported but nothing is compared.
    """
    old = save_version < FIRST_MODERN_BODY
    size_width = 4 if old else 8
    r = Reader(body)
    # A save truncated to exactly its header inflates to an EMPTY body, which is the shape of a
    # save the game created and had not finished. Named here, because otherwise the size field
    # below reports an offset of 0 in a buffer of 0 with no hint that the body is meant.
    expect(
        len(body) >= size_width,
        0,
        f"the inflated body is {len(body)} bytes, too short to hold the int{size_width * 8} "
        "size field it opens with -- a save truncated to its header inflates to nothing at all",
    )
    declared = r.i32() if old else r.i64()
    expect(
        declared == len(body) - size_width,
        0,
        f"the body says it is {declared} bytes; {len(body) - size_width} follow the size field",
    )
    warnings: list[tuple[int, str]] = []
    preamble = BodyPreamble(declared_size=declared, grids=[])
    if not old:
        # No world partition and no archive versioning below saveVersion 52: the level list --
        # or, below 30, the header run -- starts straight after the size.
        if _at_archive_header(r):
            fields, engine_version, changelist, branch = _read_archive_header(
                r, warnings, build_version
            )
            preamble.version_fields = fields
            preamble.engine_version = engine_version
            preamble.changelist = changelist
            preamble.branch = branch
            preamble.custom_versions = _read_custom_versions(r)
        preamble.grids = _read_grids(r)

    levels: list[Level] = []
    trailer_destroyed: list[tuple[str, str]] = []

    if save_version < FIRST_LEVEL_LIST:
        levels = _read_flat_levels(r, save_version)
    else:
        sub_count = r.i32()
        expect(
            0 <= sub_count <= 1_000_000,
            r.pos - 4,
            f"the body claims {sub_count} sub-levels",
        )
        for _ in range(sub_count):
            level = _read_level(r, named=True, save_version=save_version)
            levels.append(level)
            trailer_destroyed += _read_level_trailer(
                r,
                level.name,
                warnings,
                save_version=save_version,
                versioned_archive=preamble.has_archive_header,
                build_version=build_version,
            )

        # The persistent level closes the list: the same record with no name, and no trailer.
        levels.append(_read_level(r, named=False, save_version=save_version))
        if old:
            # ...except on an old body, where the unnamed record is followed by one more bare
            # list before the closing one. Read as that record's trailer, like every other
            # level's. Reading it instead as a second closing list consumes the same bytes, and
            # nothing in these saves distinguishes the two.
            trailer_destroyed += _read_destroyed_refs(
                r, "the persistent level's trailer", len(body)
            )

    closing_destroyed = _read_final_destroyed_table(r, warnings, save_version)

    return SaveBody(
        preamble=preamble,
        levels=levels,
        warnings=warnings,
        trailer_destroyed=trailer_destroyed,
        closing_destroyed=closing_destroyed,
    )
