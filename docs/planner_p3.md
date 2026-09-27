# Planner P3: what was built

Phase P3 of [planner_vision.md](planner_vision.md) §8: production graph, alternates drawer,
pins. [planner-p3_contract.md](planner-p3_contract.md) is the specification; this file says
what the backend does, where it departs, and what was measured. The page half (graph tabs,
drawer, pins layer and card) is recorded by the frontend group.

## Backend

| Piece | Where | What it does |
|---|---|---|
| Production graph | `domain/planning/summary.py` `production_graph` | Built from the solver's own processes (item ids) on every feasible `solve_summary`. Sets `SolveRow.id` (`recipe_id`, else `label:<label>`, `#2`… appended to a repeat) and `SolveRow.depth` (`layout.chain_depth` over non-MW inputs and outputs). Nodes are inputs, rows in `rows` order, exports; ranks 0 / depth+1 / max+1. Edges split each consumer's need over producers by share of output, as the old TypeScript `graphOf` did; power edges into `ex:MW` carry MW words. Infeasible: `{nodes: [], edges: []}` |
| Row changes | `domain/planning/manage.py` `row_changes` | `result_delta` gains `rows`: added, changed (machines differ, or clock by ≥ 0.001), removed; sorted by change then label. Empty when either side is unsolvable, as the rest of the delta is |
| Alternates | `domain/planning/swaps.py` `swap_deltas` | One option per recipe whose first product is the item (every maker when none has it first). `require_ops`, `ban_ops`, `free_ops` per contract §5.3; the delta is `result_delta(head, head + require_ops)`. Ordered by status (in use, required, available, banned, locked), standard before alternates, then name; never by delta. A pattern ban and a locked recipe are not solved |
| Pins store | `domain/planning/pins.py` | `config.pins_dir()/<world>.json`, written under `filelock.held` with `atomic.write_text`; `version`, per-pin `rev`, numbers never reused, 500 live pins, labels ≤ 80. Field = 200 m single-link cluster of the node's resource, frozen at pin time. Resolution reads the plan heads, the projection and the node table once per call |
| `pin:` grammar | `origin.resolve_origin` (prefix `pin`), `spatial/select.py`, `factories/select.py`, `recall.plan_ref`, `pins.canonical` | See [selectors.md](selectors.md) "Pins". All of them call `pins.terms` / `pins.place` |
| Routes | `routers/planner.py` `plan_alternates`; `routers/pins.py`; `routers/planlog.py` | `POST /api/plan/alternates`; `GET/POST /api/pins`, `PATCH/DELETE /api/pins/{n}`; `create_plan`, `push_args`, `push_ops` store canonical members and answer 400 for a pin that cannot stand there |
| Tools | `tools/planning.py`, `tools/gamedata.py`, `tools/factories.py` | `plan_factory` rewrites `sources`, `required`, `exclude_recipes` pins before solving and saving; every `plan=` (and `site_plan`) takes a plan pin; `alternates_for_item(plan=)`; `ui_context` `pins:` line and `(pin:N)`; machine-select tools echo their pins |
| Journal | `routers/pins.py`, `tools/gamedata.py` | `pin.add` (not for an existing hit), `pin.edit`, `pin.drop` from the web; `plan.view` with `args.view = "alternates"` from the tool. No new SSE event name |

## Departures from the contract

| # | Contract says | Built | Why |
|---|---|---|---|
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

The graph size budget cannot hold with the contract's field set: recipe class ids are the
node ids and appear as `id`, `row` and on every edge. Gzip on the web app, or a larger
budget, are the two ways out; neither is taken here.

## Open

- The 8 kB graph budget (above).
- `alternates_for_item(plan=)` rows follow the drawer's order (status), not the plain
  tool's alternates-first order.
- Pins created by chat stay out (contract C1, L6).
