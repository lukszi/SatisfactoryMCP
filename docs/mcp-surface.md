# The MCP surface, the context budget, caching and tests

Part of the [SatisfactoryMcp design spec](../DESIGN.md) — §10 is the tool surface with its
transcripts and the context budget that shapes every response, §11 is caching and §12 is the
testing contract. Section numbers are continuous with the rest of the spec;
[DESIGN.md](../DESIGN.md) indexes it.

---

## 10. MCP surface

`mcp` **1.28.1**, `FastMCP`, stdio, protocol `2025-11-25`.

> **`structured_output=False` on every text tool.** A tool annotated `-> str` gets an `outputSchema`
> *and* has its payload echoed into `structuredContent` — a measured **1.96×** wire-size tax for zero
> benefit (580 → 1,136 bytes on the real Plastic response). Enforced by the shared decorator
> `app.tool`.

### 10.1 Tools

**Game data:** `search_items`, `search_recipes`, `recipe_detail`, `alternates_for_item`, `list_buildings`
**Save state:** `list_worlds`, `world_summary`, `unlocked_recipes`, `power_report`, `factory_sites`, `whereami`, `phase_requirements`, `power_shards`, `collected_from_world`, `mam_research`, `somersloops`
**Factories:** `factory_map`, `propose_factories`, `factory_query`, `factory_health`, `select_machines`, `name_factory`, `rename_factory`, `amend_factory`, `list_factories`, `forget_factory`, `factory_floors`, `trace_upstream`
**Inventory:** `stock`, `storage`, `crates`
**Spatial:** `list_regions`, `describe_location`, `search_resource_nodes`, `search_conduits`, `rank_build_sites`, `show_on_map`
**Planning:** `plan_factory`, `plan_layout`, `commission_plan`, `diff_vs_save`, `bom`, `rank_unlocks`, `list_plans`, `plan_log`, `site_plan`, `rename_plan`, `forget_plan`, `explain_byproducts`, `compare_recipe_options`
**The page:** `ui_context`, `settings`
**Hard drives:** `list_pending_hard_drive_choices`, `advise_hard_drive_pick`

```
plan_factory(objective="max_mw", target_item=None, sources=[...],
             exports=["__MW__"], export_minimums={}, only_free_nodes=False,
             allow_sinks=True, save=None, world=None, limit=15,
             logistics_items=None)
  -> summary (net_MW, machines, grid_import, exports, raw, sunk)
     + warnings/binding first, then a process table with a BUILD marker
     + a logistics table of `limit` flows, plus any logistics_items pinned

search_resource_nodes(sources=[...], resource=None, purity=None, kind=None,
                      only_free=False, status="free"|"tapped"|"all"|None,
                      show="fields"|"nodes"|"nearest", near=None, limit=25, offset=0)
  -> per-field clusters (region, grid, centre, purity mix, total/free, spread)
     or per-node rows whose ids feed straight back in as node: selectors

search_recipes(query="", consumes=None, produces=None,
               recipe_kind="part"|"building"|"manual"|"all",
               only_alternates=False, include_events=False, save=None, world=None,
               limit=10, offset=0)
  -> a census over ALL 872 recipes broken down by kind and HAVE/LOCKED, then rows
  -> `world` matters: HAVE/LOCKED is a fact about one world, and without it a
     multi-world install was silently answered against the default

bom(item, qty=60, allow_sinks=True, outlets=[], exclude_recipes=[], only_recipes=[],
    limit=20, offset=0)
  -> raw totals + one row per item: made/min, used/min, the recipe chosen, machines, building
```

`warnings` and `binding_constraints` come **first** — that's where the insight is ("Blender unlocked but
0 built"; "920/min resin needs an outlet or the line stalls").

**No `plan_chain` by recursive expansion.** It is unsound on this user's own recipe set: Recycled Plastic
(30 Rubber + 30 Fuel → 60 Plastic) and Recycled Rubber (30 Plastic + 30 Fuel → 60 Rubber) form a genuine
2-cycle and **both are unlocked**. There is no correct depth limit. The LP is the only engine.
`bom` ([§10.1c](#101c-bom--the-flattened-bill)) is the sanctioned answer to the question `plan_chain`
was meant to answer, and it is a presentation layer over `solve`.

Also required and absent from the first draft: a **power target** must be expressible (the driving use
case is MW, not an item), and generator fuel throughput needs `mEnergyValue` with its per-litre /
per-item split.

### 10.1a Plan persistence

`diff_vs_save` re-solves rather than taking a plan handle, which means retyping fifteen
arguments to ask "how far along am I". Plans are now nameable:

```
plan_factory(..., save_as="north oil", plan_notes_text="...", for_factory="oil setup")
plan_factory(plan="north oil")            # recall and re-solve
diff_vs_save(plan="north oil")            # diff without retyping
plan_layout(plan="north oil")
list_plans() / forget_plan(name, base_rev=14)
```

Every stored plan is versioned and every change to one takes `base_rev`; see
[§10.1k](#101k-plan-versions-and-the-activity-journal).

**The request is stored, never the solution.** A solve depends on the unlocked recipe
set, which nodes are free and which buildings exist — all of which move as the game is
played. A stored solution would keep answering about a world that no longer exists, and
would do it silently. Storing arguments and re-solving on recall always answers about the
world as it is now.

That gives `plan_id` a second job. It already hashes the arguments *together with* the
save-derived solve inputs (§ scenario), so recording it at save time and comparing on
recall detects exactly the interesting case: **the plan did not change, the world did.**
`list_plans` reports that as `world moved`.

**What `plan_id` cannot see: the arguments changing meaning.** `sources:
["region:Spire Coast"]` is a *name*, resolved through a table this repository generates.
Re-deriving that table from the game's own `FGMapAreaTexture` took the name from 51 nodes
to 18 — no argument touched, no id disturbed, and every recall since re-planned over a
different sixth of the map in silence. A region name is advisory *by design*
(§ regions); a stored plan whose meaning moves without saying so is not.

So a plan also records **what each selector resolved to**: its count, a hash over the
node names, the names themselves (capped at 250 — a whole-map spec is 608 rows), and the
**bounding box those nodes occupied, in metres**. On recall each selector is re-resolved
and compared; on a difference the response says which selector, how many nodes then and
now, which appeared and vanished, and offers the box as a `bbox:` rewrite — the same move
`tests/conftest.py` made by hand after the re-cut, because a box cannot be re-cut under a
stored plan. The plan still recalls; the note is the deliverable. An unchanged field says
nothing at all. `list_plans` reports a moved one as `field 51->18`.

A plan saved before any of this carries **no record**, which is reported as *cannot be
checked* — never as unchanged — together with what its selectors resolve to today and
that field's box, so the reader can settle it themselves. Re-saving records it.

**The defaults trap.** MCP fills declared defaults in before a tool sees them, so
`objective` always arrives as `"max_mw"` and a naive merge would clobber every recalled
plan with it. `PLAN_DEFAULTS` records each argument's declared default, and a supplied
value counts as an override only when it *differs* from it. The honest cost: a recall
cannot explicitly reset an argument back to its default — re-save the plan for that.

Overrides are applied but **not persisted**, and the response says so. Stored args are
filtered to those that shape the solve — not `limit` (presentation) or `save`/`world`
(which file was read, not what was asked) — and defaults are dropped, so a stored plan
reads as the request that was made. `Plan.kwargs()` filters unknown keys so a plan saved
by an older build cannot break a newer `build_scenario`.

Stored per world under `saveIdentifier` in `user_data_dir/plans/<world>/`, beside the labels
and for the same reason, as one append-only op log per plan
([planner_slice_contract.md](planner_slice_contract.md) §5).

**Siting.** A stored plan may also record **where it stands**: origin (the footprint's
centre, metres, optional z), yaw (degrees about world Z, +X towards +Y — the same
convention the save stores machine facing with), and footprint (`WxD` metres, either
caller-measured or the square `plan_layout` budgets for the largest floor, and the record
says which). Set at save time (`plan_factory site_at=... save_as=...`) or afterwards
(`site_plan`), cleared with `site_plan(clear=True)`. A siting is a *record of the
decision, never a constraint*: nothing feeds it to the LP, and re-solving neither reads
nor moves it. It is read for one thing — `plan_factory` measures the terrain at the pad
and prints what the water there actually is instead of assuming it, on a fresh `site_at`
and on every recall of a sited plan; the numbers it prints never move the plan's own.
Once sited: every recall prints it, `list_plans` grows a `sited(m)` column,
`diff_vs_save plan=...` adds an **ON SITE** census — counts by building class inside the
(properly rotated) footprint against the plan's bill, named *approximate* because it
checks neither recipes nor clocks — and `show_on_map at='plan:<name>'` centres both
the public map and this project's own web map (`#z=…&c=x,y`) on the origin. Old plan
files load unchanged; no siting is an ordinary state, not an error.

**Scoping.** `diff_vs_save(factory=...)` — or a plan saved with `for_factory` — limits
what counts as *already built* to that factory's machines. Unscoped, "you already have 12
of these" counts constructors on the far side of the map that are busy doing something
else, which is the wrong answer to "how far along is the aluminium setup". On the
reference save, scoping an aluminium plan moves `to_place` from 156–167 to 188.

Node handling is the subtle part. A node tapped by a *different* factory drops out of
**both** the reusable and the free set:

- left in `tapped` it would read as already built for this plan;
- moved to `free` it would plan a second miner onto an occupied node.

`extractor_on` is deliberately left un-scoped, because occupancy is a fact about the world
rather than about the factory being asked.

**`plan_layout(factory=...)` scopes differently, because a layout has no coordinates.**
`build_layout` is abstract on purpose — blocks, buses and floors with sizes in metres —
since a player places machines themselves and a solver inventing positions would be both
wrong and unwelcome. So scoping cannot mean placing blocks. It answers the two questions
the abstract layout leaves open once you know *where* it goes:

- **Does it fit?** The structure layer knows the slab's tile count and extent; the layout
  knows its peak-floor footprint. The gap is foundations to pour. A shortfall is reported
  as a number, not a failure — floors stack, so building up may resolve it, and the note
  says so when the factory is already multi-storey.
- **What already stands there?** A block matched by (building, recipe) against machines in
  that factory is not work. On the reference save an aluminium layout reads *"106 tiles
  across 1 platform, 135×135m; layout needs 338 at its widest floor — needs 232 more tiles,
  or a floor above. 1 block standing, 36 to build."*

Two honesty constraints. The standing count is **consumed as it matches**, or one smelter
would satisfy every Iron Ingot block in a split process. And a standing machine is reported
as *present*, never as *correct* — it may be on a different clock or feeding something
else.

### 10.1b Reverse recipe lookup — "what consumes X", and proving the list is closed

`search_recipes` matched recipe **names** only, so "what eats Rubber" had no answer. What actually
happened was that candidate consumers were recalled from memory and checked one at a time — roughly
eight speculative calls, and at the end of them still no way to say the list was complete. The worry
was specific and correct: *"if some Tier 7-9 building eats rubber, I'd have missed it."*

**A parameter on `search_recipes`, not a new tool**, for the same reason `search_resource_nodes` took a
`show` instead of splitting ([§7.2a](spatial-and-map.md#72a-node-lookup--one-tool-three-views)): the body is ~90 % shared —
filter, sort, page, render, HAVE/LOCKED — and only the predicate differs. §10.3's rule against duplicate
surfaces applies with more force here, since a `consumers_of_item` tool would sit directly beside
`alternates_for_item` and make tool selection worse. `produces=` comes along free and is the only way to
ask which **build-gun** recipe makes a Blender, which `alternates_for_item` is part-only by construction.

Completeness is bought with one rule: **the census is counted over all 872 recipes, never over the page.**
`recipe_kind`, `include_events`, `limit` and `offset` decide what is *shown*; they never move the header counts.
So the default part-only view of Rubber still opens with

```
# 26 recipe(s) consume Rubber: 15 part [5 HAVE, 10 LOCKED], 7 building [6 HAVE, 1 LOCKED],
  4 manual [3 HAVE, 1 LOCKED]. Counted over all 872 recipes; recipe_kind/limit change the rows, never these totals.
! recipe_kind='part' hides 7 building and 4 manual recipe(s) that also consume Rubber -- pass recipe_kind='all'
```

Measured on the reference save, and this is exactly the case the worry named: the seven building
recipes eating Rubber are the **Fuel-Powered Generator (50/build)**, **Resource Well Pressurizer (100)**,
**Blueprint Designer Mk.2 (100)**, **Fluid Truck Station (20)**, **Packager (10)**, **Valve (4)** and
**Power Pole Mk.3 (3)**. A part-only answer misses eleven of twenty-six consumers. Plastic is worse:
10 part against **10 building**, all ten unlocked.

**Build costs must never render as rates.** `mManufactoringDuration` is 1.0 on all 547 building recipes,
so `amount × 60 / duration` turns the Fuel-Powered Generator's 50 Rubber into 3,000/min and The HUB's
20 Iron Ore into 1,200/min. Part rows carry `/min`, building rows `/build`, manual rows `/craft`, and the
suffix is on the cell rather than the header because `recipe_kind="all"` mixes them in one table.

FICSMAS recipes stay hidden by default and are **counted anyway** — 12 event recipes consume a FICSMAS
Gift, and a total that quietly dropped them is a total nobody can rely on.

### 10.1c `bom` — the flattened bill

`bom(item, qty)` gives the total raw and intermediate rates for `qty` per minute of an item. It was hand-
multiplied off a recipe tree before, which is precisely what an LP does better.

**It is a presentation layer over the existing solve, not a second engine**, and the choice is forced
rather than aesthetic: §10.1's ban on recursive expansion applies verbatim, because Recycled Plastic and
Recycled Rubber are a real 2-cycle and both unlocked. `build_scenario` builds the request exactly as
`plan_factory` would, then `min_raw` runs with **`extractor_nodes={}`** and every resource given an
unlimited raw cap — a bill is the chain, not the mine, and charging extraction would make it depend on
which nodes happen to be free. That is the same construction `compare_recipe_options` uses.

The cycle is not merely survived, it is **reported**. For 60 Plastic/min the bill builds 75.56 Plastic of
capacity and recirculates 15.56 back through Recycled Rubber; a Plastic line reading 75.56 for a 60 export
looks like an error until the response says `production loop: Plastic <-> Rubber`. Detection is mutual
reachability over the chosen processes only, which is cheap at a couple of dozen items.

**Water gets a documented lexicographic tie-break**, which §8.7 demands as the alternative to labelling
a degenerate vector. A bare `min_raw` sums every resource with weight one and therefore trades crude
against water. Measured on 60 Plastic/min:

| | Crude Oil | Water |
|---|---|---|
| bare `min_raw` | **56.25** m³/min | 0 |
| water priced last | **20.00** m³/min | 66.67 m³/min |

Water is effectively unlimited on this map, so the unweighted answer overstates the scarce input by
**2.8×**. `Scenario.raw_weights` exists for this: phase 1 minimises every other resource with water free,
phase 2 pins those and minimises water alone. Two solves. Whatever degeneracy survives is labelled in the
response rather than presented as the number, and `only_recipes` / `exclude_recipes` let a caller pin the
chain and get arithmetic instead of an optimum.

Two traps, both bugs first:

- **The phase-2 caps need 5e-5 of headroom.** `Solution.raw_used` is rounded to 4 dp, so a draw of
  13.33333 is reported as 13.3333 and a cap derived from it sits *below* what the chain needs. Phase 2
  went infeasible on Reinforced Iron Plate and silently threw the tie-break away. Same rounding, same
  fix as `compare_recipe_options`.
- **A column at 1e-6 machine-equivalents is not a building.** `ceil` turns one into a whole Smelter with
  a recipe name against it, so the bill claimed two routes to Iron Ingot where the flow ran entirely
  through one, and named two alternates carrying no flow. Processes below 1e-4 of the plan's largest are
  dropped; the threshold is relative so a bill for 0.1/min is not filtered away.

**Verified by hand.** Pinned to the base chain, 10 Reinforced Iron Plate/min:

```
raw 120 Iron Ore -- 14 machines, 78 MW
Iron Ingot  120  (4 Smelters)   Screws 120 (3)   Iron Plate 60 (3)   Iron Rod 30 (2)   RIP 10 (2 Assemblers)
```

6 Plate + 12 Screws per plate → 60 Plate + 120 Screws → 90 + 30 = 120 Iron Ingot → **120 Iron Ore**, i.e.
12 ore per plate; power is 4×4 + 8×4 + 2×15 = 78 MW. Solver and paper agree exactly. Left to choose,
this save's alternates route the same 10 plates through Stitched Iron Plate and the Pure ingot recipes for
**26.92 Iron Ore + 13.33 Copper Ore + 24.27 Water** — a 4.5× swing on iron, which is why every row names
its recipe.

### 10.1d One module per concern

`server.py` reached **3,467 lines and 36 tools** before being split. It now only imports and
re-exports; the tools live in `interfaces/mcp/tools/`, one module per concern, and the three
biggest concerns are packages of their own: `planning/` (stored plans, solve, layout,
staging, siting, analysis, the page's context), `factories/` (discovery, query, health,
labels, trace, floors) and `spatial/` (places, nodes, conduits, map links). Resources and
prompts sit beside `app.py` and `params.py` in `interfaces/mcp/`. Module sizes are not
tabled here because they move: `TOOL_MODULE_MAX_LINES` in `tests/architecture/test_module_caps.py`
caps every module under `interfaces/mcp/` at 650 lines.

These rules hold it together, each with a test:

- **Every tools `__init__.py` imports its modules for their side effects.** The decorators
  run on import, and that is what attaches a tool to the shared `mcp`. Those imports look
  unused and are not — a module dropped from its package's imports would leave the server
  starting cleanly and simply not offering its tools. A test walks every package and asserts
  nothing is missing. A subpackage's `__init__` re-exports its tools by name.
- **Tool modules never import each other.** What every group needs lives in `app` — the
  `mcp` object, `load_world`, the `tool` decorator, `Refusal` — and parameter types shared by
  several schemas live in `params`. Inside a package the only sibling imports allowed are its
  private helper modules (`planning/_plan_log.py`, `planning/_requests.py`). Shared resolvers
  live with their domains — `factories.select.resolve_factory`, `spatial.places.resolve_place`
  — because a resolver is a domain decision rather than an app detail. A sibling import is the
  first step back toward one file, so a test forbids it.
- **`server` re-exports every tool.** Tests and scripts reach for `server.plan_factory`, and
  a caller should not need to know which module a tool landed in.
- **One seam to the save.** Every tool reads its world through `app.load_world`, which turns
  any failure into the refusal `could not read save: …`; a tool that answers without a save
  uses `app.load_world_or_none`. Tests serve a world through the `use_world` fixture, which
  patches that one function, rather than patching each tool module.
- **A refusal is an answer.** `app.tool` registers a text tool and returns a raised
  `Refusal`'s text, or a selector error as `! …`, as the tool's reply.

The move was mechanical — every tool body is byte-identical — but two classes of breakage
were invisible to the linter and only showed at runtime: relative imports written for the
package root resolve one level too shallow inside `tools/` (`from .render` became
`satisfactory_mcp.tools.render`), and the **indented** lazy imports inside function bodies
escaped a line-anchored fix, so `factory_labels` failed only when called. `ruff` passed
clean in both states; the test suite caught them.

`progression` also corrects a mislabel: `phase_requirements` and `power_shards` had been
spliced under the *resources* banner during a parallel merge, and are tools.

### 10.1e Tools call services; they do not contain them

The domain already lived in `docs/`, `graph/`, `planning/` and `spatial/`. What a tool
module holds after the split is argument marshalling, orchestration and rendering — of
`plan_factory`'s 252 lines, roughly 20 marshal arguments, 10 orchestrate and 200 present.
Presentation belongs in the tool. **Orchestration did not**, and the evidence is a bug.

`plan_factory`, `plan_layout` and `diff_vs_save` each wrote out the same seven steps:
recall a saved plan → merge overrides → build a scenario → reject an empty source
selection → reject an unusable export → solve → explain a failure. Three copies drift, and
these did: `plan_layout` stopped accepting `extractor_clocks` and `water_extractors`, so it
silently re-solved at defaults and schematised a different plan than the one it was asked
to draw — **15,043 MW against 83,737**. Nothing in its output said arguments had been
dropped, because from its own point of view none had.

`planning/solver/prepare.py` is that sequence, once. It returns a `PreparedPlan` carrying either a
solution or a `PlanFailure` of headline plus notes.

**It renders nothing**, and a test asserts so. Wording stays with the tool because the three
genuinely differ — `plan_factory` explains byproduct balance, while the other two defer to
it rather than repeating a diagnosis they did not run. Sequence is shared; voice is not.

Tests pin the shape rather than the behaviour alone: no planning tool may call
`build_scenario` or `solve` directly, `prepare` may not mention `render`, and all three
must report a bad request identically. There is also a test that `prepare` works with no
MCP layer at all, which is the point of the extraction — a script or a batch planner gets
the same guards.

**Not everything was extracted, deliberately.** `factory_query` (185 lines) is long but its
length is filtering and table-building against a domain call that already exists; there is
no second copy to drift from. The rule applied was: extract where logic is *duplicated* or
*unreachable without the MCP layer*, not wherever a function is long. `search_resource_nodes`
met the second half of that rule once the World page needed the same answer: its selection,
status, totals and fields are `domain/spatial/nodes/search.py` now, beside `rank_build_sites`, `surroundings.player_surroundings`,
`surroundings.describe_point`, `conduit_search.search` and `regions.region_rows`, and each spatial tool is that
call plus its text (frontend_vision.md §18).

### 10.1f `search_conduits` — belts and pipes become queryable text

The projection has carried every conveyor and pipeline polyline since schemas 12/13 and the
web map drew them, but no *text* tool could see them: `trace_upstream` walks through logistics
deliberately, and `describe_location` sampled only nodes, buildings and foundations. Field
result: the assistant twice told the player a build **did not exist** when the tools simply
could not look.

```
search_conduits(near="x,y"|"me"|<factory>, radius_m=250, to=None, to_radius_m=None,
                conduit_kind="belt"|"pipe"|"all"|None, show="runs"|"networks",
                network=None, limit=12, offset=0)
  -> per-run rows: id (chain:<n> / pipe:<row>), kind+tier, drawn length, both ends
     (position + what stands there where known), elevation span, carries, connects
     network=<id> lists every pipe of one fluid network (ids from show="networks"),
     whatever the radius
```

Decisions that took measurement:

- **A run is a belt CHAIN or a single pipeline piece.** Chains are the game's own grouping
  (1,909 over 3,085 pieces on the reference world); pipes have no chain, and their network is
  a whole plumbing system (19 networks claim all 503 pipes), so network-granularity "runs"
  would have no endpoints.
- **Proximity is measured against the drawn line, not the corner points.** A 500 m straight
  belt has exactly two stored points; point-distance calls its middle 250 m away, which is the
  exact blindness being retired.
- **`to=` asks "between two areas", and pipes answer it exactly.** A pipe route is usually
  several pieces, none of which spans both areas — but the game's own `FGPipeNetwork` id is a
  connectivity fact, so a network touching both areas is reported as joining them even when no
  single piece qualifies. Belts have no such id (a route through a splitter is several
  chains); a note owns that gap rather than a guess.
- **`connects` is labelled a geometric read**, and it is the one part of this tool that still
  is. Ends are attributed to the nearest placed thing whose footprint (plus port reach) covers
  them, `?` where nothing known stands there, and a `chain:`/`pipe:` ident where the run
  simply continues into another — which is what lets a route be followed piece to piece.
  Junctions, pumps and valves are in no placement table, so a pipe names one only where the
  material graph does, as `via`. The EXACT answer to the same question is `factory_health`
  and `trace_upstream`, which read the save's connection records instead; since schema 20
  both tables carry an actor index, so the two views share these idents and can be read
  against each other. §6.15.
- **Lengths follow the drawn line.** A span whose tangents schema 15 records is integrated
  along its own spline, and a span with none is its chord; the projection stores tangents
  exactly where a chord would be out by a centimetre or more, so both measure the curve the
  map draws. A chord across one measured bend was out by 16.4 m.
- **Every distance is in metres and every coordinate in centimetres.** Coordinates stay in
  the save's own units and every threshold is stated in the metres it is compared in, all
  routed through `spatial.geo` (`domain/world/conduits.py` builds the runs,
  `domain/world/conduit_search.py` answers the tool).
- **It pages on the surface's `offset=` convention.** The truncation envelope ends "call again
  with offset=N", and a busy junction really does carry hundreds of chains — a next step the
  caller cannot take would be worse than none.

`describe_location` gained a `conduits=` field counted the same way — **printed even at
zero**, so with a readable save, absence in that answer finally means absence in the world.

### 10.1g `factory_map show=slabs` — bare platforms are places too

`show=slabs` listed only slabs *carrying machines*, because the slab signal exists to propose
factories — so a bare 1,901-foundation platform, the most important object in that user's
build, was invisible, and the client reconstructed its extent from **nine `describe_location`
probes by hand**. Bare (machine-less) slabs are now their own table: tile count, extent,
**bounding box**, elevation and a floors count, largest platform first.

- **`bbox` is stored on the `Slab`, not derived**, because it cannot be: `centre` is the tile
  *mean* and sits wherever the tiles are dense, so `centre ± extent/2` invents corners an
  L-shaped platform does not have.
- **Extents span tile centres.** The poured edge reaches about half a tile further, and the
  response says so rather than quietly measuring two different things.
- **Elevation is the whole span** (`lo..hi`, one number where the pour is flat). A platform
  built over three storeys stands at both heights, and a plan reading only the bottom one
  puts a machine under the floor.
- **Helper pads are summarised, and the threshold is printed.** Bare slabs under
  `BARE_TILE_FLOOR = 12` tiles (a 3×4 pour of 8 m foundations — below that it is a tile under
  a power pole or a jump-pad landing) collapse to one count line that names the threshold, so
  a summarised pad is a known omission instead of a blind spot.

### 10.1h `stock`, `storage`, `crates`, `factory_floors` — the keys the map read and no tool did

Storage (schema 15), crates (18), the crate inventory bucket (19) and the floor
decomposition all went to the web map and never to the LLM, so an assistant could be told
"short 500 Quartz" with no way to ask what was in the boxes: 151 container rows, 130 of
them holding something, read by two routers and by zero tools. `WorldState.stock()` was
reachable from exactly one place — an affordability check — and `machine_buffers()` from
none at all.

```
stock(item=None, where=False, save, world, limit=25, offset=0)
  -> per item: spendable | carried | storage | depot | buffers | crates
     where=True: one row per container or crate holding it, with a region and a coordinate

storage(item=None, near=None, radius_m=500, container_kind=None, empty=False, limit=15, offset=0)
  -> per container: region, coordinate, fill, used/slots (or m3/capacity), contents

crates(limit=25, offset=0)          -> what you lost, what kind of crate, and where it is
factory_floors(factory=None, platform=None, limit=10, offset=0)
  -> per platform: floors, minor bands, area, centre, top span, machines
     one platform: per floor -- top, area, tiles, machines and what they are
```

**The four piles stay four columns.** Spendable is carried + storage + Depot, the set every
cost check spends; buffer material and crate contents are printed beside it and never added
in, which is the rule `stock()` and schema 19 already encode and no response had ever
stated. Measured on the reference world: 61,941 Concrete spendable, 9,551 more inside
machines, 7 in a crate.

**`fill` is computed here, not read.** The projection carries a slot count and a total, and
the fraction between them is the answer to "is this box backing up" — used slots over slots
at each item's own stack size for a container, m3 over the class's capacity for a fluid
buffer, and `-` where either number is unknown rather than a fraction of a guess.

**A census counts the world; filters decide only which rows are shown.** `storage(item=…)`
still opens with all 151 containers, because a header that moved with the filter would
answer "how many containers have I got" with the number holding concrete. Empty containers
are hidden by default and the note names the count and the way back.

**`factory_floors` prints the measured decomposition, and says so.** `factory_map
show=slabs` counts storeys as a slab's z span over 4 m, on slabs welded through ramps;
`domain/factories/floors.py` clusters foundation tops per platform with no assumed pitch.
The two disagree wherever a ramp climbs, so the answer names which measurement it is. Pours
with no band at all — 62 of 132 on the reference world, the largest 7 tiles — are counted
in a note rather than listed or silently dropped.

### 10.1i `as_of=` — a save identity a client can check

**The one sentence a client author needs: pin once, and every later call is either consistent
or loudly not.** Read anything, take the `sav:…` token off the header line, and pass it as
`as_of=` on every call that belongs to the same question.

The failure it exists for: an assistant makes several tool calls, the game autosaves in the
middle, call 1 reads save A and call 3 reads save B, and the answer it composes never existed
in either world. Naming the file did not make that detectable, because the game **reuses**
filenames — `autosave_0` is a different world every rotation.

**The token.** `sav:` and twelve hex digits, hashed from the save's `save_identifier`,
`play_duration_s`, `mtime_ns` and `size` — the same identity `timeline.row_key` keys on,
minus the filename and minus both schema versions. The schema numbers version *this server's
code*, not the world, so folding them in would expire a live pin on an upgrade and the
refusal would have no true sentence to offer. 48 bits: at 100,000 saves — five minutes apart,
a year of unbroken play — the chance any two collide is about 2e-5. Six digits *looks* right
and is 24 bits, which is even odds by 5,000 saves.

**Where it is printed.** In `age_note`, the one line every save-reading answer already
carries, at the front — everything else on that line, filename included, is shared by every
autosave the file has ever held. The `satisfactory://save/current` resource leads with it,
because that is where an orienting client looks first. `/api/summary` sends it as
`save_token`, and the SSE `save` event carries it too (`null` where the header could not be
read), so the map page and an assistant can name the same world state.

**What a mismatch does.** It **refuses**. It does not fall back to the newer save, and it
does not try to answer from the pinned one: an autosave overwrites its own file, so the
pinned state is usually no longer on disk. Four refusals, each naming the pin first, because
each is a different mistake:

| the pin | the answer |
| --- | --- |
| matches the save on disk | answered normally |
| a token this install minted for **this** world, but not the current one | *that is not the save on disk now* — plus both states and the distance between them, in playtime **and** wall clock. Where the pin was a **manual** save it also offers `save=<that file>`, which is the one recovery that works: manual saves are not rewritten, so the re-read carries the pinned token and the pin holds. It is not offered for an autosave, whose bytes are gone |
| a token minted for **another** world | *that token names a different world* — and no distance, because two worlds keep two unrelated playtime clocks and subtracting them would be a fabricated measurement |
| a well-formed token never minted here | *no save this install has ever read carries that token* |
| not a token at all | *not a save token* — plus what one looks like |

**`as_of=` is a check, never a selector.** It is applied *after* `save=`/`world=` have picked
a file, so the three cannot compete. That ordering is the useful one: `save=` names a file
and the game rewrites files, so a pinned **filename** goes on resolving happily to a world
state the caller has never seen. Pinning a filename is no protection; pinning a token is.

Tokens are recorded in `save-pins.json` under the cache directory (the newest 200), which is
what lets the second refusal differ from the third. The ledger is best-effort like every other
cache here — losing it costs a refusal's sharpness, never an answer. `domain/world/pin.py` is
the implementation; `tests/domain/world/test_save_pin.py` reproduces the hazard end to end.

### 10.1j One word per question — the parameter vocabulary

A client reads this surface twice: once by writing a call, and once by reading the schema.
An alias only helps the first. So a question asked by more than one tool has exactly **one**
parameter name, and a retired spelling **errors with the caller's own value rewritten** —
`! of='power' is retired -- write show='power' instead` — rather than silently working or
failing schema validation with nothing to act on. `interfaces/mcp/app.py:retired` builds
those messages; `tests/mcp/test_conventions.py` pins one per retirement.

| the question | the parameter | retired |
|---|---|---|
| which view of this answer | `show=` | `of=`, `detail=`, `mode=`, `status=`, `group=` (on `search_resource_nodes`) |
| match a row by the text in its name | `query=` | `search=` |
| narrow to one resource type | `resource=` | `with_resource=` |
| where — a single point | `at=`, or `near=` for a search centre | see [selectors.md](selectors.md) |
| how many rows | `limit=`, `offset=` | `top=` is still an alias on `rank_build_sites` |

**`show=`'s values are per tool, and that is not the same defect one level down.** It asks
"which of *your* views", not "which kind of thing", so nobody expects `factory_map`'s
`slabs` to mean anything to `mam_research`. Across all eight only two words recur — `all`
and `nearest` — and both mean the same thing everywhere they appear.

**`kind=` was the opposite case, and split rather than merged.** It named five unrelated
vocabularies — a recipe class, a building category, a container's medium, a conduit's
medium, a node kind — and no single enum holds those. Four now say what they filter and one
kept the word:

| tool | parameter | values |
|---|---|---|
| `search_recipes` | `recipe_kind=` | `part` (default), `building`, `manual`, `all` |
| `list_buildings` | `building_kind=` | `production` (default), `extractor`, `generator`, `logistics`, `foundation`, `ramp`, `wall`, `pillar`, `beam`, `architecture`, `all` |
| `storage` | `container_kind=` | `solid`, `fluid`, `all` |
| `search_conduits` | `conduit_kind=` | `belt`, `pipe`, `all` |
| `search_resource_nodes` | `kind=` | `node`, `well_sat`, `geyser`, `all` |

The last keeps the bare word because it is shorthand for the node selector's own `kind:`
term, beside `resource=`/`resource:` and `purity=`/`purity:`. Renaming the parameter alone
would have given one tool two spellings of one filter.

**`all` means no filter, in every one of them** — and in the `kind:`, `purity:` and
`resource:` selector terms too. `search_resource_nodes` used to answer `kind must be
node|well_sat|geyser, got 'all'` for the word its four siblings read as "no filter"; one
member of a family rejecting the family's own wildcard is a bug whatever the family is
called.

### 10.1k Plan versions and the activity journal

The web page and any number of chat sessions edit the **same** stored plans, so a plan is a
history of versions rather than a file that is overwritten. The store, the merge rule and the
wording are in [planner_slice_contract.md](planner_slice_contract.md) §3–§4 and §10; this is
what the tools do with them.

**Reads print the version.** `list_plans` has `ver` and `last change` columns
(`page 2h: sloops 0→4`, cut to 36); `list_plans name=` heads with
`# plan "north hmf" v14 (key a1b2c3d4)`; every tool that recalls a plan with `plan=` prints
`recalled plan "north hmf" v14` as its first note.

**Writes take `base_rev`**, the version the caller read: `plan_factory save_as=<existing>`,
`rename_plan`, `forget_plan`, `site_plan` and `plan_log undo=/restore=`. Without it an existing
plan is refused before anything is solved or written:

```
! plan "north hmf" exists at v14: read it (list_plans name="north hmf") and pass base_rev=14; nothing saved
```

With it, edits to different settings since `base_rev` merge (`merged onto v14 (you were on
v11) -> now v15; others changed: ...`), and the same setting changed by someone else is
refused whole as `! outdated: ...`, listing the conflicts and every version since. Nothing is
ever applied partially. `save_as` over a plan sends the *whole* request, diffed against the
`base_rev` state rather than the head, which is what makes the merge three-way. A new name
ignores `base_rev` and says so. A write that finds the plan lock held for 10 s answers
`! plans are busy (another writer held the lock 10 s); nothing written`.

The store stamps each landing version with the new head's `plan_id` and resolved field
(`head_stamper` in `tools/planning/_plan_log.py`), so `world moved` stays honest after merged edits and page
edits alike.

**`required`** on `plan_factory` takes recipe names or class ids. The tool resolves each to a
class id among *all* recipes, exact id first, then exact display name (case-insensitive); an
unknown or ambiguous one is refused by name before solving. A locked one passes through and the
solve refuses it (contract §6).

**`plan_log name=`** lists versions newest first with who, what, age, `merged over` and
`undone in`; `since=` and `limit=` narrow it. `undo=<v>` writes the inverse of that version,
`restore=<v>` writes the ops that make the head equal it; both merge like any write. A
forgotten plan is found here too, so `undo=<the forget>` brings it back.

**Who wrote it.** A chat write carries `Actor("chat", clientInfo.name, pid)`: the client name
is what the MCP client sent at `initialize` (`claude-code` reads as "Claude Code", `claude-ai`
as "Claude Desktop"), read through the `ctx: Context` FastMCP injects and never shows in a
schema. Called as a plain function (tests, scripts) the client is blank and reads as "chat".

**The activity journal** (`domain/session/journal.py`, contract §8) holds what is not a plan
edit: `plan.solve` for a `plan_factory` without `save_as` (the page offers it as a from-chat
card), `plan.view` for `plan_layout`, `diff_vs_save` and `commission_plan` with `plan=`,
`world.find` for the finder tools (docs/web-wire.md, World), and `plan.rejected` from the web. One file per process under `activity/<world>/`, `chat-<pid>.jsonl`
or `web-<pid>.jsonl`, so a single writer needs no lock. Nothing is written until the process
names itself: `server.main` calls `journal.set_writer("chat")` and the web lifespan
`set_writer("web")`. Importing the tools, as the test suite does, journals nothing, so a test
run cannot leak solves into the page's activity feed. A failed journal write is swallowed. An
entry over 1 kB drops its `args`. `read` returns the newest `limit` entries after `since_ts`,
oldest first.

**`ui_context`** reads the page's focus file (the web group's `focus.read`, contract §9) and the
logs, and answers in one block: open or closed by heartbeat age, world, whether the page reads
the same save token as this session, the focused view, plan and version (with the head when
the page is behind), tab and selection -- with the selection's selector after its label,
`selected: node "Iron Ore, pure" (node:BP_...)`, so chat can resolve "the selected node" --,
the follow setting, then *since you last looked*: every
plan version and journal entry by someone else since this process's last call. The cursor is
per process and in memory. The first call shows the last five of each; the process's own
commits and entries are left out by pid. Plans past eight, versions past six per plan and
journal entries past eight are counted as `(+N more)`, and the whole stays under 3,800
characters. The server's `instructions` tell the client to call it when the user says "this",
"here" or "what I have open".

**Pins** (Planner P3, [planner_p3.md](planner_p3.md)). The page creates, renames and deletes
pins. Chat creates one with `show_on_map(at=..., pin=True)`: a node, a factory, a sited plan or
a point (anything else it shows pins as the point it resolved to; `resource:` refuses), the
reply ends `pin: pinned as pin:N ...` or `pin: already pin:N ...`, and a `pin.add` journal entry
by chat makes every open page refetch its pins. `ui_context` prints a `pins:` line (the newest eight live pins,
ascending, each cut to 90 characters, `(+N more)` past them, `pins: none` when empty, gone
pins ending `(gone)`) and appends `(pin:N)` to a selection that a pin matches. Every tool that
takes a place, a node source, a machine select, `plan=`, `required=` or `exclude_recipes=`
accepts `pin:<n>` as [selectors.md](selectors.md) "Pins" lists, and echoes what it expanded
to. `alternates_for_item(plan=)` adds the plan's view of each recipe: its status in the plan,
Δ machines, Δ MW draw and the first two raw inputs that change if it were required, and a
note naming the `plan_factory(... required=[...], base_rev=, save_as=)` call that would do it.
It journals `plan.view` with `args {"view": "alternates", "item": <class id>}` and text
`looked at recipes for <item>`. No tool was added.

**Track and asks** (Planner P4, [planner_p4.md](planner_p4.md)). A stored plan carries a
startup headroom, `headroom_mw` (a plan scalar the page sets; `null` is the save's measured
headroom, source `measured from the save`, the page's default too).
`diff_vs_save(plan=)` and `commission_plan(plan=)` both use it, so the stage numbers chat reads
are the page's; `commission_plan(headroom_mw=)` still overrides it for one call (source `given
by caller`). Headroom sources read `stored on the plan`, `measured from the save`, `nameplate from
the save` or `given by caller` in every tool; `commission_plan` names the nameplate figure as the
safe bound when it used measured, and `diff_vs_save` notes the headroom its stages used. Both journal `plan.view` with `args {"view": "track", "stage": n|null, "section":
"stages"|"startup"}`, which the page follows into its Track tab. Each process remembers the
partition it last printed per plan; when a later read partitions differently, the first note is
`the stages changed since you last read this plan (v14 -> v15): you were in stage 2 of 4, now
stage 2 of 5`. A `commission_plan` with an explicit `headroom_mw` neither prints nor records it.
`plan_factory save_as` never writes `headroom_mw`, so a chat save keeps the page's value.

**Payback horizon** ([planner-payback-horizon_contract.md](planner-payback-horizon_contract.md)).
`plan_factory` and `plan_layout` take `payback_hours` (0–100), `overclock_last` and
`power_price` (points per MWh, default the save's grid mix). A row is spread over more, slower
machines while the power saved repays their build points within that many hours of play;
`overclock_last` builds a row one machine short with the last one overclocked, weighed against
the same horizon and the shards in hand plus those craftable from slugs. `row_overclock`
(`{recipe: "last" | "spread" | "default"}`) overrides the switch for one row; a call names only
the rows it changes. Above 0 h, `max_mw` and `min_power` price each machine at its build points
over the horizon instead of `machine_cost_mw`. They are stored `PlanArgs` fields that both chat
and page write; unset, a plan follows the shared `payback_hours` and `overclock_last` settings,
and `"default"` puts a recalled plan back on them. The notes say what the plan spreads against 0 h,
what the next stop would change, and what the overclock saves or would save.

The page queues **asks** (`ask:N`, a question about a plan, process, stage, item or pin) and
the player pastes one into chat. `ui_context` prints `asks (N waiting): ...`, the newest six open
or seen asks with what each is about, then a hint line; every listed open ask is marked seen
(`seen_by` the client name) and journalled as `ask.seen`. `ui_context(answered=["ask:7"])` marks
asks answered first (journal `ask.answered`) and prints `marked answered: ask:7` as the line
after the header; an unknown or deleted id is refused on its own line and the rest still apply.
An id may carry one line of answer after it (`"ask:7 it makes the diluted fuel"`, also `ask:7:`
or `ask:7 -`): it is stored on the ask (`answer`, ≤ 200 characters, whitespace folded), shown on
the page beside the ask, and quoted in the journal text; passing a new line replaces it.
The server's `instructions` add "or quotes an ask: or pin: id". No tool was added.

**Advisors** ([advisors_contract.md](advisors_contract.md) §6). `ui_context` prints an
`advice (N, M hidden): ...` line after the asks: the first three rows the page's card shows,
`adv:` id first, then a hint line. `ui_context(dismissed=["adv:3f9a", "adv:91c2 snooze 4h"])`
hides rows on the page as chat (journal `advice.hide`), only when the user asks; the page can
restore them. `world_summary` ends with a `## worth a look` block, every active row with its
`next:` call, at most twelve. An ask about an advisory prints its `adv:` id. No tool was added.

### 10.1l `settings` — what the page and chat share

`settings()` prints every shared setting as `setting · value · takes · means`, marking a value
nobody set `(default)`, with the last writer in the header. `settings(change={...})` writes as
`chat`, last writer wins, and `null` resets a setting. The description tells the model to change
one only when the user asks. `diff_vs_save` and `commission_plan` stage against
`stage_headroom`, and `biomass=` on the four power tools defaults to `None`, meaning the shared
value. An unreadable file falls back to the default with a note. Tool count 55.
[shared-settings.md](shared-settings.md) is the specification.

The header also carries one read-only line naming the base map types the page can show:
`# base maps (show_on_map mode=): map (default), terrain, …, terrain-r3-502094 -- stale: newer
heightfield (v3 → v5)`. `show_on_map(mode=)` takes one of those ids, `artwork` (read as `map`)
or `plain`, and puts `mode=` in the local link, after `world=` as the page writes it; an unknown
id is refused with the list. Chat cannot generate or delete a map: the runner lives in the web
process and a job wants a visible confirm. [maps_contract.md](maps_contract.md) §7. No tool was
added.

### 10.1m The cave flag on every height

`describe_location`, `whereami` and the siting z (`siting.settle_z`, which `site_plan` prints) read
`Reading.cave` from the cave masks (spatial-and-map.md §23). A point with a cave under it gets
one `cave=` line, and the height stays the surface. A point inside a cave (the player's z for
`me` and `whereami`, the typed or built-median z for a site) gets
`cave=in a cave: ground height unknown here (the surface above is 233 m)`: `terrain_m=unknown`
in `describe_location`, and no site z. A ceiling is never printed. Without
`data/local/caves/` nothing changes.

### 10.1n Rock heights from the collision surface

With `rocks.npz` beside the field (spatial-and-map.md §24), every height on rock is the
surface the player and the build gun stand on: `describe_location`'s `terrain_m`, `whereami`,
the inspector and the siting z. No tool or argument changed. Two things read differently:

- A hint can pick a surface no plane holds: a lower shelf, a ledge under an overhang, a cave
  floor. The siting reply names it `surface floor`.
- Inside a cave with a hint, a collision floor at most 3 m under the hint (and not more than
  2 m above it) is the answer: `terrain_m` is that floor and the line is
  `cave=in a cave: floor -9.2 m, the rock collision just under the given height`. A site there
  gets that z, with `in a cave: z is the rock collision just under the hint` on its terrain
  line. With no floor in reach the answer stays `unknown`, as in §10.1m. No ceiling is printed.

### 10.1o What the tool descriptions leave to this page

A tool description is resident in every session, so it carries the usage contract and nothing
else; `TOOL_DESCRIPTION_BUDGET` in `tests/mcp/test_surface.py` caps the total. The measurements and
the history behind the contracts live here:

- **`plan_factory`.** `machine_cost_mw` defaults to 5 MW, just above the 2.58 MW per machine
  that spreading throughput over more machines was measured to save. Overclock modes are not
  offered by default because they spend Power Shards. Somersloops are placed one at a time
  across many machines because output is linear in sloops and power quadratic. How many
  Water Extractors a body of water holds is placement geometry no data here carries, so
  `site_at` measures the water and never moves an LP number.
- **`propose_factories`** was validated leave-one-factory-out against the twelve hand-named
  factories of the reference world: precision 1.000 on every fold, recall 0.945.
- **`trace_upstream`.** 92.5% of the connectors landing on a machine name their direction;
  the rest sit on extractors and generators, whose nature settles them. A trace from the
  generators touches 331 nodes at depth 72, nearly all conveyor -- why belts and pipes are
  walked through rather than listed.
- **`select_machines`.** On the reference save 17 machines make Concrete and 15 of them are a
  construction feed inside the steel site, which is why a product selector deserves a preview.
- **`factory_map`.** Power islands left the reference world's grown-together base as one
  476-machine blob.
- **`list_buildings`.** `all` is 540 buildings, about 60k characters unpaged.
- **`somersloops`.** Before it, the free sloop count could only be learned by guessing a
  `sloops=` budget and reading the shortfall.
- **`rename_factory`, `rename_plan`, `amend_factory`.** Before them a correction meant
  naming or saving again under a second name, or re-selecting a whole factory to drop one
  machine.
- **`recipe_detail`** resolves a display name the way `match_recipes` does for
  `exclude_recipes`; refusing one cost a caller two round trips.
- **`mam_research`.** `BP_UnlockSubsystem_C` records overclocking as
  `mIsBuildingOverclockUnlocked`; nothing in the save records production amplification.
- **`diff_vs_save`.** A generator is matched on its building alone because its fuel is piped
  in; Water Extractors have no recipe and no resolvable node; grid membership is not persisted.
- **`commission_plan`.** Generators draw 0 MW in the dump, so energising one is free.

### 10.2 Context budget

The binding constraint. All 291 automatable recipes in optimal TSV = 25,313 chars (~7k tokens). **No tool
may be able to return its full table.**

1. Compact **TSV, not JSON** — the win is dropping repeated keys and braces.
2. **Schema-enforced caps**: `Annotated[int, Field(ge=1, le=25)]`, default 10. The model *cannot* ask for
   291 rows.
3. Names in rows, **IDs once in a footer**.
4. Summary/detail split; never ship cycle time + power + unlock in a list.
5. Precompute `/min`; pre-divide fluids.
6. **Truncation envelope counting data rows only** — a header/footer miscount produced "showing 7" for 5
   recipes, which actively misleads the model. The envelope says "call again with `offset=N`"
   **only when the tool takes an `offset`** — `render.table` writes that sentence only for a
   caller that passes `offset=`, and a table that cannot page says how to narrow instead.
   Fifteen tools named a parameter their own schema rejected before that rule existed.
7. Scoped aggregates before rows ([§7.3](spatial-and-map.md#73-source-selectors)).
8. Round coordinates to metres.

Worked example, `alternates_for_item("Plastic")` — 498 chars, vs 4,893 for raw Docs subtrees:

```
# 3 automatable recipes make Plastic (1 alternate). rates=/min at 100% clock, one machine.
recipe	building	in/min	out/min	source
Alternate: Recycled Plastic	Refinery 30MW	30 Rubber + 30 Fuel	60 Plastic	ALT
Plastic	Refinery 30MW	30 Crude Oil	20 Plastic + 10 Heavy Oil Residue	tier5
Residual Plastic	Refinery 30MW	60 Polymer Resin + 20 Water	20 Plastic	tier5
# ids: Alternate: Recycled Plastic=Recipe_Alternate_Plastic_1_C Plastic=Recipe_Plastic_C …
```

15–20 tools ≈ 6–8 kB always-resident schema; keep descriptions to one line and push procedure into prompts.

### 10.3 Resources and prompts

**Resources (2–3, static).** Client-pulled, so zero context until requested:
`satisfactory://docs/summary`, `satisfactory://save/current`. Do **not** expose
`satisfactory://recipe/{id}` — it duplicates `recipe_detail`, and duplicate surfaces degrade tool
selection.

**Prompts (3):** `design_factory(target_item, rate)`, `debug_power(save)`,
`expand_here(resource, region)`. Zero cost until invoked, surface as slash commands, and keep multi-step
procedure out of tool descriptions.

### 10.4 Registration

```json
{"mcpServers": {"satisfactory": {
  "type": "stdio", "command": "uv",
  "args": ["run", "--directory", "E:/development/Hobby Projekte/SatisfactoryMcp", "satisfactory-mcp"],
  "env": {"SATISFACTORY_DOCS": "G:/SteamLibrary/steamapps/common/Satisfactory/CommunityResources/Docs/en-US.json"}
}}}
```

---

## 11. Caching

**Docs.json: no disk cache.** 50 ms to load and normalize. Build lazily into a module global; a disk cache
would add invalidation bugs to save 50 ms. Optionally persist the normalized snapshot (925 KiB raw /
68 KiB gzip) keyed `sha256(Docs.json)[:16]` purely so game updates are detectable.

**The save projection is where caching matters: 3.7 s → 1 ms.** Cache the *projection*, not the parse
tree. Key: `sha256(abspath | st_mtime_ns | st_size | schema_version)[:16]`. Two-tier: process LRU(3) in
front of a pickle at `%LOCALAPPDATA%\satisfactory-mcp\cache\` (via `platformdirs.user_cache_dir` —
`LOCALAPPDATA`, not `APPDATA`; regenerable data must not roam).

**But a call to `load_projection()` is not 1 ms — it is ~90 ms, and the difference is a subprocess.**
The memo hit is 1 ms; the `resolve_save` in front of it runs `scan_saves`, which is a `--list` sidecar
over the save directory, on every call that passes no explicit path. That is the floor on every warm
read on both surfaces. It is a subprocess because header decoding belongs to the parser and
`core/saveio/projection.py` is the only module allowed to know one exists.

**Pruned on write, not on startup.** Autosaves rotate every ~5 minutes and each rotation is a new
cache key, so a long session grows the directory by ~500 kB per autosave — and startup pruning would
never fire during the session causing the growth. Globbing a dozen files costs nothing next to the 4 s
parse that just completed. Keeps the 12 newest.

**Concurrent misses collapse into one computation** (`core/singleflight.py`). An autosave is a new key
for the file every reader resolves to, and the map page fetches eleven layers at once, so a miss is
eleven simultaneous misses. Three things are flighted: the parse and the scan in
`core/saveio/projection.py`, and the five expensive views over a projection —
`graph`, `structures`, `pipe_flow`, `conduit_runs`, `proposals`, ~0.8 s together — in
`domain/world/state.py`, which are shared by every `WorldState` built over the same projection and
game data rather than rebuilt per request. The scan is flighted but deliberately **not** stored: it is
what notices the save the player wrote a moment ago. `plans` and `labels` are not shared at all — the
tools write through them. Measured on the reference world, 11 concurrent GETs after one autosave:
**24 parser subprocesses → 2**, cold **8.40 s → 4.19 s**, warm **1.21 s → 0.36 s** (medians of five
reps alternated against the unpatched tree). See [§23](residency.md) for what is left after that and
why there is no resident daemon.

---

## 12. Testing

- **Don't commit the 2.9 MB `.sav`.** Commit the ~9 kB sidecar projection as
  `fixtures/save_projection.json` — the only save-derived thing the server consumes.
- Commit small hand-checked Docs slices, not 10.6 MB.
- **Golden-file tests on `render.py`**, asserting `len(response) < budget` per tool. Context regressions
  are otherwise invisible.
- Build-time invariant assertions: `872 == 547 + 291 + 34`; purity table joins 100%; belt/pipe rates match
  their `mDescription` prose; all production buildables map `Desc_ ↔ Build_`.
- Optimizer: the free-lunch audit and duplicate-pid assertion as permanent tests, plus the crude→plastic
  byproduct table in [§8.2](planning.md#82-the-byproduct-rule--the-crux) as a regression fixture.
- Mark real-file tests `@pytest.mark.integration`, skipped when `SATISFACTORY_DOCS` is unset, so CI is
  green with no game install.
- **Two commands, and the default is the one a clone can run.** `uv run pytest -q` carries
  `addopts = ["-m", "not integration", "-n", "8"]`, so it selects the 694 tests that read only
  committed fixtures — green on a machine with no game and no saves, **5.8 s**.
  `uv run pytest -q -m integration` is the other 805, needs both, and takes **25 s**. That split
  is enforced rather than assumed: before it, the documented command on a bare clone gave 20
  failures and 61 errors, because a tool reaches game data through the lru_cache'd `app.game()`
  rather than through the suite's fixture, and eight modules built a live `WorldState` with no
  guard. The `live` fixture in `tests/conftest.py` is now the single place that turns "no
  readable save" into a skip.
- **The suite is parallel at two levels, and the second one is where the integration run was
  won.** `-n 8` is `pytest-xdist`; on top of it, three tests marked `whole_folder` walk the
  reader's entire save directory and fan out into subprocesses of their own. Those three were
  146 s of a 198 s run — 74% of the wall clock in 3 tests out of 805 — and are 22 s now:
  `test_savparse_parity` 88.1 → 12.3 s, `test_savparse_trailers` 36.5 → 7.3 s,
  `test_sidecar_placed` 21.3 → 2.5 s. Every number in this bullet is reproducible with
  `--durations=25`, and the settings that produced them (the worker count, the fan-out width
  in `tests/support/fanout.py` and the collection order in `tests/conftest.py`) carry their
  measurement tables in docs/DEVELOPING.md, "Test suite".
- **Parallel changed what "shared state" costs, and two things had to be fixed for it.** The
  projection disk cache is written through `core/atomic.py` rather than `Path.write_bytes` —
  eight workers resolve the same newest save and miss the same key at the same moment, so
  create-then-fill let one read another's prefix — and `prune_cache` no longer raises when a
  rival pruner deletes a file between its glob and its sort. Both are production fixes rather
  than test-only isolation, deliberately: the cache directory is already shared by the web
  server and every CLI invocation, and giving each worker its own would have replaced a 0.10 s
  warm load with a 3.17 s parse, eight times over.

Deps: `mcp[cli]>=1.28`, `pydantic>=2.13`, `platformdirs`, `scipy>=1.11`, `numpy`,
`typing-extensions>=4.6`; dev `pytest`, `pytest-cov`, `pytest-xdist>=3.6,<4`, `ruff`,
`pyright[nodejs]` and `scipy-stubs` (exact pins, docs/DEVELOPING.md "Types").
`requires-python = ">=3.11"`.

