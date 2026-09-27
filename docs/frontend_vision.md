# Frontend vision: every tool, no chat needed

A design note, not a plan of record. Nothing here is built unless it says so.
The Factories + Power side panel (in progress) is step 0; everything below starts after it.

Scope: the 52 MCP tools, 4 resources and 3 prompts under `src/satisfactory_mcp/interfaces/mcp/tools/`,
and the web map under `src/satisfactory_mcp/interfaces/web/`. Where this note says "exists", it
means a route on master today.

---

## 1. Principles

1. **Local only.** Served by the FastAPI app on :8712. No CDN, no fonts, no telemetry, no
   outbound fetch. Same rule as the map today.
2. **The map is the spine.** Every screen is a panel beside the map, never a page that hides it.
   Every row that has a place has a *fly to*. Every map click can feed a form.
   One exception, argued in §8: the dashboard is a whole-page view, and it keeps the spine by
   giving every row a way back to the map.
3. **Live on save.** The SSE `save` event (it already carries `save_token`) refreshes open panels.
   A panel never mixes two saves: each fetch passes the token as `as_of=`, and a mismatch shows
   "save changed, refresh" instead of a blended answer (MCP §10.1i, same rule).
4. **One question, one answer.** A panel calls the same domain function the tool calls. The
   map and the chat must never disagree. No second implementation in TypeScript.
5. **Structured wire, not tool text.** Routes return typed JSON (docs/web-wire.md). The TSV
   renderers stay for the MCP side. Tables in the UI get real sorting and paging, so the
   context-budget caps (`le=25`) do not apply on the web.
6. **Honesty carries over.** Warnings and binding constraints render first, as they do in text.
   Census headers count the world, filters change rows only. `-` for unknown, never 0.
7. **Selectors are visible.** Every form that takes a selector shows the selector text it built,
   copyable (the click-to-copy in `copy.ts` already does this for popups). The UI teaches the
   MCP grammar instead of hiding it.
8. **Reads first, writes later, writes guarded.** The first writes landed on 2026-09-27:
   naming and forgetting a factory label (§9). They came with the Host/Origin guard from
   roadmap §4 item 5, which every non-GET request passes through (§9.2).
9. **Deep-linkable.** Panel, selection and form state live in the URL fragment beside the
   existing `#z=…&c=…` keys, so `show_on_map` links and bookmarks open the same view.

---

## 2. Information architecture

### 2.1 Shell

```
+--------------------------------------------------------------------------------------+
| [world v] Save: autosave_1  sav:3f2a91c0d4e1  12m ago  (live)   [ Search / jump...  ] |
+----+---------------------------------------------------+-----------------------------+
| F  |                                                   |  PANEL (the active rail tab) |
| P  |                                                   |                              |
| Pl |                   MAP (Leaflet)                   |  list  >  detail  >  sub-tab |
| Pr |                                                   |                              |
| I  |     layers, floor picker, inspector: as today     |  [fly to] [copy selector]    |
| W  |                                                   |                              |
| R  |                                                   |                              |
+----+---------------------------------------------------+-----------------------------+
| status: 3 warnings | power 2,389 / 3,100 MW | 22 starved | next milestone: affordable  |
+--------------------------------------------------------------------------------------+
```

- **Rail tabs:** F Factories, P Power, Pl Planner, Pr Progress, I Inventory, W World, R Recipes.
- **Panel:** list → detail → sub-tabs. Collapsible; the map keeps full width when closed.
- **Search / jump:** one box. Items, recipes, factories, plans, regions, node ids, `x,y`,
  `chain:<n>`. Enter flies the map or opens the detail. Backed by `search_items`,
  `search_recipes` and the place resolver.
- **Status strip:** one-line vitals, each a link into its panel. From `/api/summary`.
- **Map context menu** (right-click a point): *describe here*, *plan a factory here*, *nearest
  X*, *conduits near here*, *storage near here*. Each opens its panel with the point filled.

### 2.2 Screens

| Screen | Answers | Map ties |
|---|---|---|
| **Factories** (step 0) | What have I built, is it healthy, what does it make | Outline + health colour per factory; row click flies; lasso to select machines |
| **Power** (step 0) | Capacity vs draw, nameplate and measured, shards, sloops | Poles/wires layer; generators coloured by fuel state |
| **Planner** | Plan X/min or max MW, compare routes, bill, layout, site, startup order, progress | Source picker draws on the map; pad rectangle; candidate fields ranked on the map |
| **Progress** | Milestones, MAM, space elevator phase, hard drives, which unlock pays | Drop pods layer for hard drives; none otherwise |
| **Inventory** | Where is my X, how full are boxes, lost crates | Containers and crates layers; row flies |
| **World** | Nodes, regions, collectibles, conduits, whereami, describe a place | Every row is a map object; this panel is mostly map filters |
| **Recipes** | Codex: items, recipes, alternates, buildings, what I have unlocked | "Where is this made" highlights factories running the recipe |

### 2.3 The selection model

One global **selection**: a factory, a machine, a node/field, a plan, a point, a conduit run.
Map click sets it; panel rows set it. Panels that can answer about it offer a chip
("Trace upstream of *this*", "Plan here"). This is how the map ties everything together
without each panel inventing its own linking.

### 2.4 Factory detail (wireframe)

```
+-------------------------------- Factories > North Steel --------------------------------+
| [Summary] [Health] [Balance] [Machines] [Floors] [Links] [Power] [Trace]  (edit label) |
|  ! 6 machines starved of Caterium Ore -- root: Miner Mk.2 on a removed node  [show]     |
|  makes/min   measured  nameplate           needs/min   supplied   short                 |
|  Steel Beam   118.2     120                 Coal         240       0                    |
|  Steel Pipe    54.0      60                 Iron Ore     180      12   [find nodes]     |
|  health: running 41  starved 6  blocked 3  unwired 0  no generator 0  [paint on map]    |
+-----------------------------------------------------------------------------------------+
```

### 2.5 Planner workbench (wireframe)

```
+----------------------------------- Planner ----------------------------------------------+
| Goal: (o) item [Heavy Modular Frame v] [ 10 ]/min   ( ) max MW   ( ) keep plan [...v]   |
| Sources: [region: Grass Fields x] [near: 120,-340 @ 400 x] [+ draw circle] [free only]  |
|   -> 38 nodes, 11 fields (selector: region:Grass Fields, near:120,-340@400)  [copy]     |
| Recipes: [exclude...] [only...]   Clocks [100%]   Sloops [0 / 14 free]   Sinks [on]     |
| [Solve]   [Compare routes]   [Bill]                                                     |
+--------------------+--------------------------------------------------------------------+
| RESULT  sav:3f2a.. | ! binding: Water extractors capped at 200 (assumed, no site)       |
|  net 0 MW import   | process          machine       n   clock   in/min  out/min  BUILD   |
|  142 machines      | Encased Beam     Assembler     8   100%    ...     ...      +8      |
|  1,210 MW          | ...                                                                 |
| [Save as...]       | logistics: 14 flows (belt tier needed)  [pin item]                 |
| [Layout] [Site]    | byproducts: none stranded     [explain]                            |
| [Startup order]    | unlocks that would help: 3  [rank]                                 |
+--------------------+--------------------------------------------------------------------+
```

### 2.6 Progress (wireframe)

```
+---------------------------------- Progress ---------------------------------------------+
| [Next up] [Milestones] [MAM] [Elevator] [Hard drives] [Unlock value]                    |
| NEXT UP (facts, not advice)                                                             |
|  Elevator phase 4: short 1,200 Modular Engine, 300 Adaptive Control Unit                |
|  Affordable now: 3 milestones, 7 MAM nodes                        [show affordable]     |
|  Hard drives pending: 2 (1 reroll left each)                      [open]                |
| MILESTONES  tier [7 v]  show (o) todo ( ) affordable ( ) all   [query      ]            |
|  Tier 7 Bauxite Refinement   cost   have   short   grants                               |
+-----------------------------------------------------------------------------------------+
```

---

## 3. Every tool, mapped

Legend. **Route:** `exists` = a route on master already carries it (maybe partly); `new` =
needs a route. **R/W:** W writes the labels or plans store. Resources and prompts follow the
tool table.

### 3.1 Tools (52)

| Tool | Surface | Inputs as controls | Output view | Route |
|---|---|---|---|---|
| `list_worlds` | Header world picker | none | dropdown, saves per world | exists `/api/worlds` |
| `world_summary` | Status strip + World > Overview | none | vitals, last active schematic, problems | exists `/api/summary` (partial: add problems, warnings) |
| `unlocked_recipes` | Recipes > Unlocked | alternates-only toggle | sortable table | **built** `/api/gamedata/unlocked` (§12) |
| `power_report` | Power (step 0) | none | capacity vs draw, nameplate + measured, per generator kind | new (step 0 may add it) |
| `factory_sites` | Factory detail > Sites (world list: open) | none | cluster list; map clusters | **built** `/api/factories/sites` (§17) |
| `whereami` | Header "me" button; World > Here | radius slider | player marker + nearby list | exists (`/api/summary.player`) + new for the nearby list |
| `list_regions` | World > Regions; region picker in every sources form | resource filter | list; region layer highlights | exists `/api/regions` (add resource filter) |
| `describe_location` | Map click inspector | click point, radius | popup: region, elevation, nodes, conduits, buildings | exists `/api/inspect` (same default radius); add conduits/buildings counts |
| `search_conduits` | World > Conduits; context menu "conduits here" | near (click), radius, to (second click), belt/pipe, runs/networks | run list; runs highlighted on the map | new (geometry exists in `/api/belts`, `/api/pipes`) |
| `search_resource_nodes` | World > Nodes | sources builder, resource, purity, kind, free only, view fields/nodes/nearest, near | field clusters or node rows; map filters to the result | partial `/api/nodes`; new for fields and nearest |
| `show_on_map` | Built in: every *fly to* and the URL fragment | n/a | map moves, layers tick | exists (fragment); the tool's local link follows the configured port (§13) |
| `rank_build_sites` | Planner > Site > "where to mine X"; World > Nodes | resource, sources | ranked fields with raw components; numbered pins on map | new |
| `list_plans` | Planner > Plans list | name filter | table: sited, world moved, field moved | exists `/api/plans` (siting only) + new for status |
| `forget_plan` | Plans list row menu | confirm | row gone | new, W |
| `rename_plan` | Plans list row menu | text | row renamed | new, W |
| `site_plan` | Planner > Site | drag/rotate the pad on the map, WxD fields, clear | pad on map; terrain note | new, W (drawing exists in `plans.ts`) |
| `plan_factory` | Planner workbench | goal, sources builder, exports, minimums, clocks, recipe include/exclude, sloops, sinks, water extractors, supplied, save as, notes, for factory, site at | warnings first, summary, process table with BUILD, logistics flows | new (POST solve) |
| `plan_layout` | Planner > Layout | view: floors/blocks/buses/trunks/materials/sites; floor cap, belt/pipe tier, fit to factory | floor-by-floor block diagram; fit verdict; pad overlay | new |
| `diff_vs_save` | Planner > Progress (of a plan) | plan, factory scope, stage | to-place / to-remove / cost; ON SITE census | new |
| `explain_byproducts` | Planner result "byproducts" chip | item | stranded byproducts + legal consumers | new |
| `compare_recipe_options` | Planner > Compare; Recipes > item "compare routes" | item, rate, per resource, outlets | ranked routes, cost columns | new |
| `bom` | Planner > Bill; Recipes > item "bill" | item, qty, outlets, include/exclude | raw totals + per-item rows, loop note | new |
| `commission_plan` | Planner > Startup order | plan, headroom MW | waves: energise these, MW before/after | new |
| `rank_unlocks` | Progress > Unlock value; Planner result chip | plan (or current form), query | alternates by gain, granted by, INFEASIBLE marked | new (slow: many solves; measure) |
| `stock` | Inventory > Stock | item, "where" toggle | four piles as columns; where rows fly to box | **built** `/api/stock` (§10) |
| `storage` | Inventory > Containers | item, near (click), radius, solid/fluid, show empty | container table with fill bar; map filter | **built** from `/api/stock` (§10); filters client-side |
| `crates` | Inventory > Crates | none | list, sorted by distance to me | **built** from `/api/stock` (§10) |
| `phase_requirements` | Progress > Elevator | none | have / short / deliverable | **built** `/api/progress/phase`, Progress > Space Elevator (§11) |
| `power_shards` | Power > Shards | plan machines, plan clock | held / committed / free; cost of an overclock plan | **built** `/api/progress/shards`, Progress > Power shards (§11) |
| `somersloops` | Power > Sloops | none | held / slotted / owned; where slotted | **built** `/api/progress/sloops`, Progress > Somersloops (§11) |
| `mam_research` | Progress > MAM | show todo/affordable/all, query | tree or table with cost, have, affordable | **built** `/api/progress/mam` (§11) |
| `milestones` | Progress > Milestones | show, tier, query | per tier cost / have / short / grants | **built** `/api/progress/milestones` (§8, §11) |
| `collected_from_world` | World > Collectibles | group, show census/collected/remaining/nearest, near | census + list; pickups layer | exists `/api/collectibles` |
| `list_pending_hard_drive_choices` | Progress > Hard drives | none | per drive: two options, rerolls, recipes granted | **built** `/api/progress/harddrives` (§11) |
| `advise_hard_drive_pick` | Progress > Hard drives > "rank options" | drive, sources | options by marginal value | new (slow: counterfactual LPs) |
| `search_items` | Search box; Recipes > Items | query | list: form, energy, sink points | **built** `/api/gamedata/items`, `/api/search` (§12) |
| `recipe_detail` | Recipes > recipe card | recipe | rates, machine, power, unlocked by | **built** `/api/gamedata/recipe` (§12) |
| `alternates_for_item` | Recipes > item card | item, include locked | recipes that make it, HAVE/LOCKED | **built** `/api/gamedata/alternates` (§12) |
| `search_recipes` | Recipes > Search | query, consumes, produces, part/building/manual/all, alternates only, events | census header + rows | **built** `/api/gamedata/recipes` (§12) |
| `list_buildings` | Recipes > Buildings | building kind | table | new |
| `factory_map` | Factories > Candidates / Slabs / Unlabelled | show candidates/named/slabs/unlabelled/all | lists; slab outlines on the map | exists `/api/factories`, `/api/structures` (partial) |
| `factory_query` | Factory detail tabs | factory, aspects (summary, machines, recipes, buildings, balance, inputs, outputs, internal, power, nodes, links, issues) | one sub-tab per aspect | **built** `/api/factories/aspects` (§17) |
| `factory_health` | Factory detail > Health; Factories list colour | factory or all | per state counts, why stopped, evidence line | new (map colours exist via `/api/machines`) |
| `propose_factories` | Dashboard > Factories > unnamed factories | none (default span, unnamed only) | candidates with products, region, suggested name; map outline | **built** `/api/factories/candidates` (§9) |
| `select_machines` | Name/amend dialog, live preview | selector text or map lasso, split, expand | highlighted machines + count | new |
| `name_factory` | Dashboard > Factories > unnamed factories | name (prefilled), one proposal | new label; joins the table and panel | **built** `POST /api/labels`, W (§9); selector/lasso still open |
| `rename_factory` | Factories table, factory detail, side panel: edit in place | text | renamed; plans follow | **built** `PATCH /api/labels/{name}`, W (§9.7) |
| `amend_factory` | Factory detail > edit | add/drop via lasso or selector, prune missing, dry run | diff preview, then applied | **built** by lasso `POST /api/labels/amend`, W (§9.9); selector and prune still open |
| `list_factories` | Factories list (step 0) | none | named factories, % still standing | exists `/api/factories` |
| `forget_factory` | "undo" after naming in the dashboard | none | label gone | **built** `DELETE /api/labels/{name}`, W (§9); detail-view button still open |
| `trace_upstream` | Machine popup, right-click inspector, factory detail, side panel > Trace | seed (selection), up/down | path drawn on the map; items, rates, flows in a card | **built** `/api/trace` (§13) |
| `factory_floors` | Factory detail > Floors; floor picker | factory or platform | decks with machines; floor picker jumps | exists `/api/floors`; detail tab **built** (§17) |

### 3.2 Resources (4)

| Resource | Surface |
|---|---|
| `satisfactory://save/current` | Header: world, file, token, age |
| `satisfactory://docs/summary` | About dialog: game data census and hash |
| `factory_labels` | Factories list data (same store as `/api/factories`) |
| `map_regions` | Region layer + region picker; show its accuracy caveat once |

### 3.3 Prompts (3)

Prompts are chat procedures. In the UI they become **presets** that prefill a form.

| Prompt | Preset |
|---|---|
| `design_factory(target_item, rate)` | Planner, goal = item at rate |
| `plan_power_plant(fuel, sources)` | Planner, goal = max MW, sources prefilled |
| `pick_hard_drive(id)` | Progress > Hard drives, drive preselected, "rank options" run |

---

## 4. Key flows

### 4.1 Plan X/min, build it, bring it online, track it

1. Recipes or search: pick *Heavy Modular Frame* → **Plan this**. Planner opens, goal filled.
2. **Sources:** pick a region, or draw a circle on the map. Live count: "38 nodes, 11 fields".
3. **Compare routes** (`compare_recipe_options`): ranked routes. Pick one → it fills
   *only recipes* in the form.
4. **Bill** (`bom`): sanity check of raw totals.
5. **Solve** (`plan_factory`). Warnings first. **Save as** "north hmf".
6. **Where to mine** (`rank_build_sites`) pins candidate fields. **Site** (`site_plan`):
   drag the pad on the map, rotate, see the terrain note.
7. **Layout** (`plan_layout`): floor diagram, fit against an existing platform if chosen.
8. Player builds in game. The map updates on each save.
9. **Progress** (`diff_vs_save plan=`): to place, ON SITE census, "world moved" badge.
10. **Startup order** (`commission_plan`): energise waves under headroom.
11. When done: **Name it** (`name_factory` from the pad's machines). It joins Factories.

Note: `commission_plan` is a startup order, not a build tracker. Tracking is `diff_vs_save`.

### 4.2 What should I unlock next

1. Progress > **Next up**: elevator shortfall, affordable milestones and MAM nodes. Facts only.
2. **Unlock value** (`rank_unlocks`) against a chosen plan: which locked alternates pay, and
   what grants each.
3. Hard drives: pending choices; **rank options** (`advise_hard_drive_pick`).
4. A milestone row links its missing items into Inventory (`stock`) and into Planner
   ("plan N/min of the short item").

How these rank against each other is an open question (§7). The UI does not merge them into
one "best next" score.

### 4.3 Why is this factory starving

1. Factories list: red dot. Or a starved machine on the map.
2. Factory > **Health** (`factory_health`): per machine state, and the evidence line from the
   physical graph (already consumed by health).
3. **Trace** (`trace_upstream`) from the starved machine: path drawn on the map.
4. At the end: a node gone, a paused miner, a belt ending at nothing. **Conduits here**
   (`search_conduits`) for the run. **Find nodes** (`search_resource_nodes`) if supply is short.
5. Fix in game. Next save clears the dot (live refresh).

### 4.4 Where is my X

Search "Quartz" → item card → **Stock** (four piles) → **where** rows → fly to the box.
Crates list for lost items, sorted by distance from `me`.

### 4.5 Name what I built

Factories > Proposals → pick one → outline on map → **Name** dialog previews via
`select_machines` → adjust by lasso (add/drop) → save. Later **amend** with the same lasso.

---

## 5. Backend work

### 5.1 The pattern

- Routes call **domain services**, never the tool functions and never their text.
  Where logic sits in a tool body, move it to the domain first. MCP §10.1e names the cases
  (`search_resource_nodes`, `factory_query` table-building); both tools and routes then share it.
- Typed `response_model` per route; regenerate `api-schema.d.ts` (docs/web-wire.md).
- Solves go through `planning/prepare.py`, the one sequence all planning tools share.
- Save reads through the injected loader and the single-flight cache, as today.
- Every route accepts `as_of=`. Refuse on mismatch with the same four messages as MCP.

### 5.2 Routes by phase

| Route (proposed) | Method | Domain source | Notes |
|---|---|---|---|
| `/api/power/report` | GET | `domain/power/report.py` | step 0 may already add it |
| `/api/factories/aspects?factory=` | GET | `domain/factories/query.py` | **built** (§17); every aspect in one call |
| `/api/factories/{id}/health` | GET | `domain/factories/health.py` | `all` for the list colours |
| `/api/trace` | GET | `domain/factories/trace.py` | seed, direction; **built** (§13) |
| `/api/stock` | GET | `domain/world/inventory.py` | four piles and every place; **built** (§10) |
| `/api/progress/{milestones,mam,phase}` | GET | `domain/progression/ladder.py`, `phases.py` | **built**; one ladder, three views |
| `/api/progress/harddrives` | GET | `domain/progression/harddrives.py` | **built**, under `/api/progress/` |
| `/api/harddrives/{id}/advice` | POST | `domain/planning/advisor.py` | slow |
| `/api/progress/shards`, `/api/progress/sloops` | GET | `domain/progression/shards.py` | **built**, under `/api/progress/` |
| `/api/gamedata/{items,recipes,recipe,alternates,unlocked}`, `/api/search` | GET | `core/gamedata` | **built** (§12); no save needed except HAVE/LOCKED; `buildings` still open |
| `/api/nodes/fields`, `/api/sites/rank` | GET | `domain/spatial/select.py`, `ranking.py` | |
| `/api/conduits` | GET | `domain/world/conduits.py` | |
| `/api/select/nodes`, `/api/select/machines` | GET | the two selector modules | live preview counts |
| `/api/plan/solve`, `/bom`, `/compare`, `/byproducts` | POST | `prepare.py`, `bom.py`, `compare.py`, `byproducts.py` | body = plan kwargs |
| `/api/plan/layout`, `/diff`, `/commission`, `/unlocks` | POST | `layout_service`, `diff_service`, `commission_service`, `sensitivity` | `/unlocks` slow |
| `/api/plans` (CRUD) + `/api/plans/{n}/site` | POST/PATCH/DELETE | `domain/planning/store.py`, `siting.py` | W; watcher already publishes store events |
| `/api/labels` (CRUD) | POST/PATCH/DELETE | `domain/factories/labels.py` | W; POST, PATCH and DELETE **built** (§9) |

Before any W route: the Host/Origin allowlist (roadmap §4.5). Local writes from a hostile
page are the one new exposure this vision creates. **Built with the first W route** (§9.2).

Before designing async solves: **measure** solve, `rank_unlocks` and `advise_hard_drive_pick`
latency on the reference save. Only then decide between plain requests, a progress SSE, or a
job queue.

### 5.3 Gaps in the domain itself

| Gap | State | Needed for |
|---|---|---|
| Timeline "what changed since yesterday" (parked §22) | `domain/world/timeline.py` landed, no consumer, no tool | History panel, "starved since" |
| Physical trace mode (§21) | `logistics.py` feeds health; `trace_upstream` still walks the recipe graph | Drawing the real feeding path in 4.3 |
| Plan fits outline (§16) | `fit.py` fits against existing platforms; declared outlines do not exist | Site step on bare ground |
| Siting by the whole bill (roadmap §2.1) | not built | "Where to build" for a multi-resource plan |
| Byproduct remedies (roadmap §3b) | not built | Byproducts chip that says what players do |
| Pipe build review (roadmap §3b) | needs head-lift model | A "check my plumbing" action |
| Transport networks (trains, drones, trucks) | absent; **deferred** | Logistics screen |
| Awesome Sink / Dimensional Depot | unraised | Sink points, depot uploads |
| Belt arrows | `ROUTE_CHEVRONS.belts = false`; **held** | Map only |
| Fog of war | raster unverified; **held** | Map only |
| Forecasts / ETAs | forbidden until the timeline exists (roadmap §2.3) | Any "N hours to" line |

---

## 6. Roadmap

Smallest useful slice first. Each phase ships on its own. Reads before writes.

| Phase | Slice | Tools covered | New routes | Writes |
|---|---|---|---|---|
| 0 | Factories + Power panel (in progress) | `list_factories`, `power_report`, parts of `factory_map` | per step 0 | no |
| 1 | **Shell:** rail, selection model, status strip, `as_of` on fetches. **Built 2026-09-27** (§16) | `world_summary`, `list_worlds` | none | no |
| 2 | **Factory detail:** aspects, health, floors. **Built 2026-09-27** (§17) | `factory_query`, `factory_health`, `factory_floors`, `factory_sites` | 2 | no |
| 3 | **Inventory:** stock, containers, crates. **Built 2026-09-27** (§10) | `stock`, `storage`, `crates` | 1 | no |
| 4 | **Progress (read):** milestones, MAM, elevator, shards, sloops, drives list. **Built 2026-09-27** (§11) | 6 tools | 5 | no |
| 5 | **Recipes codex + search box**. **Built 2026-09-27** (§12; `list_buildings` still open) | 5 game-data tools, `unlocked_recipes` | 6 | no |
| 6 | **World finders:** nodes, fields, conduits, collectibles, whereami, inspector upgrade | 7 spatial tools, `collected_from_world` | 3 | no |
| 7 | **Trace:** upstream/downstream drawn on the map — **built** (§13) | `trace_upstream` | 1 | no |
| 8 | **Planner (stateless):** solve, bill, compare, byproducts | 4 planning tools | 4 POST | no |
| 9 | **Write guard + Plans:** save, rename, forget, site by dragging | 4 plan tools | CRUD | **yes** |
| 10 | **Plan follow-through:** layout, diff, startup order | `plan_layout`, `diff_vs_save`, `commission_plan` | 3 | no |
| 11 | **Labels:** propose, preview, name, amend by lasso. **Built 2026-09-27** (§9, §9.9; selector text still open) | 6 label tools | 2 + CRUD | **yes** |
| 12 | **Advisors (slow):** unlock value, hard drive pick | `rank_unlocks`, `advise_hard_drive_pick` | 2 | no |
| 13+ | Domain gaps in §5.3, in an order still to be picked | — | — | — |

After phase 12 every tool, resource and prompt has a surface.

---

## 7. Open questions

None of these are assumed above. Each changes the design.

**How the page is used**
1. Where does the page live while you play: second monitor, alt-tab, or between sessions only?
   (Glanceable status vs deep panels.)
2. Is the AI chat still the main way in, with the page as a viewer? Or should the page stand
   alone? Should the two share a selection (chat says "open this", page says "ask about this")?
3. Any other device (phone/tablet on the LAN)? That changes the Host rule and the layout.
4. Default landing view: map only, Factories, or Progress? **Answered 2026-09-26: the
   dashboard Overview** (§8.6).

**Writes**
5. §16 said the site-outline idea was "read-only; no editing". Should the page write labels
   and plans at all, or stay read-only with writes via chat? **Answered 2026-09-27 for labels:
   the page writes them** (§9). Plans are still open.
6. If yes: drag-to-site on the map, or typed coordinates only?

**Planning**
7. How do you usually start a plan: target item rate, max MW, or "what can this field make"?
8. Should the Planner remember the last form per world, or start empty?
9. Compare routes: which costs matter to you (raw total, one scarce resource, power, machines)?

**Progression**
10. Should the UI suggest a "next" step at all, or only show facts and let you decide?
11. Spoilers: show locked milestones, MAM nodes and alternates in full, or only what the game
    shows you at this point? **Answered 2026-09-26 for milestones: a per-browser setting**
    (§8.6). **Extended 2026-09-27 to MAM nodes and elevator phases** (§11) **and to recipes
    and alternates** (§12.3), all under the same setting.
12. Hard drives: do you pick at once, or hoard? (Affects whether "pending" is an alert.)

**World and history**
13. Collectibles: is a pickup route worth anything, or is the layer enough?
14. Timeline: do you want history at all? How many saves do you keep, and is "since my last
    session" the right default window?
15. Save events: should a new problem (a factory starts starving) raise a toast, or stay silent?

**Held and deferred items: still held?**
16. Belt arrows, fog of war, transport networks, sink/depot: confirm each stays out of the
    first twelve phases.

**Build strategy**
17. A cheap fallback exists: one route that returns any tool's text into a `<pre>`, giving
    full coverage in a day with no structured views. Wanted as a stopgap, or not at all?

---

## 8. Dashboard: the case (2026-09-26)

The request was "a first-class UI dashboard", with a justification. This section is that
justification, then the record of what was built. The code carries no prose; this is its home.

### 8.1 What is already decided

Evidence only: each row cites where the decision is written down.

| Decision | Source | What it means for a dashboard |
|---|---|---|
| Local only, bundled, no CDN | `index.html` head comment; `vite.config.ts` (Leaflet licence copied at build); web `__main__.py` binds 127.0.0.1 | No chart library. Bars are CSS widths; nothing is fetched but `/api` |
| Palette closed, "lgtm" 2026-08-06 | memory `map-ui-future-ideas` | No new colour. The dashboard uses `--ok`, `--warn`, the panel's accent blue and its greys |
| Colours are audited, dE 15 across owners | `palette.ts`; commit `db50aa7` | The dashboard declares no map colour, so the audit has nothing new to compare. Its three bar colours are 25.8 or more apart (CIE76) |
| Comment budget, no exemptions | memory `prose-goes-in-docs` ("No excemptions"); docs/comments.md | `dashboard.ts` has a two-line header and no other comments |
| Read-only | parked.md §16 "Read-only; no editing"; every route is a GET (`test_every_get_says_what_it_sends`) | The dashboard reads. No POST. **Reversed for labels on 2026-09-27** (§9) |
| Facts, not advice | commit `deadb0b` "Name the machines no wire reaches, instead of advising a power check"; roadmap §2.3 bans ETAs from one 300 s window | No "next best" score, no ETA, no trend line (the timeline has no consumer yet) |
| Ask before encoding play patterns | memory `verify-play-patterns` (a hand-feeding setup read as a factory; shoreline siting) | Order comes from the domain. "temporary …" factories are neither hidden nor discounted |
| Belt arrows and fog of war held; transport deferred | memory `map-ui-future-ideas`; "I'm not at transportnetworks yet" | No logistics section |
| Measure before designing | memory `measure-before-optimising` | Timings in §8.3 |
| Typed wire, no guessed payloads | commits `c60df84`, `63c9b5b`; `test_every_get_says_what_it_sends` | The new route has a `response_model`; TS types come from `npm run typegen` |
| Host allowlist deferred (household NAT, two people) | roadmap §5 risk 5 | Only reads were added, so there is no new exposure |
| Wording | commit subjects: plain sentences that name the thing ("Say which pods still hold a drive") | Labels are plain words. `–` means unknown, never 0 |

### 8.2 The §7 questions the record already answers

- **Q10 (suggest a next step?)**: the record points to facts only (`deadb0b`, the ETA rule).
  The dashboard shows facts. It is still open.
- **Q16 (held items)**: belt arrows and fog of war are held. Transport is deferred. Both are
  explicit. **Sink/Depot is still unraised.**
- **Q5 (writes)**: partly. §16's "read-only" was about the plan picture only. The dashboard is
  read-only either way. Label and plan writes stay open.
- **Q14 (timeline)**: §22 was picked as a next feature on 2026-08-02, so history is wanted.
  The default window is still open.
- **Q17 (`<pre>` fallback)**: the typed-wire commits lean "no". It is still open.
- **Q3 (other devices)**: the server binds 127.0.0.1 and the Host rule is deferred. That
  counts against LAN use today, but it does not settle it.
- Still open: Q1, Q2, Q6–Q9, Q12, Q13, Q15. Q4 and Q11 are answered in §8.6.

### 8.3 For, against, verdict

**For.**
1. It was requested. Principle 2 ("never a page that hides the map") was this note's own rule. It
   was drafted without review and is not a project decision.
2. **Width.** The panel is 340 px floating over the map. Fifteen factories across nine
   states, uptime, two MW figures and two power faults make a table. Principle 5 promises
   real sorting, and 340 px holds about two columns.
3. **Across factories.** "What needs me" means every factory's worst machines plus every dark
   machine, side by side. The panel shows one factory at a time.
4. **Q1.** On a second monitor, or between sessions, a glanceable page is the answer. The map
   stays the deep answer. A dashboard serves both uses; the panel only serves "map open".
5. **Cheap, measured.** Warm, on SUCK_DRAIN: `/api/factories/health` 0.09 s / 20 kB,
   `/api/power/circuits` 0.02 s / 2 kB, `/api/progress/milestones` 0.01 s / 18 kB. The
   dashboard makes **no second request** for the two panel payloads: `panel.ts` keeps the one
   registry entry per path and hands the same response to the dashboard (`onVitals`).

**Against.**
1. A second surface over the same data can drift. Mitigation: same response object. The
   TypeScript only filters, sorts and sums the served rows. It classifies nothing.
2. It hides the map. Mitigation: every placed row has a **map** button, and the map
   viewport stays in the fragment.
3. More TypeScript, with only `tsc` over it. The Python tests pin the payloads, not the view.
4. Principle 3's `as_of=` is not implemented, here or in the panel. The dashboard has the
   same gap. **Closed 2026-09-27** (§16).

**Verdict: the case holds, as a view of the same page rather than a second page.** A
`/dashboard` path would need a second Vite entry. That renames `app.js`, which
`test_architecture.py` pins, and it adds a second EventSource and a second world picker. A
fragment key reuses all of that, and a bookmark still lands on the view.

### 8.4 What was built

- **Address:** `#…&dash=<tab>[/<subject>]&z=…&c=…`. Tabs are `overview`, `factories`,
  `factories/<name>`, `power`, `power/<n>` (1-based) and `progress`. `hashFor` in `map.ts`
  writes it, `fragment.ts` applies it, and `state.dash` holds it.
- **Switching:** the header has **Map | Dashboard**. Opening the dashboard hides the map with
  `visibility` (Leaflet keeps its size, so nothing re-measures) and hides the panel and legend.
  Moves between views push history, so Back works in both directions.
- **Deep links:** a dashboard **map** button returns to the map and calls the panel's own
  `showFactory` / `showCircuit` / `showPoint`, so the panel row is selected as well. A
  selected panel row offers **open in dashboard ›**.
- **Overview:** tiles for factories (with the state mix), generation (with the draw bar),
  headroom now, headroom at full rate, machines needing action, power problems and milestones. Below them:
  factories needing action, machines needing action, and power per circuit.
- **Factories:** a sortable table (click or Enter on a heading). The detail view shows state
  counts, the eight worst machines, and label review.
- **Power:** the world ledger, the circuits table, a detail view per circuit, and the
  starved, unwired and no-generator lists.
- **Progress:** `/api/progress/milestones` (`routers/progress.py`) runs the `milestones`
  tool's `SchematicLadder` over every milestone, with a per-tier tally and the tool's READY
  caveat.
- **Refresh:** the SSE `save` event refetches the live wave, and the dashboard redraws from it.
- **Dependencies added:** none.

### 8.5 Ambiguities, not settled here

- **Two problem tiles, not one.** An unwired machine can also be "no recipe" (on SUCK_DRAIN,
  4 of the 8 unwired assemblers are), and the health route sends only 8 machines per factory.
  A single count would double-count, so the dashboard shows two counts that are each exact.
- **Only named factories are assessed.** Machines outside every label appear only if they are
  dark.
- ~~**"Blocked" is not action.**~~ **Answered in §8.6: blocked needs action.**
- **Circuit numbers** follow size in each save. A `power/2` bookmark can name another
  circuit after a rebuild.
- ~~**Headline headroom is measured.**~~ **Answered in §8.6: two tiles, neither the lead.**

### 8.6 Decided 2026-09-26

- **A blocked machine is a problem.** "Need action" is `health.ACTIONABLE`: dead node, no
  recipe, **blocked**, starved and stalled. It is one tuple in the domain, used by the
  `factory_health` MCP tool (its `todo` column and sort), by `/api/factories/health`
  (`actionable`, and `actionable_states`, which the dashboard and the side panel read instead
  of keeping their own list). Uptime still reads 0% on a backed-up factory, and that is true:
  a blocked machine produces nothing. It now sits beside a non-zero "need action" count, so
  the two agree. The map follows the same tuple: `/api/machines` sends `actionable`. A stopped
  machine gets a red outline, and a blocked one a yellow outline, because blocked is waiting on
  downstream, not broken (docs/save-projection.md §6.2d).
- **Spoilers are a setting, off by default** (T3, §14). Settings (`dash=settings`) holds a
  "show what is not unlocked yet" switch. Off, Progress shows only the tiers the HUB has open (the
  delivered Space Elevator phases open them, §12.3), plus any tier with a milestone done; the
  tier strip, the tallies and the Overview's "affordable" count follow the same filter. The
  default was on until 2026-09-27. A browser that never set the switch gets a one-time notice
  saying locked content is now hidden, with a button that shows it. `settings.ts` keeps
  the values in `localStorage` under `settings`, wrapped in try/catch, so the page still works
  without storage. Settings have no write route (labels have had one since 2026-09-27, §9). A
  new setting is one more entry in `SETTINGS`.
- **The page lands on the dashboard.** A fragment with no `dash=` and none of the map's keys
  (`z`, `c`, `floor`, `mode`, `pickups`) opens the Overview (`dashOf` in `state.ts`, used at
  boot and by `fragment.ts`). A map deep link still opens the map, because the page writes `z`
  and `c` into every fragment it makes while the map is showing. Back and Forward are
  unchanged.
- **Headroom is two metrics.** "Actual usage and potential usage": **headroom now** is
  generation minus measured draw, **headroom at full rate** is generation minus nameplate
  draw. Each tile is red when negative, and neither leads. The separate "draw, measured" tile
  is gone from the Overview: its figures are the two subtitles and its bar moved into the
  generation tile. Power and the circuit detail use the same two tiles, and the circuits
  table uses the same two names.

### 8.7 Decided 2026-09-27: biomass burners are not generation

Biomass burners are hand-fed capacity, not a power plant. Biofuel is hand-fed too, and the game
has no automated way to supply either, so their MW lasts as long as someone keeps filling them.

- **Left out by default.** Every wired, unpaused rated biomass burner (`BIOMASS_BURNERS` in
  `domain/power/report.py`) is left out of generation, headroom now, headroom at full rate and
  the starved list, for the world and for each circuit. What was left out is reported as
  `biomass_mw` and `biomass_generators`, and the "not counted" line appears only above 0 MW.
- **The HUB's built-in burners are not in that set.** Game data gives them no rating, so they
  stay under `unmodellable` in both modes, and a HUB-only circuit reads the same either way.
- **One rule on both surfaces.** `/api/power/circuits` and `/api/summary` take
  `?biomass=exclude|include`; absent means exclude. The MCP tools that print headroom
  (`power_report`, `world_summary`, `commission_plan`, `diff_vs_save`) take `biomass=`, default
  false. The same save and the same choice give the same figures in chat and on the page.
- **One setting.** Settings > power > "count biomass burners in headroom", off by default. It
  refetches both routes. Overview, Power, the circuit detail and the map panel show the
  left-out MW as one line, "+N MW biomass not counted"; the header shows it in its tooltip.
- **The timeline keeps installed capacity.** Its `installed_mw` counts burners, as it always
  has, so the rows cached before this decision stay comparable with the new ones.
- **A circuit with only burners on it is not "no generator".** A burner stands there, so
  `assess` does not list its machines, and the page shows 0 MW generation with the biomass line
  under it.

### 8.8 One reading of a ledger

`powerview.ts` is the only place a ledger figure turns into text. The Overview, Power, the
circuit detail, the map panel and the header all go through it. A figure that cannot be read
shows "–" and the reason: "generation not modelled" when game data cannot rate a generator on
the circuit, and "no machine measured" when no machine on it carries a productivity monitor.
That second case blanks the measured draw and headroom now. Headroom at full rate stays, because
it needs no measurement. A machine's location is resolved by `whereOf` in the same module:
factory, then circuit, then region.

---

## 9. Naming factories from the page (2026-09-27)

The request: "Extend the factories UI by adding a feature that runs detection on the factories,
and help me name them." Later the same day came "I like my style more, maybe make it a
setting", a filter for trivial clusters ("is it connected to a source other than just a
storage box"), and "renaming a factory is kinda elemental". This **reverses parked.md §16's
"read-only" for factory labels**, on request, on 2026-09-27. Plans stay read-only on the page.

### 9.1 What was built

- **Detect.** Dashboard > Factories has an "unnamed clusters" card with a **detect** button.
  It calls `GET /api/factories/candidates` (`routers/naming.py`). The reply lists every
  `st.proposals` entry that `LabelStore.covers` does not claim: the same proposal list and the
  same "already named" test that `propose_factories unnamed_only=true` and `/api/factories`
  use. Each row carries:
  - the machine count and whether the cluster is fed (§9.5);
  - a **graph** action that draws its production graph (§9.8);
  - its products (with where they go), intermediates, sunk, unrouted items and inputs in
    items/min, none of them truncated (§9.4);
  - every building type, the region at its centroid, its box and its `proposal:N` selector;
  - a suggested name, with `confident: false` when that name is a guess.

  The reply also carries the save `token`, the label-store `version`, and how many clusters
  the filters hid and why.
- **Map.** A row's **map** button switches to the map, flies to the cluster's box and draws the
  panel's dashed outline around it (`showBox` in `panel.ts`).
- **Name, edit, skip.** The name is an editable field, prefilled with the suggestion. A guessed
  name has a dashed border. **name** (or Enter) writes it, and Esc restores the suggestion.
  **skip** hides the row for this page session only; nothing is stored.
- **Write.** `POST /api/labels {name, proposal, as_of, version}` resolves `proposal:N` through
  `select.select_machines`, describes the machines with `identity.describe`, and writes through
  `edits.name`, which is what `name_factory` calls too. So the tool and the page write the same
  label (pinned by `test_the_page_and_the_tool_write_the_same_label`).
  Unlike the tool, the page **refuses a name the world already holds** (409) instead of
  re-anchoring it: `put`'s substring match could otherwise silently move another label.
  `as_of` is the detect reply's token. If a save was written since detection, the request is
  refused (409, `pin.check`) rather than naming whatever cluster now holds that index.
- **After naming.** The page refetches `/api/factories` and `/api/factories/health`, so the
  factory appears in the Factories table, the side panel and the map labels with no reload.
  The card lists what was named in this session, each with an **undo**. Undo is `DELETE /api/labels/{name}?version=`, which removes an exact name through
  the same `edits.forget` that `forget_factory` uses.
- **Amend by lasso** with a dry-run preview came later the same day (§9.9).

### 9.2 The request guard

`interfaces/web/guard.py` is installed as HTTP middleware in `create_app`. Every request is
refused with 403 `{"error": …}` unless its **Host** is the server's bound address and port
(`scope["server"]`). When the server is bound to loopback, the aliases `127.0.0.1`,
`localhost` and `[::1]` are all accepted, on that port only. A server bound anywhere else
accepts only its own name. This check defeats DNS rebinding, which would otherwise let a
page on another site read every GET, absolute paths included.

Decided 2026-09-27: the Host check applies to every method. It used to cover writes only,
and reads passed untouched.

GET, HEAD and OPTIONS need nothing more. Any other method must also pass the Origin check:

1. **Host**, as above.
2. **Origin** is exactly `http://<that Host>`. When there is no Origin, the Referer's origin
   stands in. When neither is present, or Origin is `null`, the request is refused. So a bare
   `curl` cannot write; the MCP tools remain the non-browser path.

There is no env override yet. Roadmap §4.5 proposed one for LAN use, and Q3 is still open.

### 9.3 Trying it on an empty store

`SATISFACTORY_USER_DATA=<dir>` moves the player's own files (`<dir>/labels` and `<dir>/plans`)
away from the platform data directory. `satisfactory-mcp-web --port N` serves on another port.
With a copy of a save in its own folder:

    $env:SATISFACTORY_SAVES = "<dir holding a copied .sav>"
    $env:SATISFACTORY_USER_DATA = "<empty dir>"
    uv run satisfactory-mcp-web --port 8713

The projection cache and the `as_of` token ledger stay in the shared cache directory. Both can
be regenerated.

### 9.4 Flows, destinations and the suggested name

**Rates** come from `query.build_view`, the same pass `factory_query` makes. Every machine row
carries nameplate `makes` and `uses`, meaning recipe rate × clock. Measured figures exist, but
a backed-up factory reads as producing nothing (the steel cluster has 97 of 108 machines
blocked), and a name is about what the factory is built to make.

**Classes come from topology, not from rates** (decided 2026-09-27: "If it outputs them into a
box somewhere, that is a product. If it just outputs them into a sink, it's not an output.").
`flowgraph.ends` walks each producing machine's outputs downstream over `st.physical`, the
contracted belt and pipe runs. It passes through splitters, mergers, junctions, pumps and
valves; lifts are part of a run. It sorts where the walk ends:

| The walk reaches | Class |
|---|---|
| a box or tank with no way out (`storage`) | **product** |
| a machine or other building outside the cluster (`export`) | **product** |
| the AWESOME Sink (`sink`) | **sunk**, listed separately, not a product |
| only machines inside the cluster | **intermediate** |
| nothing, a run that ends open, or a fitting with no way on (`nowhere`) | **unrouted** |

An item that reaches several of these takes the first class in that order that it reached.
So a splitter feeding a box and a machine makes a **product**. Each item also lists every
kind it reached, in `to`. An item consumed inside and made nowhere inside is an **input**.

- **Pass-through buffers.** A box or tank that something drains is a buffer, not an end: the
  walk continues through it. "Miner → box → constructor" is therefore an intermediate, and
  "constructor → box" with nothing after the box is a product. The two are told apart by
  whether any belt or pipe leaves the box. A box emptied by hand looks like a final box.
  `buffers` counts the pass-throughs.
- **Fluids** walk pipes, solids walk belts, chosen by the item's form. A tank at the end of a
  pipe is storage. Pipe runs whose direction the save does not state are walked both ways,
  which can only over-report.
- **Dead ends** are `nowhere`, and an item that reaches only those is unrouted, not a product.
  On the reference save that is the FICSMAS line's outputs (3 open runs), several oil and water
  extractors whose pipes end open, and the 15-machine refinery cluster's Heavy Oil Residue.
- **Generators** burn fuel as a recipe input. Water reaching a generator counts as consumed
  inside, although `build_view` does not rate it.

**Edge rates** in the graph are apportioned from nameplate figures, since the save records no
per-belt throughput. A consumer group's demand for an item is split across the inside
producers that reach it, in proportion to their production. Demand no inside producer covers
becomes an input edge. What a producer has left after that is split evenly across the
terminal kinds it reaches. A terminal it reaches with nothing left over gets an edge with no
rate. Product rates in the Detect list are what the cluster makes; the graph shows the split.

**On the reference save:**

- The 78-machine cluster: products Wire 810, Cable 120, Copper Sheet 80, **Rotor 25, Stator
  25**, Motor 10 /min, all to storage. Intermediates: Copper Ingot, Water, Steel Pipe. So the
  suggestion is "wire factory".
- The 110-machine cluster: products **Reinforced Iron Plate 40**, Rotor 20, Modular Frame 12
  and Modular Engine 2.5 /min, all to storage. **Smart Plating 8/min stays an intermediate**:
  on this save no box is reachable from its assemblers, only the Manufacturer that makes
  Modular Engines. The suggestion is "reinforced iron plate factory".
- The 108-machine cluster: Steel Ingot 1,395/min is exported (some of it belts out to the
  78-machine cluster), and Concrete, Steel Beam, Steel Pipe and Encased Industrial Beam go to
  storage. The suggestion is "steel ingot factory".
- Sunk: Polymer Resin in the 50- and 15-machine oil clusters.

**The lead item** is `naming.lead`. It picks the first of these that exists:

1. the top product a recipe makes, by rate;
2. the commonest generator, for a set that makes power;
3. the top extracted product (a mining outpost);
4. the top item made at all, marked as a guess, preferring a recipe's output to an ore;
5. the commonest building, marked as a guess.

Extracted items rank below made ones because a miner inside a cluster otherwise leads with
its ore. So a steel line whose outputs all end open is guessed "steel ingot factory", not
"coal factory".

**Wording** is `naming.suggest(item, region, taken, style)`, with a style from `STYLES`:

- **`short`** (the default) is `<lead item, lowercase> factory`, for example "steel pipe
  factory". The setting reads "short", not "like yours": other people use the tool too
  (renamed from `mine` on 2026-09-27). It follows the reference save's labels: lowercase, short
  and product-led ("steel factory", "speedwire factory", "tor factory"), with no region.
  That save also uses "setup" ("oil setup", "aluminium setup", "biofuel setup", "concrete setup").
  That choice is **not decidable from the data**. Its setups range from 2 to 50 machines and
  its factories from 11 to 110, so size does not separate them. Oil, aluminium and biofuel all
  have a "setup", while blackpowder and steel have "factory". So the suggestion always says
  "factory", and the player edits the rest.
- **`product, region`** is `<Lead Item>, <region>`, for example "Steel Pipe, Rocky Desert".
  The region is `RegionMap.label_for` at the centroid, and is left out in the sea or off the
  map.

In both styles, a name already held by a label, or by an earlier suggestion in the same reply,
gets " 2", " 3" and so on, after the item: "crude oil factory 2", "Crude Oil 2, Rocky Desert".
The comparison ignores case and compares slugs.

`naming.proposal_names` numbers every unnamed cluster in index order, hidden or not, and both
Detect and the map's cluster layer take their names from it. So a filter that hides a cluster
never renumbers the ones it shows.

The route validates `style` against `STYLES` (400 names them). The **Factory name
suggestions** setting picks it, and changing the setting re-asks the open Detect list while
keeping names already edited.

### 9.5 Fed or not, and the size floor

`fed.feeding` answers "is this a real factory or a box somebody fills by hand". It walks
upstream with `trace.trace`, the walk `trace_upstream` makes (belts and pipes walked through,
directions read from connector roles). The verdict is one of three:

- **fed:** the set contains an extractor (miner, water, oil or well extractor). Or the walk
  reaches an extractor or a production machine outside the set.
- **not fed:** the walk reaches neither. A storage container is walked through, so it counts
  only when something real feeds it. A box with nothing inbound ends the walk.
- **transport:** the walk reached a train, drone or truck station (`model.kind_of`, plus
  freight platforms by `DockingStation`). What arrives there cannot be seen, so the source is
  **unknown**. It is never hidden by the filter. The reference save has no transport yet, so this branch is
  untested there.

**What the walk cannot see:**

- **Dimensional Depot:** items come out of the depot into the player's inventory, not onto a
  belt. A cluster hand-fed from the depot reads "not fed", which is the truth about its belts.
- **Mixed belts:** the walk is item-agnostic. "Fed" means something real arrives, not that
  every ingredient does. Health's starved state covers that.
- **Hand-fed:** machines filled by hand and never belted read "not fed".
- **Direction:** belt-to-belt joins are walked both ways where no port says a direction, so
  the walk can only over-report a source.

**Filters** are query parameters on the candidates route: `fed_only` (hides "not fed") and
`min_machines`. Both default to off, so the API reports everything unless asked. The page
sends the **Only suggest fed clusters** switch (default on) and the **Minimum machines**
number (default **2**). The list says what was hidden and why, for example "10 hidden: 2 not
fed, 8 below 2 machines". **show all** re-asks with both filters off for that list only.

Why 2: the unnamed cluster sizes on the reference save are 110, 108, 78, 50, 47, 30, 28, 18, 15, 15,
15, 15, 11, 10, 7, 7, 4, 3, 3, 2, 2, and eight clusters of 1. Its smallest named factory,
"concrete setup", is 2 machines. So 2 hides exactly the singletons: six lone miners and
extractors, one Manufacturer and one Smelter. It hides nothing of the kind that save names.

On that save, 4 clusters are not fed (30, 7, 1 and 1 machines) and none reach a station. The
30 is a FICSMAS line and the 7 is a FICSMAS and SAM crafting corner, both filled from boxes.
**The 30-machine one is named ("christmas factory")**, so the fed filter hides a named
factory. That is why "show all" is one click.

### 9.6 Concurrent writers

Every chat session runs its own MCP stdio process, and the web server is another. They share
each world's label and plan files. A plain read-modify-write in two of them at once silently
dropped one change. Now:

- **Lock.** `core/filelock.py` holds an OS byte-range lock on `<file>.lock` (`msvcrt` on
  Windows, `fcntl` elsewhere; no dependency). A crashed process releases it. The lock waits up
  to 10 s and then raises `LockTimeout`, which names the file (the web routes answer 503).
  The `.lock` file stays on disk on purpose: deleting it would race the next writer.
- **Re-read inside the lock.** `LabelStore.editing` and `PlanStore.editing` load the file
  inside the lock, hand it to the block, and write it on a clean exit (atomic temp-file plus
  `os.replace`, `core/atomic.py`). Every production write goes through them:
  `domain/factories/edits.py` for labels (name, rename, amend, forget, and the plan repoint a
  rename carries), and `_editing` in the planning tools for plans.
- **Version.** Both files carry a `version` that every write bumps. `/api/factories/health`
  (`labels_version`) and the candidates route send it. The web writes send it back, and a
  mismatch is 409 "the factory labels changed elsewhere (chat, or another tab) since this page
  loaded them", with `stale: true` in the body. The page shows that, refetches, and the next
  attempt works. The MCP tools do
  not pass a version: last writer wins under the lock, and an unrelated label is never lost.
- **No write holds both locks.** A rename writes the label and releases its lock, then
  repoints each plan under that plan's own lock.

`test_label_store_lock.py` runs two processes naming 12 factories each at once. All 24
survive, and version reads 24.

### 9.7 Rename

`PATCH /api/labels/{name} {to, version}` goes through `edits.rename`, which `rename_factory`
now calls too. It keeps the label's machines, notes and dates, moves its slug, and repoints
stored plans whose `factory` is the old name. That repoint moved out of the tool body into
the shared domain code. The new name is free text (the naming style does not apply); it is
trimmed. `test_the_page_and_the_tool_rename_to_the_same_files` compares both label and plan
files after each path.

**Names.** `LabelStore._free` refuses a blank name, a name with `/` in it, and a name over 60
characters (`NAME_MAX`), for `POST` and `PATCH` alike. The `PATCH` and `DELETE` routes take
the name as the rest of the path (`{name:path}`), so a label named with a `/` before the rule
existed can still be renamed or forgotten.

**Refusals.** A 409 body is `LabelRefusedResponse`: `error` plus three flags, one of them
true. `stale` means the store moved since `version`, `name_taken` means another label holds
the name or its slug, and `pin` means a save was written since `as_of`. A bad name is 400 and
a missing label is 404. The page branches on the flags, never on the wording: a taken or
bad name shows under the field, and a stale store or a moved save reloads what the page shows
and asks for the write again. The page remembers the version its own writes returned, so a
rename followed by a Detect name in the same tab is not refused as stale.

**Order.** The label is written first, then each stored plan is repointed on its own. A plan
that cannot follow (its lock timed out) is listed in `plans_stuck` with `stuck_reason`, and
the rename still stands; the tool says the same and how to repoint the plan by hand. Before,
the repoint ran inside the label lock, so a failure part-way left a plan pointing at a name no
label held while the reply said nothing was written.

The UI edits in place (Enter saves, Esc cancels) in three places: the Factories table row, the
factory detail header, and the side panel's selected row (`rename.ts`). After a rename, the
detail view replaces its own address with the new name. The page also remembers renames it made
in this session, so an old `dash=factories/<name>` link lands on the renamed factory. A link
from before this session, or a rename made in chat, says the factory "may have been renamed or
forgotten since this link was made": the store keeps no history of former names.

### 9.8 Production graph

The request was "a function that shows me the product graph in the factory view, and can show
me that graph for a detected factory".

- **Route.** `GET /api/factories/graph` takes either `factory=<exact name>` or
  `candidate=proposal:N&token=<the detect token>`. A stale token gets 409, since the index may
  now name another cluster. Passing both or neither gets 400, and an unknown factory gets 404.
  It is built by `flowgraph.build`, the same code that classes the Detect items (§9.4).
- **Nodes.**
  - Recipe groups, e.g. "8× Assembler · Rotor", with machine count, mean clock, nameplate
    outputs with destinations, and `states`: machines per `health.assess` state. The older
    running / blocked / stopped mix stays in the payload (`ACTIONABLE` minus blocked counts
    as stopped, and everything else, paused included, as running); the page reads `states`.
  - One input node per item that enters the factory (or the cluster, for a Detect row).
  - One terminal per kind: to storage, leaves the factory or cluster, AWESOME Sink, goes
    nowhere.

  Edges are items with apportioned items/min (§9.4). The payload is grouped by recipe: the
  110-machine cluster is 16 nodes and 20 edges.
- **Drawing.** `frontend/src/graph.ts` is hand SVG with no library.
  - Inputs sit in the first column. Each group is placed by its longest path from the
    inputs, with cycles cut where the walk meets them. Terminals sit in the last column.
    Three barycentre sweeps order each column.
  - Edges between the same two nodes share one curve and one label. A backward edge dips
    below the nodes.
  - Each group's third line counts its machines by state through `states.ts`: fine states
    read "running", every other state by its own name, so paused is never running. The
    outline takes the worst tone: `--bad` when a machine needs action, `--blocked` when the
    worst is blocked, neutral otherwise. Terminals and inputs are neutral; inputs and "goes
    nowhere" are dashed, the sink dotted and "sunk: not a product" in muted text. A plan has
    no live state, so its nodes stay neutral.
  - Columns are as wide as their text, and each gap as wide as its edge labels, wrapped to
    item and rate on two lines. A label slides along its curve until it clears every node
    and label; one with no free spot is left to the edge's tooltip.
  - Hovering shows each group's outputs and where they go. Clicking a group, or Enter on it
    (groups are in the tab order), flies the map to its machines and outlines them.
  - The first view fits the whole graph when the text stays between 11 and 14 px; a wider
    graph opens at 11 px in a frame that scrolls sideways, faded at the right edge while
    more is off-screen. The frame is as tall as the graph, up to 80% of the window.
    Ctrl+wheel zooms (a plain wheel scrolls the page), dragging pans and a double-click
    fits again.
  - One card for both callers: `graphCard(heading, shown, toggle)` in `graph.ts`, with one
    **hide graph** toggle per view. The planner draws its plan with the same component
    (planner_vision.md §4.2); power is not an item there. A process's node shows its MW,
    and exported power is a **power** terminal fed by the generators, labelled in MW.
- **Where.** The factory detail view has a **graph** button next to **map**. Each Detect row
  has a **graph** action, which opens the same card above the list. The component takes any
  `{nodes, edges}` of that shape, so the planner's production graph (planner_vision.md §4.2)
  can reuse it.
- **Not yet.** Per-machine drill-down, belt tiers on edges, and a layout that keeps positions
  steady across saves.

### 9.9 Amend by lasso, and a cluster's machines on the map

- **Machines on the map.** `GET /api/factories/machines` takes `factory=<name>`, or
  `candidate=proposal:N&token=` like the graph route, and lists each standing machine with its
  building, `x_m`/`y_m` and the label that holds it. A Detect row's **map** button still flies
  to the box and outlines it, and now also rings every machine of the cluster (`lasso.ts`),
  with a card that counts them by building. Before, the page showed only the box.
- **Amend.** The factory detail header has **amend on map**, and the side panel's selected
  factory has **amend**. Both switch to the map, ring the factory's machines in the selection
  colour, and turn map dragging off so that a drag draws a freehand area. **+ add** and
  **− remove** pick what the area does. Wheel and buttons still zoom; Esc or × ends it.
- **Preview, then apply.** On release the page sends `POST /api/labels/amend {name, area,
  mode, as_of, version, dry_run: true}`. `area` is the drawn polygon in metres. The reply lists
  the machines it would add (green rings) or remove (red rings), the anchor count before and
  after, and any other label that already holds an added machine. **apply** sends the same body
  with `dry_run: false`; **discard** or a new drag starts again. Switching add/remove re-checks
  the same area.
- **Same path as the tool.** The route picks the machines inside the area (`geo.inside`, an
  even-odd test on the machine positions), then calls `edits.plan_amend`, the dry run that
  `amend_factory` now uses too, and `edits.amend`, now under `LabelStore.editing` with
  `expect`. `test_the_page_and_the_tool_amend_to_the_same_label` compares the stored label
  after each path.
- **Refusals.** As §9.7: `stale` (version), `pin` (a save written since `as_of`), 404 for a
  missing label, 400 for a bad mode or an area under three corners. Removing every machine is
  a 409 with no flag set, and the page shows its words: `forget` deletes a label. A stale or
  moved refusal reloads the rings and asks for the area again.
- **Choices left open.** One area per preview (no adding several strokes before applying); a
  lasso only, with no separate box tool; added machines another label already holds stay in
  both labels, as `amend_factory` does, with a warning in the preview. A trace started while
  the amend card is open is not closed by it.

---

## 10. Inventory (2026-09-27)

Roadmap phase 3: the `stock`, `storage` and `crates` tools as one dashboard section.

- **Address:** `dash=inventory[/<item>]`. The subject is the item filter, so a link such as
  `dash=inventory/Quartz Crystal` opens the section filtered. Typing in the filter rewrites the
  fragment in place (no history entry per keystroke).
- **Route:** `GET /api/stock` (`routers/stock.py`), live wave, rank 70. It sends
  `Inventory.breakdown()` as `items` (the `stock` tool's piles: spendable, carried, storage,
  depot, machine buffers, crates) and `Inventory.holdings()` as `places` (the rows the `storage`
  and `crates` tools print), plus a census and the player position. Each place carries its
  region (`_label_json`), fill, slots used and its ground distance from the player. Warm on the
  reference save: 4 ms, 100 kB.
- **One route, not three.** `/api/storage` and `/api/crates` build their rows from the
  projection and feed the map layers. The containers and crates tables here read `places`
  instead, so they come from the same `holdings()` the tools call, fill included. The map
  layers are unchanged.
- **Section:** three tiles (item kinds, containers, crates), then crates, stock and
  containers, in that order. Crates come first because they are few and time-limited; the
  card is left out when no crate matches. The stock table shows its top 25 rows under the
  current sort, with a button for the rest, so the containers stay within reach. The
  containers tile counts storage boxes only, and names fluid buffers and Dimensional Depot
  uploaders beside it. All three tables are `dashkit.table()`: sortable headers, explicit
  alignment, and definitions in the header tooltips rather than in notes (T9).
- **Rows:** a stock row sets the filter to that item. A container row stacks its name, region
  and size (slots, or m³ for a fluid buffer) in one cell. The contents appear once, in
  "holds", and the fill column carries the used-of-size figure as its tooltip. The holds sort
  keeps solids and fluids apart, since counts and m³ do not compare. A crate row uses
  `crateLabel()` from `crates.ts` (the name the map popup uses) and shows the server's kind
  sentence under it. Below 600 px the fill bar collapses to its percentage, and the crate
  contents move under the crate's name. A container or crate row with a position has
  **map**, which returns to the map, flies there and draws the panel's highlight ring
  (`showPoint`).
- **Filter:** case-insensitive. A query that names an item exactly shows only that item, and
  the line under the filter links the other names containing it; otherwise it matches
  substrings. The tiles count the whole world, and the filter changes rows only
  (principle 6). While a filter is active, "show empty" is disabled, since only containers
  holding a match are listed. A query that matches nothing anywhere gives one empty state
  instead of three empty cards.
- **States:** `dashkit.loading()` until `/api/stock` lands, and `dashkit.error()` with a
  retry (`loadOne`) when it fails.
- **Spoilers:** the section lists only what the save holds, so the spoiler setting has
  nothing to hide here.
- **Code:** `frontend/src/inventory.ts`. `dashboard.ts` only registers the tab and routes to
  `renderInventory`, passing its `toMap` and `render`.
- **Not yet:** a "storage near here" point filter, `as_of=`, and turning the storage layer
  on when a container row flies to the map (the ring marks the spot, but the box itself is
  hidden while that layer is off).

---

## 11. Progress (2026-09-27)

The dashboard's Progress tab now covers all six read tools of roadmap phase 4. The slow
advisors (`rank_unlocks`, `advise_hard_drive_pick`) stay in phase 12.

### 11.1 What was built

- **Routes.** Five GET routes join `/api/progress/milestones` in `routers/progress.py`:
  `mam`, `phase`, `shards`, `sloops` and `harddrives`. Each reads the same domain objects
  as its MCP tool: `SchematicLadder` and `ResearchGates` for MAM, `PhaseLedger` for the
  elevator, `OverclockBudget` for shards and sloops, and `HardDriveDesk` for the drives.
  The tool bodies are unchanged. Every route declares a response model and follows the
  `?save=`/`?world=` convention.
- **Page.** `frontend/src/progress.ts` holds the section. It was moved out of
  `dashboard.ts` with the milestone view unchanged. The shared building blocks (tile,
  note, link, table cell) now sit in `frontend/src/dashkit.ts`, which both modules import.
- **Layout.** A segmented control (`dashkit.tabs2`) reaches one page per tool. The
  Milestones landing opens with a one-line "next up" strip (T4): parts short for the
  elevator, affordable MAM nodes, pending drives, free shards and free somersloops, each a
  link to its page. Statuses read in lowercase words: done, affordable, short, blocked by…,
  running, and "locked (phase N)" for a milestone whose tier the next phase opens.
  - Milestones: tiles, the tier strip and the table.
  - MAM: tree tallies and a table with the tool's statuses ("tree not open" for TREE SHUT).
  - Space Elevator: the target phase joined to stock, plus every phase as delivered, next
    or not started.
  - Hard drives: each pending drive with both options and what they grant.
  - Power shards: free, craftable, slotted and idle, the slugs, and the overclocked buildings.
  - Somersloops: free, slotted and Mercer Spheres, and the amplified machines with both
    boost readings.
  - Every building row flies to the map.
- **Addresses.** `#dash=progress/mam`, `progress/elevator`, `progress/drives`,
  `progress/shards` and `progress/sloops`. A bare `progress` opens the milestones.

### 11.2 Decided 2026-09-27

- Routes live under `/api/progress/` rather than at `/api/harddrives`, `/api/shards` and
  `/api/sloops` as §5.2 first proposed. One module and one prefix for one tab.
- The spoiler switch covers the whole tab. Its label read "Show upcoming milestones and
  research"; with recipes added (§12.3) it now reads "Show upcoming milestones, research and
  locked recipes". When it is off, the page hides three things:
  - MAM nodes in trees not opened yet (TREE SHUT);
  - elevator phase records past the target phase;
  - milestone tiers the HUB has not opened yet.

  Hard drive options stay visible, because the game shows them once a drive is analysed.
  This answers §7 Q11 for MAM nodes. Alternates in the codex follow the same switch (§12.3).
- A stale elevator record is shown greyed and labelled, never hidden and never used as a
  cost. Only the target phase row is joined to stock, the same rule `phase_requirements`
  states.

### 11.3 Not yet

- Filters and search inside the MAM and milestone tables (the tools' `query=`).
- A drop-pod map layer tied to the hard drive page (§2.2).
- The MAM costs show the class id `Desc_HardDrive_C` where a node asks for a hard drive,
  because `item_name` has no entry for it. The MCP tool prints the same id.

---

## 12. Recipes codex and search box (2026-09-27)

Phase 5 of §6. It is a read-only surface over the game data, marked against the save.

### 12.1 What was built

- **Routes.** Each calls the function its MCP tool calls. `find_items` and `makers_of` in
  `core/gamedata/search.py` and `find_recipe` in `domain/planning/scenario.py` moved out of
  the tool bodies so both surfaces share them.

  | Route | Tool | Needs a save |
  |---|---|---|
  | `GET /api/gamedata/items?q=` | `search_items` | no; capped at 200 rows, `total` counts all |
  | `GET /api/gamedata/recipes?q=&consumes=&produces=&recipe_kind=&only_alternates=` | `search_recipes` | no; `unlocked` is null and `save_note` says why |
  | `GET /api/gamedata/recipe?recipe=` | `recipe_detail` | no; id or name, 409 lists an ambiguous name's matches |
  | `GET /api/gamedata/alternates?item=` | `alternates_for_item` | no; `granted_by` only on locked rows |
  | `GET /api/gamedata/unlocked?only_alternates=` | `unlocked_recipes` | yes; 404 without one |
  | `GET /api/search?q=` | the header box | no; factories need one |

  The recipe detail takes a query parameter rather than a path segment, so the page's
  typed `get()` can check the URL against the schema.
- **Recipes tab** (`dash=recipes`, `frontend/src/recipes.ts`). It has three modes: Items,
  Recipes (kind picker, alternates only, census line) and Unlocked. The mode and the filters
  live in the address (`dash=recipes/<mode>?q=…&kind=…&alt=1&all=1`), so Back restores
  them, and the list's scroll position comes back with them. The census line leads with the
  rows shown and names the event recipes it hides. Clicking an item opens
  its card (`dash=recipes/item/<cls>`), with **made by** (alternates first, have or locked,
  granted by) and **used by** (every kind, per minute, per build or per craft). Clicking a
  recipe opens its card (`dash=recipes/recipe/<cls>`), with machine, cycle, power, grants,
  and in and out rates that link to items. A building recipe's product is a building, not
  an item, so it is not linked; a building's own item card points at its build cost
  (`build_recipe`). A recipe name that is unknown or ambiguous shows an error and the
  recipes whose names contain it. Item icons come from `/api/icons`. The codex
  sends one HEAD probe first and draws no icons when it answers 204, so an install without
  the icon directory logs no 404 per item.
- **Header search** (`frontend/src/search.ts`). One box, focused with `/`. Results are
  grouped as factories, items and recipes of every kind, eight of each, with the totals said
  below. A recipe hit names its machine, "building" or "crafted", so two recipes with one
  name stay apart. Arrow keys and Enter pick a result; Enter pressed before the debounce
  lands waits for the reply to the text typed, not the previous one. The first Escape closes
  the list and keeps the text. A factory opens its dashboard detail, and an item or recipe
  opens its card. The box is an ARIA combobox over the `listbox` of hits.
- **Freshness.** `recipes.ts` registers `/api/gamedata/unlocked` in the live wave. Each save
  event bumps a generation that empties the codex cache, so have and locked follow the game.
  The first reply after a load or a switch only primes the cache, so a cold load fetches
  once.

### 12.2 Cost per keystroke

Both inputs debounce: 150 ms in the header and 180 ms in the codex. A newer query drops an
older reply. The codex keeps the last rows on screen while the next query loads.

Measured on the reference fixture in-process (median of 30): `/api/search` 0.7–1.0 ms,
`/api/gamedata/items` unfiltered 0.7 ms (20 KB), `/api/gamedata/recipes` unfiltered 1.1 ms
(42 KB). Over HTTP on a live server with a real save: 3 ms warm. The first call after start
took 230 ms, which is the state load and not the search. No index or cache was added.

### 12.3 Spoilers

Decided 2026-09-27: the one spoiler switch covers recipes too. Merged with the Progress
wording (§11.2), it is now labelled "Show upcoming milestones, research and locked
recipes". When it is off:

- The codex, the item card and the header box drop recipes this save has not unlocked, and
  say how many were hidden.
- A locked recipe's own card shows only that it is locked.
- Census lines keep their totals but drop the locked counts.

Items are never hidden. An item carries no unlock of its own.

**The rule lives on the server.** Every row the switch can hide carries `spoiler: bool`:
milestones and tiers above both the highest tier with a finished milestone and the highest
tier the delivered Space Elevator phases open (phase N delivered opens tiers up to 2, 4, 6, 8
and 9 for N = 0 to 4; `domain/progression/phases.py`), MAM nodes and capabilities in a tree
not opened yet, Space Elevator phases numbered past the target phase (every phase, on a save
with no target phase), and recipes the save has not unlocked (codex rows, makers, the recipe card and
search hits). `/api/progress/sloops` sends `amplifier_spoiler` for the one non-row case.
Each of those routes also takes `?spoilers=0|1`. Without it the reply is every row plus the
flag. With `spoilers=0` the spoiler rows are dropped and each count in the reply counts
only what is returned, the recipe census included. `/api/search` keeps `only_unlocked` as
an alias of `spoilers=0`. The Progress tab fetches every row and hides by the flag, so the
switch needs no refetch. A milestone row also carries `opens_at`, the phase that opens a
tier not open yet, which the tab shows as "locked (phase N)" instead of counting it short.

### 12.4 Not yet

- `list_buildings` (Recipes > Buildings).
- "Where is this made": highlighting the factories that run a recipe.
- The **compare routes** and **bill** buttons on an item card (phase 8).
- Plans, regions, node ids, `x,y` and `chain:<n>` in the search box (§2.1).
- Items following the spoiler switch (decided in §14, T3) needs a rule for when an item is
  a spoiler; no row carries one yet.

---

## 13. Trace on the map (2026-09-27)

Phase 7 of §6. What feeds a machine or a factory, or what it feeds, drawn on the map.

### 13.1 What was built

- **Route.** `GET /api/trace?seed=&direction=up|down&as_of=` (`routers/trace.py`). The seed
  grammar is `resolve_seeds` in `domain/factories/trace.py`: an instance, a building name, a
  factory label, or any selector. The MCP tool `trace_upstream` now calls the same function,
  so the two cannot disagree about what a seed means. The walk is the existing `trace()`.
- **What it sends.** Every machine on the path (seeds included) with position, hops, recipe,
  nameplate rates at its clock and its `health.assess` state; every conduit run the walk
  crossed, as polylines; and `flowgraph.build` over the seeds plus everything reached, which
  gives recipe groups and item flows with apportioned rates. `items` totals what the reached
  machines make (up) or use (down).
- **Frontend.** `frontend/src/trace.ts`. Entry points: the machine popup, the right-click
  inspector when the click lands on a machine (the machine layer has to be on), the selected
  row in the side panel's Factories tab, and "trace supply" on the dashboard's factory page.
  All but the dashboard use one delegated click on `data-trace` buttons, so no drawing module
  imports the trace module. The card toggles up/down and clears; it refetches on each live
  save and clears on a world switch.
- **On the map.** Crossed runs in the panel highlight colour. Each machine on the path gets a
  ring: red when stopped, yellow when blocked, highlight otherwise; seeds are larger. The
  colours are the placements layer's own, so the marker key still reads.

### 13.2 The two bugs

- **`trace_upstream` with a factory label.** Did not reproduce at `bb5b04c`: the fix is already
  in, and `tests/test_trace_seeds.py` covers labels and selectors. The seed resolution moved from
  the tool body into the domain (`resolve_seeds`) so the route shares it, with a domain-level
  test for the label path.
- **`show_on_map` links.** The tool already led with a local link; its host and port were
  hard-coded. `config.web_url()` now builds it from `WEB_HOST` and `web_port()`
  (`SATISFACTORY_WEB_PORT`, default 8712), and `satisfactory-mcp-web` binds the same values.
  The MCP server and the web server are separate processes, so both need the variable when the
  port moves. The satisfactory-calculator.com link stays second: spatial-and-map.md §7.2b
  documents it as the public map, and the tool's `layers` tokens only mean anything there.

### 13.3 Limits, stated

- **Over-reporting.** Belt-to-belt and pipe-to-pipe joins state no direction, and the walk
  takes them both ways (§5.3, physical trace mode). On the reference save a factory's upstream
  can include feeders that only share a manifold with it. The card says so whenever the save
  has such joins.
- **Rates are nameplate**, apportioned by `flowgraph` as on the production graph (§9.8), not
  measured belt throughput. A run on the map carries no item of its own.
- **Not in the fragment yet.** A trace is not deep-linkable (§1 principle 9); a `trace=` key
  would need `state.ts` and `fragment.ts`.
- **Right-click on a machine under a trace ring** opens the ring's popup rather than the
  machine's; the ring's popup has the same trace buttons.

---

## 14. UI audit decisions (2026-09-27)

Decided 2026-09-27, binding for the design-system work and the page batches after it:

- **T1 "Need action"** covers the five health states, blocked included. Blocked stays in the
  count but is drawn yellow. Power faults are separate, as "power problems": no wire is red
  but counted there, and the map outline for no wire uses the power-problem style.
- **T2 Palette.** Map generators move off the stopped red to a warm neutral, storage moves
  off the selection pink to a teal-violet, LOCKED is muted instead of amber, and uptime
  badges carry no colour.
- **T3 Spoilers** default to off, with a one-time notice. When off, counts and search
  results follow the rows, and the item list follows too (§12.3).
- **T4 Tiles.** At most one row of tiles per view. Progress keeps its next-up strip on the
  landing only, as one line; the planner result becomes a key/value line.
- **T5 Level-2 tabs** are one segmented control with a 3 px radius, as on Recipes.
- **T6 Phones.** The page is readable at phone width with no sideways scroll, and the map
  panel becomes a bottom sheet.
- **T7** An unnamed group of machines is an "unnamed cluster" everywhere: Detect, the map
  layer and Settings.
- **T8** The Host check applies to every method (§9.2).
- **T9 Explanations.** One line at most on the page; longer definitions go to the docs. The
  marker key stays.

The vocabulary that follows from these, one term per concept, lives in `words.ts`:

- **need action** is the count and the column name ("3 need action", "none need action");
  **not running** is the wider set of every state that is not fine, defined in its column
  tooltip.
- **power problems** are no wire, no generator and starved generator. They are never folded
  into the need-action count, on the map, the panel or the dashboard.
- **unnamed cluster** is what Detect offers, what the map layer draws and what Settings filters.
- The power ledger says **generation**, **measured draw**, **nameplate draw**, **headroom now**
  and **headroom at full rate** (always signed), and counts **poles and towers**. The header
  uses the same words: "measured draw / generation".
- Status words are lowercase and coloured by token (locked, looted, unlocked, done); upper
  case is only for tag chips.
- Copy is lowercase, without a trailing full stop, and uses a colon or semicolon rather than a
  dash. Names in running text are quoted as “…”.

Settings are grouped as spoilers, detect factories and planner, each with a one-clause hint.
What the hints leave out:

- **show what is not unlocked yet**, off: Progress shows the tiers the HUB has open, the MAM
  trees already opened and the Space Elevator phases up to the current target, and hides what
  lies beyond them; Recipes and search hide recipes the save has not unlocked.
- **only fed unnamed clusters** hides a cluster whose belts and pipes reach no miner,
  extractor or outside machine. A box filled by hand is not a source; a train, drone or truck
  station counts as unknown, and the cluster stays.
- **minimum machines** takes a whole number from 1 to 500; anything else is refused under the
  field and the old value stays.
- **follow chat** decides what the page does when chat solves a plan or opens one. A change
  chat makes to the plan already open always shows up, whatever this says, and the page never
  moves while a field has the cursor.
- **reset to defaults** clears every stored value, so each setting reads its default again.

States and actions follow two rules:

- **Not found is empty, not an error.** A deep link to a factory, circuit, item, recipe or plan
  that does not exist keeps its "‹ all …" link and shows `dashkit.empty()` with what is
  missing and what to do instead; an ambiguous recipe name does the same over its candidates.
  `dashkit.error()` with a retry is only for a read that failed. The server answers every
  missing subject with 404 and `{"error"}`, and so does any `/api/` path or method no route
  serves; `api.missing()` is the one test the page makes.
- **Actions are buttons, navigation is a link.** `dashkit.button()` does something here
  (rename, trace, detect, show all); a text link goes somewhere else ("open in dashboard",
  the Settings filters). A link never sits in a row of buttons as their peer: it gets its own
  line, or it is a word in the sentence the buttons follow.

---

## 15. Narrow widths and the keyboard (2026-09-27)

What holds at every width from 390 px up, and how the page keeps a keyboard user's place:

- **No sideways page scroll.** A wide table scrolls inside its `.dash-scroll` box; its first
  column is sticky, painted with `--surface` (the page background, or `--panel` inside a
  card, the side panel and the trace card), so the row name stays in view while the numbers
  scroll. Table cells wrap at spaces only; notes, empty and error lines and detail titles
  also break a long unspaced token.
- **Tiles** go two per row below 600 px, with tighter padding and an 18 px value.
- **Settings** is a two-column grid, label and hint on the left and the control on the
  right; it stacks below 600 px.
- **Detail titles** wrap: a long name takes its own line and the actions move under it.
- **The live indicator** is a `role="status"` region. Connected, it shows only the dot and
  says "live" to a screen reader; while connecting or after a dropped stream it also shows
  "connecting…" or "offline" as text, so the state is not carried by colour alone.
- **One focus ring**: 2 px accent, set once for links, buttons, fields, `summary` and
  anything with a `tabindex`. Components only change its offset.
- **Focus survives a re-render.** The dashboard, the side panel and the planner rebuild
  their DOM on every data change; `keepFocus` in dom.ts finds the focused control again by
  its `data-candidate`, `data-ctl`, `aria-label` or text, and falls back to the view's `h1`
  when the control is gone (a navigation). Every view has one `h1`, visually hidden on the
  list pages. A cancelled rename returns focus to its button; a jump from the dashboard to
  the map focuses the map.
- **Rows.** A clickable table row is a tab stop only when it has no link of its own;
  otherwise the link is the stop and the row click stays a mouse convenience. Enter opens
  it either way. Sortable headers take Enter and Space; the side-panel tabs take the arrow
  keys; Escape closes the search hits first and clears the query second, closes the marker
  key and cancels a rename. A copyable selector in a popup takes Enter.
- **Check:** every view at 390, 768 and 1440 px asserts `scrollWidth === innerWidth`, and a
  Tab walk through every view finds no stop without a visible ring.

---

## 16. Shell: rail, selection, status strip, `as_of` (2026-09-27)

Roadmap phase 1. §2.1 drew the shell before the dashboard existed (§8); this is the shell as
built on top of it.

### 16.1 What was built

- **Rail** (`rail.ts`, `#rail`): Map and every dashboard section in one vertical list, left of
  both views from 900 px up. The current entry carries `aria-current="page"`. Each entry is a
  `dashkit.link`, so its address is rebuilt on click and never carries a stale viewport.
  The rail replaces the dashboard's own tab row at that width; the header's **Map | Dashboard**
  stays as it was.
- **Narrow widths (T6):** below 900 px the rail is hidden and the header's **Map |
  Dashboard** plus the dashboard's tab row are the navigation, as before. Nothing scrolls
  sideways at 390 px.
- **Selection** (`selection.ts`): one selected thing, a factory, a circuit or a point. The
  map sets it (a factory label click, a panel row, a map fly-to), the side panel follows it
  (row highlight and the dashed outline, without flying), and the dashboard sets it when a
  factory or circuit detail opens. A factory that disappears from the health reply clears it.
- **Status strip** (`status.ts`, `#status`): one line under the header. It shows the
  selection with **map** and **clear**, then the vitals, each a link into its section: need
  action (toned as on the Overview), power problems, headroom now and affordable milestones.
  It reads the same replies the Overview reads (`vitals()`, `readyMilestones()`), so it makes
  no request of its own. Below 600 px it scrolls inside itself and drops the "selected" word.
- **`as_of`:** every `/api/` GET accepts `?as_of=<token>` (`interfaces/web/pinning.py`, a
  middleware in front of every route, using `domain/world/pin.py` as the MCP tools do). A
  mismatch is a 409 with `{"error", "stale": true, "pin"}`: `error` is the page sentence,
  `pin` the MCP refusal. The page holds the token `/api/summary` returns and sends it on
  every `get()` except `/api/summary` and `/api/worlds`. It drops the token when a wave
  refetches (a save event or a switch), because that wave is reading a new save. A 409 for
  the token still held puts **save changed · refresh** in the status strip; refresh
  re-reads the save.

### 16.2 Decided here, smallest option

- The strip sits under the header, not at the bottom as §2.1 drew it: the bottom already
  holds the toasts, the trace card and, on phones, the panel's bottom sheet.
- The selection is not written into the fragment. Links are built with the fragment of the
  moment, so a stale `sel=` in an older link would undo a newer selection; the dashboard
  address (`factories/<name>`, `power/<n>`) already deep-links the two kinds that matter.
- A machine clicked on the map opens its popup and does not become the selection.
- Writes keep their own checks: `as_of` in a request body (naming) and 409 conflicts are
  untouched by the middleware, which only reads GET, HEAD and OPTIONS.

### 16.3 Open

- Should the selection chip offer more than **map**: trace supply, plan here, open in
  dashboard as buttons (§2.3)?
- Should a point or a machine be selectable from the map by a plain click, and should the
  selection survive a reload through the fragment?
- Should the rail open as a drawer on phones rather than hand over to the header and the tab
  row?
- The panel beside the map still shows only Factories and Power; §2.1's panel with every
  rail section is not built. The rail sends the other sections to the dashboard.

---

## 17. Factory detail (2026-09-27)

Phase 2 of §6. The factory page gains level-2 tabs past its overview.

### 17.1 What was built

- **Address.** `factories/<name>/<aspect>`, one `dashkit.tabs2` strip under the title:
  overview, flows, machines, power, nodes, links, floors, sites. A name may itself hold a
  `/`, so the last segment counts as an aspect only when it is one of those ids and the whole
  subject is not a factory name (`factoryAddress` in `factory-detail.ts`). Renaming keeps the
  open tab.
- **`GET /api/factories/aspects?factory=`** (`routers/factory_detail.py`). One
  `query.build_view` pass, the one `factory_query` makes, sent as rows: summary numbers,
  power, balance, machines, recipes, buildings, nodes, links and issues. The doc's earlier
  `/api/factories/{id}?aspect=` became one call with every aspect, because the view computes
  all of them together and a path parameter cannot carry a name holding `/`. The balance
  rows come from `FactoryView.balance()`, which `factory_query` now renders too, so the two
  agree on the verdict. Named factories only, like `/api/factories/graph`; other selectors
  are refused with a 404.
- **`GET /api/factories/sites?factory=`.** `factory_sites` as rows. Each site now carries its
  members' instance leaves and its `near:` selector from `domain/world/sites.py`, so the tool
  and the route build the selector in one place. With `?factory=` only the sites holding that
  factory's machines are sent, each with `mine`, the count it holds.
- **Floors** reuse `/api/floors?factory=label:<name>`. A 404 there (no platform under the
  factory) is shown as an empty state, not an error.
- **Map links.** Machines and nodes fly to a point (`pointButton`); a floor row opens floor
  mode on that platform and band, the same address the map's floor picker writes; a site row
  flies to and outlines a box of its spread; a link row opens the other factory's page.

### 17.2 Limits and open questions

- **Nameplate and measured, never blended**, as in `factory_query`. The flows tab has no
  per-item "no monitor" column; the route sends `unmonitored_made` and `unmonitored_used` for
  it.
- **A floor's machine count is everything on that deck**, this factory's or another's on the
  same slab.
- **Open: a world-wide Sites list.** §3.1 placed `factory_sites` under Factories > Sites. The
  route answers without `?factory=`, but no page lists every site yet.
- **Open: proposals.** The tabs exist for named factories only. An unnamed cluster has the
  graph and nothing else.
- **Open: long machine lists.** A factory of several hundred machines renders every row;
  there is no paging or grouping by recipe yet.

