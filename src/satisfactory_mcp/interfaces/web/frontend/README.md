# The map page

TypeScript, built by Vite into `../static/`, which is the directory `app.py` mounts at `/`.

**The built bundle is NOT committed.** `static/` is gitignored: `app.js` is a dependency's
compiled code -- Leaflet, minified -- and the repository does not carry or redistribute it.
A fresh clone therefore has no page until the build below has run once; until then the
server answers `/` with exactly that instruction (and the JSON API works regardless).
`tests/architecture/test_frontend_layout.py` enforces both halves: nothing under `static/` may ever be
tracked, and every file a build puts there carries the build banner -- build output is
exactly the kind of file someone edits in place, and the next build silently throws that
edit away.

## Building, changing anything

```
cd src/satisfactory_mcp/interfaces/web/frontend
npm ci            # exact versions from package-lock.json
npm run build     # build the page into ../static/, which app.py serves
npm run dev       # Vite on :5173, hot reload, /api proxied to :8712
npm run check     # tsc --noEmit
npm test          # Vitest over test/, coverage to coverage/lcov.info
npm run typegen   # regenerate src/api/schema.d.ts from a running server
```

The artifact is never in a diff and never in a merge: what is reviewed is `src/`, and
`static/` resolves by rebuild, always -- after pulling a frontend change, run
`npm run build` again and the served page matches the sources by construction.

Two details of `vite.config.ts` that its comments only point at. The banner is written into
each chunk in `generateBundle`, after minification, rather than through `output.banner`:
esbuild strips comments that are not legal comments, and how that pass interleaves with
Rollup's banner hook is not something the guard test should bet on. And the output names
carry no content hash, so a diff between two builds says what changed; the server's
`StaticFiles` ETag does the cache busting instead.

## The dev loop

`npm run dev` serves the page from source with hot module replacement and proxies `/api` to
whatever `satisfactory-mcp-web` is already running on `127.0.0.1:8712` — so the dev server
needs no save files, no game install and no Python of its own. Start the real server first:

```
uv run satisfactory-mcp-web      # terminal 1, :8712
npm run dev                      # terminal 2, :5173
```

**`/api/events` is Server-Sent Events, and the proxy passes it through.** This was measured
rather than assumed, because a proxy that buffers a response body turns a live stream into a
page that hangs on the first event: with the dev server up, `curl -N
http://127.0.0.1:5173/api/events` delivers `event: save` frames as the watcher emits them,
the `content-type` stays `text/event-stream`, and the live dot in the header goes green.
Vite's proxy is `http-proxy` in streaming mode and adds no compression, which is what makes
that work; if a compression plugin is ever added here, this is the thing it will break.

The page reads `/api` at absolute paths, so nothing has to be configured for dev versus
production — in production it is the same origin, in dev the proxy makes it look like one.
`SATISFACTORY_MCP_WEB` points the proxy at another server, for example a throwaway instance
rather than the one a game session is running against.

## Layout

| path | what it holds |
| --- | --- |
| `index.html` | the page; Vite's entry, built into `../static/` |
| `src/main.ts` | the entry: the FEATURES block, every map listener in its order, and boot |
| `src/**/*.css` | the page's stylesheets, one per area beside its modules; `main.ts` imports them after Leaflet's, in one fixed order, so they win on order |
| `src/app/` | page state, the fetch registry and its loader, the address bar, live events, header, rail, search, selection, settings, the world picker, the map-type registry and the side panel's shared reads |
| `src/api/` | `get()` and the writes (`client.ts`), the response shapes (`shapes.ts`) and the generated schema |
| `src/kit/` | shared building blocks: DOM and escaping, keeping focus through a redraw, formatting, vocabulary, toasts, copy, dashboard widgets |
| `src/map/` | the Leaflet map: CRS and panes, layer groups, palette, base map and lighting, regions, labels, inspector, side panel and the one highlight |
| `src/map/layercontrol/` | the folded layer control |
| `src/map/drawn/` | the drawn layers: nodes and pickups, belts and pipes, placements, crates, power wires, plan sitings |
| `src/map/floors/` | floor mode: one storey at a time, by filtering what is already drawn |
| `src/map/tools/` | the map's tools: finder, lasso, trace |
| `src/dash/` | the dashboard shell, the actions its tabs ask of it (`actions.ts`, so no tab imports the shell), and the tabs: overview, inventory, power, settings, the production graph, machine states and health |
| `src/dash/factories/` | the Factories tab, the factory detail page and renaming |
| `src/dash/world/` | the World section and its finders |
| `src/dash/planner/` | the Planner tab: plan list, workbench, result, track, history, site and the pad drag |
| `src/dash/progress/`, `src/dash/recipes/`, `src/dash/maps/` | the Progress section, the Recipes codex and the Maps settings |
| `src/chat/` | what the page and chat share: advice, asks, pins and the store behind them |
| `vite.config.ts` | where the build writes, the banner it stamps, the licence it copies out of `node_modules/leaflet/`, the dev proxy |
| `node-fs.d.ts` | the one Node module the config imports, declared by hand instead of installing `@types/node` |
| `scripts/` | `typegen.mjs` regenerates the schema; `stamp-schema.mjs` re-applies its provenance header; `lcov-posix.mjs` writes the coverage report's paths with `/` |
| `test/`, `vitest.config.ts` | the unit tests, one file per module under the same path as in `src/`, and how `npm test` runs them |

Four things about the graph are deliberate and easy to undo by accident.

`app/load.ts` imports no module that fetches, and that is the newest of the four. It used to
import all seven drawing modules and call each draw function by name; they now declare what they
want fetched through `app/registry.ts` and `app/load.ts` runs the list knowing none of the names.
The catch is that a module nothing imports is a module the build leaves out, and a registration
that never ran is a layer that is simply never fetched — no compile error, no runtime error,
just an absence. So `main.ts` names every one of them in its FEATURES block, and
`tests/architecture/test_frontend_layout.py` checks that block against the set of modules calling
`registerFetch` in both directions, plus the rule that keeps it load-bearing: `app/load.ts` and
`app/registry.ts` may not import any of them. `map/regions.ts` is the exception `app/load.ts`
still names, because `/api/regions` is geography — no world to scope it to, fetched once — so
it is in neither wave and registers nothing.

`app/state.ts` imports nothing. `map/map.ts` reads `BOOT` while it is building the map, so
anything `app/state.ts` imported would have to be evaluated before the map exists. It is also
where `BaseMode` is declared, for that reason and no other: the modes belong to what the page is
currently showing, and declaring the union beside the tile layers would make this file import
the module that fetches tiles.

`map/layercontrol/control.ts` does not import `map/labels.ts`. `batch()` used to end by calling
`declutter()` by name, which put three files in a ring — control imports labels imports layers
imports control — to say "the list has stopped changing". It now offers `onSettled`, and
`main.ts` registers the pass.

The control does not import `map/tiles.ts` either, and the same shape fixes it:
`map/layercontrol/mode-picker.ts` draws the base-map radios and `map/tiles.ts` registers what a
click on one means, through `onModePick`. The arrow can only point that way — `map/tiles.ts`
reaches the control through `map/layers.ts` already — and the seam is what keeps "which picture
is the base map" out of a widget that otherwise knows nothing about pyramids. Both pickers are
built on `map/layercontrol/radio-section.ts` and hook into the control's render through
`onDecorate`, so `control.ts` imports neither of them.

The control does not import `map/floors/floors.ts` for the third time round the same shape:
`onFloorPick` and `onFloorExit` in `map/layercontrol/floor-picker.ts` are the seam, and the
picker is drawn without knowing what a storey is. The floors module is imported by
`map/labels.ts` (the card's action), `app/fragment.ts` and `main.ts` (the address bar and the
Esc key), `app/load.ts` (a redraw replaces a layer's contents, and the floor filter is a fact
about contents) and
`map/drawn/placements.ts` (a save write changes what is built, so `/api/machines` re-asks for
the decomposition) and `dash/factories/factory-places.ts` (the floors aspect's way in) — so it
must import none of those six, and does not.

### Two ranks

The page has two ranks, they decide different things, and neither drives the other. The FETCH
rank is `Fetcher.rank` in `app/registry.ts`: one number per wave, deciding WHEN a request goes
out and so which reply lands first. The ROW rank is the `[band, slot, name]` a layer declares
when `clearedLayer()` in `map/layers.ts` creates it: it decides WHERE that layer's row sits in
the control and nothing else. A feature declares both and they are free to disagree — the node
dots are fetched first and listed late.

The row rank is not draw order either. Everything clickable shares one canvas, and that canvas
draws in the order paths were ADDED to it, a list Leaflet keeps and the rank appears nowhere in.
The control's `sortFunction` reads the rank when it rebuilds its list and nothing else does,
which is why `raiseNodeDots` in `map/drawn/markers.ts` exists and why no band order could
replace it.

Leaflet is the `leaflet` npm package pinned to **1.9.4** — the exact version that used to sit
in `static/vendor/leaflet.js` — and it is compiled into the bundle together with its own
stylesheet. Its three icon PNGs are inlined as data URIs, so the built page makes no network
request to anything but this server: no CDN, no fonts, no map tiles anyone else hosts. That
is a licence and privacy posture, not a preference, and `npm run build` is expected to keep
it. If a dependency ever emits an `assets/` directory of its own, check what is in it.

The licence half of that posture has two ends, and the repository is only one of them. The
repo redistributes no Leaflet at all — the bundle is untracked, which is the point of not
committing it. But a *build* compiles Leaflet in, and BSD-2-Clause requires the notice to
travel with that binary form for whoever ever distributes one, so every build carries it:
`vite.config.ts` copies `node_modules/leaflet/LICENSE` — the npm package is the source of
truth, there is no vendored copy to drift — into `static/vendor/LEAFLET-LICENSE`, the page
names it in a comment, and the bundle's own banner states what is compiled in.

## Module notes

The reasons behind a few modules, kept here so the modules can stay code. Each module names
its section.

### The fetch registry

`app/registry.ts` is where a feature says what it wants fetched and what to do with the
answer. Nothing in it is imported at runtime: everything that draws imports it, so anything it
imported would be evaluated before all of them, and both of its imports are `import type`. The
FEATURES block in `main.ts` is what keeps each registration in the bundle (see "Layout" above).

- **The two waves are a claim about the data, not the code.** Nodes, concrete and routes
  change when the player builds; machines, pickups and the header change on every autosave. A
  save event refetches `live`; a world switch refetches both.
- **`rank`** places a fetch in its wave, in tens so a feature can be inserted without
  renumbering. Which reply lands first is load-bearing: floor mode's once-only opening flight
  measures whatever deck is drawn when it runs, and everything clickable shares one canvas
  where draw order is hit-testing. So the order is a decision, not whatever the import graph
  produced.
- **`clears`** lists layer-name prefixes to empty when the fetch fails, so a failed switch
  leaves those layers empty rather than showing the previous world under the new world's
  header. `clearPrefixed` matches on `indexOf === 0`, so a trailing space is load-bearing:
  `"node:"` would also match a layer called `"node:-something-else"`.
- **`refilters`** says whether the floor filter runs again after the draw. A redraw replaces a
  layer's contents and the filter is a fact about contents, so a layer refetched in floor mode
  would otherwise arrive holding every storey at once. It is stated per fetch because the set
  cannot be derived from `floors/filter.ts`'s own `FILTERED` list: the node dots and the factory
  labels are registered and are not filtered there.
- **`settles`** marks the fetch that ends the switch: the dimmed map and the "loading…" header
  clear when it settles, either way. Exactly one fetch may say so.
- **`after`** is for the one thing after a draw that is not a draw: `/api/machines` refreshes
  the floor decomposition, because a save write changes what is built and the ids a band lists
  are what the filter runs on. **`failed`** is its mirror, run after the layers are cleared and
  before the toast, for the one failure that has to say something outside a layer: the
  header's own text.
- **`Registered`** erases the response type to the common error branch, because the list is
  heterogeneous by construction. The erasure happens at registration and the call site keeps
  the type it declared: `draw: drawNodes` fixes the type to `NodesResponse`, so a fetcher that
  points `/api/nodes` at the pickup drawer does not compile.
- **Two fetchers for one path** are two requests for one answer; the trap is one entry per
  consumer, where `/api/summary` feeds both the header and the player dot. A dev build says so
  in the console, because the only other symptom is a duplicate line in the network panel.
- `fetchersOf` returns a copy, because its caller iterates it while the draws it triggers may
  do anything. `fetcherFor` serves a caller that refreshes one layer outside its wave, so that
  its epoch guard, its clears and its toast are the ones the wave would have given it.

### The inventory grid

`map/drawn/inventory-grid.ts` draws a container's contents as tiles of the game's own item
artwork with the quantity in the corner. It has two callers, a crate and a storage box, and
"what is in it" is one idea on the map rather than two that resemble each other.

- **The names stay.** Every tile carries its name three times: `title` for a pointer, `alt`
  for a screen reader and for a tile whose picture never arrives, and once more in the caption
  line under the grid, which needs no pointer, no hover and no icon directory. The caption is
  why `contentsRows` returns rows rather than one piece of markup: a caller assembling the two
  could leave one out. The caption joins names with a middle dot because several item names
  hold a comma and none holds a middle dot.
- **The icon URL is untagged.** `/api/icons/{desc}` serves a `?v=<build>` request `immutable`
  for a year and an untagged one `no-cache` with an ETag. The page cannot learn the tag without
  a probe that names a descriptor class up front and hopes the local icon directory holds it;
  the directory is optional and some item classes ship no picture, so a failed probe would mean
  silently unversioned URLs. Untagged plus an ETag makes a revalidation a 304 rather than 50 KB.
- **Nothing is fetched until a popup opens.** Leaflet holds a bound popup as a string and
  builds its DOM on the click, so the `<img>` tags are markup until the card opens.
- **A missing icon is a tile, not a hole.** `.item-abbr` sits in the tile all along and shows
  when the `<img>` stacked over it fails. Without the generated icon directory every icon
  fails, so this is an ordinary state, and a grid with gaps would misstate how many kinds the
  box holds. `onerror` is inline because the popup is a string until Leaflet inserts it, so
  there is no node to attach a listener to; it adds a class rather than assigning one.
- **`CONTENTS_POPUP_PX` is 380** because the value cell then fits seven 38 px tiles to a row;
  eight would need 424 px and start covering what was clicked. Leaflet's default of 300 stays
  right for every other popup on the page.
- **`more`** is the server's truncation. Both routers send whole inventories, so it arrives as
  0; the "+N more" tile is the net under a server that truncates, because a grid that simply
  stops makes a container look emptier than it is. An empty list is said in words, because an
  empty grid and a container the page failed to read would otherwise look the same.

### Crates

`map/drawn/crates.ts` draws what a pioneer dropped. A crate is a situation, not
infrastructure: it exists only after a death or a dismantle with a full inventory, and it
deletes itself once emptied, so every crate on the map is live information. It is not a
buildable either (the docs dump has no clearance for `BP_Crate_C`, and a 2 m prop is a
hundredth of a pixel at world zoom), so it is drawn as a mark at a fixed pixel size, not as a
footprint.

- **The glyph**, `CRATE_PX` = 13 px, about the size of the floor connectors' arrows, the page's
  other fixed glyph that has to be clicked rather than merely seen. A death crate is a filled
  box, a dismantle crate a hollow one, and a crate of neither kind a dashed one. `mCrateType`
  arrived in build 433351 and most crates on disk are older, so a crate without a kind might be
  a death; drawing it hollow would file it as "not a death", which is the one fact the save
  withheld. Dashed is the page's word for "not a reading", as on a machine `placements.ts`
  cannot vouch for, and a kind a later extractor learns is drawn the same way. The strap across
  the box is what makes it read as a crate among the map's other small squares.
- **The card** puts contents first, as the storage popup does: a reader who clicks a crate is
  asking what is in it. A crate without a kind is called a "crate"; the server's sentence under
  the title says why there is nothing more to call it. There is **no owner row**: `mCrateType`
  is the actor's only saved property, so in a co-op world nothing can say whose death it was,
  and a guessed row would look exactly like a known one. The "in all" row appears only when the
  grid did not already list everything.
- **On by default**, alone among the built band's recent layers. Default-off layers are off
  because they smear at world zoom, and a couple of crates per world cannot. "Where did I die"
  is asked once, in a hurry, and an answer behind an unnoticed checkbox is not an answer. The
  row sits last in the built band, after the containers.
- **A divIcon in the marker pane**, as the floor arrows are: the canvas renderer offers a
  fixed-size circle and nothing else, and a polygon square would be in world metres and vanish
  at world zoom. Above the shared canvas, a crate always takes the click from whatever it lies
  on, which is right for the smaller and rarer of two things at one spot.
- **The live wave**, because a crate is created by dying and destroyed by emptying, both
  between two autosaves; refetched only on a world switch, the layer would show where a pioneer
  died two sessions ago. Rank 25 puts it ahead of the summary, the fetch that ends the switch.
  `refilters` is false: `/api/floors` decomposes no crate and a crate outside a factory is the
  ordinary case, so it stays visible in floor mode.
- A crate whose position does not read is still sent; skipping it is the page's call, the same
  one every drawing module makes.

### Route passes

`map/drawn/route-passes.ts` holds what every route layer (belts, pipes, power) goes through by
name: the width table, the pixel restyle on zoom and the sink.

- **The sink.** The route layers share the overlay canvas with the machines and the node dots,
  and hit-testing there is draw order with the last match winning. A belt run crosses every
  machine it feeds and a polyline's hit area is its width plus Leaflet's click tolerance, so a
  route layer drawn after the machines would take the click on every machine it passes. Routes
  are pushed to the back instead: under the machines and node dots, over the foundations
  (another pane, so another canvas). A pane of their own would not work: the DOM delivers a
  click to the topmost element, and the overlay canvas covers the whole viewport. `bringToBack`
  does nothing on a path whose group is off the map, so the pass is safe at any time.
- **Order inside a layer** follows the calls: each `bringToBack` puts its piece below
  everything already sunk, so the piece sunk last ends at the bottom. Chevrons go first, then
  the glyphs, then the runs, leaving a splitter on the lines it joins and a direction mark above
  its line; a square under a line cannot be clicked, and its popup is the reason it exists. A
  power pole is the same case as a splitter, which is what `_fixed` marks: the pass partitions
  on the mark rather than on draw order, which is `power-wires.ts`'s business.
- **Widths are constants**, because the save carries a centre line and no width: a belt is the
  game's 2 m at every tier, a pipe its 1.3 m bore, a wire 0.2 m. No line is drawn thinner than
  `ROUTE_MIN_PX` (1.5 px), one hairline for every network, or the networks would fade at
  different zooms and the page would invent a difference the world does not have. Wires floor
  at 2.5 px instead: they are on at world view, where 1.5 px over the artwork barely shows, so a
  wire is a mark sized to be seen, like the pole at its end. A lift's ring has a 2 px minimum
  radius, a little more than a line, because a ring has to enclose something.
- **The zoom restyle.** A polyline's weight and a circle marker's radius are the two sizes on
  the page given in screen pixels, so they are the two that do not follow the map. Drawing
  routes as thin polygons in map units instead would allow no floor, and a layer of two-metre
  ribbons would be unclickable at exactly the zooms where its popup is worth opening. The pass
  costs well under a frame per zoom step. It skips a layer nobody is looking at, so ticking a
  layer on at another zoom has to restyle it, which `main.ts`'s `overlayadd` handler does.
- **The four kinds of piece**: a lift's ring is sized by radius and a run by weight; a splitter
  is a polygon in map units and right at every zoom; a chevron is in map units too, and only its
  opacity changes, since below some zoom it is noise. A power pole's disc keeps its fixed pixel
  radius. A casing's `_widen` rim is in screen pixels around a line that follows the scale, so
  the two are added again at every zoom. The same zoom that changes the width changes how many
  pieces a bend needs, so the pass re-tessellates the line too. Floor mode's connector glyphs
  are markers in the marker pane and take no part in either pass.

### Leaflet's private fields

`src/map/leaflet-private.d.ts` declares the fields this page hangs off Leaflet objects. It keeps
two kinds apart on purpose: the page's own marks (`_rank`, `_chevron`, `_labelWeight`, `_floor…`),
set on objects Leaflet owns to save a WeakMap probe per mark, and four real Leaflet internals
it deliberately uses (`_handlingClick`, `_update`, `layerId`, `_getBoundsOffset`). The second
list is what to read before upgrading Leaflet.

- **`FloorMark`** is a mark rather than a lookup table because the floor filter walks every
  piece. Its fields are alternatives, one per way a layer is joined; `floors/floors.ts` lists
  the joins. A power piece is a `wire` (two ends, the one kind that can leave a floor), a
  `pole` (one point, placed like storage) or the `casing` under either, which gets the same
  verdict and never a glyph. `anchors` are where each wire end counts as standing (the base of
  its pole, or the endpoint where no pole is named), against `ends`, where it is drawn.
- **`_floorAll`** sits on the group, not on a piece: the filter replaces a group's contents,
  and leaving floor mode puts them back. `clearedLayer()` clears it with the contents, since a
  snapshot of refetched data describes a world that is gone.
- **`_floorStyle`** is how a path was drawn before it was ghosted, so unghosting is exact; its
  presence means the path is ghosted now. **`_floorCard`** keeps the card's content rather than
  the popup, because `bindPopup` reuses an existing popup when handed a string.
- **`_route`** is the route a polyline was tessellated from: the drawn latlngs are an output
  and cannot be subdivided again from themselves.
- **`OnMap`** is a view type for Leaflet's own `_map`, which `@types/leaflet` declares
  `protected` on `Layer`. Redeclaring a protected member as public in the augmentation is an
  error. `map.hasLayer` is not the same question: these layers sit inside a `LayerGroup`, so the
  map's registry holds the group and not them.

## Tests

`npm test` runs Vitest in node, with no DOM. It covers the modules whose logic stands on its
own: formatting and words, markup escaping, copy, toasts' error wording, the fetch registry,
settings and the address parser, the palette audit, the sun, floor membership, machine states
and route geometry. The DOM and Leaflet glue is left to the page itself. A module that touches
the page while it is imported is loaded afresh per test with that global stubbed (`app/state.ts`
reads the address bar, `app/settings.ts` reads storage); `map/drawn/route-geometry.ts` runs with
`map/map.ts` and Leaflet mocked, because importing those creates the map.

The tests live in `test/` rather than beside their modules because the architecture tests
(`test_frontend_layout.py`, `test_comment_budget.py`) read every `.ts` under `src/` as page
source, and a test file is not part of the page.

Coverage spans every module under `src/`, tested or not, and is written to `coverage/lcov.info`
(untracked) with paths relative to the repository root, which is how Sonar's
`sonar.javascript.lcov.reportPaths` resolves them. Vitest writes those paths with `\` on
Windows, which the Linux scanner container cannot resolve, so `npm test`'s `posttest` step
(`scripts/lcov-posix.mjs`) rewrites them with `/`. It runs only after a passing run. Sonar
scans `test/` as tests, not as page source. `tests/frontend/test_format_ts.py` stays: it
holds `format.ts` to the Python tools' own rounding, a comparison only the Python side can make.

## Types

`npm run check` is `tsc --noEmit` and it is clean under **full `strict`**, plus
`noUncheckedIndexedAccess`, `noUnusedLocals` and `noUnusedParameters`. It needs no running
server: everything it reads is committed.

`noUncheckedIndexedAccess` is the one worth calling out, because it is the setting most
projects leave off. It cost five call sites here, and each was a real "this index can miss"
that the old code happened to answer correctly: a fragment with no `z`, a section key nobody
has folded yet, a session name seen once. They are now written down as answers rather than
left as luck.

One file carries the API, and it is generated. There used to be two.

- **`src/api/schema.d.ts` is generated** by `npm run typegen` from the server's own
  `/openapi.json`, and committed. It is the authority for which paths exist, which query
  parameters each takes, what a validation error looks like — `get()` only accepts a path the
  server actually serves — **and now for every response body too**: each router under
  `interfaces/web/routers/` declares a `response_model`, so its whole payload is described
  here. Adding a field to an endpoint therefore means running `typegen`, not editing two files.
  Regenerating rewrites the file whole, so its provenance header is re-stamped by
  `scripts/stamp-schema.mjs`, which `typegen` chains.
- **`src/api/shapes.ts` re-exports those components** under the names the page already used,
  one line apiece and no fields of its own. The indirection is so that a change to an endpoint
  moves one line in one file rather than every module that draws its payload, and so the page's
  names stay the page's while their definitions come from the server.
- **`src/api-types.ts` is gone.** It held the frontend's claim about the API — response shapes
  read off real payloads — and it died when the last endpoint started describing itself. What
  was in it that was never a payload lives with the code that uses it: the drawing tuples in
  `map/geometry.ts` and `ApiError` in `api/client.ts`.
- **`/api/worlds` was the last exception, and its conversion was a body change — made as
  one.** It forwarded the loader's own save headers, so the useful response model deleted the
  eight keys per row nobody read; the server now declares `WorldsResponse` like everything
  else, `app/state.ts` and `app/world-picker.ts` import the rows from `api/shapes.ts`, and the page has no
  hand-written payload claims left. `SaveRow` in `routers/world/worlds.py` names the deleted
  keys.

What is still `any`, in full:

- `L.Class.extend()` returns `any` in `@types/leaflet`, so `PyramidLayer` in `map/tiles.ts` is
  asserted back to a `new (url, options) => TileLayer` at the point of definition. That
  assertion is the only place the tile layer's shape is stated.
- `L.Control.Layers.sortFunction` and a few Leaflet option bags are typed by `@types/leaflet`
  as loosely as Leaflet itself defines them; nothing here widens them further.
- `/api/summary`'s `header` is an open map — `{[key: string]: unknown}` by the server's own
  declaration, because it forwards the sidecar's save header rather than restating its thirteen
  keys (see `SummaryResponse` in `routers/world/worlds.py`); the page reads the one key it uses,
  `session_name`, out of that map. The rest of the endpoint is generated in full. The sentence
  that used to stand here — "typed for the four branches this page reads and no further" — was
  written when every endpoint answered `-> dict` and typing more meant inventing a contract;
  that stopped being true when the routers declared their response models.

Leaflet's private fields, which `src/map/leaflet-private.d.ts` declares, are under "Module
notes" above.
