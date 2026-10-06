# The map page

TypeScript, built by Vite into `../static/`, which is the directory `app.py` mounts at `/`.

**The built bundle is NOT committed.** `static/` is gitignored: `app.js` is a dependency's
compiled code -- Leaflet, minified -- and the repository does not carry or redistribute it.
A fresh clone therefore has no page until the build below has run once; until then the
server answers `/` with exactly that instruction (and the JSON API works regardless).
`tests/test_architecture.py` enforces both halves: nothing under `static/` may ever be
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
| `src/style.css` | the page's stylesheet, imported after Leaflet's so it wins on order |
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
| `scripts/` | `typegen.mjs` regenerates the schema; `stamp-schema.mjs` re-applies its provenance header |

Four things about the graph are deliberate and easy to undo by accident.

`app/load.ts` imports no module that fetches, and that is the newest of the four. It used to
import all seven drawing modules and call each draw function by name; they now declare what they
want fetched through `app/registry.ts` and `app/load.ts` runs the list knowing none of the names.
The catch is that a module nothing imports is a module the build leaves out, and a registration
that never ran is a layer that is simply never fetched — no compile error, no runtime error,
just an absence. So `main.ts` names every one of them in its FEATURES block, and
`tests/test_architecture.py` checks that block against the set of modules calling
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

The control does not import `map/tiles.ts` either, and the same shape fixes it: the control
draws the four base-map radios and `map/tiles.ts` registers what a click on one means, through
`onModePick`. The arrow can only point that way — `map/tiles.ts` reaches the control through
`map/layers.ts` already — and the seam is what keeps "which picture is the base map" out of a
widget that otherwise knows nothing about pyramids.

The control does not import `map/floors/floors.ts` for the third time round the same shape:
`onFloorPick` and `onFloorExit` are the seam, and the control draws a floor picker without
knowing what a storey is. The floors module is imported by `map/labels.ts` (the card's action),
`app/fragment.ts` and `main.ts` (the address bar and the Esc key), `app/load.ts` (a redraw
replaces a layer's contents, and the floor filter is a fact about contents) and
`map/drawn/placements.ts` (a save write changes what is built, so `/api/machines` re-asks for
the decomposition) and `dash/factories/factory-detail.ts` (the floors aspect's way in) — so it
must import none of those six, and does not.

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
  hand-written payload claims left. `worlds()` in `routers/world.py` names the deleted keys.

What is still `any`, in full:

- `L.Class.extend()` returns `any` in `@types/leaflet`, so `PyramidLayer` in `map/tiles.ts` is
  asserted back to a `new (url, options) => TileLayer` at the point of definition. That
  assertion is the only place the tile layer's shape is stated.
- `L.Control.Layers.sortFunction` and a few Leaflet option bags are typed by `@types/leaflet`
  as loosely as Leaflet itself defines them; nothing here widens them further.
- `/api/summary`'s `header` is an open map — `{[key: string]: unknown}` by the server's own
  declaration, because it forwards the sidecar's save header rather than restating its thirteen
  keys (see `SummaryResponse` in `routers/world.py`); the page reads the one key it uses,
  `session_name`, out of that map. The rest of the endpoint is generated in full. The sentence
  that used to stand here — "typed for the four branches this page reads and no further" — was
  written when every endpoint answered `-> dict` and typing more meant inventing a contract;
  that stopped being true when the routers declared their response models.

`src/map/leaflet-private.d.ts` declares the fields this page hangs off Leaflet objects. It keeps
two kinds apart on purpose: the page's own marks (`_rank`, `_chevron`, `_labelWeight`, `_floor…`),
set on objects Leaflet owns to save a WeakMap probe per mark, and four real Leaflet internals
it deliberately uses (`_handlingClick`, `_update`, `layerId`, `_getBoundsOffset`). The second
list is what to read before upgrading Leaflet.
