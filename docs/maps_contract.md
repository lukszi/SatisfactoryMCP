# Map types: the contract

Several pictures of one world can sit under the map at once: the game's own artwork, renders
of the 1 m heightfield, and whatever a palette experiment produces next. This file says how
they are registered, dated, generated from the page, chosen between, and served. Where it and
the code disagree, the code is wrong or this file is out of date; fix one of them in the same
commit.

Base: master at `cdcdbb2` (recipe 5, the PCHIP sampler, was the newest render). Frontend paths
are relative to `src/satisfactory_mcp/interfaces/web/frontend/src/`; backend paths to
`src/satisfactory_mcp/`.

---

## 1. Scope

| In | Out |
|---|---|
| A registry of map types in `data/local/maps/manifest.json`, adopting existing folders in place | Moving or renaming any adopted folder |
| Provenance in every generator's sidecar, as three axes: data, renderer, style | A palette editor (the colour workflow writes `palette/palettes/<id>.json` in `tools/mapgen` by hand) |
| Stale and re-render verdicts computed on read | |
| A generation runner in the web process: one job, a queue of four, re-adopted after a restart | Generating from chat |
| Settings → maps: status, job card, generate form, type list, inputs | The `@@progress` line protocol (the log is parsed, §5.3) |
| A shared default base map, and a switcher built from the registry | |
| Chat reads the registry: `settings()` lists types, `show_on_map(mode=)` links to one | |

The owner's answers to the design's fifteen questions are final and are applied throughout:
a "maps" sub-tab of Settings; one shared default in the manifest; chat read-only; one job plus
a queue of four, at below-normal priority, re-adopted after a restart; a new map is offered as
the default and the old one deleted only by a separate inline confirm; the raster cache is
opt-in per job with a clear button; adopted folders stay where they are; ids are derived and
labels editable; the switcher shows ticked types only, new ones ticked; stale maps keep being
served with an amber chip; palettes are files; paint layers are an extracted input; the
recipe-3 folder may be deleted from the Maps tab now that the PCHIP maps exist.

---

## 2. The registry

`domain/maps/registry.py`, file `data/local/maps/manifest.json`, written only by the server.

```json
{"schema": 1, "version": 4, "default": "map",
 "types": {"terrain-r4-502094": {
   "label": null, "kind": "render", "layer": "terrain",
   "generator": "tools/gen_map_renders.py",
   "dir": "renders-v2/terrain", "sidecar": "meta.json",
   "axes": {"...": "§3"}, "bytes": 842873643, "max_z": 7, "max_2x_z": 5,
   "created": 1790000000.0, "status": "ready", "origin": "adopted",
   "job": null, "replaces": null, "in_switcher": true}},
 "history": [{"job": "j20261005-130310", "preset": "render", "options": {}, "seconds": 189}]}
```

- **Ids** match `[a-z0-9][a-z0-9-]{0,39}`. The server derives them as
  `{style}-r{recipe}-{cl}` (`terrain-r5-502094`), adding `-2`, `-3` when one is taken. The
  player edits only `label`. `default`, `jobs`, `cache`, `adopt`, `estimate` and `plain` are
  never ids.
- **`dir`** is relative to `data/local` and checked on every lookup to resolve inside it, so a
  hand-edited manifest cannot point a tile route outside. A request's id is a dictionary key and
  never reaches a path.
- **`status`** is `building` while a job writes the type, `ready`, or `failed`. `missing` is
  computed on read when the z0 tile is gone. Only `ready` (and `missing`, which then 404s) is
  served.
- **`history`** keeps the last twenty finished jobs; an estimate for the next run of the same
  preset is the last one scaled by area (§4.3).
- Reading never writes. Every write holds `core/filelock.py`'s lock, re-reads, and bumps
  `version`; a write carrying another `version` is refused (409) and changes nothing. A
  manifest with a higher `schema` is refused (503) and left alone.
- The registry is cached on the manifest's `(mtime_ns, size)`, so a tile request costs one stat.

### 2.1 Adoption

When the manifest is absent, `read()` adopts in memory and the served instance's start writes
it (`ensure`). `POST /api/maps/adopt` does the same for pyramids that appeared later; the Maps
tab offers it when `unregistered` is not empty. Nothing is moved.

| Found | Registered as |
|---|---|
| `tiles/0/0_0.png` or `map.png` at the root | `map`, artwork, dir `.` |
| `renders/terrain`, `renders/satellite` | `terrain`, `satellite` |
| any other `renders*/<layer>/` whose sidecar names `tools/gen_map_renders.py`, and any `maps/<dir>/` | a derived id |

Two paths that resolve to one folder are one type, adopted under the first name: `renders` is a
junction to the newest set, so that set is `terrain` and `satellite` and is not adopted twice.
`*.incoming` and `*.retired` (an interrupted `install_pyramid`) and the input folders
(`heightmap*`, `caves`) are never types. The first adoption makes `map` the default when it
exists, which is what the page opened on before there was a registry.

On this machine on 2026-10-05 adoption found seven types: `map` (artwork, ESRGAN r2), `terrain`
and `satellite` (through the junction: recipe 5, PCHIP), `terrain-r4-502094` and
`satellite-r4-502094` (`renders-v2`), and `terrain-r3-502094` and `satellite-r3-502094`
(`renders-v1`, drawn from heightfield v3 and therefore stale).

### 2.2 Delete

`DELETE /api/maps/{id}` refuses the default ("pick another default first") and any type a queued
or running job writes or replaces. Otherwise it renames the type's folder into
`maps/_trash/<id>-<ms>/` (one rename, which cannot half-delete), forgets the type, and removes
the trash in a thread; whatever Windows still holds is retried at the next start. The artwork
at `.` moves its four entries (`tiles`, `tiles@2x`, `map.png`, `map.json`) and moves them back
if one refuses. A folder left empty is removed unless it is a junction or a link.

---

## 3. Provenance: three axes, never one counter

Every map generator writes `_meta.provenance` through
`core.gameassets.provenance.provenance_block`. The change is additive: no existing key moved,
and the tools' own staleness guards read what they always read.

```json
"provenance": {
  "schema": 1,
  "game": {"cl": 502094, "branch": "++FactoryGame+rel-main-anniversary-2026", "game_version": "1.2.4.0"},
  "inputs": {
    "heightfield":   {"cl": 502094, "generator_version": 5,
                      "planes": ["height", "prov", "water", "waterq", "density", "terrain"],
                      "digest": "sha256:…"},
    "artwork_sheet": {"cl": 502094, "reader_version": 1, "digest": "sha256:…"},
    "biome_raster":  {"cl": 502094, "reader_version": 1, "digest": "sha256:…"},
    "cliff_geometry": {"cl": 502094, "reader_version": 5}
  },
  "renderer": {"family": "render", "recipe": 5, "version": 1, "label": "PCHIP",
               "sampler": "pchip", "two_regime": true, "size_px": 4096, "subsamples": 1},
  "style": {"id": "satellite-biome", "version": 1, "label": "satellite", "digest": "sha256:…"}
}
```

- **Inputs list only what the map read.** Terrain has no `biome_raster`, so a new biome raster
  cannot make terrain stale.
- **Size is a renderer parameter, not a version.** A 4096 preview and a 32768 render of one
  recipe are one renderer.
- The artwork's block has one input, `artwork_sheet`, and `renderer.family` `artwork` with the
  enhancement recipe (0 is the plain sheet).
- Digests: the heightfield sidecar now records `files[name].sha256` and a top-level `digest`
  (sha256 over the sorted `name:sha256` lines); `caves/meta.json` and `heightmap/rocks.json`
  record the digest of their arrays; a render records the sha256 of the decoded artwork sheet
  and biome raster it drew from.

### 3.1 Where "current" lives

`core/gameassets/versions.py` holds what the server compares against without importing
`tools/` or the `mapgen` package, which a wheel does not ship: `RENDER_RECIPES` (label, sampler, `requires`, version),
`ARTWORK_RECIPES`, `STYLES`, `READER_VERSIONS`, `HEIGHTFIELD_GENERATOR_VERSION` and
`CAVES_VERSION`. The generators import these, so the two sides cannot disagree;
`tests/test_map_provenance.py` holds them to it.

### 3.2 Palettes are files

`tools/mapgen/src/mapgen/palette/palettes/<style id>.json` holds every colour a painter draws
with (`terrain-hypsometric`, `satellite-biome`, `satellite-painted`). The style digest is the sha256 of the file's
canonical JSON, so an edit without a version bump still reads as a different style, and line
endings cannot change it. The version a style carries is `STYLES[id].version`.

### 3.3 Verdicts

Computed on every read by `domain/maps/axes.py` `verdict(axes, current)`; nothing is stored.

| Verdict | When | Shown as |
|---|---|---|
| **stale: game** | the installed changelist is above any input's `cl` | amber chip "older build", tooltip "older game build (502094 → 503001)" |
| **stale: data** | an input directory's version is above the recorded one, or both digests exist and differ; or a reader version moved | amber chip "older data", "newer heightfield (v3 → v5)" or "heightfield changed" |
| **re-render available** | the current recipe's `(recipe, version)` is above the map's | neutral chip; the offer says "after rebuilding the heightfield" when the current recipe's `requires` are not met |
| **restyle available** | the style's version moved | neutral chip "newer palette" |
| incomplete | the sidecar predates `provenance` (axes inferred), or a digest is missing | the type's tooltip only |

A missing digest never makes a map stale, and a stale map keeps being served. Sidecars written
before this change are read by `axes_from_sidecar` from their legacy fields: the changelist from
the `buildVersion` pin, the planes from a table per recipe, the style inferred from the layer at
version 1, digests null.

### 3.4 Paint layers

A `paint` input reads `data/local/paint/meta.json` (`generator_version`, `cl` or a game pin,
`digest`) exactly as the heightfield does, and a map that lists `paint` goes stale on the same
rules. `python -m mapgen paint` writes it (the `paint` preset, through
`tools/gen_paint_layers.py`); only the game-painted layer
lists it. spatial-and-map.md section 27 describes the planes. Generator version 2 adds
the seabed coral carpet planes (section 28), so a version 1 store reads as stale.

### 3.5 Names and order

Picker and switcher name a type `{style} · {renderer} · data {cl}/hf v{n}`, e.g. "terrain ·
PCHIP r5 · data 502094/hf v5"; the artwork is "artwork · ESRGAN r2 · data 502094". Two types
whose names would be equal add their size. A label replaces the name in the title only; the
name is always shown under it. Order: style, then data (newest changelist, then highest
heightfield version), then recipe, then style version, so older data drawn with a newer
renderer sorts below newer data.

---

## 4. Presets

`domain/maps/presets.py`. A job names a preset and enumerated options; every argument is built
from a whitelist and every path is chosen by the server.

| Preset | Command | Options |
|---|---|---|
| `render` | `gen_map_renders.py --game G --field data/local/heightmap --out-dir data/local/maps --renders-name <job> --size S [--layer L]… [--kernel-only] [--no-top] [--cache-dir data/local/maps/_cache/<S> --keep-direct]` | `layers` ⊆ terrain, satellite, painted (default the first two); `size` ∈ 1024…32768; `recipe` current or kernel-only; `top`; `keep_cache` |
| `artwork` | `gen_map_image.py --game G --out-dir data/local/maps/<id> [--enhance] [--no-tiles-2x]` | `enhance`, `tiles_2x` |
| `heightmap` | `gen_world_heightmap.py --game G --force --out-dir data/local/heightmap` | — |
| `caves` | `… --caves --field … --caves-dir data/local/caves --force` | — |
| `rocks` | `… --rocks --field … --force` | — |
| `paint` | `gen_paint_layers.py --game G --out-dir data/local/paint` | — |

Each script is a thin shim in `tools/` that hands its arguments to one `python -m mapgen`
command (`renders`, `artwork`, `heightmap`, `caves`, `rocks`, `paint`); the code lives in
`tools/mapgen/`, whose README lists the commands. The runner starts the command
(`presets.COMMANDS`); the script paths stay because `RENDERS_GENERATOR` and
`ARTWORK_GENERATOR` in `domain/maps/axes.py` match the `_meta.generator` every sidecar
records, and a job record still names its `script`.

- `--force` goes only to the input presets; a map job always writes a new folder.
- `gen_map_renders.py --size` gained 2048 and 1024, the preview sizes: a 1024 terrain render
  took 189 s here, of which the geometry sweep and the direct raster are most.
- `--cache-dir` puts `direct.cache/` and `top.cache/` where a later run at the same size and
  build reuses them. The runner passes it when the job ticks "keep the raster cache", or when a
  cache for that size already exists. `DELETE /api/maps/cache` clears it.
- The one write outside `data/local` is the pre-existing one: `--enhance` downloads the
  upscaler into the user cache folder once; the form says so.

### 4.1 Whether generation can run

`can_generate` on `GET /api/maps`, each check under a millisecond: the `gen` extra importable
(`find_spec`), `tools/` present (a source checkout), a game install found, and for renders the
heightfield present. A failed check is a muted setup line on the Maps tab, never red. The fix
for a missing `gen` extra says to stop satisfactory-mcp first, because uv cannot replace the
`.exe` a running server holds; for the same reason the runner never calls `uv run`.

### 4.2 Disk

A job is refused (507) unless free space covers what it keeps, what it needs while running
(the raster caches, about 10.7 GB at 32768, scaled by area) and 2 GB more. Checked at the form,
at submit and again at start.

### 4.3 Estimates

From the stage seconds of one measured full render (prep 30, sweep 36, direct 692, top 119,
draw 355 and cut 122 per layer), area-scaled, with the direct and top passes floored because
the triangles are the same at any size. Once a job of the same preset and recipe has finished,
its wall time scaled by area replaces the constants, and the form says "(from the last run)".

---

## 5. The runner

`interfaces/web/mapjobs.py` `MapJobRunner`, created in `create_app` and started by the
lifespan. One asyncio task owns at most one generator.

### 5.1 Launch

`sys.executable -u -m mapgen <command> <argv>` with cwd the repository root, the source tree
and `tools/mapgen/src` on `PYTHONPATH`, stdin closed, stdout and stderr into
`data/local/maps/jobs/<job>.log`, and
`CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW` on Windows. The
job record `maps/jobs/<job>.json` is rewritten atomically on every change:
`{id, preset, options, label, script, command, argv, produces, replaces, status, created, started,
ended, pid, pid_created, exit_code, stage, stage_words, pct, eta_s, peak_rss, error_line,
last_line, estimate_s}`. `status` is `queued`, `running`, `done`, `failed`, `cancelled` or
`interrupted`.

### 5.2 The queue

Four queued at most (409 past that), one running. A queued job registers its types as
`building` at once, so its ids are reserved and shown; they are never served until finished.
On exit 0 each type's sidecar is read into the registry and the type turns `ready`; a type
with no pyramid fails the job. On failure, cancel or interruption the types are forgotten and
their folders go to the trash. Cancel runs `taskkill /T /F`, because the renders cut through a
process pool and killing only the parent orphans its workers.

### 5.3 Progress

Read by `domain/maps/jobs.py` `Progress`. A `::stage {"id": "<step>[:<layer>]", "done": f}`
line (`core/mapprogress.py`) is read first: the renders print one per draw progress line, at
the start of each layer's draw and after each layer's cut, which covers `painted` too. Every
other line falls back to the regexes over the human log lines:
`N/4521 packages` and `… rock meshes,` (sweep), `direct.cache: P%`, `top.cache: P%`,
`drawing <layer> at`, `<layer>: P% of`, `pyramid zN:` (counted against the tree's depth),
`wrote …/<layer>` and `done in`. Stage weights come from §4.3, so the percentage is of the
whole job; the ETA is shown past 3%. A preset whose lines say nothing (artwork, inputs) shows
elapsed against the estimate. `tests/test_map_runner.py` pins the regexes against a recorded
full render log (`tests/fixtures/map_render_full.log`).

`peak_rss` is the largest peak working set in the child's process tree, polled every half
second: the venv's `python.exe` is a launcher and the interpreter doing the work is its child.
A 1024 px render peaked at 4.46 GB here.

### 5.4 Restart

On start the served instance reads every job file. A job that says `running` is re-adopted when
its pid is alive and its process creation time is the one recorded (a reused pid cannot match):
the log is re-read from the start and watched to the end, the exit code read through a held
process handle. Otherwise it is `interrupted`. Queued jobs carry on. Test apps
(`create_app(tail=False)`) neither read nor re-adopt.

### 5.5 The `maps` event

`watch.py` `KIND_MAPS`, published through the new public `SaveWatcher.publish`, which also
holds it as the newest of its kind, so every new stream replays it and a reloaded page sees the
running job at once. Data: `{job, queued, registry_version}`. One per state change, and at most
one per two seconds of progress. Every registry write (rename, switcher tick, default, delete,
adopt, cache clear) publishes one with `job: null`.

---

## 6. The page

### 6.1 Settings → general

A `map` group with one row, **default base map**: every `ready` type by name, " (stale)" after
a stale one, then "plain". It writes `PUT /api/maps/default`. The default lives in the manifest
rather than `settings.json` (shared-settings.md §1 says why).

### 6.2 Settings → maps (`#dash=settings/maps`)

`maps.ts`. One card per block; every confirm is inline.

1. **Status**: "N map types · size · free", the raster cache with **clear cache**, a muted
   setup line when generation cannot run, and "found N unregistered pyramids · add".
2. **Job card**, when a job is queued, running or finished in the last hour: the ids it writes,
   preset, start time, a bar (`--accent` on `--line`), percent, stage, "about N min left", the
   last log line and a folded log. **Cancel** asks "cancel? partial output is deleted · yes,
   cancel · keep running". Done is an `ok` chip with the time; a job with `replaces` offers
   **make default**, and **make default and delete <old>** behind its own confirm. Failed is the
   one red chip, with the error line and the log. A progress event redraws only this card, so
   the form keeps what was typed.
3. **Generate a map** (folded unless there are no types): what (render, artwork, heightfield
   inputs); layers; size slider (preview 1024 … full 32768); arches and boulders; recipe; keep
   the raster cache; for artwork the GPU upscale with its download note; an optional name; the
   estimate line, live; **generate**, or **queue** while a job runs, disabled with the reason as
   its title.
4. **Map types**: a 64 px z0 thumbnail that opens the map on the type, the title (label or
   name), id chip, ★ default, the name and size and folder, built date, size, the amber stale
   chip and its reason, neutral re-render and palette chips, then **set as default**,
   **re-render** / **regenerate** (queues the heightfield first when the offer needs it, and the
   render with `replaces`), **rename** (inline), **in switcher**, **delete** (inline "delete X?
   size · delete · keep", disabled on the default). Rows stack under 600 px.
5. **Inputs** (folded): heightfield, caves, rocks, paint, each with version, build and date,
   and **rebuild** where a preset exists.

### 6.3 The switcher

`tiles.ts` builds its modes from `mapstore.ts`: every `ready` type ticked "in switcher", the
default first, plus whatever is on screen or named by the address, plus plain. A row shows the
title, the name under it when the type is renamed, and an amber "older build" or "older data"
after a stale one. Each pyramid is HEAD-probed as before. A registry change rebuilds the rows
and re-probes; a type that has just become ready raises a toast "<name> is ready · show".

The page opens on the fragment's `mode=` if it can be drawn, else the shared default, else the
artwork, else plain. `mode=` holds a type id; the old `mode=artwork` is read as `map`, and
`terrain` and `satellite` are still ids, so old links open what they opened. An id the registry
does not have is ignored rather than turning the map plain. `BaseMode` and `MapTileLayer` are
`string`.

---

## 7. Chat

Read-only. `settings()` adds one header line, `# base maps (show_on_map mode=): map (default),
…, terrain-r3-502094 -- stale: newer heightfield (v3 → v5)`. `show_on_map(mode=)` takes an id
from that line, `artwork` or `plain`, checks it against the registry, and puts `mode=` in the
local link; an unknown id is refused with the list. Generation stays on the page: the runner
lives in the web process, and a half-hour job deserves a visible confirm. No tool was added.

---

## 8. Wire

| Method + path | Does | Refuses |
|---|---|---|
| `GET /api/maps` | `MapsResponse {version, default, types[], jobs[], can_generate, inputs[], disk, game_cl, unregistered[], queue_max, sizes[]}` | 503 newer manifest |
| `GET /api/maps/estimate?preset=&layers=&size=&recipe=&top=&keep_cache=&enhance=&tiles_2x=` | `MapEstimateResponse {seconds, keep_bytes, transient_bytes, free_bytes, needs_bytes, ok, reason, measured}` | 400 bad option |
| `PUT /api/maps/default {id, version?}` | `MapsResponse` | 404 unknown, 409 not ready or stale version |
| `POST /api/maps/adopt` | `MapsResponse` | |
| `DELETE /api/maps/cache` | `{freed_bytes}` | 409 while a job runs |
| `POST /api/maps/jobs {preset, options?, label?, replaces?}` | 202 `{job}` | 400 preset or setup, 409 queue full, 507 disk |
| `GET /api/maps/jobs/{job}` | `{job, log_tail}` (200 lines) | 404 |
| `DELETE /api/maps/jobs/{job}` | `{job}`: cancels or dequeues | 404 |
| `PATCH /api/maps/{id} {label?, in_switcher?, version?}` | `MapsResponse` | 404, 409 stale version |
| `DELETE /api/maps/{id}?version=` | `MapsResponse` | 409 default, busy, in use or stale version |

Every write passes the guard (Host must be this server, Origin this page; 403 otherwise),
which `tests/test_web_maps.py` checks for each one. A 409 for a stale version is
`MapsStaleResponse {error, stale: true, version}`. `/api/maptiles/{id}/{z}/{x}/{y}` serves any
registered type; an unknown id is a 404 listing the ids, and a `building` or `failed` type
answers HEAD 204 and GET 404.

---

## 9. Verified

On this machine, 2026-10-05, the worktree's server on scratch user data and the real
`data/local` (headless Chrome at 1440 and 390):

1. The first start adopted the seven types of §2.1 and wrote the manifest; nothing moved, and
   `renders` still points at the PCHIP set.
2. A 1024 px terrain render queued from the form ran in 189 s, showed its card and progress,
   survived a page reload (the event replay), and became `terrain-r5-502094`, labelled "test
   preview", in the switcher with its name on the second line.
3. A second preview job survived a server restart mid-run: the new server re-adopted the same
   pid, followed its log and registered its type when it exited.
4. Both test maps were then deleted from the Maps tab through the inline confirm and their
   folders purged; the adopted folders were not touched.

## 10. Open

- The `::stage` protocol covers the draw and cut stages; the sweep, direct and top stages
  still come from the regexes, which stay as the fallback.
- "Restyle" queues a full render, because `--cache-dir` reuse needs a kept cache; a palette-only
  path that skips the rasters entirely is the colour workflow's to add.
- The artwork's re-render offer is never made for a plain (recipe 0) artwork: the upscale needs
  a Vulkan GPU, which is a choice, not an upgrade.
