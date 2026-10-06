# Planner P3: the contract

> File paths are as of the commit this contract was written against; modules and tests have
> moved since, so look a name up rather than trusting its path.

The build contract for phase P3 of [planner_vision.md](planner_vision.md) §8: *graph,
alternates, pins*. It covers vision §4.2 (production graph), G5 (alternates drawer) and G11
(`pin:` in selectors). It builds on P1 ([planner_slice_contract.md](planner_slice_contract.md))
and P2 ([plan_management.md](plan_management.md)) and forks neither. Every plan write goes
through `PlanLog` and merge rule M1.

Where this file and the vision note disagree, this file wins for P3. Every departure is in §14.
Choices made here where the docs leave a product question open are in §15.

Binding decisions carried in (vision §9.1–9.2, frontend_vision §14): Host check on every
method; guarded writes with typed 409s and version counters; op log with M1; undo is an
inverse op; one shared state, autosaved per gesture; the page follows the agent by default;
one factory per plan; the solver picks recipes and required/banned override it; spoilers off
by default; deltas are facts, never ranked or coloured "best"; chat is marked by the text
badge "chat", never a hue; local only; the page never prompts the agent.

---

## 1. Scope

| In | Out (later phase or open) |
|---|---|
| Server-built production graph on `SolveResponse` (`graph`, row `id`/`depth`), drawn by `graph.ts` | Per-machine drill-down, belt tiers on edges, stable positions across solves |
| Result tabs **build list · graph** in the workbench | Track, Site, Layout, Byproducts tabs (P4–P7) |
| Graph node card: recipes, ban, pin, copy | Right-click context menus |
| **Chat badges** on graph nodes and build-list rows that chat's last edits changed | Outlining rows of a chat *solve* card (no plan edit) |
| **Alternates drawer** per item: every recipe, live re-solve deltas, Require / Ban / Let the solver choose as ops | `rank_unlocks` gain on locked rows (P9); per-row clocks (Q7) |
| `alternates_for_item(plan=)` with the same deltas, journalled as `plan.view` | A new MCP tool for swaps |
| **Pins** `pin:N` on plans, processes, machines, factories, fields, nodes, points: store, routes, map layer, graph tags, pins card | Pins created by chat (L6 open), pin notes, asks (`ask:N`) |
| `pin:` accepted by the place grammar, `sources=`, machine selects, `plan=`, `required=`/`exclude_recipes=` | `pin:` inside `bbox:`/`grid:` terms |
| `ui_context` lists pins and names a selection's pin | `satisfactory://ui/context` resource mirror |

---

## 2. User flows

### F1 Graph

1. Open `#dash=planner/<key>`. The result area shows tabs **build list · graph** (`tabs2`);
   the choice is remembered for the page session.
2. **graph**: inputs left, process nodes in columns by server `depth`, exports right. Edge width
   stays uniform (graph.ts as today); labels `item rate`.
3. Click or Enter on a process node: it becomes the planner selection (focus `selection`
   `{kind: "process", label, ref: recipe_id}`), is outlined, and the **node card** below the
   graph shows `building ×n · clock · MW`, in/out, and **[recipes] [ban] [pin] [copy]**.
4. Click an export node: the node card offers **[recipes]** for that item.
5. Ctrl+wheel, drag and double-click behave as in frontend_vision §9.8.

### F2 Alternates drawer

1. **[recipes]** on a node card or a build-list row opens the drawer for the row's main
   product. The address becomes `#dash=planner/<key>/alt/<item class id>` (Back closes it).
2. The drawer shows `recipes for <item> · v<head>` and a table, one row per recipe making the
   item: status, building, Δ machines, Δ MW draw, Δ MW net, Δ raw, actions. Deltas compare
   the head against the head with **Require** applied. None is toned or sorted by value.
3. **[require]** pushes that option's `require_ops` as one gesture (one version). **[ban]**
   pushes `ban_ops`. **[let the solver choose]** pushes `free_ops`. Each is undoable with
   Ctrl+Z like any own commit.
4. On every new head (own or chat) while the drawer is open, the page re-requests it; the table
   dims with `solving v<N>…` until the reply lands. Stale replies are dropped by sequence.
5. Locked recipes are muted rows with *granted by* and `–` deltas. With spoilers off they are
   omitted and the drawer says `<n> locked recipes hidden`.
6. A recipe banned by a pattern shows `banned by “<pattern>”`; its [ban] and [let the solver
   choose] are absent, and the line says to edit the banned list.
7. Escape or × closes the drawer and returns focus to the button that opened it.

### F3 Chat badges

1. Chat pushes onto the open plan (`plan_factory save_as=… base_rev=…`, `plan_log undo=`,
   etc.). The P1 strip lists the commits; P2 fetches `GET /api/plan/delta` from `F` to head.
2. `DeltaResponse.rows` (§5.3) names the process rows added or changed. Those graph nodes and
   build-list rows carry the text badge **chat** while the strip shows; the graph node also
   gets a 4 s outline pulse (none under `prefers-reduced-motion`).
3. Dismissing the strip or leaving the plan clears the badges.

### F4 Pins from the page

| Object | Where the **pin** button is | `kind` | `ref` sent |
|---|---|---|---|
| plan | workbench header | `plan` | `{plan: key}` |
| process | node card; build-list row | `process` | `{plan: key, recipe: recipe_id}` |
| factory | factory detail header | `factory` | `{factory: name}` |
| machine | map right-click popup on a machine | `machine` | `{machine: instance}` |
| node | node dot popup; right-click popup (nearest node) | `node` | `{node: instance}` |
| field | node dot popup; right-click popup (nearest node) | `field` | `{node: instance}` (server derives the field) |
| point | map right-click popup | `point` | `{x_m, y_m}` |

1. Pressing **pin** sends `POST /api/pins`. The reply's `pin:N` is copied to the clipboard and
   a toast says `pinned as pin:N · copied`. Pinning an object that already has a live pin
   returns that pin (`existing: true`), toast `already pin:N · copied`.
2. Located pins appear on the map as numbered tags (layer **pins**). Process pins of the open
   plan appear as a `pin:N` tag on their graph node and a chip on their build-list row.
3. The **pins** card on the plans list (`#dash=planner`) lists live pins: `pin:N`, what, label,
   state, and **[map] [rename] [copy] [delete]**. [map] flies to located pins; plan and process
   pins link to their plan instead.
4. Rename commits on Enter/blur (≤ 80 chars, fieldError when longer); delete asks nothing and
   is not undoable (numbers are never reused, so a deleted pin's number stays dead).

### F5 Pins in chat

1. `ui_context` lists pins and marks the selection's pin (§6.3).
2. The player says "why is pin:4 short?"; the agent passes `pin:4` wherever §7 allows.
3. The page's pin edits appear in `ui_context` "since you last looked" through the journal.

---

## 3. Data: the production graph (on `SolveResponse`)

Built by `summary.solve_summary` from the solver's own processes (item ids), with
`graph.chain_depth` over each row's non-MW inputs and outputs. TypeScript no longer builds
edges (`planner-result.ts graphOf` is deleted).

| Model | Field | Type | Rule |
|---|---|---|---|
| `SolveRow` (+2) | `id` | str | `recipe_id`, else `"label:" + label`. Unique within a solve; the join key for graph, pins, chat badges |
| | `depth` | int | `chain_depth` of the row; cycle members share one depth |
| `SolveResponse` (+1) | `graph` | `PlanGraph` | `{nodes: [], edges: []}` when infeasible |
| `PlanGraph` | `nodes`, `edges` | `list[PlanGraphNode]`, `list[PlanGraphEdge]` | nodes: inputs, then rows in `rows` order, then exports |
| `PlanGraphNode` | `id` | str | row → `SolveRow.id`; input → `in:<item name>`; export → `ex:<item name>` (`ex:MW` for power) |
| | `kind` | str | `process`, `input`, `export` |
| | `label`, `detail` | str | process: recipe name; `building ×n · ±MW · clock%` (format of today's `graphOf`). input: item; `from outside the plan`. export: item or `power`; `exported <rate>` |
| | `rank` | int | input 0; process `depth + 1`; export `max + 1` |
| | `row` | str \| null | process → `SolveRow.id` |
| | `item` | str \| null | item **class id** of the process's main product / the input / the export; null for power |
| `PlanGraphEdge` | `source`, `target`, `item`, `per_min`, `text` | str, str, str, float, str \| null | Same split as today: each consumer's need shared over producers by share of output. Power edges from generators to `ex:MW` carry `text` = MW words |

Budget: `graph` adds ≤ 1 ms and about 0.5 kB per row (≈ 20 kB for a 40-row plan). The first
figure, ≤ 8 kB, did not hold with class ids as node ids (25 kB measured at 50 rows); the page is
local, so the budget was restated rather than the fields trimmed, and no gzip is added
(settled 2026-09-30).

---

## 4. Data: pins

### 4.1 Store

`domain/session/pins.py`. File `config.pins_dir()/<world>.json` (new sibling of `ui_dir()`),
sanitised like `focus.path_for`. Writes hold `filelock.held(<file>)` and use
`atomic.write_text`; readers take no lock. The web process was the only writer in P3; since
C1 was decided, `show_on_map(pin=True)` creates pins from MCP processes too, under the same lock.

```json
{"schema": 1, "version": 7, "next": 5,
 "pins": [{"n": 3, "kind": "field", "ref": {"node": "BP_ResourceNode30_103", "resource": "Desc_OreIron_C",
           "nodes": ["BP_ResourceNode30_103", "…"]}, "x_m": 1204.0, "y_m": -3410.0,
           "label": "the good iron", "rev": 2, "created": 1790000000.1, "deleted": false}]}
```

- `n` starts at 1 and is never reused: `next` only grows; a delete sets `deleted: true`.
- `version` counts every write to the file; `rev` counts writes to one pin (create = 1).
- A `schema` above 1 raises `core.schema.NewerSchema` (503 on the web, refusal in tools).
- At most 500 live pins per world; `label` ≤ 80 chars after strip.
- **Identity** (dedupe key): `plan`/`key`; `process`/`key+recipe`; `factory`/`name`;
  `machine`/`instance`; `node`/`instance`; `field`/`resource + sorted nodes`; `point`/`x,y`
  rounded to 0.1 m. Creating a live duplicate returns the existing pin.
- **Field**: the single-link cluster (`geo.cluster`, 200 m) of the pinned node's resource that
  contains the node, frozen at pin time. Position = cluster centroid.
- **Position** stored at create for point, node, field, machine, factory; a live factory label's
  centroid and a plan's site origin are resolved at read time (null when unsited), and a gone
  factory keeps its stored place (P3-9).

### 4.2 Resolution and state

| kind | located at | `gone` when | `selector` (canonical text it stands for) |
|---|---|---|---|
| plan | site origin, else none | plan forgotten or unknown key | the plan's current name |
| process | none | plan forgotten | recipe class id |
| factory | label centroid | no label of that name in this save | `label:<name>` |
| machine | stored position | instance not in this save | `machine:<instance>` |
| node | stored position | never (map fact) | `node:<instance>` |
| field | stored centroid | never | `node:<id>` per member, comma-joined in `text` |
| point | stored position | never | `x,y` |

`gone_why` is one lowercase clause (`plan forgotten`, `no factory named “x” in this save`,
`machine not in this save`). A gone pin still resolves nowhere: selectors naming it refuse
with `gone_why`.

```python
SCHEMA = 1; MAX_LIVE = 500; LABEL_MAX = 80
KINDS = ("plan", "process", "machine", "factory", "field", "node", "point")
class PinError(ValueError): ...                  # bad kind/ref/label, unknown object
class PinMissing(PinError, KeyError): ...        # .n, .deleted: bool
class PinStale(PinError): ...                    # .pin (current row)
def path_for(world_id: str) -> Path: ...
def read(world_id: str) -> dict: ...             # the file, or an empty store
def create(st, kind: str, ref: dict, label: str = "") -> tuple[dict, bool]: ...   # (pin, existing)
def rename(world_id: str, n: int, rev: int, label: str) -> dict: ...
def drop(world_id: str, n: int, rev: int) -> dict: ...
def live(st) -> list[dict]: ...                  # rows as PinRow (§5.2), gone/selector filled
def get(st, n: int) -> dict: ...                 # PinMissing
def parse(text: str) -> int | None: ...          # "pin:3" -> 3, case-insensitive, else None
def position(st, n: int) -> tuple[tuple[float, float], str]: ...     # cm + echo, or PinError
def selector_terms(st, n: int, grammar: str) -> tuple[list[str], str]: ...  # §7; (terms, echo)
def expand(st, field: str, members: list) -> tuple[list, list[str]]: ...  # writer rewrite (§7.2)
```

---

## 5. Routes

All handlers declare `response_model`; 409 bodies are declared in `responses=` so they reach
`api-schema.d.ts`. `?world=`/`?save=` as on every route. Writes pass `guard.py` unchanged
(Host/Origin on every method). Newer-schema files are a 503 `{error, newer_schema: true}`.

### 5.1 Table

| Handler (operation id) | Method, path | Body / query | 2xx | Errors |
|---|---|---|---|---|
| `solve_plan` (changed) | POST `/api/plan/solve` | unchanged | `SolveResponse` + `graph`, rows + `id`/`depth` | unchanged |
| `plan_delta` (changed) | GET `/api/plan/delta` | unchanged | `DeltaResponse` + `rows` | unchanged |
| `plan_alternates` (new, `planner.py`) | POST `/api/plan/alternates` | `AlternatesBody {key: str, rev?: int, item: str}`; `?spoilers=0\|1` | `AlternatesResponse` | 400 bad body / solve `ValueError`; 404 unknown key, rev or item (`no item named “x”`) |
| `pins` (new, `pins.py`) | GET `/api/pins` | – | `PinsResponse` | 404 save unreadable; 503 |
| `create_pin` | POST `/api/pins` | `PinCreateBody {kind, ref: PinRef, label?: str}` | 201 `PinCreated`; 200 `PinCreated` with `existing: true` | 400 `PinError` (also 500 live pins reached); 404 object not found; 503 lock/schema |
| `rename_pin` | PATCH `/api/pins/{n}` | `PinRenameBody {rev: int, label: str}` | 200 `PinRow` | 400; 404 unknown or deleted; **409 `PinStaleResponse`**; 503 |
| `drop_pin` | DELETE `/api/pins/{n}` | `PinDropBody {rev: int}` | 200 `PinDropped {ok: true, n: int}` | 404; **409 `PinStaleResponse`**; 503 |

`push_ops` takes an optional `require_item` (an item class id) with a drawer **require**: under
the plan lock the server adds `remove required R'` for every other required recipe of that item
the head holds (`swaps.replaced_required`, C3), merged by M1 like the rest, so one required since
the page's base refuses with the usual 409 instead of leaving two.

`push_ops`, `push_args`, `create_plan` (planlog.py) additionally run `pins.expand` on
`sources`, `required`, `banned` members before the store (§7.2): a pin that cannot stand there
is a 400 naming it. Every pin write appends one journal entry (§8).

### 5.2 Models (TypedDict)

```python
class PlanGraphNode(TypedDict):
    id: str; kind: str; label: str; detail: str; rank: int; row: str | None; item: str | None
class PlanGraphEdge(TypedDict):
    source: str; target: str; item: str; per_min: float; text: str | None
class PlanGraph(TypedDict): nodes: list[PlanGraphNode]; edges: list[PlanGraphEdge]
# SolveRow += id: str, depth: int      SolveResponse += graph: PlanGraph

class RowChange(TypedDict):
    id: str; label: str; change: str        # "added" | "removed" | "changed"
    machines_before: int; machines_after: int; clock_before: float; clock_after: float
class ResultDelta(TypedDict):               # manage.result_delta's dict, now with rows
    comparable: bool; machines: int; mw_draw: float; mw_net: float
    buildings: list[DeltaRow]; inputs: list[DeltaRow]; rows: list[RowChange]; text: str
# DeltaResponse = ResultDelta + key, from_rev, to_rev (field set unchanged apart from rows)

class AlternatesBody(TypedDict): key: str; rev: NotRequired[int | None]; item: str
class SwapOption(TypedDict):
    recipe_id: str; name: str; alternate: bool; machine: str | None
    unlocked: bool | None; spoiler: bool; granted_by: list[str]
    status: str               # "in use" | "required" | "banned" | "available" | "locked"
    in_use: bool; required: bool; banned: bool; banned_by: str | None
    solved: bool              # false: locked or banned by a pattern; delta is then null
    delta: ResultDelta | None
    require_ops: list[PlanOpBody]; ban_ops: list[PlanOpBody]; free_ops: list[PlanOpBody]
class AlternatesResponse(TypedDict):
    key: str; rev: int; item: str; name: str
    head_feasible: bool; head_machines: int; head_mw_draw: float | None; head_mw_net: float | None
    options: list[SwapOption]; hidden: int; text: str

class PinRef(TypedDict, total=False):
    plan: str; recipe: str; factory: str; machine: str; node: str; x_m: float; y_m: float
    resource: str; nodes: list[str]            # filled by the server for fields
class PinRow(TypedDict):
    n: int; id: str                            # id = "pin:<n>"
    kind: str; ref: PinRef; label: str
    text: str                                  # "field Iron Ore · 4 nodes", "process Blender · Diluted Fuel in “north hmf”"
    selector: str; x_m: float | None; y_m: float | None
    rev: int; created: float; gone: bool; gone_why: str
class PinsResponse(TypedDict): version: int; pins: list[PinRow]
class PinCreated(PinRow): existing: bool
class PinCreateBody(TypedDict): kind: str; ref: PinRef; label: NotRequired[str]
class PinRenameBody(TypedDict): rev: int; label: str
class PinDropBody(TypedDict): rev: int
class PinDropped(TypedDict): ok: bool; n: int
class PinStaleResponse(TypedDict): error: str; stale: bool; pin: PinRow
```

### 5.3 Rules

- **Swap semantics** (`domain/planning/analysis/swaps.py swap_deltas(g, st, state, item_id, spoilers)`):
  for recipe R making item I (R's first product is I, or I is any product of R when no recipe
  has I first), `require_ops` = `add required R`, `remove required R'` for every other
  required R' whose first product is I, `remove banned R` when R is a literal banned member.
  `ban_ops` = `add banned R` + `remove required R` when present. `free_ops` = `remove
  required R` and/or `remove banned R` (literal members only; empty list = nothing to free).
  The delta is `manage.result_delta(solve(head), solve(head + require_ops))`. An option
  already equal to the head (`require_ops` all no-ops) still reports its delta (zero).
- **Option order**: in use, required, available (alternates after the standard recipe, then
  name), banned, locked. Never by delta.
- `status`: required beats in use; `banned_by` names the `banned` member that matches R,
  each member resolved against the save's unlocked recipes the way the solver resolves it (so
  an exact display name such as `Rubber` bans only that recipe, never the ones whose names
  contain it); a pattern match wins over a literal one and sets `solved: false`.
- `spoilers=0` drops locked options and counts them in `hidden`; without it every option comes
  with `spoiler` set, as `/api/gamedata/alternates` does.
- `text` = `recipes for Steel Beam in “north hmf” v14: 4 (1 in use, 1 locked)`.
- **Row changes** (`result_delta.rows`): rows joined by `SolveRow.id`; `added`/`removed` by
  presence; `changed` when machines differ or clock differs by ≥ 0.001. Sorted
  added, changed, removed, then label.
- **Pin 409**: `rev` ≠ the pin's current `rev` → 409 `{error: "pin:3 changed since you read it",
  stale: true, pin}`; nothing written.
- **Pin create validation** (404 when the object is absent): plan key live; recipe exists and
  is in the plan's head solve or its `required`; factory label exists; machine instance in the
  save; node instance in the map table; point finite and inside the map bounds.

---

## 6. MCP tools

### 6.1 Changes

| Tool | Change |
|---|---|
| `alternates_for_item` | New `plan: str \| None = None` ("a stored plan: add what requiring each recipe would change in it"). With `plan`, it recalls the head, calls `swaps.swap_deltas` and appends columns `in plan` (status), `Δmach`, `ΔMW draw`, `Δraw` (first two inputs), and a note `plan "x" v14; require one with plan_factory(plan="x", required=[...], base_rev=14, save_as="x")`. Journals `plan.view` with `args {"view": "alternates", "item": <item class id>}` |
| `ui_context` | New `pins:` line and `(pin:N)` after a selection that matches a pin (§6.3) |
| `plan_factory`, `plan_layout`, `diff_vs_save`, `commission_plan`, `explain_byproducts`, `rank_unlocks`, `search_resource_nodes`, `rank_build_sites`, `advise_hard_drive_pick` | `sources=` accept `pin:N` (via `spatial/nodes/selectors.py`) |
| `plan_factory` | `required=` and `exclude_recipes=` accept a process pin; `save_as` stores canonical members (§7.2); `site_at=` accepts any located pin (via `resolve_place`) |
| every `plan=` argument | accepts a plan pin (via `recall.recall_plan`) |
| `select_machines`, `name_factory`, `amend_factory`, and every `factory=` term | accept machine and factory pins (via `factories/select.py`) |
| `show_on_map`, `describe_location`, `site_plan`, `search_conduits`, `storage`, `collected_from_world` (`at=`/`near=`/`to=`) | accept any located pin (via `resolve_place`) |

No new tool. Descriptions stay one line; `test_surface.BUDGET` holds.

### 6.2 Echo and refusals

- Every expansion prints `pin:3 = node:BP_ResourceNode30_103 (Iron Ore, pure)` as a note,
  as places already echo what they resolved from.
- Refusals: `pin:9 does not exist (pins run to pin:6)`; `pin:3 was deleted`;
  `pin:4 is a process: it cannot stand for resource nodes`; `pin:2 is a point: write
  near:pin:2@<radius_m>`; `pin:5 is gone: plan forgotten`; `pin:1 is a plan with no site`.

### 6.3 `ui_context` additions

```
focus: planner › "north hmf" v14 › graph   selected: process "Blender · Diluted Fuel" (pin:4)
pins: pin:3 field Iron Ore · 4 nodes “the good iron” · pin:4 process Blender · Diluted Fuel in “north hmf” (+2 more)
since you last looked: … · journal: 14:06 page pinned pin:4 process Blender · Diluted Fuel
```

- `pins:` shows the newest 8 live pins, ascending by `n`, each ≤ 90 chars, then `(+N more)`;
  `pins: none` when empty. Gone pins end in `(gone)`.
- The selection match is `kind` + `ref` against pin refs (process: plan key + recipe id).
- The whole reply stays under `CONTEXT_BUDGET` (3800).

---

## 7. `pin:` in the grammars

### 7.1 Where each grammar resolves it

| Grammar | Resolver | Accepts | Becomes |
|---|---|---|---|
| place (`at=`, `near=`, `to=`, `site_at=`, `near:<place>@r` in both selector languages) | `places.resolve_place`, prefix `pin` | point, node, field, machine, factory, plan (sited) | the pin's position |
| node sources | `spatial/nodes/selectors.py` term `pin:N` | node, field | `node:<id>` (field: one per member) |
| machine select / `factory=` | `factories/select.py` term `pin:N` (with `-` exclusion) | machine, factory | `machine:<instance>` / `label:<name>` |
| `plan=` | `recall.recall_plan` | plan | the plan key |
| `required=`, `exclude_recipes=`/`banned` | tool boundary / planlog routes, via `pins.expand` | process | recipe class id |

All five call `pins.selector_terms` / `pins.position`; nothing else parses `pin:`.

### 7.2 Stored plans never hold `pin:`

Writers rewrite before the store: the MCP save path (`plan_factory save_as`, create and
push_args) and the web `create_plan`, `push_args`, `push_ops` (`add` ops on `sources`,
`required`, `banned`). A `near:pin:3@200` source becomes `near:<x>,<y>@200`. So a plan
reads the same after its pin is deleted, and `plan_id` and provenance see ordinary
selectors. A siting made at a pin (`site_at=pin:N`) echoes the pin in the reply but stores
the resolved place as its `origin_label`, without the `pin:N =` prefix.

---

## 8. SSE and journal

No new SSE event name.

| Journal kind | Writer | When | `plan`/`rev` | `args` | `text` |
|---|---|---|---|---|---|
| `pin.add` | web | 201 create (not an existing hit) | plan key for plan/process pins, else null | `{"n", "kind"}` | `pinned pin:4 process Blender · Diluted Fuel` |
| `pin.edit` | web | rename | as above | `{"n", "label"}` | `renamed pin:4 “x”` |
| `pin.drop` | web | delete | as above | `{"n"}` | `deleted pin:4` |
| `plan.view` (extended) | tools | `alternates_for_item(plan=)` | key/head | `{"view": "alternates", "item": id}` | `looked at recipes for Steel Beam` |

They reach the page as the existing `activity` event within ~0.5 s.

| Page reaction | Rule |
|---|---|
| `activity` kind `pin.*`, **any actor** (other tabs included) | refetch `GET /api/pins` (one request in flight, latest wins); redraw map tags, graph tags, pins card |
| `activity` `plan.view` with `args.view == "alternates"`, other actor | follow: go to `planner/<key>/alt/<item>` (after the gesture ends); toasts: toast with [open]; off: nothing |
| `save` | refetch pins (gone flags depend on the save); re-request the open drawer |
| `plans` for the open plan | as P1/P2; plus re-request the open drawer and redraw chat badges from the new strip delta |
| `plans` for any plan a plan or process pin names | refetch pins, so a plan forgotten (or restored) from chat turns its pins gone (or live) at once |
| reconnect resync | refetch pins with everything else |

---

## 9. Frontend

### 9.1 Modules

| Module | New/changed | Does | Primitives it must use |
|---|---|---|---|
| `graph.ts` | changed | `GraphNodeShape` gains optional `rank?: number` (when every non-terminal node has it, it replaces `ranks()`), `badges?: string[]` (drawn as small text tags top-right of the node, inside its box), and `drawGraph` gains an optional `pickable?: (node) => boolean` (default: `kind === "group"`), `picked?: string` (node id outlined in `--select`). `factories.ts` behaviour unchanged | `button`, `make`, `perMin`, `tone` (as today) |
| `planner-result.ts` | changed | `tabs2` **build list · graph**; graph from `SolveResponse.graph` (delete `graphOf`); node card; build-list row actions **[recipes] [ban] [pin]** (require moves to the drawer); `chat` and `pin:N` chips on rows | `tabs2`, `table`, `button`, `chip("chat","muted")`, `chip("pin:N","muted")`, `loading/empty/error`, `format.mw/pct/count/perMin/flow`, `graphCard` |
| `planner-alternates.ts` | **new** | the drawer: fetch, table, three actions via `gesture(ops)`, Escape/× close with focus return, spoiler count line | `table` (right-aligned Δ columns, no `tone` on deltas), `button`, `chip` (status: `muted` for locked, none otherwise), `loading/empty/error`, `format.mw({signed:true})`, `count`, `words.ts` |
| `planner-core.ts` | changed | `bench.tab` (`"build list"`\|`"graph"`), `bench.alt` (`{item, data, error, seq}`), `bench.chatRows: Record<string, true>` from `stripDelta.rows`, `loadAlternates()`, re-request on new head | existing `api.get/send/push` |
| `planner.ts` | changed | address `planner/<key>/alt/<item>` via `nav.dashParts`; focus `tab` = `"graph"`/`"workbench"`, drawer selection `{kind: "item", label, ref: item id}`; follow for `plan.view` alternates; pins refetch on `pin.*` | `nav.dashParts`, `nav.go`, settings `follow` |
| `planner-bench.ts` | changed | **[pin]** in the header for the plan | `button` |
| `planner-list.ts` | changed | renders the pins card below Activity | – |
| `pins.ts` | **new** | pins client: `registerFetch("/api/pins")` (live wave), `pinThis(kind, ref)`, rename/drop with 409 handling, map layer **pins** (divIcon tags, `title` = `pin:N “label”`, popup via `dom.popup` escaped rows with copyable `pin:N` and selector), `pinButtons(...)` markup + one delegated click listener (like `traceButtons`), `pinsFor(planKey)` for the graph | `registerFetch`, `layer()`/`BAND.chrome`, `dom.popup/esc/code/html`, `toast.note/fail`, `copy` click-to-copy, `map` fly |
| `pins-card.ts` | **new** | the pins card: `table` columns pin · what · label · state · actions; inline rename with `fieldError`; empty state `no pins yet: pin a plan, a process, or a place on the map` | `table`, `button`, `chip("gone","muted")`, `empty`, `fieldError`, `link` for plan pins, `words.ts` |
| `inspector.ts` | changed | right-click popup rows **pin**: point, machine (when on one), nearest node, its field | `pins.pinButtons` |
| `markers.ts` | changed | node dot popup: **pin node**, **pin field** | `pins.pinButtons` |
| `factory-detail.ts` | changed | header **[pin]** | `button` |
| `sse.ts` | changed | forward `activity` entries to `pins.onActivity` as well | – |
| `main.ts` | changed | FEATURES line for `pins.ts`; `listenForPins()` beside `listenForTraces()` | – |
| `words.ts` | changed | `PIN_KIND` words; `W.letSolverChoose = "let the solver choose"`, `W.recipes = "recipes"`, `W.pin = "pin"`, `W.lockedHidden(n)` | – |
| `style.css` | changed | `.pin-tag`, `.graph-badge`, `.graph-node.picked`, `.graph-node.flash` (+ reduced-motion), `.plan-drawer` | tokens only: `--panel`, `--ink`, `--line`, `--accent`, `--select`, `--muted`, `--fs-s`, `--sp-*`, `--r`, `--z-*` |
| `api-shapes.ts` | changed | aliases for §5.2 names | – |

No new colour, no new dependency, no `declareColours` entry (pin tags are DOM, not canvas).
Module rule: planner modules never import `dashboard.ts`; `pins.ts` imports no planner module
(the planner imports `pins.ts`).

### 9.2 Layout and states

- **Drawer**: an `aside.plan-drawer` with `aria-label="recipes for <item>"`, placed in the
  workbench after the result tabs. On the graph tab from 1280 px: a right column beside the
  result (the result narrows, no overlay); otherwise a block above the result, first in the DOM
  so the tab order matches the screen (P3-8). The Δ table scrolls inside
  `.dash-scroll` with its first column sticky (frontend_vision §15).
- **Node card**: a `dash-card` under the graph; empty until a node is picked, then one line of
  facts and the buttons. Picking again replaces it; Escape clears the pick.
- Loading/empty/error everywhere through dashkit; drawer error has retry.
- Deltas: `+2 machines` style via `format`; `–` when `solved` is false; `no longer solvable` /
  `solvable again` from `delta.text` when not comparable.

### 9.3 Map

| Layer / interaction | Behaviour |
|---|---|
| **pins** layer (BAND.chrome, after labels) | One `L.marker` + `divIcon` per located live pin, text `N`, class `.pin-tag` (gone pins add `.gone`, muted). `title` and `aria-label` set (Leaflet ignores `alt` on a `divIcon`). Keyboard-focusable (Leaflet marker `keyboard: true`) |
| tag click | popup: `pin:N` (copyable), label, what, selector (copyable), `gone_why` if gone |
| right-click (inspect) | popup gains a **pin** row: `point`, `machine` (on a machine), `node <resource>` and `field` for `nearest[0]` |
| node dot popup | **pin node**, **pin field** |
| [map] in the pins card | `nav.go("")` then fly to the pin at zoom ≥ current, and open its popup |
| plan pins with a site | tag at the site origin |

---

## 10. Chat ↔ page loop

| Chat does | Page (follow, the default) | toasts | off |
|---|---|---|---|
| edits required/banned on the open plan (`plan_factory save_as base_rev`) | new head, strip, **chat** badges + 4 s pulse on changed nodes/rows; drawer (if open) re-solves | same (edits to the open plan always show) | same |
| `alternates_for_item(item, plan=)` | opens `planner/<key>/alt/<item>` once the gesture ends | toast `chat looked at recipes for X` [open] | nothing |
| uses `pin:N` in any tool | nothing (reads never journal, as today) | – | – |
| `ui_context` | – | – | – |

| Page does | Chat sees |
|---|---|
| require/ban/free in the drawer | a version by `page` (`plan_log`, `ui_context` since-you-looked, `list_plans` last change) |
| opens the drawer / graph tab / picks a node | `ui_context` focus: tab `graph`, selection item or process, with `(pin:N)` when pinned |
| creates/renames/deletes a pin | `ui_context` `pins:` line and journal lines |

The page never moves while a field has the cursor (P1 rule).

---

## 11. Performance budget

Measured 2026-09-27 on a copy of the user-data backup (3 stored plans, newest autosave), warm
process, median of 5. Route budgets are in-process (TestClient), without the ~15 ms HTTP floor
Windows adds.

| Call | Measured | Budget |
|---|---|---|
| `solve_summary` | 10.1–12.6 ms (6–15 rows); payload 2.6–4.3 kB | + graph ≤ 1 ms, ≈ 0.5 kB per row (§3) |
| `chain_depth` on 6–15 rows | 0.01–0.02 ms | – |
| one swap option (solve + `result_delta`) | ≈ 10–13 ms | – |
| drawer, one item (3–5 options + head) | 30–64 ms (worst: Heavy Oil Residue, 5 options) | p95 ≤ 250 ms up to 8 options; one request per open and per new head |
| every item of a plan (not exposed) | 93–232 ms | not built: the drawer is per item on demand |
| `geo.cluster`, largest resource (127 nodes) | 1.9 ms | pin create ≤ 30 ms |
| `load_nodes` (cached) / labels / plans view | 0.8 / 0.5 / 2.0 ms | `GET /api/pins` ≤ 20 ms at 100 pins |
| pin edit → other tab | – | ≤ 1 s (0.5 s tail + refetch) |
| `drawGraph` | unmeasured | ≤ 50 ms at 40 nodes; the verifier measures it (A16) |

Not measured: a late-game save with every alternate unlocked (more options per item).

---

## 12. Test plan

| File | Owner | Covers |
|---|---|---|
| `tests/test_pins.py` (new) | backend | store: create/dedupe/rename/drop, numbers never reused, `rev`/`version`, 409-equivalent `PinStale`, label length, 500 cap, newer schema refused, torn/missing file reads empty; field = cluster of the node; gone rules per kind |
| `tests/test_pin_selectors.py` (new) | backend | each §7.1 row: accepted kinds resolve and echo; refused kinds use the §6.2 words; `near:pin:N@r` in both selector languages; deleted/gone pins refuse; `pins.expand` rewrites for stored plans |
| `tests/test_swaps.py` (new) | backend | `require_ops`/`ban_ops`/`free_ops` shapes (other required for the same item removed; literal banned removed); delta equals `result_delta` of the two solves; pattern ban → `solved: false`; locked hidden with spoilers off; order is by status, not delta |
| `tests/test_web_planner.py` | backend | `graph` nodes/edges/ranks on a known request; row `id`/`depth`; infeasible → empty graph; `/api/plan/alternates` 200/400/404; `DeltaResponse.rows` added/changed/removed |
| `tests/test_web_pins.py` (new) | backend | every route and status code in §5.1 incl. 409 bodies and guard refusal (bad Host on POST/PATCH/DELETE); journal entry per write; push/create with `pin:` members stored canonical |
| `tests/test_ui_context.py` | backend | `pins:` line, `(pin:N)` on a matching selection, `(+N more)`, budget |
| `tests/test_surface.py` | backend | description budget with the new `plan=` parameter |
| `tests/test_plan_tools_log.py` | backend | `save_as` with `pin:` in sources/required stores canonical members; `alternates_for_item(plan=)` journals `plan.view` |
| `tests/test_architecture.py`, `tests/test_comment_budget.py` | both run | module rules, FEATURES line, comment budget |
| frontend gates | frontend | `npm ci`, `npx tsc --noEmit`, `npm run build` |
| UI verification | verifier | §13 at 1440×900 and 390×844, screenshots read |

Both groups run Python tests with `PYTHONPATH=<worktree>/src` and the main venv, confirm
`satisfactory_mcp.__file__` is in the worktree, and set `SATISFACTORY_USER_DATA` to a scratch
copy. Never ports 8712/8713.

---

## 13. Acceptance checks (what a UI verifier must see)

| # | Check |
|---|---|
| A1 | A plan's workbench shows tabs **build list · graph** (`aria-pressed` on the current one). **graph** draws inputs left, exports right, processes in columns that match `rows[].depth`; no text is clipped past the frame, no page sideways scroll at 390 px |
| A2 | Clicking a process node (and Enter on a focused one) outlines it and shows the node card with **recipes / ban / pin / copy** |
| A3 | **recipes** opens the drawer; the address reads `#dash=planner/<key>/alt/<item id>`; Back closes it; Escape closes it and focus returns to the opener |
| A4 | The drawer lists every recipe for the item with a status chip, Δ machines, Δ MW draw, Δ MW net and Δ raw, right-aligned, with no red/green on any delta and no "best" marker |
| A5 | **require** on an option: the header goes to v+1 in one step, the row shows `required`, the drawer re-requests (dims then refreshes), Ctrl+Z returns to the previous version in one step |
| A6 | **let the solver choose** and **ban** each make exactly one version; a pattern-banned recipe shows `banned by “…”` without those buttons |
| A7 | With spoilers off, no locked recipe is listed and the line `N locked recipes hidden` shows; with them on, locked rows are muted with *granted by* and `–` deltas |
| A8 | An in-process chat `plan_factory(plan=…, required=[…], save_as=…, base_rev=…)` makes the strip appear within ~1 s, and the changed graph nodes and build-list rows show the text badge **chat**; no new colour appears |
| A9 | An in-process `alternates_for_item(item, plan=…)` opens the drawer for that item on the page (follow on); with follow off nothing moves |
| A10 | **pin** on a process: toast `pinned as pin:N · copied`; the node shows a `pin:N` tag; the pins card lists it; `ui_context` prints it under `pins:` and after the selection |
| A11 | Map right-click shows a **pin** row; pinning a point draws a numbered tag at that spot; a node dot popup offers **pin node** / **pin field**; the tag's popup shows the selector |
| A12 | In-process `plan_factory(sources=["pin:<field>"])` solves over that field and echoes `pin:N = node:…`; `show_on_map(at="pin:N")` resolves; a stored plan saved that way holds `node:` members, not `pin:` |
| A13 | Rename a pin in the pins card; a rename from a second tab on the old `rev` gets the 409 message and the refreshed row |
| A14 | Delete a pin: its tag disappears in a second tab within ~1 s; `pin:N` in a tool then refuses with `pin:N was deleted` |
| A15 | A factory pin whose label is forgotten shows **gone** with its reason in the card and a muted tag |
| A16 | `drawGraph` for the largest stored plan takes ≤ 50 ms (Performance panel or `performance.now()` around the call) |
| A17 | Tab walk through graph nodes, node card, drawer and pins card: every stop has the focus ring; icon-free buttons have names; no console errors |

---

## 14. Departures from planner_vision.md

| # | Vision says | P3 does | Why |
|---|---|---|---|
| P3-1 | Graph data = `processes[].rates`, `flows`, `depth[]` | One `graph` object built on the server; rows gain `id` and `depth` | TypeScript does no planning (vision §10); today's TS edge split moves to the domain |
| P3-2 | Alternates buttons: Require / Ban / Let the solver choose, one op each | One **commit** each, which may hold several ops (require also drops other required recipes for the item) | The delta shown must be the result the gesture produces |
| P3-3 | Pins store has one writer, no lock | A file lock anyway | The web serves writes from a thread pool; two tabs can write at once |
| P3-4 | Pins carry label + note | Label only | Smallest option (§15) |
| P3-5 | Pins inside any selector, as a reference | Resolved at use; **stored plans hold the canonical selector** | A deleted pin must not break a plan; the store stays free of pin state |
| P3-6 | Graph: edge width ∝ log(rate) | Uniform width as graph.ts draws today | No change to the shared drawing beyond badges, ranks and picking |
| P3-7 | Pins via right-click on any object | Buttons on node card, rows, headers and popups | Keyboard-reachable and uses existing primitives; no context menu primitive exists |
| P3-8 | (this contract, first draft) drawer beside the result from 900 px | Beside only on the graph tab and from 1280 px; above the result otherwise | Beside a build list the Δ table was squeezed to a column of wrapped rows at 1440 × 900 (about 560 px, four lines per row) |
| P3-9 | (this contract, first draft) factory places resolved at read time only | Also stored at create; a live label's centroid still wins, a gone factory keeps the stored place | A15 asks for a muted tag on a gone factory, which needs a place |

---

## 15. Choices made here (smallest option; open for review)

| # | Choice | Alternative |
|---|---|---|
| C1 | ~~Chat cannot create pins~~ **Decided 2026-09-30: `show_on_map(pin=True)`** pins what it shows; pinning twice returns the existing pin; the page refetches on the `pin.add` activity entry | A separate `pin` tool |
| C2 | Pins have a label, no note | Note field |
| C3 | Require replaces other required recipes for the same item; the server enforces it against the head (`require_item`, §5.1) | Keep them (N3 allows two required per item) |
| C4 | Build-list rows lose **require**; it lives in the drawer next to its delta | Keep four row buttons |
| C5 | Pinning an object twice returns the existing pin | A new number each time |
| C6 | The pins card lives on the plans list | A dashboard tab or the side panel |
| C7 | A field is the 200 m cluster of one resource, frozen at pin time | Re-cluster at read time |
| C8 | Chat badges count every change between the strip's first rev and the head (own later edits included), as P2's strip delta does | Replay per actor |
| C9 | Result tab choice is per page session, not in the address | `planner/<key>/graph` segment |
| C10 | A process pin whose recipe leaves the plan's result stays live; only a forgotten plan makes it gone | Gone when absent from the head solve |

---

## 16. File ownership

**BACKEND** and **FRONTEND** run in parallel; this contract is read-only for both.
`api-schema.d.ts` is regenerated offline by the **integrator** from `create_app().openapi()`
after both land; nobody edits it by hand. Until then the frontend types against the §5.2
names through `api-shapes.ts` aliases.

| Group | Owns (create or edit) |
|---|---|
| **BACKEND** | `src/satisfactory_mcp/domain/session/pins.py` (new); `domain/planning/analysis/swaps.py` (new); `domain/planning/readout/summary.py`; `domain/planning/stored/manage.py`; `domain/planning/stored/recall.py`; `domain/spatial/places.py`; `domain/spatial/nodes/selectors.py`; `domain/factories/select.py`; `src/satisfactory_mcp/config.py` (`pins_dir`); `interfaces/web/routers/bridge/pins.py` (new); `interfaces/web/routers/__init__.py` (append `pins.router` at the end); `interfaces/web/routers/plans/plan_solve.py`; `interfaces/web/routers/plans/planlog.py`; `interfaces/web/serial/` (only if a shape is shared by two routers); `interfaces/mcp/tools/planning.py`; `interfaces/mcp/tools/gamedata.py`; `interfaces/mcp/tools/factories.py`, `interfaces/mcp/tools/spatial.py` (only where a pin echo must be printed); `tests/test_pins.py`, `tests/test_pin_selectors.py`, `tests/test_swaps.py`, `tests/test_web_pins.py` (new); `tests/test_web_planner.py`, `tests/test_ui_context.py`, `tests/test_surface.py`, `tests/test_plan_tools_log.py`, `tests/test_plan_manage.py`, `tests/test_regions_and_select.py`; `docs/selectors.md`, `docs/mcp-surface.md`, `docs/web-wire.md`, `docs/planner_vision.md` (§8 P3 marked built), `docs/plan_management.md` (the §2.7 open item), `docs/planner_p3.md` (new: what was built) |
| **FRONTEND** | `src/satisfactory_mcp/interfaces/web/frontend/src/graph.ts`; `planner-result.ts`; `planner-alternates.ts` (new); `planner-core.ts`; `planner.ts`; `planner-bench.ts`; `planner-list.ts`; `pins.ts` (new); `pins-card.ts` (new); `inspector.ts`; `markers.ts` (node popup only); `factory-detail.ts` (header pin only); `sse.ts`; `main.ts` (FEATURES line and `listenForPins`); `words.ts`; `style.css`; `api-shapes.ts` |
| **integrator** | `frontend/src/api-schema.d.ts` (regenerated) |

**Seams.** The frontend codes against §5 and §8 only. The backend never edits TS/CSS; the
frontend never edits Python, tests or docs, and reports UI facts for `docs/planner_p3.md` in
its final notes. `factories.ts` must keep its graph unchanged: `graph.ts` changes are
additive and optional.

**Rules for both:** no code comments beyond a 1–2 line header on new files; `var`/`function`
style; no new dependencies; impersonal repo text; conventional commits without trailers; never
touch `%LOCALAPPDATA%/satisfactory-mcp` or the saves.
