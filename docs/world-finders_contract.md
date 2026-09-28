# World finders: the contract

Roadmap phase 6 of [frontend_vision.md](frontend_vision.md) §6: the seven spatial tools
(`search_resource_nodes`, `rank_build_sites`, `search_conduits`, `whereami`,
`describe_location`, `list_regions`, `show_on_map`) and `collected_from_world`, on the page,
with no chat needed. The vision note says what and why; this file says exactly what gets
built, so a backend and a frontend can build it at once. Where the two disagree, this file
wins for the phase. Departures are listed in §15.

**Binding and unchanged:** reads only (every route is a GET, Host check on every method, no
409 of its own); `as_of` pinning on every GET (§16 of the vision); spoilers off by default;
"need action" and its five states untouched; local only; the page never prompts the agent;
one question, one answer (a route calls the function its tool calls).

Base: `feat/world-finders` at `71bf388`. Frontend paths are relative to
`src/satisfactory_mcp/interfaces/web/frontend/src/`; backend paths to `src/satisfactory_mcp/`.

---

## 1. Scope

| In | Out (and where it goes) |
|---|---|
| Dashboard section **World** (`dash=world/...`): here, nodes, fields, conduits, pickups, regions | Transport networks (deferred) |
| Map finder card (`mapcard.ts`, id `finder`) and a finder pane drawn on the map | "Plan a factory here" from the inspector (planner phase; §17 Q6) |
| Right-click inspector upgrade: here, nearest nodes, fields, conduits, pickups, actions | "Storage near here" (inventory §10 "not yet") |
| `rank_build_sites` as the fields view's **rank** toggle | A pickup route planner (vision Q13 is open) |
| Spoiler flag on nodes and pickups; the map's node and pickup layers follow the switch | Items as spoilers (§12.4, still open) |
| Staleness of the node table, the collectible table and the save, said where it applies | Regenerating either table (tools/gen_*.py) |
| `show_on_map` links that ring a node or a run; the page's selection reported to `ui_context` | Chat moving the page on a finder call (§17 Q4) |
| Moving tool-body logic into the domain (vision §5.1) | Changing any tool's text beyond §6 |

---

## 2. User flows

### 2.1 Find free iron near me (dashboard)

1. Rail **World** → `dash=world` opens **here**: position, region, grid, save age, nodes
   within 500 m.
2. **nodes** tab. Filter bar: resource *Iron Ore*, status *free*, near *me*.
   Address becomes `world/nodes?resource=Desc_OreIron_C&status=free&near=me`.
3. Census line: "N nodes · X/min · Y free and reachable", the tool's header figures for the
   same filter.
   The table sorts by distance because `near` is set.
4. Row click: the row becomes the selection (`kind: "node"`). The status strip shows it.
5. **map** on the row: switches to the map, flies there, rings the node in the finder pane.
6. The `node:<id>` cell copies on click (principle 7). The player can paste it into chat.

### 2.2 Where is there a lot of copper, and where would I build (fields)

1. **fields** tab, resource *Copper Ore*: clusters with total, free, spread, region.
2. **rank** (a `dashkit.pressed` toggle, enabled only with one resource chosen) calls
   `/api/world/sites`. Columns switch to score and its raw components; one line states the
   weights. **rank** off returns to the plain list.
3. **map** on a field: flies to the field's box and draws the members in the finder pane.
4. **show all on map**: the whole current result (nodes, fields or ranked fields) is drawn
   and listed in the finder card; the card rows fly and select.

### 2.3 Is there a pipe between these two places (conduits)

1. **conduits** tab. near *me* (or `x,y`, a factory name, `chain:7`), radius 250 m, optional
   *to* and its radius, kind belt/pipe/all, view runs/networks.
2. Runs table: id, kind, length, ends, z span, carries, basis, connects. A `chain:`/`pipe:` in
   **connects** is a button that re-centres near on that run (the tool's walk, one click each).
3. A pipe network that touches both areas without one piece spanning them is listed above the
   table as the tool's "bridged" line.
4. **networks** view: one row per fluid network; **runs** on a row sets `network=<id>` and
   returns to the runs view.
5. **map** on a run draws its polyline in the finder pane and selects it (`kind: "conduit"`).

### 2.4 Which sloops are left, and the nearest one (pickups)

1. **pickups** tab: census per category (placed, collected, remaining, standing, never
   streamed), then a list: remaining | collected | nearest (from me), category filter.
2. Spoilers off: categories this save has never collected are hidden (§8.2), with one line
   "N kinds not found yet are hidden" and a link to Settings.
3. Row **map** flies, rings the pickup, and ticks that category's pickup layer on.
4. A table-age line appears when the collectible table is older than the save's build (§9).

### 2.5 Right-click anywhere (map)

1. Right-click opens the popup at once ("inspecting x, y m…"), then fills it from
   `/api/inspect`:
   - summary rows: region, elevation, grid and direction, nearest node, conduits within the
     radius, remaining pickups within 500 m, `at` (copyable);
   - **details**: elevation rows (as today), up to 5 nearest nodes with `node:` selectors, up
     to 3 fields, up to 5 pickups, any table-age note.
2. Actions (buttons in the popup): **select point**, **nodes near here**, **conduits here**,
   **pickups near here**, plus the existing trace buttons when the click lands on a machine.
   Each **… near here** opens the finder card at that point with that kind, draws the
   results, and closes the popup. **open in World** is a link to the same query in the
   dashboard (`world/nodes?near=x,y`, and so on).
3. The finder card: head "near x, y m", a `dashkit.tabs2` of nodes | conduits | pickups, one
   compact filter (resource select + free toggle; kind + radius; category), a table of at most
   25 rows with "N more" and **open in World** for the rest. Row click flies and selects.
   × or Esc closes it and clears the pane. Opening trace or lasso closes it (`claim`).

### 2.6 Where am I

1. `dash=world` (here) reads `/api/world/here`. It is registered in the live wave, so it
   follows each save. It shows "as of the save written 12 min ago" from `written_ago`,
   with the full `age_note` as the line's title.
2. **map** flies to the player and selects the point. **nodes near me**, **conduits near me**
   and **pickups near me** open those tabs with `near=me`.
3. No pawn in the save (a dedicated server): one `dashkit.empty` line; the rest of World works.

### 2.7 From chat to the page, and back

- `show_on_map at=node:<id>` (or `chain:<n>`, `pipe:<n>`) writes `show=<that ref>` into the
  local link. Opening it flies there, rings it in the finder pane and selects it. Label links
  (`show=label:<name>`) are unchanged.
- The page reports its selection in the focus heartbeat on every view (not only the planner),
  as `{kind, label, ref}` with `ref` the selector. `ui_context` prints it, so chat can resolve
  "the selected node" with `node:<id>`.
- Finder tool calls in chat do not move the page (§17 Q4).

---

## 3. Routes

All GET, all `?save=`/`?world=`, all pinned by `as_of` through the existing middleware. New
routes live in one router `interfaces/web/routers/finders.py`, prefix `/api/world`, appended to
`ALL_ROUTERS`. Errors are `{"error": ...}`. Numbers out of their declared range are FastAPI's
422; every domain refusal is 400; "needs a save and none loads" is 404. No route writes, so
there is no guard change and no 409 besides the pinning middleware's.

### 3.1 Table

| Path | Params (default) | Model | Codes | Domain |
|---|---|---|---|---|
| `/api/world/here` | `radius_m` (500, 1–5000); `spoilers` 0\|1 | `HereResponse` | 200, 404 no save | `place.here` |
| `/api/world/nodes` | `view` nodes\|fields\|nearest (nodes); `resource`; `purity` pure\|normal\|impure\|all; `kind` node\|well_sat\|geyser\|all; `status` all\|free\|tapped (all); `source` (repeatable selector); `near`; `spoilers` 0\|1 | `NodeFindResponse` | 200, 400 bad value / unresolvable near / nearest without near / every selector failed | `finder.find_nodes` |
| `/api/world/sites` | `resource` (required); `source` (repeatable); `limit` (10, 1–50) | `SitesResponse` | 200, 400 unknown resource, 404 no save | `finder.rank` |
| `/api/world/conduits` | `near` (me); `radius_m` (250, 1–2000); `to`; `to_radius_m`; `conduit_kind` belt\|pipe\|all; `view` runs\|networks; `network`; `run`; `offset` (0); `limit` (200, 1–500) | `ConduitsResponse` | 200, 400 bad value / unresolvable place / networks with belt, 404 no save | `conduits.search`, `conduits.networks` |
| `/api/world/regions` | `resource`; `spoilers` 0\|1 | `RegionTableResponse` | 200, 400 unknown resource | `regions.region_rows` |
| `/api/inspect` (changed) | `x_m`, `y_m`; `radius_m` (200, 1–2000) new | `InspectResponse` + fields in §3.2 | as today | `place.describe` |
| `/api/nodes` (changed) | as today | `NodeRow` + `spoiler` | as today | as today |
| `/api/collectibles` (changed) | as today + `spoilers` 0\|1 | `CollectiblesResponse` + fields in §3.2 | as today | `collect_view` + `service.census_rows` |

`spoilers` absent means every row plus its `spoiler` flag, so nothing changes for a caller that
does not send it (the §12.3 rule). `spoilers=0` drops spoiler rows and every count in the reply
counts only what is returned; `hidden_spoilers` says how many were dropped.

`resource` takes an item name or class id, resolved by `domain/planning/scenario.resolve_item`
(the tools' `_item_id`). `near`, `to` and `at` take the place vocabulary of `resolve_origin`
(`x,y` metres, `me`, a factory label, `node:`, `slab:`, `chain:`, `pipe:`, `plan:`).

### 3.2 Response models (TypedDict, declaration order = wire order)

Shared shapes go in `serial.py` only where two routers build them (`TableAge`, `Region`).

```python
class TableAge(TypedDict):            # serial.py; "is this map data older than the save"
    table: Literal["nodes", "collectibles"]
    behind: bool                      # the save's build is past the table's
    gap: str | None                   # "buildVersion 495413 -> 502094"; null when not behind
    moved: int                        # rows in THIS reply whose position drifted (nodes only)
    unjoinable: int                   # rows in this reply the save names differently (nodes only)
    observed_from: str | None         # collectibles: the session whose saves set `observed`
    observed_matches: bool | None     # collectibles: that session is this world's
    notes: list[str]                  # the domain's own sentences (skew_notes, build gap)

class FoundNode(TypedDict):           # finders.py
    id: str; name: str                # instance, leaf (the node: selector)
    resource: str; resource_name: str
    purity: str; kind: str
    x_m: float; y_m: float; z_m: float
    grid: str
    rate: float                       # at 100% clock, best extractor
    status: Literal["free", "tapped", "locked"]
    occupant: str | None              # "Miner Mk.2 @150%" or null
    occupant_off: bool | None
    region: Region | None
    distance_m: float | None          # set when near is set
    moved: bool                       # in TableAge.moved
    spoiler: bool                     # status == "locked"

class FoundField(TypedDict):
    key: str                          # "field:<smallest member leaf>", stable across saves
    selector: str                     # sites.selector(centroid, diameter): near:x,y@r
    members: list[str]                # member leaves
    region: str | None; grid: str; direction: str
    x_m: float; y_m: float            # centroid
    bbox_m: tuple[float, float, float, float]
    size: int
    purities: dict[str, int]
    resources: list[str]              # resource names; one unless the filter mixes
    total: float; free: float         # free = untapped and reachable
    spread_m: float
    locked: bool                      # any member unreachable
    distance_m: float | None
    spoiler: bool                     # every member locked

class NodeFindResponse(TypedDict):
    view: Literal["nodes", "fields", "nearest"]
    description: str                  # the selection's description, e.g. "region:Grass Fields"
    selectors: list[str]              # what was built from the filters (copyable)
    where: str                        # what near resolved to; "" without near
    nodes: list[FoundNode]            # [] for view=fields
    fields: list[FoundField]          # [] otherwise
    count: int; total: float; free: float
    unit: str                         # "/min", "m3/min" or "mixed"
    elevation: tuple[float, float] | None   # fluid only: z span, metres
    water: WaterBlock | None          # {bodies: dict[str, int], pumps: int,
                                      #  per_pump_m3_min: float | None, sea_level_m: float | None}
    choices: NodeChoices              # {resources: [{id, name, nodes}], purities: [str],
                                      #  kinds: [str]} over the whole table, not the filter
    notes: list[str]                  # selector errors, locked capacity, unresolved extractors
    hidden_spoilers: int
    stale: TableAge | None
    save_error: str | None

class SiteRow(TypedDict):             # SitesResponse = {resource, resource_name, description,
    rank: int; score: float           #  sites, count, weights, notes, stale}
    region: str | None; grid: str
    x_m: float; y_m: float; selector: str
    nodes: int; untapped: float; spread_m: float
    to_infra_m: float | None; purity: float
    alt_m: float | None; rough_m: float | None; slope_deg: float | None; wet_pct: float | None

class RunRow(TypedDict):
    id: str                           # chain:<n> | pipe:<n>
    kind: Literal["belt", "lift", "pipe"]; label: str
    pieces: int; length_m: float
    a: RunEnd; b: RunEnd              # {x_m, y_m, z_m, plugs: str | None}
    z_min_m: float; z_max_m: float
    directed: bool; basis: str | None
    carries: str | None               # fluid name, pipes
    rate: float | None                # belt cap /min, pipe m3/min
    network: int | None
    via: list[str]
    distance_m: float                 # to near
    lines_m: list[list[tuple[float, float]]]   # the drawn polylines, as /api/trace sends

class NetworkRow(TypedDict):
    network: int | None; carries: str | None; pieces: int; length_m: float
    x_m: float; y_m: float; z_min_m: float; z_max_m: float
    distance_m: float; touches: list[str]

class ConduitsResponse(TypedDict):
    view: Literal["runs", "networks"]
    where: str; where_to: str
    radius_m: float; to_radius_m: float | None
    runs: list[RunRow]; networks: list[NetworkRow]
    total: int; offset: int
    belts: int; pipes: int; belt_m: float; pipe_m: float; fluids: list[str]
    bridged: list[str]                # the tool's bridge sentences
    notes: list[str]
    age_note: str

class HereResponse(TypedDict):
    age_note: str; save_token: str
    written_ago: str | None           # "42 days ago", the subtitle; age_note is its title
    player: PlayerAt | None           # {x_m, y_m, z_m}; null with no pawn
    region: Region | None; grid: str | None; direction: str | None
    radius_m: float
    nodes: list[FoundNode]            # within radius_m, nearest first
    nodes_total: int
    nearest_building: NearestBuilding | None   # {name, distance_m}
    pawns: int
    stale: list[TableAge]             # nodes and collectibles, each when behind or joined badly

class RegionRow(TypedDict):           # RegionTableResponse = {resource, resource_name, rows,
    name: str; direction: str; grid: str   #  accuracy_m, hidden_spoilers}
    anchor_m: tuple[float, float]; area_km2: float; nodes: int

# NearestNode gains `spoiler: bool` (the page hides those rows by the flag).
# InspectResponse gains, after `nearest`:
    grid: str; direction: str
    conduits: ConduitCount | None     # {belt, pipe, radius_m}; null without a save
    fields: list[FoundField]          # up to 3: per-resource fields with a member within 500 m
    pickups: list[NearPickup]         # up to 5 remaining within 500 m, nearest first
    pickups_within: int | None        # how many remain within 500 m; null with no save
    pickups_within_spoilers: int      # of those, how many the spoiler switch hides
    stale: list[TableAge]
# NearPickup = CollectibleRow + {label: str, spoiler: bool}

# CollectibleRow gains `spoiler: bool`. CollectiblesResponse gains, after `where`:
    census: list[CensusRow]           # every mode; {category, label, placed, collected,
                                      #  remaining: int | None, standing, never_streamed,
                                      #  looted_standing, state_tracked, pedestal_of, spoiler}
    found: list[str]                  # categories this save has collected at least once
    hidden_spoilers: int
    stale: TableAge | None
```

`label` on pickups is the server's word for a category ("blue power slugs"), moved from
`markers.ts` `PICKUP_NAME` into `domain/collectibles/service.py` so the tool, the layer and the
tables use one vocabulary.

---

## 4. Domain moves (vision §5.1)

Each tool body becomes a call to the domain plus its text rendering. The route calls the same
domain function. `tests/test_world_parity.py` pins that both answer alike.

| New or changed domain function | Taken from | Used by |
|---|---|---|
| `domain/spatial/finder.py`: `find_nodes(st, game, *, sources, resource, purity, kind, status, view, near) -> NodeFind` | `search_resource_nodes` body (selection, annotate, status filter, distance, clusters, totals, notes, water block) | tool, `/api/world/nodes`, `place.here`, `place.describe` |
| `finder.fields(rows) -> list[FieldView]`, `finder.rank(st, game, resource, sources) -> SiteRank` | tool bodies of fields view and `rank_build_sites` | tool, `/api/world/nodes?view=fields`, `/api/world/sites` |
| `domain/spatial/place.py`: `here(st, game, radius_m) -> Here`, `describe(st, game, x, y, radius_m) -> Description` | `whereami`, `describe_location`, `routers/inspect.py` assembly | tools, `/api/world/here`, `/api/inspect` |
| `domain/world/conduits.py`: `search(st, near, radius_m, to, to_radius_m, kind, network, run) -> ConduitSearch`, `networks(st, origin) -> list[NetworkView]` | `search_conduits` body and `_networks_view` | tool, `/api/world/conduits` |
| `domain/spatial/regions.py`: `region_rows(table, rid, rows=None) -> list[dict]` | `list_regions` body | tool, `/api/world/regions` |
| `domain/spatial/nodes.py`: `table_age(header, table, instances) -> dict \| None` | `skew_for_save` + `skew_notes`, scoped | every node-bearing route |
| `domain/collectibles/service.py`: `census_rows(st)`, `found(st)`, `is_spoiler(category, found)`, `table_age(st)`, `LABELS` | `render_collectibles` census assembly, `PICKUP_NAME` | tool, `/api/collectibles`, `place.describe` |

`status` in `find_nodes`: `free` = untapped (as `only_free` today), `tapped` = an extractor
stands on it, `all`. "Locked" is not a filter; it is a row status and the spoiler.

---

## 5. SSE events

None new. Behaviour on the existing ones:

| Event | World section and finder card |
|---|---|
| `save` | The live wave refetches `/api/world/here` and `/api/collectibles` (registered). An open World view and an open finder card refetch their current query once the wave lands (`onVitals`, the trace pattern), each behind its `latest()` slot. Free/tapped and collected follow the save. |
| world or save switch (epoch bump) | Finder card and pane clear; World views reload; a reply for an old epoch is dropped |
| `notes`, `plans`, `activity` | ignored |

---

## 6. MCP tool changes

| Tool | Change | Text output |
|---|---|---|
| `search_resource_nodes` | body → `finder.find_nodes`; new `status: free \| tapped \| all` (`only_free=true` stays, means `status=free`) | unchanged except a tapped-only header word |
| `rank_build_sites` | body → `finder.rank` | unchanged |
| `search_conduits` | body → `conduits.search`/`networks`; new `network: int` (runs of one fluid network) | unchanged |
| `whereami` | body → `place.here` | unchanged |
| `describe_location` | body → `place.describe`; prints `nearest_node`, `fields` (count and the nearest), `pickups` (remaining within 500 m) | three new kv lines |
| `list_regions` | body → `regions.region_rows` | unchanged |
| `show_on_map` | local link carries `show=node:<leaf>` / `chain:<n>` / `pipe:<n>` for those places | link text only |
| `collected_from_world` | census built by `service.census_rows`; labels from `service.LABELS` | unchanged |
| `ui_context` | prints `ref` after a selection label when present: `selected: node "Iron Ore, pure" (node:BP_...)` | one line |

No tool gains a spoiler filter: spoilers are a page setting (§12.3 precedent). `docs/mcp-surface.md`
and `test_surface.py` follow the signature changes.

---

## 7. Frontend modules and primitives

No new primitive. Every table is `dashkit.table`, every action `dashkit.button`, every toggle
`dashkit.pressed`, every level-2 switch `dashkit.tabs2`, every state `loading/empty/error`,
every tag `chip`. Numbers through `format.ts`, words through `words.ts`, addresses through
`nav.ts`, map moves through `map.flyToBox`/`flyPadded`, the card through `mapcard.ts`.

| Module | Owns | Primitives it must use |
|---|---|---|
| `world.ts` (new) | section shell `renderWorld(body, subject)`, `tabs2` of the six views, **here** and **regions** views; registers `/api/world/here` (live wave); exports `here()`, `onHere()` | `tabs2`, `table`, `button`, `link`, `empty/loading/error`, `chip`, `format.num/perMin`, `W`, `nav.dashParts`/`subjectQuery`, `pointButton` |
| `world-nodes.ts` (new) | nodes, fields and the rank toggle | `table` (sortable), `pressed` (rank, free), `button` (map, show all on map), `chip` (status, moved), `code()` copy cells, `latest("world-nodes")` |
| `world-conduits.ts` (new) | runs and networks | `table`, `tabs2` (runs/networks), `button`, `latest("world-conduits")` |
| `world-pickups.ts` (new) | census and lists | `table`, `tabs2` (remaining/collected/nearest), `chip`, `markers.pickupName`, `crates`-style loot line from `markers.lootLine` (exported), `latest("world-pickups")` |
| `finder.ts` (new) | the map card `finder`, the `finder` pane (z 445, under trace's 450), `startAt(kind, x, y)`, `showRows(...)`, `showRef(ref)`, delegated click on `data-find` | `mapCard`, `claim`, `cardHead/Row/Line/Heading`, `tabs2`, `table`, `button`, `HIGHLIGHT`, `latest("finder")`, `select()` |
| `inspector.ts` | new rows and action buttons (popup HTML through `dom.popup`, all data escaped) | `popup`, `code`, `esc`, `traceButtons`, `FIND_ATTR` buttons |
| `selection.ts` | kinds `node`, `field`, `conduit`, `pickup` added; each carries `x_m`/`y_m` and `ref` | — |
| `status.ts` | kind words for the new kinds; `fly()` sends the new kinds to `finder.showRef` | existing |
| `dashboard.ts` | `["world", "World"]` after Inventory in `TABS`; route to `renderWorld` | — |
| `nav.ts` | `subjectQuery(subject) -> {head, params}` and `withQuery(head, params)`; `recipes.ts` `parseBrowse`/`browseDash` switch to them | — |
| `markers.ts` | node layer and pickup layer hide `spoiler` rows while the switch is off, redraw on `onSetting`; `PICKUP_NAME` is deleted and `pickupName` reads the census `label` from the collectibles reply (category words as the fallback before it lands); export `pickupName`, `lootLine`, `PICKUP_COLOUR`, `RESOURCE_COLOUR` | `settings.setting` |
| `panel.ts` | `showSelector` hands `node:`, `chain:`, `pipe:` to `finder.showRef` (one line) | — |
| `planner.ts` | `focusBody().selection` = the shared selection outside the planner workbench; `onSelect(scheduleFocus)` | — |
| `settings.ts` | spoiler hint: "later tiers, MAM trees, phases, locked recipes, locked nodes and unfound pickups" | — |
| `words.ts` | `free`, `tapped`, `locked`, `field`, `node`, `run`, `network`, `remaining`, `collected`, `neverStreamed: "never streamed"`, `mapDataBehind: "map data older than this save"` | — |
| `dom.ts` | `FIND_ATTR = "data-find"`, `FIND_AT_ATTR = "data-find-at"` beside the trace constants | — |
| `main.ts` | FEATURES line `import "./world";`; `listenForFinds()` beside `listenForTraces()` | — |
| `api-shapes.ts` | aliases for every §3.2 name | — |
| `style.css` | World filter bar, finder pane classes; tokens only, no hex | — |

Addresses: `world` (here), `world/nodes?…`, `world/fields?…[&rank=1]`, `world/conduits?…`,
`world/pickups?view=remaining|collected|nearest&group=…`, `world/regions?resource=…`. Keys match
the route params. Filter edits rewrite the fragment in place (`history.replaceState`), a tab
switch pushes. `document.title` = "World · Satisfactory", with the view name for sub-views.

---

## 8. Map layers and interactions

### 8.1 Layers

| Layer | Change |
|---|---|
| `node: <resource>` rows | `spoiler` dots (unreachable nodes) are not drawn while spoilers are off; the counts in the section head follow |
| `pickup: <category>` rows | spoiler categories are not drawn while spoilers are off; their rows stay in the control, disabled, titled "not found yet: turn spoilers on in Settings" |
| finder pane (new, not in the layer control) | rings for nodes and pickups (`HIGHLIGHT`, seed 11 px, others 7 px), a dashed box for a field, polylines for runs (as trace draws them); cleared by the card's ×, Esc, a world switch, or `claim` |

Switching spoilers needs no refetch: both layers already carry every row and the flag.

### 8.2 Spoiler rules (server side)

| Row | `spoiler` when |
|---|---|
| node | `reachable is False`: no extractor this world has unlocked can work it |
| field | every member is a spoiler |
| pickup, census row | its category is not in `found` (this save has collected none of it), **except** `crashed_drop_pod` and `loot_cache`, which are never spoilers (they stand in plain sight) |
| region row count | counts non-spoiler nodes when `spoilers=0` |
| conduits, sites, here's position | never (player-built, or already reachable-only) |

### 8.3 Interactions

| Gesture | Result |
|---|---|
| right-click | upgraded inspector (§2.5) |
| inspector **… near here** | finder card at that point, results in the pane |
| finder card row click / Enter | fly, ring, select |
| dashboard **map** | `toMap`, fly, ring, select |
| dashboard **show all on map** | `toMap`, whole result in the pane and the card |
| status strip **map** on a world selection | `finder.showRef(ref)` |
| `show=node:…` / `chain:…` / `pipe:…` in the fragment | `finder.showRef` after the static wave |

---

## 9. Staleness, said where it applies

| Source | Signal | Where it shows |
|---|---|---|
| Node table vs the save's build | `TableAge` from `nodes.table_age` (existing `TableSkew`, scoped to the rows in the reply) | a `chip` "moved" on affected rows (title: the drift note); one line above a table; inspector details |
| Collectible table vs the save's build | `TableAge.behind` when the save's `build_version` is past the table's `game_build` CL | one line under the pickups census: `W.mapDataBehind` + gap; pickups tab only |
| Collectible `observed` states | `observed_from` is the session whose saves the generator read; `observed_matches` false on any other world | the "never streamed" column title says whose saves it comes from; greyed when it does not match |
| The save itself | `age_note` | here view subtitle; finder card head title |
| Region names | `Region.confidence`/`accuracy_m` | the region cell's title, as the node popup does today |

On the reference save the node table has no skew and the collectible table is behind
(`CL 495413` vs `buildVersion 502094`): the pickups line shows, the node chips do not. A
fixture with a recorded skew covers the chip.

---

## 10. Performance budget

Measured on the reference save (Han Solo autosave, 608 nodes, 2,681 runs, 4,446 placements),
in-process, warm, median of 7; cold is the first call after start. Route budgets are
in-process (TestClient): over HTTP on Windows every call also pays a floor of about 15 ms
(timer tick plus the sync threadpool), which is not the route's cost.

| Call | Measured | Budget (warm) | Notes |
|---|---|---|---|
| `annotate` all nodes | 1.4 ms | — | |
| cluster all nodes, 200 m | 46 ms | — | mixed-resource fields |
| cluster one resource (iron) | 1.9 ms | — | per-resource fields |
| `st.conduit_runs` | 172 ms cold, 0 warm | — | cached on the state |
| runs near a point | 2.2 ms | — | |
| `collect_view` remaining / census | 7.8 / 4.0 ms | — | |
| `rank_build_sites` iron | 86 ms warm, 423 ms cold | — | terrain field load |
| elevation probe | 11 ms | — | |
| `/api/nodes` | 5.4 ms, 249 kB | +7% size | spoiler flag; measured 264 kB (+6%) on the branch |
| `/api/collectibles?mode=remaining` | 18 ms, 580 kB | +13% size | spoiler flag, census; measured 652 kB (+12%) on the branch |
| `/api/inspect` | 14–15 ms, 764 ms cold | ≤ 50 ms, ≤ 1 s cold | + per-resource fields, pickups |
| `/api/world/here` | tool 1.0 ms | ≤ 15 ms | live wave |
| `/api/world/nodes` view=nodes | tool 1.6 ms | ≤ 25 ms, ≤ 300 kB | every row sent; no paging |
| `/api/world/nodes` view=fields | tool 52 ms | ≤ 80 ms | |
| `/api/world/sites` | 86 ms | ≤ 150 ms, ≤ 1 s cold | only on **rank**, never per keystroke |
| `/api/world/conduits` | tool 3.2 ms | ≤ 30 ms, ≤ 400 kB | limit 200, radius ≤ 2000 m |
| `/api/world/regions` | tool 6.5 ms | ≤ 15 ms | |

Page: place inputs (`near`, `to`) query on Enter or when they lose focus, never per keystroke; each view holds one `latest()` slot (`world-here`,
`world-nodes`, `world-sites`, `world-conduits`, `world-pickups`, `world-regions`, `finder`);
the last rows stay on screen while the next query loads. Tables over 50 rows render 50 and a
**show all** button (the inventory pattern). No cache is added anywhere; measure again before
adding one.

---

## 11. Chat ↔ page loop

| Direction | What happens |
|---|---|
| chat → page | `show_on_map` links open the page on the thing, ringed and selected (§2.7). Nothing else moves the page. |
| page → chat | every row shows its selector (`node:`, `near:x,y@r`, `chain:`, `pipe:`, `region:`) as a copyable `code` cell; the shared selection reaches `ui_context` as `{kind, label, ref}` |
| same answer | a route and its tool call one domain function; `test_world_parity.py` compares them on one save |
| page prompts agent | never |

---

## 12. Test plan

Backend (`PYTHONPATH=<worktree>/src`, the main venv, `satisfactory_mcp.__file__` in the worktree):

| File | Covers |
|---|---|
| `tests/test_world_finder_domain.py` (new) | `find_nodes` filters (resource, purity, kind, status, region source, near), nearest ordering, totals, locked capacity; `fields`; `rank`; `place.here` no pawn; `place.describe` fields and pickups; `conduits.search` near/to/network/run and bridged; `region_rows`; `table_age` scoping; `census_rows`, `found`, spoiler rule incl. pods and caches |
| `tests/test_web_world_finders.py` (new) | each `/api/world/*` route: shape, defaults, every 400/404/422, `spoilers` absent vs `0` (rows dropped, counts follow, `hidden_spoilers`), `save_error` path, `as_of` 409 via the middleware, Host 403 |
| `tests/test_world_parity.py` (new) | tool vs route on the fixture: same node ids and order, same field centres and totals, same site order, same run ids, same census numbers |
| `tests/test_web_inspect.py` | new fields; `radius_m`; no-save path keeps nodes and nulls conduits |
| `tests/test_web_nodes.py`, `tests/test_web_collectibles.py` | `spoiler` flags; census; `spoilers=0`; `TableAge` with a patched build |
| `tests/test_node_search.py`, `tests/test_conduits.py`, `tests/test_collected_from_world.py`, `tests/test_ranking.py` | tool text unchanged after the move (existing assertions stay green); `status=tapped`; `network=` |
| `tests/test_local_map_link.py`, `tests/test_maplink.py` | `show=node:`/`chain:`/`pipe:` |
| `tests/test_ui_context.py` | selection `ref` printed |
| `tests/test_surface.py`, `tests/test_architecture.py`, `tests/test_comment_budget.py` | budgets, router list, FEATURES list |

Frontend: `npm ci`, `npx tsc --noEmit`, `npm run build`; schema regenerated offline from
`create_app().openapi()`.

---

## 13. Acceptance checks (UI verifier, 1440×900 and 390×844, spoilers off unless stated)

1. Rail shows **World** between Inventory and Recipes; `aria-current` on it in `dash=world*`.
2. `dash=world` shows position, region with confidence, grid, "as of" save age, and a nodes
   table; its numbers equal `whereami`'s for the same save.
3. Nodes: choosing Iron Ore + free + near me writes all three into the address; reload
   restores them; Back undoes a tab switch, not each keystroke.
4. Nodes census line equals `search_resource_nodes resource="iron ore" only_free=true` header.
5. No locked node appears with spoilers off; turning spoilers on in Settings shows them with a
   `locked` chip, and the map's node dots gain the faded ones without a reload.
6. Fields: **rank** is disabled without one resource; with Copper Ore it shows score columns
   whose order equals `rank_build_sites resource="copper ore" limit=10`.
7. Conduits near me: rows equal `search_conduits near=me` (ids, order); **map** on a run draws
   its line; a `chain:` in connects re-centres the query.
8. Pickups census: collected total equals `collected_from_world`'s `whole_world_collected`
   (719 on the reference save); the table-age line shows there.
9. With spoilers off, a category with no collection is absent from census, list and map layer,
   and "N kinds not found yet are hidden" shows; pods and loot caches stay.
10. Right-click on open ground: popup shows region, elevation, nearest node, conduits count,
    pickups count and four action buttons; **nodes near here** opens the finder card with
    rings on the map; × and Esc clear both.
11. Right-click on a machine keeps the trace buttons.
12. Selecting any finder row shows it in the status strip with **map** and **clear**; **map**
    rings it; `ui_context` in chat names it with its selector.
13. A `show_on_map at=node:<id>` link opens the map ringed on that node.
14. Opening trace or lasso closes the finder card; opening the finder closes them.
15. A save event (touch the save copy) refetches the open World view without losing filters
    or focus; a world switch clears the finder.
16. 390 px: no sideways page scroll on any World view; tables scroll inside `.dash-scroll`
    with a sticky first column; the finder card does not cover the rail or the popup.
17. Keyboard: every World tab, filter, row and action reachable with Tab, with the one focus
    ring; Enter on a row selects it; Esc closes the finder card.
18. No raw class id, `null`, `NaN` or `undefined` on screen; unknown values read `–`.
19. No hex in `style.css` outside `:root`; no new colour declared outside `palette.ts` owners.

---

## 14. File ownership

`api-schema.d.ts` is in neither list; the integrator regenerates it. This contract is
read-only for both.

**BACKEND**

- `src/satisfactory_mcp/domain/spatial/finder.py` (new), `domain/spatial/place.py` (new)
- `domain/spatial/nodes.py`, `domain/spatial/regions.py`, `domain/spatial/maplink.py`
- `domain/world/conduits.py`, `domain/world/sites.py` (only if `selector` moves)
- `domain/collectibles/service.py`, `domain/collectibles/removed.py` (census only)
- `interfaces/mcp/tools/spatial.py`, `interfaces/mcp/tools/progression.py` (`collected_from_world` only), `interfaces/mcp/tools/planning.py` (`_focus_line` only)
- `presenters/text/collectibles.py` (census source only)
- `interfaces/web/routers/finders.py` (new), `routers/inspect.py`, `routers/nodes.py`, `routers/collectibles.py`, `routers/__init__.py` (append only), `interfaces/web/serial.py` (`TableAge`)
- `tests/test_world_finder_domain.py`, `tests/test_web_world_finders.py`, `tests/test_world_parity.py` (new); `tests/test_web_inspect.py`, `tests/test_web_nodes.py`, `tests/test_web_collectibles.py`, `tests/test_node_search.py`, `tests/test_conduits.py`, `tests/test_collected_from_world.py`, `tests/test_ranking.py`, `tests/test_local_map_link.py`, `tests/test_maplink.py`, `tests/test_ui_context.py`, `tests/test_surface.py`
- `docs/frontend_vision.md` (§3.1 rows, §6 row 6, a new §18 "World finders"), `docs/mcp-surface.md`, `docs/selectors.md` (`show=` forms), `docs/spatial-and-map.md` (inspector paragraph)

**FRONTEND**

- `frontend/src/world.ts`, `world-nodes.ts`, `world-conduits.ts`, `world-pickups.ts`, `finder.ts` (new)
- `frontend/src/inspector.ts`, `selection.ts`, `status.ts`, `dashboard.ts`, `nav.ts`, `recipes.ts` (query helper only), `markers.ts`, `panel.ts` (`showSelector` only), `planner.ts` (`focusBody`, `onSelect` only), `settings.ts` (hint only), `words.ts`, `dom.ts` (constants only), `main.ts` (FEATURES line and one listener), `api-shapes.ts`, `style.css`

---

## 15. Departures from frontend_vision.md

| Vision says | Contract does | Why |
|---|---|---|
| §6: 3 new routes | 5 under `/api/world/` plus 3 changed | one route per tool; `/api/progress/` precedent (§11.2) for a prefix per tab |
| §5.2: `/api/nodes/fields`, `/api/sites/rank`, `/api/conduits` | `/api/world/nodes?view=fields`, `/api/world/sites`, `/api/world/conduits` | one prefix; fields are a view of the node search, as in the tool |
| §3.1: whereami as a header "me" button | World > here and the status strip selection | the header has no free slot at 390 px; smallest option |
| §2.1: context menu with a separate menu | the inspector popup carries the actions | one right-click answer, not two |
| §3.1: rank_build_sites under Planner > Site | fields view's **rank** toggle | the planner site step is a later phase |

---

## 16. Decided here, smallest option

- Collectibles are spoilers per category until this save collects one; pods and loot caches
  never are. Census counts follow the rows (T3).
- A node is a spoiler when no unlocked extractor can work it; the map's node layer follows.
- Staleness is shown only where the data is used, never in the status strip.
- The finder card lists at most 25 rows; the dashboard holds the rest.
- Selection kinds grow by four; the strip still offers only **map** and **clear** (§16.3 Q1
  stays open).
- **rank** scores every field of the one resource over the whole map, as `rank_build_sites`
  does with no source. While it is on, the purity, kind, status and near filters are disabled
  rather than silently ignored, and the census reads "top 10 of N".
- The page rounds a half to even, as the tools' Python formatting does, so a distance or a
  height reads the same number on the page and in chat. MW keeps its own rule.
- `/api/world/nodes` sends its notes in the page's words; the tool keeps its own sentences.
  Table age travels only as `stale`.

---

## 17. Open questions

1. Is "never collected one" the right spoiler line for pickups, or should positions follow a
   scanner unlock, or never be spoilers?
2. Should the pickup census counts (placed, remaining) stay visible for hidden categories?
3. Should unreachable nodes vanish from the map when spoilers are off, or stay faded as today?
4. Should a finder call in chat move the page (as plan events do under "follow chat")?
5. When the collectible table was generated from another world's saves, should `observed`
   be dropped rather than greyed?
6. Should the inspector offer "plan a factory here" once the planner takes a point source?
7. Should a world-wide Sites list (factory_sites) join World, or stay under Factories (§17.2)?
8. Should **rank** honour the near filter (as a `near:<place>@r` source) or purity, instead of
   disabling them?
