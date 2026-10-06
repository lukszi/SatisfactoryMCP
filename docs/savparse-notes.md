# Replacing the vendored GPL save parser — the derived format

The `.sav` format as derived, what is implemented against it, and every verification number
with the measurement that produced it. Self-contained: a reader who has seen none of the
conversation should be able to continue from this. Every class the format writes is decoded
and, since the destroyed-actor lists were read, **no region of any body on this disk is
stepped over by a declared length any more** — the byte budget is 100% rather than 99.8%.
What is *unexplained* rather than unread is listed at the end of the verdict.

**Every number in this document was re-measured against the tree as it now stands.** They
were first taken while several agents were still editing `pioneersav/`, which makes them
statements about code that no longer exists; the whole-folder parity run, the property-by-
property comparison and all timings were taken again at the end, on one unchanging tree, and
the figures below are those. Where a re-run disagreed with an earlier note, the earlier note
was corrected rather than kept alongside.

## Status

| layer | module | state |
|---|---|---|
| primitives | `pioneersav/reader.py` (87 lines) | done |
| one exception type and its check | `pioneersav/errors.py` (25) | done |
| object references | `pioneersav/references.py` (67) | done |
| header | `pioneersav/header.py` (236) | done |
| chunk decompression | `pioneersav/chunks.py` (124) | done |
| body, levels, object headers | `pioneersav/objects.py` (741) | done |
| the three destroyed-actor lists | `pioneersav/destroyed.py` (78) | done |
| tagged property serialiser | `pioneersav/properties/` (926, in five modules) | done |
| composition + the sidecar switch | `pioneersav/save.py` (201) | done |
| the lightweight buildables' trailing bytes | `pioneersav/lightweight.py` (190) | done |
| the other seven classes' trailing bytes | `pioneersav/trailers.py` (156) | done |

Line counts are `wc -l` on the tree as it stands; 2,972 with `__init__.py`'s 68 and
`versions.py`'s 73.

**132 tests** across eight `tests/test_savparse_*.py` files, inside a suite of **918 passing, 1
skipped** — the same count with `SATISFACTORY_SAVPARSE=own` and with `=vendor`. They run
against committed fixtures, so they pass with no game install and will survive the vendored
library's deletion.

**The projection is complete.** All **20** of 20 keys are leaf-identical on all 31 readable
saves, including every one of the 224,530 structure instances and every one of the 21,038
destroyed-actor references behind the `removed` key. See *Verdict*.

## Why

`sidecar/vendor/sat_sav_parse/` is **GPL-3.0** and its licence reaches the whole project.

**What "cleanroom" can mean here, stated honestly.** A strict cleanroom needs an
implementer who has never seen the original, and the library is vendored in this repo. What
is being done instead is a reimplementation **of the file format** — a fact about what the
game writes, not a creative work — verified black-box: same file in, same values out. That
is the ordinary interoperability route. It reduces exposure; it is not a legal
certification, and nobody on this project should present it as one.

**Rule for anyone continuing:** derive from the bytes and from the game's own displayed
values. Use the vendored library **only** as an oracle — call it, compare outputs — never
read its implementation for structure or naming.

## What we actually depend on

Runtime, in `src/satisfactory_mcp/core/saveio/extract.py`, is **three entry points**:

| entry point | used for | status |
|---|---|---|
| `readSaveFileInfo(path)` | 9 header fields → projection `header` | ✅ `pioneersav.read_info` |
| `readFullSaveFile(path)` | `.levels[].actorAndComponentObjectHeaders[]` + `.objects[]` + the destroyed-actor lists | ✅ `pioneersav.read_full_save` — all 20 projection fields exact on all 31 readable saves |
| `ParseError` | one `except` at the save boundary | ✅ `pioneersav.ParseError` |

**All three are wired in**, behind `SATISFACTORY_SAVPARSE=own|vendor`, **defaulting to
`vendor`**. Nothing technical blocks the flip any more — it is a decision for the user, and it
belongs to the deletion rather than preceding it, since the licence exposure is the library's
presence and not which branch runs. Until then the switch's job is to make the parity diff a
measurement.

The adapter surface `extract.py` needs from a parsed save:

- `save.levels` → each with `actorAndComponentObjectHeaders` and `objects` (parallel lists)
- header: `.typePath` (absent on components — use `getattr` default), `.instanceName`, `.position`
- object: `.properties` as a list of `[name, value]` pairs (`props()` flattens it)
- struct arrays arrive as `[values, propertyTypes]`; see `struct_fields()` for both shapes
- `save.destroyed_actors` → `(cell, actor path)` pairs, all three lists merged and
  deduplicated. `_removed` falls back to the vendored spelling — per-level `collectables1`
  and `collectables2` plus `dropPodObjectReferenceList` and `extraObjectReferenceList` — when
  the attribute is absent, which is how one projection key is produced from either engine.

`sav_data/` (6,800 lines of tables: crash sites, Mercer spheres, slugs, resource purity) is
**build-time only** — `tools/gen_region_names.py` and `tools/gen_resource_nodes.py` use it to
produce committed artifacts. Separate licence question, not on this path — and now a more
interesting one than it was, because four of those tables are location lists for exactly the
collectibles the destroyed-actor list records as gone. See *Opportunities* below.

## Format, as derived

### File shape

```
[ uncompressed header ][ chunk ][ chunk ]...   each chunk = 49-byte preamble + zlib blob
```

### Primitives (`pioneersav/reader.py`)

Little-endian. The one non-guessable rule: **a string is an int32 length then bytes, and the
SIGN of the length is the encoding** — positive is one byte per char (latin-1), negative is
UTF-16LE at two bytes per char. The count **includes** the trailing null, which is stripped.
Zero length means empty with no bytes at all.

Reads past the end raise rather than truncating; a silently short read produces a header
that parses into plausible nonsense.

### Header (`pioneersav/header.py`) — DONE

Linear, no offsets to seek by, in this order:

```
i32   save_header_type        14
i32   save_version            60   (52 and below = pre-1.0, see limits)
i32   build_version           495413
str   save_name               'Han Solo_280726-230847'
str   map_name                'Persistent_Level'
str   map_options             '?skiponboarding?ClientIdentity=...'
str   session_name            'Han Solo'
i32   play_duration_s         1151711
i64   save_datetime_ticks     639208697277810000
u8    session_visibility      0
i32   editor_object_version   40
str   mod_metadata            ''
i32   is_modded               0
str   save_identifier         'X2faPVKjX06VaRzClNv5KQ'
i32   (unnamed, 1 on every save seen)
i32   (unnamed, 1 on every save seen)
u64   save_data_hash[0]       6096361947348211065
u64   save_data_hash[1]       9325011144171762175
i32   is_creative             0
```

Then `body_offset`, where `u32 == PACKAGE_FILE_TAG (0x9E2A83C1)`. **That assertion is the
proof the whole walk is right** — every field is positional, so one wrong width silently
rereads the rest. A patch inserting a field is caught there.

**Verification:** 67 saves on the author's disk. Exact agreement on all **31** the vendored
parser can read; failure on exactly the same **36**. Those 36 are `saveVersion` 52 and older
(2021–2023) which the old parser also refuses. Same answers, same limits — nothing in this
project has ever read them.

### Chunks (`pioneersav/chunks.py`) — DONE

49-byte preamble per chunk:

```
i64  tag                  0x222222229E2A83C1  (CHUNK_TAG)
i64  max chunk size       131072
u8   compressor           3 = zlib
i64  compressed size      \
i64  uncompressed size     |  written TWICE, identically
i64  compressed size       |
i64  uncompressed size    /
[compressed size bytes of zlib]
```

Both size copies are read and compared — a free integrity check on a format with no other
one. The tag is checked **per chunk**: autosaves rewrite every few minutes, so a file torn
mid-write is routine and must fail at the tear with an offset.

**Verification:** all 31 readable saves inflate, **1,194 MB in 1.39 s**, none fail. Reference
save 2.94 MB → 44.4 MB in 0.047 s. All 9,125 chunks carry `max chunk size` 131072 with both
sizes inside it, which is checked as a self-contradiction rather than as the constant — see
the fuzzing section for why that distinction was made after the fact.

### Body layout and object headers (`pioneersav/objects.py`) — DONE

`read_body(body)` walks the inflated stream and returns levels, object headers, each object's
property block as a `(offset, length)` slice, and the three destroyed-actor lists. It does not
parse property bodies.

**Two layouts.** saveVersion **60** bodies open with an archive version header; saveVersion
**52** bodies (25 of the 31 readable saves) do not, and go straight to the grid table.
They are told apart by the header's `(0, 522, 1017)` signature at the cursor.

```
i64  body size                  len(body) - 8, self-describing
59B  archive version header     60 only: i32 0, 522, 1017, 3; 6 bytes; tag 0x80078F35;
                                str '++FactoryGame+rel-main-1.2.0'
i32  custom version count       60 only, then (16-byte GUID, i32 version) pairs -- 13 on
                                the reference save. A COUNT terminates it, not a sentinel.
i32  grid count                 7 on a 60 body, 6 on a 52 one
     per grid: str name, i32 cell size, u32 checksum, i32 cells,
               then per cell: str 25-char id, u32 checksum
i32  sub-level count            3123 on the reference save
     3123 x level record
     one more level record with NO name string -- the persistent level
i32  closing destroyed-actor table, grouped by level name
```

A level record:

```
str  name                       absent on the persistent level
i64  toc size  ; [ i32 header count ][ headers ][ destroyed-actor list ]
i64  data size ; [ i32 object count ][ object entries ]
i32  version (52 or 60)         \
i32  destroyed count ; refs      |  sub-levels only; the persistent level's place is
i32  archive-follows flag (0/1)  /  taken by the body's closing table
[ archive version header + custom versions, when the flag is 1 ]
```

All three destroyed-actor lists are **read**, not stepped over; their shapes and the measured
overlap between them are below.

**The earlier "774 KB partition table" was a misreading.** That figure measured to the
first literal `Persistent_Level\0` at body offset 774709. The grid table ends at **71578**
and is ~71 KB; 774709 is just the first sub-level holding an object whose `rootObject` is
the persistent level. Nothing searches for a literal.

**Headers and objects are two separate blocks**, both length-prefixed — parallel lists,
not interleaved.

Object header, both kinds:

```
i32  kind                       1 = actor, 0 = component
str  class path                 exposed as `typePath` on actors only, see below
str  root object                the owning level's name
str  instance name
u32  object flags               UE EObjectFlags. EIGHT distinct values over 1,243,288
                                objects; 0x280008 on actors and 0x2C0008 on
                                components -- the difference is exactly RF_DefaultSubObject
                                (0x40000), which is what identified the field.
actor:      i32 needTransform, f32 rotation[4] (x,y,z,w), position[3], scale[3],
            i32 wasPlacedInLevel
component:  str parent actor name
```

The bytes DO name a component's class. It is exposed as `class_path`, **not** `typePath`,
because `iter_objects` reads `getattr(header, "typePath", "")` and the projection's
class-based branching depends on components resolving to `""`.

Object entry:

```
i32  version                    36, 52 and 60 all occur IN THE SAME FILE -- an untouched
                                world-partition cell keeps the bytes it was written with
i32  flag                       1 on version 36/52, 0 on version 60
i32  size                       the property block's length
     size bytes                 <- the slice handed to the property stage
i32  trailing zero              version 60 only, 0 on all 39,015 of them
```

Two things measured for the property stage:

* a **version-60 payload has one extra byte before its property list** — after the
  reference lists on an actor, at the very start on a component;
* a payload ends with the property-list terminator (`"None"` + an int32), or that plus one
  more int32 (every component), or **keeps going** — 3,209 of 44,634 actors carry
  class-specific binary data after it.

The entry's size counts from immediately after itself, which is what puts version 60's
trailing int32 at the far end rather than in the head. The walk does not parse the payload; it
steps over it, and only the walk knows where the block ends, so it is the walk that refuses a
size running past it.

**The archive version header** is 26 fixed bytes — four int32s, three uint16s read as the
engine's major/minor/patch, and the changelist — then the engine branch as a length-prefixed
string, so its total varies by build: 59 bytes on `rel-main-1.2.0`, 70 on
`rel-main-anniversary-2026`. The same header opens the body and appears again between level
records, around 1,900 times on the reference save, and each is read field by field. The engine
version triple carries no label and is constant on this disk, so that reading is an
interpretation of one value. `changelist & CHANGELIST_MASK` is the build that wrote *that
record*, not the save: the first save of the reference world under build 502094 carries 279
headers on 502094 and 1,628 still on 495413, the levels the new build had not rewritten yet. So
a changelist at or below the header's `buildVersion` is ordinary, and only one *above* it — a
record from a build newer than the one that wrote the file — is a finding. It warns rather than
refuses, and runs only when the caller supplies `build_version`, since a value invented inside
the walk would only ever match itself.

**Pre-1.0 bodies.** Below saveVersion 52 every size field is an int32, and an object entry is a
bare size with no version and no flag, which is why `read_body` takes `save_version` rather
than sniffing it. Below saveVersion 30 there is no level list at all: one run of headers, one
run of entries, and nothing about levels — no count, no names, no block sizes. The level is in
each header's `root_object`, where three values occur (`Persistent_Level`,
`Persistent_Exploration`, `Persistent_Exploration_2`), and the walk groups by it, because one
flat level would have to be *called* something and any name would be wrong for the objects
rooted elsewhere. With a level list (saveVersion 30 and 36), the unnamed persistent record is
followed by one more bare destroyed-actor list before the closing one; it is read as that
record's trailer, like every other level's. Reading it as a second closing list consumes the
same bytes, and nothing in these saves tells the two apart.

**Verification:** black-box against the vendored parser on all 31 readable saves — identical
level counts, identical per-level header and object counts, identical `typePath` multisets.
Object-by-object on the reference save: `instanceName`, the actor/component split and
`position` all match, 0 differences in 44,634. The only divergence anywhere is the
persistent level's `name`, where the old parser reports `None` and this reports
`"Persistent_Level"`. The same 36 pre-1.0 saves are refused by both.

Re-measured over the whole folder on the final tree: **31 bodies, 86,403 levels and
1,243,288 objects walked in 7.77 s, with zero warnings and zero unparsed bytes** — and since the
destroyed-actor lists were decoded, *nothing is stepped over either*: every byte of every body
is now read. The reference save's 44 MB walks in **0.231 s**. `SaveBody.skipped_toc_bytes`
survives as the count of those bytes — **2,414,398 across the 31 saves, 97,250 on the reference
save** — and its name is now historical: it is a number a caller can see is nonzero, not a
confession.

#### The destroyed-actor lists — read, not skipped

**Why the save keeps them at all.** The world's collectibles are placed by the map and never
written into a save. Nothing in a `.sav` says a power slug *exists*; what it says is which
map-placed actors are **gone**. So this list is not a curiosity at the tail of a block — it is
the only record of what the player has collected, and reading it is the whole reason the block
stopped being skipped. What the projection does with it is `removed`; see §6.11, in
[save-projection.md](save-projection.md).

Three lists, in three places, and they are three different lists rather than one written thrice:

```
per level, trailing the header block, inside the TOC size:
    sub-level         [ i32 count ][ count x (str level, str actor path) ]
    persistent level  [ i32 groups ][ groups x ( str cell, i32 count, count x ref ) ]
per sub-level, in its trailer, right after the i32 version:
                      [ i32 count ][ refs ]
closing the body, the last structure there is:
                      [ i32 groups ][ groups x ( str level, [i32 count][refs] TWICE ) ]
```

The two lists per closing group are looted drop pods and crashed ships in the first, Mercer
shrines in the second — 5 and 7 on the reference save. Which shape a header block carries is
decided by *the level*, not by a flag in the file: the persistent level groups by
world-partition cell and a sub-level does not. Nothing announces that, so the reading is
checked by **landing**: after the list the cursor must sit exactly on the header block's
declared end. Over the whole folder that is **79,363 header-block lists read and 79,363 landing
exactly on the declared end, 0 mislandings** — two independent lengths, the block's and the
list's own counts, agreeing 79,363 times. 79,332 of the lists are the bare sub-level shape and
31 the grouped one, one per save.

**A level may write no count field at all.** TOC leftover over the 31 saves: **0 bytes on 7,040
levels, exactly 4 (an empty list) on 72,578, more than 4 on 6,785.** 7,032 of the zero-leftover
levels do have headers, so this is not an over-read of the headers before them; the oracle
reports zero collectables for exactly the same levels, which is what makes "the field is absent"
a measurement rather than an excuse.

**The measured overlap, which is why all three must be merged.** On the reference save 854
actors are in both the header blocks and the trailers, 5 only in the header blocks, 30 only in
the trailers, and all 12 in the closing table are already in one of the other two — **889
distinct**. Over all 31 saves two predicates hold with no exception: the header-block list is
always a subset of trailer ∪ closing (`H − T − C = 0`, 31 of 31), and **the closing table never
contributes an actor the other two lack** (`C \ (H∪T) = 0`, 31 of 31). Only the trailer list
ever contributes uniquely, 0 to 47 actors depending on the save. Merging is therefore not
belt-and-braces: picking the header-block list alone would lose up to 47 actors, and picking any
one list would lose the monotonicity below.

Eight of the 31 rows, oldest first — `H` header blocks, `T` trailers, `C` closing table, and
`T−H−C` the trailer's unique contribution:

| save | ver | H | T | C | union | cells | T−H−C |
|---|---|---|---|---|---|---|---|
| `Han solo` | 52 | 60 | 92 | 1 | 92 | 42 | 32 |
| `301025-230108` | 52 | 318 | 326 | 17 | 326 | 125 | 8 |
| `311025-200729` | 52 | 495 | 511 | 45 | 511 | 172 | 16 |
| `151125-223640` | 52 | 720 | 724 | 66 | 727 | 231 | 7 |
| `171125-235630` | 52 | 834 | 829 | 5 | 834 | 262 | 0 |
| `131225-185524` | 52 | 870 | 871 | 6 | 875 | 274 | 5 |
| `260726-212757` | 60 | 836 | 878 | 10 | 883 | 281 | 47 |
| `280726-230847` | 60 | 859 | 884 | 12 | 889 | 284 | 30 |

`H − T − C` is 0 and `C \ (H∪T)` is 0 on every one of the 31 rows, not only these eight.

**Deduplication is load-bearing, not cosmetic.** The trailer list contains *internal* duplicates
on 6 of the 31 saves — 2 on `021225-224705` and 1 on each of the five December saves — and the
oracle reproduces the same duplicates, so they are in the bytes rather than in either reader.
`SaveBody.destroyed_actors` dedups by `(cell, path)`.

**Longitudinal check across the 31 saves, ordered by `play_duration_s`.** The merged union runs
`92 … 889` and **never falls**; cells run `42 … 284` and never fall; and the actor-leaf set is
**strictly nested** — every save's set is a superset of every earlier save's, no exceptions.
That is the strongest available evidence that the list means "collected", since a playthrough
can only collect more. Two details it also settles:

* **No single list is monotone.** The header-block list alone falls **870 → 836** across the
  v52→v60 boundary and **851 → 845** within v52, and the closing table falls **66 → 5** between
  `151125-223640` and `171125-235630`. Merging the three is what produces a series that only
  rises. Measured, not assumed — and the reason no caller should read one list.
* **A ref's cell is not stable across a game update, so nothing may key on `(cell, leaf)`.**
  The `(cell, leaf)` pair set is nested at every step but one, `131225-185524` →
  `260726-212757`, the v52→v60 boundary, where 7 pairs disappear. All 7 are `BP_Ship*` crash
  sites that reappear in the same save under a different partition cell (`BP_Ship_C_5` moves
  `4WE8E45SCOKQSG5KLXDMNZY4Z` → `0O2UIH8ZOBYWRN8PY7727SVBT`). Same actors, re-keyed cell — not a
  drop, but a reason to key on the actor alone.

**Oracle parity, 31 of 31 exact.** Compared as multisets *and* index-wise per level: our
`Level.destroyed` against `collectables1`, `trailer_destroyed` against `collectables2`,
`closing_destroyed` against `dropPodObjectReferenceList` + `extraObjectReferenceList`, plus the
deduplicated unions. **Zero differences on any list on any save**, duplicates included, and the
readable/refused split unchanged — 31 readable, every other file in the folder refused by both.

**What it costs: 1–3 ms, under 1.1% of the walk.** Interleaved A/B, best of 7, against a
monkeypatched `read_destroyed_block` that jumps straight to `end`:

| save | body | read | skip | delta |
|---|---|---|---|---|
| `autosave_0` (v60) | 44.4 MB | 255.9 ms | 254.7 ms | +1.1 ms, +0.4% |
| `131225-185524` (v52) | 42.5 MB | 250.4 ms | 248.2 ms | +2.2 ms, +0.9% |
| `171125-235630` (v52) | 38.3 MB | 230.3 ms | 227.7 ms | +2.6 ms, +1.1% |

Against a full parse (~2.8 s per save, 87.1 s for the folder) that is **~0.08%**. How the
measurement can lie is worth recording: reading once and then skipping once gave 696 ms against
280 ms — a 2.5× "cost" that was entirely first-pass page-fault and allocator warm-up on a 44 MB
body. Interleaving the two orders and taking the minimum is what makes the number real.

**`ParsedSave.warnings` are unchanged by the new read**: `SaveBody.warnings` is empty on all 31
saves at both versions, and the save-level count is 0 on all 6 v60 saves and exactly 3 on each
of the 25 v52 saves — 75 in total, every one the same `mSaveData` / `mDestroyedPickups` /
`mLootedDropPods` as before.

**What is in a ref, and what a ref does not say.** Each entry is two strings: a level name and
an actor path such as `Persistent_Level:PersistentLevel.BP_Crystal_mk3_C_2146`. **There is no
class path anywhere in these lists** — see *The class-naming limit* below, which is the one real
limit on what can be said about them.

#### The class-naming limit, stated honestly

An object header carries a class path; a destroyed-actor ref carries only the instance name. So
`extract._removed_class` recovers an **approximate** class by stripping from the right —
the trailing index, then a `_UAID_<hex>` if present, then a trailing `_C` — and that is as far
as the bytes allow. The limit is not the stripping; it is that the game builds these names two
different ways and **only 32% of them spell their class out with `_C`**. The rest are
level-placed names with an instance number glued directly onto the class, with no separator.

Whether that matters depends entirely on the class, and the two cases are worth stating
separately because one of them is fine and one is not:

* **Harmless for slugs.** `BP_Crystal_mk21_23` is a `BP_Crystal_mk2` with `1` glued on and then
  an index. Strip the index and the recovered class is `BP_Crystal_mk21`, which still carries
  its `mk2`, so a prefix match files it as yellow correctly. Every slug tier survives the glue.
* **Not harmless for artifacts.** `BP_WAT1` (somersloop) and `BP_WAT2` (Mercer sphere) differ in
  **exactly the digit the game glues an instance number onto**. `BP_WAT112` cannot be assigned
  without guessing, and `BP_WAT60` fits neither prefix. That is why `REMOVED_GROUPS` marks
  somersloop and mercer_sphere `strict` — only a name that spells the class with `_C` is
  accepted — and why the remainder falls to a deliberately vague `artifact_unsplit`: **65 of the
  reference save's 98 artifacts** report there, with 6 unambiguous `BP_WAT1_C` and 27
  unambiguous `BP_WAT2_C` beside them.

**The cross-check that proves `artifact_unsplit` really does hold somersloops**, rather than
being a bucket of unknowns nobody has looked into: the reference save's 6 unambiguous
`BP_WAT1_C` names are far fewer than the somersloops the player actually holds — **11 in the
Dimensional Depot plus 4 slotted in machines**, both read from the same projection. So at least
nine of the 65 unsplit names must be somersloops. That inference rests on one game-behaviour
premise, flagged rather than buried: a somersloop can only be picked up off the map. Everything
else in the argument is a number from this disk. Longitudinally the two unambiguous runs go
0 → 6 for `BP_WAT1_C` and 0 → 27 for `BP_WAT2_C` across the playthrough, both non-decreasing.

**Census, all 31 saves: 270 distinct approximate classes and every one matched.** `other` is
empty on all 31 saves and the group sums equal the total on all 31, so `REMOVED_GROUPS` misses
nothing that occurs. Eight of the 31 rows, ending on the reference save's 889 over 284 cells:

| save | ver | total | cells | flora | pickup | slug_b | artif | shrine | crash | debris | slug_y | slug_p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `Han solo` | 52 | 92 | 42 | 32 | 6 | 35 | 1 | 1 | 0 | 1 | 10 | 6 |
| `301025-230108` | 52 | 326 | 125 | 89 | 68 | 80 | 23 | 17 | 7 | 9 | 20 | 13 |
| `311025-200729` | 52 | 511 | 172 | 108 | 97 | 110 | 63 | 54 | 16 | 15 | 26 | 22 |
| `151125-223640` | 52 | 727 | 231 | 169 | 129 | 141 | 86 | 72 | 35 | 29 | 37 | 29 |
| `171125-235630` | 52 | 834 | 262 | 184 | 159 | 150 | 94 | 78 | 49 | 43 | 46 | 31 |
| `131225-185524` | 52 | 875 | 274 | 185 | 169 | 158 | 97 | 80 | 53 | 48 | 48 | 37 |
| `260726-212757` | 60 | 883 | 281 | 185 | 170 | 159 | 97 | 80 | 55 | 51 | 49 | 37 |
| `280726-230847` | 60 | 889 | 284 | 185 | 170 | 163 | 98 | 80 | 55 | 51 | 50 | 37 |

Every one of those nine series is non-decreasing over all 31 saves, which is the same nesting
argument as above applied per group. **`artif` is every `BP_WAT*` actor**, i.e. the sum of
`somersloop`, `mercer_sphere` and `artifact_unsplit` — 6 + 27 + 65 = 98 on the reference save.
The column predates the census fix below and is kept whole rather than re-measured as three,
because the total is the number the nesting argument applies to.

Two of the nine deserve a caveat when they are quoted at a player. **`dropped_pickup`
(`FGItemPickup_Spawnable`) is not a map collectible** — it is loot the player dropped and picked
back up, so its 170 says nothing about the world. And `flora` and `debris` are cleared as a side
effect of building rather than sought, so they measure ground clearing, not exploration.

**An inconsistency the tests caught, and the fix.** The census used to be built from
`removed["counts"]`, whose keys have already had their trailing `_C` stripped by
`_removed_class`, while a `strict` group requires `name.startswith(prefix + "_C")` — so **no
strict group could ever match a census key.** `groups["somersloop"]` and
`groups["mercer_sphere"]` were absent on all 31 saves while `removed_actors("somersloop")`
happily returned its 6 entries: two halves of one returned dict disagreeing about whether the
split exists. Nothing was lost (65 + 6 + 27 = 98) and no single number was wrong, which is
exactly what made it easy to miss.

Both paths now group from the **instance names**, where the `_C` survives, so the census and the
listing agree for every group. On the reference save that reads

```
groups["somersloop"] = 6      removed_actors("somersloop")["actors"]      =  6 entries
groups["mercer_sphere"] = 27  removed_actors("mercer_sphere")["actors"]   = 27 entries
groups["artifact_unsplit"] = 65                                             65 entries
```

`test_the_census_and_the_listing_agree_for_every_group` walks every group and compares the two,
so the two paths cannot diverge again. `counts` stays in the projection as a raw class census for
diagnostics, and nothing derives a group from it.

### Properties (`pioneersav/properties/`) — DONE

The package reads the tag layouts in `tags.py`, the self-serialising structs in `structs.py`,
the values and containers in `decoder.py`, and one object's whole payload in `payload.py`.

`read_object(body, slice, actor=…)` turns one property block into `[[name, value], …]`,
which is what `extract.props()` reads. `actor` comes from the header: nothing inside a
payload says which kind of object it is.

**Payload frame.** An actor opens with its parent `ObjectReference` and a counted list of
its component children; a component opens with nothing. Object version 60 then writes one
byte before the property list (an object-reference migration flag, 0 everywhere). After the
list's `"None"` terminator come trailing bytes: 4 or 8 on an ordinary object, and much more
on **3,209** actors — 1,889 conveyor chains, 1,297 power lines, and the lightweight
buildable subsystem with 3.1 MB. All are handed on as `(extra_offset, extra_length)`; the
subsystem's are decoded by `pioneersav/lightweight.py`, the rest are skipped by that length.

**Two tag layouts**, keyed by the *object's* version, not the save's:

```
version 60 (UE5 FPropertyTag)      version 36/52 (UE4)
str  name                          str  name
...  type-name TREE                str  type
i32  size                          i32  size
u8   flags                         i32  array index
i32  array index   if flags&0x01   ...  type-specific tag data
16B  guid          if flags&0x02   u8   has property guid
                                   16B  guid            if that byte is 1
```

The type-name tree is `str name, i32 paramCount, paramCount × tree`, so a struct carries
both its name and its package as nested parameters:
`ArrayProperty(StructProperty(InventoryStack(/Script/FactoryGame)))`. UE4 keeps the same
information as fixed tag-data fields; both are normalised into one tree so there is one
value reader per type, not two.

**The flags byte is the most useful field in the format.** Three bits, each confirmed
against a value the game shows:

| bit | meaning | how it was pinned |
|---|---|---|
| `0x10` | the BoolProperty's value | size is 0 and there is nowhere else for it — this is the "16 means True" `extract.truthy` documents |
| `0x08` | the type serialises itself (raw bytes, not a nested property list) | set on `Box`, `Vector`, `Guid`, `InventoryItem`; clear on `InventoryStack`, `FeetOffset`, `FactoryCustomizationData` |
| `0x01` | a nonzero array index follows | absent tag field on v60, always present on v36/52 |

**The per-property size check is the safety net.** After reading a value the cursor must be
exactly `size` past the payload start, or it raises with the offset. A wrong width fails on
that property instead of shifting everything after it — that check is what found every one
of the format details above, including the two below.

**Two things that cost real objects to find.** The version-60 terminator is a *bare name*
with no type tree after it; reading the tree first turned the 4 trailing bytes into a string
length and broke 16,445 objects. And `InventoryItem` has two layouts that no version number in
the file tells apart, worth 87 pickups when first misread; the declared size now decides, see
*Struct layouts the bytes do not name* below.

**Struct bodies.** Decided by NAME (`NATIVE_STRUCTS`), not by the flag, because the flag is
not reliably per-element: the foliage subsystem's `mSaveData` is one MapProperty with `0x08`
set whose keys are native `IntVector` and whose values are property lists. Nine native
structs are enough for every save: `Vector` (three **doubles**, on version-52 objects too —
the width follows the writer), `Quat`, `Box` (6 doubles + a validity byte), `LinearColor`
(four floats), `Guid`, `IntVector`, `FluidBox` (one float of pipe contents),
`ClientIdentityInfo`, `InventoryItem`. `PlayerInfoHandle` and `UniqueNetIdRepl` are kept as
raw bytes on purpose. Everything else — 37 more struct names — is a nested property list.
Add nothing to the native table on the strength of a name: `Vector_NetQuantize` looks like it
belongs and does not — the map markers' `Location` is 135 bytes of tagged `X`/`Y`/`Z`
DoubleProperties. UE4's tag layout has no native-serialise bit at all, and synthesising one for
version 36/52 would skip plain property lists such as a creature spawner's `SpawnData`.

**Value shapes.** The values keep the shapes the projection was written against, quirks
included, so changing one is a silent behaviour change downstream:

* `BoolProperty` yields the raw byte — 16, 1 or 0 — rather than a `bool`, because
  `extract.truthy` reads it;
* `ByteProperty` yields `[enumName or None, value]`: `mLastAutoSaveId` is `[None, 2]`,
  `mGamePhaseCosts[].gamePhase` is `['EGamePhase', 'EGP_MidGame']`, and readers take `[-1]`;
* a struct serialised as a nested property list yields `[values, types]`;
* `InventoryItem` yields `[itemClassPath, state]` with the class as a bare path string, because
  `_accumulate_inventory`'s `ref_class` resolves a path string and not a reference object;
* `Int8Property` yields raw `bytes`; nothing in the projection reads one.

**Census on the reference save** — 44,634 objects and **82,660 top-level properties**, which
between them declare **208,548 property tags at every depth**, because a struct's fields and a
container's elements are tags too. Counting every tag, 19 distinct types occur:

```
StructProperty 76214   ArrayProperty  19849   SetProperty     1552   Int8Property     167
ObjectProperty 47670   FloatProperty   9508   UInt32Property  1550   StrProperty       26
IntProperty    41135   BoolProperty    5140   MapProperty      677   TextProperty      26
                       ByteProperty    3784   Int64Property    656   InterfaceProperty 19
                                              EnumProperty     567   DoubleProperty    18
                                                                     NameProperty       4
                                                                     SoftObjectProperty 1
```

The **top-level** 82,660 are a different distribution and only 18 types, and it is the one
that says what the layer spends its time on: `ObjectProperty` 34,139, `StructProperty` 22,301,
`ArrayProperty` 18,260, `IntProperty` 17,091, `FloatProperty` 6,472, `BoolProperty` 1,652,
`ByteProperty` 743, `EnumProperty` 554, `Int8Property` 167, then twelve types in single or
double figures. `DoubleProperty` occurs **only** nested — never as a top-level property.

The object split behind those numbers: **23,821 actors and 20,813 components** in 3,124
levels, the actors covering **184 distinct class paths**.

`SoftObjectProperty` is three strings (package, asset, sub-path), not a two-string
reference — the one occurrence declares 76 bytes and only adds up with the third.

**Verification.** All 31 readable saves compared property by property against the vendored
parser, both parsers in one process, one subprocess per save: **1,243,288 objects, 2,269,824
properties, and zero objects whose property names or order differ.** Values: identical on
2,168,837, differing on 100,987, and **every difference is one of five classes with a stated
cause** — none is unexplained, and none is in a field the projection reads. The classes, with
counts over all 31 saves (the reference save alone has 2,906 + 1 + 1 and no warnings):

| count | difference | why |
|---|---|---|
| 81,358 | `Item[1]`: `None` here vs `1`/`2` there, on a stack with no item state | the bytes there are a 0 flag; the vendored parser writes a constant that tracks the layout (1 on version 52/60, 2 on 36). Only element 0 is read, by `_accumulate_inventory` |
| 19,492 | plain `ByteProperty`'s enum name: `None` vs the literal string `"None"` | UE4 writes `"None"` and UE5 writes no parameter; collapsing both keeps one shape across versions in one file. `_phase_costs` takes `[-1]` |
| 75 | skipped by this parser | the three version-52 unnamed-struct containers below; each is also a `warnings` entry |
| 31 | `Guid` as two uint64s vs 16 raw bytes | the vendored parser is itself inconsistent here — uint64s inside a set, bytes inside a struct array. Nothing reads either |
| 31 | `FSoftObjectPath` sub-path: `""` vs `0` | it is a length-prefixed string that is empty on every occurrence, so the two readings are indistinguishable from data |

The 75 in row three are **the same 75 as the warnings**, one for one: three per saveVersion 52
save, named `mSaveData`, `mDestroyedPickups` and `mLootedDropPods` on
`FoliageRemovalSubsystem` and `ScannableSubsystem`. Nothing is skipped quietly.

**Two shape differences have to be normalised before values can be compared at all**, and
both are the ones `extract.struct_fields` already documents: a struct value is
`[values, propertyTypes]` here and sometimes bare `values` in the vendored parser (which
carries no types for a map-element struct), and a `propertyTypes` entry is
`[name, typeName, …]` whose tail past `typeName` is a rendering choice —
`['Item','StructProperty',1,'InventoryItem',1,'/Script/FactoryGame',0,8]` against
`['Item','StructProperty',1,'InventoryItem',['/Script/FactoryGame'],8]`. So the comparison
drops types from the value tree and then compares the `(fieldName, typeName)` sequence
separately, because stripping something and not checking it is how a cosmetic difference
hides a real one. Result: **123 properties of 2,269,824 where the type sequences differ, and
0 where a field both parsers name is given a different type.** Every one of the 123 is the
vendored parser omitting types it does not carry. Getting this wrong in the other direction
is worth recording: a first run of the comparison classified all 21,247 struct-bearing
properties as mismatching, and the second, which compared only the top-level value, buried
2,904 real `Item` differences as "unexplained" — the mismatch lives three lists deep, at
`.0.0.1.1`.

**The end-to-end check that matters:** `extract`'s whole projection run through this
instead of the vendored parser is identical in every field except `lightweight_counts` and
`structures`, the two that come from the undecoded trailing bytes.

**Cost, measured in one fresh process** on the reference save (2.94 MB on disk, 44.4 MB
inflated): read + header 0.001 s, inflate 0.047 s, level and header walk 0.231 s, **all
44,634 objects' properties 1.791 s** — **2.07 s** in total, against the vendored parser's
**2.4–2.5 s** for the same work in a process of its own. The earlier note that "the time is
in the property bodies" is confirmed: they are **87%** of it, the walk 11% and inflation 2%.

**Limits, stated.** UE4's tag data for a map or a set names the element's *property* type
and not the struct behind it, so a `StructProperty` element on a version-36/52 object is
genuinely ambiguous — native or property list, no way to tell. Reading it as a property list
and keeping the result only when it lands exactly on the declared end recovers
`mItemsPickedUp`, `mActorsBuiltCount` and `mItemsManuallyCraftedCount`; it correctly refuses
`mSaveData` and the scanner's two `Guid` sets, which are skipped by declared size. That is
**three warnings per saveVersion 52 save and zero on a saveVersion 60 one**, in data nothing
above reads.

Those three have since been recovered. Before falling back to a property list, the parser offers
candidate struct types and keeps the first that lands exactly on the declared end: `IntVector`
for a map key (the foliage subsystem's `mSaveData` keys its per-cell records by world-partition
cell coordinate), then `Guid` and `Vector` for a set element (the scanner's `mDestroyedPickups`
and `mLootedDropPods`, and `FGFoliageRemoval.mRemovalLocations`). Each candidate is the type
saveVersion 60 writes out in full for the same field, so the newer format is the authority
rather than the guess. Only the key is substituted: an unnamed map *value* is always a property
list. A set is offered unframed readings first, because it writes its elements end to end,
without the struct-array header a version-36/52 array opens with.

#### Adversarial pass over the containers and the escape hatches

The module was then attacked deliberately: hand-built property blocks for the cases no save
on disk contains, plus a census over all 31 saves of every path those blocks reach. **Three
defects, all fixed, all one family** — something unreadable was skipped by the *enclosing*
container's declared length, which is not the element's length.

* An untagged map element with no reader (a container inside a container, a `Bool`, an
  `FText`) was skipped to the **map's** end. With pairs still to read, the next key then came
  out of the bytes after the map and the *next* skip dragged the cursor back onto that end —
  so the declared size balanced and the object parsed. A hand-built
  `map<int, array<Vector>>` came back as two pairs, both values `None`, the second key
  invented out of the terminator's length prefix, **no error**. `skip_unknown()` now refuses to
  move the cursor backwards. Skipping the *last* element of a container still works, because
  there the container's remaining length **is** the element's length — verified on real bytes
  by renaming the value type of the calendar subsystem's one-entry
  `mCalendarsOpenedByPlayers`.
* `FText` had the same hole: a history type other than 0xFF inside an *array* of texts
  consumed the rest of the array and the elements after it were invented
  (`[[0, 3], [5, 78]]`, a "history type" that was really a string length). Its skip now goes
  through `skip_unknown()` and inherits the guard.
* A container's element count is now bounded by the bytes left in its own block instead of a
  flat 10,000,000. The count lives *inside* the payload, so the tag's size check does not
  bound it: a count of 9,000,000 planted in a 12-byte array inside a 40 MB body read
  36,000,004 bytes of the following objects, allocated 79 MB and took **13.4 s** before the
  size check noticed. No element of a container is shorter than one byte, so the bound is
  exact. Same reasoning applied to an actor's child-reference list, where a reference is at
  least eight bytes.

**None of the three changed a value.** The same walk over all 31 saves before and after:
1,243,288 objects, 2,269,824 properties, 75 warnings, and the identical digest of every
property value (`24e7bab3f7c7768a713f1813001d5c0c`). The projection diff was unchanged by them
— 17 of the 19 keys that existed at the time, `lightweight_counts` and `structures` still the
two, both since closed.

**What the census says the containers actually hold**, which is what makes the guards
correct rather than lucky:

* Map key/value types, all 31 saves: only `ObjectProperty->Int64Property`,
  `ObjectProperty->StructProperty`, `ObjectProperty->IntProperty`,
  `StructProperty->StructProperty` and `IntProperty->IntProperty`. **A map value is never
  itself a container** — `mItemsPickedUp` looks like a map of maps and is not: its value is a
  struct, and the inner map is an ordinary tagged property inside that struct's list. Set
  elements: `UInt32Property`, `StructProperty`, `ObjectProperty`.
* **Empty containers are common and must read as empty, not absent**: 918 empty struct
  arrays (`lastEditedBy`), 862 empty `UInt32` arrays and 862 empty sets
  (`RemovedLocationLookup`), 2,531 empty maps (`BuiltPerPlayer`), 9 empty string arrays. A
  version-36/52 empty struct array still writes its inner struct tag, with size 0 — 56 of
  them — so refusing an array of structs without one is right.
* `FText` history is 0xFF on all **576** of them.
* Every `BoolProperty` declares size **0** on both tag layouts (20,609 on version 60,
  126,747 on 36/52). The value is only ever in the tag.
* **63,318** version-36/52 properties carry a nonzero array index (`Locations` on the foliage
  removal data). They arrive as repeated `[name, value]` pairs — the vendored parser does the
  same — and `props()` keeps the last.
* Version-60 tag flags seen: `0x00`, `0x08`, `0x09` (native + array index), `0x10`. Never
  `0x02`, never `0x04`, never anything above `0x10`.
* Strings: **27,648,606** read, of which **25** are negative-length UTF-16 — and all 25 are
  inside an `first_exact_fit()` guess that is then thrown away. No save on this disk holds a UTF-16
  string in a property.

Verified as already correct, and worth not re-deriving: skip-by-declared-length recovers
**in every position** — top level on both tag layouts, inside a nested struct's property
list, an array's element type, a self-serialising struct with no known layout, a struct
array — and every following property still reads. A declared size that is too small or too
large by four bytes fails on that property with its offset, in both directions. Structs
inside arrays inside maps, and arrays inside structs inside arrays, read correctly. A struct
array element whose property list has no terminator fails loudly instead of eating the next
element.

**Struct layouts the bytes do not name.** A standalone `StructProperty` declares its own size,
which makes that size a referee the decoder passes down as `exact`; an element inside a
container has only the container's end, and never gets it. Two cases use it:

* **`InventoryItem` follows no version number in the file.** An object stamped version 46 in an
  autosave written at build 416835 uses two bare references — 16 declared bytes, four empty
  strings. An object stamped version 46 in Episode 107, build 463028, uses the modern layout —
  99 declared bytes, a reference and a has-state int32, where two references cannot fit. Same
  object version, same `saveVersion` 52: somewhere between those builds the game began writing
  these records the modern way without restamping them. So where the declared size is known it
  chooses between the two readings outright — they can never land on the same byte, since the
  modern one is a reference plus 4 and the legacy one a reference plus another reference, 8
  bytes at its shortest. Only a bare container element, with no size of its own, still guesses
  from the object's version.
* **An unknown struct that is not a property list costs one property, not the save.** That
  combination is not a base-game shape; it is what mods write. `FicsItCam`'s `FICFrameRange` is
  two int64s under a cleared self-serialising flag, and reading it as a tagged property list
  takes a frame number for a string length and walks off the end of a 164 MB body. With
  `exact`, the property-list reading is refereed by the declared end and a failure becomes a
  skip plus a warning.

The skip deliberately does **not** cover the native-struct table, `InventoryItem` included:
there the declared size *chooses* between readings the parser knows, and only a record that is
neither degrades to a skip. A struct the parser claims to know and then quietly drops would hide
exactly the kind of bug the `InventoryItem` gate was.

**Two messages fixed by the truncation sweep, both in the commonest failure there is.**
Cutting two real saves at fourteen fractions of their length each put **all 28** failures on
the same read — the last chunk's preamble survives the cut, its compressed blob does not — and
`Reader._take` reported it as `read of 5131 at 974874 runs past end (978615)`: no chunk, no
shortfall, and nothing to say the file was merely mid-write. `chunks.decompress_body` now
checks the blob against the bytes left and says `chunk at 2931690 declares 4106 compressed
bytes but 4105 are left in the file, a shortfall of 1`, matching the vendored parser's
shortfall to the byte on all 28. A **negative** compressed size goes through the same guard,
because `_take` rendered that one as "read of -5 … runs past end", which is not just unhelpful
but false. Separately, a save truncated to exactly its header inflates to an **empty** body —
legitimately, there are no chunks — and `read_body` opened with an int64 read that reported
`read of 8 at 0 runs past end (0)`; it now says the body is 0 bytes and why that happens.

### One exception type (`pioneersav/errors.py`) — DONE

`ParseError` used to live in `objects.py`, so `header` and `chunks` — written earlier —
raised bare `ValueError`, and so did `Reader._take`, which is where the *commonest* failure
comes from: a walk running off the end of a file the game is rewriting. The sidecar catches
one type at the save boundary, so half of all failures fell through to its bare-`Exception`
handler and were reported as `{"error": "ValueError"}` — a class name with no offset, for a
file that is merely mid-write.

`ParseError` now lives below the reader and every layer raises it. `ValueError` is still the
base class, so nothing that caught the old behaviour broke.

Each layer's messages name the layer without being told to, which is why there is no
stage-wrapping anywhere: `header` prefixes every refusal with `saveHeaderType 14,
saveVersion 60: …`, `chunks` says `chunk at 453 …`, and the walk and the serialiser say
`at body offset 71578: …`. The header prefix is the one that matters to a player rather
than a developer: **36 of the 67 saves on this disk are pre-1.0** (`saveHeaderType` 1, 8, 9
and 10) and fail on a bounds check deep in the reader, where the useful thing to say is
"saveHeaderType 10 (only 14 is understood)" and not "read of 473655 at 138 runs past end".
`KNOWN_HEADER_TYPE = 14` is deliberately **not a gate** — the `PACKAGE_FILE_TAG` check is
still the verdict, because a patch may well bump the type without moving a field.

### Composition and the sidecar switch (`pioneersav/save.py`) — DONE

`read_full_save(path)` is the one call `extract.py` makes. It returns a `ParsedSave`
whose `levels[i].actorAndComponentObjectHeaders` and `levels[i].objects` are the parallel
lists `iter_objects` walks, each object carrying `properties` as `[name, value]` pairs.

Two things in it are decisions rather than glue:

* **The inflated body is retained.** Every `ObjectSlice` and every
  `ParsedObject.extra_offset` is an absolute index into it and nothing is copied, which is
  what keeps a 44 MB body at a fifth of a second. Dropping it would leave the 3,209 actors
  with trailing data pointing at a freed buffer, and the failure would look like a decoding
  bug rather than a lifetime bug.
* **`actorSpecificInfo` is `None` rather than empty when nothing decodes the class.**
  `_lightweight` and `_structures` read it through `getattr(obj, "actorSpecificInfo", None)`.
  It is now populated for `FGLightweightBuildableSubsystem` and `None` for the seven other
  classes carrying trailing bytes — never `[]`, because an empty list is what a decoded blob
  with nothing in it looks like, and "not decoded" must not be able to pass for that. A
  partial decode would turn an undercount into a silent one: a foundation census reporting 40
  slabs where 8,347 pieces are built reads exactly like a real answer.

An unreadable *path* is left as `OSError`, not converted. A missing or locked file is not a
save that cannot be parsed, and the sidecar reports the two differently.

**The switch.** `extract.py` resolves `SATISFACTORY_SAVPARSE` **at import**, to `own`
or `vendor`, defaulting to `vendor`. A typo raises `RuntimeError` rather than falling back,
because a silent fallback would make a parity run report perfect agreement between the
vendored parser and itself — the one wrong answer this exercise cannot afford. The
environment is inherited by `projection._run_sidecar`, so setting the variable for the MCP
server switches the parser for every save it reads.

`pioneersav`'s `warnings` are printed to **stderr** and never added to the projection. They are
worth seeing, but a projection field carrying them would make the two parsers differ for a
reason that is not a disagreement.

### The lightweight buildables (`pioneersav/lightweight.py`) — DONE

Foundations, walls, ramps, catwalks and pillars are not actors. One
`FGLightweightBuildableSubsystem` actor carries every one of them in the class-specific bytes
trailing its empty property list — 3.10 MB and 8,347 pieces on the reference save, 224,530
across the 31. This is the whole of `structures` and `lightweight_counts`.

```
int32   0                     the object's own trailer
int32   2 or 4                version of what follows
int32   classCount
per class:
    reference   the buildable class: empty level name, then the class path
    int32       instanceCount
    per instance — 162 fixed bytes, 157 at version 2, plus two reference paths:
        4 x double   rotation quaternion
        3 x double   position, world centimetres
        3 x double   scale
        reference    the paint swatch
        reference x3 empty on all 224,530 instances
        2 x 4 float  override colours, primary and secondary
        reference    empty
        uint8        0
        reference    the recipe the piece was built from
        reference    the blueprint proxy it was placed as part of, empty when placed by hand
        int32        COUNT of the type-specific data blocks that follow, 0 on most classes
        per block:
            reference      the data struct's type, e.g. BuildableBeamLightweightData
            int32          byte size of the property list that follows
            property list  tagged and "None"-terminated, kept as raw bytes
        uint8        version 4 only: with the int32 after it, one FPlayerInfoHandle
        int32        version 4 only
```

**The int32 before the blocks is a count, not a constant.** It is 0 for foundations, walls,
catwalks and railings, which is every class on a save built without beams — so it read as
"0 everywhere" for as long as no beam had been placed. A beam carries one block holding its
`BeamLength`, because a beam's length is chosen per piece and there is nowhere else to keep it.
Reading the field as a constant leaves the walk 116 bytes short on the first beam, and the next
class path fails its own length check thousands of records later. The blocks are kept as raw
bytes: each is size-prefixed, so consuming it exactly needs no tag reader, and decoding
`BeamLength` is separate work. They are appended as the record's *last* field, so every index
the record already had keeps its meaning — the version-4 player handle stays at 13 and 14 — and
as a nested list, because `extract._placed` tells a real piece from a stale slot by scanning an
instance's top-level fields for a non-empty string or a `pathName`, and a bare type path at that
level would make every stale slot look placed.

`RECORD_BYTES` is therefore a **minimum** per instance rather than the whole of one, which is
what it always was — the two reference paths were never in it either. It is used only to bound a
claimed instance count, and a bound that is too generous is still a bound.

**How this was derived, and what that leaves uncertain.** The class count and the first
instance count were read off the front and matched against the oracle's census. The record
length came from the *stride between repetitions of the recipe path*, which every instance
carries: a constant 375 bytes across 4,616 consecutive foundations. Where the fields fall
inside that stride came from a per-byte variability map over those 4,616 records — the two
colour alphas are the only non-zero floats, which places both `FLinearColor`s exactly, and one
`uint8` reads 6 on 33 records where the rest read 0, which places the trailing pair.

What no measurement here can settle is how the **always-zero runs are grouped**. The 24 bytes
after the swatch are read as three empty references; three int32 zeros would be byte-identical.
References are the safer reading — a save that populates one still parses — but that is a
reason to prefer it, not evidence for it. Only the swatch slot is ever populated: 224,357
`SwatchDesc` paths across every save on disk, exactly one per instance, and not one
`PatternDesc`, `MaterialDesc` or `SkinDesc`.

**Two versions, and the trap in them.** saveVersion 52 writes blob version **2**, saveVersion
60 writes version **4**, and the difference is exactly the trailing `(uint8, int32)` — 370
bytes per foundation against 375. Version 2 is **25 of the 31 readable saves**, the common
case rather than the legacy one. Refusing an unknown version rather than reading it as the
nearest one is what caught this: the first pass knew only version 4 and turned all 25 saves
into refusals, which the whole-folder diff reported as 25 files where the two parsers disagreed
about readability. Had it guessed instead, it would have read a 370-byte record as 375 and
desynchronised 4,000 records later.

**Verification.** The walk must consume the blob to its last byte, and does: 3,103,373 of
3,103,373 across 18 classes on the reference save, 2,605,860 of 2,605,860 across 15 on a
version-2 save. Every class path is length-prefixed, so a record read one byte short lands the
next class read on a quaternion — checked before the lengths are consumed, because a
quaternion's second int32 is `0x80000000`, a negative length, and a four-gigabyte UTF-16 read
would otherwise report the buffer size instead of the class the walk was looking for.

### The conveyor chain record, explained

Everything below survived an adversarial pass that re-ran each predicate over all 31 saves --
51,200 chains, 83,389 segments, 688,282 items -- and it cut two claims as well as confirming
these, which is why the counts are stated as they are.

**Offsets increase along the direction of travel.** The last segment starts at 0 at the chain's
**input**; the first segment's end is the **output**. So `segments[0]`, and the reference this
parser calls `first_belt`, are the *downstream* end. This is settled by geometry rather than
inference: the spline points are in the chain actor's frame with `p0` at the segment's `start`
end, and the offset-0 point sits 806 cm from a `Build_MinerMk1`/`Mk2` -- a building with only an
output connection -- on 27-29 chains per save, against 1/0/0 at the offset-`length` end. In the
other direction, chains whose offset-`length` end is within 1,200 cm of a Space Elevator,
AWESOME Sink or Trading Post -- input-only buildings -- number 3/1/1 against 0 at offset 0.
An earlier note in this file said the offsets "run backwards"; that was exactly wrong.

**The item ring's capacity is derivable from the geometry**, not stored independently:

    capacity == floor(length / 120) + 2 * segmentCount + 1

on **51,200 of 51,200** chains. It is not arithmetic-by-coincidence: `ceil(length/120)` fits
equally well until you look at the 3,282 chains whose length is an exact multiple of 120, where
it fails on all 3,282. Neither neighbour of the capacity works either -- `capacity + 1` breaks
the ring relations on 21,138 chains and `capacity - 1` on 26,399 -- and the modulus is genuinely
exercised, since 21,138 chains have `first > last`, i.e. the ring really wraps.

**The two remaining ints are the ring indices of the chain's first and last item** -- matching
the first and last segment that *actually holds items* (43,823/43,823), not `segments[0]` and
`segments[-1]`, which fail on 52 and 68 chains respectively. **`-1` is the sentinel**: 10,548
segments and all 7,377 empty chains carry `(-1, -1)`, so the identity
`(last - first) mod capacity + 1 == itemCount` holds on the 43,823 non-empty chains and returns
a nonsensical 1 on an empty one. Every index is `-1` or in `[0, capacity)`.

**The per-segment float is the part of a segment's offset range with no spline behind it** -- 0 on
80,817 of 83,389 segments, and ~200/300/400 cm at conveyor lift junctions, always at the
segment's low-offset end where no item ever sits.

**An item offset is not confined to `[0, length]`, and any consumer must clamp both ends.**
Exactly one item per chain can sit above `length` -- the one at the ring head, 40,921 of 40,921,
against 0 of the other 647,361, and never two in one chain. 844 items sit *below* 0, down to
-1024.46 cm, on 36 distinct chain instances present in every save; the pack is contiguous, so
once `itemCount` exceeds what 120 cm spacing allows the surplus hangs off the low end.

**A by-product worth recording: the belt tiers' speeds fall out of the bytes.** The head item's
overshoot scales with belt speed -- median overrun 0.734 / 1.444 / 3.294 / 5.874 cm for Mk1 to
Mk4 (n = 8,238 / 11,687 / 15,087 / 4,239), i.e. ratios of 1 : 1.967 : 4.487 : 8.000 against the
published 1 : 2 : 4.5 : 8. Mk5 is unconstrained at 6 samples.

**And a negative result, which is worth as much as the rest.** Do not build a "belt is backed up"
diagnostic on this. 40,921 chains with an overrunning head item looks like a jam signal, but
**97.94% of all 644,459 consecutive-item gaps are exactly 120.0 cm**: a belt running at full
rated throughput is packed identically to a blocked one. Nor can a pair of saves rescue it --
item displacement is recoverable only modulo 120 cm, which is why a 69-second pair on this disk
shows 29 chains apparently moving one way and 16 the other.

## Verification strategy — keep doing this

1. **Black-box parity** against the vendored parser on all 31 readable saves, and not on a
   sample: object counts, per-class census, then **every property of every object**, name by
   name and value by value, and each of the three destroyed-actor lists against the oracle's
   own spelling of it — as a **multiset** and index-wise per level, so an internal duplicate
   counts as a difference rather than washing out. A comparison that has to strip a cosmetic
   rendering difference should strip it and then compare the stripped values *and* the rendering
   separately, so that "cosmetic" is a measured claim rather than an excuse.
2. **Golden fixtures.** Committed real bytes, so the tests survive the library's removal and
   pass with no game install:
   * `save_header.bin` — 2 KiB file prefix;
   * `save_body.bin` — 4,539 bytes, a **complete** saveVersion 60 body: archive header, four
     grids, one level per object version (36/52/60), an empty level, the unnamed persistent
     record, the real closing table. Only three int32s are synthesized (body size, grid
     count, sub-level count) because those frame a smaller pick of real records;
   * `save_body_v52.bin` — 3,230 bytes, the same recipe on a saveVersion 52 save, which has
     neither an outer archive header nor per-level ones;
   * `save_properties.bin` — 26,200 bytes, 18 real property blocks verbatim, one per code
     path in the serialiser: both tag layouts, every container, every native struct, and the
     awkward ones (an `InventoryItem` carrying weapon state, `FText`, `FSoftObjectPath`, an
     enum byte beside a plain one, a self-serialising struct with no known layout).
3. **The end-to-end oracle that matters:** run the sidecar both ways and diff the resulting
   projection JSON. It must be byte-identical, or the difference must be explained.

   **Done, over all 67 files in the save folder** — `SATISFACTORY_SAVPARSE=vendor` then
   `=own`, same file, JSON compared leaf by leaf:

   * **31 readable, 36 refused, by both, the same 36.** Every refusal is reported as
     `{"error": "parse_error"}` on both sides; only the message text differs, and it has to,
     because the two parsers hit the wrong layout at different fields. (One of the 36,
     `ServerManager_V2.sav`, is not a game save at all — it opens `MSGF`. Both refuse it.)
   * **All 20 projection keys are identical on all 31 saves**, `removed` included, and
     identical means leaf by leaf: **zero leaves anywhere where the two disagree on a value,
     and none present under one parser and absent under the other.** The last two to fall were
     `lightweight_counts` and `structures` — for a while they differed on every save by being
     **empty** under the own parser (488 count keys absent, 62 zero-length lists, **224,530
     structure instances in 488 classes** unreported), which is what decoding the lightweight
     blob closed. `removed` was identical from the first run, because the two engines reach the
     same three lists by different attribute names and the projection sorts before emitting.
   * `n_objects` agrees on every save, from 29,734 to 44,643.
   * Reference figures, both parsers: `Han Solo_270726-215626` → **44,307 objects**, 435
     machines + 69 extractors + 62 generators = **566** records, **11,554** material edges,
     1,287 power edges, 6,111 interned actors, 90 building classes, `schema_version` **11**;
     `structures` = 18 classes / **8,347** instances.
     `Han Solo_280726-230847` (the reference save these notes measure) → 44,634 objects,
     438 + 70 + 62 = 570 records, 11,664 material edges, and `removed` = **889 actors over 284
     cells**.
   * `--list` over the whole folder: same 31/36 split, and **zero differences in any header
     field** of any readable save.
   * Whole-sidecar wall clock including interpreter start, summed over the 31 readable saves:
     **vendor 75.7 s, own 59.2 s** — 1.28×.

   Both parsers reachable from one process boundary is what makes this a measurement. See
   `pioneersav/save.py` and `extract.ENGINE`.

4. **Attack the awkward files, and synthesize the ones the disk does not have.** Everything
   in step 3 is the *normal* case. The saves that would break a parser are the ones nobody
   has: the whole folder is one session, one player, ASCII, unmodded. Re-run independently
   and all of it held — same 31/36 split, no header field differing anywhere, and no projection
   key differing beyond the two then outstanding — and then these, which found the two fixes
   below:

   * **Truncation, twice over.** A saveVersion 60 and a saveVersion 52 save cut at fourteen
     fractions of their length each. **All 28 land on the same read**: the last chunk's
     49-byte preamble survives the cut and its compressed blob does not. Both parsers refuse
     all 28, and the shortfall each reports agrees to the byte.
   * **Single-byte corruption.** Twelve random flips in the body of each save: all inflate
     failures, all refused by both, and this parser turns zlib's error into a `ParseError`
     where the vendored one lets `zlib.error` escape and the sidecar reports
     `{"error": "error"}`. Every flip in the first 40 header bytes: the two disagree about
     **readability** on 26 of them and it is always the same direction — the vendored parser
     gates on `saveHeaderType`/`saveVersion` being known and decodes strings as strict UTF-8,
     so a damaged cosmetic field loses it the save; this one walks the header anyway and
     decodes with `errors="replace"`, and the `PACKAGE_FILE_TAG` check is still the verdict.
     Deliberate, documented in `reader.string` and `header.KNOWN_HEADER_TYPE`, and it never
     goes the other way: there is no byte whose corruption makes this parser accept a *body*
     the vendored one rejects.
   * **Non-ASCII, synthesized.** Unreal writes an FString as ANSI when every character is
     ≤ 0x7F and as UTF-16LE with a **negative** length otherwise, so a player with an umlaut
     in their session name produces a file no save here has. Headers rebuilt with umlaut,
     Cyrillic, CJK and emoji `saveName`/`sessionName`/`mapOptions` and spliced in front of the
     real body: **both parsers return the same strings** and the same object count, and the
     value survives `--list`'s JSON identically under both engines. In the body, every
     even-length ASCII string re-declared as UTF-16 by negating its length prefix — which
     keeps every declared size in the file correct — and with 400 of them patched both parsers
     agree on every `typePath`, `instanceName`, `rootObject` and `parentActorName`; with all
     273,236 patched both refuse, at the same offset.
   * **A modded save, synthesized.** `isModdedSave`, `isCreativeModeEnabled` and a non-ASCII
     `modMetadata` JSON blob round-trip identically. The real hazard is an unrecognised name,
     synthesized by renaming a name in the body to another of exactly the same byte length so
     that every declared size still holds — see the new limit below for what that found.

5. **Fuzz it against itself, exhaustively, and count what survives.** Step 4 asks whether the
   two parsers *agree* on a damaged file. This asks a different question with no oracle in it:
   does every damaged file produce a `ParseError` with an offset, and does every file that
   still parses produce the *same answer*? Roughly 60,000 mutations of two real saves
   (`Han Solo_280726-230847`, saveVersion 60, and `Han Solo_021125-030302`, 52), each
   classified by outcome rather than eyeballed. **Nothing hung** — with the container-count
   bound from step 4 in place, no case anywhere took longer than one clean parse — and after
   the four fixes below, nothing raises anything but `ParseError`.

   What found nothing, which is the more useful half:

   * **1,750 truncation points** — every byte of the header region and 240 through the body,
     on both saves. All refused, every message carrying an offset.
   * **A torn autosave, which is the realistic shape of the failure.** The game rewrites in
     place, so a file read mid-write is a prefix of the new chunk stream followed by a suffix
     of the old one. Spliced at 14 chunk boundaries between two real autosaves of the same
     world: refused every time, by the body's own `len(body) - 8` size field, at body offset 0.
     Each chunk in such a file is individually valid — right tag, matching size copies, clean
     adler32 — so that one int64 is the only thing standing between this project and a
     confidently-reported chimera of two factories.
   * **600 single-bit flips anywhere in the chunk stream**: 599 refused. The one survivor
     landed in the unused padding bits of a deflate block and inflated to **byte-identical**
     output. Generalise that: everything the property serialiser reads sits behind a per-chunk
     adler32, so silently corrupting the *inflated* body takes a deliberate re-compression and
     not a tear. Sweeps that patch the inflated body — the two below — are therefore testing
     the parser's own arithmetic, not a failure mode a disk can produce.
   * **Every int32 and int64 the body walk reads**, in both committed body fixtures, set to 0,
     1, −1 and both int32 extremes, then the *whole walk* fingerprinted — level names, every
     header field, every slice: 1,488 refused, and of the 688 that parsed only **9** changed
     the answer, all 9 a string length reading as shorter or empty. A length that says "empty"
     is indistinguishable from an empty name; there is nothing to check.
   * **~30,000 mutations inside real property blocks** — every length field of 75 objects
     picked to cover all seven (version, actor?, has-trailing-data?) buckets, plus every byte
     of the small ones. No exception but `ParseError`, no truncated property list, and the
     only silent changes were bytes inside a property *name*, which is untestable for the same
     reason.
   * **`read_info` on all 67 files in the folder**, and every int32 in two real headers set to
     six adversarial values: 31 readable and 36 refused, unchanged, and no exception type but
     `ParseError`.

   What it found, all four now fixed and each with a test in
   `tests/test_savparse_robustness.py`:

   * **A crafted 29 KB payload of nested `StructProperty` tags raised `RecursionError`**, not
     `ParseError`. Written over one real object and run through the sidecar it came out as
     `{"error": "RecursionError", "detail": "maximum recursion depth exceeded"}` with a
     traceback on stderr and **no offset** — exactly the report `errors.py` was written to
     abolish, arriving through the one door it did not cover. `PropertyDecoder.property_list` now
     counts its own depth against `_MAX_NESTING = 32`; the deepest real list in any of the 31
     saves is **4**.
   * **A property list that terminates early was absorbed in silence.** Overwriting one
     property's name with the nine bytes that spell the `"None"` terminator made a
     `Build_ConstructorMk1` read as **2 properties instead of 13**, filing the other 1,352
     bytes as trailing data with no error and no warning; through the sidecar, **exit 0 and a
     complete projection** — 44,634 objects, 438 machines, and one constructor with no recipe
     and no inventories. Nothing looked at the payload as a whole, only one property at a
     time. Now bounded by what is measurable over all **1,243,288** objects of the 31 saves:
     no object leaves a trailer shorter than **4** bytes, and a **component** leaves exactly 4
     (5,300 of them) or 8 (562,556) and never more. Injecting the terminator into every
     component of both saves is now refused 104 of 104 times and was silent 104 of 104 before.
     An actor's trailer stays unchecked — see the limit below.
   * **The chunk preamble's `max chunk size` was read and thrown away.** Setting each byte of
     three chunk preambles to five values — 588 mutations — caught every field except that
     one: **99 undetected changes, all eight of its bytes**. It is 131072 on all 9,125 chunks
     of all 31 saves, but requiring the constant would refuse a save the day the game picks a
     different block size, so what is checked is the contradiction: a maximum smaller than the
     compressed or uncompressed size written beside it.
   * **The `PACKAGE_FILE_TAG` proof was skipped when the file ended at the header.** A save cut
     to exactly its 453-byte header returned a fully populated `SaveInfo`, and the refusal then
     came from two layers down as `read of 8 at 0 runs past end (0)` — an offset into a body
     that does not exist, which reads like a corrupt save rather than one the game has only
     started writing. That string is what a player sees beside the filename in `--list`.
     `decompress_body` also says `no chunk at N` now instead of returning an empty body.

## Opportunities found while doing this

* **Header reads need no subprocess and no decompression** — `read_info` takes a 64 KiB
  prefix. Save discovery and world grouping currently spawn the sidecar per file; they could
  run in-process.
* **`saveDataHash` is in the header** (two u64). Cache validity keys on `mtime_ns` today, so
  a rewritten-but-identical autosave invalidates. A content hash would not.
* **Decompression is not the cost** — confirmed, and now split four ways in a fresh process
  on the reference save: read + header **0.001 s**, inflate 44.4 MB **0.047 s** (2%), level
  and header walk **0.231 s** (11%), all 44,634 objects' properties **1.791 s** (87%). If
  anything here is ever optimised it is the property bodies.
* **Holding every parsed object costs 0.53 s of that 1.791 s.** Parsing each property block
  and *discarding* it takes 1.257 s; keeping all 44,634 takes 1.791 s and about 136 MB. The
  projection makes exactly one pass over the objects, so a streaming `read_full_save` would
  save a quarter of the whole parse and all of that memory. Not done: it would change the
  shape the trailing-bytes work builds on, and this is a measured opportunity rather than a
  guess.
* **The grid table is unused data.** ~71 KB of world-partition cell ids and cell sizes we
  never read (`MainGrid` at 12800 uu, `ExplorationGrid` at 20480). It is a cell→size map
  over the whole world, so it may be worth something to spatial work.
* **`EObjectFlags` is on every header** and distinguishes a default subobject from a spawned
  one. Nothing reads it; it might separate blueprint-placed from hand-placed buildings.
* **The destroyed-actor list answers "collected"; a location table would answer "remaining" —
  and that is a licence decision, not an engineering one.** Everything above is an absolute
  count: 163 blue slugs collected, 55 crash sites looted, and no denominator anywhere, because
  the world's collectibles are not in the save. `sidecar/vendor/sat_sav_parse/sav_data/`
  contains `slug.py`, `somersloop.py`, `mercerSphere.py` and `crashSites.py` — location tables
  for exactly the four kinds this list records as gone. Joining them against `removed` would
  turn *"250 slugs collected"* into *"these 12 remain, nearest at X"*, which is a different
  class of answer and the one a player actually wants.

  **The catch is the whole reason this project exists.** That data is **GPL-3.0** and it is
  *build-time* input to `tools/gen_*.py`, which is precisely the licence question the parser
  replacement is meant to close — see the deletion checklist in the *Verdict*, item 4. Using it
  for a runtime feature would deepen the dependency at the moment the rest of it is being
  removed. Written up here as a decision for the user rather than taken: nothing has been read
  out of those files, and no code joins against them.

## Limits to keep stated

* 36 of the 67 files are unreadable by both parsers. 35 are pre-1.0 saves (`saveHeaderType`
  10 × 14, 9 × 9, 8 × 12) and one, `ServerManager_V2.sav`, is not a save at all. Not a
  regression, and nothing reads them. The readable 31 split **25 at saveVersion 52 and 6 at
  60**, all `saveHeaderType` 14.
* **The own parser is not the default**, though the projection now agrees on all 20 keys.
  Flipping it changes nothing about the licence, which is what the exercise is for, so the
  flip belongs to the deletion rather than preceding it. There is a test that fails if the
  default moves.
* `session_visibility` and the two unnamed int32s are skipped, not understood. The fields
  either side verify, which is what makes skipping safe.
* The 6-byte block at body offset 20 is unconfirmed.
* The **destroyed-actor lists** are read, and the projection's `removed` key carries them out —
  but what they support is a *collected* count with no denominator. How many slugs remain needs
  the map's own table of where every slug is, which this project does not ship; see the licence
  decision under *Opportunities*. Two smaller limits ride along: which of the three lists a
  given actor lands in is **not** established (that they must be merged rather than chosen
  between is), and a ref's partition cell is not stable across a game update, so nothing may key
  on `(cell, leaf)` across saves.
* **A destroyed-actor ref names an instance, not a class**, and only 32% of those names spell
  the class out with `_C`. That is harmless for slugs and genuinely lossy for artifacts, where
  `BP_WAT1` and `BP_WAT2` differ in the digit an instance number is glued onto: 65 of the
  reference save's 98 artifacts can only be reported as `artifact_unsplit`. Full account, with
  the cross-check that proves the unsplit bucket holds somersloops, under *The class-naming
  limit* above — including the one place where `removed_actors()`'s own census and its per-group
  listing disagree.
* The level trailer's `version` (52/60) and `archive-follows` flag are read and range-checked
  but not *explained*. The flag's correlation with a following archive header is exact on all
  3,123 sub-levels, and the header's own signature is verified when it says one follows.
* The extra int32 after a version-60 payload is **always 0**, so whether it is a trailing
  field or the high half of an int64 size cannot be settled from data. It is read as a
  trailing int32 and required to be 0, which fails loudly the day that changes.
* The **trailing bytes after a property list** are decoded for all eight classes that carry
  them — `pioneersav/lightweight.py` for the foundations, `pioneersav/trailers.py` for the rest —
  and every one of the 88,097 records consumes its declared length exactly.
* **An actor's trailing bytes are now length-checked**, which closed the last silent
  truncation. The component half was always exact — 562,556 components leave exactly 8 bytes
  and 5,300 leave 4, over all 567,856 — but an actor could legitimately carry any amount, so
  nothing bounded it. With every trailing-byte class known the bound is: one of the eight, or
  a plain 4 or 8 bytes. Measured before it was written — 500,350 actors leave 4, 87,016 leave
  8, 88,066 are one of the eight, nothing else exists on this disk. It **warns** rather than
  refuses, because a patch teaching a ninth class to carry data would otherwise cost the whole
  save, which is a worse failure than the one being prevented. Measured exposure before the
  check: injecting the `"None"` terminator over a property name silently truncated 3,968 of
  3,968 actors and 0 of 104 components. Still defence in depth rather than urgent — every byte
  involved sits behind a per-chunk adler32, so only a parser misread or a deliberate forgery
  can produce it.
* An object payload's **version-60 leading byte** is read and discarded. It is 0 on every
  object of every save, so what it means is a guess and is not made.
* **`Int8Property` yields raw bytes** and **`BoolProperty` yields the raw flags byte** rather
  than an `int` and a `bool`. Both are compatibility with the vendored parser's shapes, which
  the projection was written against; neither is a claim about the right Python type.
* A struct element inside a **version-36/52 map or set** is genuinely ambiguous — the tag
  names the property type and not the struct — so it is read as a property list and kept only
  if it lands exactly on the declared end. Three per saveVersion 52 save do not and are
  skipped: `mSaveData`, `mDestroyedPickups`, `mLootedDropPods`.
* `PlayerInfoHandle` and `UniqueNetIdRepl` are kept as raw bytes deliberately, so that a
  genuinely new struct still shows up as a warning instead of hiding among them.
* **An untagged element has readers for the five map/set element types that occur and no
  others.** A container, a `BoolProperty` or a `TextProperty` as a map element would raise
  with an offset rather than be skipped, because only the *last* element of a container can
  be skipped honestly. Readers for them were deliberately **not** added: a reader that has
  never been run against real bytes is a worse outcome than a loud failure, which is the same
  reason the 16-bit scalar types are absent.
* The **UTF-16 string branch is not exercised by any save on this disk** — 25 negative
  lengths in 27.6 million strings, every one inside a guess that is discarded. The
  sign-of-the-length rule is right (it is what the game writes for non-Latin text) but it is
  verified by construction in `test_savparse_header.py`, not by data. It has since been
  verified **against the oracle** by construction too: headers rebuilt with umlaut, Cyrillic,
  CJK and emoji strings, and a body with 400 of its strings re-declared UTF-16 by negating
  the length prefix (which leaves every declared size correct), give byte-identical results
  from both parsers. That is as close to data as it gets until somebody plays with an umlaut
  in their session name.
* **An unrecognised struct name inside a version-36/52 container can still lose the whole
  save.** Version 60's flags byte has `0x08` for "this struct serialises itself", so an unknown
  native struct there becomes `struct 'X' serialises itself, kept as bytes` — measured by
  renaming `FluidBox` to `MyModBox` in a v60 save's body: 44,634 objects still read, 798
  warnings, and the vendored parser refuses the file outright. UE4's tag has no such bit. A
  top-level `StructProperty` declares its own size, and that size now referees the
  property-list reading (the `exact` path under *Struct layouts the bytes do not name*), so
  there an unknown struct costs one property and a warning. An element of an array or a map
  has only the container's end, and refereeing by it would reject every element but the last
  and skip real `SplinePointData` arrays that parse correctly; there an unknown struct is still
  read as a property list with nothing to referee it. **25 of the 31 readable saves are v52**,
  so this is the version it matters on. Nothing on disk hits it — every struct name in every
  save is either in the native table or genuinely a property list.
* Version-60 tag bits **above `0x10` are ignored, while `0x04` refuses the save**. That is
  inconsistent, and deliberately left as it is: no bit above `0x10` is set on any of the
  2,269,824 properties, and inventing a meaning for one — or refusing a save because of one —
  are both guesses. If a patch ever sets one, the property's size check is the net.
* A version-60 `BoolProperty` that declared a payload byte would **refuse the save** rather
  than read the byte, since the value is taken from the tag. Every bool in every save
  declares size 0, so this is a statement of what the parser assumes, not a known risk.

## What is left

**Nothing, and now that is a statement about bytes and not only about classes.** Every class that
writes trailing bytes is decoded, and the destroyed-actor lists — the last region of the body that
was stepped over rather than read — are read: **97,250 of the reference save's 44,376,211 body
bytes, 0.22%**, which is what takes the byte budget from 99.8% to 100%. Over the folder that is
2,414,398 bytes and 21,038 references. What is *unexplained* rather than unread is listed at the
end of the verdict below.

Who carries them, measured over all 31 saves — **88,066 of 675,432 actors, in exactly eight
classes**, and no others (the 31 saves hold 675,432 actors and 567,856 components):

| class | actors, all 31 saves | trailing bytes, all 31 saves |
|---|---|---|
| class | actors | trailing bytes | what it holds |
|---|---|---|---|
| `FGConveyorChainActor` | 50,660 | 76.6 MB | belts, splines, items |
| `Build_PowerLine_C` | 36,773 | 8.0 MB | two connections |
| `FGConveyorChainActor_RepSizeMedium` / `Large` / `Huge` | 261 / 155 / 124 | 6.9 / 9.4 / 14.1 MB | the same record |
| `FGLightweightBuildableSubsystem` | 31 — one per save | 81.9 MB | every foundation and wall |
| `BP_CircuitSubsystem_C` | 31 — one per save | 4,553 B | every power circuit |
| `BP_PlayerState_C` | 31 — one per save | 558 B | a 64-bit account id |

On the reference save that is **3,209 actors and 7,370,871 bytes**: the lightweight buildable
subsystem alone 3.10 MB, 1,889 conveyor chains 2.75 MB, their 20 rep-size variants 1.24 MB,
1,297 power lines 0.28 MB, and 131 bytes between the circuit subsystem and the player state.
Everything else — 44,634 objects less those 3,209 — leaves exactly 4 or 8 bytes.

`pioneersav/lightweight.py` reads the subsystem; `pioneersav/trailers.py` reads the other seven.
Across all 31 saves that is **224,530 foundations, 688,282 items riding on belts, 225,686 spline
points, 36,773 power lines** and one circuit list and account id per save — **88,097 records, every
one consuming its declared bytes exactly**, at both save versions.

**Decoding is lazy, and that is measured.** Reading every chain costs 0.46 s on top of a 2.10 s
parse — 22% — which no projection field touched at the time and which the schema-12 `belts` key
now pays on purpose, so `ParsedObject.actorSpecificInfo` decodes on first access and caches. A malformed trailer therefore raises inside the caller rather than at the
save boundary, which is safe because the sidecar's `except ParseError` wraps projection building
too. `_attach_trailer` is also called for actors only: running it on all 1.24 M objects, components
included, cost 5% of the parse for nothing.

**What knowing all eight bought.** An **actor's** trailer is now length-checked the way a
component's always was. Before, an actor could leave any number of bytes and nothing noticed —
which is exactly what a property list that stopped early looks like. Now an actor that is neither
one of the eight nor leaving a plain 4 or 8 bytes produces a warning naming the class and the
count. Measured first, so it is a check and not a guess: over the 31 saves, 500,350 actors leave 4
bytes, 87,016 leave 8, 88,066 are one of the eight classes, and **nothing at all is left over**.

**What the belt contents are worth.** Not throughput — the planner derives that from recipes and
clocks, which is the number worth planning against. What a chain does say is where each item
physically sits, so a backed-up line is visible directly rather than inferred from machine state
the way `factory_health` does it now. The spline points are the other find: 225,686 of them are
the actual routed path of every belt in the world, which nothing in this project has had before.

## Verdict

**Is the own parser ready to become the default? Yes — on the evidence, and the decision is
still the user's.** The acceptance test this document set is met: **20 of 20 projection keys
leaf-identical on all 31 readable saves**, no key present under one parser and absent under the
other, the same 36 files refused with the same reasons, `n_objects` equal everywhere, and 1.16×
faster end to end over the folder (vendor 78.2 s, own 67.5 s of sidecar wall). 918 tests pass
under both engines. The one thing that had blocked it — `lightweight_counts` and `structures`
coming out empty, which `graph/structure.py` and `spatial/elevation.py` would have turned into
`factory_sites` returning nothing rather than failing — is gone.

`vendor` remains the default anyway, and `test_the_sidecar_still_defaults_to_the_vendored_parser`
still fails if that moves. **Flipping it buys nothing on its own:** the licence exposure comes
from the library being *in the repository*, not from which branch of an `if` runs, so the
decision that matters is the deletion. The flip is one line, is reversible by an environment
variable, and should happen as part of that deletion rather than before it.

**What would have to be true to delete `sidecar/vendor/`:**

1. ~~The trailing bytes are decoded and the whole-folder diff reads every projection key.~~
   Done, and it now reads **20 of 20** — the destroyed-actor lists added the twentieth,
   `removed`, and it was identical on both engines from the first run.
2. `SATISFACTORY_SAVPARSE=own` becomes the default, and the tests that pin the default flip
   with it.
3. The projection JSON is re-diffed **after** the flip, over the whole folder, not sampled —
   the same measurement is the acceptance test. Note that after the deletion this diff can no
   longer be run at all, so it is worth banking a copy of the vendored parser's output for
   every save first; a committed projection per save would make the comparison repeatable
   forever, at roughly 130 KB each.
4. Someone decides the licence question separately for `sav_data/`, which is build-time input
   to `tools/gen_*.py` and is not on this path. Note that reading the destroyed-actor lists has
   raised the stakes on that one rather than lowered them: `slug.py`, `somersloop.py`,
   `mercerSphere.py` and `crashSites.py` are the location tables that would turn a collected
   count into a *remaining* one, so there is now a real feature arguing for keeping GPL data at
   the moment the rest of it is being removed. That tension is the user's to resolve, not a
   detail — see *Opportunities*.
5. Every reference to the library outside `sidecar/vendor/` is removed or reworded:
   `src/satisfactory_mcp/core/saveio/extract.py`'s two-engine switch, `tests/test_savparse_save.py`'s default pin,
   and prose in `README.md`, `DESIGN.md` and this file.
6. The deletion is the user's call. Nothing here should make it for them.

**What is still unknown about the format**, distinct from what is merely not decoded:

* Fields **inside** the trailing bytes, now that all eight classes are read. In a conveyor
  chain the list is down to **one**: the item's `state` int32, 0 on all 688,282 items. What was
  here before is now explained, and the corrections matter more than the closures — see *The
  conveyor chain record, explained* below.
  In the player state, the leading `uint8` of 241 and the id type of 6. In the lightweight
  subsystem, two things: how the always-zero runs are grouped (three empty references or three
  int32s is undecidable from any save on this disk), and
  what the second override colour is for, since every instance measured has both set to
  `[0,0,0,1]`.
* Fields read and discarded because their meaning cannot be derived from data that never
  varies: `session_visibility` and the header's two unnamed int32s, the archive header's
  6-byte block at body offset 20, per-cell grid checksums, `EObjectFlags` beyond the one bit
  that separates actor from component, an object payload's version-60 leading byte (0 on
  every object of every save), and the int32 after a version-60 payload — always 0, so
  whether it is a trailer or the high half of an int64 size is not decidable from any save on
  this disk. Each is read, range-checked where a range exists, and required to be what it has
  always been, so a patch that changes one fails loudly.
* **Why an actor lands in one destroyed-actor list rather than another.** All three are read and
  their contents agree with the oracle exactly, but the *rule* dividing them is not established:
  the header-block list is always a subset of trailer ∪ closing on 31 of 31 saves, and the
  closing table never contributes uniquely, which is enough to justify merging and nothing more.
  The same applies to the closing table's two lists per group — drop pods and ships in one,
  shrines in the other, on every save on this disk — where a class-based split is a description
  of what is there rather than a derived rule. What would settle it: a save taken immediately
  after collecting one known item, diffed against the save before.
* **Which class a destroyed actor was**, beyond what its instance name shows. The refs carry no
  class path at all, so `BP_WAT112` cannot be split from `BP_WAT1`+index by any measurement —
  see *The class-naming limit*. What would settle it: a save taken after collecting exactly one
  somersloop, since the new name would then be known by construction.
* Whether **UTF-16 strings** appear where this parser expects them. 25 of 27,648,606 strings
  read have a negative length and all 25 are inside a guess that is discarded, so the branch
  is verified by construction and against the oracle by construction, never by a save a player
  produced.
* What **tag bits above `0x10`** would mean. None is set on any of the 2,269,824 properties.
* Whether a **version-36/52 map or set of structs** can be disambiguated at all. The tag names
  the element's property type and not the struct, so three properties per saveVersion 52 save
  are skipped and warned about. This is a limit of the format as written, not of the reader.
