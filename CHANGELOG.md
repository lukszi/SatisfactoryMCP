# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow the scheme in
[docs/releasing.md](docs/releasing.md). History before 0.1.0 is not reconstructed.

## [Unreleased]

Planned as 0.2.0.

### Upgrade notes

- **BREAKING: do not downgrade after running this version.** Plans move from
  `plans/<world>.json` to a versioned op log under `plans/<world>/`. The migration runs once,
  on first access, leaves the old file in place untouched and first copies it to
  `plans/<world>/backup-v<version>/`. An older version keeps reading that old file, so it
  will not see plans created or changed after the upgrade, and anything it writes there is
  not carried back into the log.
  Do not run an older MCP server next to a newer web server.
- Rebuild the web page (`npm ci && npm run build`) or unzip the release's `static.zip`.
- `world_summary` reports `last_hard_drive_analysed` in place of `last_hard_drive_spent`: the
  save's counter names the drive analysed last, which can still be pending.
- The power ledger leaves out machines and generators on no wire, so `power_report` and the
  header can read lower draw than before; `/api/power/circuits` reports them under `off_grid`.
- Label writes answer a bad name with 400 and an unknown label with 404 (both were 409); a
  409 carries `stale`, `name_taken` and `pin` flags. Names with `/` or over 60 characters
  are refused for new labels.
- *Stage headroom* and *count biomass burners* are shared by the page and chat, in
  `settings.json` in the user data dir. A browser's own earlier value is adopted once. Chat's
  `biomass=` now defaults to that setting, and a new `settings` tool reads and writes it.
- Map generation needs `zstandard`, now in the `gen` extra: stop satisfactory-mcp, then run
  `uv sync --extra gen`. Raster caches kept by an earlier version are still reused;
  `python -m mapgen compress-cache <dir>` shrinks them about 20x.

### Added

- Plans are stored as an append-only op log with revisions, merge against the revision a
  writer saw, undo and restore. The MCP planning tools write through it and journal chat
  activity.
- Web: a planner tab with a versioned workbench that follows chat; routes for plan
  versions, merges, solves, focus and live plan events.
- Web: an inventory section (stock, containers, crates), a progress section (MAM, space
  elevator, hard drives, power shards, somersloops), a recipes codex with a header search
  box, and a supply-path trace drawn on the map for a machine or factory.
- Planner: a payback horizon per plan spreads rows over more, slower machines while the
  saved power, priced at the save's grid mix, repays them; an overclock-last switch builds a
  row one machine short. Both default from shared settings and are on `plan_factory` and
  `plan_layout`.
- Map generator: the render's raster caches are stored compressed, 0.93 GB instead of
  18.5 GB at full size, and read without inflating; the tiles are the same bytes.
  `python -m mapgen compress-cache` converts caches kept before.

### Changed

- Map renders bake the live-sun lighting by default, from `python -m mapgen renders` and from
  the Maps tab alike, so a new map can be relit for any sun. `--no-light`, or unticking
  "live sun", draws the hillshade into the colour as before. `--unlit`, the old opt-in, is
  still accepted. With the light a full-size render is budgeted at about 16 minutes more and
  needs 14.5 GB more scratch space.
- Map generator: the light's scratch, `light.cache/`, is 5.4 GB smaller at full size with
  the painted layer, because the tree crowns are written once, where the bake reads them.
  `--scratch-dir` moves it off the cache drive. It is still not compressed: nothing reads it
  after the run that wrote it.
- `pioneersav`'s submodules re-export less; the top-level `pioneersav` API is unchanged.
  `pioneersav.properties` no longer exposes `ObjectReference`, `ObjectSlice`, `ParseError`,
  `Reader`, `FIRST_MODERN_BODY` or `TAG_EXTENSIONS`; `objects` no longer lists `ParseError`,
  `save` no longer lists `PLAIN_TRAILER`, and `lightweight` and `trailers` no longer re-export
  `ObjectReference`. Import each from `pioneersav` itself or from its defining module
  (`references`, `objects`, `errors`, `reader`, `versions`, `properties.tags`,
  `properties.payload`).
- Map generator: drawing a layer skips arithmetic whose answer it already had. Planes are
  sampled without the weight sums nothing read, and the void and the water are blended only
  on the pixels they cover. A full-size render draws about 12% faster, 10 to 15 minutes of
  one core over the five layers; the tiles are the same bytes.
- Map generator: a layer is drawn on several threads, 8 by default and fewer when free memory
  is short, so the draw stage of a full-size render takes about a third as long, about an
  hour less; the tiles are the same bytes. It needs about 2 GB of memory a thread at full
  size, 3.4 GB for the painted layer. `--draw-threads 1` draws one band at a time as before.
  The render's `meta.json` records the count as `draw_threads`.
- Map generator: the light bake is about 2.7 times faster at full size on 8 workers (about
  18 minutes instead of 48) and 3.5 times on 16, with the same bytes. Each of its processes
  peaks at 1.1 GB instead of 3.9 GB, and starts numpy with one BLAS thread, which saves
  about 1.5 GB of commit apiece. It now takes up to 16 by default, as many as the free memory
  holds; the new `--light-workers` sets the number.
- Map generator: a layer's three tile trees (`unlit/`, `tiles/`, `tiles@2x/`) are cut through
  one pool of encoders, with the levels resampled on threads while it encodes. A lit layer
  at full size cuts in about a third of the time; the tiles are the same bytes. The new
  `--cut-workers` sets the encoders (default one per core, at most 24, fewer when memory is
  short). `--workers N` still sets both pools wherever their own flag is not given.
- Map generator: tile PNGs are deflated at zlib level 6 instead of `optimize=True`, about a
  seventh of the CPU for about 7% more bytes, and the light's lossless normal tiles at WebP
  effort 2 instead of 4, 2.6 times faster for about 6% more. The tiles decode to the same
  pixels; their files are not the same bytes as before.
- Map generator: a render draws all its layers in one pass over the bands. Each band's
  heights, rocks, water and meshes are composed once and every layer's style colours them,
  where each layer composed them again before; drawing all five layers takes about a third
  less time (on 8 threads, windows of the full-size sheet: 140 s against 214 s), and the
  tiles are the same bytes. A band in flight takes 0.6 GB more memory when the painted layer
  is drawn with another, and a job's progress shows one draw stage for all the layers.
- Map generator: the painted and relief styles work out the colour under the water only on
  the pixels that hold water, where they worked it out for every pixel and kept it on those.
  On bands a fifth to a half wet that part costs a third to two thirds of what it did, and
  the three styles' painters 10 to 17% less CPU; a full-size draw on 8 threads, which waits
  on memory, is no faster by the clock. The tiles are the same bytes.
- Map generator: the light's horizon march and sky view, and the sampler's resampling, run
  as loops compiled by numba, now in the `gen` extra (`uv sync --extra gen`). A full-size
  light block's horizons and sky view take about 8 s instead of 91 s, and the five layers'
  full-size draw about 22% less, about 7 minutes on 8 threads. The tiles are the same bytes.
  Without numba, or with `MAPGEN_KERNELS=numpy`, the generator runs the numpy code as before.
  The first run compiles the loops, about 3 s, and keeps them beside the code.
- Map generator: the tree crowns and the water of every style (the terrain and satellite
  water, the relief styles' and the painted style's colour under the water) are drawn by
  numba-compiled loops too. Those painters take a quarter of the time they did, and a
  full-size draw about a fifth less on one thread; the tiles are the same bytes. The first
  run compiles them, about 3 s more. Each compiled signature is now kept in a file of its
  own, so processes compiling at once, such as the test suite's workers, no longer leave a
  cache that hands one signature another's code.
- The Maps tab's render estimate follows the faster draw, light bake and cut: a full-size
  render of all five layers with the light is budgeted at about 58 minutes, the default two
  layers at about 42.
- Map generator: a lit render that keeps its raster cache keeps its finished light beside it
  (`light.kept/`: the pyramid's tiles as hard links, and the default-sun terms, 3.2 GB at full
  size). A palette-only restyle that draws the same surface installs that light instead of
  baking it again, about 14 minutes less at full size, and the Maps tab budgets it so. The
  light's `meta.json` records the `key` it was baked under; the tiles are the same bytes.
- Map generator: each band is drawn in pieces of 512 columns, several pieces at once on the
  draw's threads (`--draw-columns` sets the width). On 8 threads the draw's peak memory falls
  from about 15 GB to about 2 GB, so free memory no longer cuts the thread count, and the five
  layers draw in about 30% less time over the densest water of the full-size sheet. The tiles
  are the same bytes at any piece width (with the fixed-order sums below).
- Map generator: each band goes on to its layers' tiles as soon as it is drawn, and the light
  bakes a row of blocks as soon as the surface holds the rows it reads, while the draw goes
  on. No layer's sheet is held whole, in memory or in a file: a full-size run of five layers
  needs 16 GB less scratch, and the Maps tab's disk check no longer counts it. The tiles and
  the light are the same bytes. A light kept before this is baked again once.
- Map generator: `--gpu` runs the light's horizon march and sky view as CUDA kernels. It
  needs the new `gpu` extra (CuPy and its NVRTC) and an NVIDIA driver, and a run where they
  cannot work is refused at once with exit code 2. The CPU path stays the default, and the
  tiles are the same bytes either way; a full-size light block's ground horizons take about
  3 s instead of 8.
- Map generator: a render whose light scratch another running render holds is refused with
  exit code 11 and the reason on stdout (it was exit 1 on stderr). The render sidecar's
  `cliff_geometry.placements_dropped` counts the passable `CliffPillar_03` as `excluded_mesh`
  (376 placements) instead of under `no_geometry`.
- Map generator: a pixel's colour channels are summed in one fixed order, elementwise,
  instead of by BLAS: the luminance under the painted style's tone shoulder, OKLab both ways,
  the water classes' mouth blends, the crown and layer colour transfers and the artwork's
  luma; and the rock tops' and coral specks' 3 × 3 mean no longer keeps a running sum. A map
  is now the same bytes at any width, in any column pieces and on any number of threads, and
  its colours no longer depend on the BLAS library. Once, about 0.01% of a full-size painted
  map moves by a level or two, and fewer pixels of the other styles; a 2048 map moves about 500
  painted pixels in each tile tree, a few dozen relief and about ten terrain and satellite.
  This shares the one version up every rendered map style takes for the fixes below.
- Map generator: the open sea's bed is solved by conjugate gradients whose dot products are
  summed in a fixed order, where scipy's took them from BLAS and its bits followed the
  number of BLAS threads. The maps are the same bytes, and the solve takes about 2 s less.

### Deprecated

- The map generator scripts `tools/gen_map_renders.py`, `gen_map_image.py`,
  `gen_world_heightmap.py`, `gen_paint_layers.py` and `check_map_fill.py` are now shims for
  `python -m mapgen <command>` and warn when run. They will be removed in 0.3.0.

### Fixed

- Chat saves merge against their base revision and across renames.
- A recalled plan's pinned logistics are journalled with its solve.
- The page resyncs plans and chat activity after a lost or overflowed event stream, and
  pushes writes against the revision the user saw.
- Trace: a factory trace is seeded by label, so a building name cannot win; flows into
  alternate-recipe groups stay on the trace card.
- Search drops locked recipes on the server when spoilers are off.
- Progress hides capability research in an unopened MAM tree.
- Recipe icons are probed once instead of one 404 per item.
- The world power ledger is the sum of its circuits; unwired machines no longer eat headroom.
- Standing Biomass Burners count their 30 MW; generators on no wire have their own list.
- Fluid buffers count towards stock, so the piles add up to the containers holding them.
- Building and hand-craft recipes show amounts per build, not a per-minute figure.
- Chat and the planner quote one net power figure; a recalled plan keeps its objective.
- An old save with no phase pointer no longer marks every Space Elevator phase complete.
- Retired MAM nodes are not listed or counted; item and building ids read as names.
- The Overview action list shows only machines needing action, counted across factories.
- The map and chat name an unnamed cluster the way Detect does, not by its building class.
- Detect numbers its suggestions like the map when filters hide clusters, and a guessed
  name prefers a made item to an ore. A name written after a rename in the same tab no
  longer fails as stale; a world switch mid-detect shows the new world's clusters only.
- Factories: the need-action list leaves paused machines out, name and rename errors show
  under the field, and an old deep link after a rename shows one back link.
- Map clicks work again after a trace is closed; trace run tooltips are escaped.
- Leaving a factory rename unfinished no longer freezes the dashboard or side panel.
- A malformed `%` escape in the address no longer stops the page from loading.
- Power circuit names follow a factory rename without a reload.
- A label holding `/` can be renamed or forgotten; a rename writes the label before
  repointing plans and reports any plan it could not move.
- Files written by a newer version are refused, not overwritten; a plan exporting power
  under any spelling stores it as `MW`.
- `npm run typegen` takes the server port as an argument or from `SATISFACTORY_WEB_PORT`.
- `python -m mapgen renders` no longer overwrites a map the registry lists: a run into its
  folder, through a junction or link too, is refused and names it. `--renders-name` writes
  beside it, and `--overwrite-in-use` replaces it anyway.
- Map generator: a mesh or Titan raster that does not read back after it was written stops
  the render with exit code 7, where it was dropped while the sidecar still recorded it. A
  cache the run cannot delete is named. `compress-cache` without the `gen` extra prints the
  fix instead of a traceback.
- The Maps tab's render estimate follows the "live sun" box, counts the light cache's
  scratch space against the free disk, and times a lit render only from an earlier lit one.
- A render that fails deletes its light scratch too, and the next lit run removes what a
  killed run left. On Windows, a run that would share the scratch of a render already
  drawing is refused at the start, where it used to fail after the slow preparation.
- `python -m mapgen compress-cache --to` on the source cache or its folder deleted the raw
  planes and then reported the cache "left as it was". A `--to` that is the source, holds it
  or lies inside it, through a junction or link too, is now refused before anything is
  written. Raw planes left by an interrupted run are removed only once they match their
  bands, and the report says whether a cache was converted in place or copied.
- Full-size (32768) map renders no longer draw a faint line along water edges every 256
  rows, in every layer and in the live-sun light: each band of rows is now drawn 16 rows
  past its edges instead of 8, enough for the water edge's blur. Smaller sizes are unchanged.
- Tree crowns on the game-painted map are placed from each pixel's own centre, so they no
  longer shift with where a band of rows starts. A few hundred crown pixels of a 2048 map,
  and 0.02% of a full-size one, change once where a crown's edge decides whether it shows.
- A lit render's light no longer depends on which layers it draws or in which order. Drawn
  first, the painted layer put its sea meshes into the light every layer is relit with, and
  a run without the painted layer baked the light without tree shadows. A full render of
  every layer is unchanged.
- Map generator: a triangle wider than 256 texels of a raster was dropped, which left two flat
  cliff tops open in a full-size render. It is rasterised now. The heightfield generator is
  version 6 though the field itself is unchanged, so the next render rebuilds its rock caches.
- Map generator: `CliffPillar_03` was read from the game's unused `Mesh_Old` copy, another
  shape, and 18 of its 376 placements lost their sand family. Asset paths now match a folder
  by whole names.
- Map renders take the sun term from one float32 copy, the live-sun page's own, in place of
  several copies in mixed precision. A few dozen lit pixels of a 2048 map move by one level.
  Every rendered map style is one version up for these fixes.
- Lit map renders draw the edge of the void, a pit's rim and a coast past the world's edge as
  the smooth curve the unlit colour has. The light took the void's soft edge and rim as land
  up to the field's last 1 m texel, so it drew a staircase with a light or dark rim there, and
  a lone texel with data inside the void as a dark square. The live-sun light pyramid's land
  weight changes the same way. Every rendered map style is one more version up for this.

## [0.1.0] - 2026-09-27

First tagged version. It records the state of the project at that point rather than a
change set.

### Added

- An MCP server (`satisfactory-mcp`, stdio) with 49 tools, 3 prompts and resources covering
  game data, the player's world read from local saves, stock and storage, factory detection
  and health, map queries, factory planning with an LP optimizer, and hard-drive advice.
- A local web map (`satisfactory-mcp-web`, optional `web` extra): terrain, factories, belts,
  pipes, power wiring, floors and crates, following the saves live.
- `pioneersav`, a first-party save parser run behind a subprocess boundary.
- Factory names and plans kept in the platform's user data directory.

### Fixed

- Save parsing on the anniversary build: the archive version header, the lightweight
  record's trailing count, `InventoryItem` layouts and a fourth conveyor-chain variant
  (thanks @ledairain, #1 and #2).

[Unreleased]: https://github.com/lukszi/SatisfactoryMCP/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lukszi/SatisfactoryMCP/releases/tag/v0.1.0
