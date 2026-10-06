# Map types: the contract

> File paths are as of the commit this contract was written against; modules and tests have
> moved since, so look a name up rather than trusting its path.

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

## 3. Provenance: four axes, never one counter

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

- **`light`**, on a render drawn with the light only (`--light`, the default; §8.1): the
  light model its lighting pyramid was baked for, from `lighting/model.py`'s `light_axis()`
  (`id`, `version`, the model constants, `digest`). `versions.LIGHTS` holds the current
  version. The name of such a type ends in its label, "live sun". The sun position is a
  viewer setting, never provenance.
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
with (`terrain-hypsometric`, `satellite-biome`, `satellite-painted`, `relief-muted`,
`relief-night`). The style digest is the sha256 of the file's canonical JSON, so an edit without
a version bump still reads as a different style, and line endings cannot change it. The version
a style carries is `STYLES[id].version`.

Each style also declares a **tone**, `light` or `dark` (`STYLES[id].tone`; the renders write it
into `provenance.style.tone`). `axes.style_tone` reads the sidecar's word, else the table's, else
light, and every type on `GET /api/maps` carries it. The page's overlay colours follow it
(frontend_vision.md §19). No imagery is `plain_tone`, dark.

| Layer | Style id | Label | Name (§3.5) | Tone |
|---|---|---|---|---|
| `terrain` | `terrain-hypsometric` | terrain | Terrain | light |
| `satellite` | `satellite-biome` | satellite | Satellite | light |
| `painted` | `satellite-painted` | game-painted | Painted | light |
| `relief` | `relief-muted` | relief | Relief | light |
| `relief-dark` | `relief-night` | relief dark | Relief (dark) | dark |
| (artwork) | `artwork` | artwork | Game map | light |

### 3.3 Verdicts

Computed on every read by `domain/maps/axes.py` `freshness(axes, current)`; nothing is stored.

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
lists it. spatial-and-map.md sections 27 and 30 describe the planes. Generator version 2 added
the baked ground colour, the crown tops, the cliff families and the seabed coral carpet planes
(section 32). Version 3 adds the water bodies (`water_bodies.json`, section 33) the painted
style classes its water from, and the tree crowns (section 36) whose measured crown tops
replace version 2's estimate, so a version 1 or 2 store reads as stale.

The game-painted layer also lists two readers, `rock_families` (each rock's material family
in the direct raster: the cliff layers, and desert rock since version 2) and `titan_trees`
(the Titan forest raster), compared like any reader version. A render with `--no-titan-trees` omits the second and records its own style digest.

### 3.5 Names and order

Every type carries two names on `GET /api/maps`:

- **`title`**, what the switcher, the default picker, the Maps tab and chat call it. It is
  composed once, by `domain/maps/titles.py`, so the page and chat cannot disagree:
  1. The player's `label` when the type has one, used as it is. The generate form's optional
     name sets it on every type the job makes, **rename** in the Maps tab sets or clears it,
     and a **re-render** carries it to the new type.
  2. Otherwise the style's name, `STYLES[id].name` (the table in §3.2): "Game map",
     "Painted", "Satellite", "Terrain", "Relief", "Relief (dark)". Any artwork is "Game map";
     a style the table does not know is its label, capitalised.
  3. The build date is added, "Painted · 6 Oct", only when the switcher shows another
     unlabelled type of the same name. "Shows" means `ready` or `missing`, and ticked "in
     switcher" or the default. A type the switcher does not show is still dated beside a twin it
     does show, so the Maps tab tells the two apart. When two of them were built on one day,
     the time is added too: "Terrain · 6 Oct 14:05". The date is the type's `created`, in the
     server's local time, with English month names whatever the locale. A type with no
     `created` gets no date.
  4. The default gets " ★" after all of that, a label included.
- **`name`**, the technical name from the axes: `{style} · {renderer} · data {cl}/hf v{n}`,
  e.g. "terrain · PCHIP r5 · data 502094/hf v5", and "artwork · ESRGAN r2 · data 502094" for
  the artwork, then the light. Two types whose names would be equal add their size. The page
  shows it, with the size, in the switcher row's tooltip and under the title in the Maps tab.

Order: style, then data (newest changelist, then highest heightfield version), then recipe,
then style version, so older data drawn with a newer renderer sorts below newer data.

---

## 4. Presets

`domain/maps/presets.py`. A job names a preset and enumerated options; every argument is built
from a whitelist and every path is chosen by the server.

| Preset | Command | Options |
|---|---|---|
| `render` | `gen_map_renders.py --game G --field data/local/heightmap --out-dir data/local/maps --renders-name <job> --size S [--layer L]… [--kernel-only] [--no-top] --light\|--no-light [--no-titan-trees] [--cache-dir data/local/maps/_cache/<S> --keep-direct] [--restyle]` | `layers` ⊆ terrain, satellite, painted, relief, relief-dark (default the first two); `size` ∈ 1024…32768; `recipe` current or kernel-only; `top`; `light` (default true, §8.1); `titan_trees`; `keep_cache`; `restyle` |
| `artwork` | `gen_map_image.py --game G --out-dir data/local/maps/<id> [--enhance] [--no-tiles-2x]` | `enhance` (only with a Vulkan GPU), `tiles_2x` |
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

- `--force` goes only to the input presets; a map job always writes a new folder. Its types
  are registered before it starts but hold no tiles yet, so the generator's refusal to write
  over a registered type (exit 10) lets it through without `--overwrite-in-use`.
- `gen_map_renders.py --size` gained 2048 and 1024, the preview sizes: a 1024 terrain render
  took 189 s here, of which the geometry sweep and the direct raster are most.
- `--cache-dir` puts `direct.cache/` and `top.cache/` where a later run at the same size and
  build reuses them. The runner passes it when the job ticks "keep the raster cache", or when a
  cache for that size already exists. `DELETE /api/maps/cache` clears it.
- **`restyle`** is the palette-only path. It is refused (400) unless `direct.cache`, `top.cache`
  and `meshes.cache` under `_cache/<S>` all hold a sidecar, which only a render that kept its
  cache leaves; `GET /api/maps` lists those sizes as `cached_sizes`. The generator's `--restyle`
  checks the stamps (size, sub-samples, build) itself and exits 9 rather than rebuilding a
  raster, so a palette change never turns into a full render. The plan drops the sweep, direct
  and top stages: one full-size layer is prep plus draw and cut, about 8.5 min against about
  37 min for a full two-layer render (§4.3), both without the light. With the light, the
  default, a restyle bakes it again, budgeted at 10 min at full size, because the raster cache
  does not keep it (§8.1). A restyle's history row is kept apart from full renders' when
  scaling the next estimate.
- The one write outside `data/local` is the pre-existing one: `--enhance` downloads the
  upscaler into the user cache folder once; the form says so.

### 4.1 Whether generation can run

`can_generate` on `GET /api/maps`, each check under a millisecond: the `gen` extra importable
(`find_spec`), `tools/` present (a source checkout), a game install found, and for renders the
heightfield present. `vulkan` says whether a Vulkan device exists: `core/gpu.py` loads the
Vulkan loader, makes a bare instance and counts physical devices (0.2 s here). The served
instance asks once at start, off the event loop, and the answer is kept for the process. Without
it the form does not offer the upscaler, a regenerate of the artwork asks for the plain cut, and
`enhance: true` is refused (400). A failed check is a muted setup line on the Maps tab, never red. The fix
for a missing `gen` extra says to stop satisfactory-mcp first, because uv cannot replace the
`.exe` a running server holds; for the same reason the runner never calls `uv run`.

### 4.2 Disk

A job is refused (507) unless free space covers what it keeps, what it needs while running
(the raster caches, about 1 GB at 32768 in the zstd band store of spatial-and-map.md §39, and
with the light its cache, 14.5 GB and 5.4 GB more with the painted layer, all scaled by area)
and 2 GB more. Checked at the form, at submit and again at start.

### 4.3 Estimates

From the stage seconds of one measured full render (prep 30, sweep 36, direct 692, top 119,
draw 355 and cut 122 per layer), area-scaled, with the direct and top passes floored because
the triangles are the same at any size. Once a job of the same preset and recipe, restyle or
not and light or not, has finished, its wall time scaled by area replaces the constants, and
the form says "(from the last run)".

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
(`create_app(served=False)`) neither read nor re-adopt.

### 5.5 The `maps` event

`watch.py` `KIND_MAPS`, published through the new public `SaveWatcher.publish`, which also
holds it as the newest of its kind, so every new stream replays it and a reloaded page sees the
running job at once. Data: `{job, queued, registry_version}`. One per state change, and at most
one per two seconds of progress. Every registry write (rename, switcher tick, default, delete,
adopt, cache clear) publishes one with `job: null`.

---

## 6. The page

### 6.1 Settings → general

A `map` group with one row, **default base map**: every `ready` type by title, " (stale)" after
a stale one, then "plain". It writes `PUT /api/maps/default`. The default lives in the manifest
rather than `settings.json` (shared-settings.md §1 says why).

### 6.2 Settings → maps (`#dash=settings/maps`)

`dash/maps/settings-maps.ts`. One card per block; every confirm is inline.

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
   inputs); one box per style from `styles` (its tone as the title); size slider (preview 1024 …
   full 32768); arches and boulders; recipe; keep the raster cache; "palette only" when that size
   is in `cached_sizes`; for artwork the GPU upscale with its download note, or a line saying no
   Vulkan GPU was found; an optional name; the
   estimate line, live; **generate**, or **queue** while a job runs, disabled with the reason as
   its title.
4. **Map types**: a 64 px z0 thumbnail that opens the map on the type, the title (§3.5), id
   chip, a "default" chip, the technical name and size and folder, built date, size, the amber
   stale chip and its reason, neutral re-render and palette chips, then **set as default**,
   **re-render** / **regenerate** (queues the heightfield first when the offer needs it, and the
   render with `replaces`), **rename** (inline), **in switcher**, **delete** (inline "delete X?
   size · delete · keep", disabled on the default). Rows stack under 600 px.
5. **Inputs** (folded): heightfield, caves, rocks, paint, each with version, build and date,
   and **rebuild** where a preset exists.

### 6.3 The switcher

`map/tiles.ts` builds its modes from `app/map-types.ts`: every `ready` type ticked "in switcher", the
default first, plus whatever is on screen or named by the address, plus plain. A row shows the
title (§3.5) and an amber "older build" or "older data" after a stale one. Its tooltip is the
technical name and the size, then the stale reason. Each pyramid is HEAD-probed as before. A
registry change rebuilds the rows and re-probes; a type that has just become ready raises a
toast "<title> is ready · show".

The server does not know what a page has on screen, so a type the switcher lists only for that
reason (unticked, not the default) does not count as shown when titles are dated: it is dated
beside its ticked twin, and the twin keeps its plain title. The two still read differently.

The page opens on the fragment's `mode=` if it can be drawn, else the shared default, else the
artwork, else plain. `mode=` holds a type id; the old `mode=artwork` is read as `map`, and
`terrain` and `satellite` are still ids, so old links open what they opened. An id the registry
does not have is ignored rather than turning the map plain. `BaseMode` and `MapTileLayer` are
`string`.

---

## 7. Chat

Read-only. `settings()` adds one header line naming every `ready` type by id and by the title
the page shows (§3.5): `# base maps (show_on_map mode=): map "Game map ★" (default), …,
terrain-r3-502094 "Terrain · 5 Oct" -- stale: newer heightfield (v3 → v5)`.
`show_on_map(mode=)` takes an id from that line, `artwork` or `plain`, checks it against the
registry, and puts `mode=` in the local link; an unknown id is refused with the list.
Generation stays on the page: the runner lives in the web process, and a half-hour job deserves
a visible confirm. No tool was added.

---

## 8. Wire

| Method + path | Does | Refuses |
|---|---|---|
| `GET /api/maps` | `MapsResponse {version, default, types[] (each with title, name and tone), jobs[], can_generate (with vulkan), inputs[], disk, game_cl, unregistered[], queue_max, sizes[], styles[] {layer, style, label, tone}, cached_sizes[], plain_tone}` | 503 newer manifest |
| `GET /api/maps/estimate?preset=&layers=&size=&recipe=&top=&keep_cache=&restyle=&light=&enhance=&tiles_2x=` | `MapEstimateResponse {seconds, keep_bytes, transient_bytes, free_bytes, needs_bytes, ok, reason, measured}` | 400 bad option |
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

### 8.1 Lit layers

A layer drawn with the light (`--light`, the default; `--unlit` is the old spelling) names
its lighting pyramid in its sidecar (`_meta.light.dir`, relative to the layer, refused unless
it resolves inside `data/local`). Its z0 probe adds `X-Map-Light`, compact JSON: `{build,
max_z, unlit_max_z, params, baked_sun, model}`. `?kind=unlit` serves the unlit colour (PNG),
`?kind=nrm` and `?kind=hz` the lighting tiles (WebP); with `?v=` the light build tag they are
immutable. Any other `kind`, or a layer drawn with `--no-light` or before the light existed,
is a 404. The `render` preset takes `light`, default true since 2026-10-06, and passes
`--light`, or `--no-light` when it is false. With the light the plan adds a `light` stage
after the first layer's draw, the unlit and light trees to the estimate's kept bytes, and the
light cache (§4.2) to its bytes while running. docs/spatial-and-map.md §29 describes the
light.

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
- A type's **re-render** button queues a palette-only restyle when only its palette moved and a
  cache for its size is kept; otherwise it queues a full render.
- The artwork's re-render offer is never made for a plain (recipe 0) artwork: the upscale needs
  a Vulkan GPU, which is a choice, not an upgrade.
