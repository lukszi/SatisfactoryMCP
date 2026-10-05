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
  core/       Docs.json loading, the save seam, num/plural
  domain/     world state, progression, power, factories, spatial, the LP planner
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
| `python -m mapgen renders` | terrain, satellite and painted base-map renders → `data/local/renders/`, 1.7 GB | ~30 min |
| `python -m mapgen artwork --enhance` | the game's map artwork as tiles (upscaled on a GPU) → `data/local/` | ~13.7 min |

The old entry scripts (`tools/gen_world_heightmap.py`, `gen_map_renders.py`,
`gen_map_image.py`, `gen_paint_layers.py`, `check_map_fill.py`) keep their paths as thin
shims to these commands, and every render sidecar records them as its generator. The web
map's job runner starts `python -m mapgen <command>` itself.

The other generators are plain scripts in `tools/`:

| Generator | Produces | Runtime |
| --- | --- | --- |
| `tools/gen_item_icons.py` | one PNG per item → `data/local/icons/` | ~14 s |
| `tools/gen_world_resource_nodes.py` | the node table → `data/world_resource_nodes.json` | ~4 s |

Run them as `uv run --extra gen python tools/<name>.py`. Imagery and the heightfield land in
`data/local/`, which is gitignored and stays that way; the committed tables in `data/` only
change when the game's map does. `tools/gen_world_collectibles.py`, `gen_region_names.py` and
`gen_resource_nodes.py` rebuild the remaining committed tables the same way.
`gen_region_names.py` reads the map frame and the biome calibration from `mapgen`.

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
