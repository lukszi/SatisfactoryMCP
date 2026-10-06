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
``pioneersav.properties`` reads it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .destroyed import read_closing_destroyed_table, read_destroyed_block, read_destroyed_refs
from .errors import ParseError, expect
from .reader import Reader
from .versions import FIRST_LEVEL_LIST, FIRST_MODERN_BODY, FIRST_UE5_OBJECT_VERSION

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
    cell_names: list[str] = field(default_factory=list[str])


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
    custom_versions: list[tuple[bytes, int]] = field(default_factory=list[tuple[bytes, int]])

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
    #: header block. See ``destroyed.read_destroyed_block``.
    destroyed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

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
    warnings: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    #: Destroyed actors from the sub-level trailers, and from the table that closes the body.
    #: Kept apart from ``Level.destroyed`` because the three are three different lists; see
    #: ``destroyed_actors``.
    trailer_destroyed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    closing_destroyed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    @property
    def object_count(self) -> int:
        return sum(len(level.objects) for level in self.levels)

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
        for level in self.levels:
            for ref in level.destroyed:
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
        return sum(level.toc_extra_bytes for level in self.levels)


def _at_archive_header(r: Reader) -> bool:
    """Is an archive version header at the cursor?

    Needed because a saveVersion 52 body has none -- its grid table starts right after the body
    size -- and one function reads both. Mistaking a 52 body for a 60 one would need it to
    declare zero grids, then 522 levels, then a 1017-byte level name; no save has fewer than
    six grids.
    """
    if r.remaining < 12:
        return False
    peek = Reader(r.data, r.pos)
    return (peek.i32(), peek.i32(), peek.i32()) == (*_ARCHIVE_MARK, 1017)


def _read_archive_header(
    r: Reader,
    warnings: list[tuple[int, str]],
    build_version: int | None = None,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int], int, str]:
    """The archive version header: four int32s, three uint16s of engine version, the
    changelist, then the engine branch as a length-prefixed string (savparse-notes.md)."""
    start = r.pos
    fields = (r.i32(), r.i32(), r.i32(), r.i32())
    expect(
        fields[:2] == _ARCHIVE_MARK,
        start,
        f"expected an archive version header starting {_ARCHIVE_MARK}, found {fields[:2]}",
    )
    # the parser's only uint16s, so they get no Reader primitive
    raw = r.bytes(6)
    engine_version = (
        int.from_bytes(raw[0:2], "little"),
        int.from_bytes(raw[2:4], "little"),
        int.from_bytes(raw[4:6], "little"),
    )
    changelist_at = r.pos
    changelist = r.u32()
    # warns, never refuses: per-level headers lag the save's build
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
    grids: list[Grid] = []
    for _ in range(count):
        name = r.string()
        cell_size = r.i32()
        content_id = r.u32()
        cell_count = r.i32()
        expect(
            0 <= cell_count <= 1_000_000,
            r.pos - 4,
            f"grid {name!r} claims {cell_count} cells",
        )
        cells: list[str] = []
        for _ in range(cell_count):
            cells.append(r.string())
            r.u32()  # per-cell content id, unread by anything above this
        grids.append(Grid(name=name, cell_size=cell_size, content_id=content_id, cell_names=cells))
    return grids


def _read_header(r: Reader, save_version: int) -> ActorHeader | ComponentHeader:
    """One object header, of either kind: class path, root object, instance name, then UE's
    ``EObjectFlags`` from saveVersion 52 (``None`` below, since 0 is a legal flags word).

    The kind comes from the leading int32: the ``RF_DefaultSubObject`` flag separates actors
    from components only in the common case.
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
    """One object entry's head: version, flag and payload size, leaving ``r`` on the first
    payload byte for the caller, which knows the block's end.

    Below saveVersion 52 the entry is a bare size, so the object's version is taken from the
    save: the one version this parser is given rather than reads.
    """
    if save_version >= FIRST_MODERN_BODY:
        version = r.i32()
        flag = r.i32()
    else:
        version, flag = save_version, 0
    size = r.i32()
    expect(size >= 0, r.pos - 4, f"object entry declares a negative payload size {size}")
    return ObjectSlice(version=version, flag=flag, offset=r.pos, length=size)


def _read_block_size(r: Reader, save_version: int) -> int:
    """A level's TOC or data size: int32 below saveVersion 52, int64 at and above it."""
    return r.i64() if save_version >= FIRST_MODERN_BODY else r.i32()


def _read_object_entries(
    r: Reader, count: int, end: int, save_version: int, where: str
) -> list[ObjectSlice]:
    """``count`` object entries, each payload stepped over and refused if it runs past ``end``."""
    slots: list[ObjectSlice] = []
    for index in range(count):
        slot = _read_object_entry(r, save_version)
        expect(
            slot.end <= end,
            slot.offset - 4,
            f"{where}: object {index} declares {slot.length} bytes, which runs "
            f"{slot.end - end} past the end of its block",
        )
        slots.append(slot)
        r.pos = slot.end
        if slot.version >= FIRST_UE5_OBJECT_VERSION:
            trailing = r.i32()
            expect(
                trailing == 0,
                r.pos - 4,
                f"{where}: version {slot.version} object {index} is followed by {trailing} "
                "where the format has 0. Something after the payload is not understood",
            )
    return slots


def _read_header_block(
    r: Reader, name: str, save_version: int, *, named: bool
) -> tuple[list[ActorHeader | ComponentHeader], int, list[tuple[str, str]]]:
    """A level's TOC block: its object headers, then the destroyed-actor list filling the rest.

    Returns the headers, the list's byte count and the list. The headers must finish inside the
    block, which catches a header layout change.
    """
    size_at = r.pos
    size = _read_block_size(r, save_version)
    end = r.pos + size
    expect(
        0 <= size <= r.remaining,
        size_at,
        f"level {name!r} declares a {size}-byte header block, {r.remaining} left",
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
            r.pos < end,
            r.pos,
            f"level {name!r}: header {len(headers)} of {header_count} starts past the "
            f"end of its {size}-byte block",
        )
        headers.append(_read_header(r, save_version))
    destroyed_bytes = end - r.pos
    expect(
        destroyed_bytes >= 0,
        r.pos,
        f"level {name!r}: {header_count} headers overran the header block by "
        f"{-destroyed_bytes} bytes",
    )
    # grouped by cell only on a modern persistent level: below 52 there is no world partition
    grouped = not named and save_version >= FIRST_MODERN_BODY
    destroyed = read_destroyed_block(r, name, end, grouped=grouped) if destroyed_bytes else []
    expect(
        r.pos == end,
        r.pos,
        f"level {name!r}: its destroyed-actor list ended at {r.pos}, but the header block "
        f"declared {end}. The list's shape is wrong, not its length",
    )
    return headers, destroyed_bytes, destroyed


def _read_object_block(
    r: Reader, name: str, header_count: int, save_version: int
) -> list[ObjectSlice]:
    """A level's data block: one entry per header, landing exactly on the block's declared end,
    which catches a payload size that lies."""
    size_at = r.pos
    size = _read_block_size(r, save_version)
    end = r.pos + size
    expect(
        0 <= size <= r.remaining,
        size_at,
        f"level {name!r} declares a {size}-byte object block, {r.remaining} left",
    )
    object_count = r.i32()
    expect(
        object_count == header_count,
        r.pos - 4,
        f"level {name!r} has {header_count} headers but {object_count} objects. They are "
        "parallel lists; a mismatch means one of the two blocks was misread",
    )
    objects = _read_object_entries(r, object_count, end, save_version, f"level {name!r}")
    expect(
        r.pos == end,
        r.pos,
        f"level {name!r}: {object_count} object payloads ended at {r.pos}, but the block "
        f"declared {end}. One payload size is wrong",
    )
    return objects


def _read_level(r: Reader, *, named: bool, save_version: int) -> Level:
    """One level record. ``named=False`` is the persistent level at the very end."""
    name = r.string() if named else "Persistent_Level"
    headers, destroyed_bytes, destroyed = _read_header_block(r, name, save_version, named=named)
    objects = _read_object_block(r, name, len(headers), save_version)
    return Level(
        name=name,
        headers=headers,
        objects=objects,
        toc_extra_bytes=destroyed_bytes,
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
    """A sub-level's trailer: a version (from saveVersion 52), a destroyed-actor list, and on a
    body with archive headers a flag saying whether one follows, whose signature is then
    checked rather than trusted."""
    if save_version >= FIRST_MODERN_BODY:
        version = r.i32()
        expect(
            version in (52, 60),
            r.pos - 4,
            f"level {name!r} trailer version {version}, expected 52 or 60",
        )
    destroyed = read_destroyed_refs(r, f"level {name!r} trailer", len(r.data))
    if not versioned_archive:
        return destroyed
    flag = r.i32()
    expect(flag in (0, 1), r.pos - 4, f"level {name!r} trailer flag {flag}, expected 0 or 1")
    if flag:
        # read, not skipped: nearly every archive header in a body is one of these
        _read_archive_header(r, warnings, build_version)
        _read_custom_versions(r)
    return destroyed


def _read_flat_levels(r: Reader, save_version: int) -> list[Level]:
    """Every object in a body written before the level list existed, grouped by its own level.

    Below saveVersion 30 the body is one run of headers and one run of entries, and the level
    is only in each header's ``root_object``. Groups keep first-appearance order and the file's
    order within, so ``header[i]`` still describes ``object[i]``. No block bounds either run;
    the closing destroyed-actor list landing on the last byte is the referee.
    """
    at = r.pos
    header_count = r.i32()
    # bounded by the bytes left, since no block encloses it: a header is at least 16 bytes
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
    slots = _read_object_entries(r, object_count, len(r.data), save_version, "the body")

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


def _read_body_size(r: Reader, *, old: bool) -> int:
    """The size field the body opens with, int32 below saveVersion 52 and int64 from it, which
    must count exactly the bytes after it."""
    size_width = 4 if old else 8
    body_length = len(r.data)
    # a save truncated to its header inflates to nothing; say so rather than "offset 0 of 0"
    expect(
        body_length >= size_width,
        0,
        f"the inflated body is {body_length} bytes, too short to hold the int{size_width * 8} "
        "size field it opens with -- a save truncated to its header inflates to nothing at all",
    )
    declared = r.i32() if old else r.i64()
    expect(
        declared == body_length - size_width,
        0,
        f"the body says it is {declared} bytes; {body_length - size_width} follow the size field",
    )
    return declared


def _read_preamble(
    r: Reader,
    declared: int,
    warnings: list[tuple[int, str]],
    build_version: int | None,
) -> BodyPreamble:
    """A modern body's preamble: the archive header only saveVersion 60 has, then the grids."""
    preamble = BodyPreamble(declared_size=declared, grids=[])
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
    return preamble


def _read_level_list(
    r: Reader,
    warnings: list[tuple[int, str]],
    *,
    save_version: int,
    versioned_archive: bool,
    build_version: int | None,
) -> tuple[list[Level], list[tuple[str, str]]]:
    """The sub-levels, each followed by its trailer, then the unnamed persistent level.

    Returns the levels and the destroyed actors their trailers list.
    """
    sub_count = r.i32()
    expect(
        0 <= sub_count <= 1_000_000,
        r.pos - 4,
        f"the body claims {sub_count} sub-levels",
    )
    levels: list[Level] = []
    trailer_destroyed: list[tuple[str, str]] = []
    for _ in range(sub_count):
        level = _read_level(r, named=True, save_version=save_version)
        levels.append(level)
        trailer_destroyed += _read_level_trailer(
            r,
            level.name,
            warnings,
            save_version=save_version,
            versioned_archive=versioned_archive,
            build_version=build_version,
        )
    levels.append(_read_level(r, named=False, save_version=save_version))
    if save_version < FIRST_MODERN_BODY:
        # an old persistent record has a trailer too: one bare list before the closing one
        trailer_destroyed += read_destroyed_refs(r, "the persistent level's trailer", len(r.data))
    return levels, trailer_destroyed


def read_body(
    body: bytes, save_version: int = FIRST_MODERN_BODY, build_version: int | None = None
) -> SaveBody:
    """Walk the inflated body up to (not into) the property blocks.

    ``body`` is the concatenation of the inflated chunks, and the returned slices index into
    it, so it must stay alive while they are used. ``save_version`` picks which fields are there
    (see ``versions.py``); it is a parameter because below saveVersion 52 the body does not say.
    ``build_version`` is the header's, and passing it arms the changelist check in
    ``_read_archive_header``.
    """
    r = Reader(body)
    declared = _read_body_size(r, old=save_version < FIRST_MODERN_BODY)
    warnings: list[tuple[int, str]] = []
    if save_version < FIRST_MODERN_BODY:
        # no world partition and no archive versioning: the levels follow the size directly
        preamble = BodyPreamble(declared_size=declared, grids=[])
    else:
        preamble = _read_preamble(r, declared, warnings, build_version)

    if save_version < FIRST_LEVEL_LIST:
        levels, trailer_destroyed = _read_flat_levels(r, save_version), []
    else:
        levels, trailer_destroyed = _read_level_list(
            r,
            warnings,
            save_version=save_version,
            versioned_archive=preamble.has_archive_header,
            build_version=build_version,
        )

    closing_destroyed = read_closing_destroyed_table(r, warnings, save_version)

    return SaveBody(
        preamble=preamble,
        levels=levels,
        warnings=warnings,
        trailer_destroyed=trailer_destroyed,
        closing_destroyed=closing_destroyed,
    )
