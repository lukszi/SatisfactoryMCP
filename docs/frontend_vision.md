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
8. **Reads first, writes later, writes guarded.** Every route today is a GET. The first POST
   (naming a factory, saving a plan) needs the Host/Origin allowlist from roadmap §4 item 5 in
   the same change.
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
| `unlocked_recipes` | Recipes > Unlocked | alternates-only toggle | sortable table | new |
| `power_report` | Power (step 0) | none | capacity vs draw, nameplate + measured, per generator kind | new (step 0 may add it) |
| `factory_sites` | Factories > Sites | none | cluster list; map clusters | new (or fold into `/api/factories`) |
| `whereami` | Header "me" button; World > Here | radius slider | player marker + nearby list | exists (`/api/summary.player`) + new for the nearby list |
| `list_regions` | World > Regions; region picker in every sources form | resource filter | list; region layer highlights | exists `/api/regions` (add resource filter) |
| `describe_location` | Map click inspector | click point, radius | popup: region, elevation, nodes, conduits, buildings | exists `/api/inspect` (same default radius); add conduits/buildings counts |
| `search_conduits` | World > Conduits; context menu "conduits here" | near (click), radius, to (second click), belt/pipe, runs/networks | run list; runs highlighted on the map | new (geometry exists in `/api/belts`, `/api/pipes`) |
| `search_resource_nodes` | World > Nodes | sources builder, resource, purity, kind, free only, view fields/nodes/nearest, near | field clusters or node rows; map filters to the result | partial `/api/nodes`; new for fields and nearest |
| `show_on_map` | Built in: every *fly to* and the URL fragment | n/a | map moves, layers tick | exists (fragment) |
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
| `stock` | Inventory > Stock | item, "where" toggle | four piles as columns; where rows fly to box | new (data in `/api/storage`) |
| `storage` | Inventory > Containers | item, near (click), radius, solid/fluid, show empty | container table with fill bar; map filter | exists `/api/storage` (add filters server-side) |
| `crates` | Inventory > Crates | none | list, sorted by distance to me | exists `/api/crates` |
| `phase_requirements` | Progress > Elevator | none | have / short / deliverable | new |
| `power_shards` | Power > Shards | plan machines, plan clock | held / committed / free; cost of an overclock plan | new |
| `somersloops` | Power > Sloops | none | held / slotted / owned; where slotted | new |
| `mam_research` | Progress > MAM | show todo/affordable/all, query | tree or table with cost, have, affordable | new |
| `milestones` | Progress > Milestones | show, tier, query | per tier cost / have / short / grants | new |
| `collected_from_world` | World > Collectibles | group, show census/collected/remaining/nearest, near | census + list; pickups layer | exists `/api/collectibles` |
| `list_pending_hard_drive_choices` | Progress > Hard drives | none | per drive: two options, rerolls, recipes granted | new |
| `advise_hard_drive_pick` | Progress > Hard drives > "rank options" | drive, sources | options by marginal value | new (slow: counterfactual LPs) |
| `search_items` | Search box; Recipes > Items | query | list: form, energy, sink points | new |
| `recipe_detail` | Recipes > recipe card | recipe | rates, machine, power, unlocked by | new |
| `alternates_for_item` | Recipes > item card | item, include locked | recipes that make it, HAVE/LOCKED | new |
| `search_recipes` | Recipes > Search | query, consumes, produces, part/building/manual/all, alternates only, events | census header + rows | new |
| `list_buildings` | Recipes > Buildings | building kind | table | new |
| `factory_map` | Factories > Candidates / Slabs / Unlabelled | show candidates/named/slabs/unlabelled/all | lists; slab outlines on the map | exists `/api/factories`, `/api/structures` (partial) |
| `factory_query` | Factory detail tabs | factory, aspects (summary, machines, recipes, buildings, balance, inputs, outputs, internal, power, nodes, links, issues) | one sub-tab per aspect | new |
| `factory_health` | Factory detail > Health; Factories list colour | factory or all | per state counts, why stopped, evidence line | new (map colours exist via `/api/machines`) |
| `propose_factories` | Factories > Proposals | max span, unnamed only | proposals with score; outlines on map; "name this" | exists `/api/factories` (proposals) |
| `select_machines` | Name/amend dialog, live preview | selector text or map lasso, split, expand | highlighted machines + count | new |
| `name_factory` | Name dialog | name, selector/lasso, notes, dry run | new label; map outline | new, W |
| `rename_factory` | Factory detail > edit | text | renamed | new, W |
| `amend_factory` | Factory detail > edit | add/drop via lasso or selector, prune missing, dry run | diff preview, then applied | new, W |
| `list_factories` | Factories list (step 0) | none | named factories, % still standing | exists `/api/factories` |
| `forget_factory` | Factory detail > edit | confirm | label gone | new, W |
| `trace_upstream` | Factory/machine detail > Trace | seed (selection), up/down | tree; path drawn on the map | new |
| `factory_floors` | Factory detail > Floors; floor picker | factory or platform | decks with machines; floor picker jumps | exists `/api/floors` |

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
| `/api/factories/{id}` + `?aspect=` | GET | `domain/factories/query.py` | one aspect per call |
| `/api/factories/{id}/health` | GET | `domain/factories/health.py` | `all` for the list colours |
| `/api/trace` | GET | `domain/factories/trace.py` | seed, direction |
| `/api/stock` | GET | `domain/world/inventory.py` | four piles, `where` |
| `/api/progress/{milestones,mam,phase}` | GET | `domain/progression/ladder.py`, `phases.py` | one ladder, three views |
| `/api/harddrives` | GET | `domain/progression/harddrives.py` | list |
| `/api/harddrives/{id}/advice` | POST | `domain/planning/advisor.py` | slow |
| `/api/shards`, `/api/sloops` | GET | progression | |
| `/api/gamedata/{items,recipes,recipe/{id},buildings,alternates}` | GET | `core/gamedata` | no save needed except HAVE/LOCKED |
| `/api/nodes/fields`, `/api/sites/rank` | GET | `domain/spatial/select.py`, `ranking.py` | |
| `/api/conduits` | GET | `domain/world/conduits.py` | |
| `/api/select/nodes`, `/api/select/machines` | GET | the two selector modules | live preview counts |
| `/api/plan/solve`, `/bom`, `/compare`, `/byproducts` | POST | `prepare.py`, `bom.py`, `compare.py`, `byproducts.py` | body = plan kwargs |
| `/api/plan/layout`, `/diff`, `/commission`, `/unlocks` | POST | `layout_service`, `diff_service`, `commission_service`, `sensitivity` | `/unlocks` slow |
| `/api/plans` (CRUD) + `/api/plans/{n}/site` | POST/PATCH/DELETE | `domain/planning/store.py`, `siting.py` | W; watcher already publishes store events |
| `/api/labels` (CRUD) | POST/PATCH/DELETE | `domain/factories/labels.py` | W |

Before any W route: the Host/Origin allowlist (roadmap §4.5). Local writes from a hostile
page are the one new exposure this vision creates.

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
| Transport networks (trains, drones, trucks) | absent; **deferred by Lukas** | Logistics screen |
| Awesome Sink / Dimensional Depot | unraised with Lukas | Sink points, depot uploads |
| Belt arrows | `ROUTE_CHEVRONS.belts = false`; **held** | Map only |
| Fog of war | raster unverified; **held** | Map only |
| Forecasts / ETAs | forbidden until the timeline exists (roadmap §2.3) | Any "N hours to" line |

---

## 6. Roadmap

Smallest useful slice first. Each phase ships on its own. Reads before writes.

| Phase | Slice | Tools covered | New routes | Writes |
|---|---|---|---|---|
| 0 | Factories + Power panel (in progress) | `list_factories`, `power_report`, parts of `factory_map` | per step 0 | no |
| 1 | **Shell:** rail, selection model, status strip, `as_of` on fetches | `world_summary`, `list_worlds` | none | no |
| 2 | **Factory detail:** aspects, health, floors | `factory_query`, `factory_health`, `factory_floors`, `factory_sites` | 2–3 | no |
| 3 | **Inventory:** stock, containers, crates | `stock`, `storage`, `crates` | 1 | no |
| 4 | **Progress (read):** milestones, MAM, elevator, shards, sloops, drives list | 6 tools | 4–5 | no |
| 5 | **Recipes codex + search box** | 5 game-data tools, `unlocked_recipes` | 5 | no |
| 6 | **World finders:** nodes, fields, conduits, collectibles, whereami, inspector upgrade | 7 spatial tools, `collected_from_world` | 3 | no |
| 7 | **Trace:** upstream/downstream drawn on the map | `trace_upstream` | 1 | no |
| 8 | **Planner (stateless):** solve, bill, compare, byproducts | 4 planning tools | 4 POST | no |
| 9 | **Write guard + Plans:** save, rename, forget, site by dragging | 4 plan tools | CRUD | **yes** |
| 10 | **Plan follow-through:** layout, diff, startup order | `plan_layout`, `diff_vs_save`, `commission_plan` | 3 | no |
| 11 | **Labels:** propose, preview, name, amend by lasso | 6 label tools | 2 + CRUD | **yes** |
| 12 | **Advisors (slow):** unlock value, hard drive pick | `rank_unlocks`, `advise_hard_drive_pick` | 2 | no |
| 13+ | Domain gaps in §5.3, in the order Lukas picks | — | — | — |

After phase 12 every tool, resource and prompt has a surface.

---

## 7. Open questions for Lukas

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
   and plans at all, or stay read-only with writes via chat?
6. If yes: drag-to-site on the map, or typed coordinates only?

**Planning**
7. How do you usually start a plan: target item rate, max MW, or "what can this field make"?
8. Should the Planner remember the last form per world, or start empty?
9. Compare routes: which costs matter to you (raw total, one scarce resource, power, machines)?

**Progression**
10. Should the UI suggest a "next" step at all, or only show facts and let you decide?
11. Spoilers: show locked milestones, MAM nodes and alternates in full, or only what the game
    shows you at this point? **Answered 2026-09-26 for milestones: a per-browser setting**
    (§8.6). MAM nodes and alternates are not covered yet.
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

Lukas asked for "a first-class UI dashboard", with a justification. This section is that
justification, then the record of what was built. The code carries no prose; this is its home.

### 8.1 What Lukas has already decided

Evidence only: each row cites where the decision is written down.

| Decision | Source | What it means for a dashboard |
|---|---|---|
| Local only, bundled, no CDN | `index.html` head comment; `vite.config.ts` (Leaflet licence copied at build); web `__main__.py` binds 127.0.0.1 | No chart library. Bars are CSS widths; nothing is fetched but `/api` |
| Palette closed, "lgtm" 2026-08-06 | memory `map-ui-future-ideas` | No new colour. The dashboard uses `--ok`, `--warn`, the panel's accent blue and its greys |
| Colours are audited, dE 15 across owners | `palette.ts`; commit `db50aa7` | The dashboard declares no map colour, so the audit has nothing new to compare. Its three bar colours are 25.8 or more apart (CIE76) |
| Comment budget, no exemptions | memory `prose-goes-in-docs` ("No excemptions"); docs/comments.md | `dashboard.ts` has a two-line header and no other comments |
| Read-only | parked.md §16 "Read-only; no editing"; every route is a GET (`test_every_get_says_what_it_sends`) | The dashboard reads. No POST |
| Facts, not advice | commit `deadb0b` "Name the machines no wire reaches, instead of advising a power check"; roadmap §2.3 bans ETAs from one 300 s window | No "next best" score, no ETA, no trend line (the timeline has no consumer yet) |
| Ask before encoding play patterns | memory `verify-play-patterns` (a hand-feeding setup read as a factory; shoreline siting) | Order comes from the domain. "temporary …" factories are neither hidden nor discounted |
| Belt arrows and fog of war held; transport deferred | memory `map-ui-future-ideas`; "I'm not at transportnetworks yet" | No logistics section |
| Measure before designing | memory `measure-before-optimising` | Timings in §8.3 |
| Typed wire, no guessed payloads | commits `c60df84`, `63c9b5b`; `test_every_get_says_what_it_sends` | The new route has a `response_model`; TS types come from `npm run typegen` |
| Host allowlist deferred (household NAT, two people) | roadmap §5 risk 5 | Only reads were added, so there is no new exposure |
| Wording | commit subjects: plain sentences that name the thing ("Say which pods still hold a drive") | Labels are plain words. `–` means unknown, never 0 |

### 8.2 The §7 questions his record already answers

- **Q10 (suggest a next step?)**: the record points to facts only (`deadb0b`, the ETA rule).
  The dashboard shows facts. It is still his call.
- **Q16 (held items)**: belt arrows and fog of war are held. Transport is deferred. Both are
  explicit. **Sink/Depot is still unraised.**
- **Q5 (writes)**: partly. §16's "read-only" was about the plan picture only. The dashboard is
  read-only either way. Label and plan writes stay open.
- **Q14 (timeline)**: he picked §22 as a next feature on 2026-08-02, so he wants history.
  The default window is still open.
- **Q17 (`<pre>` fallback)**: the typed-wire commits lean "no". It is still his call.
- **Q3 (other devices)**: the server binds 127.0.0.1 and he deferred the Host rule. That
  counts against LAN use today, but it does not settle it.
- Still open: Q1, Q2, Q6–Q9, Q12, Q13, Q15. Q4 and Q11 are answered in §8.6.

### 8.3 For, against, verdict

**For.**
1. He asked. Principle 2 ("never a page that hides the map") was this note's own rule. It
   was drafted without him and is not one of his decisions.
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
   same gap.

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

### 8.6 Decided by Lukas, 2026-09-26

- **A blocked machine is a problem.** "Need action" is `health.ACTIONABLE`: dead node, no
  recipe, **blocked**, starved and stalled. It is one tuple in the domain, used by the
  `factory_health` MCP tool (its `todo` column and sort), by `/api/factories/health`
  (`actionable`, and `actionable_states`, which the dashboard and the side panel read instead
  of keeping their own list). Uptime still reads 0% on a backed-up factory, and that is true:
  a blocked machine produces nothing. It now sits beside a non-zero "need action" count, so
  the two agree. The map's machine paint (`placements.ts`) still draws `blocked` like a
  running machine; that is a separate question.
- **Spoilers are a setting, on by default.** Settings (`dash=settings`) holds a "Show
  upcoming milestones" switch. Off, Progress shows only tiers with at least one milestone done
  (done and not done), plus tier 1 before anything is done; the tier strip, the tallies and
  the Overview's "affordable" count follow the same filter. Nothing in the docs or memory
  decided the default, so it is **on**, which was the behaviour before. `settings.ts` keeps
  the values in `localStorage` under `settings`, wrapped in try/catch, so the page still works
  without storage. There is no write route: the web UI stays read-only (parked.md §16). A new
  setting is one more entry in `SETTINGS`.
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
