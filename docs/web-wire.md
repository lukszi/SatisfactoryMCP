# The web wire rules

The rules every router in `interfaces/web/routers/` lives by. They are stated once, here;
routers point at this file instead of re-telling them.

1. **The function name is the operation_id.** FastAPI's default id is
   `{function_name}_{path}_{method}` and `frontend/src/api/schema.d.ts` is generated from
   it — renaming a handler churns the committed schema.
2. **Declaration order is wire order** for every `TypedDict` a `response_model` names: the
   keys are emitted in the order they are declared.
3. **A `response_model` FILTERS.** Keys the model does not declare are dropped from the
   response; a handler may build more than it sends. This is load-bearing — several
   endpoints deliberately send a subset of what the domain returns.
4. **Numeric types rewrite wire bytes.** Declaring `float` where the value is an int
   validates `15` into `15.0` on the wire, and vice versa fails validation. Match the real
   type of the value.
5. **A router never imports another router.** Shared shapes live in the `serial` package
   (`serial/shapes.py`) only when one function builds them for more than one router; two
   shapes that merely look alike stay separate. Enforced by `tests/test_architecture.py`.
6. **Regenerate, never hand-edit** `api/schema.d.ts`: throwaway server on a port in
   8920–8999, then `npm run typegen -- <port>` (`scripts/typegen.mjs`: `openapi-typescript`
   against that port, then `scripts/stamp-schema.mjs`). Without an argument it reads
   `SATISFACTORY_WEB_PORT`, then falls back to 8712.
7. **A write says what it refuses, in the schema.** A request body is a `TypedDict` taken as
   `Annotated[Body, Body()]` (routers may not import pydantic), and every non-2xx body the page
   branches on is declared with `responses={409: {"model": ...}}`, so it reaches
   `api/schema.d.ts` like a 200 does. The planner routes (`routers/plans/planlog.py`) answer a
   conflict with `OutdatedResponse` and never apply part of a push. The server stamps the
   actor (`page`, its own pid); the page never sends one.
8. **Metres, one decimal.** The save stores centimetres; every coordinate that leaves this
   layer has been divided by 100 and rounded (`serial.cm_to_m`), exactly as the text
   presenters do. A plan's siting is the exception: it records metres a player typed.
9. **`?save=` and `?world=` wherever a state is read**, so a page can pin itself to one save
   while the game keeps autosaving over another.
10. **An error is `{"error": "..."}` with a 4xx**, never a 200 with an empty list: a browser
    that cannot tell "no nodes" from "no save" draws an empty map and says nothing.

## The event stream

`/api/events` sends six event names. `save` and `notes` are triggers: they say a file moved
and the page decides what to refetch. `plans` and `activity` carry data, because they are
tailed line by line from the plan logs and the activity journal every 0.5 s rather than
stat-compared every 3 s. `settings` carries data too: the tail stats the shared settings file.
`maps` is published by the map job runner rather than found by a poll.

| Event | Source | Data |
|---|---|---|
| `save` | newest `*.sav` under the save root | `{filename, mtime, save_token}` |
| `notes` | newest `labels/**/*.json`, or the legacy top-level `plans/<world>.json` | `{filename, mtime}` |
| `plans` | new commits in one `plans/<world>/<key>/ops.jsonl`, one event per plan per tick | `{world, key, name, rev, from_rev, actors, text, ts, forgotten}` |
| `activity` | each new line of `activity/<world>/<writer>.jsonl` | `{world, id, ts, actor, kind, plan, rev, text, args}` |
| `settings` | `settings.json` in the user data dir, when its mtime or size moves | `SettingsResponse {version, values, stored, updated, by}` |
| `maps` | the map job runner: every job state change, at most one progress update per 2 s, and every registry write | `{job: MapJobBody \| null, queued: [job id], registry_version}` |

- `actors` and `actor` are `ActorBody` (`kind`, `client`, `pid`, `display`). `text` is the
  newest commit's `describe_commit` words; `from_rev` is the rev before the first new commit.
- The tail's first pass only records where each file ends, so a server start announces
  nothing old. A file that appears later is read from its start, which is how a new plan's
  `create` arrives. A line without its newline yet waits for the next tick.
- A new stream is replayed the newest event of each kind, in the order above. The page treats
  a replayed `save`/`notes` event as news only when its `mtime` is newer than page open
  minus 2 s.
- `?since=` is the page's open time in epoch seconds, and the page always sends it. A `plans`
  or `activity` event whose `ts` is at or before it is history: the stream withholds it from
  the replay and from the live fan-out alike. Live matters as much as replay, because the tail
  can publish an entry up to 0.5 s after it was written, and a new chat process's journal file
  is read from its start the first time the tail sees it. Without `since` (the default 0) every
  event goes out. No grace applies: an `activity` entry can move the page, and a find chat made
  a second before the page opened must not. A reconnect's replay of missed entries is
  `GET /api/activity?since=` from the newest entry heard, or page open, whichever is later.
- Only the served instance tails (`create_app(served=True)`), and it is also what names the
  process the `web` journal writer. Test apps leave both off. The same flag decides whether the
  map job runner reads and re-adopts the jobs on disk at start.
- `maps` is state, not a journal: `since` does not apply, and the replay of the newest one is
  how a reloaded page sees a running job. A `registry_version` other than the page's means the
  list changed and the page refetches `/api/maps`.
- Pin writes (`/api/pins`, Planner P3) add no event name: each appends one journal entry,
  `pin.add`, `pin.edit` or `pin.drop`, which reaches every page as `activity` within the 0.5 s
  tail. `plan` is the plan key for plan and process pins, else null. Chat's
  `show_on_map(pin=True)` appends `pin.add` to its own journal file, so a pin chat makes
  reaches the page the same way. A `plans` event for a plan that a plan or process pin names
  also refetches pins, so a plan forgotten from chat turns its pin gone at once.
- Ask writes (`/api/asks`, Planner P4) add no event name either: the web appends `ask.add` and
  `ask.drop`, and chat's `ui_context` appends `ask.seen` and `ask.answered` to its own journal
  file, so every change reaches every page as `activity`. `diff_vs_save(plan=)` and
  `commission_plan(plan=)` journal `plan.view` with `args.view = "track"`.
- `GET /api/activity` keeps one row for a run of the same `plan.view` (same plan, actor, view
  and item, stage or section), the newest, before it applies `limit`.
- Advisory writes (`/api/advice/hidden`) add no event name either: the web appends
  `advice.hide` and `advice.restore`, and chat's `ui_context(dismissed=)` appends `advice.hide`
  to its own journal file. Any `advice.*` entry refetches `/api/advice` on every page.

## World

- `hidden_spoilers` is sent only by `/api/collectibles`, and it counts **categories** (pickup
  kinds) dropped by `spoilers=0`, never placements. The node routes take no `spoilers`: a
  locked node is always sent with `spoiler: true` and the page fades it.
- The finder tools (`search_resource_nodes`, `rank_build_sites`, `search_conduits`,
  `whereami`, `collected_from_world`) journal `world.find` with `args {view, params}`: the
  World view and its address parameters, the ones the page's own filters write. A page set to
  follow chat opens `world/<view>?<params>`; "toasts only" offers it instead.
  `rank_build_sites` sends `view: "rank"`.
- `GET /api/activity` keeps one row for a run of `world.find` by one actor (kind and pid), the
  newest, before it applies `limit`. Every row carries `count`: the entries it stands for,
  1 unless collapsed. A collapsed `plan.view` run counts the same way.

## Nodes

`/api/nodes` (`routers/world/nodes.py`) is the static node table joined to what the save built on it.

- The join is partial and says so: `occupancy` resolves only the extractors whose target is a
  node key, so `occupied` false means "no extractor known here", never "free". The node id
  doubles as a `node:` selector for the MCP tools.
- The region is joined on this side because the raster is: sending 608 rows and the grid for
  the page to index would put the orientation trap (row 0 is the north edge) in two places.
- **A free node is not always a usable one.** `reachable` is the test the text surface marks
  `LOCKED` and leaves out of free capacity: a resource well satellite with no Pressurizer
  researched is not somewhere a plan can go. The domain reads a missing unlock set as "assume
  yes", right for a capacity sum and wrong for a dot somebody plans around, so the route sends
  null instead.
- **A failed save is not a failed answer.** The table needs no `.sav`, so a world whose save
  will not load still gets its geography; it loses the occupancy join and the unlock set, and
  `save_error` says so with `occupied` and every `reachable` null beside it.

## Placements

`/api/machines` and `/api/structures` (`routers/layers/placements.py`) answer one question in two
resolutions, and the line between them decides every nullable field on the placement layers.

- An **actor** record has an instance id, a recipe and a clock. The projection writes
  machines, extractors and generators only behind `cls.startswith("Build_")`, so their `cls`
  is a non-empty string and `name` never falls through to null. Splitters, mergers,
  containers, fluid buffers and crates are actors too.
- An **interned** row is a lightweight buildable (foundation, ramp, wall, catwalk), a belt or
  pipe piece, or a power pole, kept in a positional table because a record per piece would be
  megabytes: `{"classes": [...], "instances": [[class_index, x, y, z, yaw], ...]}` in
  centimetres. Its class is an index into a legend, and a row whose index points past the end
  is a real piece at a real place with no name, so `cls` and `name` are nullable there.
- `w_m`/`l_m` are the X and Y extent of the union of a building's clearance boxes, which is
  what makes a Manufacturer draw bigger than a Constructor; `h_m` is the third side, which a
  floor view needs (a Refinery is 15 m tall on a 12 m storey, so it comes through the deck
  above). They are null, never guessed, for a class the docs dump does not describe: the
  client picks the fallback, because one drawn on the server would look measured.
- `yaw` turns those extents into the rectangle the player placed, so the two are read
  together or not at all.
- `uptime` keeps three decimals: at two, 0.9994 rounds onto 1.0 and `health.SATURATED`'s line
  vanishes.
- `/api/machines` asks `health.assess` once for the whole world rather than per row (on the
  reference projection's 570 actors, 1.3 ms without it and 2.5 ms with) and keys the verdicts
  on the instance leaf that `/api/floors` and the page's floor ids join on. 195 of those 570
  are `blocked` (a full output box) and `actionable` like the other states in
  `health.ACTIONABLE`; how the map marks them is [save-projection.md](save-projection.md)
  §6.2d.
- `/api/structures` rows are the only record of what was physically built: lightweight
  buildables appear in no actor header. Positions are piece centres, so consecutive foundations
  of one slab sit exactly `tile_m` apart. `yaw` is the table's fifth column, degrees about world
  Z with positive turning +X towards +Y, and null for a projection cut before schema 12, which a
  client keeps drawing axis-aligned rather than reading as zero. A world with nothing built
  answers an empty list with `count: 0`, a real answer unlike a save that will not load.
- `/api/structures` sends the reference world's 8,347 pieces one row each, ungrouped: 708 KB,
  the same order as `/api/collectibles`. Grouping into grid cells would halve a payload that is
  not the bottleneck and lose the per-piece class the popup and the inspector read. None of
  these classes has clearance data, so they share one grid whose edge `tile_m` reports from
  `FOUNDATION_M`, and the page hardcodes no 8.
- `core.saveio.rows` decodes every interned table for every reader, so a malformed row costs one
  piece, segment or pole rather than the endpoint.

## Belts and pipes

`/api/belts` and `/api/pipes` (`routers/layers/belts_pipes.py`) send every conveyor piece and fluid
pipe as the polyline it was built along, one row per piece and ungrouped, because the per-piece
class is what a popup reads. Pieces are interned rows (see Placements); splitters and mergers
are actors.

- `points_m` are `[x, y, z]` metre triples, declared as tuples so typegen emits
  `[number, number, number]`, and never null in any position: `rows._points` drops a point that
  will not read.
- **Belt points are in travel order, input to output.** The save stores them output-first and
  the projection reverses them, so direction along a run needs no outside knowledge. `chain` is
  the belt chain a piece belongs to, so "the whole run" is a group-by.
- **`curve_m` is what makes a curved belt or an elbow curved.** `points_m` are the spline's
  control points, not the spline: a bend drawn from them alone is the chords between its
  corners, out by up to 16.4 m of arc on one belt piece, and an elbow drawn from its six points
  is the polygon cutting the corner it was built to round. Each entry is one span's
  `[leave, arrive]` tangents in metres, the pair a cubic Hermite between the span's two points
  takes; they are displacements in the points' own space, so the map's y-flip applies to them
  unchanged. A null slot is a straight span, and a null field means no bend anywhere or a
  projection older than the column, which a client draws the same way. A span that will not
  decode becomes straight rather than costing the route.
- **A lift is a belt whose top-down polyline is a single point**: every lift on the reference
  save has zero horizontal extent, so the client owes it a glyph. `lift` comes from the dump's
  native class `FGBuildableConveyorLift`, never from `Lift` in a class id, and is null, not
  false, for a class the dump does not carry. `items_per_min` is the tier's throughput (60 to
  780), so the page parses no "Mk3" out of a name.
- `attachments` (splitters and mergers) ride with the belts, not the machines: a splitter runs
  no recipe, draws no power and means nothing without the runs either side, and being in no
  other payload keeps it from being drawn twice. Their `w_m`/`l_m` are the dump's 4 x 4 m soft
  clearance box.
- **Each pipe says which fluid it carries**, taken from the `FGPipeNetwork` that claims it: the
  world's own answer, not an inference from what the pipe is plugged into. `network` is that
  network's game id, not an index into the reply, and null for a pipe no network claims.
  `fluid_name` is resolved against the dump; `flow_m3_min` is the tier's throughput (300 on Mk1,
  600 on Mk2).
- `row` is the pipe's position in the raw segments table, the key `/api/floors` uses, sent
  rather than counted so a torn row leaves a gap instead of renumbering everything after it.
- **`direction` is inferred, and `basis` says from what** (`domain/world/flow.py`, which declines
  wherever more than one answer is consistent): `machine port` (the pipe ends at a port the save
  types as input or output), `pump` (a pump or valve at one end), `propagated` (only the wider
  network settles it), or `unresolved` with `direction` `unknown` (a loop, or a trunk with
  producers and consumers on both sides). A client may draw an arrow on the first three and
  must not on the fourth. A projection too old to carry the join reads as all `unknown`.
- Pumps, junctions, valves and fluid buffers carry no spline, only a header position, so they
  are not pipe rows.

## Power lines

`/api/power` (`routers/layers/power_lines.py`) sends every pole, wall outlet and tower platform and the span
of every wire. The geometry sits beside `graph["power"]`, which says who is joined to whom; the
join is positional (`wires[i]` is the span of `graph["power"][i]`) and this route is the one
place the two are put back together.

- A pole is an interned row: `cls`/`name` may be null, the coordinates never are, since
  `iter_power_poles` drops a row whose class index or position will not read. `connections` is
  never null: an unwired pole reports 0, which is a measurement.
- A wire's `a_m`/`b_m` are **connector** positions, not building origins. A connector sits at a
  fixed offset on its owner (7 m above a Mk1 pole; 2.1 m forward and 4.7 m to one side of a
  constructor's centre), so origin-to-origin lines would run through every machine and a
  client filing wires on storeys by height would put them a storey high. `a_pole`/`b_pole` are
  the index into the reply's `poles` of the pole an end serves (an index because a pole row has
  no instance id), null for an end on anything else.
- `from`/`to` name each end in the edge's order, which the projection measured (the save's own
  endpoint order agrees only about half the time). They are null where no record names the
  actor, such as a hypertube entrance, a drop pod or the AWESOME Sink, rather than a guess.
- `span_m` is the 3-D chord: the save carries no sag, and a tower span's 24 m climb is real
  cable.
- `edge_count` counts power edges and `wire_count` those that published a span: a save too old
  for the geometry answers a non-zero `edge_count` with no wires, which tells "nothing to draw"
  from "nothing here".

## Storage

`/api/storage` (`routers/layers/storage.py`) answers "where did I put the steel" rather than "how much
have I got": every Storage Container and Industrial one, Personal Storage Box, Dimensional
Depot uploader, the HUB's built-in container, the Blueprint Designer's, and every fluid buffer,
in one ungrouped payload.

- **Not the splitters and mergers.** Each owns a component literally named `StorageInventory`
  holding the one to three items in transit, so matching that name would report hundreds of
  phantom containers, draw them again over the belt layer and count transit as stock. Machine
  buffers are left out on the same principle and sit on their machine's row.
- **Two row models, told apart by `kind`.** A solid row carries `items` (biggest first, named,
  the whole box), `more`, `item_kinds`, `total` and `slots` (null rather than 0 where the
  projection wrote none). A fluid row carries `fluid`, `fluid_name`, `stored_m3`,
  `capacity_m3` and `fill`. The other kind's fields are absent rather than null, because a
  container has no fluid level. Each variant is declared whole: one model with optional tails
  would re-key a solid row into the fluid tail's gaps, since pydantic emits declaration order
  and drops absent keys.
- `more` is always 0 from this server, which sends every box whole; it stays as the row's own
  statement that nothing was left off, so a client's "+N more" survives a server that caps.
- `w_m`/`l_m` are null for the HUB's and the Blueprint Designer's containers, which the dump
  gives no clearance; a size invented here would arrive looking measured.
- **The fluid comes off the plumbing, not the buffer**: the `FGPipeNetwork` that claims it, the
  join `/api/pipes` uses, and null for an unclaimed buffer. `capacity_m3` is the dump's
  `mStorageCapacity`, and `fill` is stored over capacity, null rather than a division by a
  missing one, so the three are nullable independently.
- `filled` and `items_total` count the solid rows only.

## Floors

`/api/floors` (`routers/layers/floors.py`) is what is built, one storey at a time. Nothing in the save
says "floor": `domain.factories.floors` recovers them from the geometry, 4-connected platforms
of 8 m foundation cells and then a per-platform cluster of deck heights, and the route parses
the query, calls it once and rounds.

- **It ships ids, not geometry.** The page already has every machine, splitter, belt and pipe;
  what it cannot derive is which floor each is on. A band lists `machines` and `attachments` as
  instance leaves, and a run is keyed by its belt `chain` or its pipe `row`, the joins those
  payloads carry.
- `deck_rows` lists a deck's pieces by their position in `/api/structures`, the only name a
  lightweight buildable has, since both sides walk `saveio.rows` in one order; re-deriving a
  deck from heights goes wrong at exactly this world's 1 m and 2 m half-steps. `deck_rows` is
  the pieces at the band's own level and `pieces` the cluster it was found in; a client drawing
  a deck wants `deck_rows`.
- `span_m` is how spread the band's own level is, not the storey height: the floor above is the
  next band's `top_m`. `share` is against the platform's own largest band. `clean` is, per
  platform, the share of its foundation pieces within epsilon of one of its bands. `label` and
  `slab` only name a platform and took no part in finding its floors.
- **Runs are grouped by what they do to a floor**: `same-deck` (both ends over one band, the set
  a floor filter draws), `connector` (ends on two bands, where the lifts and risers are),
  `terrain` (neither end over a deck) and `mixed`. `ends` is always two entries, head then tail,
  either null over no deck. `riser` means tall enough to be only a floor connector, a different
  claim from `lift`: a quarter of lift chains are belt-height jogs on one deck. `violations`
  lists risers with both ends on one band, which cannot happen, so one is drift made visible.
- **`placements` is only what did not land on a floor**, since what did is listed by id inside
  its band: `exempt` (a miner on a node, a water extractor on water, by native class),
  `terrain` (measured against the heightfield) and `off-deck`.
- **`terrain_measured` says whether the ground was consulted at all.** Most machines have no 1 m
  heightfield, and then an empty `terrain` group would otherwise read as nothing on the ground.
- `?factory=` takes a factory label or any MCP selector, `?platform=` the stable index this route
  hands out; either narrows placements and runs to that footprint, including what is under it.
  A selection that matches nothing is a 404.
- A save too old to carry `FGLightweightBuildableSubsystem` is a 200 with a `note`, not an
  error and not an empty list: the world has floors this file cannot show.
- Every metre field is `float | None` even where the reference world never sends a null,
  because a response model is a validator: a null in a field declared `float` is a 500.

## Inspect

`/api/inspect` (`routers/world/inspect.py`) answers what is at a map coordinate. Every answer comes
out of `place.describe`, the function `describe_location` calls; the route converts metres to
centimetres and rounds. `radius_m` is the elevation reach (default 200 m, the tool's own);
conduits count within 250 m, fields and pickups look 500 m out, and five nearest nodes are
sent.

- **A failed save is not a failed answer.** The node table is static and covers the map, so a
  world whose save will not load still gets its region, ground elevation and nearest nodes;
  what it loses is the built population and the occupancy join, and `save_error` says so
  rather than letting "no extractor here" mean "no save here".
- **Four sources, each labelled.** `terrain_m` is the extracted 1 m heightfield read bilinearly
  at the exact coordinate, preferred wherever a field exists; `terrain_source` names the layer
  that answered and `terrain_accuracy_m` what the generator measured for it (a 0.2 m landscape
  texel and a 3.9 m fill texel are not the same claim). `terrain_ambiguous` says it may be a rock
  top or roof, with `terrain_bare_m` the landscape under it. Ground and built are populations of
  things standing nearby and stay apart: a node rests on terrain, a foundation is wherever the
  player put it, and one median over them would describe none.
- `fill_m` is null more often than not, and `fill_note` names which of its two causes applied,
  too few nearby nodes or nothing built nearby; it is never 0, which is a different
  measurement. `terrain_note` does the same for the field: no field on this machine, or a point
  the field has no data for.
- `terrain_water_m` is the water surface's own height, known to centimetres from a cooked water
  volume's bounding box. `terrain_water_depth_m` is that minus the ground, sent only where the
  ground under the water was measured at 1 m: over the fill layer, which is most of the ocean,
  the difference is a number nobody measured, so it is null with `terrain_water_note` saying
  why, never 0.0.
- `region` is null for ocean and off-map rather than a nearest-land guess. A node's
  `resource_name` comes from the same helper `/api/nodes` uses, so one fact has one word.
- Not cached: the probe is a few milliseconds over the reference world, so a per-save cache
  would only buy an invalidation bug. The heightfield is cached by its own loader.

## Terrain

- `/api/inspect` `elevation.terrain_cave` is `none`, `below` or `inside`
  (spatial-and-map.md §23). Under `below`, `terrain_m` is the surface and
  `terrain_cave_note` is the line the page shows beside it. Under `inside`, `terrain_m` is
  null, the note travels as `terrain_note` and `terrain_cave_note` is null. A map click
  carries no z, so the inspector sees `below` at most.
- On rock, `terrain_m` is the collision surface from `rocks.npz` when the field has one
  (spatial-and-map.md §24): the highest of the landscape and the rock collision, never an arch
  or a boulder. No field was added or renamed. `inside` with a cave floor found keeps
  `terrain_m` and sends the floor line as `terrain_cave_note`; the inspector cannot reach that
  case without a z.
- The site preview's `terrain.cave_pct` is the pad's share with a cave under it, 0 without
  cave masks. The page prints it when it is not 0.

## Pins

`/api/pins` (`routers/bridge/pins.py`) follows the rules above: `?save=`/`?world=`, the guard on
every method, `{error}` with a 4xx. A write carries the `rev` it read; a different `rev` is a
409 `PinStaleResponse {error, stale: true, pin}` with the row as it stands, and nothing is
written. A create of an object that already has a live pin is a **200** with `existing: true`
rather than a 201. A pins file from a newer version is a 503 `{error, newer_schema: true}`
naming the pins, not the path. Pin numbers are never reused.

`PlanOpBody` lives in `serial` because two routers publish it (`planlog` for pushes,
`plan_solve` for the ops an alternates option would push). The alternates route's reply is
named `PlanAlternatesResponse` because `routers/codex/gamedata.py` already publishes an
`AlternatesResponse` and two models with one name would rename both in `api/schema.d.ts`.

`Flow`, `MachineSpot` and their builders `flow_json` and `machine_spots` live in `serial`
because `routers/factories/factory_labels.py` (candidates, amend) and `routers/factories/factory_graph.py` (`/api/factories/graph`,
`/api/factories/machines`) both send them.

## Track and asks

`GET /api/plan/track?key=&rev=&biomass=&headroom=` (`routers/plans/plan_track.py`) builds its whole reply from one
solve (`domain/planning/progress/track.py`). Not feasible, empty, and a count-as-built factory with no
machines left are all 200s that say so (`feasible`, `empty`, `scope_error`) with empty lists;
a 400 is only a solve that refuses its arguments. `biomass` is `include` or `exclude`, the
spelling every power route uses. `headroom` is `measured` (the default) or `nameplate`: the
save's figure a plan with no stored headroom is staged against. The page sends the shared
*stage headroom* setting, and `biomass` the shared biomass setting; the routes keep their own
defaults when the query is absent. `written_ago` is the save's age alone (`12 min ago`, null with no mtime); `age_note`
keeps the full line for a tooltip. `GET /api/plan/feeders` is the ~0.7 s
extractor walk, never run per save.

`/api/asks` (`routers/bridge/asks.py`) follows the pins rules: the guard on every method, a delete
carries the `rev` it read and a different one is a 409 `AskStaleResponse {error, stale: true,
ask}`, a newer asks file is a 503 `{error, newer_schema: true}`. `about.plan` must be a live plan
key (404 otherwise). Ask numbers are never reused. Unlike pins, the store has writers in every
MCP process (seen, answered), so every write holds the file lock.

## Advice

`/api/advice` (`routers/dashboard/advice.py`) follows the asks rules: the guard on every write, a write
carries the `rev` it read (0 for a row never hidden) and a different one is a 409
`AdviceStaleResponse {error, stale: true, row}` with the row as it stands, a newer file is a 503
`{error, newer_schema: true}`. A hide names the row by `key`, a restore by `adv:` id in the
path. [advisors_contract.md](advisors_contract.md) is the specification.

## Settings

`/api/settings` (`routers/bridge/settings.py`) reads and writes the settings the page and chat share:
`GET` sends `SettingsResponse`, `PATCH {values, version?, only_unset?}` changes some. A write
passes the guard. A different `version` is a 409 `SettingsStaleResponse {error, stale: true,
settings}` and writes nothing, a newer file is a 503 `{error, newer_schema: true}`, and the route
takes no `?save=`/`?world=`. Every write arrives on every page as the `settings` event.
[shared-settings.md](shared-settings.md) is the specification.

`PlanStateBody.headroom_mw` is the stored startup headroom, `null` for the save's own figure
(measured by default, see `headroom` above).
`SolveResponse.power` is the payback view: the same recipes read out at every horizon stop,
the price used and the overclock-last pick
([planner-payback-horizon_contract.md](planner-payback-horizon_contract.md) §7).
`SolveRow.last_clock` is set on a row whose last machine is overclocked.

## Maps

`/api/maps` (`routers/assets/maps.py`) lists the base map types and runs the jobs that make them;
[maps_contract.md](maps_contract.md) is the specification. The writes (`PUT /api/maps/default`,
`PATCH` and `DELETE /api/maps/{id}`, `POST /api/maps/adopt`, `POST` and `DELETE /api/maps/jobs…`,
`DELETE /api/maps/cache`) pass the guard. A registry write carries the list `version` it read;
another is a 409 `MapsStaleResponse {error, stale: true, version}` and nothing is written. The
registry writes answer with the whole `MapsResponse`, so the page redraws from the reply. A job
is queued with 202; a full queue is 409 and a short disk 507. Routes take no `?save=`/`?world=`.
`/api/maptiles/{layer}/…` takes a registry id as `layer`; `map`, `terrain` and `satellite` are
served even before the manifest exists. A type's `title` is the one name the page and chat show
it by ("Painted · 6 Oct ★"), composed by the server; `name` stays the technical name from its
axes, and the page never derives a title from it (maps_contract.md §3.5).
