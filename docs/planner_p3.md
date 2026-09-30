# Planner P3: what was built

Phase P3 of [planner_vision.md](planner_vision.md) §8: production graph, alternates drawer,
pins. [planner-p3_contract.md](planner-p3_contract.md) is the specification; this file says
what the backend and the page do, where they depart, and what was measured.

## Backend

| Piece | Where | What it does |
|---|---|---|
| Production graph | `domain/planning/summary.py` `production_graph` | Built from the solver's own processes (item ids) on every feasible `solve_summary`. Sets `SolveRow.id` (`recipe_id`, else `label:<label>`, `#2`… appended to a repeat) and `SolveRow.depth` (`layout.chain_depth` over non-MW inputs and outputs). Nodes are inputs, rows in `rows` order, exports; ranks 0 / depth+1 / max+1. Edges split each consumer's need over producers by share of output, as the old TypeScript `graphOf` did; power edges into `ex:MW` carry MW words. Infeasible: `{nodes: [], edges: []}` |
| Row changes | `domain/planning/manage.py` `row_changes` | `result_delta` gains `rows`: added, changed (machines differ, or clock by ≥ 0.001), removed; sorted by change then label. Empty when either side is unsolvable, as the rest of the delta is |
| Alternates | `domain/planning/swaps.py` `swap_deltas` | One option per recipe whose first product is the item (every maker when none has it first). `require_ops`, `ban_ops`, `free_ops` per contract §5.3; the delta is `result_delta(head, head + require_ops)`. Ordered by status (in use, required, available, banned, locked), standard before alternates, then name; never by delta. A pattern ban and a locked recipe are not solved |
| Pins store | `domain/planning/pins.py` | `config.pins_dir()/<world>.json`, written under `filelock.held` with `atomic.write_text`; `version`, per-pin `rev`, numbers never reused, 500 live pins, labels ≤ 80. Field = 200 m single-link cluster of the node's resource, frozen at pin time. Resolution reads the plan heads, the projection and the node table once per call |
| `pin:` grammar | `origin.resolve_origin` (prefix `pin`), `spatial/select.py`, `factories/select.py`, `recall.plan_ref`, `pins.canonical` | See [selectors.md](selectors.md) "Pins". All of them call `pins.terms` / `pins.place` |
| Routes | `routers/planner.py` `plan_alternates`; `routers/pins.py`; `routers/planlog.py` | `POST /api/plan/alternates`; `GET/POST /api/pins`, `PATCH/DELETE /api/pins/{n}`; `create_plan`, `push_args`, `push_ops` store canonical members and answer 400 for a pin that cannot stand there |
| Tools | `tools/planning.py`, `tools/gamedata.py`, `tools/factories.py` | `plan_factory` rewrites `sources`, `required`, `exclude_recipes` pins before solving and saving; every `plan=` (and `site_plan`) takes a plan pin, and so does `name=` on `list_plans`, `rename_plan`, `forget_plan` and `plan_log`; a siting made at a pin stores the place it resolved to, never `pin:N`; `alternates_for_item(plan=)`; `ui_context` `pins:` line and `(pin:N)`; machine-select tools echo their pins |
| Journal | `routers/pins.py`, `tools/gamedata.py` | `pin.add` (not for an existing hit), `pin.edit`, `pin.drop` from the web; `plan.view` with `args.view = "alternates"` from the tool. No new SSE event name |

## Page

| Piece | Where | What it does |
|---|---|---|
| Result tabs | `planner-result.ts` | **build list · graph** (`tabs2`); the choice lasts the page session and is not in the address (C9). A plan whose head does not solve shows the last solvable version with no tabs |
| Graph | `planner-result.ts`, `graph.ts` | Drawn from `SolveResponse.graph`; columns from the server `rank`. Picking a node (click or Enter) outlines it and fills the node card below; with nothing picked the note says `click a process for its recipes`. Chat-changed nodes get the text badge **chat** and a 4 s outline pulse (none under reduced motion); process pins of the plan show as a `pin:N` badge. A detail line wider than a node wraps at its ` · ` separators instead of being cut |
| Drawer | `planner-alternates.ts` | `#dash=planner/<key>/alt/<item>`; Opening it from a row or node moves focus to its ×; Back, × or Escape close it and focus returns to the opener. Switching to another item, from a row or by following chat, replaces the address, so one Escape or Back closes the drawer. Beside the result only on the graph tab from 1280 px; above the result otherwise, and first in the DOM there so the tab order matches the screen. Require, ban and let the solver choose wait, dimmed, until the drawer has re-solved the current head, so a burst of clicks lands one version. Δ raw shows the two largest input changes, then `+N more inputs`, with the full list in the title. Re-requested on every new head and on `save`. After require, ban or let the solver choose, focus stays on the same recipe's row. Extractor and generator rows (no recipe) offer no **recipes** button. Until an item's name is known (loading, or an address naming no item) the heading is just `recipes` |
| Pins | `pins.ts`, `pins-card.ts` | Layer **pins** (chrome band, on by default) of focusable numbered tags, gone pins muted and dashed. Tags carry `title` and `aria-label` (Leaflet drops `alt` on a `divIcon`). Refetched on the live wave, on any `pin.*` activity, on a factory label change (`notes`) and on SSE reconnect. A gone pin that still has a place keeps its **map** button. Tags sit above-right of their place so the node dot under them stays clickable; tags at one place stack upwards. Delete is one request per row until it lands. The card sits on the plans list below Activity; its place column (**map** or **open plan**) has a fixed width so the other buttons line up. A rename in progress survives another tab's write; committing it on the old `rev` gets the 409 toast and the current row |
| Activity | `planner-history.ts` | Consecutive identical `plan.view` rows (same plan, actor kind and text) show once |
| Live stream | `sse.ts` | The `EventSource` closes on `pagehide` and reopens, with the reconnect resync, on a `pageshow` from the back/forward cache, so cached pages do not hold the browser's six connections per host |
| Pin buttons | node card, build-list rows, workbench header, factory detail header, map right-click popup, node dot popup | Kinds and refs as contract F4 |

## Departures from the contract

| # | Contract says | Built | Why |
|---|---|---|---|
| P1 | Drawer beside the result from 900 px | Beside only on the graph tab and from 1280 px; above the result otherwise | Beside a build list the table was squeezed to a column of wrapped rows at 1440 px |
| P2 | Factory position resolved at read time only | Also stored at create; a live label's centroid still wins, a gone factory keeps the stored place | A15 asks for a muted tag for a gone factory, which needs a place |
| B1 | `AlternatesResponse` in `planner.py` | `PlanAlternatesResponse` | `routers/gamedata.py` already publishes `AlternatesResponse`; a second model of that name would rename both in the generated schema |
| B2 | `PlanOpBody` in `planlog.py` | moved to `serial.py`, same name and docstring | Routers may not import each other and two now publish it |
| B3 | Handler/`PinError` set in §4.1 | adds `ObjectMissing(PinError)`, `pins.row`, `pins.match`, `canonical_args`, `canonical_ops`, `recall.plan_ref`, `factories.select.pin_notes`, `origin.label_centre` | 404 needs a type of its own; the rest are the shared helpers the routes and tools call |
| B4 | A point outside the map | 400 (`PinError`) | Nothing is absent, the request is malformed; map square is the web map's `DEFAULT_MAP_BOUNDS_M` |
| B5 | Row ids unique within a solve | a repeated `recipe_id` (two clock modes of one recipe) gets `#2`, `#3` | Uniqueness had to be guaranteed, and the solver can emit one recipe twice |

## Measured (2026-09-27, copy of the user-data backup, newest autosave, warm process)

| Call | Measured | Budget |
|---|---|---|
| `production_graph`, stored plans (6–11 rows) | 0.04–0.07 ms; 2.6–5.5 kB compact JSON | ≤ 1 ms, ≤ 8 kB at 40 rows |
| `production_graph`, a 42-row request (seven exports) | 0.23 ms; **18.9 kB** compact, 2.7 kB gzipped | ≤ 1 ms met; **≤ 8 kB not met** |
| drawer, every main product of the 3 stored plans (17 items, ≤ 4 options) | median 39 ms, p95 66 ms, max 71 ms (Fuel, 4 options) | p95 ≤ 250 ms |
| same over HTTP on the served app | 122 ms (Fuel, cold-ish) | – |
| field pin create, Iron Ore (127 nodes) | 7.7 ms | ≤ 30 ms |
| `pins.live` at 101 pins (one plan pin) | median 1.7 ms, max 6.3 ms | `GET /api/pins` ≤ 20 ms |
| pin write → `activity` on a second SSE stream | 0.16 s | ≤ 1 s |
| pin delete by another client → tag gone on the page | 0.07 s after the reply | ≤ 1 s |
| chat `plan_factory save_as` → **chat** badges on the page | 0.1 s after the tool returned | ~1 s |
| graph tab redraw incl. `drawGraph`, largest stored plan (13 nodes) | 3–14 ms | ≤ 50 ms |

The graph size budget of 8 kB at 40 rows could not hold with the contract's field set: recipe
class ids are the node ids and appear as `id`, `row` and on every edge (6.1 kB at 11 rows,
25.0 kB at 50 rows). The contract now states about 0.5 kB per row; gzip or trimmed fields stay
available if a remote client ever appears.

## Open

- `alternates_for_item(plan=)` rows follow the drawer's order (status), not the plain
  tool's alternates-first order.
- Chat creates pins through `show_on_map(pin=True)` (contract C1, decided 2026-09-30).
- The activity journal keeps the newest 50 entries server-side, `plan.view` included, so a run of drawer opens from chat can still push plan commits out of the page's Activity list.
