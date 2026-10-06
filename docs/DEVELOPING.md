# Developing

This is the developer half of the README: how the code is laid out and tested, how the world
data gets regenerated, and where every byte of data comes from. For what the project *does*,
start at the [README](../README.md); for scope, decisions and architecture in full, read
[DESIGN.md](../DESIGN.md), which indexes the deeper documents in this directory.
Versions, branches and releases are in [releasing.md](releasing.md).

## Tests

```bash
uv sync --extra dev
uv run pytest -q                   # the default run: needs nothing but this checkout
uv run pytest -q -m integration    # the other half: needs the game and at least one save
```

The default run reads committed fixtures only, so a clone with no game install passes it in
seconds. The map generators' own tests live in `tools/mapgen/tests/`; their
[README](../tools/mapgen/README.md) has the command. Frontend checks are `npm run check` (strict `tsc --noEmit`) and `npm run build` in
`src/satisfactory_mcp/interfaces/web/frontend/` —
[the frontend README](../src/satisfactory_mcp/interfaces/web/frontend/README.md) covers the
dev loop, the layer modules, and the type story.

Architecture is enforced, not reviewed: `tests/test_architecture.py` reads the AST of every
module to prove imports run one way — `core` knows nothing, `domain` knows `core`,
`presenters` know `domain`, `interfaces` know everything.

```
src/satisfactory_mcp/
  core/       Docs.json loading, the save seam, plain-text helpers
  domain/     world state, progression, power, factories, spatial, the LP planner
              (planning/: solver, readout, analysis, layout, siting, progress, stored),
              and session/ (asks, focus, journal, pins shared by chat and the page)
  presenters/ all response formatting
  interfaces/ mcp/ (the FastMCP surface) and web/ (FastAPI + Leaflet map)
  server.py   the console entry point; no logic
  config.py   paths and environment
src/pioneersav/  the save parser, a standalone package
tools/           data generators
tools/mapgen/    the map generators, a uv workspace member (python -m mapgen)
```

`src/pioneersav` deserves its own sentence: it is a standalone, first-party package that reads
the save format across six `saveVersion`s, and the server talks to it through one subprocess
boundary, so a torn autosave or a format change cannot take the server down.

In planning signatures a PlanState is `stored`; `state`/`st` is a WorldState.

### Test suite

**Two runs.** The default run deselects `integration`; `-m integration` needs `Docs.json` and
the save folder. Two commands that each say what they need beat one that quietly does less than
it looks like it does.

**Layout.** `tests/` mirrors the code it tests: `core/`, `domain/<package>/`, `presenters/`,
`mcp/`, `web/` (one file per router), `pioneersav/`, `data/` (the committed world tables held
against the game), `tools/` (the generator scripts and the map job runner), `mapgen/` (the map
generator's own tests, until they join `tools/mapgen/tests/`), `frontend/` and `architecture/`.
Shared helpers and fixtures live in `tests/support/` and are imported as
`tests.support.<module>`; a test module never imports another test module or `conftest`, which
`tests/architecture/test_test_imports.py` enforces. Pytest runs with `--import-mode=importlib`
and the repository root on `pythonpath`, so two folders may hold files of the same name.

**Private user data.** An autouse fixture points `SATISFACTORY_USER_DATA` at the test's own
`tmp_path` and clears the cached `config` paths, so plans, labels, the activity journal, pins,
asks, advice, the page focus and the shared settings are never the reader's; the `user_data`
fixture is that root. `data_dir` and `cache_dir` are not redirected: a test that needs a
`data/local` tree builds one.

**Web tests.** A module that drives the app calls `pytest.importorskip("fastapi")` at module
scope, because the web stack is an optional extra. It gets the app through `client` (the shared
fixture world), `labelled_client`, `fresh_state_client` (a new world per request),
`stateless_client`, or `tests.support.web.client_over` for a hand-built world; none reads a
`.sav`.

**The reference world.** `tests/fixtures/save_projection.json` is the sidecar projection of one
real save, named in `tests/support/reference_world.py` and committed because the `.sav` is not.
Re-cut it from the repository root with

```bash
uv run python -m satisfactory_mcp.core.saveio.extract \
    "<saves>/Han Solo_280726-230847.sav" > tests/fixtures/save_projection.json
```

`-m` because that is how the server spawns the extractor; the sidecar's stdout is the fixture.
Re-cut from a newer save of the same world (`save_identifier` `X2faPVKjX06VaRzClNv5KQ`), never
from another one: the `projection` fixture asserts the world, and several modules in `src`
justify a decision with a number measured on this fixture. After a re-cut, expect
`tests/data/test_reference_counts.py` to fail, and update both its assertion and the comment in
`src` it names.

**The reference field.** Planner tests plan over `REFERENCE_FIELD`, a bounding box, rather than
`region:Spire Coast`. Region names are advisory: when the region layer was re-derived from the
game's own map areas, Spire Coast went from 51 nodes to 18 and every planner number moved
although the planner had not changed. The box is the bounding box of the 51 nodes the old
selector returned, widened by 1 cm on each edge so that sub-centimetre node positions cannot
fall out of it. It also holds 17 nodes (8 limestone, 5 iron, 2 copper, 2 raw quartz) that are
irrelevant to plans maximising power from crude and coal, which is why the hand-verified
numbers held unchanged. The stored `spire-coast-full` plan keeps its region selector on
purpose.

**`live` and `state`.** `state` is the committed projection, frozen; `live` is the newest save
on the machine, for tests that measure the tool's real answer. `live` skips on `SaveError`, so
a machine with the game and no save reports skips rather than errors.

**Speed.** `-n 8` is measured. On a 16-core, 32-thread machine the default set took 5.8 s at 8
workers, 7.7 s at 16 and 14.3 s at 32, and the integration set 30 s at 8 against 38 s at 32,
because every worker imports and collects the suite alone. `--dist worksteal` (29.6 s),
`--dist loadfile` (28.3 s) and `-p no:cacheprovider` were within noise of the default and were
not adopted. After a hardware change, re-measure `uv run pytest -q -n <k>` and the same with
`-m integration` for k in 4, 6, 8, 12, 16 and auto, three samples each.

**The whole-folder tests.** Three integration tests parse every save on the machine (vendor
parity, trailer arc lengths, lightweight records) and carry the `whole_folder` marker.
`conftest.py` deals them to the workers first, because xdist deals tests in collection order
and the longest ones would otherwise start last; that saves about a second per integration run.
Each fans its saves out through `tests/support/fanout.py` at half the logical CPUs: widths 4,
8, 16, 24 and 32 measured 43.9, 32.5, 22.6, 25.0 and 24.0 s for the integration set, the second
thread of a core contending rather than helping. Hoisting them while each fanned out eight wide
was slower, from oversubscription. `in_order` returns results in submission order, so per-save
messages and early stops match a serial loop; `SATISFACTORY_TEST_FANOUT` overrides the width.

**The vendor parity bank.** While `pioneersav` was being written, its acceptance test was a diff
against the vendored GPL-3.0 parser, leaf for leaf. Deleting that library destroyed the diff, so
it was banked first: `tests/fixtures/vendor_parity.json` holds, per save and per projection key,
a digest of what the vendored parser produced in its last minutes, when the two agreed on all 20
keys of all 31 saves it could read. `tests/pioneersav/test_vendor_parity.py` replays that
comparison. It catches this parser drifting from the agreement every claim in
[savparse-notes.md](savparse-notes.md) rests on; it cannot catch a fault both parsers shared,
which is why the notes also record predicates measured against the bytes.

The bank is never re-recorded: re-banking against this parser would replace an independent
measurement with its own output. Instead every later schema is filtered back to the schema-11
shape through the explicit list `POST_11_ADDITIONS`, one entry per change and annotated with its
schema; a structural guess ("drop what the bank has never seen") would also absorb a field
emitted by mistake. A change confined inside a key that is dropped whole needs no entry: schema
14's pipe actor column, schema 15's spline tangents and schema 20's belt actor column (which
also moved the tangents from column 3 to 4) all live inside `belts` or `pipes`. Schema 17's
`power` is new and listed, but who is wired to whom has been `graph["power"]` since schema 11,
so a sidecar that reordered or dropped a power edge still fails the comparison. The list is
pinned in both directions against the committed fixture, so a new top-level key with no entry,
or an entry for a key that is gone, fails on a clone with no game.

Two entries are corrections of a banked key rather than additions, and both are undone instead
of retiring `inventories` from the comparison. Schema 16 moved the Personal Storage Boxes, the
HUB container and the Blueprint Designer's contents (10,667 units over 31 item classes on the
reference save) from `machine` to `storage`; `_unfix_16` subtracts them from `storage` and adds
them back to `machine` from this parser's own `storage` rows, exactly and in integers. The cost
is stated: a misread of one of those containers moves both values together and cancels, a
blindness confined to eight containers of one key. Schema 19 moved crate contents out of
`machine` into a `crate` bucket of their own (schema 18 had added `crates` and deliberately left
them); `_unfix_19` folds them back, and because nothing is subtracted a miscounted crate still
moves the digest.

## Solver threads

Every `scipy.optimize.milp` and `linprog` call goes through `core/solverlane.run`, which runs it on
one of four daemon threads that live as long as the process. On Windows with CPython 3.13 and
SciPy 1.18, a thread that has run a HiGHS solve can spin forever while it exits, holding the GIL
and blocking every later thread start. The web server hits this when an idle AnyIO worker thread
retires: static files, `/api/*` and SSE all stop, and a py-spy dump shows the main thread in
`threading.start` beside a frameless thread holding the GIL. A loop that starts one thread per
solve reproduces it within about fifty solves; the same loop through the lanes ran 2,800 solves
without a stall. Solver threads therefore never exit before the process does, and new solver
code calls `solverlane.run` instead of the solver directly.

## Regenerating world data

You do not need any of this to use the project: every world table the server uses is committed
under `data/`. The generators exist so the tables can be rebuilt from your own installed game
after a map update, and so the map's imagery — which is the game's artwork and is therefore
**never committed** — can be produced locally. They need `uv sync --extra gen` and a
Satisfactory install. Approximate runtimes on one mid-range machine:

The map generators are one package, `tools/mapgen/`, with one command per output. Install it
with `uv sync --all-packages` and run `uv run python -m mapgen <command>`.
[Its README](../tools/mapgen/README.md) lists every command, its options and the package map.

| Command | Produces | Runtime |
| --- | --- | --- |
| `python -m mapgen heightmap` | 1 m heightfield (ground, bare terrain, top) and the rock pack → `data/local/heightmap/`, 75 MB | ~4 min |
| `python -m mapgen caves` | cave masks → `data/local/caves/` | 6 s sweep |
| `python -m mapgen paint` | landscape paint layers → `data/local/paint/`, 54 MB | ~25 s |
| `python -m mapgen renders` | terrain, satellite and painted base-map renders, with the live-sun light by default → `data/local/renders/`, 1.7 GB without the light | ~30 min without the light |
| `python -m mapgen artwork --enhance` | the game's map artwork as tiles (upscaled on a GPU) → `data/local/` | ~13.7 min |

The old entry scripts (`tools/gen_world_heightmap.py`, `gen_map_renders.py`,
`gen_map_image.py`, `gen_paint_layers.py`, `check_map_fill.py`) keep their paths as thin
shims to these commands, and every render sidecar records them as its generator. The web
map's job runner starts `python -m mapgen <command>` itself.

The other generators are scripts in `tools/`, sharing `mapgen.common` for the `--game` flag
and the `gen` extra check:

| Generator | Produces | Runtime |
| --- | --- | --- |
| `tools/gen_item_icons.py` | one PNG per item → `data/local/icons/` | ~14 s |
| `tools/gen_world_resource_nodes.py` | the node table → `data/world_resource_nodes.json` | ~4 s |
| `tools/gen_resource_nodes.py` | the served node table → `data/resource_nodes.json` | <1 s |
| `tools/gen_region_names.py` | the region-name grids → `data/region_names.json` | ~3 s |
| `tools/gen_world_collectibles.py` | the collectible table → `data/world_collectibles.json` | ~40 s for 99 saves |

Run them as `uv run --extra gen python tools/<name>.py`. Imagery and the heightfield land in
`data/local/`, which is gitignored and stays that way; the committed tables in `data/` only
change when the game's map does. `gen_resource_nodes.py` projects the world node table and
needs no game install. `gen_region_names.py` reads the map frame and the biome calibration
from `mapgen`. `gen_world_collectibles.py` is an entry point for the `tools/collectibles/`
package, which [world-collectibles.md](world-collectibles.md) explains.

`gen_item_icons.py` finds each item's `mSmallIcon` texture in `FactoryGame-Windows.utoc`
case-insensitively, because five items spell a directory differently from the container
(`Mam`, `Medkit`, `Cyberwagon`, and `Golfcart` twice). Two pixel formats occur, `PF_DXT5`
(BC3) on 634 of the 747 icons and `PF_B8G8R8A8` on 113, with no BC7; both decoders hand back
BGRA, which read as RGBA turns a copper ingot cyan instead of raising. On build 495413, 747 of
the 750 classes carrying `mForm` name an icon and all 747 decode; the other three name no
texture, so the page's text tile is their correct rendering. The default `--px 256` writes
41.1 MB of PNG; `--help` lists the smaller sides.

## Data provenance

Every world table under `data/` is a first-party extraction: facts, coordinates and identifiers
read out of a locally installed copy of the game by the generators in `tools/`, with no artwork
shipped in this repository as data — the map's imagery is generated locally into a gitignored
directory. (The README's screenshots show that imagery through the running tool; they are
documentation of this project, and the game content visible in them remains Coffee Stain's.)

Two third-party sources were used earlier and both retirements are kept on the record rather
than tidied away:

- The region layer was once traced from satisfactory.wiki.gg's Biome Map (CC BY-SA 4.0). It is
  now read from the game's own `FGMapAreaTexture` and `UFGMapArea` assets — boundaries and names
  alike — so no share-alike obligation reaches this repository.
- The resource-node table was once vendored from the MIT-licensed
  [rockfactory/satisfactory-logistics](https://github.com/rockfactory/satisfactory-logistics)
  node set. It is now read from the node actors of the game's own `Persistent_Level.umap`; the
  parity record of that replacement lives in `_meta.retired_mit_table` inside
  `data/world_resource_nodes.json`, pinned by tests.

The save parser has the same history: a GPL-3.0 library was vendored here until `pioneersav`
replaced it and it was deleted; the measured agreement between the two is banked in
`tests/fixtures/vendor_parity.json` and replayed by the test suite. No copyleft licence reaches
this repository.
